"""Limpieza automática: historial circular de N días.

Nunca borra segmentos protegidos (protected_refs > 0). Las jugadas guardadas
se borran a los `protected_days` días (plays.expire). Si el disco llega al
umbral crítico, borra los segmentos normales más antiguos aunque tengan menos
de N días, para que la grabación nunca se detenga por falta de espacio.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import events, plays, share
from .config import Config
from .db import now_ms

DAY_MS = 86_400_000


@dataclass
class RetentionResult:
    deleted_expired: int = 0
    deleted_for_space: int = 0
    freed_bytes: int = 0


def free_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free * 100.0 / usage.total


def _delete(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    path = Path(row["path"])
    try:
        size = path.stat().st_size
        path.unlink()
    except FileNotFoundError:
        size = 0
    conn.execute("UPDATE segments SET status = 'borrado', deleted_at = ? WHERE id = ?", (now_ms(), row["id"]))
    return size


def run(conn: sqlite3.Connection, cfg: Config, now: int | None = None, free_pct=free_percent) -> RetentionResult:
    now = now_ms() if now is None else now
    result = RetentionResult()
    cutoff = now - cfg.retention_days * DAY_MS
    expired = conn.execute(
        """SELECT id, path FROM segments
           WHERE status IN ('cerrado', 'corrupto') AND protected_refs = 0 AND start_ts < ?
           ORDER BY start_ts""",
        (cutoff,),
    ).fetchall()
    for row in expired:
        result.freed_bytes += _delete(conn, row)
        result.deleted_expired += 1
    share.cleanup(conn, cfg, now)
    count, freed = plays.expire(conn, cfg, now)
    if count:
        events.record(conn, "info", events.RETENTION,
                      f"Limpieza automática: {count} jugadas guardadas de más de {cfg.protected_days} días borradas",
                      data={"bytes": freed})
    if result.deleted_expired:
        events.record(conn, "info", events.RETENTION,
                      f"Limpieza automática: {result.deleted_expired} segmentos de más de {cfg.retention_days} días borrados",
                      data={"bytes": result.freed_bytes})

    # Disco casi lleno: liberar espacio con los segmentos normales más antiguos.
    if cfg.recordings_dir.exists() and free_pct(cfg.recordings_dir) < cfg.critical_free_percent:
        target = cfg.warn_free_percent
        while free_pct(cfg.recordings_dir) < target:
            batch = conn.execute(
                """SELECT id, path FROM segments
                   WHERE status IN ('cerrado', 'corrupto') AND protected_refs = 0
                   ORDER BY start_ts LIMIT 20"""
            ).fetchall()
            if not batch:
                break
            for row in batch:
                result.freed_bytes += _delete(conn, row)
                result.deleted_for_space += 1
        oldest = conn.execute(
            "SELECT MIN(start_ts) AS s FROM segments WHERE status = 'cerrado'"
        ).fetchone()["s"]
        days = (now - oldest) / DAY_MS if oldest else 0
        events.record(conn, "error", events.STORAGE_FULL,
                      f"Almacenamiento lleno: se borraron {result.deleted_for_space} segmentos antes de tiempo. "
                      f"El historial real es de {days:.1f} días",
                      data={"bytes": result.freed_bytes})
    return result
