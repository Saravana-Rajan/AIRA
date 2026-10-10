# AIRA — Live Voice Orchestrator Implementation Plan (Saravana · 01)

> **Addendum (2026-10-10): wiring needed by `kanish/02`.**
> 1. Mount `aira_live.messaging.webhooks.router` in `main.py`, serving `/webhooks/whatsapp` and `/webhooks/telegram`.
> 2. Register `delivery_count` and `sale_pay` as **non-blocking** Live tools. They stream `count` messages or wait for the soundbox, so the conversation must continue meanwhile.
> 3. Add the secret `WHATSAPP_APP_SECRET`, used to verify the webhook signature header `X-Hub-Signature-256`.
> 4. Add Firestore rules for the server-only collections `shops/{shopId}/msgIndex` and `shops/{shopId}/contactIndex`: no client read or write.
>
> Add a test that the router is mounted and that both tools are registered as non-blocking.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `aira-live`'s mock mode with a real Gemini Live voice loop when `GEMINI_API_KEY` is set (keep mock mode otherwise). The loop covers:
- streaming audio both ways, transcripts and barge-in
- reconnect with session resumption
- a shop-aware system prompt and the confirm-before-commit flow
- a soundbox transcript hook for Kanish, a number safety check, and vendor/alert notices spoken into live audio
- Firebase token checks on `hello`, and a real-mode tool harness

**Architecture:** `main.py` authenticates `hello`, builds a `Session`, and (in real mode) starts a `LiveBridge`.

`LiveBridge` talks to a pluggable `LiveBackend`:
- **Production:** `AdkLiveBackend`, Google ADK `run_live` + `LiveRequestQueue` on `gemini-3.8-live`.
- **Tests:** an in-memory `FakeBackend`, so no test needs the network.

The bridge works like this:
- It translates backend events into the existing `ServerMsg` types.
- It keeps a per-session registry of input-transcript listeners (used for the soundbox UPI check).
- It reconnects with the last resumption handle.
- After each turn it runs a safety check that every money/stock number AIRA spoke came from a tool result.

Every Live tool is wrapped by `tool_runtime.wrap_tool`. The wrapper sets `current_session` and the tool-call id, so idempotency keys are derived consistently. Consequential tools go through `confirm.propose` → `confirm.resolve` (exactly-once commit).

**Tech Stack:** Python 3.12 · uv · FastAPI · Google ADK (`google-adk`, Live streaming) · `google-genai` types · `firebase-admin` (ID-token verification) · `google-cloud-firestore` (async) · pytest + pytest-asyncio (`asyncio_mode = "auto"`, set by the foundation).

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§3 Principles, §4 Architecture, §4.1 Model responsibilities, §6 Safety). **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

## Global Constraints

- Use the exact names from `2026-10-10-00-interfaces.md`.
  - `Session(uid, shop_id, lang, verbosity, send)`, `get_session()`, `current_session`.
  - `Frame(id, jpeg_b64, w, h, ts, purpose)`, `FrameStore`.
  - `ids.idem_key(session_id, tool_call_id)`, `i18n.t(key, lang, **kw)`, `model_for(role)`.
  - Contract message classes from the foundation's `aira_live/contracts.py`: `HelloMsg`, `AudioInMsg`, `FrameMsg`, `TextMsg`, `EventMsg`, `ConfirmReplyMsg`, `StopMsg`, `ReadyMsg`, `AudioOutMsg`, `TranscriptMsg`, `RequestFrameMsg`, `ConfirmMsg`, `CueMsg`, `ErrorMsg`, `client_adapter`, `server_adapter`.
  - If the foundation named any of these differently, use the foundation's names and keep the semantics.
- **Mock mode:** stays active when `GEMINI_API_KEY` is absent or `AIRA_MOCK=1`. Tests never touch the network: Gemini, Firestore and Firebase Auth are all faked.
- **Model IDs:** only via `model_for("live")` and `model_for("verify")`.
- **Audio formats:** client → server 16 kHz mono PCM16 base64 (`audio.pcm16`); server → client 24 kHz mono PCM16 base64 (`audio.pcm24`).
- **Tool returns:** every tool returns a `dict` with `say`. Consequential tools return `confirm.propose(...)` first. The commit happens only in a registered committer.
- **Idempotency:** keys are `ids.idem_key(session.session_id, tool_call_id)`. A retried call with the same tool-call id produces the same key.
- **Deploy:** `aira-live` runs with `--max-instances 1` for the hackathon. The live-session registry is in-process, so a webhook → `sessions.notify` must land on the instance that holds the WebSocket (Saravana 02 sets this flag).
- **Region:** `asia-south1` (Mumbai) for Cloud Run, Firestore and Cloud Storage. The Gemini API is global.
- **Guidance hooks (Sarmitha, `sarmitha/03`):** `main.py` calls `aira_live.tools.guidance.handle_client_event(session, name, data)` for **every** client `event` message. It calls `aira_live.tools.guidance.stop_guidance(session)` on `stop` and on disconnect. Both are `async`.
  - `main.py` imports the module lazily (`importlib.import_module`), so this plan works before Sarmitha's module is merged.
  - A hook failure is logged and never closes the socket.
- **`product_find(product, count_needed=1)`:** has an optional `count_needed`. The system prompt tells the model to pass it for sales.

## Review Focus

1. **Retried tool call, or a double "yes"** (a voice `confirm_action` plus an on-screen `confirm` at the same moment) → the committer runs **once**. Tests: Task 1 (same call id → same idem key) and Task 2 (concurrent `resolve`).
2. **Long-press stop mid-answer** → no further audio from that answer reaches the phone, and the next question is answered normally. Test: Task 4.
3. **GoAway or a dropped Live connection** → the bridge reconnects with the last resumption handle, sends `cue: "reconnect"`, and soundbox listeners still fire afterwards. Test: Task 4.
4. **AIRA says a money/stock number that no tool returned** (e.g. "₹600" when the sale total is ₹60) → `cue: "warn"` + a corrective instruction to the model. Tests: Task 3 (unit) and Task 4 (integration).
5. **An anonymous (judge) token sent with `shopId: "murugan"`** is forced into the demo shop. An owner token for a different shop is refused with `error: permission`. Test: Task 5.

Also tested in Task 5: every client `event` (e.g. `fingertip_in_target`) reaches `guidance.handle_client_event`, and `stop_guidance` runs on `stop` **and** on disconnect.

## Schedule

| Day | Tasks |
|---|---|
| D1 (Sat 11 Oct) | Tasks 1–3: tool runtime, confirm flow, prompt + safety |
| D2 (Sun 12 Oct) | Tasks 4–6: Live bridge, sessions/auth/main wiring, real-mode harness + smoke on Cloud Run |

Kanish and Sarmitha can call tools through the harness from D1, because Task 6's harness code also works in mock mode.

## File Structure

```
services/live/aira_live/
  session.py            MODIFY  add current_tool_call_id ContextVar + Session.tool_results
  ids.py                MODIFY  add current_idem_key()
  tool_runtime.py       CREATE  wrap_tool(fn, session): ContextVars + result recording, ADK-compatible signature
  confirm.py            CREATE (replace foundation stub if present)  propose / register_committer / resolve
  tools/core.py         CREATE  confirm_action Live tool
  tools/__init__.py     MODIFY  add confirm_action to TOOL_FUNCS
  prompt.py             CREATE  render_prompt (pure) + build_system_prompt (Firestore)
  safety.py             CREATE  number extraction, check_numbers, llm_verify, correction_text
  live_bridge.py        CREATE  LiveEvent, LiveBackend, LiveBridge, on_input_transcript, AdkLiveBackend, translate
  sessions.py           CREATE (replace stub if present)  register / attach_bridge / unregister / by_shop / notify
  auth.py               CREATE  verify_hello (Firebase ID token → AuthResult)
  main.py               MODIFY  real-mode wiring
  devtools/call_tool.py CREATE (replace stub if present)  real-mode harness
  devtools/ws_smoke.py  CREATE  manual end-to-end voice smoke test
  i18n/en.json ta.json hi.json   MODIFY  add confirm.*, safety.correction, auth.denied, live.unavailable
services/live/tests/
  conftest.py           MODIFY/CREATE  fake_db, mk_session, wait_until fixtures
  live_fakes.py         CREATE  FakeFirestore, Recorder, FakeBackend
  test_tool_runtime.py  test_confirm.py  test_prompt_safety.py  test_live_bridge.py
  test_sessions_auth_main.py  test_call_tool.py
```

---

### Task 1: Tool runtime: session + tool-call ContextVars, idempotency keys, result recording

**Files:**
- Modify: `services/live/aira_live/session.py`, `services/live/aira_live/ids.py`
- Create: `services/live/aira_live/tool_runtime.py`, `services/live/tests/live_fakes.py`, `services/live/tests/conftest.py` (append if it exists)
- Test: `services/live/tests/test_tool_runtime.py`

**Interfaces:**
- Consumes: `Session`, `current_session`, `get_session` (foundation `session.py`); `ids.idem_key` (foundation).
- Produces:
  - `aira_live.session.current_tool_call_id: ContextVar[str]` (default `""`).
  - `Session.tool_results: list[dict]`. Each entry is `{"tool": str, "call_id": str, "result": dict}`, cleared at each turn end by the bridge.
  - `aira_live.ids.current_idem_key() -> str`, which **Kanish's tools call** to get their idem key.
  - `aira_live.tool_runtime.wrap_tool(fn, session) -> async callable` with `fn`'s signature plus a keyword-only `tool_context`. ADK hides `tool_context` from the function declaration.

- [ ] **Step 1: Create the shared test fakes `tests/live_fakes.py`**

```python
"""In-memory fakes shared by the live-orchestrator tests. No network."""
import asyncio


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

    def collection(self, name: str) -> "FakeCol":
        return FakeCol(self._store, f"{self.path}/{name}")


class FakeCol:
    def __init__(self, store: dict, path: str):
        self._store, self.path = store, path

    def document(self, doc_id: str) -> FakeDoc:
        return FakeDoc(self._store, f"{self.path}/{doc_id}")

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
        self.started_with: list[str | None] = []
        self.system_prompt: str | None = None
        self.closed = False
        self._q: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None:
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        self.system_prompt = system_prompt
        self.started_with.append(resume_handle)

    async def send_audio(self, pcm16: bytes) -> None:
        self.sent_audio.append(pcm16)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

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
```

- [ ] **Step 2: Add the fixtures to `tests/conftest.py`** (append if the foundation already created it)

```python
import asyncio
import importlib

import pytest

from live_fakes import FakeFirestore, Recorder


@pytest.fixture
def fake_db(monkeypatch):
    """Patch `db()` in every module of this plan that touches Firestore (modules not written yet are skipped)."""
    fake = FakeFirestore()
    for mod_name in ("aira_live.confirm", "aira_live.prompt", "aira_live.auth"):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        monkeypatch.setattr(mod, "db", lambda: fake)
    return fake


@pytest.fixture
def mk_session():
    from aira_live.session import Session

    def _make(shop: str = "murugan", lang: str = "en"):
        rec = Recorder()
        return Session("u1", shop, lang, "short", rec), rec

    return _make


async def wait_until(pred, timeout: float = 1.5) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
```

- [ ] **Step 3: Write the failing test `tests/test_tool_runtime.py`**

```python
import inspect
from types import SimpleNamespace

import pytest

from aira_live import ids
from aira_live.session import get_session
from aira_live.tool_runtime import wrap_tool


def test_wrapped_signature_keeps_params_and_adds_tool_context(mk_session):
    s, _ = mk_session()

    async def stock_query(product: str | None = None) -> dict:
        """How much stock is recorded."""
        return {"say": "ok"}

    w = wrap_tool(stock_query, s)
    assert list(inspect.signature(w).parameters) == ["product", "tool_context"]
    assert w.__name__ == "stock_query" and w.__doc__ == "How much stock is recorded."


async def test_wrapped_call_sets_session_and_stable_idem_key(mk_session):
    s, _ = mk_session()
    seen = {}

    async def sale_start(items: list[dict]) -> dict:
        seen["sid"] = get_session().session_id
        seen.setdefault("keys", []).append(ids.current_idem_key())
        return {"say": "Total ₹60.", "total": 60}

    w = wrap_tool(sale_start, s)
    ctx = SimpleNamespace(function_call_id="call-7")
    await w(items=[{"sku": "sakthi_curd_1l", "qty": 1}], tool_context=ctx)
    await w(items=[{"sku": "sakthi_curd_1l", "qty": 1}], tool_context=ctx)  # retried call, same id

    assert seen["sid"] == s.session_id
    assert seen["keys"][0] == seen["keys"][1] == ids.idem_key(s.session_id, "call-7")
    assert [r["result"]["total"] for r in s.tool_results] == [60, 60]
    assert s.tool_results[0]["tool"] == "sale_start" and s.tool_results[0]["call_id"] == "call-7"


def test_current_idem_key_outside_a_tool_call_raises():
    with pytest.raises(RuntimeError):
        ids.current_idem_key()
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_tool_runtime.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.tool_runtime'`.

- [ ] **Step 5: Implement the session/ids additions and `tool_runtime.py`**

In `aira_live/session.py`, add the ContextVar at module level next to `current_session`:

```python
current_tool_call_id: ContextVar[str] = ContextVar("current_tool_call_id", default="")
```

Then add this line at the end of `Session.__init__`:

```python
        self.tool_results: list[dict] = []   # tool returns of the current turn (cleared by LiveBridge)
```

Append to `aira_live/ids.py`:

```python
def current_idem_key() -> str:
    """Idempotency key for the tool call currently executing (same tool-call id → same key)."""
    from aira_live.session import current_tool_call_id, get_session

    call_id = current_tool_call_id.get()
    if not call_id:
        raise RuntimeError("current_idem_key() called outside a tool call")
    return idem_key(get_session().session_id, call_id)
```

Create `aira_live/tool_runtime.py`:

```python
"""Wraps Live tools so each call runs with its Session + tool-call id set, and its result is recorded."""
import functools
import inspect
import uuid
from typing import Any, Awaitable, Callable

try:  # ADK hides a parameter named `tool_context` from the function declaration.
    from google.adk.tools import ToolContext  # verify path: https://google.github.io/adk-docs/tools/
except ImportError:  # pragma: no cover
    ToolContext = Any  # type: ignore[misc,assignment]

from aira_live.session import Session, current_session, current_tool_call_id

ToolFn = Callable[..., Awaitable[dict]]


def wrap_tool(fn: ToolFn, session: Session) -> ToolFn:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values()) + [
        inspect.Parameter("tool_context", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=ToolContext)
    ]

    @functools.wraps(fn)
    async def wrapper(*args, tool_context=None, **kwargs):
        call_id = getattr(tool_context, "function_call_id", None) or f"local-{uuid.uuid4().hex[:12]}"
        s_token = current_session.set(session)
        c_token = current_tool_call_id.set(call_id)
        try:
            result = await fn(*args, **kwargs)
        finally:
            current_tool_call_id.reset(c_token)
            current_session.reset(s_token)
        if isinstance(result, dict):
            session.tool_results.append({"tool": fn.__name__, "call_id": call_id, "result": result})
        return result

    wrapper.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    return wrapper
```

- [ ] **Step 6: Run the tests**

Run: `cd services/live && uv run pytest -q tests/test_tool_runtime.py`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/session.py services/live/aira_live/ids.py services/live/aira_live/tool_runtime.py services/live/tests/live_fakes.py services/live/tests/conftest.py services/live/tests/test_tool_runtime.py
git commit -m "feat(live): tool runtime with session/tool-call context and stable idem keys"
```

---

### Task 2: Confirm-before-commit flow (`confirm.py`) + the `confirm_action` Live tool

**Files:**
- Create: `services/live/aira_live/confirm.py`, `services/live/aira_live/tools/core.py`
- Modify: `services/live/aira_live/tools/__init__.py`, `services/live/aira_live/i18n/en.json`, `ta.json`, `hi.json`
- Test: `services/live/tests/test_confirm.py`

**Interfaces:**
- Consumes: `get_session()`, `ConfirmMsg(id, kind, prompt)`, `db()`, `t()`.
- Produces (exact interface-registry names):
  - `async propose(kind, prompt, payload, committer) -> {"needs_confirm": True, "confirm_id", "say"}`
  - `register_committer(name, fn)`, where `fn(payload: dict, value: str | None) -> Awaitable[dict]`
  - `async resolve(confirm_id, answer, value) -> dict`
  - Live tool `confirm_action(confirm_id, answer)`
- Proposals are stored at `shops/{shop}/proposals/{confirm_id}` with `status`:

  | `status` | Meaning |
  |---|---|
  | `pending` | Waiting for the owner's answer |
  | `committed` | Committer ran |
  | `rejected` | Owner said no |
  | `expired` | Not answered within the TTL |

  The TTL is 120 s.

- [ ] **Step 1: Add the i18n keys.** Merge these keys into the existing catalogs.

`i18n/en.json`:

```json
{
  "confirm.unknown": "I can't find that request. Please ask again.",
  "confirm.expired": "That request timed out. Please ask again.",
  "confirm.cancelled": "Okay, cancelled.",
  "confirm.already": "That is already done.",
  "safety.correction": "Correction needed: you said {wrong}, which is not in any tool result. Briefly correct yourself using only the numbers from the tool results.",
  "auth.denied": "This phone is not linked to this shop.",
  "live.unavailable": "I can't reach the AI right now. Please try again in a minute."
}
```

`i18n/ta.json`. `safety.correction` is an instruction to the model, so it stays in English:

```json
{
  "confirm.unknown": "அந்த கோரிக்கை கிடைக்கவில்லை. மீண்டும் கேளுங்கள்.",
  "confirm.expired": "நேரம் முடிந்தது. மீண்டும் கேளுங்கள்.",
  "confirm.cancelled": "சரி, ரத்து செய்தேன்.",
  "confirm.already": "அது ஏற்கனவே முடிந்தது.",
  "safety.correction": "Correction needed: you said {wrong}, which is not in any tool result. Briefly correct yourself using only the numbers from the tool results.",
  "auth.denied": "இந்த போன் இந்த கடையுடன் இணைக்கப்படவில்லை.",
  "live.unavailable": "இப்போது AI-ஐ அணுக முடியவில்லை. ஒரு நிமிடம் கழித்து மீண்டும் முயலுங்கள்."
}
```

`i18n/hi.json`:

```json
{
  "confirm.unknown": "वह अनुरोध नहीं मिला। फिर से पूछिए।",
  "confirm.expired": "समय समाप्त हो गया। फिर से पूछिए।",
  "confirm.cancelled": "ठीक है, रद्द कर दिया।",
  "confirm.already": "यह पहले ही हो चुका है।",
  "safety.correction": "Correction needed: you said {wrong}, which is not in any tool result. Briefly correct yourself using only the numbers from the tool results.",
  "auth.denied": "यह फ़ोन इस दुकान से जुड़ा नहीं है।",
  "live.unavailable": "अभी AI से संपर्क नहीं हो पा रहा। एक मिनट बाद फिर कोशिश करें।"
}
```

- [ ] **Step 2: Write the failing test `tests/test_confirm.py`**

```python
import asyncio

from aira_live import confirm
from aira_live.i18n import t
from aira_live.session import current_session
from aira_live.tools.core import confirm_action


async def _proposed(s, kind="order"):
    calls = []

    async def commit(payload, value):
        calls.append(payload)
        return {"ok": True, "say": "Order sent to Sakthi vendor."}

    confirm.register_committer("test.commit", commit)
    p = await confirm.propose(kind, "Order 30 L Sakthi curd from Sakthi vendor?", {"qty": 30}, "test.commit")
    return p, calls


async def test_double_yes_commits_exactly_once(fake_db, mk_session):
    s, rec = mk_session()
    token = current_session.set(s)
    try:
        p, calls = await _proposed(s)
        assert p["needs_confirm"] is True and p["say"].startswith("Order 30 L")
        assert rec.msgs[-1].t == "confirm" and rec.msgs[-1].kind == "order" and rec.msgs[-1].id == p["confirm_id"]

        r1, r2 = await asyncio.gather(  # voice confirm_action + on-screen confirm at the same moment
            confirm_action(p["confirm_id"], "yes"),
            confirm.resolve(p["confirm_id"], "yes", None),
        )
        assert len(calls) == 1 and calls[0] == {"qty": 30}
        assert r1 == r2 == {"ok": True, "say": "Order sent to Sakthi vendor."}
        doc = fake_db.store[f"shops/murugan/proposals/{p['confirm_id']}"]
        assert doc["status"] == "committed"
    finally:
        current_session.reset(token)


async def test_no_cancels_and_never_commits(fake_db, mk_session):
    s, _ = mk_session(lang="ta")
    token = current_session.set(s)
    try:
        p, calls = await _proposed(s)
        r = await confirm.resolve(p["confirm_id"], "no", None)
        assert r == {"ok": False, "say": t("confirm.cancelled", "ta")} and calls == []
    finally:
        current_session.reset(token)


async def test_expired_and_unknown(fake_db, mk_session, monkeypatch):
    s, _ = mk_session()
    token = current_session.set(s)
    try:
        p, calls = await _proposed(s)
        monkeypatch.setattr(confirm, "PROPOSAL_TTL_S", -1)
        assert (await confirm.resolve(p["confirm_id"], "yes", None))["say"] == t("confirm.expired", "en")
        assert (await confirm.resolve("cf_missing", "yes", None))["say"] == t("confirm.unknown", "en")
        assert calls == []
    finally:
        current_session.reset(token)


async def test_unknown_kind_is_sent_as_generic(fake_db, mk_session):
    s, rec = mk_session()
    token = current_session.set(s)
    try:
        await _proposed(s, kind="restock_plan")
        assert rec.msgs[-1].kind == "generic"
    finally:
        current_session.reset(token)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_confirm.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.confirm'` or `aira_live.tools.core`.

- [ ] **Step 4: Implement `aira_live/confirm.py`**

```python
"""Confirm-before-commit: tools propose, the owner says yes/no, a registered committer commits exactly once."""
import asyncio
import time
import uuid
from typing import Awaitable, Callable, Literal

from aira_live.contracts import ConfirmMsg
from aira_live.db import db
from aira_live.i18n import t
from aira_live.session import get_session

Committer = Callable[[dict, str | None], Awaitable[dict]]
PROPOSAL_TTL_S = 120
_KINDS = {"order", "payment", "sale", "discrepancy", "generic"}
_committers: dict[str, Committer] = {}
_locks: dict[str, asyncio.Lock] = {}


def register_committer(name: str, fn: Committer) -> None:
    _committers[name] = fn


def _ref(shop_id: str, confirm_id: str):
    return db().collection("shops").document(shop_id).collection("proposals").document(confirm_id)


async def propose(kind: str, prompt: str, payload: dict, committer: str) -> dict:
    if committer not in _committers:
        raise KeyError(f"no committer registered as {committer!r}")
    s = get_session()
    confirm_id = "cf_" + uuid.uuid4().hex[:10]
    await _ref(s.shop_id, confirm_id).set({
        "kind": kind, "prompt": prompt, "payload": payload, "committer": committer,
        "status": "pending", "createdAt": time.time(), "sessionId": s.session_id,
    })
    await s.send(ConfirmMsg(id=confirm_id, kind=kind if kind in _KINDS else "generic", prompt=prompt))
    return {"needs_confirm": True, "confirm_id": confirm_id, "say": prompt}


async def resolve(confirm_id: str, answer: Literal["yes", "no"], value: str | None) -> dict:
    s = get_session()
    lock = _locks.setdefault(confirm_id, asyncio.Lock())
    async with lock:
        ref = _ref(s.shop_id, confirm_id)
        snap = await ref.get()
        if not snap.exists:
            return {"ok": False, "say": t("confirm.unknown", s.lang)}
        doc = snap.to_dict()
        if doc["status"] in ("committed", "rejected"):
            return doc.get("result") or {"ok": doc["status"] == "committed", "say": t("confirm.already", s.lang)}
        if doc["status"] == "expired" or time.time() - doc["createdAt"] > PROPOSAL_TTL_S:
            await ref.update({"status": "expired"})
            return {"ok": False, "say": t("confirm.expired", s.lang)}
        if answer == "no":
            result = {"ok": False, "say": t("confirm.cancelled", s.lang)}
            await ref.update({"status": "rejected", "result": result, "resolvedAt": time.time()})
            return result
        result = await _committers[doc["committer"]](doc["payload"], value)
        await ref.update({"status": "committed", "result": result, "resolvedAt": time.time()})
        return result
```

The in-process lock covers same-instance races; `aira-live` runs single-instance (see Global Constraints). Committers are additionally idempotent because Kanish's payloads carry `ids.current_idem_key()`.

- [ ] **Step 5: Implement `aira_live/tools/core.py` and register it**

```python
"""Core Live tools owned by the orchestrator."""
from typing import Literal

from aira_live import confirm


async def confirm_action(confirm_id: str, answer: Literal["yes", "no"]) -> dict:
    """Resolve a pending confirmation after the shop OWNER clearly says yes or no.

    Call this only with the confirm_id returned by a tool that said needs_confirm,
    and only for the owner's answer (never a customer's).
    """
    return await confirm.resolve(confirm_id, answer, None)
```

In `aira_live/tools/__init__.py`, add the import and include the tool in the list. Keep every existing entry:

```python
from aira_live.tools.core import confirm_action

TOOL_FUNCS = [*TOOL_FUNCS, confirm_action]  # if TOOL_FUNCS is a list literal above, add confirm_action inside it instead
```

- [ ] **Step 6: Run the tests**

Run: `cd services/live && uv run pytest -q tests/test_confirm.py`
Expected: `4 passed`.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/confirm.py services/live/aira_live/tools/core.py services/live/aira_live/tools/__init__.py services/live/aira_live/i18n services/live/tests/test_confirm.py
git commit -m "feat(live): confirm-before-commit proposals with exactly-once committers"
```

---

### Task 3: Shop-aware system prompt + the number safety check

**Files:**
- Create: `services/live/aira_live/prompt.py`, `services/live/aira_live/safety.py`
- Test: `services/live/tests/test_prompt_safety.py`

**Interfaces:**
- Consumes: `db()`. Firestore `shops/{id}` (`name`), `shops/{id}/products/{sku}` (`name, brand, unit, packSizeL?, price`), `shops/{id}/suppliers/{id}` (`name, channel`) per spec §5. Also `vision.gemini.generate_json` (Sarmitha), imported lazily so this module works before that lands.
- Produces:
  - `prompt.render_prompt(shop, products, suppliers, lang, verbosity) -> str` and `async prompt.build_system_prompt(shop_id, lang, verbosity) -> str`
  - `safety.SafetyVerdict(ok, unexpected, needs_llm)`
  - `safety.check_numbers(spoken, results) -> SafetyVerdict`
  - `async safety.llm_verify(spoken, results) -> SafetyVerdict`
  - `safety.correction_text(unexpected, lang) -> str`

- [ ] **Step 1: Write the failing test `tests/test_prompt_safety.py`**

```python
from aira_live import safety
from aira_live.prompt import build_system_prompt, render_prompt

CURD = {"sku": "sakthi_curd_1l", "name": "curd", "brand": "Sakthi", "unit": "pouch", "packSizeL": 1, "price": 60}


def test_render_prompt_has_shop_products_suppliers_language_and_rules():
    p = render_prompt({"name": "Murugan Dairy"}, [CURD], [{"name": "Sakthi vendor", "channel": "whatsapp"}], "ta", "short")
    assert "Murugan Dairy" in p and "sakthi_curd_1l" in p and "₹60" in p and "Sakthi vendor via whatsapp" in p
    assert "Tamil" in p and "Tamil-English" in p
    low = p.lower()
    assert "read it back" in low and "confirm_action" in p
    assert "never say a number" in low and "not sure" in low
    assert "count_needed" in p and '"check touch <label>"' in p
    assert "credit" not in low


async def test_build_system_prompt_reads_firestore(fake_db):
    shop = fake_db.collection("shops").document("murugan")
    await shop.set({"name": "Murugan Dairy"})
    await shop.collection("products").document("aavin_milk_500ml").set(
        {"name": "milk", "brand": "Aavin", "unit": "pouch", "packSizeL": 0.5, "price": 30})
    await shop.collection("suppliers").document("sup1").set({"name": "Selvam (milk vendor)", "channel": "whatsapp"})
    p = await build_system_prompt("murugan", "en", "short")
    assert "Murugan Dairy" in p and "aavin_milk_500ml" in p and "0.5 L pouch" in p and "Selvam (milk vendor)" in p
    assert "English" in p


def test_flags_money_number_not_in_tool_results():
    v = safety.check_numbers("Total is ₹600.", [{"total": 60, "say": "Total ₹60."}])
    assert v.ok is False and v.unexpected == [600] and v.needs_llm is False


def test_accepts_numbers_from_results_including_say_text_and_quantities():
    results = [{"items": [{"sku": "sakthi_curd_1l", "qty": 2}], "total": 120, "say": "2 litres curd, ₹120."}]
    assert safety.check_numbers("2 litres curd, total 120 rupees.", results).ok


def test_ignores_numbers_without_money_or_stock_context():
    assert safety.check_numbers("Step 3 of 7. Hold it closer.", [{"say": "ok"}]).ok


def test_tamil_and_devanagari_digits_are_normalised():
    assert safety.check_numbers("மொத்தம் ₹௬௦", [{"total": 60}]).ok
    assert not safety.check_numbers("कुल ₹६००", [{"total": 60}]).ok


def test_english_number_words_are_parsed():
    assert safety.check_numbers("Total sixty rupees.", [{"total": 60}]).ok
    assert safety.check_numbers("Total six hundred rupees.", [{"total": 60}]).unexpected == [600]


def test_tamil_number_words_need_the_llm():
    v = safety.check_numbers("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert v.ok is True and v.needs_llm is True


async def test_llm_verify_uses_verify_role(monkeypatch):
    seen = {}

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        seen["role"] = role
        return {"consistent": False, "wrong_numbers": [600]}

    monkeypatch.setattr(safety, "generate_json", fake_generate_json)
    v = await safety.llm_verify("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert seen["role"] == "verify" and v.ok is False and v.unexpected == [600]


async def test_llm_verify_failure_is_treated_as_ok(monkeypatch):
    async def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(safety, "generate_json", boom)
    assert (await safety.llm_verify("அறுநூறு ரூபாய்", [{"total": 60}])).ok


def test_correction_text_names_the_wrong_number():
    assert "600" in safety.correction_text([600], "en")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_prompt_safety.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.prompt'`.

- [ ] **Step 3: Implement `aira_live/prompt.py`**

```python
"""System prompt for the Live voice agent: shop context + language + safety rules."""
from aira_live.db import db

LANG_NAME = {"ta": "Tamil", "hi": "Hindi", "en": "English"}
MIX = {"ta": "Tamil-English mixing is fine and natural.", "hi": "Hindi-English mixing is fine and natural.",
       "en": "Simple Indian English."}


def _pack(p: dict) -> str:
    unit = p.get("unit", "pack")
    return f"{p['packSizeL']:g} L {unit}" if p.get("packSizeL") else unit


def render_prompt(shop: dict, products: list[dict], suppliers: list[dict], lang: str, verbosity: str) -> str:
    length = "One short sentence unless the owner asks for more." if verbosity == "short" else "At most three short sentences."
    lines = [
        f"You are AIRA, the voice assistant of {shop.get('name', 'the shop')}, a small dairy shop run by a blind owner. "
        "You help him run the shop alone: orders, deliveries, invoices, payments, sales and stock.",
        f"Speak {LANG_NAME.get(lang, 'English')}. {MIX.get(lang, '')} {length}",
        "RULES:",
        "1. Before any order, payment or sale is committed, read it back and wait for the owner's yes. "
        "When a tool returns needs_confirm, ask exactly its 'say' question, then call confirm_action with the "
        "confirm_id and the owner's answer.",
        "2. Never say a number (price, quantity, stock, money, change) that did not come from a tool result in "
        "this conversation. If you need a number, call a tool.",
        "3. If a tool result is unsure, has needs_retake or low confidence, say you are not sure and give its aim "
        "hint. Never guess counts, amounts or products.",
        "4. Customers may also speak. Only the owner can confirm actions.",
        "5. Never describe or identify people. Never say a currency note is real or fake.",
        "6. Messages starting with [NOTICE FOR THE SHOPKEEPER] or [CONFIRMATION] come from the system: relay or "
        "acknowledge them briefly.",
        "7. For a customer sale, first call product_find with count_needed set to how many packs to pick, then "
        "sale_start.",
        '8. If the phone sends the text "check touch <label>", call product_find for that label to check what the '
        "owner is touching, and say only the result.",
        "PRODUCTS (sku: brand name, pack, price):",
        *[f"- {p['sku']}: {p.get('brand', '')} {p.get('name', '')}, {_pack(p)}, ₹{p.get('price')}" for p in products],
        "SUPPLIERS:",
        *[f"- {s.get('name')} via {s.get('channel', 'whatsapp')}" for s in suppliers],
    ]
    return "\n".join(lines)


async def build_system_prompt(shop_id: str, lang: str, verbosity: str) -> str:
    shop_ref = db().collection("shops").document(shop_id)
    snap = await shop_ref.get()
    shop = snap.to_dict() if snap.exists else {"name": shop_id}
    products = [(d.to_dict() or {}) | {"sku": d.id} async for d in shop_ref.collection("products").stream()]
    suppliers = [(d.to_dict() or {}) | {"id": d.id} async for d in shop_ref.collection("suppliers").stream()]
    return render_prompt(shop, products, suppliers, lang, verbosity)
```

- [ ] **Step 4: Implement `aira_live/safety.py`**

```python
"""Checks that money/stock numbers AIRA spoke actually came from tool results (spec §3 'model proposes')."""
import re
from dataclasses import dataclass, field

from aira_live.i18n import t

_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯०१२३४५६७८९", "01234567890123456789")
_UNIT = (r"₹|rs\.?|rupees?|inr|litres?|liters?|ltrs?|l(?![a-z])|packs?|pouch(?:es)?|boxes?|"
         r"ரூபாய்|லிட்டர்|பாக்கெட்|रुपये|रुपए|लीटर|पैकेट")
_NUM = r"\d+(?:,\d{2,3})*(?:\.\d+)?"
_PAT = re.compile(rf"(?:(?:{_UNIT})\s*({_NUM}))|(?:({_NUM})\s*(?:{_UNIT}))", re.IGNORECASE)
_HAS_UNIT = re.compile(_UNIT, re.IGNORECASE)
_INDIC_LETTERS = re.compile(r"[஀-௿ऀ-ॿ]")
_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


@dataclass
class SafetyVerdict:
    ok: bool
    unexpected: list[int] = field(default_factory=list)
    needs_llm: bool = False


def _words_to_digits(text: str) -> str:
    out, cur, total, in_num = [], 0, 0, False
    for tok in re.split(r"(\s+|-)", text):
        w = tok.lower().strip(" ,.")
        if w in _ONES or w in _TENS or w in ("hundred", "thousand"):
            in_num = True
            if w in _ONES:
                cur += _ONES[w]
            elif w in _TENS:
                cur += _TENS[w]
            elif w == "hundred":
                cur = max(cur, 1) * 100
            else:
                total += max(cur, 1) * 1000
                cur = 0
            continue
        if tok.strip() == "" or tok == "-":
            if not in_num:
                out.append(tok)
            continue
        if in_num:
            out.append(f"{total + cur} ")
            cur, total, in_num = 0, 0, False
        out.append(tok)
    if in_num:
        out.append(f" {total + cur}")
    return "".join(out)


def money_stock_numbers(text: str) -> set[int]:
    norm = _words_to_digits(text.translate(_DIGITS))
    found = set()
    for a, b in _PAT.findall(norm):
        raw = (a or b).replace(",", "")
        found.add(int(round(float(raw))))
    return found


def numbers_in_results(results: list) -> set[int]:
    nums: set[int] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            nums.add(int(round(v)))
        elif isinstance(v, str):
            for m in re.findall(_NUM, v.translate(_DIGITS)):
                nums.add(int(round(float(m.replace(",", "")))))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x)

    walk(results)
    return nums


def check_numbers(spoken: str, results: list[dict]) -> SafetyVerdict:
    spoken_nums = money_stock_numbers(spoken)
    if not spoken_nums:
        needs_llm = bool(_INDIC_LETTERS.search(spoken)) and bool(_HAS_UNIT.search(spoken))
        return SafetyVerdict(ok=True, needs_llm=needs_llm)
    allowed = numbers_in_results(results)
    unexpected = sorted(n for n in spoken_nums if n not in allowed)
    return SafetyVerdict(ok=not unexpected, unexpected=unexpected)


async def generate_json(*args, **kwargs) -> dict:  # lazy shim → Sarmitha's aira_live.vision.gemini
    from aira_live.vision.gemini import generate_json as _gen
    return await _gen(*args, **kwargs)


async def llm_verify(spoken: str, results: list[dict]) -> SafetyVerdict:
    prompt = (
        "A shop assistant said this to a blind shopkeeper:\n"
        f"SPOKEN: {spoken}\nTOOL RESULTS (the only allowed numbers): {results}\n"
        "Do the money, quantity and stock numbers in SPOKEN (they may be written as Tamil or Hindi words) all "
        "match numbers in TOOL RESULTS? List any spoken numbers that do not match, as integers."
    )
    try:
        out = await generate_json("verify", [prompt], '{"consistent": bool, "wrong_numbers": [int]}',
                                  thinking="low", timeout_s=3.0)
    except Exception:
        return SafetyVerdict(ok=True)  # never block the shop on a verifier outage; deterministic check already ran
    wrong = [int(n) for n in out.get("wrong_numbers", [])]
    return SafetyVerdict(ok=bool(out.get("consistent", True)) and not wrong, unexpected=wrong)


def correction_text(unexpected: list[int], lang: str) -> str:
    return t("safety.correction", lang, wrong=", ".join(str(n) for n in unexpected))
```

- [ ] **Step 5: Run the tests**

Run: `cd services/live && uv run pytest -q tests/test_prompt_safety.py`
Expected: `12 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/prompt.py services/live/aira_live/safety.py services/live/tests/test_prompt_safety.py
git commit -m "feat(live): shop-aware system prompt and money/stock number safety check"
```

---

### Task 4: `LiveBridge`: events → messages, barge-in, hard stop, reconnect, soundbox hook, safety integration

**Files:**
- Create: `services/live/aira_live/live_bridge.py`
- Test: `services/live/tests/test_live_bridge.py`

**Interfaces:**
- Consumes: `Session` (`send`, `tool_results`, `lang`, `uid`, `session_id`), `AudioOutMsg`, `TranscriptMsg`, `CueMsg`, `ErrorMsg`, `safety.*`, `t()`, `model_for("live")`.
- Produces:
  - `LiveEvent(kind, audio, text, final, handle)`.
  - The `LiveBackend` protocol: `start(system_prompt, tools, resume_handle)`, `send_audio(bytes)`, `send_text(str)`, `events()`, `close()`.
  - `LiveBridge(session, backend_factory, system_prompt, tools)` with `start()`, `send_audio(b64)`, `send_text(text)`, `speak_notice(text)`, `hard_stop()`, `close()`.
  - `on_input_transcript(session_id, callback) -> unsubscribe` (exact name, for Kanish's soundbox check).
  - `AdkLiveBackend(session)` and the pure `translate(adk_event) -> list[LiveEvent]`.

- [ ] **Step 1: Write the failing test `tests/test_live_bridge.py`**

```python
import base64
from types import SimpleNamespace

from conftest import wait_until
from live_fakes import FakeBackend

from aira_live.live_bridge import LiveBridge, LiveEvent, on_input_transcript, translate


def _bridge(s, *backends, max_reconnects=5):
    it = iter(backends)
    bridge = LiveBridge(s, lambda: next(it), "prompt", [])
    bridge.BACKOFF_BASE_S = 0
    bridge.MAX_RECONNECTS = max_reconnects
    return bridge


async def test_events_become_messages_and_soundbox_listener_fires(mk_session):
    s, rec = mk_session()
    heard = []

    async def cb(text):
        heard.append(text)

    unsub = on_input_transcript(s.session_id, cb)
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("input_transcript", text="received rupees 60", final=True))
    b.push(LiveEvent("audio", audio=b"\x01\x02"))
    b.push(LiveEvent("output_transcript", text="Payment received.", final=True))
    await wait_until(lambda: any(m.t == "audio" for m in rec.msgs) and heard)
    audio = next(m for m in rec.msgs if m.t == "audio")
    assert base64.b64decode(audio.pcm24) == b"\x01\x02"
    assert [(m.role, m.text) for m in rec.msgs if m.t == "transcript"] == [
        ("user", "received rupees 60"), ("aira", "Payment received.")]
    assert heard == ["received rupees 60"]
    unsub()
    b.push(LiveEvent("input_transcript", text="received rupees 90", final=True))
    await wait_until(lambda: len([m for m in rec.msgs if m.t == "transcript"]) == 3)
    assert heard == ["received rupees 60"]  # unsubscribed listener no longer fires
    await bridge.close()


async def test_client_audio_and_text_reach_the_backend(mk_session):
    s, _ = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.send_audio(base64.b64encode(b"\x00\x01").decode())
    await bridge.send_text("hello")
    await bridge.speak_notice("Sakthi vendor confirmed: delivery at 6 pm.")
    assert b.sent_audio == [b"\x00\x01"] and b.sent_text[0] == "hello"
    assert b.sent_text[1].startswith("[NOTICE FOR THE SHOPKEEPER]") and "6 pm" in b.sent_text[1]
    assert b.system_prompt == "prompt"
    await bridge.close()
    assert b.closed


async def test_barge_in_cue_and_hard_stop_mutes_until_next_question(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("audio", audio=b"a1"))
    await wait_until(lambda: any(m.t == "audio" for m in rec.msgs))
    bridge.hard_stop()                                   # long-press on the phone
    b.push(LiveEvent("audio", audio=b"a2"))              # rest of the stopped answer → dropped
    b.push(LiveEvent("input_transcript", text="two litres curd", final=True))
    b.push(LiveEvent("audio", audio=b"a3"))              # answer to the new question → played
    b.push(LiveEvent("interrupted"))
    await wait_until(lambda: any(m.t == "cue" and m.cue == "interrupted" for m in rec.msgs))
    assert [base64.b64decode(m.pcm24) for m in rec.msgs if m.t == "audio"] == [b"a1", b"a3"]
    await bridge.close()


async def test_go_away_reconnects_with_handle_and_listeners_survive(mk_session):
    s, rec = mk_session()
    heard = []

    async def cb(text):
        heard.append(text)

    unsub = on_input_transcript(s.session_id, cb)
    b1, b2 = FakeBackend(), FakeBackend()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("resumption_handle", handle="h1"))
    b1.push(LiveEvent("go_away"))
    await wait_until(lambda: b2.started_with == ["h1"])
    b2.push(LiveEvent("input_transcript", text="₹60 received", final=True))
    await wait_until(lambda: heard == ["₹60 received"])
    assert b1.closed and any(m.t == "cue" and m.cue == "reconnect" for m in rec.msgs)
    unsub()
    await bridge.close()


async def test_gives_up_after_max_reconnects_with_spoken_error(mk_session):
    s, rec = mk_session()

    class Broken(FakeBackend):
        async def start(self, *a):
            raise ConnectionError("live down")

    b1 = FakeBackend()
    bridge = _bridge(s, b1, Broken(), Broken(), Broken(), max_reconnects=2)
    await bridge.start()
    b1.end()  # stream drops
    await wait_until(lambda: any(m.t == "error" for m in rec.msgs))
    err = next(m for m in rec.msgs if m.t == "error")
    assert err.code == "network" and "AI" in err.say
    await bridge.close()


async def test_wrong_money_number_triggers_warn_and_correction(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    s.tool_results.append({"tool": "sale_start", "call_id": "c1", "result": {"total": 60, "say": "Total ₹60."}})
    b.push(LiveEvent("output_transcript", text="Total is ₹600.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: any("600" in x for x in b.sent_text))
    assert any(m.t == "cue" and m.cue == "warn" for m in rec.msgs)
    assert s.tool_results == []  # cleared for the next turn
    await bridge.close()


def test_translate_adk_event():
    ev = SimpleNamespace(
        interrupted=False,
        input_transcription=SimpleNamespace(text="hi", finished=True),
        output_transcription=None,
        content=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(mime_type="audio/pcm;rate=24000", data=b"x"))]),
        live_session_resumption_update=SimpleNamespace(new_handle="h9"),
        go_away=None,
        turn_complete=True,
    )
    out = translate(ev)
    assert [e.kind for e in out] == ["input_transcript", "audio", "resumption_handle", "turn_complete"]
    assert out[0].final is True and out[1].audio == b"x" and out[2].handle == "h9"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_live_bridge.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.live_bridge'`.

- [ ] **Step 3: Implement `aira_live/live_bridge.py`**

```python
"""Gemini Live bridge: phone audio ⇄ Live model, transcripts, barge-in, reconnect, soundbox hook, safety check."""
from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Literal, Protocol

from aira_live import safety
from aira_live.contracts import AudioOutMsg, CueMsg, ErrorMsg, TranscriptMsg
from aira_live.i18n import t
from aira_live.models import model_for
from aira_live.session import Session

log = logging.getLogger(__name__)

EventKind = Literal["audio", "input_transcript", "output_transcript", "interrupted", "turn_complete",
                    "resumption_handle", "go_away"]


@dataclass
class LiveEvent:
    kind: EventKind
    audio: bytes = b""
    text: str = ""
    final: bool = False
    handle: str | None = None


class LiveBackend(Protocol):
    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None: ...
    async def send_audio(self, pcm16: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...
    async def close(self) -> None: ...


class LiveConnectionLost(Exception):
    pass


TranscriptListener = Callable[[str], Awaitable[None]]
_listeners: dict[str, set[TranscriptListener]] = {}


def on_input_transcript(session_id: str, callback: TranscriptListener) -> Callable[[], None]:
    """Subscribe to the owner/customer/soundbox speech heard by Live in this session (Kanish: soundbox UPI)."""
    _listeners.setdefault(session_id, set()).add(callback)

    def unsubscribe() -> None:
        _listeners.get(session_id, set()).discard(callback)

    return unsubscribe


class LiveBridge:
    MAX_RECONNECTS = 5
    BACKOFF_BASE_S = 0.5

    def __init__(self, session: Session, backend_factory: Callable[[], LiveBackend], system_prompt: str, tools: list):
        self.session = session
        self._factory = backend_factory
        self.system_prompt = system_prompt
        self.tools = tools
        self._backend: LiveBackend | None = None
        self._handle: str | None = None
        self._muted = False
        self._closed = False
        self._out_text: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._connect()
        self._task = asyncio.create_task(self._pump())

    async def _connect(self) -> None:
        self._backend = self._factory()
        await self._backend.start(self.system_prompt, self.tools, self._handle)

    async def send_audio(self, pcm16_b64: str) -> None:
        await self._backend.send_audio(base64.b64decode(pcm16_b64))

    async def send_text(self, text: str) -> None:
        await self._backend.send_text(text)

    async def speak_notice(self, text: str) -> None:
        await self._backend.send_text(f"[NOTICE FOR THE SHOPKEEPER] Tell the shopkeeper briefly, in his language: {text}")

    def hard_stop(self) -> None:
        """Long-press: drop the rest of the current answer until the next question starts."""
        self._muted = True

    async def close(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._backend:
            with suppress(Exception):
                await self._backend.close()
        _listeners.pop(self.session.session_id, None)

    async def _pump(self) -> None:
        failures = 0
        while not self._closed:
            try:
                async for ev in self._backend.events():
                    failures = 0
                    await self._handle_event(ev)
                    if ev.kind == "go_away":
                        raise LiveConnectionLost("go_away")
                raise LiveConnectionLost("stream ended")
            except Exception as exc:  # CancelledError is BaseException and propagates
                if self._closed:
                    return
                log.warning("live connection lost: %s", exc)
            while not self._closed:
                failures += 1
                if failures > self.MAX_RECONNECTS:
                    await self.session.send(ErrorMsg(code="network", say=t("live.unavailable", self.session.lang)))
                    self._closed = True
                    return
                await self.session.send(CueMsg(cue="reconnect"))
                await asyncio.sleep(self.BACKOFF_BASE_S * 2 ** (failures - 1))
                with suppress(Exception):
                    await self._backend.close()
                try:
                    await self._connect()
                    break
                except Exception as exc:
                    log.warning("reconnect %s failed: %s", failures, exc)

    async def _handle_event(self, ev: LiveEvent) -> None:
        s = self.session
        if ev.kind == "audio":
            if not self._muted:
                await s.send(AudioOutMsg(pcm24=base64.b64encode(ev.audio).decode()))
        elif ev.kind == "output_transcript":
            self._out_text.append(ev.text)
            await s.send(TranscriptMsg(role="aira", text=ev.text, final=ev.final))
        elif ev.kind == "input_transcript":
            self._muted = False  # a new question has started
            await s.send(TranscriptMsg(role="user", text=ev.text, final=ev.final))
            for cb in list(_listeners.get(s.session_id, ())):
                try:
                    await cb(ev.text)
                except Exception:
                    log.exception("input-transcript listener failed")
        elif ev.kind == "interrupted":
            await s.send(CueMsg(cue="interrupted"))
        elif ev.kind == "turn_complete":
            self._muted = False
            await self._safety_check()
            self._out_text.clear()
            s.tool_results.clear()
        elif ev.kind == "resumption_handle" and ev.handle:
            self._handle = ev.handle

    async def _safety_check(self) -> None:
        spoken = " ".join(self._out_text).strip()
        results = [r["result"] for r in self.session.tool_results]
        if not spoken or not results:
            return
        verdict = safety.check_numbers(spoken, results)
        if verdict.ok and verdict.needs_llm:
            verdict = await safety.llm_verify(spoken, results)
        if not verdict.ok:
            await self.session.send(CueMsg(cue="warn"))
            await self._backend.send_text(safety.correction_text(verdict.unexpected, self.session.lang))


def translate(e) -> list[LiveEvent]:
    """ADK live event → LiveEvents. Field names: verify against https://google.github.io/adk-docs/streaming/
    and https://ai.google.dev/gemini-api/docs/live-session (resumption/GoAway)."""
    out: list[LiveEvent] = []
    if getattr(e, "interrupted", False):
        out.append(LiveEvent("interrupted"))
    it = getattr(e, "input_transcription", None)
    if it is not None and getattr(it, "text", None):
        out.append(LiveEvent("input_transcript", text=it.text, final=bool(getattr(it, "finished", False))))
    ot = getattr(e, "output_transcription", None)
    if ot is not None and getattr(ot, "text", None):
        out.append(LiveEvent("output_transcript", text=ot.text, final=bool(getattr(ot, "finished", False))))
    content = getattr(e, "content", None)
    for part in (getattr(content, "parts", None) or []):
        blob = getattr(part, "inline_data", None)
        if blob is not None and str(getattr(blob, "mime_type", "")).startswith("audio/pcm"):
            out.append(LiveEvent("audio", audio=blob.data))
    upd = getattr(e, "live_session_resumption_update", None)
    if upd is not None and getattr(upd, "new_handle", None):
        out.append(LiveEvent("resumption_handle", handle=upd.new_handle))
    if getattr(e, "go_away", None):
        out.append(LiveEvent("go_away"))
    if getattr(e, "turn_complete", False):
        out.append(LiveEvent("turn_complete"))
    return out


class AdkLiveBackend:
    """Real backend: Google ADK run_live + LiveRequestQueue on model_for('live')."""

    def __init__(self, session: Session):
        self._s = session
        self._queue = None
        self._gen = None

    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None:
        from google.adk.agents import Agent, LiveRequestQueue
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        agent = Agent(name="aira", model=model_for("live"), instruction=system_prompt, tools=tools)
        runner = InMemoryRunner(agent=agent, app_name="aira-live")
        adk_session = await runner.session_service.create_session(app_name="aira-live", user_id=self._s.uid)
        self._queue = LiveRequestQueue()
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100_000, sliding_window=types.SlidingWindow(target_tokens=60_000)),
        )
        self._gen = runner.run_live(user_id=self._s.uid, session_id=adk_session.id,
                                    live_request_queue=self._queue, run_config=run_config)

    async def send_audio(self, pcm16: bytes) -> None:
        from google.genai import types
        self._queue.send_realtime(types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))

    async def send_text(self, text: str) -> None:
        from google.genai import types
        self._queue.send_content(types.Content(role="user", parts=[types.Part(text=text)]))

    async def events(self):
        async for e in self._gen:
            for le in translate(e):
                yield le

    async def close(self) -> None:
        if self._queue is not None:
            self._queue.close()
```

Design note: native-audio Live streams speech before the text transcript is final. So the safety check runs **at turn end** and makes AIRA correct itself ("Sorry, the total is ₹60"). Hard business truth is still enforced by the deterministic tools and the confirm flow.

- [ ] **Step 4: Run the tests**

Run: `cd services/live && uv run pytest -q tests/test_live_bridge.py`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/live_bridge.py services/live/tests/test_live_bridge.py
git commit -m "feat(live): LiveBridge with barge-in, hard stop, resumption reconnect, soundbox hook and safety check"
```

---

### Task 5: Session registry + `notify`, Firebase auth on `hello`, and `main.py` real-mode wiring

**Files:**
- Create: `services/live/aira_live/sessions.py`, `services/live/aira_live/auth.py`
- Modify: `services/live/aira_live/main.py`
- Test: `services/live/tests/test_sessions_auth_main.py`

**Interfaces:**
- Consumes:
  - Everything from Tasks 1–4.
  - `config.load()` (`mock`, `max_frame_b64`).
  - `TOOL_FUNCS`.
  - Sarmitha's `aira_live.tools.guidance.handle_client_event(session, name, data)` and `stop_guidance(session)`, both async and imported lazily.
- Produces:
  - `sessions.register(session, bridge=None)`, `attach_bridge(session_id, bridge)`, `unregister(session_id)`, `by_shop(shop_id)`, and `async notify(shop_id, text, cue="ack") -> int` (exact interface name).
  - `auth.AuthResult(uid, shop_id, role)`, `auth.AuthError`, `auth.DEMO_SHOP` (env `AIRA_DEMO_SHOP`, default `"murugan_dairy"`), and `async auth.verify_hello(id_token, uid, shop_id, mock) -> AuthResult`.
  - `app.state.backend_factory`: a `Callable[[Session], LiveBackend]` that tests override.

- [ ] **Step 1: Write the failing test `tests/test_sessions_auth_main.py`**

```python
import base64
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from live_fakes import FakeBackend

from aira_live import auth, sessions
from aira_live import main as main_mod
from aira_live.live_bridge import LiveEvent


def test_client_events_and_stop_reach_guidance_hooks(monkeypatch, fake_db):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # mock mode is enough: hooks run in both modes
    calls = {"event": [], "stop": []}

    async def handle_client_event(session, name, data):
        calls["event"].append((session.session_id, name, data))

    async def stop_guidance(session):
        calls["stop"].append(session.session_id)

    monkeypatch.setitem(sys.modules, "aira_live.tools.guidance",
                        SimpleNamespace(handle_client_event=handle_client_event, stop_guidance=stop_guidance))
    with TestClient(main_mod.app).websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "uid": "u1", "shopId": "murugan", "lang": "en",
                      "verbosity": "short", "appVersion": "0.1.0"})
        sid = ws.receive_json()["sessionId"]
        ws.send_json({"t": "event", "name": "fingertip_in_target", "data": {"label": "Sakthi curd"}})
        ws.send_json({"t": "event", "name": "visibility", "data": {"hidden": True}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    deadline = time.time() + 2
    while len(calls["stop"]) < 2 and time.time() < deadline:  # disconnect cleanup runs after the client closes
        time.sleep(0.01)
    assert calls["event"] == [(sid, "fingertip_in_target", {"label": "Sakthi curd"}),
                              (sid, "visibility", {"hidden": True})]
    assert calls["stop"] == [sid, sid]  # once on stop, once on disconnect


def test_broken_guidance_hook_never_closes_the_socket(monkeypatch, fake_db):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def boom(*a):
        raise RuntimeError("guidance bug")

    monkeypatch.setitem(sys.modules, "aira_live.tools.guidance",
                        SimpleNamespace(handle_client_event=boom, stop_guidance=boom))
    with TestClient(main_mod.app).websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "uid": "u1", "shopId": "murugan", "lang": "en",
                      "verbosity": "short", "appVersion": "0.1.0"})
        ws.receive_json()
        ws.send_json({"t": "event", "name": "fingertip_in_target", "data": {}})
        ws.send_json({"t": "text", "text": "still alive?"})
        assert ws.receive_json()["text"] == "(mock) you said: still alive?"


async def test_notify_reaches_every_live_session_of_that_shop_only(mk_session):
    s1, r1 = mk_session(shop="murugan")
    s2, r2 = mk_session(shop="murugan")
    s3, r3 = mk_session(shop="other")
    spoken = []

    class Bridge:
        async def speak_notice(self, text):
            spoken.append(text)

    sessions.register(s1, Bridge())
    sessions.register(s2)
    sessions.register(s3)
    try:
        n = await sessions.notify("murugan", "Sakthi vendor confirmed: delivery at 6 pm.")
        assert n == 2 and spoken == ["Sakthi vendor confirmed: delivery at 6 pm."]
        assert [m.t for m in r1.msgs] == ["cue", "transcript"] and r1.msgs[1].text.startswith("Sakthi vendor")
        assert r3.msgs == []
    finally:
        for s in (s1, s2, s3):
            sessions.unregister(s.session_id)


async def test_anonymous_token_is_forced_into_demo_shop(monkeypatch, fake_db):
    monkeypatch.setattr(auth, "_verify", lambda tok: {"uid": "anon1", "firebase": {"sign_in_provider": "anonymous"}})
    r = await auth.verify_hello("tok", uid="anon1", shop_id="murugan", mock=False)
    assert r.shop_id == auth.DEMO_SHOP and r.role == "demo" and r.uid == "anon1"


async def test_owner_token_must_match_the_shop(monkeypatch, fake_db):
    await fake_db.collection("users").document("u9").set({"shopId": "murugan", "role": "owner"})
    monkeypatch.setattr(auth, "_verify", lambda tok: {"uid": "u9", "firebase": {"sign_in_provider": "phone"}})
    assert (await auth.verify_hello("tok", "u9", "murugan", mock=False)).role == "owner"
    with pytest.raises(auth.AuthError):
        await auth.verify_hello("tok", "u9", "other_shop", mock=False)


async def test_missing_token_allowed_only_in_mock_or_dev_flag(monkeypatch, fake_db):
    assert (await auth.verify_hello(None, "dev", "murugan", mock=True)).shop_id == auth.DEMO_SHOP
    monkeypatch.delenv("AIRA_ALLOW_NO_TOKEN", raising=False)
    with pytest.raises(auth.AuthError):
        await auth.verify_hello(None, "dev", "murugan", mock=False)


def test_ws_real_mode_wires_bridge_audio_and_transcripts(monkeypatch, fake_db):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("AIRA_MOCK", raising=False)

    async def fake_verify(id_token, uid, shop_id, mock):
        return auth.AuthResult(uid=uid, shop_id=shop_id, role="owner")

    async def fake_prompt(shop_id, lang, verbosity):
        return f"prompt for {shop_id}"

    monkeypatch.setattr(main_mod, "verify_hello", fake_verify)
    monkeypatch.setattr(main_mod, "build_system_prompt", fake_prompt)
    fb = FakeBackend()
    main_mod.app.state.backend_factory = lambda session: fb
    try:
        with TestClient(main_mod.app).websocket_connect("/ws") as ws:
            ws.send_json({"t": "hello", "uid": "u1", "idToken": "tok", "shopId": "murugan", "lang": "en",
                          "verbosity": "short", "appVersion": "0.1.0"})
            ready = ws.receive_json()
            assert ready["t"] == "ready" and ready["mock"] is False
            assert fb.system_prompt == "prompt for murugan"
            ws.send_json({"t": "audio", "pcm16": base64.b64encode(b"\x00\x01").decode()})
            deadline = time.time() + 2
            while not fb.sent_audio and time.time() < deadline:
                time.sleep(0.01)
            assert fb.sent_audio == [b"\x00\x01"]
            fb.push(LiveEvent("output_transcript", text="Hello Murugan.", final=True))
            msg = ws.receive_json()
            assert msg == {"t": "transcript", "role": "aira", "text": "Hello Murugan.", "final": True}
            ws.send_json({"t": "stop"})
            assert ws.receive_json() == {"t": "cue", "cue": "done"}
    finally:
        main_mod.app.state.backend_factory = lambda session: main_mod.AdkLiveBackend(session)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_sessions_auth_main.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.sessions'` (or `aira_live.auth`).

- [ ] **Step 3: Implement `aira_live/sessions.py`**

```python
"""In-process registry of live sessions per shop; notify() speaks vendor replies/alerts into live audio."""
import logging

from aira_live.contracts import CueMsg, TranscriptMsg
from aira_live.session import Session

log = logging.getLogger(__name__)
_registry: dict[str, tuple[Session, object | None]] = {}


def register(session: Session, bridge: object | None = None) -> None:
    _registry[session.session_id] = (session, bridge)


def attach_bridge(session_id: str, bridge: object) -> None:
    session, _ = _registry[session_id]
    _registry[session_id] = (session, bridge)


def unregister(session_id: str) -> None:
    _registry.pop(session_id, None)


def by_shop(shop_id: str) -> list[tuple[Session, object | None]]:
    return [(s, b) for s, b in _registry.values() if s.shop_id == shop_id]


async def notify(shop_id: str, text: str, cue: str = "ack") -> int:
    delivered = 0
    for session, bridge in by_shop(shop_id):
        try:
            await session.send(CueMsg(cue=cue))
            await session.send(TranscriptMsg(role="aira", text=text, final=True))
            if bridge is not None:
                await bridge.speak_notice(text)
            delivered += 1
        except Exception:
            log.exception("notify failed for session %s", session.session_id)
    return delivered
```

- [ ] **Step 4: Implement `aira_live/auth.py`**

```python
"""Verifies the Firebase ID token sent in hello. Anonymous (judge) users always land in the demo shop."""
import asyncio
import os
from dataclasses import dataclass
from typing import Literal

from aira_live.db import db

DEMO_SHOP = os.getenv("AIRA_DEMO_SHOP", "murugan_dairy")


class AuthError(Exception):
    pass


@dataclass
class AuthResult:
    uid: str
    shop_id: str
    role: Literal["owner", "family", "demo"]


def _verify(id_token: str) -> dict:
    import firebase_admin
    from firebase_admin import auth as fb_auth

    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    return fb_auth.verify_id_token(id_token)


async def verify_hello(id_token: str | None, uid: str, shop_id: str, mock: bool) -> AuthResult:
    if not id_token:
        if mock or os.getenv("AIRA_ALLOW_NO_TOKEN") == "1":  # local dev / smoke only; never set in Cloud Run
            return AuthResult(uid=uid, shop_id=DEMO_SHOP, role="demo")
        raise AuthError("missing id token")
    try:
        claims = await asyncio.to_thread(_verify, id_token)
    except Exception as exc:
        raise AuthError("invalid id token") from exc
    real_uid = claims["uid"]
    if claims.get("firebase", {}).get("sign_in_provider") == "anonymous":
        return AuthResult(uid=real_uid, shop_id=DEMO_SHOP, role="demo")
    snap = await db().collection("users").document(real_uid).get()
    user = snap.to_dict() if snap.exists else {}
    if user.get("shopId") != shop_id:
        raise AuthError("user is not linked to this shop")
    return AuthResult(uid=real_uid, shop_id=shop_id, role=user.get("role", "owner"))
```

- [ ] **Step 5: Replace `aira_live/main.py`.** It keeps the foundation's mock behaviour and adds real mode.

```python
import importlib
import inspect
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from aira_live import config, confirm, sessions
from aira_live.auth import AuthError, verify_hello
from aira_live.contracts import (AudioInMsg, ConfirmReplyMsg, CueMsg, ErrorMsg, EventMsg, FrameMsg, HelloMsg,
                                 ReadyMsg, StopMsg, TextMsg, TranscriptMsg, client_adapter, server_adapter)
from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.live_bridge import AdkLiveBackend, LiveBridge
from aira_live.prompt import build_system_prompt
from aira_live.session import Session, current_session
from aira_live.tool_runtime import wrap_tool
from aira_live.tools import TOOL_FUNCS

log = logging.getLogger(__name__)
app = FastAPI(title="aira-live")
app.state.backend_factory = lambda session: AdkLiveBackend(session)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mock": config.load().mock}


async def _guidance_hook(name: str, *args) -> None:
    """Call aira_live.tools.guidance.<name>(*args) if Sarmitha's module is present; never let it kill the socket."""
    try:
        guidance = importlib.import_module("aira_live.tools.guidance")
    except ImportError:
        return
    try:
        result = getattr(guidance, name)(*args)
        if inspect.isawaitable(result):
            await result
    except Exception:
        log.exception("guidance hook %s failed", name)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    settings = config.load()

    async def send(msg):
        await ws.send_text(server_adapter.dump_json(msg, by_alias=True, exclude_none=True).decode())

    session: Session | None = None
    bridge: LiveBridge | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                if data.get("t") == "frame" and len(data.get("jpeg", "")) > settings.max_frame_b64:
                    await send(ErrorMsg(code="unsupported", say="Image too large"))
                    continue
                msg = client_adapter.validate_python(data)
            except (ValidationError, ValueError):
                await send(ErrorMsg(code="unsupported", say="Unsupported message"))
                continue

            if isinstance(msg, HelloMsg):
                try:
                    who = await verify_hello(msg.id_token, msg.uid, msg.shop_id, settings.mock)
                except AuthError:
                    await send(ErrorMsg(code="permission", say=t("auth.denied", msg.lang)))
                    await ws.close(code=4403)
                    return
                session = Session(who.uid, who.shop_id, msg.lang, msg.verbosity, send)
                current_session.set(session)
                sessions.register(session)
                if not settings.mock:
                    try:
                        prompt = await build_system_prompt(session.shop_id, session.lang, session.verbosity)
                        tools = [wrap_tool(fn, session) for fn in TOOL_FUNCS]
                        s = session
                        bridge = LiveBridge(session, lambda: app.state.backend_factory(s), prompt, tools)
                        await bridge.start()
                        sessions.attach_bridge(session.session_id, bridge)
                    except Exception:
                        log.exception("live start failed")
                        bridge = None
                        await send(ErrorMsg(code="network", say=t("live.unavailable", session.lang)))
                await send(ReadyMsg(session_id=session.session_id, resume=session.session_id, mock=settings.mock))
                continue

            if session is None:
                await send(ErrorMsg(code="permission", say="Send hello first"))
                continue
            if isinstance(msg, AudioInMsg):
                if bridge:
                    await bridge.send_audio(msg.pcm16)
            elif isinstance(msg, FrameMsg):
                session.frames.add(Frame(id=msg.id, jpeg_b64=msg.jpeg, w=msg.w, h=msg.h, ts=msg.ts, purpose=msg.purpose))
            elif isinstance(msg, TextMsg):
                if bridge:
                    await bridge.send_text(msg.text)
                else:
                    await send(TranscriptMsg(role="aira", text=f"(mock) you said: {msg.text}", final=True))
            elif isinstance(msg, ConfirmReplyMsg):
                result = await confirm.resolve(msg.id, msg.answer, msg.value)
                await send(TranscriptMsg(role="aira", text=result.get("say", ""), final=True))
                if bridge:
                    await bridge.send_text(
                        f"[CONFIRMATION] The owner answered '{msg.answer}' on the phone for {msg.id}. "
                        f"Result: {result.get('say', '')}. Do not call confirm_action for it again.")
            elif isinstance(msg, EventMsg):
                await _guidance_hook("handle_client_event", session, msg.name, msg.data)
            elif isinstance(msg, StopMsg):
                await _guidance_hook("stop_guidance", session)
                if bridge:
                    bridge.hard_stop()
                await send(CueMsg(cue="done"))
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            await _guidance_hook("stop_guidance", session)
            sessions.unregister(session.session_id)
        if bridge:
            await bridge.close()
```

- [ ] **Step 6: Run the new tests and the foundation's mock-mode tests**

Run: `cd services/live && uv run pytest -q`
Expected: all tests pass, including the foundation's existing mock WebSocket tests. A mock `hello` without `idToken` lands in the demo shop.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/sessions.py services/live/aira_live/auth.py services/live/aira_live/main.py services/live/tests/test_sessions_auth_main.py
git commit -m "feat(live): session registry with notify, Firebase auth on hello, real-mode wiring in main"
```

---

### Task 6: Real-mode tool harness (`call_tool`) + manual voice smoke test

**Files:**
- Create: `services/live/aira_live/devtools/__init__.py` (empty, if missing), `services/live/aira_live/devtools/call_tool.py`, `services/live/aira_live/devtools/ws_smoke.py`
- Test: `services/live/tests/test_call_tool.py`

**Interfaces:**
- Consumes: `TOOL_FUNCS`, `wrap_tool`, `Session`, `Frame`, `RequestFrameMsg`, `server_adapter`, `confirm.resolve`.
- Produces: CLI `uv run python -m aira_live.devtools.call_tool <tool> --args '<json>' [--image a.jpg ...] [--purpose P] [--shop demo] [--lang en|ta|hi] [--confirm yes|no]`. It is the exact harness from the interface registry. Kanish and Sarmitha use it to run real tools on photos against real Gemini/Firestore (with env keys), or against fakes.

- [ ] **Step 1: Write the failing test `tests/test_call_tool.py`**

```python
from aira_live import confirm, ids
from aira_live.devtools import call_tool
from aira_live.session import get_session


async def test_runs_tool_with_images_frames_and_idem_key(tmp_path, monkeypatch, capsys, fake_db):
    img = tmp_path / "crate.jpg"
    img.write_bytes(b"\xff\xd8fake-jpeg")

    async def delivery_count(sku: str, mode: str = "one_by_one") -> dict:
        frames = await get_session().request_frames("count", count=2)
        return {"say": f"{sku}: {len(frames)} frames", "idem": ids.current_idem_key(),
                "purpose": frames[0].purpose}

    monkeypatch.setattr(call_tool, "load_tools", lambda: {"delivery_count": delivery_count})
    rc = await call_tool.run(["delivery_count", "--args", '{"sku": "sakthi_curd_1l"}', "--image", str(img)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sakthi_curd_1l: 2 frames" in out and '"purpose": "count"' in out
    assert '"t":"request_frame"' in out and '"idem":' in out


async def test_confirm_flag_resolves_proposals(monkeypatch, capsys, fake_db):
    committed = []

    async def commit(payload, value):
        committed.append(payload)
        return {"ok": True, "say": "Order sent."}

    confirm.register_committer("cli.test", commit)

    async def order_create(supplier: str, items: list[dict]) -> dict:
        return await confirm.propose("order", f"Order from {supplier}?", {"items": items}, "cli.test")

    monkeypatch.setattr(call_tool, "load_tools", lambda: {"order_create": order_create})
    rc = await call_tool.run(["order_create", "--args", '{"supplier": "Sakthi vendor", "items": [{"sku": "x", "qty": 30}]}',
                              "--confirm", "yes"])
    out = capsys.readouterr().out
    assert rc == 0 and committed == [{"items": [{"sku": "x", "qty": 30}]}] and "Order sent." in out


async def test_unknown_tool_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(call_tool, "load_tools", lambda: {"stock_query": None})
    assert await call_tool.run(["nope"]) == 2
    assert "available: stock_query" in capsys.readouterr().err
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_call_tool.py`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `aira_live.devtools.call_tool`.

- [ ] **Step 3: Implement `aira_live/devtools/call_tool.py`**

```python
"""Run any Live tool from the shell, with photos as camera frames.

uv run python -m aira_live.devtools.call_tool delivery_count --args '{"sku":"sakthi_curd_1l","mode":"crate"}' \
    --image crate.jpg --shop demo --lang en
Real mode needs GEMINI_API_KEY (+ GOOGLE_APPLICATION_CREDENTIALS / gcloud ADC for Firestore).
"""
import argparse
import asyncio
import base64
import dataclasses
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from aira_live import confirm
from aira_live.contracts import RequestFrameMsg, server_adapter
from aira_live.frames import Frame
from aira_live.session import Session, current_session
from aira_live.tool_runtime import wrap_tool

DEFAULT_PURPOSE = {
    "delivery_count": "count", "product_find": "point", "product_identify": "identify",
    "invoice_check": "invoice", "supplier_pay": "cash", "sale_pay": "cash",
}


class HarnessSession(Session):
    """Session whose camera is a list of photos: request_frames returns them immediately."""

    def __init__(self, *args, images: list[Frame], **kwargs):
        super().__init__(*args, **kwargs)
        self._images = images

    async def request_frames(self, purpose, count=1, interval_ms=0, timeout_s=4.0):
        await self.send(RequestFrameMsg(purpose=purpose))
        if not self._images:
            return []
        return [dataclasses.replace(self._images[i % len(self._images)], purpose=purpose) for i in range(count)]


def load_tools() -> dict:
    from aira_live.tools import TOOL_FUNCS
    return {fn.__name__: fn for fn in TOOL_FUNCS}


async def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="call_tool")
    ap.add_argument("tool")
    ap.add_argument("--args", default="{}")
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--purpose")
    ap.add_argument("--shop", default=os.getenv("AIRA_DEMO_SHOP", "murugan_dairy"))
    ap.add_argument("--lang", default="en", choices=["en", "ta", "hi"])
    ap.add_argument("--confirm", choices=["yes", "no"])
    ns = ap.parse_args(argv)

    tools = load_tools()
    if ns.tool not in tools:
        print(f"unknown tool {ns.tool}; available: {', '.join(sorted(tools))}", file=sys.stderr)
        return 2
    purpose = ns.purpose or DEFAULT_PURPOSE.get(ns.tool, "ask")
    frames = [Frame(id=f"img{i}", jpeg_b64=base64.b64encode(Path(p).read_bytes()).decode(), w=0, h=0,
                    ts=time.time() * 1000 + i, purpose=purpose) for i, p in enumerate(ns.image)]

    async def send(msg):
        print("→", server_adapter.dump_json(msg, by_alias=True, exclude_none=True).decode())

    session = HarnessSession("devtools", ns.shop, ns.lang, "short", send, images=frames)
    token = current_session.set(session)
    try:
        fn = wrap_tool(tools[ns.tool], session)
        result = await fn(**json.loads(ns.args), tool_context=SimpleNamespace(function_call_id=f"cli-{uuid.uuid4().hex[:8]}"))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if ns.confirm and isinstance(result, dict) and result.get("needs_confirm"):
            resolved = await confirm.resolve(result["confirm_id"], ns.confirm, None)
            print(json.dumps(resolved, ensure_ascii=False, indent=2, default=str))
    finally:
        current_session.reset(token)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
```

If the foundation's `Frame` is a pydantic model rather than a dataclass, replace `dataclasses.replace(f, purpose=purpose)` with `f.model_copy(update={"purpose": purpose})`.

- [ ] **Step 4: Run the tests**

Run: `cd services/live && uv run pytest -q tests/test_call_tool.py`
Expected: `3 passed`.

- [ ] **Step 5: Create the manual voice smoke script `aira_live/devtools/ws_smoke.py`**

```python
"""Manual end-to-end voice check: stream a 16 kHz mono WAV to aira-live and print what comes back.

AIRA_ALLOW_NO_TOKEN=1 GEMINI_API_KEY=... uv run uvicorn aira_live.main:app --port 8080
uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws question_16k.wav --shop demo
"""
import asyncio
import base64
import json
import sys
import wave

import websockets


async def main(url: str, wav_path: str, shop: str) -> None:
    async with websockets.connect(url, max_size=2 ** 23) as ws:
        await ws.send(json.dumps({"t": "hello", "uid": "smoke", "shopId": shop, "lang": "en",
                                  "verbosity": "short", "appVersion": "smoke"}))
        print(await ws.recv())
        with wave.open(wav_path) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono PCM16"
            pcm = w.readframes(w.getnframes())
        chunk = 1280  # 40 ms
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"t": "audio", "pcm16": base64.b64encode(pcm[i:i + chunk]).decode()}))
            await asyncio.sleep(0.04)
        for _ in range(40):  # 1.6 s of silence so voice activity detection ends the turn
            await ws.send(json.dumps({"t": "audio", "pcm16": base64.b64encode(b"\x00" * chunk).decode()}))
            await asyncio.sleep(0.04)
        audio_bytes = 0
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if m["t"] == "audio":
                    audio_bytes += len(base64.b64decode(m["pcm24"]))
                else:
                    print(m)
        except asyncio.TimeoutError:
            pass
        print(f"received {audio_bytes} bytes of 24 kHz audio (~{audio_bytes / 48000:.1f} s)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "murugan_dairy"))
```

- [ ] **Step 6: Run the manual smoke test** with real keys and the demo shop seeded by the foundation

Record a question as a 16 kHz mono WAV:

```bash
ffmpeg -i question.m4a -ar 16000 -ac 1 -sample_fmt s16 question_16k.wav
```

Then run the server and the smoke script in two shells:

```bash
cd services/live && AIRA_ALLOW_NO_TOKEN=1 GEMINI_API_KEY=$GEMINI_API_KEY uv run uvicorn aira_live.main:app --port 8080
cd services/live && uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws question_16k.wav --shop demo
```

The question to record is "How much Sakthi curd do we have?"

Expected:
- `{"t":"ready",...,"mock":false}` is printed.
- A user `transcript` is printed with the question.
- An `aira` transcript answer is printed. If `stock_query` isn't merged yet, the agent says it can't check.
- `received N bytes of 24 kHz audio` with N > 0.

Then run the same smoke against the deployed `wss://aira-live-…run.app/ws` with a real anonymous token flow, using Ishwarya's app. Note the p50 first-audio latency for the benchmark slide.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/devtools services/live/tests/test_call_tool.py
git commit -m "feat(live): real-mode call_tool harness and ws voice smoke script"
```

---

## Self-review notes

- **Spec coverage.** Each spec requirement maps to a task:

  | Spec requirement | Task |
  |---|---|
  | Voice orchestrator | 4, 5 |
  | Confirm before consequence | 2 |
  | Model proposes / backend commits; idempotency | 1, 2 |
  | Honest uncertainty + number guard | 3, 4 |
  | Interruptible, short sentences, ta/hi/en mix | 3, 4 |
  | Vendor replies spoken via `sessions.notify` | 5 |
  | Firebase Auth / demo shop for judges | 5 |
  | Resumption / compression | 4 (`AdkLiveBackend` config) |

- **Name checks.** These match the interface registry: `on_input_transcript`, `notify`, `propose` / `register_committer` / `resolve`, `confirm_action` (in `tools/core.py`), `idem_key`, `call_tool`, the guidance hooks `handle_client_event` / `stop_guidance`, and the `product_find(..., count_needed)` prompt rule.
- **Coordinator additions (2026-10-10):**
  - Guidance hooks wired for every `event`, plus `stop_guidance` on `stop` and on disconnect (tests in Task 5).
  - Prompt rules 7 and 8: `count_needed`, and `"check touch <label>"` → `product_find`.
  - Region `asia-south1`.
- **New names for other teams:**
  - `aira_live.ids.current_idem_key()`: Kanish's tools use it instead of computing keys themselves.
  - `aira_live.session.current_tool_call_id`.
  - `aira_live.tool_runtime.wrap_tool`.
  - `LiveBridge.speak_notice`.
  - i18n keys `confirm.*`, `safety.correction`, `auth.denied` and `live.unavailable`.
  - Env `AIRA_ALLOW_NO_TOKEN` (local dev only).
  - The `--max-instances 1` deploy requirement (Saravana 02).
