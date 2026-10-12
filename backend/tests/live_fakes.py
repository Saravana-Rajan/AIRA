"""In-memory fakes shared by the live-orchestrator tests. No network.

Import as ``from live_fakes import ...``: ``tests/conftest.py`` puts this folder on ``sys.path``.
"""
import asyncio
import uuid


class _Snap:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, store: dict, path: str):
        self._store, self.path = store, path

    @property
    def id(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    async def set(self, data: dict, merge: bool = False) -> None:
        if merge and self.path in self._store:
            self._store[self.path].update(data)
        else:
            self._store[self.path] = dict(data)

    async def get(self) -> _Snap:
        return _Snap(self.id, self._store.get(self.path))

    async def update(self, data: dict) -> None:
        if self.path not in self._store:
            raise KeyError(self.path)
        self._store[self.path].update(data)

    async def delete(self) -> None:
        self._store.pop(self.path, None)

    def collection(self, name: str) -> "FakeCol":
        return FakeCol(self._store, f"{self.path}/{name}")


class FakeCol:
    def __init__(self, store: dict, path: str):
        self._store, self.path = store, path

    def document(self, doc_id: str | None = None) -> FakeDoc:
        return FakeDoc(self._store, f"{self.path}/{doc_id or uuid.uuid4().hex[:20]}")

    async def stream(self):
        prefix = self.path + "/"
        for key in sorted(self._store):
            rest = key[len(prefix):] if key.startswith(prefix) else None
            if rest and "/" not in rest:
                yield _Snap(rest, self._store[key])


class FakeFirestore:
    def __init__(self):
        self.store: dict[str, dict] = {}

    def collection(self, name: str) -> FakeCol:
        return FakeCol(self.store, name)


class Recorder:
    """Stands in for a WebSocket: records every ServerMsg the server sends."""

    def __init__(self):
        self.msgs: list = []

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)


class FakeBackend:
    """In-memory LiveBackend. push()/end() are thread-safe (used from TestClient threads)."""

    def __init__(self):
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.sent_video: list[bytes] = []
        self.queued = 0  # what backlog() reports (live video drops frames when it is high)
        self.started_with: list[str | None] = []
        self.system_prompt: str | None = None
        self.tools: list | None = None
        self.closed = False
        self._q: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None:
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        self.system_prompt, self.tools = system_prompt, tools
        self.started_with.append(resume_handle)

    async def send_audio(self, pcm16: bytes) -> None:
        self.sent_audio.append(pcm16)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_video(self, jpeg: bytes) -> None:
        self.sent_video.append(jpeg)

    def backlog(self) -> int:
        return self.queued

    def push(self, ev) -> None:
        self._loop.call_soon_threadsafe(self._q.put_nowait, ev)

    def end(self) -> None:
        self._loop.call_soon_threadsafe(self._q.put_nowait, None)

    async def events(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True


async def wait_until(pred, timeout: float = 1.5) -> None:
    """Poll ``pred()`` until it is true; fail the test after ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
