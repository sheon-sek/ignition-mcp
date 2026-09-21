"""Phase 4 milestone 4c (ticket #13): named static tokens with per-token scopes.

D07's Phase 4 amendment: `static-token` supports several **named** static
tokens, each with its own deployment-configured scope set drawn from the four
canonical scopes, with no hierarchy. The token's name is its Mutation principal
in audit and operation records; the token value never appears in logs, audit,
errors or metrics. `auth=none` stays `ignition.read` only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ignition_rest_mcp.auth import build_auth, principal_from_token
from ignition_rest_mcp.config import (
    CANONICAL_SCOPES,
    LEGACY_STATIC_TOKEN_NAME,
    MAX_STATIC_TOKENS,
    ConfigurationError,
    Settings,
    StaticToken,
)
from test_config import _settings

ROOT = Path(__file__).resolve().parents[3]

READ = "ignition.read"
CONFIG = "ignition.config"
CONTROL = "ignition.control"
ADMIN = "ignition.admin"


def _verify(settings: Settings, value: str) -> Any:
    async def scenario() -> Any:
        verifier = build_auth(settings)
        assert verifier is not None
        return await verifier.verify_token(value)

    return asyncio.run(scenario())


def test_each_named_token_verifies_to_its_own_scopes() -> None:
    settings = _settings(auth_mode="static-token", static_tokens=(
        StaticToken(name="reader", token="read-secret", scopes=(READ,)),
        StaticToken(name="configurator", token="cfg-secret", scopes=(READ, CONFIG)),
    ))
    settings.validate()

    assert _verify(settings, "nope") is None
    reader = _verify(settings, "read-secret")
    configurator = _verify(settings, "cfg-secret")
    assert (reader.client_id, reader.subject, reader.scopes) == ("reader", "reader", [READ])
    assert (configurator.client_id, configurator.subject, configurator.scopes) == (
        "configurator", "configurator", [READ, CONFIG],
    )


def test_verification_never_retains_the_token_value() -> None:
    settings = _settings(auth_mode="static-token", static_tokens=(
        StaticToken(name="reader", token="top-secret", scopes=(READ,)),
    ))
    settings.validate()
    verifier = build_auth(settings)
    assert verifier is not None
    access = asyncio.run(verifier.verify_token("top-secret"))

    assert access is not None and access.token == ""
    assert "top-secret" not in repr(verifier)
    assert "top-secret" not in repr(settings)
    assert "top-secret" not in json.dumps(access.claims)


def test_token_name_is_the_principal_and_scopes_have_no_hierarchy() -> None:
    settings = _settings(auth_mode="static-token", static_tokens=(
        StaticToken(name="config-agent", token="cfg-secret", scopes=(CONFIG,)),
    ))
    settings.validate()

    principal = principal_from_token(settings, _verify(settings, "cfg-secret"))
    assert principal.key == "static-token:config-agent"
    assert principal.has_scope(CONFIG)
    assert not principal.has_scope(READ), "scopes have no implicit hierarchy"


def test_auth_none_principal_stays_read_only() -> None:
    settings = _settings(auth_mode="none", static_tokens=())
    settings.validate()

    principal = principal_from_token(settings, None)
    assert principal.scopes == frozenset({READ})
    assert not principal.has_scope(CONFIG)


def test_legacy_single_token_keeps_the_phase3_principal_key() -> None:
    """The legacy env var is one named token whose name preserves G3 attribution."""

    assert LEGACY_STATIC_TOKEN_NAME == "trusted-internal-static-token"
    settings = _settings(auth_mode="static-token", static_tokens=(
        StaticToken(name=LEGACY_STATIC_TOKEN_NAME, token="legacy-secret", scopes=(READ,)),
    ))
    settings.validate()
    assert principal_from_token(settings, _verify(settings, "legacy-secret")).key == (
        "static-token:trusted-internal-static-token"
    )


@pytest.mark.parametrize(("tokens", "message"), [
    ((StaticToken(name="a", token="x", scopes=("ignition.write",)),), "unknown scope"),
    ((StaticToken(name="a", token="x", scopes=()),), "at least one scope"),
    ((StaticToken(name="a", token="x", scopes=(READ, READ)),), "duplicate scope"),
    ((StaticToken(name="a b", token="x", scopes=(READ,)),), "token name"),
    ((StaticToken(name="", token="x", scopes=(READ,)),), "token name"),
    ((StaticToken(name="a", token="", scopes=(READ,)),), "token value"),
    ((StaticToken(name="a", token="x", scopes=(READ,)),
      StaticToken(name="a", token="y", scopes=(READ,))), "duplicate token name"),
    ((StaticToken(name="a", token="x", scopes=(READ,)),
      StaticToken(name="b", token="x", scopes=(READ,))), "share one token value"),
])
def test_static_token_configuration_fails_closed(
    tokens: tuple[StaticToken, ...], message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        _settings(auth_mode="static-token", static_tokens=tokens).validate()


def test_static_token_mode_requires_a_configured_token() -> None:
    with pytest.raises(ConfigurationError, match="requires at least one"):
        _settings(auth_mode="static-token", static_tokens=()).validate()


def test_static_tokens_require_static_token_mode() -> None:
    tokens = (StaticToken(name="a", token="x", scopes=(READ,)),)
    with pytest.raises(ConfigurationError, match="IGNITION_MCP_AUTH_MODE"):
        _settings(auth_mode="none", static_tokens=tokens).validate()


def test_static_token_count_is_bounded() -> None:
    tokens = tuple(
        StaticToken(name=f"token-{index}", token=f"value-{index}", scopes=(READ,))
        for index in range(MAX_STATIC_TOKENS + 1)
    )
    with pytest.raises(ConfigurationError, match="at most"):
        _settings(auth_mode="static-token", static_tokens=tokens).validate()


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **values: str) -> None:
    monkeypatch.setenv("IGNITION_MCP_GATEWAY_API_TOKEN", "name:key")
    monkeypatch.setenv("IGNITION_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IGNITION_MCP_AUTH_MODE", "static-token")
    for name in ("IGNITION_MCP_STATIC_TOKEN", "IGNITION_MCP_STATIC_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_env_configures_several_named_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _env(monkeypatch, tmp_path, IGNITION_MCP_STATIC_TOKENS=json.dumps({
        "reader": {"token": "read-secret", "scopes": [READ]},
        "configurator": {"token": "cfg-secret", "scopes": [READ, CONFIG]},
    }))
    settings = Settings.from_env()

    assert settings.static_tokens == (
        StaticToken(name="reader", token="read-secret", scopes=(READ,)),
        StaticToken(name="configurator", token="cfg-secret", scopes=(READ, CONFIG)),
    )


def test_env_single_token_form_is_one_read_only_named_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _env(monkeypatch, tmp_path, IGNITION_MCP_STATIC_TOKEN="legacy-secret")
    settings = Settings.from_env()

    assert settings.static_tokens == (
        StaticToken(name=LEGACY_STATIC_TOKEN_NAME, token="legacy-secret", scopes=(READ,)),
    )


def test_env_rejects_both_static_token_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _env(
        monkeypatch, tmp_path, IGNITION_MCP_STATIC_TOKEN="legacy-secret",
        IGNITION_MCP_STATIC_TOKENS=json.dumps({"reader": {"token": "read-secret", "scopes": [READ]}}),
    )
    with pytest.raises(ConfigurationError, match="not both"):
        Settings.from_env()


@pytest.mark.parametrize("raw", [
    "not json",
    '"a string"',
    '{"reader": "read-secret"}',
    '{"reader": {"token": "read-secret"}}',
    '{"reader": {"token": 1, "scopes": ["ignition.read"]}}',
    '{"reader": {"token": "read-secret", "scopes": "ignition.read"}}',
    '{"reader": {"token": "read-secret", "scopes": [1]}}',
])
def test_env_rejects_malformed_static_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str,
) -> None:
    _env(monkeypatch, tmp_path, IGNITION_MCP_STATIC_TOKENS=raw)
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_env_rejects_an_oversize_static_tokens_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _env(monkeypatch, tmp_path, IGNITION_MCP_STATIC_TOKENS=json.dumps({
        "reader": {"token": "read-secret", "scopes": [READ]}, "pad": {"token": "x" * 40_000, "scopes": [READ]},
    }))
    with pytest.raises(ConfigurationError, match="32 KiB"):
        Settings.from_env()


def test_canonical_scopes_match_the_shared_permission_contract() -> None:
    contract = json.loads((ROOT / "contracts/shared/permission-classes.json").read_text(encoding="utf-8"))
    external = [entry["externalScope"] for entry in contract["classes"].values()]

    assert sorted(CANONICAL_SCOPES) == sorted(external)
    assert frozenset({READ, CONFIG, CONTROL, ADMIN}) == frozenset(CANONICAL_SCOPES)
