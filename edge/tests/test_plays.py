"""Fase 5: GUARDAR JUGADA y la sección JUGADAS."""

import dataclasses
import json
import os
import stat
import threading
import urllib.error
import urllib.request

import pytest

from billar_edge import plays, replay, web
from billar_edge.retention import DAY_MS
from billar_edge.web import make_server

from .conftest import needs_ffmpeg
from .test_replay import T0, _segments

META = {"game_number": 3, "turn_player": "Carlos", "player1": "Carlos", "player2": "Ana",
        "score1": 12, "score2": 9, "innings": 14}


def _post(base, path, body, timeout=30):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture
def server(cfg, conn, monkeypatch):
    now = T0 + 600_000
    monkeypatch.setattr(replay.time, "time", lambda: now / 1000)
    monkeypatch.setattr(web, "now_ms", lambda: now)
    srv = make_server(cfg, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_clean_meta_keeps_only_known_fields():
    meta = plays.clean_meta({"turn_player": "x" * 100, "score1": "7", "score2": "no", "otro": 1})
    assert meta["turn_player"] == "x" * 60 and meta["score1"] == 7 and meta["score2"] is None
    assert "otro" not in meta


def test_unprotected_plays_hide_with_the_recording(cfg, conn):
    old = plays.record(conn, cfg, "mesa1", T0, T0 - 30_000, T0 + 15_000, "repeticion", META)
    now = T0 + cfg.retention_days * DAY_MS
    assert plays.search(conn, cfg, now) == []
    conn.execute("UPDATE plays SET protected_at = 1, path = '/x' WHERE id = ?", (old,))
    [row] = plays.search(conn, cfg, now)
    assert row["protected"] and row["expires_ms"] == 1 + cfg.protected_days * DAY_MS and "path" not in row


def test_search_filters(cfg, conn):
    a = plays.record(conn, cfg, "mesa1", T0, T0 - 30_000, T0 + 15_000, "repeticion", META)
    plays.record(conn, cfg, "mesa1", T0 + 3_600_000, T0, T0 + 1, "repeticion",
                 {**META, "turn_player": "Ana", "game_number": 4})
    now = T0 + 7_200_000
    assert len(plays.search(conn, cfg, now)) == 2
    assert [r["id"] for r in plays.search(conn, cfg, now, player="Carlos")] == [a]
    assert [r["id"] for r in plays.search(conn, cfg, now, game=3)] == [a]
    assert plays.search(conn, cfg, now, only_protected=True) == []
    opts = plays.options(conn, cfg, now)
    assert opts["players"] == ["Ana", "Carlos"] and [g["number"] for g in opts["games"]] == [4, 3] and len(opts["days"]) >= 1


@needs_ffmpeg
def test_replay_is_listed_and_save_protects_a_read_only_copy(cfg, conn, server):
    _segments(cfg)
    clip = _post(server, "/api/replay", {"moment_ms": T0 + 35_000, "meta": META})
    assert clip["play_id"]
    listed = _get(server, "/api/plays")["plays"]
    assert [p["id"] for p in listed] == [clip["play_id"]]
    assert listed[0]["turn_player"] == "Carlos" and not listed[0]["protected"]

    saved = _post(server, "/api/plays", {"play_id": clip["play_id"], "clip": clip["clip"]})
    assert saved["protected"] and saved["url"] == f"/jugada/{clip['play_id']}.mp4"
    path = plays.file_path(conn, cfg, clip["play_id"])
    assert path.parent.parent == cfg.plays_dir
    assert not os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert path.read_bytes() == (cfg.run_dir / "repeticiones" / clip["clip"]).read_bytes()
    # Guardar otra vez no hace otra copia.
    _post(server, "/api/plays", {"play_id": clip["play_id"], "clip": clip["clip"]})
    assert len(list(cfg.plays_dir.rglob("*.mp4"))) == 1

    with urllib.request.urlopen(server + saved["url"], timeout=5) as r:
        assert r.headers["Content-Type"] == "video/mp4" and r.read() == path.read_bytes()
    only = _get(server, "/api/plays?protected=1")
    assert len(only["plays"]) == 1 and only["usage_bytes"] == path.stat().st_size
    assert plays.verify(conn) == []
    events = [r["type"] for r in conn.execute("SELECT type FROM system_events")]
    assert "jugada_guardada" in events


@needs_ffmpeg
def test_save_from_the_game_and_from_history(cfg, conn, server):
    _segments(cfg)
    # Desde la partida: −30/+15 s del momento en pantalla.
    a = _post(server, "/api/plays", {"moment_ms": T0 + 35_000, "source": "pantalla", "meta": META})
    assert a["protected"] and a["source"] == "pantalla" and a["end_ms"] == T0 + 50_000
    # Desde el historial: el tramo que se está viendo; si el clip ya no está, se corta de nuevo.
    b = _post(server, "/api/plays", {"moment_ms": T0 + 20_000, "start_ms": T0 + 10_000,
                                     "end_ms": T0 + 40_000, "source": "historial", "clip": "nada.mp4"})
    assert b["protected"] and b["start_ms"] == T0 + 10_000 and b["turn_player"] is None
    assert len(list(cfg.plays_dir.rglob("*.mp4"))) == 2


@needs_ffmpeg
def test_saved_play_survives_losing_the_recording(cfg, conn, server):
    _segments(cfg)
    a = _post(server, "/api/plays", {"moment_ms": T0 + 35_000, "source": "pantalla"})
    for p in cfg.camera_dir("mesa1").iterdir():
        p.unlink()
    with urllib.request.urlopen(server + a["url"], timeout=5) as r:
        assert len(r.read()) > 1000
    # Una jugada sin proteger cuya grabación ya no existe no se puede guardar.
    pid = plays.record(conn, cfg, "mesa1", T0 + 35_000, T0 + 5_000, T0 + 50_000, "repeticion")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, "/api/plays", {"play_id": pid})
    assert e.value.code == 409


def test_verify_reports_missing_and_changed_files(cfg, conn, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"uno")
    a = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla")
    b = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla")
    conn.execute("UPDATE plays SET protected_at = 1, path = ?, sha256 = 'x' WHERE id = ?", (str(f), a))
    conn.execute("UPDATE plays SET protected_at = 1, path = '/no/existe.mp4', sha256 = 'x' WHERE id = ?", (b,))
    assert sorted(p["problema"] for p in plays.verify(conn)) == ["el archivo cambió", "falta el archivo"]


def test_play_files_outside_the_plays_folder_are_not_served(cfg, conn, server, tmp_path):
    a = plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla")
    conn.execute("UPDATE plays SET protected_at = 1, path = ? WHERE id = ?", (str(cfg.db_path), a))
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{server}/jugada/{a}.mp4", timeout=5)
    assert e.value.code == 404


def test_saved_plays_expire_after_protected_days(cfg, conn):
    cfg.plays_dir.mkdir(parents=True)
    old, new = (plays.record(conn, cfg, "mesa1", T0, T0, T0 + 1, "pantalla") for _ in range(2))
    for pid, at in ((old, T0), (new, T0 + 20 * DAY_MS)):
        f = cfg.plays_dir / f"{pid}.mp4"
        f.write_bytes(b"x" * 10)
        f.chmod(0o444)
        conn.execute("UPDATE plays SET protected_at = ?, path = ? WHERE id = ?", (at, str(f), pid))
    from billar_edge import retention
    retention.run(conn, cfg, now=T0 + 31 * DAY_MS, free_pct=lambda _: 50.0)
    assert plays.get(conn, old) is None and not (cfg.plays_dir / f"{old}.mp4").exists()
    assert plays.get(conn, new) is not None and (cfg.plays_dir / f"{new}.mp4").exists()
    # Con 0 días no se borran nunca.
    never = dataclasses.replace(cfg, protected_days=0)
    assert plays.expire(conn, never, T0 + 999 * DAY_MS) == (0, 0)
