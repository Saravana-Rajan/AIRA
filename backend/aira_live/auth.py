"""Who is on the phone (the ``hello`` check) and who is watching the live feed (``verify_firebase_user``).

The phone (``/ws``): there is one user and one shop. A ``hello`` is accepted only in mock mode or when
``AIRA_ALLOW_NO_TOKEN=1`` (set on the deployed service), and it always lands in the configured demo shop (env
``AIRA_DEMO_SHOP``, default ``sunrise_dairy``) as its owner. The ``shopId`` the client sends is ignored, so no phone
can reach another shop's data by naming it (newer clients send no ``shopId`` at all). The phone has no user
login: what keeps a stranger with the URL out is the shared demo key: when ``AIRA_APP_KEY`` is set (Secret Manager
``aira-app-key``), ``hello.idToken`` must equal it. The app builds the same value in as ``VITE_AIRA_APP_KEY``. It is
one key for the one demo phone, not user auth (shared/docs/ops/security.md). ``main.py`` adds the browser Origin
allowlist on ``/ws`` and the per-address cap.

The live feed (``/monitor``, the dashboard's Live page): a viewer must be a signed-in Google account that the owner
listed. The dashboard signs in with Firebase Authentication and sends its ID token; ``verify_firebase_user``
checks it server-side with the official Google library (``google.oauth2.id_token.verify_firebase_token``:
signature against Google's securetoken certificates, ``aud`` = the Firebase project, ``exp`` / ``iat``), plus the
checks that library leaves to the caller (``iss``, ``sub``, ``auth_time``), and ``monitor_viewer_allowed`` checks
the account against ``AIRA_MONITOR_VIEWERS``. Everything fails closed: no project id, no viewers, or Firebase Auth
not yet enabled on the project means nobody can watch. Revocation is not checked (that needs firebase-admin); a
token lives at most an hour, and the socket is closed at its ``exp`` unless refreshed.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from aira_live import config

log = logging.getLogger(__name__)

DEMO_SHOP = config.load().demo_shop_id  # env AIRA_DEMO_SHOP, default "sunrise_dairy"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthResult:
    uid: str
    shop_id: str
    role: Literal["owner", "family", "demo"]


def _app_key_ok(id_token: str | None, app_key: str) -> bool:
    """Constant-time compare of the hello token with the shared demo key."""
    return bool(id_token) and hmac.compare_digest(id_token.encode(), app_key.encode())


async def verify_hello(id_token: str | None, uid: str, shop_id: str | None = None, mock: bool = False) -> AuthResult:
    """Accept a ``hello`` into the demo shop, or raise ``AuthError`` (the socket then gets ``error/permission``).

    ``shop_id`` is what an older client put in ``hello.shopId``; it is never used to pick the shop."""
    settings = config.load()
    if not (mock or settings.allow_no_token):
        raise AuthError("hello refused: tokenless mode is off and there is no Firebase Auth to check a token")
    if not mock and settings.app_key and not _app_key_ok(id_token, settings.app_key):
        raise AuthError("hello refused: idToken does not match AIRA_APP_KEY")
    if shop_id and shop_id != settings.demo_shop_id:  # an old client naming another shop: served the one shop
        log.info("hello for shop %r served as demo shop %r", shop_id, settings.demo_shop_id)
    return AuthResult(uid=uid, shop_id=settings.demo_shop_id, role="owner")


# ---------- the live feed: Firebase Authentication ID tokens ----------
FIREBASE_ISSUER = "https://securetoken.google.com/"
MAX_ID_TOKEN_LEN = 4096      # a Firebase ID token is 1-2 KB; anything longer is not one
CLOCK_SKEW_S = 10            # tolerated drift on iat / exp / auth_time
CERTS_DEFAULT_TTL_S = 300.0  # when the certs response carries no Cache-Control max-age
CERTS_MAX_TTL_S = 24 * 3600.0


@dataclass(frozen=True)
class FirebaseUser:
    """A verified viewer: the token's subject, its (verified) email and when the token expires (epoch seconds)."""
    uid: str
    email: str | None
    email_verified: bool
    exp: int
    auth_time: int


class _CachedCerts:
    """A ``google.auth.transport.Request`` that caches GET responses (the securetoken certificates) in process,
    honouring the response's ``Cache-Control: max-age`` (``CERTS_DEFAULT_TTL_S`` without one), so a token check
    costs one HTTPS round trip every few hours, not one per socket. One ``requests.Session`` is reused."""

    def __init__(self, inner: Callable[..., Any] | None = None, now: Callable[[], float] = time.monotonic):
        self._inner = inner
        self._now = now
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}

    def _request(self) -> Callable[..., Any]:
        if self._inner is None:
            import requests as _requests  # lazy: keeps the import light for tests that never verify
            from google.auth.transport.requests import Request as _Request
            self._inner = _Request(session=_requests.Session())
        return self._inner

    @staticmethod
    def ttl_of(headers: Mapping[str, str] | None) -> float:
        cc = ""
        for k, v in (headers or {}).items():
            if str(k).lower() == "cache-control":
                cc = str(v)
                break
        m = re.search(r"max-age\s*=\s*(\d+)", cc)
        if not m:
            return CERTS_DEFAULT_TTL_S
        return max(0.0, min(CERTS_MAX_TTL_S, float(m.group(1))))

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        if method.upper() != "GET":
            return self._request()(url, method=method, **kwargs)
        with self._lock:
            hit = self._cache.get(url)
            if hit is not None and hit[0] > self._now():
                return hit[1]
            response = self._request()(url, method=method, **kwargs)
            if getattr(response, "status", None) == 200:
                self._cache[url] = (self._now() + self.ttl_of(getattr(response, "headers", None)), response)
            return response

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


certs_request = _CachedCerts()  # the process-wide certificate cache


def _default_decode(id_token: str, project_id: str) -> Mapping[str, Any]:
    """``google.oauth2.id_token.verify_firebase_token``: signature (securetoken certs), ``aud``, ``exp``, ``iat``."""
    from google.oauth2 import id_token as google_id_token  # lazy: never imported by a test that injects a decoder
    return google_id_token.verify_firebase_token(id_token, certs_request, audience=project_id,
                                                 clock_skew_in_seconds=CLOCK_SKEW_S)


def uid_hash(uid: str | None) -> str:
    """What the logs say instead of a uid or an email: the first 12 hex chars of SHA-256 of the uid."""
    return hashlib.sha256((uid or "").encode()).hexdigest()[:12] if uid else "-"


def verify_firebase_user(
    id_token: str | None,
    *,
    project_id: str | None = None,
    decode: Callable[[str, str], Mapping[str, Any]] | None = None,
    now: Callable[[], float] = time.time,
) -> FirebaseUser | None:
    """The viewer a Firebase ID token proves, or None when it proves nothing (never raises).

    ``decode`` (tests inject one; no network) must verify the signature, ``aud`` and ``exp`` / ``iat`` and return
    the claims; this adds ``iss`` (``https://securetoken.google.com/<project>``), a non-empty ``sub``, an
    ``auth_time`` that is not in the future, and ``aud`` once more. The outcome is logged with a uid hash only."""
    project = project_id if project_id is not None else config.load().firebase_project_id
    if not project:
        log.warning("monitor auth refused: no Firebase project id (AIRA_FIREBASE_PROJECT / GCP_PROJECT)")
        return None
    if not isinstance(id_token, str) or not id_token or len(id_token) > MAX_ID_TOKEN_LEN or id_token.count(".") != 2:
        log.info("monitor auth refused: malformed token")
        return None
    try:
        claims = (decode or _default_decode)(id_token, project)
    except Exception as exc:  # noqa: BLE001 - bad signature, expired, wrong audience, certs unreachable, ...
        log.info("monitor auth refused: %s", type(exc).__name__)
        return None
    if not isinstance(claims, Mapping):
        return None
    sub = claims.get("sub")
    exp = claims.get("exp")
    auth_time = claims.get("auth_time")
    if claims.get("iss") != FIREBASE_ISSUER + project:
        log.info("monitor auth refused: issuer")
        return None
    if claims.get("aud") != project:
        log.info("monitor auth refused: audience")
        return None
    if not isinstance(sub, str) or not sub.strip() or claims.get("user_id", sub) != sub:
        log.info("monitor auth refused: subject")
        return None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp + CLOCK_SKEW_S < now():
        log.info("monitor auth refused: expired")
        return None
    if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)) or auth_time - CLOCK_SKEW_S > now():
        log.info("monitor auth refused: auth_time")
        return None
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    user = FirebaseUser(uid=sub, email=email, email_verified=claims.get("email_verified") is True,
                        exp=int(exp), auth_time=int(auth_time))
    log.info("monitor auth ok uid=%s", uid_hash(user.uid))
    return user


def monitor_viewer_allowed(user: FirebaseUser | None, viewers: tuple[str, ...] | list[str] | None = None) -> bool:
    """True when ``user`` is on the viewer allowlist (``config.monitor_viewers`` unless ``viewers`` is given):
    by ``uid:<uid>``, or by email when the token says the email is verified. An empty list allows nobody."""
    if user is None:
        return False
    entries = config.load().monitor_viewers if viewers is None else tuple(viewers)
    if not entries:
        return False
    if "uid:" + user.uid in entries:
        return True
    return bool(user.email) and user.email_verified and user.email in entries
