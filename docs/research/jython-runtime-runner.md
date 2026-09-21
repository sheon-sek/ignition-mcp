# Local Jython runner for Runtime Tool handlers

Date: 2026-09-21

## Question

What existing tool can execute the unchanged Runtime Bundle `onToolCalled.py` files under Jython 2.7 in local tests and CI, with recorded `system.tag.*` results, while a Python test validates `structuredContent` against the repository's Draft 2020-12 output schemas?

The immediate target is bucket C in the G3 failure triage. Nine live runs failed in Jython or Java-object normalization. The smallest proof needs to execute `tag_query/onToolCalled.py` with a result whose item has `fullPath` and whose result object has `continuationPoint`. It must then validate the returned value against `contracts/schemas/tag-query.output.schema.json`.

## Recommendation

Use the stable `org.python:jython-standalone:2.7.4` JAR from Maven Central as a test-only interpreter. Download it into a gitignored cache, verify its published SHA-256, and invoke it with `java -jar`. Keep fixture selection, process control, JSON parsing, and Draft 2020-12 schema validation in pytest.

Pin these initial inputs:

- Artifact: `https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/jython-standalone-2.7.4.jar`
- SHA-256: `1fba1769effcc8b19f5e10436bc8274a158ce988559f257927c24c73bb137f3c`, from [Maven Central's checksum file](https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/jython-standalone-2.7.4.jar.sha256)
- Size: 50,453,449 bytes, as listed in the [Maven Central directory](https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/)
- JVM: Java 11 for the first proof. Jython lists Java 8 and 11 as the supported runtimes for stable 2.7.4. [Jython downloads](https://www.jython.org/download.html)

This option reuses the real interpreter and its Java bridge. The repository only needs a small adapter around an existing runtime:

1. A Jython launcher loads the unchanged handler and installs a recorded `system` object with `tag.query`, `util.jsonEncode`, and `util.getLogger`.
2. Fixtures use Java collections such as `java.util.LinkedHashMap` and `java.util.ArrayList` where the Gateway returns Java mappings and lists. A result wrapper supplies iteration and the recorded `continuationPoint` attribute. A path fixture preserves the recorded string form of the Gateway's `TagPath`.
3. The launcher emits one JSON document. The existing Python environment parses it and runs `jsonschema.Draft202012Validator`, which the repository already uses.

The minimal `tag_query` proof is little glue. It does not need an Ignition Gateway, Docker, Maven, Gradle, an installed Jython distribution, or a custom Java build. Later fixtures for `QualifiedValue` can implement only the methods the handler calls. A generic Java enum from the JDK can exercise the Java-enum normalization branch. Exact Ignition class identity, especially the real `DataType` enum, remains the responsibility of the existing live-Gateway matrix unless Inductive Automation's SDK artifact redistribution terms are reviewed and accepted.

Do not install `ignition-api` or `incendium` into this runner. They solve editor and reusable-script-library problems, not recorded Gateway execution.

## Option comparison

| Option | Maintenance and license | `system.tag.*` behavior | Java object fidelity | JVM and CI cost | Glue still required | Fit |
|---|---|---|---|---|---|---|
| Maven Central `jython-standalone` 2.7.4 | Stable release from August 2024. Upstream is still active, with 2.7.5 beta published in August 2026, but says only limited effort is available for the 2.7 line. PSF License v2 plus bundled component notices, compatible with GPL-3.0. | None. The runner injects recorded calls and return values. | Real Jython conversion and `java.*` behavior. No Ignition classes unless separate Ignition SDK JARs are added. | One 48.1 MiB JAR and Java 11. No container daemon or Gateway startup. | Small for one Tool. Bounded and incremental for more recorded call shapes. | **Recommended.** It tests the failure boundary that static validation misses without copying an interpreter into the repo. |
| `ignition-api` | Active community project. Release `8.3.9.post1` was published on 2026-08-26. MIT license, compatible with GPL-3.0. | Functions are executable documentation and placeholders. For example, `system.tag.query` prints its arguments and returns an empty `Results()`. It does not reproduce Gateway tag queries. | Python facsimiles of Ignition names, not the Gateway's Java classes. It cannot prove `java.util.Map`, `TagPath`, `QualifiedValue`, or Java enum behavior. | CPython 2.7.18 is the documented runtime. Adding it to Jython does not remove the need for Jython or fixtures. | The same call interception and recorded result work, plus adapting CPython-oriented placeholders to Jython. | Reject as the runner. It may remain useful for IDE completion or as a catalog of names. |
| `incendium` | Active community project. Latest release is `2026.6.1`, published 2026-06-03. MIT license, compatible with GPL-3.0. | It extends and wraps Ignition scripting functions. It assumes Ignition supplies `system.*`; it is not a fake Gateway. | Under Jython its packaging installs `typing`, not `ignition-api`, so it relies on the real Ignition environment for Java and `system.*` objects. | Its own instructions require Java 17 and Jython 2.7.4. Installing it adds a package but no execution seam for these handlers. | All runner and recorded-object glue would still be required. Its utilities are unrelated to the handlers under test. | Reject as the runner. It is an application library for scripts running in Ignition. |
| Prebuilt generic Jython Docker image | No current Jython entry exists in Docker's Official Images source of truth. Search results expose community images, but no maintained, version-pinned image with upstream provenance comparable to Maven Central was found. The Jython component is GPL-compatible; every base-image layer would need its own license and update review. | None. A generic image only packages a JVM and Jython. | Same as the standalone JAR. It still lacks Ignition classes. | Docker pull, image cache, container startup, and architecture support, with no reduction in runner code. | The same fixtures, launcher, and schema bridge as the JAR option. | Reject. Pinning the JAR is smaller, easier to verify, and closer to upstream. |
| Official Ignition image and existing live harness | Inductive Automation maintains the `inductiveautomation/ignition` image. The repository already pins 8.3.8 and 8.3.9. Ignition is proprietary and requires explicit EULA acceptance, so it is suitable as an external test tool but not as a GPL-3.0 dependency redistributed with this repository. | Exact Gateway implementations and scope. The deployed MCP bundle already invokes the real handler through the official Module. | Exact `TagPath`, `QualifiedValue`, `DataType`, `Results`, and Gateway-patched Jython behavior for the pinned build. | Highest. Docker, image pull, Gateway boot, module installation, project deployment, memory allocation, and fixture provisioning. The current phase 2 compose file allocates 1536 MiB to the Gateway and also starts MariaDB. | Little for an end-to-end MCP call because the harness exists. Substituting recorded `system.tag.*` objects inside the Gateway would require a test-only Gateway script or module seam. | Retain for integration and exact-class confirmation. Do not use it as the fast recorded-object runner. |

## Evidence by option

### Jython standalone 2.7.4

Jython publishes the standalone JAR specifically to run without an installation or to place on a Java application's class path. The same page identifies 2.7.4 as the current stable version and Java 8 and 11 as its supported JVMs. [Jython downloads](https://www.jython.org/download.html) Maven Central publishes the JAR, POM, signatures, and SHA-256 and SHA-512 checksum files. [Maven Central 2.7.4 directory](https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/)

Ignition 8.3.8 updated its embedded interpreter from Jython 2.7.3 to 2.7.4, so the stable standalone release matches the interpreter version in the repository's required Gateway row. [Ignition 8.3 release notes](https://docs.inductiveautomation.com/docs/8.3/new-in-this-version) Inductive Automation notes that it maintains its own Jython codebase and applies fixes when needed. The same minor version therefore reduces drift but does not prove byte-for-byte equivalence with the Gateway build. [Python in Ignition](https://support.inductiveautomation.com/hc/en-us/articles/360056397252-Python-In-Ignition)

Maintenance is active but limited. Jython says the 2.7 line exists for continuity, Python 2 is unsupported by the PSF, and only a small amount of effort is available. [Jython repository](https://github.com/jython/jython) A 2.7.5 beta appeared in August 2026, but using it would lose the match to Ignition 8.3.8. [Jython news](https://www.jython.org/news.html)

There is a security caveat. Jython's current `NEWS` records CVE-2026-5598 in Bouncy Castle and CVE-2023-34462/CVE-2022-41915 in Netty as defects in 2.7.4 fixed on the 2.7.5 development line. [Jython `NEWS`](https://github.com/jython/jython/blob/master/NEWS) The proposed runner must not listen on a socket or process untrusted scripts or data. CI should treat the JAR as a test tool, review the pin when 2.7.5 becomes stable or Ignition changes its embedded version, and allow security policy to disable the runner if carrying the vulnerable shaded dependencies is unacceptable even without those code paths.

The Jython license grants use, modification, and redistribution under PSF License v2, with notices and a change summary required for derivatives. Bundled supporting libraries include Apache-licensed components. [Jython license](https://github.com/jython/jython/blob/v2.7.4/LICENSE.txt) The Free Software Foundation lists Python 2.0.1 and newer licenses as GPL-compatible and states that Apache 2.0 is compatible with GPLv3. [FSF license list](https://www.gnu.org/licenses/license-list.html) Preserve upstream license and notice files if the cache is ever redistributed. A download-only cache should remain outside release artifacts.

Jython supplies the part the current static validator cannot test: Python 2.7 execution plus Java imports, overload selection, `isinstance` against Java interfaces, collection proxies, Java exception handling, and `unicode()` conversion of Java objects. Ignition's manual confirms that Ignition scripting uses Jython and can import Java classes as Python modules. [Ignition libraries](https://docs.inductiveautomation.com/docs/8.3/platform/scripting/python-scripting/libraries)

It does not supply `system`, `builder`, or Ignition SDK classes. The fixture adapter must provide the first two. Standard Java collections cover the `Map` and `List` branches without custom Java. Recorded wrappers cover the attributes and methods read by a handler. If a test must prove the exact Ignition class rather than its Jython behavior, run it in the live Gateway. Inductive Automation publishes Javadocs and Maven-based SDK examples for `TagPath`, `BasicQualifiedValue`, and the `DataType` enum, but adding SDK JARs would introduce a larger dependency graph and a separate license review. [Ignition SDK examples](https://github.com/inductiveautomation/ignition-sdk-examples), [DataType Javadoc](https://sdk.inductiveautomation.com/javadoc/ignition83/8.3.7-SNAPSHOT/com/inductiveautomation/ignition/common/sqltags/model/types/DataType.html), [BasicQualifiedValue Javadoc](https://sdk.inductiveautomation.com/javadoc/ignition81/8.1.10/com/inductiveautomation/ignition/common/model/values/BasicQualifiedValue.html)

### `ignition-api`

`ignition-api` describes itself as a Python package for code completion. It requires CPython 2.7.18 and is community-maintained, not an Inductive Automation product. [Project README](https://github.com/ignition-devs/ignition-api/tree/8.3#readme) PyPI shows regular releases through `8.3.9.post1` on 2026-08-26. [PyPI release history](https://pypi.org/project/ignition-api/) The repository uses the MIT license. [Project license](https://github.com/ignition-devs/ignition-api/blob/8.3/LICENSE)

The implementation is intentionally synthetic. Its `system.tag.query` logs the supplied values and returns an empty `Results()` object. `getConfiguration` returns a fixed example tree. [Tag implementation](https://github.com/ignition-devs/ignition-api/blob/8.3/src/system/tag.py#L252-L397) Its `system.util.jsonEncode` delegates to Python's `json.dumps`, and `getLogger` returns a package-defined placeholder. [Utility implementation](https://github.com/ignition-devs/ignition-api/blob/8.3/src/system/util.py)

Those objects are Python implementations named after Ignition classes. They do not reproduce a Gateway's Java object graph. Installing the package would not catch the bucket C failures caused by `java.util.Map`, native path objects, or a Java `DataType` enum. Replacing its canned returns with recorded returns would amount to writing the same fixture seam the standalone Jython runner needs, while executing against the wrong interpreter if used as documented.

### `incendium`

`incendium` describes itself as a package that extends and wraps the Ignition Scripting API. Its current README supports Jython 2.7.4 and Java 17. [Project README](https://github.com/ignition-devs/incendium#readme) GitHub lists release `2026.6.1` on 2026-06-03. [Project releases](https://github.com/ignition-devs/incendium/releases) The package uses the MIT license. [Project license](https://github.com/ignition-devs/incendium/blob/main/LICENSE)

Its packaging makes the boundary explicit. On CPython it depends on `ignition-api`; on Jython it installs only `typing`. [Project `setup.py`](https://github.com/ignition-devs/incendium/blob/main/setup.py#L43-L47) The Jython installation instructions copy `incendium` into an existing Ignition Gateway. The package expects the Gateway to provide `system.*` and the Ignition Java classes. It neither records nor simulates `system.tag.*` results, and the Runtime Bundle handlers do not use its helper APIs. It adds no useful execution capability for this task.

### Generic Jython container images

Docker states that the `docker-library/official-images` repository is the source of truth for current Official Images, and that removed tags may remain on Docker Hub without maintenance. [Docker Official Images repository](https://github.com/docker-library/official-images) There is no current `library/jython` definition. Docker Hub search exposes community images such as `bertjwregeer/jython:latest`, but the accessible listing provides an unversioned tag and no source or maintenance contract. [Docker Hub layer listing](https://hub.docker.com/layers/bertjwregeer/jython/latest/images/sha256-48d0bc759da47fe149cbf2805b1d2fbd7d7263ed7dc4e6e28ca8561b19b58436)

A generic container would package the same JAR and a JVM. It would not add `system.tag.*`, Ignition classes, fixture capture, output transport, or JSON Schema validation. It also replaces one upstream checksum with an image manifest plus base layers. The container has more supply-chain and CI cost and no functional gain for this runner.

### Ignition's own runtime

The official Gateway is the only surveyed option that supplies exact `system.tag.*` behavior and real Ignition Java types. The Ignition documentation identifies `fullPath` as a `BasicTagPath`, describes `QualifiedValue`, and documents a result continuation point. [Scripting object reference](https://docs.inductiveautomation.com/docs/8.3/appendix/reference-pages/scripting-object-reference) `system.tag.query` runs in Gateway scope and returns a `Results` object; its continuation example reads `results.continuationPoint`. [system.tag.query](https://docs.inductiveautomation.com/docs/8.1/appendix/scripting-functions/system-tag/system-tag-query)

The Designer Script Console is not a CI runner. It is an interactive terminal available only in the Designer. Gateway-scoped work runs elsewhere and logs to the Gateway. [Script Console](https://docs.inductiveautomation.com/docs/8.3/platform/designer/designer-tools/script-console) A Gateway message handler can return results to another scope, but it requires a project resource and message transport. [Gateway message scripts](https://docs.inductiveautomation.com/docs/8.3/platform/scripting/scripting-in-ignition/gateway-event-scripts) That is more glue than invoking the already deployed MCP Tool, and neither path replaces native results with recorded objects without adding another test-only injection point.

The repository's existing live harness remains valuable because it tests the official Module, Gateway-patched Jython, exact Java classes, script scope, and wire serialization together. It is poorly suited to a fast unit seam. The current phase 2 setup pulls exact images, starts a 1536 MiB Gateway plus MariaDB, installs the Module and project, provisions fixtures, and waits for readiness. Re-running that stack for each recorded normalization case would preserve the cost that caused the nine-run feedback loop.

Ignition's image and SDK are under Inductive Automation's license rather than the repository's GPL. The agreement says the software is licensed in object-code form, not sold, and restricts transfer and public distribution. [Inductive Automation software license](https://inductiveautomation.com/ignition/license) The official Docker workflows require `ACCEPT_IGNITION_EULA=Y`, as Inductive Automation's own examples show. [Inductive Automation Docker example](https://github.com/inductiveautomation/proveit-2026-app/blob/main/docker-compose.yml) Keep the image external to release artifacts and use it only under the accepted terms already governing the live harness.

## Proposed test boundary

The runner should test this sequence:

```text
pytest
  -> ensure cached JAR matches the pinned SHA-256
  -> start `java -jar jython-standalone-2.7.4.jar launcher.py fixture.json handler.py`
  -> launcher installs recorded `system` and `builder`
  -> launcher executes the unchanged `onToolCalled` entry point
  -> launcher prints one JSON result
  -> pytest validates `structuredContent` with the contract's output schema
```

Keep schema validation outside Jython. Draft 2020-12 support is already present in the Python test environment, while adding a JSON Schema implementation compatible with Jython 2.7 would create an unrelated dependency problem.

For the first `tag_query` fixture, record semantic data rather than pickling Gateway classes:

- one Java mapping with key `fullPath` and a path object whose `unicode()` value is the captured fully qualified path;
- a result iterable with the captured non-empty `continuationPoint`;
- the exact Tool arguments used by the failed probe;
- the expected normalized path and continuation string.

This catches the three `tag_query` defect classes in the triage: ignoring `fullPath`, reading continuation through the wrong member, and serializing an empty continuation instead of the Runtime null marker. The schema assertion catches the last defect without a source-text test.

Add later fixtures only when a handler consumes a new native shape. Use Java collections for Java collection branches. Model `QualifiedValue` methods explicitly. Exercise a Java enum before adding any type-specific string conversion. Preserve the captured native type as fixture metadata, and record the concrete class name only when the evidence contains it. A fixture must not silently claim exact type fidelity.

## Risks and controls

- **Interpreter drift.** Standalone 2.7.4 matches Ignition 8.3.8's declared Jython version, not Inductive Automation's private patch set or Java 17 host. The live harness remains the authority for exact integration.
- **Security debt.** Stable 2.7.4 contains shaded dependencies with published CVEs. Run it offline on repository-controlled scripts and fixtures, and revisit the pin when Jython or Ignition advances.
- **Fixture overreach.** A Python wrapper is not an Ignition Java class. Each fixture must state whether it tests a Java mechanism, a recorded public shape, or exact class identity. Only the live Gateway can make the last claim without approved SDK JARs.
- **Artifact availability.** Cache the JAR outside Git, verify the checksum on every use, and fail with a download instruction if network access is unavailable. Do not silently skip the tests in CI.
- **Output confusion.** Reserve stdout for the single machine-readable result. Send diagnostics to stderr and fail if the handler returns neither `structuredContent` nor a declared Tool error.

## Conclusion

No surveyed package already combines Jython execution, recorded Ignition calls, and contract validation. The standalone JAR is the existing component that removes the hard part, the interpreter and Java bridge. The remaining adapter is small for `tag_query` and grows only with recorded native shapes. `ignition-api` and `incendium` do not execute Gateway behavior, a generic Jython container adds cost without capability, and the real Gateway is too heavy for this feedback loop. Keep the live harness as the exact-runtime backstop.
