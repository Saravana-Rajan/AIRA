"""Camera sources: a phone in shop-camera mode (frames over /ws) and an RTSP camera through ffmpeg.

No ffmpeg is needed: the RTSP source takes a ``spawn`` callable, and the tests hand it a fake process whose
stdout is a scripted byte stream. The one real-ffmpeg test is skipped when ffmpeg is absent.
"""
from __future__ import annotations

import asyncio
import base64
import shutil

import pytest

from aira_live.cameras import sources
from aira_live.cameras.sources import JpegSplitter, PhoneSource, RtspSource, camera_name, camera_uid, is_camera_session
from aira_live.frames import Frame

JPEG_A = b"\xff\xd8\xff\xe0AAAA\xff\xd9"
JPEG_B = b"\xff\xd8\xff\xe0BBBBBB\xff\xd9"


# ---------- the camera role on /ws ----------

def test_camera_uid_round_trips():
    assert camera_uid("front") == "camera:front"
    assert camera_name("camera:front") == "front"
    assert camera_name("u1") is None
    assert camera_name("camera:") is None


def test_is_camera_session(mk_session):
    from aira_live.session import Session

    s, _ = mk_session()
    assert is_camera_session(s) is False
    cam = Session(camera_uid("front"), "murugan_dairy", "en", "short", s._send)
    assert is_camera_session(cam) is True


# ---------- JPEG splitting ----------

def test_splitter_finds_whole_jpegs_across_chunks():
    sp = JpegSplitter()
    assert sp.feed(JPEG_A[:5]) == []
    assert sp.feed(JPEG_A[5:] + JPEG_B[:3]) == [JPEG_A]
    assert sp.feed(JPEG_B[3:]) == [JPEG_B]


def test_splitter_drops_garbage_before_the_first_soi():
    sp = JpegSplitter()
    assert sp.feed(b"junk" + JPEG_A) == [JPEG_A]


def test_splitter_bounds_its_buffer():
    sp = JpegSplitter(max_buffer=16)
    assert sp.feed(b"\xff\xd8" + b"x" * 40) == []
    assert len(sp.pending()) <= 16


# ---------- ffmpeg command ----------

def test_ffmpeg_args_use_tcp_low_fps_and_768px():
    args = RtspSource.ffmpeg_args("rtsp://cam/stream", interval_s=5, width=768)
    assert args[0] == "ffmpeg"
    assert "-rtsp_transport" in args and args[args.index("-rtsp_transport") + 1] == "tcp"
    vf = args[args.index("-vf") + 1]
    assert "fps=1/5" in vf and "scale=768:-2" in vf
    assert args[-1] == "-" and "image2pipe" in args and "mjpeg" in args
    assert args[args.index("-i") + 1] == "rtsp://cam/stream"


def test_ffmpeg_args_never_leak_the_url_when_rendered_for_logs():
    args = RtspSource.ffmpeg_args("rtsp://user:secret@cam/stream", interval_s=5)
    assert "secret" not in RtspSource.describe(args)


# ---------- the RTSP source with a fake process ----------

class FakeProc:
    """Stands in for ``asyncio.subprocess.Process``: a scripted stdout, then EOF."""

    def __init__(self, chunks: list[bytes]):
        self.stdout = asyncio.StreamReader()
        for c in chunks:
            self.stdout.feed_data(c)
        self.stdout.feed_eof()
        self.killed = False
        self.returncode = None

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.returncode = self.returncode or 0
        return self.returncode


async def collect(src, n, timeout=2.0):
    out = []
    async with asyncio.timeout(timeout):
        async for jpeg, ts in src.frames():
            out.append((jpeg, ts))
            if len(out) >= n:
                break
    await src.close()
    return out


async def test_rtsp_yields_each_jpeg_with_a_timestamp(monkeypatch):
    monkeypatch.setenv("CAMERA_FRONT_URL", "rtsp://cam/stream")
    spawned = []

    async def spawn(args):
        spawned.append(args)
        return FakeProc([JPEG_A[:4], JPEG_A[4:] + JPEG_B])

    src = RtspSource("front", "CAMERA_FRONT_URL", interval_s=5, spawn=spawn, clock=lambda: 42.0)
    got = await collect(src, 2)
    assert got == [(JPEG_A, 42.0), (JPEG_B, 42.0)]
    assert spawned and spawned[0][args_index(spawned[0], "-i") + 1] == "rtsp://cam/stream"


def args_index(args, flag):
    return args.index(flag)


async def test_rtsp_reconnects_with_backoff_after_eof(monkeypatch):
    monkeypatch.setenv("CAMERA_FRONT_URL", "rtsp://cam/stream")
    slept = []
    calls = 0

    async def spawn(args):
        nonlocal calls
        calls += 1
        return FakeProc([] if calls < 3 else [JPEG_A])   # two dead connections, then a frame

    async def sleep(s):
        slept.append(s)

    src = RtspSource("front", "CAMERA_FRONT_URL", spawn=spawn, sleep=sleep, backoff=(1, 2, 4))
    got = await collect(src, 1)
    assert got[0][0] == JPEG_A
    assert calls == 3
    assert slept[:2] == [1, 2]   # exponential, from the configured schedule


async def test_rtsp_waits_when_the_url_env_is_missing_and_never_spawns(monkeypatch):
    monkeypatch.delenv("CAMERA_FRONT_URL", raising=False)
    spawned = 0
    sleeps = []

    async def spawn(args):
        nonlocal spawned
        spawned += 1
        return FakeProc([JPEG_A])

    async def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError  # the watcher's task would be cancelled at shutdown

    src = RtspSource("front", "CAMERA_FRONT_URL", spawn=spawn, sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        async for _ in src.frames():
            pass
    assert spawned == 0 and len(sleeps) == 3


async def test_rtsp_close_kills_the_process(monkeypatch):
    monkeypatch.setenv("CAMERA_FRONT_URL", "rtsp://cam/stream")
    proc = FakeProc([JPEG_A, JPEG_B])

    async def spawn(args):
        return proc

    src = RtspSource("front", "CAMERA_FRONT_URL", spawn=spawn)
    await collect(src, 1)
    assert proc.killed is True


async def test_rtsp_spawn_failure_backs_off_instead_of_raising(monkeypatch):
    monkeypatch.setenv("CAMERA_FRONT_URL", "rtsp://cam/stream")
    calls = 0

    async def spawn(args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FileNotFoundError("ffmpeg")
        return FakeProc([JPEG_A])

    slept = []

    async def sleep(s):
        slept.append(s)

    src = RtspSource("front", "CAMERA_FRONT_URL", spawn=spawn, sleep=sleep)
    got = await collect(src, 1)
    assert got[0][0] == JPEG_A and slept == [sources.DEFAULT_BACKOFF[0]]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_real_ffmpeg_decodes_a_synthetic_source():
    """ffmpeg's lavfi test pattern stands in for a camera; the args are the real ones minus the RTSP input."""
    args = RtspSource.ffmpeg_args("rtsp://unused", interval_s=1)
    i = args.index("-i")
    args[i - 1:i + 1] = ["-f", "lavfi", "-i"]
    args[i + 1] = "testsrc=size=320x240:rate=2"
    args.insert(args.index("-vf"), "-t")
    args.insert(args.index("-vf"), "2")
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    sp = JpegSplitter()
    found = []
    while True:
        chunk = await proc.stdout.read(65536)
        if not chunk:
            break
        found += sp.feed(chunk)
    await proc.wait()
    assert found and all(f.startswith(b"\xff\xd8") for f in found)


# ---------- the phone source ----------

def _frame(ts: float, data: bytes = JPEG_A) -> Frame:
    return Frame(id=f"f{ts}", jpeg_b64=base64.b64encode(data).decode(), w=640, h=480, ts=ts, purpose="ask")


async def test_phone_source_reads_only_its_camera_session(mk_session):
    from aira_live.session import Session

    owner, rec = mk_session()
    cam = Session(camera_uid("front"), "murugan_dairy", "ta", "short", rec)
    other = Session(camera_uid("back"), "murugan_dairy", "ta", "short", rec)
    src = PhoneSource("front", "murugan_dairy", poll_s=0.02, find_sessions=lambda shop: [(owner, None), (cam, None), (other, None)])

    async def feed():
        await asyncio.sleep(0.03)
        owner.frames.add(_frame(1000.0, JPEG_B))   # the owner's own frames are not the camera
        other.frames.add(_frame(1000.0, JPEG_B))
        cam.frames.add(_frame(1000.0))
        await asyncio.sleep(0.03)
        cam.frames.add(_frame(2000.0, JPEG_B))

    asyncio.create_task(feed())
    got = await collect(src, 2)
    assert got == [(JPEG_A, 1.0), (JPEG_B, 2.0)]   # phone epoch ms -> seconds


async def test_phone_source_waits_for_the_camera_phone_to_connect(mk_session):
    from aira_live.session import Session

    registry: list = []
    src = PhoneSource("front", "murugan_dairy", poll_s=0.02, find_sessions=lambda shop: list(registry))
    _, rec = mk_session()

    async def connect_later():
        await asyncio.sleep(0.05)
        cam = Session(camera_uid("front"), "murugan_dairy", "en", "short", rec)
        registry.append((cam, None))
        cam.frames.add(_frame(5000.0))

    asyncio.create_task(connect_later())
    got = await collect(src, 1)
    assert got == [(JPEG_A, 5.0)]
