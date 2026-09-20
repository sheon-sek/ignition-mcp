# D27 — Runtime Native outputSchema Limitation on MCP Module 1.3.5 build 2026021307

**Status:** DECIDED

## Context

D26 G0 required real-protocol characterization before Runtime implementation expanded. GitHub Actions run `35505312397` executed the Phase 0 fixture on the exact baseline tuple:

```text
Gateway           8.3.8 (b2026071409)
Gateway image     inductiveautomation/ignition:8.3.8
Image digest      sha256:560d0c069911476ff65aa7fd0fefab0530897e81c2a6cc5ea1410026126ed8ce
MCP Module        1.3.5-SNAPSHOT
MCP Module build  2026021307
Artifact version  1.3.5.2026021307-SNAPSHOT
Artifact SHA-256  b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365
MCP protocol      2025-06-18
```

The live MCP transport proved:

- initialize: PASS;
- exact Tool discovery and input schema: PASS;
- successful Tool `structuredContent`: PASS;
- failed Tool `isError=true`: PASS;
- Text Resource list/read: PASS;
- Prompt list/get: PASS;
- Tool `outputSchema` in `tools/list`: **ABSENT**.

The success response contained both native `structuredContent` and text content. The deliberate failure returned native `isError=true`. The missing `outputSchema` is therefore a distinct discovery limitation, not evidence of text-only Tool behavior.

## Decision

D06 remains the semantic contract source: every public Tool must have an explicit repo-owned output schema, and successful structured data must conform to it.

For **ignition-runtime only**, the pinned official MCP Module build characterized above is granted a documented native-discovery exception:

1. Native `structuredContent` remains mandatory.
2. Native Tool Error signaling with `isError=true` remains mandatory.
3. A repo-owned JSON Schema remains mandatory for every structured Runtime Tool result.
4. CI must validate Runtime structured-result fixtures/live results against that schema where applicable.
5. A schema may also be published as a Text Resource for documentation/discovery, but this does **not** pretend that `tools/list.outputSchema` exists.
6. Clients and manifests must report native output-schema availability truthfully.
7. No text-only success/error fallback is permitted.

For the verified Phase 0 baseline tuple, G0 may therefore resolve as:

```text
VERIFIED_WITH_LIMITATION
```

when all G0 checks except native `tools/list.outputSchema` pass and the exact D27 exception identity matches.

## Scope of exception

This exception is fail-closed and does not silently propagate.

It is valid only for the explicitly characterized Runtime compatibility identity:

```text
Gateway version/build: 8.3.8 / 2026071409
MCP Module runtime version: 1.3.5-SNAPSHOT
MCP Module build: 2026021307
MCP Module artifact SHA-256: b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365
```

A different Gateway build, MCP Module build, or module artifact must be re-characterized. It does not inherit `VERIFIED_WITH_LIMITATION`.

If a future official MCP Module publishes native `outputSchema`, the exception no longer applies to that tuple and Runtime must use/verify the native schema path.

## Compatibility semantics

`VERIFIED_WITH_LIMITATION` is a **native response-binding evidence status**, not a D21 deployment compatibility status.

It does not mean `SUPPORTED`.

D21 deployment compatibility remains:

```text
SUPPORTED | UNTESTED | INCOMPATIBLE | UNKNOWN
```

A tuple can become `SUPPORTED` only after all later required D23/D26 gates pass. D27 merely resolves the Phase 0 architectural uncertainty for the exact baseline tuple.

## ignition-rest

No exception applies to `ignition-rest`. FastMCP-owned Tools continue to publish their output schema normally.

## Evidence requirements

Machine-readable G0 evidence must preserve at least:

- exact Gateway version/build/image digest;
- MCP Module runtime version/build;
- module artifact version and SHA-256;
- `outputSchemaPublished`;
- `successStructuredContent`;
- `failureIsError`;
- Resource/Prompt results;
- the native response-binding status;
- a stable indication that the D27 exception was or was not applied.

Raw protocol responses should be retained as CI artifacts and a release-relevant evidence snapshot should be committed when the tuple is used for compatibility certification.

## D06 / D21 / D23 / D26 amendment

D27 is the explicit Decision anticipated by D26 when the target official MCP Module cannot satisfy the project's stricter D06 native-output-schema requirement.

It changes only the Runtime native schema-discovery requirement for the exact characterized tuple. It does not weaken structured output, Tool Error semantics, semantic output schemas, exact inventory, or real-Gateway testing.

## Frozen Configuration

```yaml
decision: D27
status: DECIDED

runtime_native_output_schema:
  semantic_schema_required: true
  structured_content_required: true
  is_error_required: true
  text_only_fallback: forbidden
  native_tools_list_output_schema:
    baseline_tuple: unavailable
    exception: exact_tuple_only
    future_builds_recharacterize: required

phase0_g0:
  accepted_binding_status:
    - VERIFIED
    - VERIFIED_WITH_LIMITATION
  limitation_requires_exact_D27_identity: true
  limitation_implies_D21_SUPPORTED: false

ignition_rest:
  native_output_schema_exception: false
```
