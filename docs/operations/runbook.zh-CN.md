# 运维手册

> English: [`runbook.md`](runbook.md)。本文是英文版的译本，两者不一致时以英文版为准。命令、参数、环境变量、退出码、状态字符串和文件路径保留英文原文。

本手册是你了解基本用法之后，运维 `ignition-mcp` 部署时查阅的参考。它详细说明 `setup` 和 `status` 的行为、升级、Runtime Target Policy、在两个 server 上打开写入，以及如何阅读 `operation_diagnose` 的输出。

第一次安装请按[快速开始](../guide/quick-start.zh-CN.md) 操作，那里按顺序讲解了每个命令。

| 我想…… | 章节 |
| --- | --- |
| 知道开始前要准备什么 | [前置条件](#前置条件) |
| 设置 token 文件和部署状态 | [部署状态与凭证文件](#部署状态与凭证文件) |
| 安装或升级 MCP Module | [setup](#setup) |
| 看懂 `status` 的某一行 | [用 `status` 诊断](#用-status-诊断) |
| 看懂 setup 显示的计划 | [阅读 setup 的计划](#阅读-setup-的计划) |
| 确切了解 `setup` 做了什么 | [setup 写入了什么](#setup-写入了什么) |
| 检查一个部署 | [验证部署](#验证部署) |
| 部署更新的 bundle | [升级 bundle](#升级-bundle) |
| 修改 Runtime 写入 Tool 可以碰的范围 | [Runtime Target Policy](#runtime-target-policy) |
| 打开写入 | [打开写入](#打开写入) |
| 查一次 REST 调用发生了什么 | [阅读 `operation_diagnose` 的输出](#阅读-operation_diagnose-的输出) |
| 看懂退出码 | [退出码](#退出码) |

示例中的命令在仓库文件夹里运行，因为 `setup` 从检出构建 Runtime bundle。`ignition-mcp` 是 `uv run --no-sync ignition-mcp` 的简写。

## 前置条件

| 项目 | 来源 |
| --- | --- |
| Gateway 地址 | 你的 Gateway，例如 `http://127.0.0.1:8088` |
| 有写入权限的 Gateway API token | Gateway 网页界面的 Security 部分。存进文件，见下一节 |
| MCP Module 文件及其 SHA-256 | Inductive Automation。本仓库固定版本 `1.3.5.2026021307-SNAPSHOT`、build `2026021307`、SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`，记录在 `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| bundle 发布产物：ZIP、manifest 和校验和 | 只有做发布时才需要，用 `tooling.native.cli release` 构建，见下文。`setup` 直接从仓库检出构建 bundle，你不用自己构建 |

`setup` 生成 Runtime Target Policy 和 Server Config 的权限树，你不用手写它们。它们的格式见[配置参考](../guide/configuration.zh-CN.md#runtime-target-policy)。

你自己的电脑上需要这些工具：

| 工具 | Linux 或 macOS | Windows |
| --- | --- | --- |
| Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/) | [官方安装脚本](https://docs.astral.sh/uv/)或包管理器。`uv` 会替你安装 Python | `winget install --id=astral-sh.uv -e`，或同一个官方安装脚本 |
| Shell | 任意 POSIX shell | PowerShell 7。Windows 检查清单用到 `-SkipHttpErrorCheck`，需要 7 版 |
| Git Bash 或 WSL | 不需要 | 只在运行 `bash` 脚本时需要：`tooling.ci.check_workflows` |
| Java 11 | 只有 Jython 测试需要。两个 server 都不需要 | 同左 |

本手册里的 Windows 指令都标注为**尚未在 Windows 上运行**。

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

**尚未在 Windows 上运行。** 先把 Git 版本存进一个变量，再以 `--source-revision $rev` 传入。用 `Get-FileHash` 或 `certutil` 代替 `sha256sum -c` 校验，把结果和 `.sha256` 文件里的值比对：

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

## 部署状态与凭证文件

每个命令都从一个部署文件夹读取状态。`--deployment NAME` 选一个部署，默认 `default`。每个部署的状态都在 `~/.config/ignition-mcp/deployments/<name>/`，里面有 `deployment.toml`（Gateway 地址、环境、角色）、生成的 policy 和 permissions 文档，以及每个机密一个文件。

先在 Gateway 网页界面的 Security 里新建一个 API key，它的安全级别要在 Security > General Settings 的每一项权限下都打勾。把它按 `名称:密钥` 存进一个文件：

```bash
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<name>:<your-ignition-api-key>' > ~/.config/ignition-mcp/gateway-token
chmod 0600 ~/.config/ignition-mcp/gateway-token
```

在 Windows 上，**尚未在 Windows 上运行**：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\ignition-mcp"
Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway-token" -Value "<name>:<your-ignition-api-key>"
```

Windows 不强制 `0600` 规则，所以要给 token 文件设置文件系统 ACL，只让服务账号能读取。

token 文件的规则：

- 必须是普通文件，不能是符号链接，里面恰好有一行非空内容，格式是 `名称:密钥`。
- 在 Linux 和 macOS 上只有所有者能读取，也就是权限 `0600`。
- 不符合以上规则是用法错误，退出码为 2。

地址的规则：

- 地址必须是带主机名的完整 `http` 或 `https` URL，不能包含用户名或密码。
- Gateway URL 是 `http` 时，Runtime token 需要一个具名确认。`prod` 环境要求安全通道，token 只能通过 `https` 使用。

第一次运行时还没有部署文件夹，`setup` 会从参数或向导得到值，然后把它们保存下来。之后每次命令都读取这个文件夹，而不是再问一遍。

## setup

`setup` 运行整个部署，可以重复运行。它先只读地算出要做什么，把每一处改动显示一行，取出确认，然后才写任何东西。`--dry-run` 只显示计划就停下。第一次运行时向导会问缺少的值，参数给全时它什么都不问。命令和参数见[快速开始](../guide/quick-start.zh-CN.md#setup)。

Module 安装是 `setup` 的一步。它只安装本地那个 SHA-256 对得上的文件，从不下载，也拒绝更低的 build。已经装好同一个 build，但 Gateway 没有把它列为 `ACTIVE`，或者根本没有给出状态时，它在任何写入之前就停下，因为 Gateway 不运行的 Module 不承载任何路由。证书、EULA 和需要时的重启各自需要一个具名确认。Module 文件默认从 `tests/fixtures/modules/` 或 `~/Downloads` 找，用 `--module-file` 可以指定。Module id 和 build 取自文件里的 `module.xml`。仓库里只有一个 Module build，所以升级路径由单元测试覆盖，没有做过真实的升级。

`setup` 从不期望任何一步失败。一步在该状态下无法成功时，它要么先把状态改对，要么跳过那一步并说明原因。

## 用 `status` 诊断

`status` 只读，每个检查一行。它不修复、不写入、不删除，也不等待正在启动的 Gateway，所以重启后要再运行一次。

```bash
ignition-mcp status \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

检查按顺序进行：`deployment`、`gateway`、`module`、`bundle`，接着每个角色一行 `level <role>`、`token <role>`、`server config <role>` 和 `endpoint <role>`，然后是 `runtime policy`、`rest token`、`rest static tokens`、`rest settings`、`named-query registry`，最后是 `leftover files`。

每行有一个状态：`OK`、`CHANGED`、`SKIPPED` 或 `FAILED`。读不到的状态记为 `SKIPPED`，第一个读失败的检查带出原因，依赖它的后续检查用同一个原因跳过，所以一个原因不会变成每个检查一行。`endpoint <role>` 行是带这个角色自己的 token 的收尾检查。

| 输出行 | 含义 | 怎么办 |
| --- | --- | --- |
| `gateway` FAILED，HTTP 401 | Gateway 拒绝了 setup token。 | 修正 token 文件，或给 token 读取权限。 |
| `module` FAILED | MCP Module 没装、没有运行，或 build 不对。 | 运行 `ignition-mcp setup` 安装它。 |
| `bundle` FAILED | 受管 bundle 项目和仓库检出不一致。 | 运行 `ignition-mcp setup` 重新部署。 |
| `level <role>`、`token <role>`、`server config <role>` FAILED | 有人手工改了 Gateway，或本地机密文件丢失。 | `setup` 会报告差异并在确认后恢复；机密丢失时加 `--recreate-tokens`。 |
| `endpoint <role>` FAILED，HTTP 403 | 没有发送 token、token 的安全级别不满足 Server Config 的权限，或 token 要求安全通道而 Gateway URL 是 `http`。 | 按那一行写出的原因处理，必要时重新运行 `setup`。 |
| `named-query registry` FAILED | Gateway 没有设置 `IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON`。 | 在 Gateway 上设置它，见 [Tool 目录](../guide/tools.zh-CN.md#named-query-注册表)。 |
| `leftover files` | 部署不再服务的角色在部署文件夹里留下了文件。 | 用 `reset` 删掉这个部署，或手工清理。 |

`status` 报告状态，不修复它。机密丢失和手工改动这两种情况，会连同修复它的命令一起报告出来。

## 阅读 setup 的计划

`setup` 先运行同样的只读检查，算出会发生什么，把计划显示出来，然后才写任何东西。

```bash
ignition-mcp setup \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --roles analysis,engineer \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --dry-run
```

计划里的每一行是一个阶段和它要做的改动。`runtime` 阶段负责 MCP Module、bundle 项目、安全级别、角色 token、Server Config 和 Runtime Target Policy；`rest` 阶段负责 `ignition-mcp-rest` token、角色的具名静态 token 和 `start` 会用到的设置。

没有改动的阶段什么都不列。计划有改动时，一行模式需要 `--yes` 才能继续，没有它就以 `not_confirmed` 失败，退出码 2。证书和 EULA 永远需要 `--accept-certificate` 和 `--accept-eula`。

bundle 项目的改动在计划里写明版本变化的类型：`patch`、`minor`、`major` 或 `downgrade`。`major` 和 `downgrade` 各自需要一个具名确认，`--yes` 可以代替它。有意回滚就是通过降级完成的。

一步在该状态下无法成功时，`setup` 要么先把状态改对，要么跳过并说明原因。没有哪一步是注定失败的。

## setup 写入了什么

`setup` 先显示计划，取出确认，然后按这个顺序写入：

1. MCP Module。需要时安装并重启 Gateway，每 5 秒检查一次，最多 10 分钟，直到新 build 运行起来。
2. 受管 bundle 项目 `ignition_runtime`。替换已有项目之前，先把旧项目备份进部署文件夹，替换时持有 Project writer 的写入锁，并先比对 `pcf1` 指纹。已部署的 bundle 版本与本仓库的版本相同时，受管项目的 Tool、Text Resource 或 Prompt 内容与 bundle 不一致才会报告为手工改动，恢复需要 `overwrite_hand_edit` 接受项；版本不同时属于升级，不是恢复手工改动。
3. 每个角色的安全级别，用一次最小的 singleton 修改完成，写完读回校验结构。
4. 每个角色的 Gateway API token，只获得上面那个安全级别，密钥以 `0600` 权限写入部署文件夹。
5. 每个角色的 Server Config。新建时先以关闭状态创建，读回后再打开；已有的 Server Config 只更新 Tool 列表和权限树。Tool 列表总是逐个列出，从不用 `*`。
6. Runtime Target Policy。写入后读回，读回内容不一致时再写一次。
7. `ignition-mcp-rest` Gateway API token，供 REST server 自己调用 Gateway。
8. 每个角色的具名静态 token。
9. `start` 会用到的设置，以及生成的 policy 和 permissions 文档。

需要知道的几点：

- 某一步失败时命令停止，之前的写入保留。`reset` 会删掉这次部署创建并被记录下来的东西，它不碰别人手工创建的同名项目。
- 命令从不显示 token。再运行一次时，它会用部署文件夹里的机密核对 Gateway 上的 token，对得上就报告 `OK`，不会生成新 token。
- 权限漂移算改动：Server Config 的 Tool 列表对得上但权限树不同时，报告为 `CHANGED`，从不报 `NO CHANGE`。
- Module 有时会短暂地让新 Server Config 不提供任何 Tool。这时 `setup` 会把同一份 Server Config 重新发送，最多三次。结果是同样的部署。

## 验证部署

`status` 是检查部署的命令。每个角色的 `endpoint <role>` 行就是收尾检查，它带这个角色自己的 token 调用端点，确认它能初始化并列出应有的 Tool。

- 所有行都是 `OK` 或 `SKIPPED` 时，部署是好的。
- 某个 `FAILED` 行给出原因和下一步可以运行的命令。
- 每次 Gateway 重启后，以及 `setup` 装好或升级 Module 之后，都要运行一次。

`status` 不等待正在启动的 Gateway，所以刚重启时可能读到旧状态，稍后再运行一次即可。

## 升级 bundle

升级 bundle 就是用本仓库更新版本的 Tool 替换 bundle 项目。

1. 获取更新版本的仓库。如果你在开发 bundle，就提高 `packages/ignition-runtime-bundle/BUNDLE_VERSION` 里的版本号。`bundle_info` 里的版本或归属标记与这个文件不一致时，构建会失败。
2. 运行 `status`，看 `bundle` 行。Gateway 上跑的还是旧版本时它报告不一致。
3. 运行 `setup --dry-run`，看计划里 bundle 项目那一行。它写明变化类型是 `patch`、`minor`、`major` 还是 `downgrade`。
4. 运行 `setup`。`major` 变化和降级各自需要一个具名确认，`--yes` 可以代替它。有意回滚就是通过降级完成的。
5. 运行 `status`，然后重新连接 AI 应用，因为 Tool 列表可能变了。

升级会替换整个 bundle 项目。你手动加进 `ignition_runtime` 的任何内容都会丢失，`setup` 替换前会把它备份进部署文件夹的 `backups/`。

## Runtime Target Policy

Runtime 写入 Tool 的 allowlist 来自 Gateway 上的一份文档，而不是 bundle。它以两个 Tag 的形式存在保留的 Tag provider `IgnitionMCPPolicy` 里：

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` 存放 JSON 文本。
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` 存放它的字节数。Tool 会先读大小，所以过大的文档不会被加载就被拒绝。

只有 `setup` 写入 policy。REST server 的 `config_resource_*` Tool 无论 allowlist 怎么写都拒绝 `IgnitionMCPPolicy` provider，所以 agent 改不了 policy。

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "auditProfile": "MCP_AUDIT",
  "allowlistsWildcardIncludeUdtTypes": true,
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
- Tag 条目覆盖该路径及其下的路径，以 `/` 为边界：`[default]AHU` 覆盖 `[default]AHU/Temp`，但不覆盖 `[default]AHU2`。`allowlistsWildcardIncludeUdtTypes` 决定 `*` 是否也覆盖 `_types_` 下的 UDT 定义。`setup` 在部署的任一角色使用 `full` 时默认设为 `true`，否则为 `false`。旧 policy 缺少此字段时按 `false` 处理。
- 报警条目是以 `prov:` 开头、不含 `*` 的带 provider 前缀的报警路径，覆盖该路径及其下的路径，以 `/` 或 `:` 为边界。
- `serviceIdentity` 是每次 Runtime 写入在 Ignition 审计日志里的执行者名字。agent 不能设置它。
- `auditMode` 为 `best_effort`、`required` 或 `off`。`required` 模式下，`auditProfile` 指定的审计 profile 不可用时拒绝写入。
- 每次调用的条目上限在 1 到 100 之间，不写时为 20。命令检查 `tagUpdateMaxItems`、`tagCreateMaxItems` 和 `tagCopyMaxItems`。schema 以同样方式限制 `tagDeleteMaxItems`、`tagMoveMaxItems`、`tagRenameMaxItems`、`tagWriteMaxWrites` 和 `alarmMaxPaths`。
- `alarmShelveMaxSeconds` 只能把搁置时长上限调低，不能超过 86400 秒。
- 存储的文本最多 32768 字节。

`setup` 以固定格式存储 policy：键排序、没有空格。这样它可以把生成的文档和已存储的副本逐字节比较。你不用写这个文件。

要修改 policy，就改部署环境或角色，再运行 `setup`。计划会显示 `*` 是否包括 UDT 定义；首次写入启用该设置的开发 policy 时，还会单独要求确认。例如 `dev` 的 allowlist 是每个 Runtime 修改 Tool 的 `*`，`prod` 的是空。`*` 仍需存在于具体 Tool 的 allowlist 中才会放行；此设置本身不会创建 allowlist。

policy 缺失、无法读取、过大或无效时，所有 Runtime 写入 Tool 都返回 `operation_disabled`。删除 policy 会关闭 Runtime 写入，而不会放开它们。

## 打开写入

两个 server 上的每一类写入一开始都是关闭的。共有三类：`CONFIG_MUTATION` 是配置修改，`CONTROL_MUTATION` 是控制操作，`ADMIN_MUTATION` 是管理操作。scope 和 profile 之间没有包含关系，每一层都要你自己打开。

在 Runtime server 上：

1. 助手角色决定 endpoint 用哪个 profile，也决定它提供哪些 Tool。Analysis 用 `readonly`，没有写入 Tool。Engineer 用 `full`，两组写入 Tool 都有。要换 profile 就换角色，再运行一次 `setup`。
2. Server Config 的权限树决定谁可以连接，`setup` 从角色生成它。
3. Runtime Target Policy 的 allowlist 决定每个 Tool 可以修改哪些目标。`dev` 默认对每个 Runtime 修改 Tool 都是 `*`，且 Engineer 的 `full` profile 默认让 `*` 包括 `_types_` 下的 UDT 定义；`prod` 默认 allowlist 为空。角色提供了某个 Tool，但没有 allowlist 条目时，它仍然什么都改不了。

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
| 0 | 成功。命令完成，`status` 没有 `FAILED` 行。 |
| 1 | 某一步失败，或网络失败。 |
| 2 | 用法错误：参数错误、缺少接受、token 文件被拒绝，或回答有问题。 |

`--json` 带着稳定错误码，完整列表见[快速开始](../guide/quick-start.zh-CN.md#退出码和错误码)。

按 Ctrl-C 以退出码 2 结束，错误码是 `interrupted`。意外崩溃以退出码 1 结束，只输出错误类型，所以 token 不会通过堆栈信息泄露。

## Windows

Windows 不是受支持的平台，不过目前没有已知的问题。范围、已知限制，以及一份还没有人执行过的手动检查清单都记在本手册里。

[前置条件](#前置条件)里的工具表和[部署状态与凭证文件](#部署状态与凭证文件)里的 PowerShell 代码块是 Windows 的起点，两处都带有**尚未在 Windows 上运行**的标注。

- **换行符。** 使用 Windows（CRLF）换行符的仓库副本可以直接使用：bundle 工具会把这些文件按 LF 读取，构建出的 ZIP 与 Linux 副本完全相同。
- **校验和。** Windows 没有 `sha256sum -c`。在 `dist/release` 里运行 `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` 或 `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`，把结果和 `ignition-runtime-bundle-<version>.sha256` 里的值比对。
- **向导。** 在 Git Bash 的 mintty 里没有伪控制台时，向导退回普通逐行提示，问题和校验不变。这样的提示关不掉终端回显，粘贴的机密会在输入时显示在屏幕上，问题里会说明这一点。
- **token 文件和数据文件夹。** Windows 跳过 `0600` 的 token 文件检查和 `0700` 的数据文件夹检查，只记录一条 WARNING。请用文件系统 ACL，只让服务账号能读取 token 文件和 `IGNITION_MCP_DATA_DIR`。

## 第 1 版的已知限制

- `alarm_status`、`alarm_journal` 和 `alarm_acknowledge` 被关闭了。Ignition 的报警查询函数没有行数上限，也不支持续取，所以这些 Tool 无法限制回答或预检查的规模。它们的代码在 `packages/ignition-runtime-bundle/deferred/`，没有任何 profile 列出它们。要重新打开其中任何一个，需要一个有上限的机制和新的实机测试证据。
- MCP Module 返回结构化结果，但不发布 Tool 输出 schema。以 `contracts/schemas/` 里的 schema 为准。
- MCP Module 会丢掉对象里的 `null` 值，所以 Runtime 的回答会对它们编码。见[运作原理](../guide/how-it-works.zh-CN.md#给客户端开发者的两个细节)。
- 在 8.3.9 上，Module 回应格式记为 `FAILED_NATIVE_BINDING`；在 8.3.8 上记为 `VERIFIED_WITH_LIMITATION`。两者都没有记为获得生产支持，本手册里的任何命令也不会这样记录。
- bundle 版本仍是 0.x。
- 仓库里只有一个 Module build，所以 Module 升级只由单元测试覆盖，没有做过真实升级。
- `status` 不等待正在启动的 Gateway。只有 `tests/harness/` 下的实机测试环境会等待。

## 来源

Module 安装、Module 升级和 Bundle 升级的定义在 `CONTEXT.md`。每个参数都与 `packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/gateway_ops/`、`cli/engine/` 和 `cli/setup/`，以及各命令的 `--help` 核对过；`status` 的输出格式取自源码。本手册里的控制台示例只展示输出的形式，不是真实运行记录。
