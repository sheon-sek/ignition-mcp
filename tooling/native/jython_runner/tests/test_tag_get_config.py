"""D29 recorded-Jython coverage for the `tag_get_config` UDT definition read.

D30 §6 lets a Tag CONFIG Mutation target a UDT definition under an explicit
`_types_` allowlist entry, and every such Mutation needs the `tcf1` token its own
`tag_get_config` read published. The read therefore has to be able to answer for
one exact definition path — a recursive read of the definition namespace stays
`invalid_argument`, because `udt_type_get` owns the subtree view.
"""

from __future__ import annotations

import json
from pathlib import Path

from tooling.native.jython_runner import run_recorded_tool, run_recorded_tool_error

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"


def _fixture(name: str) -> Path:
    return FIXTURES / f"{name}.json"


def test_an_exact_udt_definition_read_publishes_the_token_a_mutation_compares() -> None:
    structured = run_recorded_tool(
        "tag_get_config", _fixture("tag_get_config-udt-definition-exact-read")
    )["structuredContent"]

    assert structured["path"] == UDT
    assert structured["recursive"] is False
    assert structured["fingerprint"].startswith("tcf1:")
    assert structured["configuration"][0]["tagType"] == "UdtType"


def test_a_recursive_udt_definition_read_is_still_refused() -> None:
    # The fixture records no native call, so the runner fails the run if the
    # handler reads the definitions namespace before refusing the request.
    error = run_recorded_tool_error(
        "tag_get_config", _fixture("tag_get_config-udt-definition-recursive-refused"),
        expected_code="invalid_argument",
    )

    assert "invalid_argument" == error["code"]


def test_the_read_token_and_the_update_it_authorizes_agree() -> None:
    """The whole point of the exact definition read: the token it publishes is the
    token a `tag_update` on the same definition accepts."""
    read = run_recorded_tool(
        "tag_get_config", _fixture("tag_get_config-udt-definition-exact-read")
    )["structuredContent"]
    fixture = json.loads(_fixture("tag_update-udt-definition-with-read-token").read_text(encoding="utf-8"))
    items = fixture["arguments"]["items"]

    assert [entry["expectedFingerprint"] for entry in items] == [read["fingerprint"]]
    structured = run_recorded_tool("tag_update", _fixture("tag_update-udt-definition-with-read-token"))[
        "structuredContent"
    ]
    assert [entry["status"] for entry in structured["items"]] == ["executed"]
    assert structured["summary"]["succeeded"] == 1


def test_a_folder_named_types_below_the_provider_is_not_the_definition_namespace() -> None:
    """D30 6 names `[provider]_types_/...`. Only the first post-provider segment
    selects the definition namespace, so a recursive read of an ordinary folder
    called `_types_` is not the definition read and is not refused as one."""
    structured = run_recorded_tool(
        "tag_get_config", _fixture("tag_get_config-nested-folder-types-read")
    )["structuredContent"]

    assert structured["path"] == "[default]IgnitionMCP_CI/_types_/Probe"
    assert structured["recursive"] is True
    assert structured["summary"]["returned"] == 2


def test_the_contract_separates_the_exact_definition_read_from_the_subtree_view() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_get_config.contract.json").read_text(encoding="utf-8")
    )
    declaration = contract["parameters"]["path"]["udtDefinitionNamespace"]

    assert declaration is not False
    assert "exact" in declaration
    assert "recursive" in declaration
