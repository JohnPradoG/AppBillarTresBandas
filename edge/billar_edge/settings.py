"""Ajustes que se cambian desde la pantalla y se guardan en la base de datos."""

from __future__ import annotations

import sqlite3

from .db import now_ms

DISPLAY = "pantalla"
DELAY_KEY = "retraso_segundos"
DELAY_CHOICES = (10, 20, 30, 45, 60)
DEFAULT_DELAY = 20


def get_delay(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE scope = ? AND key = ?", (DISPLAY, DELAY_KEY)
    ).fetchone()
    try:
        value = int(row["value"]) if row else DEFAULT_DELAY
    except ValueError:
        return DEFAULT_DELAY
    return value if value in DELAY_CHOICES else DEFAULT_DELAY


def set_delay(conn: sqlite3.Connection, seconds: int, updated_by: str | None = None) -> int:
    if seconds not in DELAY_CHOICES:
        raise ValueError(f"Retraso no permitido: {seconds}. Opciones: {DELAY_CHOICES}")
    conn.execute(
        """INSERT INTO settings (scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (scope, key) DO UPDATE SET value = excluded.value,
               updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
        (DISPLAY, DELAY_KEY, str(seconds), now_ms(), updated_by),
    )
    return seconds
