# Phase 1 G1 evidence

Captured from [G1 run 35513611855](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611855), artifact `phase1-g1-35513611855`, implementation commit `7f2b3a1f31fcefddeba7306f93b6867108e8488f`.

Same-commit [CI](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611887) and [G0](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513612004) also passed. G1 used an ephemeral official Gateway and deployed the exact generated Runtime ZIP.

[evidence.json](evidence.json) and [raw protocol responses](raw/runtime-initialize.json) are copied unchanged from the workflow artifact. Credentials, security bootstrap resources, and logs are deliberately excluded. Bundle SHA-256: `18a938ff7b8ee799504157274f185df44a4e68d48ed48171bf042d431f20a96b`.

Gate G1 is `VERIFIED`; native binding is `VERIFIED_WITH_LIMITATION` (D27 absent native outputSchema; D28 explicit reversible null encoding). Runtime Prompt capability is absent with an expected empty inventory, so prompts/list is `NOT_APPLICABLE`, not PASS. External prompts/list actually returned empty.

This is a Phase 1 development acceptance snapshot, not D21 `SUPPORTED` certification or completion of later phases. Build identity supplied by the harness is pinned via artifact hash and startup identity; bundle_info honestly reports an unknown module build when the native version string does not expose it.
