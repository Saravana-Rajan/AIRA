"""Shared fixtures for the live-service tests. Everything is faked; no test touches the network."""
import sys
from pathlib import Path

import pytest

# tests/ has no __init__.py and pytest runs with --import-mode=importlib, so this folder is not on sys.path.
# Append it (installed packages keep priority) so conftests and tests can `from live_fakes import ...`.
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.append(_TESTS_DIR)

from live_fakes import FakeFirestore, Recorder, wait_until  # noqa: E402

__all__ = ["wait_until"]


@pytest.fixture
def fake_db(monkeypatch):
    """A FakeFirestore behind ``aira_live.db.db()``.

    Modules that import ``db`` lazily (inside a function) see the fake through ``aira_live.db``. Modules that
    already bound ``db`` by name at import time are patched too.
    """
    from aira_live import db as db_mod

    fake = FakeFirestore()
    real = db_mod.db
    monkeypatch.setattr(db_mod, "db", lambda: fake)
    for name, mod in list(sys.modules.items()):
        if name.startswith("aira_live.") and mod is not db_mod and getattr(mod, "db", None) is real:
            monkeypatch.setattr(mod, "db", lambda: fake)
    return fake


@pytest.fixture
def mk_session():
    """Factory for a Session whose ``send`` is a Recorder: ``s, rec = mk_session()``."""
    from aira_live.session import Session

    def _make(shop: str = "murugan_dairy", lang: str = "en", verbosity: str = "short"):
        rec = Recorder()
        return Session("u1", shop, lang, verbosity, rec), rec

    return _make
