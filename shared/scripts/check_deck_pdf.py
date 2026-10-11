#!/usr/bin/env python3
"""Keyword and honesty guard for the exported deck PDF (the text the AI pre-screen reads).

Usage:
  python3 shared/scripts/check_deck_pdf.py shared/docs/submission/AIRA-deck.pdf [--project-id ID]

Checks the text pdftotext extracts from each page: exactly 16 pages (the official template),
every rubric phrase, no leftover editing notes (<fill: ...>, file paths, script names), no service
the prototype does not run outside the roadmap slide, no counterfeit claim, no banned word, no AI
attribution, no project ID or API key. Size and the %PDF- header are checked by check_submission.py.

The project ID defaults to GCP_PROJECT from the environment or a local (git-ignored) .env.
Needs pdftotext from poppler (macOS: brew install poppler; Debian/Ubuntu: apt-get install poppler-utils).
Exit code 0 = passed, 1 = at least one check failed, 2 = could not read the PDF.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLIDES = 16
ROADMAP_SLIDE = 13
MIN_WORDS = 3

RUBRIC = [
    "Generative AI", "Gemini", "Google Cloud architecture", "Cloud Run", "Firestore", "Firebase",
    "scalab", "sustainab", "impact", "measurable", "original", "accessib", "user experience",
    "functional prototype",
]

# Removed by the build overrides (section 1), or not built (no evidence-image store, no on-phone
# face blur). The deck may name them only on the roadmap slide.
NOT_IN_PROTOTYPE = [
    "Pub/Sub", "BigQuery", "Remote Config", "Cloud Scheduler", "App Check",
    "Cloud Messaging", "FCM", "Firebase Auth", "Cloud Build", "us-central1",
    "Cloud Storage", "blurred",
]

BANNED_WORDS = ("udhaar", "kaggle", "aira business")
COUNTERFEIT_CLAIMS = ("counterfeit detection", "detects counterfeit", "fake note detection", "fake-note detection")
AI_ATTRIBUTION = ("co-authored-by", "generated with claude", "claude-session", "claude code")

LEFTOVER_RE = re.compile(r"<fill:|shared/docs/submission/|shared/scripts/\w+\.py")
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


def normalize(text: str) -> str:
    """NFKC (ligatures, non-breaking spaces), drop soft hyphens, one space between words."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    return re.sub(r"\s+", " ", text).strip()


def split_pages(text: str) -> list[str]:
    """pdftotext ends every page with a form feed; empty (image-only) pages are kept."""
    pages = text.split("\f")
    if len(pages) > 1 and pages[-1] == "":
        pages.pop()
    return pages


def extract_pages(pdf: Path, run=subprocess.run, exe: str = "pdftotext") -> list[str]:
    proc = run([exe, "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext failed on {pdf}: {proc.stderr.strip()}")
    return split_pages(proc.stdout)


def check_pages(pages: list[str], project_id: str = "") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    norm = [normalize(p) for p in pages]
    full = " ".join(norm).lower()

    if len(pages) != SLIDES:
        errors.append(f"deck has {len(pages)} pages (the template needs exactly {SLIDES})")

    missing = [k for k in RUBRIC if k.lower() not in full]
    if missing:
        errors.append("missing rubric keywords: " + ", ".join(missing))

    for number, text in enumerate(norm, start=1):
        low = text.lower()
        if len(text.split()) < MIN_WORDS:
            warnings.append(f"page {number} has no readable text (the AI pre-screen cannot see image-only slides)")
        if number != ROADMAP_SLIDE:
            for term in NOT_IN_PROTOTYPE:
                if term.lower() in low:
                    errors.append(f"page {number} names {term}, which the prototype does not use (roadmap slide only)")
        for m in LEFTOVER_RE.finditer(text):
            errors.append(f"page {number} has a leftover editing note: {m.group(0)!r}")
        for claim in COUNTERFEIT_CLAIMS:
            if claim in low:
                errors.append(f"page {number} claims {claim!r}; AIRA never claims to spot fake notes")
        for word in BANNED_WORDS + AI_ATTRIBUTION:
            if word in low:
                errors.append(f"page {number} contains the banned phrase {word!r}")
        if API_KEY_RE.search(text):
            errors.append(f"page {number} contains an API key")
        if "pending" in low:
            warnings.append(f"page {number} still says 'pending' (only for benchmark rows not yet measured)")

    if project_id:
        squashed = re.sub(r"\s+", "", full)
        if project_id.lower() in full or project_id.lower() in squashed:
            errors.append("deck shows the GCP project ID; crop it out of the screenshots")
    return errors, warnings


def project_id_from_env() -> str:
    """GCP_PROJECT from the environment or a local .env; never written in the repo."""
    project = os.environ.get("GCP_PROJECT", "").strip()
    for env_file in (ROOT / ".env", ROOT / "backend" / ".env"):
        if project or not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "GCP_PROJECT":
                project = value.strip().strip("\"'")
    return project


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Keyword and honesty guard for the exported AIRA deck PDF")
    p.add_argument("pdf", type=Path)
    p.add_argument("--project-id", default=None, help="defaults to GCP_PROJECT from env or .env")
    a = p.parse_args(argv)

    if not a.pdf.exists():
        print(f"FAIL  PDF not found: {a.pdf}")
        return 2
    exe = shutil.which("pdftotext")
    if exe is None:
        print("FAIL  pdftotext not found: install poppler (brew install poppler / apt-get install poppler-utils)")
        return 2
    try:
        pages = extract_pages(a.pdf, exe=exe)
    except RuntimeError as exc:
        print(f"FAIL  {exc}")
        return 2

    project = project_id_from_env() if a.project_id is None else a.project_id.strip()
    errors, warnings = check_pages(pages, project_id=project)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}")
    if errors:
        print(f"{len(errors)} check(s) failed")
        return 1
    print(f"OK    deck PDF: {len(pages)} pages, every rubric keyword present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
