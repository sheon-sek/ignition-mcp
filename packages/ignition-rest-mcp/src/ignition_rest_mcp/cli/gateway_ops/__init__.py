"""Gateway operations the CLI reuses from the Runtime Bundle deployment path.

This package holds the verified helper layer: bounded read-only Gateway probes
(``gateway``), the curated guarded writer (``writer``), the Security Level and
credential handling (``security``), the policy and Server Config documents
(``documents``), the Module artifact readers (``install_module``), the bounded MCP
Streamable HTTP client (``mcp_http``), the shared diagnostic checks (``checks``),
the policy confirmation step (``policy``), the post-provisioning sequence
(``verify``), the change classification (``action``) and the validated input
shapes (``inputs``).

The commands that drive this layer live in ``cli/engine`` and ``cli/setup`` and
are reached through the ``ignition-mcp`` entry point (D32). Nothing here reads a
repo-relative path, and nothing here can mutate a Gateway except through
:class:`~ignition_rest_mcp.cli.gateway_ops.writer.GatewayWriter`.
"""
