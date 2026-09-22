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

## Phase 4: Mutation Tools (D30)

The bundle now ships nine Mutation Tools next to the 13 READ Tools. Their behaviour is fixed by D30
and their contracts live in `contracts/tools/runtime/`:

| Tool | Class | Destructive | Precondition token | Target check (D30 §6) |
|---|---|---|---|---|
| `tag_write` | CONTROL | no | — | the Target; the per-item Native outcome is the result |
| `alarm_shelve`, `alarm_unshelve` | CONTROL | no | — | the exact Alarm path (`alarm_shelved_list` is the Observed state) |
| `tag_update` | CONFIG | no | Tag config fingerprint | the Target |
| `tag_create` | CONFIG | no | — (an existing target is `conflict`) | the Target |
| `tag_copy` | CONFIG | no | — (an existing destination is `conflict`) | the **destination** |
| `tag_delete` | CONFIG | yes | Tag config fingerprint | the Target |
| `tag_move` | CONFIG | yes | Tag config fingerprint (source) | the **source and the destination** |
| `tag_rename` | CONFIG | no | Tag config fingerprint | the **new path** |

Every one of them reads the **Runtime Target Policy** before it acts and fails closed with
`operation_disabled` when that document is missing, unreadable, malformed or oversized. The policy is
a Tag in the reserved `IgnitionMCPPolicy` provider (`[IgnitionMCPPolicy]RuntimeTargetPolicy`, with a
companion `RuntimeTargetPolicyLength` Int4 Tag that gates the read at 32 KiB), written by
`ignition-mcp setup-native apply` and never by the Runtime server. It carries the per-Tool Target
allowlists, the Service identity used as the audit actor, the D18 audit mode and the `alarm_shelve`
duration cap. Mutation-class enablement stays in the Server Config profile: `readonly` is unchanged
(13 Tools), `operator` adds the three CONTROL Tools (16), `configurator` adds the six CONFIG Tools
(19) and `full` adds both (22). Every profile list is explicit — never `*`.

Cross-cutting rules, all implemented in each handler and pinned by its D29 fixtures:

- **Preflight.** Input bounds, the reserved-provider refusal and the Target allowlist are checked for
  every item before any item executes; one bad item refuses the whole batch and nothing runs. After
  Preflight, items execute one at a time with per-item outcomes and no rollback.
- **Reserved provider.** Any target inside `IgnitionMCPPolicy` is refused with `permission_denied`
  before the allowlist is consulted, including under an explicit `*`. The match is on the provider
  component, and for a move, copy or rename it covers both ends.
- **UDT definitions.** A Tag CONFIG Mutation reaches `[provider]_types_/…` only when the policy lists
  an explicit `_types_` prefix; a bare `*` does not cover it.
- **Fixed knobs.** `references=ABORT`, `allowInvalidReferences=false` and `collisionPolicy=Abort` are
  not caller parameters.
- **Observed state.** Each Tool re-reads its own targets within a bounded budget and reports what it
  observed; the Observed state never decides success. A read that cannot be made bounded is reported
  as an explicit `limit_exceeded` observed error instead of being materialized.
- **Audit.** `system.util.audit` runs in the policy's mode with the policy's Service identity as
  actor. `required` checks the named audit profile before executing and fails closed with
  `operation_disabled` when it is unavailable; a refused item is audited with a `decision` row; a
  failed result write leaves the outcome alone and reports `auditRecorded=false` (D18).
- **No retry.** A dispatch is attempted once. An item whose Native outcome is itself indeterminate is
  `outcome_unknown`, and later items are `not_executed` — never replayed.

`BUNDLE_VERSION` tracks the released milestones (Phase 3 closed at 0.2.0; Phase 4 4a–4d bump it per
milestone). `bundle_info` gains nothing for the Mutations — the inventory it reports is the bundle's
own, not the deployment's profile.
