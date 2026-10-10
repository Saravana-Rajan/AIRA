"""docs/ops/monitoring.md must match what the service actually exposes (no network, text checks only)."""
from __future__ import annotations

import re
from pathlib import Path

from aira_live import jobs, remote_config

REPO = Path(__file__).resolve().parents[4]
DOC = REPO / "docs" / "ops" / "monitoring.md"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_uptime_check_probes_health_not_healthz():
    # Cloud Run's frontend reserves some paths ending in "z", so probes use /health (see aira_live/main.py).
    text = _text()
    assert "--path=/health " in text or "--path=/health\n" in text
    assert "--path=/healthz" not in text
    assert "| Path | `/healthz` |" not in text


def test_health_route_exists():
    from aira_live.main import app

    assert "/health" in {getattr(r, "path", None) for r in app.routes}


def test_documents_every_runtime_field():
    text = _text()
    assert "config/runtime" in text
    for field in remote_config.PARAMS:
        assert f"`{field}`" in text, field


def test_documents_job_routes_and_token_header():
    text = _text()
    assert jobs.TOKEN_HEADER in text
    assert "AIRA_JOB_TOKEN" in text
    for route in ("/jobs/expiry", "/jobs/day-summary", "/jobs/forecast-refresh"):
        assert route in text, route


def test_cli_examples_use_real_cli_verbs():
    for field, value in re.findall(r"remote_config set (\w+) (\S+)", _text()):
        assert field in remote_config.PARAMS, field
        assert value, field


def test_no_project_id_or_removed_services_configured():
    text = _text()
    assert "$GCP_PROJECT" in text
    assert not re.search(r"gemini-enterprise-\d+", text)
    # Removed by the build overrides: mentioned only in the "not set up" section, never as steps.
    steps = text.split("## 5.")[0]
    for gone in ("gcloud scheduler", "gcloud pubsub", "bq mk", "gcloud billing budgets", "remoteconfig"):
        assert gone not in steps, gone
