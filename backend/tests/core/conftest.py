"""Fixtures for the /monitor tests: a fake Firebase token verifier (no network), a viewer allowlist and a
helper that opens an authenticated monitor socket (the fakes live in monitor_fakes.py so tests can import them)."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from aira_live import monitor
from core.monitor_fakes import ORIGIN, SHOP, TOKEN_VIEWER, VIEWER_EMAIL, FakeVerifier


@pytest.fixture
def fake_viewers(monkeypatch) -> FakeVerifier:
    """The live feed's auth, faked: the verifier table above and an allowlist with the viewer's email and a uid.
    Also sets AIRA_MONITOR_AUTH=1, so every test using it runs the sign-in gate (the default is no auth at all,
    tested in test_monitor_open.py)."""
    monkeypatch.setenv("AIRA_MONITOR_AUTH", "1")
    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.delenv("AIRA_FIREBASE_PROJECT", raising=False)
    monkeypatch.setenv("AIRA_MONITOR_VIEWERS", f"{VIEWER_EMAIL},uid:uid-listed")
    fake = FakeVerifier()
    monkeypatch.setattr(monitor, "verify_user", fake)
    return fake


@pytest.fixture
def mon_open(fake_viewers):
    """``with mon_open(client) as mon:``: a /monitor socket from the hosting site that sends the viewer's token
    first (``token=`` / ``shop=`` / ``headers=`` / ``query=`` to vary it)."""
    @contextmanager
    def _open(client, shop: str = SHOP, headers: dict = ORIGIN, token: str | None = TOKEN_VIEWER,
              query: str | None = None):
        path = f"/monitor?{query}" if query is not None else f"/monitor?shop={shop}"
        cm = client.websocket_connect(path, headers=headers)
        ws = cm.__enter__()
        try:
            if token is not None:
                ws.send_json({"t": "auth", "idToken": token})
            yield ws
        finally:
            cm.__exit__(None, None, None)

    return _open
