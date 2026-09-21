#!/usr/bin/env python3
"""Phase 3 G3 live driver: the read plane, the guarded executor and D16
transactions against a real (disposable) Ignition Gateway.

Exit codes: 0 all G3 checks passed on the required (D27-tuple) row; 3 all G3
checks passed but the row carries the known fail-closed native-binding
limitation (compatibility candidate); 2 fatal — a stage failed, with the
observations file recording the failing stage honestly.

This is harness code, not an installer: every Gateway write it performs goes
through the guarded mutation executor / Project transaction service under the
run-scoped allowlists, against the run-unique disposable Project only.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Coroutine

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from harness_common import (
    EXPECTED_GATE_ON_TOOLS,
    MUTATION_TOOL_NAMES,
    McpHttp,
    ProbeError,
    error_envelope,
)

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.audit.sink import Auditor, SqliteAuditSink
from ignition_rest_mcp.auth import build_auth, principal_from_token
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.cli.setup_native import doctor as setup_doctor
from ignition_rest_mcp.cli.setup_native import plan as setup_plan
from ignition_rest_mcp.cli.setup_native import verify as setup_verify
from ignition_rest_mcp.cli.setup_native.inputs import load_inputs
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext, uuid7
from ignition_rest_mcp.projects.capture import capture_project
from ignition_rest_mcp.projects.identity import gateway_identity
from ignition_rest_mcp.projects.locks import ProcessWriterGuard, ProjectLockRegistry
from ignition_rest_mcp.projects.transactions import (
    PROJECT_IMPORT_OPERATION,
    CandidateBuilder,
    ProjectTransactionService,
    TransactionState,
)
from ignition_rest_mcp.safety.executor import MutationRequest, VerificationOutcome, execute_mutation
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.paths import validate_data_directory
from ignition_rest_mcp.storage.records import OperationRecordStore

GATEWAY_INFO_PATH = "/data/api/v1/gateway-info"
DISPOSABLE_NAME_RE = re.compile(r"^mcp_g3_[0-9]+_[0-9A-Za-z]+$")
PCF_RE = re.compile(r"^pcf1:[0-9a-f]{64}$")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Stage:
    """Sequential stage recorder; the first failing check aborts with rc 2."""

    def __init__(self, observations: dict[str, Any]) -> None:
        self.observations = observations
        self.current = "identity"

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        entry = {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}
        self.observations["checks"].append(entry)
        print(f"[{self.current}] {entry['status']:<4} {name}: "
              f"{json.dumps(detail, default=str, sort_keys=True)[:300]}")
        if not ok:
            raise ProbeError(f"check failed in stage {self.current}: {name}")

    def stage(self, name: str) -> None:
        self.current = name
        self.observations["stages"].append(name)


# --------------------------------------------------------------------- JWT material


def mint_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


def make_jwt(private_pem: str, issuer: str, audience: str, *, sub: str, scopes: list[str],
             skew_seconds: int = 300) -> str:
    from joserfc import jwt as jose_jwt
    from joserfc.jwk import RSAKey

    now = int(time.time())
    claims = {
        "iss": issuer, "aud": audience, "sub": sub,
        "iat": now - 5, "exp": now + skew_seconds,
        "scope": " ".join(scopes),
    }
    return jose_jwt.encode({"alg": "RS256"}, claims, RSAKey.import_key(private_pem))


def make_foreign_jwt(issuer: str, audience: str) -> str:
    """A token signed by a throwaway key the deployment does not trust."""
    private_pem, _ = mint_pair()
    return make_jwt(private_pem, issuer, audience, sub="g3-attacker",
                    scopes=["ignition.read", "ignition.config"])


# --------------------------------------------------------------------- candidate builders


def _drained_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _read_entries(data: bytes) -> dict[str, bytes]:
    source = zipfile.ZipFile(io.BytesIO(data))
    return {info.filename: source.read(info.filename) for info in source.infolist()}


def _edit_description(entries: dict[str, bytes], token: str) -> dict[str, bytes]:
    """Deterministic content edit: append a run token to the Project description."""
    edited = dict(entries)
    original = edited.get("project.json")
    if original is not None:
        try:
            document = json.loads(original.decode("utf-8"))
        except ValueError:
            document = None
        if isinstance(document, dict):
            base = str(document.get("description") or "").strip()
            document["description"] = f"{base} mcp-g3-candidate={token}".strip()
            edited["project.json"] = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
            return edited
    edited["mcp-g3-probe.txt"] = token.encode("utf-8")
    return edited


async def _drain(reader: Any) -> bytes:
    chunks = bytearray()
    while True:
        chunk = await reader.read_chunk()
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


class _CopyBuilder(CandidateBuilder):
    """Re-writes the baseline entries verbatim; pcf1 stays equal (NO_CHANGE)."""

    async def build(self, baseline: Any, out: Any) -> None:
        data = await _drain(baseline)
        await out.write(_drained_zip(_read_entries(data)))


class _EditBuilder(CandidateBuilder):
    """Deterministic description edit + optional external-drift hook."""

    def __init__(self, token: str, hook: Callable[[], Coroutine[Any, Any, None]] | None = None) -> None:
        self.token = token
        self.hook = hook

    async def build(self, baseline: Any, out: Any) -> None:
        data = await _drain(baseline)
        if self.hook is not None:
            await self.hook()
        await out.write(_drained_zip(_edit_description(_read_entries(data), self.token)))


async def _artifact_bytes(store: LocalArtifactStore, artifact_id: str) -> bytes:
    reader = await store.open_read(artifact_id)
    try:
        return await _drain(reader)
    finally:
        await reader.close()


async def _chunked(data: bytes) -> AsyncIterator[bytes]:
    for start in range(0, len(data), 65536):
        yield data[start:start + 65536]


def _verify_project_description(client: GatewayClient, store: LocalArtifactStore, settings: Settings,
                                project: str, token: str) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    """Bounded verification hook for the external drift import: re-capture the
    Project and confirm the run token landed in its description."""

    async def verify(dispatch: Any) -> VerificationOutcome:
        try:
            context = OperationContext.start("capture", "jwt:g3-drift", "ARTIFACT")
            capture = await capture_project(
                client, store, context, project_name=project, gateway_id=settings.gateway_id,
                retention_class="EPHEMERAL", deadline_seconds=settings.artifact_timeout_seconds,
            )
            try:
                data = await _artifact_bytes(store, capture.artifact.artifact_id)
            finally:
                with contextlib.suppress(GatewayError):
                    await store.delete_internal(capture.artifact.artifact_id)
            marker = f"mcp-g3-candidate={token}".encode("utf-8")
            if marker in _read_entries(data).get("project.json", b""):
                return VerificationOutcome.CONFIRMED
            return VerificationOutcome.MISMATCH
        except GatewayError:
            return VerificationOutcome.INDETERMINATE

    return verify


# --------------------------------------------------------------------- driver


class Driver:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.observations: dict[str, Any] = {"schemaVersion": 1, "gate": "G3", "checks": [], "stages": []}
        self.stage = Stage(self.observations)
        self.settings = Settings.from_env()
        self.data_dir = validate_data_directory(self.settings.data_dir, self.settings.deployment_profile)
        self.client: GatewayClient | None = None
        self.registry: CapabilityRegistry | None = None
        self.storage: Storage | None = None
        self.store: LocalArtifactStore | None = None
        self.records: OperationRecordStore | None = None
        self.sink: SqliteAuditSink | None = None
        self.guard = ProcessWriterGuard(self.data_dir)
        self.binding_accepted: bool | None = None
        self.output_schema_published: bool | None = None

    # -- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> "Driver":
        self.client = GatewayClient(
            base_url=self.settings.gateway_url,
            api_token=self.settings.gateway_api_token,
            timeout_seconds=self.settings.request_timeout_seconds,
        )
        self.registry = CapabilityRegistry(self.client)
        self.storage = Storage(self.data_dir)
        await self.storage.open()
        self.records = OperationRecordStore(self.storage.state)
        self.sink = SqliteAuditSink(self.storage.audit)
        self.store = LocalArtifactStore(self.storage.state, self.data_dir, quotas_from_settings(self.settings))
        self.store.prepare()
        await self.registry.refresh()
        if not self.registry.supports("project_import"):
            raise ProbeError("the Gateway does not offer the project_import capability; guarded evidence impossible")
        if not self.settings.project_writer_enabled:
            raise ProbeError("the driver deployment must enable the Project writer (guarded-transaction evidence)")
        await self.guard.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.guard.release_sync()
        if self.registry is not None:
            await self.registry.aclose()
        if self.client is not None:
            await self.client.aclose()
        if self.storage is not None:
            await self.storage.close()

    # -- helpers ----------------------------------------------------------

    def context(self, tool: str, actor: str, *, destructive: bool = False) -> OperationContext:
        context = OperationContext.start(
            tool, actor, "ARTIFACT", permission_class="CONTROL" if destructive else "READ",
            destructive=destructive,
        )
        assert self.sink is not None and self.records is not None
        context.auditor = Auditor(self.sink, self.records, context, None)
        return context

    async def audit_rows(self, correlation_id: str) -> list[dict[str, Any]]:
        assert self.storage is not None

        def _read(conn: Any) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT phase, outcome, error_code FROM audit_log WHERE correlation_id = ? ORDER BY seq",
                (correlation_id,),
            ).fetchall()
            return [{"phase": r["phase"], "outcome": r["outcome"], "error_code": r["error_code"]} for r in rows]

        return await self.storage.audit.run(_read)

    async def transaction_row(self, transaction_id: str) -> dict[str, Any] | None:
        assert self.storage is not None

        def _read(conn: Any) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT state, gateway_id, gateway_id_derived, import_dispatched, error_code "
                "FROM project_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            return dict(row) if row else None

        return await self.storage.state.run(_read)

    async def artifact_row(self, artifact_id: str) -> dict[str, Any] | None:
        assert self.storage is not None

        def _read(conn: Any) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT state, retention_class, retention_lock, expires_at FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return dict(row) if row else None

        return await self.storage.state.run(_read)

    async def mint_principal(self, private_pem: str, sub: str, scopes: list[str]) -> tuple[str, Any]:
        verifier = build_auth(self.settings)
        if verifier is None:
            raise ProbeError("the driver deployment must run in JWT mode")
        token = make_jwt(private_pem, self.settings.jwt_issuer or "", self.settings.jwt_audience or "",
                         sub=sub, scopes=scopes)
        access = await verifier.verify_token(token)
        if access is None:
            raise ProbeError(f"deployment verifier rejected the harness {sub} token")
        return token, principal_from_token(self.settings, access)

    # -- stages -----------------------------------------------------------

    async def stage_identity_l5(self) -> str:
        """L5 mutation guards: explicit CI marker + Gateway identity + run-unique project."""
        self.stage.stage("identity-l5")
        marker = json.loads(Path(self.args.ci_marker).read_text(encoding="utf-8"))
        self.stage.check("ci-marker-present", marker.get("marker") == "ignition-mcp-phase3-live",
                         marker.get("marker"))
        self.stage.check("ci-marker-environment", marker.get("environment") == "phase3-live",
                         marker.get("environment"))
        assert self.client is not None
        info = await self.client.get_json(GATEWAY_INFO_PATH, context=self.context("gateway_info", "jwt:g3-live"))
        reported = str(info.get("ignitionVersion") or "")
        expected = str(marker.get("gatewayVersion") or "")
        self.stage.check("gateway-identity-match", reported == expected or reported.startswith(f"{expected} "),
                         {"reported": reported, "expected": expected, "name": info.get("name")})
        project = str(marker.get("disposableProject") or "")
        self.stage.check("disposable-project-name",
                         DISPOSABLE_NAME_RE.fullmatch(project) is not None and str(marker.get("runId")) in project,
                         project)
        self.stage.check("writer-allowlist-scoped",
                         list(self.settings.mutation_operations) == ["project_import"]
                         and list(self.settings.mutation_targets.get("project_import", ())) == [project],
                         {"operations": list(self.settings.mutation_operations),
                          "targets": {k: list(v) for k, v in self.settings.mutation_targets.items()}})
        self.stage.check("writer-settings",
                         self.settings.project_writer_enabled and self.settings.gateway_id != ""
                         and self.settings.config_mutation_enabled,
                         {"gatewayId": self.settings.gateway_id, "writer": True})
        return project

    async def stage_setup_native(self) -> None:
        self.stage.stage("setup-native")
        # The token file lives inside the 0700 data dir, never under an uploaded
        # evidence directory (secrets must never reach evidence).
        token_path = self.data_dir / "setup-native.token"
        token_path.write_text(self.settings.gateway_api_token + "\n", encoding="utf-8")
        os.chmod(token_path, 0o600)
        base = [
            "--bundle-manifest", str(self.args.release_manifest),
            "--bundle-zip", str(self.args.release_zip),
            "--gateway-url", self.settings.gateway_url,
            "--mcp-url", self.args.runtime_mcp_url,
            "--gateway-token-file", str(token_path),
            "--mcp-token-file", str(token_path),
            "--profile", "readonly",
            "--server-config-name", self.args.server_config_name,
            "--json",
        ]
        reports: dict[str, Any] = {}
        for command, runner in (("doctor", setup_doctor.run), ("plan", setup_plan.run), ("verify", setup_verify.run)):
            inputs = load_inputs(base, command)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = await runner(inputs)
            text = buffer.getvalue()
            payload: Any = None
            if text.strip():
                candidate = text if command != "plan" else text[: text.rindex("}") + 1]
                try:
                    payload = json.loads(candidate)
                except ValueError:
                    payload = {"raw": text[:2000]}
            reports[command] = payload
            self.stage.check(f"{command}-exit-zero", code == 0 and isinstance(payload, dict), {"exitCode": code})
        plan_report = reports.get("plan") or {}
        actions = plan_report.get("actions", []) if isinstance(plan_report, dict) else []
        blocked = [a for a in actions if isinstance(a, dict) and a.get("action") == "BLOCKED"]
        changed = [a for a in actions if isinstance(a, dict) and a.get("action") in ("CREATE", "UPDATE")]
        self.stage.check("plan-no-change-only", not blocked and not changed,
                         {"actions": actions, "applied": plan_report.get("applied")})
        doctor_checks = (reports.get("doctor") or {}).get("checks", [])
        self.observations["setupNative"] = {
            "doctor": reports.get("doctor"),
            "plan": reports.get("plan"),
            "verify": reports.get("verify"),
            "managedProject": [c for c in doctor_checks if isinstance(c, dict) and c.get("name") == "bundle-project"],
        }

    async def stage_rest_plane(self, project: str) -> None:
        """Export/download/diagnose over the real Streamable-HTTP server."""
        self.stage.stage("rest-plane")
        private_pem = Path(self.args.jwt_private_key).read_text(encoding="utf-8")
        token_read, principal_read = await self.mint_principal(private_pem, "g3-live", ["ignition.read"])
        self.stage.check("principal-scoped-to-read", set(principal_read.scopes) == {"ignition.read"},
                         sorted(principal_read.scopes))
        mcp = McpHttp(self.args.rest_url + "/mcp", token_read, timeout=180.0)
        try:
            await mcp.initialize()
            tools = await mcp.tools_list()
            names = {str(t.get("name")) for t in tools}
            self.stage.check("exact-inventory-gate-on", names == set(EXPECTED_GATE_ON_TOOLS),
                             {"missing": sorted(EXPECTED_GATE_ON_TOOLS - names),
                              "extra": sorted(names - EXPECTED_GATE_ON_TOOLS)})
            self.stage.check("zero-mutation-tools", not names & MUTATION_TOOL_NAMES,
                             sorted(names & MUTATION_TOOL_NAMES))

            first = await mcp.call_structured("project_export", {"projectName": project})
            second = await mcp.call_structured("project_export", {"projectName": project})
            fp1, fp2 = str(first["fingerprint"]), str(second["fingerprint"])
            self.stage.check("fingerprint-shape", PCF_RE.fullmatch(fp1) is not None, fp1)
            self.stage.check("fingerprint-stable", fp1 == fp2, {"first": fp1, "second": fp2})
            self.observations["fingerprintStability"] = "STABLE" if fp1 == fp2 else "UNSTABLE"
            self.observations["projectMutationConcurrencySafe"] = fp1 == fp2

            artifact = first["artifact"]
            aid = str(artifact["artifactId"])
            self.stage.check(
                "export-classification",
                artifact["kind"] == "project_export" and artifact["sensitivity"] == "CONFIDENTIAL"
                and artifact["retentionClass"] == "EXPORT",
                {"kind": artifact["kind"], "sensitivity": artifact["sensitivity"],
                 "retention": artifact["retentionClass"]},
            )
            async with httpx.AsyncClient(timeout=120.0) as http:
                headers = {"Authorization": "Bearer " + token_read}
                get = await http.get(self.args.rest_url + str(artifact["download"]["path"]), headers=headers)
                head = await http.head(
                    self.args.rest_url + str(second["artifact"]["download"]["path"]), headers=headers,
                )
            body_sha = _sha256_bytes(get.content)
            self.stage.check("get-status", get.status_code == 200, get.status_code)
            self.stage.check("body-sha-matches-metadata", body_sha == str(artifact["sha256"]),
                             {"body": body_sha, "metadata": artifact["sha256"]})
            self.stage.check("etag-matches", get.headers.get("etag") == f'"{artifact["sha256"]}"',
                             get.headers.get("etag"))
            self.stage.check("length-matches",
                             int(get.headers.get("content-length", "-1")) == int(artifact["sizeBytes"]),
                             get.headers.get("content-length"))
            self.stage.check("repr-digest", str(get.headers.get("repr-digest", "")).startswith("sha-256=:"),
                             get.headers.get("repr-digest"))
            self.stage.check("no-store", "no-store" in str(get.headers.get("cache-control", "")),
                             get.headers.get("cache-control"))
            compared = ("content-type", "content-length", "etag", "repr-digest", "content-disposition",
                        "cache-control")
            parity = {k: get.headers.get(k) for k in compared}
            head_parity = {k: head.headers.get(k) for k in compared}
            self.stage.check("head-get-parity", parity == head_parity and head.status_code == 200,
                             {"get": parity, "head": head_parity, "status": head.status_code})
            self.stage.check("head-no-body", len(head.content) == 0, len(head.content))

            listed = await mcp.call_structured("artifact_list", {"limit": 500})
            ids = [str(a["artifactId"]) for a in listed.get("items", [])]
            self.stage.check("artifact-list-visible", aid in ids, {"count": len(ids)})
            info = await mcp.call_structured("artifact_info", {"artifactId": aid})
            self.stage.check("artifact-info-matches", str(info["artifact"]["sha256"]) == str(artifact["sha256"]),
                             info["artifact"])

            tags = await mcp.call_structured("tag_config_export", {
                "provider": "default", "path": self.args.tag_export_path,
                "recursive": True, "includeUdts": False,
            })
            tag_artifact = tags["artifact"]
            async with httpx.AsyncClient(timeout=120.0) as http:
                tag_body = await http.get(self.args.rest_url + str(tag_artifact["download"]["path"]),
                                          headers={"Authorization": "Bearer " + token_read})
            json.loads(tag_body.text)  # a non-JSON body raises here and fails the stage
            self.observations["tagExport"] = {
                "provider": tags.get("provider"), "path": tags.get("path"),
                "bytes": len(tag_body.content), "mediaType": tag_artifact.get("mediaType"),
            }
            self.stage.check("tag-export-json", tag_artifact.get("mediaType") == "application/json",
                             tag_artifact.get("mediaType"))

            correlation = str(first["correlationId"])
            diagnosis = await mcp.call_structured("operation_diagnose", {"correlationId": correlation})
            self.stage.check("operation-diagnose-visible",
                             str(diagnosis.get("correlationId")) == correlation
                             and str(diagnosis.get("tool")) == "project_export"
                             and str(diagnosis.get("outcome")) == "succeeded",
                             {"outcome": diagnosis.get("outcome"), "phases": diagnosis.get("phases")})
            unknown = await mcp.tool_call("operation_diagnose", {"correlationId": uuid7()})
            self.stage.check("operation-diagnose-unknown-not-found",
                             bool(unknown.get("isError")) and error_envelope(unknown)["code"] == "not_found",
                             error_envelope(unknown)["code"] if unknown.get("isError") else "not-an-error")

            rows = await self.audit_rows(correlation)
            phases = [r["phase"] for r in rows]
            self.stage.check("export-audit-triple", phases == ["decision", "attempt", "result"], rows)
            self.observations["restPlane"] = {
                "export": first, "exportRepeatFingerprint": fp2, "diagnose": diagnosis,
                "auditRows": rows, "artifactId": aid, "bodySha256": body_sha,
                "principal": principal_read.key,
            }
        finally:
            await mcp.aclose()

    async def stage_runtime_plane(self) -> None:
        """D27 native-binding observations on the Runtime Bundle endpoint."""
        self.stage.stage("runtime-plane")
        mcp = McpHttp(self.args.runtime_mcp_url, self.settings.gateway_api_token, timeout=60.0)
        try:
            await mcp.initialize()
            tools = await mcp.tools_list()
            names = {str(t.get("name")) for t in tools}
            self.output_schema_published = any(isinstance(t.get("outputSchema"), dict) for t in tools)
            self.stage.check("runtime-exact-13", len(names) == 13 and "bundle_info" in names, sorted(names))
            bundle = await mcp.call_structured("bundle_info", {})
            manifest = json.loads(Path(self.args.release_manifest).read_text(encoding="utf-8"))
            self.stage.check("bundle-source-revision-stamped",
                             re.fullmatch(r"[0-9a-f]{40}", str(bundle.get("bundleSourceRevision") or "")) is not None,
                             str(bundle.get("bundleSourceRevision"))[:12])
            self.stage.check("bundle-version-matches-manifest",
                             bundle.get("bundleVersion") == manifest["bundleVersion"], bundle.get("bundleVersion"))
            failed = await mcp.tool_call("tag_read", {"tagPaths": ["!!!not-a-tag!!!"]})
            self.binding_accepted = bool(failed.get("isError"))
            self.stage.check("runtime-failure-iserror", bool(failed.get("isError")), failed.get("isError"))
            self.observations["runtime"] = {
                "tools": sorted(names), "bundleInfo": bundle,
                "outputSchemaPublished": self.output_schema_published,
                "nativeResponseBindingAccepted": self.binding_accepted,
            }
        finally:
            await mcp.aclose()

    async def stage_authz_denials(self, project: str) -> None:
        self.stage.stage("authz-denials")
        assert self.client is not None and self.registry is not None and self.store is not None
        private_pem = Path(self.args.jwt_private_key).read_text(encoding="utf-8")
        issuer = self.settings.jwt_issuer or ""
        audience = self.settings.jwt_audience or ""

        baseline = await capture_project(
            self.client, self.store, self.context("capture", "jwt:g3-live"),
            project_name=project, gateway_id=self.settings.gateway_id,
            retention_class="EPHEMERAL", deadline_seconds=self.settings.artifact_timeout_seconds,
        )
        denials: dict[str, Any] = {}

        verifier = build_auth(self.settings)
        assert verifier is not None
        foreign = make_foreign_jwt(issuer, audience)
        expired = make_jwt(private_pem, issuer, audience, sub="g3-live",
                           scopes=["ignition.read", "ignition.config"], skew_seconds=-60)
        self.stage.check("invalid-jwt-rejected", await verifier.verify_token(foreign) is None, "foreign signing key")
        self.stage.check("expired-jwt-rejected", await verifier.verify_token(expired) is None, "expired token")
        denials["invalidJwt"] = "rejected-before-authz"

        _, principal_read = await self.mint_principal(private_pem, "g3-readonly", ["ignition.read"])
        _, principal_config = await self.mint_principal(private_pem, "g3-config",
                                                        ["ignition.read", "ignition.config"])

        async def denied(label: str, principal: Any, target: str, expected_code: str,
                         expected_outcome: str) -> None:
            context = self.context("project_import", str(principal.key), destructive=True)
            try:
                await execute_mutation(
                    client=self.client, registry=self.registry, settings=self.settings,
                    context=context,
                    request=MutationRequest(
                        operation=PROJECT_IMPORT_OPERATION, principal=principal, target_id=target,
                        request_path=f"/data/api/v1/projects/import/{target}",
                        body_chunks=_chunked(b"PK\x03\x04never-dispatched"), content_type="application/zip",
                        dispatch_deadline_seconds=self.settings.artifact_timeout_seconds,
                        verification_deadline_seconds=30.0,
                        audit_fields={"projectName": target}, target_type="project",
                    ),
                )
            except GatewayError as error:
                rows = await self.audit_rows(context.correlation_id)
                outcomes = [r["outcome"] for r in rows]
                self.stage.check(f"denied-{label}",
                                 error.code == expected_code and outcomes == [expected_outcome]
                                 and all(r["phase"] == "decision" for r in rows),
                                 {"code": error.code, "outcomes": outcomes})
                denials[label] = {"code": error.code, "decision": outcomes[0] if outcomes else None}
                return
            self.stage.check(f"denied-{label}", False, "execute_mutation returned instead of raising")

        await denied("read-only-jwt", principal_read, project, "permission_denied",
                     "denied:authz-scope:missing-scope:ignition.config")
        await denied("non-allowlisted-target", principal_config, "ignition_runtime", "operation_disabled",
                     "denied:target-allowlist:target-not-allowlisted")

        after = await capture_project(
            self.client, self.store, self.context("capture", "jwt:g3-live"),
            project_name=project, gateway_id=self.settings.gateway_id,
            retention_class="EPHEMERAL", deadline_seconds=self.settings.artifact_timeout_seconds,
        )
        self.stage.check("gateway-unchanged-after-denials", after.fingerprint == baseline.fingerprint,
                         {"before": str(baseline.fingerprint)[:16], "after": str(after.fingerprint)[:16]})
        with contextlib.suppress(GatewayError):
            await self.store.delete_internal(baseline.artifact.artifact_id)
            await self.store.delete_internal(after.artifact.artifact_id)
        self.observations["authzDenials"] = {
            "denials": denials,
            "baselineFingerprint": baseline.fingerprint,
            "afterDenialsFingerprint": after.fingerprint,
        }

    async def stage_transactions(self, project: str) -> None:
        self.stage.stage("transactions")
        assert self.client is not None and self.registry is not None and self.store is not None
        assert self.storage is not None
        private_pem = Path(self.args.jwt_private_key).read_text(encoding="utf-8")
        _, principal_config = await self.mint_principal(private_pem, "g3-config",
                                                        ["ignition.read", "ignition.config"])
        service = ProjectTransactionService(
            client=self.client, registry=self.registry, store=self.store, settings=self.settings,
            locks=ProjectLockRegistry(timeout_seconds=self.settings.project_lock_timeout_seconds,
                                      max_entries=self.settings.project_lock_max_entries),
            identity=gateway_identity(self.settings.gateway_id, self.settings.gateway_url),
            db=self.storage.state,
        )
        attempts = await service.reconcile_interrupted(batch=self.settings.retention_batch_rows,
                                                       per_txn_seconds=self.settings.artifact_timeout_seconds)
        self.stage.check("restart-reconcile-clean-start", attempts == 0, attempts)

        async def run(builder: CandidateBuilder) -> tuple[OperationContext, Any]:
            context = self.context("project_import", str(principal_config.key), destructive=True)
            result = await service.execute(project_name=project, builder=builder, context=context,
                                           principal=principal_config)
            return context, result

        results: dict[str, Any] = {}

        # -- NO_CHANGE
        _, result = await run(_CopyBuilder())
        self.stage.check(
            "txn-no-change",
            result.state is TransactionState.NO_CHANGE and result.import_dispatched is False
            and result.baseline_fingerprint == result.candidate_fingerprint,
            {"state": result.state.value, "dispatched": result.import_dispatched},
        )
        results["NO_CHANGE"] = {
            "state": result.state.value, "importDispatched": result.import_dispatched,
            "transactionId": result.transaction_id, "fingerprint": result.baseline_fingerprint,
        }

        # -- COMMITTED
        committed_token = uuid7()[:8]
        context, result = await run(_EditBuilder(committed_token))
        self.stage.check(
            "txn-committed",
            result.state is TransactionState.COMMITTED and result.import_dispatched is True
            and result.error is None and result.result_fingerprint == result.candidate_fingerprint,
            {"state": result.state.value, "dispatched": result.import_dispatched, "error": str(result.error)},
        )
        row = await self.transaction_row(result.transaction_id)
        self.stage.check("txn-committed-record",
                         row is not None and row["state"] == "COMMITTED"
                         and row["gateway_id"] == self.settings.gateway_id and row["gateway_id_derived"] == 0,
                         row)
        recovery = await self.artifact_row(str(result.baseline_artifact_id))
        self.stage.check("txn-committed-recovery-persisted",
                         recovery is not None and recovery["retention_class"] == "RECOVERY"
                         and recovery["retention_lock"] == 0 and recovery["state"] == "READY"
                         and recovery["expires_at"] is not None,
                         recovery)
        token_read, _ = await self.mint_principal(private_pem, "g3-live", ["ignition.read"])
        mcp = McpHttp(self.args.rest_url + "/mcp", token_read, timeout=180.0)
        try:
            await mcp.initialize()
            reexport = await mcp.call_structured("project_export", {"projectName": project})
        finally:
            await mcp.aclose()
        self.stage.check("txn-committed-reexport", reexport["fingerprint"] == result.candidate_fingerprint,
                         {"candidate": result.candidate_fingerprint, "reexport": reexport["fingerprint"]})
        audit = await self.audit_rows(context.correlation_id)
        results["COMMITTED"] = {
            "state": result.state.value, "importDispatched": result.import_dispatched,
            "transactionId": result.transaction_id,
            "baselineFingerprint": result.baseline_fingerprint,
            "candidateFingerprint": result.candidate_fingerprint,
            "resultFingerprint": result.result_fingerprint, "auditRows": audit,
            "reexportFingerprint": reexport["fingerprint"],
            "recoveryArtifact": {"id": result.baseline_artifact_id, "row": dict(recovery or {})},
        }

        # -- CONFLICTED with real injected external drift
        external_token = uuid7()[:8]

        async def inject_drift() -> None:
            assert self.client is not None and self.registry is not None and self.store is not None
            drift = await capture_project(
                self.client, self.store, self.context("capture", "jwt:g3-drift"),
                project_name=project, gateway_id=self.settings.gateway_id,
                retention_class="EPHEMERAL", deadline_seconds=self.settings.artifact_timeout_seconds,
            )
            data = _drained_zip(_edit_description(
                _read_entries(await _artifact_bytes(self.store, drift.artifact.artifact_id)), external_token,
            ))
            drift_context = self.context("project_import", "jwt:g3-drift", destructive=True)
            outcome = await execute_mutation(
                client=self.client, registry=self.registry, settings=self.settings,
                context=drift_context,
                request=MutationRequest(
                    operation=PROJECT_IMPORT_OPERATION, principal=principal_config, target_id=project,
                    request_path=f"/data/api/v1/projects/import/{project}",
                    body_chunks=_chunked(data), content_type="application/zip", params={"overwrite": "true"},
                    dispatch_deadline_seconds=self.settings.artifact_timeout_seconds,
                    verification_deadline_seconds=60.0,
                    verify=_verify_project_description(self.client, self.store, self.settings, project,
                                                       external_token),
                    audit_fields={"projectName": project}, target_type="project",
                ),
            )
            drift_rows = await self.audit_rows(drift_context.correlation_id)
            results.setdefault("externalDrift", []).append({
                "state": outcome.state.value, "correlationId": drift_context.correlation_id,
                "auditRows": drift_rows,
            })
            with contextlib.suppress(GatewayError):
                await self.store.delete_internal(drift.artifact.artifact_id)

        conflicted_token = uuid7()[:8]
        context, result = await run(_EditBuilder(conflicted_token, hook=inject_drift))
        self.stage.check(
            "txn-conflicted",
            result.state is TransactionState.CONFLICTED and result.import_dispatched is False
            and result.error is not None and result.error.code == "conflict",
            {"state": result.state.value, "dispatched": result.import_dispatched, "error": str(result.error)},
        )
        row = await self.transaction_row(result.transaction_id)
        self.stage.check("txn-conflicted-record",
                         row is not None and row["state"] == "CONFLICTED" and row["import_dispatched"] == 0,
                         row)
        recovery = await self.artifact_row(str(result.baseline_artifact_id))
        self.stage.check("txn-conflicted-recovery-released",
                         recovery is not None and recovery["retention_class"] == "RECOVERY"
                         and recovery["retention_lock"] == 0,
                         recovery)
        audit = await self.audit_rows(context.correlation_id)
        self.stage.check("txn-conflicted-no-attempt", all(r["phase"] != "attempt" for r in audit), audit)
        results["CONFLICTED"] = {
            "state": result.state.value, "importDispatched": result.import_dispatched,
            "transactionId": result.transaction_id,
            "baselineFingerprint": result.baseline_fingerprint,
            "candidateFingerprint": result.candidate_fingerprint,
            "errorCode": result.error.code if result.error else None, "auditRows": audit,
            "recoveryArtifact": {"id": result.baseline_artifact_id, "row": dict(recovery or {})},
        }
        self.observations["transactions"] = results

    # -- entry ------------------------------------------------------------

    async def run(self) -> int:
        fatal: str | None = None
        try:
            project = await self.stage_identity_l5()
            await self.stage_setup_native()
            await self.stage_rest_plane(project)
            await self.stage_runtime_plane()
            await self.stage_authz_denials(project)
            await self.stage_transactions(project)
            self.observations["disposableProject"] = project
        except (ProbeError, GatewayError, httpx.HTTPError, ValueError, OSError) as error:
            fatal = f"{type(error).__name__}: {str(error)[:400]}"
            self.stage.check(f"stage-{self.stage.current}-fatal", False, fatal)
        except Exception as error:  # record the stage honestly, never a secret
            fatal = type(error).__name__
            self.stage.check(f"stage-{self.stage.current}-fatal", False, fatal)
        return await self._finish(fatal)

    async def _finish(self, fatal: str | None) -> int:
        assert self.registry is not None
        self.observations["openapiSha256"] = self.registry.snapshot.openapi_sha256
        self.observations["deployment"] = {
            "authMode": self.settings.auth_mode,
            "sensitiveExportsEnabled": self.settings.sensitive_exports_enabled,
            "projectWriterEnabled": self.settings.project_writer_enabled,
            "mutationOperations": list(self.settings.mutation_operations),
            "designerPolicy": self.settings.project_designer_policy,
        }
        self.observations["runtimeNativeBindingAccepted"] = self.binding_accepted
        self.observations["runtimeOutputSchemaPublished"] = self.output_schema_published
        self.observations["expectD27Tuple"] = self.args.expect_d27_tuple
        self.observations["fatal"] = fatal
        _write_json(self.args.observations, self.observations)
        failures = [c for c in self.observations["checks"] if c["status"] == "FAIL"]
        if fatal or failures:
            print(f"G3 driver FAILED at stage {self.stage.current}: {fatal or f'{len(failures)} check(s)'}")
            return 2
        if not self.args.expect_d27_tuple and self.output_schema_published is False:
            print("G3 driver: all G3 checks passed; row carries the native-binding limitation (candidate)")
            return 3
        print("G3 driver: all checks passed")
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci-marker", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--release-zip", required=True, type=Path)
    parser.add_argument("--jwt-private-key", required=True, type=Path)
    parser.add_argument("--rest-url", default="http://127.0.0.1:8765")
    parser.add_argument("--runtime-mcp-url", default="http://127.0.0.1:8088/data/mcp/phase3-runtime")
    parser.add_argument("--server-config-name", default="phase3-runtime")
    parser.add_argument("--tag-export-path", default="")
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--expect-d27-tuple", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    driver = Driver(args)

    async def go() -> int:
        async with driver:
            return await driver.run()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
