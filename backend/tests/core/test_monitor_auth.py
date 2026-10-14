"""The live feed's access check: a signed-in Google account the owner listed (shared/docs/ops/security.md §2a).

Two layers, both without network: ``auth.verify_firebase_user`` with an injected decoder (what google-auth
would return after checking the signature, audience and expiry) for the claim checks, and the /monitor route
with a fake verifier for the handshake, the allowlist, the expiry watchdog, refreshes and reconnects.
"""
from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aira_live import auth, config, monitor, rtc
from aira_live import main as main_mod
from aira_live.auth import FirebaseUser
from core.monitor_fakes import (
    ORIGIN, SHOP, TOKEN_BAD, TOKEN_STRANGER, TOKEN_UNVERIFIED_EMAIL, TOKEN_VIEWER, TOKEN_VIEWER_UID, VIEWER_EMAIL,
    VIEWER_UID, viewer,
)

HELLO = {"t": "hello", "uid": "u1", "lang": "en", "verbosity": "short", "appVersion": "0.1.0"}
PROJECT = "test-project"
NOW = 1_760_000_000


def _wait(pred, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def _recv_until(ws, kind: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.receive_json()
        if msg["t"] == "mon" and msg["kind"] == kind:
            return msg
    raise AssertionError(f"no {kind!r} monitor message")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monitor.reset()
    yield
    monitor.reset()


# ---------- the verifier: claims google-auth hands back, checked the rest of the way ----------
def claims(**over) -> dict:
    base = {"iss": auth.FIREBASE_ISSUER + PROJECT, "aud": PROJECT, "sub": "uid-1", "user_id": "uid-1",
            "iat": NOW - 60, "exp": NOW + 3000, "auth_time": NOW - 120, "email": "Viewer@Example.com",
            "email_verified": True, "firebase": {"sign_in_provider": "google.com"}}
    base.update(over)
    return base


def decoder(result, seen: list | None = None):
    def decode(token: str, project: str):
        if seen is not None:
            seen.append((token, project))
        if isinstance(result, Exception):
            raise result
        return result
    return decode


def verify(result, token: str = "h.p.s", **kw) -> FirebaseUser | None:
    return auth.verify_firebase_user(token, project_id=PROJECT, decode=decoder(result), now=lambda: NOW, **kw)


def test_a_good_token_yields_the_viewer_with_a_lowercased_email_and_its_expiry():
    seen: list = []
    user = auth.verify_firebase_user("h.p.s", project_id=PROJECT, decode=decoder(claims(), seen), now=lambda: NOW)
    assert user == FirebaseUser(uid="uid-1", email="viewer@example.com", email_verified=True, exp=NOW + 3000,
                                auth_time=NOW - 120)
    assert seen == [("h.p.s", PROJECT)]  # the decoder is told which project the audience must be


@pytest.mark.parametrize("token", [None, "", "no-dots", "one.dot", "a.b.c.d", "h." + "x" * 5000 + ".s", 5])
def test_malformed_tokens_are_refused_before_any_decoding(token):
    seen: list = []
    assert auth.verify_firebase_user(token, project_id=PROJECT, decode=decoder(claims(), seen)) is None
    assert seen == []


def test_a_token_the_library_rejects_is_refused_whatever_the_error():
    """Bad signature, expired, wrong audience, unreachable certs: google-auth raises, the viewer is None."""
    for exc in (ValueError("Token expired"), ValueError("Token has wrong audience"), RuntimeError("certs"),
                Exception("Could not verify token signature")):
        assert verify(exc) is None


def test_the_issuer_must_be_securetoken_for_this_project():
    assert verify(claims(iss="https://securetoken.google.com/other-project")) is None
    assert verify(claims(iss="https://accounts.google.com")) is None
    assert verify(claims(iss=None)) is None
    assert verify(claims()) is not None


def test_the_audience_is_checked_again_here():
    """Wrong audience: the library would already refuse it; a decoder that let it through still does not pass."""
    assert verify(claims(aud="other-project")) is None
    assert verify(claims(aud=[PROJECT])) is None


def test_the_subject_must_be_a_non_empty_string_matching_user_id():
    assert verify(claims(sub="")) is None
    assert verify(claims(sub="  ")) is None
    assert verify(claims(sub=None)) is None
    assert verify(claims(sub=42, user_id=42)) is None
    assert verify(claims(sub="uid-1", user_id="uid-2")) is None
    assert verify({k: v for k, v in claims().items() if k != "user_id"}) is not None  # user_id is optional


def test_expiry_and_auth_time_are_checked_with_a_small_skew():
    assert verify(claims(exp=NOW - 60)) is None  # expired (the library checks this too)
    assert verify(claims(exp=NOW - 5)) is not None  # within CLOCK_SKEW_S
    assert verify(claims(exp="soon")) is None and verify(claims(exp=True)) is None
    assert verify(claims(auth_time=NOW + 600)) is None  # signed in in the future: refused
    assert verify(claims(auth_time=NOW + 5)) is not None  # skew
    assert verify(claims(auth_time=None)) is None


def test_no_project_id_means_nobody_verifies(monkeypatch):
    """Fail closed: without AIRA_FIREBASE_PROJECT / GCP_PROJECT no token is even decoded."""
    monkeypatch.delenv("AIRA_FIREBASE_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    seen: list = []
    assert auth.verify_firebase_user("h.p.s", decode=decoder(claims(), seen)) is None
    assert seen == []
    monkeypatch.setenv("GCP_PROJECT", PROJECT)
    assert auth.verify_firebase_user("h.p.s", decode=decoder(claims(), seen), now=lambda: NOW) is not None
    monkeypatch.setenv("AIRA_FIREBASE_PROJECT", "firebase-side")  # wins over GCP_PROJECT
    assert config.load().firebase_project_id == "firebase-side"
    assert auth.verify_firebase_user("h.p.s", decode=decoder(claims(), seen), now=lambda: NOW) is None  # aud mismatch


def test_the_default_decoder_is_google_auths_firebase_verifier_with_the_cached_certs(monkeypatch):
    calls = []
    fake = SimpleNamespace(verify_firebase_token=lambda token, request, audience, clock_skew_in_seconds: calls.append(
        (token, request, audience, clock_skew_in_seconds)) or claims())
    monkeypatch.setitem(__import__("sys").modules, "google.oauth2.id_token", fake)
    user = auth.verify_firebase_user("h.p.s", project_id=PROJECT, now=lambda: NOW)
    assert user is not None and user.uid == "uid-1"
    assert calls == [("h.p.s", auth.certs_request, PROJECT, auth.CLOCK_SKEW_S)]


def test_verify_never_logs_the_token_or_the_email(caplog):
    caplog.set_level(logging.DEBUG, logger="aira_live.auth")
    token = "h.secret-payload.s"
    assert verify(claims(), token=token) is not None
    assert verify(ValueError("bad"), token=token) is None
    text = caplog.text
    assert "secret-payload" not in text and "example.com" not in text and "uid-1" not in text
    assert auth.uid_hash("uid-1") in text and "refused" in text


# ---------- the certificate cache ----------
class _Resp:
    def __init__(self, status=200, headers=None, data=b"{}"):
        self.status, self.headers, self.data = status, headers or {}, data


def test_certs_are_fetched_once_per_cache_control_max_age():
    now = [1000.0]
    fetched = []

    def inner(url, method="GET", **kw):
        fetched.append((url, method))
        return _Resp(headers={"Cache-Control": "public, max-age=600, must-revalidate"})

    req = auth._CachedCerts(inner, now=lambda: now[0])
    a = req("https://certs", method="GET")
    b = req("https://certs")
    assert a is b and len(fetched) == 1
    now[0] += 599
    assert req("https://certs") is a and len(fetched) == 1
    now[0] += 2
    assert req("https://certs") is not a and len(fetched) == 2
    req("https://other")  # another URL has its own entry
    assert len(fetched) == 3
    req("https://certs", method="POST")  # never cached
    assert len(fetched) == 4
    req.clear()
    req("https://certs")
    assert len(fetched) == 5


def test_certs_cache_honours_defaults_and_bounds_and_skips_errors():
    assert auth._CachedCerts.ttl_of({}) == auth.CERTS_DEFAULT_TTL_S
    assert auth._CachedCerts.ttl_of({"cache-control": "max-age=0"}) == 0
    assert auth._CachedCerts.ttl_of({"Cache-Control": "max-age=99999999"}) == auth.CERTS_MAX_TTL_S
    assert auth._CachedCerts.ttl_of({"Cache-Control": "no-store"}) == auth.CERTS_DEFAULT_TTL_S
    fetched = []
    req = auth._CachedCerts(lambda url, method="GET", **kw: fetched.append(url) or _Resp(status=503), now=lambda: 0.0)
    req("https://certs")
    req("https://certs")
    assert len(fetched) == 2  # a failed fetch is not cached


# ---------- the allowlist ----------
def test_viewers_env_parses_emails_lowercased_and_uids_as_they_are(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_VIEWERS", " Owner@Example.com , uid:AbC123 ,, uid: ,viewer@example.com, owner@example.com ")
    assert config.load().monitor_viewers == ("owner@example.com", "uid:AbC123", "viewer@example.com")
    monkeypatch.setenv("AIRA_MONITOR_VIEWERS", " , ")
    assert config.load().monitor_viewers == ()
    monkeypatch.delenv("AIRA_MONITOR_VIEWERS")
    assert config.load().monitor_viewers == ()


def test_allowed_by_verified_email_or_uid_and_nobody_when_the_list_is_empty(monkeypatch):
    listed = ("owner@example.com", "uid:u-9")
    assert auth.monitor_viewer_allowed(viewer(email="owner@example.com"), listed)
    assert auth.monitor_viewer_allowed(viewer(uid="u-9", email=None), listed)
    assert auth.monitor_viewer_allowed(viewer(uid="u-9", email="other@example.com", verified=False), listed)
    assert not auth.monitor_viewer_allowed(viewer(email="owner@example.com", verified=False), listed)  # unverified email
    assert not auth.monitor_viewer_allowed(viewer(email="stranger@example.com"), listed)
    assert not auth.monitor_viewer_allowed(viewer(uid="u-8", email=None), listed)
    assert not auth.monitor_viewer_allowed(None, listed)
    assert not auth.monitor_viewer_allowed(viewer(email="owner@example.com"), ())
    monkeypatch.delenv("AIRA_MONITOR_VIEWERS", raising=False)
    assert not auth.monitor_viewer_allowed(viewer(email="owner@example.com"))  # the env: unset = nobody
    monkeypatch.setenv("AIRA_MONITOR_VIEWERS", "OWNER@example.com")
    assert auth.monitor_viewer_allowed(viewer(email="owner@example.com"))


# ---------- the route: the handshake ----------
def _refused(client, token, headers=ORIGIN, shop=SHOP, send_first=None) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/monitor?shop={shop}", headers=headers) as mon:
            if send_first is not None:
                mon.send_text(send_first)
            elif token is not None:
                mon.send_json({"t": "auth", "idToken": token})
            mon.receive_json()
    return closed.value.code


def test_no_auth_message_within_the_deadline_is_4401(monkeypatch, fake_viewers):
    monkeypatch.setattr(monitor, "AUTH_TIMEOUT_S", 0.2)
    with TestClient(main_mod.app) as client:
        assert _refused(client, None) == 4401
    assert not monitor.monitored(SHOP) and fake_viewers.calls == []


@pytest.mark.parametrize("first", ["{not json", json.dumps({"t": "rtc", "kind": "want", "to": "s1"}),
                                   json.dumps({"t": "auth", "key": "k"}), json.dumps({"t": "auth", "idToken": ""}),
                                   json.dumps({"t": "hello", "idToken": "a.b.c"}), "AAAA"])
def test_a_first_message_that_is_not_an_auth_message_is_4401(first, fake_viewers):
    with TestClient(main_mod.app) as client:
        assert _refused(client, None, send_first=first) == 4401
    assert not monitor.monitored(SHOP) and fake_viewers.calls == []


@pytest.mark.parametrize("token", [TOKEN_BAD, "hdr.expired.sig", "hdr.wrong-audience.sig", "hdr.bad-signature.sig"])
def test_a_token_that_does_not_verify_is_4401(token, fake_viewers):
    """Invalid, expired, wrong audience, bad signature: the verifier says None, the socket closes with 4401."""
    with TestClient(main_mod.app) as client:
        assert _refused(client, token) == 4401
    assert fake_viewers.calls == [token] and not monitor.monitored(SHOP)


@pytest.mark.parametrize("token", [TOKEN_STRANGER, TOKEN_UNVERIFIED_EMAIL])
def test_a_signed_in_account_that_is_not_listed_is_4403(token, fake_viewers):
    with TestClient(main_mod.app) as client:
        assert _refused(client, token) == 4403
    assert fake_viewers.calls == [token] and not monitor.monitored(SHOP)


def test_empty_viewers_refuses_everyone(monkeypatch, fake_viewers):
    for value in (None, "", " , "):
        if value is None:
            monkeypatch.delenv("AIRA_MONITOR_VIEWERS", raising=False)
        else:
            monkeypatch.setenv("AIRA_MONITOR_VIEWERS", value)
        with TestClient(main_mod.app) as client:
            assert _refused(client, TOKEN_VIEWER) == 4403
            assert _refused(client, TOKEN_VIEWER_UID) == 4403
    assert not monitor.monitored(SHOP)


def test_a_listed_uid_entry_admits_an_account_without_an_email(mon_open):
    with mon_open(TestClient(main_mod.app), token=TOKEN_VIEWER_UID):
        _wait(lambda: monitor.monitored(SHOP))
        sub = monitor._HUB[SHOP][0]
        assert sub.viewer is not None and sub.viewer.uid == "uid-listed" and sub.viewer.email is None


def test_an_allowed_origin_without_a_token_admits_nobody(monkeypatch, fake_viewers):
    """Origin never bypasses auth: the hosting site's own Origin, forged or real, still needs a token."""
    monkeypatch.setattr(monitor, "AUTH_TIMEOUT_S", 0.2)
    with TestClient(main_mod.app) as client:
        for headers in (ORIGIN, {"Origin": "https://aira-shop.firebaseapp.com"}):
            assert _refused(client, None, headers=headers) == 4401
    assert not monitor.monitored(SHOP)


def test_a_disallowed_origin_is_refused_even_with_a_valid_token(fake_viewers):
    with TestClient(main_mod.app) as client:
        for headers in ({"Origin": "https://evil.example"}, {}):
            assert _refused(client, TOKEN_VIEWER, headers=headers) == 4403
    assert fake_viewers.calls == [] and not monitor.monitored(SHOP)


def test_nothing_is_subscribed_sent_or_relayed_before_auth(fake_viewers):
    """A live session, a monitor that has not authenticated yet: no session list, no frames, no preview request."""
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            time.sleep(0.1)
            assert not monitor.monitored(SHOP)
            assert monitor.publish(SHOP, "status", sid, state="idle") == 0  # nobody to send to
            ws.send_json({"t": "stop"})
            assert ws.receive_json() == {"t": "cue", "cue": "done"}  # the phone was not asked for its preview
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
            start = _recv_until(mon, "session")  # the first thing after auth: the live session
            assert start["session"] == sid and start["state"] == "start"
            assert ws.receive_json() == {"t": "request_frame", "purpose": "preview", "continuous": True}
            assert monitor.monitors(SHOP) == 1
            assert monitor._HUB[SHOP][0].viewer.uid == VIEWER_UID


def test_every_reconnect_authenticates_from_scratch(fake_viewers):
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
            _wait(lambda: monitor.monitored(SHOP))
        _wait(lambda: not monitor.monitored(SHOP))
        assert _refused(client, None, send_first=json.dumps({"t": "rtc", "kind": "want", "to": "s1"})) == 4401
        assert _refused(client, TOKEN_STRANGER) == 4403
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
            _wait(lambda: monitor.monitored(SHOP))
    assert fake_viewers.calls == [TOKEN_VIEWER, TOKEN_STRANGER, TOKEN_VIEWER]


# ---------- the route: expiry and refresh ----------
def test_a_token_that_expires_without_a_refresh_closes_the_socket_with_4401(monkeypatch, fake_viewers):
    monkeypatch.setattr(monitor, "EXPIRY_GRACE_S", 0.0)
    fake_viewers.tokens["hdr.short.sig"] = viewer(ttl_s=2.0)
    with TestClient(main_mod.app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
                mon.send_json({"t": "auth", "idToken": "hdr.short.sig"})
                _wait(lambda: monitor.monitored(SHOP))
                assert monitor.publish(SHOP, "status", "x", state="idle") == 1
                assert _recv_until(mon, "status")["state"] == "idle"
                mon.receive_json()  # nothing more comes: the next thing is the close
        assert closed.value.code == 4401
    _wait(lambda: not monitor.monitored(SHOP))


def test_a_refresh_for_the_same_account_extends_the_deadline(monkeypatch, fake_viewers):
    monkeypatch.setattr(monitor, "EXPIRY_GRACE_S", 0.0)
    fake_viewers.tokens["hdr.short.sig"] = viewer(ttl_s=2.0)
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json({"t": "auth", "idToken": "hdr.short.sig"})
            _wait(lambda: monitor.monitored(SHOP))
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})  # the same uid, an hour of life
            time.sleep(2.8)  # well past the first token's exp
            assert monitor.monitored(SHOP)
            assert monitor.publish(SHOP, "status", "x", state="idle") == 1
            assert _recv_until(mon, "status")["state"] == "idle"
    assert fake_viewers.calls == ["hdr.short.sig", TOKEN_VIEWER]


@pytest.mark.parametrize("token, code", [(TOKEN_STRANGER, 4401), (TOKEN_BAD, 4401), (TOKEN_UNVERIFIED_EMAIL, 4401)])
def test_a_refresh_for_another_account_or_a_bad_token_closes_the_socket(token, code, fake_viewers):
    with TestClient(main_mod.app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
                mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
                _wait(lambda: monitor.monitored(SHOP))
                mon.send_json({"t": "auth", "idToken": token})
                mon.receive_json()
        assert closed.value.code == code
    _wait(lambda: not monitor.monitored(SHOP))


def test_a_refresh_for_an_account_that_was_delisted_closes_with_4403(monkeypatch, fake_viewers):
    with TestClient(main_mod.app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
                mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
                _wait(lambda: monitor.monitored(SHOP))
                monkeypatch.setenv("AIRA_MONITOR_VIEWERS", "someone-else@example.com")
                mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
                mon.receive_json()
        assert closed.value.code == 4403


# ---------- the route: signalling is behind the same check ----------
def test_rtc_signalling_reaches_a_phone_only_from_an_authenticated_monitor(fake_viewers):
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        assert _refused(client, None, send_first=json.dumps({"t": "rtc", "kind": "want", "to": sid})) == 4401
        assert _refused(client, TOKEN_STRANGER) == 4403
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}  # no want reached the phone
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
            _recv_until(mon, "session")
            assert ws.receive_json()["t"] == "request_frame"
            mon.send_json({"t": "rtc", "kind": "want", "to": sid})
            want = ws.receive_json()
            assert want["t"] == "rtc" and want["kind"] == "want" and want["to"] == sid
            assert rtc.monitor_id(monitor._HUB[SHOP][0]) == want["from"]


async def test_the_relay_refuses_a_subscriber_it_never_registered(mk_session):
    """Defence in depth inside the relay: a Subscriber that did not come through subscribe() has no id."""
    from aira_live import sessions
    s, rec = mk_session()
    sessions.register(s)
    try:
        stray = monitor.Subscriber(SHOP)
        msg = __import__("aira_live.contracts", fromlist=["x"]).client_adapter.validate_python(
            {"t": "rtc", "kind": "want", "to": s.session_id})
        assert await rtc.from_monitor(stray, msg) is False
        assert rec.msgs == []
    finally:
        sessions.unregister(s)


# ---------- logs ----------
def test_logs_and_diag_carry_a_uid_hash_and_never_the_token_or_the_email(caplog, capsys, fake_viewers):
    caplog.set_level(logging.DEBUG)
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
            _wait(lambda: monitor.monitored(SHOP))
            mon.send_json({"t": "auth", "idToken": TOKEN_VIEWER})
        _wait(lambda: not monitor.monitored(SHOP))
        assert _refused(client, TOKEN_STRANGER) == 4403
        assert _refused(client, TOKEN_BAD) == 4401
    out = capsys.readouterr().out + caplog.text
    for secret in (TOKEN_VIEWER, TOKEN_STRANGER, TOKEN_BAD, VIEWER_EMAIL, "stranger@example.com", VIEWER_UID,
                   "uid-stranger"):
        assert secret not in out, secret
    assert auth.uid_hash(VIEWER_UID) in out and auth.uid_hash("uid-stranger") in out
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")] or []
    assert all("idToken" not in json.dumps(l) for l in lines)
