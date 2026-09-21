# Contracts

`contracts/` is the repository-level semantic source of truth. It is not a runtime package or code generator.

Phase 0 establishes shared error, permission, mutation, budget, pagination, batch, artifact, profile and compatibility-evidence semantics. Product implementations remain explicit and are checked against these contracts.

Profile files use a JSON-compatible YAML 1.2 subset so the Phase 0 linter remains standard-library-only; consumers must treat them as YAML documents, not as a runtime contract interpreter.

`shared/refused-resource-types.json` (D30 §5) classifies every configuration resource type in a
supported OpenAPI document as **allowed** or **refused** for generic config Mutations; a type in
neither list is refused, and refused types answer `permission_denied` whatever the Target allowlist
says. Two documents anchor the classification: the full 8.3.8 export under
`docs/ignition-8.3.8-openapi/` (57 resource types) and the 8.3.9 candidate's derived resource-type
inventory under `docs/ignition-8.3.9-openapi/` (56 types, a strict subset; the candidate's 12.7 MB
document is not committed, and the inventory records its SHA-256 and the live run that captured it).
A test classifies both, and rediscovering either by path means a new version fails the test until its
types are classified.
