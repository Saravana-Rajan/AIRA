"""Runtime settings, read from the environment (Cloud Run env vars, Secret Manager mounts or a local .env).

The GCP project id is never written in code: it always comes from env ``GCP_PROJECT``.
"""
import os
from dataclasses import dataclass


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
    app_key: str | None = None  # shared demo key: when set, hello.idToken must equal it (docs/ops/security.md)
    job_token: str | None = None  # X-AIRA-Job-Token for /jobs/*


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
    )
