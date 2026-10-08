"""Monitor de salud: decide qué indicador se muestra y registra los cambios.

Cada 5 s revisa, por cámara, que el servicio de grabación responde, que el
archivo actual crece, que la imagen no está negra ni congelada, y el estado
del disco. El resultado se escribe en /run/billar/status.json para la
pantalla y el panel del administrador.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import events, sdnotify, statefile
from .config import Config
from .db import connect, now_ms
from .media import SNAPSHOT_NAME, read_pgm
from .recorder import RECORDING, WAITING_CAMERA
from .retention import free_percent

log = logging.getLogger("billar.health")

# Indicadores, de mayor a menor gravedad.
STORAGE_ERROR = "ERROR_ALMACENAMIENTO"
CAMERA_DISCONNECTED = "CAMARA_DESCONECTADA"
NO_SIGNAL = "SIN_SENAL"
STOPPED = "GRABACION_DETENIDA"
RECORDING_OK = "GRABANDO"

LABELS = {
    STORAGE_ERROR: "ERROR DE ALMACENAMIENTO",
    CAMERA_DISCONNECTED: "CÁMARA DESCONECTADA",
    NO_SIGNAL: "SIN SEÑAL DE VIDEO",
    STOPPED: "GRABACIÓN DETENIDA",
    RECORDING_OK: "GRABANDO",
}
SEVERITY = [STORAGE_ERROR, CAMERA_DISCONNECTED, NO_SIGNAL, STOPPED, RECORDING_OK]

STALE_STATE_SECONDS = 30
BLACK_MEAN = 10
CLOCK_JUMP_SECONDS = 2.0


@dataclass
class SignalTracker:
    last_pixels: bytes | None = None
    same_since: float | None = None
    black_since: float | None = None


@dataclass
class Health:
    cfg: Config
    clock: object = time.monotonic
    wall: object = time.time
    free_pct: object = free_percent
    signals: dict[str, SignalTracker] = field(default_factory=dict)
    last_status: dict[str, str] = field(default_factory=dict)
    last_storage: str | None = None
    last_tick: tuple[float, float] | None = None

    # ---- comprobaciones ----

    def storage(self) -> tuple[str, float | None, str | None]:
        """('ok' | 'bajo' | 'lleno' | 'error', % libre, detalle)."""
        d = self.cfg.recordings_dir
        if not d.is_dir():
            return "error", None, f"No existe la carpeta de video {d}"
        if self.cfg.require_mount and not os.path.ismount(d):
            return "error", None, f"El disco de video no está conectado o no se montó en {d}"
        probe = d / ".prueba-escritura"
        try:
            with open(probe, "wb") as f:
                f.write(b"ok")
                f.flush()
                os.fsync(f.fileno())
            probe.unlink()
        except OSError as exc:
            return "error", None, f"No se puede escribir en el disco de video: {exc.strerror or exc}"
        pct = self.free_pct(d)
        if pct < self.cfg.critical_free_percent:
            return "lleno", pct, None
        if pct < self.cfg.warn_free_percent:
            return "bajo", pct, None
        return "ok", pct, None

    def signal_state(self, camera_id: str, now: float) -> str | None:
        """None si la imagen es normal; si no, el motivo."""
        snap = self.cfg.camera_run_dir(camera_id) / SNAPSHOT_NAME
        try:
            age = self.wall() - snap.stat().st_mtime
        except FileNotFoundError:
            return "sin imagen de la cámara"
        if age > self.cfg.no_signal_seconds:
            return f"sin imagen nueva desde hace {int(age)} s"
        pixels = read_pgm(snap)
        if pixels is None:
            return None  # se está escribiendo; se revisa en la siguiente vuelta
        tr = self.signals.setdefault(camera_id, SignalTracker())
        mean = sum(pixels) / len(pixels)
        if mean < BLACK_MEAN:
            tr.black_since = tr.black_since or now
        else:
            tr.black_since = None
        if pixels != tr.last_pixels:
            tr.last_pixels = pixels
            tr.same_since = now  # desde cuándo se ve esta misma imagen
        if tr.black_since is not None and now - tr.black_since >= self.cfg.no_signal_seconds:
            return "imagen negra"
        if tr.same_since is not None and now - tr.same_since >= self.cfg.no_signal_seconds:
            return "imagen congelada"
        return None

    def camera_status(self, camera_id: str, storage_state: str, now: float) -> tuple[str, str | None]:
        if storage_state == "error":
            return STORAGE_ERROR, None
        rec = statefile.read(self.cfg.camera_run_dir(camera_id) / "recorder.json")
        if rec is None:
            return STOPPED, "el servicio de grabación no está funcionando"
        age = (self.wall() * 1000 - rec.get("updated_at", 0)) / 1000
        if age > STALE_STATE_SECONDS:
            return STOPPED, f"el servicio de grabación no responde desde hace {int(age)} s"
        state = rec.get("state")
        if state == WAITING_CAMERA:
            return CAMERA_DISCONNECTED, rec.get("last_error")
        if state != RECORDING:
            return STOPPED, rec.get("last_error")
        problem = self.signal_state(camera_id, now)
        if problem:
            return NO_SIGNAL, problem
        return RECORDING_OK, None

    # ---- una vuelta ----

    def check(self, conn) -> dict:
        now, wall = self.clock(), self.wall()
        if self.last_tick is not None:
            drift = (wall - self.last_tick[1]) - (now - self.last_tick[0])
            if abs(drift) > CLOCK_JUMP_SECONDS:
                events.record(conn, "advertencia", events.CLOCK_JUMP,
                              f"La hora del sistema saltó {drift:+.0f} s", data={"segundos": drift})
        self.last_tick = (now, wall)

        storage_state, pct, detail = self.storage()
        self._storage_events(conn, storage_state, pct, detail)

        cameras = []
        for cam in self.cfg.cameras:
            status, why = self.camera_status(cam.id, storage_state, now)
            self._camera_events(conn, cam.id, status, why)
            cameras.append({"camera_id": cam.id, "table_number": cam.table_number, "status": status,
                            "label": LABELS[status], "detail": why})
        overall = min((c["status"] for c in cameras), key=SEVERITY.index)
        report = {
            "status": overall,
            "label": LABELS[overall],
            "ok": overall == RECORDING_OK,
            "cameras": cameras,
            "storage": {"state": storage_state, "free_percent": None if pct is None else round(pct, 1),
                        "detail": detail},
            "updated_at": now_ms(),
        }
        statefile.write(self.cfg.run_dir / "status.json", report)
        return report

    def _camera_events(self, conn, camera_id: str, status: str, why: str | None) -> None:
        previous = self.last_status.get(camera_id)
        if status == previous:
            return
        self.last_status[camera_id] = status
        if status == RECORDING_OK:
            if previous == NO_SIGNAL:
                events.record(conn, "info", events.SIGNAL_OK, "Señal de video recuperada", camera_id)
            return
        if status == NO_SIGNAL:
            events.record(conn, "error", events.NO_SIGNAL, f"Cámara sin señal: {why}", camera_id)
        # Desconexión y grabación detenida ya las registra el servicio de grabación;
        # aquí solo se registra cuando el propio servicio deja de responder.
        elif status == STOPPED and why and "servicio" in why:
            events.record(conn, "error", events.RECORDING_STOPPED, f"Grabación detenida: {why}", camera_id)

    def _storage_events(self, conn, state: str, pct: float | None, detail: str | None) -> None:
        if state == self.last_storage:
            return
        previous, self.last_storage = self.last_storage, state
        if state == "error":
            events.record(conn, "error", events.STORAGE_ERROR, f"Error de almacenamiento: {detail}")
        elif state == "lleno":
            events.record(conn, "error", events.STORAGE_FULL, f"Almacenamiento casi lleno: {pct:.1f} % libre")
        elif state == "bajo":
            events.record(conn, "advertencia", events.STORAGE_WARNING, f"Advertencia de almacenamiento: {pct:.1f} % libre")
        elif previous is not None:
            events.record(conn, "info", events.STORAGE_OK, f"Almacenamiento normal: {pct:.1f} % libre")


def main(cfg: Config) -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    conn = connect(cfg.db_path)
    events.record(conn, "info", events.SYSTEM_START, "Sistema iniciado")
    health = Health(cfg)
    sdnotify.ready()
    while not stop.is_set():
        try:
            report = health.check(conn)
            sdnotify.status(report["label"])
        except Exception:  # el monitor nunca debe caerse por un error puntual
            log.exception("Error en la revisión de salud")
        sdnotify.watchdog()
        stop.wait(5.0)
    conn.close()
