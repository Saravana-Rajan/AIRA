"""Checks for the deploy scripts, firebase.json/.firebaserc and the GitHub workflows.

Nothing here talks to Google Cloud. Every script runs as a dry run against stub
`gcloud`, `npx`, `curl`, `npm` and `uv` binaries placed first on PATH, from a copy of
the script inside a temporary repo (so the real `.env` is never read), with an empty
gcloud config dir and bogus application-default credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
INFRA_SCRIPTS = ["infra/bootstrap.sh", "infra/deploy_live.sh", "infra/setup_github_deploy.sh"]
SCRIPTS = INFRA_SCRIPTS + ["scripts/build_hosting.sh"]
WORKFLOWS = [".github/workflows/ci.yml", ".github/workflows/deploy.yml"]
CONFIGS = ["firebase.json", ".firebaserc"]

PROJECT = "aira-test-proj"
PROJECT_NUMBER = "123456789012"
RUN_URL = "https://aira-live-abc123-el.a.run.app"
REQUIRED_DEPLOY_FLAGS = {
    "--source",
    "--region",
    "--project",
    "--service-account",
    "--min-instances",
    "--max-instances",
    "--timeout",
    "--session-affinity",
    "--set-secrets",
    "--set-env-vars",
}
CONDITION = "assertion.repository=='Saravana-Rajan/AIRA' && assertion.ref=='refs/heads/main'"

# --- stub binaries -----------------------------------------------------------------

_EXISTS_FN = r"""
exists() {  # STUB_EXISTS="ALL" or "pat1|pat2": which describe/get lookups succeed
  [ "${STUB_EXISTS:-}" = "ALL" ] && return 0
  local IFS='|' p
  for p in ${STUB_EXISTS:-}; do
    [ -n "$p" ] && case "$args" in *"$p"*) return 0 ;; esac
  done
  return 1
}
"""

GCLOUD_STUB = (
    r"""#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "$STUB_LOG"
args=" $* "
"""
    + _EXISTS_FN
    + r"""
case "$args" in
  *" projects describe "*) echo "$STUB_PROJECT_NUMBER"; exit 0 ;;
  *" run services describe "*"--format=json"*)
    printf '{"status":{"url":"%s"},"spec":{"template":{"metadata":{"annotations":{"autoscaling.knative.dev/maxScale":"%s"}}}}}\n' \
      "$STUB_RUN_URL" "${STUB_MAX_SCALE:-1}"
    exit 0 ;;
  *" run services describe "*) echo "$STUB_RUN_URL"; exit 0 ;;
  *" secrets versions access "*)
    if exists; then printf '%s' "${STUB_SECRET_VALUE:-}"; exit 0; fi
    exit 1 ;;
  *" describe "*) if exists; then exit 0; fi; exit 1 ;;
esac
cat > /dev/null 2>&1 < /dev/null
exit 0
"""
)

NPX_STUB = (
    r"""#!/usr/bin/env bash
printf 'npx %s\n' "$*" >> "$STUB_LOG"
args=" $* "
"""
    + _EXISTS_FN
    + r"""
case "$args" in
  *" hosting:sites:get "*) if exists; then exit 0; fi; exit 1 ;;
esac
exit 0
"""
)

NPM_STUB = r"""#!/usr/bin/env bash
printf 'npm %s\n' "$*" >> "$STUB_LOG"
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--outDir" ]; then out="$a"; fi
  case "$a" in --outDir=*) out="${a#--outDir=}" ;; esac
  prev="$a"
done
if [ -n "$out" ]; then
  mkdir -p "$out"
  printf '<!doctype html><title>stub</title>\n' > "$out/index.html"
fi
exit 0
"""

LOG_ONLY_STUB = r"""#!/usr/bin/env bash
printf '%s %s\n' "$(basename "$0")" "$*" >> "$STUB_LOG"
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$STUB_LOG"
echo '{"ok":true,"mock":false}'
"""

# Must never be called by these scripts: fail loudly if they are.
FORBIDDEN_STUB = r"""#!/usr/bin/env bash
printf 'FORBIDDEN %s %s\n' "$(basename "$0")" "$*" >> "$STUB_LOG"
exit 97
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _stub_bin(tmp_path: Path) -> Path:
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    _write_exe(bindir / "gcloud", GCLOUD_STUB)
    _write_exe(bindir / "npx", NPX_STUB)
    _write_exe(bindir / "npm", NPM_STUB)
    _write_exe(bindir / "uv", LOG_ONLY_STUB)
    _write_exe(bindir / "curl", CURL_STUB)
    for name in ("firebase", "gsutil", "bq", "gh"):
        _write_exe(bindir / name, FORBIDDEN_STUB)
    return bindir


def _fake_repo(tmp_path: Path, script: str) -> Path:
    root = tmp_path / "repo"
    (root / script).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / script, root / script)
    (root / "infra").mkdir(exist_ok=True)
    (root / "infra" / "firestore.rules").write_text("rules_version = '2';\n")
    (root / "infra" / "firestore.indexes.json").write_text('{"indexes": [], "fieldOverrides": []}\n')
    (root / "services" / "live").mkdir(parents=True, exist_ok=True)
    (root / "node_modules").mkdir(exist_ok=True)
    shutil.copy(REPO / "firebase.json", root / "firebase.json")
    return root


def run_script(tmp_path: Path, script: str, env: dict[str, str] | None = None, dotenv: str | None = None):
    root = _fake_repo(tmp_path, script)
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    log = tmp_path / "calls.log"
    log.write_text("")
    full_env = {
        "PATH": os.pathsep.join([str(_stub_bin(tmp_path)), str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "STUB_PROJECT_NUMBER": PROJECT_NUMBER,
        "STUB_RUN_URL": RUN_URL,
        "CLOUDSDK_CONFIG": str(tmp_path / "gcloud-config"),
        "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "no-credentials.json"),
    }
    full_env.update(env or {})
    proc = subprocess.run(
        ["bash", str(root / script)],
        cwd=root,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, log.read_text().splitlines(), root


def _calls(calls: list[str], *needles: str) -> list[str]:
    return [c for c in calls if all(n in c for n in needles)]


def _flags(command: str) -> set[str]:
    return set(re.findall(r"(?<!\S)(--[a-z][a-z-]*)", command))


def _deploy_live_command() -> str:
    text = (REPO / "infra/deploy_live.sh").read_text().replace("\\\n", " ")
    lines = [ln for ln in text.splitlines() if "gcloud run deploy" in ln]
    assert len(lines) == 1, lines
    return lines[0]


# --- static checks -----------------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_is_strict_bash_and_executable(script):
    path = REPO / script
    text = path.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert os.access(path, os.X_OK), f"{script} is not executable"


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_bash_syntax(script):
    proc = subprocess.run(["bash", "-n", str(REPO / script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize("script", SCRIPTS)
def test_script_shellcheck(script):
    proc = subprocess.run(["shellcheck", "-S", "warning", str(REPO / script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_project_id_is_never_committed():
    """The real project id (from env or .env) must not appear in any of these files."""
    values = {os.environ.get("GCP_PROJECT", "")}
    dotenv = REPO / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.startswith("GCP_PROJECT="):
                values.add(line.split("=", 1)[1].strip().strip("\"'"))
    values.discard("")
    for rel in SCRIPTS + WORKFLOWS + CONFIGS:
        text = (REPO / rel).read_text()
        for value in values:
            assert value not in text, f"project id leaked into {rel}"


def test_firebase_json_single_hosting_site_and_aira_database():
    cfg = json.loads((REPO / "firebase.json").read_text())
    hosting = cfg["hosting"]
    assert isinstance(hosting, dict), "exactly one hosting entry"
    assert hosting["site"] == "aira-shop"
    assert hosting["public"] == "dist/hosting"
    assert hosting["rewrites"] == [
        {"source": "/dashboard/**", "destination": "/dashboard/index.html"},
        {"source": "**", "destination": "/index.html"},
    ]
    firestore = cfg["firestore"]
    dbs = firestore if isinstance(firestore, list) else [firestore]
    assert dbs == [
        {"database": "aira", "rules": "infra/firestore.rules", "indexes": "infra/firestore.indexes.json"}
    ]
    assert "(default)" not in (REPO / "firebase.json").read_text()
    assert cfg["emulators"]["firestore"]["port"] == 8085


def test_firebaserc_is_a_placeholder():
    rc = json.loads((REPO / ".firebaserc").read_text())
    assert rc["projects"]["default"] == "demo-aira"  # emulator-only demo project; CI passes --project


# --- workflows -----------------------------------------------------------------------


def _load_workflow(rel: str) -> dict:
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((REPO / rel).read_text())
    assert isinstance(wf, dict) and wf.get("jobs"), rel
    wf["on"] = wf.pop(True, wf.get("on"))  # YAML 1.1 parses the bare key `on` as True
    return wf


def _runs(job: dict) -> list[str]:
    return [" ".join(s["run"].replace("\\\n", " ").split()) for s in job["steps"] if "run" in s]


def _test_job_ok(job: dict) -> None:
    runs = _runs(job)
    assert "npm ci && npm test" in runs
    assert "cd services/live && uv sync --frozen && uv run pytest -q" in runs


@pytest.mark.parametrize("rel", WORKFLOWS)
def test_workflow_parses(rel):
    _load_workflow(rel)


IMAGE = "asia-south1-docker.pkg.dev/${{ secrets.GCP_PROJECT }}/aira/aira-live:${{ github.sha }}"
# Overrides §3a: every action pinned to a full commit SHA (tag kept as a comment).
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "astral-sh/setup-uv": "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    "google-github-actions/auth": "c200f3691d83b41bf9bbd8638997a462592937ed",
    "google-github-actions/setup-gcloud": "e427ad8a34f8676edf47cf7d7925499adf3eb74f",
}


def _all_uses(wf: dict) -> list[str]:
    return [s["uses"] for job in wf["jobs"].values() for s in job["steps"] if "uses" in s]


@pytest.mark.parametrize("rel", WORKFLOWS)
def test_workflow_security(rel):
    wf = _load_workflow(rel)
    text = (REPO / rel).read_text()
    assert wf["permissions"] == {"contents": "read"}, "top level: contents read only"
    assert "pull_request_target" not in text
    assert "vars." not in text, "use secrets.*, never vars.*"
    for uses in _all_uses(wf):
        name, ref = uses.split("@")
        assert PINNED_ACTIONS.get(name) == ref, f"{uses} is not pinned to the agreed SHA"
    for job_name, job in wf["jobs"].items():
        if job_name != "deploy":
            assert "id-token" not in job.get("permissions", {}), job_name


def test_ci_runs_on_prs_and_non_main_pushes():
    wf = _load_workflow(".github/workflows/ci.yml")
    assert "pull_request" in wf["on"]
    assert wf["on"]["push"]["branches-ignore"] == ["main"]
    assert "secrets." not in (REPO / ".github/workflows/ci.yml").read_text()
    _test_job_ok(wf["jobs"]["test"])


def test_deploy_workflow_tests_then_deploys_with_wif():
    wf = _load_workflow(".github/workflows/deploy.yml")
    assert wf["on"] == {"push": {"branches": ["main"]}}
    assert wf["concurrency"] == {"group": "deploy-main", "cancel-in-progress": False}
    _test_job_ok(wf["jobs"]["test"])

    deploy = wf["jobs"]["deploy"]
    assert deploy["needs"] == "test"
    assert deploy["permissions"] == {"contents": "read", "id-token": "write"}
    assert deploy["if"] == "github.repository == 'Saravana-Rajan/AIRA' && github.ref == 'refs/heads/main'"
    uses = {s["uses"].split("@")[0]: s for s in deploy["steps"] if "uses" in s}
    auth = uses["google-github-actions/auth"]
    assert auth["with"] == {
        "workload_identity_provider": "${{ secrets.GCP_WIF_PROVIDER }}",
        "service_account": "${{ secrets.GCP_DEPLOY_SA }}",
        "create_credentials_file": True,
    }
    assert "google-github-actions/setup-gcloud" in uses
    text = (REPO / ".github/workflows/deploy.yml").read_text()
    assert "credentials_json" not in text  # keyless only

    runs = _runs(deploy)
    assert "gcloud auth configure-docker asia-south1-docker.pkg.dev --quiet" in runs
    assert f"docker build -t {IMAGE} services/live" in runs
    assert f"docker push {IMAGE}" in runs
    assert not any("--source" in r for r in runs), "build the image in the runner, not Cloud Build"
    [run_deploy] = [r for r in runs if r.startswith("gcloud run deploy aira-live")]
    for part in (
        f"--image {IMAGE}",
        "--region asia-south1",
        "--project ${{ secrets.GCP_PROJECT }}",
        "--service-account aira-live@${{ secrets.GCP_PROJECT }}.iam.gserviceaccount.com",
        "--min-instances 1",
        "--max-instances 1",
        "--timeout 3600",
        "--session-affinity",
        "--allow-unauthenticated",
        "--set-secrets GEMINI_API_KEY=aira-gemini-api-key:latest",
        "--set-env-vars FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=murugan_dairy,AIRA_ALLOW_NO_TOKEN=1,"
        "GCP_PROJECT=${{ secrets.GCP_PROJECT }}",
    ):
        assert part in run_deploy, part

    [build] = [s for s in deploy["steps"] if s.get("run", "").strip() == "bash scripts/build_hosting.sh"]
    assert build["env"]["VITE_AIRA_LIVE_WS"].startswith("${{ steps.")
    firebase_env = {k: v for k, v in build["env"].items() if k.startswith("VITE_FIREBASE_")}
    assert set(firebase_env) == {
        "VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID",
        "VITE_FIREBASE_STORAGE_BUCKET", "VITE_FIREBASE_MESSAGING_SENDER_ID", "VITE_FIREBASE_APP_ID",
    }
    assert all(v == f"${{{{ secrets.{k} }}}}" for k, v in firebase_env.items())
    assert any("gcloud run services describe aira-live" in r and "wss://" in r for r in runs)
    assert (
        "npx firebase-tools@latest deploy --only hosting:aira-shop --project ${{ secrets.GCP_PROJECT }} --non-interactive"
        in runs
    )
    order = [
        i for i, r in enumerate(runs)
        if r.startswith("docker build") or "gcloud run deploy" in r or "build_hosting" in r or "hosting:aira-shop" in r
    ]
    assert order == sorted(order) and len(order) == 4


def test_deploy_workflow_and_deploy_live_use_the_same_flags():
    wf = _load_workflow(".github/workflows/deploy.yml")
    [ci_cmd] = [r for r in _runs(wf["jobs"]["deploy"]) if r.startswith("gcloud run deploy aira-live")]
    script_flags = _flags(_deploy_live_command())
    assert REQUIRED_DEPLOY_FLAGS - {"--source"} | {"--image"} <= _flags(ci_cmd)
    # CI deploys the image it built in the runner; the hand-run deploy_live.sh deploys from source
    assert script_flags - {"--source"} == _flags(ci_cmd) - {"--image"}


# --- dry runs against stubs -------------------------------------------------------------


@pytest.mark.parametrize("script", INFRA_SCRIPTS)
def test_infra_script_refuses_to_run_without_gcp_project(tmp_path, script):
    proc, calls, _ = run_script(tmp_path, script)
    assert proc.returncode != 0
    assert "GCP_PROJECT" in proc.stderr
    assert calls == [], "no cloud CLI may run before GCP_PROJECT is known"


def test_bootstrap_fresh_project(tmp_path):
    secret = "test-gemini-key-not-real"
    proc, calls, _ = run_script(tmp_path, "infra/bootstrap.sh", {"GCP_PROJECT": PROJECT, "GEMINI_API_KEY": secret})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    joined = "\n".join(calls)
    assert "FORBIDDEN" not in joined

    [enable] = _calls(calls, "gcloud services enable")
    for api in (
        "run.googleapis.com",
        "cloudbuild.googleapis.com",
        "artifactregistry.googleapis.com",
        "secretmanager.googleapis.com",
        "firestore.googleapis.com",
        "firebase.googleapis.com",
        "firebasehosting.googleapis.com",
        "firebaserules.googleapis.com",
        "generativelanguage.googleapis.com",
    ):
        assert api in enable, api
    for banned in ("pubsub", "bigquery", "cloudscheduler", "firebaseremoteconfig", "fcm", "identitytoolkit", "compute"):
        assert banned not in enable, banned

    assert _calls(calls, "firestore databases create", "--database=aira", "--location=asia-south1", "--type=firestore-native")
    assert "(default)" not in joined
    assert _calls(calls, "iam service-accounts create aira-live")
    assert _calls(calls, f"serviceAccount:aira-live@{PROJECT}.iam.gserviceaccount.com", "roles/datastore.user")
    assert _calls(calls, "secrets create aira-gemini-api-key")
    assert _calls(calls, "secrets versions add aira-gemini-api-key", "--data-file=-")
    assert _calls(calls, "secrets add-iam-policy-binding aira-gemini-api-key", "roles/secretmanager.secretAccessor")
    assert _calls(calls, "npx", "hosting:sites:create aira-shop")
    assert _calls(calls, "npx", "deploy --only firestore", f"--project {PROJECT}")
    assert not _calls(calls, "run deploy"), "bootstrap creates infra only; deploy_live.sh deploys"
    assert not _calls(calls, "seed_firestore"), "seeding is opt-in (SEED=1)"
    assert secret not in joined and secret not in proc.stdout + proc.stderr


def test_bootstrap_is_idempotent(tmp_path):
    secret = "test-gemini-key-not-real"
    proc, calls, _ = run_script(
        tmp_path,
        "infra/bootstrap.sh",
        {"GCP_PROJECT": PROJECT, "GEMINI_API_KEY": secret, "STUB_EXISTS": "ALL", "STUB_SECRET_VALUE": secret},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in calls if " create " in f" {c} " or "create-oidc" in c or "sites:create" in c]
    assert not _calls(calls, "secrets versions add"), "same key: no new secret version"


def test_bootstrap_seed_is_opt_in(tmp_path):
    proc, calls, _ = run_script(tmp_path, "infra/bootstrap.sh", {"GCP_PROJECT": PROJECT, "STUB_EXISTS": "ALL", "SEED": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _calls(calls, "uv run", "seed_firestore.py", f"--project {PROJECT}")


def test_deploy_live_dry_run(tmp_path):
    proc, calls, _ = run_script(tmp_path, "infra/deploy_live.sh", {"GCP_PROJECT": PROJECT})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [deploy] = _calls(calls, "gcloud run deploy aira-live")
    for part in (
        "--source services/live",
        "--region asia-south1",
        f"--project {PROJECT}",
        f"--service-account aira-live@{PROJECT}.iam.gserviceaccount.com",
        "--allow-unauthenticated",
        "--min-instances 1",
        "--max-instances 1",
        "--timeout 3600",
        "--session-affinity",
        "--set-secrets GEMINI_API_KEY=aira-gemini-api-key:latest",
        f"--set-env-vars FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=murugan_dairy,AIRA_ALLOW_NO_TOKEN=1,GCP_PROJECT={PROJECT}",
    ):
        assert part in deploy, part
    assert _calls(calls, "curl", f"{RUN_URL}/healthz")
    assert "wss://aira-live-abc123-el.a.run.app/ws" in proc.stdout


def test_deploy_live_reads_project_from_dotenv(tmp_path):
    proc, calls, _ = run_script(tmp_path, "infra/deploy_live.sh", dotenv=f"# local\nGCP_PROJECT={PROJECT}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _calls(calls, "gcloud run deploy aira-live", f"--project {PROJECT}")


def test_deploy_live_fails_when_more_than_one_instance(tmp_path):
    proc, _, _ = run_script(tmp_path, "infra/deploy_live.sh", {"GCP_PROJECT": PROJECT, "STUB_MAX_SCALE": "3"})
    assert proc.returncode != 0
    assert "maxScale" in proc.stderr


def test_setup_github_deploy_fresh(tmp_path):
    proc, calls, _ = run_script(
        tmp_path,
        "infra/setup_github_deploy.sh",
        {"GCP_PROJECT": PROJECT, "STUB_EXISTS": f"aira-live@{PROJECT}"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    joined = "\n".join(calls)
    assert "FORBIDDEN" not in joined
    assert _calls(calls, "workload-identity-pools create aira-github-pool", "--location=global")
    [provider] = _calls(calls, "providers create-oidc aira-github")
    assert "--workload-identity-pool=aira-github-pool" in provider
    assert "--issuer-uri=https://token.actions.githubusercontent.com" in provider
    assert f"--attribute-condition={CONDITION}" in provider
    assert "attribute.repository=assertion.repository" in provider
    assert _calls(calls, "iam service-accounts create aira-deployer")
    assert _calls(
        calls, "artifacts repositories create aira", "--repository-format=docker", "--location=asia-south1"
    )

    deployer = f"serviceAccount:aira-deployer@{PROJECT}.iam.gserviceaccount.com"
    project_bindings = _calls(calls, f"projects add-iam-policy-binding {PROJECT}", deployer)
    project_roles = {m.group(1) for c in project_bindings for m in [re.search(r"--role=(\S+)", c)]}
    assert project_roles == {
        "roles/run.developer",
        "roles/firebasehosting.admin",
        "roles/serviceusage.serviceUsageConsumer",
    }
    [run_dev] = [c for c in project_bindings if "roles/run.developer" in c]
    assert "services/aira-" in run_dev, "run.developer is limited to aira-* services"
    assert _calls(
        calls,
        "artifacts repositories add-iam-policy-binding aira",
        "--location=asia-south1",
        deployer,
        "roles/artifactregistry.writer",
    )
    assert _calls(
        calls,
        f"iam service-accounts add-iam-policy-binding aira-live@{PROJECT}.iam.gserviceaccount.com",
        deployer,
        "roles/iam.serviceAccountUser",
    )
    assert _calls(
        calls,
        f"iam service-accounts add-iam-policy-binding aira-deployer@{PROJECT}.iam.gserviceaccount.com",
        "roles/iam.workloadIdentityUser",
        f"principalSet://iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/"
        "workloadIdentityPools/aira-github-pool/attribute.repository/Saravana-Rajan/AIRA",
    )
    out = proc.stdout
    assert f"GCP_PROJECT={PROJECT}" in out
    assert (
        f"GCP_WIF_PROVIDER=projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/"
        "aira-github-pool/providers/aira-github" in out
    )
    assert f"GCP_DEPLOY_SA=aira-deployer@{PROJECT}.iam.gserviceaccount.com" in out
    assert "SECRETS" in out and "gh secret set GCP_PROJECT" in out
    assert "gh variable" not in out, "the repo is public: secrets, never variables"


def test_setup_github_deploy_is_idempotent(tmp_path):
    proc, calls, _ = run_script(tmp_path, "infra/setup_github_deploy.sh", {"GCP_PROJECT": PROJECT, "STUB_EXISTS": "ALL"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in calls if " create " in f" {c} " or "create-oidc" in c]
    [update] = _calls(calls, "providers update-oidc aira-github")
    assert f"--attribute-condition={CONDITION}" in update


def test_setup_github_deploy_needs_runtime_service_account(tmp_path):
    proc, calls, _ = run_script(tmp_path, "infra/setup_github_deploy.sh", {"GCP_PROJECT": PROJECT})
    assert proc.returncode != 0
    assert "bootstrap.sh" in proc.stderr
    assert not _calls(calls, "create")


def test_build_hosting_puts_app_at_root_and_dashboard_under_dashboard(tmp_path):
    proc, calls, root = run_script(tmp_path, "scripts/build_hosting.sh", {"VITE_AIRA_LIVE_WS": "wss://x/ws"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = root / "dist" / "hosting"
    assert (out / "index.html").is_file()
    assert (out / "dashboard" / "index.html").is_file()
    [app] = _calls(calls, "npm run build -w apps/app")
    [dash] = _calls(calls, "npm run build -w apps/dashboard")
    assert f"--outDir {out}" in app
    assert "--base /dashboard/" in dash and f"--outDir {out / 'dashboard'}" in dash
    assert calls.index(app) < calls.index(dash)


def test_build_hosting_clears_stale_output(tmp_path):
    root = _fake_repo(tmp_path, "scripts/build_hosting.sh")
    stale = root / "dist" / "hosting" / "old-chunk.js"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")
    proc, _, _ = run_script(tmp_path, "scripts/build_hosting.sh")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not stale.exists()
