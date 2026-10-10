# AIRA: Memory & Product Find (Sarmitha · 03)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AIRA a durable shop memory: where each product sits, what happened today, and the owner's preferences. On top of it, build the `product_find` / `product_identify` / `location_remember` tools. Murugan says "2 litres curd", and AIRA says where the curd was last recorded, guides his hand to it with live pointing, and checks the pack he touches: "Sakthi curd 1 L ✓. Take 1 more."

**Architecture:**
- Three small memory modules (`memory/spatial.py`, `memory/episodic.py`, `memory/prefs.py`) read and write Firestore under `shops/{shopId}/…`, exactly as spec §5.
- A pure `memory/zones.py` turns spoken places ("fridge two, middle shelf, left") into a canonical zone (`fridge2/middle/left`) and back into speech in ta/hi/en.
- `tools/guidance.py` runs a per-session loop:
  1. Takes the newest continuous `guide` frame (about 1 fps).
  2. Calls `vision.point_to` and sends a `target` message to the app, which plays the tones.
  3. When the app reports `fingertip_in_target`, calls `vision.identify_product` on the latest frame and speaks the result.
- `tools/products.py` exposes the three Live tools. Memory never touches stock or money; it only remembers and guides.

**Tech Stack:** Python 3.12, FastAPI service `aira-live`, pydantic v2, `google-cloud-firestore` AsyncClient, pytest + pytest-asyncio (`asyncio_mode = "auto"`), uv. Tests use an in-memory fake Firestore; there is no emulator dependency.

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md`: §2 steps 5–6, §3 (honest uncertainty), §5 (`locations`, `memoryEvents`, `prefs/main`), §7 (guidance time-to-touch).
**Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`, sections Memory, Vision, Live tools, Server core.

**Owner / schedule:** Sarmitha. D4 (Oct 13) Tasks 1–3; D5 (Oct 14) Tasks 4–5. Prerequisites:
- the foundation plan is merged (`aira_live.db`, `aira_live.session`, `aira_live.frames`, `aira_live.contracts`, `aira_live.i18n`, `aira_live.sessions`, tool registry)
- `sarmitha/01-vision-er2` is merged (`point_to`, `identify_product`, `ProductRef`)
- Kanish's `business/catalog.resolve`: until it merges, the tests replace it with a fake

## Global Constraints

- **Exact names from the interface registry:**
  - `remember(shop_id, sku, zone, confirmed_by, confidence) -> Location`
  - `where(shop_id, sku) -> Location | None`
  - `log(shop_id, type, text, ref_type=None, ref_id=None) -> str`
  - `recent(shop_id, limit=20) -> list[dict]`
  - `prefs.get(shop_id) -> dict`
  - `prefs.set(shop_id, **kw) -> dict`
  - `Location` fields: `sku, zone, confirmed_by ("user"|"model"), confirmed_at, confidence`
- **Firestore paths:**
  - `shops/{shopId}/locations/{sku}` with fields `sku, zone, confirmedBy, confirmedAt, confidence`
  - `shops/{shopId}/memoryEvents/{id}` with fields `type, text, refType, refId, at`
  - `shops/{shopId}/prefs/main` with fields `speechRate, verbosity, productAliases, supplierAliases`
- **Modules call `dbmod.db()` at runtime** (`from aira_live import db as dbmod`) so tests can swap in the fake Firestore.
- **Staleness rule:** a location is **stale** if `confirmed_by != "user"` or it is older than **3 days**. Stale locations are spoken as "last recorded on … but it may have moved". A stale location is never spoken as fact.
- **Thresholds:** point confidence ≥ **0.3** to send a `target`; identification confidence ≥ **0.6** to accept a pick. Below that, AIRA says "Not sure" and never guesses.
- **Coordinates** are passed through unchanged: `point [y, x]` and `box [ymin, xmin, ymax, xmax]`, normalised 0–1000.
- **Frame purposes:** continuous guidance uses `"guide"`; a one-shot "what is in my hand" uses `"identify"`. Guidance starts with `request_frame {purpose:"guide", continuous:true}` and ends with `{purpose:"guide", stop:true}`.
- **Speech:** every spoken string comes from `aira_live.i18n.t(key, lang, **kw)`. Add each new key to `i18n/en.json`, `ta.json` and `hi.json` together.
- **Contract class names:** the plan assumes the foundation's Python classes are `RequestFrameMsg` (fields `purpose, burst, continuous, stop`) and `TargetMsg` (fields `frame_id, label, point, box, confidence`). If the foundation named them differently, change only the imports.
- **New cross-team hooks (Saravana's `main.py` must call them):**
  - `aira_live.tools.guidance.handle_client_event(session, name, data)` for every client `event` message
  - `aira_live.tools.guidance.stop_guidance(session)` on a `stop` message and on disconnect
- **Tool signature extension:** `product_find(product: str, count_needed: int = 1)`. The parameter is optional, so existing callers still work.

## Review Focus

1. **Stale location presented as fact.** A model-observed location, or one confirmed more than 3 days ago, must say "may have moved". Tested by `test_describe_location_stale_when_model_confirmed` and `test_describe_location_stale_after_three_days` (Task 1).
2. **Look-alike pack picked** (Aavin milk 500 ml instead of Sakthi curd 1 L). AIRA must say "This is Aavin milk 500 ml, not Sakthi curd 1 L" and must not count it. Tested by `test_touch_wrong_product_is_not_counted` (Task 4).
3. **Camera covered or no frames arriving.** AIRA must say "I can't see …" **once** and not repeat it every second. Tested by `test_step_announces_lost_once` (Task 4).
4. **A late or duplicate fingertip event after the pick is complete.** It must be ignored: no extra count, no second announcement, no crash. Tested by `test_duplicate_touch_after_done_is_ignored` (Task 4).
5. **Unknown product name** ("give me some paneer" when the shop has none). AIRA must say "I don't know …" and must **not** start the camera. Tested by `test_product_find_unknown_product_starts_nothing` (Task 4).

---

## File Structure

```
services/live/aira_live/memory/__init__.py        (create, empty)
services/live/aira_live/memory/zones.py           normalize_zone(), speak_zone()
services/live/aira_live/memory/spatial.py         Location, remember(), where(), is_stale(), describe_location()
services/live/aira_live/memory/episodic.py        log(), recent()
services/live/aira_live/memory/prefs.py           get(), set()
services/live/aira_live/tools/guidance.py         GuidanceLoop, start(), handle_client_event(), stop_guidance()
services/live/aira_live/tools/products.py         product_find(), product_identify(), location_remember(), shop_products()
services/live/aira_live/tools/__init__.py         (modify) register the three tools
services/live/aira_live/i18n/{en,ta,hi}.json      (modify) add memory/guidance keys
services/live/tests/memory/conftest.py            FakeDB (in-memory Firestore), FakeSession, fixtures
services/live/tests/memory/test_zones_spatial.py
services/live/tests/memory/test_episodic_prefs.py
services/live/tests/memory/test_products_tools.py
services/live/tests/memory/test_guidance.py
bench/score_guidance.py                           time-to-touch scorer (stdlib only)
bench/tests/test_score_guidance.py
bench/data/guidance/trials.example.json           log format example (real data recorded D6)
```

---

### Task 1: Zones + spatial memory with staleness

**Files:**
- Create: `services/live/aira_live/memory/__init__.py`, `services/live/aira_live/memory/zones.py`, `services/live/aira_live/memory/spatial.py`
- Create: `services/live/tests/memory/conftest.py`
- Test: `services/live/tests/memory/test_zones_spatial.py`

**Interfaces:**
- Consumes:
  - `aira_live.db.db() -> AsyncClient`
  - `aira_live.i18n.t(key, lang, **kw) -> str`
- Produces:
  - `normalize_zone(text: str) -> str`
  - `speak_zone(zone: str, lang: Lang) -> str`
  - `Location`
  - `async remember(shop_id, sku, zone, confirmed_by, confidence) -> Location`
  - `async where(shop_id, sku) -> Location | None`
  - `is_stale(loc: Location, now: datetime) -> bool`
  - `describe_location(name: str, loc: Location | None, lang: Lang, now: datetime) -> str`
  - `_utcnow()` (module function, patched in tests)

- [ ] **Step 0: Create the branch**

```bash
git checkout main && git pull && git checkout -b sarmitha/memory-product-find
```

- [ ] **Step 1: Write the fake Firestore and session fixtures (`tests/memory/conftest.py`)**

```python
"""In-memory Firestore + session fakes for memory/product tests (no emulator needed)."""
import itertools

import pytest

from aira_live.frames import FrameStore


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class FakeQuery:
    def __init__(self, coll, order=None, desc=False, lim=None):
        self._coll, self._order, self._desc, self._lim = coll, order, desc, lim

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._coll, field, direction == "DESCENDING", self._lim)

    def limit(self, n):
        return FakeQuery(self._coll, self._order, self._desc, n)

    async def stream(self):
        docs = self._coll._docs()
        if self._order:
            docs.sort(key=lambda s: s.to_dict()[self._order], reverse=self._desc)
        if self._lim is not None:
            docs = docs[: self._lim]
        for d in docs:
            yield d


class FakeCollection(FakeQuery):
    _ids = itertools.count(1)

    def __init__(self, store, path):
        self._store, self._path = store, path
        super().__init__(self)

    def document(self, doc_id=None):
        return FakeDoc(self._store, self._path + (doc_id or f"auto{next(FakeCollection._ids)}",))

    def _docs(self):
        n = len(self._path) + 1
        return [FakeSnapshot(p[-1], v) for p, v in self._store.items() if len(p) == n and p[:-1] == self._path]


class FakeDoc:
    def __init__(self, store, path):
        self._store, self._path = store, path
        self.id = path[-1]

    def collection(self, name):
        return FakeCollection(self._store, self._path + (name,))

    async def get(self):
        return FakeSnapshot(self.id, self._store.get(self._path))

    async def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path] = {**self._store[self._path], **data}
        else:
            self._store[self._path] = dict(data)

    async def delete(self):
        self._store.pop(self._path, None)


class FakeDB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, (name,))


class FakeSession:
    def __init__(self, shop_id="demo", lang="en"):
        self.session_id, self.uid, self.shop_id = "s1", "u1", shop_id
        self.lang, self.verbosity = lang, "short"
        self.frames = FrameStore()
        self.sent, self.requested = [], []

    async def send(self, msg):
        self.sent.append(msg)

    async def request_frames(self, purpose, count=1, interval_ms=0, timeout_s=4.0):
        self.requested.append(purpose)
        return self.frames.latest(count, purpose=purpose)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr("aira_live.db.db", lambda: db)
    return db


@pytest.fixture
def session():
    return FakeSession()
```

- [ ] **Step 2: Write the failing tests (`tests/memory/test_zones_spatial.py`)**

```python
from datetime import datetime, timedelta, timezone

import pytest

from aira_live.memory import spatial
from aira_live.memory.zones import normalize_zone, speak_zone

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def test_normalize_zone_spoken_forms():
    assert normalize_zone("Fridge 2, middle shelf, left") == "fridge2/middle/left"
    assert normalize_zone("fridge two top") == "fridge2/top"
    assert normalize_zone("fridge2/middle/left") == "fridge2/middle/left"
    assert normalize_zone("counter") == "counter"


def test_normalize_zone_rejects_empty():
    with pytest.raises(ValueError):
        normalize_zone("  the shelf ")


def test_speak_zone_three_languages():
    assert speak_zone("fridge2/middle/left", "en") == "fridge 2, middle shelf, left"
    assert speak_zone("fridge2/middle/left", "ta") == "ஃப்ரிட்ஜ் 2, நடு அலமாரி, இடது"
    assert speak_zone("fridge2/middle/left", "hi") == "फ्रिज 2, बीच की शेल्फ़, बाएँ"


async def test_remember_then_where_roundtrip(fake_db, monkeypatch):
    monkeypatch.setattr(spatial, "_utcnow", lambda: NOW)
    loc = await spatial.remember("demo", "sakthi_curd_1l", "fridge2/middle/left", "user", 1.0)
    assert loc.confirmed_at == NOW
    stored = fake_db.store[("shops", "demo", "locations", "sakthi_curd_1l")]
    assert stored["confirmedBy"] == "user" and stored["zone"] == "fridge2/middle/left"
    again = await spatial.where("demo", "sakthi_curd_1l")
    assert again == loc


async def test_where_missing_returns_none(fake_db):
    assert await spatial.where("demo", "nothing") is None


async def test_remember_rejects_bad_confirmed_by(fake_db):
    with pytest.raises(ValueError):
        await spatial.remember("demo", "x", "counter", "camera", 0.5)


def _loc(by="user", age=timedelta(days=1)):
    return spatial.Location(sku="sakthi_curd_1l", zone="fridge2/middle/left",
                            confirmed_by=by, confirmed_at=NOW - age, confidence=0.9)


def test_describe_location_fresh():
    assert spatial.describe_location("Sakthi curd", _loc(), "en", NOW) == \
        "Sakthi curd: last recorded on fridge 2, middle shelf, left."


def test_describe_location_stale_when_model_confirmed():
    text = spatial.describe_location("Sakthi curd", _loc(by="model"), "en", NOW)
    assert "may have moved" in text


def test_describe_location_stale_after_three_days():
    assert not spatial.is_stale(_loc(age=timedelta(days=3)), NOW)
    assert spatial.is_stale(_loc(age=timedelta(days=3, minutes=1)), NOW)
    assert "may have moved" in spatial.describe_location("Sakthi curd", _loc(age=timedelta(days=4)), "en", NOW)


def test_describe_location_none():
    assert spatial.describe_location("Sakthi curd", None, "en", NOW) == \
        "I don't have a saved place for Sakthi curd. Let me look with the camera."
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/memory/test_zones_spatial.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.memory'`.

- [ ] **Step 4: Add the i18n keys used by this task, and by Tasks 3–4, to all three catalogs**

Merge these keys into the existing JSON object of each file. If a file does not exist yet, create it containing just these keys.

`services/live/aira_live/i18n/en.json`:

```json
{
  "products.unknown": "I don't know the product \"{spoken}\".",
  "loc.fresh": "{name}: last recorded on {zone}.",
  "loc.stale": "{name} was last recorded on {zone}, but it may have moved. I'll check with the camera.",
  "loc.none": "I don't have a saved place for {name}. Let me look with the camera.",
  "loc.saved": "Saved: {name} is on {zone}.",
  "guide.start": "Hold your hand out and follow the beeps.",
  "guide.lost": "I can't see the {name}. Turn slowly to the left.",
  "pick.ok_more": "{item} ✓. Take {n} more.",
  "pick.ok_done": "{item} ✓. That's all {n}.",
  "pick.wrong": "This is {got}, not {want}.",
  "pick.unsure": "Not sure. Hold it closer to the camera.",
  "identify.ok": "This is {name}, {pack}.",
  "camera.none": "I can't see a camera picture. Please check the camera."
}
```

`services/live/aira_live/i18n/ta.json`:

```json
{
  "products.unknown": "\"{spoken}\" என்ற பொருள் எனக்குத் தெரியவில்லை.",
  "loc.fresh": "{name}: கடைசியாக {zone} இல் வைக்கப்பட்டது.",
  "loc.stale": "{name} கடைசியாக {zone} இல் இருந்தது, ஆனால் இடம் மாறியிருக்கலாம். கேமராவில் சரிபார்க்கிறேன்.",
  "loc.none": "{name} க்கு சேமித்த இடம் இல்லை. கேமராவில் தேடுகிறேன்.",
  "loc.saved": "சேமித்தேன்: {name} {zone} இல் உள்ளது.",
  "guide.start": "கையை நீட்டி பீப் ஒலியைப் பின்தொடருங்கள்.",
  "guide.lost": "{name} தெரியவில்லை. மெதுவாக இடது பக்கம் திரும்புங்கள்.",
  "pick.ok_more": "{item} ✓. இன்னும் {n} எடுங்கள்.",
  "pick.ok_done": "{item} ✓. மொத்தம் {n} ஆகிவிட்டது.",
  "pick.wrong": "இது {got}, {want} இல்லை.",
  "pick.unsure": "உறுதியாகத் தெரியவில்லை. கேமராவுக்கு அருகில் காட்டுங்கள்.",
  "identify.ok": "இது {name}, {pack}.",
  "camera.none": "கேமரா படம் வரவில்லை. கேமராவைச் சரிபார்க்கவும்."
}
```

`services/live/aira_live/i18n/hi.json`:

```json
{
  "products.unknown": "मुझे \"{spoken}\" नाम का सामान नहीं पता।",
  "loc.fresh": "{name}: आख़िरी बार {zone} पर रखा गया था।",
  "loc.stale": "{name} आख़िरी बार {zone} पर था, पर शायद जगह बदल गई हो। मैं कैमरे से देखता हूँ।",
  "loc.none": "{name} की कोई सेव की हुई जगह नहीं है। मैं कैमरे से ढूँढता हूँ।",
  "loc.saved": "सेव कर लिया: {name} {zone} पर है।",
  "guide.start": "हाथ आगे बढ़ाइए और बीप की आवाज़ के पीछे चलिए।",
  "guide.lost": "{name} नहीं दिख रहा। धीरे से बाईं ओर मुड़िए।",
  "pick.ok_more": "{item} ✓. {n} और लीजिए।",
  "pick.ok_done": "{item} ✓. पूरे {n} हो गए।",
  "pick.wrong": "यह {got} है, {want} नहीं।",
  "pick.unsure": "पक्का नहीं पता। इसे कैमरे के पास लाइए।",
  "identify.ok": "यह {name} है, {pack}।",
  "camera.none": "कैमरे की तस्वीर नहीं आ रही। कृपया कैमरा देखिए।"
}
```

- [ ] **Step 5: Implement `memory/__init__.py` (empty file) and `memory/zones.py`**

```python
"""Canonical shop zones: 'fridge two, middle shelf, left' <-> 'fridge2/middle/left' <-> speech."""
import re

from aira_live.contracts import Lang

_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
_SKIP = {"shelf", "shelves", "the", "on", "in", "of", "side", "at"}
_CONTAINERS = {"fridge", "freezer", "rack", "counter", "cupboard", "box"}
_WORDS: dict[str, dict[str, str]] = {
    "en": {"fridge": "fridge", "freezer": "freezer", "rack": "rack", "counter": "counter",
           "cupboard": "cupboard", "box": "box", "top": "top shelf", "middle": "middle shelf",
           "bottom": "bottom shelf", "left": "left", "right": "right", "center": "centre"},
    "ta": {"fridge": "ஃப்ரிட்ஜ்", "freezer": "ஃப்ரீசர்", "rack": "ரேக்", "counter": "கவுண்டர்",
           "cupboard": "அலமாரி", "box": "பெட்டி", "top": "மேல் அலமாரி", "middle": "நடு அலமாரி",
           "bottom": "கீழ் அலமாரி", "left": "இடது", "right": "வலது", "center": "நடுவில்"},
    "hi": {"fridge": "फ्रिज", "freezer": "फ्रीज़र", "rack": "रैक", "counter": "काउंटर",
           "cupboard": "अलमारी", "box": "डिब्बा", "top": "ऊपर की शेल्फ़", "middle": "बीच की शेल्फ़",
           "bottom": "नीचे की शेल्फ़", "left": "बाएँ", "right": "दाएँ", "center": "बीच में"},
}


def normalize_zone(text: str) -> str:
    tokens = [_NUM.get(tok, tok) for tok in re.findall(r"[a-z]+|\d+", text.lower()) if tok not in _SKIP]
    if not tokens:
        raise ValueError(f"empty zone: {text!r}")
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _CONTAINERS and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            out.append(tok + tokens[i + 1])
            i += 2
        else:
            out.append(tok)
            i += 1
    return "/".join(out)


def speak_zone(zone: str, lang: Lang) -> str:
    words = _WORDS[lang]
    parts = []
    for part in zone.split("/"):
        m = re.fullmatch(r"([a-z]+)(\d+)", part)
        if m:
            base, num = m.groups()
            parts.append(f"{words.get(base, base)} {num}")
        else:
            parts.append(words.get(part, part))
    return ", ".join(parts)
```

- [ ] **Step 6: Implement `memory/spatial.py`**

```python
"""Spatial memory: where each product was last recorded (spec §5 shops/{shopId}/locations/{sku})."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from aira_live import db as dbmod
from aira_live.contracts import Lang
from aira_live.i18n import t
from aira_live.memory.zones import speak_zone

STALE_AFTER = timedelta(days=3)


class Location(BaseModel):
    sku: str
    zone: str
    confirmed_by: Literal["user", "model"]
    confirmed_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _doc(shop_id: str, sku: str):
    return dbmod.db().collection("shops").document(shop_id).collection("locations").document(sku)


async def remember(shop_id: str, sku: str, zone: str, confirmed_by: str, confidence: float) -> Location:
    loc = Location(sku=sku, zone=zone, confirmed_by=confirmed_by, confirmed_at=_utcnow(), confidence=confidence)
    await _doc(shop_id, sku).set({"sku": sku, "zone": zone, "confirmedBy": loc.confirmed_by,
                                  "confirmedAt": loc.confirmed_at, "confidence": loc.confidence})
    return loc


async def where(shop_id: str, sku: str) -> Location | None:
    snap = await _doc(shop_id, sku).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    return Location(sku=d.get("sku", sku), zone=d["zone"], confirmed_by=d["confirmedBy"],
                    confirmed_at=d["confirmedAt"], confidence=d.get("confidence", 0.0))


def is_stale(loc: Location, now: datetime) -> bool:
    return loc.confirmed_by != "user" or (now - loc.confirmed_at) > STALE_AFTER


def describe_location(name: str, loc: Location | None, lang: Lang, now: datetime) -> str:
    if loc is None:
        return t("loc.none", lang, name=name)
    key = "loc.stale" if is_stale(loc, now) else "loc.fresh"
    return t(key, lang, name=name, zone=speak_zone(loc.zone, lang))
```

`pydantic.ValidationError` subclasses `ValueError`, so `test_remember_rejects_bad_confirmed_by` passes.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/memory/test_zones_spatial.py -q`
Expected: `10 passed`.

- [ ] **Step 8: Commit**

```bash
git add services/live/aira_live/memory services/live/aira_live/i18n services/live/tests/memory/conftest.py services/live/tests/memory/test_zones_spatial.py
git commit -m "feat(memory): canonical zones + spatial memory with staleness rule"
```

---

### Task 2: Episodic memory + owner preferences

**Files:**
- Create: `services/live/aira_live/memory/episodic.py`, `services/live/aira_live/memory/prefs.py`
- Test: `services/live/tests/memory/test_episodic_prefs.py`

**Interfaces:**
- Consumes: `aira_live.db.db()`
- Produces:
  - `async log(shop_id, type, text, ref_type=None, ref_id=None) -> str` (returns the event id)
  - `async recent(shop_id, limit=20) -> list[dict]` (newest first; each dict has `id, type, text, refType, refId, at`)
  - `async prefs.get(shop_id) -> dict` (always includes `speechRate, verbosity, productAliases, supplierAliases`)
  - `async prefs.set(shop_id, *, speech_rate=…, verbosity=…, product_aliases=…, supplier_aliases=…) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timedelta, timezone

import pytest

from aira_live.memory import episodic, prefs

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


async def test_log_and_recent_newest_first(fake_db, monkeypatch):
    times = iter([T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)])
    monkeypatch.setattr(episodic, "_utcnow", lambda: next(times))
    await episodic.log("demo", "find", "Finding Sakthi curd 1 L", "product", "sakthi_curd_1l")
    await episodic.log("demo", "pick", "Picked 2 x Sakthi curd 1 L", "product", "sakthi_curd_1l")
    third = await episodic.log("demo", "location", "Aavin milk -> fridge1/top")
    events = await episodic.recent("demo", limit=2)
    assert [e["type"] for e in events] == ["location", "pick"]
    assert events[0]["id"] == third and events[0]["refType"] is None


async def test_log_rejects_blank_text(fake_db):
    with pytest.raises(ValueError):
        await episodic.log("demo", "find", "   ")


async def test_recent_limit_bounds(fake_db):
    with pytest.raises(ValueError):
        await episodic.recent("demo", limit=0)


async def test_prefs_defaults_then_merge(fake_db):
    assert await prefs.get("demo") == {"speechRate": 1.0, "verbosity": "short",
                                       "productAliases": {}, "supplierAliases": {}}
    out = await prefs.set("demo", speech_rate=1.3, product_aliases={"thayir": "sakthi_curd_1l"})
    assert out["speechRate"] == 1.3 and out["verbosity"] == "short"
    assert out["productAliases"] == {"thayir": "sakthi_curd_1l"}
    out2 = await prefs.set("demo", verbosity="detail")
    assert out2["speechRate"] == 1.3 and out2["verbosity"] == "detail"


async def test_prefs_validation(fake_db):
    with pytest.raises(ValueError):
        await prefs.set("demo", speech_rate=3.0)
    with pytest.raises(ValueError):
        await prefs.set("demo", verbosity="loud")
    with pytest.raises(ValueError):
        await prefs.set("demo", favourite_colour="blue")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/memory/test_episodic_prefs.py -q`
Expected: FAIL with `ImportError: cannot import name 'episodic'`.

- [ ] **Step 3: Implement `memory/episodic.py`**

```python
"""Episodic memory: what happened in the shop (spec §5 shops/{shopId}/memoryEvents)."""
from datetime import datetime, timezone

from aira_live import db as dbmod


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coll(shop_id: str):
    return dbmod.db().collection("shops").document(shop_id).collection("memoryEvents")


async def log(shop_id: str, type: str, text: str, ref_type: str | None = None, ref_id: str | None = None) -> str:
    if not text.strip():
        raise ValueError("memory event text is empty")
    ref = _coll(shop_id).document()
    await ref.set({"type": type, "text": text, "refType": ref_type, "refId": ref_id, "at": _utcnow()})
    return ref.id


async def recent(shop_id: str, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    query = _coll(shop_id).order_by("at", direction="DESCENDING").limit(limit)
    return [{"id": snap.id, **snap.to_dict()} async for snap in query.stream()]
```

- [ ] **Step 4: Implement `memory/prefs.py`**

The module defines `set`, so it never calls the built-in `set()` and uses `dict.keys()` set operations instead.

```python
"""Owner preferences (spec §5 shops/{shopId}/prefs/main)."""
from aira_live import db as dbmod

DEFAULTS = {"speechRate": 1.0, "verbosity": "short", "productAliases": {}, "supplierAliases": {}}
_FIELDS = {"speech_rate": "speechRate", "verbosity": "verbosity",
           "product_aliases": "productAliases", "supplier_aliases": "supplierAliases"}


def _doc(shop_id: str):
    return dbmod.db().collection("shops").document(shop_id).collection("prefs").document("main")


async def get(shop_id: str) -> dict:
    snap = await _doc(shop_id).get()
    stored = snap.to_dict() if snap.exists else {}
    return {**DEFAULTS, **stored}


async def set(shop_id: str, **kw) -> dict:
    unknown = kw.keys() - _FIELDS.keys()
    if unknown:
        raise ValueError(f"unknown preference(s): {sorted(unknown)}")
    update = {_FIELDS[k]: v for k, v in kw.items()}
    if "speechRate" in update and not 0.5 <= float(update["speechRate"]) <= 2.0:
        raise ValueError("speech_rate must be between 0.5 and 2.0")
    if "verbosity" in update and update["verbosity"] not in ("short", "detail"):
        raise ValueError("verbosity must be 'short' or 'detail'")
    await _doc(shop_id).set(update, merge=True)
    return await get(shop_id)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/memory/test_episodic_prefs.py -q`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/memory/episodic.py services/live/aira_live/memory/prefs.py services/live/tests/memory/test_episodic_prefs.py
git commit -m "feat(memory): episodic shop events and owner preferences"
```

---

### Task 3: `location_remember` and `product_identify` tools

**Files:**
- Create: `services/live/aira_live/tools/products.py` (first part)
- Test: `services/live/tests/memory/test_products_tools.py`

**Interfaces:**
- Consumes:
  - `business.catalog.resolve(shop_id, spoken, lang) -> ProductRef | None` (Kanish)
  - `vision.identify.identify_product(frame, candidates) -> IdentifyResult`
  - `vision.identify.ProductRef`
  - `session.get_session()`
  - `Session.request_frames()`
  - `memory.spatial`, `memory.episodic`, `memory.zones`, `i18n.t`
- Produces:
  - `async shop_products(shop_id) -> list[ProductRef]`
  - Live tools:
    - `async location_remember(product: str, zone: str) -> dict` returning `{say, zone}`
    - `async product_identify() -> dict` returning `{say, sku, confidence}`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from aira_live.frames import Frame
from aira_live.tools import products
from aira_live.vision.identify import IdentifyResult, ProductRef

CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L")
MILK = ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="0.5 L")


@pytest.fixture
def wired(fake_db, session, monkeypatch):
    fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")] = {"name": "Sakthi curd", "brand": "Sakthi", "unit": "pack", "packSizeL": 1.0}
    fake_db.store[("shops", "demo", "products", "aavin_milk_500ml")] = {"name": "Aavin milk", "brand": "Aavin", "unit": "pack", "packSizeL": 0.5}
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def resolve(shop_id, spoken, lang):
        return {"curd": CURD, "thayir": CURD, "milk": MILK}.get(spoken.lower())

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    return session


async def test_shop_products_from_firestore(wired):
    refs = await products.shop_products("demo")
    assert {r.sku: r.pack for r in refs} == {"sakthi_curd_1l": "1 L", "aavin_milk_500ml": "0.5 L"}


async def test_location_remember_saves_user_confirmed(wired, fake_db):
    out = await products.location_remember("curd", "fridge two, middle shelf, left")
    assert out["zone"] == "fridge2/middle/left"
    assert out["say"] == "Saved: Sakthi curd is on fridge 2, middle shelf, left."
    doc = fake_db.store[("shops", "demo", "locations", "sakthi_curd_1l")]
    assert doc["confirmedBy"] == "user" and doc["confidence"] == 1.0


async def test_location_remember_unknown_product(wired, fake_db):
    out = await products.location_remember("paneer", "counter")
    assert out["say"] == "I don't know the product \"paneer\"."
    assert not any(p[2] == "locations" for p in fake_db.store if len(p) > 2)


async def test_product_identify_ok(wired, monkeypatch):
    wired.frames.add(Frame(id="f1", jpeg_b64="x", w=10, h=10, ts=5.0, purpose="identify"))

    async def ident(frame, candidates):
        assert {c.sku for c in candidates} == {"sakthi_curd_1l", "aavin_milk_500ml"}
        return IdentifyResult(sku="aavin_milk_500ml", confidence=0.91, alternatives=[])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert wired.requested == ["identify"]
    assert out == {"say": "This is Aavin milk, 0.5 L.", "sku": "aavin_milk_500ml", "confidence": 0.91}


async def test_product_identify_low_confidence_says_not_sure(wired, monkeypatch):
    wired.frames.add(Frame(id="f1", jpeg_b64="x", w=10, h=10, ts=5.0, purpose="identify"))

    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.4, alternatives=["aavin_milk_500ml"])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out["sku"] is None and out["say"] == "Not sure. Hold it closer to the camera."


async def test_product_identify_no_frame(wired):
    out = await products.product_identify()
    assert out["say"] == "I can't see a camera picture. Please check the camera." and out["sku"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/memory/test_products_tools.py -q`
Expected: FAIL with `ImportError: cannot import name 'products' from 'aira_live.tools'`. If the foundation left a stub file, the error is instead `AttributeError ... shop_products`.

- [ ] **Step 3: Implement the first part of `tools/products.py`**

This replaces any foundation stub file of the same name.

```python
"""Live tools: product_find, product_identify, location_remember (spec §2 steps 5–6)."""
from aira_live import db as dbmod
from aira_live.business.catalog import resolve as catalog_resolve
from aira_live.i18n import t
from aira_live.memory import episodic, spatial
from aira_live.memory.zones import normalize_zone, speak_zone
from aira_live.session import get_session
from aira_live.vision.identify import ProductRef, identify_product

MIN_ID_CONF = 0.6


async def shop_products(shop_id: str) -> list[ProductRef]:
    out: list[ProductRef] = []
    coll = dbmod.db().collection("shops").document(shop_id).collection("products")
    async for snap in coll.stream():
        d = snap.to_dict()
        pack = f"{d['packSizeL']:g} L" if d.get("packSizeL") else d.get("unit", "")
        out.append(ProductRef(sku=snap.id, name=d["name"], brand=d.get("brand", ""), pack=pack))
    return out


async def location_remember(product: str, zone: str) -> dict:
    """Owner says where a product is kept, e.g. 'curd is on fridge two, middle shelf, left'."""
    s = get_session()
    ref = await catalog_resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"say": t("products.unknown", s.lang, spoken=product)}
    canonical = normalize_zone(zone)
    await spatial.remember(s.shop_id, ref.sku, canonical, "user", 1.0)
    await episodic.log(s.shop_id, "location", f"{ref.name} -> {canonical}", "product", ref.sku)
    return {"say": t("loc.saved", s.lang, name=ref.name, zone=speak_zone(canonical, s.lang)), "zone": canonical}


async def product_identify() -> dict:
    """What is in my hand? One 'identify' frame, checked against this shop's products."""
    s = get_session()
    frames = await s.request_frames("identify", count=1, timeout_s=4.0)
    if not frames:
        return {"say": t("camera.none", s.lang), "sku": None, "confidence": 0.0}
    candidates = await shop_products(s.shop_id)
    res = await identify_product(frames[-1], candidates)
    if res.sku is None or res.confidence < MIN_ID_CONF:
        return {"say": t("pick.unsure", s.lang), "sku": None, "confidence": res.confidence}
    ref = next(c for c in candidates if c.sku == res.sku)
    return {"say": t("identify.ok", s.lang, name=ref.name, pack=ref.pack), "sku": ref.sku, "confidence": res.confidence}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/memory/test_products_tools.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/tools/products.py services/live/tests/memory/test_products_tools.py
git commit -m "feat(tools): location_remember and product_identify"
```

---

### Task 4: Guidance loop, `product_find`, client-event hooks and tool registration

**Files:**
- Create: `services/live/aira_live/tools/guidance.py`
- Modify: `services/live/aira_live/tools/products.py` (add `product_find`), `services/live/aira_live/tools/__init__.py` (register the tools)
- Test: `services/live/tests/memory/test_guidance.py`

**Interfaces:**
- Consumes:
  - `vision.point.point_to(frame, target_label) -> TargetResult | None`
  - `vision.identify.identify_product`
  - `sessions.notify(shop_id, text, cue) -> int`
  - `contracts.RequestFrameMsg`, `contracts.TargetMsg`
  - `FrameStore.wait_for(count, purpose, after_ts, timeout_s)`, `FrameStore.latest(n, purpose)`
  - `memory.episodic.log`
- Produces:
  - `class GuidanceLoop` with `step()`, `run()`, `on_touch()`, `stop()`
  - `async start(session, ref, needed, candidates, interval_s=1.0, frame_timeout_s=3.0) -> GuidanceLoop`
  - `active(session) -> GuidanceLoop | None`
  - **Cross-team hooks:**
    - `async handle_client_event(session, name: str, data: dict) -> None`
    - `async stop_guidance(session) -> None`
  - Live tool: `async product_find(product: str, count_needed: int = 1) -> dict` returning `{say, found, sku?, stale?}`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from aira_live.frames import Frame
from aira_live.tools import guidance, products
from aira_live.vision.identify import IdentifyResult, ProductRef
from aira_live.vision.point import TargetResult

CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L")
MILK = ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="500 ml")
CANDS = [CURD, MILK]


@pytest.fixture
def spoken(monkeypatch):
    said = []

    async def notify(shop_id, text, cue="ack"):
        said.append((text, cue))
        return 1

    monkeypatch.setattr(guidance, "notify", notify)
    return said


@pytest.fixture(autouse=True)
def clean_registry():
    guidance._ACTIVE.clear()
    yield
    guidance._ACTIVE.clear()


def _frame(fid, ts):
    return Frame(id=fid, jpeg_b64="x", w=640, h=480, ts=ts, purpose="guide")


async def test_start_requests_continuous_guide_frames(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 2, CANDS, run=False)
    msg = session.sent[-1]
    assert msg.t == "request_frame" and msg.purpose == "guide" and msg.continuous is True
    assert guidance.active(session) is loop


async def test_step_sends_target_for_confident_point(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        assert label == "Sakthi curd 1 L"
        return TargetResult(label=label, point=(412, 530), box=(390, 505, 435, 555), confidence=0.8)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    assert await loop.step() is True
    tgt = session.sent[-1]
    assert tgt.t == "target" and tuple(tgt.point) == (412, 530) and tgt.frame_id == "f1"


async def test_step_announces_lost_once(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        return None

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    for i in range(5):
        session.frames.add(_frame(f"f{i}", float(i + 1)))
        await loop.step()
    lost = [s for s in spoken if s[0] == "I can't see the Sakthi curd. Turn slowly to the left."]
    assert len(lost) == 1 and lost[0][1] == "unsure"


async def test_touch_correct_then_done(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    await guidance.start(session, CURD, 2, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert spoken[-1] == ("Sakthi curd 1 L ✓. Take 1 more.", "match")
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert spoken[-1] == ("Sakthi curd 1 L ✓. That's all 2.", "match")
    assert guidance.active(session) is None
    stop = session.sent[-1]
    assert stop.t == "request_frame" and stop.stop is True


async def test_touch_wrong_product_is_not_counted(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="aavin_milk_500ml", confidence=0.95, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert spoken[-1] == ("This is Aavin milk 500 ml, not Sakthi curd 1 L.", "mismatch")
    assert loop.picked == 0 and guidance.active(session) is loop


async def test_duplicate_touch_after_done_is_ignored(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    count = len(spoken)
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert len(spoken) == count


async def test_stop_guidance_idempotent(session, fake_db, spoken):
    await guidance.start(session, CURD, 1, CANDS, run=False)
    await guidance.stop_guidance(session)
    await guidance.stop_guidance(session)
    stops = [m for m in session.sent if m.t == "request_frame" and m.stop]
    assert len(stops) == 1


async def test_product_find_fresh_location_starts_guidance(session, fake_db, spoken, monkeypatch):
    from datetime import datetime, timezone

    now = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
    fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")] = {"name": "Sakthi curd", "brand": "Sakthi", "unit": "pack", "packSizeL": 1.0}
    fake_db.store[("shops", "demo", "locations", "sakthi_curd_1l")] = {
        "sku": "sakthi_curd_1l", "zone": "fridge2/middle/left", "confirmedBy": "user", "confirmedAt": now, "confidence": 1.0}
    monkeypatch.setattr(products.spatial, "_utcnow", lambda: now)
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def resolve(shop_id, spoken_name, lang):
        return CURD

    async def no_run(self):
        return None

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    monkeypatch.setattr(guidance.GuidanceLoop, "run", no_run)
    out = await products.product_find("curd", count_needed=2)
    assert out["found"] is True and out["stale"] is False and out["sku"] == "sakthi_curd_1l"
    assert out["say"] == ("Sakthi curd: last recorded on fridge 2, middle shelf, left. "
                          "Hold your hand out and follow the beeps.")
    assert guidance.active(session).needed == 2
    await guidance.stop_guidance(session)


async def test_product_find_unknown_product_starts_nothing(session, fake_db, spoken, monkeypatch):
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def resolve(shop_id, spoken_name, lang):
        return None

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    out = await products.product_find("paneer")
    assert out == {"say": "I don't know the product \"paneer\".", "found": False}
    assert session.sent == [] and guidance.active(session) is None


def test_tools_registered():
    from aira_live.tools import TOOL_FUNCS

    names = {f.__name__ for f in TOOL_FUNCS}
    assert {"product_find", "product_identify", "location_remember"} <= names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/memory/test_guidance.py -q`
Expected: FAIL with `ImportError: cannot import name 'guidance' from 'aira_live.tools'`.

- [ ] **Step 3: Implement `tools/guidance.py`**

```python
"""Hand guidance loop: continuous 'guide' frames -> ER 2 point -> `target` msgs -> app tones;
fingertip_in_target -> identify the touched pack -> spoken verdict (spec §2 step 6)."""
import asyncio

from aira_live.contracts import RequestFrameMsg, TargetMsg
from aira_live.i18n import t
from aira_live.memory import episodic
from aira_live.sessions import notify
from aira_live.vision.identify import ProductRef, identify_product
from aira_live.vision.point import point_to

MIN_POINT_CONF = 0.3
MIN_ID_CONF = 0.6
LOST_AFTER = 3
_ACTIVE: dict[str, "GuidanceLoop"] = {}


def _item(ref: ProductRef | None) -> str:
    return "" if ref is None else f"{ref.name} {ref.pack}".strip()


class GuidanceLoop:
    def __init__(self, session, ref: ProductRef, needed: int, candidates: list[ProductRef],
                 interval_s: float = 1.0, frame_timeout_s: float = 3.0):
        self.session, self.ref, self.candidates = session, ref, candidates
        self.needed, self.picked = max(1, needed), 0
        self.interval_s, self.frame_timeout_s = interval_s, frame_timeout_s
        self.last_ts, self.misses, self.active, self.task = 0.0, 0, True, None

    async def step(self) -> bool:
        if not self.active:
            return False
        s = self.session
        frames = await s.frames.wait_for(count=1, purpose="guide", after_ts=self.last_ts, timeout_s=self.frame_timeout_s)
        hit = False
        if frames:
            frame = frames[-1]
            self.last_ts = frame.ts
            res = await point_to(frame, _item(self.ref))
            if res is not None and res.confidence >= MIN_POINT_CONF:
                hit = True
                await s.send(TargetMsg(frame_id=frame.id, label=self.ref.name, point=res.point,
                                       box=res.box, confidence=res.confidence))
        if hit:
            self.misses = 0
        else:
            self.misses += 1
            if self.misses == LOST_AFTER:
                await notify(s.shop_id, t("guide.lost", s.lang, name=self.ref.name), cue="unsure")
        return self.active

    async def run(self) -> None:
        while self.active and await self.step():
            await asyncio.sleep(self.interval_s)

    async def on_touch(self) -> dict:
        s = self.session
        frames = s.frames.latest(1, purpose="guide")
        if not frames:
            say = t("camera.none", s.lang)
            await notify(s.shop_id, say, cue="warn")
            return {"say": say, "picked": self.picked, "needed": self.needed}
        res = await identify_product(frames[-1], self.candidates)
        if res.sku is None or res.confidence < MIN_ID_CONF:
            say, cue = t("pick.unsure", s.lang), "unsure"
        elif res.sku != self.ref.sku:
            got = next((c for c in self.candidates if c.sku == res.sku), None)
            say, cue = t("pick.wrong", s.lang, got=_item(got) or res.sku, want=_item(self.ref)), "mismatch"
        else:
            self.picked += 1
            remaining = self.needed - self.picked
            cue = "match"
            if remaining > 0:
                say = t("pick.ok_more", s.lang, item=_item(self.ref), n=remaining)
            else:
                say = t("pick.ok_done", s.lang, item=_item(self.ref), n=self.needed)
                await episodic.log(s.shop_id, "pick", f"Picked {self.needed} x {_item(self.ref)}", "product", self.ref.sku)
                await stop_guidance(s)
        await notify(s.shop_id, say, cue=cue)
        return {"say": say, "picked": self.picked, "needed": self.needed}

    async def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        await self.session.send(RequestFrameMsg(purpose="guide", stop=True))
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()


async def start(session, ref: ProductRef, needed: int, candidates: list[ProductRef],
                interval_s: float = 1.0, frame_timeout_s: float = 3.0, run: bool = True) -> GuidanceLoop:
    await stop_guidance(session)
    loop = GuidanceLoop(session, ref, needed, candidates, interval_s, frame_timeout_s)
    _ACTIVE[session.session_id] = loop
    await session.send(RequestFrameMsg(purpose="guide", continuous=True))
    if run:
        loop.task = asyncio.create_task(loop.run())
    return loop


def active(session) -> GuidanceLoop | None:
    return _ACTIVE.get(session.session_id)


async def handle_client_event(session, name: str, data: dict) -> None:
    """Cross-team hook: Saravana's main.py calls this for every client `event` message."""
    if name != "fingertip_in_target":
        return
    loop = _ACTIVE.get(session.session_id)
    if loop is None or not loop.active:
        return
    await loop.on_touch()


async def stop_guidance(session) -> None:
    """Cross-team hook: main.py calls this on `stop` and on disconnect. Safe to call twice."""
    loop = _ACTIVE.pop(session.session_id, None)
    if loop is not None:
        await loop.stop()
```

- [ ] **Step 4: Add `product_find` to `tools/products.py`**

Append these imports next to the existing ones:

```python
from aira_live.tools import guidance
```

Append the tool:

```python
async def product_find(product: str, count_needed: int = 1) -> dict:
    """Guide the owner's hand to a product: memory first, then live pointing, then a touch check."""
    s = get_session()
    ref = await catalog_resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"say": t("products.unknown", s.lang, spoken=product), "found": False}
    now = spatial._utcnow()
    loc = await spatial.where(s.shop_id, ref.sku)
    stale = loc is None or spatial.is_stale(loc, now)
    candidates = await shop_products(s.shop_id)
    await guidance.start(s, ref, max(1, count_needed), candidates)
    await episodic.log(s.shop_id, "find", f"Finding {ref.name} {ref.pack}", "product", ref.sku)
    say = f"{spatial.describe_location(ref.name, loc, s.lang, now)} {t('guide.start', s.lang)}"
    return {"say": say, "found": True, "sku": ref.sku, "stale": stale}
```

- [ ] **Step 5: Register the tools in `tools/__init__.py`**

If the foundation created stubs named `product_find`, `product_identify` or `location_remember` in this package, delete those stubs. Then make sure the registry includes the real functions:

```python
from aira_live.tools.products import location_remember, product_find, product_identify

for _fn in (product_find, product_identify, location_remember):
    if _fn not in TOOL_FUNCS:
        TOOL_FUNCS.append(_fn)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/memory -q`
Expected: `30 passed`. That is 10 + 5 + 6 from Tasks 1–3, plus 9 here.

- [ ] **Step 7: Hand the hooks to Saravana (cross-team, 2 lines in `main.py`)**

Open a small PR, or ask Saravana to add this to the WebSocket loop in `aira_live/main.py`:

```python
from aira_live.tools import guidance

# inside the receive loop, after a message is validated and the session exists:
if isinstance(msg, EventMsg):
    await guidance.handle_client_event(session, msg.name, msg.data)
elif isinstance(msg, StopMsg):
    await guidance.stop_guidance(session)

# in the WebSocketDisconnect / finally block:
if session is not None:
    await guidance.stop_guidance(session)
```

Manual check with the tool harness and a real frame of the demo fridge:

```bash
cd services/live && uv run python -m aira_live.devtools.call_tool product_find --args '{"product":"curd","count_needed":2}' --image ../../bench/data/guidance/fridge.jpg --shop demo
```

Expected: the printed result has `"found": true` and a `say` starting with the location sentence. The harness's printed ServerMsgs include `request_frame` (`continuous: true`) followed by a `target` message for the curd.

- [ ] **Step 8: Commit**

```bash
git add services/live/aira_live/tools/guidance.py services/live/aira_live/tools/products.py services/live/aira_live/tools/__init__.py services/live/tests/memory/test_guidance.py
git commit -m "feat(tools): product_find hand guidance loop with touch verification"
```

---

### Task 5: Guidance time-to-touch scorer (benchmark slide)

**Files:**
- Create: `bench/score_guidance.py`, `bench/tests/test_score_guidance.py`, `bench/data/guidance/trials.example.json`

**Interfaces:**
- Consumes: a trial log written by the app bench runner (`ishwarya/03-demo-mode-apk-bench`) in this format:
  `{"trials": [{"trial": int, "product": sku, "startedAtMs": int, "touchedAtMs": int|null, "correctItem": bool}]}`
- Produces:
  - `score(trials: list[dict]) -> dict` with keys `trials, successes, successRate, wrongItem, timeouts, p50S, p95S, meanS, meetsSampleSize`
  - the CLI `python bench/score_guidance.py <trials.json>`

- [ ] **Step 1: Write the failing tests**

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from score_guidance import percentile, score  # noqa: E402


def test_percentile_nearest_rank():
    assert percentile([6.0, 12.0, 8.4], 50) == 8.4
    assert percentile([6.0, 12.0, 8.4], 95) == 12.0
    assert percentile([], 50) is None


def test_score_counts_successes_wrong_and_timeouts():
    trials = [
        {"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": True},
        {"trial": 2, "product": "sakthi_curd_1l", "startedAtMs": 1000, "touchedAtMs": 13000, "correctItem": True},
        {"trial": 3, "product": "aavin_milk_500ml", "startedAtMs": 0, "touchedAtMs": 6000, "correctItem": True},
        {"trial": 4, "product": "aavin_milk_500ml", "startedAtMs": 0, "touchedAtMs": 5000, "correctItem": False},
        {"trial": 5, "product": "arun_icecream_box", "startedAtMs": 0, "touchedAtMs": None, "correctItem": False},
    ]
    out = score(trials)
    assert out == {"trials": 5, "successes": 3, "successRate": 0.6, "wrongItem": 1, "timeouts": 1,
                   "p50S": 8.4, "p95S": 12.0, "meanS": 8.8, "meetsSampleSize": False}


def test_score_rejects_touch_before_start():
    with pytest.raises(ValueError):
        score([{"trial": 1, "product": "x", "startedAtMs": 5000, "touchedAtMs": 1000, "correctItem": True}])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest ../../bench/tests/test_score_guidance.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'score_guidance'`.

- [ ] **Step 3: Implement `bench/score_guidance.py`**

```python
#!/usr/bin/env python3
"""Score AIRA hand-guidance trials: seconds from guidance start to touching the correct pack (spec §7).

Usage: python bench/score_guidance.py bench/data/guidance/trials.json
Trial log (written by the app bench runner, ishwarya/03):
  {"trials": [{"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": true}]}
touchedAtMs = null means the trial timed out. The spec target is 20 trials.
"""
import json
import math
import sys

SAMPLE_TARGET = 20


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[k]


def score(trials: list[dict]) -> dict:
    times: list[float] = []
    wrong = timeouts = 0
    for tr in trials:
        if tr.get("touchedAtMs") is None:
            timeouts += 1
            continue
        if tr["touchedAtMs"] < tr["startedAtMs"]:
            raise ValueError(f"trial {tr.get('trial')}: touched before start")
        if not tr.get("correctItem", False):
            wrong += 1
            continue
        times.append((tr["touchedAtMs"] - tr["startedAtMs"]) / 1000.0)
    n = len(trials)
    return {
        "trials": n,
        "successes": len(times),
        "successRate": round(len(times) / n, 3) if n else 0.0,
        "wrongItem": wrong,
        "timeouts": timeouts,
        "p50S": percentile(times, 50),
        "p95S": percentile(times, 95),
        "meanS": round(sum(times) / len(times), 2) if times else None,
        "meetsSampleSize": n >= SAMPLE_TARGET,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python bench/score_guidance.py <trials.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    print(json.dumps(score(data["trials"]), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Create `bench/data/guidance/trials.example.json`**

This only shows the format. The real 20 trials are recorded on D6 with a blind participant or a blindfolded teammate (disclosed as such in the deck).

```json
{
  "trials": [
    {"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": true},
    {"trial": 2, "product": "aavin_milk_500ml", "startedAtMs": 0, "touchedAtMs": 11200, "correctItem": true},
    {"trial": 3, "product": "arun_icecream_box", "startedAtMs": 0, "touchedAtMs": null, "correctItem": false}
  ]
}
```

- [ ] **Step 5: Run the tests and the CLI**

Run: `cd services/live && uv run pytest ../../bench/tests/test_score_guidance.py -q`
Expected: `3 passed`.

Run: `python3 bench/score_guidance.py bench/data/guidance/trials.example.json`
Expected: JSON with `"trials": 3, "successes": 2, "successRate": 0.667, "timeouts": 1, "meetsSampleSize": false`.

- [ ] **Step 6: Commit and open the PR**

```bash
git add bench/score_guidance.py bench/tests/test_score_guidance.py bench/data/guidance/trials.example.json
git commit -m "feat(bench): hand-guidance time-to-touch scorer"
git push -u origin sarmitha/memory-product-find
gh pr create --base main --title "Memory + product_find hand guidance (Sarmitha 03)" --body "Spatial/episodic/prefs memory, location_remember, product_identify, product_find guidance loop, guidance scorer. Needs Saravana: wire guidance.handle_client_event / stop_guidance in main.py (Task 4 Step 7)."
```

---

## Self-review checklist (done while writing)

- **Spec coverage:**
  - §2 step 5 (remember a location): Task 3
  - §2 step 6 (guided pick + verify the pack): Task 4
  - §3 honest uncertainty (stale, not sure): Tasks 1 and 4
  - §5 `locations`, `memoryEvents`, `prefs/main`: Tasks 1–2
  - §7 guidance time-to-touch: Task 5
- **Interface names match the registry:**
  - `remember`, `where`, `log`, `recent`, `get`, `set`, `Location`
  - `point_to`, `identify_product`, `ProductRef`, `IdentifyResult`, `TargetResult`
  - `catalog.resolve`, `sessions.notify`, `get_session`, `request_frames`
  - frame purposes `guide` and `identify`
  - client event `fingertip_in_target`
- **New cross-team items, flagged:**
  - the `handle_client_event` and `stop_guidance` hooks
  - the optional `count_needed` parameter on `product_find`
  - the guidance trial-log format consumed from Ishwarya's bench runner
