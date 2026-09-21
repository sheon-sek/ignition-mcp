"""Minimal dependency-free Prometheus text metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(slots=True)
class Metrics:
    tool_calls: Counter[tuple[str, str]] = field(default_factory=Counter)
    gateway_requests: Counter[str] = field(default_factory=Counter)
    audit_write_failures: Counter[str] = field(default_factory=Counter)
    operation_record_write_failures: Counter[str] = field(default_factory=Counter)
    artifact_cleanup_deleted: int = 0
    artifact_orphans_removed: int = 0
    transaction_terminals: Counter[str] = field(default_factory=Counter)

    def record_tool(self, tool: str, outcome: str) -> None:
        self.tool_calls[(tool, outcome)] += 1

    def record_audit_write_failure(self, phase: str) -> None:
        self.audit_write_failures[phase] += 1

    def record_operation_record_write_failure(self, kind: str) -> None:
        self.operation_record_write_failures[kind] += 1

    def record_artifact_cleanup(self, deleted: int, orphans_removed: int = 0) -> None:
        self.artifact_cleanup_deleted += max(deleted, 0)
        self.artifact_orphans_removed += max(orphans_removed, 0)

    def record_transaction_terminal(self, state: str) -> None:
        self.transaction_terminals[state] += 1

    def render(self) -> str:
        lines = [
            "# HELP ignition_mcp_tool_calls_total MCP tool calls by tool and outcome.",
            "# TYPE ignition_mcp_tool_calls_total counter",
        ]
        for (tool, outcome), count in sorted(self.tool_calls.items()):
            lines.append(
                f'ignition_mcp_tool_calls_total{{server="ignition-rest",tool="{tool}",outcome="{outcome}"}} {count}'
            )
        lines.append("# HELP ignition_mcp_audit_write_failures_total Durable audit write failures by phase.")
        lines.append("# TYPE ignition_mcp_audit_write_failures_total counter")
        for phase, count in sorted(self.audit_write_failures.items()):
            lines.append(f'ignition_mcp_audit_write_failures_total{{phase="{phase}"}} {count}')
        lines.append("# HELP ignition_mcp_artifact_cleanup_deleted_total Artifacts removed by TTL cleanup.")
        lines.append("# TYPE ignition_mcp_artifact_cleanup_deleted_total counter")
        lines.append(f"ignition_mcp_artifact_cleanup_deleted_total {self.artifact_cleanup_deleted}")
        lines.append("# HELP ignition_mcp_artifact_orphans_removed_total Orphan files removed by reconciliation.")
        lines.append("# TYPE ignition_mcp_artifact_orphans_removed_total counter")
        lines.append(f"ignition_mcp_artifact_orphans_removed_total {self.artifact_orphans_removed}")
        lines.append(
            "# HELP ignition_mcp_project_transactions_terminal_total"
            " Project transactions reaching a terminal state."
        )
        lines.append("# TYPE ignition_mcp_project_transactions_terminal_total counter")
        for state, count in sorted(self.transaction_terminals.items()):
            lines.append(f'ignition_mcp_project_transactions_terminal_total{{state="{state}"}} {count}')
        lines.append(
            "# HELP ignition_mcp_operation_record_write_failures_total Operation record write failures by kind."
        )
        lines.append("# TYPE ignition_mcp_operation_record_write_failures_total counter")
        for kind, count in sorted(self.operation_record_write_failures.items()):
            lines.append(f'ignition_mcp_operation_record_write_failures_total{{kind="{kind}"}} {count}')
        return "\n".join(lines) + "\n"
