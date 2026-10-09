"""API de la pantalla ADMINISTRACIÓN (Fase 8), siempre con sesión de PIN.

Rutas (todas bajo /api/admin/, con la cabecera X-Sesion salvo login y
primer uso):
  GET  estado                     resumen: grabación, disco, eventos, ajustes...
  POST primer-uso {name, pin}     crea el primer administrador (solo sin usuarios)
  POST login {pin} / logout
  PUT  ajustes {...}              nombre del billar, reposo, tacar, retraso
  POST usuarios {name, role, pin} / PUT usuarios/<id> {...}
  POST jugadas/<id>/desproteger {reason}
  POST alertas/vincular           enlace de Telegram para recibir alertas
"""

from __future__ import annotations

import secrets
import shutil
import sqlite3
from http import HTTPStatus

from . import auth, events, plays, settings, statefile
from .config import Config
from .db import connect, now_ms
from .health import STALE_STATE_SECONDS

UNPROTECT_REASONS = ("Pedido del cliente", "Jugada equivocada", "Liberar espacio", "Otro motivo")
ALERT_LINK_MS = 15 * 60_000

# Qué se manda al dueño por Telegram: problemas y su solución.
ALERT_TYPES_OK = (events.CAMERA_CONNECTED, events.SIGNAL_OK, events.STORAGE_OK)


class AdminApi:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sessions = auth.Sessions()

    def handle(self, method: str, path: str, token: str | None, body: dict) -> tuple[int, dict]:
        parts = path.strip("/").split("/")[2:]   # sin "api/admin"
        conn = connect(self.cfg.db_path)
        try:
            return HTTPStatus.OK, self._route(conn, method, parts, token, body)
        except auth.Denied as e:
            return HTTPStatus.FORBIDDEN, {"error": str(e)}
        except auth.AuthError as e:
            return HTTPStatus.UNAUTHORIZED, {"error": str(e), "needs_pin": True}
        except LookupError as e:
            return HTTPStatus.NOT_FOUND, {"error": str(e)}
        except (ValueError, TypeError, plays.PlayError) as e:
            return HTTPStatus.BAD_REQUEST, {"error": str(e)}
        finally:
            conn.close()

    def _route(self, conn, method, parts, token, body):
        if method == "GET" and parts == ["inicio"]:
            # Sin sesión: solo dice si ya hay usuarios (para pedir PIN o crear el primero).
            return {"has_users": auth.has_users(conn)}
        if method == "POST" and parts == ["primer-uso"]:
            return self.first_admin(conn, body)
        if method == "POST" and parts == ["login"]:
            tok, user = self.sessions.login(conn, body.get("pin"))
            auth.audit(conn, user, "inicio_sesion", f"{user['name']} entró a la administración")
            return {"token": tok, "user": user}
        if method == "POST" and parts == ["logout"]:
            self.sessions.logout(token)
            return {"ok": True}
        if method == "GET" and parts == ["estado"]:
            return self.summary(conn, self.sessions.user(token, "estado"))
        if method == "PUT" and parts == ["ajustes"]:
            user = self.sessions.user(token, "ajustes")
            done = settings.update_display(conn, body, user["name"])
            if done:
                auth.audit(conn, user, "ajustes", "Cambió " + ", ".join(f"{k} = {v}" for k, v in done.items()), done)
            return {"changed": done, "settings": self._settings(conn)}
        if parts[:1] == ["usuarios"]:
            user = self.sessions.user(token, "usuarios")
            if method == "POST" and len(parts) == 1:
                row = auth.create_user(conn, body.get("name"), body.get("role"), body.get("pin"))
                auth.audit(conn, user, "usuario_creado", f"Creó a {row['name']} ({row['role']})")
                return {"user": auth.public_user(row), "users": self._users(conn)}
            if method == "PUT" and len(parts) == 2:
                changes = {k: body[k] for k in ("name", "role", "pin", "active") if k in body}
                row = auth.update_user(conn, parts[1], changes)
                self.sessions.drop_user(row["id"]) if row["id"] != user["id"] else None
                what = ", ".join("PIN" if k == "pin" else k for k in changes)
                auth.audit(conn, user, "usuario_modificado", f"Modificó a {row['name']} ({what})")
                return {"user": auth.public_user(row), "users": self._users(conn)}
        if method == "POST" and len(parts) == 3 and parts[0] == "jugadas" and parts[2] == "desproteger":
            user = self.sessions.user(token, "desproteger")
            reason = str(body.get("reason") or "")
            if reason not in UNPROTECT_REASONS:
                raise ValueError("Elige el motivo.")
            row = plays.unprotect(conn, self.cfg, parts[1])
            auth.audit(conn, user, "jugada_desprotegida",
                       f"Quitó la protección de la jugada de la mesa {row['table_number']} "
                       f"({plays.local_time(row['moment_ms'])}). Motivo: {reason}",
                       {"jugada": parts[1], "motivo": reason})
            return {"ok": True}
        if method == "POST" and parts == ["alertas", "vincular"]:
            user = self.sessions.user(token, "alertas")
            if not (self.cfg.telegram_token and self.cfg.telegram_bot):
                raise ValueError("Primero hay que configurar el bot de Telegram del billar.")
            code = secrets.token_urlsafe(9)
            settings.set_system(conn, settings.ALERT_CODE, f"{code}|{now_ms() + ALERT_LINK_MS}", user["name"])
            return {"url": f"https://t.me/{self.cfg.telegram_bot}?start=alertas_{code}"}
        raise LookupError("Ruta de administración desconocida.")

    def first_admin(self, conn: sqlite3.Connection, body: dict) -> dict:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if auth.has_users(conn):
                raise auth.Denied("Ya hay usuarios: entra con tu PIN.")
            row = auth.create_user(conn, body.get("name") or "Administrador", "administrador", body.get("pin"))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        user = auth.public_user(row)
        auth.audit(conn, user, "usuario_creado", f"Primer uso: se creó el administrador {row['name']}")
        tok, user = self.sessions.login(conn, body.get("pin"))
        return {"token": tok, "user": user}

    def _settings(self, conn) -> dict:
        return {
            **settings.display(conn, self.cfg),
            "delay_seconds": settings.get_delay(conn),
            "choices": {
                "idle_minutes": list(settings.IDLE_CHOICES),
                "shot_seconds": list(settings.SHOT_CHOICES),
                "delay_seconds": list(settings.DELAY_CHOICES),
            },
        }

    def _users(self, conn) -> list[dict]:
        return [auth.public_user(r) for r in conn.execute("SELECT * FROM users ORDER BY active DESC, name")]

    def summary(self, conn: sqlite3.Connection, user: dict) -> dict:
        status = statefile.read(self.cfg.run_dir / "status.json")
        if status and now_ms() - status.get("updated_at", 0) > STALE_STATE_SECONDS * 1000:
            status = None
        try:
            du = shutil.disk_usage(self.cfg.recordings_dir)
            disk = {"total": du.total, "free": du.free}
        except OSError:
            disk = None
        oldest = conn.execute("SELECT MIN(start_ts) FROM segments WHERE status = 'cerrado'").fetchone()[0]
        protected = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM plays WHERE protected_at IS NOT NULL").fetchone()
        out = {
            "user": user,
            "status": status,
            "disk": disk,
            "oldest_recording_ms": oldest,
            "protected_plays": {"count": protected[0], "bytes": protected[1]},
            "events": [dict(r) for r in conn.execute(
                "SELECT ts, level, message FROM system_events ORDER BY ts DESC LIMIT 60")],
            "settings": self._settings(conn),
            "retention_days": self.cfg.retention_days,
            "protected_days": self.cfg.protected_days,
            "telegram": {
                "bot": self.cfg.telegram_bot if self.cfg.telegram_token else None,
                "alerts_linked": bool(settings.get_system(conn, settings.ALERT_CHAT)),
            },
            "unprotect_reasons": list(UNPROTECT_REASONS),
            "roles": list(auth.ROLES),
        }
        if "auditoria" in user["permissions"]:
            out["audit"] = auth.recent_audit(conn)
        if "usuarios" in user["permissions"]:
            out["users"] = self._users(conn)
        return out


def pending_alerts(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(ALERT_TYPES_OK))
    return conn.execute(
        f"""SELECT id, ts, level, type, message FROM system_events
            WHERE notified = 0 AND (level IN ('advertencia', 'error') OR type IN ({marks}))
            ORDER BY ts LIMIT ?""",
        (*ALERT_TYPES_OK, limit),
    ).fetchall()
