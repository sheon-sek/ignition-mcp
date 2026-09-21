"""Operator-facing commands, code-separated from the MCP server (D25).

Nothing in this package imports ``ignition_rest_mcp.server``: the CLI probes a
running deployment over the wire instead of sharing in-process state with it.
"""
