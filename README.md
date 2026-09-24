# Ignition MCP

> 中文版：[`README.zh-CN.md`](README.zh-CN.md)

Two MCP servers that let AI agents read and, under layered safety rules, change an
[Inductive Automation Ignition](https://inductiveautomation.com/) Gateway.

| Server | Plane | What it is | Where it runs | Start it with |
| --- | --- | --- | --- | --- |
| `ignition-rest` | REST | FastMCP 4 server (Python 3.11+, Streamable HTTP) wrapping curated Ignition Native REST operations. | As a standalone process. | `ignition-rest-mcp` |
| `ignition-runtime` | Runtime | A bundle of Jython Tools, Text Resources and Prompts that call `system.*`. | On the Gateway, hosted by the Ignition Official MCP Module. | `ignition-mcp setup-native apply` (or `./scripts/deploy-runtime-bundle.sh`) |

Both planes are first-class and independent: you can deploy either alone. Capability ownership is
**exclusive** — an operation belongs to exactly one plane. Native REST owns everything a
semantically complete official endpoint covers; the Runtime plane owns the rest. There is no
arbitrary REST-request Tool, no arbitrary SQL (approved Named Queries only), and no WebDev bridge.
The rules are binding in [`docs/decisions/`](docs/decisions/INDEX.md).

## Status

v1 is delivered. Phases 0–6 (gates G0–G6) are closed and merged; each phase has a delivery record
under [`docs/development/`](docs/development/). Two Gateway/Module tuples are live-verified:

| Gateway | MCP Module | G6 gate | Native response binding |
| --- | --- | --- | --- |
| Ignition 8.3.8 (`b2026071409`) | `1.3.5.2026021307-SNAPSHOT` (`b2026021307`) | `VERIFIED` | `VERIFIED_WITH_LIMITATION` (D27) |
| Ignition 8.3.9 (`b2026082511`) | same build | `VERIFIED` | `UNVERIFIED_LIMITATION` / `FAILED_NATIVE_BINDING` |

The Module returns `structuredContent` and `isError` but publishes no Tool `outputSchema`; the
repo-owned JSON Schemas in `contracts/schemas/` are the mandatory output contract (D27). Gates record
`VERIFIED` / `VERIFIED_WITH_LIMITATION` / `UNVERIFIED_LIMITATION` / `UNTESTED` only — **never**
production `SUPPORTED`.

### REST surface (33 Tools, 2 Text Resources)

Exposure is decided by three independent switches: the scope on the caller's credential, whether the
Gateway documents the Tool's capability, and whether the Tool's Mutation class is enabled.

| Class | Scope | Enablement | Tools |
| --- | --- | --- | --- |
| read | `ignition.read` | always on | 21 |
| config mutation | `ignition.config` | `IGNITION_MCP_CONFIG_MUTATION_ENABLED` | 11 |
| control mutation | `ignition.control` | `IGNITION_MCP_CONTROL_MUTATION_ENABLED` | 1 |
| admin mutation | `ignition.admin` | `IGNITION_MCP_ADMIN_MUTATION_ENABLED` | 0 today |

Reads cover Gateway identity and diagnosis, projects, config resources, audit, alarm pipelines,
artifacts and exports, Perspective and operation diagnosis. The config Mutations are the four
`config_resource_*`, `project_import`, `tag_config_import`, `artifact_delete` and the four Perspective
writes; the control Mutation is `alarm_pipeline_cancel`. Text Resources:
`ignition://gateway/capabilities` and `ignition://gateway/openapi-info`. Every enabled Mutation also
needs its id in `IGNITION_MCP_MUTATION_OPERATIONS` and its targets in
`IGNITION_MCP_MUTATION_TARGETS`; none of this is wildcard-by-default.

### Runtime surface (22 Tools, 3 Text Resources, no Prompts)

The Server Config's **profile** decides the Tools the endpoint serves. Inventories are always
explicit, never `*`.

| Profile | Scopes | Tools |
| --- | --- | --- |
| `readonly` | READ | 13 |
| `operator` | READ + CONTROL | 16 |
| `configurator` | READ + CONFIG | 19 |
| `full` | READ + CONFIG + CONTROL | 22 |

Reads cover tags, alarms, historians, UDTs and Named Query execution. The Mutations are the CONTROL
Tools (`tag_write`, `alarm_shelve`, `alarm_unshelve`) and the CONFIG Tools (`tag_update`, `tag_create`,
`tag_copy`, `tag_delete`, `tag_move`, `tag_rename`). Every Mutation additionally requires the Runtime
Target Policy in the reserved `[IgnitionMCPPolicy]` Tag provider and fails closed without it.

Known v1 limitation: `alarm_status`, `alarm_journal` (D12 Phase 2 amendment) and `alarm_acknowledge`
(D12 Phase 4 amendment) stay parked until a native pre-execution bound exists; their handlers live in
`packages/ignition-runtime-bundle/deferred/`.

## Repository layout

| Path | Contents |
| --- | --- |
| `packages/ignition-rest-mcp/` | The external `ignition-rest` server and the `setup-native` operator CLI. |
| `packages/ignition-runtime-bundle/` | Designer Project source for the Runtime bundle, plus `BUNDLE_VERSION`. |
| `contracts/` | Language-neutral source of truth: per-Tool contracts, shared taxonomies, output schemas. |
| `tooling/` | Bundle validator/builder/releaser, contract linter, compatibility validator, CI helpers. |
| `tests/` | Live Docker Gateway harnesses and persisted compatibility evidence. |
| `docs/` | Decisions (binding), development records, the operator runbook, `CONTEXT.md` vocabulary. |

## Quick start

### Prerequisites

The toolchain differs by platform; the Gateway and Module inputs below are shared.

| Toolchain item | Linux/macOS | Windows |
| --- | --- | --- |
| Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) | the [official installer](https://docs.astral.sh/uv/) or a package manager | `winget install --id=astral-sh.uv -e`, or the same official installer |
| Shell | a POSIX shell | PowerShell 7 — the D31 §6 checklist uses `-SkipHttpErrorCheck`, which needs 7 |
| Git for Windows | not needed | only for the `bash`-based checks: `tooling.ci.check_workflows` and the bundled bash wizard |
| Java 11 | only for the recorded-Jython tests (D29); neither server needs it | same |

Windows instructions in this repository are marked **not run on Windows yet**; see [D31](docs/decisions/D31-windows-support-scope.md).

- An Ignition 8.3.8/8.3.9 Gateway reachable over HTTP(S), with an `ignition/api-token` resource.
  The REST server connects to it outbound; the Runtime bundle is installed on it.
- For the Runtime plane only: the official MCP Module `.modl` file and its SHA-256, from the
  Inductive Automation download channel. The repo pins version `1.3.5.2026021307-SNAPSHOT`, build
  `2026021307`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.

```bash
uv sync --all-packages   # the install step on both platforms
```

### Plane 1 — launch the REST server

```bash
export IGNITION_MCP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_GATEWAY_API_TOKEN=<your-ignition-api-token>
export IGNITION_MCP_DATA_DIR=$HOME/.local/state/ignition-mcp   # durable SQLite + artifact store

uv run --no-sync ignition-rest-mcp
```

The same variables in PowerShell (**not run on Windows yet**; see [D31](docs/decisions/D31-windows-support-scope.md)):

```powershell
$env:IGNITION_MCP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_GATEWAY_API_TOKEN = "<your-ignition-api-token>"
$env:IGNITION_MCP_DATA_DIR = "C:\ProgramData\ignition-mcp"   # durable SQLite + artifact store

uv run --no-sync ignition-rest-mcp
```

The development profile binds `127.0.0.1:8000/mcp` (`IGNITION_MCP_HOST` / `_PORT` / `_PATH` override
it) and serves `/health/live`, `/health/ready` and `/metrics`. Point a Streamable HTTP MCP client at
the URL:

```json
{
  "mcpServers": {
    "ignition-rest": { "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

Reads work as soon as the capability registry reports `READY` (it follows the Gateway's OpenAPI); a
Tool whose route the Gateway does not document stays hidden. Mutation Tools are hidden from
`tools/list` until their class is enabled *and* the Gateway documents their capability, and the
operation and target allowlists are then enforced at call time as well. To turn on the first config
Mutation on a trusted deployment:

```bash
export IGNITION_MCP_AUTH_MODE=static-token
export IGNITION_MCP_STATIC_TOKENS='{"agent":{"token":"<secret>","scopes":["ignition.read","ignition.config"]}}'
export IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
export IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
export IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/tag-provider/MyProvider"]}'
```

Full configuration — deployment profiles, auth modes, budgets, artifact limits, sensitive exports —
is in [`packages/ignition-rest-mcp/README.md`](packages/ignition-rest-mcp/README.md).

### Plane 2 — deploy the Runtime bundle

The bundle runs on the Gateway, not as a local process.

**Option A — interactive wizard** (walks the procedure stage by stage, persists answers to `.env`):

```bash
./scripts/deploy-runtime-bundle.sh
```

**Option B — the `setup-native` CLI.** Build the release artifacts first:

```bash
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
```

This writes three deterministic files: `ignition-runtime-bundle-<version>.zip`, `.manifest.json`
and `sha256sum -c`-compatible `.sha256`. Then:

```bash
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
mkdir -p ~/.config/ignition-mcp
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token && chmod 0600 ~/.config/ignition-mcp/gateway.token

# 1. Install the pinned Module (omit the acceptance flags to be shown what they accept first).
ignition-mcp setup-native install-module \
  --file ~/downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-url http://127.0.0.1:8088 --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --accept-certificate --accept-eula --restart

# 2. Diagnose, plan, apply, verify.
ignition-mcp setup-native doctor --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-url http://127.0.0.1:8088 --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly
ignition-mcp setup-native plan   --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip --server-config-name production --profile readonly --json
ignition-mcp setup-native apply  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip --policy-file policy.json \
  --server-config-permissions-file permissions.json --server-config-name production --profile readonly
ignition-mcp setup-native verify --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --server-config-name production --profile readonly
```

`apply` writes the Runtime Target Policy, the bundle Project and the MCP Server Config; `doctor`,
`plan` and `verify` write nothing. The agent then connects to
`<gateway-url>/data/mcp/<server-config-name>`. Raise the profile to `operator`, `configurator` or
`full` to serve more Tools, then re-run `apply`.

The complete operator procedure — every flag, exit code, certificate/EULA rule and failure
diagnosis — is in [`docs/operations/runbook.md`](docs/operations/runbook.md).

## Windows

Windows is not broken, but it is not a supported platform. [D31](docs/decisions/D31-windows-support-scope.md)
records the scope and the declared limitations. Nothing here has been run on Windows yet.

The [Prerequisites](#prerequisites) table and the PowerShell block in
[Plane 1 — launch the REST server](#plane-1--launch-the-rest-server) are the Windows prerequisites and
start path, and every instruction there carries the same **not run on Windows yet** marker.

- **Checksums.** Windows has no built-in `sha256sum -c`. Use `certutil -hashfile <file> SHA256` or
  `Get-FileHash <file> -Algorithm SHA256`, and compare the result with the hash in the `.sha256` file.
- **Wizard.** `scripts/deploy-runtime-bundle.sh` is a bash wizard, so it needs Git Bash or WSL. The
  `ignition-mcp setup-native doctor|plan|apply` commands are native and do not need it.
- **Secrets and the data directory.** POSIX-mode checks are skipped on Windows, and one WARNING is
  logged instead. Protect `IGNITION_MCP_DATA_DIR` and every credential file with filesystem ACLs.

## Safety model

- **Bounded everything.** Every input, execution and output is bounded; oversize fails explicitly
  and is never silently truncated (D10).
- **Target allowlists.** A Mutation may touch only targets the deployment listed; empty means none,
  and allowing all needs an explicit `*` (D08).
- **Class gates.** `CONFIG` / `CONTROL` / `ADMIN` Mutations default disabled, in both discovery and
  dispatch. The four scope names have no hierarchy.
- **Precondition tokens.** A change carries the token read from the state it modifies; a stale token
  is `conflict` and nothing is dispatched. Ambiguous dispatches are `outcome_unknown` and never
  replayed.
- **Refused resource types.** Security, identity, token and Module-administration resources are
  refused whatever the allowlist says; an unclassified type is refused too.
- **No secrets in output.** Credentials never reach logs, reports, audit rows or Tool output.
- **Errors, not envelopes.** Tools return MCP structured output plus the canonical error taxonomy in
  `contracts/shared/error-codes.json`; there is no `{ok,result,error}` envelope (D06).

## Development

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests  # needs Java 11 (D29)
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
```

Runtime `onToolCalled.py` / `onPrompt.py` files are Jython 2.7, tab-indented and self-contained.
Contributor rules, delegation policy and delivery conventions are in
[`AGENTS.md`](AGENTS.md); the domain vocabulary is in [`CONTEXT.md`](CONTEXT.md).

## Documentation

- [`docs/decisions/INDEX.md`](docs/decisions/INDEX.md) — binding architecture decisions D01–D31.
- [`docs/operations/runbook.md`](docs/operations/runbook.md) — v1 operator runbook.
- [`docs/development/`](docs/development/) — per-phase delivery records and gate evidence.
- [`contracts/README.md`](contracts/README.md) — the contract source of truth.
- [`tests/compatibility/evidence/`](tests/compatibility/evidence/) — persisted live-Gateway evidence.

## License

GPL-3.0-only. See [`LICENSE`](LICENSE).
