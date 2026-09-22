#!/usr/bin/env python3
"""Rehearse the ticket #21 live stage against the recorded Gateway fake.

The stage drives the shipped ``setup-native`` CLI, so its rehearsal has to be the
same command against the same fixtures the live run uses: a deterministic release
(built here into a temporary directory), the ticket #6 policy document, the
harness's own permissions tree, and the recorded Gateway fake which models the
Project import, the Server Config collection routes and the reserved policy Tag
provider (including the recorded first-import readiness flake).

Ticket #56: the rehearsal also runs the module-install phase of the stage against a
fake started with ``module_install_flow=True`` (no MCP Module until the documented
install flow puts one there and the Gateway comes back from its restart), then the
apply stage with the NO CHANGE read-back and the Bundle upgrade in the round-1
order: the released bundle one version lower is the starting deployment, and the
exact release is applied over it with ``--acknowledge-upgrade``.

Run it before spending a live ``phase4-live`` run:

    uv run --no-sync python tests/harness/phase4-live/rehearse_apply.py

Exit 0 means the stage passed end to end against the fake. A rehearsal is never
evidence: everything it writes stays in a temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
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


#: The pinned Module artifact the install phase installs, exactly as the workflow names it.
MODULE_FILE = ROOT / "tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.modl"
MODULE_SHA256 = "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365"


def _run_stage(stage_args: list[str], work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STAGE), *stage_args],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p4-apply-rehearsal-") as temporary:
        work = Path(temporary)
        release_dir, manifest = build_release(work)
        archive = next(release_dir.glob("*.zip"))
        readonly_tools = [str(name) for name in manifest["profileInventories"]["readonly"]["tools"]]  # type: ignore[index]
        readonly_resources = [
            str(uri) for uri in manifest["profileInventories"]["readonly"]["resources"]  # type: ignore[index]
        ]
        # Ticket #56: a validated bundle source copy for the upgrade case, exactly the
        # directory the workflow prepares.
        bundle_source = work / "bundle-source"
        bundle_source.mkdir()
        for sibling in ("BUNDLE_VERSION", "RESOURCE_SCHEMA_VERSION"):
            shutil.copy(
                ROOT / "packages/ignition-runtime-bundle" / sibling,
                bundle_source / sibling,
            )
        shutil.copytree(ROOT / "packages/ignition-runtime-bundle/project", bundle_source / "project")

        # Phase one: the module-install stage against a Gateway with NO module. Both
        # phases share the evidence directory, exactly as the workflow's steps do, so
        # the apply phase embeds the recorded INSTALL step.
        with RecordedGateway(
            runtime_tools=tuple(readonly_tools),
            runtime_resources=tuple(readonly_resources),
            bundle_version=str(manifest["bundleVersion"]),
            source_revision=str(manifest["sourceRevision"]),
            policy_provider=policy_document.POLICY_PROVIDER,
            module_install_flow=True,
        ) as install_gateway:
            install = _run_stage(
                [
                    "--base-url", install_gateway.base_url,
                    "--api-token", API_TOKEN,
                    "--module-file", str(MODULE_FILE),
                    "--module-sha256", MODULE_SHA256,
                    "--install-only",
                    "--evidence-dir", str(work / "evidence"),
                ],
                work,
            )
        if install.stdout:
            print(install.stdout.rstrip())
        if install.stderr:
            print(install.stderr.rstrip(), file=sys.stderr)
        if install.returncode != 0:
            print("the module-install rehearsal failed", file=sys.stderr)
            return install.returncode

        # Phase two: the apply stage over the lowered starting deployment, the NO
        # CHANGE read-back, the idempotency checks, and the Bundle upgrade to the
        # real released bundle under acknowledgement.
        with RecordedGateway(
            runtime_tools=tuple(readonly_tools),
            runtime_resources=tuple(readonly_resources),
            bundle_version=str(manifest["bundleVersion"]),
            source_revision=str(manifest["sourceRevision"]),
            policy_provider=policy_document.POLICY_PROVIDER,
            module_install_flow=True,
            module_installed=True,
        ) as gateway:
            completed = _run_stage(
                [
                    "--base-url", gateway.base_url,
                    "--api-token", API_TOKEN,
                    "--bundle-manifest", str(next(release_dir.glob("*.manifest.json"))),
                    "--bundle-zip", str(archive),
                    "--evidence-dir", str(work / "evidence"),
                    "--source-revision", str(manifest["sourceRevision"]),
                    "--project", PROJECT,
                    "--server-config", SERVER_CONFIG,
                    "--module-file", str(MODULE_FILE),
                    "--module-sha256", MODULE_SHA256,
                    "--bundle-source", str(bundle_source),
                ],
                work,
            )
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        if completed.returncode != 0:
            print(f"the apply stage rehearsal failed (exit {completed.returncode})", file=sys.stderr)
            return completed.returncode
        evidence = json.loads((work / "evidence" / "setup-native-apply.json").read_text(encoding="utf-8"))
        upgrade = evidence["steps"]["upgradeApply"]
        print("verdict:", json.dumps({
            "ok": evidence.get("ok"),
            "installAgain": evidence["steps"]["install-module-again"]["report"]["outcome"],
            "verifyAttempts": evidence["steps"]["apply"].get("verifyAttempts"),
            "firstApply": [w["action"] for w in evidence["steps"]["apply"]["report"]["writes"]],
            "secondApply": [w["action"] for w in evidence["steps"]["secondApply"]["report"]["writes"]],
            "startingBundle": evidence.get("startingBundleVersion"),
            "upgrade": {
                "from": upgrade["bundleVersionBefore"],
                "to": upgrade["bundleVersionAfter"],
                "exitCode": upgrade["exitCode"],
                "verifyGreen": upgrade["verifyGreen"],
            },
        }, indent=2, sort_keys=True))
        return 0 if evidence.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
