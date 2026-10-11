"""Guards the demo-video plan (saravana/03 Task 7).

Covers docs/submission/support-email.md (a draft; nothing in this repo sends email) and
docs/submission/video-checklist.md (3:50 main cut, <= 2:55 fallback cut, decision rule).
These tests stop a late edit from breaking the timings, the decision rule, the commands
that feed scripts/check_submission.py, or the honesty and privacy rules.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import check_submission as cs

ROOT = Path(__file__).resolve().parents[2]
SUBMISSION = ROOT / "docs" / "submission"
EMAIL = SUBMISSION / "support-email.md"
CHECKLIST = SUBMISSION / "video-checklist.md"
GITIGNORE = ROOT / ".gitignore"
DASHBOARD_RULES = ROOT / "docs" / "hackathon" / "official" / "participation" / "dashboard-submission.md"
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"

MAIN_TARGET_S = 230  # 3:50
MAIN_MAX_S = 240  # never over 4:00
SHORT_MAX_S = 175  # 2:55: strictly under 3:00, with margin
STRICT_WARN_S = 179

# Spec §8 scene table, in order. The main cut must keep these exact ranges.
SPEC_SCENES = [
    ("0:00", "0:25"),
    ("0:25", "0:55"),
    ("0:55", "1:40"),
    ("1:40", "2:05"),
    ("2:05", "2:55"),
    ("2:55", "3:20"),
    ("3:20", "3:40"),
    ("3:40", "3:50"),
]
WORDINGS = ("3 to 4 minute", "up to 3 minutes", "strictly below three minutes")
DEADLINE = "18 October 2026, 11:59 PM IST"
SUPPORT = "support@hack2skill.com"
CHECKER_FLAGS = {"--description", "--links", "--pdf", "--video", "--max-video", "--strict-warn"}
# The order caption must name the channel that really sent the order (messaging lane: WhatsApp
# Cloud API -> Telegram bot -> click-to-chat). Catalog channel -> words the caption rule must use.
CHANNEL_CAPTION = {"telegram": "telegram bot", "whatsapp": "whatsapp business cloud api"}

BANNED = (
    "udhaar",
    "kaggle",
    "aira business",
    "claude",
    "generated with",
    "co-authored-by",
    "counterfeit",
    "fake note",
    "bigquery",
    "pub/sub",
    "remote config",
    "cloud scheduler",
    "firebase auth",
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INDIAN_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
ROOT_SUBMISSION_PATH = re.compile(r"(?<!docs/)(?<![\w.-])submission/")
RANGE_ROW = re.compile(r"^\|\s*(\d:\d\d)\s*[–-]\s*(\d:\d\d)\s*\|", flags=re.MULTILINE)
CUT_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", flags=re.MULTILINE)


class FakeRun:
    def __init__(self, seconds: float):
        self.seconds = seconds

    def __call__(self, cmd, **kwargs):
        class Out:
            stdout = f"{self.seconds}\n"

        return Out()


def _secs(mmss: str) -> int:
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def _section(text: str, heading: str) -> str:
    """Text from a level-2 heading that starts with `heading` to the next level-2 heading."""
    m = re.search(rf"^## {re.escape(heading)}.*$", text, flags=re.MULTILINE)
    assert m, f"missing section: ## {heading}"
    nxt = re.search(r"^## ", text[m.end():], flags=re.MULTILINE)
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def _checker_commands(text: str) -> list[list[str]]:
    """Every `python3 scripts/check_submission.py ...` command, with line continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    cmds = []
    for line in joined.splitlines():
        if "python3 scripts/check_submission.py" in line:
            cmd = line[line.index("python3 scripts/check_submission.py"):].strip().strip("`")
            cmds.append(shlex.split(cmd)[2:])
    return cmds


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def _email() -> str:
    return EMAIL.read_text(encoding="utf-8")


def _checklist() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


# ---------- support email draft ----------


def test_email_is_a_draft_addressed_to_support():
    text = _email()
    low = text.lower()
    assert "draft" in low
    assert "not sent" in low
    assert "oct 11" in low
    assert f"To: {SUPPORT}" in text
    assert re.search(r"^Subject: .*AIRA.*video length", text, flags=re.MULTILINE | re.IGNORECASE)


def test_email_quotes_all_three_wordings_and_asks_in_writing():
    text = _email()
    for wording in WORDINGS:
        assert wording in text, wording
    low = text.lower()
    assert "in writing" in low
    assert "4:00" in text
    assert "under 3:00" in text


def test_email_deadline_matches_the_dashboard():
    assert DEADLINE in _email()
    assert DEADLINE in DASHBOARD_RULES.read_text(encoding="utf-8")


def test_email_has_a_follow_up_and_no_personal_contact_details():
    text = _email()
    assert "follow-up" in text.lower()
    assert set(EMAIL_RE.findall(text)) == {SUPPORT}
    assert not INDIAN_MOBILE.search(text)


# ---------- video checklist: timings ----------


def test_main_cut_keeps_the_spec_scene_ranges():
    rows = RANGE_ROW.findall(_section(_checklist(), "Shot list"))
    assert rows == SPEC_SCENES
    assert _secs(rows[-1][1]) == MAIN_TARGET_S <= MAIN_MAX_S


def test_fallback_cut_trims_every_scene_and_stays_under_three_minutes():
    rows = CUT_ROW.findall(_section(_checklist(), "Fallback cut"))
    assert len(rows) == len(SPEC_SCENES)
    main = [int(r[1]) for r in rows]
    short = [int(r[2]) for r in rows]
    spec = [_secs(b) - _secs(a) for a, b in SPEC_SCENES]
    assert main == spec  # the main-cut column matches the shot list
    assert sum(main) == MAIN_TARGET_S
    for scene, m, s in zip((r[0] for r in rows), main, short):
        assert 0 < s <= m, scene
    assert sum(short) <= SHORT_MAX_S
    assert "2:55" in _checklist()


# ---------- video checklist: decision rule and commands ----------


def test_decision_rule_defaults_to_the_short_cut():
    rule = _section(_checklist(), "Decision rule")
    assert "Oct 16" in rule
    assert "18:00 IST" in rule
    assert "in writing" in rule.lower()
    assert "aira-demo.mp4" in rule
    assert "aira-demo-short.mp4" in rule
    assert "no written" in rule.lower()


def test_checker_commands_are_valid_and_match_each_cut():
    cmds = _checker_commands(_checklist())
    assert cmds, "the checklist must run scripts/check_submission.py"
    by_video: dict[str, float] = {}
    for args in cmds:
        flags = {a for a in args if a.startswith("--")}
        assert flags <= CHECKER_FLAGS, flags - CHECKER_FLAGS
        assert _flag(args, "--description") == "docs/submission/description.txt"
        video = _flag(args, "--video")
        if video:
            assert video.startswith("docs/submission/"), video
            by_video[Path(video).name] = float(_flag(args, "--max-video") or 240.0)
    assert by_video == {"aira-demo.mp4": MAIN_MAX_S, "aira-demo-short.mp4": SHORT_MAX_S}  # 2:55, not 2:59


def test_checker_enforces_the_decision_rule():
    """The limits written in the checklist, run through the real checker with a fake ffprobe."""
    cmds = _checker_commands(_checklist())
    limits = {Path(_flag(a, "--video")).name: float(_flag(a, "--max-video") or 240.0) for a in cmds if "--video" in a}
    main_max, short_max = limits["aira-demo.mp4"], limits["aira-demo-short.mp4"]
    video = CHECKLIST  # any existing file; the duration comes from the fake ffprobe

    # The fallback cut passes the strict command with no FAIL and no WARN.
    assert cs.check_video(video, short_max, STRICT_WARN_S, run=FakeRun(SHORT_MAX_S)) == ([], [])
    # The 3:50 cut can never pass the strict command by mistake.
    errors, _ = cs.check_video(video, short_max, STRICT_WARN_S, run=FakeRun(MAIN_TARGET_S))
    assert errors
    # With written approval the 3:50 cut passes, and the checker still warns about the terms.
    errors, warnings = cs.check_video(video, main_max, STRICT_WARN_S, run=FakeRun(MAIN_TARGET_S))
    assert errors == [] and len(warnings) == 1
    # Over 4:00 always fails.
    errors, _ = cs.check_video(video, main_max, STRICT_WARN_S, run=FakeRun(MAIN_MAX_S + 0.4))
    assert errors


def test_videos_are_git_ignored():
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines()
    assert "docs/submission/*.mp4" in lines


# ---------- honesty, privacy and wording ----------


def test_checklist_honesty_rules():
    text = _checklist()
    low = text.lower()
    assert "consent" in low and "face" in low
    assert "click-to-chat" in low and "cloud api" in low  # caption says how the order was really sent
    assert "firestore" in low and "forecast" in low  # the forecast comes from Firestore, not BigQuery
    assert "soundbox" in low
    assert "bench_table.py" in text and "pending" in low  # no guessed numbers on screen
    assert "private window" in low and "public" in low
    assert "reshoot" in low
    assert text.count("- [ ]") >= 15


def test_files_use_docs_submission_paths_and_no_banned_words():
    for path in (EMAIL, CHECKLIST):
        text = path.read_text(encoding="utf-8")
        assert not ROOT_SUBMISSION_PATH.search(text), f"{path.name}: use docs/submission/"
        low = text.lower()
        for banned in BANNED:
            assert banned not in low, f"{path.name}: {banned}"
        assert "AIRA" in text


# ---------- vendor channel and console privacy ----------


def test_order_caption_rule_names_every_vendor_channel():
    honesty = _section(_checklist(), "Honesty rules").lower()
    suppliers = json.loads(CATALOG.read_text(encoding="utf-8"))["suppliers"]
    channels = {s["channel"] for s in suppliers} - {"call"}  # a phone call is not filmed
    for channel in channels | set(CHANNEL_CAPTION):
        assert CHANNEL_CAPTION[channel] in honesty, channel
    assert "click-to-chat" in honesty


def test_vendor_reply_is_not_spoken_after_click_to_chat():
    # A click-to-chat reply lands in the owner's own WhatsApp, so AIRA never speaks it
    # (docs/ops/whatsapp-setup.md, "Free fallbacks").
    honesty = _section(_checklist(), "Honesty rules").lower()
    assert any("click-to-chat" in line and "reply" in line and "never dub" in line for line in honesty.splitlines())


def test_before_the_shoot_sets_up_the_real_vendor_channel():
    before = _section(_checklist(), "Before the shoot")
    assert "content/catalog/murugan_dairy.json" in before
    assert "Telegram" in before
    assert "Start" in before  # a bot can message the vendor only after they press Start
    assert "telegramChatId" in before


def test_scene_names_do_not_promise_a_channel():
    text = _checklist()
    shots = _section(text, "Shot list")
    rows = CUT_ROW.findall(_section(text, "Fallback cut"))
    assert "Vendor order by voice" in shots
    assert not any("whatsapp" in r[0].lower() for r in rows)


def test_console_shots_hide_the_project_id_and_account():
    lines = _checklist().lower().splitlines()
    assert any(
        "project id" in line and "email" in line and ("blur" in line or "crop" in line) for line in lines
    )


def test_proof_of_deployment_shots_hide_the_project_id_number_and_service_url():
    """The video is public: the consoles show the GCP project id, its number and the *.run.app URL."""
    text = _checklist()
    rows = [line for line in text.splitlines() if "Proof of deployment" in line and line.startswith("|")]
    assert len(rows) == 2, "one shot-list row and one fallback row"
    for row in rows:
        low = row.lower()
        assert "blur" in low or "hide" in low or "hidden" in low, row
        assert "project id" in low and "project number" in low and "run.app" in low, row
    honesty = _section(text, "Honesty rules").lower()
    assert any("project id" in l and "project number" in l and "console" in l for l in honesty.splitlines())


def test_vendor_channel_is_telegram_now_and_whatsapp_cloud_api_is_next():
    text = _checklist()
    shots = _section(text, "Shot list")
    order_row = next(line for line in shots.splitlines() if "Vendor order by voice" in line)
    assert "Telegram" in order_row and "docs/ops/telegram-setup.md" in order_row
    assert "next" in order_row.lower() and "cloud api" in order_row.lower()
    assert "docs/ops/telegram-setup.md" in _section(text, "Before the shoot")
    assert (ROOT / "docs" / "ops" / "telegram-setup.md").exists()
