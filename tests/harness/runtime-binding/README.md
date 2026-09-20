# Runtime native-response binding characterization

This is the Phase 0 / G0 executable live-test fixture. It does **not** emulate Ignition and it does not require a user-supplied Gateway.

The canonical path is GitHub Actions creating a fresh `inductiveautomation/ignition:8.3.8` container, mounting the checksum-pinned official MCP Module fixture, exposing this standalone test project/server config, executing the deterministic MCP client, collecting evidence, and destroying the container/volumes.

Pinned Phase 0 tuple:

- Gateway: `8.3.8` (expected build `2026071409`; the workflow verifies the startup log)
- MCP Module: `1.3.5.2026021307-SNAPSHOT` / build `2026021307`
- MCP Module SHA-256: `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`
- MCP protocol: `2025-06-18`

The test project publishes exactly two Tools, one Text Resource, and one Prompt. G0 requires:

1. MCP initialize succeeds and returns a session ID.
2. `tools/list` returns the exact Tool inventory and publishes an `outputSchema` for the success Tool.
3. successful `tools/call` returns native MCP `structuredContent` matching the fixture model.
4. deliberate failure returns a Tool result with `isError=true`.
5. `resources/list` + `resources/read` work for the fixture resource.
6. `prompts/list` + `prompts/get` work for the fixture Prompt.
7. evidence records exact Gateway/module/image/bundle identities and OpenAPI fingerprint when reachable.

The CI-only server config intentionally uses `AllOf` with an empty security-level set so the local disposable container can characterize the MCP transport without creating a reusable API credential. This configuration is test-only and must never be copied to deployment profiles.

Run locally only when Docker is available:

```bash
(cd tests/fixtures/modules && sha256sum -c MCP-module-1.3.5.2026021307-SNAPSHOT.sha256)
python tests/harness/runtime-binding/build_fixture.py dist/phase0-characterization.zip
docker pull inductiveautomation/ignition:8.3.8
docker compose -f tests/harness/runtime-binding/docker-compose.yml up -d
# then run probe.py with the image digest and Gateway build observed from the container
```

Until all Tool-binding checks are live-verified, production Runtime remains fail-closed. A live failure is evidence: it must be recorded and resolved by a new module tuple or an explicit Decision/Amendment rather than hidden by a text-only fallback.
