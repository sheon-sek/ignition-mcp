# Ignition MCP

两个 MCP server，让 AI agent 能够读取、并在分层安全规则下修改
[Inductive Automation Ignition](https://inductiveautomation.com/) Gateway。

| Server | 平面 | 形态 | 运行位置 | 启动方式 |
| --- | --- | --- | --- | --- |
| `ignition-rest` | REST | FastMCP 4 server（Python 3.11+，Streamable HTTP），封装经过筛选的 Ignition Native REST 操作。 | 独立进程。 | `ignition-rest-mcp` |
| `ignition-runtime` | Runtime | 一组 Jython Tools、Text Resources 和 Prompts 构成的 bundle，调用 `system.*`。 | 运行在 Gateway 上，由 Ignition Official MCP Module 承载。 | `ignition-mcp setup-native apply`（或 `./scripts/deploy-runtime-bundle.sh`） |

两个平面都是一等公民、彼此独立：只部署其中任何一个都可以。能力归属是**排他**的 —— 一个操作只属于
其中一个平面。凡是官方 REST endpoint 在语义上完整覆盖的操作都归 Native REST；其余归 Runtime 平面。
这里没有任意 REST 请求 Tool、没有任意 SQL（只允许已批准的 Named Query），也没有 WebDev 桥接。这些规则
以 [`docs/decisions/`](docs/decisions/INDEX.md) 为准，具备约束力。

> 本文档是 [`README.md`](README.md) 的中文译本。英文版为准（canonical），如有歧义以英文版为准。
> Tool 名、环境变量、flag、错误码、文件路径与实现标识符一律保留英文原文。

## 状态

v1 已交付。Phase 0–6（关卡 G0–G6）全部关闭并合并；每个 phase 的交付记录在
[`docs/development/`](docs/development/) 下。两个 Gateway/Module 组合已实机验证：

| Gateway | MCP Module | G6 关卡 | Native 响应绑定 |
| --- | --- | --- | --- |
| Ignition 8.3.8 (`b2026071409`) | `1.3.5.2026021307-SNAPSHOT` (`b2026021307`) | `VERIFIED` | `VERIFIED_WITH_LIMITATION`（D27） |
| Ignition 8.3.9 (`b2026082511`) | 同一 build | `VERIFIED` | `UNVERIFIED_LIMITATION` / `FAILED_NATIVE_BINDING` |

Module 会返回 `structuredContent` 和 `isError`，但不发布 Tool `outputSchema`；因此本仓库自有的
`contracts/schemas/` JSON Schema 就是强制的输出契约（D27）。关卡只会记录
`VERIFIED` / `VERIFIED_WITH_LIMITATION` / `UNVERIFIED_LIMITATION` / `UNTESTED` —— **绝不**记录生产环境的
`SUPPORTED`。

### REST 平面（33 个 Tool，2 个 Text Resource）

是否暴露由三个彼此独立的开关决定：调用方凭证上的 scope、Gateway 是否声明该 Tool 的能力，以及该 Tool
的 Mutation 类别是否启用。

| 类别 | Scope | 启用方式 | Tool 数 |
| --- | --- | --- | --- |
| 读取 | `ignition.read` | 始终开启 | 21 |
| config mutation | `ignition.config` | `IGNITION_MCP_CONFIG_MUTATION_ENABLED` | 11 |
| control mutation | `ignition.control` | `IGNITION_MCP_CONTROL_MUTATION_ENABLED` | 1 |
| admin mutation | `ignition.admin` | `IGNITION_MCP_ADMIN_MUTATION_ENABLED` | 目前 0 |

读取覆盖 Gateway 身份与诊断、项目、配置资源、审计、Alarm Pipeline、artifact 与导出、Perspective 以及
操作诊断。config Mutation 是四个 `config_resource_*`、`project_import`、`tag_config_import`、
`artifact_delete` 以及四个 Perspective 写入；control Mutation 是 `alarm_pipeline_cancel`。Text Resource：
`ignition://gateway/capabilities` 与 `ignition://gateway/openapi-info`。每个已启用的 Mutation 还需要把自身
操作 id 列入 `IGNITION_MCP_MUTATION_OPERATIONS`、把目标列入 `IGNITION_MCP_MUTATION_TARGETS`；这些配置都
不是默认通配。

### Runtime 平面（22 个 Tool，3 个 Text Resource，无 Prompt）

Server Config 的 **profile** 决定该 endpoint 提供哪些 Tool。清单始终是显式列举，绝不用 `*`。

| Profile | Scope | Tool 数 |
| --- | --- | --- |
| `readonly` | READ | 13 |
| `operator` | READ + CONTROL | 16 |
| `configurator` | READ + CONFIG | 19 |
| `full` | READ + CONFIG + CONTROL | 22 |

读取覆盖 Tag、Alarm、Historian、UDT 以及 Named Query 执行。Mutation 是 CONTROL Tool（`tag_write`、
`alarm_shelve`、`alarm_unshelve`）和 CONFIG Tool（`tag_update`、`tag_create`、`tag_copy`、`tag_delete`、
`tag_move`、`tag_rename`）。每个 Mutation 还额外要求保留的 `[IgnitionMCPPolicy]` Tag provider 中存在
Runtime Target Policy，缺失时一律 fail closed。

已知 v1 限制：`alarm_status`、`alarm_journal`（D12 Phase 2 修订案）与 `alarm_acknowledge`（D12 Phase 4
修订案）保持搁置，直到存在原生的事前执行上界；它们的 handler 存放在
`packages/ignition-runtime-bundle/deferred/`。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `packages/ignition-rest-mcp/` | 外部 `ignition-rest` server 以及 `setup-native` 运维 CLI。 |
| `packages/ignition-runtime-bundle/` | Runtime bundle 的 Designer Project 源文件，以及 `BUNDLE_VERSION`。 |
| `contracts/` | 与语言无关的事实来源：逐 Tool 契约、共享分类法、输出 schema。 |
| `tooling/` | Bundle 校验器/构建器/发布器、契约 linter、兼容性校验器、CI 辅助脚本。 |
| `tests/` | 真实 Gateway 的 Docker 实机 harness 与持久化的兼容性证据。 |
| `docs/` | 决策（具备约束力）、开发记录、运维 runbook、`CONTEXT.md` 术语表。 |

## 快速开始

### 前置条件

- Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)。
- 一个可通过 HTTP(S) 访问的 Ignition 8.3.8/8.3.9 Gateway，且已有 `ignition/api-token` 资源。
  REST server 会向外连接它；Runtime bundle 会安装到它上面。
- 仅 Runtime 平面需要：来自 Inductive Automation 官方下载渠道的 MCP Module `.modl` 文件及其 SHA-256。
  本仓库固定（pin）的版本为 `1.3.5.2026021307-SNAPSHOT`，build `2026021307`，SHA-256
  `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`。

```bash
uv sync --all-packages   # 安装 workspace 包及其 console script
```

### 平面 1 — 启动 REST server

```bash
export IGNITION_MCP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_GATEWAY_API_TOKEN=<your-ignition-api-token>
export IGNITION_MCP_DATA_DIR=$HOME/.local/state/ignition-mcp   # 持久化 SQLite + artifact 存储

uv run --no-sync ignition-rest-mcp
```

development profile 绑定 `127.0.0.1:8000/mcp`（可用 `IGNITION_MCP_HOST` / `_PORT` / `_PATH` 覆盖），并
提供 `/health/live`、`/health/ready` 和 `/metrics`。把任意 Streamable HTTP MCP 客户端指向该 URL：

```json
{
  "mcpServers": {
    "ignition-rest": { "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

只要 capability registry 报告 `READY`（它跟随 Gateway 的 OpenAPI），读取就可以用；Gateway 未声明的路由
对应的 Tool 会保持隐藏。Mutation Tool 在「类别已启用」**且**「Gateway 声明了对应能力」之前不会出现在
`tools/list` 中，此后 operation 与 target 允许列表还会在调用时再次校验。要在受信部署上打开第一个 config
Mutation：

```bash
export IGNITION_MCP_AUTH_MODE=static-token
export IGNITION_MCP_STATIC_TOKENS='{"agent":{"token":"<secret>","scopes":["ignition.read","ignition.config"]}}'
export IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
export IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
export IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/tag-provider/MyProvider"]}'
```

完整配置 —— deployment profile、auth 模式、各类预算、artifact 限制、敏感导出 —— 见
[`packages/ignition-rest-mcp/README.md`](packages/ignition-rest-mcp/README.md)。

### 平面 2 — 部署 Runtime bundle

bundle 运行在 Gateway 上，而不是本地进程。

**方式 A —— 交互式向导**（逐阶段带你走完流程，答案持久化到 `.env`）：

```bash
./scripts/deploy-runtime-bundle.sh
```

**方式 B —— `setup-native` CLI。** 先构建发布产物：

```bash
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
```

它会写出三个确定性文件：`ignition-runtime-bundle-<version>.zip`、`.manifest.json`，以及兼容
`sha256sum -c` 的 `.sha256`。然后：

```bash
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
mkdir -p ~/.config/ignition-mcp
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token && chmod 0600 ~/.config/ignition-mcp/gateway.token

# 1. 安装固定版本的 Module（先省略那两个接受类 flag，可以让命令先把内容展示给你）。
ignition-mcp setup-native install-module \
  --file ~/downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-url http://127.0.0.1:8088 --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --accept-certificate --accept-eula --restart

# 2. 诊断、计划、应用、验证。
ignition-mcp setup-native doctor --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-url http://127.0.0.1:8088 --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly
ignition-mcp setup-native plan   --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip --server-config-name production --profile readonly --json
ignition-mcp setup-native apply  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip --policy-file policy.json \
  --server-config-permissions-file permissions.json --server-config-name production --profile readonly
ignition-mcp setup-native verify --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --server-config-name production --profile readonly
```

`apply` 会写入 Runtime Target Policy、bundle Project 和 MCP Server Config；`doctor`、`plan`、`verify` 不做任何
写入。之后 agent 连接的是 `<gateway-url>/data/mcp/<server-config-name>`。把 profile 提升为 `operator`、
`configurator` 或 `full` 可以开放更多 Tool，然后重新 `apply`。

完整的运维流程 —— 每个 flag、退出码、证书/EULA 规则与失败诊断 —— 见
[`docs/operations/runbook.zh-CN.md`](docs/operations/runbook.zh-CN.md)（英文版：
[`runbook.md`](docs/operations/runbook.md)）。

## Windows

Windows 不会出错，但不是受支持的平台。范围与已声明的限制见
[D31](docs/decisions/D31-windows-support-scope.md)。本仓库的内容尚未在 Windows 上运行过。

- **校验和。** Windows 没有内置的 `sha256sum -c`。请使用 `certutil -hashfile <file> SHA256` 或
  `Get-FileHash <file> -Algorithm SHA256`，并把结果与 `.sha256` 文件中的哈希比对。
- **向导。** `scripts/deploy-runtime-bundle.sh` 是 bash 向导，需要 Git Bash 或 WSL。
  `ignition-mcp setup-native doctor|plan|apply` 命令是原生命令，不需要它。
- **机密与数据目录。** 在 Windows 上会跳过 POSIX 权限模式检查，改为记录一条 WARNING。请用文件系统 ACL
  保护 `IGNITION_MCP_DATA_DIR` 和每个凭证文件。

## 安全模型

- **一切有界。** 每个输入、执行与输出都有上界；超限会显式失败，绝不静默截断（D10）。
- **Target 允许列表。** Mutation 只能触及部署方列出的目标；空列表表示什么都不允许，放开全部需要显式的
  `*`（D08）。
- **类别门禁。** `CONFIG` / `CONTROL` / `ADMIN` Mutation 默认关闭，在发现阶段与调用阶段都会把关。四个
  scope 名称之间没有任何层级关系。
- **前置条件令牌。** 变更必须携带从它要修改的状态读出的令牌；令牌过期会返回 `conflict` 且不派发任何请求。
  结果不明确的派发记为 `outcome_unknown`，绝不重放。
- **被拒绝的资源类型。** 安全、身份、令牌与 Module 管理类资源，无论允许列表怎么写都会被拒绝；未分类的
  类型同样拒绝。
- **输出中不含机密。** 凭证不会出现在日志、报告、审计行或 Tool 输出中。
- **返回错误，而非信封。** Tool 返回 MCP structured output 加上
  `contracts/shared/error-codes.json` 中的规范错误分类；没有 `{ok,result,error}` 信封（D06）。

## 开发

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests  # 需要 Java 11（D29）
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
```

Runtime 的 `onToolCalled.py` / `onPrompt.py` 是 Jython 2.7、用 Tab 缩进且自包含的脚本。贡献者规则、
委派策略与交付约定见 [`AGENTS.md`](AGENTS.md)；领域术语见 [`CONTEXT.md`](CONTEXT.md)。

## 文档

- [`docs/decisions/INDEX.md`](docs/decisions/INDEX.md) —— 具备约束力的架构决策 D01–D31。
- [`docs/operations/runbook.zh-CN.md`](docs/operations/runbook.zh-CN.md) —— v1 运维 runbook（中文）。
- [`docs/development/`](docs/development/) —— 各 phase 的交付记录与关卡证据。
- [`contracts/README.md`](contracts/README.md) —— 契约事实来源。
- [`tests/compatibility/evidence/`](tests/compatibility/evidence/) —— 持久化的实机 Gateway 证据。

## 许可证

GPL-3.0-only。见 [`LICENSE`](LICENSE)。
