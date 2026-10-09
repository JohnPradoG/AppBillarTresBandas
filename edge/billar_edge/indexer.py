"""Índice de segmentos: registra cada archivo cerrado y repara el índice tras un corte."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import events
from .config import Config
from .db import now_ms
from .ids import uuid7
from .media import parse_segment_start, probe_duration, salvage, sha256_file


def segment_files(cfg: Config, camera_id: str) -> list[Path]:
    d = cfg.camera_dir(camera_id)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.suffix == ".mp4" and parse_segment_start(p) is not None]
    return sorted(files, key=lambda p: p.name)


def _recording_for(conn: sqlite3.Connection, camera_id: str, start_ts: int) -> str | None:
    row = conn.execute(
        """SELECT id FROM recordings WHERE camera_id = ? AND started_at <= ?
           ORDER BY started_at DESC LIMIT 1""",
        (camera_id, start_ts + 60_000),
    ).fetchone()
    return row["id"] if row else None


def index_file(conn: sqlite3.Connection, cfg: Config, camera_id: str, path: Path) -> str:
    """Registra un segmento cerrado. Devuelve su estado: 'cerrado' o 'corrupto'."""
    start = parse_segment_start(path)
    assert start is not None
    duration = probe_duration(cfg.ffprobe, path)
    if duration is None:
        if path.stat().st_size > 0 and salvage(cfg.ffmpeg, path):
            duration = probe_duration(cfg.ffprobe, path)
            if duration is not None:
                events.record(conn, "advertencia", events.SEGMENT_RECOVERED,
                              f"Segmento {path.name} dañado: se recuperó su parte legible", camera_id)
    status = "cerrado" if duration is not None else "corrupto"
    end = start + int(duration * 1000) if duration is not None else None
    size = path.stat().st_size
    digest = sha256_file(path) if status == "cerrado" else None
    conn.execute(
        """INSERT INTO segments (id, camera_id, recording_id, path, start_ts, end_ts, bytes, sha256, status, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (path) DO NOTHING""",
        (uuid7(start), camera_id, _recording_for(conn, camera_id, start), str(path), start, end,
         size, digest, status, now_ms()),
    )
    if status == "corrupto":
        events.record(conn, "advertencia", events.SEGMENT_CORRUPT,
                      f"Segmento {path.name} dañado e ilegible", camera_id)
    return status


def index_camera(conn: sqlite3.Connection, cfg: Config, camera_id: str, active: Path | None) -> int:
    """Registra los segmentos nuevos y cerrados. `active` es el que se está escribiendo."""
    known = {r["path"] for r in conn.execute("SELECT path FROM segments WHERE camera_id = ?", (camera_id,))}
    count = 0
    for path in segment_files(cfg, camera_id):
        if active is not None and path == active:
            continue
        if str(path) in known:
            continue
        index_file(conn, cfg, camera_id, path)
        count += 1
    return count


def reconcile(conn: sqlite3.Connection, cfg: Config, camera_id: str) -> None:
    """Al arrancar: cierra las grabaciones que el corte dejó abiertas y pone el índice al día."""
    open_recs = conn.execute(
        "SELECT id, started_at FROM recordings WHERE camera_id = ? AND ended_at IS NULL", (camera_id,)
    ).fetchall()
    index_camera(conn, cfg, camera_id, active=None)
    for rec in open_recs:
        last = conn.execute(
            "SELECT MAX(end_ts) AS e FROM segments WHERE camera_id = ? AND start_ts >= ?",
            (camera_id, rec["started_at"]),
        ).fetchone()["e"]
        conn.execute(
            "UPDATE recordings SET ended_at = ?, end_reason = 'apagado_inesperado' WHERE id = ?",
            (last or rec["started_at"], rec["id"]),
        )
        events.record(conn, "advertencia", events.UNCLEAN_SHUTDOWN,
                      "La grabación anterior terminó sin apagado ordenado (corte de energía o reinicio)", camera_id)
    for row in conn.execute(
        "SELECT id, path FROM segments WHERE camera_id = ? AND status = 'cerrado'", (camera_id,)
    ).fetchall():
        if not Path(row["path"]).exists():
            conn.execute("UPDATE segments SET status = 'borrado', deleted_at = ? WHERE id = ?", (now_ms(), row["id"]))
            events.record(conn, "advertencia", events.STORAGE_ERROR,
                          f"Falta el archivo {Path(row['path']).name} en el disco", camera_id)
