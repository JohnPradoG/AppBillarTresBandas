"""Fase 8, parte 2: panel de administración por la red con HTTPS."""

import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from billar_edge import panel

STATIC = Path(panel.__file__).parent / "static"


@pytest.fixture
def server(cfg, conn):
    srv = panel.make_panel_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def fetch(base, cfg, path, method="GET", body=None, token=None):
    ctx = ssl.create_default_context(cafile=str(cfg.panel_tls[0]))
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", **({"X-Sesion": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_certificate_is_created_once(cfg):
    cert, key = panel.ensure_certificate(cfg)
    assert cert.exists() and key.stat().st_mode & 0o777 == 0o600
    before = cert.read_bytes()
    panel.ensure_certificate(cfg)
    assert cert.read_bytes() == before


def test_panel_serves_only_the_admin(cfg, server):
    status, headers, body = fetch(server, cfg, "/")
    assert status == 200 and b"panel.js" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert fetch(server, cfg, "/vendor/fonts/barlow-latin-400-normal.woff2")[0] == 200
    # Nada de la pantalla de la mesa ni de sus datos.
    for path in ("/index.html", "/app.js", "/api/state", "/api/plays", "/live/mesa1/index.m3u8",
                 "/vendor/fonts/../../web.py", "/../billar.db"):
        assert fetch(server, cfg, path)[0] == 404, path


def test_panel_uses_the_pin_api(cfg, server):
    assert json.loads(fetch(server, cfg, "/api/admin/inicio")[2]) == {"has_users": False}
    status, _, body = fetch(server, cfg, "/api/admin/primer-uso", "POST", {"pin": "8642"})
    token = json.loads(body)["token"]
    assert fetch(server, cfg, "/api/admin/estado")[0] == 401
    status, _, body = fetch(server, cfg, "/api/admin/estado", token=token)
    data = json.loads(body)
    assert status == 200 and data["remote"]["panel_url"].startswith("https://")
    status, _, body = fetch(server, cfg, "/api/admin/jugadas", token=token)
    assert status == 200 and json.loads(body) == {"plays": []}


def test_panel_page_has_the_same_admin_markup_as_the_table():
    def block(name):
        html = (STATIC / name).read_text(encoding="utf-8")
        return html[html.index('<section class="admin-view"'):html.index('<div class="toast"')].strip()
    assert block("panel.html") == block("index.html")
