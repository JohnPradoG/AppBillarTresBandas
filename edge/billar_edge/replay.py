"""Clips de repetición cortados de la grabación.

Al pulsar REPETICIÓN la pantalla envía el momento que estaba mostrando
(hora de pulsación − retraso). Aquí se arma un MP4 con 30 s antes y 15 s
después, copiando el video de los segmentos de 1 minuto sin recomprimir, en
memoria (/run). Tarda menos de un segundo y no toca los archivos grabados.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .ids import uuid7
from .media import parse_segment_start, probe_duration, probe_fps

BEFORE_MS = 30_000
AFTER_MS = 15_000
# El último fragmento del segmento que se está grabando todavía no se puede leer.
WRITE_MARGIN_MS = 1_500
RETRY_TRIM_MS = 2_000
# Los clips viejos se borran: la pantalla solo necesita el último.
KEEP_SECONDS = 15 * 60


class NoRecording(Exception):
    """No hay grabación para ese momento."""


@dataclass
class Clip:
    id: str
    path: Path
    start_ms: int     # hora real del primer fotograma del clip
    end_ms: int
    moment_ms: int    # la jugada que se estaba viendo al pulsar
    fps: float = 30.0


def replays_dir(cfg: Config) -> Path:
    return cfg.run_dir / "repeticiones"


def pieces(cfg: Config, camera_id: str, start_ms: int, end_ms: int) -> list[tuple[Path, float | None, float | None]]:
    """Segmentos que cubren [start_ms, end_ms) con el punto de entrada y salida dentro de cada uno."""
    files = sorted(
        (s, p) for p in cfg.camera_dir(camera_id).glob("*.mp4")
        if (s := parse_segment_start(p)) is not None
    )
    out = []
    for i, (seg_start, path) in enumerate(files):
        seg_end = files[i + 1][0] if i + 1 < len(files) else None
        if seg_start >= end_ms or (seg_end is not None and seg_end <= start_ms):
            continue
        inpoint = (start_ms - seg_start) / 1000 if start_ms > seg_start else None
        outpoint = (end_ms - seg_start) / 1000 if seg_end is None or end_ms < seg_end else None
        out.append((path, inpoint, outpoint))
    return out


def concat_list(items: list[tuple[Path, float | None, float | None]]) -> str:
    lines = ["ffconcat version 1.0"]
    for path, inpoint, outpoint in items:
        lines.append("file '" + str(path).replace("'", "'\\''") + "'")
        if inpoint is not None:
            lines.append(f"inpoint {inpoint:.3f}")
        if outpoint is not None:
            lines.append(f"outpoint {outpoint:.3f}")
    return "\n".join(lines) + "\n"


# Un clip pedido desde el historial no puede ser más largo que esto.
MAX_RANGE_MS = 3 * 60_000


def build(cfg: Config, camera_id: str, moment_ms: int, clock=time.time, sleep=time.sleep) -> Clip:
    """Repetición de una jugada: 30 s antes y 15 s después del momento visto."""
    return build_range(cfg, camera_id, moment_ms - BEFORE_MS, moment_ms + AFTER_MS, moment_ms, clock, sleep)


def build_range(cfg: Config, camera_id: str, start_ms: int, end_ms: int, moment_ms: int,
                clock=time.time, sleep=time.sleep) -> Clip:
    if not 0 < end_ms - start_ms <= MAX_RANGE_MS:
        raise ValueError("Rango de tiempo no válido.")
    # Con un retraso corto, los segundos de después pueden no estar grabados aún: esperar.
    wait = (end_ms + WRITE_MARGIN_MS - int(clock() * 1000)) / 1000
    if wait > 0:
        sleep(min(wait, AFTER_MS / 1000 + WRITE_MARGIN_MS / 1000))
    # Lo que aún no está escrito (minuto en curso del historial) no se pide:
    # el último fragmento del archivo actual está incompleto.
    end_ms = min(end_ms, int(clock() * 1000) - WRITE_MARGIN_MS)
    if end_ms <= start_ms:
        raise NoRecording("Ese momento todavía no está grabado.")
    out_dir = replays_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    _cleanup(out_dir)
    # Si el final cae en el fragmento que la cámara todavía está entregando,
    # FFmpeg puede fallar: se reintenta pidiendo un poco menos.
    for attempt in range(3):
        items = pieces(cfg, camera_id, start_ms, end_ms)
        if not items:
            raise NoRecording("No hay grabación de ese momento.")
        out, duration, error = _cut(cfg, out_dir, items)
        if duration:
            break
        near_now = int(clock() * 1000) - end_ms < 15_000
        if not near_now or end_ms - RETRY_TRIM_MS <= start_ms:
            break
        end_ms -= RETRY_TRIM_MS
    if not duration:
        raise NoRecording(f"No se pudo armar la repetición: {error[-200:]}")
    # La copia empieza en el fotograma clave anterior al punto pedido, así que
    # el inicio real se calcula desde el final, que sí es exacto.
    fps = probe_fps(cfg.ffprobe, out) or 30.0
    return Clip(out.stem, out, end_ms - int(duration * 1000), end_ms, moment_ms, fps)


def _cut(cfg: Config, out_dir: Path, items) -> tuple[Path, float | None, str]:
    clip_id = uuid7()
    list_path = out_dir / f"{clip_id}.txt"
    out = out_dir / f"{clip_id}.mp4"
    list_path.write_text(concat_list(items))
    try:
        result = subprocess.run(
            [cfg.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    finally:
        list_path.unlink(missing_ok=True)
    duration = probe_duration(cfg.ffprobe, out) if result.returncode == 0 and out.exists() else None
    if not duration:
        out.unlink(missing_ok=True)
    return out, duration, result.stderr.strip()


def clip_path(cfg: Config, name: str) -> Path | None:
    if not name.endswith(".mp4") or "/" in name or name.startswith("."):
        return None
    return replays_dir(cfg) / name


def _cleanup(out_dir: Path) -> None:
    limit = time.time() - KEEP_SECONDS
    for p in out_dir.iterdir():
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except FileNotFoundError:
            pass
