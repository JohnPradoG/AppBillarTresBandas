"""Comandos de FFmpeg/FFprobe y utilidades de archivos de video."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import CameraConfig

# Nombre de cada segmento: hora UTC de inicio, p. ej. 20261008T163100Z.mp4
SEGMENT_PATTERN = "%Y%m%dT%H%M%SZ.mp4"
SNAPSHOT_NAME = "snapshot.pgm"


def recorder_command(ffmpeg: str, camera: CameraConfig, out_dir: Path, run_dir: Path, segment_seconds: int) -> list[str]:
    """Una sola conexión a la cámara con dos salidas.

    1. Segmentos de video en MP4 fragmentado, copiados sin recomprimir y
       alineados al reloj (16:00:00, 16:01:00...). Un corte de luz pierde a lo
       sumo el último fragmento (1 s con un fotograma clave por segundo).
    2. Una miniatura en gris de 32x18 por cada fotograma clave, que el monitor
       usa para detectar imagen negra o congelada. Solo se decodifican los
       fotogramas clave, así que cuesta muy poco procesador.
    """
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning"]
    if camera.url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "5000000"]
    cmd += list(camera.input_args)
    cmd += ["-skip_frame", "nokey", "-i", camera.url]
    cmd += [
        "-map", "0:v:0", "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-segment_atclocktime", "1",
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        str(out_dir / SEGMENT_PATTERN),
    ]
    cmd += [
        "-map", "0:v:0",
        "-vf", "scale=32:18,format=gray",
        "-c:v", "pgm", "-f", "image2", "-update", "1", "-y",
        str(run_dir / SNAPSHOT_NAME),
    ]
    return cmd


def recorder_env() -> dict[str, str]:
    # Los nombres de archivo usan la hora UTC, independiente de la zona del equipo.
    return {**os.environ, "TZ": "UTC"}


def parse_segment_start(path: Path) -> int | None:
    """Hora de inicio (ms UTC) a partir del nombre del archivo."""
    try:
        dt = datetime.strptime(path.name, SEGMENT_PATTERN).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def probe_duration(ffprobe: str, path: Path, timeout: float = 20) -> float | None:
    """Duración en segundos, o None si el archivo no se puede leer."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        duration = float(out.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def salvage(ffmpeg: str, path: Path, timeout: float = 60) -> bool:
    """Intenta recuperar la parte legible de un segmento dañado copiándolo de nuevo."""
    tmp = path.with_suffix(".rescate.mp4")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-err_detect", "ignore_err",
             "-i", str(path), "-c", "copy", "-movflags", "+faststart", "-y", str(tmp)],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        tmp.unlink(missing_ok=True)
        return False
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, path)
    return True


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pgm(path: Path) -> bytes | None:
    """Píxeles de una imagen PGM binaria (P5), o None si está incompleta."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # Cabecera: "P5" ancho alto maxval, separados por espacios, y UN carácter
    # de espacio antes de los píxeles (que pueden empezar con bytes de espacio).
    tokens: list[bytes] = []
    pos = 0
    while len(tokens) < 4:
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        start = pos
        while pos < len(data) and not data[pos : pos + 1].isspace():
            pos += 1
        if start == pos:
            return None
        tokens.append(data[start:pos])
    pos += 1
    if tokens[0] != b"P5":
        return None
    try:
        width, height, maxval = (int(t) for t in tokens[1:])
    except ValueError:
        return None
    if maxval > 255 or width <= 0 or height <= 0:
        return None
    pixels = data[pos : pos + width * height]
    if len(pixels) < width * height:
        return None
    return pixels
