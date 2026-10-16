"""Runtime settings, read from the environment (Cloud Run env vars, Secret Manager mounts or a local .env).

The GCP project id is never written in code: it always comes from env ``GCP_PROJECT``.
"""
import os
from dataclasses import dataclass


# The hosting site only. Local dev adds "http://localhost:*,http://127.0.0.1:*" through AIRA_MONITOR_ORIGINS
# (shared/.env.example); an entry ending in ":*" matches any port on that scheme and host.
DEFAULT_MONITOR_ORIGINS: tuple[str, ...] = ("https://aira-shop.web.app", "https://aira-shop.firebaseapp.com")


def _origins(raw: str | None) -> tuple[str, ...]:
    """A comma-separated Origin list, trimmed, without trailing slashes, empty entries dropped."""
    out = tuple(o.strip().rstrip("/") for o in (raw or "").split(",") if o.strip())
    return out or DEFAULT_MONITOR_ORIGINS


def _viewers(raw: str | None) -> tuple[str, ...]:
    """AIRA_MONITOR_VIEWERS: comma-separated Google accounts allowed to watch the live feed. An entry is an email
    (lowercased here) or ``uid:<Firebase uid>`` (the uid kept as it is). Unset or blank: nobody (fail closed)."""
    out = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry.lower().startswith("uid:"):
            uid = entry[4:].strip()
            if uid:
                out.append("uid:" + uid)
        else:
            out.append(entry.lower())
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gcp_project: str | None
    mock: bool
    whatsapp_token: str | None
    whatsapp_phone_id: str | None
    whatsapp_verify_token: str | None
    telegram_token: str | None
    demo_shop_id: str = "sunrise_dairy"
    max_frame_b64: int = 1_500_000
    firestore_database: str = "aira"  # never "(default)"
    allow_no_token: bool = False  # hello without a Firebase ID token is accepted (no Firebase Auth)
    app_key: str | None = None  # shared demo key: when set, hello.idToken must equal it (shared/docs/ops/security.md)
    job_token: str | None = None  # X-AIRA-Job-Token for /jobs/*
    # AIRA_MONITOR_AUTH=1: the read-only /monitor socket requires the Google sign-in (a Firebase ID token for an
    # account in ``monitor_viewers``). Unset or "0" (the default, the owner's decision): no authentication, anyone
    # who can open the dashboard watches the live feed (shared/docs/ops/security.md §2a).
    monitor_auth: bool = False
    # AIRA_MONITOR_ORIGINS (comma-separated): browser Origins that may open the read-only /monitor socket
    # (aira_live.monitor.origin_allowed). Defence in depth only, not authentication: a script can send any Origin.
    monitor_origins: tuple[str, ...] = DEFAULT_MONITOR_ORIGINS
    # The Firebase project whose Authentication ID tokens the live feed accepts (the token's ``aud`` and the
    # issuer ``https://securetoken.google.com/<project>``): AIRA_FIREBASE_PROJECT, else GCP_PROJECT. None = no
    # token can verify (fail closed). Only matters with AIRA_MONITOR_AUTH=1.
    firebase_project_id: str | None = None
    # AIRA_MONITOR_VIEWERS: the Google accounts allowed to watch the live feed (emails, lowercased, or
    # ``uid:<uid>``) when AIRA_MONITOR_AUTH=1; empty = nobody may watch then. Ignored without the flag
    # (shared/docs/ops/security.md §2a).
    monitor_viewers: tuple[str, ...] = ()
    live_video: bool = True  # AIRA_LIVE_VIDEO: the phone's 1 fps ``live`` frames go into Gemini Live as video
    live_nudge: bool = True  # AIRA_LIVE_NUDGE: a settled scene change asks Live to speak up (aira_live.live_video)
    precount: bool = True  # AIRA_PRECOUNT: a settled scene change starts a background count (vision.precount)
    # AIRA_ER2=0: a Live-only test mode. No Robotics-ER tools, loops or pre-counts; Gemini Live answers every
    # camera question from its own video (counts said as "about").
    er2: bool = True
    # AIRA_WALK=1: "walk with me" guidance (walk_to / walk_stop / nav_report), perceived by Gemini Live itself
    # (no Robotics-ER), with AIRA_ER2 on or off. Off by default.
    walk: bool = False
    # AIRA_OWNER_NAME: how walking stops address the owner ("Sam, stop."). Unset: just "Stop."
    owner_name: str | None = None
    # AIRA_WALK_NAV_SESSION (on unless "0"): a walk gets its own navigation-only Live session (aira_live.nav_session)
    # so it keeps seeing while the owner talks; "0" checks through the conversation session in quiet moments only.
    walk_nav_session: bool = True
    # AIRA_SCENE_MEMORY=1: Live silently reports what it sees (scene_report) so AIRA can say where it last saw
    # something after the camera turned away (aira_live.scene_watch / scene_memory). Off by default.
    scene_memory: bool = False
    # AIRA_ROUTES=1 (needs AIRA_WALK=1): step-by-step indoor routes from the shop map (aira_live.shop_map,
    # vision.route): zone localisation through nav_report, the phone's step count, hand-written legs. Off by
    # default; also off, with a log line, when the map is missing or invalid.
    routes: bool = False
    # AIRA_ROUTES_MAP: path of the map file. Unset: shared/catalog/<demo shop id>_map.json in a repo checkout.
    routes_map: str | None = None


def _env(name: str) -> str | None:
    return os.getenv(name) or None


def load() -> Settings:
    key = _env("GEMINI_API_KEY")
    return Settings(
        gemini_api_key=key,
        gcp_project=_env("GCP_PROJECT"),
        mock=(os.getenv("AIRA_MOCK", "0") == "1") or key is None,
        whatsapp_token=_env("WHATSAPP_TOKEN"),
        whatsapp_phone_id=_env("WHATSAPP_PHONE_ID"),
        whatsapp_verify_token=_env("WHATSAPP_VERIFY_TOKEN"),
        telegram_token=_env("TELEGRAM_TOKEN"),
        demo_shop_id=_env("AIRA_DEMO_SHOP") or "sunrise_dairy",
        firestore_database=_env("FIRESTORE_DATABASE") or "aira",
        allow_no_token=os.getenv("AIRA_ALLOW_NO_TOKEN", "0") == "1",
        app_key=_env("AIRA_APP_KEY"),
        job_token=_env("AIRA_JOB_TOKEN"),
        monitor_auth=os.getenv("AIRA_MONITOR_AUTH", "0") == "1",
        monitor_origins=_origins(_env("AIRA_MONITOR_ORIGINS")),
        firebase_project_id=_env("AIRA_FIREBASE_PROJECT") or _env("GCP_PROJECT"),
        monitor_viewers=_viewers(_env("AIRA_MONITOR_VIEWERS")),
        live_video=os.getenv("AIRA_LIVE_VIDEO", "1") != "0",
        live_nudge=os.getenv("AIRA_LIVE_NUDGE", "1") != "0",
        precount=os.getenv("AIRA_PRECOUNT", "1") != "0",
        er2=os.getenv("AIRA_ER2", "1") != "0",
        walk=os.getenv("AIRA_WALK", "0") == "1",
        owner_name=_env("AIRA_OWNER_NAME"),
        walk_nav_session=os.getenv("AIRA_WALK_NAV_SESSION", "1") != "0",
        scene_memory=os.getenv("AIRA_SCENE_MEMORY", "0") == "1",
        routes=os.getenv("AIRA_ROUTES", "0") == "1",
        routes_map=_env("AIRA_ROUTES_MAP"),
    )
