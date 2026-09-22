"""Phase 6 ticket #54: ``setup-native install-module`` (D20, D26).

The Gateway half is an ``httpx.MockTransport`` fake that speaks the recorded 8.3.8
module routes (``docs/ignition-8.3.8-openapi/openapi.min.json``): healthy, upload,
certificate, eula, install and restart. Every case asserts what the Gateway was
asked to do and what the run reported, so a refusal is proven by the request that
never arrived, not by a message. The restart wait runs on a recorded sleeper, so
no test sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from ignition_rest_mcp.cli.setup_native import gateway, install_module, writer
from ignition_rest_mcp.cli.setup_native import main as cli_main
from ignition_rest_mcp.cli.setup_native.inputs import Endpoint, ModuleInputs, UsageError

GATEWAY_TOKEN = "GWSENTINELc0ffee000111222333"
MODULE_ID = gateway.MCP_MODULE_ID
FILE_VERSION = "1.3.5.2026021307-SNAPSHOT"
FILE_BUILD = "2026021307"
OLDER_BUILD = "2025010101"
NEWER_BUILD = "2027010101"
ENDPOINT = Endpoint(url="http://127.0.0.1:8088", scheme="http", host="127.0.0.1", port=8088)
REMOTE_ENDPOINT = Endpoint(url="http://10.0.0.9:8088", scheme="http", host="10.0.0.9", port=8088)

CERTIFICATE = {
    "subjectName": "CN=Inductive Automation, O=IA",
    "issuerName": "CN=IA Module Signing",
    "notValidBefore": "2026-01-01T00:00:00Z",
    "notValidAfter": "2027-01-01T00:00:00Z",
    "selfSigned": False,
    "serialNumber": "00AABBCC",
}
EULA_HTML = b"<html><body>the terms</body></html>"


def modl(version: str = FILE_VERSION, module_id: str = MODULE_ID) -> bytes:
    """One ``.modl`` shaped like the real artifact: a ZIP holding ``module.xml``."""

    document = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"<modules><module><name>MCP</name><id>{module_id}</id><version>{version}</version>"
        "</module></modules>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.xml", document)
        archive.writestr("mcp-gateway.jar", b"jar bytes")
    return buffer.getvalue()


class FakeModuleGateway:
    """The module REST flow, with the state each step changes on a real Gateway."""

    def __init__(
        self,
        *,
        installed: dict[str, str] | None = None,
        certificate: bool = True,
        eula: bool = True,
        accept_status: int = 200,
        upload_module_id: str | None = MODULE_ID,
        ready_polls: int = 0,
        offline: bool = False,
        restart_refusal: int | None = None,
    ) -> None:
        self.installed = dict(installed or {})
        self.certificate = certificate
        self.eula = eula
        self.accept_status = accept_status
        self.upload_module_id = upload_module_id
        self.ready_polls = ready_polls
        self.offline = offline
        self.restart_refusal = restart_refusal
        self.calls: list[tuple[str, str]] = []
        self.uploaded: bytes | None = None
        self.accepted: list[str] = []
        self.restarts = 0
        self.polls = 0
        self.uploaded_file_name: str | None = None

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self) -> list[str]:
        return [path for _, path in self.calls]

    def _healthy(self) -> httpx.Response:
        # A restart takes the Gateway down; it answers only after ``ready_polls`` tries.
        if self.restarts and self.polls < self.ready_polls:
            self.polls += 1
            raise httpx.ConnectError("connection refused")
        items = [{"id": key, "version": value} for key, value in self.installed.items()]
        return httpx.Response(200, json={"total": len(items), "items": items})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        assert request.headers["x-ignition-api-token"] == GATEWAY_TOKEN
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == gateway.MODULES_PATH:
            return self._healthy()
        if path == gateway.MODULE_CERTIFICATE_PATH and request.method == "GET":
            return httpx.Response(200, json=CERTIFICATE) if self.certificate else httpx.Response(404, json={})
        if path == gateway.MODULE_EULA_PATH and request.method == "GET":
            if not self.eula:
                return httpx.Response(404, json={})
            return httpx.Response(200, content=EULA_HTML, headers={"content-type": "text/html"})
        if path == writer.MODULE_UPLOAD_PATH:
            self.uploaded = request.content
            self.uploaded_file_name = request.url.params.get("fileName")
            body: dict[str, Any] = {"moduleId": self.upload_module_id, "licenseAccepted": False,
                                    "certAccepted": False, "containsEula": self.eula,
                                    "containsCert": self.certificate}
            return httpx.Response(200, json=body)
        if path in (writer.MODULE_CERTIFICATE_ACCEPT_PATH, writer.MODULE_EULA_ACCEPT_PATH):
            self.accepted.append(path)
            return httpx.Response(self.accept_status, json={"success": True, "message": "already accepted"})
        if path == writer.MODULE_INSTALL_PATH:
            self.installed = {MODULE_ID: f"1.3.5-SNAPSHOT (b{FILE_BUILD})"}
            return httpx.Response(200, json={"filename": "mcp.modl"})
        if path == writer.GATEWAY_RESTART_PATH:
            assert request.url.params["confirm"] == "true"
            self.restarts += 1
            if self.restart_refusal is not None:
                return httpx.Response(self.restart_refusal, json={"message": "no"})
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"unexpected {path}"})


def make_inputs(tmp_path: Path, payload: bytes | None = None, **overrides: Any) -> ModuleInputs:
    """One valid ``install-module`` input set over a written ``.modl``."""

    data = modl() if payload is None else payload
    path = tmp_path / "mcp-module.modl"
    path.write_bytes(data)
    values: dict[str, Any] = {
        "command": "install-module",
        "module_file": path,
        "upload_name": "mcp-module.modl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "gateway_url": ENDPOINT,
        "gateway_token": GATEWAY_TOKEN,
        "timeout_seconds": 5.0,
        "allow_insecure_authorize": False,
        "as_json": True,
        "accept_certificate": False,
        "accept_eula": False,
        "acknowledge_upgrade": False,
        "restart": False,
    }
    values.update(overrides)
    return ModuleInputs(**values)


def install(
    tmp_path: Path,
    fake: FakeModuleGateway,
    *,
    as_json: bool = True,
    **flags: Any,
) -> tuple[int, dict[str, Any], str, list[float]]:
    """Run ``install-module`` against the fake and return code, report, text and waits."""

    inputs = make_inputs(tmp_path, as_json=as_json, **flags)
    waits: list[float] = []

    async def sleeper(seconds: float) -> None:
        waits.append(seconds)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = asyncio.run(install_module.run(inputs, gateway_transport=fake.transport, sleeper=sleeper))
    text = out.getvalue()
    report = json.loads(text) if as_json else {}
    return code, report, text, waits


ACCEPT_BOTH = {"accept_certificate": True, "accept_eula": True}


def test_a_fresh_install_uploads_accepts_and_installs_in_the_documented_order(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway()
    code, report, _, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 0
    assert report["outcome"] == "INSTALL"
    assert report["moduleId"] == MODULE_ID
    assert report["moduleBuild"] == FILE_BUILD
    assert fake.uploaded == modl()
    assert fake.uploaded_file_name == "mcp-module.modl"
    assert fake.calls == [
        ("GET", gateway.MODULES_PATH),
        ("POST", writer.MODULE_UPLOAD_PATH),
        ("GET", gateway.MODULE_CERTIFICATE_PATH),
        ("GET", gateway.MODULE_EULA_PATH),
        ("POST", writer.MODULE_CERTIFICATE_ACCEPT_PATH),
        ("POST", writer.MODULE_EULA_ACCEPT_PATH),
        ("POST", writer.MODULE_INSTALL_PATH),
    ]


def test_without_restart_the_run_hands_the_pending_restart_to_the_operator(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway()
    code, report, text, waits = install(tmp_path, fake, as_json=False, **ACCEPT_BOTH)

    assert code == 0
    assert fake.restarts == 0
    assert waits == []
    assert writer.GATEWAY_RESTART_PATH not in fake.paths()
    assert "NEEDS-ACKrestart" in text
    assert "restart the Gateway, then run setup-native verify" in text
    assert report == {}


def test_restart_waits_for_the_gateway_and_proves_the_served_build(tmp_path: Path) -> None:
    fake = FakeModuleGateway(ready_polls=2)
    code, report, _, waits = install(tmp_path, fake, restart=True, **ACCEPT_BOTH)

    assert code == 0
    assert fake.restarts == 1
    # Two refused polls, each given the bounded interval, then the third read confirms.
    assert waits == [install_module.RESTART_POLL_SECONDS] * (fake.polls)
    assert report["restart"] == {
        "requested": True, "ready": True, "build": FILE_BUILD,
        "readback": f"poll 3: version=1.3.5-SNAPSHOT build={FILE_BUILD} "
                    f"reported=1.3.5-SNAPSHOT (b{FILE_BUILD})",
    }
    assert report["outcome"] == "INSTALL"


def test_a_module_that_never_comes_back_reports_the_bound_it_hit(tmp_path: Path) -> None:
    fake = FakeModuleGateway(ready_polls=10_000)
    code, report, _, waits = install(tmp_path, fake, restart=True, **ACCEPT_BOTH)

    assert code == 1
    assert report["outcome"] == "FAILED"
    assert len(waits) == int(
        install_module.RESTART_READY_SECONDS // install_module.RESTART_POLL_SECONDS
    ) - 1
    assert "within" in report["error"] and FILE_BUILD in report["error"]


def test_a_refused_restart_is_not_retried(tmp_path: Path) -> None:
    fake = FakeModuleGateway(restart_refusal=503)
    code, report, _, waits = install(tmp_path, fake, restart=True, **ACCEPT_BOTH)

    assert code == 1
    assert fake.restarts == 1
    assert waits == []
    assert "restart request was refused" in report["error"]


# --------------------------------------------------------------------- idempotence


def test_the_same_build_installed_is_no_change_and_uploads_nothing(tmp_path: Path) -> None:
    fake = FakeModuleGateway(installed={MODULE_ID: f"1.3.5-SNAPSHOT (b{FILE_BUILD})"})
    code, report, _, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 0
    assert report["outcome"] == "NO CHANGE"
    assert fake.paths() == [gateway.MODULES_PATH]
    assert fake.uploaded is None


def test_a_higher_build_needs_acknowledge_upgrade(tmp_path: Path) -> None:
    installed = {MODULE_ID: f"1.3.5-SNAPSHOT (b{OLDER_BUILD})"}
    without = FakeModuleGateway(installed=installed)
    code, report, _, _ = install(tmp_path, without, **ACCEPT_BOTH)

    assert code == 3
    assert report["outcome"] == "REFUSED"
    assert "--acknowledge-upgrade" in report["error"]
    assert without.paths() == [gateway.MODULES_PATH]
    assert without.uploaded is None

    acknowledged = FakeModuleGateway(installed=dict(installed))
    code, report, _, _ = install(tmp_path, acknowledged, acknowledge_upgrade=True, **ACCEPT_BOTH)

    assert code == 0
    assert report["outcome"] == "UPGRADE"
    assert acknowledged.uploaded == modl()


def test_a_lower_build_than_the_gateway_has_is_always_refused(tmp_path: Path) -> None:
    installed = {MODULE_ID: f"1.3.5-SNAPSHOT (b{NEWER_BUILD})"}
    plain = FakeModuleGateway(installed=installed)
    code, report, _, _ = install(tmp_path, plain, acknowledge_upgrade=True, **ACCEPT_BOTH)

    assert code == 1
    assert report["outcome"] == "REFUSED"
    assert "a lower build is never installed" in report["error"]
    assert plain.uploaded is None
    assert plain.paths() == [gateway.MODULES_PATH]


def test_an_unreadable_installed_build_is_refused(tmp_path: Path) -> None:
    fake = FakeModuleGateway(installed={MODULE_ID: "1.3.5"})
    code, report, _, _ = install(tmp_path, fake, acknowledge_upgrade=True, **ACCEPT_BOTH)

    assert code == 1
    assert "unreadable" in report["error"]
    assert fake.uploaded is None


# ---------------------------------------------------------------- safety refusals


def test_a_hash_mismatch_is_refused_before_the_gateway_hears_anything(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway()
    inputs = make_inputs(tmp_path, sha256="0" * 64)

    with pytest.raises(UsageError, match="SHA-256 mismatch"):
        asyncio.run(install_module.run(inputs, gateway_transport=fake.transport))

    assert fake.calls == []


@pytest.mark.parametrize("payload, fragment", [
    (b"not a zip at all", "not a ZIP archive"),
    (lambda: _zip_without_module_xml(), "holds no module.xml"),
    (lambda: _zip_with(b"<modules><module><id>x</id></module></modules>"), "no <version>"),
    (lambda: _zip_with(b"<modules><module><id>x</id><version>1.2.3</version></module></modules>"),
     "no 10-digit build"),
])
def test_an_artifact_that_is_not_a_module_is_a_usage_error(
    tmp_path: Path, payload: Any, fragment: str
) -> None:
    data = payload() if callable(payload) else payload
    fake = FakeModuleGateway()
    inputs = make_inputs(tmp_path, payload=data)

    with pytest.raises(UsageError, match=fragment):
        asyncio.run(install_module.run(inputs, gateway_transport=fake.transport))

    assert fake.calls == []


def _zip_with(module_xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("module.xml", module_xml)
    return buffer.getvalue()


def _zip_without_module_xml() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other.xml", b"<modules/>")
    return buffer.getvalue()


def test_certificate_and_eula_are_shown_and_nothing_installed_without_their_flags(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway()
    code, report, text, _ = install(tmp_path, fake, as_json=False)

    assert code == 3
    assert fake.paths().count(writer.MODULE_INSTALL_PATH) == 0
    assert fake.accepted == []
    assert CERTIFICATE["subjectName"] in text
    assert CERTIFICATE["issuerName"] in text
    assert CERTIFICATE["notValidBefore"] in text and CERTIFICATE["notValidAfter"] in text
    assert f"{ENDPOINT.url}{gateway.MODULE_EULA_PATH}?moduleId={MODULE_ID}" in text
    assert "--accept-certificate, --accept-eula not passed" in text
    assert report == {}


def test_the_eula_body_is_pointed_at_and_never_rendered(tmp_path: Path) -> None:
    fake = FakeModuleGateway()
    code, report, _, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 0
    assert report["eula"] == {
        "sizeBytes": len(EULA_HTML),
        "readAt": f"{ENDPOINT.url}{gateway.MODULE_EULA_PATH}?moduleId={MODULE_ID}",
    }
    assert "the terms" not in json.dumps(report)


def test_a_module_with_no_certificate_and_no_eula_installs_with_no_flags(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway(certificate=False, eula=False)
    code, report, _, _ = install(tmp_path, fake)

    assert code == 0
    assert "certificate" not in report and "eula" not in report
    assert fake.accepted == []
    assert fake.paths().count(writer.MODULE_INSTALL_PATH) == 1


def test_an_already_accepted_certificate_still_installs(tmp_path: Path) -> None:
    fake = FakeModuleGateway(accept_status=409)
    code, report, _, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 0
    actions = {step["name"]: step["detail"] for step in report["steps"]}
    assert actions["certificate"] == "already accepted by the Gateway"
    assert actions["eula"] == "already accepted by the Gateway"
    assert fake.paths().count(writer.MODULE_INSTALL_PATH) == 1


def test_an_upload_the_gateway_stores_under_another_id_installs_nothing(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway(upload_module_id="com.some.other.module")
    code, report, _, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 1
    assert report["outcome"] == "REFUSED"
    assert "com.some.other.module" in report["error"]
    assert fake.paths().count(writer.MODULE_INSTALL_PATH) == 0


def test_an_unreachable_gateway_fails_without_leaking_the_token(tmp_path: Path) -> None:
    fake = FakeModuleGateway(offline=True)
    code, report, text, _ = install(tmp_path, fake, **ACCEPT_BOTH)

    assert code == 1
    assert GATEWAY_TOKEN not in text
    assert "install-module" in report["command"]


def test_plain_http_to_a_remote_gateway_is_refused_before_any_request(
    tmp_path: Path,
) -> None:
    fake = FakeModuleGateway()
    inputs = make_inputs(tmp_path, gateway_url=REMOTE_ENDPOINT, **ACCEPT_BOTH)

    with pytest.raises(UsageError, match="allow-insecure-authorize"):
        asyncio.run(install_module.run(inputs, gateway_transport=fake.transport))

    assert fake.calls == []


# ------------------------------------------------------------------------- CLI wiring


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_install_module_documents_its_own_flags_and_no_manifest() -> None:
    code, out, err = run_main(["setup-native", "install-module", "--help"])

    assert code == 0 and err == ""
    for flag in ("--file", "--sha256", "--accept-certificate", "--accept-eula", "--restart"):
        assert flag in out
    assert "--bundle-manifest" not in out and "--profile" not in out


def test_install_module_needs_its_file_and_its_hash() -> None:
    code, _, err = run_main(["setup-native", "install-module", "--gateway-url", "http://127.0.0.1:8088"])

    assert code == 2
    assert "--file" in err and "--sha256" in err


def test_install_module_needs_a_gateway_token(tmp_path: Path) -> None:
    path = tmp_path / "mcp.modl"
    path.write_bytes(modl())
    code, _, err = run_main([
        "setup-native", "install-module", "--file", str(path),
        "--sha256", "0" * 64, "--gateway-url", "http://127.0.0.1:8088",
    ])

    assert code == 2 and "Gateway API token is required" in err
