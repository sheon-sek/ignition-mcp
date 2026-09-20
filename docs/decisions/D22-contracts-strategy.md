# D22 — `contracts/` Strategy: Schemas / Fixtures / Tests vs Codegen

**Status:** DECIDED

## Decision

采用：

> Contract-first、validation-driven，但 v1 不建立通用 Tool code generator。

`contracts/` 是跨两个 capability planes 的规范层，不是第三个 runtime package，也不是 DSL/compiler。

## Role of `contracts/`

```text
contracts/
    = semantic source of truth
    + schemas
    + fixtures
    + deployment policy
    + compatibility metadata
    + conformance tests

NOT
    = generated FastMCP implementation
    = generated Jython business logic
```

## Why No General Codegen

FastMCP 可以自然表达 rich JSON Schema：

- enum
- nested object
- item schema
- bounds
- defaults
- nullable
- typed output models

Ignition Built-in MCP Tool 当前验证过的 `resource.json` 参数能力主要是：

```text
name
description
type
required
```

已验证类型：

```text
string
object
array
number
integer
boolean
```

细粒度约束需要：

```text
description
+
Jython explicit validation
```

因此一个统一 DSL 如果强行生成两边，要么只能使用最低公分母，要么会演化为复杂 schema compiler。

v1 不做这种过度工程化。

## Repository Structure

建议：

```text
contracts/
├── README.md
├── shared/
│   ├── error-codes.yaml
│   ├── pagination.yaml
│   ├── permission-classes.yaml
│   ├── mutation-classes.yaml
│   └── budget-classes.yaml
├── tools/
│   ├── ignition-rest/
│   └── ignition-runtime/
├── resources/
├── prompts/
├── profiles/
│   ├── readonly.yaml
│   ├── operator.yaml
│   ├── configurator.yaml
│   └── full.yaml
├── schemas/
└── fixtures/
```

## Tool Contract Content

Per-tool contract 描述 semantic contract，不描述 implementation code。

可包含：

- Tool owner/server
- stability
- operation kind
- permission class
- mutation class
- budget class
- parameters
- semantic defaults
- bounds
- output model
- stable errors
- native requirements

重要区分：

```text
semantic contract
≠ transport schema
≠ Ignition resource schema
```

## Resource / Prompt Contract Content

contracts 不只描述 Tools。按需为另外两类 primitive 建立 contract：

Text Resource contract 可包含：

- 公开 identifier 与 URI（以真实 `resources/list` 为准）
- 所属 server/bundle
- mimeType / dataType
- 数据来源（例如某个 source schema）
- payload 大小预算（D10）
- deterministic build 要求
- stability

Prompt contract 可包含：

- 公开名称（以真实 `prompts/list` 为准）
- arguments 与类型
- handler 签名（`onPrompt(builder, arguments)`）
- 引用的 Tool / contract
- 稳定错误/边界语义（如适用）

Resource/Prompt contract 仍然只是 describe / validate / compare / test，不是 runtime interpreter。

## No Runtime Contract Interpreter

禁止：

```text
contracts YAML
→ runtime contract engine
→ dynamically decide business behavior
```

Contracts 只负责：

```text
describe
validate
compare
test
```

不执行业务操作。

## FastMCP

FastMCP 保持 code-first：

- Python type hints
- Pydantic models
- FastMCP native definitions

CI introspect actual schema，并验证与 contracts 一致。

不使用：

```text
YAML → generate Pydantic/FastMCP business implementation
```

## Ignition Runtime

Tool 保持显式：

```text
tools/.../resource.json      # Tool 定义
tools/.../onToolCalled.py
```

不从 contracts 自动生成 Jython business logic。

每个 Tool 仍是可读、可审查的真实实现。

Text Resource 与 Prompt 同样是显式项目资源：

```text
resources/.../resource.json + data.bin
prompts/.../resource.json + onPrompt.py
```

source schema 可以确定性派生为 Text Resource（见 “Allowed Derived Generation”）；Tool 定义 `resource.json` 与 Jython 业务代码仍然手写。

## Conformance Testing

解决重复信息漂移的主要机制是 tests，不是 codegen。

### FastMCP checks

验证：

- Tool name
- owner
- parameter names
- required/optional
- types
- enum/bounds where expressible
- output schema
- permission class
- mutation class
- budget class
- error taxonomy

### Runtime checks

验证：

- Tool `resource.json`
- supported parameter types
- parameter name/order
- handler signature
- optional semantics
- static rules
- behavioral fixtures
- live MCP behavior
- Text Resource `resource.json`、`data.bin` 与 mimeType/size 一致性
- Text Resource payload 与 source schema 的确定性一致
- Prompt `resource.json` arguments 与 `def onPrompt(builder, arguments)` 签名
- 最终 ZIP 中 Resources/Prompts inventory 与 manifest 一致

对于 enum 等无法完全表达在 `resource.json` 的约束，通过 parameterized behavioral tests 验证。

## Fixtures

复杂 Tool 可拥有：

```text
fixtures/<tool>/
```

Fixtures 应：

- small
- deterministic
- semantically meaningful

避免大型 snapshot 与动态 timestamp/correlation metadata 绑定。

## Output Schemas

复杂、复用或需要 fixture validation 的 output model 可以放：

```text
contracts/schemas/
```

简单 model 不必为了形式主义拆成独立文件。

## Shared Semantics

真正跨两个 Server 共享：

- error taxonomy
- correlation semantics
- pagination conventions
- batch conventions
- artifact references
- permission classes
- mutation classes
- budget terminology
- versioning terminology

不要求两个 Server 的 Tool surface 对称。

## Profile Manifests

`contracts/profiles/*.yaml` 作为 machine-readable deployment policy。

Installer 直接消费：

```text
profile allowlist
∩ compatible tools
=
Server Config explicit inventory
```

这不是 codegen，而是 declarative policy。

## Budget Contracts

Contracts 记录规范值与 hard ceiling。

实现可采用不同 runtime configuration，但：

```text
implementation hard ceiling > contract ceiling
→ CI FAIL
```

更严格 deployment limit 可以小于 contract ceiling。

## Error Taxonomy

Stable error codes 可以有 machine-readable single source of truth。

但不生成异常处理业务代码。

Contracts 用于 lint/validation。

## Allowed Derived Generation

允许生成：

- deterministic Designer ZIP
- bundle manifest
- SHA-256
- documentation tables
- compatibility reports
- resolved profile inventory
- test matrices
- source schema → deterministic Text Resource payload + resource metadata（例如已验证 Skill 的 `schema_resource.py` 行为）

这些属于 derived artifacts。

## Forbidden v1 Codegen

禁止：

- contracts → FastMCP business implementation
- contracts → Jython `onToolCalled.py`
- generic JSON Schema → Ignition **Tool 定义** `resource.json`
- generic contract → Jython business code
- OpenAPI → public MCP Tools
- runtime dynamically interpreting contracts

source schema → Text Resource 的派生不属于上述禁止项：它只生成发布用的 schema Text Resource（`resource.json` + `data.bin`），不生成 Tool 定义，也不生成业务代码。

## Future Narrow Resource Generator

本节只针对 **Tool 定义 `resource.json`** 的生成器。Text Resource 的 schema 确定性派生已在上文 "Allowed Derived Generation" 中允许，不需要新的 ADR。

未来可以单独重新评估只生成 Tool 定义 `resource.json` 的 Ignition-specific narrow generator。

前提：

- multiple production Tools 已证明稳定重复 shape
- 手写重复已成为实际维护问题
- schema 已稳定
- Designer round-trip evidence 足够
- 只使用真实 Designer 验证字段
- deterministic
- `--check` mode
- golden round-trip tests
- 不生成 business logic

需要新的 ADR，不在 D22 中预先承诺。

## Frozen Configuration

```yaml
decision: D22
status: DECIDED

contracts_role:
  normative_semantics: true
  runtime_package: false
  runtime_interpreter: false

model:
  contract_first: true
  validation_driven: true
  implementation_code_first: true

general_tool_codegen_v1: false

fastmcp:
  generated_from_contracts: false
  use_native_python_types_and_pydantic: true
  conformance_test_against_contracts: true

ignition_runtime:
  generated_handler: false
  generated_tool_definition_resource_json_v1: false
  explicit_tool_resource_json: true
  explicit_jython_handler: true
  generated_text_resource_from_source_schema: allowed
  generated_text_resource_deterministic: true
  generated_text_resource_contains_business_logic: false
  explicit_prompt_handler: true
  contract_conformance_tests: true

contracts_contents:
  shared_semantics: true
  per_tool_semantics: true
  per_resource_semantics: true
  per_prompt_semantics: true
  schemas: true
  fixtures: true
  permission_profiles: true
  compatibility_metadata: true
  implementation_logic: false

shared_semantics:
  - error_taxonomy
  - correlation
  - pagination
  - batch_conventions
  - permission_classes
  - mutation_classes
  - budget_classes
  - artifact_semantics
  - versioning_terms

derived_generation_allowed:
  - deterministic_designer_zip
  - bundle_manifest
  - sha256
  - documentation
  - compatibility_reports
  - resolved_profile_inventory
  - text_resource_from_source_schema

forbidden_codegen:
  fastmcp_business_logic: true
  jython_business_logic: true
  generic_json_schema_to_ignition_tool_definition_resource: true
  generic_contract_to_jython_business_code: true
  openapi_to_public_tools: true
  runtime_contract_interpreter: true

profile_manifests:
  machine_readable: true
  installer_consumes_directly: true

contract_ci:
  schema_validation: required
  fastmcp_schema_conformance: required
  native_resource_conformance: required
  native_behavior_fixtures: required
  resource_prompt_conformance: required
  error_code_lint: required
  budget_ceiling_check: required

future_resource_generator:
  allowed_to_reconsider: true
  requires_separate_decision: true
  scope_tool_definition_resource_json_only: true
  text_resource_schema_generation_already_allowed: true
  ignition_specific_only: true
  must_use_designer_verified_fields: true
  must_be_deterministic: true
  must_have_golden_roundtrip_tests: true
  must_not_generate_business_logic: true
```

## Pre-D26 consistency amendment
Extended contracts from per-Tool only to per-primitive where useful (optional `contracts/resources/` and `contracts/prompts/`, Resource/Prompt contract content, and Resource/Prompt conformance checks), and explicitly allowed deterministic derivation of a **Text Resource** (`resource.json` + `data.bin`) from a source schema, as the verified Skill's `schema_resource.py` does. The v1 codegen ban is narrowed to what it always meant: contracts → FastMCP business logic, contracts → Jython `onToolCalled.py`, generic JSON Schema → the Ignition **Tool definition** `resource.json`, and runtime contract interpretation. Related YAML keys were renamed for clarity (`generated_resource_json_v1` → `generated_tool_definition_resource_json_v1`, `generic_json_schema_to_ignition_resource` → `generic_json_schema_to_ignition_tool_definition_resource`). Contract-first philosophy, no general codegen, and the future-generator preconditions are otherwise unchanged.
