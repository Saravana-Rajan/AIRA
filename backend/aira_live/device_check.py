"""Start-up camera check (mounted by main.py through OPTIONAL_ROUTERS).

POST /api/device-check/camera  {"image": <jpeg base64>, "lang": "en|ta|hi", "device": "phone|laptop", "key": ...}
  -> {"ok": bool, "seen": "<a few words>", "problem": null | "dark" | "blurry" | "covered" | "blank" | "unclear"}

Before the phone app opens the voice conversation it tests the microphone on the phone and sends one camera
picture here. A vision model says whether it is a real, usable camera view and names what is in it ("a person at a
desk", "milk packets on a shelf"); the app speaks that back and only continues on ``ok``. No Live session is opened
and nothing is stored.

Guards, as for /ws (shared/docs/ops/security.md): the request must come from an allowed browser origin, carry the
app key when ``AIRA_APP_KEY`` is set, and one address gets at most ``MAX_PER_MINUTE`` checks. The model's words are
cleaned and cut short before they leave, because the app reads them aloud.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import re
import time
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aira_live import config
from aira_live.diag import diag, trunc
from aira_live.prompt import LANG_NAME
from aira_live.vision.gemini import VisionError, generate_json, image_part

log = logging.getLogger(__name__)
router = APIRouter()

MAX_IMAGE_B64 = 1_500_000      # the app's own cap (frontend/app/src/camera/capture.ts MAX_B64)
MAX_SEEN = 60
MAX_PER_MINUTE = 6
PROBLEMS = ("dark", "blurry", "covered", "blank")
_DEVICE_HINT = {
    "laptop": "This is a laptop camera, so a person sitting in front of it is a normal, good view.",
    "phone": "This is a phone worn on the chest, so it should show what is in front of the wearer.",
}
PROMPT = (
    "This picture is from the camera of a voice assistant for a blind shopkeeper. Check that the camera works. "
    "{device} ok is true only when it is a real, live camera view in which you can make out at least one thing. "
    "ok is false when the picture is black or nearly black, washed out, one flat colour, completely blurred, or "
    "covered by a finger or tape; then set problem to one of: dark, blurry, covered, blank. Otherwise problem is "
    "an empty string. seen names the main things in view in at most six words, in {lang}. Call any person just "
    "'a person': never describe or identify them."
)
SCHEMA = '{"ok": true, "seen": "<at most six words>", "problem": "<dark|blurry|covered|blank or empty>"}'

_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
_MARKERS = re.compile(r"\[[^\]]{0,40}\]")       # "[SYSTEM NOTE]"-style markers never reach the speaker
_JPEG = re.compile(r"^/9j/[A-Za-z0-9+/]+={0,2}$")
_hits: dict[str, deque] = {}


def reset_limits() -> None:
    _hits.clear()


def _limited(ip: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    q = _hits.setdefault(ip, deque())
    while q and now - q[0] > 60.0:
        q.popleft()
    if len(q) >= MAX_PER_MINUTE:
        return True
    q.append(now)
    return False


def _clean(text: object) -> str:
    s = _MARKERS.sub(" ", _CONTROL.sub(" ", str(text or "")))
    s = " ".join(s.split())
    return s[:MAX_SEEN].rstrip(" ,.;:")


def verdict(data: dict) -> dict:
    """The model's reply -> what the app is told. A pass needs ok, no named problem and something seen."""
    problem = str(data.get("problem") or "").strip().lower()
    problem = problem if problem in PROBLEMS else ""
    seen = _clean(data.get("seen"))
    if problem:
        return {"ok": False, "seen": "", "problem": problem}
    if data.get("ok") is True and seen:
        return {"ok": True, "seen": seen, "problem": None}
    return {"ok": False, "seen": "", "problem": "unclear"}


def _refuse(status: int, why: str) -> JSONResponse:
    return JSONResponse({"ok": False, "seen": "", "problem": why}, status_code=status)


@router.post("/api/device-check/camera")
async def camera_check(request: Request) -> JSONResponse:
    from aira_live.main import client_ip, origin_allowed  # lazy: main imports this module to mount it

    if not origin_allowed(request.headers.get("origin")):
        return _refuse(403, "refused")
    try:
        body = await request.json()
    except ValueError:
        return _refuse(400, "bad_request")
    if not isinstance(body, dict):
        return _refuse(400, "bad_request")
    settings = config.load()
    if settings.app_key and not hmac.compare_digest(str(body.get("key") or "").encode(), settings.app_key.encode()):
        return _refuse(403, "refused")
    image = body.get("image")
    if not isinstance(image, str):
        return _refuse(400, "bad_request")
    if len(image) > MAX_IMAGE_B64:
        return _refuse(413, "too_large")
    try:
        if not _JPEG.match(image):
            raise ValueError("not a jpeg")
        base64.b64decode(image, validate=True)
    except (ValueError, binascii.Error):
        return _refuse(400, "bad_request")
    ip = client_ip(request)
    if _limited(ip):
        return _refuse(429, "busy")
    lang = body.get("lang") if body.get("lang") in LANG_NAME else "en"
    device = body.get("device") if body.get("device") in _DEVICE_HINT else "phone"
    if settings.mock:
        return JSONResponse({"ok": True, "seen": "mock camera", "problem": None})
    prompt = PROMPT.format(device=_DEVICE_HINT[device], lang=LANG_NAME[lang])
    try:
        data = await generate_json("read", [image_part(image), prompt], SCHEMA, thinking="low", timeout_s=8.0)
    except VisionError as exc:
        log.warning("device check: vision failed: %s", exc)
        diag("device_check", severity="WARNING", stage="error", device=device, message=trunc(str(exc), 200))
        return _refuse(503, "unavailable")
    out = verdict(data)
    diag("device_check", stage="done", device=device, lang=lang, ok=out["ok"], problem=out["problem"],
         seen=trunc(out["seen"]), model=data.get("_model"))
    return JSONResponse(out)
