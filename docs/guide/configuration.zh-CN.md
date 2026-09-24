# 配置参考

> English: [`configuration.md`](configuration.md)。本文是英文版的译本，两者不一致时以英文版为准。

本文按设置的位置分组，列出所有设置项。哪个 Tool 需要哪个设置，见 [Tool 目录](tools.zh-CN.md)。

- [REST server 设置](#rest-server-设置)：`ignition-rest-mcp` 的环境变量。
- [安装命令的设置](#安装命令的设置)：`ignition-mcp setup-native` 的参数和环境变量。
- [Runtime Target Policy](#runtime-target-policy)：允许 Runtime 写入的 JSON 文件。
- [Server Config 权限文件](#server-config-权限文件)：谁可以连接 Runtime server。
- [Named Query 注册表](#named-query-注册表)：Runtime server 可以运行的数据库查询。

## REST server 设置

REST server 启动时从环境变量读取设置。它会检查每个值，只要有一个不对就拒绝启动，报错信息会写出是哪个变量。改完设置后要重启 server。

[安装指南](setup-rest.zh-CN.md#第-4-步写一个设置文件)把设置放在一个设置文件里，用 `uv run --no-sync --env-file ignition-rest.env ignition-rest-mcp` 启动 server。在这个文件里，JSON 值和 Windows 路径要加单引号。你也可以在 shell 里直接设置：Linux 和 macOS 用 `export NAME=value`，PowerShell 用 `$env:NAME = "value"`。

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

## 安装命令的设置

`ignition-mcp setup-native` 负责部署 Runtime server。各子命令的说明见[运维手册](../operations/runbook.zh-CN.md)。下面是它的输入。

| 参数 | 环境变量 | 含义 |
| --- | --- | --- |
| `--gateway-url URL` | `IGNITION_MCP_SETUP_GATEWAY_URL` | Gateway 的网址。 |
| `--gateway-token-file PATH` | `IGNITION_MCP_SETUP_GATEWAY_TOKEN`，直接存放 token 本身 | 存有 Gateway API token 的单行文件。在 Linux 和 macOS 上权限必须是 `0600`。 |
| `--mcp-url URL` | `IGNITION_MCP_SETUP_MCP_URL` | Runtime MCP 地址。不填时，`doctor` 和 `verify` 会根据 `--server-config-name` 拼出来。 |
| `--mcp-token-file PATH` | `IGNITION_MCP_SETUP_MCP_TOKEN` | Runtime MCP 地址需要登录时使用的 token。 |
| `--bundle-manifest PATH` | | release manifest。除 `install-module` 外的子命令都要。 |
| `--bundle-zip PATH` | | release ZIP。`apply` 必填。 |
| `--profile NAME` | | `readonly`（默认）、`operator`、`configurator` 或 `full`。 |
| `--bundle-project NAME` | | 放置 Tool 的 Ignition 项目。默认 `ignition_runtime`。 |
| `--server-config-name NAME` | | MCP endpoint 的名字。`apply` 必填。agent 连接 `<gateway-url>/data/mcp/<name>`。 |
| `--policy-file PATH` | | Runtime Target Policy。`apply` 必填。 |
| `--server-config-permissions-file PATH` | | 谁可以连接。`apply` 第一次创建 endpoint 时必填。 |
| `--backup-dir PATH` | | `apply` 替换旧项目之前，把旧项目存一份到这个文件夹。 |
| `--provision-security-levels` | | 为这个 profile 创建安全级别，名为 `IgnitionMcpRuntime<Profile>`。 |
| `--security-level-name NAME` | | 使用另一个安全级别名称。 |
| `--create-runtime-token` | | 为 agent 创建一个带该安全级别的 API token。 |
| `--runtime-token-file PATH` | | 新 agent token 的保存位置。和 `--create-runtime-token` 一起时必填。 |
| `--runtime-token-name NAME` | | 新 token 的名字。默认用 `--server-config-name`。 |
| `--runtime-token-insecure-channel` | | 让新 token 可以通过普通 `http` 使用。只用于实验环境的 Gateway。 |
| `--acknowledge-upgrade` | | 接受 bundle 的大版本升级、降级，或更新的 Module build。 |
| `--allow-insecure-authorize` | | 允许通过普通 `http` 把 Gateway token 发给另一台机器。只用于实验网络。 |
| `--timeout-seconds N` | | 每个请求的时限。默认 `10`。 |
| `--json` | | 以 JSON 输出报告。 |

`install-module` 有自己的参数，见[运维手册](../operations/runbook.zh-CN.md#安装-mcp-module)。

## Runtime Target Policy

policy 决定 Runtime 写入 Tool 可以修改哪些 Tag 和报警。你把它写成 JSON 文件，传给 `setup-native apply --policy-file`。命令把它存进 Gateway 上的 `IgnitionMCPPolicy` Tag provider，agent 改不了它。

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

`allowlists` 为空的 policy 是有效的。它让所有 Runtime 写入保持关闭，是一个安全的起点。

## Server Config 权限文件

Server Config 是 MCP Module 对一个 MCP endpoint 的记录。它的权限树列出调用方使用这个 endpoint 必须具备的 Ignition 安全级别。`setup-native` 从不自己编造权限树，所以第一次要用 `--server-config-permissions-file` 提供。之后的运行会沿用 Gateway 上已有的那份。

```json
{
  "type": "AllOf",
  "securityLevels": [
    {
      "name": "Authenticated",
      "children": [
        {"name": "IgnitionMcpRuntimeReadonly", "children": []}
      ]
    }
  ]
}
```

- `type` 是 `AllOf` 或 `AnyOf`。
- `securityLevels` 沿用 Ignition 的安全级别树。这个例子要求 `Authenticated/IgnitionMcpRuntimeReadonly` 级别，`--provision-security-levels` 会为 `readonly` profile 创建它。
- agent 连接时使用的 token 必须带有这个级别。`--create-runtime-token` 会创建这样的 token。

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
