import uuid
from pathlib import Path

from billar_edge.config import CameraConfig, load
from billar_edge.ids import uuid7
from billar_edge.media import parse_segment_start, read_pgm, recorder_command
from billar_edge.recorder import _redact


def test_uuid7_is_version_7_and_time_ordered():
    a, b = uuid7(1_000), uuid7(2_000)
    assert uuid.UUID(a).version == 7
    assert a < b


def test_segment_name_is_utc_start_time():
    # 2026-10-08 16:31:00 UTC
    assert parse_segment_start(Path("20261008T163100Z.mp4")) == 1791477060000
    assert parse_segment_start(Path("otro.mp4")) is None


def test_read_pgm_keeps_pixels_that_start_with_whitespace_bytes(tmp_path):
    pixels = bytes([10, 32, 9] + [200] * 573)  # 32x18 = 576
    p = tmp_path / "s.pgm"
    p.write_bytes(b"P5\n32 18\n255\n" + pixels)
    assert read_pgm(p) == pixels
    p.write_bytes(b"P5\n32 18\n255\n" + pixels[:100])
    assert read_pgm(p) is None


def test_recorder_command_copies_video_without_reencoding(tmp_path):
    cam = CameraConfig(id="c", url="rtsp://admin:secreto@192.168.1.64:554/s")
    cmd = recorder_command("ffmpeg", cam, tmp_path / "v", tmp_path / "r", 60)
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[cmd.index("-segment_time") + 1] == "60"
    assert "secreto" not in " ".join(_redact(cmd))


def test_load_config(tmp_path):
    p = tmp_path / "billar.toml"
    p.write_text(
        '[general]\nrecordings_dir = "/v"\nretention_days = 7\n'
        '[[cameras]]\nid = "mesa2"\nurl = "rtsp://x/y"\ntable_number = 2\n'
    )
    cfg = load(p)
    assert cfg.camera("mesa2").table_number == 2
    assert cfg.segment_seconds == 60
