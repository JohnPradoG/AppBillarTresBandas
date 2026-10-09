"""Fase 8: usuarios con PIN, roles, auditoría, ajustes y alertas."""

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from billar_edge import admin, auth, events, plays, settings, telegram
from billar_edge.config import Config
from billar_edge.web import make_server

from .test_plays import T0


@pytest.fixture
def api(cfg, conn):
    return admin.AdminApi(cfg)


def call(api, method, path, token=None, body=None):
    return api.handle(method, f"/api/admin/{path}", token, body or {})


def login(api, pin):
    status, data = call(api, "POST", "login", body={"pin": pin})
    assert status == 200, data
    return data["token"]


@pytest.fixture
def admin_token(api):
    status, data = call(api, "POST", "primer-uso", body={"name": "John", "pin": "1234"})
    assert status == 200, data
    return data["token"]


def test_pin_hash_and_rules():
    stored = auth.hash_pin("2468")
    assert stored.startswith("pbkdf2$") and "2468" not in stored
    assert auth.check_pin("2468", stored) and not auth.check_pin("2469", stored)
    for bad in ("123", "123456789", "12a4", ""):
        with pytest.raises(ValueError):
            auth.valid_pin(bad)


def test_first_use_creates_one_admin_only(api, admin_token):
    assert call(api, "GET", "inicio")[1] == {"has_users": True}
    status, _ = call(api, "POST", "primer-uso", body={"name": "Otro", "pin": "9999"})
    assert status == 403
    status, data = call(api, "GET", "estado", admin_token)
    assert status == 200 and data["user"]["role"] == "administrador"
    assert [u["name"] for u in data["users"]] == ["John"]


def test_wrong_pins_lock_the_screen(api, admin_token, conn):
    for _ in range(4):
        assert call(api, "POST", "login", body={"pin": "0000"})[0] == 403
    status, data = call(api, "POST", "login", body={"pin": "0000"})
    assert status == 403 and "bloqueada" in data["error"]
    # Ni el PIN correcto entra mientras está bloqueada.
    assert call(api, "POST", "login", body={"pin": "1234"})[0] == 403
    ev = conn.execute("SELECT level FROM system_events WHERE type = ?", (events.PIN_LOCKED,)).fetchone()
    assert ev["level"] == "advertencia"
    assert [p["id"] for p in admin.pending_alerts(conn) if p["type"] == events.PIN_LOCKED]


def test_sessions_expire_after_idle(cfg, conn):
    now = [T0]
    sessions = auth.Sessions(clock=lambda: now[0])
    auth.create_user(conn, "Ana", "operador", "5555")
    token, user = sessions.login(conn, "5555")
    assert user["permissions"] == ["estado"]
    now[0] += auth.SESSION_IDLE_MS - 1
    sessions.user(token)                      # usarla la renueva
    now[0] += auth.SESSION_IDLE_MS + 1
    with pytest.raises(auth.AuthError):
        sessions.user(token)


def test_roles_limit_what_each_user_can_do(api, admin_token, conn):
    status, _ = call(api, "POST", "usuarios", admin_token, {"name": "Pedro", "role": "operador", "pin": "1111"})
    assert status == 200
    call(api, "POST", "usuarios", admin_token, {"name": "Luz", "role": "encargado", "pin": "2222"})
    # Cada usuario necesita un PIN distinto porque se entra solo con el PIN.
    status, data = call(api, "POST", "usuarios", admin_token, {"name": "Copia", "role": "operador", "pin": "1111"})
    assert status == 400 and "PIN" in data["error"]

    op = login(api, "1111")
    status, data = call(api, "GET", "estado", op)
    assert status == 200 and "users" not in data and "audit" not in data
    assert call(api, "PUT", "ajustes", op, {"idle_minutes": 5})[0] == 403
    assert call(api, "POST", "jugadas/x/desproteger", op, {"reason": "Otro motivo"})[0] == 403
    assert call(api, "POST", "usuarios", op, {"name": "X", "role": "administrador", "pin": "7777"})[0] == 403

    enc = login(api, "2222")
    assert call(api, "PUT", "ajustes", enc, {"idle_minutes": 5})[0] == 200
    assert call(api, "POST", "jugadas/x/desproteger", enc, {"reason": "Otro motivo"})[0] == 403
    assert "audit" in call(api, "GET", "estado", enc)[1]
    assert call(api, "GET", "estado", "inventado")[0] == 401


def test_always_one_active_admin(api, admin_token, conn):
    me = conn.execute("SELECT id FROM users").fetchone()["id"]
    status, data = call(api, "PUT", f"usuarios/{me}", admin_token, {"role": "operador"})
    assert status == 400 and "administrador" in data["error"]
    assert call(api, "PUT", f"usuarios/{me}", admin_token, {"active": False})[0] == 400


def test_changed_user_is_logged_out(api, admin_token, conn):
    call(api, "POST", "usuarios", admin_token, {"name": "Pedro", "role": "operador", "pin": "1111"})
    op = login(api, "1111")
    pid = conn.execute("SELECT id FROM users WHERE name = 'Pedro'").fetchone()["id"]
    assert call(api, "PUT", f"usuarios/{pid}", admin_token, {"active": False})[0] == 200
    assert call(api, "GET", "estado", op)[0] == 401
    assert call(api, "POST", "login", body={"pin": "1111"})[0] == 403


def test_settings_override_the_config_and_are_audited(cfg, conn, api, admin_token):
    status, data = call(api, "PUT", "ajustes", admin_token,
                        {"establishment_name": "  Billar   El Taco ", "idle_minutes": 10, "shot_seconds": 50})
    assert status == 200
    shown = settings.display(conn, cfg)
    assert shown == {"establishment_name": "Billar El Taco", "idle_minutes": 10, "shot_seconds": 50}
    assert call(api, "PUT", "ajustes", admin_token, {"shot_seconds": 41})[0] == 400
    last = auth.recent_audit(conn)[0]
    assert last["user_name"] == "John" and "nombre del billar: Billar El Taco" in last["detail"]


def test_unprotect_needs_a_reason_and_leaves_a_record(cfg, conn, api, admin_token):
    cfg.plays_dir.mkdir(parents=True)
    pid = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla")
    f = cfg.plays_dir / f"{pid}.mp4"
    f.write_bytes(b"x" * 10)
    f.chmod(0o444)
    conn.execute("UPDATE plays SET protected_at = ?, path = ?, bytes = 10 WHERE id = ?", (T0, str(f), pid))
    assert call(api, "POST", f"jugadas/{pid}/desproteger", admin_token, {"reason": "porque sí"})[0] == 400
    status, _ = call(api, "POST", f"jugadas/{pid}/desproteger", admin_token, {"reason": "Liberar espacio"})
    assert status == 200
    assert not f.exists() and plays.get(conn, pid)["protected_at"] is None
    last = auth.recent_audit(conn)[0]
    assert last["action"] == "jugada_desprotegida" and "Motivo: Liberar espacio" in last["detail"]
    # Ya no está protegida: no se puede quitar dos veces.
    assert call(api, "POST", f"jugadas/{pid}/desproteger", admin_token, {"reason": "Otro motivo"})[0] == 400


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout):
        self.calls.append((req.full_url.rsplit("/", 1)[1], json.loads(req.data)))
        return io.BytesIO(json.dumps({"ok": True, "result": True}).encode())


def test_alerts_link_and_send(cfg, conn, admin_token):
    cfg = Config(**{**cfg.__dict__, "telegram_token": "t", "telegram_bot": "BillarBot"})
    api = admin.AdminApi(cfg)
    assert call(api, "POST", "alertas/vincular", "malo")[0] == 401
    # La sesión del fixture es de otra AdminApi: se entra de nuevo.
    token = login(api, "1234")
    events.record(conn, "advertencia", events.CAMERA_DISCONNECTED, "Cámara vieja")   # antes de vincular
    status, data = call(api, "POST", "alertas/vincular", token)
    assert status == 200 and data["url"].startswith("https://t.me/BillarBot?start=alertas_")
    code = data["url"].split("start=alertas_", 1)[1]

    fake = FakeTelegram()
    bot = telegram.Bot(cfg, opener=fake)
    bot.handle(77, "/start alertas_otro")
    assert "venci" in fake.calls[-1][1]["text"] and not settings.get_system(conn, settings.ALERT_CHAT)
    bot.handle(77, f"/start alertas_{code}")
    assert settings.get_system(conn, settings.ALERT_CHAT) == "77"
    fake.calls.clear()

    events.record(conn, "info", events.PLAY_SAVED, "No se avisa")
    events.record(conn, "error", events.STORAGE_FULL, "Disco lleno")
    events.record(conn, "info", events.STORAGE_OK, "Disco normal otra vez")
    assert bot.send_alerts() == 2
    texts = [c[1]["text"] for c in fake.calls]
    assert texts[0].startswith("🔴") and "Disco lleno" in texts[0] and "Cámara vieja" not in "".join(texts)
    assert texts[1].startswith("🟢")
    assert bot.send_alerts() == 0


def test_admin_api_over_http(cfg, conn):
    server = make_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/admin/"
    try:
        def req(method, path, body=None, token=None):
            r = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                       headers={"Content-Type": "application/json", **({"X-Sesion": token} if token else {})})
            try:
                with urllib.request.urlopen(r, timeout=10) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        assert req("GET", "inicio") == (200, {"has_users": False})
        status, data = req("POST", "primer-uso", {"pin": "4321"})
        assert status == 200 and data["user"]["name"] == "Administrador"
        assert req("GET", "estado")[0] == 401
        status, data = req("GET", "estado", token=data["token"])
        assert status == 200 and data["settings"]["shot_seconds"] == 40
        # La pantalla de la mesa usa el tiempo para tacar elegido aquí.
        with urllib.request.urlopen(base.replace("/api/admin/", "/api/state"), timeout=10) as res:
            assert json.loads(res.read())["shot_seconds"] == 40
    finally:
        server.shutdown()
        server.server_close()
