"""Runtime switches without a redeploy (replaces Firebase Remote Config).

Source of truth: the Firestore doc ``config/runtime`` in database ``aira`` with the fields
``pointing_model``, ``count_model``, ``live_model`` (model ids), ``whatsapp_enabled`` (bool), ``cameras`` (list)
and ``languages`` (list of BCP-47 codes: overrides the shops' expected languages).
Values are cached for 60 s. A missing doc means "use the defaults"; a failed read keeps the last good
values and retries after 10 s. A model override is only honoured when it names one of that role's own
models in ``aira_live.models`` (its ``DEFAULTS`` or ``FALLBACKS`` entry), so the Live session can never be
pointed at a non-Live model or ER-2 pointing at the Live model; anything else is ignored with one warning.

Model priority (applied in ``models.model_for``): env ``AIRA_MODEL_<ROLE>`` > this doc > ``DEFAULTS``.

Emergency switch (ER 2 -> the role's fallback in ``models.FALLBACKS``) from a shell with ADC and
``GCP_PROJECT`` set (the CLI reads Firestore even when no Gemini key is set)::

    uv run python -m aira_live.remote_config show
    uv run python -m aira_live.remote_config set pointing_model fallback
    uv run python -m aira_live.remote_config set pointing_model default   # clear: back to DEFAULTS
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

from aira_live import config

log = logging.getLogger(__name__)

COLLECTION = "config"
DOC_ID = "runtime"
MODEL_FIELDS = ("pointing_model", "count_model", "live_model")
FLAG_FIELDS = ("whatsapp_enabled",)
PARAMS = MODEL_FIELDS + FLAG_FIELDS
CAMERAS_FIELD = "cameras"  # list of shop cameras (aira_live.cameras.config); default empty = off
LANGUAGES_FIELD = "languages"  # overrides every shop's expected languages (aira_live.i18n.expected)
_LIST_FIELDS = (CAMERAS_FIELD, LANGUAGES_FIELD)
_KEPT = PARAMS + _LIST_FIELDS
ROLE_PARAM = {"point": "pointing_model", "count": "count_model", "live": "live_model"}
PARAM_ROLE = {v: k for k, v in ROLE_PARAM.items()}
TTL_S = 60.0
RETRY_S = 10.0  # after a failed read, try again sooner than the full TTL

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_CLEAR = {"", "default"}  # set_value aliases that remove the override

_cache: dict[str, Any] = {}
_fetched_at: float | None = None
_inflight: asyncio.Task | None = None
_warned: set[tuple[str, str]] = set()


def _reset() -> None:
    """Forget cached values (tests and the CLI)."""
    global _fetched_at, _inflight
    _cache.clear()
    _warned.clear()
    _fetched_at = None
    _inflight = None


def parse_doc(doc: dict | None) -> dict[str, Any]:
    """Keep only known, non-empty fields. Strings are stripped; bools/ints are kept as they are."""
    out: dict[str, Any] = {}
    for name, value in (doc or {}).items():
        if name not in _KEPT or value is None:
            continue
        if name in _LIST_FIELDS:
            if isinstance(value, list):
                out[name] = value
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        out[name] = value
    return out


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
    return None


def _role_models(field: str) -> set[str]:
    """The models one field may name: its role's default and fallback (``live_model`` -> the Live model only)."""
    from aira_live import models  # local import: models.model_for imports this module

    role = PARAM_ROLE[field]
    return {m for m in (models.DEFAULTS.get(role), models.FALLBACKS.get(role)) if m}


def _reachable() -> bool:
    """Firestore is configured: an emulator, or a project (ADC), whatever the Gemini mock flag says."""
    return bool(os.getenv("FIRESTORE_EMULATOR_HOST") or config.load().gcp_project)


def _enabled() -> bool:
    """The running service reads Firestore only when it is reachable: an emulator, or a real project outside
    mock mode. The ops CLI uses ``_reachable`` instead (``refresh(ops=True)``)."""
    if os.getenv("FIRESTORE_EMULATOR_HOST"):
        return True
    return _reachable() and not config.load().mock


def _doc_ref():
    from aira_live import db as firestore_db  # lazy: keeps models.model_for light; tests patch db.db

    return firestore_db.db().collection(COLLECTION).document(DOC_ID)


def _stale(now: float | None = None) -> bool:
    if _fetched_at is None:
        return True
    return (time.monotonic() if now is None else now) - _fetched_at >= TTL_S


async def refresh(force: bool = False, *, ops: bool = False) -> dict[str, Any]:
    """Re-read ``config/runtime`` when the cache is older than 60 s (or always with ``force``).

    ``ops=True`` (the CLI) reads whenever Firestore is configured, even in mock mode: an operator's shell with
    only ADC and ``GCP_PROJECT`` has no Gemini key, and must still see the real switch values.
    """
    global _fetched_at
    now = time.monotonic()
    if not force and not _stale(now):
        return dict(_cache)
    try:
        if not (_reachable() if ops else _enabled()):
            _fetched_at = now
            return dict(_cache)
        snap = await _doc_ref().get()
        values = parse_doc(snap.to_dict() if snap.exists else None)
        _cache.clear()
        _cache.update(values)
        _fetched_at = now
    except Exception as exc:  # keep the last good values; retry soon
        log.warning("runtime config refresh failed, keeping last values: %s", exc)
        _fetched_at = now - TTL_S + RETRY_S
    return dict(_cache)


def _kick() -> None:
    """From sync accessors: if the cache is stale and an event loop is running, refresh in background."""
    global _inflight
    if not _stale():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _inflight is not None and not _inflight.done() and _inflight.get_loop() is loop:
        return
    _inflight = loop.create_task(refresh())


def _validated_model(field: str) -> str | None:
    value = _cache.get(field)
    if not isinstance(value, str) or not value:
        return None
    if value in _role_models(field):
        return value
    if (field, value) not in _warned:  # once per bad value, not on every model_for() call
        _warned.add((field, value))
        log.warning("ignoring model %r in config/runtime.%s: not one of %s", value, field,
                    sorted(_role_models(field)))
    return None


def override_for_role(role: str) -> str | None:
    """Validated model override for ``point`` / ``count`` / ``live``; ``None`` means use the default."""
    _kick()
    field = ROLE_PARAM.get(role)
    return _validated_model(field) if field else None


def get_override(param: str) -> Any:
    """Override by role (``point``) or by field (``pointing_model``, ``whatsapp_enabled``)."""
    _kick()
    field = ROLE_PARAM.get(param, param)
    if field in MODEL_FIELDS:
        return _validated_model(field)
    return _cache.get(field)


def flag(name: str, default: bool) -> bool:
    """Boolean switch, e.g. ``flag("whatsapp_enabled", True)``. Missing or unreadable -> ``default``."""
    _kick()
    parsed = _to_bool(_cache.get(name))
    return default if parsed is None else parsed


def cameras() -> list:
    """The ``cameras`` list from ``config/runtime`` (raw entries; ``aira_live.cameras.config`` validates them).
    Missing -> ``[]``: the shop-camera feature is off by default."""
    _kick()
    value = _cache.get(CAMERAS_FIELD)
    return list(value) if isinstance(value, list) else []


def languages() -> list[str] | None:
    """The ``languages`` override from ``config/runtime`` (BCP-47 codes), or ``None`` when the shop doc decides."""
    _kick()
    value = _cache.get(LANGUAGES_FIELD)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else None


async def run_refresher() -> None:
    """Background loop for the app lifespan: refresh now, then every 60 s."""
    while True:
        await refresh(force=True)
        await asyncio.sleep(TTL_S)


def _model_value(field: str, value: str) -> str:
    from aira_live import models

    value = value.strip()
    if value.lower() == "fallback":
        fallback = models.FALLBACKS.get(PARAM_ROLE[field])
        if not fallback:
            raise ValueError(f"{field} has no fallback model in aira_live.models.FALLBACKS")
        return fallback
    allowed = _role_models(field)
    if value not in allowed:
        raise ValueError(f"{value!r} is not a {PARAM_ROLE[field]} model; expected one of {', '.join(sorted(allowed))}")
    return value


async def set_value(field: str, value: Any) -> dict[str, Any]:
    """Ops writer for the emergency switch. Validates, merges into ``config/runtime``, refreshes the cache.

    For model fields, ``fallback`` means the role's model in ``models.FALLBACKS``. An empty value or
    ``default`` clears the override (back to ``models.DEFAULTS``).
    """
    if field not in PARAMS:
        raise ValueError(f"unknown field {field!r}; expected one of {', '.join(PARAMS)}")
    stored: Any
    if value is None or (isinstance(value, str) and value.strip().lower() in _CLEAR):
        stored = None
    elif field in MODEL_FIELDS:
        stored = _model_value(field, str(value))
    else:
        stored = _to_bool(value)
        if stored is None:
            raise ValueError(f"{value!r} is not a boolean (use true/false)")
    await _doc_ref().set({field: stored, "updated_at": datetime.now(timezone.utc)}, merge=True)
    return await refresh(force=True, ops=True)  # read back what was just written, mock mode or not


def _main(argv: list[str]) -> int:
    usage = "usage: python -m aira_live.remote_config show | set <field> <value|fallback|default>"
    if not argv or argv[0] not in ("show", "set") or (argv[0] == "set" and len(argv) != 3):
        print(usage, file=sys.stderr)
        return 2
    if argv[0] == "show":
        values = asyncio.run(refresh(force=True, ops=True))
    else:
        try:
            values = asyncio.run(set_value(argv[1], argv[2]))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print({k: values.get(k) for k in PARAMS})
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
