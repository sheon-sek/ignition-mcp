"""D30 §5 / owner ruling 4: the reserved Runtime Target Policy config resource.

The Runtime Target Policy lives in the Tag provider ``IgnitionMCPPolicy``
(``[IgnitionMCPPolicy]RuntimeTargetPolicy``, ticket #6's characterized location). D30 §1
keeps the Runtime plane out of it and refuses every Tag Mutation addressed to that
provider; the *config* resource that *is* that provider is the other way into the same
storage, so the owner ruled (D30 ``Owner rulings``, item 4) that generic config Mutations
refuse it too.

This is a refusal **by name within an allowed type**, not an entry in D30 §5's Refused
resource types:

- ``ignition/tag-provider`` stays classified as allowed, and every other Tag provider
  stays manageable through ``config_resource_*``;
- the name is compared as a whole, exactly like D30 §1's provider-component rule — a
  longer name such as ``IgnitionMCPPolicyStaging`` is a different resource;
- the comparison folds case and trims surrounding whitespace, because Ignition documents
  no rule for how two config resource names compare, and a name that differs only in case
  could address the same resource. Fail closed, and the refusal names the *reserved*
  spelling so the recorded reason is one value however the caller wrote it.

The rule is a *target-class* rule for the config Mutations, so the guarded executor
evaluates it before the Target allowlist: no ``*`` and no entry naming the reserved
resource can reach it. It covers a rename's source *and* its destination, because a
rename produces a resource at its destination as well as changing its source (D30 §3).
``config_resource_get`` is untouched: the refusal is Mutation-only.
"""

from __future__ import annotations

from ignition_rest_mcp.safety.policy import PolicyDecision

#: The resource type the reserved name lives in. It is an *allowed* type (D30 §5): the
#: refusal below is about one name inside it, and nothing here changes that
#: classification.
RESERVED_CONFIG_RESOURCE_TYPE = "ignition/tag-provider"

#: The config resource that holds the Runtime Target Policy (#6, D30 §1). It is the
#: ``ignition/tag-provider`` resource named exactly this, in the ``core`` collection.
RESERVED_CONFIG_RESOURCES = (RESERVED_CONFIG_RESOURCE_TYPE, "IgnitionMCPPolicy")


def reserved_config_resource(resource_type: str, name: str) -> str | None:
    """The reserved resource one config Target addresses, or ``None`` (D30 ruling 4).

    The identity is the exact ``<resourceType>/<name>`` of the Target, which is what the
    caller supplies: the type is compared as the Gateway's own catalogued form, and the
    name as a whole, case-folded.
    """

    reserved_type, reserved_name = RESERVED_CONFIG_RESOURCES
    if resource_type.strip() != reserved_type:
        return None
    if name.strip().casefold() != reserved_name.casefold():
        return None
    return f"{reserved_type}/{reserved_name}"


def reserved_config_resource_decision(resource_type: str, name: str) -> PolicyDecision:
    """The pre-dispatch decision for one config Target's type and name (D30 ruling 4).

    It is the operation's Target-class rule beside D30 §5's Refused resource types, so it
    runs at the same point in the D08 chain — before the Target allowlist — and carries
    the same ``permission_denied`` code (D30 §7).
    """

    reserved = reserved_config_resource(resource_type, name)
    if reserved is None:
        return PolicyDecision.allow()
    return PolicyDecision(
        allowed=False, layer="target-class",
        reason=f"reserved-config-resource:{reserved}", error_code="permission_denied",
    )
