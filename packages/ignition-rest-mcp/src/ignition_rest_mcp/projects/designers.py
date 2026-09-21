"""D16 Designer-session policy: bounded paginated ``GET /data/api/v1/designers``
filtered to the target project. Policy comes only from deployment config
(``deny`` default, not caller-overridable); unknown wire shapes fail closed."""

from __future__ import annotations


from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext

PAGE_SIZE = 100
MAX_PAGES = 20  # bounded: at most 2000 session records inspected per check


async def active_sessions_for_project(
    client: GatewayClient, registry: CapabilityRegistry, context: OperationContext, project_name: str,
) -> list[dict[str, str]]:
    if not registry.supports("designer_sessions"):
        raise GatewayError(
            "unsupported_capability",
            "the Gateway does not expose /data/api/v1/designers; the Designer-session "
            "policy cannot be evaluated and mutation fails closed",
        )
    sessions: list[dict[str, str]] = []
    for page in range(MAX_PAGES):
        payload = await client.get_json(
            "/data/api/v1/designers",
            params={"limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
            context=context,
        )
        items = payload.get("items")
        metadata = payload.get("metadata")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise GatewayError("schema_mismatch", "Designer session listing has an unknown shape")
        if not isinstance(metadata, dict):
            raise GatewayError("schema_mismatch", "Designer session listing is missing metadata")
        for item in items:
            project = item.get("project")
            identifier = item.get("id")
            user = item.get("user")
            if project == project_name:
                # safe identity fields only; unknown item shapes still count as active sessions
                sessions.append({
                    "id": identifier if isinstance(identifier, str) else "",
                    "user": user if isinstance(user, str) else "",
                })
        matching = metadata.get("matching")
        if not isinstance(matching, int) or isinstance(matching, bool):
            raise GatewayError("schema_mismatch", "Designer session metadata is invalid")
        if (page + 1) * PAGE_SIZE >= matching or not items:
            return sessions
    raise GatewayError(
        "limit_exceeded",
        f"more than {MAX_PAGES * PAGE_SIZE} Designer sessions are active; refusing to evaluate the policy",
    )


def enforce_policy(policy: str, sessions: list[dict[str, str]], project_name: str) -> None:
    if not sessions:
        return
    if policy == "deny":
        raise GatewayError(
            "conflict",
            f"active Designer sessions hold project {project_name!r}; the mutation was denied "
            "(IGNITION_MCP_PROJECT_DESIGNER_POLICY=deny)",
        )
    # warn: caller logs and proceeds; ignore is resolved before fetching.
