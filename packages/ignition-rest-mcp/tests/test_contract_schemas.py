from __future__ import annotations

import json
from pathlib import Path

from ignition_rest_mcp.models import (
    AlarmPipelineListResult,
    ArtifactInfoResult,
    ArtifactListResult,
    OperationDiagnoseResult,
    AlarmPipelineStatusResult,
    AuditQueryResult,
    ConfigResourceDescribeResult,
    ConfigResourceGetResult,
    ConfigResourceListResult,
    ConfigResourceNamesResult,
    ConfigResourceSearchResult,
    ConfigResourceUpdateResult,
    GatewayDiagnoseResult,
    GatewayInfoResult,
    ProjectListResult,
    ProjectExportResult,
    ProjectImportResult,
    TagConfigExportResult,
)

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


def test_external_models_match_contract_top_level_shapes() -> None:
    cases = (
        (GatewayInfoResult, "gateway-info.output.schema.json"),
        (GatewayDiagnoseResult, "gateway-diagnose.output.schema.json"),
        (ProjectListResult, "project-list.output.schema.json"),
        (ConfigResourceSearchResult, "config-resource-search.output.schema.json"),
        (ConfigResourceDescribeResult, "config-resource-describe.output.schema.json"),
        (ConfigResourceNamesResult, "config-resource-names.output.schema.json"),
        (ConfigResourceListResult, "config-resource-list.output.schema.json"),
        (ConfigResourceGetResult, "config-resource-get.output.schema.json"),
        (ConfigResourceUpdateResult, "config-resource-update.output.schema.json"),
        (AuditQueryResult, "audit-query.output.schema.json"),
        (AlarmPipelineListResult, "alarm-pipeline-list.output.schema.json"),
        (AlarmPipelineStatusResult, "alarm-pipeline-status.output.schema.json"),
        (ProjectExportResult, "project-export.output.schema.json"),
        (ProjectImportResult, "project-import.output.schema.json"),
        (TagConfigExportResult, "tag-config-export.output.schema.json"),
        (ArtifactListResult, "artifact-list.output.schema.json"),
        (ArtifactInfoResult, "artifact-info.output.schema.json"),
        (OperationDiagnoseResult, "operation-diagnose.output.schema.json"),
    )
    for model, schema_name in cases:
        _assert_shape_matches(model.model_json_schema(), _contract_schema(schema_name))


def test_public_collection_models_preserve_contract_item_caps() -> None:
    cases = (
        (ProjectListResult, "project-list.output.schema.json"),
        (ArtifactListResult, "artifact-list.output.schema.json"),
        (ConfigResourceSearchResult, "config-resource-search.output.schema.json"),
        (ConfigResourceNamesResult, "config-resource-names.output.schema.json"),
        (ConfigResourceListResult, "config-resource-list.output.schema.json"),
        (AuditQueryResult, "audit-query.output.schema.json"),
        (AlarmPipelineListResult, "alarm-pipeline-list.output.schema.json"),
        (AlarmPipelineStatusResult, "alarm-pipeline-status.output.schema.json"),
    )
    for model, schema_name in cases:
        model_items = model.model_json_schema()["properties"]["items"]
        contract_items = _contract_schema(schema_name)["properties"]["items"]  # type: ignore[index]
        assert model_items["maxItems"] == contract_items["maxItems"] == 500  # type: ignore[index]
