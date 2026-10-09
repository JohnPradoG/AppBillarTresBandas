from billar_edge import indexer, retention
from billar_edge.db import now_ms
from billar_edge.ids import uuid7
from billar_edge.retention import DAY_MS

from .conftest import make_clip, needs_ffmpeg


def _events(conn, type_):
    return conn.execute("SELECT * FROM system_events WHERE type = ?", (type_,)).fetchall()


@needs_ffmpeg
def test_index_registers_closed_segments_and_flags_corrupt_ones(cfg, conn):
    d = cfg.camera_dir("mesa1")
    make_clip(d / "20261008T163000Z.mp4")
    (d / "20261008T163100Z.mp4").write_bytes(b"esto no es video")
    make_clip(d / "20261008T163200Z.mp4")  # el que se está escribiendo

    n = indexer.index_camera(conn, cfg, "mesa1", active=d / "20261008T163200Z.mp4")

    assert n == 2
    rows = {r["path"].rsplit("/", 1)[1]: r for r in conn.execute("SELECT * FROM segments")}
    ok = rows["20261008T163000Z.mp4"]
    assert ok["status"] == "cerrado" and len(ok["sha256"]) == 64
    assert ok["end_ts"] - ok["start_ts"] == 2000
    assert rows["20261008T163100Z.mp4"]["status"] == "corrupto"
    assert "20261008T163200Z.mp4" not in rows
    assert len(_events(conn, "segmento_corrupto")) == 1
    # Una segunda pasada no duplica nada.
    assert indexer.index_camera(conn, cfg, "mesa1", active=None) == 1


@needs_ffmpeg
def test_reconcile_after_power_cut_closes_open_recording(cfg, conn):
    rec_id = uuid7()
    conn.execute("INSERT INTO recordings (id, camera_id, started_at) VALUES (?, 'mesa1', ?)", (rec_id, 1791477000000))
    make_clip(cfg.camera_dir("mesa1") / "20261008T163000Z.mp4")

    indexer.reconcile(conn, cfg, "mesa1")

    rec = conn.execute("SELECT * FROM recordings WHERE id = ?", (rec_id,)).fetchone()
    assert rec["ended_at"] is not None and rec["end_reason"] == "apagado_inesperado"
    seg = conn.execute("SELECT * FROM segments").fetchone()
    assert seg["recording_id"] == rec_id
    assert len(_events(conn, "apagado_inesperado")) == 1


def _add_segment(cfg, conn, start_ts, protected=0):
    path = cfg.camera_dir("mesa1") / f"{start_ts}.mp4"
    path.write_bytes(b"x" * 10)
    conn.execute(
        """INSERT INTO segments (id, camera_id, path, start_ts, end_ts, bytes, status, protected_refs, indexed_at)
           VALUES (?, 'mesa1', ?, ?, ?, 10, 'cerrado', ?, ?)""",
        (uuid7(), str(path), start_ts, start_ts + 60_000, protected, now_ms()),
    )
    return path


def test_retention_deletes_old_segments_but_never_protected_ones(cfg, conn):
    now = 2_000_000_000_000
    old = _add_segment(cfg, conn, now - 8 * DAY_MS)
    old_protected = _add_segment(cfg, conn, now - 9 * DAY_MS, protected=1)
    recent = _add_segment(cfg, conn, now - 6 * DAY_MS)

    result = retention.run(conn, cfg, now=now, free_pct=lambda _: 50.0)

    assert result.deleted_expired == 1
    assert not old.exists()
    assert old_protected.exists() and recent.exists()
    statuses = sorted(r["status"] for r in conn.execute("SELECT status FROM segments"))
    assert statuses == ["borrado", "cerrado", "cerrado"]


def test_retention_frees_space_early_when_disk_is_full(cfg, conn):
    now = 2_000_000_000_000
    a = _add_segment(cfg, conn, now - 3 * DAY_MS)
    b = _add_segment(cfg, conn, now - 2 * DAY_MS, protected=1)
    c = _add_segment(cfg, conn, now - 1 * DAY_MS)
    readings = iter([2.0, 2.0, 20.0])  # lleno, lleno, y tras borrar ya hay espacio

    result = retention.run(conn, cfg, now=now, free_pct=lambda _: next(readings, 20.0))

    assert result.deleted_for_space >= 1
    assert not a.exists()
    assert b.exists()
    assert len(_events(conn, "almacenamiento_lleno")) == 1
