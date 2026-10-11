"""Guards the user-validation pack in shared/docs/submission/user-validation/.

The pack is text that a person reads aloud or sends by hand; nothing here sends anything.
These tests stop a late edit from breaking the data-sheet format, the conversion to
benchmark rows (shared/scripts/bench_table.py), or the privacy and wording rules.
"""
import csv
import re
from pathlib import Path

import bench_table

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "shared" / "docs" / "submission" / "user-validation"
PACK_FILES = ("outreach.md", "protocol.md", "consent-form.md", "data-sheet.csv")

DATA_SHEET_HEADER = [
    "participant_id",
    "date",
    "build",
    "task_id",
    "with_aira",
    "completed",
    "time_s",
    "errors_caught",
    "aira_wrong",
    "aira_unsure",
    "assists",
    "confidence_1_5",
    "quote",
]
AIRA_TASKS = ("T1", "T2", "T3", "T4", "T5", "T6", "T7a", "T7b")
BASELINE_TASKS = ("B6", "B7")
OPTIONAL_TASKS = ("T8", "T9")
STUDY_METRICS = ("sale_time_with_aira", "sale_time_baseline", "task_completion")

BANNED = (
    "udhaar",
    "kaggle",
    "aira business",
    "counterfeit",
    "claude",
    "generated with",
    "co-authored-by",
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INDIAN_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
TAMIL = re.compile(r"[஀-௿]")


def _read(name: str) -> str:
    return (PACK / name).read_text(encoding="utf-8")


def _rows() -> list[dict[str, str]]:
    with (PACK / "data-sheet.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_pack_files_exist():
    for name in PACK_FILES:
        path = PACK / name
        assert path.is_file(), name
        assert path.read_text(encoding="utf-8").strip(), name


def test_data_sheet_header_is_exact():
    first = _read("data-sheet.csv").splitlines()[0]
    assert first.split(",") == DATA_SHEET_HEADER


def test_data_sheet_rows_are_valid():
    """Empty until the study; once filled, every row must follow the protocol's codes."""
    known = set(AIRA_TASKS) | set(BASELINE_TASKS) | set(OPTIONAL_TASKS)
    for i, row in enumerate(_rows(), start=2):
        where = f"data-sheet.csv line {i}"
        assert re.fullmatch(r"P\d+", row["participant_id"]), where
        assert row["task_id"] in known, where
        expected_with = "N" if row["task_id"] in BASELINE_TASKS else "Y"
        assert row["with_aira"] == expected_with, where
        assert row["completed"] in {"Y", "N", "H"}, where
        for field in ("errors_caught", "aira_wrong", "aira_unsure", "assists"):
            assert row[field] == "" or row[field].isdigit(), f"{where} {field}"
        if row["time_s"]:
            assert float(row["time_s"]) >= 0, where
        if row["confidence_1_5"]:
            assert row["confidence_1_5"] in {"1", "2", "3", "4", "5"}, where
        if row["completed"] == "H":
            assert row["assists"].isdigit() and int(row["assists"]) > 0, where


def test_protocol_names_every_task():
    text = _read("protocol.md")
    for task in AIRA_TASKS + BASELINE_TASKS + OPTIONAL_TASKS:
        assert re.search(rf"\b{task}\b", text), task


def test_protocol_conversion_matches_benchmark_format():
    text = _read("protocol.md")
    assert ",".join(bench_table.HEADER) in text
    for metric in STUDY_METRICS:
        assert metric in bench_table.EXPECTED_METRICS, metric
        assert metric in text, metric
    assert "shared/bench/results/user_study.csv" in text


def test_protocol_covers_click_to_chat_and_safety():
    text = _read("protocol.md").lower()
    assert "click-to-chat" in text
    assert "stop" in text
    assert "cane" in text  # the bump guard never replaces the participant's own mobility aid


def test_outreach_has_english_and_tamil():
    text = _read("outreach.md")
    assert "## English" in text
    assert "## தமிழ்" in text
    tamil = text.split("## தமிழ்", 1)[1]
    assert TAMIL.search(tamil)
    assert text.count("AIRA") >= 2
    assert "not sent" in text.lower() or "do not send" in text.lower()


def test_consent_covers_rights_and_choices():
    text = _read("consent-form.md")
    low = text.lower()
    for phrase in ("voluntary", "stop", "participant id", "ten years", "delete"):
        assert phrase in low, phrase
    # four separate yes/no choices: take part, film hands/voice, face, quotes
    assert low.count("☐ | ☐") >= 4
    assert TAMIL.search(text), "consent needs a Tamil reading version"


def test_pack_has_no_contact_details_or_banned_words():
    for name in PACK_FILES:
        text = _read(name)
        assert not EMAIL.search(text), f"{name}: email address"
        assert not INDIAN_MOBILE.search(text), f"{name}: phone number"
        low = text.lower()
        for banned in BANNED:
            assert banned not in low, f"{name}: {banned}"
