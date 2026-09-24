# 安装 Runtime server

> English: [`setup-runtime.md`](setup-runtime.md)。本文是英文版的译本，两者不一致时以英文版为准。

本指南把 `ignition-runtime` server 安装到你的 Gateway 上，并让 AI agent 连上它。Runtime server 让 agent 可以使用 Tag 值、Tag 历史、Tag 配置、报警搁置和已批准的数据库查询。

和 REST server 不同，Runtime server 运行在 **Gateway 内部**。你要在 Gateway 上装两样东西：

1. Inductive Automation 官方的 Ignition MCP Module。MCP endpoint 由它运行。
2. 本仓库的 Tool，打包成一个 Ignition 项目，叫做 bundle。

本仓库的命令行工具 `ignition-mcp setup-native` 通过 Gateway 的 Web API 完成这两件事。你在自己的电脑上运行它。

不确定需要的是不是这个 server？见[我需要哪个 server](how-it-works.zh-CN.md#我需要哪个-server)。

## 需要准备什么

- 一台 Ignition 8.3 Gateway，已在 8.3.8 和 8.3.9 上测试过，你要有权限在上面安装模块和重启 Gateway。
- MCP Module 文件 `MCP-module-1.3.5.2026021307-SNAPSHOT.modl`，从 Inductive Automation 获取。仓库在 `tests/fixtures/modules/` 里保留了一份副本供自动化测试使用。它的记录说明这份文件是为测试提供的，不涉及再分发授权，所以使用这份副本前请先确认你与 Inductive Automation 的条款。
- 一台装有 Git 和 `uv` 的电脑。按[安装 REST server](setup-rest.zh-CN.md#第-1-步安装-git-和-uv)的第 1 步和第 2 步安装它们并下载项目。
- 大约 30 分钟，其中包括一次 Gateway 重启。

## 安装过程是怎样的

`setup-native` 有五个子命令，按下面的顺序运行：

| 子命令 | 做什么 | 会改动 Gateway 吗？ |
| --- | --- | --- |
| `install-module` | 校验 `.modl` 文件的校验和，上传、接受证书和许可协议、安装，然后重启 Gateway。 | 会 |
| `doctor` | 检查 Gateway、Module 以及之前的部署，每项检查输出一行。 | 不会 |
| `plan` | 列出 `apply` 将要新建或修改的内容。 | 不会 |
| `apply` | 部署 bundle、MCP endpoint 和 Runtime Target Policy，然后运行 `verify`。 | 会 |
| `verify` | 连接 MCP endpoint，确认它提供的 Tool 正好是预期的那些。 | 不会 |

`apply` 会在 Gateway 上创建三样东西：

- bundle 项目，默认名叫 `ignition_runtime`。
- 一个 **Server Config**，是 Module 对一个 MCP endpoint 的记录。它的名字会成为 endpoint 地址的一部分：`<gateway-url>/data/mcp/<name>`。它列出你所选 profile 的 Tool，以及允许谁连接。
- **Runtime Target Policy**，列出写入 Tool 可以改什么。

如果你要求，`apply` 还会为 agent 创建一个安全级别和一个 API token。

## 第 1 步：创建用于安装的 Gateway API token

`setup-native` 需要一个可以修改 Gateway 配置的 API token。它和 agent 以后使用的 token 不是同一个，后者在第 5 步创建。

1. 打开 Gateway 网页界面，用管理员登录。
2. 进入 Security 部分，创建一个有读写权限的 API token。
3. 把 token 单独存进一个文件：

   ```bash
   mkdir -p ~/.config/ignition-mcp
   printf '%s\n' 'paste-your-token-here' > ~/.config/ignition-mcp/gateway.token
   chmod 600 ~/.config/ignition-mcp/gateway.token
   ```

   文件里只能有一行。在 Linux 和 macOS 上，只能你自己读取。别人也能读的文件会被命令拒绝。

## 第 2 步：选择 profile

profile 决定 agent 能拿到哪些 Tool。先从 `readonly` 开始，以后再运行一次 `apply` 就能换。

| Profile | agent 可以 |
| --- | --- |
| `readonly` | 读取 Tag、Tag 配置、UDT、历史数据、已搁置的报警，并运行已批准的查询 |
| `operator` | 另外还能写入 Tag 值、搁置或取消搁置报警 |
| `configurator` | 另外还能新建、修改、复制、移动、重命名和删除 Tag |
| `full` | 以上全部 |

每个 profile 的完整 Tool 列表见 [Tool 目录](tools.zh-CN.md#profile-决定有哪些-tool)。

## 第 3 步：写 policy 文件

在 `ignition-mcp` 文件夹里新建 `policy.json`。下面这个版本不允许任何写入，适合 `readonly`：

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "allowlists": {}
}
```

`serviceIdentity` 是 agent 做出的修改在 Ignition 审计日志里显示的名字。`allowlists` 等你打开写入时再填。

## 第 4 步：写权限文件

权限文件规定调用方需要哪个安全级别才能使用这个 endpoint。新建 `permissions.json`：

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

`IgnitionMcpRuntimeReadonly` 是 `apply` 在下一步为 `readonly` profile 创建的安全级别。其他 profile 对应的名字是 `IgnitionMcpRuntimeOperator`、`IgnitionMcpRuntimeConfigurator` 或 `IgnitionMcpRuntimeFull`。

## 第 5 步：部署

可以用向导部署，也可以逐条运行命令。两种方式效果相同。

### 方式 A：向导

向导会逐个询问需要的值，说明每一步在做什么，并把你的回答存进 `.env`，方便下次再运行。它需要 `bash`，所以在 Windows 上请用 Git Bash 或 WSL。

```bash
./scripts/deploy-runtime-bundle.sh
```

问到是否创建 Runtime Security Level 和 Runtime API token 时，回答 `y`。把 `permissions.json` 和 `policy.json` 的路径告诉它。完成后它会显示 MCP endpoint 地址。然后继续[第 6 步](#第-6-步连接你的-ai-应用)。

向导有两处不足。它无法传入 `--runtime-token-insecure-channel`，所以如果你的 Gateway 地址以 `http://` 开头，请改用方式 B。它最后运行 `verify` 时也不带 agent token，所以即使部署成功，这一步也可能报 HTTP 401 或 403。请用[第 7 步](#第-7-步试一试)里的 `verify` 命令检查。

### 方式 B：逐条运行命令

在 `ignition-mcp` 文件夹里运行。先设置两个各条命令共用的值：

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
```

构建 bundle。它会在 `dist/release/` 里生成三个文件：ZIP、描述它的 manifest，以及校验和文件。

```bash
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
```

安装 MCP Module。先去掉最后一行运行一次，看看它要你接受的证书和许可协议。然后加上最后一行再运行。Gateway 会重启，命令最多等 10 分钟让它恢复。

```bash
uv run --no-sync ignition-mcp setup-native install-module \
  --file ~/Downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --accept-certificate --accept-eula --restart
```

如果 Module 已经装好，命令会显示 `NO CHANGE`，什么也不做。

检查 Gateway 并预览改动。这两条命令都不改任何东西。第一次部署前，`doctor` 会在 `server-config-presence` 和 `mcp-initialize` 上报 `FAIL`，因为 endpoint 还不存在，依赖它的检查会被跳过。请确认 `gateway-info` 和 `module-installed` 是通过的。

```bash
uv run --no-sync ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly

uv run --no-sync ignition-mcp setup-native plan \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly \
  --policy-file policy.json
```

`plan` 每项改动输出一行，例如 `CREATE bundle-project ignition_runtime: ...`。`BLOCKED` 行表示 `apply` 会拒绝执行，这一行会说明原因。最后一行总是 `No changes have been applied.`。

部署：

```bash
uv run --no-sync ignition-mcp setup-native apply \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly \
  --policy-file policy.json \
  --server-config-permissions-file permissions.json \
  --backup-dir backups \
  --provision-security-levels \
  --create-runtime-token --runtime-token-file ~/.config/ignition-mcp/runtime.token
```

`apply` 无法撤销。以后再运行时，`--backup-dir` 会在替换旧的 bundle 项目之前先存一份副本。`apply` 最后会输出一行汇总，例如 `apply: wrote=5 skipped=0 failed=0 => exit 0`。`exit 0` 表示全部写入并通过了验证。

`--create-runtime-token` 把 agent 的 API token 存进 `~/.config/ignition-mcp/runtime.token`。命令从不显示它。再次运行 `apply` 也不会替换它。

新 token 默认只能通过 `https` 使用。如果你的 Gateway 地址以 `http://` 开头，要在命令里加上 `--runtime-token-insecure-channel`。只能在你信任的网络上这样做，因为 token 会以明文传输。

`apply` 最后会运行 `verify`。第一次运行时，`verify` 还没有 agent token，可能在 `mcp-initialize` 上报 HTTP 401 或 403，`apply` 以 1 退出。部署内容其实已经写好了。按第 7 步带上 `--mcp-token-file` 运行 `verify` 确认即可。

## 第 6 步：连接你的 AI 应用

endpoint 地址是 `<gateway-url>/data/mcp/<server-config-name>`，例如 `http://127.0.0.1:8088/data/mcp/production`。传输方式是 Streamable HTTP。

agent 用 `runtime.token` 里的 token 登录，放在 `X-Ignition-API-Token` 请求头里。用 `cat ~/.config/ignition-mcp/runtime.token` 查看 token。

使用 Claude Code：

```bash
claude mcp add --transport http ignition-runtime http://127.0.0.1:8088/data/mcp/production \
  --header "X-Ignition-API-Token: <runtime.token 的内容>"
```

其他客户端在各自的 MCP 配置里填写同样的地址和请求头，例如：

```json
{
  "mcpServers": {
    "ignition-runtime": {
      "url": "http://127.0.0.1:8088/data/mcp/production",
      "headers": {"X-Ignition-API-Token": "<runtime.token 的内容>"}
    }
  }
}
```

## 第 7 步：试一试

问 agent：“Gateway 上有哪些 Tag provider 和顶层文件夹？”它应该会调用 `tag_browse`。再试试“读取 `[default]<某个 Tag 路径>` 的当前值”或“显示这个 Tag 最近一小时的历史”。

想检查部署时就运行 `verify`，每次 Gateway 重启后也要运行：

```bash
uv run --no-sync ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production --profile readonly
```

## 可选：批准数据库查询

`database_query` 只能运行你在 Gateway 的环境变量 `IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON` 里列出的 Named Query。先在某个 Ignition 项目里建好 Named Query，再照着 [Named Query 注册表](tools.zh-CN.md#named-query-注册表)的例子写。这个变量属于 Gateway 自己的进程，所以要在启动 Gateway 的地方设置，然后重启 Gateway。

## 打开写入

agent 能不能改某样东西由两件事决定：profile 里要有这个 Tool，policy 里要列出这个目标。

1. 把目标加进 `policy.json`。下面的例子让 agent 可以写入某台空调箱下的 Tag 值，并把它的报警搁置最多一小时：

   ```json
   {
     "schemaVersion": 1,
     "serviceIdentity": "ignition-mcp-service",
     "auditMode": "best_effort",
     "allowlists": {
       "tag_write": ["[default]Plant/AHU1"],
       "alarm_shelve": ["prov:default:/tag:Plant/AHU1"],
       "alarm_unshelve": ["prov:default:/tag:Plant/AHU1"]
     },
     "alarmShelveMaxSeconds": 3600
   }
   ```

   条目如何匹配见 [Tool 目录](tools.zh-CN.md#runtime-写入-tool-都需要什么)。

2. 用新 profile 运行 `plan`，再运行 `apply`，这次不要带那两个创建类的参数。现有的 token 和权限保持不变：

   ```bash
   uv run --no-sync ignition-mcp setup-native apply \
     --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
     --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
     --gateway-token-file ~/.config/ignition-mcp/gateway.token \
     --server-config-name production --profile operator \
     --policy-file policy.json \
     --backup-dir backups
   ```

3. 重新连接 AI 应用，让它加载新的 Tool 列表。
4. 先试一次写入，在 Ignition 里确认结果后，再放宽 allowlist。

只改 policy 时，编辑 `policy.json`，用同样的 profile 再运行一次 `plan` 和 `apply`。

## 常见问题

| 你看到的 | 原因 | 解决办法 |
| --- | --- | --- |
| `gateway-info` FAIL，HTTP 401 | Gateway 拒绝了安装用的 token。 | 检查 token 文件，以及 token 在 Gateway 上的权限。 |
| 退出码 2，提示 token 文件有问题 | 文件超过一行，或别人也能读取。 | 只保留一行，并对它运行 `chmod 600`。 |
| `module-installed` FAIL | MCP Module 没装或没有运行。 | 运行 `install-module`，或在 Gateway 网页界面查看模块列表。 |
| `plan` 显示 `BLOCKED mcp-module` | 同上。 | 先装好 Module。 |
| `bundle-project` FAIL，`UNMANAGED_SAME_NAME` 或 `MARKER_INVALID` | 已经有别的项目用了这个名字。 | 用 `--bundle-project` 换一个新名字。命令从不接管不是它创建的项目。 |
| `BLOCKED server-config`，提示权限 | endpoint 是新的，但没有提供权限文件。 | 加上 `--server-config-permissions-file permissions.json`。 |
| `inventory-tools` FAIL，带有 `missing=[...]` 或 `extra=[...]` | endpoint 提供的 Tool 和你指定的 profile 不一致。 | 传入部署时用的那个 `--profile`，或用你想要的 profile 运行 `apply`。 |
| `verify` 或 `doctor` 在 `mcp-initialize` 上显示 HTTP 401 或 403 | endpoint 需要登录。 | 加上 `--mcp-token-file ~/.config/ignition-mcp/runtime.token`。 |
| token 没错，agent 却登录不了 | token 只能用于 `https`，而 Gateway 用的是 `http`。 | 改用 Gateway 的 `https` 地址。或者在可信网络上，删除 Gateway 上的这个 token 和本地的 `runtime.token` 文件，再带上 `--create-runtime-token --runtime-token-insecure-channel` 运行 `apply`。 |
| `compatibility` UNKNOWN | 你的 Gateway 版本没有测试过。 | 在其他版本上这是正常的，不会阻止任何操作。 |
| 写入 Tool 返回 `operation_disabled` | policy 缺失或无效。 | 带上有效的 `--policy-file` 运行 `plan` 和 `apply`。 |
| 写入 Tool 返回 `permission_denied` | 目标不在 policy 里这个 Tool 的 allowlist 中。 | 把它加进 `policy.json`，再运行 `apply`。 |
| `database_query_list` 什么都没返回 | Gateway 上没有设置 Named Query 注册表变量。 | 设置它并重启 Gateway。 |

`doctor` 的每一行、`plan` 的每种动作和每个退出码的含义，见[运维手册](../operations/runbook.zh-CN.md)。

## Windows 说明

这些步骤尚未在 Windows 上运行过。已知情况见 [D31](../decisions/D31-windows-support-scope.md)。

- 向导需要 Git Bash 或 WSL。`uv run --no-sync ignition-mcp setup-native ...` 这些命令可以在 PowerShell 里运行，把行尾的 `\` 换成反引号 `` ` ``。
- 在 PowerShell 里这样设置共用的值：`$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"`、`$V = Get-Content packages/ignition-runtime-bundle/BUNDLE_VERSION` 和 `$rev = git rev-parse HEAD`，然后传入 `--source-revision $rev`。
- 用 `Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<token>"` 保存 token 文件。Windows 不检查文件权限，请自行设置，只让你的账号能读取。
- Windows 没有 `sha256sum`。要校验文件，运行 `Get-FileHash <文件> -Algorithm SHA256`，把结果和 `.sha256` 文件里的值比对。
- 如果你在 `.gitattributes` 加入之前就 clone 了仓库，构建会报 `must use LF line endings`。运行一次 `git rm --cached -rq . && git reset --hard`，或者重新 clone。这两种做法都会丢掉尚未提交的修改。
