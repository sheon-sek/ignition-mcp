# D28 — Lossless Runtime null encoding on the pinned Module

**Status:** DECIDED — explicitly approved by the project owner during Phase 1 repair.

## Evidence and scope

G1 run [35510130690](https://github.com/sheon-sek/ignition-mcp/actions/runs/35510130690) returned `diagnosticMessage: null` in the auto-generated text copy, but omitted that member from native `structuredContent`. The pinned Module's shared Gson does not enable `serializeNulls`. Its handler conversion preserves null initially; final protocol serialization removes null-valued object members. Local `JsonNull`, another Gson instance, and text-only output do not fix that final serializer.

This affects arbitrary nested Tag values, not just Quality metadata. D27 only covers absent native outputSchema; it does not authorize this loss. We will not modify the official binary, mutate its global serializer, silently make required fields optional, or reconstruct structured results from text.

## Decision

The experimental Runtime Bundle uses **ignition-null-v1**, a reversible JSON domain encoding:

- A logical null is `{"$ignition":"null"}` everywhere it can occur in Runtime structured domain data.
- Arrays retain order and recursively encode their members.
- An ordinary object without the reserved `$ignition` key retains its shape; its values are recursively encoded.
- An input object containing `$ignition` is escaped as `{"$ignition":"object","entries":[["key",encodedValue],...]}`. Keys are unique strings. This prevents a genuine Tag document from being confused with an encoding marker.
- Non-null JSON scalar values are unchanged.
- The decoder recognizes only those two exact marker shapes; it recursively decodes array members, ordinary object values, and escaped entries. An escaped object is reconstructed once, not decoded again as a marker.

Example: logical `{"x":null,"data":{"$ignition":"null"}}` becomes `{"x":{"$ignition":"null"},"data":{"$ignition":"object","entries":[["$ignition","null"]]}}`.

The schema explicitly models nullable Quality/timestamp/module-build fields and recursive Tag JSON values. `value`, `quality`, and `timestamp` remain required for successful Tag items. Bad/Uncertain quality remains domain data, including null-valued samples. No null becomes an empty string, missing field, zero, or fabricated Good value.

This is a Runtime domain encoding, not an `{ok,result}` envelope and not a JSON string containing the whole response. Native `structuredContent` and `isError` remain mandatory. The external REST server retains ordinary JSON null. Canonical errors contain non-null code/message/correlationId.

## Verification and compatibility

L1 tests round-trip nulls, nested objects/arrays, Unicode and reserved-key collisions. L4 validates **actual structuredContent** against source schemas, checks text/structured equality, and exercises a missing Tag (Bad quality with null value), duplicate paths, both timestamp modes, and canonical Tool errors.

Phase 1 inventory verification is capability-aware: for an explicitly empty Prompt inventory, absent initialize capability means `NOT_APPLICABLE` for prompts/list, not a fabricated successful response. If prompts are advertised, list must succeed and be exactly empty. Nonempty expected inventories always require the capability and successful discovery. This clarifies D23/D26 without adding a dummy public Prompt.

The encoding remains explicit for this experimental bundle; changing it after an upstream Module fix requires a versioned contract change and real re-characterization. D27's exact identity restriction still applies, and G1 does not imply production `SUPPORTED` or complete v1.

## Amendments

D06/D11/D21/D22/D26: preserve logical null through this declared encoding on Runtime, instead of assuming the pinned Module transports object JSON null unchanged. D23: validate live domain schemas and distinguish absent optional capability from successful empty listing. D27's outputSchema exception is unchanged.
