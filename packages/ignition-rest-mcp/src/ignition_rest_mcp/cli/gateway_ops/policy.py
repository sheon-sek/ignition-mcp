"""Confirming that the served Runtime Target Policy matches the document written (D20).

The Gateway's Tag import is asynchronous, so a write the Gateway accepted can read
back before the value lands. A mismatch is repaired once with a single idempotent
re-import, and a second mismatch is an error rather than a loop.
"""

from __future__ import annotations

from typing import Any

from ignition_rest_mcp.cli.gateway_ops import documents as docs
from ignition_rest_mcp.cli.gateway_ops.writer import GatewayWriter, WriteError


async def confirm_policy(writer: GatewayWriter, documents: docs.Documents) -> tuple[bool, str]:
    """Read the served policy back; repair once with an idempotent re-import if it disagrees."""

    assert documents.policy_text is not None
    want = documents.policy_text
    mismatch = policy_mismatch(await writer.export_policy(), want)
    if mismatch == "":
        return False, str(docs.byte_length(want))
    await writer.import_policy(want, first_policy="MergeOverwrite")
    document = await writer.export_policy()
    mismatch = policy_mismatch(document, want)
    if mismatch != "":
        raise WriteError(f"the served Runtime Target Policy does not match the document this run wrote ({mismatch})")
    return True, str(docs.byte_length(want))


def policy_mismatch(document: dict[str, Any], want: str) -> str:
    """How the served policy differs from the desired one; ``""`` when it matches."""

    tag = docs.find_tag(document, docs.TAG_NAME)
    value = tag.get("value") if isinstance(tag, dict) else None
    if not isinstance(value, str):
        return "the provider serves no RuntimeTargetPolicy Tag value"
    if value != want:
        return (
            f"served {docs.byte_length(value)} bytes (sha256 {docs.sha256_text(value)[:16]}...), "
            f"wanted {docs.byte_length(want)} bytes (sha256 {docs.sha256_text(want)[:16]}...)"
        )
    length = docs.find_tag(document, docs.LENGTH_TAG_NAME)
    declared = length.get("value") if isinstance(length, dict) else None
    expected = docs.byte_length(want)
    if declared != expected:
        return f"the declared length Tag reads back {declared!r}, not {expected}"
    return ""
