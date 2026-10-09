import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from billar_edge.config import CameraConfig, Config
from billar_edge.db import connect, ensure_camera

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="requiere ffmpeg y ffprobe")


def make_config(tmp_path: Path, url: str = "rtsp://10.0.0.9/stream", **kw) -> Config:
    cam = CameraConfig(id="mesa1", url=url, table_number=1)
    defaults = dict(
        data_dir=tmp_path / "data",
        recordings_dir=tmp_path / "video",
        run_dir=tmp_path / "run",
        cameras=(cam,),
    )
    defaults.update(kw)
    cfg = Config(**defaults)
    cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
    cfg.camera_dir("mesa1").mkdir(parents=True, exist_ok=True)
    cfg.camera_run_dir("mesa1").mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def conn(cfg):
    c = connect(cfg.db_path)
    ensure_camera(c, "Billar de prueba", cfg.cameras[0])
    yield c
    c.close()


def make_clip(path: Path, seconds: float = 2) -> None:
    """Un clip H.264 corto en MP4 fragmentado, como los que escribe el grabador."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=160x90:rate=10:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "10", "-pix_fmt", "yuv420p", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         "-y", str(path)],
        check=True,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeCamera:
    """Una "cámara" que emite H.264 en MPEG-TS por TCP, con un fotograma clave por segundo."""

    def __init__(self, port: int):
        self.port = port
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
             "testsrc2=size=320x180:rate=15", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
             "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
             "-f", "mpegts", f"tcp://127.0.0.1:{self.port}?listen=1"],
            stdin=subprocess.DEVNULL,
        )
        time.sleep(0.5)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
