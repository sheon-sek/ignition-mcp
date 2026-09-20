# ignition-runtime-bundle

Designer Project source for the `ignition-runtime` server provided by the Ignition Official MCP Module.

Phase 0 native response binding is characterized on the D27 baseline tuple. The official MCP Module returns real `structuredContent` and `isError`, but does not publish Tool `outputSchema` in `tools/list`. D27 therefore keeps repo-owned JSON Schemas as mandatory semantic output contracts and records the native discovery limitation explicitly.

This package is production-buildable, but Phase 0/G0 evidence alone does **not** make a Gateway/Module/Bundle tuple D21 `SUPPORTED`; later D26 gates still apply.
