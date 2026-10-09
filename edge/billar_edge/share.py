"""Compartir jugadas (Fase 7) sin gastos recurrentes.

Al tocar COMPARTIR se hace una copia liviana de la jugada (720p, 30 cps,
unos 2,5 Mbps: ~14 MB por 45 s, bajo el límite de WhatsApp) con la marca de
agua: nombre del billar, mesa, fecha, jugadores y marcador arriba, y VANO
SYSTEMS (y el logo, si está configurado) abajo. Esa copia se entrega por:

- Un enlace en la red del billar (QR en la pantalla): el celular conectado
  al WiFi lo abre, descarga el video y lo comparte por WhatsApp desde la
  galería. Lo sirve un proceso aparte (`billar share-server`) que solo
  conoce estas copias; la pantalla y su API siguen solo en 127.0.0.1.
- Telegram, si el dueño configura un bot (gratis): el QR abre el bot, el
  cliente toca Iniciar y el bot le manda el video (`billar telegram`). Este
  camino necesita Internet; el resto funciona sin ella.

Los enlaces vencen a las `share_hours` horas y la limpieza borra sus copias.
"""

from __future__ import annotations

import html
import logging
import os
import re
import secrets
import socket
import sqlite3
import subprocess
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, plays, replay
from .config import Config
from .db import connect, now_ms

log = logging.getLogger("billar.share")

FONTS = Path(__file__).parent / "assets" / "fonts"
FONT_BOLD = FONTS / "barlow-condensed-latin-700-normal.woff"
FONT_SEMI = FONTS / "barlow-condensed-latin-600-normal.woff"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HOUR_MS = 3_600_000
RENDER_TIMEOUT = 240


class ShareError(Exception):
    pass


# ---------- marca de agua ----------

def _esc(path: Path) -> str:
    """Ruta dentro de un filtro de FFmpeg."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def caption_lines(cfg: Config, play: sqlite3.Row) -> tuple[str, str]:
    when = datetime.fromtimestamp(play["moment_ms"] / 1000).strftime("%d/%m/%Y %H:%M:%S")
    top = f"{cfg.establishment_name} · Mesa {play['table_number']}"
    bottom = when
    if play["player1"] and play["player2"] and play["score1"] is not None and play["score2"] is not None:
        bottom += f" · {play['player1']} {play['score1']} – {play['score2']} {play['player2']}"
    return top, bottom


def render_command(cfg: Config, src: Path, out: Path, text_files: tuple[Path, Path, Path]) -> list[str]:
    top, bottom, brand = text_files
    common = "expansion=none:fontcolor=white:shadowcolor=black@0.6:shadowx=2:shadowy=2"
    filters = [
        "scale=-2:720", "fps=30", "format=yuv420p",
        f"drawbox=x=0:y=0:w=iw:h=104:color=black@0.45:t=fill",
        f"drawtext=fontfile='{_esc(FONT_BOLD)}':textfile='{_esc(top)}':{common}:fontsize=38:x=28:y=16",
        f"drawtext=fontfile='{_esc(FONT_SEMI)}':textfile='{_esc(bottom)}':{common}:fontsize=28:x=28:y=62",
        f"drawtext=fontfile='{_esc(FONT_BOLD)}':textfile='{_esc(brand)}':expansion=none:fontcolor=0xF0D57A@0.92"
        ":shadowcolor=black@0.7:shadowx=2:shadowy=2:fontsize=44:x=w-tw-28:y=h-th-24",
    ]
    cmd = [cfg.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
    if cfg.share_logo and cfg.share_logo.exists():
        cmd += ["-i", str(cfg.share_logo)]
        graph = (f"[0:v]{','.join(filters)}[base];[1:v]scale=-1:72[logo];"
                 "[base][logo]overlay=x=W-w-28:y=H-h-80[v]")
    else:
        graph = f"[0:v]{','.join(filters)}[v]"
    return cmd + [
        "-filter_complex", graph, "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2500k", "-bufsize", "5000k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ]


def render(cfg: Config, src: Path, out: Path, lines: tuple[str, str]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    texts = []
    for name, text in zip(("arriba", "abajo", "marca"), (*lines, "VANO SYSTEMS")):
        t = out.with_name(f"{out.stem}.{name}.txt")
        t.write_text(text, encoding="utf-8")
        texts.append(t)
    tmp = out.with_name(f"{out.stem}.tmp.mp4")
    try:
        result = subprocess.run(render_command(cfg, src, tmp, tuple(texts)), capture_output=True, text=True,
                                timeout=RENDER_TIMEOUT, check=False)
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise ShareError(f"No se pudo preparar el video. {result.stderr.strip()[-300:]}")
        os.replace(tmp, out)
    except subprocess.TimeoutExpired as e:
        raise ShareError("Preparar el video tardó demasiado.") from e
    finally:
        tmp.unlink(missing_ok=True)
        for t in texts:
            t.unlink(missing_ok=True)


# ---------- enlaces ----------

def lan_address(cfg: Config) -> str:
    """La IP del equipo en la red del billar (para el QR)."""
    if cfg.share_address:
        return cfg.share_address
    # Conectar un socket UDP no envía nada: solo elige la interfaz de salida.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def links(cfg: Config, token: str) -> dict:
    out = {"web": f"http://{lan_address(cfg)}:{cfg.share_port}/c/{token}", "telegram": None}
    if cfg.telegram_token and cfg.telegram_bot:
        out["telegram"] = f"https://t.me/{cfg.telegram_bot}?start={token}"
    return out


def create(conn: sqlite3.Connection, cfg: Config, play_id: str, clip: Path | None = None) -> dict:
    """Prepara (o reutiliza) el video para compartir de una jugada."""
    play = plays.get(conn, play_id)
    if play is None:
        raise ShareError("Esa jugada no existe.")
    now = now_ms()
    row = conn.execute(
        "SELECT * FROM shares WHERE play_id = ? AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
        (play_id, now + HOUR_MS),
    ).fetchone()
    if row is None or not Path(row["path"]).exists():
        src = Path(play["path"]) if play["protected_at"] else clip
        if src is None or not src.exists():
            try:
                src = replay.build_range(cfg, play["camera_id"], play["start_ms"], play["end_ms"],
                                         play["moment_ms"]).path
            except replay.NoRecording as e:
                raise ShareError(f"La grabación de esa jugada ya no está disponible. {e}") from e
        token = secrets.token_urlsafe(12)
        out = cfg.shares_dir / f"{token}.mp4"
        render(cfg, src, out, caption_lines(cfg, play))
        conn.execute(
            "INSERT INTO shares (token, play_id, path, bytes, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, play_id, str(out), out.stat().st_size, now, now + cfg.share_hours * HOUR_MS),
        )
        row = conn.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    return {
        "token": row["token"],
        "bytes": row["bytes"],
        "expires_at": row["expires_at"],
        **links(cfg, row["token"]),
    }


def valid(conn: sqlite3.Connection, token: str, now: int | None = None) -> sqlite3.Row | None:
    if not TOKEN.match(token or ""):
        return None
    row = conn.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    if row is None or row["expires_at"] <= (now_ms() if now is None else now):
        return None
    return row


def cleanup(conn: sqlite3.Connection, cfg: Config, now: int) -> int:
    """Borra las copias de los enlaces vencidos (y las que quedaron a medias)."""
    rows = conn.execute("SELECT token, path FROM shares WHERE expires_at <= ?", (now,)).fetchall()
    for r in rows:
        Path(r["path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM shares WHERE token = ?", (r["token"],))
    if cfg.shares_dir.exists():
        live = {Path(r[0]).name for r in conn.execute("SELECT path FROM shares")}
        limit = now / 1000 - 3600
        for p in cfg.shares_dir.iterdir():
            try:
                if p.name not in live and p.stat().st_mtime < limit:
                    p.unlink()
            except FileNotFoundError:
                pass
    return len(rows)


# ---------- servidor para los celulares ----------

PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin: 0; background: #0B1110; color: #ECEFEA; font-family: system-ui, sans-serif; text-align: center; }}
main {{ max-width: 720px; margin: 0 auto; padding: 20px 16px 32px; }}
h1 {{ font-size: 22px; margin: 8px 0 4px; }}
p {{ color: #9AA5A1; margin: 6px 0 16px; line-height: 1.4; }}
video {{ width: 100%; border-radius: 12px; background: #000; }}
a.btn {{ display: block; margin: 18px 0 10px; padding: 16px; border-radius: 12px; background: #1E8A55; color: #fff;
  font-size: 19px; font-weight: 700; text-decoration: none; }}
.brand {{ margin-top: 28px; color: #F0D57A; letter-spacing: 4px; font-weight: 700; }}
</style></head>
<body><main>
{body}
<div class="brand">VANO SYSTEMS</div>
</main></body></html>"""


def page_for(cfg: Config, row: sqlite3.Row | None, play: sqlite3.Row | None) -> str:
    if row is None or play is None:
        body = ("<h1>Este enlace ya venció</h1>"
                "<p>Pide en la pantalla de la mesa que vuelvan a compartir la jugada.</p>")
        return PAGE.format(title="Enlace vencido", body=body)
    top, bottom = caption_lines(cfg, play)
    until = datetime.fromtimestamp(row["expires_at"] / 1000).strftime("%d/%m/%Y %H:%M")
    mp4 = f"/c/{row['token']}.mp4"
    name = f"jugada-{datetime.fromtimestamp(play['moment_ms'] / 1000).strftime('%Y%m%d-%H%M%S')}.mp4"
    body = (
        f"<h1>{html.escape(top)}</h1><p>{html.escape(bottom)}</p>"
        f'<video src="{mp4}" controls playsinline preload="metadata"></video>'
        f'<a class="btn" href="{mp4}?descargar=1" download="{name}">Descargar video</a>'
        "<p>Después ábrelo en tu galería y compártelo por WhatsApp.<br>"
        f"El enlace funciona hasta el {until}.</p>"
    )
    return PAGE.format(title=html.escape(top), body=body)


def make_share_handler(cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"billar-compartir/{__version__}"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            m = re.match(r"^/c/([A-Za-z0-9_-]+)(\.mp4)?$", path)
            if not m:
                return self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            conn = connect(cfg.db_path)
            try:
                row = valid(conn, m.group(1))
                if m.group(2):
                    if row is None:
                        return self._send(HTTPStatus.GONE, b"", "text/plain")
                    if "descargar=1" in self.path:
                        conn.execute("UPDATE shares SET downloads = downloads + 1 WHERE token = ?", (row["token"],))
                    return self._video(Path(row["path"]))
                play = plays.get(conn, row["play_id"]) if row else None
                status = HTTPStatus.OK if row and play else HTTPStatus.GONE
                return self._send(status, page_for(cfg, row, play).encode(), "text/html; charset=utf-8")
            finally:
                conn.close()

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _video(self, f: Path):
            try:
                size = f.stat().st_size
                fh = open(f, "rb")
            except FileNotFoundError:
                return self._send(HTTPStatus.GONE, b"", "text/plain")
            with fh:
                start, end = 0, size - 1
                rng = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = min(int(rng.group(2)), size - 1) if rng.group(2) else end
                    else:
                        start = max(0, size - int(rng.group(2)))
                    if start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(HTTPStatus.PARTIAL_CONTENT)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

    return Handler


def make_share_server(cfg: Config, host: str = "0.0.0.0", port: int | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, cfg.share_port if port is None else port), make_share_handler(cfg))
    server.daemon_threads = True
    return server


def main(cfg: Config) -> None:
    server = make_share_server(cfg)
    log.info("Enlaces para compartir en http://%s:%s/c/…", lan_address(cfg), server.server_address[1])
    server.serve_forever()
