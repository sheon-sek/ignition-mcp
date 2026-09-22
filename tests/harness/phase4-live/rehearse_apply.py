#!/usr/bin/env python3
"""Rehearse the ticket #21 live stage against the recorded Gateway fake.

The stage drives the shipped ``setup-native`` CLI, so its rehearsal has to be the
same command against the same fixtures the live run uses: a deterministic release
(built here into a temporary directory), the ticket #6 policy document, the
harness's own permissions tree, and the recorded Gateway fake which models the
Project import, the Server Config collection routes and the reserved policy Tag
provider (including the recorded first-import readiness flake).

Run it before spending a live ``phase4-live`` run:

    uv run --no-sync python tests/harness/phase4-live/rehearse_apply.py

Exit 0 means the stage passed end to end against the fake. A rehearsal is never
evidence: everything it writes stays in a temporary directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))
sys.path.insert(0, str(ROOT / "tests/harness/phase4-live"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

import policy_document  # noqa: E402

STAGE = Path(__file__).resolve().parent / "apply_stage.py"
PROJECT = "ignition_runtime_apply_rehearsal"
SERVER_CONFIG = "phase4-apply-runtime"


def build_release(work: Path) -> tuple[Path, dict[str, object]]:
    out = work / "release"
    completed = subprocess.run(
        [
            sys.executable, "-m", "tooling.native.cli", "release",
            "--project-dir", "packages/ignition-runtime-bundle/project",
            "--out-dir", str(out),
            "--source-revision", _head(),
            "--evidence-dir", "tests/compatibility/evidence",
        ],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        print(completed.stdout + completed.stderr, file=sys.stderr)
        raise SystemExit("the release build failed")
    manifest = json.loads(next(out.glob("*.manifest.json")).read_text(encoding="utf-8"))
    return out, manifest


def _head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, check=True,
    )
    return completed.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p4-apply-rehearsal-") as temporary:
        work = Path(temporary)
        release_dir, manifest = build_release(work)
        archive = next(release_dir.glob("*.zip"))
        readonly_tools = [str(name) for name in manifest["profileInventories"]["readonly"]["tools"]]  # type: ignore[index]
        readonly_resources = [
            str(uri) for uri in manifest["profileInventories"]["readonly"]["resources"]  # type: ignore[index]
        ]
        with RecordedGateway(
            runtime_tools=tuple(readonly_tools),
            runtime_resources=tuple(readonly_resources),
            bundle_version=str(manifest["bundleVersion"]),
            source_revision=str(manifest["sourceRevision"]),
            policy_provider=policy_document.POLICY_PROVIDER,
        ) as gateway:
            completed = subprocess.run(
                [
                    sys.executable, str(STAGE),
                    "--base-url", gateway.base_url,
                    "--api-token", API_TOKEN,
                    "--bundle-manifest", str(next(release_dir.glob("*.manifest.json"))),
                    "--bundle-zip", str(archive),
                    "--evidence-dir", str(work / "evidence"),
                    "--source-revision", str(manifest["sourceRevision"]),
                    "--project", PROJECT,
                    "--server-config", SERVER_CONFIG,
                ],
                cwd=str(ROOT), capture_output=True, text=True, check=False,
                env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT)},
            )
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        if completed.returncode != 0:
            print(f"the apply stage rehearsal failed (exit {completed.returncode})", file=sys.stderr)
            return completed.returncode
        evidence = json.loads((work / "evidence" / "setup-native-apply.json").read_text(encoding="utf-8"))
        print("verdict:", json.dumps({
            "ok": evidence.get("ok"),
            "verifyAttempts": evidence["steps"]["apply"].get("verifyAttempts"),
            "firstApply": [w["action"] for w in evidence["steps"]["apply"]["report"]["writes"]],
            "secondApply": [w["action"] for w in evidence["steps"]["secondApply"]["report"]["writes"]],
        }, indent=2, sort_keys=True))
        return 0 if evidence.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
