"""Fakes for the /monitor tests: a fake Firebase token verifier (no network), a viewer allowlist and a
helper that opens an authenticated monitor socket. The real verifier (``auth.verify_firebase_user``) is unit
tested in test_monitor_auth.py with an injected decoder."""
from __future__ import annotations

import time
from contextlib import contextmanager

from aira_live import monitor
from aira_live.auth import FirebaseUser

SHOP = "sunrise_dairy"
ORIGIN = {"Origin": "https://aira-shop.web.app"}
VIEWER_EMAIL = "viewer@example.com"
VIEWER_UID = "uid-viewer-1"
# Fake tokens: three dot-separated parts like a JWT, so the real verifier's shape check would pass them on.
TOKEN_VIEWER = "hdr.viewer-ok.sig"        # a listed account (by email)
TOKEN_VIEWER_UID = "hdr.viewer-uid.sig"   # a listed account (by uid:)
TOKEN_STRANGER = "hdr.stranger-ok.sig"    # a real, signed-in account that is not listed
TOKEN_BAD = "hdr.bad.sig"                 # does not verify (bad signature, wrong audience, expired, ...)
TOKEN_UNVERIFIED_EMAIL = "hdr.viewer-unverified.sig"  # the listed email, but the token says it is not verified


def viewer(uid: str = VIEWER_UID, email: str | None = VIEWER_EMAIL, *, verified: bool = True,
           ttl_s: float = 3600.0) -> FirebaseUser:
    now = int(time.time())
    return FirebaseUser(uid=uid, email=email, email_verified=verified, exp=int(now + ttl_s), auth_time=now)


class FakeVerifier:
    """``monitor.verify_user`` for tests: a token -> viewer table (None = refused) and a record of every call."""

    def __init__(self):
        self.tokens: dict[str, FirebaseUser | None] = {
            TOKEN_VIEWER: viewer(),
            TOKEN_VIEWER_UID: viewer(uid="uid-listed", email=None),
            TOKEN_STRANGER: viewer(uid="uid-stranger", email="stranger@example.com"),
            TOKEN_UNVERIFIED_EMAIL: viewer(uid="uid-unverified", verified=False),
            TOKEN_BAD: None,
        }
        self.calls: list[str] = []

    def __call__(self, id_token: str) -> FirebaseUser | None:
        self.calls.append(id_token)
        return self.tokens.get(id_token)


