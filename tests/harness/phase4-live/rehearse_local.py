#!/usr/bin/env python3
"""Rehearse the Phase 4 ticket #6 driver against the recorded Gateway fake.

Run this before spending a live `phase4-live` run. It starts the shared recorded
Gateway fake on the one origin the driver accepts (`127.0.0.1:8093`), writes the
CI marker the live workflow writes, and runs every driver stage over it:

* ``policy-provision`` exercises the Native REST Tag provider + Tag import path.
* ``policy-read`` / ``alarm`` exercise the driver's probe-call and fact-derivation
  code against the recorded probe payloads.
* ``summarize`` exercises the drift check and verdict.

The rehearsal runs against ``tests/fixtures/recorded/gateway-8.3/phase4``. Those
fixtures are the recorded live bodies once the ticket's live run has been
captured; until then they model the expected handler output, and the rehearsal
says so. Everything the rehearsal writes stays in a temporary directory: a
rehearsal is never evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))
sys.path.insert(0, str(ROOT / "tests/harness/phase4-live"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

import driver  # noqa: E402
import policy_document  # noqa: E402

DRIVER = Path(__file__).resolve().parent / "driver.py"
GATEWAY_VERSION = "8.3.8"
GATEWAY_BUILD = "2026071409"
RUN_ID = "0000000000"
ALARM_ROOT = "mcp_p4_" + RUN_ID
MCP_PATH = driver.EXPECTED_MCP_PATH
FIXTURE_DIR = ROOT / "tests/fixtures/recorded/gateway-8.3/phase4"


def recorded_fixtures() -> list[str]:
    return sorted(path.name for path in FIXTURE_DIR.glob("*.json"))


def write_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "marker": "ignition-mcp-phase4-live",
        "environment": "phase4-live",
        "runId": RUN_ID,
        "gatewayVersion": GATEWAY_VERSION,
        "gatewayBuild": GATEWAY_BUILD,
        "gatewayId": f"phase4-g4a-{GATEWAY_VERSION}-{RUN_ID}",
        "trustedRepo": "sheon-sek/ignition-mcp",
        "policyProvider": policy_document.POLICY_PROVIDER,
        "alarmRoot": ALARM_ROOT,
    }, indent=2) + "\n", encoding="utf-8")


def run_stage(
    stage: list[str], base_url: str, mcp_url: str, work: Path, marker: Path, evidence: Path,
) -> int:
    command = [
        sys.executable, str(DRIVER), *stage,
        "--base-url", base_url,
        "--mcp-url", mcp_url,
        "--api-token", API_TOKEN,
        "--evidence-dir", str(evidence),
        "--ci-marker", str(marker),
        "--run-id", RUN_ID,
        "--gateway-version", GATEWAY_VERSION,
        "--gateway-build", GATEWAY_BUILD,
        "--root-name", ALARM_ROOT,
        "--noise-count", "60",
        "--cycles", "3",
        "--repeats", "3",
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    print("+", " ".join(stage), flush=True)
    completed = subprocess.run(command, cwd=str(ROOT), env=environment, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    return completed.returncode


def main() -> int:
    fixtures = recorded_fixtures()
    print(f"recorded fixtures in use: {', '.join(fixtures) if fixtures else '<none>'}")
    with tempfile.TemporaryDirectory(prefix="p4-rehearsal-") as temporary:
        work = Path(temporary)
        marker = work / "ci-marker.json"
        evidence = work / "evidence"
        write_marker(marker)
        with RecordedGateway(
            policy_provider=policy_document.POLICY_PROVIDER,
            runtime_tools=("policy_probe", "alarm_probe"),
            port=driver.EXPECTED_ORIGIN_PORT,
        ) as gateway:
            base_url = gateway.base_url
            mcp_url = base_url + MCP_PATH
            stages = [
                (["policy-provision"], "policy-provision"),
                (["policy-read", "--label", "before-restart"], "policy-read-before-restart"),
                (["policy-read", "--label", "after-restart"], "policy-read-after-restart"),
                (["alarm"], "alarm"),
            ]
            for stage, record in stages:
                code = run_stage(stage, base_url, mcp_url, work, marker, evidence)
                if code != 0:
                    print(f"rehearsal stage {record} failed with exit {code}", file=sys.stderr)
                    return 2
            code = run_stage(["summarize"], base_url, mcp_url, work, marker, evidence)
            evidence_path = evidence / "evidence.json"
            if not evidence_path.is_file():
                print("summarize produced no evidence.json", file=sys.stderr)
                return 2
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            print("verdict:", json.dumps(document.get("verdict"), indent=2, sort_keys=True))
            print("drift:", json.dumps(document.get("drift"), indent=2, sort_keys=True))
            if code not in {0, 3}:
                return code
            if code == 3:
                print("rehearsal characterized every stage but drifted from characterization.json")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
