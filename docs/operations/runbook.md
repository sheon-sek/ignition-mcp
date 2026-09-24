# Operations runbook

> Chinese translation: [`runbook.zh-CN.md`](runbook.zh-CN.md)

This runbook is the reference for running a deployment after you understand the basics. It covers
each `ignition-mcp` command, the MCP Module, upgrades, the Runtime Target Policy, turning on writes,
and reading `operation_diagnose` output.

For a first installation, follow [Quick start](../guide/quick-start.md) instead. It walks through the
same commands in order.

| I want to... | Section |
| --- | --- |
| know what I need before I start | [Prerequisites](#prerequisites) |
| know what a deployment directory holds | [Deployment state](#deployment-state) |
| set up a Gateway | [Set up a deployment](#set-up-a-deployment) |
| check a deployment | [Check a deployment](#check-a-deployment) |
| run the REST server | [Start the REST server](#start-the-rest-server) |
| register an agent | [Connect an agent](#connect-an-agent) |
| remove a deployment | [Reset a deployment](#reset-a-deployment) |
| install or upgrade the MCP Module | [The MCP Module](#the-mcp-module) |
| deploy a newer bundle | [Upgrade the bundle](#upgrade-the-bundle) |
| change what the Runtime write Tools may touch | [The Runtime Target Policy](#the-runtime-target-policy) |
| turn on writes | [Turn on writes](#turn-on-writes) |
| find out what happened to a REST call | [Reading `operation_diagnose` output](#reading-operation_diagnose-output) |
| understand an exit code | [Exit codes](#exit-codes) |

In the examples the deployment is `default`, the Gateway is `http://127.0.0.1:8088`, and the commands
run from the repository folder.

## Prerequisites

| Item | Where it comes from |
| --- | --- |
| The Gateway's address | your Gateway, for example `http://127.0.0.1:8088` |
| A Gateway API key with write access | the Gateway web interface, Security section. Create a new API key whose Security Level is ticked under every permission in Security > General Settings, and save it in a file |
| The MCP Module `.modl` file | Inductive Automation. The repository pins version `1.3.5.2026021307-SNAPSHOT`, build `2026021307`, SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`. The record is `tests/fixtures/modules/MCP-module-1.3.5.2026021307-SNAPSHOT.provenance.json` |
| A repository checkout | `ignition-mcp setup` builds the Runtime bundle from the checkout, so it needs `packages/ignition-runtime-bundle/project` and `tooling/native` |

The operator writes no policy file and no permissions file. `setup` generates both from the
Deployment environment and the roles.

The tools you need on your own computer:

| Tool | Linux or macOS | Windows |
| --- | --- | --- |
| Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) | the [official installer](https://docs.astral.sh/uv/) or a package manager. `uv` installs Python for you | `winget install --id=astral-sh.uv -e`, or the same official installer |
| A shell | any POSIX shell | PowerShell 7. The D31 section 6 checklist uses `-SkipHttpErrorCheck`, which needs version 7 |
| Java 11 | only for the Jython tests (D29). Neither server needs it | same |

The Windows instructions in this runbook are marked **not run on Windows yet**. See
[D31](../decisions/D31-windows-support-scope.md).

Repository maintainers build a release from the checkout, for the compatibility evidence:

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
`release` reads `tests/compatibility/evidence/` and does not change it. `setup` builds the bundle
itself, so an operator never handles these files.

### Building on Windows

**Not run on Windows yet.** See [D31](../decisions/D31-windows-support-scope.md). Store the Git
revision in a variable and pass it as `--source-revision $rev`. Check the checksum with `Get-FileHash`
or `certutil` instead of `sha256sum -c`, and compare the result with the hash in the `.sha256` file:

```powershell
$rev = git rev-parse HEAD
uv run --no-sync python -m tooling.native.cli release `
  --project-dir packages/ignition-runtime-bundle/project `
  --out-dir dist/release `
  --source-revision $rev `
  --evidence-dir tests/compatibility/evidence
$V = Get-Content packages/ignition-runtime-bundle/BUNDLE_VERSION
Get-FileHash "dist/release/ignition-runtime-bundle-$V.zip" -Algorithm SHA256
```

## Deployment state

A deployment is a named directory, `~/.config/ignition-mcp/deployments/<name>/`. Several deployments
can sit side by side, one per Gateway. Every command takes `--deployment NAME`, and the default is
`default`.

| Path | Contents |
| --- | --- |
| `deployment.toml` | The Gateway URL, the environment, the roles, the generated REST settings, the record of what `setup` created and which risks were accepted, and a `bind` entry when someone saved one by hand |
| `runtime-policy.json` | The generated Runtime Target Policy |
| `gateway-token.secret` | The setup key you supplied, kept for re-runs |
| `runtime-<role>.secret` | Each role's Runtime token |
| `rest-gateway-token.secret` | The `ignition-mcp-rest` Gateway API token the REST server uses |
| `rest-data/` | The REST server's own directory: its SQLite databases, the artifact store and the project-writer lock file |
| `backups/` | The copy of a managed bundle project that `setup` replaces |

The directory is created with mode `0700` and each secret file with mode `0600`, where the platform
has POSIX modes. Windows skips both checks and logs one warning instead, so give the files a
filesystem ACL that lets only the service account read them.

The commands do not read the deployment directory from anywhere else, and secrets never appear in
output, logs or the one-line command the wizard prints.

Rules for token files:

- A token file must be a regular file, not a symbolic link, with exactly one non-empty line holding
  `<name>:<key>`, where the name is the API key's name on the Gateway.
- On Linux and macOS only the owner may read it, that is mode `0600`. Anything else is refusal with
  exit code 2.
- Prefer a file over typing the key, so the key does not sit in the shell history.

Rules for addresses:

- An address must be an absolute `http` or `https` URL with a host, and must not contain a user name
  or password.

## Set up a deployment

`setup` deploys both planes for the chosen roles. It plans first, shows the changes it will make,
accepts each named risk, and asks for confirmation before writing anything. `--dry-run` shows the
plan and stops. Running it again is the normal way to reconcile a deployment, so it is safe to
repeat.

```bash
ignition-mcp setup \
  --deployment default \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --roles analysis,engineer \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --yes
```

| Flag | Meaning |
| --- | --- |
| `--gateway-url URL` | The Gateway's web address. |
| `--environment dev\|prod` | The Deployment environment. Default `dev`. |
| `--roles LIST` | The roles to deploy, a comma-separated subset of `analysis` and `engineer`. Default `analysis,engineer` in `dev` and `analysis` in `prod`. |
| `--gateway-token-file PATH` | A file holding the Gateway API key on one line. |
| `--module-file PATH` | The MCP Module `.modl` file. Without it, `setup` looks in `tests/fixtures/modules/` and `~/Downloads` for a file whose SHA-256 matches the pinned build. |
| `--recreate-tokens` | Delete and recreate every managed token whose local secret file is lost. |
| `--provision-security-levels` | In `prod`, create the roles' missing Security Levels. |
| `--rest-mutation-classes LIST` | Which REST Mutation classes the server may offer: `none`, or a comma-separated list of `config`, `control` and `admin`. Default `config,control` in `dev` and `none` in `prod`. Adding `admin` needs the ADMIN acceptance, which `--yes` covers. |
| `--rest-target-allowlist *\|none` | Whether the enabled REST Mutation classes may target anything (`*`) or nothing (`none`). Default `*` in `dev` and `none` in `prod`. |
| `--rest-project-writer on\|off` | Whether the REST server may import a Project. Default `on` in `dev` and `off` in `prod`. |
| `--dry-run` | Show the plan and stop before any write. |

Every command also takes `--deployment`, `--json`, `--yes`, `--accept-certificate` and
`--accept-eula`. In one-line mode a plan with changes needs `--yes`, and the certificate and the EULA
always need their own flags. [Quick start](../guide/quick-start.md) explains the wizard, the
acceptances and the exit codes.

`setup` runs its stages in a fixed order. Each stage plans with read-only requests and reports
`OK`, `CHANGED`, `SKIPPED` or `FAILED` with the reason:

1. Install the MCP Module from the local file. See [The MCP Module](#the-mcp-module).
2. Create the roles' Security Levels. `dev` creates them; `prod` needs
   `--provision-security-levels`.
3. Create the roles' Runtime tokens and the `ignition-mcp-rest` token, and save each secret to its
   own `*.secret` file.
4. Deploy the Runtime bundle project. A managed project is backed up before it is replaced. A project
   with the bundle's name that `setup` did not create is never taken over.
5. Create or update one Server Config per role with an explicit Tool list and the generated
   permissions tree.
6. Write the Runtime Target Policy.
7. Write the REST settings.

Things to know:

- A Server Config whose Tool list matches but whose permissions tree differs is reported as
  `CHANGED`, never `NO CHANGE`.
- Nothing is retried blindly. A stage that cannot finish says why and gives a next command.
- A change someone made on the Gateway by hand is reported as a difference, and the desired state is
  restored after confirmation, because that overwrites the other person's change.
- When a local secret file is missing but its Gateway token still exists, `setup` reports the
  mismatch. It deletes the Gateway token and creates a new one after confirmation, or with
  `--recreate-tokens` in one-line mode.
- Changing the environment from `dev` to `prod` lists everything that narrows, such as allowlists
  emptied or a role removed, and writes only after confirmation.

## Check a deployment

`status` changes nothing. It reads the Gateway and the deployment directory and prints one line per
check, each with `OK`, `SKIPPED` or `FAILED` and a reason.

```bash
ignition-mcp status \
  --deployment default \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

The lines are `deployment`, `gateway`, `module`, `bundle`, then four lines per deployed role (`level
<role>`, `token <role>`, `server config <role>`, `endpoint <role>`), then `runtime policy`,
`rest token`, `rest static tokens`, `rest settings`, `named-query registry` and `leftover files`.

| Line | What it means | What to do |
| --- | --- | --- |
| `gateway` FAILED | The Gateway did not answer. | Check the address and that the Gateway is running, then run `status` again. |
| `module` FAILED | The MCP Module is missing, not running, or another build. | Run `setup`, which installs the pinned build. |
| `bundle` `ABSENT` | Nothing is deployed yet. | Run `setup`. |
| `bundle` a name clash or a bad marker | A project with that name exists that this tool did not create, or its ownership mark is damaged. | Rename or remove that project, or choose another deployment on a Gateway without it. `setup` never takes over a project it did not create. |
| `bundle` `NOT standalone` | The bundle project is marked inheritable. | Make it standalone in the Designer. |
| `level <role>` FAILED | A role's Security Level is missing or changed. | Run `setup`. |
| `token <role>` FAILED | The role's token is missing on the Gateway, or its secret file is lost locally. | Run `setup`, or `setup --recreate-tokens` when the local file is lost. |
| `server config <role>` FAILED | The Server Config is missing or its permissions tree differs. | Run `setup`. |
| `endpoint <role>` FAILED | The MCP endpoint did not answer as expected. | Check the Module, then run `setup` and `status` again. |
| `runtime policy` FAILED | The served policy is missing or is not the generated one. | Run `setup`. |
| `rest settings` FAILED | A risky setting is active with no record that it was accepted. | Run `start --yes`, or run `setup` again. |
| `named-query registry` | Whether `IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON` is set on the Gateway. | Set it on the Gateway machine if you want the database Tools. |

`status` does not wait for a Gateway that is starting, so run it again after a restart.

## Start the REST server

`start` runs the REST server in the foreground until Ctrl+C. It prints the health result and one
endpoint line per deployed role before it serves. It derives every server setting from the
deployment, so you do not set any environment variable by hand.

```bash
ignition-mcp start
```

```bash
ignition-mcp start --bind 127.0.0.1:8000 --yes
```

| Flag | Meaning |
| --- | --- |
| `--bind HOST:PORT` | The address the REST server listens on. Default `127.0.0.1:8000`. A `bind` entry saved in `deployment.toml` is used when `--bind` is absent. Another host needs `--yes`, because it exposes the server beyond this computer. |

Agents reach the endpoint at `http://<bind>/mcp`. The server also answers `/health/live`,
`/health/ready` and `/metrics` on the same port. If the port is taken, `start` names the conflict and
suggests another port. Leave `start` running in its own terminal while the agents work; it is not
started as a background service.

When a risky value was accepted in an earlier `setup` run, `start` activates it without asking and
lists it with the acceptance it rests on. A risky value with no matching record, for example after a
hand edit of `deployment.toml`, needs `--yes` in that `start` run.

## Connect an agent

`connect <role>` registers one role's endpoints with an agent client. It registers two MCP servers:
`ignition-runtime-<role>` at `<gateway-url>/data/mcp/<role>`, and `ignition-rest-<role>` at the REST
server's `/mcp` address. It needs the deployment to be set up already, and the REST endpoint needs
`start` to be running.

```bash
ignition-mcp connect analysis --client claude
```

| Flag | Meaning |
| --- | --- |
| `--client claude\|codex\|none` | The client to register with. `claude` is Claude Code, `codex` is Codex, `none` registers nothing. A client that is not installed is offered as an option you cannot choose, and the reason names the missing binary. |

## Reset a deployment

`reset` removes what `setup` created, on the Gateway and locally. It deletes only a resource the
deployment's own record says `setup` created, and it lists anything else it finds as left in place.
It is refused outside `dev`.

```bash
ignition-mcp reset \
  --deployment default \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

## The MCP Module

`setup` installs one `.modl` file from your computer on the Gateway, through the Gateway's own module
routes. It downloads nothing. It learns the Module id and build from the file's own `module.xml`.

The Module step runs in a fixed order, and each check happens before the step it protects:

1. Check the file. `setup` refuses a file whose SHA-256 does not match the pinned build, that is
   larger than 64 MiB, is not a ZIP, has no `module.xml`, has no `<id>` or `<version>`, has a version
   without a 10-digit build, or is not the MCP Module, `com.inductiveautomation.mcp`.
2. Compare with the Gateway. The same build already installed is `OK` and uploads nothing. A lower
   build is always refused. A newer build needs the upgrade acceptance, which `--yes` covers.
3. Upload the file. If the Gateway reports a different Module id, the step stops and installs nothing.
4. Install the Module. This needs `--accept-certificate` and `--accept-eula`; a missing acceptance
   stops the run before any write. If the Module has no certificate or no license, the step is
   skipped.
5. Restart the Gateway and wait until the Module runs with the new build.

The acceptance for the restart is one of the named items too. Grant it with `--yes` in one-line mode.

## Upgrade the bundle

A bundle upgrade replaces the bundle project with a newer version of this repository's Tools. It is
the normal `setup` path, not a separate command.

1. Get the newer repository version, or, if you develop the bundle, raise the number in
   `packages/ignition-runtime-bundle/BUNDLE_VERSION`. The build refuses a bundle whose `bundle_info`
   version or ownership mark disagrees with that file.
2. Validate the project, as in [Prerequisites](#prerequisites).
3. Run `setup` again. It names the kind of version change, `patch`, `minor`, `major` or `downgrade`. A
   `major` change and a downgrade need explicit acknowledgement, which `--yes` covers. A downgrade is
   how you roll back on purpose.
4. Run `status` to check the deployment, then reconnect the AI application with `connect`, because
   the Tool list may have changed.

Before `setup` replaces a managed project, it backs that project up. The upgrade replaces the whole
bundle project, so anything you added to it by hand is lost.

## The Runtime Target Policy

The Runtime write Tools read their allowlists from a document on the Gateway. It is stored as two
Tags in the reserved Tag provider `IgnitionMCPPolicy`:

- `[IgnitionMCPPolicy]RuntimeTargetPolicy` holds the JSON text.
- `[IgnitionMCPPolicy]RuntimeTargetPolicyLength` holds its size in bytes. The Tools read the size
  first, so they refuse an oversized document without loading it.

Only `ignition-mcp setup` writes the policy. The REST server's `config_resource_*` Tools refuse the
`IgnitionMCPPolicy` provider whatever their allowlist says, so an agent cannot change the policy.

`setup` generates the document from the Deployment environment and the roles and stores it in the
deployment directory as `runtime-policy.json`. In `dev` every Runtime Mutation Tool gets `*`; in
`prod` every allowlist is empty. You do not write or edit it.

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
  no key can change nothing. `"*"` allows everything.
- A Tag entry covers that path and the paths below it, at `/` boundaries: `[default]AHU` covers
  `[default]AHU/Temp` but not `[default]AHU2`. UDT definitions under `_types_` need an explicit
  `_types_` entry, which `*` does not cover.
- An alarm entry is a provider-qualified alarm path starting with `prov:` and containing no `*`. It
  covers that path and the paths below it, at `/` or `:` boundaries.
- `serviceIdentity` is the actor name in Ignition's audit log for every Runtime write. The agent
  cannot set it.
- `auditMode` is `best_effort`, `required` or `off`. With `required`, a write is refused when the
  audit profile named in `auditProfile` is unavailable.
- The per-call item limits are between 1 and 100, 20 when absent. The schema limits
  `tagUpdateMaxItems`, `tagCreateMaxItems`, `tagCopyMaxItems`, `tagDeleteMaxItems`, `tagMoveMaxItems`,
  `tagRenameMaxItems`, `tagWriteMaxWrites` and `alarmMaxPaths` the same way.
- `alarmShelveMaxSeconds` can lower the shelve limit, never raise it past 86400 seconds.
- The stored text is at most 32768 bytes.

`setup` stores the policy in a fixed form, with sorted keys and no spaces, so `status` can compare it
byte for byte with the served copy. A served document that differs is reported as a hand edit, and
`setup` restores the generated one.

If the policy is missing, unreadable, too large or invalid, every Runtime write Tool refuses with
`operation_disabled`. Removing the policy switches Runtime writes off. It never opens them up.

## Turn on writes

Every kind of write starts switched off. There are three kinds: `CONFIG_MUTATION` for configuration
changes, `CONTROL_MUTATION` for control actions and `ADMIN_MUTATION` for administration. No scope or
role includes another. A `prod` deployment leaves all of them off. A `dev` deployment turns on what
the Engineer role needs.

On the Runtime server, the role decides:

1. The role's profile decides which Tools the endpoint offers. Analysis uses `readonly`, which has no
   write Tools. Engineer uses `full`, which has all of them.
2. The Server Config's permissions tree decides who may connect. `setup` generates the tree and each
   role's Runtime token together, so they match.
3. The Runtime Target Policy allowlists decide which targets each Tool may change. In `dev` a Tool can
   change anything; in `prod` it can change nothing until you switch the deployment to `dev`.

On the REST server, `setup` derives the settings from the environment and the role, and `start`
passes them:

```bash
IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,project_import
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["com.inductiveautomation.historian/historian-provider/Core"],"project_import":["MES"]}'
IGNITION_MCP_CONTROL_MUTATION_ENABLED=true    # for alarm_pipeline_cancel
IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true   # for project_export and tag_config_export
IGNITION_MCP_PROJECT_WRITER_ENABLED=true      # for project_import and the Perspective writes
IGNITION_MCP_GATEWAY_ID=plant-gateway-1
```

The caller's token also needs the matching scope, `ignition.config` or `ignition.control`. In `dev`
the Engineer role's Named static token carries both; the Analysis role's token carries
`ignition.read` only. The exports switch is separate from the write switches.

The REST server checks a write in this order, and the first refusal decides the error: the kind is
switched on, the Tool is in `IGNITION_MCP_MUTATION_OPERATIONS`, the target is not a refused resource
type, the target is in `IGNITION_MCP_MUTATION_TARGETS`, and the Gateway offers the route. A Tool whose
kind is switched off is also missing from the Tool list.

Turn on one Tool and one target, try it, check the result, and only then allow more. Neither server
retries a write on its own. The [Tool catalog](../guide/tools.md) lists what every Tool needs.

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
| 0 | Success. |
| 1 | A step failed, or the Gateway could not be reached. |
| 2 | A problem with the flags or the answers. Nothing was written. |

With `--json` the report is one document with stable error codes, listed in
[Quick start](../guide/quick-start.md#exit-codes-and-error-codes). Pressing Ctrl+C exits with 2 and
keeps the steps that already ended. An unexpected crash exits with 1 and prints only the error type,
so a secret cannot leak through a stack trace.

## Windows

Windows is not a supported platform, although nothing is known to be broken.
[D31](../decisions/D31-windows-support-scope.md) records the scope, the known limitations and a manual
checklist that nobody has run yet.

The tool table in [Prerequisites](#prerequisites) is the Windows starting point. It carries the
**not run on Windows yet** marker.

- **Line endings.** A clone made before `.gitattributes` was added keeps Windows line endings, and
  `tooling.native.cli validate` rejects them with `must use LF line endings`. Run
  `git rm --cached -rq . && git reset --hard` once, or clone again. Both discard uncommitted changes.
  See [D31 section 5](../decisions/D31-windows-support-scope.md#5-migration-for-existing-windows-clones).
- **Checksums.** Windows has no `sha256sum -c`. In `dist/release`, run
  `certutil -hashfile ignition-runtime-bundle-<version>.zip SHA256` or
  `Get-FileHash ignition-runtime-bundle-<version>.zip -Algorithm SHA256`, and compare the result with the
  hash in `ignition-runtime-bundle-<version>.sha256`.
- **Prompts.** Under mintty without a pseudo console, the wizard falls back to plain line prompts with
  the same questions and checks. They cannot switch terminal echo off, so a pasted secret shows on
  screen while you type it. The secret still never reaches the CLI's output.
- **Token files and directories.** Windows skips the `0600` secret-file check and the `0700`
  deployment-directory check, and logs one WARNING instead. Use filesystem ACLs so only the service
  account can read the files under `~/.config/ignition-mcp/`.

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
- The repository has one Module build, so a Module upgrade to a newer build is tested with unit tests,
  not with a real upgrade.
- `status` does not wait for a Gateway that is starting. Only the live test setups under
  `tests/harness/` wait.
- The hand-edit rule does not yet cover the content of the managed bundle project, and replacing that
  project does not yet take the D16 lock or compare `pcf1` fingerprints. Both are deferred to issue
  #80.

## Sources

The rules in this runbook come from D20 for the setup commands and their safety rules, D32 for the
CLI, the environments and the roles, the D26 Phase 6 amendment for the v1 scope, D27 and D28 for
Runtime response behavior, and D30 for the write rules. `CONTEXT.md` defines Module install, Module
upgrade and Bundle upgrade. Every flag was checked against
`packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/gateway_ops/`,
`packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/engine/` and
`packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/setup/`, and each command's `--help`. The
console samples in this runbook show the shape of the output; they are not recorded runs.
