# Tool 目录与各 Tool 的前提条件

> English: [`tools.md`](tools.md)。本文是英文版的译本，两者不一致时以英文版为准。

本文列出两个 server 提供的每一个 Tool：它做什么，以及 AI agent 看到它之前你要设置什么。如果 agent 说某个 Tool 不存在，就在这里找到它，逐行检查前提条件。

前提条件缺了任何一项，两个 server 都会隐藏这个 Tool。它根本不会出现在 agent 的 Tool 列表里，所以 agent 不会误用它。

- [REST server（`ignition-rest`）](#rest-serverignition-rest)
- [Runtime server（`ignition-runtime`）](#runtime-serverignition-runtime)
- [第 1 版不提供的 Tool](#第-1-版不提供的-tool)

scope、profile、allowlist 和 Precondition token 这几个词的解释见[运作原理](how-it-works.zh-CN.md#本文用到的词)。

## REST server（`ignition-rest`）

REST server 运行在你自己的机器上，通过 Gateway 的 Web API 和它通信。安装见[安装 REST server](setup-rest.zh-CN.md)。下文提到的每个设置都是环境变量，完整列表见[配置参考](configuration.zh-CN.md#rest-server-设置)。

### 每个 REST Tool 都需要的

- server 正在运行，并设置了 `IGNITION_MCP_GATEWAY_URL`、`IGNITION_MCP_GATEWAY_API_TOKEN` 和 `IGNITION_MCP_DATA_DIR`。
- Gateway 的 API 描述里列出了该 Tool 使用的路由。server 启动时读取这份描述，之后每 60 秒再读一次。如果你的 Gateway 缺少某个模块，例如 Alarm Notification，依赖它的 Tool 就会一直隐藏。`ignition://gateway/capabilities` 资源会显示 server 找到了什么。
- 调用方的凭证带有该 Tool 需要的 scope。`IGNITION_MCP_AUTH_MODE=none` 时，所有调用方都只有 `ignition.read`，所以只能读取。

### 读取 Tool

这些 Tool 不改任何东西。除非表中另有说明，它们只需要 `ignition.read` scope，不需要额外开关。

| Tool | 做什么 | 额外要求 |
| --- | --- | --- |
| `gateway_info` | 返回 Gateway 的名称、版本、edition、冗余角色和时区。 | 无 |
| `gateway_diagnose` | 检查 server 能否连上并登录 Gateway、从 Gateway API 里了解到什么，以及自己的存储是否正常。出问题时先用它。 | 无。即使连不上 Gateway 它也会显示 |
| `operation_diagnose` | 用 `correlationId` 查一次之前的调用，看它进行到了哪一步。 | 无。记录默认保留 72 小时 |
| `project_list` | 列出 Gateway 上的项目。 | 无 |
| `config_resource_search` | 按关键字查找配置资源类型，例如 "database"。 | 无 |
| `config_resource_describe` | 显示某个资源类型有哪些字段。 | 无 |
| `config_resource_names` | 列出某个类型下所有资源的名称。 | 无 |
| `config_resource_list` | 列出某个类型的资源及其设置。密码和密钥会被替换成 `<redacted>`。 | 无 |
| `config_resource_get` | 读取一个资源，并返回以后修改它时需要的 `signature`。 | 无 |
| `audit_query` | 读取 Gateway 某个审计日志里的条目。 | Ignition 里要有审计 profile |
| `alarm_pipeline_list` | 列出报警通知管道。 | Gateway 上装有 Alarm Notification 模块 |
| `alarm_pipeline_status` | 显示某个管道正在运行的实例。 | Gateway 上装有 Alarm Notification 模块 |
| `artifact_list` | 列出 server 替你保存的文件，例如导出文件。 | 无 |
| `artifact_info` | 显示一个已保存文件的大小、类型和过期时间。 | 无 |
| `perspective_view_list` | 列出某个项目的 Perspective View。 | Gateway 要支持项目导出 |
| `perspective_view_get` | 返回一个 View 的 JSON，以及修改它时需要的项目 fingerprint。 | Gateway 要支持项目导出 |
| `perspective_page_config_get` | 返回项目的 Perspective 页面配置。 | Gateway 要支持项目导出 |
| `perspective_session_props_get` | 返回项目的 Perspective 会话属性。 | Gateway 要支持项目导出 |
| `perspective_view_validate` | 检查 View JSON 的基本结构，不向 Gateway 发送任何东西。 | Gateway 要支持项目导出 |
| `project_export` | 把整个项目导出成可下载的 ZIP 文件。 | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` |
| `tag_config_export` | 把 Tag 配置导出成可下载的 JSON 文件。 | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` |

两个导出 Tool 有自己的开关，因为项目或 Tag 导出里可能含有凭证。导出结果会给出 REST server 上的下载地址 `/artifacts/<id>`，下载时需要和调用 Tool 相同的凭证。

server 还提供两个只读资源：`ignition://gateway/capabilities` 列出 Gateway 支持的功能，`ignition://gateway/openapi-info` 标识 server 加载的是哪一份 Gateway API 描述。

### 写入 Tool

所有写入 Tool 默认关闭。要打开一个，下面几条必须同时满足：

1. 打开了身份验证：`IGNITION_MCP_AUTH_MODE=static-token` 或 `jwt`。没有身份验证的 server 不能写入。
2. 调用方的 token 带有该 Tool 的 scope：配置修改用 `ignition.config`，`alarm_pipeline_cancel` 用 `ignition.control`。有 `ignition.admin` 并不代表有其他 scope。
3. 该 Tool 的类别开关已打开：`IGNITION_MCP_CONFIG_MUTATION_ENABLED=true` 或 `IGNITION_MCP_CONTROL_MUTATION_ENABLED=true`。
4. Tool 名在 `IGNITION_MCP_MUTATION_OPERATIONS` 里，这是一个逗号分隔的列表。
5. 它要修改的对象，也就是 Target，列在 `IGNITION_MCP_MUTATION_TARGETS` 里该 Tool 名下。没有 Target 条目的 Tool 什么都改不了。
6. server 使用的 Gateway API token 在 Gateway 上有写入权限。

下表列出每个 Tool 在这六条之外还需要什么。

| Tool | 做什么 | Scope | `IGNITION_MCP_MUTATION_TARGETS` 里的目标格式 | 额外要求 |
| --- | --- | --- | --- | --- |
| `config_resource_create` | 新建一个配置资源。名字已被占用时失败。 | `ignition.config` | `<resourceType>/<name>`，例如 `ignition/database-connection/MES` | 无 |
| `config_resource_update` | 修改一个资源。需要 `config_resource_get` 返回的 `signature`。 | `ignition.config` | `<resourceType>/<name>` | 无 |
| `config_resource_delete` | 删除一个资源。需要 `signature`。 | `ignition.config` | `<resourceType>/<name>` | 无 |
| `config_resource_rename` | 重命名一个资源。 | `ignition.config` | 旧名和新名两个 `<resourceType>/<name>` 都要列出 | 无 |
| `project_import` | 用 ZIP 文件替换一个已有项目，替换前先备份。 | `ignition.config` | 项目名 | Project writer，见下文，以及一个来自 `project_export` 或上传的 ZIP |
| `perspective_view_upsert` | 新建或替换一个 Perspective View。 | `ignition.config` | 项目名 | Project writer |
| `perspective_view_delete` | 删除一个 Perspective View。 | `ignition.config` | 项目名 | Project writer |
| `perspective_page_config_update` | 替换项目的页面配置。 | `ignition.config` | 项目名 | Project writer |
| `perspective_session_props_update` | 替换项目的会话属性。 | `ignition.config` | 项目名 | Project writer |
| `tag_config_import` | 用 `tag_config_export` 生成的文件新建 Tag，从不覆盖已有 Tag。 | `ignition.config` | 目标位置，例如 `[default]Imports` | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`，因为输入文件来自 `tag_config_export` |
| `artifact_delete` | 删除 server 保存的一个文件，不向 Gateway 发送任何东西。 | `ignition.config` | artifact id | 无 |
| `alarm_pipeline_cancel` | 停止一个正在运行的报警通知。 | `ignition.control` | 管道的完整路径 | 用 `IGNITION_MCP_CONTROL_MUTATION_ENABLED=true`，而不是配置类开关 |

替换项目文件的 Tool 由 Project writer 处理。两个设置都要打开：

```bash
IGNITION_MCP_PROJECT_WRITER_ENABLED=true
IGNITION_MCP_GATEWAY_ID=plant-gateway-1   # 为这台 Gateway 取一个固定不变的名字
```

每台 Gateway 只能运行一个 Project writer。server 无法跨机器检查这一点。

`project_import` 需要一个 server 已经保存的项目 ZIP。有两种方式获得：

- 先调用 `project_export`。这需要 `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`。
- 用 `POST /artifacts?kind=project_archive` 上传 ZIP。这需要 `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED=true`。

Target 条目有三种匹配方式：

- 完全相同的名字，例如项目名或 `<resourceType>/<name>`。
- 对 Tag 目标位置，匹配该路径及其下的所有路径。`[default]Imports` 也覆盖 `[default]Imports/Line1`，但不覆盖 `[default]Imports2`。
- `*`，允许所有 Target。只有你确实要这样时才写。

有些资源无论 allowlist 怎么写，都不能通过这些 Tool 修改：API token、安全级别、用户源、身份提供者、机密提供者、模块设置、MCP Module 自己的 Server Config，以及 `IgnitionMCPPolicy` Tag provider。完整列表在 `contracts/shared/refused-resource-types.json`。

打开一项配置修改的完整示例：

```bash
IGNITION_MCP_AUTH_MODE=static-token
IGNITION_MCP_STATIC_TOKENS='{"agent":{"token":"<一长串随机密钥>","scopes":["ignition.read","ignition.config"]}}'
IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"]}'
```

第 1 版的 REST server 没有需要 `ignition.admin` 的 Tool。`IGNITION_MCP_ADMIN_MUTATION_ENABLED` 存在，但打开它不会多出任何 Tool。

## Runtime server（`ignition-runtime`）

Runtime server 运行在 Gateway 内部。官方 Ignition MCP Module 承载它，本仓库以 Ignition 项目的形式提供 Tool。安装见[安装 Runtime server](setup-runtime.zh-CN.md)。

### 每个 Runtime Tool 都需要的

- Gateway 上已安装并运行 MCP Module。
- 已用 `ignition-mcp setup-native apply` 部署了 bundle 项目、Server Config 和 Runtime Target Policy。
- agent 连接 `<gateway-url>/data/mcp/<server-config-name>`，使用的 Ignition API token 的安全级别被 Server Config 允许。

### profile 决定有哪些 Tool

你传给 `setup-native apply` 的 `--profile` 决定 endpoint 提供哪些 Tool。

| Profile | 包含 | Tool 数 |
| --- | --- | --- |
| `readonly` | 13 个读取 Tool | 13 |
| `operator` | `readonly` 加 3 个控制 Tool | 16 |
| `configurator` | `readonly` 加 6 个 Tag 配置 Tool | 19 |
| `full` | 以上全部 | 22 |

要换 profile，就用新的 `--profile` 再运行一次 `setup-native apply`，然后重新连接 agent。

### 读取 Tool，所有 profile 都有

| Tool | 做什么 | 额外要求 |
| --- | --- | --- |
| `bundle_info` | 返回 bundle 版本、Gateway 版本和 MCP Module 版本。 | 无 |
| `tag_browse` | 列出某个路径下的 Tag 和文件夹。 | 无 |
| `tag_query` | 按路径、名称或类型搜索某个 Tag provider。 | 无 |
| `tag_read` | 读取最多 500 个 Tag 的当前值、质量和时间戳。 | 无 |
| `tag_get_config` | 读取 Tag 配置，并返回修改它时需要的 fingerprint。 | 无 |
| `udt_type_list` | 列出某个 Tag provider 里的 UDT 类型。 | 无 |
| `udt_type_get` | 读取一个 UDT 类型定义。 | 无 |
| `alarm_shelved_list` | 列出当前被搁置的报警。 | 无 |
| `historian_browse` | 列出 Historian 保存的路径。 | Gateway 上有 Historian |
| `historian_query_series` | 返回最多 50 个路径的原始历史数据，时间跨度最多 7 天、最多 25,000 个点。 | Gateway 上有 Historian |
| `historian_query_aggregate` | 对一段时间内的每个路径返回一个值，例如平均值或最大值。 | Gateway 上有 Historian |
| `database_query_list` | 列出你批准过的数据库查询。 | Named Query 注册表，见下文 |
| `database_query` | 运行一个已批准的查询。 | Named Query 注册表，见下文 |

endpoint 还提供三个只读资源：`bundle_info`、`tag_browse` 和 `tag_read` 输出的 JSON Schema。

#### Named Query 注册表

agent 永远不能发送 SQL。它只能运行你事先列出的 Named Query，每个查询用一个简短的名字，叫做 alias。这份列表放在 **Gateway 所在机器**的一个环境变量里，而不是你自己的电脑上：

```text
IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON
```

没有这个变量时，`database_query_list` 返回空列表，`database_query` 没有可运行的查询。一个包含单个查询的例子：

```json
{
  "schemaVersion": 1,
  "entries": [
    {
      "alias": "line_downtime",
      "description": "Downtime events for one production line, newest first.",
      "project": "MES",
      "path": "Reports/LineDowntime",
      "resultMode": "dataset",
      "datasourcePolicy": "named-query-fixed",
      "parameters": {
        "line": {"type": "string", "required": true, "maxLength": 32}
      },
      "pagination": {
        "mode": "offset",
        "limitParameter": "limit",
        "offsetParameter": "offset",
        "defaultPageSize": 50,
        "hardPageSize": 500,
        "maxOffset": 100000
      }
    }
  ]
}
```

各字段的规则见[配置参考](configuration.zh-CN.md#named-query-注册表)。设置变量时把 JSON 写成一行，然后重启 Gateway。用 Docker 的话，加在 compose 文件的 `environment:` 下。用服务方式安装的话，加到 Gateway 服务的环境变量里。

### 控制 Tool，`operator` 和 `full` 才有

| Tool | 做什么 | policy 里的 allowlist 键 |
| --- | --- | --- |
| `tag_write` | 向 Tag 写入新值，然后读回。每次调用默认 20 个写入，最多 100 个。 | `tag_write` |
| `alarm_shelve` | 按完整报警路径把报警搁置指定的秒数。 | `alarm_shelve` |
| `alarm_unshelve` | 按完整报警路径取消报警搁置。 | `alarm_unshelve` |

### Tag 配置 Tool，`configurator` 和 `full` 才有

| Tool | 做什么 | 需要 fingerprint | policy 里的 allowlist 键 |
| --- | --- | --- | --- |
| `tag_create` | 新建 Tag。路径已被占用时失败。 | 否 | `tag_create` |
| `tag_update` | 修改已有 Tag 的配置。 | 是 | `tag_update` |
| `tag_copy` | 把一个 Tag 或文件夹复制到新路径。目标已被占用时失败。 | 否 | `tag_copy`，按目标位置检查 |
| `tag_delete` | 删除 Tag 和文件夹。 | 是 | `tag_delete` |
| `tag_move` | 把 Tag 和文件夹移到新的上级文件夹。 | 是 | `tag_move`，源和目标都要检查 |
| `tag_rename` | 在原文件夹内重命名一个 Tag 或文件夹。 | 是 | `tag_rename`，按新路径检查 |

每次调用默认 20 个条目，policy 可以把上限提高到 100。

“需要 fingerprint”是指 agent 必须先用 `tag_get_config` 读取这个 Tag，再把拿到的 fingerprint 传进来。如果这期间有人改了这个 Tag，调用以 `conflict` 失败，什么都不改。

### Runtime 写入 Tool 都需要什么

profile 只让写入 Tool 变得可见。Tool 在改动任何东西之前，还会读取 **Runtime Target Policy**，这是 `setup-native apply` 存在 Gateway 上的一份小 JSON 文档。policy 缺失或损坏时，Tool 返回 `operation_disabled` 拒绝执行；目标不在该 Tool 的 allowlist 里时，返回 `permission_denied`。

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU"],
    "tag_update": ["[default]Plant"]
  },
  "alarmShelveMaxSeconds": 3600
}
```

- Tag 条目覆盖该路径及其下的所有路径。`[default]Plant/AHU` 覆盖 `[default]Plant/AHU/Temp`，但不覆盖 `[default]Plant/AHU2`。
- 报警条目是带 provider 前缀、不含 `*` 的报警路径，覆盖该路径及其下的报警路径。
- `allowlists` 里没有对应键的 Tool 什么都改不了。`["*"]` 允许全部。
- `[provider]_types_/` 下的 UDT 定义需要单独的条目，例如 `[default]_types_/Motor`。`*` 不覆盖它们。
- 任何东西都不能修改存放 policy 本身的 `IgnitionMCPPolicy` Tag provider。

policy 的所有字段见[配置参考](configuration.zh-CN.md#runtime-target-policy)。

## 第 1 版不提供的 Tool

`alarm_status`、`alarm_journal` 和 `alarm_acknowledge` 已经写好，但被关闭了。Ignition 的报警查询函数没有内置的返回行数上限，所以这些 Tool 无法保证回答有上限。它们的代码保存在 `packages/ignition-runtime-bundle/deferred/`，没有任何 profile 列出它们。

目前要查看报警相关信息，可以用 REST server 的 `alarm_pipeline_*` Tool 查看通知管道，或用 `alarm_shelved_list` 查看被搁置的报警。
