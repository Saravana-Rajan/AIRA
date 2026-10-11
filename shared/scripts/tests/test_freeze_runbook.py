"""Guards the Oct 18 deploy-freeze and submission runbook (saravana/03 Task 8).

Covers shared/docs/submission/freeze-runbook.md and shared/docs/submission/links.template.json. Both are documents
for people to follow on the day: nothing in this repo deploys, tags, pushes or submits anything.
These tests stop a late edit from:
- pointing a command at the wrong region, service, site or file,
- writing the project id into a committed file,
- breaking the order freeze -> links -> commit + tag -> form,
- drifting away from the video decision rule in shared/docs/submission/video-checklist.md.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import check_submission as cs

ROOT = Path(__file__).resolve().parents[3]
SUBMISSION = ROOT / "shared" / "docs" / "submission"
RUNBOOK = SUBMISSION / "freeze-runbook.md"
TEMPLATE = SUBMISSION / "links.template.json"
LINKS = SUBMISSION / "links.json"
VIDEO_CHECKLIST = SUBMISSION / "video-checklist.md"
DASHBOARD_RULES = ROOT / "shared" / "docs" / "hackathon" / "official" / "participation" / "dashboard-submission.md"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
MAIN_PY = ROOT / "backend" / "aira_live" / "main.py"
FIREBASE_JSON = ROOT / "shared" / "infra" / "firebase.json"
README = ROOT / "README.md"
GITIGNORE = ROOT / ".gitignore"

REPO = "https://github.com/Saravana-Rajan/AIRA"
APP_URL = "https://aira-shop.web.app"
REGION = "asia-south1"
SERVICE = "aira-live"
SITE = "aira-shop"
TAG = "v1.0.0-submission"
DEADLINE = "18 October 2026, 11:59 PM IST"
TEAM_TARGET = "20:00 IST"
LINK_KEYS = {"app", "dashboard", "api_health", "repo", "video"}
PLACEHOLDERS = {"APP_URL", "DASHBOARD_URL", "LIVE_URL", "VIDEO_URL"}
CHECKER_FLAGS = {"--description", "--links", "--pdf", "--video", "--max-video", "--strict-warn"}
# Files that only exist on the day (generated or git-ignored), so they may be named before they exist.
DAY_FILES = {
    "shared/docs/submission/links.json",
    "shared/docs/submission/AIRA-deck.pdf",
    "shared/docs/submission/aira-demo.mp4",
    "shared/docs/submission/aira-demo-short.mp4",
}

BANNED = (
    "udhaar",
    "kaggle",
    "aira business",
    "claude",
    "generated with",
    "counterfeit",
    "fake note",
    "bigquery",
    "pub/sub",
    "remote config",
    "cloud scheduler",
    "firebase auth",
    "app check",
    "us-central1",
    "builds triggers",
    "cloudbuild",
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INDIAN_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
ROOT_SUBMISSION_PATH = re.compile(r"(?<!shared/docs/)(?<![\w.-])submission/")
REPO_PATH = re.compile(r"(?<![\w/.-])((?:docs|scripts|infra|services|\.github)/[\w./-]*[\w-])")


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _joined(text: str) -> str:
    """Shell line continuations joined, so one command is one line."""
    return re.sub(r"\\\n\s*", " ", text)


def _section(text: str, heading: str) -> str:
    """Text from a level-2 heading that starts with `heading` to the next level-2 heading."""
    m = re.search(rf"^## {re.escape(heading)}.*$", text, flags=re.MULTILINE)
    assert m, f"missing section: ## {heading}"
    nxt = re.search(r"^## ", text[m.end():], flags=re.MULTILINE)
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def _commands(text: str, program: str) -> list[str]:
    """Every shell command line that starts with `program` (after `$(`, a prompt or an assignment)."""
    out = []
    for line in _joined(text).splitlines():
        m = re.search(rf"(?:^|[\s($=])({re.escape(program)}\s)", line)
        if m:
            out.append(line[m.start(1):].strip())
    return out


def _checker_commands(text: str) -> list[list[str]]:
    cmds = []
    for line in _joined(text).splitlines():
        if "python3 shared/scripts/check_submission.py" in line:
            cmd = line[line.index("python3 shared/scripts/check_submission.py"):].split("#")[0].strip().strip("`")
            cmds.append(shlex.split(cmd)[2:])
    return cmds


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def _video_limits(text: str) -> dict[str, float]:
    return {
        Path(_flag(a, "--video")).name: float(_flag(a, "--max-video") or 240.0)
        for a in _checker_commands(text)
        if "--video" in a
    }


def _fill(template: str, env: dict[str, str], monkeypatch) -> str:
    """The runbook's substitution: os.path.expandvars over the template, unset names left as-is."""
    import os

    for name in PLACEHOLDERS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return os.path.expandvars(template)


# ---------- links template ----------


def test_links_template_has_the_five_links():
    raw = TEMPLATE.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert set(data) == LINK_KEYS
    assert data["repo"] == REPO
    assert data["api_health"] == "${LIVE_URL}/health"
    assert set(re.findall(r"\$\{(\w+)\}", raw)) == PLACEHOLDERS
    for key, value in data.items():
        assert value.startswith("https://") or value.startswith("${"), key


def test_filled_template_passes_the_link_checker(monkeypatch):
    env = {
        "APP_URL": APP_URL,
        "DASHBOARD_URL": f"{APP_URL}/dashboard/",
        "LIVE_URL": "https://aira-live.example.run.app",
        "VIDEO_URL": "https://www.youtube.com/watch?v=example",
    }
    data = json.loads(_fill(TEMPLATE.read_text(encoding="utf-8"), env, monkeypatch))
    assert data["api_health"] == "https://aira-live.example.run.app/health"
    assert cs.check_links(data, fetch=lambda u: (200, u)) == []


def test_unset_placeholder_fails_the_link_checker(monkeypatch):
    """Forgetting VIDEO_URL leaves ${VIDEO_URL} in links.json, and the checker refuses it."""
    env = {"APP_URL": APP_URL, "DASHBOARD_URL": f"{APP_URL}/dashboard/", "LIVE_URL": "https://x.run.app"}
    data = json.loads(_fill(TEMPLATE.read_text(encoding="utf-8"), env, monkeypatch))
    errors = cs.check_links(data, fetch=lambda u: (200, u))
    assert errors == ["video: URL must start with https:// (${VIDEO_URL})"]


def test_generated_links_file_when_present():
    """links.json only exists from Oct 18. When it does, it is filled in and matches the template."""
    if not LINKS.exists():
        return
    raw = LINKS.read_text(encoding="utf-8")
    assert "${" not in raw
    data = json.loads(raw)
    assert set(data) == LINK_KEYS
    assert data["repo"] == REPO
    assert all(v.startswith("https://") for v in data.values())


def test_links_file_is_committed_but_deck_and_video_are_not():
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines()
    assert "shared/docs/submission/*.pdf" in lines
    assert "shared/docs/submission/*.mp4" in lines
    assert not any("links" in line for line in lines)


# ---------- the runbook matches the real setup ----------


def test_every_gcloud_command_targets_the_right_region_and_project():
    cmds = _commands(_runbook(), "gcloud")
    assert cmds, "the runbook must use gcloud"
    for cmd in cmds:
        assert '--project "$GCP_PROJECT"' in cmd, cmd
        if cmd.startswith("gcloud run"):
            assert f"--region {REGION}" in cmd, cmd
            assert SERVICE in cmd or '"$REV"' in cmd, cmd
        assert "deploy" not in cmd.split("#")[0].split(), f"the runbook never deploys: {cmd}"


def test_every_gh_command_targets_the_repo():
    cmds = _commands(_runbook(), "gh")
    assert cmds
    for cmd in cmds:
        assert "--repo Saravana-Rajan/AIRA" in cmd, cmd


def test_freeze_disables_the_real_deploy_workflow_first():
    text = _runbook()
    freeze = _section(text, "18:00 IST")
    assert "gh workflow disable deploy.yml --repo Saravana-Rajan/AIRA" in freeze
    assert "disabled_manually" in freeze
    assert freeze.index("gh workflow disable") < freeze.index("update-traffic")
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"push:\s*\n\s*branches:\s*\n\s*- main", workflow)
    assert f"gcloud run deploy {SERVICE}" in workflow
    assert f"hosting:{SITE}" in workflow


def test_freeze_pins_traffic_and_records_the_deployed_commit():
    freeze = _joined(_section(_runbook(), "18:00 IST"))
    assert "status.latestReadyRevisionName" in freeze
    assert '--to-revisions="$REV=100"' in freeze
    assert "yaml(status.traffic)" in freeze
    assert "autoscaling.knative.dev/minScale" in freeze
    assert "autoscaling.knative.dev/maxScale" in freeze
    assert "spec.containers[0].image" in freeze  # the image tag is the deployed commit sha
    assert "--min-instances 0" in DEPLOY_WORKFLOW.read_text(encoding="utf-8")


def test_health_check_matches_the_real_endpoint():
    text = _runbook()
    assert 'curl -fsS "$LIVE_URL/health"' in text
    assert '{"ok":true,"mock":false}' in text
    main = MAIN_PY.read_text(encoding="utf-8")
    assert '@app.get("/health")' in main
    assert '"mock":' in main


def test_health_path_avoids_cloud_run_reserved_z_paths():
    """Cloud Run's frontend reserves some paths that end in "z", so /healthz can fail on a healthy service."""
    for path in (RUNBOOK, TEMPLATE):
        assert "/healthz" not in path.read_text(encoding="utf-8"), path.name


def test_hosting_links_match_the_site():
    text = _runbook()
    assert f"export APP_URL={APP_URL}" in text
    assert f"export DASHBOARD_URL={APP_URL}/dashboard/" in text
    assert APP_URL in README.read_text(encoding="utf-8")
    assert f'"site": "{SITE}"' in FIREBASE_JSON.read_text(encoding="utf-8")


def test_links_are_generated_from_the_template_with_expandvars():
    text = _runbook()
    assert "os.path.expandvars" in text
    assert "< shared/docs/submission/links.template.json > shared/docs/submission/links.json" in text
    for name in PLACEHOLDERS:
        assert f"export {name}=" in text, name


# ---------- checker commands and the video decision rule ----------


def test_checker_commands_use_the_real_flags_and_files():
    cmds = _checker_commands(_runbook())
    assert cmds
    for args in cmds:
        flags = {a for a in args if a.startswith("--")}
        assert flags <= CHECKER_FLAGS, flags - CHECKER_FLAGS
        assert _flag(args, "--description") == "shared/docs/submission/description.txt"
        assert _flag(args, "--links") in (None, "shared/docs/submission/links.json")
        assert _flag(args, "--pdf") in (None, "shared/docs/submission/AIRA-deck.pdf")
    full = [a for a in cmds if {"--description", "--links", "--pdf", "--video"} <= set(a)]
    assert len(full) == 2, "one full check per video cut"


def test_video_limits_match_the_video_checklist():
    runbook = _video_limits(_runbook())
    checklist = _video_limits(VIDEO_CHECKLIST.read_text(encoding="utf-8"))
    assert runbook == checklist == {"aira-demo.mp4": 240.0, "aira-demo-short.mp4": 175.0}
    text = _runbook()
    assert "written approval" in text.lower()
    assert "2:55" in text


def test_the_upload_step_in_the_video_checklist_points_here():
    assert "18:15" in VIDEO_CHECKLIST.read_text(encoding="utf-8")
    _section(_runbook(), "18:15 IST")


# ---------- order of the day ----------


def test_sections_run_in_order():
    text = _runbook()
    headings = [h for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE)]
    times = [h.split(" IST")[0] for h in headings if re.match(r"\d\d:\d\d IST", h)]
    assert times == sorted(times)
    assert times[:5] == ["12:00", "18:00", "18:15", "19:15", "19:30"]


def test_links_are_committed_before_the_tag_and_the_tag_before_the_form():
    text = _runbook()
    commit = _section(text, "19:15 IST")
    assert "git add shared/docs/submission/links.json" in commit
    assert commit.index("git add shared/docs/submission/links.json") < commit.index(f"git tag {TAG}")
    assert f"git push origin main {TAG}" in commit
    assert "git diff --stat" in commit  # only shared/docs/submission changed since the deployed commit
    assert text.index(f"git tag {TAG}") < text.index("## 19:30 IST")


def test_deadlines():
    text = _runbook()
    assert TEAM_TARGET in text
    assert DEADLINE in text
    assert DEADLINE in DASHBOARD_RULES.read_text(encoding="utf-8")
    assert "Nov 6" in text


# ---------- the form ----------


def test_form_table_covers_every_field():
    form = _section(_runbook(), "19:30 IST")
    rows = [r.split("|")[1].strip().lower() for r in form.splitlines() if r.startswith("| ") and "---" not in r]
    for field in ("challenge", "prototype link", "deck", "github repository", "demo video", "brief description"):
        assert any(r.startswith(field) for r in rows), field
    assert REPO in form
    assert "shared/docs/submission/AIRA-deck.pdf" in form
    assert "shared/docs/submission/description.txt" in form
    assert "Sustainability & Social Impact" in form
    assert "screenshot" in form.lower()


# ---------- evaluation window ----------


def test_evaluation_window_rules():
    window = _section(_runbook(), "Oct 19")
    low = window.lower()
    assert "no pushes to main" in low
    assert "firebase deploy" in low
    assert "(default)" in window
    assert "private window" in low
    assert 'curl -fsS "$LIVE_URL/health"' in window
    assert "no budget alerts" in low


def test_rollback_uses_the_recorded_revision():
    rollback = _joined(_section(_runbook(), "If something breaks"))
    assert '--to-revisions="$REV=100"' in rollback
    assert "release history" in rollback.lower()


# ---------- hygiene ----------


def test_repo_hygiene_checks_before_the_tag():
    text = _runbook()
    assert 'test -n "$GCP_PROJECT"' in text
    assert 'git grep -n "$GCP_PROJECT"' in text
    assert 'git log --all -p | grep -c "$GCP_PROJECT"' in text
    assert "git log --format='%(trailers:only)'" in text
    assert text.index('git grep -n "$GCP_PROJECT"') < text.index(f"git tag {TAG}")


def test_paths_named_in_the_runbook_exist():
    for path in sorted(set(REPO_PATH.findall(_runbook()))):
        if path in DAY_FILES:
            continue
        assert (ROOT / path).exists(), path


def test_no_banned_words_personal_details_or_root_submission_paths():
    for path in (RUNBOOK, TEMPLATE):
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        for banned in BANNED:
            assert banned not in low, f"{path.name}: {banned}"
        assert not EMAIL_RE.search(text), path.name
        assert not INDIAN_MOBILE.search(text), path.name
        assert not ROOT_SUBMISSION_PATH.search(text), f"{path.name}: use shared/docs/submission/"
    assert "AIRA" in _runbook()


def test_runbook_is_a_checklist_for_people():
    text = _runbook()
    low = text.lower()
    assert "nothing in this repo" in low
    assert text.count("- [ ]") >= 25
    assert "## Log" in text
