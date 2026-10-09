"""Supervisor de grabación de una cámara.

Arranca solo con el equipo (servicio de systemd), espera a la cámara, lanza
FFmpeg, comprueba cada segundo que el archivo actual crece y reinicia la
grabación si se detiene. Nadie tiene que pulsar "Grabar".
"""

from __future__ import annotations

import collections
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import events, indexer, sdnotify, statefile
from .config import Config
from .db import connect, ensure_camera, now_ms
from .ids import uuid7
from .media import parse_segment_start, recorder_command, recorder_env

log = logging.getLogger("billar.recorder")

# Estados que el monitor y la pantalla muestran.
RECORDING = "grabando"
WAITING_CAMERA = "camara_desconectada"
RESTARTING = "reiniciando"
STOPPED = "detenido"
STORAGE_UNAVAILABLE = "sin_almacenamiento"

BACKOFF_SECONDS = (1, 2, 5, 5, 10)
# FFmpeg analiza la señal unos segundos antes de escribir el primer archivo.
STARTUP_GRACE_SECONDS = 20
INDEX_EVERY = 10.0


def camera_reachable(url: str, timeout: float = 3.0) -> bool:
    """Para cámaras RTSP, comprueba que responde antes de lanzar FFmpeg."""
    parsed = urlparse(url)
    if parsed.scheme != "rtsp" or not parsed.hostname:
        return True
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 554), timeout=timeout):
            return True
    except OSError:
        return False


class Recorder:
    def __init__(self, cfg: Config, camera_id: str, clock=time.monotonic):
        self.cfg = cfg
        self.camera = cfg.camera(camera_id)
        self.out_dir = cfg.camera_dir(camera_id)
        self.run_dir = cfg.camera_run_dir(camera_id)
        self.state_path = self.run_dir / "recorder.json"
        self.clock = clock
        self.conn = None
        self.proc: subprocess.Popen | None = None
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self.recording_id: str | None = None
        self.state = STOPPED
        self.state_since = now_ms()
        self.last_error: str | None = None
        self.failures = 0
        self.next_start_at = 0.0
        self.started_at = 0.0
        self.last_size = -1
        self.last_growth_at = 0.0
        self.last_index_at = 0.0
        self.camera_was_down = False
        self.ever_recorded = False
        # Archivos que ya existían al lanzar FFmpeg: no cuentan como "creciendo".
        self.preexisting: set[str] = set()
        self._stop = threading.Event()

    # ---- ciclo principal ----

    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.conn = connect(self.cfg.db_path)
        ensure_camera(self.conn, self.cfg.establishment_name, self.camera)
        indexer.reconcile(self.conn, self.cfg, self.camera.id)
        sdnotify.ready()
        try:
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(1.0)
        finally:
            self.shutdown()

    def stop(self, *_args) -> None:
        self._stop.set()

    def tick(self) -> None:
        sdnotify.watchdog()
        now = self.clock()
        if self.proc is None:
            if now >= self.next_start_at:
                self._try_start(now)
        else:
            self._check_running(now)
        if now - self.last_index_at >= INDEX_EVERY:
            self.last_index_at = now
            active = self.current_file() if self.proc is not None else None
            indexer.index_camera(self.conn, self.cfg, self.camera.id, active)
        self._write_state()

    # ---- arranque y vigilancia de FFmpeg ----

    def _try_start(self, now: float) -> None:
        if self.cfg.require_mount and not os.path.ismount(self.cfg.recordings_dir):
            # Sin disco de video no se graba en el disco del sistema; el monitor
            # muestra ERROR DE ALMACENAMIENTO. Se reintenta cada 5 s.
            self.last_error = "disco de video no montado"
            self._set_state(STORAGE_UNAVAILABLE)
            self.next_start_at = now + 5
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if not camera_reachable(self.camera.url):
            if not self.camera_was_down:
                self.camera_was_down = True
                events.record(self.conn, "error", events.CAMERA_DISCONNECTED,
                              "Cámara desconectada: no responde en la red", self.camera.id)
                self.conn.execute("UPDATE cameras SET status = 'desconectada' WHERE id = ?", (self.camera.id,))
            self._set_state(WAITING_CAMERA)
            self.next_start_at = now + 5
            return
        if self.camera_was_down or not self.ever_recorded:
            events.record(self.conn, "info", events.CAMERA_CONNECTED, "Cámara conectada", self.camera.id)
            self.conn.execute("UPDATE cameras SET status = 'conectada' WHERE id = ?", (self.camera.id,))
            self.camera_was_down = False

        cmd = recorder_command(self.cfg.ffmpeg, self.camera, self.out_dir, self.run_dir, self.cfg.segment_seconds)
        log.info("Iniciando FFmpeg: %s", " ".join(_redact(cmd)))
        self.stderr_tail.clear()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=recorder_env(), text=True, errors="replace",
        )
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        self.preexisting = {p.name for p in self.out_dir.glob("*.mp4")}
        self.started_at = now
        self.last_growth_at = now
        self.last_size = -1
        self.recording_id = uuid7()
        self.conn.execute(
            "INSERT INTO recordings (id, camera_id, started_at) VALUES (?, ?, ?)",
            (self.recording_id, self.camera.id, now_ms()),
        )
        if self.ever_recorded:
            events.record(self.conn, "advertencia", events.RECORDING_RESTARTED,
                          "Grabación reiniciada automáticamente", self.camera.id)
        else:
            events.record(self.conn, "info", events.RECORDING_STARTED,
                          "Grabación iniciada automáticamente", self.camera.id)
        self.ever_recorded = True
        self._set_state(RESTARTING)  # pasa a GRABANDO cuando el archivo empiece a crecer

    def _check_running(self, now: float) -> None:
        rc = self.proc.poll()
        if rc is not None:
            detail = self.stderr_tail[-1] if self.stderr_tail else f"código {rc}"
            self._finish_recording("proceso_terminado")
            self.last_error = detail
            events.record(self.conn, "error", events.RECORDING_STOPPED,
                          f"Grabación detenida: {detail}", self.camera.id, {"returncode": rc})
            self._schedule_retry(now)
            return
        current = self.current_file()
        if current is not None and current.name in self.preexisting:
            current = None
        try:
            size = current.stat().st_size if current is not None else -1
        except FileNotFoundError:
            size = -1
        if size != self.last_size:
            self.last_size = size
            self.last_growth_at = now
            if size > 0:
                if self.state != RECORDING:
                    self.failures = 0
                self._set_state(RECORDING)
        elif now - self.last_growth_at > self._allowed_stall():
            self.last_error = f"El archivo no crece desde hace {int(now - self.last_growth_at)} s"
            events.record(self.conn, "error", events.RECORDING_STOPPED,
                          f"Grabación detenida: {self.last_error}. Reiniciando", self.camera.id)
            self._kill()
            self._finish_recording("sin_avance")
            self._schedule_retry(now)

    def _allowed_stall(self) -> float:
        if self.last_size <= 0:
            return max(self.cfg.stall_seconds, STARTUP_GRACE_SECONDS)
        return self.cfg.stall_seconds

    def _schedule_retry(self, now: float) -> None:
        delay = BACKOFF_SECONDS[min(self.failures, len(BACKOFF_SECONDS) - 1)]
        self.failures += 1
        self.next_start_at = now + delay
        self.proc = None
        self._set_state(RESTARTING)

    def _finish_recording(self, reason: str) -> None:
        if self.recording_id:
            self.conn.execute(
                "UPDATE recordings SET ended_at = ?, end_reason = ? WHERE id = ?",
                (now_ms(), reason, self.recording_id),
            )
            self.recording_id = None

    def _kill(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        # SIGINT deja que FFmpeg cierre el segmento actual de forma ordenada.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def shutdown(self) -> None:
        if self.proc is not None:
            self._kill()
            self.proc = None
            self._finish_recording("apagado_ordenado")
            events.record(self.conn, "info", events.RECORDING_STOPPED,
                          "Grabación detenida por apagado ordenado", self.camera.id)
        self._set_state(STOPPED)
        if self.conn is not None:
            indexer.index_camera(self.conn, self.cfg, self.camera.id, active=None)
            self._write_state()
            self.conn.close()
            self.conn = None

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            line = line.strip()
            if line:
                self.stderr_tail.append(line)
                log.warning("ffmpeg: %s", line)

    # ---- estado ----

    def current_file(self) -> Path | None:
        files = [p for p in self.out_dir.glob("*.mp4") if parse_segment_start(p) is not None]
        return max(files, key=lambda p: p.name) if files else None

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_since = now_ms()
            sdnotify.status(state)

    def _write_state(self) -> None:
        current = self.current_file() if self.proc is not None else None
        statefile.write(self.state_path, {
            "camera_id": self.camera.id,
            "state": self.state,
            "since": self.state_since,
            "pid": self.proc.pid if self.proc is not None else None,
            "current_file": str(current) if current else None,
            "last_error": self.last_error,
            "updated_at": now_ms(),
        })


def _redact(cmd: list[str]) -> list[str]:
    """No escribir la contraseña de la cámara en el registro."""
    out = []
    for part in cmd:
        parsed = urlparse(part)
        if parsed.scheme == "rtsp" and parsed.password:
            part = part.replace(f":{parsed.password}@", ":***@")
        out.append(part)
    return out


def main(cfg: Config, camera_id: str) -> None:
    rec = Recorder(cfg, camera_id)
    signal.signal(signal.SIGTERM, rec.stop)
    signal.signal(signal.SIGINT, rec.stop)
    rec.run()
