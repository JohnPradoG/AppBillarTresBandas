"""Actualizaciones con vuelta atrás (Fase 8, parte 2).

Cada versión del programa vive en su propia carpeta y una sola está activa:

    /opt/billar/releases/20261009-210000-a5fe898/   (venv + bin)
    /opt/billar/current -> releases/20261009-210000-a5fe898

Actualizar = preparar la carpeta nueva (lo hace `billar-actualizar`), copiar
la base de datos, cambiar el enlace `current`, reiniciar los servicios y
comprobar que todo quedó bien. Si algo falla (un servicio no arranca, la
pantalla no responde o la grabación no vuelve), se cambia el enlace a la
versión anterior y se reinicia otra vez. Las migraciones de la base de datos
solo agregan, así que la versión anterior sigue funcionando con la base
nueva; la copia queda por si hiciera falta volver a ella a mano.

Se ejecuta como root (systemctl). Lo que toca la base de datos se hace con el
usuario `billar` para no dejar archivos de root en /var/lib/billar.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import statefile
from .config import Config
from .db import now_ms
from .health import STALE_STATE_SECONDS

PREFIX = Path("/opt/billar")
KEEP_RELEASES = 3
SETTLE_SECONDS = 20
RECORDING_WAIT_SECONDS = 90

UPDATE_OK = "actualizacion"
UPDATE_FAILED = "actualizacion_fallida"
UPDATE_ROLLBACK = "version_anterior"


def critical_units(cfg: Config) -> list[str]:
    """Servicios que deben quedar funcionando para dar la versión por buena."""
    out = []
    for cam in cfg.cameras:
        out += [f"billar-recorder@{cam.id}.service", f"billar-live@{cam.id}.service"]
    return out + ["billar-health.service", "billar-ui.service", "billar-share.service", "billar-panel.service"]


# Telegram se apaga solo si no está configurado; la pantalla puede no estar
# conectada. Se reinician, pero no deciden si la versión es buena.
OPTIONAL_UNITS = ["billar-telegram.service", "billar-kiosk.service"]


def units(cfg: Config) -> list[str]:
    """Servicios que se reinician al cambiar de versión (la grabación primero)."""
    return critical_units(cfg) + OPTIONAL_UNITS


def releases(prefix: Path = PREFIX) -> list[str]:
    d = prefix / "releases"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def current(prefix: Path = PREFIX) -> str | None:
    link = prefix / "current"
    return Path(os.readlink(link)).name if link.is_symlink() else None


def switch(prefix: Path, name: str) -> None:
    """Cambia la versión activa de una vez (un enlace nuevo reemplaza al viejo)."""
    if not (prefix / "releases" / name / "venv" / "bin" / "billar").exists():
        raise FileNotFoundError(f"La versión {name} no está completa.")
    tmp = prefix / ".current.tmp"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(Path("releases") / name)
    os.replace(tmp, prefix / "current")


def prune(prefix: Path, keep: int = KEEP_RELEASES) -> list[str]:
    """Borra versiones viejas; nunca la activa ni la anterior a ella."""
    names = releases(prefix)
    active = current(prefix)
    protect = {active}
    if active in names and names.index(active) > 0:
        protect.add(names[names.index(active) - 1])
    gone = [n for n in names[:-keep] if n not in protect] if len(names) > keep else []
    for n in gone:
        subprocess.run(["rm", "-rf", "--", str(prefix / "releases" / n)], check=False)
    return gone


@dataclass
class Updater:
    cfg: Config
    prefix: Path = PREFIX
    config_path: str = "/etc/billar/billar.toml"
    run: Callable = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    settle: float = SETTLE_SECONDS
    recording_wait: float = RECORDING_WAIT_SECONDS
    log: list[str] = field(default_factory=list)

    def say(self, text: str) -> None:
        self.log.append(text)
        print(text, flush=True)

    # ---- acciones con el sistema ----

    def systemctl(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(["systemctl", *args], capture_output=True, text=True, check=False)

    def as_billar(self, *args: str, release: str = "current") -> subprocess.CompletedProcess:
        base = self.prefix / "current" if release == "current" else self.prefix / "releases" / release
        billar = str(base / "venv" / "bin" / "billar")
        return self.run(["runuser", "-u", "billar", "--", billar, "--config", self.config_path, *args],
                        capture_output=True, text=True, check=False)

    def note(self, level: str, type_: str, message: str) -> None:
        # Queda en el registro de eventos y, si es un error, llega por Telegram.
        self.as_billar("note-event", level, type_, message)

    def restart(self) -> None:
        names = units(self.cfg)
        self.systemctl("daemon-reload")
        self.systemctl("restart", *names)

    def recording_ok(self) -> bool:
        status = statefile.read(self.cfg.run_dir / "status.json")
        return bool(status and status.get("ok")
                    and now_ms() - status.get("updated_at", 0) <= STALE_STATE_SECONDS * 1000)

    def ui_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.cfg.ui_port}/api/state", timeout=5) as r:
                return r.status == 200 and "version" in json.loads(r.read())
        except (OSError, ValueError):
            return False

    def healthy(self, was_recording: bool) -> tuple[bool, str]:
        self.sleep(self.settle)
        for unit in critical_units(self.cfg):
            state = self.systemctl("is-active", unit).stdout.strip()
            restarts = self.systemctl("show", "-p", "NRestarts", "--value", unit).stdout.strip() or "0"
            if state != "active" or restarts not in ("", "0"):
                return False, f"{unit} no quedó funcionando ({state}, reinicios: {restarts})"
        if not self.ui_ok():
            return False, "la pantalla no responde"
        if was_recording:
            deadline = self.clock() + self.recording_wait
            while not self.recording_ok():
                if self.clock() > deadline:
                    return False, "la grabación no volvió"
                self.sleep(2)
        return True, "todo funcionando"

    # ---- lo que pide el usuario ----

    def apply(self, name: str) -> int:
        previous = current(self.prefix)
        if previous == name:
            self.say(f"La versión {name} ya está activa.")
            return 0
        was_recording = self.recording_ok()
        if previous or self.cfg.db_path.exists():
            out = self.as_billar("backup-db", release=name)
            self.say(out.stdout.strip() or "Copia de la base de datos hecha.")
            if out.returncode != 0:
                self.say(f"No se pudo copiar la base de datos; no se actualiza. {out.stderr.strip()}")
                return 1
        self.say(f"Activando la versión {name}…")
        switch(self.prefix, name)
        self.restart()
        if previous is None:
            self.say("Primera instalación: servicios arrancados.")
            self.note("info", UPDATE_OK, f"Programa instalado: versión {name}")
            return 0
        ok, why = self.healthy(was_recording)
        if ok:
            self.note("info", UPDATE_OK, f"Programa actualizado a la versión {name}")
            gone = prune(self.prefix)
            self.say(f"Actualización correcta: {why}." + (f" Versiones viejas borradas: {', '.join(gone)}." if gone else ""))
            return 0
        self.say(f"Falló la actualización: {why}. Volviendo a la versión {previous}…")
        switch(self.prefix, previous)
        self.restart()
        ok_back, why_back = self.healthy(was_recording)
        self.note("error", UPDATE_FAILED,
                  f"Falló la actualización a {name} ({why}). Se volvió a la versión {previous}"
                  + ("." if ok_back else f", que tampoco quedó bien: {why_back}."))
        self.say(f"Versión {previous} activa otra vez: {why_back}.")
        return 1

    def rollback(self) -> int:
        names = releases(self.prefix)
        active = current(self.prefix)
        older = [n for n in names if active is None or n < active]
        if not older:
            self.say("No hay una versión anterior guardada.")
            return 1
        target = older[-1]
        self.say(f"Volviendo de {active} a {target}…")
        switch(self.prefix, target)
        self.restart()
        ok, why = self.healthy(self.recording_ok())
        self.note("advertencia" if ok else "error", UPDATE_ROLLBACK,
                  f"Se volvió a la versión anterior {target} (antes {active}): {why}")
        self.say(f"Listo: {why}.")
        return 0 if ok else 1


def backup_db(cfg: Config, keep: int = 5) -> Path:
    """Copia consistente de la base (API de respaldo de SQLite, sin parar nada)."""
    import sqlite3
    dest_dir = cfg.data_dir / "respaldos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"billar-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(cfg.db_path)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    for old in sorted(dest_dir.glob("billar-*.db"))[:-keep]:
        old.unlink()
    return dest
