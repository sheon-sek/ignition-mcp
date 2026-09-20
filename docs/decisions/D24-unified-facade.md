# D24 — Canonical Dual-Server Architecture and Optional Unified Facade

**Status:** DECIDED

## Decision

两个 MCP Server 永远保持 canonical capability servers：

```text
ignition-rest
ignition-runtime
```

它们保持：

- independent deployment
- independent connection
- independent authentication
- independent versioning
- independent testing
- independent diagnostics

未来允许 optional unified facade，但：

> Facade 只能是 edge/federation adapter，不能成为 capability owner，也不能取代两个 canonical servers。

## Preferred Aggregation Order

优先顺序：

```text
1. MCP Host 原生支持 multiple servers
2. Client-side aggregation / ClientGroup
3. Server-side unified facade
```

能在 client side 聚合时，不增加额外网络 hop。

## v1 Scope

Unified facade：

```text
architecturally allowed
v1 required = false
v1 implementation = no
```

只有明确出现“重要 Client 只能配置单 MCP endpoint”等实际需求时再实现。

## Separate Component

未来 facade 必须是独立组件。

禁止：

```text
ignition-rest
└── runtime_proxy.py
```

即不能把 aggregation 嵌入 External REST capability owner。

未来概念名：

```text
ignition-unified
```

但 v1 不创建空 package。

## Facade Responsibilities

允许：

- discover
- namespace
- filter
- route
- authenticate
- authorize
- correlate
- observe

禁止：

- implement `tag_read`
- implement `project_import`
- 直接调用 Ignition REST
- 直接调用 `system.*`
- 自己解释 domain semantics
- ownership fallback

Facade 永远通过 MCP protocol 调用 canonical server。

## No Ownership Fallback

如果 Runtime 不可用：

```text
runtime operation unavailable
```

不能 fallback 到 REST。

如果 REST 不可用：

```text
REST operation unavailable
```

不能由 facade 直接请求 Gateway。

D02 ownership 永远保持。

## Namespacing

两个独立 Server 继续使用 canonical Tool names：

```text
project_list
tag_read
```

Facade 单 endpoint 时必须使用 deterministic namespace，例如概念上：

```text
rest_project_list
runtime_tag_read
```

这些是 facade-qualified names，不是新的 canonical Tool names。

具体 alias (`rest_` / `runtime_`) 在真正实现 facade 时另行冻结。

## Prompts in Future Federation

D24 v1 不实现 facade，因此本条对 v1 无影响。

未来实现 facade 时，discovery/federation 设计必须同时覆盖 Prompts，而不只是 `tools/list` + Resources。namespace、schema/metadata preservation、authorization、identity、correlation、degraded mode 等规则同样适用于 Prompt surface。

## Schema Preservation

Facade 应从 downstream discovery 获取 schema/metadata，并尽量透明保留：

```text
tools/list      → Tool input/output schema
resources/list + resources/read → Resource URI / mimeType / payload
prompts/list + prompts/get      → Prompt arguments / messages
```

禁止手工重新维护一套重复的 Tool / Resource / Prompt definitions。

## Credentials

Inbound client credential 与 downstream credentials 必须分开：

```text
Client → Facade credential
Facade → ignition-rest credential
Facade → ignition-runtime credential
```

禁止盲目 forward inbound Authorization/token。

Header propagation 默认 deny。

## No Super Credential

禁止默认：

```text
Facade
├── REST admin credential
└── Runtime full credential
```

least privilege：

```text
facade readonly
→ REST readonly identity
→ Runtime readonly identity
```

operator/configurator 同理。

front-door privilege 不得超过 downstream privilege。

## Authorization

最终 surface 必须同时满足：

```text
facade policy
∩ downstream discovery surface
    (tools/list + resources/list + prompts/list)
```

不能 hardcode 一个 downstream 实际无权限调用（或根本未发布）的 Tool/Resource/Prompt。

## Protocol Versions

Facade 不要求两个 downstream MCP Server 使用相同 MCP protocol revision。

每个 downstream connection 独立 negotiation。

Facade 对外再提供自己的 protocol surface。

## Identity / Audit

不能伪造 end-user identity。

如果 downstream 实际只认证 service credential，则记录：

```text
callerActor
downstreamServiceIdentity
```

两者分离。

不能声称 Gateway 实际以某个人类身份执行，除非存在真实 delegation/impersonation mechanism。

## Correlation

Facade 使用 parent/child correlation：

```text
facade rootCorrelationId
→ downstream correlationId
```

不强制所有服务使用同一 ID。

Audit/diagnostics 应保留映射。

## Error Semantics

保留 D06 stable error taxonomy。

Facade 只增加：

- backend identity
- downstream correlation

不能把：

```text
permission_denied
```

改成模糊：

```text
runtime operation failed
```

## Mutation Retry

Facade 禁止自动 retry downstream mutations。

例如：

- tag write
- alarm acknowledge
- project import

timeout 时遵循 D08：

```text
outcome_unknown
→ verification
```

只允许对 discovery/read infrastructure 做严格 bounded retry。

## Timeout Budget

Facade 不得延长 caller deadline。

Outer request budget 必须覆盖 facade overhead + downstream deadline。

具体数值以后实现时定义。

## Degraded Mode

已成功 discovery 的 backend 暂时 outage 时，facade 可以维持稳定 catalog，并让调用返回 backend unavailable，同时整体报告 degraded。

Cold start 若从未成功 discovery 某 backend：

```text
no verified schema
→ do not invent catalog
```

未来 catalog persistence 另做决定。

## Compatibility Authority

D21 compatibility authority 永远属于 canonical servers。

Facade 只消费：

- tools/list
- resources/list + resources/read
- prompts/list + prompts/get
- bundle_info
- capability metadata

不能自行推断 Gateway capability。

## Deployment Boundary

Unified facade 不属于 `setup-native`。

`setup-native` 只负责 Ignition Official MCP Server deployment。

未来 facade 有自己的独立 deployment workflow。

## Testing

如果实现 facade，必须新增：

- namespace tests
- schema preservation
- routing
- auth filtering
- identity separation
- parent/child correlation
- one-backend-down behavior
- different protocol revisions
- timeout propagation
- no mutation retry
- no credential/header leakage
- Resource/Prompt discovery、read/get 透传
- Prompt surface 的 namespace 与 authorization

Canonical server tests 永远仍然必须独立通过。

## Frozen Configuration

```yaml
decision: D24
status: DECIDED

canonical_architecture:
  servers:
    - ignition-rest
    - ignition-runtime

  always_independently_deployable: true
  always_directly_connectable: true
  independently_authenticated: true
  independently_versioned: true
  independently_tested: true

preferred_aggregation:
  client_native_multi_server: first
  client_side_group: second
  server_side_facade: fallback

unified_facade:
  allowed: true
  required: false
  v1_scope: false
  replaces_canonical_servers: false
  capability_owner: false

  must_be_separate_component: true
  may_be_embedded_in_ignition_rest: false

  business_logic: forbidden
  direct_gateway_rest_calls: forbidden
  direct_system_calls: forbidden
  ownership_fallback: forbidden

  responsibilities:
    - discover
    - namespace
    - filter
    - route
    - authenticate
    - authorize
    - correlate
    - observe

naming:
  canonical_names_unchanged: true
  facade_namespaced: true
  facade_qualified_names_are_not_canonical: true

schema:
  preserve_downstream_schema: true
  preserve_downstream_resources_and_prompts: true
  duplicate_manual_tool_definitions: false

security:
  inbound_credentials_forwarded: false
  downstream_credentials_separate: true
  supercredential_default: forbidden
  least_privilege_downstream_identity: required
  header_propagation: deny_by_default

identity:
  caller_identity_and_downstream_identity_distinct: true
  impersonation_without_real_delegation: forbidden

authorization:
  effective_surface_must_respect_downstream_tools_list: true
  effective_surface_must_respect_downstream_primitives: true
  fail_closed: true

observability:
  facade_root_correlation: true
  downstream_child_correlation: true
  preserve_backend_identity: true
  preserve_D06_error_semantics: true

resilience:
  mutation_retry_at_facade: forbidden
  ownership_failover: forbidden
  healthy_backend_may_continue_serving: true
  transient_backend_loss_may_report_degraded: true

compatibility:
  authority_remains_canonical_servers: true
  facade_must_not_infer_gateway_capability: true
  federation_must_cover_prompts: true

testing:
  canonical_server_tests_remain_required: true
  facade_integration_tests_if_implemented: true
  resource_prompt_federation_tests_if_implemented: true
```

## Pre-D26 consistency amendment
Future facade federation now explicitly covers Prompts as well as Tools and Resources: the authorization rule intersects the downstream discovery surface for all three primitives, schema preservation covers Resource payloads and Prompt arguments, the consumed-interface list includes `resources/read` and `prompts/get`, and testing adds Resource/Prompt pass-through and Prompt namespacing/authorization. Canonical dual-server architecture, v1 scope, ownership, credentials, correlation, and error semantics are unchanged; v1 is unaffected because the facade is not implemented.
