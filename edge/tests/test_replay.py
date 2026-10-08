"""Fase 3: clips de repetición cortados de la grabación."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from billar_edge import replay
from billar_edge.media import parse_segment_start
from billar_edge.web import make_server

from .conftest import make_clip, needs_ffmpeg

T0 = parse_segment_start(Path("20261008T163000Z.mp4"))


def _segments(cfg, n=3, seconds=20):
    """n segmentos seguidos de `seconds` s, como los que deja el grabador."""
    d = cfg.camera_dir("mesa1")
    for i in range(n):
        ts = T0 + i * seconds * 1000
        start = datetime.fromtimestamp(ts / 1000, timezone.utc)
        make_clip(d / start.strftime("%Y%m%dT%H%M%SZ.mp4"), seconds=seconds)


def test_pieces_pick_the_segments_and_cut_points(cfg):
    d = cfg.camera_dir("mesa1")
    for name in ("20261008T163000Z.mp4", "20261008T163100Z.mp4", "20261008T163200Z.mp4"):
        (d / name).write_bytes(b"x")
    moment = T0 + 75_000  # 16:31:15
    items = replay.pieces(cfg, "mesa1", moment - 30_000, moment + 15_000)
    assert [(p.name, i, o) for p, i, o in items] == [
        ("20261008T163000Z.mp4", 45.0, None),
        ("20261008T163100Z.mp4", None, 30.0),
    ]
    # El último segmento (el que se está grabando) no tiene fin conocido.
    items = replay.pieces(cfg, "mesa1", T0 + 130_000, T0 + 175_000)
    assert [(p.name, i, o) for p, i, o in items] == [("20261008T163200Z.mp4", 10.0, 55.0)]
    assert replay.pieces(cfg, "mesa1", T0 - 90_000, T0 - 45_000) == []


def test_concat_list_quotes_paths():
    text = replay.concat_list([(Path("/v/a'b.mp4"), 1.5, None), (Path("/v/c.mp4"), None, 2.0)])
    assert "file '/v/a'\\''b.mp4'" in text and "inpoint 1.500" in text and "outpoint 2.000" in text


@needs_ffmpeg
def test_build_cuts_30_before_and_15_after_across_segments(cfg):
    _segments(cfg)
    moment = T0 + 35_000
    clip = replay.build(cfg, "mesa1", moment, clock=lambda: (T0 + 600_000) / 1000)
    assert clip.path.exists() and clip.path.parent == cfg.run_dir / "repeticiones"
    assert clip.end_ms == moment + 15_000
    # Empieza en el fotograma clave anterior a −30 s (clips de prueba: 1 por segundo).
    assert moment - 31_000 <= clip.start_ms <= moment - 30_000
    assert clip.moment_ms == moment


@needs_ffmpeg
def test_build_waits_until_the_seconds_after_are_recorded(cfg):
    _segments(cfg)
    moment = T0 + 35_000
    waited = []
    replay.build(cfg, "mesa1", moment, clock=lambda: (moment + 5_000) / 1000, sleep=waited.append)
    assert waited and 11 <= waited[0] <= 12


def test_build_without_recording_raises(cfg):
    with pytest.raises(replay.NoRecording):
        replay.build(cfg, "mesa1", T0, clock=lambda: (T0 + 600_000) / 1000)


@needs_ffmpeg
def test_server_builds_and_serves_replay_with_ranges(cfg, conn, monkeypatch):
    _segments(cfg)
    monkeypatch.setattr(replay.time, "time", lambda: (T0 + 600_000) / 1000)
    srv = make_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/api/replay", data=json.dumps({"moment_ms": T0 + 35_000}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            clip = json.loads(r.read())
        assert clip["url"].startswith("/repeticion/") and clip["moment_ms"] == T0 + 35_000
        req = urllib.request.Request(base + clip["url"], headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 206 and len(r.read()) == 100
            assert r.headers["Content-Range"].startswith("bytes 0-99/")
            assert r.headers["Content-Type"] == "video/mp4"

        req = urllib.request.Request(base + "/api/replay", data=json.dumps({"moment_ms": T0 - 900_000}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/repeticion/..%2Fstatus.json", timeout=5)
    finally:
        srv.shutdown()
        srv.server_close()
