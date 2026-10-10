"""The ONLY place model IDs are written. Env override: AIRA_MODEL_<ROLE> (the Firestore config/runtime override
lands in saravana/02)."""
import os
from typing import Literal

Role = Literal["live", "point", "count", "read", "verify"]

DEFAULTS: dict[str, str] = {
    "live": "gemini-3.8-live",
    "point": "gemini-robotics-er-2-preview",
    "count": "gemini-robotics-er-2-preview",
    "read": "gemini-3.8-flash",
    # A different model generation than read A, so the two reads are not correlated (build overrides §7).
    "verify": "gemini-3.6-flash",
}
FALLBACKS: dict[str, str] = {"point": "gemini-3.8-flash", "count": "gemini-3.8-flash"}


def model_for(role: Role) -> str:
    return os.getenv(f"AIRA_MODEL_{role.upper()}") or DEFAULTS[role]


def fallback_for(role: Role) -> str | None:
    return FALLBACKS.get(role)
