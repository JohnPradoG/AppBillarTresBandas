"""Configuración de la mesa, leída de un archivo TOML."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = "/etc/billar/billar.toml"


@dataclass(frozen=True)
class CameraConfig:
    id: str
    url: str
    table_number: int = 1
    role: str = "principal"
    # Opciones extra de FFmpeg antes de -i (por ejemplo para fuentes de prueba).
    input_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    data_dir: Path
    recordings_dir: Path
    run_dir: Path
    establishment_name: str = "Billar"
    retention_days: int = 7
    segment_seconds: int = 60
    warn_free_percent: float = 15.0
    critical_free_percent: float = 5.0
    # Segundos sin que crezca el archivo actual antes de reiniciar la grabación.
    stall_seconds: int = 15
    # Segundos con imagen negra o congelada antes de alertar "sin señal".
    no_signal_seconds: int = 30
    # Jugadas protegidas: copias propias que la limpieza nunca borra, en el
    # disco de video (por defecto <recordings_dir>/jugadas). Se avisa cuando
    # ocupan más de este espacio.
    protected_dir: Path | None = None
    protected_quota_gb: float = 100.0
    # Días que dura una jugada guardada antes de borrarse sola (0 = nunca).
    protected_days: int = 30
    # Exigir que la carpeta de video sea un disco o partición montada, para no
    # llenar el disco del sistema si el disco de video falta.
    require_mount: bool = False
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    # Servidor de la pantalla táctil. Solo local mientras no haya usuarios (Fase 8).
    ui_host: str = "127.0.0.1"
    ui_port: int = 8080
    # Minutos sin tocar la pantalla antes del modo reposo (la grabación sigue).
    idle_minutes: int = 20
    # Contacto de Vano Systems que se muestra en el modo reposo (vacío = no se muestra).
    brand_contact: str = ""
    # Compartir (Fase 7): enlaces para el celular en la red del billar y bot
    # de Telegram opcional.
    share_port: int = 8081
    share_address: str = ""          # IP o nombre del equipo en el WiFi; vacío = se detecta
    share_hours: int = 24            # lo que dura un enlace
    share_logo: Path | None = None   # PNG con el logo de Vano Systems para la marca de agua
    telegram_token: str = ""
    telegram_bot: str = ""           # nombre del bot, sin @
    cameras: tuple[CameraConfig, ...] = field(default_factory=tuple)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "billar.db"

    def camera(self, camera_id: str) -> CameraConfig:
        for cam in self.cameras:
            if cam.id == camera_id:
                return cam
        raise KeyError(f"Cámara no configurada: {camera_id}")

    def camera_dir(self, camera_id: str) -> Path:
        return self.recordings_dir / camera_id

    def camera_run_dir(self, camera_id: str) -> Path:
        return self.run_dir / camera_id

    @property
    def shares_dir(self) -> Path:
        return self.recordings_dir / "compartir"

    @property
    def plays_dir(self) -> Path:
        return self.protected_dir or self.recordings_dir / "jugadas"

    def live_dir(self, camera_id: str) -> Path:
        return self.camera_run_dir(camera_id) / "live"


def load(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("BILLAR_CONFIG", DEFAULT_CONFIG_PATH))
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    general = raw.get("general", {})
    storage = raw.get("storage", {})
    recorder = raw.get("recorder", {})
    ui = raw.get("ui", {})
    share = raw.get("share", {})
    cameras = tuple(
        CameraConfig(
            id=c["id"],
            url=c["url"],
            table_number=int(c.get("table_number", 1)),
            role=c.get("role", "principal"),
            input_args=tuple(c.get("input_args", ())),
        )
        for c in raw.get("cameras", [])
    )
    if not cameras:
        raise ValueError("La configuración no tiene ninguna cámara ([[cameras]]).")
    if len({c.id for c in cameras}) != len(cameras):
        raise ValueError("Hay cámaras con el mismo id.")
    if any(c.id in ("jugadas", "compartir") for c in cameras):
        raise ValueError("Una cámara no puede llamarse 'jugadas' ni 'compartir': son carpetas del sistema.")
    return Config(
        data_dir=Path(general.get("data_dir", "/var/lib/billar")),
        recordings_dir=Path(general.get("recordings_dir", "/srv/billar/video")),
        run_dir=Path(general.get("run_dir", "/run/billar")),
        establishment_name=general.get("establishment_name", "Billar"),
        retention_days=int(general.get("retention_days", 7)),
        segment_seconds=int(recorder.get("segment_seconds", 60)),
        warn_free_percent=float(storage.get("warn_free_percent", 15)),
        critical_free_percent=float(storage.get("critical_free_percent", 5)),
        stall_seconds=int(recorder.get("stall_seconds", 15)),
        no_signal_seconds=int(recorder.get("no_signal_seconds", 30)),
        require_mount=bool(storage.get("require_mount", False)),
        protected_dir=Path(storage["protected_dir"]) if storage.get("protected_dir") else None,
        protected_quota_gb=float(storage.get("protected_quota_gb", 100)),
        protected_days=int(storage.get("protected_days", 30)),
        ffmpeg=recorder.get("ffmpeg", "ffmpeg"),
        ffprobe=recorder.get("ffprobe", "ffprobe"),
        ui_host=ui.get("host", "127.0.0.1"),
        ui_port=int(ui.get("port", 8080)),
        idle_minutes=int(ui.get("idle_minutes", 20)),
        brand_contact=str(ui.get("brand_contact", "")),
        share_port=int(share.get("port", 8081)),
        share_address=str(share.get("address", "")),
        share_hours=int(share.get("link_hours", 24)),
        share_logo=Path(share["logo"]) if share.get("logo") else None,
        telegram_token=str(share.get("telegram_token", "")),
        telegram_bot=str(share.get("telegram_bot", "")).lstrip("@"),
        cameras=cameras,
    )
