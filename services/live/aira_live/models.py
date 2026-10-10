"""The ONLY place model IDs are written.

Priority (build overrides §7): env ``AIRA_MODEL_<ROLE>`` > the Firestore ``config/runtime`` doc
(``remote_config.override_for_role``, validated against this role's own models) > ``DEFAULTS``."""
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
    from aira_live import remote_config  # lazy: remote_config imports this module to validate overrides

    return os.getenv(f"AIRA_MODEL_{role.upper()}") or remote_config.override_for_role(role) or DEFAULTS[role]


def fallback_for(role: Role) -> str | None:
    return FALLBACKS.get(role)
