"""Registro de eventos del sistema, visible para el administrador."""

from __future__ import annotations

import json
import logging
import sqlite3

from .db import now_ms
from .ids import uuid7

log = logging.getLogger("billar")

# Tipos de evento (el texto que ve el administrador va en `message`).
CAMERA_CONNECTED = "camara_conectada"
CAMERA_DISCONNECTED = "camara_desconectada"
NO_SIGNAL = "sin_senal"
SIGNAL_OK = "senal_recuperada"
RECORDING_STARTED = "grabacion_iniciada"
RECORDING_STOPPED = "grabacion_detenida"
RECORDING_RESTARTED = "grabacion_reiniciada"
STORAGE_WARNING = "almacenamiento_bajo"
STORAGE_FULL = "almacenamiento_lleno"
STORAGE_ERROR = "error_almacenamiento"
STORAGE_OK = "almacenamiento_normal"
SEGMENT_CORRUPT = "segmento_corrupto"
SEGMENT_RECOVERED = "segmento_recuperado"
PLAY_SAVED = "jugada_guardada"
RETENTION = "limpieza"
SYSTEM_START = "sistema_iniciado"
UNCLEAN_SHUTDOWN = "apagado_inesperado"
CLOCK_JUMP = "hora_desajustada"

_LEVELS = {"info": logging.INFO, "advertencia": logging.WARNING, "error": logging.ERROR}


def record(
    conn: sqlite3.Connection,
    level: str,
    type_: str,
    message: str,
    camera_id: str | None = None,
    data: dict | None = None,
) -> None:
    log.log(_LEVELS.get(level, logging.INFO), "%s%s", f"[{camera_id}] " if camera_id else "", message)
    conn.execute(
        "INSERT INTO system_events (id, ts, level, type, camera_id, message, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid7(), now_ms(), level, type_, camera_id, message, json.dumps(data) if data else None),
    )


def recent(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
