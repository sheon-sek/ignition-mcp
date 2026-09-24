# Ignition MCP

> English: [`README.md`](README.md)。本文是英文版的译本，两者不一致时以英文版为准。Tool 名、环境变量、命令和文件路径保留英文原文。

Ignition MCP 让 Claude 这类 AI 助手能够操作
[Inductive Automation Ignition](https://inductiveautomation.com/) 8.3 Gateway。助手可以读取 Tag、历史数据、配置和项目，也可以修改它们，但只能改你允许的内容，而且每一类修改都要你单独打开。

它使用 MCP，全称 Model Context Protocol，是 AI 应用连接外部工具的标准方式。任何支持 Streamable HTTP 的 MCP 客户端都能用，例如 Claude Code、Cursor 或 VS Code。

## 可以问什么

装好之后，你可以这样问助手：

- “`[default]Plant` 下面所有 AHU 的 Tag 现在是多少度？”
- “这个 Tag 过去 24 小时的平均值和最大值是多少？”
- “现在有哪些报警被搁置了？”
- “Gateway 上配置了哪些数据库连接？”
- “新建一个 Perspective View，显示这三个 Tag。”这一条需要先打开写入。

## 两个 server

本项目有两个 MCP server，可以只装一个，也可以都装。

| | `ignition-rest` | `ignition-runtime` |
| --- | --- | --- |
| 负责 | Gateway 信息、配置资源、项目、Perspective View、审计日志、报警通知管道 | Tag 值和 Tag 配置、UDT、Tag 历史、报警搁置、已批准的数据库查询 |
| 运行位置 | 作为独立程序，运行在任何能连到 Gateway 的机器上 | 运行在 Gateway 内部，由官方 Ignition MCP Module 承载 |
| 安装指南 | [快速开始](docs/guide/quick-start.zh-CN.md) | [快速开始](docs/guide/quick-start.zh-CN.md) |

一条 `ignition-mcp setup` 命令会同时装好两个 server 的两个助手角色。读 Tag 和历史数据是最常见的需求，先跑一次默认的 `dev` 部署就能用。[运作原理](docs/guide/how-it-works.zh-CN.md#我需要哪个-server)里有更完整的对照表。

## 文档

| 我想…… | 看这里 |
| --- | --- |
| 了解两个 server 做什么、一次调用怎么进行 | [运作原理](docs/guide/how-it-works.zh-CN.md) |
| 一步步装好两个 server | [快速开始](docs/guide/quick-start.zh-CN.md) |
| 查某个 Tool 为什么不见了，或者它需要什么设置 | [Tool 目录](docs/guide/tools.zh-CN.md) |
| 查某个设置项 | [配置参考](docs/guide/configuration.zh-CN.md) |
| 升级、修改 policy、看懂 `status` 的某一行或某个退出码 | [运维手册](docs/operations/runbook.zh-CN.md) |
| 查错误码 | [运作原理：错误](docs/guide/how-it-works.zh-CN.md#错误) |

## 安全

助手一开始只有读权限。每一类修改在你打开之前都是关闭的。

- **写入默认关闭。** REST server 上，每一类修改要用一个设置单独打开。Runtime server 上，要选一个包含写入 Tool 的 profile。
- **能改什么由你列出。** 每个写入 Tool 只能碰你列出的目标，例如一个 Tag 文件夹或一个项目。列表为空的 Tool 什么都改不了。
- **修改前必须先读。** 大多数修改 Tool 需要一个来自最近一次读取的 token。如果这期间别人改过目标，调用会以 `conflict` 失败，什么都不改。
- **不会盲目重试。** 如果 server 无法确定修改是否生效，它会返回 `outcome_unknown`，不会自己再试一次。
- **有些东西永远不能改。** API token、安全设置、用户源、模块设置和两个 server 自己的 policy，任何 Tool 都改不了。
- **没有自由 SQL，也没有任意网络请求。** 助手只能按名字运行你批准过的数据库查询，也只能通过目录里列出的 Tool 访问 Gateway。
- **每个回答都有大小上限。** 回答太大时返回 `limit_exceeded`，不会在你不知情的情况下被截断。
- **机密信息不外泄。** token 和密码不会出现在 Tool 回答、日志或审计记录里。

## 状态

第 1 版已经完成。实机测试在 Ignition 8.3.8 和 8.3.9、MCP Module `1.3.5.2026021307-SNAPSHOT` 上通过。项目把这些结果记为 `VERIFIED` 测试结果，不声称任何版本获得生产支持。详见[运作原理：已测试的版本](docs/guide/how-it-works.zh-CN.md#已测试的版本)。

第 1 版的已知限制：

- 报警状态、报警日志和报警确认这三个 Tool 被关闭了。Ignition 的报警查询函数无法限制返回的行数，所以这些 Tool 无法保证回答有上限。
- 没有在 Windows 上测试过。指南里的 Windows 步骤都标注了“尚未在 Windows 上运行”。见 [D31](docs/decisions/D31-windows-support-scope.md)。
- MCP Module 不发布 Runtime Tool 的输出 schema。本仓库在 `contracts/schemas/` 里提供它们。

## 给贡献者

| 路径 | 内容 |
| --- | --- |
| `packages/ignition-rest-mcp/` | REST server 和 `ignition-mcp` 命令。它的 [README](packages/ignition-rest-mcp/README.md) 详细说明各 Tool 的行为。 |
| `packages/ignition-runtime-bundle/` | 作为 Ignition 项目的 Runtime Tool。见它的 [README](packages/ignition-runtime-bundle/README.md)。 |
| `contracts/` | Tool 契约、输出 schema 和共享错误码，两个 server 都以它为准做检查。 |
| `tooling/` | bundle 构建器、契约 linter 和 CI 辅助脚本。 |
| `tests/` | 实机 Gateway 测试环境和已记录的测试证据。 |
| `docs/decisions/` | 架构决策 D01 到 D31，具有约束力。从 [INDEX.md](docs/decisions/INDEX.md) 开始看。 |
| `docs/development/` | 每个开发阶段一份交付记录。 |

提交修改前要跑的检查：

```bash
uv sync --all-packages
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
```

测试需要 Java 11，用来在 Jython 下运行 Runtime 脚本。贡献者规则见 [`AGENTS.md`](AGENTS.md)，项目术语见 [`CONTEXT.md`](CONTEXT.md)。

## 许可证

GPL-3.0-only。见 [`LICENSE`](LICENSE)。
