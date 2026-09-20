# D23 — Testing Matrix and Real Ignition Gateway CI

**Status:** DECIDED

## Decision

采用：

> 多层测试金字塔 + ephemeral real Gateway integration CI + 精确版本矩阵 + release compatibility certification。

Built-in MCP Tools 不能只凭静态检查、mock 或 ZIP 结构验证宣布兼容。必须实际运行在 Ignition Gateway 中，并通过真实 MCP protocol：

```text
initialize
→ tools/list
→ tools/call
→ state verification
```

## Test Layers

```text
L0  Static / Contract
L1  Unit
L2  Artifact / Build
L3  Real Gateway REST Integration
L4  Real Gateway MCP Integration
L5  Extended / Mutation / Failure / Soak
```

| Layer | Real Gateway | Purpose |
|---|---:|---|
| L0 | No | schema、contract、lint、static rules |
| L1 | No | pure logic、policy、error mapping、budget |
| L2 | No | ZIP、manifest、deterministic build |
| L3 | Yes | External FastMCP ↔ Native REST |
| L4 | Yes | Official MCP Module ↔ Jython Tools |
| L5 | Yes | mutation、failure、concurrency、leak/soak |

## Real Gateway

使用官方 Ignition Docker image。

CI：

```text
CI Runner
→ Docker Compose
→ fresh isolated Gateway
→ tests
→ docker compose down -v
```

不维护共享、长期运行的 CI Gateway。

### CI-owned Gateway provisioning

L3–L5 自动化测试所需的真实 Gateway 由 CI Runner 自己创建和销毁。Phase 0 baseline 使用精确镜像 `inductiveautomation/ignition:8.3.8`。除非专门执行人工环境兼容性调查，否则**不得把“由用户提供一台现成真实 Gateway”作为开发、G0 或 release certification 的前置条件**。

开发与认证的标准路径是：repo checkout → CI 拉取官方 exact-patch Docker image → 创建 fresh isolated Gateway → 安装/挂载精确 MCP Module fixture → 执行真实协议测试 → 收集证据 → `docker compose down -v`。

## Version Pinning

Certification 禁止使用浮动：

```text
8.3
latest
```

Matrix 使用 exact patch tags，并在 release certification 尽量 pin image digest。

初始 candidate matrix：

```text
Gateway 8.3.8
Gateway 8.3.9
```

MCP Module 使用 exact version + build identity。

这些 tuple 在通过 release suite 前仍不是 `SUPPORTED`。

## MCP Module Artifact

MCP Module 必须使用 exact version + build identity，并在执行前验证 SHA-256。禁止自动下载 “latest”。

本项目允许两种受控来源：

1. **Repo-pinned CI fixture**：当项目所有者确认 artifact 来自官方渠道并明确授权该 repository 保存它时，可以把 `.modl` 作为测试 fixture 提交到 repo。必须同时保存 provenance metadata 与固定 SHA-256；CI 不信任 PR 中任意变化的 hash，必须对预期 artifact 路径与已审查 checksum 做 fail-closed 校验。
2. **Protected artifact injection**：当 artifact 不适合提交、授权范围不足或后续版本需要保密时，使用受保护 CI artifact/secret 注入，例如 `MCP_MODULE_ARTIFACT` + `MCP_MODULE_SHA256`。

当前 Phase 0 baseline 采用第 1 种：项目所有者提供并授权提交的官方 MCP Module `1.3.5.2026021307-SNAPSHOT`（build `2026021307`）。

CI：

```text
load pinned artifact
→ verify SHA-256
→ install/mount into fresh Gateway
→ verify actual module version/build
```


## Public vs Protected CI

### Public CI

运行：

- L0
- L1
- L2
- External tests that need no protected artifact
- contracts

### Trusted Live CI

Repo-pinned MCP Module fixture 使 baseline L4 不再依赖 secret，但 live workflow 仍属于受信任执行路径：

- trusted branch push / maintainer-controlled PR 可运行 baseline L3/L4；
- 未信任 fork 不允许运行可替换 module binary 的 live job；
- L5、release certification、任何需要额外 credentials/licensed artifacts 的 job 继续使用 protected environment。

若将来恢复 protected artifact injection，则未信任 fork 同样不得获得 secrets/artifacts。

## Licensing

CI 不使用 production Ignition license。

使用 ephemeral trial Gateway。

正常 CI 不开发 automatic trial-reset workflow；应通过拆分/并行优化保证 job 在 trial 窗口内完成。

## Readiness

禁止固定：

```text
sleep 60
```

采用 bounded polling：

```text
start
→ commissioned?
→ REST reachable?
→ required module RUNNING?
→ OpenAPI available?
→ continue
```

要求 per-attempt timeout、overall timeout、failure diagnostics。

无论成功失败都 cleanup。

## L0 — Static / Contract

每个 PR 必跑：

- contract schema validation
- error taxonomy lint
- budget ceilings
- permission profile validation
- FastMCP contract conformance
- Runtime Tool `resource.json` validation
- six supported parameter types
- parameter order == handler argument order
- `def onToolCalled` first line
- single top-level def
- Text Resource `resource.json` / `data.bin` metadata、layout 与 mimeType/size 校验
- Text Resource payload 与 source schema 的确定性一致
- Prompt `resource.json` arguments 校验
- `def onPrompt(builder, arguments)` 签名
- deterministic resource layout（tools/ + resources/ + prompts/）
- Jython 2.7 static restrictions

CPython 3 pass 不能替代 Jython compatibility evidence。

## L1 — Unit

External：

- GatewayClient
- URL/path construction
- auth headers
- redirect disabled
- timeout
- bounded retry
- typed error mapping
- capability registry
- pagination
- budgets
- artifact IDs
- ZIP manipulation
- concurrency guards

Runtime：

只测试可安全抽离的纯逻辑：

- validation
- normalization
- pagination
- serialization helpers
- budget calculations

不建立假装等同真实 Ignition 的巨大 FakeIgnition runtime。

## L2 — Artifact / Build

验证：

- final Designer ZIP layout
- manifest
- SHA-256
- bundle version
- profile manifests
- Tool / Text Resource / Prompt inventory（与 manifest 声明一致）
- `data.bin` 的 size / MIME / 内容与 Resource metadata 一致
- prompts 的 onPrompt.py 与 arguments 一致
- reproducibility

必须对**最终生成 ZIP**重新解包验证，而不是只检查 source tree。

## L3 — External FastMCP ↔ Real Gateway

完整路径：

```text
MCP request
→ FastMCP Tool
→ service
→ AsyncClient
→ real Ignition REST
→ normalized response
→ MCP result
```

至少覆盖：

- `gateway_diagnose`
- Gateway info
- projects
- config resources
- artifact/export path
- capability registry
- v1 REST mutations

每个 matrix row 都获取 `/openapi.json`，记录 `openapiSha256`，验证 required/optional capabilities。

## L4 — Runtime MCP Bundle Through Real MCP Protocol

流程：

```text
Gateway
→ exact MCP Module
→ setup-native
→ import Runtime Bundle Project
→ create security
→ create credential
→ create explicit Server Config
→ verify standalone/non-inheritable
→ MCP initialize
→ tools/list
→ resources/list
→ resources/read
→ prompts/list
→ prompts/get
→ tools/call
```

仅直接调用 `onToolCalled()` / 直接读取项目文件不足以通过 integration。

必须覆盖真实：

- MCP discovery（三类 primitive）
- server-config
- project resolution
- permissions
- transport
- parameter serialization
- Resource 发布与读取
- Prompt 发布与实例化
- module behavior

Text Resource 可读只能证明 Resource 发布成功，**不能**证明 `tools/list` 已有 `outputSchema`、成功返回 `structuredContent` 或失败返回 `isError`。这些绑定必须分别真实验证并记录。D27 已记录 baseline Module build 的 `structuredContent` / `isError` 成功与 native `outputSchema` 缺失；只有精确 D27 identity 可据此产生 `VERIFIED_WITH_LIMITATION`。

## No LLM in CI

核心 correctness CI 不依赖：

- ChatGPT
- Claude
- DeepSeek
- 任何 LLM planner

使用 deterministic MCP client。

LLM eval 如有需要，属于单独 `eval/`，不成为核心 release gate。

## Exact Primitive Inventory

Profile 测试：

```text
actual tools == expected tools
actual resources == expected resources
actual prompts == expected prompts
```

不能只检查 expected subset。

多暴露 Tool 也是 security failure。Resource/Prompt inventory 不匹配按失败报告；这里验证的是 discovery 结果与 bundle 预期 inventory 一致，不假设存在 Resource/Prompt allowlist（见 D09/D20）。

同时验证 Tool schema：

- name
- description
- parameter names
- required flags
- types

以及 Resource/Prompt metadata：

- identifier / URI
- mimeType、size
- Prompt arguments 与类型

## Real Smoke Call Per Tool

每个 Tool 至少一个最小真实 `tools/call` smoke test。

例：

```text
tag_read → one known tag
tag_browse → bounded folder
historian → tiny range
```

每个已发布的 Text Resource 至少一次真实 `resources/read`，每个已发布的 Prompt 至少一次真实 `prompts/get`。

不能只靠 `tools/list`。

## Mutation Tests

只能在 disposable namespace：

```text
[default]__mcp_ci/<run-id>/
__mcp_ci_project_<run-id>
mcp_ci_<run-id>
```

必须验证 pre/post state。

不能只凭：

```text
MCP call success / HTTP success
```

D06 已禁止 universal `{ok,result,error}` envelope，因此这里不引用 `ok=true`；必须实际验证 mutation 后的 Gateway 状态。

## Database Integration

使用独立 local PostgreSQL container。

不依赖真实 production DB。

测试：

- approved Named Query alias 的正常调用
- Value 参数绑定
- bounded row limits
- timeout
- unapproved alias 被拒绝
- dynamic datasource 选择被拒绝
- unsafe query contract（例如 QueryString 参数、generic SQL mutation）被拒绝
- large-result budget

不测试 arbitrary SQL，因为 D14 不暴露它：v1 只存在 approved Named Query registry，没有 `allowed SELECT` / `forbidden mutation` SQL 语句层面的公共入口。

## Perspective / ZIP Integration

必须测试：

```text
export
→ inspect
→ modify
→ validate
→ import
→ re-export
→ verify
```

以及：

- concurrent modification abort
- backup creation
- invalid ZIP rejection
- path traversal rejection
- unexpected resource path rejection
- partial failure reporting

## Fixture Modes

### Fresh

从官方 image 开始，用于测试：

- setup-native
- security provisioning
- module install
- project import
- server-config
- clean deployment

### Seeded

用于 domain integration：

- memory tags
- alarms
- historian samples
- test project
- DB connection

`.gwbk` 不作为唯一初始化方式，以免掩盖 installer regression。

## Independent Fixture Provisioning

尽量不要使用“被测试的 public MCP Tool”建立自身 fixture。

优先使用：

- Native config API
- bootstrap fixtures
- SQL seed
- prebuilt project

降低 correlated false positives。

## Failure Injection

至少覆盖：

External:

- Gateway unreachable
- timeout
- 401
- 403
- missing capability
- 5xx
- stale OpenAPI
- invalid response
- cancellation

Runtime:

- invalid tag path
- Bad Quality
- timeout
- permission denied
- missing provider
- unsupported capability
- oversized request
- invalid enum

Project transactions:

- concurrent modification
- verification failure
- backup failure

并验证：

- original cause retained
- stable error code
- correlationId
- `outcome_unknown`

## Scheduled Soak

Nightly/weekly 运行：

- repeated MCP connect/disconnect
- repeated tag reads
- pagination loops
- artifact lifecycle
- export cycles
- bounded concurrency

观察：

- RSS
- file descriptors
- HTTP connections
- task count
- temp artifacts
- Gateway thread behavior
- volume growth

不是 performance leaderboard，而是 leak sentinel。

## Nightly Canary

使用 moving `8.3-nightly` 作为 canary。

目的：

> 提前发现未来 Ignition breakage。

Canary PASS 不能产生 `SUPPORTED` 状态。

初期 canary failure 不阻塞正常 stable release。

## Pipelines

### PR

- L0
- L1
- L2
- affected real Gateway tests
- Runtime changes 时 baseline + current stable rows

### Scheduled

- full matrix
- failure tests
- soak
- nightly canary

### Release

必须：

- clean checkout
- clean build
- all declared supported tuples
- L0–L5
- artifact hash verification
- fresh setup-native
- upgrade path
- compatibility evidence

## Upgrade Tests

Release 必须测试：

```text
fresh → new version
```

以及：

```text
latest supported previous
→ plan
→ upgrade
→ verify
```

确保 profile 不意外扩大权限。

## Compatibility Evidence

每个真实 tuple 产出 machine-readable evidence：

- gatewayVersion
- gatewayBuild
- image digest
- mcpModuleVersion
- mcpModuleBuild
- bundleVersion
- bundle SHA-256
- OpenAPI SHA-256
- test result
- initialize status
- toolsList status
- resourcesList / resourcesRead status
- promptsList / promptsGet status
- native response binding status（`NATIVE_BINDING_PENDING` 或已验证）
- smoke status

D21 tested tuples 必须来源于这些 CI evidence，而不是人工手写。

## Diagnostics Artifacts

失败时保存：

- JUnit XML
- compatibility evidence
- Docker logs
- relevant Gateway logs
- OpenAPI fingerprint
- tools/list
- resources/list + resources/read 结果
- prompts/list + prompts/get 结果
- native response binding status
- bundle_info
- setup-native plan

必须 redact：

- API token
- Authorization
- cookies
- passwords
- DB credentials
- secrets

## Production Safety

Destructive test runner 只能连接 localhost/container network 或明确 CI instance。

必须验证 CI marker / expected Gateway identity。

不满足直接拒绝 destructive tests。

## Frozen Configuration

```yaml
decision: D23
status: DECIDED

test_layers:
  L0_static_contract: required
  L1_unit: required
  L2_artifact_build: required
  L3_real_gateway_rest: required
  L4_real_gateway_mcp: required
  L5_extended_failure_soak: required

real_gateway:
  official_docker_image: true
  ephemeral_per_job: true
  shared_persistent_ci_gateway: false
  exact_patch_tags: true
  floating_8_3_or_latest_for_certification: false
  pin_digest_for_release: true

initial_matrix:
  gateway:
    - 8.3.8
    - 8.3.9
  supported_status_requires_green_release_suite: true

mcp_module:
  exact_version_and_build: required
  stored_in_repo: allowed_when_owner_authorized_and_provenance_pinned
  baseline_phase0_artifact_in_repo: true
  automatic_latest_download: false
  protected_artifact_injection: optional_for_nonredistributable_or_secret_artifacts
  sha256_verify: true

licensing:
  production_license_in_ci: forbidden
  use_ephemeral_trial: true
  automatic_trial_reset_normal_ci: false

runtime_live_test:
  real_mcp_transport: required
  initialize: required
  tools_list: required
  exact_profile_inventory: required
  tools_call: required
  resources_list: required
  resources_read: required
  prompts_list: required
  prompts_get: required
  text_resource_readable_proves_output_binding: false
  native_response_binding_status_recorded: required
  direct_handler_call_only_is_insufficient: true

llm_dependency:
  required_for_ci: false

fixtures:
  fresh_gateway_mode: true
  deterministic_seeded_mode: true
  test_namespace_isolated: true
  use_public_tools_to_create_their_own_fixture: discouraged

mutation_tests:
  ephemeral_gateway_only: true
  verify_pre_and_post_state: true
  production_target: forbidden

external_services:
  database: isolated_local_container
  external_production_dependencies: forbidden

failure_injection:
  required: true

soak:
  scheduled: true
  bounded_concurrency: true
  resource_leak_detection: true
  every_pr: false

nightly_canary:
  ignition_8_3_nightly: true
  counts_as_supported: false
  initially_release_blocking: false

pipelines:
  pull_request:
    static_unit_build: true
    affected_real_gateway_tests: true

  scheduled:
    full_matrix: true
    failure_tests: true
    soak: true
    nightly_canary: true

  release:
    clean_build: true
    all_declared_supported_tuples: true
    full_L0_to_L5: true
    fresh_install: true
    upgrade_path: true
    generate_compatibility_evidence: true

compatibility_evidence:
  machine_readable: true
  include_gateway_build: true
  include_image_digest: true
  include_mcp_module_build: true
  include_bundle_sha256: true
  include_openapi_sha256: true
  include_resource_prompt_results: true
  include_native_response_binding_status: true
  feeds_D21_tested_tuples: true

cleanup:
  always_destroy_container: true
  always_remove_ephemeral_volumes: true

diagnostic_artifacts:
  upload_on_failure: true
  redact_secrets: required
```

## Pre-D26 consistency amendment
C03 — mutation verification no longer refers to `ok=true`, because D06 rejects a universal `{ok,result,error}` envelope; the requirement is now stated as “do not trust MCP call success / HTTP success alone”.

C04 — the database integration tests are rephrased around D14's approved Named Query registry: approved aliases, Value parameters, fixed datasource policy, bounded results, and rejection of unapproved aliases / dynamic datasource / unsafe query contracts. There is no arbitrary-SQL surface to test.

Runtime MCP primitives — L0/L2/L4 now cover Text Resources and Prompts as well as Tools: Resource metadata/layout and `data.bin` size/MIME/content, Prompt arguments and `def onPrompt(builder, arguments)`, deterministic ZIP inventory, real `resources/list` / `resources/read` and `prompts/list` / `prompts/get`, and compatibility evidence that records those results plus the native response binding status. Test layers, matrix, fixtures, failure injection, soak, and pipelines are otherwise unchanged.


## D23 Amendment — CI-owned Gateway and repo-pinned MCP Module fixture

Phase 0 clarification: real Gateway evidence is produced by CI-owned ephemeral official Docker containers; a user-supplied Gateway is not a prerequisite. The project owner has explicitly authorized the official MCP Module artifact supplied for Phase 0 to be committed as a checksum-pinned test fixture. This amendment replaces the earlier blanket `stored_in_repo: false` rule while preserving exact-version, provenance, checksum, trust-boundary, cleanup, and no-automatic-latest requirements.


## D27 amendment — G0 limitation evidence

G0 live CI may pass with native response-binding status `VERIFIED_WITH_LIMITATION` only when all non-outputSchema checks pass and the exact D27 Gateway/Module/artifact identity matches. Any other tuple missing native `outputSchema` remains a G0 failure.
