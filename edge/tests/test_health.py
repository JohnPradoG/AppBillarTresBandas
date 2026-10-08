import os
import time

from billar_edge import statefile
from billar_edge.db import now_ms
from billar_edge.health import (
    CAMERA_DISCONNECTED, NO_SIGNAL, RECORDING_OK, STOPPED, STORAGE_ERROR, Health,
)

from .conftest import make_config


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _recorder_state(cfg, state, updated_at=None):
    statefile.write(cfg.camera_run_dir("mesa1") / "recorder.json",
                    {"state": state, "updated_at": updated_at or now_ms(), "last_error": None})


def _snapshot(cfg, value):
    (cfg.camera_run_dir("mesa1") / "snapshot.pgm").write_bytes(b"P5\n32 18\n255\n" + bytes(value) * 576)


def _health(cfg, clock):
    return Health(cfg, clock=clock, free_pct=lambda _: 50.0)


def test_recording_when_file_grows_and_image_is_live(cfg, conn):
    clock = Clock()
    h = _health(cfg, clock)
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [120])
    report = h.check(conn)
    assert report["status"] == RECORDING_OK and report["label"] == "GRABANDO"
    assert (cfg.run_dir / "status.json").exists()


def test_black_image_becomes_no_signal_after_threshold(cfg, conn):
    clock = Clock()
    h = _health(cfg, clock)
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [0])
    assert h.check(conn)["status"] == RECORDING_OK
    clock.t += cfg.no_signal_seconds + 1
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [0])
    report = h.check(conn)
    assert report["status"] == NO_SIGNAL
    assert conn.execute("SELECT COUNT(*) FROM system_events WHERE type = 'sin_senal'").fetchone()[0] == 1


def test_frozen_image_becomes_no_signal(cfg, conn):
    clock = Clock()
    h = _health(cfg, clock)
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [77])
    h.check(conn)
    clock.t += cfg.no_signal_seconds + 1
    _snapshot(cfg, [77])
    assert h.check(conn)["status"] == NO_SIGNAL


def test_camera_disconnected_and_stale_recorder(cfg, conn):
    clock = Clock()
    h = _health(cfg, clock)
    _recorder_state(cfg, "camara_desconectada")
    assert h.check(conn)["status"] == CAMERA_DISCONNECTED
    _recorder_state(cfg, "grabando", updated_at=now_ms() - 60_000)
    report = h.check(conn)
    assert report["status"] == STOPPED
    assert "no responde" in report["cameras"][0]["detail"]


def test_storage_error_when_video_folder_is_missing(tmp_path, conn):
    cfg = make_config(tmp_path)
    clock = Clock()
    h = _health(cfg, clock)
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [120])
    for p in sorted(cfg.recordings_dir.rglob("*"), reverse=True):
        p.rmdir()
    cfg.recordings_dir.rmdir()
    report = h.check(conn)
    assert report["status"] == STORAGE_ERROR
    assert report["label"] == "ERROR DE ALMACENAMIENTO"


def test_low_storage_warns_once(cfg, conn):
    clock = Clock()
    h = Health(cfg, clock=clock, free_pct=lambda _: 10.0)
    _recorder_state(cfg, "grabando")
    _snapshot(cfg, [120])
    h.check(conn)
    h.check(conn)
    rows = conn.execute("SELECT * FROM system_events WHERE type = 'almacenamiento_bajo'").fetchall()
    assert len(rows) == 1
    assert rows[0]["message"].startswith("Advertencia de almacenamiento")
