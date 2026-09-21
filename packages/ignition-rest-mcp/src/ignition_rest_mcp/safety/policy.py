"""D08 deployment mutation policy: class enablement, operation and target
allowlists, and effect-based scope mapping. Deny by default at every layer;
allowing everything requires an explicit ``*`` entry."""

from __future__ import annotations

from dataclasses import dataclass

from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.config import ADMIN_SCOPE, CONFIG_SCOPE, CONTROL_SCOPE, Settings

CONFIG_MUTATION = "CONFIG_MUTATION"
CONTROL_MUTATION = "CONTROL_MUTATION"
ADMIN_MUTATION = "ADMIN_MUTATION"
MUTATION_CLASSES = (CONFIG_MUTATION, CONTROL_MUTATION, ADMIN_MUTATION)

# D07: scope by operation effect, not module/domain.
CLASS_SCOPE = {
    CONFIG_MUTATION: CONFIG_SCOPE,
    CONTROL_MUTATION: CONTROL_SCOPE,
    ADMIN_MUTATION: ADMIN_SCOPE,
}

WILDCARD = "*"

#: D06 codes a Target-allowlist denial may carry: D30 §7 decides
#: ``permission_denied`` for the Phase 4 Mutations, and the Phase 3 machinery that
#: predates that decision records ``operation_disabled``.
TARGET_DENIAL_CODES = ("operation_disabled", "permission_denied")


@dataclass(frozen=True, slots=True)
class MutationOperation:
    op_id: str
    mutation_class: str
    capability: str
    destructive: bool
    #: The D06 code a Target-allowlist denial carries for this operation. D30 §7
    #: decides `permission_denied` for the Phase 4 Mutations; the Phase 3 machinery
    #: that shipped before that decision keeps its recorded `operation_disabled`,
    #: and its frozen G3 evidence stays valid because the code is per operation.
    target_denial_code: str = "operation_disabled"

    def __post_init__(self) -> None:
        if self.mutation_class not in MUTATION_CLASSES:
            raise ValueError("mutation operations must declare a real D08 mutation class")
        if self.target_denial_code not in TARGET_DENIAL_CODES:
            raise ValueError(f"unknown Target denial code: {self.target_denial_code}")


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
    *,
    target_class: PolicyDecision | None = None,
) -> PolicyDecision:
    """The deployment-side checks, in D08's order.

    Class enablement, then the operation allowlist, then the operation's own
    Target-class rule (D30 §5 Refused resource types — evaluated *before* the
    Target allowlist, so a refused type is denied even under ``*``), then the
    Target allowlist, then the capability.
    """

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
    if target_class is not None and not target_class.allowed:
        return target_class
    targets = settings.mutation_targets.get(operation.op_id, ())
    if WILDCARD not in targets and target_id not in targets:
        return PolicyDecision(
            allowed=False, layer="target-allowlist", reason="target-not-allowlisted",
            error_code=operation.target_denial_code,
        )
    if not capability_present:
        return PolicyDecision(
            allowed=False, layer="capability", reason=f"capability-missing:{operation.capability}",
            error_code="unsupported_capability",
        )
    return PolicyDecision.allow()
