"""D29 recorded-Jython coverage for the Tag config fingerprint (Phase 4 ticket #10).

The fingerprint is repo-defined (D30 §2), so its definition is the shared contract
`contracts/shared/tag-config-fingerprint.json` and its golden vectors are what
pin it. These tests run the *shipped* `tag_get_config` handler under Jython 2.7.4
over each vector's recorded native read and require the handler to publish the
vector's own fingerprint: the Python copy of the rule in `tooling/contracts/lint`
and the Jython copy in the handler therefore have to agree, and any change to the
canonical-JSON rule, the D28 encoding or the digest breaks a test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tooling.native.jython_runner import run_recorded_tool

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HANDLER = (
    ROOT
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_get_config"
    / "onToolCalled.py"
)
FINGERPRINT_CONTRACT = ROOT / "contracts/shared/tag-config-fingerprint.json"


def _contract() -> dict:
    return json.loads(FINGERPRINT_CONTRACT.read_text(encoding="utf-8"))


def _vectors() -> list[dict]:
    return _contract()["goldenVectors"]


def test_the_contract_carries_the_vectors_this_module_asserts_against() -> None:
    vectors = _vectors()
    assert [vector["name"] for vector in vectors] == [
        "atomic-tag-as-recorded",
        "explicit-null-property",
        "escaped-reserved-key-object",
        "non-ascii-and-control-characters",
    ]
    assert {vector["fingerprint"].split(":", 1)[0] for vector in vectors} == {_contract()["version"]}
    # Every vector says where it came from, so a rule vector is never mistaken for
    # a captured Gateway body.
    assert all(vector["source"].strip() for vector in vectors)


@pytest.mark.parametrize("vector", _vectors(), ids=[vector["name"] for vector in _vectors()])
def test_the_handler_publishes_the_golden_fingerprint(vector: dict) -> None:
    fixture = FIXTURES / f"tag_get_config-fingerprint-{vector['name']}.json"
    structured = run_recorded_tool("tag_get_config", fixture)["structuredContent"]

    assert structured["fingerprint"] == vector["fingerprint"]
    assert structured["configuration"] == vector["configuration"]


def test_the_fingerprint_is_required_by_the_output_contract() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_get_config.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    assert "fingerprint" in schema["properties"]
    assert "fingerprint" in schema["required"]
    assert schema["properties"]["fingerprint"]["pattern"] == "^tcf1:[0-9a-f]{64}$"

    fixture = FIXTURES / "tag_get_config-fingerprint-atomic-tag-as-recorded.json"
    structured = run_recorded_tool("tag_get_config", fixture)["structuredContent"]
    Draft202012Validator(schema).validate(structured)


def test_a_second_read_of_the_same_configuration_is_the_same_token() -> None:
    """Determinism is the whole point: two identical reads must not disagree."""
    fixture = FIXTURES / "tag_get_config-fingerprint-atomic-tag-as-recorded.json"
    first = run_recorded_tool("tag_get_config", fixture)["structuredContent"]["fingerprint"]
    second = run_recorded_tool("tag_get_config", fixture)["structuredContent"]["fingerprint"]

    assert first == second


def test_handler_is_self_contained_and_tab_indented() -> None:
    source = HANDLER.read_text(encoding="utf-8")
    for line in source.splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = line[: len(line) - len(line.lstrip())]
        assert set(indentation) <= {"\t"}, line
