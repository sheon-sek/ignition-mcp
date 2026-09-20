# D19 — Diagnostics / Capability Discovery Public Surface

**Status:** DECIDED

## Decision

采用三层分离模型：

```text
Agent-facing MCP
    → diagnose / compatibility / exact-operation diagnostics

MCP Resources
    → capability registry / OpenAPI metadata

Operational HTTP
    → liveness / readiness / metrics
```

诊断、能力发现与运维监控必须分开。Agent 需要的是可解释、低成本、可行动的诊断信息；负载均衡器/Kubernetes 需要的是 liveness/readiness；Prometheus 等监控系统需要的是 metrics。

## Public MCP Surface

### `ignition-rest`

提供：

- Tool: `gateway_diagnose`
- Tool: `operation_diagnose`
- Resource: `ignition://gateway/capabilities`
- Resource: `ignition://gateway/openapi-info`

### `ignition-runtime`

提供：

- Tool: `bundle_info`
- Resource surface: `resources/list`（bundle 当前发布的 Text Resources）
- Prompt surface: `prompts/list`（bundle 当前发布的 Prompts）

`bundle_info` 仍然是 Tool。Resource/Prompt 的发现使用 MCP 原生 primitive surface，不为它们再造一组 capability Tools 或诊断 Tools。

## `gateway_diagnose`

用途：

- 检查 External MCP → Gateway 可达性。
- 检查 Ignition API authentication。
- 检查 capability registry 状态。
- 检查 OpenAPI fingerprint / registry generation。
- 检查 FastMCP 自身必要子系统，例如 artifact store / audit sink。
- 返回安全、有限、可供 Agent 行动的诊断摘要。

禁止触发重量级 Gateway diagnostics，例如：

- thread dump
- memory dump
- 大量日志 dump
- project scan
- config scan
- 任意可能影响 Gateway 运行的 heavyweight diagnostics

`gateway_diagnose` 必须是低成本操作。

## `bundle_info`

`bundle_info` 描述当前 Ignition Runtime MCP Bundle 身份与运行兼容性，不重复实现 External REST Gateway info。

建议至少返回：

- `bundleVersion`
- `gatewayVersion`
- `mcpModuleVersion`
- `mcpModuleBuild`（如果可取得）
- compatibility status
- 轻量 native capability summary

它不负责返回 CPU、memory、database status、Gateway module health 等 REST/Gateway plane 信息。

## Capability Discovery

有效公共 surface 以真实 MCP discovery 为最终视图，覆盖三类 primitive：

```text
tools/list
resources/list
prompts/list
```

Tool 的调用视图为：

```text
implemented
∩ gateway-supported
∩ deployment-enabled
∩ caller-authorized
```

Resource/Prompt 视图以其实际 discovery 结果为准（`resources/list` / `prompts/list`）。本决定不假设目标 Module 为 Resource/Prompt 提供与 Tool 等价的 deployment allowlist 或授权机制；相关能力未经 D20/D23 验证前不得假定存在（见 D09）。

详细 capability metadata 通过 Resource 暴露，而不是额外建立一组重复的 capability Tools。

### Resources

```text
ignition://gateway/capabilities
ignition://gateway/openapi-info
```

`openapi-info` 仅提供：

- OpenAPI SHA-256
- registry generation
- fetchedAt / age
- Gateway version
- registry state
- 必要环境 fingerprint

默认不公开完整 `/openapi.json` 为 MCP Resource，避免把大型 OpenAPI 文档直接送入 Agent context。

## Operational HTTP

External Server 提供：

```text
GET /health/live
GET /health/ready
GET /metrics
```

### `/health/live`

只判断 External MCP process/event loop 是否存活。

Gateway 暂时不可达时：

```text
live = true
```

不应因此触发进程重启风暴。

### `/health/ready`

判断当前实例是否具备正常处理业务 MCP operation 的条件。

示例：

```text
Gateway unreachable
→ live = true
→ ready = false
```

D04 capability registry 为 `STALE` 时可根据当前 Gateway 状态报告 degraded/ready；cold-start `UNAVAILABLE` 时应 not-ready，但 diagnostics 仍应可用。

### `/metrics`

提供低基数 operational metrics。

v1 不提供：

- `metrics_get` MCP Tool
- raw metrics MCP Resource

如果将来确实存在 Agent 诊断需求，应单独设计 curated semantic diagnostic Tool，而不是直接 dump 整个 metrics registry。

## `operation_diagnose(correlationId)`

用于按精确 `correlationId` 查询单次 MCP operation 的安全诊断摘要。

典型用途：

```text
mutation
→ timeout / outcome_unknown
→ operation_diagnose(correlationId)
```

返回：

- tool
- start / completion timestamps
- outcome
- phase summary
- transactionId（如果有）
- stable error code
- downstream correlation（如果有）

限制：

- 只能精确 correlationId 查询
- bounded records
- 不提供任意 time-range audit browsing
- 不返回 secrets
- 不返回 raw stack trace
- 不返回 raw HTTP bodies
- 不提供 unrestricted logs

权限：

- same verified actor/client：可使用普通 read 权限
- 查询其他 actor/client：要求 admin
- `auth=none` trusted-internal 模式下，视为配置的 service identity trust domain

## Audit query ownership

`generic_mcp_audit_query_v1=false` 只表示：不提供 MCP-local / generic 的 audit browsing Tool，也不提供任意 time-range 的 audit 查询。

它**不撤销 D02**：当 Ignition Native REST 暴露 audit log query 时，该 Gateway audit query 仍由 `ignition-rest` 作为 canonical owner 提供，不构成 duplicate operation，也不与 D18 的“v1 不暴露 unbounded Runtime `audit_query`”冲突。

## Manual Capability Refresh

不提供 Agent-facing MCP Tool。

D04 已定义自动 refresh 机制；人工 refresh 属于 CLI / protected operational endpoint，避免 Agent 高频触发 OpenAPI rebuild。

## Frozen Configuration

```yaml
decision: D19
status: DECIDED

agent_tools:
  ignition-rest:
    - gateway_diagnose
    - operation_diagnose
  ignition-runtime:
    - bundle_info

capability_discovery:
  primitive_surfaces:
    - tools/list
    - resources/list
    - prompts/list
  tools_list_is_effective_tool_surface: true
  bundle_info_is_tool: true
  resource_prompt_allowlist_assumed: false
  detailed_capabilities_resource: ignition://gateway/capabilities
  openapi_metadata_resource: ignition://gateway/openapi-info
  full_openapi_resource_default: false
  capability_list_tool: false

operational_http:
  liveness: /health/live
  readiness: /health/ready
  metrics: /metrics

metrics_mcp_tool: false
metrics_mcp_resource: false

generic_mcp_audit_query_v1: false
gateway_rest_audit_query_ownership_unchanged: D02
exact_operation_diagnostics: true

diagnostics_may_trigger_heavy_gateway_operations: false
manual_capability_refresh_agent_tool: false
```

## Pre-D26 consistency amendment (C05)
Broadened the effective discovery surface from Tool-only to the three MCP primitive surfaces (`tools/list`, `resources/list`, `prompts/list`); `bundle_info` remains a Tool. Added the “Audit query ownership” section so `generic_mcp_audit_query_v1=false` is unambiguously scoped to MCP-local/generic audit browsing and does not revoke D02's Gateway Native REST audit-query ownership. Diagnostics tools, operational HTTP endpoints, and deferrals are unchanged.
