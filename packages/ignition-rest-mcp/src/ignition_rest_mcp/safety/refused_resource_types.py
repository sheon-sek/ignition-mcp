"""D30 §5 Refused resource types.

Generic config Mutations never touch these Gateway config resource types, whatever
the Target allowlist says. The rule is an *allowlist*: only a type this contract
classifies as allowed may be changed, so a type added by a new Gateway version is
refused until it is classified. ``contracts/shared/refused-resource-types.json``
is the semantic source of truth, and a test compares the two exactly.
"""

from __future__ import annotations

from ignition_rest_mcp.safety.policy import PolicyDecision

#: Resource types a generic config Mutation may change.
ALLOWED_RESOURCE_TYPES = frozenset({
    "com.inductiveautomation.alarm-notification/alarm-notification-profile",
    "com.inductiveautomation.eam/agent-group",
    "com.inductiveautomation.eam/agent-management",
    "com.inductiveautomation.eam/eam-tasks",
    "com.inductiveautomation.eam/event-thresholds",
    "com.inductiveautomation.historian/historian-provider",
    "com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig",
    "com.inductiveautomation.opcua/access-control",
    "com.inductiveautomation.opcua/device",
    "com.inductiveautomation.opcua/server-config",
    "com.inductiveautomation.perspective/fonts",
    "com.inductiveautomation.perspective/icons",
    "com.inductiveautomation.perspective/themes",
    "com.inductiveautomation.sfc/chart-settings",
    "com.inductiveautomation.sip-notification/script-settings",
    "ignition/alarm-journal",
    "ignition/audit-profile",
    "ignition/cobranding",
    "ignition/database-connection",
    "ignition/database-driver",
    "ignition/database-translator",
    "ignition/email-profile",
    "ignition/gateway-network-incoming",
    "ignition/gateway-network-outgoing",
    "ignition/gateway-network-proxy-rules",
    "ignition/gateway-network-queue-settings",
    "ignition/gateway-network-settings",
    "ignition/general-alarm-settings",
    "ignition/holiday",
    "ignition/keyboard_layout",
    "ignition/metrics-dashboard",
    "ignition/opc-connection",
    "ignition/quickstart",
    "ignition/roster-config",
    "ignition/schedule",
    "ignition/service-connector",
    "ignition/store-and-forward-engine",
    "ignition/tag-provider",
    "ignition/translations",
})

#: Resource types refused with ``permission_denied`` even under a ``*`` allowlist.
REFUSED_RESOURCE_TYPES = frozenset({
    "com.inductiveautomation.eam/hw-license-management",
    "com.inductiveautomation.eam/leased-license-management",
    "com.inductiveautomation.eam/module-certificates",
    "com.inductiveautomation.eam/module-eulas",
    "com.inductiveautomation.eam/module-settings",
    "com.inductiveautomation.eam/remote-upgrade",
    "com.inductiveautomation.mcp/server-config",
    "ignition/api-token",
    "ignition/edge-system-properties",
    "ignition/identity-provider",
    "ignition/local-system-properties",
    "ignition/oauth2-client",
    "ignition/secret-provider",
    "ignition/security-levels",
    "ignition/security-properties",
    "ignition/security-zone",
    "ignition/system-properties",
    "ignition/user-source",
})


def is_refused_resource_type(resource_type: str) -> bool:
    """D30 §5, fail-closed: anything unclassified counts as refused."""

    return resource_type not in ALLOWED_RESOURCE_TYPES


def refuse_resource_type_decision(resource_type: str) -> PolicyDecision:
    """The pre-dispatch decision for one Target's resource type (D30 §5)."""

    if is_refused_resource_type(resource_type):
        return PolicyDecision(
            allowed=False, layer="target-class",
            reason=f"refused-resource-type:{resource_type}", error_code="permission_denied",
        )
    return PolicyDecision.allow()
