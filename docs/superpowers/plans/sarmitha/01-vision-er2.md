# AIRA — Vision with Gemini Robotics-ER 2 (Sarmitha · 01)

> **Addendum (2026-10-10): full dairy range.** `identify_product` candidates now include ALL SKUs:
> `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `aavin_buttermilk_200ml`, `aavin_ghee_200ml`, `aavin_ghee_500ml`, `aavin_paneer_200g`, `aavin_butter_100g`, `arun_icecream_box`.
> - **New confusion pairs:**
>   - milk 500 ml vs buttermilk 200 ml pouch
>   - ghee 200 ml vs 500 ml jar
>   - paneer vs butter pack
> - Any confusion pair returns `aim_hint` = `vision.aim.show_label`.
> - `bench/data/identify/labels.json` must cover every SKU at least twice.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AIRA reliable "eyes" for the shop. Build one shared Gemini helper, then three vision functions on top of it:

- **count** packs in a crate or on a shelf
- **identify** which product Murugan is holding
- **point** to a product so the app can guide his hand

Then measure whether Gemini Robotics-ER 2 is fast and accurate enough, and switch to Flash if it isn't.

**Architecture:** All model calls go through `aira_live/vision/gemini.py`.

- **`generate_json`** retries once, then falls back to `fallback_for(role)`. It extracts JSON even from fenced or chatty replies, and every model ID comes from `model_for`.
- **Pure decision functions** turn raw model output into typed results that state their confidence and whether a retake is needed:
  - `count.assess`
  - `identify.decide` / `identify.next_hint`
  - `point.parse_target`
- **Never invent a count or a product.** When unsure: `count=None` / `sku=None` + `needs_retake` + an `aim_hint` key.
- **Spoken hints:** `vision/hints.py` turns hint keys into Tamil, Hindi or English sentences, which the tools speak.
- **Bench scripts** measure latency and accuracy on real photos.

**Tech Stack:** Python 3.12, `google-genai`, pydantic v2, pytest + pytest-asyncio (asyncio_mode = auto), uv.

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§3 principles, §4.1 model responsibilities, §7 benchmarks). **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md` (Vision section).

**Prerequisite:** the foundation plan (`2026-10-10-00-foundation.md`) is merged. It provides:

- `aira_live/models.py` (`model_for`, `fallback_for`, `Role`)
- `aira_live/frames.py` (`Frame`)
- `aira_live/config.py`

## Schedule

| Day | Date | Tasks |
|---|---|---|
| D0 | Fri Oct 10 | Task 1 (Gemini helper) + Task 5 (ER 2 spike; take the 20 photos tonight) |
| D1 | Sat Oct 11 | Task 2 (count) + Task 3 (identify) |
| D2 | Sun Oct 12 | Task 4 (point) |
| D3 | Mon Oct 13 | Task 6 (scorers), then hand the numbers to Saravana for the benchmark slide |

## Global Constraints

- **Coordinates:** points are `[y, x]` and boxes are `[ymin, xmin, ymax, xmax]`, normalised **0–1000**. Never convert to pixels in this layer.
- **Model IDs** only via `aira_live.models.model_for(role)` / `fallback_for(role)`. **Roles used here:**
  - `count` (ER 2): counting
  - `point` (ER 2): identify + point. Remote Config `pointing_model` or env `AIRA_MODEL_POINT` can switch it to Flash.
- **Thinking level:** `"high"` for counting, `"low"` for identify and point.
- **Timeouts:** 12 s for count, 6 s for identify, 5 s for point, applied per attempt.
- **Uncertainty:**
  - Never invent a count or a SKU.
  - Uncertain → `count=None` / `sku=None` + an `aim_hint` key from `vision/hints.py`.
- **No people:** prompts never ask for or describe people. **No counterfeit claims** (cash is plan 02).
- **SKU IDs for the demo shop**, which must match Kanish's `content/catalog/murugan_dairy.json`:
  - `aavin_milk_500ml`, `aavin_milk_1l`
  - `sakthi_curd_500ml`, `sakthi_curd_1l`
  - `arun_icecream_box`
- **Branch:** `sarmitha/vision-er2`. Small PRs to `main`.

## Review Focus

1. **The model returns fenced, chatty or broken JSON** → extract it or fall back; never crash. *(Task 1: `test_extract_json_handles_fences_and_prose`, `test_all_fail_raises`)*
2. **ER 2 hangs or times out** → fall back to Flash within the per-attempt timeout. *(Task 1: `test_timeout_falls_back`)*
3. **The model double-points the same pack, or returns out-of-range or malformed points** → dedupe and drop them; never count them. *(Task 2: `test_dedupes_double_points`, `test_ignores_invalid_points`)*
4. **The model returns a SKU that isn't in the candidate list, or confuses 500 ml with 1 L** → `sku=None` and ask to show the label. *(Task 3: `test_unknown_sku_is_rejected`, `test_confusion_pair_needs_label`)*
5. **The target isn't visible** (`{"point": null}`) or a box doesn't contain its point → `None` / drop the box; never a fake target. *(Task 4: `test_not_visible_returns_none`, `test_box_not_containing_point_is_dropped`)*

## File Structure

```
services/live/aira_live/vision/
  __init__.py
  gemini.py      # generate_json, image_part, extract_json, ToolReply, VisionError, set_client
  hints.py       # aim-hint keys → ta/hi/en sentences; hint_text(key, lang)
  count.py       # CountResult, assess(), count_in_frame()
  identify.py    # ProductRef, IdentifyResult, decide(), next_hint(), identify_product()
  point.py       # TargetResult, parse_target(), point_to()
services/live/tests/vision/
  __init__.py  conftest.py  test_gemini.py  test_hints.py  test_count.py  test_identify.py  test_point.py
bench/
  common.py  spike_er2.py  score_count.py  score_identify.py
  tests/test_common.py
  data/packs/labels.json  data/count/labels.json  data/identify/labels.json  data/identify/candidates.json
  results/   (git-ignored CSV/JSON outputs)
```

---

### Task 1: Gemini helper (`vision/gemini.py`)

**Files:**
- Create: `services/live/aira_live/vision/__init__.py` (empty)
- Create: `services/live/aira_live/vision/gemini.py`
- Create: `services/live/tests/vision/__init__.py` (empty)
- Create: `services/live/tests/vision/conftest.py`
- Test: `services/live/tests/vision/test_gemini.py`

**Interfaces:**
- **Consumes:**
  - `aira_live.models.model_for(role) -> str` and `fallback_for(role) -> str | None`, `Role`
  - `aira_live.config.load().gemini_api_key`
  - `aira_live.frames.Frame(id, jpeg_b64, w, h, ts, purpose)`
- **Produces:**
  - `async generate_json(role: Role, parts: list, schema_hint: str, thinking: Literal["low","medium","high"]="low", timeout_s: float=6.0) -> dict`
    - A JSON list reply is wrapped as `{"items": [...]}`.
    - Adds `"_model"` naming the model that answered.
    - Raises `VisionError` when every attempt fails.
  - `image_part(jpeg_b64: str) -> google.genai.types.Part`
  - `extract_json(text: str) -> Any`
  - `class ToolReply(BaseModel)` with `.to_dict()`
  - `set_client(c)` (tests only)
  - `class VisionError(RuntimeError)`

- [ ] **Step 1: Write the test fixtures** in `services/live/tests/vision/conftest.py`

```python
import asyncio
import base64
from types import SimpleNamespace

import pytest

from aira_live.frames import Frame
from aira_live.vision import gemini

TINY_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xff\xd9").decode()


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeAioModels:
    """Scripted replies: str → reply text, float → hang that long (simulates a timeout), Exception → raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, float):
            await asyncio.sleep(item)
            return FakeResp("{}")
        return FakeResp(item)


class FakeClient:
    def __init__(self, script):
        self.aio = SimpleNamespace(models=FakeAioModels(script))


@pytest.fixture
def fake_genai(monkeypatch):
    for role in ("POINT", "COUNT", "READ", "VERIFY", "LIVE"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)

    def make(*script):
        client = FakeClient(script)
        gemini.set_client(client)
        return client

    yield make
    gemini.set_client(None)


@pytest.fixture
def frame():
    return Frame(id="f1", jpeg_b64=TINY_JPEG_B64, w=640, h=480, ts=1.0, purpose="count")
```

- [ ] **Step 2: Write the failing tests** in `services/live/tests/vision/test_gemini.py`

```python
import pytest

from aira_live.models import fallback_for, model_for
from aira_live.vision.gemini import ToolReply, VisionError, extract_json, generate_json, image_part


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! [{"point": [1, 2]}] done.') == [{"point": [1, 2]}]
    assert extract_json('x {"s": "a } b"} y') == {"s": "a } b"}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("")


async def test_generate_json_wraps_list_and_reports_model(fake_genai):
    client = fake_genai('[{"point": [10, 20]}]')
    out = await generate_json("count", ["prompt"], "list", thinking="high")
    assert out["items"] == [{"point": [10, 20]}]
    assert out["_model"] == model_for("count")
    assert client.aio.models.calls[0]["model"] == model_for("count")
    assert client.aio.models.calls[0]["config"] is not None


async def test_retry_once_then_fallback(fake_genai):
    client = fake_genai("not json", RuntimeError("503"), '{"ok": true}')
    out = await generate_json("point", ["prompt"], "{ok}")
    assert out["ok"] is True
    assert [c["model"] for c in client.aio.models.calls] == [
        model_for("point"), model_for("point"), fallback_for("point")]


async def test_timeout_falls_back(fake_genai):
    fake_genai(0.5, 0.5, '{"ok": 1}')
    out = await generate_json("count", ["prompt"], "{ok}", timeout_s=0.05)
    assert out["_model"] == fallback_for("count")


async def test_all_fail_raises(fake_genai):
    fake_genai("x", "y", "z")
    with pytest.raises(VisionError):
        await generate_json("count", ["prompt"], "{}")


def test_image_part_decodes_base64(frame):
    part = image_part(frame.jpeg_b64)
    assert part.inline_data.mime_type == "image/jpeg"
    assert part.inline_data.data.startswith(b"\xff\xd8")


def test_toolreply_defaults():
    assert ToolReply(say_first="hi").to_dict() == {
        "say_first": "hi", "detail": "", "confidence": 0.0, "needs_retake": False, "aim_hint": ""}
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `cd services/live && uv run pytest -q tests/vision/test_gemini.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision'`.

- [ ] **Step 4: Implement `services/live/aira_live/vision/gemini.py`**

```python
"""Shared Gemini helper for AIRA vision. Model IDs come ONLY from aira_live.models."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from aira_live import config
from aira_live.models import Role, fallback_for, model_for

log = logging.getLogger(__name__)
Thinking = Literal["low", "medium", "high"]
_client: Any = None


class VisionError(RuntimeError):
    """Primary model, its retry, and the fallback all failed."""


class ToolReply(BaseModel):
    say_first: str
    detail: str = ""
    confidence: float = 0.0
    needs_retake: bool = False
    aim_hint: str = ""

    def to_dict(self) -> dict:
        return self.model_dump()


def set_client(c: Any) -> None:
    """Inject a client (tests). Pass None to reset to the real client."""
    global _client
    _client = c


def client() -> Any:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.load().gemini_api_key)
    return _client


def image_part(jpeg_b64: str) -> types.Part:
    return types.Part.from_bytes(data=base64.b64decode(jpeg_b64), mime_type="image/jpeg")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _balanced(s: str, start: int, open_c: str, close_c: str) -> str | None:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may be fenced or wrapped in prose."""
    if not text or not text.strip():
        raise ValueError("empty model reply")
    m = _FENCE.search(text)
    candidate = (m.group(1) if m else text).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    starts = [(candidate.find(o), o, c) for o, c in (("[", "]"), ("{", "}")) if candidate.find(o) != -1]
    for start, o, c in sorted(starts):
        chunk = _balanced(candidate, start, o, c)
        if chunk:
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in model reply: {text[:80]!r}")


def _config(thinking: Thinking) -> types.GenerateContentConfig:
    base = {"temperature": 0.2, "response_mime_type": "application/json"}
    try:
        # verify against https://ai.google.dev/gemini-api/docs/robotics-overview (thinking_level) for the installed SDK
        return types.GenerateContentConfig(**base, thinking_config=types.ThinkingConfig(thinking_level=thinking))
    except Exception:  # older SDK without thinking_level: still return JSON-mode config
        return types.GenerateContentConfig(**base)


async def generate_json(role: Role, parts: list, schema_hint: str,
                        thinking: Thinking = "low", timeout_s: float = 6.0) -> dict:
    contents = [*parts, f"Return ONLY valid JSON, no prose. Schema: {schema_hint}"]
    primary = model_for(role)
    attempts = [primary, primary]
    fb = fallback_for(role)
    if fb and fb != primary:
        attempts.append(fb)
    last: Exception | None = None
    for model in attempts:
        try:
            resp = await asyncio.wait_for(
                client().aio.models.generate_content(model=model, contents=contents, config=_config(thinking)),
                timeout=timeout_s,
            )
            data = extract_json(resp.text)
            out = {"items": data} if isinstance(data, list) else dict(data)
            out["_model"] = model
            return out
        except Exception as e:  # timeout, API error, or unparseable reply → next attempt
            last = e
            log.warning("vision.generate_json role=%s model=%s failed: %r", role, model, e)
    raise VisionError(f"{role}: all attempts failed: {last!r}")
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `cd services/live && uv run pytest -q tests/vision/test_gemini.py`
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/vision/__init__.py services/live/aira_live/vision/gemini.py services/live/tests/vision
git commit -m "feat(vision): shared Gemini JSON helper with retry, fallback and ToolReply"
```

---

### Task 2: Counting packs (`vision/count.py`) + spoken hints (`vision/hints.py`)

**Files:**
- Create: `services/live/aira_live/vision/hints.py`, `services/live/aira_live/vision/count.py`
- Test: `services/live/tests/vision/test_hints.py`, `services/live/tests/vision/test_count.py`

**Interfaces:**
- **Consumes:** `generate_json`, `image_part`, `Frame`.
- **Produces:**
  - `class CountResult(BaseModel)`: `count: int | None`, `confidence: float`, `needs_retake: bool`, `aim_hint: str`
  - `def assess(items: list[dict]) -> CountResult` (pure)
  - `async def count_in_frame(frame: Frame, item_label: str) -> CountResult`
  - `HINTS: dict[str, dict[str, str]]` and `def hint_text(key: str, lang: str) -> str`. This is a **new cross-team name**: Kanish's `delivery_count` tool uses it to speak `aim_hint`.

- [ ] **Step 1: Write the failing tests**

`services/live/tests/vision/test_hints.py`:

```python
from aira_live.vision.hints import HINTS, hint_text


def test_every_hint_has_three_languages():
    for key, entry in HINTS.items():
        for lang in ("en", "ta", "hi"):
            assert entry.get(lang), f"{key} missing {lang}"


def test_hint_text_fallbacks():
    assert hint_text("", "ta") == ""
    assert hint_text("vision.aim.unknown", "en") == ""
    assert hint_text("vision.aim.move_back", "fr") == HINTS["vision.aim.move_back"]["en"]
    assert hint_text("vision.aim.move_back", "ta") == HINTS["vision.aim.move_back"]["ta"]
```

`services/live/tests/vision/test_count.py`:

```python
from aira_live.models import model_for
from aira_live.vision.count import assess, count_in_frame


def grid(n, start=150, step=120):
    pts = []
    for i in range(n):
        pts.append({"point": [start + (i // 5) * step, start + (i % 5) * step], "label": "pack", "cut": False})
    return pts


def test_clean_grid_counts_with_full_confidence():
    r = assess(grid(12))
    assert r.count == 12 and r.confidence == 1.0 and r.needs_retake is False and r.aim_hint == ""


def test_dedupes_double_points():
    items = grid(4) + [{"point": [152, 151], "label": "pack"}]   # same pack pointed twice
    assert assess(items).count == 4


def test_ignores_invalid_points():
    items = grid(3) + [{"point": [1200, 5]}, {"point": [1]}, {"point": "a"}, "junk", {"label": "no point"}]
    assert assess(items).count == 3


def test_edge_cut_items_ask_to_move_back():
    items = grid(5) + [{"point": [10, 500], "label": "pack", "cut": True}]
    r = assess(items)
    assert r.count == 6 and r.needs_retake is True and r.aim_hint == "vision.aim.move_back"
    assert r.confidence < 1.0


def test_overlapping_cluster_asks_to_spread_out():
    items = [{"point": [500, 500 + i * 20], "label": "pack"} for i in range(6)]
    r = assess(items)
    assert r.needs_retake is True and r.aim_hint == "vision.aim.spread_out"


def test_nothing_visible_returns_none():
    r = assess([])
    assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.show_items"


async def test_count_in_frame_uses_er2_count_role(fake_genai, frame):
    client = fake_genai('[{"point": [200, 200], "label": "Sakthi curd 1 L pouch"}, {"point": [200, 400], "label": "Sakthi curd 1 L pouch"}]')
    r = await count_in_frame(frame, "Sakthi curd 1 L pouch")
    assert r.count == 2
    call = client.aio.models.calls[0]
    assert call["model"] == model_for("count")
    assert any("Sakthi curd 1 L pouch" in str(p) for p in call["contents"])


async def test_count_in_frame_accepts_wrapped_dict(fake_genai, frame):
    fake_genai('{"points": [{"point": [300, 300]}, {"point": [300, 600]}, {"point": [600, 300]}]}')
    assert (await count_in_frame(frame, "Aavin milk 500 ml pouch")).count == 3
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd services/live && uv run pytest -q tests/vision/test_hints.py tests/vision/test_count.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.hints'`.

- [ ] **Step 3: Implement `services/live/aira_live/vision/hints.py`**

```python
"""Aim/retake hints spoken to Murugan. Tools call hint_text(result.aim_hint, session.lang)."""

HINTS: dict[str, dict[str, str]] = {
    "vision.aim.move_back": {
        "en": "Move the phone back so I can see the whole crate.",
        "ta": "முழு கிரேட் தெரியும்படி போனை கொஞ்சம் பின்னால் நகர்த்துங்கள்.",
        "hi": "फ़ोन थोड़ा पीछे करें ताकि पूरा क्रेट दिखे।",
    },
    "vision.aim.spread_out": {
        "en": "Some packs are on top of each other. Spread them apart, or count one by one.",
        "ta": "சில பாக்கெட்டுகள் ஒன்றின் மேல் ஒன்று உள்ளன. பிரித்து வையுங்கள், அல்லது ஒவ்வொன்றாக எண்ணுங்கள்.",
        "hi": "कुछ पैकेट एक-दूसरे पर हैं। उन्हें अलग करें, या एक-एक करके गिनें।",
    },
    "vision.aim.show_items": {
        "en": "I can't see any packs. Point the camera at them.",
        "ta": "எந்த பாக்கெட்டும் தெரியவில்லை. கேமராவை அவற்றை நோக்கி காட்டுங்கள்.",
        "hi": "कोई पैकेट नहीं दिख रहा। कैमरा उनकी ओर करें।",
    },
    "vision.aim.show_label": {
        "en": "Turn the pack so the label with the size faces me.",
        "ta": "அளவு எழுதிய லேபிள் தெரியும்படி பாக்கெட்டைத் திருப்புங்கள்.",
        "hi": "पैकेट घुमाइए ताकि साइज़ वाला लेबल दिखे।",
    },
    "vision.aim.hold_closer": {
        "en": "Hold the pack a little closer to the camera.",
        "ta": "பாக்கெட்டை கேமராவுக்கு கொஞ்சம் அருகில் காட்டுங்கள்.",
        "hi": "पैकेट को कैमरे के थोड़ा पास लाइए।",
    },
    "vision.aim.not_visible": {
        "en": "I can't see it right now. Turn slowly to the left or right.",
        "ta": "இப்போது அது தெரியவில்லை. மெதுவாக இடது அல்லது வலது பக்கம் திரும்புங்கள்.",
        "hi": "अभी यह नहीं दिख रहा। धीरे से बाएँ या दाएँ मुड़िए।",
    },
}


def hint_text(key: str, lang: str) -> str:
    if not key:
        return ""
    entry = HINTS.get(key)
    if not entry:
        return ""
    return entry.get(lang) or entry["en"]
```

- [ ] **Step 4: Implement `services/live/aira_live/vision/count.py`**

```python
"""Count packs in one frame (crate / shelf snapshot) with Gemini Robotics-ER 2 pointing."""
from __future__ import annotations

import math

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

DEDUPE_RADIUS = 12     # two points closer than this (0–1000 space) are the same pack
OVERLAP_RADIUS = 35    # packs this close are probably stacked / overlapping
EDGE_MARGIN = 25       # a point this close to the frame edge is probably a cut-off pack


class CountResult(BaseModel):
    count: int | None
    confidence: float
    needs_retake: bool
    aim_hint: str


COUNT_PROMPT = (
    "You are counting stock for a blind shopkeeper. Point to EVERY individual {label} visible in this image, "
    "exactly one point per item, at the centre of each item. Do not point to anything else. "
    'For each item set "cut": true if part of it is outside the image edge or hidden behind another item. '
    "Points are [y, x] normalized to 0-1000. Return no more than 80 items."
)
COUNT_SCHEMA = '[{"point": [y, x], "label": "<label>", "cut": false}]'


def _is_point(p) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in p))


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def assess(items: list) -> CountResult:
    pts: list[tuple[float, float]] = []
    cut_flags: list[bool] = []
    for it in items or []:
        p = it.get("point") if isinstance(it, dict) else None
        if not _is_point(p):
            continue
        q = (float(p[0]), float(p[1]))
        if any(_dist(q, r) < DEDUPE_RADIUS for r in pts):
            continue
        pts.append(q)
        near_edge = min(q[0], q[1], 1000 - q[0], 1000 - q[1]) < EDGE_MARGIN
        cut_flags.append(bool(it.get("cut")) or near_edge)
    n = len(pts)
    if n == 0:
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.show_items")
    overlaps = sum(1 for i in range(n) for j in range(i + 1, n) if _dist(pts[i], pts[j]) < OVERLAP_RADIUS)
    overlap_ratio = min(1.0, overlaps / n)
    edge_ratio = sum(cut_flags) / n
    confidence = max(0.0, min(1.0, 1.0 - 0.6 * overlap_ratio - 0.4 * edge_ratio))
    if edge_ratio > 0:
        hint = "vision.aim.move_back"
    elif overlap_ratio > 0.15:
        hint = "vision.aim.spread_out"
    else:
        hint = ""
    return CountResult(count=n, confidence=round(confidence, 2), needs_retake=bool(hint), aim_hint=hint)


async def count_in_frame(frame: Frame, item_label: str) -> CountResult:
    data = await generate_json(
        "count",
        [image_part(frame.jpeg_b64), COUNT_PROMPT.format(label=item_label)],
        COUNT_SCHEMA,
        thinking="high",
        timeout_s=12.0,
    )
    items = data.get("items") or data.get("points") or []
    return assess(items)
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `cd services/live && uv run pytest -q tests/vision/test_hints.py tests/vision/test_count.py`
Expected: `10 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/vision/hints.py services/live/aira_live/vision/count.py services/live/tests/vision/test_hints.py services/live/tests/vision/test_count.py
git commit -m "feat(vision): ER 2 crate/shelf counting with dedupe, confidence and retake hints"
```

---

### Task 3: Product identification (`vision/identify.py`)

**Files:**
- Create: `services/live/aira_live/vision/identify.py`
- Test: `services/live/tests/vision/test_identify.py`

**Interfaces:**
- **Consumes:** `generate_json` (role `point`), `image_part`, `Frame`.
- **Produces:**
  - `class ProductRef(BaseModel)`: `sku`, `name`, `brand`, `pack`
  - `class IdentifyResult(BaseModel)`: `sku: str | None`, `confidence: float`, `alternatives: list[str]`
  - `def decide(data: dict, candidates: list[ProductRef]) -> IdentifyResult` (pure)
  - `def confusion_partners(sku: str, candidates: list[ProductRef]) -> list[str]`
  - `def next_hint(result: IdentifyResult, candidates: list[ProductRef]) -> str`. This is a **new cross-team name** (hint key). Kanish's one-by-one counting and Sarmitha's `product_identify` speak it.
  - `async def identify_product(frame: Frame, candidates: list[ProductRef]) -> IdentifyResult`

- [ ] **Step 1: Write the failing tests** in `services/live/tests/vision/test_identify.py`

```python
from aira_live.models import model_for
from aira_live.vision.identify import (ProductRef, confusion_partners, decide, identify_product, next_hint)

CANDS = [
    ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="500 ml pouch"),
    ProductRef(sku="aavin_milk_1l", name="Aavin milk", brand="Aavin", pack="1 L pouch"),
    ProductRef(sku="sakthi_curd_500ml", name="Sakthi curd", brand="Sakthi", pack="500 ml pouch"),
    ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L pouch"),
    ProductRef(sku="arun_icecream_box", name="Arun ice cream", brand="Arun", pack="family box"),
]


def test_confusion_partners_are_same_brand_other_pack():
    assert confusion_partners("sakthi_curd_1l", CANDS) == ["sakthi_curd_500ml"]
    assert confusion_partners("arun_icecream_box", CANDS) == []


def test_confident_label_visible_is_accepted():
    r = decide({"sku": "sakthi_curd_1l", "confidence": 0.93, "alternatives": [], "label_visible": True}, CANDS)
    assert r.sku == "sakthi_curd_1l" and r.confidence == 0.93


def test_unknown_sku_is_rejected():
    r = decide({"sku": "amul_butter", "confidence": 0.99}, CANDS)
    assert r.sku is None and r.confidence == 0.0


def test_low_confidence_abstains():
    r = decide({"sku": "arun_icecream_box", "confidence": 0.4}, CANDS)
    assert r.sku is None and r.alternatives[0] == "arun_icecream_box"
    assert next_hint(r, CANDS) == "vision.aim.hold_closer"


def test_confusion_pair_needs_label():
    r = decide({"sku": "aavin_milk_1l", "confidence": 0.72, "alternatives": ["aavin_milk_500ml"]}, CANDS)
    assert r.sku is None and r.alternatives[:2] == ["aavin_milk_1l", "aavin_milk_500ml"]
    assert next_hint(r, CANDS) == "vision.aim.show_label"


def test_label_not_visible_blocks_even_high_confidence():
    r = decide({"sku": "sakthi_curd_500ml", "confidence": 0.95, "label_visible": False}, CANDS)
    assert r.sku is None
    assert next_hint(r, CANDS) == "vision.aim.show_label"


def test_no_partner_accepts_at_moderate_confidence():
    r = decide({"sku": "arun_icecream_box", "confidence": 0.7, "label_visible": False}, CANDS)
    assert r.sku == "arun_icecream_box"
    assert next_hint(r, CANDS) == ""


async def test_identify_product_uses_point_role_and_lists_candidates(fake_genai, frame):
    client = fake_genai('{"sku": "aavin_milk_500ml", "confidence": 0.9, "alternatives": [], "label_visible": true}')
    r = await identify_product(frame, CANDS)
    assert r.sku == "aavin_milk_500ml"
    call = client.aio.models.calls[0]
    assert call["model"] == model_for("point")
    assert "sakthi_curd_1l" in " ".join(str(p) for p in call["contents"])


async def test_identify_product_without_candidates_makes_no_call(fake_genai, frame):
    client = fake_genai()
    r = await identify_product(frame, [])
    assert r.sku is None and client.aio.models.calls == []
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd services/live && uv run pytest -q tests/vision/test_identify.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.identify'`.

- [ ] **Step 3: Implement `services/live/aira_live/vision/identify.py`**

```python
"""Which product is Murugan holding? ER 2 picks from a candidate list; code decides whether to trust it."""
from __future__ import annotations

import json

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

ACCEPT = 0.6             # below this we never name a product
CONFUSION_ACCEPT = 0.8   # same brand, different pack (500 ml vs 1 L) needs more certainty AND a visible label


class ProductRef(BaseModel):
    sku: str
    name: str
    brand: str
    pack: str


class IdentifyResult(BaseModel):
    sku: str | None
    confidence: float
    alternatives: list[str]


IDENTIFY_PROMPT = (
    "A blind shopkeeper is holding up ONE product to the camera. Which ONE of these candidates is it?\n"
    "Candidates (JSON): {cands}\n"
    "Use the brand logo, colours and the printed pack size (for example 500 ml vs 1 L). "
    "If the pack-size text is not readable, set label_visible to false. "
    "If none of the candidates match, set sku to null."
)
IDENTIFY_SCHEMA = '{"sku": "<one candidate sku or null>", "confidence": 0.0, "alternatives": ["<sku>"], "label_visible": true}'


def confusion_partners(sku: str, candidates: list[ProductRef]) -> list[str]:
    me = next((c for c in candidates if c.sku == sku), None)
    if me is None:
        return []
    return [c.sku for c in candidates if c.brand == me.brand and c.sku != sku and c.pack != me.pack]


def decide(data: dict, candidates: list[ProductRef]) -> IdentifyResult:
    skus = {c.sku for c in candidates}
    raw = data.get("sku")
    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    label_visible = bool(data.get("label_visible", True))
    alts = [s for s in (data.get("alternatives") or []) if s in skus and s != raw]
    if raw not in skus:
        return IdentifyResult(sku=None, confidence=0.0, alternatives=alts[:3])
    partners = confusion_partners(raw, candidates)
    if conf < ACCEPT:
        return IdentifyResult(sku=None, confidence=conf, alternatives=[raw, *alts][:3])
    if partners and (conf < CONFUSION_ACCEPT or not label_visible):
        ordered = [raw, *partners, *[a for a in alts if a not in partners]]
        return IdentifyResult(sku=None, confidence=conf, alternatives=ordered[:3])
    return IdentifyResult(sku=raw, confidence=conf, alternatives=alts[:3])


def next_hint(result: IdentifyResult, candidates: list[ProductRef]) -> str:
    if result.sku:
        return ""
    if result.alternatives and any(
            p in result.alternatives for p in confusion_partners(result.alternatives[0], candidates)):
        return "vision.aim.show_label"
    return "vision.aim.hold_closer"


async def identify_product(frame: Frame, candidates: list[ProductRef]) -> IdentifyResult:
    if not candidates:
        return IdentifyResult(sku=None, confidence=0.0, alternatives=[])
    cands = json.dumps([c.model_dump() for c in candidates], ensure_ascii=False)
    data = await generate_json(
        "point",
        [image_part(frame.jpeg_b64), IDENTIFY_PROMPT.format(cands=cands)],
        IDENTIFY_SCHEMA,
        thinking="low",
        timeout_s=6.0,
    )
    return decide(data, candidates)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd services/live && uv run pytest -q tests/vision/test_identify.py`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/vision/identify.py services/live/tests/vision/test_identify.py
git commit -m "feat(vision): product identification with confusion-pair guard (500 ml vs 1 L)"
```

---

### Task 4: Pointing to a product (`vision/point.py`)

**Files:**
- Create: `services/live/aira_live/vision/point.py`
- Test: `services/live/tests/vision/test_point.py`

**Interfaces:**
- **Consumes:** `generate_json` (role `point`), `image_part`, `Frame`.
- **Produces:**
  - `class TargetResult(BaseModel)`: `label`, `point: tuple[int, int]`, `box: tuple[int, int, int, int] | None`, `confidence: float`
  - `def parse_target(data: dict, label: str) -> TargetResult | None` (pure)
  - `async def point_to(frame: Frame, target_label: str) -> TargetResult | None`
- **Note:** plan 03's `product_find` turns a `TargetResult` into the `target` ServerMsg for the app's guidance tones.

- [ ] **Step 1: Write the failing tests** in `services/live/tests/vision/test_point.py`

```python
from aira_live.models import model_for
from aira_live.vision.point import parse_target, point_to


def test_valid_point_and_box():
    t = parse_target({"point": [412, 530], "box": [390, 505, 435, 555], "label": "Sakthi curd", "confidence": 0.8}, "curd")
    assert t.point == (412, 530) and t.box == (390, 505, 435, 555) and t.label == "Sakthi curd"


def test_not_visible_returns_none():
    assert parse_target({"point": None}, "curd") is None
    assert parse_target({}, "curd") is None


def test_out_of_range_or_malformed_point_is_none():
    assert parse_target({"point": [1200, 10]}, "curd") is None
    assert parse_target({"point": [10]}, "curd") is None


def test_low_confidence_is_none():
    assert parse_target({"point": [100, 100], "confidence": 0.1}, "curd") is None


def test_box_not_containing_point_is_dropped():
    t = parse_target({"point": [100, 100], "box": [500, 500, 600, 600], "confidence": 0.9}, "curd")
    assert t is not None and t.box is None


def test_missing_label_uses_requested_label_and_default_confidence():
    t = parse_target({"point": [250.4, 750.6]}, "Aavin milk")
    assert t.label == "Aavin milk" and t.point == (250, 751) and t.confidence == 0.5


async def test_point_to_handles_list_reply(fake_genai, frame):
    client = fake_genai('[{"point": [300, 400], "label": "Arun ice cream box"}]')
    t = await point_to(frame, "Arun ice cream box")
    assert t.point == (300, 400)
    assert client.aio.models.calls[0]["model"] == model_for("point")


async def test_point_to_not_visible(fake_genai, frame):
    fake_genai('{"point": null}')
    assert await point_to(frame, "Sakthi curd 1 L") is None
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd services/live && uv run pytest -q tests/vision/test_point.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.point'`.

- [ ] **Step 3: Implement `services/live/aira_live/vision/point.py`**

```python
"""Point to one product so the app can guide Murugan's hand. [y, x] and boxes stay normalized 0–1000."""
from __future__ import annotations

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

MIN_CONF = 0.3

POINT_PROMPT = (
    "Point to the {label} in this image. If it is not visible, return {{\"point\": null}}. "
    "Points are [y, x] and boxes are [ymin, xmin, ymax, xmax], all normalized to 0-1000."
)
POINT_SCHEMA = '{"point": [y, x] or null, "box": [ymin, xmin, ymax, xmax] or null, "label": "<label>", "confidence": 0.0}'


class TargetResult(BaseModel):
    label: str
    point: tuple[int, int]
    box: tuple[int, int, int, int] | None
    confidence: float


def _nums_in_range(v, n: int) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == n
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1000 for x in v))


def parse_target(data: dict, label: str) -> TargetResult | None:
    p = data.get("point")
    if not _nums_in_range(p, 2):
        return None
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    if conf < MIN_CONF:
        return None
    y, x = round(p[0]), round(p[1])
    box = None
    b = data.get("box")
    if _nums_in_range(b, 4):
        ymin, xmin, ymax, xmax = (round(v) for v in b)
        if ymin <= y <= ymax and xmin <= x <= xmax:
            box = (ymin, xmin, ymax, xmax)
    return TargetResult(label=data.get("label") or label, point=(y, x), box=box, confidence=conf)


async def point_to(frame: Frame, target_label: str) -> TargetResult | None:
    data = await generate_json(
        "point",
        [image_part(frame.jpeg_b64), POINT_PROMPT.format(label=target_label)],
        POINT_SCHEMA,
        thinking="low",
        timeout_s=5.0,
    )
    if "items" in data:  # ER 2 often answers with its list format
        items = [i for i in data["items"] if isinstance(i, dict)]
        data = items[0] if items else {}
    return parse_target(data, target_label)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd services/live && uv run pytest -q tests/vision/test_point.py`
Expected: `8 passed`.

- [ ] **Step 5: Run the whole vision suite**

Run: `cd services/live && uv run pytest -q tests/vision`
Expected: `34 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/vision/point.py services/live/tests/vision/test_point.py
git commit -m "feat(vision): point_to with range/box validation for hand guidance"
```

---

### Task 5: ER 2 spike (latency + accuracy), with the Flash switch decision

**Files:**
- Create: `bench/common.py`, `bench/spike_er2.py`, `bench/data/identify/candidates.json`, `bench/data/packs/labels.json`
- Modify: `.gitignore` (add `bench/results/`)
- Test: `bench/tests/test_common.py`

**Interfaces:**
- **Consumes:** `identify_product`, `ProductRef`, `fallback_for`, `Frame`.
- **Produces:**
  - Helpers: `load_labels(path, required: set[str]) -> list[dict]`, `frame_from_file(path, purpose) -> Frame`, `percentile(values, p) -> float`, `async timed(coro) -> tuple[Any, float]`, `write_csv(path, rows)`, `load_candidates() -> list[ProductRef]`
  - `spike_er2.decide(er2_p50_ms: float) -> str` returning `"KEEP"` or `"SWITCH"`
  - **Labels format** for `bench/data/packs/labels.json`: `[{"file": "p01.jpg", "sku": "<sku>"}]`

- [ ] **Step 1: Take the photos (D0 evening, real data)**

1. Photograph **20 pack photos** with the chest-mounted phone, about 40–60 cm from the pack, like Murugan holding it up:
   - 4 × each of `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `arun_icecream_box`
   - In each set of 4: 2 with the label facing the camera, 1 tilted, 1 in shop lighting.
2. Save them as `bench/data/packs/p01.jpg` … `p20.jpg`.
3. Write one row per photo in `bench/data/packs/labels.json` using exactly this format:

```json
[
  {"file": "p01.jpg", "sku": "aavin_milk_500ml"},
  {"file": "p02.jpg", "sku": "aavin_milk_500ml"}
]
```

Continue to `p20.jpg`. Every row needs a `sku` from `candidates.json`.

- [ ] **Step 2: Create `bench/data/identify/candidates.json`.** These are the demo-shop SKUs, which must match Kanish's catalog.

```json
[
  {"sku": "aavin_milk_500ml", "name": "Aavin milk", "brand": "Aavin", "pack": "500 ml pouch"},
  {"sku": "aavin_milk_1l", "name": "Aavin milk", "brand": "Aavin", "pack": "1 L pouch"},
  {"sku": "sakthi_curd_500ml", "name": "Sakthi curd", "brand": "Sakthi", "pack": "500 ml pouch"},
  {"sku": "sakthi_curd_1l", "name": "Sakthi curd", "brand": "Sakthi", "pack": "1 L pouch"},
  {"sku": "arun_icecream_box", "name": "Arun ice cream", "brand": "Arun", "pack": "family box"}
]
```

- [ ] **Step 3: Write the failing test** in `bench/tests/test_common.py`

```python
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

import common  # noqa: E402
import spike_er2  # noqa: E402


def test_percentile_linear_interpolation():
    assert common.percentile([100, 200, 300, 400], 50) == 250
    assert common.percentile([100], 95) == 100
    assert common.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == pytest.approx(9.55)


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        common.percentile([], 50)


def test_load_labels_requires_keys(tmp_path):
    good = tmp_path / "labels.json"
    good.write_text(json.dumps([{"file": "p01.jpg", "sku": "aavin_milk_1l"}]))
    assert common.load_labels(good, {"file", "sku"})[0]["sku"] == "aavin_milk_1l"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"file": "p01.jpg"}]))
    with pytest.raises(ValueError):
        common.load_labels(bad, {"file", "sku"})


def test_candidates_file_matches_demo_skus():
    skus = [c.sku for c in common.load_candidates()]
    assert skus == ["aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "arun_icecream_box"]


def test_spike_decision_threshold():
    assert spike_er2.decide(2999) == "KEEP"
    assert spike_er2.decide(3001) == "SWITCH"
```

- [ ] **Step 4: Run the test to confirm it fails**

Run: `cd services/live && uv run pytest -q ../../bench/tests/test_common.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'common'`.

- [ ] **Step 5: Implement `bench/common.py`**

```python
"""Shared helpers for AIRA vision benchmarks. Run with the services/live venv:
   cd services/live && uv run python ../../bench/<script>.py ..."""
from __future__ import annotations

import base64
import csv
import json
import time
from pathlib import Path
from typing import Any

from aira_live.frames import Frame
from aira_live.vision.identify import ProductRef

BENCH = Path(__file__).resolve().parent


def load_labels(path: Path, required: set[str]) -> list[dict]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    for i, r in enumerate(rows):
        missing = required - set(r)
        if missing:
            raise ValueError(f"{path} row {i} missing {sorted(missing)}")
    return rows


def frame_from_file(path: Path, purpose: str) -> Frame:
    data = Path(path).read_bytes()
    return Frame(id=Path(path).stem, jpeg_b64=base64.b64encode(data).decode(), w=0, h=0,
                 ts=time.time() * 1000, purpose=purpose)


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("no values")
    v = sorted(values)
    if len(v) == 1:
        return float(v[0])
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return float(v[lo] + (v[hi] - v[lo]) * (k - lo))


async def timed(coro) -> tuple[Any, float]:
    t0 = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - t0) * 1000


def write_csv(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def load_candidates() -> list[ProductRef]:
    raw = json.loads((BENCH / "data" / "identify" / "candidates.json").read_text(encoding="utf-8"))
    return [ProductRef(**c) for c in raw]
```

- [ ] **Step 6: Implement `bench/spike_er2.py`**

```python
"""D0 spike: is Gemini Robotics-ER 2 fast and accurate enough for identify/point?
Usage: cd services/live && uv run python ../../bench/spike_er2.py --data ../../bench/data/packs --out ../../bench/results/spike_er2.csv
Needs GEMINI_API_KEY in the environment (or services/live/.env)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import frame_from_file, load_candidates, load_labels, percentile, timed, write_csv  # noqa: E402
from aira_live.models import fallback_for, model_for  # noqa: E402
from aira_live.vision.identify import identify_product  # noqa: E402

THRESHOLD_MS = 3000


def decide(er2_p50_ms: float) -> str:
    return "SWITCH" if er2_p50_ms > THRESHOLD_MS else "KEEP"


async def run_pass(label: str, override: str | None, rows: list[dict], data_dir: Path) -> list[dict]:
    if override:
        os.environ["AIRA_MODEL_POINT"] = override
    else:
        os.environ.pop("AIRA_MODEL_POINT", None)
    cands = load_candidates()
    out = []
    for r in rows:
        frame = frame_from_file(data_dir / r["file"], "identify")
        try:
            res, ms = await timed(identify_product(frame, cands))
            pred, conf, err = res.sku, res.confidence, ""
        except Exception as e:  # record failures instead of stopping the spike
            pred, conf, ms, err = None, 0.0, float("nan"), repr(e)
        out.append({"pass": label, "model": model_for("point"), "file": r["file"], "truth": r["sku"],
                    "pred": pred, "confidence": conf, "ms": round(ms, 1), "error": err})
    return out


def summarize(rows: list[dict]) -> dict:
    ms = [r["ms"] for r in rows if r["ms"] == r["ms"]]  # drop NaN
    correct = sum(1 for r in rows if r["pred"] == r["truth"])
    abstain = sum(1 for r in rows if r["pred"] is None)
    return {"n": len(rows), "p50_ms": round(percentile(ms, 50)) if ms else None,
            "p95_ms": round(percentile(ms, 95)) if ms else None,
            "accuracy": round(correct / len(rows), 3), "abstain": round(abstain / len(rows), 3)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path(__file__).parent / "data" / "packs")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results" / "spike_er2.csv")
    args = ap.parse_args()
    rows = load_labels(args.data / "labels.json", {"file", "sku"})
    er2 = await run_pass("er2", None, rows, args.data)
    flash = await run_pass("flash", fallback_for("point"), rows, args.data)
    os.environ.pop("AIRA_MODEL_POINT", None)
    write_csv(args.out, er2 + flash)
    s_er2, s_flash = summarize(er2), summarize(flash)
    print(f"ER 2  : {s_er2}")
    print(f"Flash : {s_flash}")
    if decide(s_er2["p50_ms"] or 10**9) == "KEEP":
        print("DECISION: KEEP ER 2 for identify + point (role 'point').")
    else:
        print(f"DECISION: SWITCH identify + point to {fallback_for('point')}: set Remote Config "
              f"pointing_model={fallback_for('point')} (or env AIRA_MODEL_POINT on aira-live). "
              "Counting stays on ER 2 (role 'count').")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Ignore results and run the tests**

Add the line `bench/results/` to `.gitignore`.

Run: `cd services/live && uv run pytest -q ../../bench/tests/test_common.py`
Expected: `5 passed`.

- [ ] **Step 8: Run the live spike** (needs a real key)

Run: `cd services/live && uv run python ../../bench/spike_er2.py`
Expected: two summary lines (`ER 2 : {...}`, `Flash : {...}`) and one `DECISION:` line.

Post the two summary lines and the decision in the team chat. If the decision is `SWITCH`, Saravana sets Remote Config `pointing_model` (and `AIRA_MODEL_POINT` on `aira-live`) to the Flash fallback.

- [ ] **Step 9: Commit** (code and labels only; photos only if the team agrees to publish them)

```bash
git add bench/common.py bench/spike_er2.py bench/tests/test_common.py bench/data/identify/candidates.json bench/data/packs/labels.json .gitignore
git commit -m "bench: ER 2 vs Flash identify spike with p50/p95, accuracy and switch decision"
```

---

### Task 6: Scorers for counting and identification (numbers for the deck)

**Files:**
- Create: `bench/score_count.py`, `bench/score_identify.py`, `bench/data/count/labels.json`, `bench/data/identify/labels.json`
- Test: `bench/tests/test_scorers.py`

**Interfaces:**
- **Consumes:** `count_in_frame`, `identify_product`, `bench/common.py`.
- **Produces** (pure functions):
  - `score_count.score_counts(rows) -> dict` with keys `n`, `exact`, `within1`, `mae`, `abstain`
  - `score_identify.score_identify(rows) -> dict` with keys `n`, `accuracy`, `abstain`, `wrong_confident`, `pairs`
- **Outputs:** `bench/results/count.csv|json` and `bench/results/identify.csv|json`. Saravana's `03-submission` reads the JSON files for the benchmark slide.
- **Labels formats:**
  - `bench/data/count/labels.json`: `[{"file": "c01.jpg", "label": "Sakthi curd 1 L pouch", "count": 12}]`
  - `bench/data/identify/labels.json`: `[{"file": "i01.jpg", "sku": "aavin_milk_1l"}]`

- [ ] **Step 1: Collect the data**

1. **Counting set:**
   - **20 photos** of crates and shelves with a known number of packs: 4–30 packs each, a mix of milk, curd and ice-cream boxes, and at least 5 with packs partly overlapping.
   - Save them as `bench/data/count/c01.jpg` … `c20.jpg`.
   - Write the labels using this format:

   ```json
   [{"file": "c01.jpg", "label": "Sakthi curd 1 L pouch", "count": 12}]
   ```

2. **Identification set:**
   - **30 held-pack photos**, different from the spike photos: 6 per SKU.
   - Save them as `bench/data/identify/i01.jpg` … `i30.jpg`.
   - Write the labels using this format:

   ```json
   [{"file": "i01.jpg", "sku": "aavin_milk_1l"}]
   ```

- [ ] **Step 2: Write the failing tests** in `bench/tests/test_scorers.py`

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score_count import score_counts  # noqa: E402
from score_identify import score_identify  # noqa: E402


def test_score_counts():
    rows = [
        {"truth": 12, "pred": 12},
        {"truth": 10, "pred": 9},
        {"truth": 8, "pred": None},   # abstained (needs retake)
        {"truth": 20, "pred": 17},
    ]
    s = score_counts(rows)
    assert s["n"] == 4
    assert s["exact"] == 0.25
    assert s["within1"] == 0.5
    assert s["abstain"] == 0.25
    assert s["mae"] == pytest.approx((0 + 1 + 3) / 3, abs=1e-3)


def test_score_identify():
    rows = [
        {"truth": "aavin_milk_1l", "pred": "aavin_milk_1l"},
        {"truth": "aavin_milk_1l", "pred": None},
        {"truth": "sakthi_curd_1l", "pred": "sakthi_curd_500ml"},
        {"truth": "arun_icecream_box", "pred": "arun_icecream_box"},
    ]
    s = score_identify(rows)
    assert s["accuracy"] == 0.5
    assert s["abstain"] == 0.25
    assert s["wrong_confident"] == 0.25
    assert s["pairs"] == {"sakthi_curd_1l->sakthi_curd_500ml": 1}
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `cd services/live && uv run pytest -q ../../bench/tests/test_scorers.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'score_count'`.

- [ ] **Step 4: Implement `bench/score_count.py`**

```python
"""Counting accuracy on bench/data/count. Usage:
cd services/live && uv run python ../../bench/score_count.py"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import frame_from_file, load_labels, percentile, timed, write_csv  # noqa: E402


def score_counts(rows: list[dict]) -> dict:
    n = len(rows)
    answered = [r for r in rows if r["pred"] is not None]
    exact = sum(1 for r in answered if r["pred"] == r["truth"])
    within1 = sum(1 for r in answered if abs(r["pred"] - r["truth"]) <= 1)
    mae = sum(abs(r["pred"] - r["truth"]) for r in answered) / len(answered) if answered else None
    return {"n": n, "exact": round(exact / n, 3), "within1": round(within1 / n, 3),
            "mae": round(mae, 3) if mae is not None else None,
            "abstain": round((n - len(answered)) / n, 3)}


async def main() -> None:
    from aira_live.vision.count import count_in_frame

    data = Path(__file__).parent / "data" / "count"
    labels = load_labels(data / "labels.json", {"file", "label", "count"})
    rows = []
    for r in labels:
        res, ms = await timed(count_in_frame(frame_from_file(data / r["file"], "count"), r["label"]))
        rows.append({"file": r["file"], "truth": r["count"], "pred": None if res.needs_retake else res.count,
                     "raw_count": res.count, "confidence": res.confidence, "aim_hint": res.aim_hint,
                     "ms": round(ms, 1)})
    summary = score_counts(rows)
    summary["p50_ms"] = round(percentile([r["ms"] for r in rows], 50))
    summary["p95_ms"] = round(percentile([r["ms"] for r in rows], 95))
    out = Path(__file__).parent / "results"
    write_csv(out / "count.csv", rows)
    (out / "count.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Implement `bench/score_identify.py`**

```python
"""Identification accuracy on bench/data/identify. Usage:
cd services/live && uv run python ../../bench/score_identify.py"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import frame_from_file, load_candidates, load_labels, percentile, timed, write_csv  # noqa: E402


def score_identify(rows: list[dict]) -> dict:
    n = len(rows)
    correct = sum(1 for r in rows if r["pred"] == r["truth"])
    abstain = sum(1 for r in rows if r["pred"] is None)
    wrong = [r for r in rows if r["pred"] is not None and r["pred"] != r["truth"]]
    pairs = Counter(f'{r["truth"]}->{r["pred"]}' for r in wrong)
    return {"n": n, "accuracy": round(correct / n, 3), "abstain": round(abstain / n, 3),
            "wrong_confident": round(len(wrong) / n, 3), "pairs": dict(pairs)}


async def main() -> None:
    from aira_live.vision.identify import identify_product

    data = Path(__file__).parent / "data" / "identify"
    labels = load_labels(data / "labels.json", {"file", "sku"})
    cands = load_candidates()
    rows = []
    for r in labels:
        res, ms = await timed(identify_product(frame_from_file(data / r["file"], "identify"), cands))
        rows.append({"file": r["file"], "truth": r["sku"], "pred": res.sku,
                     "confidence": res.confidence, "ms": round(ms, 1)})
    summary = score_identify(rows)
    summary["p50_ms"] = round(percentile([r["ms"] for r in rows], 50))
    summary["p95_ms"] = round(percentile([r["ms"] for r in rows], 95))
    out = Path(__file__).parent / "results"
    write_csv(out / "identify.csv", rows)
    (out / "identify.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run the tests, then the live scorers**

1. Run: `cd services/live && uv run pytest -q ../../bench/tests`
   Expected: `7 passed`.
2. Run: `cd services/live && uv run python ../../bench/score_count.py && uv run python ../../bench/score_identify.py`
   Expected: one JSON summary line each, e.g. `{"n": 20, "exact": ..., "within1": ..., "mae": ..., "abstain": ..., "p50_ms": ..., "p95_ms": ...}`. The files `bench/results/count.json` and `identify.json` are written.
3. Post both summaries to Saravana for the benchmark slide. **Report abstentions honestly**: they are AIRA saying "show me again" instead of guessing.

- [ ] **Step 7: Commit**

```bash
git add bench/score_count.py bench/score_identify.py bench/tests/test_scorers.py bench/data/count/labels.json bench/data/identify/labels.json
git commit -m "bench: counting and identification scorers for the benchmark slide"
```
