# D25 — Final Monorepo / Package Layout

**Status:** DECIDED

## Decision

采用：

> 一个 monorepo、两个 primary product packages、若干 repository-level supporting layers。

两个主要产品：

```text
packages/ignition-rest-mcp
packages/ignition-runtime-bundle
```

`contracts/`、`tests/`、`tooling/`、`docs/` 不是第三个 capability package。

## Repository Layout

```text
ignition-mcp/
│
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .editorconfig
│
├── packages/
│   ├── ignition-rest-mcp/
│   └── ignition-runtime-bundle/
│
├── contracts/
├── tests/
├── tooling/
├── docs/
├── examples/
│
└── .github/
    └── workflows/
```

## Naming

第二个 package 正式使用：

```text
ignition-runtime-bundle
```

理由：交付物是 MCP primitive bundle（Tools + Text Resources + Prompts），不再只是 Tools，因此不使用 `ignition-runtime-tools`。

不使用 `ignition-native-tools`，避免与 Native REST / Native API 等概念混淆。

`ignition-runtime-bundle` 是产品/artifact 名，**不是第三个 MCP Server**。Server 仍然是官方 MCP Module 提供的 canonical `ignition-runtime`。

Canonical Server naming：

```text
ignition-rest
ignition-runtime
```

## `ignition-rest-mcp`

唯一 publishable Python runtime package。

建议结构：

```text
packages/ignition-rest-mcp/
├── pyproject.toml
├── README.md
├── src/
│   └── ignition_rest_mcp/
│       ├── server.py
│       ├── config.py
│       ├── errors.py
│       ├── client/
│       ├── capabilities/
│       ├── middleware/
│       ├── services/
│       ├── tools/
│       ├── cli/
│       │   └── setup_native/
│       └── observability/
└── tests/
```

### Internal Layering

```text
tools/
    = MCP transport adapter

services/
    = use-case orchestration

client/
    = Ignition REST transport

capabilities/
    = OpenAPI/capability truth
```

Tool implementation 保持薄。

## GatewayClient Boundary

`GatewayClient` 负责：

- base URL
- AsyncClient lifecycle
- TLS
- auth header
- timeout
- safe bounded retry
- HTTP status mapping
- request/response transport

不负责：

- project transaction logic
- Perspective ZIP mutation
- capability policy
- mutation confirmation
- artifact lifecycle

避免形成巨型 client class。

## No Generic `utils/common`

不预建：

```text
utils/
helpers/
common/
misc/
```

共享逻辑必须有明确 domain ownership。

真正跨 domain 的抽取必须由实际重复驱动。

## `setup-native`

D20 CLI 随 `ignition-rest-mcp` Python distribution 发布：

```text
ignition-mcp setup-native ...
```

这是 operator tooling，不改变 Runtime capability ownership。

Server runtime 与 CLI 保持代码边界分离。

## `ignition-runtime-bundle`

不是 Python package。

它的产品本体是真实 Ignition Designer Project source，包含三类 MCP primitive：

```text
packages/ignition-runtime-bundle/
├── README.md
├── BUNDLE_VERSION
├── project/
│   ├── project.json
│   └── com.inductiveautomation.mcp/
│       ├── tools/
│       │   ├── bundle-info/
│       │   │   ├── resource.json
│       │   │   └── onToolCalled.py
│       │   ├── tag-browse/
│       │   ├── tag-read/
│       │   ├── tag-write/
│       │   └── ...
│       ├── resources/
│       │   └── contracts/
│       │       └── tag-read-output/
│       │           ├── resource.json
│       │           └── data.bin
│       └── prompts/
│           └── ...
└── tests/
```

目录名是 Designer resource path，不自动等于 public MCP identifier。Tool / Resource / Prompt 的公开 identifier 以真实 `tools/list`、`resources/list`、`prompts/list` discovery 为准（见 D05）。

不为了 monorepo 对称而添加 fake `pyproject.toml` / Python package。

## Runtime Primitive Design

v1 默认 Tool self-contained。

不预建大规模 shared Project Library。

如果将来多个真实 Tool 证明某一 helper 稳定重复，并且真实 Designer export 已验证 resource format，可再提取。

不能自己猜 Project Library ZIP layout。

Text Resource 与 Prompt 与 Tools 同属一个 Bundle Project：

- Text Resource 使用 `resources/<folders>/<name>/resource.json + data.bin`，可由 source schema 确定性派生（见 D22）。
- Prompt 使用 `prompts/<folders>/<name>/resource.json + onPrompt.py`。
- 三类 primitive 都与 Tool 一起 import/upgrade，不拆成独立 package。

## Bundle Version

Runtime Bundle version 单独存放：

```text
packages/ignition-runtime-bundle/BUNDLE_VERSION
```

它与 `resource.json.version` 完全不同。

## Generated Artifacts

生成到 root：

```text
dist/
```

默认 gitignored。

例如：

```text
ignition-runtime-bundle-0.1.0.zip
ignition-runtime-bundle-0.1.0.manifest.json
ignition-runtime-bundle-0.1.0.sha256
```

source control 保存源码、contracts、tests、build logic，不默认提交 derived binaries。

## `contracts/`

放 repository root：

```text
contracts/
├── shared/
├── tools/
│   ├── rest/
│   └── runtime/
├── resources/
├── prompts/
├── profiles/
├── schemas/
└── fixtures/
```

这是 cross-plane semantic layer。

不是 runtime package。

## Profiles

`readonly/operator/configurator/full` 放：

```text
contracts/profiles/
```

因为它们是 deployment/authorization policy，不是 Runtime Bundle Project capability source。

Runtime Bundle Project 说明“有哪些 Tools / Text Resources / Prompts”。

Profile 说明“允许暴露哪些 Tools”。

## Tests

### Package-local

External：

```text
packages/ignition-rest-mcp/tests/
```

用于 unit/component tests。

Runtime：

```text
packages/ignition-runtime-bundle/tests/
```

用于 static validation、behavior fixtures 等。

### Root cross-system tests

```text
tests/
├── harness/
│   ├── compose/
│   ├── gateway/
│   └── mcp_client/
├── integration/
│   ├── rest/
│   ├── runtime/
│   ├── installer/
│   └── perspective/
├── compatibility/
├── failure/
└── soak/
```

对应 D23 L3–L5。

## Test Fixtures

优先使用：

- small project fixtures
- REST config payloads
- SQL seed
- Tag definitions
- deterministic test data

不默认 commit 大型 `.gwbk`。

必要 `.gwbk` 必须带版本、hash、provenance 与明确 regression 原因。

## `tooling/`

Repository tooling：

```text
tooling/
├── native/
│   ├── build.py
│   ├── validate.py
│   └── manifest.py
├── contracts/
│   ├── lint.py
│   └── diff.py
└── release/
    └── compatibility_evidence.py
```

规则：

```text
tooling → product/contracts
product runtime ↛ tooling
```

`tooling/` 不承载生产业务逻辑。

## Existing Skill Logic

当前 verified Skill 中的 build/validation 规则应转化为 repo 自己维护的 tooling/tests。

不长期依赖外部 skill path 作为生产 build dependency。

具体迁移必须继续遵守 D01 license/clean-room policy。

## Python Workspace

Root `pyproject.toml`：

- uv workspace
- dev dependencies
- pytest
- lint/type tooling

`packages/ignition-rest-mcp/pyproject.toml`：

- publishable package metadata
- runtime dependencies
- console script

Runtime Tools 不需要 fake Python metadata。

`uv.lock` 只放 root，v1 使用单一 repository lock。

## Cross-Package Dependency Rule

External server runtime 不得依赖 monorepo relative path，例如：

```text
../../ignition-runtime-bundle/project
```

`setup-native` 如需 Runtime artifact，应使用：

- explicit `--bundle`
- release artifact
- 正式 package data mechanism（未来如决定）

不假设 monorepo filesystem。

## Runtime Bundle Distribution

Canonical：

> Runtime ZIP 是独立 release artifact。

v1 不 embed 一份 Runtime Bundle 到 Python wheel。

External Python version 与 Runtime Bundle version 独立。

不使用 repository-global SemVer 强绑两个产品。

## Documentation

```text
docs/
├── architecture/
├── decisions/
├── research/
├── operations/
└── development/
```

`research/` 保存 evidence/investigation。

`decisions/` 保存 frozen architecture。

Implementation Agent 应优先服从 decisions。

D01–D26 后建立：

```text
docs/decisions/INDEX.md
```

记录：

- decision ID
- title
- status
- one-line decision
- supersedes/amendment

以后修改旧决定需通过新 decision/amendment 明确留痕。

## Examples

只放真正可执行、长期维护的小例子。

不用于保存 POC、old implementation、random scripts。

## Optional Unified Facade

D24 v1 scope = false。

因此当前不预创建：

```text
packages/ignition-unified/
```

未来如真正实现，倾向：

```text
extras/ignition-unified/
```

从目录层面区分 canonical products 与 optional adapters。

## Final Recommended Tree

```text
ignition-mcp/
│
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .editorconfig
│
├── packages/
│   │
│   ├── ignition-rest-mcp/
│   │   ├── pyproject.toml
│   │   ├── README.md
│   │   ├── src/
│   │   │   └── ignition_rest_mcp/
│   │   │       ├── server.py
│   │   │       ├── config.py
│   │   │       ├── errors.py
│   │   │       ├── client/
│   │   │       ├── capabilities/
│   │   │       ├── middleware/
│   │   │       ├── services/
│   │   │       ├── tools/
│   │   │       ├── cli/
│   │   │       └── observability/
│   │   └── tests/
│   │
│   └── ignition-runtime-bundle/
│       ├── README.md
│       ├── BUNDLE_VERSION
│       ├── project/
│       │   ├── project.json
│       │   └── com.inductiveautomation.mcp/
│       │       ├── tools/
│       │       ├── resources/
│       │       └── prompts/
│       └── tests/
│
├── contracts/
│   ├── README.md
│   ├── shared/
│   ├── tools/
│   │   ├── rest/
│   │   └── runtime/
│   ├── resources/
│   ├── prompts/
│   ├── profiles/
│   ├── schemas/
│   └── fixtures/
│
├── tests/
│   ├── harness/
│   ├── integration/
│   ├── compatibility/
│   ├── failure/
│   └── soak/
│
├── tooling/
│   ├── native/
│   ├── contracts/
│   └── release/
│
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── research/
│   ├── operations/
│   └── development/
│
├── examples/
│
└── .github/
    └── workflows/
```

## Frozen Configuration

```yaml
decision: D25
status: DECIDED

repository:
  model: monorepo
  unified_repo_version: false

primary_products:
  - ignition-rest-mcp
  - ignition-runtime-bundle

packages:
  directory: packages

ignition_rest_mcp:
  publishable_python_package: true
  source_layout: src
  async_gateway_client: true

  responsibilities:
    - external_fastmcp_server
    - native_rest_capability_plane
    - operator_cli
    - setup_native_cli

  runtime_bundle_implementation_owner: false

ignition_runtime_bundle:
  publishable_python_package: false
  designer_project_source: true
  name: ignition-runtime-bundle
  source_root: project
  primitives:
    - tools
    - text_resources
    - prompts

  required_layout:
    - project.json
    - com.inductiveautomation.mcp/tools
    - com.inductiveautomation.mcp/resources
    - com.inductiveautomation.mcp/prompts

  bundle_version_file: BUNDLE_VERSION

  default_tool_design:
    self_contained: true
    shared_project_library_required: false

  text_resource_design:
    resource_json_plus_data_bin: true
    derivable_from_source_schema: true

  prompt_design:
    resource_json_plus_onprompt_py: true

contracts:
  repository_root: true
  runtime_package: false

  contains:
    - shared_semantics
    - tool_contracts
    - resource_contracts
    - prompt_contracts
    - profile_policy
    - schemas
    - fixtures

tests:
  package_local_unit_tests: true
  repository_cross_system_tests: true

  root_categories:
    - harness
    - integration
    - compatibility
    - failure
    - soak

tooling:
  repository_root: true
  publishable_product: false
  production_runtime_dependency: forbidden

generated_artifacts:
  root: dist
  committed_by_default: false

versioning:
  external_and_runtime_independent: true
  repository_global_semver: false

runtime_bundle_distribution:
  canonical: separate_release_artifact
  embedded_in_python_wheel_v1: false

docs:
  architecture: true
  decisions: true
  research: true
  operations: true
  development: true

architecture_decisions:
  canonical_location: docs/decisions
  single_index: true

optional_unified_facade:
  directory_precreated: false
  canonical_package: false
  future_location_preference: extras/ignition-unified

anti_patterns:
  shared_common_package_without_proven_need: forbidden
  generic_utils_bucket: forbidden
  runtime_depend_on_tooling: forbidden
  rest_runtime_depend_on_monorepo_relative_paths: forbidden
  generated_dist_committed_by_default: false
```

## Pre-D26 consistency amendment
Renamed the second product package from `ignition-runtime-tools` to **`ignition-runtime-bundle`** (dist artifacts follow: `ignition-runtime-bundle-x.y.z.zip/.manifest.json/.sha256`, and the YAML key `ignition_runtime_tools` becomes `ignition_runtime_bundle`) because the shipped product is an MCP primitive bundle, not merely a collection of Tools. The Designer project layout now includes `resources/` and `prompts/` next to `tools/` under `com.inductiveautomation.mcp/`, the required layout and `contracts/` contents cover the three primitives, and a note records that Designer folder names are not automatically public MCP identifiers (see D05). The canonical MCP server remains the official Module's `ignition-runtime`; `ignition-runtime-bundle` is a product/artifact name, not a third server. Monorepo model, package boundaries, tests, tooling, versioning independence, docs layout, and the optional-facade stance are otherwise unchanged.
