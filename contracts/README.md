# Contracts

`contracts/` describes what every Tool accepts and returns, in a form that does not depend on either
server's language. Both servers are written by hand and checked against these files. Nothing here is
generated into code, and nothing here runs.

| Path | Contents |
| --- | --- |
| `tools/rest/<tool>.contract.json` | One contract per REST Tool: its scope, write class, budget class, parameters, Target rules and output schema path. |
| `tools/runtime/<tool>.contract.json` | The same for each Runtime Tool. |
| `schemas/<tool>.output.schema.json` | The JSON Schema of each Tool's output. For Runtime Tools these are the only output contract, because the MCP Module does not publish one (D27). |
| `profiles/*.yaml` | The Tools, resources and prompts of each Runtime profile: `readonly`, `operator`, `configurator` and `full`. |
| `shared/` | Definitions both servers share: error codes, permission, write and budget classes, pagination, batch results, artifacts, the Runtime Target Policy schema, the bundle manifest schema and compatibility statuses. |
| `resources/` | Contracts for the Text Resources. |

The profile files use a subset of YAML that is also valid JSON, so the linter needs only the Python
standard library. Read them as YAML documents.

`tooling/contracts/lint.py` checks these files against each other and against the Tool lists. To add,
remove or rename a Tool, change its implementation, its contract, its schema and the lists in
`lint.py` together, then run:

```bash
uv run --no-sync python -m tooling.contracts.lint
```

## Refused resource types

`shared/refused-resource-types.json` (D30 §5) sorts every configuration resource type in a supported
Gateway API description into **allowed** or **refused** for the REST server's generic configuration
writes. A type in neither list is refused. A refused type answers `permission_denied` whatever the
Target allowlist says.

Two documents anchor the list. One is the full 8.3.8 API description in
`docs/ignition-8.3.8-openapi/`, with 57 resource types. The other is the resource type list derived
from the 8.3.9 description in `docs/ignition-8.3.9-openapi/`, with 56 types, all of them also in the
8.3.8 list. The 8.3.9 description itself is 12.7 MB and is not committed. The derived list records its
SHA-256 and the live run that captured it. A test classifies both, so a new Gateway version fails the
test until its types are sorted.
