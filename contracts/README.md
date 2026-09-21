# Contracts

`contracts/` is the repository-level semantic source of truth. It is not a runtime package or code generator.

Phase 0 establishes shared error, permission, mutation, budget, pagination, batch, artifact, profile and compatibility-evidence semantics. Product implementations remain explicit and are checked against these contracts.

Profile files use a JSON-compatible YAML 1.2 subset so the Phase 0 linter remains standard-library-only; consumers must treat them as YAML documents, not as a runtime contract interpreter.

`shared/refused-resource-types.json` (D30 §5) classifies every configuration resource type in a
supported OpenAPI document as **allowed** or **refused** for generic config Mutations; a type in
neither list is refused, and refused types answer `permission_denied` whatever the Target allowlist
says. The 8.3.8 document under `docs/ignition-8.3.8-openapi/` is the committed anchor; no 8.3.9
document is committed, so its types are unclassified and therefore refused by the same rule.
