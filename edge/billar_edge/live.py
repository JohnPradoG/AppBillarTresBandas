"""Supervisor de la señal en vivo para la pantalla de una cámara.

Igual que el grabador, arranca solo, espera a la cámara y se reinicia si la
señal se detiene; pero escribe en memoria (/run) y no toca la base de datos ni
el disco de video. Si este proceso falla, la grabación sigue intacta.
"""

from __future__ import annotations

import collections
import logging
import shutil
import signal
import subprocess
import threading
import time

from . import sdnotify, statefile
from .config import Config
from .db import now_ms
from .media import LIVE_PLAYLIST, live_command, recorder_env
from .recorder import BACKOFF_SECONDS, STARTUP_GRACE_SECONDS, _redact, camera_reachable

log = logging.getLogger("billar.live")

LIVE = "en_vivo"
WAITING_CAMERA = "camara_desconectada"
RESTARTING = "reiniciando"
STOPPED = "detenido"

# Sin playlist nueva en este tiempo, se reinicia la señal.
STALL_SECONDS = 10


class LiveStream:
    def __init__(self, cfg: Config, camera_id: str, clock=time.monotonic):
        self.cfg = cfg
        self.camera = cfg.camera(camera_id)
        self.live_dir = cfg.live_dir(camera_id)
        self.state_path = cfg.camera_run_dir(camera_id) / "live.json"
        self.clock = clock
        self.proc: subprocess.Popen | None = None
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self.state = STOPPED
        self.state_since = now_ms()
        self.last_error: str | None = None
        self.failures = 0
        self.next_start_at = 0.0
        self.last_mtime = 0.0
        self.last_change_at = 0.0
        self.got_first = False
        self._stop = threading.Event()

    def run(self) -> None:
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
        self._write_state()

    def _try_start(self, now: float) -> None:
        if not camera_reachable(self.camera.url):
            self.last_error = "la cámara no responde en la red"
            self._set_state(WAITING_CAMERA)
            self.next_start_at = now + 5
            return
        # Empezar limpio: la pantalla no debe mezclar trozos de antes del reinicio.
        shutil.rmtree(self.live_dir, ignore_errors=True)
        self.live_dir.mkdir(parents=True, exist_ok=True)
        cmd = live_command(self.cfg.ffmpeg, self.camera, self.live_dir)
        log.info("Iniciando señal en vivo: %s", " ".join(_redact(cmd)))
        self.stderr_tail.clear()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=recorder_env(), text=True, errors="replace",
        )
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        self.last_mtime = 0.0
        self.last_change_at = now
        self.got_first = False
        self._set_state(RESTARTING)

    def _check_running(self, now: float) -> None:
        rc = self.proc.poll()
        if rc is not None:
            self.last_error = self.stderr_tail[-1] if self.stderr_tail else f"código {rc}"
            log.warning("La señal en vivo terminó: %s", self.last_error)
            self._schedule_retry(now)
            return
        try:
            mtime = (self.live_dir / LIVE_PLAYLIST).stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if mtime != self.last_mtime:
            self.last_mtime = mtime
            self.last_change_at = now
            if mtime:
                self.got_first = True
                self.failures = 0
                self._set_state(LIVE)
            return
        allowed = STALL_SECONDS if self.got_first else STARTUP_GRACE_SECONDS
        if now - self.last_change_at > allowed:
            self.last_error = f"sin imagen nueva desde hace {int(now - self.last_change_at)} s"
            log.warning("Reiniciando la señal en vivo: %s", self.last_error)
            self._kill()
            self._schedule_retry(now)

    def _schedule_retry(self, now: float) -> None:
        delay = BACKOFF_SECONDS[min(self.failures, len(BACKOFF_SECONDS) - 1)]
        self.failures += 1
        self.next_start_at = now + delay
        self.proc = None
        self._set_state(RESTARTING)

    def _kill(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def shutdown(self) -> None:
        self._kill()
        self.proc = None
        self._set_state(STOPPED)
        self._write_state()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            line = line.strip()
            if line:
                self.stderr_tail.append(line)
                log.warning("ffmpeg: %s", line)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_since = now_ms()
            sdnotify.status(state)

    def _write_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        statefile.write(self.state_path, {
            "camera_id": self.camera.id,
            "state": self.state,
            "since": self.state_since,
            "last_error": self.last_error,
            "updated_at": now_ms(),
        })


def main(cfg: Config, camera_id: str) -> None:
    live = LiveStream(cfg, camera_id)
    signal.signal(signal.SIGTERM, live.stop)
    signal.signal(signal.SIGINT, live.stop)
    live.run()
