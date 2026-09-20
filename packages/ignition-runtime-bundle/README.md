# ignition-runtime-bundle

Designer Project source for the `ignition-runtime` server provided by the Ignition Official MCP Module.

Phase 0 native response binding is characterized on the D27 baseline tuple. The official MCP Module returns real `structuredContent` and `isError`, but does not publish Tool `outputSchema` in `tools/list`. D27 therefore keeps repo-owned JSON Schemas as mandatory semantic output contracts and records the native discovery limitation explicitly.

The Phase 1 bundle publishes exactly `bundle_info`, `tag_browse`, `tag_read`, three schema Text Resources, and no Prompts. It is buildable, but G0/G1 do **not** make a tuple production `SUPPORTED`; later D26 gates still apply.

The pinned Module drops object-valued JSON null members during serialization. [D28](../../docs/decisions/D28-runtime-null-wire-encoding.md) specifies the lossless `ignition-null-v1` wire encoding: null becomes `{"$ignition":"null"}`; arrays recurse; ordinary objects recurse; a Tag document containing the reserved `$ignition` key is escaped as `{"$ignition":"object","entries":[[key,encodedValue],...]}`. Decode escaped entries once to recover the original document. This applies to Tag values/quality/timestamps and unknown module build, not the external REST plane. Actual structured responses are schema-validated in G1; text is not a fallback.

After editing a source schema, run `uv run --no-sync python -m tooling.native.sync_schemas` and validate/build with `tooling.native.cli`. The live harness deploys the exact generated ZIP. An empty Prompt inventory may omit its initialize capability; G1 records prompts/list as `NOT_APPLICABLE`, not a fictional PASS.
