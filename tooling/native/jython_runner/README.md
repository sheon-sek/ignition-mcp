# Recorded Jython runner

This test utility executes an unchanged Runtime Bundle `onToolCalled.py` under Jython 2.7.4, injects a recorded `system.*` result, and validates successful `structuredContent` against the schema selected by the Tool contract.

It is a fast recorded-shape check. It does not replace the D23 live-Gateway tests or claim that fixture adapters are Inductive Automation classes.

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
