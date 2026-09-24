# 安装 REST server

> English: [`setup-rest.md`](setup-rest.md)。本文是英文版的译本，两者不一致时以英文版为准。

本指南带你从零开始，让 AI agent 通过 `ignition-rest` server 读取你的 Gateway。第一部分设置只读访问，在任何 Gateway 上试用都是安全的。第二部分教你逐个打开写入。

不确定需要的是不是这个 server？见[我需要哪个 server](how-it-works.zh-CN.md#我需要哪个-server)。

## 需要准备什么

- 一台你的电脑能连到的 Ignition 8.3 Gateway，以及它网页界面的管理员账号。本项目在 8.3.8 和 8.3.9 上测试过。
- 一台运行 Linux、macOS 或 Windows 的电脑。server 运行在这台电脑上，不需要在 Gateway 上安装任何东西。
- 大约 15 分钟。

Windows 步骤尚未在 Windows 上运行过。见 [Windows 说明](#windows-说明)。

## 第 1 步：安装 Git 和 uv

Git 用来下载代码。`uv` 会替你安装 Python 和项目需要的包，所以你不用自己装 Python。

在 Linux 或 macOS 上，打开终端运行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git --version        # 如果这一行报错，请从 https://git-scm.com 安装 Git
```

在 Windows 上，打开 PowerShell 7 运行：

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Git.Git -e
```

关掉终端再重新打开一个，让它找到刚装的程序。

## 第 2 步：下载项目

```bash
git clone https://github.com/sheon-sek/ignition-mcp.git
cd ignition-mcp
uv sync --all-packages
```

`uv sync` 会在需要时下载 Python 3.11 或更新版本，以及项目用到的所有包。本指南后面的命令都在 `ignition-mcp` 文件夹里运行。

## 第 3 步：在 Gateway 上创建 API token

server 用 Ignition API token 调用 Gateway。agent 永远看不到这个 token。

1. 打开 Gateway 网页界面，通常是 `http://<gateway-address>:8088`，然后登录。
2. 进入 Security 部分，新建一个 API token。
3. 给它一个 Gateway 允许**读取**配置的安全级别。以后要打开写入时，它还需要**写入**权限。
4. Ignition 显示 token 时把它复制下来。它的格式是 `名称:一长串随机字符`，Ignition 只显示这一次。

## 第 4 步：写一个设置文件

在 `ignition-mcp` 文件夹里新建一个文件，名叫 `ignition-rest.env`：

```ini
IGNITION_MCP_GATEWAY_URL=http://127.0.0.1:8088
IGNITION_MCP_GATEWAY_API_TOKEN=paste-your-token-here
IGNITION_MCP_DATA_DIR=/home/you/ignition-mcp-data
```

- `IGNITION_MCP_GATEWAY_URL` 是你打开 Gateway 用的地址，端口后面不要带任何路径。
- `IGNITION_MCP_DATA_DIR` 是 server 保存记录的文件夹，要写完整路径。在 Windows 上要给路径加单引号，例如 `IGNITION_MCP_DATA_DIR='C:\ProgramData\ignition-mcp'`。文件夹不存在时 server 会自己创建。

凡是含有 `"`、`{` 或 `\` 的值，例如本指南后面出现的 JSON 值，都要加单引号。不加的话，设置文件会吃掉双引号，server 会拒绝这个值。

这个文件里有机密信息，请妥善保管。在 Linux 和 macOS 上运行 `chmod 600 ignition-rest.env`。不要把它提交到 Git。

其他设置都是可选的，完整列表见[配置参考](configuration.zh-CN.md#rest-server-设置)。

## 第 5 步：启动 server

```bash
uv run --no-sync --env-file ignition-rest.env ignition-rest-mcp
```

让这个终端一直开着。按 Ctrl-C 之前 server 会一直运行。

在第二个终端里检查它是否就绪：

```bash
curl http://127.0.0.1:8000/health/ready
```

你要看到的是 `"ready":true`。如果看到 `"registryState":"UNAVAILABLE"`，说明 server 连不上 Gateway，或者 token 被拒绝了。见[常见问题](#常见问题)。

## 第 6 步：连接你的 AI 应用

MCP 地址是 `http://127.0.0.1:8000/mcp`，传输方式是 Streamable HTTP。

使用 Claude Code：

```bash
claude mcp add --transport http ignition-rest http://127.0.0.1:8000/mcp
```

其他客户端大多接受下面这样的 JSON 配置。各客户端的键名不完全一样，请查看它自己的 MCP 文档：

```json
{
  "mcpServers": {
    "ignition-rest": {"url": "http://127.0.0.1:8000/mcp"}
  }
}
```

## 第 7 步：试一试

问 agent：“我的 Gateway 运行的是哪个 Ignition 版本？”它应该会调用 `gateway_info`。再试试“列出 Gateway 上的项目”或“配置了哪些数据库连接？”

现在 agent 已经有了所有读取 Tool，列表见 [Tool 目录](tools.zh-CN.md#读取-tool)。

## 打开身份验证

没有身份验证时，任何能访问这个地址的人都能读取，但谁也不能写入。在把 server 分享给别人或打开写入之前，给每个调用方一个 token。在 `ignition-rest.env` 里加上：

```ini
IGNITION_MCP_AUTH_MODE=static-token
IGNITION_MCP_STATIC_TOKENS='{"my-laptop":{"token":"make-up-a-long-random-secret","scopes":["ignition.read"]}}'
```

生成随机密钥可以运行 `uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"`。

重启 server。现在客户端必须在请求头里带上这个密钥：

```bash
claude mcp remove ignition-rest
claude mcp add --transport http ignition-rest http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer make-up-a-long-random-secret"
```

在 JSON 客户端配置里，在 `"url"` 旁边加上 `"headers": {"Authorization": "Bearer make-up-a-long-random-secret"}`。

要让其他机器也能连接，再设置 `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` 和 `IGNITION_MCP_HOST=0.0.0.0`。`development` profile 只接受来自本机的连接。

## 打开一个写入

每次只打开一个 Tool、一个目标。下面的例子让 agent 可以修改一个名叫 `MES` 的数据库连接。

1. 给调用方的 token 加上 `ignition.config` scope：

   ```ini
   IGNITION_MCP_STATIC_TOKENS='{"my-laptop":{"token":"...","scopes":["ignition.read","ignition.config"]}}'
   ```

2. 打开配置写入，写明 Tool 和目标：

   ```ini
   IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
   IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
   IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"]}'
   ```

3. 确认第 3 步的 Gateway API token 有写入权限。
4. 重启 server，重新连接客户端。`config_resource_update` 现在会出现在 Tool 列表里。
5. 让 agent 去做修改。它会先用 `config_resource_get` 读取这个连接，再带着拿到的 `signature` 发送修改。
6. 在 Gateway 网页界面里检查结果，确认无误后再放开更多。

以后要允许更多 Tool，就把它们加到两个列表里，用逗号分隔：

```ini
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,config_resource_create
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"],"config_resource_create":["ignition/database-connection/MES_Test"]}'
```

每个写入 Tool 的目标格式见 [Tool 目录](tools.zh-CN.md#写入-tool)。

### 项目和 Perspective 写入

`project_import` 和 Perspective 写入 Tool 还需要 Project writer：

```ini
IGNITION_MCP_PROJECT_WRITER_ENABLED=true
IGNITION_MCP_GATEWAY_ID=plant-gateway-1
IGNITION_MCP_MUTATION_OPERATIONS=perspective_view_upsert
IGNITION_MCP_MUTATION_TARGETS='{"perspective_view_upsert":["MES"]}'
```

这里的目标是项目名。每次写项目之前，server 都会先备份这个项目，备份保留 7 天。

### 导出

`project_export` 和 `tag_config_export` 需要 `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`。结果里会给出 server 上的下载地址 `/artifacts/<id>`。下载时带上和客户端相同的 `Authorization` 请求头。

## 作为服务运行

要让 server 在你退出登录后继续运行，就用系统的服务管理器运行同一条命令，例如 Linux 上的 systemd，或 Windows 上的计划任务或服务包装程序。数据文件夹不要放在临时目录里，并设置 `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` 或 `secured`。

## 常见问题

| 你看到的 | 原因 | 解决办法 |
| --- | --- | --- |
| server 启动时报 `ConfigurationError` 并退出 | 某个设置缺失或有误，报错信息会写出是哪个。 | 修改 `ignition-rest.env` 里对应的那一行。 |
| `IGNITION_MCP_DATA_DIR is required` | 没设置数据文件夹。 | 加上 `IGNITION_MCP_DATA_DIR`，写完整路径。 |
| `development profile may bind only to loopback` | `IGNITION_MCP_HOST` 不是 `127.0.0.1`。 | 同时设置 `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal`。 |
| `/health/ready` 显示 `"registryState":"UNAVAILABLE"` | server 连不上 Gateway，或 token 被拒绝。 | 在同一台机器的浏览器里打开 Gateway 地址，检查 token，让 agent 运行 `gateway_diagnose`。 |
| Tool 列表里缺了某个 Tool | 缺少某项前提条件。 | 在 [Tool 目录](tools.zh-CN.md)里找到这个 Tool，逐项检查。 |
| `permission_denied` | 调用方 token 缺少 scope，或目标不在 `IGNITION_MCP_MUTATION_TARGETS` 里。 | 加上 scope 或目标，然后重启。 |
| `operation_disabled` | 某个开关没打开，或 Tool 不在 `IGNITION_MCP_MUTATION_OPERATIONS` 里。 | 打开它，然后重启。 |
| `conflict` | agent 读取之后有人改了目标。 | 让 agent 重新读取。 |

遇到任何错误，都可以把它的 `correlationId` 交给 `operation_diagnose`，或在 server 日志里搜索它。

## Windows 说明

这些步骤尚未在 Windows 上运行过。已知情况见 [D31](../decisions/D31-windows-support-scope.md)。

- 使用 PowerShell 7。
- 设置文件和 `uv run --env-file` 的用法相同。
- 在 Windows 上 server 不检查数据文件夹的文件权限。请设置文件夹权限，只让运行 server 的账号能读取。
- 如果你在 `.gitattributes` 加入之前就 clone 了仓库，有些检查会报 `must use LF line endings`。运行一次 `git rm --cached -rq . && git reset --hard`，或者重新 clone。这两种做法都会丢掉尚未提交的修改。
