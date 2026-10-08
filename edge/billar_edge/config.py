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
    # Exigir que la carpeta de video sea un disco o partición montada, para no
    # llenar el disco del sistema si el disco de video falta.
    require_mount: bool = False
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
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


def load(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("BILLAR_CONFIG", DEFAULT_CONFIG_PATH))
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    general = raw.get("general", {})
    storage = raw.get("storage", {})
    recorder = raw.get("recorder", {})
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
        ffmpeg=recorder.get("ffmpeg", "ffmpeg"),
        ffprobe=recorder.get("ffprobe", "ffprobe"),
        cameras=cameras,
    )
