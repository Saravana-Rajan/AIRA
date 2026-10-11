"""Top-level layout: frontend/, backend/, shared/ (owner request 2026-10-11) and the Sunrise Dairy persona.

Run: cd backend && uv run pytest -q tests/infra/test_repo_layout.py
The old folder names and the old persona are spelled from pieces here, so a grep for them stays empty.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "package.json").exists() and (p / "firebase.json").exists():
            return p
    return Path(__file__).resolve().parents[3]


ROOT = _repo_root()
# Pre-restructure checkout: nothing to check yet. The restructure script moves the folders; after that every
# test here runs.
pytestmark = pytest.mark.skipif(not (ROOT / "backend").is_dir(), reason="repo not restructured yet")

OLD_PERSONA = "mur" + "ugan"
OLD_TOP_LEVEL = ["ap" + "ps", "serv" + "ices", "pack" + "ages", "cont" + "ent", "inf" + "ra", "scri" + "pts",
                 "ben" + "ch", "do" + "cs", "ass" + "ets"]
_OLD_NESTED = ["serv" + "ices/live", "ap" + "ps/app", "ap" + "ps/dashboard", "pack" + "ages/contracts",
               "cont" + "ent/catalog", "cont" + "ent/schemas"]
# Root-relative spellings of the old paths. The bare folders are checked only when not preceded by a path
# character (shared/docs/..., frontend/app/src/bench/ are fine).
OLD_PATHS = re.compile(
    "|".join(re.escape(p) for p in _OLD_NESTED)
    + r"|(?<![\w./$-])(?:" + "|".join(OLD_TOP_LEVEL[4:]) + ")/"
)

EXPECTED_DIRS = [
    "frontend/app/src",
    "frontend/dashboard/src",
    "backend/aira_live",
    "backend/tests",
    "shared/contracts/src",
    "shared/catalog/schemas",
    "shared/infra",
    "shared/scripts",
    "shared/bench",
    "shared/docs",
    "shared/assets/logo",
]
EXPECTED_FILES = [
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/Dockerfile",
    "shared/catalog/sunrise_dairy.json",
    "shared/catalog/schemas/catalog.schema.json",
    "shared/infra/firestore.rules",
    "shared/infra/firestore.indexes.json",
    "shared/scripts/build_hosting.sh",
    "shared/scripts/seed_firestore.py",
    "shared/docs/ideas/02-sam-day.md",
]
ROOT_ALLOWED = {
    ".github", "README.md", "CLAUDE.md", "package.json", "package-lock.json", "firebase.json", ".firebaserc",
    ".env.example", ".gitignore", "frontend", "backend", "shared",
}


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    files = []
    for rel in out.decode().split("\0"):
        if not rel:
            continue
        p = ROOT / rel
        if not p.is_file() or p.suffix in {".png", ".jpg", ".jpeg", ".webp", ".ico", ".mp4", ".pdf", ".woff", ".woff2"}:
            continue
        if p.name == "package-lock.json":
            continue
        files.append(p)
    return files


def _lines():
    for p in _tracked_text_files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            yield p.relative_to(ROOT).as_posix(), i, line


@pytest.mark.parametrize("rel", EXPECTED_DIRS)
def test_expected_dir_exists(rel):
    assert (ROOT / rel).is_dir(), rel


@pytest.mark.parametrize("rel", EXPECTED_FILES)
def test_expected_file_exists(rel):
    assert (ROOT / rel).is_file(), rel


@pytest.mark.parametrize("rel", OLD_TOP_LEVEL)
def test_old_top_level_folder_is_gone(rel):
    assert not (ROOT / rel).exists(), f"{rel}/ must not exist at the repo root any more"


def test_root_has_only_the_allowed_entries():
    stray = sorted(
        e.name for e in ROOT.iterdir()
        if e.name not in ROOT_ALLOWED and not e.name.startswith(".") and e.name not in {"node_modules", "dist"}
    )
    assert stray == [], f"stray entries at the repo root: {stray}"


def test_workspaces_point_at_the_new_folders():
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert pkg["workspaces"] == ["frontend/*", "shared/contracts"]


def test_firebase_json_points_at_shared_infra():
    fb = json.loads((ROOT / "firebase.json").read_text(encoding="utf-8"))
    assert fb["firestore"] == [
        {"database": "aira", "rules": "shared/infra/firestore.rules", "indexes": "shared/infra/firestore.indexes.json"}
    ]


def test_catalog_is_sunrise_dairy():
    cat = json.loads((ROOT / "shared/catalog/sunrise_dairy.json").read_text(encoding="utf-8"))
    assert cat["shop"]["id"] == "sunrise_dairy"
    assert cat["shop"]["name"] == "Sunrise Dairy"


def test_no_old_persona_anywhere():
    hits = [f"{rel}:{i}" for rel, i, line in _lines() if OLD_PERSONA in line.lower()]
    assert hits == [], f"the old persona still appears in: {hits[:20]}"


def test_no_stale_old_paths():
    hits = [f"{rel}:{i}: {line.strip()[:100]}" for rel, i, line in _lines() if OLD_PATHS.search(line)]
    assert hits == [], "stale pre-restructure paths:\n" + "\n".join(hits[:40])
