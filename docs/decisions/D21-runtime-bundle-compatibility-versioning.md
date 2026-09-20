# D21 — Runtime MCP Bundle Compatibility / Versioning

**Status:** DECIDED

## Decision

采用：

> Bundle SemVer + 三维运行兼容矩阵 + per-primitive capability requirements + fail-closed deployment verification。

不能仅以：

```text
Ignition >= 8.3
```

作为完整兼容条件。

Bundle 的兼容范围覆盖三类 MCP primitive：**Tools、Text Resources、Prompts**。

## Independent Version Dimensions

以下版本必须完全独立：

```text
Gateway Version
MCP Module Version
MCP Module Build
Runtime Bundle Version
Resource Schema Version
```

命名必须明确：

```text
bundleVersion
gatewayVersion
mcpModuleVersion
mcpModuleBuild
resourceSchemaVersion
```

`resource.json` 中的 `"version": 1` 不是 Bundle version。

## Bundle Versioning

Bundle 使用 SemVer：

```text
MAJOR.MINOR.PATCH
```

### PATCH

仅限不改变公共 contract 的修复，例如：

- Jython exception handling bug
- serialization bug
- logging
- internal batching
- performance optimization without contract change

### MINOR

向后兼容 capability 增加，例如：

- 新 Tool
- 新 optional 参数
- 新 optional output field
- 新 Text Resource
- 新 Prompt
- 新 optional Prompt argument

但：

> 新 Tool 不因为 Bundle MINOR upgrade 自动进入既有 Runtime Server Profile。

授权扩展必须是独立的 deployment policy change。新增 Resource/Prompt 不改变 Server Config Tool allowlist；其暴露方式以目标 Module 已验证行为为准。

### MAJOR

任何 breaking public contract change，例如：

- Tool rename/remove
- required parameter rename
- parameter type change
- optional → required
- enum value removal
- output field removal/semantic change
- breaking error semantics
- Resource URI/name rename or removal
- Resource payload schema breaking change
- Prompt rename/removal
- Prompt argument rename/removal or newly required argument

## 0.x Policy

0.x 阶段仍禁止随意 breaking。

约定：

- PATCH：兼容修复
- MINOR：可发生 breaking，但必须明确标记并被 setup plan 识别

正式稳定后进入 1.0.0 严格 SemVer。

## Compatibility Matrix

不声明 blanket “Ignition 8.3 supported”。

真实 compatibility key：

```text
Gateway Version
× MCP Module Version/Build
× Bundle Version
```

状态：

```text
SUPPORTED
UNTESTED
INCOMPATIBLE
UNKNOWN
```

### `SUPPORTED`

精确 tuple 经真实 Gateway release integration suite 验证通过。

生产 `SUPPORTED` 还要求 Tool 原生响应绑定通过 D23 真机验证。`structuredContent` 与 `isError` 始终是硬要求；native `outputSchema` 默认也是硬要求。D27 对精确的 baseline Runtime tuple 记录了一个 fail-closed 例外：Module 不发布 `outputSchema` 时，repo-owned JSON Schema 继续作为强制 semantic contract，且该 tuple 必须明确标记 native binding limitation。Text Resource 可读本身仍不能证明 Tool 输出契约。

### `UNTESTED`

没有已知 incompatibility，但没有正式测试证据。

### `INCOMPATIBLE`

已知不能运行或无法满足 Tool contract。

### `UNKNOWN`

无法确定环境身份或 metadata 不完整。

## Deployment Policy

```text
SUPPORTED     → normal apply
UNTESTED      → plan/doctor allowed; apply denied by default
INCOMPATIBLE  → deny
UNKNOWN       → deny
```

允许：

```text
--allow-untested
```

显式 override，但 compatibility 状态仍保持 `UNTESTED`，并记录 `overrideUsed=true`。

## Initial Development Baseline

初始 development baseline：

```text
Ignition 8.3.8
```

但不能自动标记为 `SUPPORTED`。

只有真实 D23 compatibility test 通过后才可进入 tested matrix。

MCP EA build 必须尽量精确到：

```text
moduleVersion + moduleBuild
```

不能只记录 `1.3.5-SNAPSHOT`。

## Per-Primitive Requirements

Compatibility 不只 bundle-wide，还要 per-primitive。

Tool 示例（canonical public name，见 D05）：

```yaml
tag_read:
  requires:
    nativeFunctions:
      - system.tag.readBlocking
```

Text Resource 示例（identifier 以真实 `resources/list` 为准）：

```yaml
resources:
  <resource-identifier>:
    requires:
      resourceFormat:
        - data.bin
        - mimeType
        - size
```

Prompt 示例（identifier 以真实 `prompts/list` 为准）：

```yaml
prompts:
  <prompt-identifier>:
    requires:
      handlerSignature: onPrompt(builder, arguments)
```

原则：

- 优先 capability/function detection
- version gating 仅作补充
- Tool handler 仍需 defensive runtime check

缺失 requirement 时返回 stable：

```text
unsupported_capability
```

而不是泄漏 `AttributeError` 等底层异常。

## Effective Server Config Inventory

最终 Server Config 治理的是 Tool：

```text
explicit profile allowlist
∩ compatible tools
```

如果 profile 中某 Tool 在当前环境 incompatible，则 omit 并在 plan 中报告原因。

核心 Tool（至少 `bundle_info`）若不可运行，则整个 deployment incompatible。

Resource/Prompt 不进入 Server Config allowlist（该机制未经 Module 验证）。bundle 的预期 Resource/Prompt inventory 由 bundle manifest 记录，并由 D20/D23 的 discovery 结果核对；incompatible 的 Resource/Prompt 使 bundle compatibility 降级并在报告中给出原因。

## `bundle_info`

作为 Runtime identity contract，至少返回：

- `bundleVersion`
- source revision/build info
- `gatewayVersion`
- `mcpModuleVersion`
- `mcpModuleBuild`
- compatibility status

不返回完整 compatibility matrix。

## Distribution Manifest

Designer Project ZIP 内不发明未经验证的 bundle manifest resource。

发行物：

```text
dist/
├── ignition-runtime-bundle-x.y.z.zip
├── ignition-runtime-bundle-x.y.z.manifest.json
└── ignition-runtime-bundle-x.y.z.sha256
```

Manifest 至少包含：

- `schemaVersion`
- `bundleVersion`
- artifact SHA-256
- source revision
- resource schema version
- tested tuples
- per-tool requirements
- Tool inventory
- Text Resource inventory
- Prompt inventory
- native response binding status

## Reproducible Build

要求：

```text
same source
+
same builder version
=
same artifact SHA-256
```

Release 必须提供 ZIP + manifest + checksum。

## Upgrade Rules

### PATCH

正常：

```text
plan → backup → apply → verify
```

### MINOR

同样允许 normal upgrade，但新 Tool 不自动暴露。

新增 Resource/Prompt 属于 bundle 内的 additive 变更，不需要新的授权扩展，但 plan 必须列出其 discovery 结果与预期 inventory 的差异。

### MAJOR

默认拒绝。

需要显式：

```text
--allow-major-upgrade
```

plan 必须列出 contract diff。

### Downgrade

默认拒绝。

需要显式：

```text
--allow-downgrade
```

并进行 compatibility preflight、backup、Server Config reconciliation 与 exact `tools/list` + `resources/list` + `prompts/list` verification。

## Runtime Environment Drift

如果 Gateway/MCP Module 原地升级，`doctor` / `bundle_info` 必须重新识别当前 tuple。

原先 `SUPPORTED` 可能变成：

```text
UNTESTED
```

不能仅因为 Bundle ZIP 未变化就维持旧 compatibility 结论。

## No Runtime Self-Update

Runtime MCP Bundle 不允许自动更新自己。

升级永远属于 deployment plane：

```text
setup-native plan
setup-native apply
setup-native verify
```

## Contract Compatibility Tests

CI 必须做 semantic contract diff，而非 byte diff。

检测：

- Tool removed
- parameter removed
- required flag change
- type change
- enum reduction
- output required-field change
- permission/profile change
- Text Resource added/removed/renamed
- Text Resource payload schema breaking change
- Prompt added/removed/renamed
- Prompt argument removed or newly required
- native response binding status change

避免 breaking change 只 bump PATCH。

## Frozen Configuration

```yaml
decision: D21
status: DECIDED

bundle_versioning:
  scheme: semver
  version_tool_names: false
  covers_primitives: [tools, text_resources, prompts]

version_dimensions:
  - gateway_version
  - mcp_module_version
  - mcp_module_build
  - bundle_version
  - resource_schema_version

resource_schema_version_is_bundle_version: false

compatibility:
  blanket_ignition_8_3_support: false
  exact_tested_matrix: true
  statuses:
    - SUPPORTED
    - UNTESTED
    - INCOMPATIBLE
    - UNKNOWN

  untested_apply_default: deny
  untested_explicit_override: true
  incompatible_apply: deny
  unknown_apply: deny
  native_response_binding_required_for_supported: true

initial_development_baseline:
  gateway: "8.3.8"
  automatically_mark_supported: false

tool_requirements:
  per_tool: true
  prefer_capability_detection: true
  version_gating_as_fallback: true
  runtime_defensive_check: true
  missing_requirement_error: unsupported_capability

primitive_requirements:
  per_text_resource: true
  per_prompt: true

primitive_inventory:
  tools: required
  text_resources: required
  prompts: required
  recorded_in_manifest: true

server_config:
  effective_inventory: "explicit_profile_allowlist ∩ compatible_tools"
  wildcard: forbidden
  new_minor_tool_auto_authorized: false
  resource_prompt_in_config_allowlist: false

manifest:
  location: outside_designer_project_zip
  schema_versioned: true
  artifact_sha256: required
  source_revision: required
  compatibility_matrix: true
  per_tool_requirements: true
  tool_inventory: required
  text_resource_inventory: required
  prompt_inventory: required
  native_response_binding_status: required

runtime_identity:
  tool: bundle_info
  expose_current_tuple: true
  expose_full_matrix: false

release_artifacts:
  deterministic_zip: required
  sha256: required
  manifest: required

upgrade:
  patch: normal_plan_apply_verify
  minor: normal_plan_apply_verify
  new_tools_auto_exposed: false
  major_requires_explicit_ack: true
  downgrade_default: deny

self_update:
  runtime_bundle: forbidden

testing:
  live_gateway_required_for_supported_status: true
  contract_semantic_diff: required
  resource_prompt_protocol_tests: required
```

## Pre-D26 consistency amendment
Renamed the decision from “Built-in Tool Bundle” to **Runtime MCP Bundle** and broadened its compatibility/versioning scope to all three MCP primitives. SemVer MINOR/MAJOR rules now cover added/removed/renamed Resources and Prompts and breaking Resource payload / Prompt argument changes; requirements are per-primitive; the manifest carries Tool, Text Resource and Prompt inventories plus the native response binding status; distribution artifacts are renamed `ignition-runtime-bundle-x.y.z.*`; and production `SUPPORTED` now explicitly requires the pending Tool response binding to have passed D23 live verification. The per-tool requirement example now uses the canonical `tag_read` name from D05 instead of the Skill's `tag-read` folder/title sample. Compatibility philosophy, 0.x policy, deployment policy, reproducibility, and upgrade/downgrade rules are otherwise unchanged.


## D27 amendment

Native response-binding evidence now also allows `VERIFIED_WITH_LIMITATION` for the exact D27 tuple. This is not a deployment compatibility status and does not itself grant `SUPPORTED`.
