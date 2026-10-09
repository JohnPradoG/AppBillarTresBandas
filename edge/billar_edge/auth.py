"""Usuarios, roles, PIN y registro de auditoría (Fase 8).

En la mesa los jugadores no inician sesión: marcador, repetición, guardar y
compartir quedan libres. Lo delicado (ajustes, usuarios, quitar la
protección de una jugada, alertas) pide el PIN de un usuario y queda en el
registro de auditoría con su nombre. El PIN se guarda con PBKDF2-SHA256 y
sal propia; tras 5 intentos fallidos la pantalla espera 5 minutos.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading

from .db import now_ms
from .ids import uuid7

ROLES = ("administrador", "encargado", "operador")
PERMISSIONS = {
    "administrador": {"estado", "ajustes", "usuarios", "desproteger", "auditoria", "alertas"},
    "encargado": {"estado", "ajustes", "auditoria"},
    "operador": {"estado"},
}
ITERATIONS = 120_000
MAX_FAILS = 5
LOCK_MS = 5 * 60_000
SESSION_IDLE_MS = 5 * 60_000
NAME_MAX = 30


class AuthError(Exception):
    pass


class Denied(AuthError):
    pass


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def check_pin(pin: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


def valid_pin(pin) -> str:
    pin = str(pin or "")
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("El PIN debe tener de 4 a 8 números.")
    return pin


def clean_name(name) -> str:
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise ValueError("Falta el nombre.")
    return name


def has_users(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def find_by_pin(conn: sqlite3.Connection, pin: str, exclude: str | None = None) -> sqlite3.Row | None:
    for row in conn.execute("SELECT * FROM users WHERE active = 1"):
        if row["id"] != exclude and check_pin(pin, row["pin_hash"]):
            return row
    return None


def create_user(conn: sqlite3.Connection, name, role: str, pin) -> sqlite3.Row:
    name, pin = clean_name(name), valid_pin(pin)
    if role not in ROLES:
        raise ValueError("Rol no válido.")
    # Se entra solo con el PIN: cada usuario necesita uno distinto.
    if find_by_pin(conn, pin):
        raise ValueError("Ese PIN ya lo usa otro usuario.")
    uid, now = uuid7(), now_ms()
    conn.execute("INSERT INTO users (id, name, role, pin_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (uid, name, role, hash_pin(pin), now, now))
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def update_user(conn: sqlite3.Connection, user_id: str, changes: dict) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError("Ese usuario no existe.")
    name = clean_name(changes["name"]) if "name" in changes else row["name"]
    role = changes.get("role", row["role"])
    if role not in ROLES:
        raise ValueError("Rol no válido.")
    active = int(bool(changes["active"])) if "active" in changes else row["active"]
    pin_hash = row["pin_hash"]
    if changes.get("pin"):
        pin = valid_pin(changes["pin"])
        if find_by_pin(conn, pin, exclude=user_id):
            raise ValueError("Ese PIN ya lo usa otro usuario.")
        pin_hash = hash_pin(pin)
    # Siempre debe quedar al menos un administrador activo.
    if row["role"] == "administrador" and row["active"] and (role != "administrador" or not active):
        others = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'administrador' AND active = 1 AND id != ?",
                              (user_id,)).fetchone()[0]
        if others == 0:
            raise ValueError("Debe quedar al menos un administrador activo.")
    conn.execute("UPDATE users SET name = ?, role = ?, active = ?, pin_hash = ?, updated_at = ? WHERE id = ?",
                 (name, role, active, pin_hash, now_ms(), user_id))
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def public_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "role": row["role"], "active": bool(row["active"])}


def audit(conn: sqlite3.Connection, user: dict | None, action: str, detail: str, data: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (id, ts, user_id, user_name, action, detail, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid7(), now_ms(), user and user["id"], user and user["name"], action, detail,
         json.dumps(data, ensure_ascii=False) if data else None),
    )


def recent_audit(conn: sqlite3.Connection, limit: int = 60) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT ts, user_name, action, detail FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))]


class Sessions:
    """Sesiones de la pantalla, en memoria: se cierran solas a los 5 min sin uso."""

    def __init__(self, clock=now_ms):
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.fails = 0
        self.locked_until = 0

    def login(self, conn: sqlite3.Connection, pin: str) -> tuple[str, dict]:
        with self.lock:
            now = self.clock()
            if now < self.locked_until:
                wait = (self.locked_until - now) // 1000 + 1
                raise Denied(f"Demasiados intentos. Espera {wait} s.")
            row = find_by_pin(conn, str(pin or ""))
            if row is None:
                self.fails += 1
                if self.fails >= MAX_FAILS:
                    self.fails = 0
                    self.locked_until = now + LOCK_MS
                    raise Denied("PIN incorrecto. Pantalla bloqueada 5 minutos.")
                raise Denied("PIN incorrecto.")
            self.fails = 0
            token = secrets.token_urlsafe(24)
            user = {**public_user(row), "permissions": sorted(PERMISSIONS[row["role"]])}
            self.sessions[token] = {"user": user, "seen": now}
            return token, user

    def user(self, token: str | None, permission: str | None = None) -> dict:
        with self.lock:
            now = self.clock()
            s = self.sessions.get(token or "")
            if s is None or now - s["seen"] > SESSION_IDLE_MS:
                self.sessions.pop(token or "", None)
                raise AuthError("La sesión se cerró. Vuelve a poner el PIN.")
            s["seen"] = now
            if permission and permission not in s["user"]["permissions"]:
                raise Denied("Tu usuario no tiene permiso para esto.")
            return s["user"]

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)

    def drop_user(self, user_id: str) -> None:
        """Cierra las sesiones de un usuario cambiado o desactivado."""
        with self.lock:
            for t in [t for t, s in self.sessions.items() if s["user"]["id"] == user_id]:
                del self.sessions[t]
