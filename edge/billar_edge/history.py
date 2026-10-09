"""Historial: qué minutos de cada día están grabados.

La pantalla de historial muestra los últimos días; para el día elegido pinta
cada hora y cada minuto según cuánto tiene grabado, para que se vean los
huecos (cámara desconectada, equipo apagado). Las horas son las del equipo
(hora de Colombia); en la base de datos todo está en UTC.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import statefile
from .config import Config
from .media import parse_segment_start
from .recorder import RECORDING

MINUTE_MS = 60_000


def day_bounds(day: date) -> tuple[int, int]:
    """Inicio y fin del día en hora local, en ms UTC."""
    start = datetime.combine(day, datetime.min.time()).astimezone()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def available_days(cfg: Config, today: date) -> list[str]:
    return [(today - timedelta(days=i)).isoformat() for i in range(cfg.retention_days + 1)]


def recorded_ranges(conn: sqlite3.Connection, cfg: Config, camera_id: str,
                    start_ms: int, end_ms: int, now_ms: int) -> list[tuple[int, int]]:
    rows = conn.execute(
        """SELECT start_ts, end_ts FROM segments
           WHERE camera_id = ? AND status = 'cerrado' AND end_ts > ? AND start_ts < ?
           ORDER BY start_ts""",
        (camera_id, start_ms, end_ms),
    ).fetchall()
    ranges = [(r["start_ts"], r["end_ts"]) for r in rows]
    # El segmento que se está grabando todavía no está en el índice.
    rec = statefile.read(cfg.camera_run_dir(camera_id) / "recorder.json") or {}
    if rec.get("state") == RECORDING and rec.get("current_file"):
        seg_start = parse_segment_start(Path(rec["current_file"]))
        if seg_start is not None and seg_start < end_ms and now_ms > start_ms:
            ranges.append((seg_start, now_ms))
    return ranges


def day_coverage(conn: sqlite3.Connection, cfg: Config, camera_id: str, day: date, now_ms: int) -> dict:
    """Porcentaje grabado de cada minuto del día (1440 valores, de 0 a 100)."""
    start_ms, end_ms = day_bounds(day)
    total = (end_ms - start_ms) // MINUTE_MS
    covered = [0] * total
    for a, b in recorded_ranges(conn, cfg, camera_id, start_ms, end_ms, now_ms):
        a, b = max(a, start_ms), min(b, end_ms)
        m = (a - start_ms) // MINUTE_MS
        while a < b and m < total:
            minute_end = start_ms + (m + 1) * MINUTE_MS
            covered[m] += min(b, minute_end) - a
            a = minute_end
            m += 1
    return {
        "date": day.isoformat(),
        "start_ms": start_ms,
        "minutes": [min(100, round(ms * 100 / MINUTE_MS)) for ms in covered],
    }
