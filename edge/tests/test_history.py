"""Fase 4: historial por día, hora y minuto."""

import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

import pytest

from billar_edge import history, statefile
from billar_edge.db import now_ms
from billar_edge.ids import uuid7
from billar_edge.web import make_server


def _segment(cfg, conn, start, end, status="cerrado"):
    conn.execute(
        """INSERT INTO segments (id, camera_id, path, start_ts, end_ts, bytes, status, indexed_at)
           VALUES (?, 'mesa1', ?, ?, ?, 10, ?, ?)""",
        (uuid7(), f"/v/{start}.mp4", start, end, status, now_ms()),
    )


def test_day_bounds_use_local_midnight():
    start, end = history.day_bounds(date(2026, 10, 8))
    assert datetime.fromtimestamp(start / 1000).strftime("%Y-%m-%d %H:%M") == "2026-10-08 00:00"
    assert end - start == 24 * 3600 * 1000


def test_coverage_marks_recorded_minutes_and_gaps(cfg, conn):
    day = date(2026, 10, 8)
    start, _ = history.day_bounds(day)
    m = history.MINUTE_MS
    _segment(cfg, conn, start + 600 * m, start + 602 * m)                 # 10:00 y 10:01 completos
    _segment(cfg, conn, start + 602 * m, start + 602 * m + 30_000)        # 10:02 a medias
    _segment(cfg, conn, start + 603 * m, start + 604 * m, status="corrupto")
    _segment(cfg, conn, start - 30_000, start + 30_000)                   # cruza la medianoche
    cov = history.day_coverage(conn, cfg, "mesa1", day, now_ms=start + 2000 * m)
    assert len(cov["minutes"]) == 1440 and cov["start_ms"] == start
    assert cov["minutes"][600:605] == [100, 100, 50, 0, 0]
    assert cov["minutes"][0] == 50
    assert sum(1 for v in cov["minutes"] if v) == 4


def test_coverage_includes_the_file_being_recorded(cfg, conn):
    day = date(2026, 10, 8)
    start, _ = history.day_bounds(day)
    seg_start = start + 700 * history.MINUTE_MS
    name = datetime.fromtimestamp(seg_start / 1000, timezone.utc).strftime("%Y%m%dT%H%M%SZ.mp4")
    statefile.write(cfg.camera_run_dir("mesa1") / "recorder.json",
                    {"state": "grabando", "current_file": f"/v/mesa1/{name}"})
    cov = history.day_coverage(conn, cfg, "mesa1", day, now_ms=seg_start + 45_000)
    assert cov["minutes"][700] == 75


def test_available_days_cover_retention(cfg):
    days = history.available_days(cfg, date(2026, 10, 9))
    assert days[0] == "2026-10-09" and days[-1] == "2026-10-02" and len(days) == 8


def test_server_history_endpoint(cfg, conn):
    srv = make_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/history", timeout=5) as r:
            data = json.loads(r.read())
        assert data["date"] == date.today().isoformat() and len(data["minutes"]) == 1440
        assert len(data["days"]) == cfg.retention_days + 1
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/history?date=2020-01-01", timeout=5)
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/history?date=basura", timeout=5)
        assert e.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
