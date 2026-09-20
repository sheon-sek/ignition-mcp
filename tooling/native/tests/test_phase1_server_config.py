from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER_CONFIG = (
    ROOT
    / "tests/harness/phase1-live/gateway-config/com.inductiveautomation.mcp"
    / "server-config/phase1-runtime/config.json"
)


def test_phase1_runtime_server_config_advertises_all_primitive_surfaces() -> None:
    payload = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
    expected = {"project/ignition_runtime": "*"}
    assert payload["tools"] == expected
    assert payload["resources"] == expected
    assert payload["prompts"] == expected

    prompts_root = (
        ROOT
        / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/prompts"
    )
    if prompts_root.exists():
        assert not any(prompts_root.rglob("resource.json"))
