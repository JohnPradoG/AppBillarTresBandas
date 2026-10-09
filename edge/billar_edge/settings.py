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


# ---------- ajustes de la administración (Fase 8) ----------
# Se guardan en la base de datos y tienen prioridad sobre billar.toml.

SYSTEM = "sistema"
IDLE_CHOICES = (5, 10, 20, 30, 60)
SHOT_CHOICES = (30, 40, 50, 60)
DEFAULT_SHOT = 40


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE scope = ? AND key = ?", (SYSTEM, key)).fetchone()
    return row["value"] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str, updated_by: str | None) -> None:
    conn.execute(
        """INSERT INTO settings (scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (scope, key) DO UPDATE SET value = excluded.value,
               updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
        (SYSTEM, key, value, now_ms(), updated_by),
    )


def display(conn: sqlite3.Connection, cfg) -> dict:
    """Nombre del billar, minutos para el reposo y segundos para tacar vigentes."""
    def num(key, choices, default):
        try:
            v = int(_get(conn, key) or default)
        except ValueError:
            return default
        return v if v in choices else default
    return {
        "establishment_name": _get(conn, "nombre_billar") or cfg.establishment_name,
        "idle_minutes": num("reposo_minutos", IDLE_CHOICES, cfg.idle_minutes),
        "shot_seconds": num("tacar_segundos", SHOT_CHOICES, DEFAULT_SHOT),
    }


def update_display(conn: sqlite3.Connection, changes: dict, updated_by: str | None) -> dict:
    """Valida y guarda; devuelve solo lo que cambió (para la auditoría)."""
    done = {}
    if "establishment_name" in changes:
        name = " ".join(str(changes["establishment_name"] or "").split())[:40]
        if not name:
            raise ValueError("Falta el nombre del billar.")
        _set(conn, "nombre_billar", name, updated_by)
        done["establishment_name"] = name
    for field, key, choices in (("idle_minutes", "reposo_minutos", IDLE_CHOICES),
                                ("shot_seconds", "tacar_segundos", SHOT_CHOICES)):
        if field in changes:
            v = int(changes[field])
            if v not in choices:
                raise ValueError(f"Valor no permitido: {v}. Opciones: {choices}")
            _set(conn, key, str(v), updated_by)
            done[field] = v
    if "delay_seconds" in changes:
        done["delay_seconds"] = set_delay(conn, int(changes["delay_seconds"]), updated_by)
    return done


ALERT_CHAT = "alertas_telegram_chat"
ALERT_CODE = "alertas_codigo"


def get_system(conn: sqlite3.Connection, key: str) -> str | None:
    return _get(conn, key)


def set_system(conn: sqlite3.Connection, key: str, value: str, updated_by: str | None = None) -> None:
    _set(conn, key, value, updated_by)
