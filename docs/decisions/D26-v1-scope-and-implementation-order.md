# D26 — v1 Scope, Milestones, and Implementation Order

**Status:** DECIDED

## Decision

采用以下总原则：

> **完整 v1 scope 预先冻结，但实现采用 dependency-first vertical slices。先证明 Native MCP binding 与双 Server 最小端到端链路，再扩展 read-only surface；mutation 必须等 safety / audit / verification 基础就绪后才开放；Perspective authoring 最后建立在已验证的 Artifact + Project transaction 基础上。**

D26 关闭最初 D01–D26 architecture / implementation decision backlog。后续设计变化必须通过新的 Decision 或 Amendment 留痕，不能在实现中静默改变 D01–D26。

## v1 的含义

这里的 v1 表示 first complete product scope，不强制两个产品立即采用 SemVer 1.0.0。

特别是 ignition-runtime 仍依赖官方 MCP Module 的快速演进版本。Runtime Bundle 在 D21 / D23 的真实 compatibility evidence 足够之前可以继续使用 0.x。是否进入 1.0.0 由稳定性与兼容性证据决定，而不是由 D26 的 v1 名称自动触发。

## v1 completion definition

v1 完成必须同时包含：

- ignition-rest；
- ignition-runtime；
- setup-native；
- contracts / profiles / build tooling；
- real Gateway CI / compatibility evidence。

它们共同满足 D01–D25 的 production rules，并形成可独立部署、独立连接、独立诊断的 canonical dual-server 产品。

v1 不追求把 Ignition OpenAPI 的全部 endpoints 转换成 MCP Tools，也不追求 Tool 数量最大化。Public surface 只包含已经被冻结或在本决定明确纳入的 curated / controlled capabilities。

# v1 public scope — ignition-rest

## Core / diagnostics / operational surface

Required Tools：

- gateway_info
- gateway_diagnose
- operation_diagnose

Required MCP Resources：

- ignition://gateway/capabilities
- ignition://gateway/openapi-info

Required operational HTTP：

- GET /health/live
- GET /health/ready
- GET /metrics

gateway_diagnose 必须保持 low-cost；D19 禁止的 heavyweight Gateway diagnostics 不进入 v1。

## Controlled configuration-resource surface

D03 的完整 controlled generic configuration layer 属于 v1：

- config_resource_search
- config_resource_describe
- config_resource_names
- config_resource_list
- config_resource_get
- config_resource_create
- config_resource_update
- config_resource_delete
- config_resource_rename

Reads 默认可用，但仍受 capability、authorization 和 deployment policy 约束。

Writes 必须实现，但 deployment 默认 disabled，并受 D08 全链路 mutation policy 控制。

## Project / artifact surface

v1 baseline：

- project_list
- project_export
- project_import
- artifact_list
- artifact_info
- artifact_delete

project_export 返回 ArtifactRef；project_import 只消费已经完成类型、大小、checksum 与 lifecycle validation 的 artifact ID。Binary body 只通过 D17 Artifact HTTP data plane streaming。

project_create、project_copy、project_rename、project_delete 不作为 initial v1 release gate。

D02 仍保持权威：未来如果公开这些语义且 Native REST 完整覆盖，canonical owner 仍是 ignition-rest。加入这些 Tool 需要独立 contract，但无需重新打开 D02。

## Tag bulk configuration

v1 包含：

- tag_config_export
- tag_config_import

保持 D11 ownership：bulk configuration 走 Native REST。Runtime 不重复公开 system.tag.exportTags / importTags 的等价能力。

## Alarm Notification Pipeline runtime surface

v1 包含：

- alarm_pipeline_list
- alarm_pipeline_status
- alarm_pipeline_cancel

ownership 与 semantics 继续遵循 D12。alarm_pipeline_cancel 属于 CONTROL / destructive mutation。

## Gateway audit query

当目标 Gateway OpenAPI 实际暴露语义完整的 audit log query 时，v1 提供：

- audit_query

它遵循 D02 / D18 / D19，是 Gateway Native REST audit query，不是 MCP-local generic audit browser。

若目标 Gateway 不支持，则按 D04 从 effective surface 隐藏，并在 call-time 继续 fail closed。

## Perspective / Project-local authoring

D15 的完整 v1 surface 属于 release scope：

- perspective_view_list
- perspective_view_get
- perspective_view_validate
- perspective_view_upsert
- perspective_view_delete
- perspective_page_config_get
- perspective_page_config_update
- perspective_session_props_get
- perspective_session_props_update

所有 mutation 必须走 typed ZIP adapter + D16 transaction protocol。不得退回 WebDev、Gateway filesystem write 或 project scan。

## D03 examples are not automatic release gates

D03 中为了说明 curated semantic Tool 而出现的示例名称，不自动等于 frozen v1 inventory。

除本决定明确列入或其他 Decision 已明确冻结的 Tool 外，不因为研究文档、上游 Repo 或 OpenAPI 中存在某 endpoint 就扩大 v1 scope。

REST-exposed Perspective live-session controls、Reporting、SFC、EAM、Gateway backup 等后续 capability 继续遵循 D02 ownership，但不属于 D26 initial v1 release gate，除非新的 Decision / contract 明确纳入。

# v1 public scope — ignition-runtime

## Identity

v1 必须提供：

- bundle_info

这是 Runtime Bundle compatibility / identity contract，也是 deployment 核心 Tool。

## Tags — D11 full runtime surface

v1 必须提供：

- tag_browse
- tag_query
- tag_read
- tag_write
- tag_get_config
- tag_create
- tag_update
- tag_delete
- tag_copy
- tag_move
- tag_rename
- udt_type_list
- udt_type_get

不进入 v1 public surface：

- tag_exists；
- async variants；
- raw public tag_configure；
- Runtime bulk export / import。

## Alarms — D12 full runtime surface

v1 必须提供：

- alarm_status
- alarm_journal
- alarm_shelved_list
- alarm_acknowledge
- alarm_shelve
- alarm_unshelve

## Historian — D13 full v1 surface

v1 必须提供：

- historian_browse
- historian_query_series
- historian_query_aggregate

不加入 true / unbounded raw、metadata / annotation write、Historian write surface。

## Database — D14 full v1 surface

v1 必须提供：

- database_query_list
- database_query

只允许 Approved Named Query Registry。禁止 arbitrary SQL、generic writes、transaction handles 和 generic stored-procedure execution surface。

# Runtime Text Resources and Prompts

Runtime 产品继续是 D09 / D21 / D25 定义的完整 MCP primitive bundle：Tools + Text Resources + Prompts。

D26 不要求为了形式完整而建立大型 Prompt catalog，也不要求为每个 Tool 都生成 schema Text Resource。

规则：

- Tool contract 是主要 capability contract；
- 小型、确定性的 schema / documentation Text Resource 可以按 D22 发布；
- Prompt 只在存在明确、长期维护的 Agent workflow 时加入；
- v1 Prompt inventory 可以为空；
- Resource / Prompt inventory 无论为空还是非空都必须进入 manifest，并由 D20 / D23 真实 discovery 精确验证；
- Text Resource 可读不能替代 Tool outputSchema / structuredContent / isError 的真实 binding 验证。

# setup-native v1 scope

D20 的五个 operator / deployment commands 全部属于 v1：

- ignition-mcp setup-native doctor
- ignition-mcp setup-native plan
- ignition-mcp setup-native apply
- ignition-mcp setup-native verify
- ignition-mcp setup-native install-module

默认部署 readonly。

Security Level provisioning、Runtime API token creation、local .modl install 继续显式 opt-in。

不自动下载 latest module，不 silent accept certificate / EULA，不自动 upgrade。

# Explicit v1 exclusions

以下明确不属于 v1：

- WebDev bridge；
- arbitrary REST request Tool；
- arbitrary Jython execution；
- OS command execution；
- arbitrary SQL；
- generic DB write console；
- generic project-resource editor；
- Gateway filesystem + project scan authoring；
- unbounded / raw Historian retrieval；
- Historian writes；
- binary Base64 inside Tool JSON；
- arbitrary URL artifact import；
- automatic cross-server fallback / proxy；
- ignition-unified facade；
- Runtime ADMIN profile；
- automatic MCP Module internet download / upgrade；
- generic Runtime / MCP-local audit browsing；
- heavyweight Gateway diagnostics；
- general Tool code generation；
- runtime contract interpreter。

这些 exclusions 不阻止未来通过新 Decision 扩展 capability。

# Implementation order

## Phase 0 — Repository foundation + Native binding proof

目标：在扩展 Tool 数量之前，先消除 Runtime architecture 最大的不确定性。

必须完成：

1. 按 D25 建立真实 monorepo skeleton：两个 primary products、contracts、tests、tooling、GitHub workflows。
2. 建立 root Python / uv workspace、基础 lint / type / test 流程与 GPL-3.0 release metadata。
3. 把 verified Ignition MCP Tool skill 已证明的 Designer resource validation / build rules 转化为 repo-owned tooling/native + tests；生产 build 不依赖外部 skill path。
4. 建立 D06 / D08 / D10 / D18 / D22 的最小 shared contracts：error taxonomy、permission / mutation / budget classes、pagination / batch / artifact semantics、profiles。
5. 建立 Runtime MCP protocol characterization fixture，由 GitHub CI 从官方 exact-patch Docker image 创建和销毁真实 Gateway（Phase 0 baseline 为 `inductiveautomation/ignition:8.3.8`），再配合 exact MCP Module build 验证；不得把用户提供现成 Gateway 当作 G0 前置条件：
   - Tool discovery；
   - input parameter mapping；
   - outputSchema registration；
   - success structuredContent；
   - failure isError；
   - resources/list + resources/read；
   - prompts/list + prompts/get（fixture 发布 Prompt 时）。
6. 把真实 Native response binding 固化为 adapter / contract tests，并只有在证据成立后移除 NATIVE_BINDING_PENDING。D27 已对 baseline tuple 的 native `outputSchema` 缺失建立精确、fail-closed 的例外；该 tuple 可使用 `VERIFIED_WITH_LIMITATION` 关闭 G0，但不能据此获得 D21 `SUPPORTED`。

### Hard gate G0

在以下链路被真实证明前，不允许把 Runtime Bundle 声明为 production-compatible，也不应 breadth-first 实现完整 Runtime Tool catalog：

real Gateway + exact MCP Module build + tools/list + tools/call success structured output + tools/call failure isError + output schema behavior。

如果目标 MCP Module 无法满足 D06，必须新开 Decision / Amendment。禁止悄悄退化成 text-only success / error contract 后继续开发。

External FastMCP foundation、contracts 和 CI 可以与 G0 characterization 并行。

## Phase 1 — Minimal read-only end-to-end vertical slice

目标：用尽量少的 public surface 证明两个 capability planes 都真实工作，而不是一次实现几十个 Tool。

ignition-rest 先实现：

- gateway_info
- gateway_diagnose
- ignition://gateway/capabilities
- ignition://gateway/openapi-info
- /health/live
- /health/ready
- /metrics

同时完成必要 foundation：

- FastMCP 4 Streamable HTTP lifecycle；
- shared httpx.AsyncClient；
- redirects disabled；
- D07 / D07-A auth profiles；
- OperationContext / correlation；
- typed HTTP / error mapping；
- D04 immutable capability registry + singleflight refresh；
- D10 budgets；
- structured logging + low-cardinality metrics。

ignition-runtime 先实现：

- bundle_info
- tag_browse
- tag_read

只建立 readonly profile。

### Gate G1

至少在 development baseline Gateway 8.3.8 + exact MCP Module build 上通过：

- L0 + L1 + L2；
- L3 ignition-rest live path；
- L4 Runtime initialize / list / call；
- exact Tool / Resource / Prompt inventory。

Phase 1 的目标是证明 architecture，不是宣布完整 v1 release。

## Phase 2 — Complete readonly capability surface

G1 通过后，优先把默认 readonly deployment 做完整。

Runtime READ：

- tag_query
- tag_get_config
- udt_type_list
- udt_type_get
- alarm_status
- alarm_journal
- alarm_shelved_list
- historian_browse
- historian_query_series
- historian_query_aggregate
- database_query_list
- database_query

加上 Phase 1 的 bundle_info、tag_browse、tag_read。

REST non-artifact READ：

- project_list
- config_resource_search
- config_resource_describe
- config_resource_names
- config_resource_list
- config_resource_get
- audit_query（capability-gated）
- alarm_pipeline_list
- alarm_pipeline_status

所有 collection / pagination / output 必须先满足 D10，再扩展功能。

### Gate G2

必须满足：

- readonly profile inventory frozen and exact；
- all READ contracts + real smoke calls pass；
- Bad / Uncertain Tag Quality 和其他 domain-negative states 保持 domain data，而不是 Tool failure；
- Gateway 8.3.8 与 8.3.9 candidate rows 都产出 compatibility evidence；
- 未完成正式 certification 的 tuple 继续为 UNTESTED，不能人工写成 SUPPORTED。

## Phase 3 — Artifact / Project / diagnostics / deployment foundations

目标：任何 Project / Perspective overwrite 之前，先建立可恢复、可诊断的基础设施。

必须完成：

- D17 ArtifactStore abstraction + LocalArtifactStore；
- streaming artifact HTTP ingress / egress；
- staging → checksum / size / type validation → atomic publish；
- TTL / quota / free-space controls + bounded cleanup；
- artifact metadata persistence；
- artifact_list / artifact_info；
- project_export；
- tag_config_export；
- D16 Project logical fingerprint；
- per-Gateway + Project writer lock；
- durable recovery artifact；
- mandatory pre-import re-export comparison；
- post-import reconcile / verification machinery；
- External AuditSink / operation record persistence；
- operation_diagnose；
- setup-native doctor / plan / verify；
- deterministic Runtime Bundle ZIP + manifest + SHA-256；
- compatibility evidence plumbing。

Phase 0–2 的 CI 可以使用 minimal test-only provisioning harness 把 Runtime project / server-config 安装到 disposable Gateway，但它不能演化为第二套产品 installer。v1 release 前，D20 setup-native 必须成为 canonical deployment workflow。

### Gate G3

任何 mutation Tool 对用户暴露前，D08 + D18 safety chain 必须具备：

authentication / authorization
∩ deployment mutation-class enablement
∩ operation allowlist
∩ target / resource allowlist
∩ capability check
∩ precondition / concurrency check
∩ exactly-once send attempt
∩ bounded verification
∩ audit / correlation。

Ambiguous mutation outcome 必须表达 outcome_unknown，禁止 automatic replay。

## Phase 4 — Controlled mutation surfaces

G3 通过后实现非-Perspective mutation。

Runtime CONTROL：

- tag_write
- alarm_acknowledge
- alarm_shelve
- alarm_unshelve

Runtime CONFIG：

- tag_create
- tag_update
- tag_delete
- tag_copy
- tag_move
- tag_rename

REST mutations：

- config_resource_create
- config_resource_update
- config_resource_delete
- config_resource_rename
- project_import
- tag_config_import
- alarm_pipeline_cancel
- artifact_delete

同时完成：

- Runtime operator / configurator / full explicit profiles；
- Runtime mutation audit mode；
- target allowlists；
- per-item native results + post-state verification；
- setup-native apply；
- Security Level / credential provisioning 的显式 opt-in workflow。

所有 mutation deployment 仍默认 disabled；代码已经实现不等于部署自动允许。

### Gate G4

D23 L5 必须覆盖至少：

- partial failure；
- timeout；
- ambiguous outcome；
- permission denied；
- oversize；
- concurrent modification；
- audit failure；
- cancellation。

并验证没有 unsafe automatic retry。

## Phase 5 — Perspective typed authoring

Perspective 最后实现，因为它依赖已经稳定的 Project export / import、ArtifactStore、D16 transaction 与 mutation safety。

先完成 read / validate：

- perspective_view_list
- perspective_view_get
- perspective_view_validate
- perspective_page_config_get
- perspective_session_props_get

再完成 writes：

- perspective_view_upsert
- perspective_view_delete
- perspective_page_config_update
- perspective_session_props_update

必须使用以下 transaction semantics：

export baseline A
→ typed logical-resource adapter
→ preserve unknown / unrelated resources
→ validate candidate B
→ durable backup A
→ fresh export A-prime
→ verify fingerprint A-prime equals A
→ import B exactly once
→ fresh export C
→ semantic verification / reconcile。

Inherited non-local resource mutation继续 fail closed；禁止 silent child override；禁止 generic ZIP path write。

### Gate G5

Perspective integration 必须覆盖：

- normal edit；
- no-op；
- concurrent external change abort；
- backup failure abort-before-import；
- ambiguous import outcome reconciliation；
- invalid ZIP / path traversal / duplicate / symlink / bomb rejection；
- unrelated-resource preservation；
- post-import verification failure / recovery-required path。

## Phase 6 — Deployment completion and release certification

完成剩余 operator workflow：

- setup-native install-module --file <trusted-local-modl>

并补齐：

- local .modl hash / certificate / EULA guarded flow；
- fresh install + upgrade path；
- exact Tool / Resource / Prompt inventory verification；
- compatibility semantic diff；
- release evidence generation；
- full failure suite；
- scheduled soak / leak checks；
- nightly canary（不得产生 SUPPORTED）；
- release documentation / operations runbook。

### Final v1 release gate G6

v1 只有全部满足后才算完成：

1. D26 v1 required surface 全部实现；
2. all public contracts / profiles / hard budgets pass L0–L2；
3. ignition-rest required capabilities pass L3 on declared supported Gateway tuples；
4. Runtime Bundle 通过真实 MCP initialize → lists → reads / gets / calls；
5. Tool outputSchema / structuredContent / isError binding 已从 NATIVE_BINDING_PENDING 变成 live-verified；
6. mutations 通过 state verification 与 D08 / D18 safety / audit paths；
7. Perspective 通过 D16 / D17 recovery / concurrency tests；
8. setup-native fresh-install + upgrade + exact inventory verification 通过；
9. release artifact 可复现，并发布 manifest + SHA-256；
10. D21 SUPPORTED tuples 只能来自 machine-readable D23 evidence。

# Parallelism rule

Implementation 可以按 workstream 并行，但不得跨越 hard dependency gate。

Phase 0 推荐并行：

- Workstream A：contracts / tooling / CI；
- Workstream B：ignition-rest foundation；
- Workstream C：Native MCP binding characterization（CI-owned ephemeral real Gateway）。

G0 / G1 之后可以并行：

- Runtime READ domains；
- REST READ / config discovery；
- Artifact / Project infrastructure。

禁止通过“多个 Agent 同时写完整 Tool catalog”绕过先决验证。这会放大 contract drift、重复 helper、错误 Native API 假设和返工风险。

# Per-Tool completion order

每个 Tool 的完成顺序固定为：

1. semantic contract；
2. input / budget validation；
3. native adapter / client behavior；
4. domain serialization；
5. stable error mapping；
6. observability / audit requirements；
7. unit / static tests；
8. real Gateway smoke；
9. failure / state verification。

不能把“handler 已经能跑”视为完成。

# Release philosophy

第一目标不是最多 Tools，而是：

small proven vertical slice
→ complete safe readonly product
→ controlled mutations
→ transaction-heavy authoring
→ compatibility-certified v1。

Read-only milestones 可以提前用于 development / internal evaluation，但未通过最终 Gate 的 snapshot 不得宣称为完整 v1，也不得把未经 D23 evidence 的 environment tuple 标为 SUPPORTED。

# Frozen configuration

- decision: D26
- status: DECIDED
- v1 meaning: first_complete_product_scope
- implies SemVer 1.0.0: false
- exhaustive OpenAPI wrapper: false
- default deployment profile: readonly
- mutations enabled by default: false
- canonical products: ignition-rest-mcp, ignition-runtime-bundle
- canonical servers: ignition-rest, ignition-runtime
- Runtime primitives: Tools required; Text Resources / Prompts supported and inventory-verified; non-empty Prompt catalog not required
- External v1: diagnostics + capability Resources + full D03 generic config layer + project/artifact baseline + REST tag bulk + alarm pipeline + capability-gated audit_query + full D15 Perspective + health endpoints
- Runtime v1: bundle_info + full D11 Runtime Tags + full D12 Runtime Alarms + full D13 Historian + full D14 Database
- setup-native v1: doctor, plan, apply, verify, install-module
- hard gates: G0 binding, G1 dual-plane vertical slice, G2 readonly, G3 mutation safety, G4 mutation failure suite, G5 Perspective transaction suite, G6 release certification
- Text Resource is not Tool output binding
- text-only Runtime fallback without a new Decision is forbidden
- production SUPPORTED while Native binding is pending is forbidden
- initial candidate Gateways: 8.3.8 and 8.3.9
- Phase 0 real Gateway source: CI-owned ephemeral official Docker image; user-supplied Gateway is not required
- SUPPORTED status comes from machine evidence only


## D27 amendment — Phase 0 G0 resolution rule

D27 fulfils this Decision's requirement to open a new Decision when the target official MCP Module cannot satisfy D06's stricter native `outputSchema` requirement. For the exact D27 tuple, G0 accepts `VERIFIED_WITH_LIMITATION` only if native `structuredContent`, `isError`, discovery, Resources and Prompts all pass and only native `outputSchema` is absent. Future tuples do not inherit the exception.
