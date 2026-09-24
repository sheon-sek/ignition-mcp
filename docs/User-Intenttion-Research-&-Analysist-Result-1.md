# 结论

这个方向**可行，而且比这两个 Repo 现有架构更合理**。

我建议把新项目正式定义成两个彼此独立的 capability plane：

```text
                         AI Agent / MCP Client
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
       ignition-rest MCP Server       ignition-runtime MCP Server
             FastMCP 4.x                Ignition 官方 MCP Module
          Streamable HTTP                 Streamable HTTP
                    │                           │
                    ▼                           ▼
        Ignition Native REST              Jython 2.7 Tool
           /data/api/v1/*                   system.*
                    │                           │
                    └──────────┬────────────────┘
                               ▼
                         Ignition Gateway
```

**FastMCP 不再承担 WebDev bridge。Ignition MCP Module 不再承担本来已经有官方 REST 的配置管理功能。**

这是我认为最干净的最终边界。

---

# 1. 两个现有 Repo 的本质差异

| 项目            | `jsgorana/ignition-mcp`      | `WhiskeyHouse/ignition-mcp`                   | 我们应该吸收什么              |
| ------------- | ---------------------------- | --------------------------------------------- | --------------------- |
| 定位            | 功能完整度优先                      | REST API wrapper / Developer API 优先           | 两者结合                  |
| 当前 Tool 数量    | 43                           | 37                                            | 不追求数量，重新整理 capability |
| Python MCP 实现 | `mcp.server.fastmcp.FastMCP` | 独立 `fastmcp` package                          | 新项目直接 FastMCP 4       |
| HTTP          | 当前主要偏 stdio                  | Streamable HTTP 默认                            | Whiskey 方向更适合         |
| HTTP Client   | sync `httpx.Client`          | async `httpx.AsyncClient` + lifespan          | **Whiskey 架构更好**      |
| REST          | 有                            | 有                                             | 统一重写                  |
| WebDev        | 一个 HMAC `mcp-bridge`         | 多个 WebDev endpoints                           | **全部删除**              |
| Tag runtime   | WebDev                       | WebDev                                        | Ignition MCP Tool     |
| Alarm         | WebDev                       | WebDev                                        | Ignition MCP Tool     |
| Historian     | WebDev                       | WebDev                                        | Ignition MCP Tool     |
| DB            | WebDev                       | 较少                                            | Ignition MCP Tool     |
| Perspective   | 很强，直接操作项目文件                  | 宣称有 Project Resource REST                     | 需要重新设计                |
| 写入保护          | 较完整：全局开关、confirm、allowlist   | 相对弱                                           | 借鉴 jsgorana，但升级       |
| Error model   | typed error + remediation    | 多数 `except Exception -> {"error":...}`        | 借鉴 jsgorana           |
| Diagnose      | 有                            | 较少                                            | 保留并加强                 |
| License       | MIT                          | **README 写 MIT，但 GitHub LICENSE 识别为 GPL-3.0** | ⚠️ 需要先解决              |

jsgorana 自己明确把架构描述为 REST plane + WebDev bridge plane；WhiskeyHouse 也明确指出 runtime tag、alarm、historian 等依赖 WebDev。([GitHub][1])

所以你的基本判断是正确的：

> 这些 WebDev 并不是作者“喜欢 WebDev”，而是因为 Native REST 本身覆盖不到 Gateway scripting runtime。

现在官方 MCP Module 给了我们一个更正确的 Gateway 内部入口，所以 **WebDev bridge 这一层已经可以被彻底拿掉**。

---

# 2. jsgorana 值得保留的部分很多

我认为 `jsgorana` 的价值主要不在 WebDev bridge，而在它外围的工程设计。

它做得比较好的地方是：

| 设计                          | 评价                               |
| --------------------------- | -------------------------------- |
| `GatewayClient` 集中处理 HTTP   | 应保留                              |
| typed domain errors         | 应保留                              |
| 401 / 403 / 404 / 5xx 分类    | 应保留                              |
| remediation 提示              | 很适合 Agent                        |
| GET transient retry         | 合理                               |
| POST/PUT 不自动 retry          | 正确                               |
| `IGNITION_ALLOW_WRITES`     | 思路正确                             |
| tag write allowlist         | 很重要                              |
| destructive name echo       | 可保留做 UX guard                    |
| `ignition_diagnose`         | 应升级成核心 Tool                      |
| Perspective schema/template | 非常有价值                            |
| Tool 输入限制                   | 值得吸收                             |
| bridge capability detection | 应替换成“双 MCP capability detection” |

它最大的问题就是 WebDev bridge。

WebDev bridge 要自己维护：

```text
FastMCP
  ↓
HTTP
  ↓
HMAC envelope
  ↓
WebDev
  ↓
Jython routing
  ↓
system.*
```

而新架构直接：

```text
AI Client
  ↓ MCP
Ignition MCP Module
  ↓
onToolCalled()
  ↓
system.*
```

少了一整层协议、HMAC、WebDev project、shared secret、route registration 和脚本 reload。

而且这不是理论上的复杂度：jsgorana 的 bridge 文档已经记录过 project import 后 Jython script 没有正常 hot reload，需要直接操作 filesystem 或 Designer save 才能恢复。([GitHub][2])

所以 **WebDev → 官方 MCP Module 是实质性的可靠性提升，不只是架构更漂亮。**

---

# 3. WhiskeyHouse 值得吸收的是它的 FastMCP 外壳

WhiskeyHouse 当前的外部 MCP Server 结构，反而比较接近我们应该采用的 FastMCP package：

```text
FastMCP
    │
    ├── lifespan
    │     └── one shared Async IgnitionClient
    │
    ├── gateway tools
    ├── project tools
    ├── tag provider tools
    └── ...
```

尤其是：

```python
httpx.AsyncClient(...)
```

由 FastMCP lifespan 创建一次，所有 request 共用 connection pool。

这个模式比 jsgorana 的同步 `httpx.Client` 更适合一个真正的 Streamable HTTP MCP Server。

但是 WhiskeyHouse 有一个需要非常注意的问题：

## 它不能作为 Native REST endpoint 的事实来源

它定义了：

```text
GET /data/api/v1/projects/{project}/resources
GET /data/api/v1/projects/{project}/resources/{resourcePath}
PUT ...
DELETE ...
```

并把这四个列为 Native REST。

但目前已有实际 8.3 Gateway 的验证显示这些 project-resource routes 并不存在；另一个针对真实 Gateway OpenAPI 做验证的实现也明确记录 `/projects/{p}/resources/**` 不存在。([Docs.rs][3])

而 Ignition 官方文档明确说：

> `/openapi.json` 是**根据当前 Gateway + 已安装 Modules 动态生成**的公开 API 事实来源；只有当前真正公开的 routes 才会出现在里面。([Ignition User Manual][4])

所以我们不能这样开发：

```python
# ❌ Repo A 有
"/data/api/v1/foo"

# 就认为所有 Ignition 8.3 都有
```

应该变成：

```text
Gateway startup
       │
       ▼
GET /openapi.json
       │
       ▼
Capability Registry
       │
       ├── projects.export = yes
       ├── projects.resources = no
       ├── tags.export = yes
       └── ...
```

这会成为新 FastMCP 最重要的基础设施之一。

---

# 4. 不应该用 “Ignition 8.3+” 作为唯一兼容条件

8.3 本身的 REST API 在 patch release 中还在增加。

例如 tag JSON export/import 是 **8.3.2 才加入**的，8.3.0 / 8.3.1 并没有。IA 官方人员也确认了这一点。([Inductive Automation Forum][5])

所以：

```text
if version >= 8.3:
    assume_endpoint_exists()
```

是不够的。

应该使用：

```text
Version
+
Installed Modules
+
/openapi.json capability detection
```

例如：

```json
{
  "gatewayVersion": "8.3.x",
  "capabilities": {
    "projects.list": true,
    "projects.export": true,
    "projects.import": true,
    "projectResources.directCrud": false,
    "tags.export": true,
    "tags.import": true,
    "entity.browse": true
  }
}
```

甚至 OpenAPI spec 可以 hash：

```text
openapiSha256
```

方便 diagnose、bug report、CI fixture。

---

# 5. 不建议直接把 `/openapi.json` 自动变成几百个 MCP Tools

FastMCP 本身已经有成熟的 OpenAPI parser，可以从 OpenAPI 自动产生 MCP components。([FastMCP][6])

但我**不建议把整个 Ignition OpenAPI 自动公开给 Agent**。

原因是 Ignition Gateway API 很大，而且里面有非常危险的东西，包括 restart、scan、diagnostics、license/config operations。

官方甚至专门警告：

`scan/projects` 在生产环境通常不应该调用，因为可能影响 Designer 保存项目；thread dump、config scan lock、restart 等 endpoint 也都有明显风险。([Ignition User Manual][7])

所以最佳模式是：

```text
/openapi.json
     │
     ├── capability detection
     ├── endpoint validation
     ├── request/response schema reference
     └── integration-test source
              │
              ▼
       Curated MCP Tools
```

而不是：

```text
/openapi.json
     ↓
500 MCP tools
     ↓
LLM 自己决定调用什么
```

后者会造成 context pollution，也会让攻击面大很多。

---

# 6. 我建议的最终 capability ownership

这是整个项目最重要的边界。

| Capability                                 |    FastMCP REST Server |           Ignition MCP Module |
| ------------------------------------------ | ---------------------: | ----------------------------: |
| Gateway info/version                       |                      ✅ |                             — |
| Module health                              |                      ✅ |                             — |
| Gateway logs                               |                      ✅ |                             — |
| DB connection status                       |                      ✅ |                             — |
| OPC connection status                      |                      ✅ |                             — |
| system metrics                             |                      ✅ |                             — |
| Project list/get/create/copy/rename/delete |                      ✅ |                             — |
| Project export/import                      |                      ✅ |                             — |
| Gateway config resources                   |                      ✅ |                             — |
| Tag Provider config                        |                      ✅ |                             — |
| Bulk Tag export/import                     | ✅ when endpoint exists |                             — |
| Generic `/entity/browse`                   |                      ✅ |                             — |
| **Live Tag browse**                        |                      — |         ✅ `system.tag.browse` |
| **Live Tag read**                          |                      — |   ✅ `system.tag.readBlocking` |
| **Live Tag write**                         |                      — |  ✅ `system.tag.writeBlocking` |
| Tag granular getConfiguration              |                      — |                             ✅ |
| Tag granular configure/delete/move/copy    |                      — |                             ✅ |
| Alarm status                               |                      — |  ✅ `system.alarm.queryStatus` |
| Alarm journal                              |                      — | ✅ `system.alarm.queryJournal` |
| Alarm acknowledge/shelve                   |                      — |                             ✅ |
| Historian browse                           |                      — |                             ✅ |
| Historian raw query                        |                      — |                             ✅ |
| Historian aggregate query                  |                      — |                             ✅ |
| DB runtime query                           |                      — |                        ✅，但强限制 |
| Audit query/write                          |                      — |                             ✅ |
| Perspective sessions                       |   ✅ REST where exposed |                             — |
| Perspective View file CRUD                 |               **特殊处理** |       **不直接 filesystem CRUD** |
| arbitrary Jython execution                 |                      ❌ |                             ❌ |
| Gateway OS command execution               |                      ❌ |                             ❌ |

Ignition 官方 scripting API 本身已经完整覆盖 live tag read/write、tag configuration、alarms 和 historian。例如 `system.tag.readBlocking` 返回 QualifiedValue，`writeBlocking` 返回每个 Tag 对应的 QualityCode；Historian 有专门的 raw/aggregate APIs。([Ignition User Manual][8])

这正是官方 MCP Module 最应该承担的部分。

---

# 7. Tag Tool 会比两个原 Repo 都更完整

根据已经载入的 `ignition-mcp-tools-skill`，我们目前确认内建 MCP Tool 可以直接覆盖：

```text
system.tag.browse
system.tag.query
system.tag.readBlocking
system.tag.readAsync
system.tag.writeBlocking
system.tag.writeAsync

system.tag.getConfiguration
system.tag.configure
system.tag.deleteTags
system.tag.copy
system.tag.move
system.tag.rename

system.tag.exists
system.tag.exportTags
system.tag.importTags
```

也就是说 WhiskeyHouse WebDev 中的：

```text
read_tags
write_tag
get_tag_config
create_tags
edit_tags
delete_tags
list_udt_types
get_udt_definition
```

基本都可以重新设计成真正的一等 MCP Tools。

而且比 WebDev 好的一点是，我们可以完整保留：

```json
{
  "value": 10.5,
  "quality": {
    "good": true,
    "name": "Good"
  },
  "timestamp": "..."
}
```

不能像一般 REST wrapper 那样只返回：

```json
{"value": 10.5}
```

因为对于 SCADA，`10.5 Bad_Stale` 和 `10.5 Good` 完全不是同一件事情。

这也是已经加载的 Skill 强制要求的设计。

---

# 8. Historian 也不应该继续复制旧的 `queryTagHistory`

这里我建议比两个 Repo 再进一步。

Ignition 8.3 当前已经有新的：

```text
system.historian.browse
system.historian.queryRawPoints
system.historian.queryAggregatedPoints
system.historian.queryMetadata
system.historian.queryAnnotations
...
```

而不只是旧式：

```text
system.tag.queryTagHistory
```

所以新内建 Tool 可以直接设计成：

```text
historian_browse
historian_query_raw
historian_query_aggregate
historian_query_metadata
```

Agent 会明显更容易理解。

而且参数语义更清楚：

```text
query_raw
    paths
    startTime
    endTime
    returnFormat
    returnSize
```

和：

```text
query_aggregate
    paths
    startTime
    endTime
    aggregates
    returnFormat
    returnSize
```

分开。

不要做一个 20 个 optional 参数的大万能 historian tool。

---

# 9. Perspective 是目前最需要重新设计的地方

这里我不建议直接照抄任何一个 Repo。

### jsgorana 的办法

它实际上是在 Gateway 上：

```text
data/projects/<project>/
    com.inductiveautomation.perspective/
        views/...
```

直接 filesystem write：

```text
files/write
```

然后：

```text
POST /data/api/v1/scan/projects
```

能工作，但属于绕过正常 resource lifecycle 的方式。

Ignition 官方现在明确警告，手动 filesystem modification + project scan 不应成为生产环境正常工作流，`scan/projects` 也可能干扰 Designer save。([Ignition User Manual][7])

所以：

> **不要把 jsgorana 的 files/read/write WebDev endpoint 直接翻译成 `system.file.readFileAsString/writeFile` MCP tools。**

即使技术上能做，也是在把旧问题搬到新架构。

### WhiskeyHouse 的办法

它假设：

```text
/projects/{project}/resources/*
```

存在。

现在看来这个假设不可靠。

### 我建议的办法

采用：

```text
Project Export
      ↓
ZIP Resource Adapter
      ↓
modify exact Perspective resources
      ↓
offline validate
      ↓
Project Import
```

也就是：

```text
Native REST
GET projects/export/{project}
        ↓
temporary workspace
        ↓
Perspective Resource Editor
        ↓
POST projects/import/{project}?overwrite=true
```

第三方对真实 8.3 Gateway 做过类似验证后，也转向了 **project-export ZIP surgery**，原因正是 project-resource REST route 不存在。([Docs.rs][3])

这样可以完全避免 WebDev 和 Gateway filesystem scan。

---

# 10. 但 ZIP surgery 一定要加 transaction guard

这部分不能像普通 ZIP editor 那么简单。

例如：

```text
T0: Agent export project
T1: Engineer 在 Designer 修改 project
T2: Agent 修改旧 ZIP
T3: Agent import overwrite
```

结果 T1 的修改直接被覆盖。

所以建议我们的 Perspective Adapter 做：

```text
export A
  ↓
record project metadata/hash
  ↓
modify temp copy
  ↓
validate
  ↓
re-check current project
  ↓
changed?
   ├── YES → abort: concurrent modification
   └── NO  → import
              ↓
           re-export / verify
```

而且 import 前自动保存：

```text
backup/
    ProjectName-20260918Txxxx.zip
```

这是一个必须从一开始就做对的问题。

---

# 11. Project export 也不应该像 WhiskeyHouse 那样返回 Base64 ZIP 给 LLM

WhiskeyHouse 的：

```python
content_b64 = base64.b64encode(resp.content)
```

对于大项目非常浪费。

例如一个 50 MB project：

```text
50 MB ZIP
→ ~67 MB base64
→ MCP JSON
→ model context / client memory
```

完全没有必要。

新 Server 应该让 archive 留在 Server 端：

```text
project_export
→ artifact ID / metadata
```

或者 Perspective tool 自己内部：

```text
export → modify → import
```

不要让 binary archive 经过模型。

FastMCP 本身可以通过 Resource / HTTP custom route 提供文件类数据，因此如果未来用户真的需要下载 export，可以做成 artifact/resource，而不是 tool JSON 内塞 Base64。([FastMCP][9])

---

# 12. 两个 Server 应该采用同一套 response contract

虽然底层完全不同，我建议 Agent 看到的 Tool 输出保持一致。

例如：

```json
{
  "ok": true,
  "result": {},
  "meta": {
    "tool": "tag_read",
    "backend": "ignition-native",
    "nativeFunctions": [
      "system.tag.readBlocking"
    ],
    "correlationId": "..."
  }
}
```

REST Server：

```json
{
  "ok": true,
  "result": {},
  "meta": {
    "tool": "project_list",
    "backend": "ignition-rest",
    "endpoint": "GET /data/api/v1/projects/list",
    "correlationId": "..."
  }
}
```

Error：

```json
{
  "ok": false,
  "error": {
    "code": "permission_denied",
    "message": "...",
    "remediation": "..."
  },
  "meta": {
    "tool": "...",
    "correlationId": "..."
  }
}
```

这样对于 Agent 而言实际上还是一个统一的 Ignition Tool ecosystem。

---

# 13. Tool naming 要刻意避免两个 Server 互相撞名

不要两个 Server 都出现：

```text
browse_tags
```

一个指：

```text
GET /entity/browse
```

一个指：

```text
system.tag.browse
```

LLM 会很难判断。

我倾向于：

```text
Ignition REST Server
--------------------
gateway_info
gateway_logs
project_list
project_export
project_import
config_resource_list
tag_config_export
tag_config_import
entity_browse

Ignition Runtime Server
-----------------------
tag_browse
tag_read
tag_write
tag_get_config
tag_configure
tag_delete
alarm_status
alarm_journal
alarm_acknowledge
historian_browse
historian_query_raw
historian_query_aggregate
```

这样 semantic ownership 很清楚。

客户端自己的 server namespace 最终可能又形成：

```text
ignition_rest_project_list
ignition_runtime_tag_read
```

FastMCP 4 的 `ClientGroup` 也正是使用 server-based namespace 解决多个 MCP Server tool collision。([GoFastMCP][10])

---

# 14. FastMCP Server 我建议直接以 FastMCP 4 为基线

截至 **2026 年 9 月 18 日**，FastMCP 4 已经 GA，支持 MCP `2026-07-28`，并能兼容较旧 MCP clients。新的协议把 request 变成独立请求，使 HTTP 横向扩展比过去简单很多。([GoFastMCP][10])

所以不要：

```toml
fastmcp>=2.12.3
```

也不要照 WhiskeyHouse 当前代码原样升级。

直接按 FastMCP 4 API 设计。

核心结构可以是：

```text
FastMCP 4
│
├─ middleware/
│   ├─ auth
│   ├─ authorization
│   ├─ correlation
│   ├─ audit
│   └─ error_mapping
│
├─ services/
│   ├─ gateway_client
│   ├─ openapi_capabilities
│   ├─ project_archive
│   └─ perspective_resources
│
└─ tools/
    ├─ gateway
    ├─ projects
    ├─ config
    ├─ tags
    └─ perspective
```

底层：

```python
httpx.AsyncClient
```

lifespan 共用。

并配置：

```text
connection pool
keep-alive
per-operation timeout
TLS verify
custom CA
redirect disabled
```

特别是 **redirect 应关闭**，避免 Ignition API Token 在意外 redirect 中泄露给另一个 host。

---

# 15. Streamable HTTP 本身也必须有独立认证

不要因为：

```text
FastMCP → Ignition
```

已经有：

```text
X-Ignition-API-Token
```

就认为：

```text
AI Client → FastMCP
```

也安全了。

这是两个完全不同的 trust boundary：

```text
Agent
  │
  │ JWT/OAuth/API credential
  ▼
FastMCP
  │
  │ X-Ignition-API-Token
  ▼
Ignition
```

FastMCP 已经支持 JWT verification、remote OAuth/OIDC、OAuth proxy、MultiAuth 等，而且 Authorization Middleware 可以在 `tools/list` 时直接隐藏调用者没权限的 tools。([FastMCP][11])

这一点很适合我们：

```text
scope: ignition.read
    → read tools

scope: ignition.config
    → config writes

scope: ignition.control
    → live tag writes

scope: ignition.admin
    → destructive operations
```

比：

```text
IGNITION_ALLOW_WRITES=true
```

高级很多。

---

# 16. `confirm=true` 不是安全控制

jsgorana 这个设计适合作为防误操作 UX：

```python
confirm=True
```

但是它**不能作为 security boundary**。

因为调用者是 AI：

```text
AI 可以自己传 confirm=true
```

所以真正的层次应该是：

```text
Authentication
      ↓
Authorization / scope
      ↓
server-side allowlist
      ↓
tool enabled?
      ↓
confirm / name echo
      ↓
native operation
      ↓
verification
```

`confirm` 只是最后一道减少误调用的 guard。

不是 permission。

FastMCP 4 现在也有 interactive / approval 类能力，但不同 MCP Client 的支持程度不一样，所以不能把基础安全性依赖在 HITL UI 上。([GoFastMCP][10])

---

# 17. Ignition 内建 MCP Server 更需要权限分层

官方 MCP Module 自己支持基于 Ignition Security Levels 配置 server permissions。

但目前还是 **Early Access**。

IA 明确说明 EA：

* 官方 module；
* 使用 Streamable HTTP；
* 实现 MCP 2025-06-18；
* Designer 可以创建 Tools / Resources / Prompts；
* 当前不提供任何 built-in primitives；
* EA 阶段**不承诺 backwards compatibility**。([Inductive Automation Forum][12])

因此我们的 native tools package **必须版本化**：

```text
Ignition version
MCP Module version
Tool Bundle version
```

例如 manifest：

```json
{
  "bundleVersion": "0.1.0",
  "ignition": {
    "min": "8.3.8"
  },
  "mcpModule": {
    "tested": ["1.3.5-SNAPSHOT"]
  }
}
```

不能现在做出 ZIP，然后假定未来 MCP Module 1.0 永远兼容。

---

# 18. MCP Module 目前还有一些 EA-specific 坑

当前 EA 论坛已经出现几个很值得我们提前 CI 化的问题。

例如 project 必须是：

```text
standalone
inheritable = false
```

否则 MCP initialize 可能不会 advertise tools，`tools/list` 会失败。IA 官方人员已经明确确认这一点。([Inductive Automation Forum][12])

另外实际用户还遇到：

```text
403
405
security-level configuration
server config loading
```

等 EA 问题，直到 2026 年 9 月仍有人报告 MCP endpoint 的 405/403 故障。([Inductive Automation Forum][13])

因此 native bundle 测试不能只做：

```text
ZIP structure valid
```

必须做真正的：

```text
Import project
    ↓
save
    ↓
MCP initialize
    ↓
tools/list
    ↓
call every smoke tool
```

这跟我们刚刚载入的 skill 要求是一致的。

---

# 19. 官方 MCP Server 的配置也应该自动化

这是我认为可以比你原先方案再好一点的地方。

用户当然可以：

```text
Import ZIP to Designer
```

但整个 setup 还包括：

```text
MCP Module installed?
MCP tool project imported?
project inheritable=false?
server-config exists?
correct project selected?
correct Security Level?
API key?
tools/list works?
```

所以新 FastMCP package 可以额外提供一个 CLI：

```bash
ignition-mcp setup
```

它不调用 WebDev，只用 Native REST：

```text
check Gateway
check MCP module
import native tools project.zip
create/update MCP server-config resource
verify project
probe MCP initialize
probe tools/list
```

官方 EA getting-started 本身也提供了通过 `/data/api/v1/resources/com.inductiveautomation.mcp/server-config` 创建 Server Config 的示例，而不一定需要用户手改 filesystem。([Inductive Automation Forum][14])

最终安装体验可以变成：

```bash
uvx ignition-mcp setup \
  --gateway https://ignition.example.com \
  --server-name ignition-runtime
```

然后：

```text
✓ Gateway 8.3.x
✓ MCP Module detected
✓ Native tool project imported
✓ Server config created
✓ 18 tools discovered
✓ Runtime MCP endpoint ready
```

这个 UX 会明显好很多。

---

# 20. 不要移植 `run_gateway_script`

WhiskeyHouse 有：

```text
run_gateway_script(script)
```

虽然默认关闭，但我认为我们的正式 bundle 应该**直接不提供**。

因为一旦存在：

```python
exec(user_controlled_script)
```

那么：

```text
tag allowlist
DB restrictions
filesystem restrictions
typed MCP tools
```

基本全部失去意义。

而 Ignition `system.util` 本身甚至有 OS command execution 能力。官方 scripting surface 很强，所以绝对不能把 scripting surface 整体暴露给 LLM。([Ignition User Manual][15])

应该是：

```text
LLM
 ↓
typed tool
 ↓
validated arguments
 ↓
one known system.* function
```

绝不是：

```text
LLM
 ↓
"write any Jython"
 ↓
Gateway
```

---

# 21. DB Tool 也需要特别保守

jsgorana bridge 有：

```text
db/named-query
db/query
```

新版本可以做 native MCP Tool，但最好把能力拆成：

```text
db_connections
db_query_readonly
```

甚至第一版只支持：

```text
SELECT
```

以及 connection allowlist：

```text
IGNITION_MCP_DB_ALLOWLIST=ReportingDB,HistorianReadReplica
```

不建议默认提供：

```text
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

数据库写操作以后单独设计。

---

# 22. 内建 Tool 本身也必须做 request budgets

Ignition Tool 是 Gateway 内执行。

例如：

```python
system.tag.readBlocking(paths)
```

如果 Agent 一次送：

```text
30,000 tags
```

或者 historian 请求：

```text
1 year × 500 tags × raw
```

你可以直接给 Gateway 制造很大的负载。

所以 internal tool 必须自己限制：

```text
tag_read
    max 100 / 200 paths

tag_write
    max 20 / 50 writes

alarm_status
    max 500

historian
    max paths
    max duration
    max returnSize
```

不能完全相信 MCP client。

而且 Historian 应优先支持 continuation/page 或 aggregation，而不是一次返回几十 MB JSON。

---

# 23. 两端审计应该统一

Native REST mutation 有一个优势：Ignition 官方会把 POST / PUT / DELETE 记录到 audit log，包括 user、IP、API key。([Ignition User Manual][4])

内建 MCP Tool 调用 `system.*` 则不能假设自然会得到完全相同的 audit trail。

所以 mutating native tools 应主动：

```python
system.util.audit(...)
```

记录：

```text
tool
target
operation
correlationId
result
actor if available
```

Ignition 8.3 本身提供 `system.util.audit`。([Ignition User Manual][15])

但是**不要假设 Jython Tool 能自动拿到调用 MCP 的真实用户 identity**；这一点要等我们研究 MCP Module builder/context API 后再决定如何关联 actor。

如果拿不到，就明确写：

```text
actor = MCP service identity
```

不能伪造成人类用户。

---

# 24. License 是现在必须先处理的问题

这里有一个明显异常：

jsgorana：

```text
MIT
```

没有问题。([GitHub][16])

WhiskeyHouse 当前 GitHub 页面同时显示：

```text
README:
License
MIT
```

但 GitHub repository metadata 又识别：

```text
GPL-3.0 license
```

而我读取到仓库实际 `LICENSE` 文件内容也是 **GNU GPL v3**。([GitHub][1])

所以在真正开始“拿两个源码结合”前必须决定新项目 License。

如果新 Repo 接受：

```text
GPL-3.0
```

那么可以在满足相应许可证义务下直接整合。

如果你希望：

```text
MIT
Apache-2.0
commercial/proprietary-friendly
```

那我建议：

> **不要复制 WhiskeyHouse implementation code。**

可以研究：

```text
tool behavior
endpoint list
architecture
bugs
tests
```

然后 clean-room 重新实现。

REST endpoint 本身和架构思想不等于它的具体实现代码。

这个问题最好现在处理，不要代码写到一半再发现 license 污染。

---

# 25. 我建议的新 Repo Layout

```text
ignition-mcp/
│
├── pyproject.toml                  # uv workspace
│
├── packages/
│   │
│   ├── ignition-rest-mcp/
│   │   ├── pyproject.toml
│   │   └── src/ignition_rest_mcp/
│   │       ├── server.py
│   │       ├── config.py
│   │       ├── client.py
│   │       ├── capabilities.py
│   │       ├── errors.py
│   │       ├── middleware/
│   │       ├── services/
│   │       │   ├── openapi.py
│   │       │   ├── project_archive.py
│   │       │   └── perspective.py
│   │       └── tools/
│   │           ├── diagnostics.py
│   │           ├── gateway.py
│   │           ├── projects.py
│   │           ├── config.py
│   │           ├── tags.py
│   │           └── perspective.py
│   │
│   └── ignition-native-tools/
│       ├── project/
│       │   ├── project.json
│       │   └── com.inductiveautomation.mcp/
│       │       └── tools/
│       │           ├── tag-read/
│       │           ├── tag-write/
│       │           ├── ...
│       │           └── historian-query-aggregate/
│       ├── server-config/
│       │   ├── readonly.example.json
│       │   └── operator.example.json
│       └── dist/
│           └── ignition-native-tools.zip
│
├── contracts/
│   ├── errors.json
│   ├── response-envelope.json
│   └── tool-semantics/
│
├── tests/
│   ├── contract/
│   ├── rest-live/
│   ├── native-static/
│   └── native-live/
│
└── docs/
```

这里 `contracts/` 不需要变成第三个 package。

它只是两个 package 共享的行为定义。

---

# 26. `ignition-native-tools` 必须严格按照已经载入的 Skill

这个部分我们现在已有很清楚的标准。

每个 tool：

```text
com.inductiveautomation.mcp/
└── tools/
    └── tag-read/
        ├── resource.json
        └── onToolCalled.py
```

`resource.json`：

```json
{
  "attributes": {
    "parameters": [
      {
        "name": "paths",
        "description": "...",
        "type": "array",
        "required": true
      }
    ]
  }
}
```

不会错误地塞：

```json
{
  "type": "array",
  "items": {},
  "enum": [],
  "default": []
}
```

Jython：

```python
def onToolCalled(builder, paths):
	...
```

并且：

```text
resource parameter names
=
order
=
handler signature
```

完全一致。

这一套可以直接用于实现 WhiskeyHouse/jsgorana WebDev capabilities 的正式替代。

---

# 27. 一个值得做的统一 Tool contract 层

虽然 external Python MCP 能用 Pydantic：

```python
Literal["Low", "Medium", "High"]
```

而 Ignition Tool resource 不支持 JSON Schema enum，

我们仍然可以在：

```text
contracts/tool-semantics/
```

维护：

```yaml
alarm-status:
  states:
    - ActiveUnacked
    - ActiveAcked
    - ClearUnacked
    - ClearAcked
```

然后：

FastMCP：

```text
→ Pydantic schema
```

Ignition：

```text
→ parameter description
→ Jython explicit validation
```

这样两个世界不会逐渐出现：

```text
Python 支持 A/B/C
Jython 支持 A/B
README 写 A/B/C/D
```

不过我不会一开始做复杂 generator framework。

先把它作为 contract fixture + tests，够用了。

---

# 28. Capability / Diagnostics 应成为一等功能

我建议两个 server 都有一个极轻量 diagnostics tool：

```text
ignition_rest_diagnose
```

返回：

```json
{
  "gateway": {
    "reachable": true,
    "version": "8.3.x"
  },
  "auth": {
    "ok": true
  },
  "openapi": {
    "loaded": true,
    "hash": "..."
  },
  "capabilities": {
    "projects": true,
    "tagsExport": true
  }
}
```

官方 server：

```text
runtime_info
```

返回：

```json
{
  "ignitionVersion": "...",
  "toolBundleVersion": "...",
  "nativeFunctions": {
    "system.tag.readBlocking": true,
    "system.historian.queryRawPoints": true
  }
}
```

这样 Agent 不需要失败 5 次才知道 capability 缺失。

---

# 29. 不要让 FastMCP 调用官方 MCP Server——至少第一版不要

技术上之后完全可以。

FastMCP 4 的 `ClientGroup` 可以同时管理新旧 protocol MCP servers，并自动 namespace/routing。([GoFastMCP][10])

甚至 FastMCP 本身也有 proxy 能力。

但第一版如果：

```text
AI
 ↓
FastMCP
 ↓
Ignition MCP
 ↓
system.*
```

我们又重新制造：

```text
double authentication
double MCP serialization
double error translation
identity propagation
timeout propagation
audit ambiguity
```

和 WebDev bridge 很类似。

所以第一版最好的架构就是你提出的：

```text
AI → FastMCP REST

AI → Ignition Native MCP
```

**两个平行 server。**

这是正确的。

以后可以另加：

```text
ignition-unified
```

作为 optional facade。

不要放进 core。

---

# 30. 我建议的研发顺序

1. **先冻结 architecture / license / capability ownership。** 不写 Tool；先把 WhiskeyHouse GPL 冲突、两个 server 的职责和命名确定下来。

2. **重写 FastMCP 4 external core。** 做 Async GatewayClient、FastMCP 4 Streamable HTTP、auth middleware、typed errors、`/openapi.json` capability registry、diagnose；先只实现完全确定的 read-only Native REST。

3. **建立 Ignition native tool foundation。** 按已经载入的 `ignition-mcp-tools-skill` 实现 `runtime_info → tag_browse → tag_read → tag_write`，并做到 Designer ZIP import + 真正 MCP `tools/list/call` integration test。

4. **迁移 WebDev capabilities。** 顺序做 Tags → Alarms → Historian → DB。每个 capability 都从 jsgorana/WhiskeyHouse 中提取 semantics，但重新按 `system.*` 原生 API 设计，不一比一复制旧 tool。

5. **最后解决 Perspective。** 不使用不存在的 Project Resource REST，也不默认 filesystem write + scan；建立 project-export ZIP adapter、offline validation、concurrency guard、backup 和 post-import verification。

6. **最后做 installer / unified UX。** `ignition-mcp setup` 自动检查 MCP Module、导入 tool project、建立 MCP server config、security setup diagnostics；再考虑 optional unified facade。

---

# 最终架构判断

我会把这次项目定义成：

> **一个 Ignition-aware MCP ecosystem，而不是一个巨型 MCP Server。**

核心是：

```text
Configuration / Administration Plane
        =
FastMCP 4 + Ignition Native REST

Runtime / SCADA Plane
        =
Official Ignition MCP Module + system.*
```

这比：

```text
FastMCP
  + REST
  + WebDev
  + HMAC
  + arbitrary Gateway scripts
```

清晰得多，也更符合 Ignition 本身的能力边界。

其中我认为现在最值得提前锁死的 5 个决定是：**不再使用 WebDev、Native REST capability 以目标 Gateway `/openapi.json` 为事实来源、不提供任意 script execution、不使用 filesystem+scan 作为 Perspective 正常部署路径、两个 MCP Server 第一版保持独立。**

如果按这个方向推进，下一步最合理的是我直接对 **两个 Repo 做完整 Tool-by-Tool inventory**，把现有 43/37 个 Tool 去重后做成一张：

```text
原 Tool
→ 实际 Native API
→ 是否真实存在
→ 新 FastMCP
→ 新 Ignition Native Tool
→ 删除/重设计
→ 风险等级
```

的 **最终迁移矩阵**。这张表确定后，就可以直接作为整个 Repo 的 implementation backlog。

[1]: https://github.com/WhiskeyHouse/ignition-mcp?utm_source=chatgpt.com "GitHub - WhiskeyHouse/ignition-mcp: Ignition (8.3 and above) MCP server work with the new REST API · GitHub"
[2]: https://github.com/jsgorana/ignition-mcp/blob/main/docs/bridge.md?utm_source=chatgpt.com "ignition-mcp/docs/bridge.md at main · jsgorana/ignition-mcp · GitHub"
[3]: https://docs.rs/ignition-core/latest/ignition_core/client/index.html?utm_source=chatgpt.com "ignition_core::client - Rust"
[4]: https://www.docs.inductiveautomation.com/docs/8.3/platform/gateway/openapi?utm_source=chatgpt.com "API Documentation | Ignition User Manual"
[5]: https://forum.inductiveautomation.com/t/ignition-8-3-openapi-functionality-similar-to-tag-browser-export-for-tag-and-udt-json/113192?utm_source=chatgpt.com "Ignition 8.3 OpenAPI - Functionality Similar to Tag Browser Export for Tag and UDT JSON - Ignition - Inductive Automation Forum"
[6]: https://gofastmcp.com/v2/changelog?utm_source=chatgpt.com "Changelog - FastMCP"
[7]: https://docs.inductiveautomation.com/docs/8.3/appendix/scripting-functions/system-config?utm_source=chatgpt.com "system.config | Ignition User Manual"
[8]: https://www.docs.inductiveautomation.com/docs/8.3/appendix/scripting-functions/system-tag/system-tag-readBlocking?utm_source=chatgpt.com "system.tag.readBlocking | Ignition User Manual"
[9]: https://gofastmcp.com/v2/servers/resources?utm_source=chatgpt.com "Resources & Templates - FastMCP"
[10]: https://blog.gofastmcp.com/3mufbh2vcv22o?utm_source=chatgpt.com "FastMCP 4 is GA - fastmcp"
[11]: https://gofastmcp.com/servers/auth/authentication?utm_source=chatgpt.com "Authentication - FastMCP"
[12]: https://forum.inductiveautomation.com/t/mcp-module-early-access/113966?utm_source=chatgpt.com "MCP Module Early Access - Ignition Early Access - Inductive Automation Forum"
[13]: https://forum.inductiveautomation.com/t/mcp-module-early-access/113966/37?utm_source=chatgpt.com "MCP Module Early Access - #37 by stevenwhite1989 - Ignition Early Access - Inductive Automation Forum"
[14]: https://forum.inductiveautomation.com/t/mcp-module-early-access/113966/2?utm_source=chatgpt.com "MCP Module Early Access - #2 by paul-griffith - Ignition Early Access - Inductive Automation Forum"
[15]: https://www.docs.inductiveautomation.com/docs/8.3/appendix/scripting-functions/system-util?utm_source=chatgpt.com "system.util | Ignition User Manual"
[16]: https://github.com/jsgorana/ignition-mcp?utm_source=chatgpt.com "GitHub - jsgorana/ignition-mcp: MCP server for Inductive Automation Ignition — tags, alarms, history, projects, and full Perspective deployment · GitHub"
