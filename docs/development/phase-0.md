# Phase 0 — Repository foundation + Native binding proof

Status: **COMPLETE — G0 CLOSED**.

## Completion evidence

Phase 0 is complete under D26 + D27. The final trusted CI result is:

- static/unit/build workflow: **PASS** — run `35506715663`;
- live Gateway G0 workflow: **PASS** — run `35506715670`;
- live workflow head: `cfb53a55dfccf5e28907879c7fac38b212e1b3b2`;
- live evidence artifact digest: `sha256:6f4e2ff245aec92f7c1ed7eb4425ba295623b1ac2d25a237c46640400dee6b0e`.

## Completed foundation

- D25 monorepo skeleton with both canonical product packages.
- Root uv workspace, lint/type/test/build CI, and GPL-3.0-only release metadata.
- Repo-owned Designer project validation and deterministic Runtime ZIP builder.
- Minimal shared D06/D08/D10/D18/D22 contracts and explicit Runtime profiles.
- Production-buildable Runtime `bundle_info` foundation; `NATIVE_BINDING_PENDING` removed.
- Official MCP Module fixture pinned by exact artifact SHA-256 and provenance.
- CI-owned ephemeral real Gateway; a user-provided Gateway is not a prerequisite.
- Deterministic MCP protocol characterization fixture and machine-readable evidence.
- D27 explicit exact-tuple handling for the official Module's missing native `tools/list.outputSchema`.

## G0 live binding result

The final workflow provisioned a fresh `inductiveautomation/ignition:8.3.8` Gateway and exercised the real MCP transport against:

```text
Gateway           8.3.8 (b2026071409)
Gateway image     inductiveautomation/ignition:8.3.8
Image digest      sha256:560d0c069911476ff65aa7fd0fefab0530897e81c2a6cc5ea1410026126ed8ce
MCP Module        1.3.5-SNAPSHOT
MCP Module build  2026021307
Artifact SHA-256  b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365
MCP protocol      2025-06-18
```

Verified:

- `initialize` and MCP session establishment;
- exact Tool inventory and input parameter mapping;
- successful native `structuredContent`;
- deliberate failure with native `isError=true`;
- `resources/list` + `resources/read`;
- `prompts/list` + `prompts/get`;
- deterministic evidence and cleanup.

Native `outputSchema` is absent on this exact official Module build. D27 therefore resolves G0 as `VERIFIED_WITH_LIMITATION`, without text-only fallback and without claiming D21 `SUPPORTED`. A different Gateway/Module tuple must be re-characterized.

## CI API-token / OpenAPI foundation

The Phase 0 live harness now also provisions a deterministic **ephemeral-CI-only** API token for later Native REST testing.

Security tree:

```text
Authenticated
├── IgnitionMcpCi
└── Roles
```

`IgnitionMcpCi` is therefore a child of `Authenticated` and a sibling of `Roles`.

The fresh Gateway's existing security configuration is patched rather than replaced. Gateway General Security assigns the exact path `Authenticated/IgnitionMcpCi` to all three permission classes:

- Gateway Access Permissions — `AnyOf`;
- Gateway Read Permissions — `AnyOf`;
- Gateway Write Permissions — `AnyOf`.

The API token is assigned the same security level and uses `secureChannelRequired=false` only because the disposable GitHub Runner talks to the Gateway over loopback HTTP. The credential is not a production secret and must never be reused outside ephemeral CI.

Live proof:

- unauthenticated `/openapi.json` → `403`;
- authenticated `/openapi.json` → `200`;
- authenticated `/data/api/v1/gateway-info` → `200`;
- authenticated `POST /data/api/v1/api-token/generate` → `200`, proving Gateway Write permission;
- OpenAPI SHA-256 is now recorded in G0 evidence rather than `null`.

The same test credential/security fixture is available to later Runtime MCP security smoke tests, but Phase 0 G0 deliberately keeps native response-binding characterization separate from D09 authorization characterization.

## Next gate

The implementation entry point is now **Phase 1 / G1**:

- minimal `ignition-rest` read-only vertical slice;
- Runtime `bundle_info`, `tag_browse`, `tag_read`;
- real L3 REST path using the CI API-token fixture;
- real L4 Runtime initialize/list/call with exact inventory.

Phase 0 must not be reopened implicitly. Any change to its frozen native-binding conclusion or D27 exception requires new evidence and the appropriate Decision/Amendment.
