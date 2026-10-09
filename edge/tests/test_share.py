"""Fase 7: compartir jugadas con marca de agua."""

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from billar_edge import plays, replay, share, telegram, web
from billar_edge.config import Config
from billar_edge.web import make_server

from .conftest import needs_ffmpeg
from .test_plays import META, T0
from .test_replay import _segments

NOW = T0 + 600_000


@pytest.fixture
def servers(cfg, conn, monkeypatch):
    monkeypatch.setattr(replay.time, "time", lambda: NOW / 1000)
    monkeypatch.setattr(web, "now_ms", lambda: NOW)
    monkeypatch.setattr(share, "now_ms", lambda: NOW)
    ui = make_server(cfg, host="127.0.0.1", port=0)
    lan = share.make_share_server(cfg, host="127.0.0.1", port=0)
    for s in (ui, lan):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{ui.server_address[1]}", f"http://127.0.0.1:{lan.server_address[1]}"
    for s in (ui, lan):
        s.shutdown()
        s.server_close()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def test_caption_has_table_date_and_score(cfg, conn):
    pid = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla", META)
    top, bottom = share.caption_lines(cfg, plays.get(conn, pid))
    assert top == f"{cfg.establishment_name} · Mesa 1"
    assert bottom.endswith("· Carlos 12 – 9 Ana")


def test_render_command_escapes_paths_and_adds_logo(cfg, tmp_path):
    logo = tmp_path / "lo:go.png"
    logo.write_bytes(b"x")
    cfg = Config(**{**cfg.__dict__, "share_logo": logo})
    cmd = share.render_command(cfg, tmp_path / "a.mp4", tmp_path / "b.mp4",
                               (tmp_path / "t:1.txt", tmp_path / "t2.txt", tmp_path / "t3.txt"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "t\\:1.txt" in graph and "overlay" in graph and str(logo) in cmd
    assert cmd[cmd.index("-maxrate") + 1] == "2500k"


def test_links(cfg):
    cfg = Config(**{**cfg.__dict__, "share_address": "192.168.1.50"})
    assert share.links(cfg, "abcdefgh") == {"web": "http://192.168.1.50:8081/c/abcdefgh", "telegram": None}
    cfg = Config(**{**cfg.__dict__, "telegram_token": "t", "telegram_bot": "MiBot"})
    assert share.links(cfg, "abcdefgh")["telegram"] == "https://t.me/MiBot?start=abcdefgh"


@needs_ffmpeg
def test_share_replay_and_download_on_the_lan(cfg, conn, servers):
    ui, lan = servers
    _segments(cfg)
    clip = _post(ui, "/api/replay", {"moment_ms": T0 + 35_000, "meta": META})
    out = _post(ui, "/api/share", {"play_id": clip["play_id"], "clip": clip["clip"]})
    assert out["web"].endswith(f"/c/{out['token']}") and out["bytes"] > 1000
    assert out["expires_at"] == NOW + cfg.share_hours * share.HOUR_MS
    # El mismo enlace se reutiliza mientras le quede tiempo.
    assert _post(ui, "/api/share", {"play_id": clip["play_id"]})["token"] == out["token"]

    with urllib.request.urlopen(f"{lan}/c/{out['token']}", timeout=5) as r:
        page = r.read().decode()
    assert "Descargar video" in page and "Carlos 12 – 9 Ana" in page and "VANO SYSTEMS" in page
    with urllib.request.urlopen(f"{lan}/c/{out['token']}.mp4?descargar=1", timeout=5) as r:
        data = r.read()
        assert r.headers["Content-Type"] == "video/mp4"
    assert data == (cfg.shares_dir / f"{out['token']}.mp4").read_bytes()
    assert conn.execute("SELECT downloads FROM shares").fetchone()[0] == 1
    # El video es 720p a 30 cps (las cámaras de prueba son 160x90: se agranda a 720 de alto).
    import subprocess
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=height", "-of", "csv=p=0", str(cfg.shares_dir / f"{out['token']}.mp4")],
                           capture_output=True, text=True)
    assert probe.stdout.strip() == "720"
    # Solo existen las rutas de los enlaces.
    for bad in ("/", "/api/state", "/c/../billar.db", "/c/abc"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(lan + bad, timeout=5)


@needs_ffmpeg
def test_share_from_history_records_the_range(cfg, conn, servers):
    ui, _ = servers
    _segments(cfg)
    out = _post(ui, "/api/share", {"moment_ms": T0 + 20_000, "start_ms": T0 + 10_000, "end_ms": T0 + 40_000})
    row = conn.execute("SELECT p.source, p.start_ms FROM shares s JOIN plays p ON p.id = s.play_id "
                       "WHERE s.token = ?", (out["token"],)).fetchone()
    assert tuple(row) == ("historial", T0 + 10_000)


def test_expired_links_are_gone_and_cleaned(cfg, conn, servers):
    _, lan = servers
    cfg.shares_dir.mkdir(parents=True)
    f = cfg.shares_dir / "vencido12345.mp4"
    f.write_bytes(b"x")
    pid = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla")
    conn.execute("INSERT INTO shares VALUES ('vencido12345', ?, ?, 1, 0, ?, 0, 0)", (pid, str(f), NOW - 1))
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{lan}/c/vencido12345", timeout=5)
    assert e.value.code == 410 and "venció" in e.value.read().decode()
    assert share.cleanup(conn, cfg, NOW) == 1 and not f.exists()


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout):
        method = req.full_url.rsplit("/", 1)[1]
        self.calls.append((method, req.data))
        return io.BytesIO(json.dumps({"ok": True, "result": True}).encode())


def test_telegram_sends_the_video_for_a_valid_link(cfg, conn, monkeypatch):
    monkeypatch.setattr(share, "now_ms", lambda: NOW)
    cfg = Config(**{**cfg.__dict__, "telegram_token": "t", "telegram_bot": "B"})
    cfg.shares_dir.mkdir(parents=True)
    f = cfg.shares_dir / "token123456.mp4"
    f.write_bytes(b"VIDEO")
    pid = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla", META)
    conn.execute("INSERT INTO shares VALUES ('token123456', ?, ?, 5, 0, ?, 0, 0)", (pid, str(f), 10 ** 15))
    fake = FakeTelegram()
    bot = telegram.Bot(cfg, opener=fake)
    bot.handle(42, "/start token123456")
    methods = [m for m, _ in fake.calls]
    assert methods == ["sendChatAction", "sendVideo"]
    body = fake.calls[1][1]
    assert b"VIDEO" in body and b'name="chat_id"\r\n\r\n42' in body and "Carlos 12 – 9 Ana".encode() in body
    assert conn.execute("SELECT telegram_sends FROM shares").fetchone()[0] == 1
    fake.calls.clear()
    bot.handle(42, "/start noexiste123")
    assert [m for m, _ in fake.calls] == ["sendMessage"] and "venci".encode() in fake.calls[0][1]
