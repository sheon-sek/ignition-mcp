# 配置参考

> English: [`configuration.md`](configuration.md)。本文是英文版的译本，两者不一致时以英文版为准。

本文按设置的位置分组，列出所有设置项。哪个 Tool 需要哪个设置，见 [Tool 目录](tools.zh-CN.md)。

- [REST server 设置](#rest-server-设置)：`ignition-rest-mcp` 的环境变量。
- [setup 命令的设置](#setup-命令的设置)：`ignition-mcp setup` 的参数。
- [连接 agent](#连接-agent)：每个 MCP server 需要的 URL、header 和 token。
- [Runtime Target Policy](#runtime-target-policy)：允许 Runtime 写入的 JSON 文件。
- [Server Config 权限文件](#server-config-权限文件)：谁可以连接 Runtime server。
- [Named Query 注册表](#named-query-注册表)：Runtime server 可以运行的数据库查询。

## REST server 设置

REST server 启动时从环境变量读取设置。它会检查每个值，只要有一个不对就拒绝启动，报错信息会写出是哪个变量。改完设置后要重启 server。

正常用法是运行 [快速开始](quick-start.zh-CN.md)里的 `ignition-mcp start`，它从部署文件夹推导出下面这些值，你不用自己设置。你也可以直接运行 `ignition-rest-mcp` 并自己设置环境变量：Linux 和 macOS 用 `export NAME=value`，PowerShell 用 `$env:NAME = "value"`。自己设置时，JSON 值和 Windows 路径里的引号要按各 shell 的规则处理。

### 必填项

| 变量 | 填什么 |
| --- | --- |
| `IGNITION_MCP_GATEWAY_URL` | Gateway 的网址，例如 `http://127.0.0.1:8088`。默认 `http://127.0.0.1:8088`。 |
| `IGNITION_MCP_GATEWAY_API_TOKEN` | 一个 Ignition API token。server 调用 Gateway 时都用它，agent 永远看不到。 |
| `IGNITION_MCP_DATA_DIR` | server 保存记录和文件的文件夹，必须是完整路径，例如 `/var/lib/ignition-mcp` 或 `C:\ProgramData\ignition-mcp`。在 `development` 以外的 profile 中，不能是 `/tmp` 这样的临时文件夹。 |

### server 监听的地址

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `IGNITION_MCP_HOST` | `127.0.0.1` | 监听的网络地址。`127.0.0.1` 表示只有本机能连接。 |
| `IGNITION_MCP_PORT` | `8000` | 端口。 |
| `IGNITION_MCP_PATH` | `/mcp` | MCP endpoint 的 URL 路径。 |

使用默认值时，agent 连接 `http://127.0.0.1:8000/mcp`。同一个端口上还有 `/health/live`、`/health/ready` 和 `/metrics`。

### 部署 profile 和身份验证

`IGNITION_MCP_DEPLOYMENT_PROFILE` 决定 server 有多严格。`IGNITION_MCP_AUTH_MODE` 决定调用方如何证明身份。

| Profile | 适用场景 | 规则 |
| --- | --- | --- |
| `development`（默认） | 在一台电脑上试用 | 只能监听 `127.0.0.1` 或其他本机回环地址 |
| `trusted-internal` | 工厂或办公室内网里的 server | 可以监听任何地址。可以不开身份验证，但那样就只能读取 |
| `secured` | 暴露范围更大的场合 | 必须 `IGNITION_MCP_AUTH_MODE=jwt` |

| 验证方式 | 调用方发送什么 | 能写入吗？ |
| --- | --- | --- |
| `none`（默认） | 什么都不发 | 不能。所有调用方都只有 `ignition.read` |
| `static-token` | `Authorization: Bearer <token>` | 能，使用你给这个 token 的 scope |
| `jwt` | `Authorization: Bearer <身份提供者签发的 JWT>` | 能，使用 token 里的 scope |

使用 `static-token` 时，在 `IGNITION_MCP_STATIC_TOKENS` 里用 JSON 列出 token。每个 token 有一个名字和一组 scope：

```json
{
  "claude-desktop": {"token": "<一长串随机密钥>", "scopes": ["ignition.read"]},
  "maintenance-agent": {"token": "<另一个密钥>", "scopes": ["ignition.read", "ignition.config"]}
}
```

- 四个 scope 是 `ignition.read`、`ignition.config`、`ignition.control` 和 `ignition.admin`。它们互不包含，要哪个就列哪个。
- 审计记录里显示的是 token 的名字，不是密钥，格式为 `static-token:<name>`。
- 名字由字母、数字和 `._:-` 组成，最长 64 个字符。密钥最长 512 个字符。最多 32 个 token。
- `IGNITION_MCP_STATIC_TOKEN` 是旧的单 token 写法，只有 `ignition.read`。不要两个都设置。

使用 `jwt` 时，设置 `IGNITION_MCP_JWT_ISSUER`、`IGNITION_MCP_JWT_AUDIENCE`，以及 `IGNITION_MCP_JWT_JWKS_URI`（`https` 地址）和 `IGNITION_MCP_JWT_PUBLIC_KEY`（PEM 公钥）二者之一。server 接受 RS256 token，从 token 里读取 scope。它只校验 token，不签发 token。

### 打开写入和导出

下面这些默认都是关闭的。哪个 Tool 需要哪个开关，见 [Tool 目录](tools.zh-CN.md#写入-tool)。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `IGNITION_MCP_CONFIG_MUTATION_ENABLED` | `false` | 允许配置修改类 Tool。 |
| `IGNITION_MCP_CONTROL_MUTATION_ENABLED` | `false` | 允许 `alarm_pipeline_cancel`。 |
| `IGNITION_MCP_ADMIN_MUTATION_ENABLED` | `false` | 第 1 版没有 Tool 使用它。 |
| `IGNITION_MCP_MUTATION_OPERATIONS` | 空 | 允许写入的 Tool 名，用逗号分隔，例如 `config_resource_update,project_import`。`*` 表示全部。 |
| `IGNITION_MCP_MUTATION_TARGETS` | 空 | 一个 JSON，把每个 Tool 名对应到它可以修改的 Target，例如 `{"project_import":["MES"]}`。没有条目的 Tool 什么都改不了。 |
| `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED` | `false` | 允许 `project_export` 和 `tag_config_export`。 |
| `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED` | `false` | 允许用 `POST /artifacts?kind=project_archive` 上传项目 ZIP。 |

### Project writer

`project_import` 和四个 Perspective 写入 Tool 需要 Project writer。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `IGNITION_MCP_PROJECT_WRITER_ENABLED` | `false` | 打开 Project writer。 |
| `IGNITION_MCP_GATEWAY_ID` | 空 | 你为 Gateway 取的固定名字，打开 writer 时必填。由字母、数字和 `._:-` 组成，最长 128 个字符。 |
| `IGNITION_MCP_PROJECT_DESIGNER_POLICY` | `deny` | 有人在 Designer 里打开了这个项目时怎么办：`deny`、`warn` 或 `ignore`。 |
| `IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS` | `10` | 一次写入等待同一项目上另一次写入的时间。 |
| `IGNITION_MCP_PROJECT_VERIFICATION_TIMEOUT_SECONDS` | `60` | server 等待确认导入结果的时间。 |
| `IGNITION_MCP_PROJECT_RECONCILE_INTERVAL_SECONDS` | `60` | server 多久处理一次被重启打断的导入。 |
| `IGNITION_MCP_PROJECT_LOCK_MAX_ENTRIES` | `32` | 同时持有写锁的项目数上限。 |

### 上限和时间

这些很少需要改。每一项都有 server 不会超过的最大值。

| 变量 | 默认值 | 最大值 | 含义 |
| --- | --- | --- | --- |
| `IGNITION_MCP_GATEWAY_TIMEOUT_SECONDS` | `10` | `30` | 单个 Gateway 请求的时限。 |
| `IGNITION_MCP_TOOL_TIMEOUT_SECONDS` | `30` | `30` | 普通 Tool 调用的时限。 |
| `IGNITION_MCP_QUERY_TIMEOUT_SECONDS` | `30` | `120` | `audit_query` 这类查询 Tool 的时限。 |
| `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS` | `120` | `300` | 导出和导入 Tool 的时限。 |
| `IGNITION_MCP_STRUCTURED_OUTPUT_LIMIT_BYTES` | `262144`，即 256 KiB | `1048576` | Tool 回答的最大大小。超过时返回 `limit_exceeded`，不会被截断。 |
| `IGNITION_MCP_WATCHER_INTERVAL_SECONDS` | `60` | | server 多久重新读取一次 Gateway 的 API 描述。 |
| `IGNITION_MCP_AUDIT_MAX_ROWS` | `50000` | | 保留的审计记录条数。 |
| `IGNITION_MCP_AUDIT_MAX_AGE_DAYS` | `90` | | 审计记录超过多少天后删除。 |
| `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS` | `10000` | | 为 `operation_diagnose` 保留的调用记录条数。 |
| `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS` | `72` | | 调用记录超过多少小时后删除。 |
| `IGNITION_MCP_RETENTION_INTERVAL_SECONDS` | `300` | | 多久清理一次旧记录。 |
| `IGNITION_MCP_RETENTION_BATCH_ROWS` | `500` | | 每次清理的记录数。 |
| `IGNITION_MCP_STORAGE_PROBE_INTERVAL_SECONDS` | `30` | | server 多久检查一次自己的存储。 |

保存的文件叫做 artifact，它们有自己的上限：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `IGNITION_MCP_ARTIFACT_MAX_BYTES` | 256 MiB | 单个文件的最大大小。 |
| `IGNITION_MCP_ARTIFACT_TOTAL_BYTES` | 1 GiB | 所有文件的总大小。 |
| `IGNITION_MCP_ARTIFACT_MAX_COUNT` | `1000` | 文件数。 |
| `IGNITION_MCP_ARTIFACT_MIN_FREE_BYTES` | 100 MiB | server 要保留的磁盘空闲空间。 |
| `IGNITION_MCP_ARTIFACT_MIN_FREE_RATIO` | `0.05` | server 要保留的磁盘空闲比例。 |
| `IGNITION_MCP_ARTIFACT_EXPORT_TTL_HOURS` | `24` | 导出文件保留多久。 |
| `IGNITION_MCP_ARTIFACT_RECOVERY_TTL_DAYS` | `7` | 导入前的备份保留多久。 |
| `IGNITION_MCP_ARTIFACT_STAGING_DEADLINE_SECONDS` | `900` | 未完成上传的时限。 |
| `IGNITION_MCP_ARTIFACT_CLEANUP_INTERVAL_SECONDS` | `300` | 多久清理一次过期文件。 |
| `IGNITION_MCP_ARTIFACT_CLEANUP_BATCH` | `50` | 每次清理的文件数。 |

### 日志

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `IGNITION_MCP_LOG_FORMAT` | `auto` | `text`、`json`，或 `auto`：在 `development` 以外的 profile 中选 `json`。 |
| `IGNITION_MCP_SERVICE_IDENTITY` | `ignition-rest` | 未开身份验证时，记录里用来称呼调用方的名字。 |

## setup 命令的设置

`ignition-mcp setup` 负责部署两个 server。每个命令做什么，见[快速开始](quick-start.zh-CN.md)。下面是它接受的参数。

| 参数 | 含义 |
| --- | --- |
| `--deployment NAME` | 部署名，对应 `~/.config/ignition-mcp/deployments/<name>/`。默认 `default`。 |
| `--gateway-url URL` | Gateway 的网址。 |
| `--environment dev\|prod` | 部署环境，默认 `dev`。 |
| `--roles analysis,engineer` | 要部署的助手角色。`dev` 默认两个，`prod` 默认只有 `analysis`。 |
| `--gateway-token-file PATH` | 存有 Gateway API key 的单行 `<name>:<key>` 文件。在 Linux 和 macOS 上权限必须是 `0600`。 |
| `--module-file PATH` | MCP Module 的 `.modl` 文件。不填时在 `tests/fixtures/modules/` 和 `~/Downloads` 里找。 |
| `--recreate-tokens` | 本地机密文件丢失但 Gateway 上的 token 还在时，删掉并重建它。 |
| `--provision-security-levels` | 在 `prod` 里也创建角色缺少的安全级别。 |
| `--dry-run` | 只显示计划，不写任何东西。 |
| `--json` | 以 JSON 输出报告，带稳定的错误码。 |
| `--yes` | 接受除证书和 EULA 之外的每个具名风险。 |
| `--accept-certificate` | 信任 Module 的证书。 |
| `--accept-eula` | 接受 Module 的 EULA。 |

每个命令都接受 `--deployment`、`--json`、`--yes`、`--accept-certificate` 和 `--accept-eula`。`status` 另外读一个 `--gateway-token-file`；不给时读 `setup` 保存在部署里的 `gateway-token.secret`。`start` 接受 `--bind HOST:PORT`，默认 `127.0.0.1:8000`。`connect` 接受 `--client claude|codex|none`。

`setup` 不需要环境变量：每个值来自参数、上次保存的部署，或向导的问题。

## 连接 agent

每个角色有两个 MCP server，它们的身份验证方式不同。`ignition-mcp connect <role>` 会为 Claude Code 或 Codex 写好这两项。要手工配置客户端，或核对 `connect` 写了什么，用下面的设置。两个 server 都使用 Streamable HTTP transport。

| Server | URL | Header | Token |
| --- | --- | --- | --- |
| Runtime（`ignition-runtime-<role>`） | `<gateway-url>/data/mcp/<role>` | `X-Ignition-API-Token: <token>` | 角色的 Ignition API token，在 `runtime-<role>.secret` 里 |
| REST（`ignition-rest-<role>`） | `http://<bind>/mcp`，默认 `http://127.0.0.1:8000/mcp` | `Authorization: Bearer <token>` | 角色的 static token，在 `rest-<role>-token.secret` 里 |

机密文件在部署目录 `~/.config/ignition-mcp/deployments/<name>/` 里。每个文件只有一行 `<name>:<key>`，整行就是 token。

### Runtime server

MCP Module 只从 `X-Ignition-API-Token` header 读取 Ignition API token，不接受 `Authorization: Bearer`。所以只提供 bearer token 设置的客户端连不上 Runtime server。header 里放整行 `<name>:<key>`，不加 `Bearer` 前缀。

Claude Code：

```bash
claude mcp add --transport http ignition-runtime-analysis \
  http://127.0.0.1:8088/data/mcp/analysis --scope user \
  --header "X-Ignition-API-Token: ignition-mcp-analysis:<key>"
```

Codex，写在 `~/.codex/config.toml` 里。`codex mcp add` 设不了自定义 header，所以直接编辑文件：

```toml
[mcp_servers.ignition-runtime-analysis]
url = "http://127.0.0.1:8088/data/mcp/analysis"

[mcp_servers.ignition-runtime-analysis.http_headers]
"X-Ignition-API-Token" = "ignition-mcp-analysis:<key>"
```

接受 JSON server 列表的客户端，例如 `claude mcp add-json`：

```json
{
  "type": "http",
  "url": "http://127.0.0.1:8088/data/mcp/analysis",
  "headers": {"X-Ignition-API-Token": "ignition-mcp-analysis:<key>"}
}
```

### REST server

REST server 读取标准的 `Authorization: Bearer <token>` header。它接受哪些 token 取决于 auth mode，见[部署 profile 和身份验证](#部署-profile-和身份验证)。`setup` 创建的部署使用 `static-token`，每个角色一个 token。`start` 必须正在运行。

Claude Code：

```bash
claude mcp add --transport http ignition-rest-analysis \
  http://127.0.0.1:8000/mcp --scope user \
  --header "Authorization: Bearer ignition-mcp-analysis:<key>"
```

Codex，写在 `~/.codex/config.toml` 里：

```toml
[mcp_servers.ignition-rest-analysis]
url = "http://127.0.0.1:8000/mcp"

[mcp_servers.ignition-rest-analysis.http_headers]
"Authorization" = "Bearer ignition-mcp-analysis:<key>"
```

JSON server 列表：

```json
{
  "type": "http",
  "url": "http://127.0.0.1:8000/mcp",
  "headers": {"Authorization": "Bearer ignition-mcp-analysis:<key>"}
}
```

`IGNITION_MCP_AUTH_MODE=none` 时 REST server 不需要 header，每个调用方都只能读。

## Runtime Target Policy

policy 决定 Runtime 写入 Tool 可以修改哪些 Tag 和报警。`setup` 根据部署环境和角色生成它，存进 Gateway 上的 `IgnitionMCPPolicy` Tag provider，agent 改不了它。operator 从不手写这个文件，`setup` 会让它和 Gateway 保持一致。下面是生成文档的字段，供你阅读 Gateway 上的副本。

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `schemaVersion` | 是 | 固定为 `1`。 |
| `serviceIdentity` | 是 | 每次 Runtime 写入在 Ignition 审计日志里记录的执行者名字。 |
| `auditMode` | 是 | `best_effort`：能记审计就记。`required`：审计 profile 不可用时拒绝写入。`off`：不记录。 |
| `auditProfile` | 否 | 要写入的 Ignition 审计 profile。`required` 模式需要它。 |
| `allowlists` | 是 | 一个对象，每个 Tool 名一个键，值是该 Tool 可以修改的路径列表。 |
| `alarmShelveMaxSeconds` | 否 | `alarm_shelve` 接受的最长搁置时间，最多 86400 秒，也就是一天。 |
| `tagWriteMaxWrites` | 否 | 每次 `tag_write` 调用的写入数，1 到 100，默认 20。 |
| `tagCreateMaxItems`、`tagUpdateMaxItems`、`tagCopyMaxItems`、`tagDeleteMaxItems`、`tagMoveMaxItems`、`tagRenameMaxItems` | 否 | 各 Tag Tool 每次调用的条目数，1 到 100，默认 20。 |
| `alarmMaxPaths` | 否 | 每次 `alarm_shelve` 或 `alarm_unshelve` 调用的报警路径数，1 到 100。 |

文件最大 32 KiB。allowlist 条目如何匹配，见 [Tool 目录](tools.zh-CN.md#runtime-写入-tool-都需要什么)。

`allowlists` 为空的 policy 是有效的。`prod` 默认就是这样，它让所有 Runtime 写入保持关闭，是一个安全的起点。

## Server Config 权限文件

Server Config 是 MCP Module 对一个 MCP endpoint 的记录。它的权限树列出调用方使用这个 endpoint 必须具备的 Ignition 安全级别。`setup` 从角色生成这棵树，存进部署文件夹的 `permissions-<role>.json`，并保持它和 Gateway 一致。你不用手写这个文件。

Analysis 角色生成的权限树是这样。Engineer 角色相同，只是级别名为 `IgnitionMcpEngineer`。

```json
{
  "type": "AllOf",
  "securityLevels": [
    {
      "name": "Authenticated",
      "children": [
        {"name": "IgnitionMcpAnalysis", "children": []}
      ]
    }
  ]
}
```

- `type` 是 `AllOf` 或 `AnyOf`。
- `securityLevels` 沿用 Ignition 的安全级别树。这个例子要求 `Authenticated/IgnitionMcpAnalysis` 级别，`setup` 会在 `dev` 里创建它。
- agent 连接时使用的 token 必须带有这个级别，`setup` 会创建带这个级别的 token。

## Named Query 注册表

Runtime 的 `database_query` Tool 只能运行这里列出的 Named Query。把 JSON 设置为 **Gateway 上**的环境变量 `IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON`，然后重启 Gateway。示例见 [Tool 目录](tools.zh-CN.md#named-query-注册表)。

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` | 固定为 `1`。 |
| `entries` | 最多 100 个查询。整个值最大 32 KiB。 |
| `entries[].alias` | agent 使用的名字：小写字母、数字和 `_`，以字母开头，最长 64 个字符。 |
| `entries[].description` | 告诉 agent 这个查询返回什么，最长 512 个字符。 |
| `entries[].project` | 存放该 Named Query 的 Ignition 项目。 |
| `entries[].path` | Named Query 在项目中的路径，例如 `Reports/LineDowntime`。 |
| `entries[].resultMode` | 表格用 `dataset`，单个值用 `scalar`。 |
| `entries[].datasourcePolicy` | 固定为 `named-query-fixed`。查询使用 Named Query 里设定的数据库。 |
| `entries[].parameters` | agent 可以传入的值。每个都有 `type`：`string`、`integer`、`number`、`boolean` 或 `datetime`；还可以有 `required`、`minimum`、`maximum`，字符串还可以有 `maxLength`。最多 32 个。 |
| `entries[].pagination` | 返回多少行，见下文。 |

`pagination` 有三种写法：

- `{"mode": "none"}`，`scalar` 查询必须用这种。
- `{"mode": "fixed", "maxRows": 200}`，最多返回 `maxRows` 行，上限 2000。
- `{"mode": "offset", "limitParameter": "limit", "offsetParameter": "offset", "defaultPageSize": 50, "hardPageSize": 500, "maxOffset": 100000}`，分页返回。Named Query 必须声明 `limitParameter` 和 `offsetParameter` 指定的两个参数，并在 SQL 里使用它们。`hardPageSize` 最大 2000。

没有设置这个变量时，注册表为空。变量格式有误时，两个数据库 Tool 都会报错，而不会去猜。
