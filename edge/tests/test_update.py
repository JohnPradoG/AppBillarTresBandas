"""Fase 8, parte 2: actualizaciones con vuelta atrás."""

import sqlite3
import subprocess

import pytest

from billar_edge import update


def make_release(prefix, name):
    b = prefix / "releases" / name / "venv" / "bin"
    b.mkdir(parents=True)
    (b / "billar").write_text("#!/bin/sh\n")


class FakeSystem:
    """systemctl y runuser simulados. `bad` = versión cuyos servicios fallan."""

    def __init__(self, prefix, bad=()):
        self.prefix = prefix
        self.bad = set(bad)
        self.calls = []
        self.notes = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[0] == "systemctl" and cmd[1] == "is-active":
            out = "failed" if update.current(self.prefix) in self.bad else "active"
        elif cmd[0] == "systemctl" and cmd[1] == "show":
            out = "0"
        elif cmd[0] == "runuser" and "note-event" in cmd:
            self.notes.append(tuple(cmd[-3:]))
        elif cmd[0] == "runuser" and "backup-db" in cmd:
            out = "Copia de la base de datos: /var/lib/billar/respaldos/x.db"
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def prefix(tmp_path):
    p = tmp_path / "opt"
    for n in ("20261001-000000-aaa", "20261005-000000-bbb", "20261008-000000-ccc"):
        make_release(p, n)
    update.switch(p, "20261008-000000-ccc")
    return p


def updater(cfg, prefix, fake, recording=True, ui=True):
    up = update.Updater(cfg, prefix=prefix, run=fake, sleep=lambda s: None, settle=0, recording_wait=0)
    up.recording_ok = lambda: recording
    up.ui_ok = lambda: ui
    return up


def restarts(fake):
    return [c for c in fake.calls if c[:2] == ["systemctl", "restart"]]


def test_switch_is_a_single_symlink_change(prefix):
    assert update.current(prefix) == "20261008-000000-ccc"
    with pytest.raises(FileNotFoundError):
        update.switch(prefix, "no-existe")
    assert update.current(prefix) == "20261008-000000-ccc"


def test_good_update_stays_and_prunes_old_versions(cfg, prefix):
    make_release(prefix, "20261009-000000-ddd")
    fake = FakeSystem(prefix)
    assert updater(cfg, prefix, fake).apply("20261009-000000-ddd") == 0
    assert update.current(prefix) == "20261009-000000-ddd"
    assert update.releases(prefix) == ["20261005-000000-bbb", "20261008-000000-ccc", "20261009-000000-ddd"]
    assert len(restarts(fake)) == 1 and "billar-recorder@mesa1.service" in restarts(fake)[0]
    assert fake.notes[-1][:2] == ("info", update.UPDATE_OK)
    # La copia de la base se hace antes de cambiar de versión.
    assert any("backup-db" in c for c in fake.calls[:3])


def test_broken_update_goes_back_by_itself(cfg, prefix):
    make_release(prefix, "20261009-000000-mal")
    fake = FakeSystem(prefix, bad={"20261009-000000-mal"})
    assert updater(cfg, prefix, fake).apply("20261009-000000-mal") == 1
    assert update.current(prefix) == "20261008-000000-ccc"
    assert len(restarts(fake)) == 2
    level, kind, msg = fake.notes[-1]
    assert level == "error" and kind == update.UPDATE_FAILED and "20261008-000000-ccc" in msg


def test_update_that_stops_recording_goes_back(cfg, prefix):
    make_release(prefix, "20261009-000000-ddd")
    fake = FakeSystem(prefix)
    up = updater(cfg, prefix, fake)
    states = iter([True] + [False] * 50)          # grababa antes; después ya no
    up.recording_ok = lambda: next(states)
    assert up.apply("20261009-000000-ddd") == 1
    assert update.current(prefix) == "20261008-000000-ccc"
    assert "la grabación no volvió" in fake.notes[-1][2]


def test_camera_off_before_update_does_not_block_it(cfg, prefix):
    make_release(prefix, "20261009-000000-ddd")
    fake = FakeSystem(prefix)
    assert updater(cfg, prefix, fake, recording=False).apply("20261009-000000-ddd") == 0


def test_screen_not_answering_goes_back(cfg, prefix):
    make_release(prefix, "20261009-000000-ddd")
    fake = FakeSystem(prefix)
    assert updater(cfg, prefix, fake, ui=False).apply("20261009-000000-ddd") == 1
    assert update.current(prefix) == "20261008-000000-ccc"


def test_manual_rollback(cfg, prefix):
    fake = FakeSystem(prefix)
    assert updater(cfg, prefix, fake).rollback() == 0
    assert update.current(prefix) == "20261005-000000-bbb"
    assert fake.notes[-1][1] == update.UPDATE_ROLLBACK


def test_prune_never_deletes_the_active_or_previous(tmp_path):
    p = tmp_path / "opt"
    for n in ("a1", "a2", "a3", "a4", "a5"):
        make_release(p, n)
    update.switch(p, "a2")
    gone = update.prune(p, keep=2)
    assert sorted(gone) == ["a3"] and update.releases(p) == ["a1", "a2", "a4", "a5"]


def test_backup_db_keeps_the_last_five(cfg, conn, monkeypatch):
    conn.execute("CREATE TABLE prueba (x)")
    conn.execute("INSERT INTO prueba VALUES (42)")
    stamps = iter(f"20261009-0000{i:02d}" for i in range(7))
    monkeypatch.setattr(update.time, "strftime", lambda fmt: next(stamps))
    for _ in range(7):
        last = update.backup_db(cfg)
    files = sorted((cfg.data_dir / "respaldos").glob("billar-*.db"))
    assert len(files) == 5 and files[-1] == last
    assert sqlite3.connect(last).execute("SELECT x FROM prueba").fetchone() == (42,)
