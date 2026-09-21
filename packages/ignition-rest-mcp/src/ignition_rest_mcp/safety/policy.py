"""D08 deployment mutation policy: class enablement, operation and target
allowlists, and effect-based scope mapping. Deny by default at every layer;
allowing everything requires an explicit ``*`` entry."""

from __future__ import annotations

from dataclasses import dataclass

from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.config import Settings

CONFIG_MUTATION = "CONFIG_MUTATION"
CONTROL_MUTATION = "CONTROL_MUTATION"
ADMIN_MUTATION = "ADMIN_MUTATION"
MUTATION_CLASSES = (CONFIG_MUTATION, CONTROL_MUTATION, ADMIN_MUTATION)

# D07: scope by operation effect, not module/domain.
CLASS_SCOPE = {
    CONFIG_MUTATION: "ignition.config",
    CONTROL_MUTATION: "ignition.control",
    ADMIN_MUTATION: "ignition.admin",
}

WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class MutationOperation:
    op_id: str
    mutation_class: str
    capability: str
    destructive: bool

    def __post_init__(self) -> None:
        if self.mutation_class not in MUTATION_CLASSES:
            raise ValueError("mutation operations must declare a real D08 mutation class")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    layer: str
    reason: str
    error_code: str | None

    @staticmethod
    def allow() -> "PolicyDecision":
        return PolicyDecision(allowed=True, layer="", reason="", error_code=None)


def authorize_scope(principal: VerifiedPrincipal, operation: MutationOperation) -> PolicyDecision:
    required = CLASS_SCOPE[operation.mutation_class]
    if not principal.has_scope(required):
        return PolicyDecision(
            allowed=False, layer="authz-scope", reason=f"missing-scope:{required}",
            error_code="permission_denied",
        )
    return PolicyDecision.allow()


def evaluate_deployment_policy(
    settings: Settings, operation: MutationOperation, target_id: str, capability_present: bool,
) -> PolicyDecision:
    class_enabled = {
        CONFIG_MUTATION: settings.config_mutation_enabled,
        CONTROL_MUTATION: settings.control_mutation_enabled,
        ADMIN_MUTATION: settings.admin_mutation_enabled,
    }[operation.mutation_class]
    if not class_enabled:
        return PolicyDecision(
            allowed=False, layer="deployment-class", reason=f"class-disabled:{operation.mutation_class}",
            error_code="operation_disabled",
        )
    operations = settings.mutation_operations
    if WILDCARD not in operations and operation.op_id not in operations:
        return PolicyDecision(
            allowed=False, layer="operation-allowlist", reason="operation-not-allowlisted",
            error_code="operation_disabled",
        )
    targets = settings.mutation_targets.get(operation.op_id, ())
    if WILDCARD not in targets and target_id not in targets:
        return PolicyDecision(
            allowed=False, layer="target-allowlist", reason="target-not-allowlisted",
            error_code="operation_disabled",
        )
    if not capability_present:
        return PolicyDecision(
            allowed=False, layer="capability", reason=f"capability-missing:{operation.capability}",
            error_code="unsupported_capability",
        )
    return PolicyDecision.allow()
