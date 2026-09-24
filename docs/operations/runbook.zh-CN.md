# v1 运维 runbook

本 runbook 覆盖单个 `ignition-runtime-bundle` 部署的运维路径：安装 MCP Module，然后诊断、计划、应用与
验证该部署，升级 Bundle，维护 Runtime Target Policy，启用 Mutation 类别，以及在调用出问题时阅读
`operation_diagnose` 的输出。

权威依据：命令集及其安全规则以 D20 为准，v1 覆盖范围以 D26 Phase 6 修订案为准，Runtime 线上行为以 D27、
D28 为准，Mutation 契约以 D30 为准。Module 安装（Module install）、Module 升级（Module upgrade）、Bundle
升级（Bundle upgrade）等术语在 `CONTEXT.md` 中定义；本文件沿用这些术语及其含义。

每个 flag 都与 `packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/setup_native/` 中实际发布的源码核对
过，并与各子命令的 `--help` 输出核对过。下文中 `doctor`、`plan`、`verify` 的示例是真实的运行输出；
`install-module` 的示例展示的是该命令的输出格式（读自其源码）。

> 本文档是 [`runbook.md`](runbook.md) 的中文译本。英文版为准（canonical），如有歧义以英文版为准。
> 命令、flag、环境变量、退出码、状态字符串、文件路径与实现标识符一律保留英文原文。

## 前置条件

任何命令运行之前都需要具备以下条件。

| 项目 | 来源 |
| --- | --- |
| Gateway base URL | 你的部署，例如 `http://127.0.0.1:8088` |
| Ignition API token | Gateway 上的一个 `ignition/api-token` 资源，由运维方以单行形式保存在权限为 `0600` 的文件中 |
| MCP Module 文件及其 SHA-256 | Inductive Automation 官方下载渠道。本仓库固定 `com.inductiveautomation.mcp` 版本 `1.3.5.2026021307-SNAPSHOT`、build `2026021307`、SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`，记录在 `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| Bundle 发布产物：ZIP、manifest、校验和 | `tooling.native.cli release`，见下文说明 |
| Runtime Target Policy 文档 | 由部署方编写，schema 为 `contracts/shared/runtime-target-policy.schema.json` |
| Server Config 权限树 | 由部署方编写；当 `apply` 需要创建 Server Config，或已部署的 Server Config 没有可保留的权限树时必须提供 |

工具链按平台区分；上面的部署输入为两个平台共用。

| 工具链项目 | Linux/macOS | Windows |
| --- | --- | --- |
| Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/) | [官方安装脚本](https://docs.astral.sh/uv/)或包管理器 | `winget install --id=astral-sh.uv -e`，或同一个官方安装脚本 |
| Shell | 一个 POSIX shell | PowerShell 7 —— D31 清单第 6 步使用 `-SkipHttpErrorCheck`，需要 7 |
| Git for Windows | 不需要 | 仅用于基于 `bash` 的检查：`tooling.ci.check_workflows` 与内置的 bash 向导 |
| Java 11 | 仅用于录制式 Jython 测试（D29）；两个 server 都不需要 | 同上 |

本 runbook 中的 Windows 指令都标记为**尚未在 Windows 上运行**；见 [D31](../decisions/D31-windows-support-scope.md)。

从一份 checkout 构建发布产物：

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

`release` 会按 `packages/ignition-runtime-bundle/BUNDLE_VERSION` 命名写出三个文件：
`ignition-runtime-bundle-<version>.zip`、`.manifest.json` 和 `.sha256`。校验和文件兼容
`sha256sum -c`。构建器是确定性的，因此同一 revision 上运行两次会得到逐字节相同的归档；并且 `release`
只读取 `tests/compatibility/evidence/`，不会改写它。

### 在 Windows 上构建发布产物

**尚未在 Windows 上运行**；见 [D31](../decisions/D31-windows-support-scope.md)。先把 revision 存入变量，
再以 `--source-revision $rev` 传入；并用 `Get-FileHash` 或 `certutil` 校验和，而不是 `sha256sum -c`：

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

`IGNITION_MCP_SETUP_*` 系列变量只是兜底值，flag 永远优先于对应的变量。

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_SETUP_MCP_URL=$IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token
chmod 0600 ~/.config/ignition-mcp/gateway.token
```

在 Windows 上（**尚未在 Windows 上运行**；见 [D31](../decisions/D31-windows-support-scope.md)）：

```powershell
$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_SETUP_MCP_URL = "$env:IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\ignition-mcp"
Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<your-ignition-api-token>"
```

在 Windows 上不强制 `0600` 规则，因此 token 文件需要一个文件系统 ACL，只允许服务账户读取。

token 文件必须是常规文件（非符号链接）、没有 group/other 权限位（mode `0600`）、且恰好包含一行非空内容。
符号链接、权限过宽、或包含零行/两行 token，都属于用法错误并以退出码 2 结束。请优先使用
`--gateway-token-file` 而不是 `IGNITION_MCP_SETUP_GATEWAY_TOKEN`，这样凭证就不会出现在进程环境里。

URL 必须是带 host 的绝对 `http` 或 `https`，且不得内嵌凭证。向非 loopback 主机使用明文 HTTP 会在发出任何
请求之前被拒绝，因为那样这次运行会把 API token 明文带出去。请改用 `https`，或在受信任的实验网络上加
`--allow-insecure-authorize`。

这些命令不接受任何仓库路径。`--bundle-manifest` 是期望状态的唯一来源，因此运行期永远不会读取
`contracts/` 和 `packages/ignition-runtime-bundle/`。

## 安装 MCP Module

`setup-native install-module` 通过 Gateway 自有的 module 路由，把一个受信任的本地 `.modl` 文件放到
Gateway 上。它不需要 bundle manifest：它判断的 Module id 与 build 来自归档自身的 `module.xml`。它不下载
任何东西。

```bash
ignition-mcp setup-native install-module \
  --file ~/downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token
```

加上 `--accept-certificate` 和 `--accept-eula` 表示接受该 Module 携带的内容；`--restart` 会重启 Gateway
并等待 Module 恢复；`--acknowledge-upgrade` 允许安装比当前已安装 build 更高的 build。

| Flag | 含义 |
| --- | --- |
| `--file PATH` | 必填。本地 `.modl` 文件。在哈希匹配之前不会上传任何内容 |
| `--sha256 HEX` | 必填。该文件必须哈希到的 64 位十六进制值。大小写会折叠为小写 |
| `--accept-certificate` | 接受该 Module 的证书。缺省时这次运行只会打印证书，不安装任何东西 |
| `--accept-eula` | 接受该 Module 的 EULA。缺省时这次运行会说明到哪里阅读 EULA，不安装任何东西 |
| `--acknowledge-upgrade` | 允许安装比已安装 build 更高的 Module build |
| `--restart` | 安装后重启 Gateway，并等待 Module 恢复 |

该命令还共用 `--gateway-url`、`--gateway-token-file`、`--timeout-seconds`、`--allow-insecure-authorize`
和 `--json`。

流程顺序是固定的，每一步都在「发生之前」被拒绝，而不是发生之后再回退：

1. 读取文件、计算哈希、打开其中的 `module.xml`。此阶段的任何失败都以退出码 2 结束，且不发送任何请求：
   哈希与 `--sha256` 不符；文件超过 `.modl` 的 67108864 字节上界，或不是 ZIP，或没有 `module.xml`，或未
   声明 `<id>`/`<version>`，或版本写法中没有可比较的 10 位 build；basename 不是本 CLI 会作为 `fileName`
   上传的名字；或归档的 `<id>` 不是 `com.inductiveautomation.mcp`。该命令只安装 MCP Module、不装别的，
   因此外来 Module 产物会在任何 Gateway 调用之前被拒绝。
2. 以每页 500 条、最多四页的方式读取 `GET /data/api/v1/modules/healthy`，找出 Gateway 为该 Module id 报告的
   身份。已安装同一 build 时结果为 `NO CHANGE`，退出码 0，不上传。Gateway 上是更新的 build 时以退出码 1
   拒绝；已安装的 build 无法比较时同样拒绝，而不是盲目替换。文件中的 build 更高时需要
   `--acknowledge-upgrade`，否则运行以退出码 3 停止。当清单无法读到最后一项时（Gateway 不按其报告的总数
   翻页，或返回中没有 item 列表），这次运行会在上传之前以退出码 1 拒绝，而不是把这个 Module 当作不存在。
3. 用原始字节 `POST /data/api/v1/modules/upload?fileName=...`，其中 `fileName` 是文件的 basename。若
   Gateway 返回的 `moduleId` 不同，则以退出码 1 拒绝，不安装任何东西。
4. 读取 `GET /data/api/v1/modules/certificate` 和 `GET /data/api/v1/modules/eula`。缺少任一接受类 flag 时，
   这次运行会打印证书的 subject、issuer 和有效期，并说明 EULA 可以在哪里阅读，然后以退出码 3 停止。此时
   归档已经上传，所以那次运行会留下一次「已上传但未安装」。带上这些 flag 时，它会分别提交每一项接受；返回
   `409` 表示 Gateway 已经持有该接受记录。若某 Module 不携带证书或不携带 EULA，该步骤会报告为 skipped，
   而不是要求你接受。
5. `POST /data/api/v1/modules/install?moduleId=...`。
6. 不带 `--restart` 时，以退出码 0 结束，outcome 为 `INSTALL` 或 `UPGRADE`，输出一行「等待重启」的提示，
   并给出「重启 Gateway 后运行 `verify`」的指示。带 `--restart` 时，用 `confirm=true` 确认重启，然后每 5
   秒轮询一次 `modules/healthy`，最多 600 秒，直到 Module 以已安装的 build 对外服务。若 Gateway 始终没有以
   该 build 恢复，则以退出码 1 结束，并说明该安装仍在等待重启。

文本输出每个步骤一行，格式为 `<MARKER> <step>: <detail>`，marker 取 `DONE`、`SKIPPED`、`NEEDS-ACK`、
`REFUSED`、`FAILED`，最后是一行汇总：

```console
install-module: INSTALL com.inductiveautomation.mcp build=2026021307 => exit 0
```

`--json` 会把同一次运行报告为单个对象，包含 `outcome`、`steps[]`、`moduleId`、`moduleVersion`、
`moduleBuild`、`installedBefore`、`restart`，以及在该步骤到达时附带的 `certificate` 和 `eula` 视图。证书
视图只包含 subject、issuer、有效期区间和自签名标志；任何输出都不包含凭证。

两条运维注意事项：`install-module` 不查询兼容性矩阵，因为那由 `doctor` 报告；并且它绝不会在没有对应 flag
的情况下接受证书或 EULA。Module 升级（即更高的 build）就是第 2 步中那条需要确认的路径。本仓库只持有一个
Module build，因此 v1 是用拒绝路径和单元测试来证明该逻辑，而不是用真实的第二个 build 做 build 间升级。

## 用 `doctor` 诊断部署

`doctor` 是只读且有序的。它从不等待正在启动中的 Gateway：`initialize` 只尝试一次，所以请在 Gateway 重启后
重新运行它，而不是把它当作就绪探针。

下列命令假定已经按上一节导出了那两个变量并准备好了 token 文件。它们不在命令行上传递 URL 或凭证，从而避免
机密出现在进程列表中。

```bash
ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly
```

`doctor` 和 `verify` 需要一个 MCP endpoint。可以传 `--mcp-url`，也可以传 `--server-config-name` 让命令自行
推导 `<gateway-url>/data/mcp/<name>`。

检查按此顺序执行：`gateway-info`、`openapi-sha256`、`module-installed`、`bundle-project`、
`server-config-presence`，然后为 `server-config`、`project-import`、`security-levels`、`api-token` 和
`designers` 各输出一行 `capabilities.<name>`，接着是 `mcp-initialize`、`inventory-tools`、
`inventory-resources`、`inventory-prompts`、`bundle-info`，最后是 `compatibility`。

状态取值为 `PASS`、`FAIL`、`SKIP`、`NOT_APPLICABLE` 和 `UNKNOWN`。对一个不存在该 Server Config 的 Gateway
做一次真实运行，输出类似：

```console
FAIL           gateway-info: GET /data/api/v1/gateway-info returned HTTP 401: { "message":"Unauthorized", ... }
SKIP           openapi-sha256: Gateway did not answer /data/api/v1/gateway-info
...
FAIL           mcp-initialize: initialize returned HTTP 404: { "message":"MCP server not found: production", ... }
SKIP           inventory-tools: MCP session unavailable
doctor: 16 check(s) {"FAIL": 2, "SKIP": 14} => exit 1
```

按下面的方式解读这些失败：

| 报告行 | 含义 | 处理方式 |
| --- | --- | --- |
| `gateway-info` FAIL 且 HTTP 401 | token 被拒绝 | 重新签发一个具备读取权限的 token，或修正 token 文件 |
| `module-installed` FAIL | MCP Module 缺失或不健康 | 安装它、重启，然后再次运行 `doctor` |
| `capabilities.<name>` 为 `NOT_APPLICABLE` 或 `SKIP` | Gateway 未声明该路由，或 OpenAPI 清单不可用 | 对应的 plan 行会是 `BLOCKED`；不要指望那次写入会发生 |
| `bundle-project` FAIL `MARKER_INVALID` | 存在同名 project，但它的归属标记是外来的或格式错误 | `plan` 会拒绝接管；请重命名或移除该外来 project |
| `bundle-project` FAIL `UNMANAGED_SAME_NAME` | 存在同名 project 但没有归属标记 | 同样拒绝。该命令绝不会接管别人的 project |
| `bundle-project` FAIL `NOT standalone` | 受管理的 project 是可继承的 | 将其改为 standalone，或用 `--bundle-project` 指向新的名字 |
| `inventory-tools` FAIL 且带 `missing=[...]` 或 `extra=[...]` | 该 endpoint 没有提供 manifest 中该 profile 的清单 | 清单是精确匹配的。超集和子集都会失败。请用正确的 `--profile` 重新 `apply` |
| `bundle-info` FAIL | 已部署的 bundle 报告的 `bundleVersion` 不同，或在 manifest 已盖章时 `bundleSourceRevision` 不同 | 执行下文的 Bundle 升级 |
| `compatibility` UNKNOWN | 观测到的组合在 `testedTuples` 中没有匹配行，或某个身份字段不完整 | 在未测试过的 Gateway 上这是预期结果。该命令绝不会把兼容性结论升级 |

`compatibility` 按五个字段匹配：`gatewayVersion`、`gatewayBuild`、`mcpModuleVersion`、`mcpModuleBuild` 和
`bundleVersion`。`gate` 和 `mcpModuleSha256` 无法通过这些 API 观测，因此不会出现在比较中。

## 读取 plan

`plan` 基于同样的观测推导出意图，不做任何写入。

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

每一行形如 `<ACTION> <kind> <name>: <reason>`，最后一行永远是 `No changes have been applied.`，带 `--json`
时也一样。Action 取 `CREATE`、`UPDATE`、`NO CHANGE`、`BLOCKED`、`SKIP`。行的顺序与 `apply` 的执行顺序一致：
先是 `mcp-module`，然后是可选启用的 `security-level` 和 `runtime-token`，接着是 `bundle-project`、
`server-config`、`runtime-policy`，最后是本次运行不写入的那些平面所对应的、仅用于探测的 `security-level`
和 `runtime-token` 行。

`mcp-module` 这一行永远不是 `CREATE`。Module 是前置条件，因此检测到健康 Module 时该行为 `NO CHANGE`，在
缺失或其状态无法读取时为 `BLOCKED`。

`bundle-project` 的 `UPDATE` 会标出 D21 变更类别：`patch`、`minor`、`major` 或 `downgrade`。`major` 和
`downgrade` 会带有 `requires explicit acknowledgement in apply` 字样，`apply` 在没有
`--acknowledge-upgrade` 时会拒绝它们。

只要出现任何 `BLOCKED` 行，`plan` 就以退出码 3 结束。修复原因（通常是 Module 安装或外来 project），然后
重新运行。

## 应用部署

`apply` 先计划、再写入。它要求三个在其他命令中都是可选的 flag。

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

决定行为方式的规则：

- `apply` 需要 `--server-config-name`、`--bundle-zip` 和 `--policy-file`。缺任何一个都是用法错误，退出码 2。
- `--bundle-zip` 会先与 manifest 中的 `artifact.sha256` 做哈希比对。不匹配时以退出码 2 结束，而不是针对
  另一个产物去计划。
- 只要出现任何 `BLOCKED` 的 plan 行，就会在哪怕一次写入之前停止，退出码 3。未确认的 `major` 或
  `downgrade` bundle 变更同样会以这种方式停止。
- 写入按 plan 的顺序执行：Security Level、Runtime API token、bundle Project、Server Config、Runtime Target
  Policy。某次写入失败会停止后续步骤，且不会回滚。
- 没有撤销。`--backup-dir` 是唯一的本地副本：在 `apply` 覆盖受管理的 project 之前，它会把已部署的归档导出
  到该目录。没有它，Gateway 自身的配置备份就是你唯一的恢复途径。
- Server Config 会先以 disabled 创建、读回，然后用那次读回得到的 signature 启用。更新时会在一次写入中调和
  Tool 列表，并保留 `enabled` 以及运维方持有的其他所有字段。Tool 列表始终是显式列举，绝不用 `*`。权限树在
  提供 `--server-config-permissions-file` 时来自该文件，否则来自已部署的资源；两条路径都拿不到权限树的
  Server Config 会成为一个 `BLOCKED` 的 plan 行。
- policy 文档在写入任何内容之前会被校验并规范化（canonicalize），超过 32768 字节会被拒绝。写入之后，
  `apply` 会通过官方 export 路由读回所服务的 Tag，若读回结果不一致，则用一次幂等的重新导入修复。
- CLI 绝不回显凭证。创建出来的 Runtime token 只会写进你指定的文件，并以 mode `0600` 创建。第二次运行会
  用该文件中的密钥去比对 Gateway 提供的 token 哈希，从而证明这个 token 仍归你所有，因此重复运行是
  `NO CHANGE`，而不是轮换。
- 可选启用的 flag 在解析阶段就拒绝错误输入：`--create-runtime-token` 需要 `--runtime-token-file`，并且需要
  `--runtime-token-name` 或 `--server-config-name`；三个 `--runtime-token-*` flag 在没有
  `--create-runtime-token` 时会被拒绝；`--security-level-name` 在没有 `--provision-security-levels` 或
  `--create-runtime-token` 时会被拒绝。

由于 Module 会在 project 自己的线程上注册该 project 的 provider，基于刚写入的 Server Config 构建的 endpoint
可能会在 `initialize` 时报告没有任何能力。`apply` 会最多三次重新宣告同一份已批准的文档，每次输出一行
`REFRESH server-config ...` 并记录到 `refreshes[]`，之后才判定结果。这样之后部署就是 `NO CHANGE`。

`apply` 最后会执行 `verify` 序列并把其报告嵌入自己的输出，所以一次正常的运行会依次打印 plan 行、写入行、
一个空行、verify 行，以及一行汇总：

```console
apply: wrote=3 skipped=2 failed=0 => exit 0
```

退出码 1 表示某次写入失败或验证失败。退出码 3 表示这次运行什么都没写。

## 验证部署

```bash
ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly
```

顺序为 `endpoint-reachable`、`mcp-initialize`、`inventory-tools`、`inventory-resources`、
`inventory-prompts`，然后为 profile 清单中的每个 Text Resource 输出一行 `resources-read <uri>`、为每个
Prompt 输出一行 `prompts-get <name>`，最后是 `bundle-info`。`endpoint-reachable` 的 `PASS` 只意味着那个 URL
有响应。退出码 0 要求每一项检查都是 `PASS` 或 `NOT_APPLICABLE`；空的 Resource 或 Prompt 清单属于
`NOT_APPLICABLE`，不是失败。

每次 Gateway 重启之后，以及每次带 `--restart` 的 Module 安装之后，都要运行 `verify`。

## Bundle 升级

Bundle 升级会用更新的 bundle 版本替换受管理的 Runtime Bundle Project。按照 D26 Phase 6 修订案，这就是 v1 的
升级路径。

1. 提升 `packages/ignition-runtime-bundle/BUNDLE_VERSION`。`tooling.native` 会拒绝 `bundle_info` 字面量或
   project 归属标记与该文件不一致的构建，因此版本只有一个来源。
2. 按「前置条件」一节所示构建并校验发布产物。
3. 用新的 manifest 运行 `doctor`。预期 `bundle-info` FAIL，因为已部署的 bundle 仍报告旧版本；并预期
   `compatibility` 保持 `UNKNOWN`，直到新 bundle 部署完成且有 `testedTuples` 行匹配其组合。
4. 用新的 manifest 和 ZIP 运行 `plan`。预期看到 `UPDATE bundle-project ignition_runtime: redeploy
   managed bundle 0.7.0 -> 0.8.0 (minor)`。
5. 带 `--backup-dir` 运行 `apply`。仅当变更是 `major` 或降级时才加 `--acknowledge-upgrade`，这也是你刻意
   回滚 bundle 的方式。
6. 运行 `verify`，然后让 MCP 客户端重新连接，因为 Tool 清单可能已经变化。

`UPDATE` 会替换整个受管理的 Project。你手工写入 `ignition_runtime` 的任何内容都会丢失，除非你先导出过它
—— 这正是 `--backup-dir` 的用途。

## Runtime Target Policy

Runtime 平面从部署方自有的文档（而不是 bundle）读取它的 Target 允许列表。该文档位于保留的 Tag provider
`IgnitionMCPPolicy` 中，由两个 Tag 组成：

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` 保存规范化（canonical）的 JSON 文本；
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` 保存其字节长度，handler 会先读它，因此超过上限的文档会
  在未被完整物化（materialize）的情况下被拒绝。

`setup-native apply` 是唯一受支持的写入方，而通用的 `config_resource_*` Tool 无论 Target 允许列表怎么写
都会拒绝该保留 provider，因此 MCP 调用方无法改动这份 policy。

文档以规范化形式存储：键有序、无空白。正是这种逐字节的稳定性，使 `plan` 能把自身计算的 SHA-256 与所服务的
值比较并报告 `NO CHANGE`。手工格式化该文件没有害处，因为 CLI 会重新规范化它。

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "auditProfile": "MCP_AUDIT",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU/*"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU/*"]
  },
  "alarmShelveMaxSeconds": 3600,
  "tagUpdateMaxItems": 20
}
```

规则来自 `contracts/shared/runtime-target-policy.schema.json` 以及 CLI 的形状检查：

- `schemaVersion`、`allowlists`、`serviceIdentity` 和 `auditMode` 为必填。
- `schemaVersion` 必须是 `1`。
- `allowlists` 以 Tool 名为键，因此 Tag 的允许列表不能代替 Alarm 的允许列表。某个键缺失意味着该 Tool 没有
  任何目标。放开全部需要显式的 `"*"`。
- `serviceIdentity` 是非空字符串，它是 Runtime Mutation 的审计 actor。调用方无法提供它。
- `auditMode` 取 `best_effort`、`required` 或 `off`。`auditProfile` 可选且非空。
- 条目数量上限的取值范围是 1 到 100。`tagUpdateMaxItems`、`tagCreateMaxItems` 和 `tagCopyMaxItems` 由 CLI
  检查；schema 对 `tagDeleteMaxItems`、`tagMoveMaxItems`、`tagRenameMaxItems`、`tagWriteMaxWrites` 和
  `alarmMaxPaths` 施加同样的约束。某个上限缺失时，取项目默认的 20 个目标。
- `alarmShelveMaxSeconds` 是正整数，产品硬上限为 86400 秒（D12）。部署方可以调低，永远不能调高。
- 规范化文本必须不超过 32768 字节。

要修改 policy，请编辑该文件并依次运行 `plan`、`apply`。`plan` 会报告
`UPDATE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy: replace the served policy (...)`，并带上
字节数以及新旧 SHA-256 前缀；`apply` 会用读回确认这次变更。

当该文档缺失、不可读、超过上限或非法时，Runtime Mutation 会以 `operation_disabled` fail closed —— 所以
运维方移除 policy 的效果是「禁用了所有 Runtime Mutation」，而不是「放开了它们」。Tag 条目按路径段边界匹配：
`[default]AHU` 授权 `[default]AHU/Temp`，但绝不会授权 `[default]AHU2`。

## 启用 Mutation 类别

两个平面上每个 Mutation 类别默认都是关闭的。类别为：读取用 `NONE`，写入用 `CONFIG_MUTATION`、
`CONTROL_MUTATION` 和 `ADMIN_MUTATION`（D08，`contracts/shared/mutation-classes.json`）。没有任何类别能靠
层级关系或单独的 scope 被启用；每一层都要你自己打开。

Runtime 平面，来自 `setup-native` 与 Gateway：

1. `--profile` 决定 Server Config 对外宣告的 Tool 列表。`readonly` 不包含任何 Mutation。`operator` 增加
   CONTROL Tool：`tag_write`、`alarm_shelve` 和 `alarm_unshelve`。`configurator` 增加 CONFIG Tool：
   `tag_update`、`tag_create`、`tag_copy`、`tag_delete`、`tag_move` 和 `tag_rename`。`full` 同时加入这两组。
   Runtime 没有 ADMIN profile（D26）。
2. `--provision-security-levels` 为该 profile 创建专用的 Security Level，它是 `Authenticated` 的叶子子级，
   除非 `--security-level-name` 另有指定，名字为 `IgnitionMcpRuntime<Profile>`。已存在的 level 绝不会被修改，
   并且带有子 level 的 level 会被拒绝。
3. `--create-runtime-token` 创建恰好被授予该 level 的 Runtime API token，并把密钥一次性写入你所指定的
   `--runtime-token-file`。仅在明文 HTTP 的实验 Gateway 上，加 `--runtime-token-insecure-channel`。
4. Runtime Target Policy 的允许列表决定这些 Tool 可以触及哪些目标。已启用但没有允许列表条目的 Tool 依然
   会失败。

REST 平面，配置在 `ignition-rest-mcp` 的环境变量中：

```bash
export IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
export IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,project_import
export IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["com.inductiveautomation.historian/historian-provider"]}'
export IGNITION_MCP_CONTROL_MUTATION_ENABLED=true    # alarm_pipeline_cancel
export IGNITION_MCP_ADMIN_MUTATION_ENABLED=true      # no v1 Tool is ADMIN
export IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true   # project_export, tag_config_export
```

按照 D07，调用方凭证还需要匹配的 scope（`ignition.config`、`ignition.control`、`ignition.admin`），并且
只有经过验证的 principal 才能执行 Mutation：`auth=none` 的部署是只读的。敏感导出是与类别门禁相互独立的
另一个开关。

部署侧检查按此顺序运行，第一个拒绝决定错误码：类别是否启用、operation 允许列表、该 operation 自身的
Target 类别规则（即使在 `*` 下，被拒绝的资源类型也会被拒）、Target 允许列表、能力。类别被禁用的 Tool 既不
会出现在 `tools/list` 中，也会在调用时以 `operation_disabled` 被拒绝。

要安全地打开某一个写入，先只允许一个 operation id 和一个 Target，调用它，读回状态，然后才逐步放宽。D08
对写入从不自动重试。

## 阅读 `operation_diagnose` 输出

`operation_diagnose` 是 REST 平面的读取型 Tool。它只有一个参数 `correlationId`，必须是精确的 36 字符
UUIDv7 字符串。格式非法的标识符会得到 `invalid_argument`，并且查找从不模糊或部分匹配。

在哪里找到该标识符：

- 来自 `ignition-rest` 的每个 Tool 错误都带有包含 `code`、`message` 和 `correlationId` 的 JSON body；
- 该调用的结构化日志行带有同样的 `correlationId` 字段（`IGNITION_MCP_LOG_FORMAT=json`）；
- Mutation 的成功载荷会带有它，例如 `project_import` 会返回 `correlationId` 和 `transactionId`。

输出字段及其解读方式：

| 字段 | 含义 |
| --- | --- |
| `tool` | 该记录所属的 Tool |
| `outcome` | `in_progress`、`succeeded`、`failed`、`outcome_unknown`、`cancelled`、`interrupted`。仍为 `in_progress` 的记录没有 `finishedAt`：说明调用仍在进行，或进程在写入结果之前就退出了 |
| `errorCode` | D06 分类中的错误码，成功时为 `null` |
| `startedAt`、`finishedAt` | 该调用的时间窗口 |
| `phases` | 有序的 `name` 与 `at` 对，用来看该操作进行到了哪一步 |
| `phasesTruncated` | 为 `true` 表示该记录触及 32 条的上限，丢弃了最旧的阶段并保留最新的。这种丢弃会被报告，绝不静默发生 |
| `transactionId` | 该操作涉及的 D16 Project 事务 |
| `downstreamCorrelationId` | server 在 Gateway 侧用于关联的标识符（如果存在） |
| `auditResultMissing` | 为 `true` 表示该被审计操作缺少结果审计行，因此该次调用的审计轨迹不完整 |

追查一条较早的调用时，有三个限制需要注意：

- 记录按预算修剪，默认值是 `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS=72` 和
  `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS=10000`。对上周的某次调用得到 `not_found` 是预期结果；
- 记录按 principal 隔离。别的 principal 的标识符会得到 `not_found`，这是刻意设计，使该 Tool 不成为存在性
  探测（existence oracle）。只有持有 `ignition.admin` 的调用方能跨 principal 查看；
- 如果调用时记录存储不可用，调用本身仍然会执行，但不会留下任何记录。server 会为此写一行
  `operation_record_failure` 日志。

`outcome_unknown` 是停止信号，不是重试信号。先用一次读取确认目标状态，再作决定。D06 禁止对结果不明确的
Mutation 做自动重放。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | `doctor` 或 `verify` 完成且没有 `FAIL`；`plan` 完成且没有 `BLOCKED`；`apply` 完成了所有写入并通过验证；`install-module` 安装或升级了 Module、报告了 `NO CHANGE`，或完成了一次重启仍待进行的安装 |
| 1 | 某项检查失败、某次写入失败，或发生传输错误。`install-module` 也在以下情况返回 1：Gateway 运行的 build 比文件更新、healthy-module 清单无法读到最后一项、Gateway 拒绝上传/某项接受/安装，或带 `--restart` 后 Gateway 始终没有以已安装的 build 恢复 |
| 2 | 用法错误：flag 不合法、manifest 不可读或非法、产物哈希不匹配、凭证文件被拒绝。`install-module` 也在以下产问题（在任何请求发出之前就停止运行）时返回 2：SHA-256 不匹配、文件超过 `.modl` 上界、归档中没有可读的 `module.xml`、版本中没有 10 位 build、Module id 不是 `com.inductiveautomation.mcp` |
| 3 | 这次运行需要某个它没有收到的决定。`plan` 报告一个 `BLOCKED` 行，`apply` 什么都不写。`install-module` 什么都不安装；在证书或 EULA 的情形下，它此时已经上传了归档 |

被中断的运行退出码为 2。非预期的崩溃退出码为 1，且只打印异常类型，因此凭证不会通过 traceback 泄露。

## Windows

Windows 不会出错，但不是受支持的平台。[D31](../decisions/D31-windows-support-scope.md) 记录了范围、
已声明的限制，以及一份尚无人执行过的手动验证清单。

[前置条件](#前置条件) 中的工具链表与 [环境变量与凭证文件](#环境变量与凭证文件) 中的 PowerShell 区块
就是 Windows 的前置条件与启动路径，两者都带有同样的**尚未在 Windows 上运行**标记。

- **校验和。** Windows 没有内置的 `sha256sum -c`。在 `dist/release` 中运行
  `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` 或
  `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`，并把结果与
  `ignition-runtime-bundle-<version>.sha256` 中的哈希比对。
- **向导。** `scripts/deploy-runtime-bundle.sh` 是 bash 向导，需要 Git Bash 或 WSL。
  `ignition-mcp setup-native doctor|plan|apply` 命令是原生命令，不需要它。
- **凭证文件与数据目录。** 在 Windows 上不检查 token 文件的 `0600` 规则和数据目录的 `0700` 规则，改为记录一条
  WARNING。请用文件系统 ACL 保护每个 token 文件和 `IGNITION_MCP_DATA_DIR`，只让服务账户能读取。

## 已知 v1 限制

- 三个 Alarm Tool 被搁置，这是 Runtime 平面已知的 v1 缺口。`alarm_status` 和 `alarm_journal` 依据 D12
  Phase 2 有限执行修订案保留在 `packages/ignition-runtime-bundle/deferred/`。`alarm_acknowledge` 依据
  D12 Phase 4 修订案、按 ticket #9 的结论搁置，原因相同：精确路径的 `queryStatus` 没有原生上限或续传机制，
  因此无法为 acknowledge 的事前检查或 Observed state 设定上界。这三者都不可被发现、不可被调用，profile
  也不列出它们；重新启用任何一个都需要一个原生的事前执行上界，外加新的实机证据。
- 固定的官方 MCP Module 会发布 `structuredContent` 和 `isError`，但不发布 Tool `outputSchema`（D27）。
  `contracts/schemas/` 中本仓库自有的 schema 仍然是具备约束力的输出契约。
- Module 会丢弃对象值为 JSON null 的成员，因此 Runtime 输出使用 `ignition-null-v1` 编码（D28）：null 变为
  `{"$ignition":"null"}`，而携带 `$ignition` 的对象会被转义为
  `{"$ignition":"object","entries":[...]}`。这只适用于 Runtime 平面。
- 8.3.9 组合自 G3、G4、G5 起一直带有 `FAILED_NATIVE_BINDING`。8.3.8 组合依据 D27 以
  `VERIFIED_WITH_LIMITATION` 关闭。两者都不是 `SUPPORTED`，本 runbook 中没有任何命令会记录 `SUPPORTED`。
- Runtime Bundle 仍为 0.x。
- 本仓库只固定一个 MCP Module build，因此 Module 升级由 `install-module` 的拒绝逻辑和单元测试覆盖，而不是
  用真实的 build 间升级来覆盖。
- `doctor` 和 `verify` 不等待正在启动中的 Gateway。等待就绪属于实机 harness（`tests/harness/`）的职责。
