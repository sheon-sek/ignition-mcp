# Contracts

`contracts/` is the repository-level semantic source of truth. It is not a runtime package or code generator.

Phase 0 establishes shared error, permission, mutation, budget, pagination, batch, artifact, profile and compatibility-evidence semantics. Product implementations remain explicit and are checked against these contracts.

Profile files use a JSON-compatible YAML 1.2 subset so the Phase 0 linter remains standard-library-only; consumers must treat them as YAML documents, not as a runtime contract interpreter.
