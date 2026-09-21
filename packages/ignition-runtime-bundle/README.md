# ignition-runtime-bundle

Designer Project source for the `ignition-runtime` server provided by the Ignition Official MCP Module.

Phase 0 native response binding is characterized on the D27 baseline tuple. The official MCP Module returns real `structuredContent` and `isError`, but does not publish Tool `outputSchema` in `tools/list`. D27 therefore keeps repo-owned JSON Schemas as mandatory semantic output contracts and records the native discovery limitation explicitly.

The Phase 1 bundle publishes exactly `bundle_info`, `tag_browse`, `tag_read`, three schema Text Resources, and no Prompts. It is buildable, but G0/G1 do **not** make a tuple production `SUPPORTED`; later D26 gates still apply.

The pinned Module drops object-valued JSON null members during serialization. [D28](../../docs/decisions/D28-runtime-null-wire-encoding.md) specifies the lossless `ignition-null-v1` wire encoding: null becomes `{"$ignition":"null"}`; arrays recurse; ordinary objects recurse; a Tag document containing the reserved `$ignition` key is escaped as `{"$ignition":"object","entries":[[key,encodedValue],...]}`. Decode escaped entries once to recover the original document. This applies to Tag values/quality/timestamps and unknown module build, not the external REST plane. Actual structured responses are schema-validated in G1; text is not a fallback.

After editing a source schema, run `uv run --no-sync python -m tooling.native.sync_schemas` and validate/build with `tooling.native.cli`. The live harness deploys the exact generated ZIP. An empty Prompt inventory may omit its initialize capability; G1 records prompts/list as `NOT_APPLICABLE`, not a fictional PASS.

## Release artifacts (D21)

`uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release --source-revision <40-hex git SHA> --evidence-dir tests/compatibility/evidence` writes three deterministic files: `ignition-runtime-bundle-<bundleVersion>.zip`, `…manifest.json`, `…sha256` (`sha256sum -c` compatible). CI builds twice and byte-compares all three.

- `BUNDLE_VERSION` (this directory) is the only version authority. The native validator fails unless the `bundle_info` handler's `bundleVersion` literal and the project ownership marker both equal it. The marker is the trailing `project.json` description line `ignition-mcp-managed: product=ignition-runtime-bundle; bundle=<bundleVersion>`; `setup-native doctor` classifies deployments through it and `release` checks it. `RESOURCE_SCHEMA_VERSION` tracks resource-shape changes.
- The release ZIP stamps the source revision into the `bundle_info` handler (`__BUNDLE_SOURCE_REVISION__`), so a deployed bundle answers `bundleSourceRevision` with the 40-hex git SHA; a plain `build` output stays `UNSTAMPED`.
- The manifest publishes the bundled Tool/Resource/Prompt inventories, the per-profile inventories, per-Tool native requirements, and `testedTuples` generated only from evidence rows that pass the `tooling.compat` validator. The release runs the validator itself and rejects production compatibility claims during Phase 3.
- The G3 live harness deploys exactly the release ZIP, and the evidence row records its SHA-256 (`deployedBundleSha256`) plus the manifest.
