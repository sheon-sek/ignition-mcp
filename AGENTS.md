# Repository Guidelines

## Project Structure & Module Organization

This `uv` workspace is a dual-server MCP ecosystem for Inductive Automation Ignition (GPL-3.0) with two products:

- `packages/ignition-rest-mcp/src/ignition_rest_mcp/` — the external `ignition-rest` MCP server (FastMCP 4, Streamable HTTP, Python 3.11+). It wraps curated Ignition Native REST operations. Its unit tests are in the package's `tests/`.
- `packages/ignition-runtime-bundle/project/` — Designer Project source for the `ignition-runtime` server. The *Ignition Official MCP Module* hosts that server; this repo only ships the bundle of Tools, Text Resources and Prompts, written as self-contained Jython scripts that call `system.*`. `ignition-runtime-bundle` is an artifact name, not a third MCP server.

Language-neutral Tool contracts, shared taxonomies, and output schemas live under `contracts/` — the language-neutral semantic source of truth (JSON Schemas and contract JSON/YAML). It is not a code generator; both products are written by hand and checked against it. Build and validation code belongs in `tooling/`, while real-Gateway harnesses and compatibility evidence live in `tests/`. Treat `docs/decisions/` as binding architecture records; archived documents (`docs/_archive/`) are not authoritative.

## Governance: Decisions and Phase Gates

- `docs/decisions/D01–D30` plus `INDEX.md` are **binding**. Read the relevant decision before changing behavior. Changing a decided rule needs an explicit new decision or amendment section. Never make silent edits.
- Delivery is phase-gated (D26). Phases 0–3 (G0/G1/G2/G3) are closed and frozen; see `docs/development/phase-{0,1,2,3}.md`. **Phase 4 starts only on a new user-directed branch.** Don't begin next-phase work on `main` without being told to.
- Key rules that cut across files:
  - No duplicate equivalent operation across the two servers. Native REST owns an operation whenever a semantically complete official REST endpoint exists. Runtime MCP owns everything else (D02).
  - No arbitrary REST-request Tool, no arbitrary SQL (Named Queries only, D14), no WebDev bridge.
  - Tool names are `snake_case` (D05). Tools return MCP structured output plus the shared error taxonomy (`contracts/shared/error-codes.json`). There is no `{ok,result,error}` envelope (D06).
  - Every input, execution and output is bounded (D10). Oversize input or output must fail explicitly and never be silently truncated.
  - Never claim a Gateway/Module tuple is production `SUPPORTED`. The gates record `VERIFIED` / `VERIFIED_WITH_LIMITATION` / `UNVERIFIED_LIMITATION` / `UNTESTED` only.
  - `alarm_status` and `alarm_journal` are parked in `packages/ignition-runtime-bundle/deferred/` until a native pre-execution bound exists (D12 amendment).

## Commands

These mirror `.github/workflows/ci.yml`, which pins exact tool versions. The unit tests require Java 11 (D29) to run Runtime `onToolCalled.py` handlers under Jython 2.7.4. Put it on `PATH` or set `JYTHON_RUNNER_JAVA`. Without it, the tests fail rather than skip:

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
# ci.yml runs that same collection as two parallel jobs with pytest-xdist (D29's
# Java 11 is only installed for the first): the recorded-Jython handler tests
# (tooling/native/jython_runner/tests) and everything else — `tooling` minus that
# directory, plus packages/ignition-rest-mcp/tests. The two paths are disjoint and
# their union is the single command above.

# single test
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q packages/ignition-rest-mcp/tests/test_phase2_readonly.py -k <name>

# Pre-push: lint every workflow file and every `run:` block shell.
# actionlint is pinned to 1.7.12, fetched over HTTPS and sha256-verified into
# ~/.cache/ignition-mcp-ci. Its shellcheck and pyflakes integrations are
# always disabled (-shellcheck= -pyflakes=), so the result does not depend on
# which of those a machine has installed; `bash -n` covers shell syntax.
# Exit 1 lists each finding as path:line.
uv run --no-sync python -m tooling.ci.check_workflows

uv build --package ignition-rest-mcp

# Runtime bundle: validate + deterministic build (CI builds twice and `cmp`s the ZIPs)
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime.zip

# Deterministic release artifacts (CI builds twice and `cmp`s all three files)
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release --source-revision "$(git rev-parse HEAD)" --evidence-dir tests/compatibility/evidence

# After editing a contracts/schemas source that is published as a Runtime Text Resource
uv run --no-sync python -m tooling.native.sync_schemas

# Contract/inventory lint (also covered by tooling/contracts/tests)
uv run --no-sync python -m tooling.contracts.lint
```

mypy runs in `strict` mode. Ruff and mypy exclude the Jython bundle project directories and the vendored Designer scaffolds under `docs/`.

Run the server locally with the `ignition-rest-mcp` script (`ignition_rest_mcp.server:main`). Configuration is entirely through `IGNITION_MCP_*` environment variables; see `packages/ignition-rest-mcp/README.md` and `config.py`. The development profile binds `127.0.0.1:8000/mcp`.

The live Gateway harnesses (`tests/harness/runtime-binding`, `phase1-live`, `phase2-live`, `phase3-live`) use docker compose with a real Ignition image and the checksum-pinned MCP Module in `tests/fixtures/modules/`. They are driven by the `live-gateway-g0`, `phase1-live-g1`, `phase2-live-g2` and `phase3-live-g3` workflows. The G3 harness uses an in-process transaction driver; run `uv run --no-sync python tests/harness/phase3-live/rehearse_local.py` before spending another live CI run on driver or workflow changes. Persisted evidence lives in `tests/compatibility/evidence/`.

## Architecture

### External server (`ignition_rest_mcp`)

- `server.py` has `create_server()`, which registers every Tool and Resource with `@mcp.tool` and runs them through `_run_read`, `_enforce_output_budget` and `_raise_tool_error`. It also serves the `/health/live`, `/health/ready` and `/metrics` HTTP routes.
- `capabilities/registry.py` holds the D04 immutable OpenAPI capability registry. It fetches the Gateway OpenAPI, derives semantic capabilities, and publishes snapshots (`READY`, `STALE`, …). A background `_watch_capabilities` loop calls `_apply_visibility`, which enables or disables each gated Tool depending on whether its capability is present. **A new REST Tool must be added to that gating map and backed by a registry capability.**
- `services/` contains the domain logic (`readonly.py`, `gateway.py`). `client/gateway.py` holds the single shared `httpx.AsyncClient`, with streamed, size-capped and identity-encoding responses.
- `auth.py` and `config.py` handle deployment profiles `development` / `trusted-internal` / `secured` and auth modes `none` / `static-token` / `jwt` (D07/D07-A). The caller's credential is never forwarded to the Gateway, which only sees the deployment-owned API token.
- `operation.py` and `observability/` provide server-generated correlation IDs, structured logs and low-cardinality metrics (D18).

### Runtime bundle (Jython on the Gateway)

- Layout: `project/com.inductiveautomation.mcp/{tools,resources,prompts}/<name>/`. A Tool is a `resource.json` (parameters, description) plus an `onToolCalled.py`. A Resource is a `resource.json` plus a `data.bin`.
- `onToolCalled.py` and `onPrompt.py` are **Jython 2.7 and indented with tabs** (enforced by `.editorconfig`). Use `unicode`/`long`/`basestring` and Java interop. Python 3 syntax and stdlib assumptions do not apply. Each script is self-contained. There are no shared modules, so helpers such as `toolError` and `encodeNulls` are repeated per Tool.
- **D27:** the pinned Module returns `structuredContent` and `isError` but does not publish Tool `outputSchema`. The repo-owned schemas in `contracts/schemas/*.output.schema.json` are therefore the mandatory output contract. Selected schemas are also published as Text Resources through `sync_schemas`.
- **D28:** the Module drops object-valued JSON nulls. Runtime outputs use the `ignition-null-v1` encoding: null becomes `{"$ignition":"null"}`, and objects containing `$ignition` are escaped as `{"$ignition":"object","entries":[...]}`. This applies only to the Runtime plane, not to REST.
- `tooling/native/` holds the validator and deterministic ZIP builder for Designer projects. The live harnesses deploy the exact generated ZIP.

### Contracts

- `contracts/tools/{rest,runtime}/<tool>.contract.json` holds per-Tool contracts: permission class, mutation class, budget class, and `outputSchema` path. `contracts/shared/` holds the taxonomies. `contracts/profiles/*.yaml` use a JSON-compatible YAML subset so the linter stays standard-library-only.
- `tooling/contracts/lint.py` hard-codes the current-phase Tool inventories (`CURRENT_REST_READ_TOOLS`, `CURRENT_RUNTIME_TOOLS`) and the expected error codes and profiles. Adding, removing or renaming a Tool means updating the implementation, its contract, its schema, and these lists together.

## Coding Style & Naming Conventions

Use four spaces for normal Python and two spaces for JSON, YAML, and TOML. Runtime `onToolCalled.py` and `onPrompt.py` files require tabs and Jython 2.7-compatible syntax. Ruff enforces selected `E`, `F`, and `B` rules with a 120-character line limit; mypy is strict. Use `snake_case` for Tool names, modules, and functions. Keep Runtime scripts self-contained.

## Testing Guidelines

Pytest discovers `test_*.py`; name tests after observable behavior. Add focused tests beside the affected package or tooling module, including contract-shape and bound/error cases. No numeric coverage threshold is configured. Use Docker live harnesses only when Gateway behavior changes, and preserve evidence under `tests/compatibility/evidence/`.

## Commit & Pull Request Guidelines

History follows Conventional Commit-style subjects such as `fix(runtime): normalize tag config mappings` and `test(phase2): cover REST capability catalog`; use an imperative subject and `!` for breaking changes. PRs should explain the behavior and contract impact, reference relevant decisions or issues, list validation commands, and include live-Gateway evidence when applicable. Do not silently change a frozen decision or phase gate.

## Agent skills

### Issue tracker

Issues live in the GitHub Issues of `sheon-sek/ignition-mcp` and are managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses the five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

The repo is single-context: one root `CONTEXT.md`, with decisions in `docs/decisions/`. See `docs/agents/domain.md`.
