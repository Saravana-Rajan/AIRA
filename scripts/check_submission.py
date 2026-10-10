#!/usr/bin/env python3
"""Pre-submission checks for the Google Cloud AI Builder Cup form (stdlib only).

Usage:
  python3 scripts/check_submission.py --description docs/submission/description.txt \
      --links docs/submission/links.json --pdf docs/submission/AIRA-deck.pdf \
      --video docs/submission/aira-demo.mp4 [--max-video 240] [--strict-warn 179]
Exit code 0 = every required check passed; 1 = at least one failure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

MAX_DESCRIPTION = 1024
MAX_PDF_BYTES = 5_000_000  # portal says "up to 5 MB"; stay strictly below 5,000,000 bytes
REQUIRED_TERMS = {
    "Firebase": r"\bFirebase\b",
    "Firestore": r"\bFirestore\b",
    "Cloud Run": r"\bCloud[\s-]+Run\b",
    "Gemini": r"\bGemini\b",
}
LOGIN_MARKERS = ("accounts.google.com", "ServiceLogin", "/signin", "/login")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def js_length(text: str) -> int:
    """Length the way a browser textarea counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def check_description(text: str) -> list[str]:
    errors: list[str] = []
    n = js_length(text)
    if n > MAX_DESCRIPTION:
        errors.append(f"description is {n} chars (max {MAX_DESCRIPTION})")
    for name, pattern in REQUIRED_TERMS.items():
        if not re.search(pattern, text, flags=re.IGNORECASE):
            errors.append(f"description must mention {name}")
    if re.search(r"\b(TODO|TBD)\b", text):
        errors.append("description contains TODO/TBD")
    return errors


def fetch_status(url: str, timeout: float = 15.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AIRA submission check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, url
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, url


def check_links(links: dict[str, str], fetch=fetch_status) -> list[str]:
    errors: list[str] = []
    for name, url in links.items():
        if not url.startswith("https://"):
            errors.append(f"{name}: URL must start with https:// ({url})")
            continue
        status, final_url = fetch(url)
        if status != 200:
            errors.append(f"{name}: HTTP {status} for {url}")
        elif any(marker in final_url for marker in LOGIN_MARKERS):
            errors.append(f"{name}: redirects to a login page ({final_url}); make it public")
    return errors


def check_pdf(path: Path) -> list[str]:
    if not path.exists():
        return [f"PDF not found: {path}"]
    errors: list[str] = []
    size = path.stat().st_size
    if size >= MAX_PDF_BYTES:
        errors.append(f"PDF is {size} bytes (must be under {MAX_PDF_BYTES})")
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            errors.append("file is not a PDF (missing %PDF- header)")
    return errors


def video_duration(path: Path, run=subprocess.run) -> float:
    out = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def check_video(path: Path, max_s: float, strict_warn_s: float, run=subprocess.run) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [f"video not found: {path}"], []
    try:
        duration = video_duration(path, run=run)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as e:
        return [f"could not read video duration with ffprobe: {e}"], []
    errors: list[str] = []
    warnings: list[str] = []
    if duration > max_s:
        errors.append(f"video is {duration:.1f}s (max {max_s:.0f}s)")
    if duration > strict_warn_s:
        warnings.append(
            f"video is {duration:.1f}s; the official terms say strictly under 3:00 - "
            "keep support's written approval for the longer cut on file"
        )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AI Builder Cup pre-submission checks")
    p.add_argument("--description", type=Path, required=True)
    p.add_argument("--links", type=Path)
    p.add_argument("--pdf", type=Path)
    p.add_argument("--video", type=Path)
    p.add_argument("--max-video", type=float, default=240.0)
    p.add_argument("--strict-warn", type=float, default=179.0)
    a = p.parse_args(argv)

    report = Report()
    if a.description.exists():
        report.errors += check_description(a.description.read_text(encoding="utf-8").strip())
    else:
        report.errors.append(f"description not found: {a.description}")
    if a.links:
        if a.links.exists():
            report.errors += check_links(json.loads(a.links.read_text(encoding="utf-8")))
        else:
            report.errors.append(f"links file not found: {a.links}")
    if a.pdf:
        report.errors += check_pdf(a.pdf)
    if a.video:
        errors, warnings = check_video(a.video, a.max_video, a.strict_warn)
        report.errors += errors
        report.warnings += warnings

    for w in report.warnings:
        print(f"WARN  {w}")
    for e in report.errors:
        print(f"FAIL  {e}")
    print("OK    all checks passed" if report.ok else f"{len(report.errors)} check(s) failed")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
