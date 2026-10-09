"""Fase 2: señal en vivo para la pantalla, ajustes y servidor local."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from billar_edge import settings, statefile
from billar_edge.config import CameraConfig
from billar_edge.db import now_ms
from billar_edge.live import LIVE, LiveStream
from billar_edge.media import live_command
from billar_edge.web import make_server

from .conftest import FakeCamera, free_port, make_config, needs_ffmpeg


def test_live_command_copies_video_into_short_hls_window(tmp_path):
    cam = CameraConfig(id="c", url="rtsp://admin:secreto@192.168.1.64:554/s")
    cmd = live_command("ffmpeg", cam, tmp_path)
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-f") + 1] == "hls"
    assert cmd[cmd.index("-hls_time") + 1] == "1"
    # Debe alcanzar para el retraso máximo de 60 s.
    assert int(cmd[cmd.index("-hls_list_size") + 1]) > max(settings.DELAY_CHOICES)
    assert cmd[-1] == str(tmp_path / "index.m3u8")


def test_delay_setting_defaults_to_20_and_only_accepts_the_offered_values(conn):
    assert settings.get_delay(conn) == 20
    settings.set_delay(conn, 45)
    assert settings.get_delay(conn) == 45
    with pytest.raises(ValueError):
        settings.set_delay(conn, 25)
    assert settings.get_delay(conn) == 45


@pytest.fixture
def server(cfg, conn):
    srv = make_server(cfg, host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _put(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_server_serves_screen_state_and_live_files(cfg, server):
    status, ctype, body = _get(server + "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b"REPETICI" in body and b"VANO SYSTEMS" in body
    assert _get(server + "/vendor/hls.light.min.js")[0] == 200

    state = json.loads(_get(server + "/api/state")[2])
    assert state["table_number"] == 1
    assert state["delay_seconds"] == 20
    assert state["idle_minutes"] == 20
    assert state["cameras"][0]["live_url"] == "/live/mesa1/index.m3u8"
    assert state["status"] is None  # el monitor de salud no está corriendo

    statefile.write(cfg.run_dir / "status.json", {"status": "grabando", "label": "GRABANDO", "ok": True,
                                                 "cameras": [], "updated_at": now_ms()})
    assert json.loads(_get(server + "/api/state")[2])["status"]["label"] == "GRABANDO"

    live = cfg.live_dir("mesa1")
    live.mkdir(parents=True)
    (live / "index.m3u8").write_text("#EXTM3U\n")
    status, ctype, _ = _get(server + "/live/mesa1/index.m3u8")
    assert status == 200 and ctype == "application/vnd.apple.mpegurl"


def test_server_changes_delay_and_rejects_bad_values(server):
    assert _put(server + "/api/settings/delay", {"seconds": 30}) == {"delay_seconds": 30}
    assert json.loads(_get(server + "/api/state")[2])["delay_seconds"] == 30
    with pytest.raises(urllib.error.HTTPError) as e:
        _put(server + "/api/settings/delay", {"seconds": 7})
    assert e.value.code == 400


@pytest.mark.parametrize("path", [
    "/live/otra/index.m3u8",          # cámara que no está configurada
    "/live/mesa1/..%2Fstatus.json",
    "/live/mesa1/status.json",        # solo la lista y los trozos de video
    "/../billar.db",
    "/vendor/../../db.py",
])
def test_server_does_not_leak_files_outside_its_folders(server, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server + path)
    assert e.value.code == 404


def _wait_for(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.25)
    return False


@needs_ffmpeg
def test_live_stream_starts_by_itself_and_recovers_after_camera_loss(tmp_path):
    cam = FakeCamera(free_port())
    cfg = make_config(tmp_path, url=cam.url)
    state = lambda: (statefile.read(cfg.camera_run_dir("mesa1") / "live.json") or {}).get("state")
    playlist = cfg.live_dir("mesa1") / "index.m3u8"
    cam.start()
    live = LiveStream(cfg, "mesa1")
    t = threading.Thread(target=live.run, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: state() == LIVE, 20), "la señal en vivo no arrancó sola"
        assert _wait_for(lambda: playlist.read_text().count(".m4s") >= 2, 10)

        cam.stop()
        assert _wait_for(lambda: state() != LIVE, 25), "no detectó la pérdida de la cámara"
        cam.start()
        assert _wait_for(lambda: state() == LIVE, 30), "no se recuperó al volver la cámara"
    finally:
        live.stop()
        t.join(timeout=15)
        cam.stop()
