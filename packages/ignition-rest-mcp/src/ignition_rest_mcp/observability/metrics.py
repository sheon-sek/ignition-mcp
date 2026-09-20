"""Minimal dependency-free Prometheus text metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(slots=True)
class Metrics:
    tool_calls: Counter[tuple[str, str]] = field(default_factory=Counter)
    gateway_requests: Counter[str] = field(default_factory=Counter)

    def record_tool(self, tool: str, outcome: str) -> None:
        self.tool_calls[(tool, outcome)] += 1

    def render(self) -> str:
        lines = [
            "# HELP ignition_mcp_tool_calls_total MCP tool calls by tool and outcome.",
            "# TYPE ignition_mcp_tool_calls_total counter",
        ]
        for (tool, outcome), count in sorted(self.tool_calls.items()):
            lines.append(
                f'ignition_mcp_tool_calls_total{{server="ignition-rest",tool="{tool}",outcome="{outcome}"}} {count}'
            )
        return "\n".join(lines) + "\n"
