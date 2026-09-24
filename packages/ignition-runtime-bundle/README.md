# ignition-runtime-bundle

This folder holds the Tools of the `ignition-runtime` MCP server, written as an Ignition Designer
project. The official Ignition MCP Module runs them inside the Gateway. This repository ships only the
project, called the bundle. It is not a separate server program.

To install it, follow [Quick start](../../docs/guide/quick-start.md). To see what each
Tool does and needs, read the [Tool catalog](../../docs/guide/tools.md#the-runtime-server-ignition-runtime).
This page is for people who change the bundle.

## Contents

The bundle has 22 Tools, 3 Text Resources and no Prompts.

- 13 read Tools: `bundle_info`, `tag_browse`, `tag_query`, `tag_read`, `tag_get_config`,
  `udt_type_list`, `udt_type_get`, `alarm_shelved_list`, `historian_browse`,
  `historian_query_series`, `historian_query_aggregate`, `database_query_list` and `database_query`.
- 3 CONTROL Tools: `tag_write`, `alarm_shelve` and `alarm_unshelve`.
- 6 CONFIG Tools: `tag_update`, `tag_create`, `tag_copy`, `tag_delete`, `tag_move` and `tag_rename`.
- 3 Text Resources with the output schemas of `bundle_info`, `tag_browse` and `tag_read`.

The profiles in `contracts/profiles/` decide which Tools a Server Config offers: `readonly` has 13,
`operator` 16, `configurator` 19 and `full` 22. Every profile lists its Tools by name, never with `*`.

`deferred/` holds `alarm_status` and `alarm_journal`, which are switched off. See
[deferred/README.md](deferred/README.md).

## Layout and coding rules

```text
project/com.inductiveautomation.mcp/
  tools/<name>/resource.json                parameters and description
  tools/<name>/onToolCalled.py              the handler
  resources/contracts/<name>/resource.json  a Text Resource
  resources/contracts/<name>/data.bin       its content
```

- Handlers are Jython 2.7 and indented with tabs. Use `unicode`, `long`, `basestring` and Java
  classes. Python 3 syntax does not work.
- Each handler is self-contained. There are no shared modules, so helpers such as `toolError` and
  `encodeNulls` are copied into every handler.
- Handlers call Ignition's `system.*` functions and return MCP structured output, or an error with the
  shared codes from `contracts/shared/error-codes.json`.

## Output contract

The pinned MCP Module returns real `structuredContent` and `isError`, but does not publish each Tool's
`outputSchema` in `tools/list` (D27). The JSON Schemas in `contracts/schemas/` are therefore the
output contract, and the tests check handler output against them. A text answer is never a fallback
for structured output.

The Module also drops JSON `null` values inside objects. Handlers use the `ignition-null-v1` encoding
(D28):

- `null` becomes `{"$ignition":"null"}`.
- Lists and ordinary objects are encoded item by item.
- An object that has the reserved key `$ignition` is written as
  `{"$ignition":"object","entries":[[key, encodedValue], ...]}`. Decode it once to get the original.

This covers Tag values, qualities, timestamps and an unknown module build. The REST server does not
use it.

## Write Tools

The write rules come from D30. Each Tool's contract is in `contracts/tools/runtime/`.

| Tool | Class | Destructive | Precondition token | Target the allowlist checks |
| --- | --- | --- | --- | --- |
| `tag_write` | CONTROL | no | none | the Tag. The result per item is the Ignition write result |
| `alarm_shelve`, `alarm_unshelve` | CONTROL | no | none | the exact alarm path. `alarm_shelved_list` shows the result |
| `tag_update` | CONFIG | no | Tag config fingerprint | the Tag |
| `tag_create` | CONFIG | no | none. An existing target is `conflict` | the Tag |
| `tag_copy` | CONFIG | no | none. An existing destination is `conflict` | the destination |
| `tag_delete` | CONFIG | yes | Tag config fingerprint | the Tag |
| `tag_move` | CONFIG | yes | Tag config fingerprint of the source | the source and the destination |
| `tag_rename` | CONFIG | no | Tag config fingerprint | the new path |

Every write Tool reads the Runtime Target Policy before it acts, and refuses with
`operation_disabled` when the policy is missing, unreadable, invalid or larger than 32 KiB. The policy
is the Tag `[IgnitionMCPPolicy]RuntimeTargetPolicy`, with a companion Int4 Tag
`RuntimeTargetPolicyLength` that is read first so an oversized policy is never loaded. Only
`ignition-mcp setup` writes it. The policy holds the per-Tool allowlists, the service
identity used as the audit actor, the audit mode, the per-call item limits and the `alarm_shelve`
duration limit. Its fields are listed in the
[Configuration reference](../../docs/guide/configuration.md#runtime-target-policy).

Rules every write handler follows, each covered by its recorded Jython test fixtures (D29):

- **Preflight.** Input limits, the reserved-provider check and the allowlist are checked for every item
  before any item runs. One bad item refuses the whole batch. After that, items run one at a time,
  each with its own result, and nothing is rolled back.
- **Reserved provider.** A target in the `IgnitionMCPPolicy` provider is refused with
  `permission_denied` before the allowlist is checked, even under `*`. Only the provider part is
  compared. For a move, copy or rename, both ends are checked.
- **UDT definitions.** A CONFIG write reaches `[provider]_types_/...` only when the policy lists an
  explicit `_types_` entry. `*` does not cover it.
- **Fixed settings.** `references=ABORT`, `allowInvalidReferences=false` and `collisionPolicy=Abort`
  are not parameters.
- **Observed state.** Each Tool reads its targets again, within a bounded budget, and reports what it
  saw. That read never decides success. A read that cannot stay within its bound is reported as a
  `limit_exceeded` observed error instead of being loaded.
- **Audit.** `system.util.audit` runs in the policy's audit mode with the policy's service identity as
  actor. In `required` mode the handler checks the audit profile first and refuses with
  `operation_disabled` when it is unavailable. A refused item gets a `decision` audit row. If the
  result audit write fails, the outcome stands and the answer says `auditRecorded=false` (D18).
- **No retry.** Each write is sent once. An item whose Ignition result is itself uncertain is
  `outcome_unknown`, and the items after it are `not_executed`.

## Named Query registry

`database_query_list` and `database_query` read their approved queries from the environment variable
`IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON` of the Gateway process (D14). The caller cannot choose the
project, the Named Query path or the datasource, and cannot send SQL. The format is in the
[Configuration reference](../../docs/guide/configuration.md#named-query-registry). A missing variable is
an empty registry. A malformed one makes both Tools fail.

## Versions and releases

`BUNDLE_VERSION` in this folder is the only version number. The validator fails unless the
`bundle_info` handler's `bundleVersion` value and the project's ownership mark both equal it. The
ownership mark is the last line of the `project.json` description:

```text
ignition-mcp-managed: product=ignition-runtime-bundle; bundle=<bundleVersion>
```

`ignition-mcp status` uses that mark to tell a project it deployed from a project someone else made.
`RESOURCE_SCHEMA_VERSION` tracks changes to the resource file format.

Build a release (D21):

```bash
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision <40-hex git SHA> \
  --evidence-dir tests/compatibility/evidence
```

It writes three files: `ignition-runtime-bundle-<bundleVersion>.zip`, `.manifest.json` and a
`.sha256` file that `sha256sum -c` accepts. CI builds them twice and compares the bytes.

- The release ZIP writes the Git revision into the `bundle_info` handler, in place of
  `__BUNDLE_SOURCE_REVISION__`, so a deployed bundle reports `bundleSourceRevision`. A plain `build`
  reports `UNSTAMPED`.
- The manifest lists the bundle's Tools, Resources and Prompts, the Tools per profile, each Tool's
  Ignition requirements, and `testedTuples`, built only from evidence rows that pass the
  `tooling.compat` validator. `release` runs the validator itself and refuses any production
  compatibility claim.
- The live tests deploy exactly the release ZIP, and each evidence row records its SHA-256 as
  `deployedBundleSha256`.

## After you change the bundle

```bash
# after editing a schema in contracts/schemas/ that is also a Text Resource
uv run --no-sync python -m tooling.native.sync_schemas

uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime.zip
uv run --no-sync python -m tooling.contracts.lint
```

The handler tests run each `onToolCalled.py` under Jython 2.7.4 and need Java 11 (D29). See
`tooling/native/jython_runner/README.md`.

An empty Prompt list may leave out the prompts capability in `initialize`. The tests record
`prompts/list` as `NOT_APPLICABLE` in that case, not as a pass.
