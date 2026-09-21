# Phase 3 G3 evidence: 8.3.9 compatibility candidate row

Captured from [G3 run 35595061842](https://github.com/sheon-sek/ignition-mcp/actions/runs/35595061842), artifact `phase3-g3-8.3.9-35595061842` (id `10636525661`). The run evaluated implementation commit `c3f69552045ca0ed5a200ba2a3934d0cbfcebf19`; its pull-request test merge revision `a2ff07b4d626516433c4a2b1b684743bdc45f819` is stamped in the deployed manifest. This matrix row is a non-required compatibility candidate.

The exact deployed identities were:

- Gateway `8.3.9 (b2026082511)`, image `inductiveautomation/ignition:8.3.9`, digest `sha256:28bd6b320157ec8dbbe465d0cd7c9f0ababfda4ff01c0bab982c0522a7b4eba2`.
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`, artifact version `1.3.5.2026021307-SNAPSHOT`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.
- Runtime Bundle `0.2.0`, deployed ZIP SHA-256 `4ecbb3ca4b5b0d127e735080e774e5baf5b659f7d798929b1048980d101085a7`. The artifact's manifest records the same ZIP, bundle version, and source revision.
- Gateway OpenAPI SHA-256 `d74e347fe97ae917f714282070d58c4b6a73faf92be10af7f1a271db7bb7fb62`.

The live row verified `setup-native doctor`, `plan`, and `verify`, with every plan action reporting `NO CHANGE`. Gate-on discovery exposed the exact 16 read-only Tools. Gate-off discovery exposed the exact 14-Tool inventory, hid both export Tools, denied a direct `project_export` call, and exposed zero mutation Tools. Runtime discovery stayed at the exact 13 Tools; `bundle_info` reported bundle `0.2.0` and the stamped revision. The endpoint reachability probe accepted the observed HTTP 415 response, and the absent `prompts/list` capability was handled as not applicable.

Both unchanged Project exports produced stable fingerprint `pcf1:f2a6639cbbd9e89f9e8a7e990df1fe1024146cf40e30a87c67602d44d111fdfb`. GET and HEAD metadata matched, and the downloaded archive SHA-256 matched its artifact metadata and ETag. `artifact_list`, `artifact_info`, `tag_config_export`, and `operation_diagnose` passed, including the canonical `not_found` result for an unknown correlation ID.

The JWT authorization probes rejected an invalid token, a read-only principal, and a non-allowlisted target before dispatch. The disposable Project fingerprint did not change. The transaction run produced `NO_CHANGE` without dispatch, `COMMITTED` after one import and a matching re-export, and `CONFLICTED` after real external drift without an import attempt. Recovery artifacts were persisted. The sensitive export, committed import, and external-drift injection each recorded `decision`, `attempt`, and `result` audit rows; pre-dispatch denials recorded decision rows only. The round-trip probe was stable after the candidate changed the Named Query `query.sql` payload that Ignition preserves.

The row records the owner-accepted `phase3-live-environment-protection` deviation. The workflow still applied the trusted-repository guard, local-only endpoints, a run-specific CI marker, expected Gateway identity, and a disposable Project name before import.

[evidence.json](evidence.json) is copied byte-for-byte from the generated artifact. Logs, metrics, the bootstrap identity material, and tokens remain outside the repository.

Gate G3 status for this candidate row is `VERIFIED_WITH_LIMITATION`. The machine-readable row remains fail-closed with `status: FAILED_NATIVE_BINDING`, `nativeResponseBinding: UNVERIFIED_LIMITATION`, and `d27ExceptionApplied: false`, because D27's native-binding exception belongs only to the exact 8.3.8 tuple. D21 deployment compatibility remains `UNTESTED`; this row makes no production compatibility certification.
