# v1 operations runbook

> Chinese translation: [`runbook.zh-CN.md`](runbook.zh-CN.md)

This runbook covers the operator path for one `ignition-runtime-bundle` deployment: installing the MCP
Module, then diagnosing, planning, applying and verifying the deployment, upgrading the Bundle,
maintaining the Runtime Target Policy, enabling Mutation classes, and reading
`operation_diagnose` output when a call goes wrong.

Authority: D20 for the command set and its safety rules, the D26 Phase 6 amendment for what v1
covers, D27 and D28 for Runtime wire behavior, and D30 for the Mutation contract. Terms such as
Module install, Module upgrade and Bundle upgrade are defined in `CONTEXT.md`; this file uses them
with those meanings.

Every flag was checked against the shipped source in
`packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/setup_native/` and confirmed against each
subcommand's `--help` output. The `doctor`, `plan` and `verify` samples below are real run output. The
`install-module` examples show the format the command prints, read off its source.

## Prerequisites

You need these before any command runs.

| Item | Where it comes from |
| --- | --- |
| Gateway base URL | your deployment, for example `http://127.0.0.1:8088` |
| Ignition API token | an `ignition/api-token` resource on the Gateway, held by the operator in a `0600` file as one line |
| MCP Module file and its SHA-256 | the official Inductive Automation download channel. The repository pins `com.inductiveautomation.mcp` version `1.3.5.2026021307-SNAPSHOT`, build `2026021307`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`, recorded in `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| Bundle release: ZIP, manifest, checksum | `tooling.native.cli release`, described below |
| Runtime Target Policy document | written by the deployment owner, schema `contracts/shared/runtime-target-policy.schema.json` |
| Server Config permissions tree | written by the deployment owner, required when `apply` creates a Server Config or when the deployed one carries no tree to preserve |

The toolchain differs by platform; the deployment inputs above are shared.

| Toolchain item | Linux/macOS | Windows |
| --- | --- | --- |
| Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) | the [official installer](https://docs.astral.sh/uv/) or a package manager | `winget install --id=astral-sh.uv -e`, or the same official installer |
| Shell | a POSIX shell | PowerShell 7 — the D31 §6 checklist uses `-SkipHttpErrorCheck`, which needs 7 |
| Git for Windows | not needed | only for the `bash`-based checks: `tooling.ci.check_workflows` and the bundled bash wizard |
| Java 11 | only for the recorded-Jython tests (D29); neither server needs it | same |

Windows instructions in this runbook are marked **not run on Windows yet**; see [D31](../decisions/D31-windows-support-scope.md).

Build the release artifacts from a checkout:

```bash
uv run --no-sync python -m tooling.native.cli validate \
  --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
(cd dist/release && sha256sum -c "ignition-runtime-bundle-$V.sha256")
```

`release` writes three files named after `packages/ignition-runtime-bundle/BUNDLE_VERSION`:
`ignition-runtime-bundle-<version>.zip`, `.manifest.json` and `.sha256`. The checksum file is
`sha256sum -c` compatible. The builder is deterministic, so two runs on one revision produce
byte-identical archives, and `release` reads `tests/compatibility/evidence/` without rewriting it.

### Building the release on Windows

**Not run on Windows yet**; see [D31](../decisions/D31-windows-support-scope.md). Capture the revision
into a variable first and pass it as `--source-revision $rev`, and check the checksum with
`Get-FileHash` or `certutil` instead of `sha256sum -c`, comparing the value with the hash in the
`.sha256` file:

```powershell
$rev = git rev-parse HEAD
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli release `
  --project-dir packages/ignition-runtime-bundle/project `
  --out-dir dist/release `
  --source-revision $rev `
  --evidence-dir tests/compatibility/evidence
$V = Get-Content packages/ignition-runtime-bundle/BUNDLE_VERSION
Get-FileHash "dist/release/ignition-runtime-bundle-$V.zip" -Algorithm SHA256
```

## Environment and credential files

The `IGNITION_MCP_SETUP_*` variables are fallbacks, and a flag always wins over its variable.

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_SETUP_MCP_URL=$IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token
chmod 0600 ~/.config/ignition-mcp/gateway.token
```

On Windows (**not run on Windows yet**; see [D31](../decisions/D31-windows-support-scope.md)):

```powershell
$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_SETUP_MCP_URL = "$env:IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\ignition-mcp"
Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<your-ignition-api-token>"
```

The `0600` rule is not enforced on Windows, so the token file needs a filesystem ACL that grants read
access only to the service account.

A token file must be a regular non-symlink file with no group or other bits (mode `0600`) holding
exactly one non-empty line. A symlink, a loose mode, or zero or two token lines is a usage error and
exits 2. Prefer `--gateway-token-file` over `IGNITION_MCP_SETUP_GATEWAY_TOKEN`, so the credential
never sits in a process environment.

URLs must be absolute `http` or `https` with a host, and must not embed credentials. Plain HTTP to a
host that is not loopback is refused before any request, because the run would carry the API token
unencrypted. Use `https`, or pass `--allow-insecure-authorize` on a trusted lab network.

The commands take no repository path. `--bundle-manifest` is the only source of desired state, so
`contracts/` and `packages/ignition-runtime-bundle/` are never read at operation time.

## Install the MCP Module

`setup-native install-module` puts one trusted local `.modl` on a Gateway through the Gateway's own
module routes. It takes no bundle manifest: the Module id and build it reasons about come from the
archive's own `module.xml`. It downloads nothing.

```bash
ignition-mcp setup-native install-module \
  --file ~/downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token
```

Add `--accept-certificate` and `--accept-eula` to accept what the Module carries, `--restart` to
restart the Gateway and wait for the Module to come back, and `--acknowledge-upgrade` to install a
build higher than the installed one.

| Flag | Meaning |
| --- | --- |
| `--file PATH` | required. The local `.modl`. Nothing is uploaded until its hash matches |
| `--sha256 HEX` | required. The 64 hex digits this file must hash to. Case is folded to lowercase |
| `--accept-certificate` | accept the Module certificate. Without it the run prints the certificate and installs nothing |
| `--accept-eula` | accept the Module EULA. Without it the run says where to read the EULA and installs nothing |
| `--acknowledge-upgrade` | allow a Module build higher than the installed one |
| `--restart` | restart the Gateway after the install and wait for the Module to come back |

The command also shares `--gateway-url`, `--gateway-token-file`, `--timeout-seconds`,
`--allow-insecure-authorize` and `--json`.

The sequence is fixed, and every step is refused before it happens rather than undone afterwards:

1. Read the file, hash it, and open its `module.xml`. Every failure here exits 2 with no request sent:
   the hash does not match `--sha256`; the file passes the 67108864 byte bound for a `.modl`, or is
   not a ZIP, or holds no `module.xml`, or declares no `<id>` or `<version>`, or spells a version with
   no 10-digit build to compare; the basename is not a name this CLI will upload as `fileName`; or the
   archive's `<id>` is not `com.inductiveautomation.mcp`. This command installs the MCP Module and
   nothing else, so a foreign Module artifact is refused before any Gateway call.
2. Read `GET /data/api/v1/modules/healthy` in pages of 500, at most four pages, and find the identity
   the Gateway reports for that Module id. The same build installed is `NO CHANGE` with exit 0 and no
   upload. A newer build on the Gateway is refused with exit 1, and an installed build that cannot be
   compared is refused too rather than replaced blind. A higher build in the file needs
   `--acknowledge-upgrade`, or the run stops with exit 3. When the inventory cannot be read to its
   end, because the Gateway does not page through its reported total or answers with no item list, the
   run refuses with exit 1 before uploading instead of treating the Module as absent.
3. `POST /data/api/v1/modules/upload?fileName=...` with the raw bytes, where `fileName` is the file's
   basename. A Gateway that answers with a different `moduleId` is a refusal with exit 1, and nothing
   is installed.
4. Read `GET /data/api/v1/modules/certificate` and `GET /data/api/v1/modules/eula`. With either
   acceptance flag missing, the run prints the certificate subject, issuer and validity dates, says
   where the EULA can be read, and stops with exit 3. The archive is uploaded at that point, so that
   run leaves an upload with nothing installed. With the flags it posts each acceptance, and a `409`
   means the Gateway already holds one. A Module that carries no certificate or no EULA reports that
   step as skipped rather than asking for an acceptance.
5. `POST /data/api/v1/modules/install?moduleId=...`.
6. Without `--restart`, exit 0 with outcome `INSTALL` or `UPGRADE`, a pending-restart line, and the
   instruction to restart the Gateway and run `verify`. With `--restart`, confirm the restart with
   `confirm=true`, then poll `modules/healthy` every 5 seconds for up to 600 seconds until the Module
   is served with the installed build. A Gateway that never comes back with that build exits 1 and
   says the install is still waiting on a restart.

Text output is one line per step, `<MARKER> <step>: <detail>`, with the markers `DONE`, `SKIPPED`,
`NEEDS-ACK`, `REFUSED` and `FAILED`, then a summary line:

```console
install-module: INSTALL com.inductiveautomation.mcp build=2026021307 => exit 0
```

`--json` reports the same run as one object with `outcome`, `steps[]`, `moduleId`, `moduleVersion`,
`moduleBuild`, `installedBefore`, `restart`, and the `certificate` and `eula` views when the step
reached them. The certificate view carries only the subject, issuer, validity window and self-signed
flag, and no output carries a credential.

Two operator facts: `install-module` does not consult the compatibility matrix, because `doctor`
reports that, and it never accepts a certificate or an EULA without its own flag. A Module upgrade,
meaning a higher build, is the acknowledged path in step 2. The repository holds one Module build, so
v1 proves that logic with refusals and unit tests rather than with a live second build.

## Diagnose a deployment with `doctor`

`doctor` is read-only and ordered. It never waits for a starting Gateway: `initialize` gets exactly
one attempt, so run it again after a restart rather than making it a readiness probe.

The commands below assume the two exported variables and the token file from the previous section.
They pass no URL or credential on the command line, which keeps a secret out of the process list.

```bash
ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly
```

`doctor` and `verify` need an MCP endpoint. Pass `--mcp-url`, or pass `--server-config-name` and let
the command derive `<gateway-url>/data/mcp/<name>`.

Checks run in this order: `gateway-info`, `openapi-sha256`, `module-installed`, `bundle-project`,
`server-config-presence`, then one `capabilities.<name>` line for `server-config`, `project-import`,
`security-levels`, `api-token` and `designers`, then `mcp-initialize`, `inventory-tools`,
`inventory-resources`, `inventory-prompts`, `bundle-info`, and finally `compatibility`.

Statuses are `PASS`, `FAIL`, `SKIP`, `NOT_APPLICABLE` and `UNKNOWN`. A real run against a Gateway with
no such Server Config looks like this:

```console
FAIL           gateway-info: GET /data/api/v1/gateway-info returned HTTP 401: { "message":"Unauthorized", ... }
SKIP           openapi-sha256: Gateway did not answer /data/api/v1/gateway-info
...
FAIL           mcp-initialize: initialize returned HTTP 404: { "message":"MCP server not found: production", ... }
SKIP           inventory-tools: MCP session unavailable
doctor: 16 check(s) {"FAIL": 2, "SKIP": 14} => exit 1
```

Read the failures in this way:

| Report line | What it means | What to do |
| --- | --- | --- |
| `gateway-info` FAIL with HTTP 401 | the token was rejected | issue a token with read access, or fix the token file |
| `module-installed` FAIL | the MCP Module is absent or unhealthy | install it, restart, run `doctor` again |
| `capabilities.<name>` `NOT_APPLICABLE` or `SKIP` | the Gateway does not document that route, or the OpenAPI inventory was unavailable | the matching plan line is `BLOCKED`; do not expect that write |
| `bundle-project` FAIL `MARKER_INVALID` | a project of that name exists with a foreign or malformed ownership marker | `plan` refuses the takeover; rename or remove the foreign project |
| `bundle-project` FAIL `UNMANAGED_SAME_NAME` | a project of that name exists with no ownership marker | same refusal. The command never adopts someone else's project |
| `bundle-project` FAIL `NOT standalone` | the managed project is inheritable | make it standalone, or point `--bundle-project` at a new name |
| `inventory-tools` FAIL with `missing=[...]` or `extra=[...]` | the endpoint does not serve the manifest's profile inventory | inventories are exact. A superset and a subset both fail. Re-apply with the right `--profile` |
| `bundle-info` FAIL | the deployed bundle reports a different `bundleVersion`, or a different `bundleSourceRevision` when the manifest is stamped | run the Bundle upgrade below |
| `compatibility` UNKNOWN | the observed tuple has no matching `testedTuples` row, or an identity field is incomplete | expected on an untested Gateway. The command never upgrades a compatibility verdict |

`compatibility` matches on five fields: `gatewayVersion`, `gatewayBuild`, `mcpModuleVersion`,
`mcpModuleBuild` and `bundleVersion`. `gate` and `mcpModuleSha256` are not observable over these APIs,
so they never appear in the comparison.

## Read the plan

`plan` derives the intentions from the same observations and writes nothing.

```bash
ignition-mcp setup-native plan \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-0.7.0.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly \
  --policy-file policy.json \
  --json
```

Each line reads `<ACTION> <kind> <name>: <reason>`, and the last line is always
`No changes have been applied.`, including with `--json`. Actions are `CREATE`, `UPDATE`,
`NO CHANGE`, `BLOCKED` and `SKIP`. Lines come in the order `apply` executes them: `mcp-module`, then
the opt-in `security-level` and `runtime-token`, then `bundle-project`, `server-config`,
`runtime-policy`, then the detect-only `security-level` and `runtime-token` lines for planes this run
does not write.

The `mcp-module` line is never `CREATE`. The Module is a precondition, so the line is `NO CHANGE` when
a healthy Module is detected and `BLOCKED` when it is missing or its state cannot be read.

A `bundle-project` `UPDATE` names the D21 change class: `patch`, `minor`, `major` or `downgrade`.
`major` and `downgrade` carry the phrase `requires explicit acknowledgement in apply`, and `apply`
refuses them without `--acknowledge-upgrade`.

Any `BLOCKED` line makes `plan` exit 3. Fix the cause, usually the Module install or a foreign
project, and run it again.

## Apply the deployment

`apply` plans first, then writes. It requires three flags that every other command treats as optional.

```bash
ignition-mcp setup-native apply \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-0.7.0.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --profile configurator \
  --bundle-project ignition_runtime \
  --server-config-name production \
  --policy-file policy.json \
  --server-config-permissions-file permissions.json \
  --backup-dir /var/backups/ignition-mcp \
  --provision-security-levels \
  --create-runtime-token \
  --runtime-token-file ~/.config/ignition-mcp/runtime.token
```

The rules that decide what happens:

- `apply` needs `--server-config-name`, `--bundle-zip` and `--policy-file`. A missing one is a usage
  error, exit 2.
- `--bundle-zip` is hashed against `artifact.sha256` in the manifest before anything else. A mismatch
  exits 2 rather than planning against a different artifact.
- Any `BLOCKED` plan line stops the run before a single write, exit 3. An unacknowledged `major` or
  `downgrade` bundle change stops it the same way.
- Writes run in plan order: Security Level, Runtime API token, bundle Project, Server Config, Runtime
  Target Policy. A failed write stops the sequence, and nothing rolls back.
- There is no undo. `--backup-dir` is the only local copy: before `apply` overwrites a managed
  project it exports the deployed archive into that directory. Without it, the Gateway's own
  configuration backup is your only recovery path.
- A Server Config is created disabled, read back, then enabled with the signature that read returned.
  An update reconciles the Tool list in one write and preserves `enabled` and every other
  operator-held field. A Tool list is always explicit, never `*`. The permissions tree comes from
  `--server-config-permissions-file` when that file is supplied, otherwise from the deployed
  resource, and a Server Config with no tree available either way is a `BLOCKED` plan line.
- The policy document is validated and canonicalized before anything is written, and refused above
  32768 bytes. After the write, `apply` reads the served Tags back through the documented export
  route and repairs once with an idempotent re-import if the read-back disagrees.
- The CLI never echoes a credential. The created Runtime token goes only to the file you named,
  created with mode `0600`. A second run proves you still own that token by hashing the file's secret
  against the token hash the Gateway serves, so re-running is `NO CHANGE`, not a rotation.
- The opt-in flags refuse bad input at parse time: `--create-runtime-token` needs
  `--runtime-token-file`, and needs `--runtime-token-name` or `--server-config-name`; the three
  `--runtime-token-*` flags are refused without `--create-runtime-token`; `--security-level-name` is
  refused unless `--provision-security-levels` or `--create-runtime-token` is present.

Because the Module registers a Project's provider on the Project's own thread, an endpoint built from
a just-written Server Config can answer `initialize` with no capability. `apply` re-announces the same
approved document up to three times, reports each one as a `REFRESH server-config ...` line and in
`refreshes[]`, and only then judges the result. That leaves a `NO CHANGE` deployment afterwards.

`apply` ends by running the `verify` sequence and embedding its report, so a normal run prints the plan
lines, the write lines, a blank line, the verify lines, and a summary:

```console
apply: wrote=3 skipped=2 failed=0 => exit 0
```

Exit 1 means a write failed or verification failed. Exit 3 means the run wrote nothing.

## Verify a deployment

```bash
ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production \
  --profile readonly
```

The sequence is `endpoint-reachable`, `mcp-initialize`, `inventory-tools`, `inventory-resources`,
`inventory-prompts`, then one `resources-read <uri>` line per Text Resource in the profile inventory,
one `prompts-get <name>` line per Prompt, and `bundle-info`. A `PASS` on `endpoint-reachable` only
means the URL answered. Exit 0 requires every check to be `PASS` or `NOT_APPLICABLE`; an empty
Resource or Prompt inventory is `NOT_APPLICABLE`, not a failure.

Run `verify` after every Gateway restart, and after a Module install with `--restart`.

## Bundle upgrade

A Bundle upgrade replaces the managed Runtime Bundle Project with a newer bundle version. It is the
v1 upgrade path, per the D26 Phase 6 amendment.

1. Bump `packages/ignition-runtime-bundle/BUNDLE_VERSION`. `tooling.native` refuses a build whose
   `bundle_info` literal or project ownership marker disagrees with that file, so the version has one
   source.
2. Build and check the release, as shown under Prerequisites.
3. `doctor` with the new manifest. Expect `bundle-info` FAIL, because the deployed bundle still
   reports the old version, and expect `compatibility` to stay `UNKNOWN` until the new bundle is
   deployed and a `testedTuples` row matches its tuple.
4. `plan` with the new manifest and ZIP. Expect `UPDATE bundle-project ignition_runtime: redeploy
   managed bundle 0.7.0 -> 0.8.0 (minor)`.
5. `apply` with `--backup-dir` set. Add `--acknowledge-upgrade` only for a `major` change or a
   downgrade, which is how you deliberately roll a bundle back.
6. `verify`, then have the MCP client reconnect, because the Tool inventory may have changed.

The `UPDATE` replaces the whole managed Project. Anything you authored inside `ignition_runtime` by
hand is lost unless you exported it first, which is what `--backup-dir` is for.

## The Runtime Target Policy

The Runtime plane reads its Target allowlists from a deployment-owned document, not from the bundle.
It lives in the reserved Tag provider `IgnitionMCPPolicy` as two Tags:

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` holds the canonical JSON text;
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` holds its byte length, which the handler reads first
  so an over-cap document is refused without being materialized.

`setup-native apply` is the only supported writer, and the generic `config_resource_*` Tools refuse
the reserved provider whatever the Target allowlist says, so an MCP caller cannot move the policy.

A document is stored in canonical form: keys sorted, no whitespace. Byte-for-byte stability is what
lets `plan` compare its own SHA-256 with the served value and report `NO CHANGE`. Formatting the file
by hand is harmless, because the CLI re-canonicalizes it.

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "auditProfile": "MCP_AUDIT",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU/*"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU/*"]
  },
  "alarmShelveMaxSeconds": 3600,
  "tagUpdateMaxItems": 20
}
```

Rules, from `contracts/shared/runtime-target-policy.schema.json` and the CLI's shape check:

- `schemaVersion`, `allowlists`, `serviceIdentity` and `auditMode` are required.
- `schemaVersion` must be `1`.
- `allowlists` is keyed by Tool name, so a Tag allowlist cannot stand in for an Alarm allowlist. An
  absent key means no target for that Tool. Allowing everything needs an explicit `"*"`.
- `serviceIdentity` is a non-empty string, and it is the audit actor for Runtime Mutations. The caller
  cannot supply it.
- `auditMode` is `best_effort`, `required` or `off`. `auditProfile` is optional and non-empty.
- Item ceilings are bounded 1 to 100. `tagUpdateMaxItems`, `tagCreateMaxItems` and `tagCopyMaxItems`
  are checked by the CLI; the schema bounds `tagDeleteMaxItems`, `tagMoveMaxItems`,
  `tagRenameMaxItems`, `tagWriteMaxWrites` and `alarmMaxPaths` the same way. An absent ceiling means
  the 20-target project default.
- `alarmShelveMaxSeconds` is a positive integer, and the product hard maximum is 86400 seconds
  (D12). A deployment may lower it, never raise it.
- The canonical text must be at most 32768 bytes.

To change the policy, edit the file and run `plan` then `apply`. `plan` reports
`UPDATE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy: replace the served policy (...)` with
the byte count and the old and new SHA-256 prefixes, and `apply` confirms the change with a read-back.

A Runtime Mutation fails closed with `operation_disabled` when the document is missing, unreadable,
over the cap, or invalid, so an operator who removes the policy has disabled every Runtime Mutation
rather than opened them. Tag entries match at segment boundaries: `[default]AHU` authorizes
`[default]AHU/Temp` and never `[default]AHU2`.

## Enabling Mutation classes

Every Mutation class is disabled by default on both planes. The classes are `NONE` for reads, and
`CONFIG_MUTATION`, `CONTROL_MUTATION` and `ADMIN_MUTATION` for writes (D08,
`contracts/shared/mutation-classes.json`). No class is enabled by a hierarchy or a scope alone; you
turn on each layer.

Runtime plane, from `setup-native` and the Gateway:

1. `--profile` chooses the Tool list the Server Config advertises. `readonly` carries no Mutation.
   `operator` adds the `CONTROL` Tools `tag_write`, `alarm_shelve` and `alarm_unshelve`.
   `configurator` adds the `CONFIG` Tools `tag_update`, `tag_create`, `tag_copy`, `tag_delete`,
   `tag_move` and `tag_rename`. `full` adds both groups. There is no ADMIN Runtime profile (D26).
2. `--provision-security-levels` creates the dedicated Security Level for that profile, a leaf child
   of `Authenticated`, named `IgnitionMcpRuntime<Profile>` unless `--security-level-name` says
   otherwise. An existing level is never modified, and one with child levels is refused.
3. `--create-runtime-token` creates the Runtime API token granted exactly that level, and writes its
   secret once to your `--runtime-token-file`. On a plain-HTTP lab Gateway only, add
   `--runtime-token-insecure-channel`.
4. The Runtime Target Policy allowlists decide which targets those Tools may touch. An enabled Tool
   with no allowlist entry still fails.

REST plane, in `ignition-rest-mcp` environment configuration:

```bash
export IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
export IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,project_import
export IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["com.inductiveautomation.historian/historian-provider"]}'
export IGNITION_MCP_CONTROL_MUTATION_ENABLED=true    # alarm_pipeline_cancel
export IGNITION_MCP_ADMIN_MUTATION_ENABLED=true      # no v1 Tool is ADMIN
export IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true   # project_export, tag_config_export
```

The caller's credential also needs the matching scope (`ignition.config`, `ignition.control`,
`ignition.admin`) under D07, and only a verified principal may mutate: an `auth=none` deployment is
read-only. Sensitive exports are a separate switch from the class gates.

The deployment checks run in this order, and the first refusal decides the error code: class enabled,
operation allowlist, the operation's own Target-class rule (Refused resource types are denied even
under `*`), Target allowlist, capability. A Tool whose class is disabled is hidden from `tools/list` as
well as refused at call time with `operation_disabled`.

To turn one write on safely, allow one operation id and one Target, call it, read the state back, and
only then widen. D08 never auto-retries a write.

## Reading `operation_diagnose` output

`operation_diagnose` is a REST-plane read Tool. It takes one parameter, `correlationId`, which must be
an exact UUIDv7 string of 36 characters. A malformed identifier is `invalid_argument`, and a lookup is
never fuzzy or partial.

Where to find the identifier:

- every Tool error from `ignition-rest` carries a JSON body with `code`, `message` and
  `correlationId`;
- a structured log line for the call carries the same `correlationId` field
  (`IGNITION_MCP_LOG_FORMAT=json`);
- a Mutation's success payload carries it, for example `project_import` returns `correlationId` and
  `transactionId`.

The output fields and how to read them:

| Field | Meaning |
| --- | --- |
| `tool` | the Tool the record belongs to |
| `outcome` | `in_progress`, `succeeded`, `failed`, `outcome_unknown`, `cancelled`, `interrupted`. A record that is still `in_progress` has no `finishedAt`: the call is running, or the process died before it wrote a result |
| `errorCode` | the D06 taxonomy code, or `null` on a success |
| `startedAt`, `finishedAt` | the call window |
| `phases` | ordered `name` and `at` pairs, so you can see how far the operation got |
| `phasesTruncated` | `true` means the record hit the 32-entry ceiling, dropped its oldest phase and kept the newest. The drop is reported, never silent |
| `transactionId` | the D16 Project transaction, when the operation had one |
| `downstreamCorrelationId` | the identifier the server correlated on the Gateway side, when present |
| `auditResultMissing` | `true` means the audited operation has no result audit row, so the audit trail is incomplete for that call |

Three limits matter when you chase an old call:

- records are pruned on a budget, defaults `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS=72` and
  `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS=10000`. A `not_found` on a call from last week is expected;
- records are principal-scoped. Another principal's identifier answers `not_found`, which is
  deliberate, so the Tool is not an existence oracle. Only an `ignition.admin` caller sees across
  principals;
- if the record store was unavailable at call time, the call still ran and no record exists. The
  server logs an `operation_record_failure` line for it.

An `outcome_unknown` is a stop, not a retry signal. Verify the target's state with a read first, then
decide. D06 forbids automatic replay of an ambiguous Mutation.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `doctor` or `verify` finished with no `FAIL`; `plan` finished with no `BLOCKED`; `apply` wrote everything and verified; `install-module` installed or upgraded the Module, reported `NO CHANGE`, or completed an install whose restart is still pending |
| 1 | a check failed, a write failed, or a transport error occurred. `install-module` also answers 1 when the Gateway runs a newer build than the file, when the healthy-module inventory cannot be read to its end, when the Gateway refuses the upload, an acceptance or the install, or when the Gateway never came back with the installed build after `--restart` |
| 2 | usage error: bad flag, unreadable or invalid manifest, artifact hash mismatch, rejected credential file. `install-module` also answers 2 for an artifact problem that stops the run before any request: a SHA-256 mismatch, a file over the `.modl` bound, an archive with no readable `module.xml`, a version with no 10-digit build, and a Module id that is not `com.inductiveautomation.mcp` |
| 3 | the run needs a decision it was not given. `plan` reports a `BLOCKED` line and `apply` writes nothing. `install-module` installs nothing, and in the certificate or EULA case it has already uploaded the archive |

An interrupted run exits 2. An unexpected crash exits 1 and prints only the exception type, so a
credential cannot leak through a traceback.

## Windows

Windows is not broken, but it is not a supported platform. [D31](../decisions/D31-windows-support-scope.md)
records the scope, the declared limitations and a manual verification checklist that nobody has run yet.

The toolchain table in [Prerequisites](#prerequisites) and the PowerShell block in
[Environment and credential files](#environment-and-credential-files) are the Windows prerequisites and
start path, and both carry the same **not run on Windows yet** marker.

- **Checksums.** Windows has no built-in `sha256sum -c`. In `dist/release`, run
  `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` or
  `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`, and compare the result with the
  hash in `ignition-runtime-bundle-<version>.sha256`.
- **Wizard.** `scripts/deploy-runtime-bundle.sh` is a bash wizard, so it needs Git Bash or WSL. The
  `ignition-mcp setup-native doctor|plan|apply` commands are native and do not need it.
- **Credential files and the data directory.** On Windows, the `0600` token-file rule and the `0700`
  data-directory rule are not checked, and one WARNING is logged instead. Protect every token file and
  `IGNITION_MCP_DATA_DIR` with filesystem ACLs so that only the service account can read them.

## Known v1 limitations

- Three Alarm Tools are parked, and that is the known v1 gap on the Runtime plane. `alarm_status` and
  `alarm_journal` are held in `packages/ignition-runtime-bundle/deferred/` under the D12 Phase 2
  bounded-execution amendment. `alarm_acknowledge` is parked under the D12 Phase 4 amendment by the
  ticket #9 outcome, for the same reason: an exact-path `queryStatus` has no native limit or
  continuation, so it cannot bound the acknowledge pre-check or the Observed state. None of the three
  is discoverable or callable, the profiles do not list them, and re-enabling one needs a native
  pre-execution bound plus fresh live evidence.
- The pinned official MCP Module publishes `structuredContent` and `isError` but no Tool
  `outputSchema` (D27). Repo-owned schemas in `contracts/schemas/` remain the binding output contract.
- The Module drops object-valued JSON nulls, so Runtime output uses the `ignition-null-v1` encoding
  (D28): null becomes `{"$ignition":"null"}`, and an object carrying `$ignition` is escaped as
  `{"$ignition":"object","entries":[...]}`. This applies to the Runtime plane only.
- The 8.3.9 tuple carries `FAILED_NATIVE_BINDING` from G3, G4 and G5. The 8.3.8 tuple closes as
  `VERIFIED_WITH_LIMITATION` under D27. Neither is `SUPPORTED`, and no command in this runbook records
  one.
- The Runtime Bundle is 0.x.
- The repository pins one MCP Module build, so Module upgrade is covered by `install-module` refusal
  logic and unit tests, not by a live build-to-build upgrade.
- `doctor` and `verify` do not wait for a starting Gateway. Readiness waiting belongs to the live
  harness (`tests/harness/`).
