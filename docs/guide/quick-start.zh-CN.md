# 快速开始

> English: [`quick-start.md`](quick-start.md)。本文是英文版的译本，两者不一致时以英文版为准。命令、参数、环境变量、退出码和文件路径保留英文原文。

`ignition-mcp` 是同时装好并运行两个 MCP server 的那一条命令。它安装 MCP Module 并部署 Runtime bundle，创建两个 server 需要的每个安全级别、凭证和文档，启动 REST server，并把端点注册到 Claude Code 或 Codex。operator 把它当向导运行，agent 用一行命令运行它。

一个部署是一个具名文件夹，`~/.config/ignition-mcp/deployments/<name>/`。里面有 `deployment.toml`，含 Gateway 地址、环境和角色，还有生成的 policy 和 permissions 文档，以及每个机密一个 `*.secret` 文件。再运行一次会读取这个文件夹而不是再问一遍，所以 `setup` 可以重复运行。多个部署可以并存，一个 Gateway 一个。每个命令都用 `--deployment NAME` 选一个，默认是 `default`。

命令有 `setup`、`status`、`start`、`connect <role>` 和 `reset`。

## 安装

`ignition-mcp` 就在本仓库里，所以要在一个 checkout 里运行它：

```bash
git clone https://github.com/sheon-sek/ignition-mcp.git
cd ignition-mcp
uv sync --locked --package ignition-rest-mcp
source .venv/bin/activate
```

机器上没有 Python 3.11 或更新版本时，`uv` 会自己装一个。Windows 上用 `.venv\Scripts\activate` 激活。下文每条命令都在这个 checkout 里、在这个环境激活的状态下运行；`uv run --no-sync ignition-mcp` 是不激活时等价的写法。

## 向导

不带参数运行一个命令，并把 stdin 接在终端上。向导只问还缺的值，其他值不动。每个问题都写出默认值和可选项。

- 答错、路径不存在、文件读不了，或 Gateway 拒绝了某个 key 时，会带着原因再问一次，不用从头开始。
- 运行结束时，向导打印值完全相同的单行命令，方便你重复运行或交给 agent。机密从不出现在那一行里。
- 在 Git Bash 的 mintty 里没有伪控制台时，向导退回普通逐行提示，问题和校验都不变。这样的提示关不掉终端回显，粘贴的机密会在输入时显示在屏幕上。机密仍然不会出现在 CLI 的输出或日志里。

## 一行命令

参数给全时，命令什么都不问。这是 agent 用的方式。计划会改动任何东西时，`setup` 还需要 `--yes`，它代替向导里的确认。没有它时，命令在第一次写入之前就停下，并指出缺的是哪个参数。

```bash
ignition-mcp setup \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --roles analysis,engineer \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --accept-certificate --accept-eula \
  --yes
```

有两个确认永远不被 `--yes` 覆盖。Module 证书需要 `--accept-certificate`，Module 许可协议需要 `--accept-eula`，所以对着一台空白 Gateway 的第一次运行要把两个都写上。缺任何一个确认都会在写任何东西之前停下。`--json` 的输出从不提问，哪怕在终端上，因为读它的是程序。这次运行接受的每一项，以及它打开的每一个有风险的值，都出现在报告里。

先手工创建 Gateway key。Ignition 8.3 没有用用户名和密码换 key 的办法，所以在 Gateway 网页界面新建一个 API key，它的安全级别要在 Security > General Settings 的每一项权限下都打勾。把 key 按 `名称:密钥` 的格式单独写成一行放进文件：

```bash
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<name>:<your-ignition-api-key>' > ~/.config/ignition-mcp/gateway-token
chmod 0600 ~/.config/ignition-mcp/gateway-token
```

CLI 立刻拿这个 key 去问 Gateway，被拒绝或缺少某项权限时会再问一次。setup key 只在 setup 期间使用，永远不交给 REST server，后者有自己的 token `ignition-mcp-rest`。

## 两个助手角色

一个部署服务两个角色。operator 选的是角色，不是 profile，每个角色自带它的 Runtime profile 和 REST scope。

| | Analysis 助手 | Engineer 助手 |
| --- | --- | --- |
| Runtime Tools | `readonly` profile | `full` profile |
| REST Tools | 读取 Tool | 读取 Tool，加 `CONFIG` 和 `CONTROL` 修改 |
| Server Config | `analysis` | `engineer` |
| MCP endpoint | `<gateway-url>/data/mcp/analysis` | `<gateway-url>/data/mcp/engineer` |
| 安全级别 | `Authenticated/IgnitionMcpAnalysis` | `Authenticated/IgnitionMcpEngineer` |
| Runtime 凭证 | 它自己的 Gateway API token | 它自己的 Gateway API token |
| REST 凭证 | 带 `ignition.read` 的具名静态 token | 带 `ignition.read`、`ignition.config` 和 `ignition.control` 的具名静态 token |

一个 REST server 进程同时服务两个角色。它以 `static-token` 验证运行，带两个具名静态 token，所以 token 的 scope 决定每个角色能做什么。

## dev 和 prod 的默认值

一次 setup 运行有一个部署环境，`dev` 或 `prod`。`dev` 是默认值，环境和部署一起保存。它只选择默认值。

| 设置 | `dev` 默认值 | `prod` 默认值 |
| --- | --- | --- |
| 部署的助手角色 | Analysis 和 Engineer | 只有 Analysis |
| 安全级别 | 创建 | 只在 `--provision-security-levels` 时改动 |
| Runtime 和 REST 凭证 | 每个已部署角色都创建 | 只为 `--roles` 指名的角色创建 |
| Server Config 权限树 | 由角色生成 | 由角色生成 |
| Runtime Target Policy 的 allowlist | 每个 Runtime 修改 Tool 都是 `*` | 空 |
| 报警搁置上限 | 3600 秒 | 3600 秒 |
| REST 修改类别 | `CONFIG` 和 `CONTROL` 打开，`ADMIN` 关闭 | 全部关闭 |
| REST Target allowlist | 已打开类别都是 `*` | 空 |
| REST project writer | 打开 | 关闭 |
| REST 监听地址 | `127.0.0.1:8000` | `127.0.0.1:8000` |
| Runtime token 的安全通道 | Gateway URL 是 `http` 时不要求 | 要求 |

在 `dev` 里打开 `ADMIN` 就是在 `--rest-mutation-classes` 里加上 `admin`；这样运行还需要 ADMIN 修改类别的确认，因为它会改动 Gateway 自己的配置，而一行命令模式下的 `--yes` 覆盖这个确认。Engineer 助手开发项目并不需要 `ADMIN`。

operator 从不手写 policy 文件或 permissions 文件。CLI 从环境和角色生成两者，和部署一起保存，并让它们和 Gateway 保持一致。`prod` 把写入都默认关掉，靠下面表里的三个 REST 参数和一个空的 Runtime Target Policy 实现；这些默认值仍然可以被有意打开。把部署从 `dev` 改到 `prod` 会列出收紧的每一项，例如被清空的 allowlist 或被移除的角色，确认之后才写入。

## `setup`

`setup` 为选定的角色部署两个平面。它读取当前状态，显示要做的改动，接受每个具名风险，并在写任何东西之前问确认。`--dry-run` 只显示计划就停下。它可以安全地重复运行。

| 参数 | 含义 |
| --- | --- |
| `--gateway-url URL` | Gateway 的网页地址，例如 `http://127.0.0.1:8088`。 |
| `--environment dev\|prod` | 部署环境。默认 `dev`。 |
| `--roles LIST` | 要部署的助手角色，`analysis` 和 `engineer` 的逗号分隔子集。`dev` 默认 `analysis,engineer`，`prod` 默认 `analysis`。 |
| `--gateway-token-file PATH` | 存有 Gateway API key 的单行文件，格式是 `名称:密钥`。在 Linux 和 macOS 上权限必须是 `0600`。 |
| `--module-file PATH` | MCP Module 的 `.modl` 文件。`setup` 先在 `tests/fixtures/modules/` 和 `~/Downloads` 里找，并用 SHA-256 对得上固定 build 的文件。 |
| `--recreate-tokens` | 本地机密文件丢失时，删除并重建每个受管 token。机密文件丢失后用这个。 |
| `--provision-security-levels` | 在 `prod` 里创建角色缺少的安全级别。`dev` 已经会创建。 |
| `--rest-mutation-classes LIST` | REST server 可以提供的修改类别：`none`，或 `config`、`control`、`admin` 的逗号分隔列表。`dev` 默认 `config,control`，`prod` 默认 `none`。加上 `admin` 需要 ADMIN 确认，`--yes` 覆盖它。 |
| `--rest-target-allowlist *\|none` | 已打开的 REST 修改类别是否可以针对任何目标（`*`）或什么都不针对（`none`）。`dev` 默认 `*`，`prod` 默认 `none`。有类别打开时的 `*` 需要通配确认，`--yes` 覆盖它。 |
| `--rest-project-writer on\|off` | REST server 是否可以导入 Project。`dev` 默认 `on`，`prod` 默认 `off`。 |
| `--dry-run` | 只显示计划，不写任何东西。 |

各步骤按固定顺序运行，每一步报告 `OK`、`CHANGED`、`SKIPPED` 或 `FAILED` 以及原因：

1. 从本地文件安装 MCP Module。同一个 build 已经装好时是 `OK`，不上传任何东西。更低的 build 一律拒绝。
2. 创建角色的安全级别。`dev` 会创建它们；在 `prod` 里这一步需要 `--provision-security-levels`。
3. 创建每个角色的 Runtime token 和 `ignition-mcp-rest` token，每个机密写进自己的 `*.secret` 文件，权限 `0600`。
4. 部署 Runtime bundle 项目。替换受管项目之前先备份。带着 bundle 名字、但不是 `setup` 创建的项目，永远不会被接管。
5. 为每个角色创建一个 Server Config，带明确的 Tool 列表和它的权限树。
6. 把 Runtime Target Policy 写进 `IgnitionMCPPolicy` Tag provider。
7. 从角色和环境写出 REST server 的设置。

```bash
ignition-mcp setup --deployment default \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --accept-certificate --accept-eula --yes
```

## `status`

`status` 什么都不改。它读取 Gateway 和部署文件夹，每个检查一行，每行带一个状态。每次 Gateway 重启后，以及有人在 Gateway 上手工改动之后，都要运行它。

它接受 `--gateway-url` 和 setup key 文件 `--gateway-token-file`。它报告部署文件夹、Gateway、Module build、bundle 项目、每个角色四行（它的安全级别、token、Server Config 和端点）、Runtime Target Policy、REST token 和静态 token、REST 设置、Gateway 是否设置了 Named Query 注册表，以及任何残留文件。

```bash
ignition-mcp status \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

## `start`

`start` 在前台运行 REST server，直到你按 Ctrl+C。它在开始服务之前打印健康结果，以及每个已部署角色各一行端点，让你看到它已经就绪。agent 连接 `http://<bind>/mcp`。

| 参数 | 含义 |
| --- | --- |
| `--bind HOST:PORT` | REST server 监听的地址。默认 `127.0.0.1:8000`。没有 `--bind` 时，用 `deployment.toml` 里保存的 `bind`。其他主机需要 `--yes`。 |

```bash
ignition-mcp start
```

```bash
ignition-mcp start --bind 127.0.0.1:8000 --yes
```

agent 工作期间让 `start` 在它自己的终端里一直运行。它不作为后台服务启动。

## `connect`

`connect <role>` 把一个角色的端点注册到 agent 客户端。角色是 `analysis` 或 `engineer`。它为这个角色注册两个 MCP server：`ignition-runtime-<role>` 指向 `<gateway-url>/data/mcp/<role>`，`ignition-rest-<role>` 指向 REST server 的 `/mcp` 地址。它要求部署已经 setup 好，REST 端点还要求 `start` 正在运行。

| 参数 | 含义 |
| --- | --- |
| `--client claude\|codex\|none` | 要注册的客户端。`claude` 是 Claude Code，`codex` 是 Codex，`none` 什么都不注册。没安装的客户端会作为一个选不了的选择出现，原因里写出缺的是哪个程序。 |

```bash
ignition-mcp connect analysis --client claude
```

```bash
ignition-mcp connect engineer --client codex
```

## `reset`

`reset` 删除 `setup` 创建的东西，在 Gateway 上和本地都删。它只删除部署自己的记录写明由 `setup` 创建的资源，发现的其他东西会作为保留项列出。它在 `dev` 以外的环境被拒绝。

```bash
ignition-mcp reset \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

## 退出码和错误码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。 |
| 1 | 某一步失败，或连不上 Gateway。 |
| 2 | 参数或回答有问题。什么都没写。 |

加 `--json` 时，报告是一个文档，带稳定的错误码。错误码发布后含义不再改变，所以脚本可以按它匹配。

| 错误码 | 退出码 | 含义 |
| --- | --- | --- |
| `missing_input` | 2 | 某个值没有参数、没有保存值，也没有终端可以问。 |
| `invalid_input` | 2 | 参数或保存值没通过校验，也没有终端可以重问。 |
| `acceptance_required` | 2 | 某个具名风险或法律条款没有被接受。 |
| `gateway_token_rejected` | 2 | Gateway 回答了，并拒绝了 setup key。 |
| `deployment_unreadable` | 2 | `deployment.toml` 存在，但读不了或解析不了。 |
| `secret_file_invalid` | 2 | 机密文件丢失、读不了、权限过松或形状不对。 |
| `step_failed` | 1 | 某一步无法完成，原因写在那一行。 |
| `not_implemented` | 1 | 命令已注册，但还没有实现。 |
| `interrupted` | 2 | 你按了 Ctrl+C 或关掉了提示。之前结束的步骤会保留。 |
| `unexpected_error` | 1 | CLI 的 bug。只报告异常类型，所以机密不会泄漏。 |
| `gateway_unreachable` | 1 | DNS、TCP、TLS 或超时，Gateway 根本没回答，所以什么都没判断。 |
| `not_confirmed` | 2 | 计划显示了，但没有被确认。什么都没写。 |
| `module_not_active` | 1 | Gateway 列出了 MCP Module，但没有运行它，所以它承载的路由不存在。 |
| `module_uninstall_refused` | 1 | Gateway 拒绝把 Module 标记为卸载；`GATEWAY_MODULES_ENABLED` 里列了 Module ID 时它就会拒绝。 |

## 常见问题

| 你看到的 | 含义和做法 |
| --- | --- |
| Gateway 拒绝 setup key。 | key 不对，或它的安全级别缺某项权限。新建一个 API key，在 Security > General Settings 的每一项权限下都打勾，存进文件，再运行一次 `setup`。 |
| Runtime 或 REST 调用返回 HTTP 403，CLI 报告没有发送 token。 | 这个角色的机密文件里没有可用的机密。运行 `setup --recreate-tokens`，它会删掉 Gateway 上的 token 并新建一个。 |
| Runtime 调用返回 HTTP 403，CLI 报告 token 的安全级别不满足 Server Config 的权限。 | token 和 Server Config 对不上了，通常是手工改动之后。再运行一次 `setup`，它会恢复 Server Config 的权限树并报告改动。 |
| Runtime 调用返回 HTTP 403，CLI 报告 token 要求安全通道而 Gateway URL 是 `http`。 | token 是为 `https` 创建的，而部署指向 `http`。改用 Gateway 的 `https` 地址，或改用 `dev`，它不要求 `http` 上的安全通道。 |
| 连不上 Gateway。 | 地址不对，或 Gateway 没运行。先在浏览器里打开 `--gateway-url` 确认，再运行一次 `status`。 |
| 缺一个值，而且 stdin 不是终端。 | 命令以 `missing_input` 失败，退出码 2，并列出所有缺的参数。补上这些参数，或在终端上运行以得到向导。什么都没写。 |
| 找不到 Module 文件，它的 SHA-256 对不上，或它的 build 比 Gateway 上的低。 | `setup` 只安装 SHA-256 对得上固定 build 的本地 `.modl` 文件，也会拒绝更低的 build。用 `--module-file` 传入正确的文件，或把它放进 `~/Downloads`。 |
| `setup` 在 Module 上以 `module_not_active` 失败。 | Gateway 有 Module 但没有运行它，所以它不承载任何路由，Server Config 那一步本来会以 404 失败。原因那一行写出状态和列表里的其他信息，例如 `onStartup disabled`。Gateway 不允许启动的 Module 会停在 `INACTIVE` 加 `onStartup disabled`：检查 Gateway 环境里的 `GATEWAY_MODULES_ENABLED`。 |
| `reset` 以 `module_uninstall_refused` 失败。 | 卸载路由回答 `success=false`，Gateway 日志说这个 Module 不能卸载。Gateway 环境里的 `GATEWAY_MODULES_ENABLED` 列了 Module ID 时，它会拒绝任何 Module 卸载。去掉这个变量，重启 Gateway，再运行一次 `reset`；或在 Gateway > Modules 里卸载这个 Module。 |
| 本地机密文件丢失。 | `status` 和 `setup` 会报告这种不一致：Gateway 上的 token 还在，但保存它的文件不见了。运行 `setup --recreate-tokens`，它会删掉 Gateway 上的 token 并新建一个。 |
| 有人手工改了 Gateway 上的受管资源。 | `setup` 报告差异并在确认后恢复目标状态，因为那会覆盖别人的改动。不再和记录对得上的已接受风险，会在打开它的那次运行里重新接受。 |

## 接下来读什么

- [Tool 目录](tools.zh-CN.md)：每个 Tool，以及 agent 能看到它之前需要什么。
- [配置参考](configuration.zh-CN.md)：REST server 读取的每个设置、Runtime Target Policy 的字段，以及 Named Query 注册表。
- [运维手册](../operations/runbook.zh-CN.md)：升级、Runtime Target Policy、打开写入，以及阅读 `operation_diagnose` 的输出。
