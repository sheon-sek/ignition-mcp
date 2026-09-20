# Phase 1 — readonly dual-plane vertical slice

Scope is [D26 Phase 1/G1](../decisions/D26-v1-scope-and-implementation-order.md#phase-1--minimal-read-only-end-to-end-vertical-slice), not Phase 2 and not complete v1. Keep work on `phase-1-readonly-vertical-slice` (upstream `feature/phase-1-readonly-vertical-slice`); do not merge or expand phases automatically.

## Intent and authoritative contracts

The original intent is two independent MCP connections: external FastMCP → Native REST, and official Runtime MCP → Gateway `system.*`. No WebDev, arbitrary scripts, REST-to-Runtime proxy, or duplicated capability ownership. The research document is historical: final decisions supersede its universal result envelope and tentative inventories. D06 requires direct structured domain output; D27 permits missing native outputSchema only on the exact characterized baseline; D28 explicitly addresses the Module's null serialization defect.

Phase 1 exposes only:

| Plane | Tools | Resources | Prompts |
|---|---|---|---|
| External | gateway_info, gateway_diagnose | capabilities, openapi-info | empty |
| Runtime | bundle_info, tag_browse, tag_read | three source-schema Text Resources | empty |

External health/live, health/ready and metrics are operational HTTP endpoints. Mutations, expanded readonly catalog, durable operation audit, installer and artifact/project authoring remain later phases.

## Investigation: last ten commits and repeated failures

The starting HEAD was `62e93ab`. The last ten commits were:

| Commit | Change | Assessment |
|---|---|---|
| 62e93ab | assert no Prompt resource files | Static absence does not prove prompts/list is registered |
| b4b5851 | lock primitive selectors | Selector presence is not initialize capability presence |
| 20b5b92 | add Prompt wildcard selector | Does not create a Prompt handler for an empty project inventory |
| 2ca9086 | use 256 KiB Runtime output ceiling | Correct budget tightening; unrelated to discovery failure |
| a6b4090 | structured observability | Useful foundation; not a Prompt fix |
| b1c244a | accept build-qualified Gateway version | Valid observed wire-format correction |
| bb5c9f3 | persist compatibility identity | Useful, but identity alone does not validate domain output |
| e009eed | validate Tool input schemas | Correct discovery coverage; output schema remained unchecked |
| c81a5e6 | lock snake_case identifiers | Correct regression coverage for Module discovery names |
| d507b61 | rename Runtime resource identifiers | Addresses names derived from paths rather than titles |

[Run 35509943623](https://github.com/sheon-sek/ignition-mcp/actions/runs/35509943623) and [run 35510130690](https://github.com/sheon-sek/ignition-mcp/actions/runs/35510130690) have the same fatal error: Runtime `prompts/list` JSON-RPC `-32600`. Their raw initialize capabilities contain tools/resources, not prompts. The successful G0 fixture has an actual Prompt; copying its unconditional Prompt calls to a product with zero Prompts was the incorrect assumption.

The pinned Module creates a Prompt handler only when the resolved inventory is nonempty. Adding wildcard selectors cannot change that. G1 now checks initialize first: explicitly empty expected inventory + absent capability is `NOT_APPLICABLE`; advertised capability must list exactly empty and errors are not swallowed. No dummy product Prompt is introduced.

The old workflow forced the failed probe step to exit zero and failed only later at “Enforce G1”, so `gh run view --log-failed` showed a generic exit code instead of the root cause. Probe failures now fail their own step, preserve stage information, and still upload diagnostics/clean up via `always()`.

## Additional defects found before declaring success

1. Actual Runtime structured output omitted object members with null values, while its text copy preserved them. Prior smoke tests never validated the source output schema. [D28](../decisions/D28-runtime-null-wire-encoding.md), explicitly approved by the owner, defines collision-free reversible `ignition-null-v1`; no text-only fallback or silent optional-field relaxation.
2. Runtime errors were prose rather than canonical JSON. Invalid arguments now produce native `isError=true` with code/message/correlationId. Python and Java exceptions are both handled.
3. External transport buffered unlimited upstream bodies and bounded each I/O rather than elapsed time. It now streams within finite limits and an elapsed deadline; Resource outputs also enforce D10 budgets.
4. A lock serialized concurrent refreshes but did not coalesce them. Concurrent callers now share one shielded refresh task.
5. `secured` was rejected outright. A minimal RS256 JWT verifier with issuer/audience/expiry/read-scope validation is now implemented, alongside trusted-internal static-token/explicit none modes.
6. Diagnose falsely confirmed authentication during outages; output-limit failures bypassed canonical errors. Direct failures now invalidate readiness, schema/capability mismatches coalesce a metadata refresh without replaying the operation, and canonical errors/timeout/cancellation have correlated telemetry. Startup cancellation and failing parallel metadata requests drain/close resources. Regression tests cover these paths, including actual MCP Client calls and HTTP readiness transitions.
7. CI built a ZIP but deployed the source tree. G1 now deploys the exact generated artifact whose checksum it records.
8. First repair run [35513269783](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513269783) proved D28/null/Bad-quality/duplicates and canonical Runtime errors, then caught an error in the new probe: it assumed `application/json` while source Resources declared `application/schema+json`. The probe now compares each Resource against its actual declared MIME/size/URI/payload, with regressions for all three resources. This run is not recorded as a G1 pass.
9. Disposable API-token values were visible in step environment logs. Bootstrap now registers masks before exporting the token to subsequent steps.

## Reproduce without repeating the loop

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
uv run --no-sync python -m tooling.native.sync_schemas
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/ignition-runtime-bundle.zip
```

Before a push: run the complete local checks, compare generated schemas/metadata, review the diff, and group coherent fixes rather than repeatedly pushing speculative one-line changes. G1 runs the local tests before provisioning its Gateway.

After a failed run: download its evidence artifact first; inspect fatal stage, initialize capabilities, raw discovery/calls, then Gateway/server logs. Compare actual wire output against the contract, not only files and titles. Never loosen a gate until green or assume the Phase 0 fixture proves all Phase 1 behavior.

CI uses official ephemeral Ignition 8.3.8 and pinned Module build 2026021307 with checksum verification. It always tears down containers/volumes. Credentials are disposable fixture credentials, never production credentials. No user-supplied Gateway is needed. This workstation cannot access its Docker daemon; live validation therefore uses GitHub Actions, not a claimed local Gateway run.

## Evidence and limits

Local tests prove pure logic/static/build behavior, not Jython/JVM semantics. Live G1 must prove both servers, exact inventories, Resource payload/source equivalence, output schemas, text/structured equivalence, Bad-quality null samples, ordered duplicate reads, ISO/epoch timestamps, and native canonical errors. G0 continues to characterize its separate fixture.

G1 success does not imply `SUPPORTED`, Designer GUI import verification, JWKS rotation integration testing, Phase 2 completion, or full v1. Keep the D27 native-outputSchema and D28 null-encoding limitations visible.

## Acceptance — COMPLETE

Implementation commit: `7f2b3a1f31fcefddeba7306f93b6867108e8488f`.

- Local L0/L1/L2: **72 tests PASS**, Ruff PASS, mypy PASS (30 source files), lockfile check PASS, external wheel/sdist build PASS, Runtime reproducible ZIP comparison PASS.
- [GitHub CI 35513611887](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611887): **PASS**.
- [G0 35513612004](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513612004): **PASS**.
- [G1 35513611855](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611855): **PASS**.
- [Persisted evidence](../../tests/compatibility/evidence/g1-8.3.8-mcp-2026021307/evidence.json): unchanged workflow JSON, plus raw protocol replies and provenance. Bundle SHA-256 `18a938ff7b8ee799504157274f185df44a4e68d48ed48171bf042d431f20a96b` matches the locally rebuilt artifact.

G1 is closed for this Phase 1 baseline with explicit D27/D28 limitations. Documentation/evidence-only commits after the implementation commit do not change its verified runtime. Stop here; Phase 2 needs a separate user instruction and independent feature branch.
