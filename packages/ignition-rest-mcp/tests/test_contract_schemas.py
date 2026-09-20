from __future__ import annotations

import json
from pathlib import Path

from ignition_rest_mcp.models import GatewayDiagnoseResult, GatewayInfoResult

ROOT = Path(__file__).resolve().parents[3]


def _contract_schema(name: str) -> dict[str, object]:
    value = json.loads((ROOT / "contracts" / "schemas" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _assert_shape_matches(model_schema: dict[str, object], contract_schema: dict[str, object]) -> None:
    assert model_schema.get("additionalProperties") is False
    assert contract_schema.get("additionalProperties") is False
    assert set(model_schema["properties"]) == set(contract_schema["properties"])  # type: ignore[arg-type]
    assert set(model_schema["required"]) == set(contract_schema["required"])  # type: ignore[arg-type]


def test_gateway_info_model_matches_contract_shape() -> None:
    _assert_shape_matches(
        GatewayInfoResult.model_json_schema(),
        _contract_schema("gateway-info.output.schema.json"),
    )


def test_gateway_diagnose_model_matches_contract_shape() -> None:
    _assert_shape_matches(
        GatewayDiagnoseResult.model_json_schema(),
        _contract_schema("gateway-diagnose.output.schema.json"),
    )
