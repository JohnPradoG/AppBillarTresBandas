"""Base de datos SQLite de la mesa.

Fase 1 crea las tablas de grabación (establishments, tables, cameras,
recordings, segments, settings, system_events). Las demás tablas del diseño
(jugadas en la 2; partidas, usuarios...) llegan en sus fases como nuevas migraciones.
Todas las horas se guardan en milisegundos UTC.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .ids import uuid7

MIGRATIONS: list[str] = [
    # 1: tablas de grabación
    """
    CREATE TABLE establishments (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE tables (
        id TEXT PRIMARY KEY,
        establishment_id TEXT NOT NULL REFERENCES establishments(id),
        number INTEGER NOT NULL,
        name TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE (establishment_id, number)
    );
    CREATE TABLE cameras (
        id TEXT PRIMARY KEY,
        table_id TEXT NOT NULL REFERENCES tables(id),
        role TEXT NOT NULL DEFAULT 'principal',
        url TEXT NOT NULL,
        status TEXT,
        last_signal_at INTEGER
    );
    CREATE TABLE recordings (
        id TEXT PRIMARY KEY,
        camera_id TEXT NOT NULL REFERENCES cameras(id),
        started_at INTEGER NOT NULL,
        ended_at INTEGER,
        end_reason TEXT
    );
    CREATE TABLE segments (
        id TEXT PRIMARY KEY,
        camera_id TEXT NOT NULL REFERENCES cameras(id),
        recording_id TEXT REFERENCES recordings(id),
        path TEXT NOT NULL UNIQUE,
        start_ts INTEGER NOT NULL,
        end_ts INTEGER,
        bytes INTEGER,
        sha256 TEXT,
        status TEXT NOT NULL CHECK (status IN ('cerrado', 'corrupto', 'borrado')),
        protected_refs INTEGER NOT NULL DEFAULT 0,
        indexed_at INTEGER NOT NULL,
        deleted_at INTEGER
    );
    CREATE INDEX segments_camera_time ON segments (camera_id, start_ts);
    CREATE INDEX segments_status_time ON segments (status, start_ts);
    CREATE TABLE settings (
        scope TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        updated_by TEXT,
        PRIMARY KEY (scope, key)
    );
    CREATE TABLE system_events (
        id TEXT PRIMARY KEY,
        ts INTEGER NOT NULL,
        level TEXT NOT NULL CHECK (level IN ('info', 'advertencia', 'error')),
        type TEXT NOT NULL,
        camera_id TEXT,
        message TEXT NOT NULL,
        data TEXT,
        notified INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX system_events_ts ON system_events (ts);
    """,
    # 2: jugadas (Fase 5). Cada REPETICIÓN queda anotada; GUARDAR JUGADA la
    # protege con una copia propia que la limpieza nunca borra.
    """
    CREATE TABLE plays (
        id TEXT PRIMARY KEY,
        camera_id TEXT NOT NULL REFERENCES cameras(id),
        table_number INTEGER NOT NULL,
        moment_ms INTEGER NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('repeticion', 'pantalla', 'historial')),
        game_number INTEGER,
        turn_player TEXT,
        player1 TEXT,
        player2 TEXT,
        score1 INTEGER,
        score2 INTEGER,
        innings INTEGER,
        created_at INTEGER NOT NULL,
        protected_at INTEGER,
        path TEXT,
        bytes INTEGER,
        sha256 TEXT,
        fps REAL
    );
    CREATE INDEX plays_moment ON plays (moment_ms);
    CREATE INDEX plays_protected ON plays (protected_at);
    """,
]


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # FULL: cada transacción confirmada sobrevive a un corte de energía.
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Otro proceso pudo migrar mientras esperábamos el bloqueo.
            if conn.execute("PRAGMA user_version").fetchone()[0] >= i:
                conn.execute("COMMIT")
                continue
            for statement in script.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version={i}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def ensure_camera(conn: sqlite3.Connection, establishment_name: str, camera) -> None:
    """Registra el local, la mesa y la cámara de la configuración si no existen."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM establishments ORDER BY created_at LIMIT 1").fetchone()
        if row:
            est_id = row["id"]
        else:
            est_id = uuid7()
            conn.execute(
                "INSERT INTO establishments (id, name, created_at) VALUES (?, ?, ?)",
                (est_id, establishment_name, now_ms()),
            )
        row = conn.execute(
            "SELECT id FROM tables WHERE establishment_id = ? AND number = ?",
            (est_id, camera.table_number),
        ).fetchone()
        if row:
            table_id = row["id"]
        else:
            table_id = uuid7()
            conn.execute(
                "INSERT INTO tables (id, establishment_id, number, name) VALUES (?, ?, ?, ?)",
                (table_id, est_id, camera.table_number, f"Mesa {camera.table_number}"),
            )
        conn.execute(
            """INSERT INTO cameras (id, table_id, role, url) VALUES (?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET table_id = excluded.table_id,
                   role = excluded.role, url = excluded.url""",
            (camera.id, table_id, camera.role, camera.url),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
