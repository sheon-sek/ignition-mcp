"""Central D07 authorization: one declared scope per component, enforced twice.

D07 puts scope-based authorization in middleware rather than in every handler:
unauthorized components are filtered out of ``tools/list`` and ``resources/list``,
and the check runs again before the component executes, so a stale or forged
discovery response can never reach a handler.

A component declares its required scope once, as a ``scope:<scope>`` tag next to
its capability tag. The tag states D07's effect-based rule (assign scope by
operation effect, not module or domain), and ``contracts/tools/rest/*.contract.json``
carries the same value as ``requiredScope`` for the contract test to compare.
Anything unresolvable — no tag, two tags, an unknown scope — fails closed: the
component is hidden and calls to it are denied.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any, NoReturn

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
import mcp_types as mt

from ignition_rest_mcp.auth import Principal, current_principal
from ignition_rest_mcp.config import CANONICAL_SCOPES, Settings
from ignition_rest_mcp.operation import uuid7

LOGGER = logging.getLogger("ignition_rest_mcp")

SCOPE_TAG_PREFIX = "scope:"


def _component_server(context: MiddlewareContext[Any]) -> FastMCP | None:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is None:
        return None
    return fastmcp_context.fastmcp


def scope_tag(scope: str) -> str:
    """The tag that declares ``scope`` as a component's required scope."""

    return SCOPE_TAG_PREFIX + scope


def declared_scope(tags: Iterable[str]) -> str | None:
    """The single canonical scope a component requires, or ``None`` (fail closed).

    ``None`` covers every unresolvable declaration: no scope tag, more than one,
    or one outside the D07 canonical set.
    """

    declared = [tag[len(SCOPE_TAG_PREFIX):] for tag in tags if tag.startswith(SCOPE_TAG_PREFIX)]
    if len(declared) != 1 or declared[0] not in CANONICAL_SCOPES:
        return None
    return declared[0]


class ScopeAuthorizationMiddleware(Middleware):
    """D07 enforcement for the Tools and Resources of one deployment."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------ tools

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        return [tool for tool in tools if self._allows(tool.tags)]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        name = context.message.name
        if context.fastmcp_context is None:
            # Without the server we cannot read the Tool's declared scope: deny
            # rather than let an unchecked call through.
            self._deny_tool(name, None)
        tool = await self._resolve_tool(context, name)
        if tool is not None and not self._allows(tool.tags):
            self._deny_tool(name, declared_scope(tool.tags))
        # An unknown or capability-disabled Tool keeps FastMCP's own refusal.
        return await call_next(context)

    # ---------------------------------------------------------------- resources

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        resources = await call_next(context)
        return [resource for resource in resources if self._allows(resource.tags)]

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        uri = str(context.message.uri)
        resource = await self._resolve_resource(context, uri)
        if resource is not None and not self._allows(resource.tags):
            self._deny_resource(uri, declared_scope(resource.tags))
        return await call_next(context)

    # ------------------------------------------------------------------ helpers

    def _principal(self) -> Principal:
        # auth.py is the only constructor of a verified principal; `auth=none`
        # resolves to the configured service identity with ignition.read only.
        return current_principal(self._settings)

    def _allows(self, tags: Iterable[str]) -> bool:
        """The caller holds the scope this component declares."""

        scope = declared_scope(tags)
        return scope is not None and self._principal().has_scope(scope)

    async def _resolve_tool(self, context: MiddlewareContext[Any], name: str) -> Tool | None:
        server = _component_server(context)
        if server is None:
            return None
        return await server.get_tool(name)

    async def _resolve_resource(
        self, context: MiddlewareContext[Any], uri: str,
    ) -> Resource | ResourceTemplate | None:
        server = _component_server(context)
        if server is None:
            return None
        resource = await server.get_resource(uri)
        if resource is not None:
            return resource
        return await server.get_resource_template(uri)

    def _log_denial(self, component: str, name: str, reason: str) -> None:
        LOGGER.warning(
            "Authorization denied",
            extra={
                "event": "authorization",
                "targetType": component,
                "targetId": name,
                "actor": self._principal().key,
                "outcome": f"denied:{reason}",
            },
        )

    def _deny_tool(self, name: str, scope: str | None) -> NoReturn:
        reason = f"missing-scope:{scope}" if scope is not None else "no-declared-scope"
        self._log_denial("tool", name, reason)
        raise ToolError(json.dumps({
            "code": "permission_denied",
            "message": (
                f"The caller's scopes do not cover this Tool ({reason}). "
                "Use a credential configured with the required scope."
            ),
            "correlationId": uuid7(),
        }, separators=(",", ":")))

    def _deny_resource(self, uri: str, scope: str | None) -> NoReturn:
        reason = f"missing-scope:{scope}" if scope is not None else "no-declared-scope"
        self._log_denial("resource", uri, reason)
        raise ResourceError(
            f"permission_denied: the caller's scopes do not cover this resource ({reason})"
        )
