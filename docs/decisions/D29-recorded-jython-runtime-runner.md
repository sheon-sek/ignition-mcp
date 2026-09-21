# D29 — Recorded Jython runner for Runtime Tool handlers

**Status:** DECIDED — explicitly approved by the project owner on 2026-09-21 during Phase 4 preparation. The owner also confirmed that Java 11 is required for every local and CI test run, with no skip path.

## Context

`tooling/native/jython.py` checks a conservative Python 2.7 syntax subset, but it does not execute Runtime Tool handlers. Nine historical G2 live runs failed at the Jython and Java-object boundary. The failures involved native `fullPath` and `continuationPoint` members, Java mappings, and an Ignition `DataType` enum. Static source checks and CPython helper extraction cannot cover that boundary.

D23 requires real-Gateway L4 tests and warns against a large fake Ignition runtime. Those tests remain necessary for the official MCP Module, script scope, exact Ignition classes, and protocol serialization. Their startup and provisioning cost makes them a poor feedback loop for already-recorded native result shapes.

The option survey is recorded in [Local Jython runner for Runtime Tool handlers](../research/jython-runtime-runner.md).

## Decision

Add a test-only recorded-Jython runner under `tooling/native/jython_runner/`.

The runner will:

1. Execute the unchanged `onToolCalled.py` source with `org.python:jython-standalone:2.7.4`.
2. Pin the Maven Central JAR by URL, byte size, and SHA-256. It will store the download only in the gitignored `tooling/native/jython_runner/build/cache/` directory and verify the checksum on every use.
3. Require Java 11 for the baseline test environment. It will not install Jython or Java globally.
4. Inject only the recorded `system.*` calls, builder behavior, Java collections, attributes, and methods required by each fixture.
5. Carry a recorded native type label as fixture metadata. A fixture adapter must not claim to be the vendor class or an exact concrete implementation.
6. Emit JSON to the CPython test process. The CPython side will resolve the Tool's `outputSchema` from `contracts/tools/runtime/<tool>.contract.json` and validate `structuredContent` with `jsonschema.Draft202012Validator`.
7. Fail on an unavailable or invalid interpreter artifact, a handler error, malformed process output, or schema mismatch. CI must not silently skip the test.

The initial dependency identity is:

```text
coordinate  org.python:jython-standalone:2.7.4
url         https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/jython-standalone-2.7.4.jar
size        50453449 bytes
sha256      1fba1769effcc8b19f5e10436bc8274a158ce988559f257927c24c73bb137f3c
jvm         Java 11
```

The first proof will cover `tag_query` with one recorded result item whose `fullPath` has the captured `TagPath` string form and whose result iterable has a non-empty `continuationPoint`. Later fixtures should be added only for native shapes consumed by a handler.

## Boundary with existing decisions

This decision adds a recorded execution layer to D23. It does not replace L4, change a compatibility status, or certify a Gateway and Module tuple. The live harness remains authoritative for exact Ignition class identity, Inductive Automation's Jython patches, the official MCP Module, and MCP wire behavior.

The fixture layer is deliberately smaller than a fake Gateway. It does not implement Tag semantics, permissions, project resolution, persistence, or transport. It replays observed values at the `system.*` call boundary and tests how the unchanged handler processes them.

D27 still requires schema validation of Runtime structured results. D28's `ignition-null-v1` encoding remains the Runtime output contract. The runner selects the existing contract schema and adds no alternate output shape.

## Considered options

- `ignition-api` supplies executable documentation and canned Python returns for editor use. Its `system.tag.query` does not replay Gateway behavior or provide the Gateway's Java objects.
- `incendium` is a library for scripts that run inside Ignition. It expects Ignition to provide `system.*` and does not add a recorded execution seam.
- A generic Jython container packages the same interpreter and JVM but still needs the fixture launcher and schema bridge. No current Docker Official Image for Jython was found.
- The official Ignition image supplies exact behavior and classes. Keep it for L4. Using it for every recorded normalization case retains the startup, licensing, and provisioning costs this runner is meant to avoid.

## License and security

Jython's PSF License v2 and the licenses of its bundled components are compatible with GPL-3.0. The cache is not part of source or release artifacts. If the artifact is ever redistributed, its upstream licenses and notices must accompany it.

Jython 2.7.4 contains shaded Bouncy Castle and Netty versions with CVEs recorded on the 2.7.5 development line. The runner must execute only repository-controlled handlers and fixtures, must not listen on a network socket, and should run without network access after the verified download. Revisit the pin when Ignition changes its embedded Jython version or Jython publishes a stable maintenance release that addresses these CVEs.

## Consequences

- Runtime handler syntax, Java collection conversion, Java imports, and native-object normalization can fail in the ordinary test job before a live-Gateway run.
- Fixture adapters require small, explicit additions as Tools consume new native shapes.
- A passing recorded test proves behavior for the modeled shape under upstream Jython 2.7.4. It does not prove exact Gateway behavior or support a production compatibility claim.

## Configuration

```yaml
decision: D29
status: DECIDED

recorded_jython_runner:
  interpreter: org.python:jython-standalone:2.7.4
  sha256: 1fba1769effcc8b19f5e10436bc8274a158ce988559f257927c24c73bb137f3c
  java: 11
  cache: tooling/native/jython_runner/build/cache
  committed_artifact: false
  schema_source: contracts/tools/runtime/<tool>.contract.json
  exact_ignition_class_fidelity: false
  replaces_D23_L4: false
```
