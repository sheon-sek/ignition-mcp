# Operations runbook

> Chinese translation: [`runbook.zh-CN.md`](runbook.zh-CN.md)

This runbook is the reference for running a Runtime server deployment after you understand the
basics. It covers each `ignition-mcp setup-native` subcommand in detail, upgrades, the Runtime Target
Policy, turning on writes on both servers, and reading `operation_diagnose` output.

For a first installation, follow [Set up the Runtime server](../guide/setup-runtime.md) instead. It
walks through the same commands in order.

| I want to... | Section |
| --- | --- |
| know what I need before I start | [Prerequisites](#prerequisites) |
| set up the token file and shared values | [Environment and credential files](#environment-and-credential-files) |
| install or upgrade the MCP Module | [Install the MCP Module](#install-the-mcp-module) |
| understand a `doctor` line | [Diagnose with `doctor`](#diagnose-with-doctor) |
| understand a `plan` line | [Read the plan](#read-the-plan) |
| know exactly what `apply` does | [Apply the deployment](#apply-the-deployment) |
| check a deployment | [Verify a deployment](#verify-a-deployment) |
| deploy a newer bundle | [Upgrade the bundle](#upgrade-the-bundle) |
| change what the Runtime write Tools may touch | [The Runtime Target Policy](#the-runtime-target-policy) |
| turn on writes | [Turn on writes](#turn-on-writes) |
| find out what happened to a REST call | [Reading `operation_diagnose` output](#reading-operation_diagnose-output) |
| understand an exit code | [Exit codes](#exit-codes) |

In the examples, the bundle version is `0.7.0`, the Server Config is called `production`, and the
commands run from the repository folder. `ignition-mcp` is short for
`uv run --no-sync ignition-mcp`.

## Prerequisites

| Item | Where it comes from |
| --- | --- |
| The Gateway's address | your Gateway, for example `http://127.0.0.1:8088` |
| A Gateway API token with write access | the Gateway web interface, Security section. Save it in a file, see the next section |
| The MCP Module file and its SHA-256 | Inductive Automation. The repository pins version `1.3.5.2026021307-SNAPSHOT`, build `2026021307`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`. The record is `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| The bundle release: ZIP, manifest and checksum | built with `tooling.native.cli release`, below |
| A Runtime Target Policy file | written by you. See [The Runtime Target Policy](#the-runtime-target-policy) |
| A Server Config permissions file | written by you. Needed when `apply` creates a Server Config, or when the existing one has no permissions tree. See the [Configuration reference](../guide/configuration.md#server-config-permissions-file) |

The tools you need on your own computer:

| Tool | Linux or macOS | Windows |
| --- | --- | --- |
| Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) | the [official installer](https://docs.astral.sh/uv/) or a package manager. `uv` installs Python for you | `winget install --id=astral-sh.uv -e`, or the same official installer |
| A shell | any POSIX shell | PowerShell 7. The D31 section 6 checklist uses `-SkipHttpErrorCheck`, which needs version 7 |
| Git Bash or WSL | not needed | only for `bash` scripts: the deploy wizard and `tooling.ci.check_workflows` |
| Java 11 | only for the Jython tests (D29). Neither server needs it | same |

The Windows instructions in this runbook are marked **not run on Windows yet**. See
[D31](../decisions/D31-windows-support-scope.md).

Build the release from the repository folder:

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

`release` writes three files named after the version in
`packages/ignition-runtime-bundle/BUNDLE_VERSION`: `ignition-runtime-bundle-<version>.zip`,
`.manifest.json` and `.sha256`. Two builds of the same Git revision produce identical files.
`release` reads `tests/compatibility/evidence/` and does not change it.

### Building the release on Windows

**Not run on Windows yet.** See [D31](../decisions/D31-windows-support-scope.md). Store the Git
revision in a variable and pass it as `--source-revision $rev`. Check the checksum with `Get-FileHash`
or `certutil` instead of `sha256sum -c`, and compare the result with the hash in the `.sha256` file:

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

Every command takes its Gateway address and token from a flag or from an environment variable. A
flag wins over the variable.

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
export IGNITION_MCP_SETUP_MCP_URL=$IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' '<your-ignition-api-token>' > ~/.config/ignition-mcp/gateway.token
chmod 0600 ~/.config/ignition-mcp/gateway.token
```

On Windows (**not run on Windows yet**, see [D31](../decisions/D31-windows-support-scope.md)):

```powershell
$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_SETUP_MCP_URL = "$env:IGNITION_MCP_SETUP_GATEWAY_URL/data/mcp/production"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\ignition-mcp"
Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<your-ignition-api-token>"
```

Windows does not enforce the `0600` rule, so give the token file a filesystem ACL that lets only the
service account read it.

Rules for token files:

- A token file must be a regular file, not a symbolic link, with exactly one non-empty line.
- On Linux and macOS, only the owner may read it. That is mode `0600`.
- Anything else is a usage error with exit code 2.
- Prefer `--gateway-token-file` over the `IGNITION_MCP_SETUP_GATEWAY_TOKEN` variable, so the token does
  not sit in the process environment.

Rules for addresses:

- Addresses must be absolute `http` or `https` URLs with a host, and must not contain a user name or
  password.
- The commands refuse to send the token over plain `http` to another machine, because it would travel
  unencrypted. Use `https`, or pass `--allow-insecure-authorize` on a lab network you trust.

The commands do not read the repository at run time. `--bundle-manifest` is their only description
of what should be deployed, so you can copy the three release files to another machine and run the
commands there.

## Install the MCP Module

`setup-native install-module` installs one `.modl` file from your computer on the Gateway, through the
Gateway's own module routes. It downloads nothing and reads no manifest. It learns the Module id and
build from the file's own `module.xml`.

```bash
ignition-mcp setup-native install-module \
  --file ~/Downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token
```

| Flag | Meaning |
| --- | --- |
| `--file PATH` | Required. The `.modl` file. Nothing is uploaded until its hash matches. |
| `--sha256 HEX` | Required. The 64 hex digits the file must hash to. Upper or lower case both work. |
| `--accept-certificate` | Accept the Module's certificate. Without it, the command prints the certificate and installs nothing. |
| `--accept-eula` | Accept the Module's license. Without it, the command says where to read the license and installs nothing. |
| `--acknowledge-upgrade` | Allow a build newer than the installed one. |
| `--restart` | Restart the Gateway after the install and wait until the Module runs. |

It also takes `--gateway-url`, `--gateway-token-file`, `--timeout-seconds`,
`--allow-insecure-authorize` and `--json`.

The command works in a fixed order. Each check happens before the step it protects, so a refusal
leaves nothing to undo.

1. **Check the file.** It hashes the file and opens its `module.xml`. It stops with exit code 2, before
   any request, if the hash does not match `--sha256`, the file is larger than 64 MiB, is not a ZIP,
   has no `module.xml`, has no `<id>` or `<version>`, has a version without a 10-digit build, has a
   file name it will not upload, or is not the MCP Module, `com.inductiveautomation.mcp`.
2. **Compare with the Gateway.** It reads the Gateway's module list, 500 entries per page, at most
   four pages.
   - The same build is installed: `NO CHANGE`, exit 0, nothing uploaded.
   - A newer build is installed: refused, exit 1.
   - The installed build cannot be compared: refused, exit 1.
   - The file has a newer build: needs `--acknowledge-upgrade`, or the command stops with exit 3.
   - The module list cannot be read to the end: refused, exit 1, instead of assuming the Module is
     absent.
3. **Upload** the file. If the Gateway reports a different Module id, the command stops with exit 1
   and installs nothing.
4. **Accept the certificate and license.** Without both flags, the command prints the certificate's
   subject, issuer and validity dates, says where to read the license, and stops with exit 3. The file
   is already uploaded at that point, but nothing is installed. With the flags it accepts both. If
   the Module has no certificate or no license, that step is skipped.
5. **Install** the Module.
6. **Restart.** Without `--restart`, it exits 0 with `INSTALL` or `UPGRADE` and tells you to restart
   the Gateway and run `verify`. With `--restart`, it restarts the Gateway and checks every 5 seconds,
   for up to 10 minutes, until the Module runs with the new build. If that does not happen, it exits 1.

The text output has one line per step, `<MARKER> <step>: <detail>`, with the markers `DONE`,
`SKIPPED`, `NEEDS-ACK`, `REFUSED` and `FAILED`, then a summary:

```console
install-module: INSTALL com.inductiveautomation.mcp build=2026021307 => exit 0
```

With `--json` the same result is one JSON object with `outcome`, `steps[]`, `moduleId`,
`moduleVersion`, `moduleBuild`, `installedBefore`, `restart`, and the `certificate` and `eula` details
when the command reached them. No output contains a token.

`install-module` does not check the compatibility list. `doctor` does that. The repository holds only
one Module build, so the upgrade path is tested with unit tests, not with a real upgrade.

## Diagnose with `doctor`

`doctor` changes nothing. It runs a fixed list of checks and prints one line per check. It does not
wait for a Gateway that is starting up, so run it again after a restart.

```bash
ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production \
  --profile readonly
```

`doctor` and `verify` need the MCP endpoint address. Pass `--mcp-url`, or pass `--server-config-name`
and the command uses `<gateway-url>/data/mcp/<name>`. If the endpoint requires a login, pass the agent
token with `--mcp-token-file`. Leave that flag out before the first `apply`, when the token file does
not exist yet.

The checks run in this order: `gateway-info`, `openapi-sha256`, `module-installed`, `bundle-project`,
`server-config-presence`, one `capabilities.<name>` line each for `server-config`, `project-import`,
`security-levels`, `api-token` and `designers`, then `mcp-initialize`, `inventory-tools`,
`inventory-resources`, `inventory-prompts`, `bundle-info`, and `compatibility`.

Each line has a status: `PASS`, `FAIL`, `SKIP`, `NOT_APPLICABLE` or `UNKNOWN`. A run with a rejected
token and no Server Config looks like this:

```console
FAIL           gateway-info: GET /data/api/v1/gateway-info returned HTTP 401: { "message":"Unauthorized", ... }
SKIP           openapi-sha256: Gateway did not answer /data/api/v1/gateway-info
...
FAIL           mcp-initialize: initialize returned HTTP 404: { "message":"MCP server not found: production", ... }
SKIP           inventory-tools: MCP session unavailable
doctor: 16 check(s) {"FAIL": 2, "SKIP": 14} => exit 1
```

| Line | What it means | What to do |
| --- | --- | --- |
| `gateway-info` FAIL with HTTP 401 | The Gateway rejected the token. | Fix the token file, or give the token read access. |
| `module-installed` FAIL | The MCP Module is missing or not running. | Install it, restart, and run `doctor` again. |
| `bundle-project` PASS `ABSENT` | Nothing is deployed yet. | Expected before the first `apply`. |
| `bundle-project` FAIL `MARKER_INVALID` | A project with that name exists, with a damaged or foreign ownership mark. | `plan` refuses to replace it. Rename or remove that project, or use another `--bundle-project`. |
| `bundle-project` FAIL `UNMANAGED_SAME_NAME` | A project with that name exists that this tool did not create. | Same as above. The command never takes over a project it did not create. |
| `bundle-project` FAIL `NOT standalone` | The bundle project is marked inheritable. | Make it standalone, or use a new `--bundle-project` name. |
| `server-config-presence` FAIL | The Server Config does not exist yet. | Expected before the first `apply`. `plan` proposes `CREATE`. |
| `capabilities.<name>` `NOT_APPLICABLE` or `SKIP` | The Gateway's API description does not list that route, or could not be read. | The matching `plan` line is `BLOCKED`. That write cannot happen on this Gateway. |
| `mcp-initialize` FAIL with HTTP 401 or 403 | The endpoint needs a login. | Pass `--mcp-token-file` with the agent token. |
| `inventory-tools` FAIL with `missing=[...]` or `extra=[...]` | The endpoint offers a different Tool list than the profile you named. Too many and too few both fail. | Pass the profile you deployed, or `apply` again with the profile you want. |
| `bundle-info` FAIL | The deployed bundle reports another version, or another Git revision. | Run the [bundle upgrade](#upgrade-the-bundle). |
| `compatibility` UNKNOWN | This Gateway, Module and bundle combination was not tested, or a version field is missing. | Expected on untested versions. It blocks nothing. |

`compatibility` compares five values with the tested list in the manifest: `gatewayVersion`,
`gatewayBuild`, `mcpModuleVersion`, `mcpModuleBuild` and `bundleVersion`. The Module's SHA-256 is not
visible through these APIs, so it is not compared.

## Read the plan

`plan` works out what `apply` would do, from the same checks as `doctor`, and changes nothing.

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

Each line reads `<ACTION> <kind> <name>: <reason>`. The actions are `CREATE`, `UPDATE`, `NO CHANGE`,
`BLOCKED` and `SKIP`. The last line is always `No changes have been applied.`, also with `--json`.

The lines come in the order `apply` would carry them out:

1. `mcp-module`
2. `security-level` and `runtime-token`, when you asked for them
3. `bundle-project`
4. `server-config`
5. `runtime-policy`
6. `security-level` and `runtime-token` again, as information only, when you did not ask for them

The `mcp-module` line is never `CREATE`, because `apply` does not install the Module. It is
`NO CHANGE` when the Module runs, and `BLOCKED` when it is missing or its state cannot be read.

A `bundle-project` `UPDATE` names the kind of version change: `patch`, `minor`, `major` or
`downgrade`. `major` and `downgrade` say `requires explicit acknowledgement in apply`, and `apply`
refuses them without `--acknowledge-upgrade`.

Any `BLOCKED` line makes `plan` exit with 3. Fix the cause, usually the Module or a project name
clash, and run it again.

## Apply the deployment

`apply` runs `plan` first, then makes the changes. It needs three flags that the other commands treat
as optional: `--server-config-name`, `--bundle-zip` and `--policy-file`.

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

Before it writes anything:

- A missing required flag is a usage error, exit 2.
- It hashes `--bundle-zip` and compares it with the manifest. A mismatch is exit 2.
- Any `BLOCKED` plan line, or an unacknowledged `major` or `downgrade` change, stops it with exit 3.
- It checks the policy file and refuses one larger than 32 KiB.
- It checks the flag combinations. `--create-runtime-token` needs `--runtime-token-file`, and a name
  from `--runtime-token-name` or `--server-config-name`. The other `--runtime-token-*` flags need
  `--create-runtime-token`. `--security-level-name` needs `--provision-security-levels` or
  `--create-runtime-token`.

What it writes, in this order:

1. The security level, with `--provision-security-levels`. It is a child of `Authenticated` named
   `IgnitionMcpRuntime<Profile>`, unless `--security-level-name` says otherwise. An existing level is
   never changed, and a level that has child levels is refused.
2. The agent's API token, with `--create-runtime-token`. It gets exactly that security level. The
   secret goes only into `--runtime-token-file`, created with mode `0600`. On a plain-`http` Gateway,
   add `--runtime-token-insecure-channel`, or the token will only work over `https`.
3. The bundle project. Before it replaces an existing one, it saves the old project into
   `--backup-dir`.
4. The Server Config. A new one is created switched off, read back, then switched on. An existing one
   gets its Tool list updated and keeps everything else, including whether it is switched on. The Tool
   list is always an explicit list, never `*`. The permissions tree comes from
   `--server-config-permissions-file`, or else from the existing Server Config.
5. The Runtime Target Policy. It reads the policy back afterwards and writes it once more if the
   read-back differs.

Things to know:

- There is no undo. If a write fails, the command stops, and earlier writes stay. `--backup-dir` is
  the only local copy. Without it, only the Gateway's own configuration backup can restore the old
  state.
- The command never prints a token. Running it again with the same token file checks the file against
  the token on the Gateway and reports `NO CHANGE`. It does not make a new token.
- The Module sometimes serves a new Server Config with no Tools for a moment. `apply` then sends the
  same Server Config again, up to three times, and prints a `REFRESH server-config ...` line for each.
  The result is the same deployment.

`apply` ends by running `verify` and printing its report, then a summary:

```console
apply: wrote=3 skipped=2 failed=0 => exit 0
```

Exit 1 means a write or the final check failed. Exit 3 means nothing was written. On the very first
run, the final check can fail with HTTP 401 or 403 because it has no agent token yet. The writes are
done, so run `verify` with `--mcp-token-file`.

## Verify a deployment

```bash
ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-0.7.0.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production \
  --profile readonly
```

The checks are `endpoint-reachable`, `mcp-initialize`, `inventory-tools`, `inventory-resources`,
`inventory-prompts`, one `resources-read <uri>` line per resource in the profile, one
`prompts-get <name>` line per prompt, and `bundle-info`.

- `endpoint-reachable` PASS only means the address answered.
- Exit 0 needs every line to be `PASS` or `NOT_APPLICABLE`.
- An empty resource or prompt list is `NOT_APPLICABLE`, not a failure. The bundle has no prompts.

Run `verify` after every Gateway restart, and after `install-module --restart`.

## Upgrade the bundle

A bundle upgrade replaces the bundle project with a newer version of this repository's Tools.

1. Get the newer repository version, or, if you develop the bundle, raise the number in
   `packages/ignition-runtime-bundle/BUNDLE_VERSION`. The build refuses a bundle whose `bundle_info`
   version or ownership mark disagrees with that file.
2. Build and check the release, as in [Prerequisites](#prerequisites).
3. Run `doctor` with the new manifest. `bundle-info` FAIL is expected, because the Gateway still runs
   the old version. `compatibility` stays `UNKNOWN` until a tested combination matches.
4. Run `plan` with the new manifest and ZIP. Expect a line like
   `UPDATE bundle-project ignition_runtime: redeploy managed bundle 0.7.0 -> 0.8.0 (minor)`.
5. Run `apply` with `--backup-dir`. Add `--acknowledge-upgrade` only for a `major` change or a
   downgrade. A downgrade is how you roll back on purpose.
6. Run `verify`, then reconnect the AI application, because the Tool list may have changed.

The upgrade replaces the whole bundle project. Anything you added to `ignition_runtime` by hand is
lost, unless `--backup-dir` saved it.

## The Runtime Target Policy

The Runtime write Tools read their allowlists from a document on the Gateway, not from the bundle. It
is stored as two Tags in the reserved Tag provider `IgnitionMCPPolicy`:

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` holds the JSON text.
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` holds its size in bytes. The Tools read the size
  first, so they refuse an oversized document without loading it.

Only `setup-native apply` writes the policy. The REST server's `config_resource_*` Tools refuse the
`IgnitionMCPPolicy` provider whatever their allowlist says, so an agent cannot change the policy.

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "auditProfile": "MCP_AUDIT",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU"]
  },
  "alarmShelveMaxSeconds": 3600,
  "tagUpdateMaxItems": 20
}
```

Rules, from `contracts/shared/runtime-target-policy.schema.json` and the command's own checks:

- `schemaVersion`, `allowlists`, `serviceIdentity` and `auditMode` are required. `schemaVersion` is
  `1`.
- `allowlists` has one key per Tool name, so a Tag entry can never allow an alarm Tool. A Tool with
  no key can change nothing. `"*"` allows everything and must be written out.
- A Tag entry covers that path and the paths below it, at `/` boundaries: `[default]AHU` covers
  `[default]AHU/Temp` but not `[default]AHU2`. UDT definitions under `_types_` need an explicit
  `_types_` entry, which `*` does not cover.
- An alarm entry is a provider-qualified alarm path starting with `prov:` and containing no `*`. It
  covers that path and the paths below it, at `/` or `:` boundaries.
- `serviceIdentity` is the actor name in Ignition's audit log for every Runtime write. The agent
  cannot set it.
- `auditMode` is `best_effort`, `required` or `off`. With `required`, a write is refused when the
  audit profile named in `auditProfile` is unavailable.
- The per-call item limits are between 1 and 100, 20 when absent. The command checks
  `tagUpdateMaxItems`, `tagCreateMaxItems` and `tagCopyMaxItems`. The schema limits
  `tagDeleteMaxItems`, `tagMoveMaxItems`, `tagRenameMaxItems`, `tagWriteMaxWrites` and `alarmMaxPaths`
  the same way.
- `alarmShelveMaxSeconds` can lower the shelve limit, never raise it past 86400 seconds.
- The stored text is at most 32768 bytes.

The command stores the policy in a fixed form, with sorted keys and no spaces, so `plan` can compare
it byte for byte with the stored copy. You can format your file any way you like.

To change the policy, edit the file and run `plan`, then `apply`. `plan` shows
`UPDATE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy: replace the served policy (...)` with the
size and the start of the old and new SHA-256.

If the policy is missing, unreadable, too large or invalid, every Runtime write Tool refuses with
`operation_disabled`. Removing the policy switches Runtime writes off. It never opens them up.

## Turn on writes

Every kind of write starts switched off on both servers. There are three kinds:
`CONFIG_MUTATION` for configuration changes, `CONTROL_MUTATION` for control actions and
`ADMIN_MUTATION` for administration. No scope or profile includes another. You turn on each layer
yourself.

On the Runtime server:

1. `--profile` decides which Tools the endpoint offers. `readonly` has no write Tools. `operator` adds
   `tag_write`, `alarm_shelve` and `alarm_unshelve`. `configurator` adds `tag_update`, `tag_create`,
   `tag_copy`, `tag_delete`, `tag_move` and `tag_rename`. `full` adds both groups. There is no
   administration profile.
2. The Server Config's permissions tree decides who may connect. `--provision-security-levels` and
   `--create-runtime-token` create a matching security level and token.
3. The Runtime Target Policy allowlists decide which targets each Tool may change. A Tool the profile
   offers still changes nothing without an allowlist entry.

On the REST server, in its environment settings:

```bash
IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,project_import
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["com.inductiveautomation.historian/historian-provider/Core"],"project_import":["MES"]}'
IGNITION_MCP_CONTROL_MUTATION_ENABLED=true    # for alarm_pipeline_cancel
IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true   # for project_export and tag_config_export
IGNITION_MCP_PROJECT_WRITER_ENABLED=true      # for project_import and the Perspective writes
IGNITION_MCP_GATEWAY_ID=plant-gateway-1
```

The caller's token also needs the matching scope, `ignition.config` or `ignition.control`. With
`IGNITION_MCP_AUTH_MODE=none` the server is read-only. The exports switch is separate
from the write switches.

The REST server checks a write in this order, and the first refusal decides the error: the kind is
switched on, the Tool is in `IGNITION_MCP_MUTATION_OPERATIONS`, the target is not a refused resource
type, the target is in `IGNITION_MCP_MUTATION_TARGETS`, and the Gateway offers the route. A Tool whose
kind is switched off is also missing from the Tool list.

Turn on one Tool and one target, try it, check the result, and only then allow more. Neither server
retries a write on its own.

The [Tool catalog](../guide/tools.md) lists what every Tool needs.

## Reading `operation_diagnose` output

`operation_diagnose` is a read Tool on the REST server. It takes one value, `correlationId`, which
must be the exact 36-character id. A malformed id is `invalid_argument`. The lookup is exact, never
partial.

Where to find the id:

- Every REST Tool error carries `code`, `message` and `correlationId`.
- The server's log line for the call carries the same `correlationId`. Set
  `IGNITION_MCP_LOG_FORMAT=json` for structured logs.
- A successful write returns it too. For example, `project_import` returns `correlationId` and
  `transactionId`.

| Field | Meaning |
| --- | --- |
| `tool` | The Tool that made the call. |
| `outcome` | `in_progress`, `succeeded`, `failed`, `outcome_unknown`, `cancelled` or `interrupted`. `in_progress` without `finishedAt` means the call is still running, or the server stopped before it recorded the result. |
| `errorCode` | The error code, or `null` for a success. |
| `startedAt`, `finishedAt` | When the call started and ended. |
| `phases` | The steps the call reached, in order, each with a time. |
| `phasesTruncated` | `true` means the call had more than 32 steps. The oldest were dropped and the newest kept. |
| `transactionId` | The project transaction, for project writes. |
| `downstreamCorrelationId` | The id the Gateway used for the same request, when there is one. |
| `auditResultMissing` | `true` means the audit log has no result entry for this call. |

Why a lookup can return `not_found`:

- Records are removed after 72 hours or beyond 10,000 records, by default. See
  `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS` and `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS`.
- Each caller only sees their own records. Another caller's id answers `not_found`, so the Tool
  cannot reveal whether a call exists. Only a caller with `ignition.admin` sees all records.
- If the record store was down during the call, the call still ran but has no record. The server
  logged an `operation_record_failure` line instead.

`outcome_unknown` means stop and look. Read the target to see its real state before you decide what to
do next. The server never repeats an uncertain write on its own.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. `doctor` or `verify` found no `FAIL`. `plan` found no `BLOCKED`. `apply` wrote everything and verified. `install-module` installed or upgraded the Module, found it already installed (`NO CHANGE`), or installed it and is waiting for a restart. |
| 1 | A check failed, a write failed, or the network failed. For `install-module`, also: the Gateway runs a newer build, the module list could not be read to the end, the Gateway refused the upload, an acceptance or the install, or the Module did not come back after `--restart`. |
| 2 | Usage error: a bad flag, an unreadable or invalid manifest, a ZIP that does not match the manifest, or a rejected token file. For `install-module`, also a problem with the file found before any request: wrong SHA-256, larger than the `.modl` limit, no readable `module.xml`, no 10-digit build, or not the MCP Module. |
| 3 | The command needs a decision you did not give it, so it changed nothing. `plan` has a `BLOCKED` line and `apply` wrote nothing. `install-module` installed nothing. In the certificate or license case it already uploaded the file. |

Pressing Ctrl-C exits with 2. An unexpected crash exits with 1 and prints only the error type, so a
token cannot leak through a stack trace.

## Windows

Windows is not a supported platform, although nothing is known to be broken.
[D31](../decisions/D31-windows-support-scope.md) records the scope, the known limitations and a manual
checklist that nobody has run yet.

The tool table in [Prerequisites](#prerequisites) and the PowerShell block in
[Environment and credential files](#environment-and-credential-files) are the Windows starting point.
Both carry the **not run on Windows yet** marker.

- **Line endings.** A clone made before `.gitattributes` was added keeps Windows line endings, and
  `tooling.native.cli validate` rejects them with `must use LF line endings`. Run
  `git rm --cached -rq . && git reset --hard` once, or clone again. Both discard uncommitted changes.
  See [D31 section 5](../decisions/D31-windows-support-scope.md#5-migration-for-existing-windows-clones).
- **Checksums.** Windows has no `sha256sum -c`. In `dist/release`, run
  `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` or
  `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`, and compare the result with the
  hash in `ignition-runtime-bundle-<version>.sha256`.
- **Wizard.** `scripts/deploy-runtime-bundle.sh` is a bash script and needs Git Bash or WSL. The
  `setup-native` commands work without it.
- **Token files and the data folder.** Windows skips the `0600` token-file check and the `0700`
  data-folder check, and logs one WARNING instead. Use filesystem ACLs so only the service account can
  read the token files and `IGNITION_MCP_DATA_DIR`.

## Known v1 limitations

- `alarm_status`, `alarm_journal` and `alarm_acknowledge` are switched off. Ignition's alarm query
  functions have no row limit or continuation, so these Tools cannot bound their answer or their
  pre-checks. Their code is in `packages/ignition-runtime-bundle/deferred/`, no profile lists them,
  and turning one on needs a bounded mechanism plus new live test evidence. See D12.
- The MCP Module returns structured results but publishes no Tool output schemas (D27). The schemas in
  `contracts/schemas/` are the reference.
- The MCP Module drops `null` values inside objects, so Runtime answers encode them (D28). See
  [How it works](../guide/how-it-works.md#two-details-for-client-developers).
- On 8.3.9 the Module's response format is recorded as `FAILED_NATIVE_BINDING`. On 8.3.8 it is
  `VERIFIED_WITH_LIMITATION`. Neither is recorded as production-supported, and no command in this
  runbook records that.
- The bundle version is still 0.x.
- The repository has one Module build, so Module upgrades are tested with unit tests, not with a real
  upgrade.
- `doctor` and `verify` do not wait for a Gateway that is starting. Only the live test setups under
  `tests/harness/` wait.

## Sources

The rules in this runbook come from these decisions: D20 for the setup commands and their safety
rules, the D26 Phase 6 amendment for the v1 scope, D27 and D28 for Runtime response behavior, and D30
for the write rules. `CONTEXT.md` defines Module install, Module upgrade and Bundle upgrade. Every flag
was checked against `packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/setup_native/` and each
subcommand's `--help`. The `doctor`, `plan` and `verify` samples are real output. The `install-module`
sample shows the format the source prints.
