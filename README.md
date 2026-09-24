# Ignition MCP

Two MCP servers that let AI agents read and, under layered safety rules, change an
[Inductive Automation Ignition](https://inductiveautomation.com/) Gateway.

| Server | Plane | What it is |
| --- | --- | --- |
| `ignition-rest` | REST | External FastMCP 4 server (Python 3.11+, Streamable HTTP) wrapping curated Ignition Native REST operations. |
| `ignition-runtime` | Runtime | A bundle of Jython Tools, Text Resources and Prompts, hosted on the Gateway by the Ignition Official MCP Module. This repo ships the bundle, not the Module. |

Capability ownership is exclusive: an operation belongs to exactly one plane. Native REST owns
everything a semantically complete official endpoint covers; the Runtime plane owns the rest. There
is no arbitrary REST-request Tool, no arbitrary SQL (approved Named Queries only), and no WebDev
bridge. The rules are binding in [`docs/decisions/`](docs/decisions/INDEX.md).

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

Shipped inventory:

- **REST** — 33 Tools (21 read + 12 mutation), 2 Text Resources (`ignition://gateway/capabilities`,
  `ignition://gateway/openapi-info`).
- **Runtime** — 22 Tools (13 read + 9 mutation), 3 Text Resources, no Prompts.

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

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).
- An Ignition 8.3.8/8.3.9 Gateway reachable over HTTP(S), with an `ignition/api-token` resource.
- For the Runtime plane only: the official MCP Module `.modl` file and its SHA-256, from the
  Inductive Automation download channel. The repo pins version `1.3.5.2026021307-SNAPSHOT`, build
  `2026021307`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.

### 1. Run the REST server

```bash
uv sync --all-packages          # installs the workspace package and its console scripts

export IGNITION_MCP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_GATEWAY_API_TOKEN=<your-ignition-api-token>
export IGNITION_MCP_DATA_DIR=$HOME/.local/state/ignition-mcp   # durable SQLite + artifact store

uv run --no-sync ignition-rest-mcp
```

The development profile binds `127.0.0.1:8000/mcp` and serves `/health/live`, `/health/ready` and
`/metrics`. Point any Streamable HTTP MCP client at that URL:

```json
{
  "mcpServers": {
    "ignition-rest": { "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

Reads work as soon as the capability registry reports `READY` (it follows the Gateway's OpenAPI).
Mutation Tools stay hidden from `tools/list` and refused at call time until the deployment enables
their class and allowlists them. Full configuration — deployment profiles, auth modes, budgets,
artifact limits — is in [`packages/ignition-rest-mcp/README.md`](packages/ignition-rest-mcp/README.md).

### 2. Deploy the Runtime server

The bundle is deployed onto the Gateway, not run as a local process.

**Option A — interactive wizard** (walks the manual procedure stage by stage, persists answers to
`.env`):

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
`plan` and `verify` write nothing. The MCP endpoint the agent then connects to is
`<gateway-url>/data/mcp/<server-config-name>`.

The Runtime **profile** decides the Tool inventory: `readonly` (13 read), `operator` (+3 CONTROL),
`configurator` (+6 CONFIG), `full` (all 22). Inventories are always explicit, never `*`. Every
Bundle Mutation additionally requires the Runtime Target Policy in the reserved
`[IgnitionMCPPolicy]` Tag provider and fails closed without it.

The complete operator procedure — every flag, exit code, certificate/EULA rule and failure
diagnosis — is in [`docs/operations/runbook.md`](docs/operations/runbook.md).

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

- [`docs/decisions/INDEX.md`](docs/decisions/INDEX.md) — binding architecture decisions D01–D30.
- [`docs/operations/runbook.md`](docs/operations/runbook.md) — v1 operator runbook.
- [`docs/development/`](docs/development/) — per-phase delivery records and gate evidence.
- [`contracts/README.md`](contracts/README.md) — the contract source of truth.
- [`tests/compatibility/evidence/`](tests/compatibility/evidence/) — persisted live-Gateway evidence.

## License

GPL-3.0-only. See [`LICENSE`](LICENSE).
