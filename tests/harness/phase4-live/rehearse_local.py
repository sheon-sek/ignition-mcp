#!/usr/bin/env python3
"""Rehearse the Phase 4 driver against the recorded Gateway fake.

Run this before spending a live `phase4-live` run. It starts the shared recorded
Gateway fake on the one origin the driver accepts (`127.0.0.1:8093`), writes the
CI marker the live workflow writes, and runs one milestone's driver stages over it
(`--stages 4a` is the default; `--stages 4b` is the ticket #10 Tag CONFIG
Mutation):

* ``policy-provision`` exercises the Native REST Tag provider + Tag import path.
* ``policy-read`` / ``alarm`` exercise the driver's probe-call and fact-derivation
  code against the recorded probe payloads.
* ``tag-write`` / ``alarm-no-policy`` / ``alarm-shelve`` exercise the shipped
  Mutation cases against the recorded Tool bodies.
* ``tag-update-no-policy`` / ``tag-update-setup`` / ``tag-update`` (milestone 4b)
  exercise the Tag config fingerprint and `tag_update` against the recorded Tag
  configuration the fake serves.
* ``summarize`` exercises the drift check and verdict.

The rehearsal runs against ``tests/fixtures/recorded/gateway-8.3/phase4``. Those
fixtures are the recorded live bodies once the ticket's live run has been
captured; until then they model the expected handler output, and the rehearsal
says so. Everything the rehearsal writes stays in a temporary directory: a
rehearsal is never evidence.
"""

from __future__ import annotations

import argparse
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
#: The milestone's CI marker label; the live workflow writes the same one, and the
#: driver's guard compares it, so a rehearsal marker can never stand in for another
#: milestone's.
MARKER_LABELS = {driver.MILESTONE_4A: "g4a", driver.MILESTONE_4B: "g4b"}
GATEWAY_VERSION = "8.3.8"
GATEWAY_BUILD = "2026071409"
RUN_ID = "0000000000"
ALARM_ROOT = "mcp_p4_" + RUN_ID
MCP_PATH = driver.EXPECTED_MCP_PATH
MILESTONES = {
    driver.MILESTONE_4A: [
        (["tag-write-no-policy"], "tag-write-no-policy"),
        (["alarm-no-policy"], "alarm-no-policy"),
        (["policy-provision"], "policy-provision"),
        (["policy-read", "--label", "before-restart"], "policy-read-before-restart"),
        (["policy-read", "--label", "after-restart"], "policy-read-after-restart"),
        (["alarm"], "alarm"),
        (["tag-write-setup"], "tag-write-setup"),
        (["tag-write"], "tag-write"),
        (["alarm-shelve"], "alarm-shelve"),
    ],
    driver.MILESTONE_4B: [
        (["tag-update-no-policy"], "tag-update-no-policy"),
        (["policy-provision"], "policy-provision"),
        (["tag-update-setup"], "tag-update-setup"),
        (["tag-update"], "tag-update"),
    ],
}
FIXTURE_DIR = ROOT / "tests/fixtures/recorded/gateway-8.3/phase4"


def tag_update_paths() -> dict[str, str]:
    """The Tag CONFIG Mutation paths of this rehearsal's run, for the fake's templates."""
    return {
        "writeTarget": policy_document.TAG_UPDATE_TARGET,
        "textTarget": policy_document.TAG_UPDATE_TEXT_TARGET,
        "nestedFolder": policy_document.TAG_UPDATE_FOLDER,
        "siblingTarget": policy_document.TAG_FIXTURE_SIBLING_PATH,
        "missingTarget": policy_document.TAG_FIXTURE_MISSING_PATH,
        "udtTarget": policy_document.TAG_UPDATE_UDT_TARGET,
    }


def recorded_fixtures() -> list[str]:
    return sorted(path.name for path in FIXTURE_DIR.glob("*.json"))


def write_marker(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "marker": "ignition-mcp-phase4-live",
        "environment": "phase4-live",
        "runId": RUN_ID,
        "gatewayVersion": GATEWAY_VERSION,
        "gatewayBuild": GATEWAY_BUILD,
        "gatewayId": f"phase4-{label}-{GATEWAY_VERSION}-{RUN_ID}",
        "trustedRepo": "sheon-sek/ignition-mcp",
        "policyProvider": policy_document.POLICY_PROVIDER,
        "alarmRoot": ALARM_ROOT,
        "runtimeProject": driver.RUNTIME_PROJECT,
        "auditProfile": policy_document.AUDIT_PROFILE_NAME,
    }, indent=2) + "\n", encoding="utf-8")


EXIT_OK = 0
EXIT_DRIFTED = 3


def run_stage(
    stage: list[str], base_url: str, mcp_url: str, work: Path, marker: Path, evidence: Path,
    characterization: str, stages: str,
) -> int:
    command = [
        sys.executable, str(DRIVER), *stage,
        "--base-url", base_url,
        "--mcp-url", mcp_url,
        "--operator-mcp-url", base_url + driver.OPERATOR_MCP_PATH,
        "--configurator-mcp-url", base_url + driver.CONFIGURATOR_MCP_PATH,
        "--marker-label", MARKER_LABELS[stages],
        "--stages", stages,
        "--api-token", API_TOKEN,
        "--evidence-dir", str(evidence),
        "--ci-marker", str(marker),
        "--run-id", RUN_ID,
        "--gateway-version", GATEWAY_VERSION,
        "--gateway-build", GATEWAY_BUILD,
        "--root-name", ALARM_ROOT,
        "--audit-profile", policy_document.AUDIT_PROFILE_NAME,
        "--characterization", characterization,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse the Phase 4 driver against the recorded Gateway fake")
    parser.add_argument(
        "--stages", default=driver.MILESTONE_4A, choices=sorted(driver.STAGE_SETS),
        help="which milestone's stages and expectations to rehearse",
    )
    parser.add_argument(
        "--characterization",
        default="",
        help="expectation file the summarize stage compares against; defaults to the "
             "selected milestone's own file",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    fixtures = recorded_fixtures()
    print(f"recorded fixtures in use: {', '.join(fixtures) if fixtures else '<none>'}")
    with tempfile.TemporaryDirectory(prefix="p4-rehearsal-") as temporary:
        work = Path(temporary)
        marker = work / "ci-marker.json"
        evidence = work / "evidence"
        write_marker(marker, MARKER_LABELS[args.stages])
        with RecordedGateway(
            policy_provider=policy_document.POLICY_PROVIDER,
            runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
            audit_profile=policy_document.AUDIT_PROFILE_NAME,
            port=driver.EXPECTED_ORIGIN_PORT,
            alarm_root=ALARM_ROOT,
            tag_update_paths=tag_update_paths(),
        ) as gateway:
            base_url = gateway.base_url
            mcp_url = base_url + MCP_PATH
            for stage, record in MILESTONES[args.stages]:
                code = run_stage(
                    stage, base_url, mcp_url, work, marker, evidence, args.characterization, args.stages,
                )
                if code != 0:
                    print(f"rehearsal stage {record} failed with exit {code}", file=sys.stderr)
                    return 2
            code = run_stage(
                ["summarize"], base_url, mcp_url, work, marker, evidence, args.characterization, args.stages,
            )
            evidence_path = evidence / "evidence.json"
            if not evidence_path.is_file():
                print("summarize produced no evidence.json", file=sys.stderr)
                return 2
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            print("verdict:", json.dumps(document.get("verdict"), indent=2, sort_keys=True))
            print("drift:", json.dumps(document.get("drift"), indent=2, sort_keys=True))
            if code not in {EXIT_OK, EXIT_DRIFTED}:
                return code
            if code == EXIT_DRIFTED:
                # The pre-live check exists to catch drift before live CI spends a
                # run, so it has to fail on it: exit 3 is the drift signal, not a
                # warning.
                print("rehearsal drifted from characterization.json", file=sys.stderr)
                return EXIT_DRIFTED
            return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
