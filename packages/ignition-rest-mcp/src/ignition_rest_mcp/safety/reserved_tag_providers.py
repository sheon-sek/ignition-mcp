"""D30 §1's reserved Tag provider: where the Runtime Target Policy is stored.

The Runtime Target Policy lives in a dedicated ``IgnitionMCPPolicy`` Tag provider
(the location ticket #6 characterized: ``[IgnitionMCPPolicy]RuntimeTargetPolicy``
plus its companion length Tag). D30 §1 requires that the Runtime MCP server cannot
write it, and a Jython handler's scope is not a security boundary — a handler can
write inside that provider — so the boundary is a product rule.

Every Tag Mutation therefore refuses a Target that resolves inside the reserved
provider *before* the Target allowlist is consulted, exactly as D30 §5 makes a
Refused resource type refuse even under ``*``. That covers the policy Tag itself,
its companion, and anything else a deployment puts there, and it also stops a
deployment from widening the policy by allowlisting the provider explicitly.
"""

from __future__ import annotations

from ignition_rest_mcp.safety.policy import PolicyDecision

#: The provider `setup-native apply` creates to hold the Runtime Target Policy.
#: Provider names are compared case-insensitively: a Tag path that differs from the
#: reserved name only in case addresses the same provider.
RESERVED_TAG_PROVIDERS = ("IgnitionMCPPolicy",)


def reserved_tag_provider(provider: str) -> str | None:
    """The reserved provider name one Tag provider addresses, or ``None`` (D30 §1)."""

    folded = provider.strip().casefold()
    for reserved in RESERVED_TAG_PROVIDERS:
        if folded == reserved.casefold():
            return reserved
    return None


def is_reserved_tag_provider(provider: str) -> bool:
    """Whether one Tag provider name is the reserved policy provider (D30 §1)."""

    return reserved_tag_provider(provider) is not None


def refuse_reserved_tag_provider_decision(provider: str) -> PolicyDecision:
    """The pre-dispatch decision for one Tag Mutation's provider (D30 §1).

    The denial names the *reserved* provider rather than the caller's spelling of it,
    so the recorded reason is one value however the request was cased.
    """

    reserved = reserved_tag_provider(provider)
    if reserved is None:
        return PolicyDecision.allow()
    return PolicyDecision(
        allowed=False, layer="target-class",
        reason=f"reserved-tag-provider:{reserved}", error_code="permission_denied",
    )
