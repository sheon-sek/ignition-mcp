from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER_CONFIG = (
    ROOT
    / "tests/harness/phase1-live/gateway-config/com.inductiveautomation.mcp"
    / "server-config/phase1-runtime/config.json"
)


def test_phase1_runtime_server_config_selectors_and_empty_prompt_sources() -> None:
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
    profile = json.loads((ROOT / "contracts/profiles/readonly.yaml").read_text())
    assert profile["prompts"] == []
    assert profile["resources"] == [
        "ignition://?contracts/bundle-info-output",
        "ignition://?contracts/tag-browse-output",
        "ignition://?contracts/tag-read-output",
    ]
