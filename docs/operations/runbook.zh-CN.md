# 运维手册

> English: [`runbook.md`](runbook.md)。本文是英文版的译本，两者不一致时以英文版为准。命令、参数、环境变量、退出码、状态字符串和文件路径保留英文原文。

本手册是你了解基本用法之后，运维 Runtime server 部署时查阅的参考。它详细说明 `ignition-mcp setup-native` 的每个子命令、升级、Runtime Target Policy、在两个 server 上打开写入，以及如何阅读 `operation_diagnose` 的输出。

第一次安装请按[安装 Runtime server](../guide/setup-runtime.zh-CN.md) 操作，那里按顺序讲解了同样的命令。

| 我想…… | 章节 |
| --- | --- |
| 知道开始前要准备什么 | [前置条件](#前置条件) |
| 设置 token 文件和共用的值 | [环境变量与凭证文件](#环境变量与凭证文件) |
| 安装或升级 MCP Module | [安装 MCP Module](#安装-mcp-module) |
| 看懂 `doctor` 的某一行 | [用 `doctor` 诊断](#用-doctor-诊断) |
| 看懂 `plan` 的某一行 | [阅读 plan](#阅读-plan) |
| 确切了解 `apply` 做了什么 | [应用部署](#应用部署) |
| 检查一个部署 | [验证部署](#验证部署) |
| 部署更新的 bundle | [升级 bundle](#升级-bundle) |
| 修改 Runtime 写入 Tool 可以碰的范围 | [Runtime Target Policy](#runtime-target-policy) |
| 打开写入 | [打开写入](#打开写入) |
| 查一次 REST 调用发生了什么 | [阅读 `operation_diagnose` 的输出](#阅读-operation_diagnose-的输出) |
| 看懂退出码 | [退出码](#退出码) |

示例中 bundle 版本为 `0.7.0`，Server Config 名为 `production`，命令在仓库文件夹里运行。`ignition-mcp` 是 `uv run --no-sync ignition-mcp` 的简写。

## 前置条件

| 项目 | 来源 |
| --- | --- |
| Gateway 地址 | 你的 Gateway，例如 `http://127.0.0.1:8088` |
| 有写入权限的 Gateway API token | Gateway 网页界面的 Security 部分。存进文件，见下一节 |
| MCP Module 文件及其 SHA-256 | Inductive Automation。本仓库固定版本 `1.3.5.2026021307-SNAPSHOT`、build `2026021307`、SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`，记录在 `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| bundle 发布产物：ZIP、manifest 和校验和 | 用 `tooling.native.cli release` 构建，见下文 |
| Runtime Target Policy 文件 | 由你编写。见 [Runtime Target Policy](#runtime-target-policy) |
| Server Config 权限文件 | 由你编写。`apply` 创建 Server Config，或现有 Server Config 没有权限树时需要。见[配置参考](../guide/configuration.zh-CN.md#server-config-权限文件) |

你自己的电脑上需要这些工具：

| 工具 | Linux 或 macOS | Windows |
| --- | --- | --- |
| Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/) | [官方安装脚本](https://docs.astral.sh/uv/)或包管理器。`uv` 会替你安装 Python | `winget install --id=astral-sh.uv -e`，或同一个官方安装脚本 |
| Shell | 任意 POSIX shell | PowerShell 7。D31 第 6 节的检查清单用到 `-SkipHttpErrorCheck`，需要 7 版 |
| Git Bash 或 WSL | 不需要 | 只在运行 `bash` 脚本时需要：部署向导和 `tooling.ci.check_workflows` |
| Java 11 | 只有 Jython 测试需要（D29）。两个 server 都不需要 | 同左 |

本手册里的 Windows 指令都标注为**尚未在 Windows 上运行**。见 [D31](../decisions/D31-windows-support-scope.md)。

在仓库文件夹里构建发布产物：

```bash
uv run --no-sync python -m tooling.native.cli validate \
  --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
(cd dist/release && sha256sum -c "ignition-runtime-bundle-$V.sha256")
```

`release` 生成三个以 `packages/ignition-runtime-bundle/BUNDLE_VERSION` 中的版本命名的文件：`ignition-runtime-bundle-<version>.zip`、`.manifest.json` 和 `.sha256`。同一个 Git 版本构建两次，得到的文件完全相同。`release` 会读取 `tests/compatibility/evidence/`，但不修改它。

### 在 Windows 上构建发布产物

**尚未在 Windows 上运行。** 见 [D31](../decisions/D31-windows-support-scope.md)。先把 Git 版本存进一个变量，再以 `--source-revision $rev` 传入。用 `Get-FileHash` 或 `certutil` 代替 `sha256sum -c` 校验，把结果和 `.sha256` 文件里的值比对：

```powershell
$rev = git rev-parse HEAD
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli release `
  --project-dir packages/ignition-runtime-bundle/project `
  --out-dir dist/release `
  --source-revision $rev `
  --evidence-dir tests/compatibility/evidence
$V = Get-Content packages/ignition-runtime-bundle/BUNDLE_VERSION
Get-FileHash "dist/release/ignition-runtime-bundle-$V.zip" -Algorithm SHA256
```

## 环境变量与凭证文件

每个命令都从参数或环境变量获取 Gateway 地址和 token。参数优先于环境变量。

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_SETUP_MCP_URL=$IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token
chmod 0600 ~/.config/ignition-mcp/gateway.token
```

在 Windows 上，**尚未在 Windows 上运行**，见 [D31](../decisions/D31-windows-support-scope.md)：

```powershell
$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_SETUP_MCP_URL = "$env:IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\ignition-mcp"
Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<your-ignition-api-token>"
```

Windows 不强制 `0600` 规则，所以要给 token 文件设置文件系统 ACL，只让服务账号能读取。

token 文件的规则：

- 必须是普通文件，不能是符号链接，里面恰好有一行非空内容。
- 在 Linux 和 macOS 上只有所有者能读取，也就是权限 `0600`。
- 不符合以上规则是用法错误，退出码为 2。
- 优先用 `--gateway-token-file`，少用 `IGNITION_MCP_SETUP_GATEWAY_TOKEN` 变量，这样 token 不会留在进程环境里。

地址的规则：

- 地址必须是带主机名的完整 `http` 或 `https` URL，不能包含用户名或密码。
- 命令拒绝通过普通 `http` 把 token 发送给另一台机器，因为 token 会以明文传输。请使用 `https`，或在你信任的实验网络上加 `--allow-insecure-authorize`。

命令运行时不读取仓库。`--bundle-manifest` 是它们对“应该部署什么”的唯一描述，所以你可以把三个发布文件复制到另一台机器上，在那里运行命令。

## 安装 MCP Module

`setup-native install-module` 通过 Gateway 自己的模块路由，把你电脑上的一个 `.modl` 文件安装到 Gateway。它不下载任何东西，也不读取 manifest。Module id 和 build 取自文件里的 `module.xml`。

```bash
ignition-mcp setup-native install-module \
  --file ~/Downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token
```

| 参数 | 含义 |
| --- | --- |
| `--file PATH` | 必填。`.modl` 文件。哈希对上之前不会上传任何东西。 |
| `--sha256 HEX` | 必填。文件应有的 64 位十六进制哈希，大小写都可以。 |
| `--accept-certificate` | 接受 Module 的证书。不加时，命令只显示证书，不安装。 |
| `--accept-eula` | 接受 Module 的许可协议。不加时，命令只说明在哪里阅读协议，不安装。 |
| `--acknowledge-upgrade` | 允许安装比已安装版本更新的 build。 |
| `--restart` | 安装后重启 Gateway，并等到 Module 运行起来。 |

它还接受 `--gateway-url`、`--gateway-token-file`、`--timeout-seconds`、`--allow-insecure-authorize` 和 `--json`。

命令按固定顺序执行。每项检查都发生在它所保护的步骤之前，所以被拒绝时不留下需要撤销的东西。

1. **检查文件。** 计算文件哈希并打开 `module.xml`。以下任何情况都会在发出请求前以退出码 2 停止：哈希与 `--sha256` 不符、文件大于 64 MiB、不是 ZIP、没有 `module.xml`、没有 `<id>` 或 `<version>`、版本里没有 10 位 build 号、文件名不能用于上传，或者它不是 MCP Module `com.inductiveautomation.mcp`。
2. **与 Gateway 比对。** 读取 Gateway 的模块列表，每页 500 条，最多四页。
   - 已安装同一个 build：`NO CHANGE`，退出码 0，不上传。
   - 已安装更新的 build：拒绝，退出码 1。
   - 已安装的 build 无法比较：拒绝，退出码 1。
   - 文件的 build 更新：需要 `--acknowledge-upgrade`，否则以退出码 3 停止。
   - 模块列表无法读到结尾：拒绝，退出码 1，而不是当作 Module 不存在。
3. **上传**文件。如果 Gateway 报告的 Module id 不同，以退出码 1 停止，不安装任何东西。
4. **接受证书和许可协议。** 两个参数没有同时给出时，命令显示证书的主题、签发者和有效期，说明在哪里阅读协议，然后以退出码 3 停止。此时文件已经上传，但没有安装。给出参数时它会接受两者。Module 没有证书或许可协议时，对应步骤会被跳过。
5. **安装** Module。
6. **重启。** 不加 `--restart` 时，以退出码 0 结束，显示 `INSTALL` 或 `UPGRADE`，并提示你重启 Gateway、运行 `verify`。加了 `--restart` 时，它会重启 Gateway，每 5 秒检查一次，最多 10 分钟，直到 Module 以新 build 运行。做不到时以退出码 1 结束。

文本输出每步一行，格式为 `<MARKER> <step>: <detail>`，标记有 `DONE`、`SKIPPED`、`NEEDS-ACK`、`REFUSED` 和 `FAILED`，最后是一行汇总：

```console
install-module: INSTALL com.inductiveautomation.mcp build=2026021307 => exit 0
```

加 `--json` 时，同样的结果输出为一个 JSON 对象，包含 `outcome`、`steps[]`、`moduleId`、`moduleVersion`、`moduleBuild`、`installedBefore`、`restart`，以及命令执行到的 `certificate` 和 `eula` 信息。任何输出都不包含 token。

`install-module` 不检查兼容性列表，那是 `doctor` 的工作。仓库里只有一个 Module build，所以升级路径由单元测试覆盖，没有做过真实的升级。

## 用 `doctor` 诊断

`doctor` 不改任何东西。它运行一组固定的检查，每项输出一行。它不等待正在启动的 Gateway，所以重启后要再运行一次。

```bash
ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production \
  --profile readonly
```

`doctor` 和 `verify` 需要 MCP endpoint 地址。传入 `--mcp-url`，或传入 `--server-config-name`，命令会使用 `<gateway-url>/data/mcp/<name>`。endpoint 需要登录时，用 `--mcp-token-file` 传入 agent token。第一次 `apply` 之前 token 文件还不存在，此时不要加这个参数。

检查按以下顺序进行：`gateway-info`、`openapi-sha256`、`module-installed`、`bundle-project`、`server-config-presence`，然后 `server-config`、`project-import`、`security-levels`、`api-token` 和 `designers` 各一行 `capabilities.<name>`，接着 `mcp-initialize`、`inventory-tools`、`inventory-resources`、`inventory-prompts`、`bundle-info`，最后是 `compatibility`。

每行有一个状态：`PASS`、`FAIL`、`SKIP`、`NOT_APPLICABLE` 或 `UNKNOWN`。token 被拒绝、也没有 Server Config 时，输出类似这样：

```console
FAIL           gateway-info: GET /data/api/v1/gateway-info returned HTTP 401: { "message":"Unauthorized", ... }
SKIP           openapi-sha256: Gateway did not answer /data/api/v1/gateway-info
...
FAIL           mcp-initialize: initialize returned HTTP 404: { "message":"MCP server not found: production", ... }
SKIP           inventory-tools: MCP session unavailable
doctor: 16 check(s) {"FAIL": 2, "SKIP": 14} => exit 1
```

| 输出行 | 含义 | 怎么办 |
| --- | --- | --- |
| `gateway-info` FAIL，HTTP 401 | Gateway 拒绝了 token。 | 修正 token 文件，或给 token 读取权限。 |
| `module-installed` FAIL | MCP Module 没装或没有运行。 | 安装它，重启，再运行 `doctor`。 |
| `bundle-project` PASS `ABSENT` | 还没有部署任何东西。 | 第一次 `apply` 之前是正常的。 |
| `bundle-project` FAIL `MARKER_INVALID` | 已有同名项目，其归属标记损坏或属于别人。 | `plan` 拒绝替换它。重命名或删除那个项目，或换一个 `--bundle-project`。 |
| `bundle-project` FAIL `UNMANAGED_SAME_NAME` | 已有同名项目，但不是本工具创建的。 | 同上。命令从不接管不是它创建的项目。 |
| `bundle-project` FAIL `NOT standalone` | bundle 项目被标记为可继承。 | 把它改为独立项目，或换一个新的 `--bundle-project` 名字。 |
| `server-config-presence` FAIL | Server Config 还不存在。 | 第一次 `apply` 之前是正常的。`plan` 会建议 `CREATE`。 |
| `capabilities.<name>` `NOT_APPLICABLE` 或 `SKIP` | Gateway 的 API 描述里没有这个路由，或描述读不到。 | 对应的 `plan` 行是 `BLOCKED`，这项写入在这台 Gateway 上无法进行。 |
| `mcp-initialize` FAIL，HTTP 401 或 403 | endpoint 需要登录。 | 用 `--mcp-token-file` 传入 agent token。 |
| `inventory-tools` FAIL，带有 `missing=[...]` 或 `extra=[...]` | endpoint 提供的 Tool 与你指定的 profile 不一致。多了少了都算失败。 | 传入部署时用的 profile，或用你想要的 profile 再运行 `apply`。 |
| `bundle-info` FAIL | 已部署的 bundle 报告的版本或 Git 版本不同。 | 执行[升级 bundle](#升级-bundle)。 |
| `compatibility` UNKNOWN | 这个 Gateway、Module 和 bundle 的组合没有测试过，或缺少某个版本字段。 | 在未测试的版本上是正常的，不会阻止任何操作。 |

`compatibility` 把五个值与 manifest 里的测试列表比对：`gatewayVersion`、`gatewayBuild`、`mcpModuleVersion`、`mcpModuleBuild` 和 `bundleVersion`。这些 API 看不到 Module 的 SHA-256，所以不比较它。

## 阅读 plan

`plan` 根据和 `doctor` 相同的检查，算出 `apply` 会做什么，但不改任何东西。

```bash
ignition-mcp setup-native plan \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-0.7.0.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly \
  --policy-file policy.json \
  --json
```

每行格式为 `<ACTION> <kind> <name>: <reason>`。动作有 `CREATE`、`UPDATE`、`NO CHANGE`、`BLOCKED` 和 `SKIP`。最后一行总是 `No changes have been applied.`，加 `--json` 时也一样。

各行按 `apply` 执行的顺序排列：

1. `mcp-module`
2. `security-level` 和 `runtime-token`，你要求创建时才有
3. `bundle-project`
4. `server-config`
5. `runtime-policy`
6. 你没有要求创建时，再列出 `security-level` 和 `runtime-token`，仅供参考

`mcp-module` 行从不是 `CREATE`，因为 `apply` 不安装 Module。Module 在运行时它是 `NO CHANGE`，缺失或状态读不到时是 `BLOCKED`。

`bundle-project` 的 `UPDATE` 会写明版本变化的类型：`patch`、`minor`、`major` 或 `downgrade`。`major` 和 `downgrade` 会写着 `requires explicit acknowledgement in apply`，没有 `--acknowledge-upgrade` 时 `apply` 会拒绝它们。

只要有一行 `BLOCKED`，`plan` 就以退出码 3 结束。修正原因后再运行，原因通常是 Module 或项目重名。

## 应用部署

`apply` 先运行 `plan`，再做修改。它需要三个其他命令里可选的参数：`--server-config-name`、`--bundle-zip` 和 `--policy-file`。

```bash
ignition-mcp setup-native apply \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-0.7.0.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --profile configurator \
  --bundle-project ignition_runtime \
  --server-config-name production \
  --policy-file policy.json \
  --server-config-permissions-file permissions.json \
  --backup-dir /var/backups/ignition-mcp \
  --provision-security-levels \
  --create-runtime-token \
  --runtime-token-file ~/.config/ignition-mcp/runtime.token
```

写入任何东西之前：

- 缺少必填参数是用法错误，退出码 2。
- 它计算 `--bundle-zip` 的哈希并和 manifest 比对，不符时退出码 2。
- 任何 `BLOCKED` 行，或未确认的 `major`、`downgrade` 变化，都会以退出码 3 停止。
- 它检查 policy 文件，拒绝大于 32 KiB 的文件。
- 它检查参数组合。`--create-runtime-token` 需要 `--runtime-token-file`，以及来自 `--runtime-token-name` 或 `--server-config-name` 的名字。其他 `--runtime-token-*` 参数需要 `--create-runtime-token`。`--security-level-name` 需要 `--provision-security-levels` 或 `--create-runtime-token`。

它按以下顺序写入：

1. 安全级别，加了 `--provision-security-levels` 时。它是 `Authenticated` 的子级别，名为 `IgnitionMcpRuntime<Profile>`，除非用 `--security-level-name` 另行指定。已有的级别从不修改，有子级别的级别会被拒绝。
2. agent 的 API token，加了 `--create-runtime-token` 时。它只获得上面那个安全级别。密钥只写入 `--runtime-token-file`，文件以 `0600` 权限创建。Gateway 使用普通 `http` 时要加 `--runtime-token-insecure-channel`，否则 token 只能通过 `https` 使用。
3. bundle 项目。替换已有项目之前，先把旧项目存进 `--backup-dir`。
4. Server Config。新建时先以关闭状态创建，读回后再打开。已有的 Server Config 只更新 Tool 列表，其他一切保持不变，包括它是否处于打开状态。Tool 列表总是逐个列出，从不用 `*`。权限树取自 `--server-config-permissions-file`，没有时取自现有的 Server Config。
5. Runtime Target Policy。写入后读回，读回内容不一致时再写一次。

需要知道的几点：

- 无法撤销。某项写入失败时命令停止，之前的写入保留。`--backup-dir` 是唯一的本地副本。没有它的话，只有 Gateway 自己的配置备份能恢复旧状态。
- 命令从不显示 token。用同一个 token 文件再次运行时，它会用文件核对 Gateway 上的 token，报告 `NO CHANGE`，不会生成新 token。
- Module 有时会短暂地让新 Server Config 不提供任何 Tool。这时 `apply` 会把同一份 Server Config 重新发送，最多三次，每次输出一行 `REFRESH server-config ...`。结果是同样的部署。

`apply` 最后运行 `verify` 并输出它的报告，然后是一行汇总：

```console
apply: wrote=3 skipped=2 failed=0 => exit 0
```

退出码 1 表示某项写入或最后的检查失败。退出码 3 表示什么都没写。第一次运行时，最后的检查可能因为还没有 agent token 而报 HTTP 401 或 403。这时写入已经完成，带上 `--mcp-token-file` 运行 `verify` 即可。

## 验证部署

```bash
ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production \
  --profile readonly
```

检查项依次是 `endpoint-reachable`、`mcp-initialize`、`inventory-tools`、`inventory-resources`、`inventory-prompts`，profile 中每个资源一行 `resources-read <uri>`，每个 prompt 一行 `prompts-get <name>`，最后是 `bundle-info`。

- `endpoint-reachable` PASS 只表示这个地址有回应。
- 每一行都是 `PASS` 或 `NOT_APPLICABLE` 时，退出码才是 0。
- 资源或 prompt 列表为空时是 `NOT_APPLICABLE`，不算失败。bundle 没有 prompt。

每次 Gateway 重启后，以及 `install-module --restart` 之后，都要运行 `verify`。

## 升级 bundle

升级 bundle 就是用本仓库更新版本的 Tool 替换 bundle 项目。

1. 获取更新版本的仓库。如果你在开发 bundle，就提高 `packages/ignition-runtime-bundle/BUNDLE_VERSION` 里的版本号。`bundle_info` 里的版本或归属标记与这个文件不一致时，构建会失败。
2. 按[前置条件](#前置条件)构建并校验发布产物。
3. 用新 manifest 运行 `doctor`。`bundle-info` FAIL 是正常的，因为 Gateway 上跑的还是旧版本。在某个测试过的组合匹配之前，`compatibility` 一直是 `UNKNOWN`。
4. 用新 manifest 和 ZIP 运行 `plan`。应该看到类似 `UPDATE bundle-project ignition_runtime: redeploy managed bundle 0.7.0 -> 0.8.0 (minor)` 的一行。
5. 带上 `--backup-dir` 运行 `apply`。只有 `major` 变化或降级时才加 `--acknowledge-upgrade`。有意回滚就是通过降级完成的。
6. 运行 `verify`，然后重新连接 AI 应用，因为 Tool 列表可能变了。

升级会替换整个 bundle 项目。你手动加进 `ignition_runtime` 的任何内容都会丢失，除非 `--backup-dir` 保存了它。

## Runtime Target Policy

Runtime 写入 Tool 的 allowlist 来自 Gateway 上的一份文档，而不是 bundle。它以两个 Tag 的形式存在保留的 Tag provider `IgnitionMCPPolicy` 里：

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` 存放 JSON 文本。
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` 存放它的字节数。Tool 会先读大小，所以过大的文档不会被加载就被拒绝。

只有 `setup-native apply` 写入 policy。REST server 的 `config_resource_*` Tool 无论 allowlist 怎么写都拒绝 `IgnitionMCPPolicy` provider，所以 agent 改不了 policy。

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "auditProfile": "MCP_AUDIT",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU"]
  },
  "alarmShelveMaxSeconds": 3600,
  "tagUpdateMaxItems": 20
}
```

规则，来自 `contracts/shared/runtime-target-policy.schema.json` 和命令自身的检查：

- `schemaVersion`、`allowlists`、`serviceIdentity` 和 `auditMode` 必填。`schemaVersion` 为 `1`。
- `allowlists` 每个 Tool 名一个键，所以 Tag 条目永远不会放行报警 Tool。没有对应键的 Tool 什么都改不了。`"*"` 允许全部，必须明确写出。
- Tag 条目覆盖该路径及其下的路径，以 `/` 为边界：`[default]AHU` 覆盖 `[default]AHU/Temp`，但不覆盖 `[default]AHU2`。`_types_` 下的 UDT 定义需要明确的 `_types_` 条目，`*` 不覆盖它们。
- 报警条目是以 `prov:` 开头、不含 `*` 的带 provider 前缀的报警路径，覆盖该路径及其下的路径，以 `/` 或 `:` 为边界。
- `serviceIdentity` 是每次 Runtime 写入在 Ignition 审计日志里的执行者名字。agent 不能设置它。
- `auditMode` 为 `best_effort`、`required` 或 `off`。`required` 模式下，`auditProfile` 指定的审计 profile 不可用时拒绝写入。
- 每次调用的条目上限在 1 到 100 之间，不写时为 20。命令检查 `tagUpdateMaxItems`、`tagCreateMaxItems` 和 `tagCopyMaxItems`。schema 以同样方式限制 `tagDeleteMaxItems`、`tagMoveMaxItems`、`tagRenameMaxItems`、`tagWriteMaxWrites` 和 `alarmMaxPaths`。
- `alarmShelveMaxSeconds` 只能把搁置时长上限调低，不能超过 86400 秒。
- 存储的文本最多 32768 字节。

命令以固定格式存储 policy：键排序、没有空格。这样 `plan` 可以和已存储的副本逐字节比较。你的文件可以用任何格式书写。

要修改 policy，编辑文件，然后运行 `plan`，再运行 `apply`。`plan` 会显示 `UPDATE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy: replace the served policy (...)`，附带大小以及新旧 SHA-256 的开头部分。

policy 缺失、无法读取、过大或无效时，所有 Runtime 写入 Tool 都返回 `operation_disabled`。删除 policy 会关闭 Runtime 写入，而不会放开它们。

## 打开写入

两个 server 上的每一类写入一开始都是关闭的。共有三类：`CONFIG_MUTATION` 是配置修改，`CONTROL_MUTATION` 是控制操作，`ADMIN_MUTATION` 是管理操作。scope 和 profile 之间没有包含关系，每一层都要你自己打开。

在 Runtime server 上：

1. `--profile` 决定 endpoint 提供哪些 Tool。`readonly` 没有写入 Tool。`operator` 增加 `tag_write`、`alarm_shelve` 和 `alarm_unshelve`。`configurator` 增加 `tag_update`、`tag_create`、`tag_copy`、`tag_delete`、`tag_move` 和 `tag_rename`。`full` 两组都有。没有管理类 profile。
2. Server Config 的权限树决定谁可以连接。`--provision-security-levels` 和 `--create-runtime-token` 会创建相应的安全级别和 token。
3. Runtime Target Policy 的 allowlist 决定每个 Tool 可以修改哪些目标。profile 提供了某个 Tool，但没有 allowlist 条目时，它仍然什么都改不了。

在 REST server 上，写在它的环境设置里：

```bash
IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,project_import
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["com.inductiveautomation.historian/historian-provider/Core"],"project_import":["MES"]}'
IGNITION_MCP_CONTROL_MUTATION_ENABLED=true    # 用于 alarm_pipeline_cancel
IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true   # 用于 project_export 和 tag_config_export
IGNITION_MCP_PROJECT_WRITER_ENABLED=true      # 用于 project_import 和 Perspective 写入
IGNITION_MCP_GATEWAY_ID=plant-gateway-1
```

调用方的 token 还需要对应的 scope：`ignition.config` 或 `ignition.control`。`IGNITION_MCP_AUTH_MODE=none` 时 server 只能读取。导出开关和写入开关是分开的。

REST server 按以下顺序检查一次写入，第一个拒绝决定错误码：该类别已打开、Tool 在 `IGNITION_MCP_MUTATION_OPERATIONS` 里、目标不是被拒绝的资源类型、目标在 `IGNITION_MCP_MUTATION_TARGETS` 里、Gateway 提供对应路由。类别关闭的 Tool 也不会出现在 Tool 列表里。

先打开一个 Tool、一个目标，试用并检查结果，之后再放开更多。两个 server 都不会自动重试写入。

每个 Tool 需要什么，见 [Tool 目录](../guide/tools.zh-CN.md)。

## 阅读 `operation_diagnose` 的输出

`operation_diagnose` 是 REST server 上的读取 Tool。它只接受一个值 `correlationId`，必须是完整的 36 个字符的 id。格式不对时返回 `invalid_argument`。查找是精确匹配，从不做部分匹配。

去哪里找这个 id：

- 每个 REST Tool 错误都带有 `code`、`message` 和 `correlationId`。
- server 日志里这次调用的那一行也有同一个 `correlationId`。设置 `IGNITION_MCP_LOG_FORMAT=json` 可以得到结构化日志。
- 成功的写入也会返回它。例如 `project_import` 返回 `correlationId` 和 `transactionId`。

| 字段 | 含义 |
| --- | --- |
| `tool` | 发起这次调用的 Tool。 |
| `outcome` | `in_progress`、`succeeded`、`failed`、`outcome_unknown`、`cancelled` 或 `interrupted`。`in_progress` 且没有 `finishedAt`，表示调用仍在运行，或 server 在记录结果前停止了。 |
| `errorCode` | 错误码，成功时为 `null`。 |
| `startedAt`、`finishedAt` | 调用的开始和结束时间。 |
| `phases` | 调用依次经过的步骤，每步带时间。 |
| `phasesTruncated` | `true` 表示调用超过 32 步，最早的步骤被丢弃，保留最新的。 |
| `transactionId` | 项目写入对应的项目事务。 |
| `downstreamCorrelationId` | Gateway 对同一请求使用的 id，有的话才会出现。 |
| `auditResultMissing` | `true` 表示审计日志里没有这次调用的结果条目。 |

查找返回 `not_found` 的原因：

- 记录默认在 72 小时后或超过 10,000 条后被删除。见 `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS` 和 `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS`。
- 每个调用方只能看到自己的记录。别人的 id 返回 `not_found`，所以这个 Tool 不会泄露某次调用是否存在。只有带 `ignition.admin` 的调用方能看到所有记录。
- 调用时记录存储不可用，调用照常执行但没有记录。server 会在日志里写一行 `operation_record_failure`。

`outcome_unknown` 的意思是停下来看一看。先读取目标的真实状态，再决定下一步。server 从不自己重复一次结果不确定的写入。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。`doctor` 或 `verify` 没有 `FAIL`。`plan` 没有 `BLOCKED`。`apply` 全部写入并通过验证。`install-module` 安装或升级了 Module，发现已经装好（`NO CHANGE`），或已安装但在等待重启。 |
| 1 | 检查失败、写入失败或网络失败。对 `install-module` 还包括：Gateway 上的 build 更新、模块列表读不到结尾、Gateway 拒绝了上传、接受或安装，或 `--restart` 后 Module 没有恢复。 |
| 2 | 用法错误：参数错误、manifest 无法读取或无效、ZIP 与 manifest 不符，或 token 文件被拒绝。对 `install-module` 还包括发出请求前发现的文件问题：SHA-256 不符、超过 `.modl` 大小上限、没有可读的 `module.xml`、没有 10 位 build 号，或不是 MCP Module。 |
| 3 | 命令需要你没有给出的决定，所以什么都没改。`plan` 有 `BLOCKED` 行，`apply` 什么都没写。`install-module` 没有安装任何东西；在证书或许可协议这一步停下时，文件已经上传。 |

按 Ctrl-C 以退出码 2 结束。意外崩溃以退出码 1 结束，只输出错误类型，所以 token 不会通过堆栈信息泄露。

## Windows

Windows 不是受支持的平台，不过目前没有已知的问题。[D31](../decisions/D31-windows-support-scope.md) 记录了范围、已知限制，以及一份还没有人执行过的手动检查清单。

[前置条件](#前置条件)里的工具表和[环境变量与凭证文件](#环境变量与凭证文件)里的 PowerShell 代码块是 Windows 的起点，两处都带有**尚未在 Windows 上运行**的标注。

- **换行符。** 在 `.gitattributes` 加入之前 clone 的仓库保留 Windows 换行符，`tooling.native.cli validate` 会以 `must use LF line endings` 拒绝它们。运行一次 `git rm --cached -rq . && git reset --hard`，或者重新 clone。两种做法都会丢掉未提交的修改。见 [D31 第 5 节](../decisions/D31-windows-support-scope.md#5-migration-for-existing-windows-clones)。
- **校验和。** Windows 没有 `sha256sum -c`。在 `dist/release` 里运行 `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` 或 `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`，把结果和 `ignition-runtime-bundle-<version>.sha256` 里的值比对。
- **向导。** `scripts/deploy-runtime-bundle.sh` 是 bash 脚本，需要 Git Bash 或 WSL。`setup-native` 命令不需要它。
- **token 文件和数据文件夹。** Windows 跳过 `0600` 的 token 文件检查和 `0700` 的数据文件夹检查，只记录一条 WARNING。请用文件系统 ACL，只让服务账号能读取 token 文件和 `IGNITION_MCP_DATA_DIR`。

## 第 1 版的已知限制

- `alarm_status`、`alarm_journal` 和 `alarm_acknowledge` 被关闭了。Ignition 的报警查询函数没有行数上限，也不支持续取，所以这些 Tool 无法限制回答或预检查的规模。它们的代码在 `packages/ignition-runtime-bundle/deferred/`，没有任何 profile 列出它们。要重新打开其中任何一个，需要一个有上限的机制和新的实机测试证据。见 D12。
- MCP Module 返回结构化结果，但不发布 Tool 输出 schema（D27）。以 `contracts/schemas/` 里的 schema 为准。
- MCP Module 会丢掉对象里的 `null` 值，所以 Runtime 的回答会对它们编码（D28）。见[运作原理](../guide/how-it-works.zh-CN.md#给客户端开发者的两个细节)。
- 在 8.3.9 上，Module 回应格式记为 `FAILED_NATIVE_BINDING`；在 8.3.8 上记为 `VERIFIED_WITH_LIMITATION`。两者都没有记为获得生产支持，本手册里的任何命令也不会这样记录。
- bundle 版本仍是 0.x。
- 仓库里只有一个 Module build，所以 Module 升级只由单元测试覆盖，没有做过真实升级。
- `doctor` 和 `verify` 不等待正在启动的 Gateway。只有 `tests/harness/` 下的实机测试环境会等待。

## 来源

本手册的规则来自这些决策：D20 规定安装命令及其安全规则，D26 Phase 6 修订规定第 1 版的范围，D27 和 D28 规定 Runtime 的回应行为，D30 规定写入规则。`CONTEXT.md` 定义了 Module install、Module upgrade 和 Bundle upgrade。每个参数都与 `packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/setup_native/` 和各子命令的 `--help` 核对过。`doctor`、`plan` 和 `verify` 的示例是真实输出。`install-module` 的示例展示的是源码输出的格式。
