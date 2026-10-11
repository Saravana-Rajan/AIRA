"""Firestore rules and indexes for database `aira` (shared/infra/firestore.rules, shared/infra/firestore.indexes.json).

The static checks always run. The emulator checks need Java and a running Firestore emulator; they are
skipped when Java is unavailable or FIRESTORE_EMULATOR_HOST is unset. Run them with:
  npx firebase-tools emulators:exec --only firestore --project demo-aira \
    "cd backend && uv run pytest -q tests/infra/test_firestore_rules.py"
"""
import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "shared" / "infra" / "firestore.rules"
INDEXES = ROOT / "shared" / "infra" / "firestore.indexes.json"
PROJECT = "demo-aira"
DATABASE = "aira"
WRITE_OPS = {"write", "create", "update", "delete"}
READ_OPS = {"read", "get", "list"}


def _allows() -> list[tuple[set[str], str]]:
    text = RULES.read_text(encoding="utf-8")
    found = re.findall(r"allow\s+([a-z,\s]+?)\s*:\s*if\s+([^;]+);", text)
    return [({op.strip() for op in ops.split(",")}, cond.strip()) for ops, cond in found]


# ---------- static checks (always run) ----------

def test_rules_use_version_2():
    assert "rules_version = '2';" in RULES.read_text(encoding="utf-8")


def test_every_client_write_is_denied():
    allows = _allows()
    assert any(ops & WRITE_OPS for ops, _ in allows)
    for ops, cond in allows:
        if ops & WRITE_OPS:
            assert cond == "false", (ops, cond)


DASHBOARD_COLLECTIONS = ["products", "inventory", "sales", "payments", "orders", "deliveries", "auditLogs"]
SERVER_ONLY = ["suppliers", "msgIndex", "contactIndex", "proposals", "ledger", "prefs", "memoryEvents", "events", "memory",
               "locations"]


def test_only_the_demo_shop_is_readable():
    readable = [" ".join(cond.split()) for ops, cond in _allows() if ops & READ_OPS and cond != "false"]
    allowlist = "', '".join(DASHBOARD_COLLECTIONS)
    assert readable == [
        "shopId == 'sunrise_dairy'",  # the shop doc
        f"shopId == 'sunrise_dairy' && coll in ['{allowlist}']",  # only what the dashboard draws
    ]


def test_server_only_collections_are_never_listed_as_readable():
    """suppliers hold vendor phones and Telegram chat ids; proposals hold pending payloads and session ids."""
    text = RULES.read_text(encoding="utf-8")
    conditions = [cond for ops, cond in _allows() if ops & READ_OPS and cond != "false"]
    for coll in SERVER_ONLY:
        assert not any(f"'{coll}'" in cond for cond in conditions), coll
    assert "{document=**}" not in text.split("match /shops/")[1].split("match /{document=**}")[0], \
        "no wildcard read under the shop"


def test_dashboard_reads_only_allowlisted_collections():
    """frontend/dashboard/src/lib/live.ts must not subscribe to a collection the rules close."""
    live = (ROOT / "frontend" / "dashboard" / "src" / "lib" / "live.ts").read_text(encoding="utf-8")
    body = live.split("function shopSources")[1].split("type Docs")[0]
    used = set(re.findall(r'(?:all|since)\("([A-Za-z]+)"', body)) | set(re.findall(r'col\("([A-Za-z]+)"\)', body))
    assert used <= set(DASHBOARD_COLLECTIONS), used - set(DASHBOARD_COLLECTIONS)


def test_indexes_file_is_well_formed():
    data = json.loads(INDEXES.read_text(encoding="utf-8"))
    assert set(data) == {"indexes", "fieldOverrides"}
    assert data["indexes"]
    for ix in data["indexes"]:
        assert ix["collectionGroup"] and ix["queryScope"] == "COLLECTION"
        assert len(ix["fields"]) >= 2
        for f in ix["fields"]:
            assert f["fieldPath"] and f["order"] in {"ASCENDING", "DESCENDING"}
    assert {"ledger", "sales", "orders"} <= {ix["collectionGroup"] for ix in data["indexes"]}


# ---------- emulator checks (need Java + FIRESTORE_EMULATOR_HOST) ----------

EMU = os.getenv("FIRESTORE_EMULATOR_HOST", "")


def _java_available() -> bool:
    if shutil.which("java") is None:
        return False
    try:
        return subprocess.run(["java", "-version"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


emulator = pytest.mark.skipif(
    not (EMU and _java_available()),
    reason="Firestore emulator tests need Java and FIRESTORE_EMULATOR_HOST (Java is not installed here)",
)

DOCS = f"/v1/projects/{PROJECT}/databases/{DATABASE}/documents"
ON_HAND_99 = {"fields": {"onHand": {"integerValue": "99"}}}


def _call(method: str, path: str, body: dict | None = None, token: str | None = None) -> int:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{EMU}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def _user_token(uid: str) -> str:
    """Unsigned ID token, accepted only by the emulator (a signed-in client, if Auth is ever added)."""
    now = int(time.time())
    header = {"alg": "none", "kid": "fakekid", "typ": "JWT"}
    payload = {"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "iat": now, "exp": now + 3600,
               "auth_time": now, "sub": uid, "user_id": uid,
               "firebase": {"sign_in_provider": "anonymous", "identities": {}}}

    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{enc(header)}.{enc(payload)}."


@pytest.fixture(scope="module")
def emu():
    rules = RULES.read_text(encoding="utf-8")
    assert _call("PUT", f"/emulator/v1/projects/{PROJECT}:securityRules",
                 {"rules": {"files": [{"content": rules}]}}) == 200
    _call("DELETE", f"/emulator/v1/projects/{PROJECT}/databases/{DATABASE}/documents")
    for path in ("shops/sunrise_dairy", "shops/sunrise_dairy/inventory/sakthi_curd_1l",
                 "shops/other_shop", "shops/other_shop/inventory/x", "idempotency/k1"):
        # "Bearer owner" is the emulator's admin bypass (the role aira-live's service account plays in prod).
        assert _call("PATCH", f"{DOCS}/{path}", {"fields": {"v": {"integerValue": "1"}}}, token="owner") == 200
    yield


@emulator
@pytest.mark.parametrize("token", [None, _user_token("judge")])
def test_anyone_can_read_the_demo_shop(emu, token):
    assert _call("GET", f"{DOCS}/shops/sunrise_dairy", token=token) == 200
    assert _call("GET", f"{DOCS}/shops/sunrise_dairy/inventory/sakthi_curd_1l", token=token) == 200
    assert _call("GET", f"{DOCS}/shops/sunrise_dairy/inventory", token=token) == 200


@emulator
@pytest.mark.parametrize("token", [None, _user_token("judge")])
def test_no_client_can_write_the_demo_shop(emu, token):
    assert _call("PATCH", f"{DOCS}/shops/sunrise_dairy/inventory/sakthi_curd_1l", ON_HAND_99, token) == 403
    assert _call("DELETE", f"{DOCS}/shops/sunrise_dairy/inventory/sakthi_curd_1l", token=token) == 403
    assert _call("POST", f"{DOCS}/shops/sunrise_dairy/ledger", ON_HAND_99, token) == 403
    assert _call("PATCH", f"{DOCS}/shops/sunrise_dairy", ON_HAND_99, token) == 403


@emulator
@pytest.mark.parametrize("token", [None, _user_token("judge")])
def test_other_shops_and_server_collections_are_closed(emu, token):
    for path in ("shops/other_shop", "shops/other_shop/inventory/x", "idempotency/k1"):
        assert _call("GET", f"{DOCS}/{path}", token=token) == 403
        assert _call("PATCH", f"{DOCS}/{path}", ON_HAND_99, token) == 403
