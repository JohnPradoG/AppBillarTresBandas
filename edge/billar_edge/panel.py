"""Panel de administración por la red del billar o la VPN (Fase 8, parte 2).

Proceso aparte de la pantalla: la pantalla y su API siguen solo en
127.0.0.1. Este servidor escucha en la red con HTTPS (certificado propio,
creado la primera vez) y solo ofrece la ADMINISTRACIÓN: la misma API de
`admin.py`, que pide el PIN de un usuario para todo. Desde fuera del billar se
llega por la VPN (Tailscale), nunca abriendo puertos en el router.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import socket
import ssl
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, share
from .config import Config

log = logging.getLogger("billar.panel")

STATIC = Path(__file__).parent / "static"
# Lo único que se sirve: la página del panel y lo que necesita.
FILES = {
    "/": "panel.html",
    "/panel.html": "panel.html",
    "/panel.js": "panel.js",
    "/admin.js": "admin.js",
    "/keyboard.js": "keyboard.js",
    "/app.css": "app.css",
    "/panel.css": "panel.css",
    "/vano-logo.svg": "vano-logo.svg",
    "/vendor/qrcode.mjs": "vendor/qrcode.mjs",
}
FONTS = "/vendor/fonts/"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2"}
SECURITY = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def ensure_certificate(cfg: Config) -> tuple[Path, Path]:
    """Crea un certificado propio (10 años) si no existe. El navegador avisa la
    primera vez porque no lo firma una autoridad; se acepta una vez por equipo."""
    cert, key = cfg.panel_tls
    if cert.exists() and key.exists():
        return cert, key
    cert.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    names = ["DNS:localhost", "IP:127.0.0.1", f"DNS:{socket.gethostname()}"]
    lan = share.lan_address(cfg)
    names.append(f"IP:{lan}" if lan.replace(".", "").isdigit() else f"DNS:{lan}")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
         "-subj", f"/CN=Billar {cfg.establishment_name[:40]}/O=Vano Systems",
         "-addext", "subjectAltName=" + ",".join(dict.fromkeys(names)),
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True, timeout=60,
    )
    key.chmod(0o600)
    log.info("Certificado del panel creado en %s", cert)
    return cert, key


def make_handler(api: admin.AdminApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "billar-panel"
        sys_version = ""
        timeout = 30   # una conexión lenta o a medias no deja hilos colgados

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for k, v in SECURITY.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _api(self, method: str):
            body = {}
            if method != "GET":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
                    if not isinstance(body, dict):
                        raise TypeError("se esperaba un objeto JSON")
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            path = self.path.split("?", 1)[0]
            status, data = api.handle(method, path, self.headers.get("X-Sesion"), body)
            self._json(data, status)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/admin/"):
                return self._api("GET")
            name = FILES.get(path)
            if name is None and path.startswith(FONTS) and path.endswith(".woff2") and "/" not in path[len(FONTS):]:
                name = path.lstrip("/")
            f = STATIC / name if name else None
            if f is None or not f.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            ctype = TYPES.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, f.read_bytes(), ctype, "no-cache")

        def do_POST(self):
            if self.path.startswith("/api/admin/"):
                return self._api("POST")
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")

        def do_PUT(self):
            if self.path.startswith("/api/admin/"):
                return self._api("PUT")
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")

    return Handler


def make_panel_server(cfg: Config, host: str | None = None, port: int | None = None,
                      tls: bool = True) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host or cfg.panel_host, cfg.panel_port if port is None else port),
                                 make_handler(admin.AdminApi(cfg)))
    server.daemon_threads = True
    if tls:
        cert, key = ensure_certificate(cfg)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        # El saludo TLS se hace en el hilo de cada conexión, no al aceptarla.
        server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return server


def main(cfg: Config) -> None:
    server = make_panel_server(cfg)
    log.info("Panel de administración en https://%s:%s/", share.lan_address(cfg), server.server_address[1])
    server.serve_forever()
