"""Servidor local de la pantalla táctil.

Sirve la interfaz (archivos de `static/`), la señal en vivo de cada cámara
(HLS en /run) y una API pequeña en JSON. Solo usa la biblioteca estándar de
Python y escucha en 127.0.0.1: el navegador en modo kiosco del mismo equipo
es su único cliente hasta que existan usuarios y roles (Fase 8).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import date
from urllib.parse import parse_qs, urlparse

from . import __version__, history, plays, replay, settings, statefile
from .config import Config
from .db import connect, ensure_camera, now_ms
from .health import STALE_STATE_SECONDS

log = logging.getLogger("billar.web")

STATIC_DIR = Path(__file__).parent / "static"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Un guardado a la vez: dos toques seguidos no hacen dos copias.
        self.save_lock = threading.Lock()

    def state(self) -> dict:
        conn = connect(self.cfg.db_path)
        try:
            delay = settings.get_delay(conn)
        finally:
            conn.close()
        status = statefile.read(self.cfg.run_dir / "status.json")
        stale = status is None or now_ms() - status.get("updated_at", 0) > STALE_STATE_SECONDS * 1000
        cameras = []
        for cam in self.cfg.cameras:
            live = statefile.read(self.cfg.camera_run_dir(cam.id) / "live.json") or {}
            cameras.append({
                "id": cam.id,
                "table_number": cam.table_number,
                "role": cam.role,
                "live_url": f"/live/{cam.id}/index.m3u8",
                "live_state": live.get("state"),
            })
        return {
            "version": __version__,
            "server_time": now_ms(),
            "establishment": self.cfg.establishment_name,
            "table_number": self.cfg.cameras[0].table_number,
            "cameras": cameras,
            "delay_seconds": delay,
            "delay_choices": list(settings.DELAY_CHOICES),
            "idle_minutes": self.cfg.idle_minutes,
            "brand_contact": self.cfg.brand_contact,
            "status": None if stale else status,
        }

    def set_delay(self, seconds: int) -> int:
        conn = connect(self.cfg.db_path)
        try:
            return settings.set_delay(conn, seconds, updated_by="pantalla")
        finally:
            conn.close()

    def history(self, day: str | None) -> dict:
        today = date.today()
        days = history.available_days(self.cfg, today)
        chosen = date.fromisoformat(day) if day else today
        if chosen.isoformat() not in days:
            raise ValueError("Ese día ya no está en el historial.")
        conn = connect(self.cfg.db_path)
        try:
            data = history.day_coverage(conn, self.cfg, self.cfg.cameras[0].id, chosen, now_ms())
        finally:
            conn.close()
        return {**data, "days": days, "now_ms": now_ms()}

    def build_replay(self, moment_ms: int, start_ms: int | None = None, end_ms: int | None = None,
                     meta: dict | None = None) -> dict:
        cam = self.cfg.cameras[0]
        play_id = None
        if start_ms is None or end_ms is None:
            clip = replay.build(self.cfg, cam.id, moment_ms)
            # Cada REPETICIÓN queda anotada en JUGADAS (sin proteger).
            conn = connect(self.cfg.db_path)
            try:
                play_id = plays.record(conn, self.cfg, cam.id, clip.moment_ms, clip.start_ms, clip.end_ms,
                                       "repeticion", meta)
            finally:
                conn.close()
        else:
            clip = replay.build_range(self.cfg, cam.id, start_ms, end_ms, moment_ms)
        return {
            "url": f"/repeticion/{clip.path.name}",
            "clip": clip.path.name,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "moment_ms": clip.moment_ms,
            "fps": clip.fps,
            "play_id": play_id,
        }

    def save_play(self, body: dict) -> dict:
        """GUARDAR JUGADA. Con play_id protege una jugada ya anotada (la de la
        REPETICIÓN); sin él anota y protege una nueva: desde la partida
        (−30/+15 s del momento en pantalla) o desde el historial (el tramo
        que se está viendo)."""
        with self.save_lock:
            return self._save_play(body)

    def _save_play(self, body: dict) -> dict:
        cam = self.cfg.cameras[0]
        clip_file = replay.clip_path(self.cfg, body["clip"]) if body.get("clip") else None
        conn = connect(self.cfg.db_path)
        try:
            play_id = body.get("play_id")
            if not play_id:
                source = body.get("source", "pantalla")
                moment_ms = int(body["moment_ms"])
                if "start_ms" in body and "end_ms" in body:
                    start_ms, end_ms = int(body["start_ms"]), int(body["end_ms"])
                    if not 0 < end_ms - start_ms <= replay.MAX_RANGE_MS:
                        raise ValueError("El tramo a guardar no es válido.")
                else:
                    clip = replay.build(self.cfg, cam.id, moment_ms)
                    clip_file, start_ms, end_ms = clip.path, clip.start_ms, clip.end_ms
                play_id = plays.record(conn, self.cfg, cam.id, moment_ms, start_ms, end_ms, source,
                                       body.get("meta"))
            row = plays.protect(conn, self.cfg, str(play_id), clip_file)
            used = plays.usage_bytes(self.cfg)
            quota = int(self.cfg.protected_quota_gb * 1024 ** 3)
            out = plays.public(row, self.cfg)
            if used > quota:
                out["warning"] = (f"Las jugadas guardadas ocupan {used / 1024 ** 3:.0f} GB, más de los "
                                  f"{self.cfg.protected_quota_gb:.0f} GB previstos.")
            return out
        finally:
            conn.close()

    def list_plays(self, query: dict) -> dict:
        one = lambda k: (query.get(k) or [None])[0] or None  # noqa: E731
        day = one("date")
        bounds = history.day_bounds(date.fromisoformat(day)) if day else (None, None)
        hour = int(one("hour")) if one("hour") is not None else None
        game = int(one("game")) if one("game") is not None else None
        now = now_ms()
        conn = connect(self.cfg.db_path)
        try:
            rows = plays.search(conn, self.cfg, now, bounds[0], bounds[1], hour, one("player"), game,
                                one("protected") == "1")
            opts = plays.options(conn, self.cfg, now)
        finally:
            conn.close()
        return {
            "plays": rows,
            "options": opts,
            "usage_bytes": plays.usage_bytes(self.cfg),
            "quota_bytes": int(self.cfg.protected_quota_gb * 1024 ** 3),
            "now_ms": now,
            "protected_days": self.cfg.protected_days,
        }

    def play_file(self, name: str) -> Path | None:
        if not name.endswith(".mp4") or not SAFE_NAME.match(name):
            return None
        conn = connect(self.cfg.db_path)
        try:
            return plays.file_path(conn, self.cfg, name[:-4])
        finally:
            conn.close()

    def live_file(self, camera_id: str, name: str) -> Path | None:
        if not SAFE_NAME.match(camera_id) or not SAFE_NAME.match(name):
            return None
        if camera_id not in {c.id for c in self.cfg.cameras}:
            return None
        if not name.endswith((".m3u8", ".m4s", ".mp4")):
            return None
        return self.cfg.live_dir(camera_id) / name

    @staticmethod
    def static_file(path: str) -> Path | None:
        rel = path.lstrip("/") or "index.html"
        parts = rel.split("/")
        if not all(SAFE_NAME.match(p) for p in parts):
            return None
        return STATIC_DIR.joinpath(*parts)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"billar/{__version__}"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/history":
                day = parse_qs(urlparse(self.path).query).get("date", [None])[0]
                try:
                    return self._json(app.history(day))
                except ValueError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            if path == "/api/plays":
                try:
                    return self._json(app.list_plays(parse_qs(urlparse(self.path).query)))
                except ValueError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            if path.startswith("/jugada/"):
                f = app.play_file(path.split("/", 2)[2])
                if f is None:
                    return self._error(HTTPStatus.NOT_FOUND)
                return self._file(f, cache="no-cache")
            if path.startswith("/live/"):
                parts = path.split("/")
                if len(parts) == 4:
                    f = app.live_file(parts[2], parts[3])
                    if f is not None:
                        return self._file(f, cache="no-store")
                return self._error(HTTPStatus.NOT_FOUND)
            if path.startswith("/repeticion/"):
                f = replay.clip_path(app.cfg, path.split("/", 2)[2])
                if f is None:
                    return self._error(HTTPStatus.NOT_FOUND)
                return self._file(f, cache="no-store")
            f = app.static_file(path)
            if f is None:
                return self._error(HTTPStatus.NOT_FOUND)
            return self._file(f, cache="no-cache")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/plays":
                return self._save_play()
            if path != "/api/replay":
                return self._error(HTTPStatus.NOT_FOUND)
            try:
                body = self._body()
                moment_ms = int(body["moment_ms"])
                start_ms = int(body["start_ms"]) if "start_ms" in body else None
                end_ms = int(body["end_ms"]) if "end_ms" in body else None
                meta = body.get("meta") if isinstance(body.get("meta"), dict) else None
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            try:
                clip = app.build_replay(moment_ms, start_ms, end_ms, meta)
            except ValueError as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except replay.NoRecording as e:
                return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            log.info("Repetición de %s armada", clip["moment_ms"])
            return self._json(clip)

        def _save_play(self):
            try:
                body = self._body()
                if body.get("meta") is not None and not isinstance(body["meta"], dict):
                    raise TypeError("meta debe ser un objeto")
                play = app.save_play(body)
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except replay.NoRecording as e:
                return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except plays.PlayError as e:
                return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            except OSError as e:
                log.exception("No se pudo guardar la jugada")
                return self._json({"error": f"No se pudo escribir la jugada en el disco ({e.strerror})."},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
            log.info("Jugada %s guardada", play["id"])
            return self._json(play)

        def do_PUT(self):
            path = urlparse(self.path).path
            if path != "/api/settings/delay":
                return self._error(HTTPStatus.NOT_FOUND)
            try:
                seconds = app.set_delay(int(self._body()["seconds"]))
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            log.info("Retraso de pantalla cambiado a %s s", seconds)
            return self._json({"delay_seconds": seconds})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
            if not isinstance(body, dict):
                raise TypeError("se esperaba un objeto JSON")
            return body

        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, f: Path, cache: str):
            try:
                size = f.stat().st_size
                fh = open(f, "rb")
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                return self._error(HTTPStatus.NOT_FOUND)
            with fh:
                # El reproductor pide rangos para saltar dentro de la repetición.
                start, end = 0, size - 1
                rng = RANGE.match(self.headers.get("Range", ""))
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        if rng.group(2):
                            end = min(int(rng.group(2)), size - 1)
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
                ctype = TYPES.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", cache)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _error(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def make_server(cfg: Config, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    # Las jugadas apuntan a la cámara: que esté registrada aunque el grabador
    # todavía no haya arrancado.
    conn = connect(cfg.db_path)
    try:
        for cam in cfg.cameras:
            ensure_camera(conn, cfg.establishment_name, cam)
    finally:
        conn.close()
    server = ThreadingHTTPServer((host or cfg.ui_host, cfg.ui_port if port is None else port),
                                 make_handler(App(cfg)))
    server.daemon_threads = True
    return server


def main(cfg: Config) -> None:
    server = make_server(cfg)
    log.info("Pantalla disponible en http://%s:%s/", *server.server_address[:2])
    server.serve_forever()
