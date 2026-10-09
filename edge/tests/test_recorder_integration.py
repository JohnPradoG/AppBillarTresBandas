"""Prueba de punta a punta con una cámara simulada: grabar, perder la cámara y recuperarse solo."""

import threading
import time

from billar_edge import statefile
from billar_edge.db import connect
from billar_edge.recorder import RECORDING, Recorder

from .conftest import FakeCamera, free_port, make_config, needs_ffmpeg


def _wait_for(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def _state(cfg):
    return (statefile.read(cfg.camera_run_dir("mesa1") / "recorder.json") or {}).get("state")


@needs_ffmpeg
def test_records_segments_and_recovers_after_camera_loss(tmp_path):
    cam = FakeCamera(free_port())
    cfg = make_config(tmp_path, url=cam.url, segment_seconds=3, stall_seconds=3)
    cam.start()
    rec = Recorder(cfg, "mesa1")
    t = threading.Thread(target=rec.run, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: _state(cfg) == RECORDING, 15), "no empezó a grabar solo"
        assert (cfg.camera_run_dir("mesa1") / "snapshot.pgm").exists() or _wait_for(
            lambda: (cfg.camera_run_dir("mesa1") / "snapshot.pgm").exists(), 5)

        # Se cae la cámara: la grabación se detecta detenida y se reintenta sola.
        cam.stop()
        assert _wait_for(lambda: _state(cfg) != RECORDING, 15), "no detectó la pérdida de la cámara"

        # Vuelve la cámara: la grabación se reanuda sin intervención.
        cam.start()
        assert _wait_for(lambda: _state(cfg) == RECORDING, 30), "no se recuperó al volver la cámara"
        time.sleep(4)
    finally:
        rec.stop()
        t.join(timeout=30)
        cam.stop()

    conn = connect(cfg.db_path)
    segs = conn.execute("SELECT * FROM segments WHERE status = 'cerrado'").fetchall()
    assert len(segs) >= 2
    assert all(s["sha256"] and s["end_ts"] > s["start_ts"] for s in segs)
    types = [r["type"] for r in conn.execute("SELECT type FROM system_events ORDER BY ts")]
    assert types[0] == "camara_conectada"
    assert "grabacion_iniciada" in types
    assert "grabacion_detenida" in types
    assert "grabacion_reiniciada" in types
    recs = conn.execute("SELECT * FROM recordings").fetchall()
    assert len(recs) >= 2 and all(r["ended_at"] for r in recs)
