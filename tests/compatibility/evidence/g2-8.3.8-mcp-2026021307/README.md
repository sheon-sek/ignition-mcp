# Phase 2 G2 evidence — 8.3.8 required row

Captured from [G2 run 35534976018](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976018), artifact `phase2-g2-8.3.8-35534976018` (id 10612770013), implementation commit `852fc37c3c116db7addb924d4a9962f885c5e290`.

Same-commit [CI](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976025) and [G0](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976030) also passed. The harness provisioned its fixtures successfully (`provision.json` status `PASS` in the full artifact) and deployed the exact generated Runtime ZIP:

- Gateway `8.3.8 (b2026071409)`, image `inductiveautomation/ignition:8.3.8`, digest `sha256:560d0c069911476ff65aa7fd0fefab0530897e81c2a6cc5ea1410026126ed8ce`;
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`, artifact version `1.3.5.2026021307-SNAPSHOT`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365` (exact D27 identity);
- Runtime Bundle `0.1.0` ZIP SHA-256 `5fa7f06a6bde2cc3ccc876e250aac569fe710aa2688349192e903903a3b6cde2` (byte-identical to the deterministic local build at this commit and to the sibling 8.3.9 row);
- Gateway OpenAPI SHA-256 `2e1fd9aaad79e8bad1a7578e81efead364e9f392a13fa1872c071edc1c2978fa`.

[evidence.json](evidence.json) and [raw protocol responses](raw/) are copied from the artifact. Excluded as sensitive: `ci-security-bootstrap.json`, all logs/metrics, and the two raw responses `rest-config_resource_get.json` / `rest-config_resource_list.json` (they echo the fixture DB username, connect URL, and API-token name; their Tools' success is recorded in `external.smokeTools`).

Gate G2 on this row is `VERIFIED`; the native binding is `VERIFIED_WITH_LIMITATION` with `d27ExceptionApplied: true` — the pinned Module publishes real `structuredContent` and `isError` but no `tools/list.outputSchema` (D27), and Runtime logical nulls travel as `ignition-null-v1` (D28). All 13 frozen Runtime Tools, both canonical Runtime negative cases, all 3 Runtime Text Resources, the capability-aware empty Prompt inventory (`NOT_APPLICABLE`), all 11 External Tools (10 success smokes plus `alarm_pipeline_status` returning the canonical `not_found` on a pipeline-less Gateway), and both External Text Resources ran against the real Gateway.

D21 deployment compatibility remains `UNTESTED`. This closes Phase 2 / G2; it is not production `SUPPORTED` certification and does not absorb Phase 3.
