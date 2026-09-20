# D20 — Ignition Official MCP Server Setup Automation

**Status:** DECIDED

## Decision

采用：

> 受控、声明式、可验证的自动化。

不采用全手工，也不采用“一条命令静默完成所有 Gateway 高权限动作”的全自动模型。

提供 operator/deployment CLI：

```text
ignition-mcp setup-native doctor
ignition-mcp setup-native plan
ignition-mcp setup-native apply
ignition-mcp setup-native verify
ignition-mcp setup-native install-module
```

这些能力不是 MCP Tools。

## `doctor`

完全只读，用于检查：

- Gateway reachable
- Gateway version
- `/openapi.json`
- MCP Module installed / version / build / health
- MCP server-config resource capability
- project import capability
- security-level capability
- API token capability
- Runtime Bundle Project 是否存在
- project 是否 standalone / non-inheritable
- expected Server Config 是否存在
- MCP endpoint initialize
- `tools/list` 与 expected Tool inventory
- `resources/list` 与 expected Resource inventory
- `prompts/list` 与 expected Prompt inventory
- `bundle_info`

`doctor` 不修改任何 Gateway 状态。

## `plan`

生成 current state 与 desired state 的声明式 diff。

示例：

```text
CREATE project
CREATE security level
CREATE MCP server config
SET explicit tools
NO CHANGE ...
```

必须明确：

```text
No changes have been applied.
```

## `apply`

### 默认 Profile

默认只部署：

```text
readonly
```

额外 profile 必须显式选择，例如：

```text
--profile readonly
--profile operator
```

不默认部署 configurator/full。

## Bundle Project

Runtime Bundle Project 可以自动导入。

要求：

- 使用 Native REST project import。
- project 必须 standalone / `inheritable=false`。
- 已存在 project 不得 blind overwrite。
- 必须先判断是否为我们管理的 deployment。
- 未知来源同名 project：拒绝接管。
- upgrade 使用 D16 transaction/concurrency/backup policy。
- bundle 的 Tools / Text Resources / Prompts 随同一 project import 一起交付。

## MCP Server Config

允许完全自动 provision，但：

- 只走 Native REST resource API。
- 禁止直接写 Gateway filesystem。
- production 禁止 `tools: "*"`。
- 必须使用 explicit Tool inventory。
- 新增 Tool 不自动进入现有 Server Config。
- 不假设 Module 对 Resources/Prompts 提供与 Tool 等价的 allowlist 或授权开关；未经验证的 Resource/Prompt 控制不得写入 desired state。

## Security Levels

支持自动检查。

默认：

```text
auto modify = false
```

只有显式：

```text
--provision-security-levels
```

才允许修改。

要求：

```text
GET current singleton
→ preserve existing tree
→ minimal structural change
→ optimistic precondition
→ PUT
→ GET
→ structural verification
```

未知 schema/topology 必须 abort，不允许猜测。

Security adapter 必须 version-aware，尤其 EA MCP Module 下不能假定任意 Security Level hierarchy 都可工作。

## Runtime API Token

支持检测 existing credential。

默认不自动创建。

只有显式：

```text
--create-runtime-token
```

才创建。

原则：

- 尽量每 privilege profile 使用独立 credential。
- secret 不写日志。
- secret 不写 audit payload。
- secret 不生成到 shell command。
- credential storage 必须使用安全权限（例如 `0600`）。
- 不再回显已保存 secret。

## MCP Module Installation

### 默认

`setup-native apply` 发现 MCP Module 不存在时：

```text
MCP_MODULE_MISSING
→ abort with remediation
```

不自动联网下载安装。

### 显式安装

支持：

```text
ignition-mcp setup-native install-module --file ./module.modl
```

v1 只接受本地 trusted `.modl`。

安装流程：

```text
verify local artifact/hash
→ upload
→ inspect certificate
→ explicit certificate acceptance if required
→ inspect EULA
→ explicit EULA acceptance if required
→ install
→ verify module identity/build
```

禁止：

- silent certificate acceptance
- silent EULA acceptance
- 自动下载 “latest”
- 自动 upgrade

## Upgrade Behavior

MCP Module：

```text
missing          → explicit install
compatible       → continue
untested         → warning/fail per compatibility policy
known incompatible → fail
newer available  → no automatic upgrade
```

Runtime Bundle 升级必须走 plan/apply/verify。

## Idempotent Reconciliation

重复执行相同 setup 应为：

```text
first run:
  CREATE ...

subsequent run:
  NO CHANGE
```

禁止重复创建 token、重复 import、重复修改 singleton security tree。

目标模型：

```text
Desired State
→ Observe
→ Diff
→ Apply Minimum Changes
→ Verify
```

## Transaction Model

setup 跨多个 Ignition resources，不宣称 atomic。

流程：

```text
preflight
→ snapshot/backup
→ bounded ordered mutations
→ verify each phase
→ best-effort rollback where provably safe
→ explicit partial-failure report otherwise
```

推荐顺序：

```text
1. preflight
2. backup/snapshot
3. provision Security Level if requested
4. provision credential if requested
5. import/update Runtime Bundle Project
6. create Server Config disabled
7. validate
8. enable Server Config
9. MCP initialize
10. exact tools/list verification
11. exact resources/list + prompts/list verification
12. bundle_info
```

## Verification

正式部署成功必须实际通过：

```text
HTTP endpoint reachable
→ MCP initialize
→ tools/list
→ actual tools == expected profile inventory
→ resources/list == expected Resource inventory
→ prompts/list == expected Prompt inventory
→ resources/read / prompts/get 最小 smoke（对应 primitive 存在时）
→ bundle_info
```

`actual tools ⊃ expected tools` 也视为失败。Resource/Prompt inventory 不匹配按失败报告；不假设存在 Resource/Prompt allowlist，只验证实际 discovery 结果与期望 inventory 一致。

不在生产 setup 中执行 mutation smoke tests。Resource/Prompt 的 read/get smoke 不是 mutation。

## Frozen Configuration

```yaml
decision: D20
status: DECIDED

native_setup:
  first_class_cli: true
  exposed_as_mcp_tool: false

commands:
  - doctor
  - plan
  - apply
  - verify
  - install-module

model:
  declarative: true
  idempotent: true
  minimum-change-reconciliation: true

default_profile:
  - readonly

tool_project:
  auto_import: true
  blind_overwrite: false
  require_standalone_non_inheritable: true
  managed_project_precondition: true
  upgrade_uses_D16_transaction_policy: true
  delivers: [tools, text_resources, prompts]

server_config:
  auto_provision: true
  native_rest_only: true
  direct_filesystem_write: false
  wildcard_tools_in_production: false
  explicit_tool_inventory: true
  resource_prompt_allowlist_assumed: false

security_levels:
  detect: true
  auto_modify_by_default: false
  provision_opt_in: true
  version_aware_adapter: true
  preserve_existing_tree: true
  optimistic_precondition: true

runtime_api_token:
  detect_existing: true
  auto_create_by_default: false
  create_opt_in: true
  dedicated_per_privilege_profile: preferred
  log_secret: false

mcp_module:
  auto_detect: true
  automatic_install_during_apply: false
  local_modl_install_supported: true
  automatic_internet_download_v1: false
  silent_certificate_acceptance: false
  silent_eula_acceptance: false
  automatic_upgrade: false

verification:
  initialize: required
  tools_list: required
  exact_inventory_match: required
  resources_list: required
  expected_resource_inventory: required
  prompts_list: required
  expected_prompt_inventory: required
  resource_read_smoke: where_present
  prompt_get_smoke: where_present
  bundle_info: required
  production_mutation_smoke_test: false

transactionality:
  claim_atomic: false
  preflight_before_mutation: true
  backup_before_replace: true
  safe_best_effort_rollback: true
  partial_failure_reported_explicitly: true
```

## Pre-D26 consistency amendment
`doctor`/`plan`/`apply`/`verify` now treat the Runtime product as an MCP primitive bundle: the Runtime Bundle Project delivers Tools + Text Resources + Prompts, deployment verification checks exact Tool inventory **plus** expected Resource and Prompt inventories, and performs minimal `resources/read` / `prompts/get` smoke calls where those primitives exist. Server Config Tool allowlisting is verified exactly as before; no Resource/Prompt allowlist is assumed or written into desired state. Setup model, security-level handling, token policy, module installation, and transactionality are unchanged.
