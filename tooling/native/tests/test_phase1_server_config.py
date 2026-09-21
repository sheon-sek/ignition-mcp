"""Harness Server Configs must select a profile's explicit Tool inventory (D09).

The pinned Module documents the config's ``tools`` mapping as
``"providerId": "[tool1, tool2]"`` with a wildcard as the alternative. A wildcard
serves every Tool in the deployed project, so once one bundle carries a Mutation
Tool a read-only deployment would expose it: the selection, not the bundle, has to
name the profile. These checks keep that property from drifting silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GATEWAY_CONFIG = "com.inductiveautomation.mcp/server-config"

#: Harness Server Config -> the profile whose exact Tool list it must select. The
#: Phase 4 harness deploys two: the CONTROL profile (tickets #7/#8) and the CONFIG
#: profile (ticket #10). A deployment's name says which profile it selects, so the
#: expectation is per Server Config, not per harness directory.
PROFILE_SELECTIONS = {
    "tests/harness/phase1-live": "readonly",
    "tests/harness/phase2-live": "readonly",
    "tests/harness/phase3-live": "readonly",
    "tests/harness/phase4-live": None,
}
#: The Phase 4 Server Configs, each with the profile it must select.
PHASE4_SELECTIONS = {
    "phase4-operator": "operator",
    "phase4-configurator": "configurator",
}


def _profile_tools(profile: str) -> list[str]:
    document = json.loads((ROOT / f"contracts/profiles/{profile}.yaml").read_text(encoding="utf-8"))
    return list(document["tools"])


def _configs(harness: str) -> list[Path]:
    return sorted((ROOT / harness / "gateway-config" / GATEWAY_CONFIG).glob("*/config.json"))


def test_every_harness_server_config_selects_an_explicit_tool_list() -> None:
    checked = 0
    for harness, profile in PROFILE_SELECTIONS.items():
        expected = _profile_tools(profile) if profile else []
        for path in _configs(harness):
            document = json.loads(path.read_text(encoding="utf-8"))
            selection = document["tools"]
            assert isinstance(selection, dict) and len(selection) == 1, path
            project, tools = next(iter(selection.items()))
            if project.startswith("project/mcp_"):
                # A probe project hosts characterization Tools, not a profile.
                assert tools == "*", (path, "a probe project may keep the wildcard")
                continue
            if profile is None:
                # Phase 4 deploys one Server Config per profile it verifies.
                assert path.parent.name in PHASE4_SELECTIONS, path
                expected = _profile_tools(PHASE4_SELECTIONS[path.parent.name])
            assert tools != "*", (path, "a product deployment must not use the Tool wildcard")
            assert tools == expected, (path, profile, set(expected) ^ set(tools))
            checked += 1
    assert checked >= 3, "expected the read-only harnesses and each Phase 4 profile to be checked"


def test_the_phase4_operator_selection_is_the_operator_profile() -> None:
    """The ticket #7 inventory check compares the endpoint to this profile, so the
    config it deploys must select exactly that list."""
    phase4 = _configs("tests/harness/phase4-live")
    operator = [path for path in phase4 if "phase4-operator" in str(path)]
    assert len(operator) == 1
    document = json.loads(operator[0].read_text(encoding="utf-8"))
    selection = next(iter(document["tools"].values()))
    assert selection == _profile_tools("operator")
    assert "tag_write" in selection
    assert "tag_write" not in _profile_tools("readonly")


def test_the_readonly_selection_excludes_every_mutation_tool() -> None:
    from tooling.contracts.lint import CURRENT_RUNTIME_MUTATION_TOOLS

    readonly = _profile_tools("readonly")
    assert set(readonly).isdisjoint(CURRENT_RUNTIME_MUTATION_TOOLS)
    for path in _configs("tests/harness/phase1-live"):
        document = json.loads(path.read_text(encoding="utf-8"))
        for tools in document["tools"].values():
            if tools != "*":
                assert set(tools).isdisjoint(CURRENT_RUNTIME_MUTATION_TOOLS)


@pytest.mark.parametrize("harness", sorted(PROFILE_SELECTIONS))
def test_harness_configs_parse_and_carry_the_security_level(harness: str) -> None:
    for path in _configs(harness):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["title"] and document["version"]
        if "mcp_" not in str(path):
            assert document["permissions"]["securityLevels"], path
