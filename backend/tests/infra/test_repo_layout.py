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
        if (p / ".github").is_dir() and (p / "frontend").is_dir() and (p / "shared").is_dir():
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
    # moved out of the repo root (owner request 2026-10-11: the root holds folders only, plus README and .gitignore)
    "frontend/package.json",
    "frontend/package-lock.json",
    "shared/contracts/package-lock.json",
    "shared/infra/firebase.json",
    "shared/infra/.firebaserc",
    "shared/.env.example",
    "shared/docs/team-rules.md",
]
# The exact repo root. Git-ignored local entries (node_modules/, dist/, .venv, .env, caches) are allowed too.
ROOT_ALLOWED = {".github", "README.md", ".gitignore", "frontend", "backend", "shared"}
ROOT_MOVED = {
    "CLAUDE.md", "package.json", "package-lock.json", "firebase.json", ".firebaserc", ".env.example",
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


def _git_ignored(names: list[str]) -> set[str]:
    if not names:
        return set()
    proc = subprocess.run(["git", "check-ignore", "--no-index", "--", *names], cwd=ROOT, capture_output=True, text=True)
    return set(proc.stdout.split())


def test_root_has_only_the_allowed_entries():
    """ls -A at the root shows frontend/, backend/, shared/, .github/, README.md and .gitignore, plus git-ignored
    local entries only (node_modules/, dist/, .venv, .env, caches)."""
    names = sorted(e.name for e in ROOT.iterdir() if e.name != ".git")
    ignored = _git_ignored([n for n in names if n not in ROOT_ALLOWED])
    stray = [n for n in names if n not in ROOT_ALLOWED and n not in ignored]
    assert stray == [], f"stray entries at the repo root: {stray}"
    assert ROOT_ALLOWED <= set(names)


def test_tracked_root_entries_are_exactly_the_allowed_ones():
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, check=True).stdout.decode()
    top = {rel.split("/", 1)[0] for rel in out.split("\0") if rel}
    assert top == ROOT_ALLOWED, top


@pytest.mark.parametrize("name", sorted(ROOT_MOVED))
def test_moved_root_file_is_gone(name):
    assert not (ROOT / name).exists(), f"{name} moved out of the repo root"


def test_frontend_is_the_npm_workspace_root():
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["workspaces"] == ["app", "dashboard"]
    for app in ("app", "dashboard"):
        deps = json.loads((ROOT / "frontend" / app / "package.json").read_text(encoding="utf-8"))["dependencies"]
        assert deps["@aira/contracts"] == "file:../../shared/contracts", app


def test_firebase_json_lives_in_shared_infra_and_uses_relative_paths():
    fb = json.loads((ROOT / "shared" / "infra" / "firebase.json").read_text(encoding="utf-8"))
    assert fb["firestore"] == [{"database": "aira", "rules": "firestore.rules", "indexes": "firestore.indexes.json"}]
    assert fb["hosting"]["public"] == "../../dist/hosting"
    rc = json.loads((ROOT / "shared" / "infra" / ".firebaserc").read_text(encoding="utf-8"))
    assert rc["projects"]["default"] == "demo-aira"


def test_team_rules_doc_replaces_the_root_instructions_file():
    text = (ROOT / "shared" / "docs" / "team-rules.md").read_text(encoding="utf-8")
    assert text.startswith("# AIRA team rules\n")
    assert "claude" not in text.lower()
    assert "The model proposes, the backend commits." in text


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
