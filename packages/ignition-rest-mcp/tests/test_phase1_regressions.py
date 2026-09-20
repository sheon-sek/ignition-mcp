from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from fastmcp import Client

from ignition_rest_mcp.auth import build_auth, operation_actor
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import ConfigurationError
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.server import create_server
from ignition_rest_mcp.services.gateway import gateway_diagnose
from test_config import _settings


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *, delay: float = 0) -> None:
        self.delay = delay
        self.reads = 0
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for _ in range(100):
            await asyncio.sleep(self.delay)
            self.reads += 1
            yield b"x" * 100

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("delay,limit,code", [(0, 150, "limit_exceeded"), (0.01, 10000, "timeout")])
def test_stream_budget_and_elapsed_timeout(delay: float, limit: int, code: str) -> None:
    async def scenario() -> None:
        stream = Chunks(delay=delay)
        client = GatewayClient(
            base_url="http://gateway", api_token="service:secret", timeout_seconds=0.035,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
        )
        try:
            with pytest.raises(GatewayError) as caught:
                await client.get_bytes("/x", limit_bytes=limit)
            assert caught.value.code == code
            assert stream.reads < 100
            assert stream.closed
        finally:
            await client.aclose()
    asyncio.run(scenario())


def test_transport_cancellation_closes_stream() -> None:
    async def scenario() -> None:
        stream = Chunks(delay=0.01)
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            return httpx.Response(200, stream=stream)

        client = GatewayClient(base_url="http://gateway", api_token="key", timeout_seconds=1,
                               transport=httpx.MockTransport(handler))
        task = asyncio.create_task(client.get_bytes("/x"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
        await client.aclose()
    asyncio.run(scenario())


def test_singleflight_and_waiter_cancellation() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            path = request.url.path
            payload = {"paths": {}} if path == "/openapi.json" else (
                {"items": []} if path.endswith("healthy") else {"ignitionVersion": "8.3.8"}
            )
            return httpx.Response(200, json=payload)

        client = GatewayClient(base_url="http://gateway", api_token="key", timeout_seconds=1,
                               transport=httpx.MockTransport(handler))
        registry = CapabilityRegistry(client)
        tasks = [asyncio.create_task(registry.refresh()) for _ in range(5)]
        await entered.wait()
        tasks[0].cancel()
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert all(result is results[1] for result in results[1:])
        assert calls == 3
        assert registry.snapshot.generation == 1
        await registry.aclose()
        await client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["network", "503", "malformed", "missing-fields"])
def test_diagnose_does_not_claim_authentication_on_failure(mode: str) -> None:
    async def scenario() -> None:
        context = OperationContext.read("gateway_diagnose", "verified-subject")

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Correlation-ID"] == context.correlation_id
            assert request.headers["X-Ignition-API-Token"] == "service-secret"
            assert "authorization" not in request.headers
            if mode == "network":
                raise httpx.ConnectError("secret", request=request)
            if mode == "503":
                return httpx.Response(503)
            if mode == "malformed":
                return httpx.Response(200, content=b"not JSON")
            return httpx.Response(200, json={})

        client = GatewayClient(base_url="http://gateway", api_token="service-secret", timeout_seconds=1,
                               transport=httpx.MockTransport(handler))
        result = await gateway_diagnose(client, CapabilityRegistry(client), context)
        assert result.authenticationOk is False
        assert result.correlationId == context.correlation_id
        await client.aclose()
    asyncio.run(scenario())


def test_secured_configuration_and_verified_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(deployment_profile="secured", auth_mode="jwt", jwt_jwks_uri="https://idp/keys",
                         jwt_issuer="https://idp", jwt_audience="ignition-rest")
    settings.validate()
    assert build_auth(settings) is not None
    with pytest.raises(ConfigurationError):
        replace(settings, jwt_audience=None).validate()
    with pytest.raises(ConfigurationError):
        replace(settings, auth_mode="none").validate()
    from fastmcp.server.auth import AccessToken
    monkeypatch.setattr("ignition_rest_mcp.auth.get_access_token", lambda: AccessToken(
        token="caller-secret", client_id="client", subject="subject", scopes=["ignition.read"],
    ))
    assert operation_actor(settings) == "subject"


@pytest.mark.parametrize("change", ["valid", "expired", "no-expiry", "wrong-audience", "wrong-issuer", "admin-only"])
def test_jwt_signature_claims_and_scope(change: str) -> None:
    import time

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    claims: dict[str, object] = {"iss": "issuer", "aud": "audience", "sub": "alice",
                                 "exp": int(time.time()) + 60, "scope": "ignition.read"}
    if change == "expired":
        claims["exp"] = int(time.time()) - 60
    elif change == "no-expiry":
        del claims["exp"]
    elif change == "wrong-audience":
        claims["aud"] = "other"
    elif change == "wrong-issuer":
        claims["iss"] = "other"
    elif change == "admin-only":
        claims["scope"] = "ignition.admin"
    token = jwt.encode({"alg": "RS256"}, claims, RSAKey.import_key(pem))
    settings = _settings(deployment_profile="secured", auth_mode="jwt", jwt_public_key=public,
                         jwt_issuer="issuer", jwt_audience="audience")
    settings.validate()
    verifier = build_auth(settings)
    assert verifier is not None
    result = asyncio.run(verifier.verify_token(token))
    assert (result is not None) == (change == "valid")


def test_tool_and_resources_budget_errors_are_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async def info(self: GatewayClient, context: OperationContext | None = None) -> dict[str, str]:
            return {"ignitionVersion": "8.3.8"}

        async def modules(self: GatewayClient, context: OperationContext | None = None) -> dict[str, object]:
            return {"items": []}

        async def openapi(self: GatewayClient) -> bytes:
            return b'{"paths": {}}'

        monkeypatch.setattr(GatewayClient, "gateway_info", info)
        monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
        monkeypatch.setattr(GatewayClient, "openapi", openapi)
        recorded: list[tuple[str, str]] = []
        monkeypatch.setattr("ignition_rest_mcp.observability.metrics.Metrics.record_tool",
                            lambda self, tool, outcome: recorded.append((tool, outcome)))
        server = create_server(_settings(structured_output_limit_bytes=1))
        async with Client(server) as client:
            result = await client.call_tool("gateway_diagnose", {}, raise_on_error=False)
            assert result.is_error
            assert "limit_exceeded" in str(result.content)
            assert "correlationId=" in str(result.content)
            assert recorded == [("gateway_diagnose", "limit_exceeded")]
            for uri in ["ignition://gateway/capabilities", "ignition://gateway/openapi-info"]:
                with pytest.raises(Exception, match="limit_exceeded"):
                    await client.read_resource(uri)
    asyncio.run(scenario())
