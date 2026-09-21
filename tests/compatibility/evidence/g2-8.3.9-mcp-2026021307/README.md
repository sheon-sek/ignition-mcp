# Phase 2 G2 evidence — 8.3.9 compatibility candidate row

Captured from [G2 run 35534976018](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976018), artifact `phase2-g2-8.3.9-35534976018` (id 10613065068), implementation commit `852fc37c3c116db7addb924d4a9962f885c5e290`. The matrix row is a non-required candidate (`continue-on-error`), so the overall run passes on the 8.3.8 row.

The harness provisioned its fixtures successfully and deployed the same deterministic Runtime ZIP SHA-256 `5fa7f06a6bde2cc3ccc876e250aac569fe710aa2688349192e903903a3b6cde2` on:

- Gateway `8.3.9 (b2026082511)`, image `inductiveautomation/ignition:8.3.9`, digest `sha256:28bd6b320157ec8dbbe465d0cd7c9f0ababfda4ff01c0bab982c0522a7b4eba2`;
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`, artifact version `1.3.5.2026021307-SNAPSHOT`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365` — the same module build as the D27 tuple but on a different Gateway build;
- Gateway OpenAPI SHA-256 `9d1804ba5c8903d73982fce9a0fadf7a7e41625e5e5dc96380539a6236f9ff5f`.

[evidence.json](evidence.json) and [raw protocol responses](raw/) are copied from the artifact. Excluded as sensitive: `ci-security-bootstrap.json`, all logs/metrics, and `rest-config_resource_get.json` / `rest-config_resource_list.json` (fixture DB username, connect URL, and API-token name).

This row completed the same full smoke as the required row: all 13 Runtime Tools produced schema-validated structured results (including the enum-normalized `tag_get_config`/`udt_type_get` config trees), both canonical negative cases held native `isError=true`, the 3 Runtime and 2 External Text Resources and the 11-Tool External plane all executed, and the machine-readable evidence was written.

The binding status is honestly fail-closed: `nativeResponseBinding: UNVERIFIED_LIMITATION`, `status: FAILED_NATIVE_BINDING`, `d27ExceptionApplied: false`. D27's absent-native-`outputSchema` exception is exact-tuple only; this tuple must be re-characterized (per the plan this row is the re-characterization record) and inherits nothing from 8.3.8. D21 deployment compatibility is `UNTESTED`.
