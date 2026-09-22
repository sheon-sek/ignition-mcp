# Recorded Jython runner

This test utility executes an unchanged Runtime Bundle `onToolCalled.py` under Jython 2.7.4, replays a recorded sequence of `system.*` calls, and validates the handler's result against the schema selected by the Tool contract.

It is a fast recorded-shape check. It does not replace the D23 live-Gateway tests or claim that fixture adapters are Inductive Automation classes.

## Fixture format

A `schemaVersion: 2` fixture lists the native calls the handler must make, in order:

```json
{
  "schemaVersion": 2,
  "tool": "tag_write",
  "parameterOrder": ["writes", "timeout"],
  "arguments": {"writes": [{"path": "[default]AHU/PV", "value": 22}], "timeout": 5000},
  "calls": [
    {"target": "system.tag.readBlocking", "args": [["[p]Len"], 5000],
     "result": {"kind": "qualified-values", "items": [{"quality": {"name": "Good", "code": 192,
                "level": "Good", "good": true, "diagnosticMessage": null}, "value": 123}]}},
    {"target": "system.util.audit", "kwargs": {"action": "ignition-mcp.tag_write"},
     "result": {"kind": "none"}}
  ]
}
```

Matching rules:

- The handler must make exactly the recorded calls, in order. An unrecorded call, a
  recorded call it never makes, a mismatched positional `args` list, or a mismatched
  recorded `kwargs` value fails the run. That is what lets a fixture assert the
  negative half of a behavior: the policy Tag was never read, no write was dispatched.
- `args` is compared exactly; `kwargs` compares only the recorded keys (so a generated
  value such as a correlation ID inside `actionValue` stays out of the fixture).
- Result kinds: `none` (returns `None`), `qualified-values` (`system.tag.readBlocking`),
  `quality-codes` (`system.tag.writeBlocking`), `results` (`system.tag.query`),
  `resource` / `missing` (`system.config.getResource`), and `raise` (a
  `java.lang.RuntimeException`, the shape a handler's `except (Exception, JavaException)`
  catches).
- `system.util.jsonEncode`, `jsonDecode` and `getLogger` are real implementations in the
  Jython process; only Gateway state is recorded.
- A recorded *value* may name an explicit native shape with a reserved `nativeType` key,
  which is how a fixture replays a shape JSON cannot express: `Dataset`
  (`{"columns": [...], "rows": [[...]]}`, the object whose
  `getColumnCount`/`getColumnName`/`getRowCount`/`getValueAt` a handler reads), `TagPath`,
  `iteritems-object`
  (`{"entries": [[key, value], ...]}`, an object whose only mapping interface is
  `iteritems`), `java-array` (`{"items": [...]}`, a real Java array), and `native-object`
  (`{"class": ..., "text": ..., "repeat": ...}`, a native object with no container
  interface; `repeat` multiplies the text). Decoding is recursive, and every other JSON
  object or array becomes the Jython dict or list the Gateway would return.

`run_recorded_tool(tool, fixture)` requires a successful result and validates
`structuredContent` against the contract's `outputSchema`.
`run_recorded_tool_error(tool, fixture, expected_code=...)` requires a canonical D06 Tool
Error and returns the error object (including its `details`), so a test can assert
*which* refusal a batch produced.

## Requirements

- Java 11. Set `JYTHON_RUNNER_JAVA` to its `java` executable when it is not the default.
- Network access on the first run. The runner downloads the Maven Central JAR to `build/cache/`, checks its size and SHA-256, and verifies it again on every use. The repository's existing `build/` ignore rule keeps the artifact out of Git.
- The repository's locked Python environment, including `jsonschema`.

Fixtures are committed repository files under `fixtures/`. D29 confines the Jython process to repository-controlled inputs, so a fixture path that resolves outside that directory (including through a symlink) is rejected, as is a fixture over 256 KiB. Both checks run before Java starts; a rejection is a loud error, never a skip.

Run the proof through pytest:

```bash
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 \
  python -m pytest -q tooling/native/jython_runner/tests/test_tag_query.py
```

Run the same fixture from the command line:

```bash
uv run --locked --package ignition-rest-mcp \
  python -m tooling.native.jython_runner tag_query \
  tooling/native/jython_runner/fixtures/tag_query-full-path-continuation.json
```

Set `IGNITION_MCP_JYTHON_CACHE` only when the default cache location is unsuitable. CI must not skip the test when Java or the verified JAR is unavailable.
