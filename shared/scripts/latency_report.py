#!/usr/bin/env python3
"""Print p50 / p90 reply latency for the deployed aira-live service from Cloud Logging.

Reads the ``aira="latency"`` diag lines (server: ``first_audio`` = owner's last words -> AIRA's first audio
chunk out, ``frame_wait``, ``model`` per vision role, ``tool`` per tool name) and the phone's ``latency`` client
log (``play_start``: first audio chunk received -> audible on the phone).

Usage (from the repo root; needs gcloud logged in):
    python3 shared/scripts/latency_report.py --project <gcp-project-id> [--since 1h] [--limit 2000]
    GCP_PROJECT=<gcp-project-id> python3 shared/scripts/latency_report.py

The project id is never hard-coded: pass --project or set GCP_PROJECT.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys

SERVICE = "aira-live"


def gcloud_command(project: str, since: str, limit: int) -> list[str]:
    flt = (f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE}" AND '
           '(jsonPayload.aira="latency" OR (jsonPayload.aira="client" AND jsonPayload.tag="latency"))')
    return ["gcloud", "logging", "read", flt, f"--project={project}", f"--freshness={since}",
            f"--limit={limit}", "--format=json"]


def _key_and_ms(payload: dict) -> tuple[str, float] | None:
    if payload.get("aira") == "client":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        stage, ms = data.get("stage"), data.get("ms")
        key = f"phone:{stage}"
    else:
        stage, ms = payload.get("stage"), payload.get("ms")
        key = str(stage)
        if stage == "tool":
            key = f"tool:{payload.get('name')}"
        elif stage == "model":
            key = f"model:{payload.get('role')}"
        elif stage == "frame_wait":
            key = f"frame_wait:{'reused' if payload.get('reused') else payload.get('purpose')}"
    if not stage or not isinstance(ms, (int, float)):
        return None
    return key, float(ms)


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summarize(rows: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        got = _key_and_ms(row.get("jsonPayload") or {})
        if got:
            groups.setdefault(got[0], []).append(got[1])
    return {k: {"n": len(v), "p50": _pct(v, 50), "p90": _pct(v, 90), "max": max(v)}
            for k, v in sorted(groups.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("GCP_PROJECT"))
    ap.add_argument("--since", default="1h", help="gcloud --freshness window, e.g. 30m, 1h, 1d")
    ap.add_argument("--limit", type=int, default=2000)
    args = ap.parse_args(argv)
    if not args.project:
        ap.error("pass --project or set GCP_PROJECT")
    out = subprocess.run(gcloud_command(args.project, args.since, args.limit), check=True, capture_output=True,
                         text=True).stdout
    table = summarize(json.loads(out or "[]"))
    if not table:
        print("no latency lines in that window")
        return 0
    print(f"{'stage':32} {'n':>5} {'p50 ms':>8} {'p90 ms':>8} {'max ms':>8}")
    for key, row in table.items():
        print(f"{key:32} {row['n']:>5} {row['p50']:>8.0f} {row['p90']:>8.0f} {row['max']:>8.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
