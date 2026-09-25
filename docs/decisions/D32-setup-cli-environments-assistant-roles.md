# D32 — Setup CLI, deployment environments and Assistant roles

**Status:** DECIDED. The owner set every rule below in the Phase 7 design interview and approved the record on 2026-09-25. This decision amends D20.

## Context

An operator followed `docs/guide/setup-runtime.md` on a Windows playground Gateway and could not finish. `apply` ended every run with `mcp-initialize` HTTP 403. Three causes combined:

1. The `verify` step that `apply` runs never uses the Runtime token that `apply` has just created. It only reads `--mcp-token-file`, so it always sends an unauthenticated `initialize`.
2. The guide asks the operator to hand-write `permissions.json` and names `IgnitionMcpRuntimeReadonly` in its example. With `--profile full` the Runtime token gets `IgnitionMcpRuntimeFull`, so a copied example no longer matches it.
3. Once a Server Config exists with the right Tool list, `apply` reports `NO CHANGE` and never compares its permissions tree. A corrected `permissions.json` is ignored.

The owner judged the problem to be the setup model itself, not the three bugs. The profile name, the Security Level name, the permissions tree and the token's grant are one fact that the operator must keep consistent by hand in four places. Two files must be written and copied by hand. A check is expected to fail on every first run. The owner asked for one tool that takes an operator from install through setup to a connected agent, and that an AI agent can run in one command.

The repository is for internal use for now. Its first consumers are two agents: one that inspects and troubleshoots a Gateway, and one that develops Ignition projects.

## 1. Scope

One CLI, `ignition-mcp`, covers both planes end to end. It installs the MCP Module, deploys the Runtime bundle, creates every Security Level, credential and document both planes need, starts the REST server, and registers the endpoints with an agent client.

The CLI replaces `ignition-mcp setup-native` and `scripts/deploy-runtime-bundle.sh`. Logic that earlier phases verified is reused as it is: the Gateway observation, the managed-project marker, the singleton security-tree edit and its read-back, the token file handling, the Module install flow, and the deterministic bundle build. Both old entry points are deleted in the phase that reaches parity. No release carries both.

It is written in Python and lives in the `ignition-rest-mcp` package, so D25's layout does not change. Interactive prompts use `questionary` and output uses `rich`. The owner first proposed rewriting the CLI in Go with `huh`. The interview rejected that because the terminal problem that motivated it, prompts failing in Git Bash's mintty without a pseudo console, affects Go programs in the same way.

## 2. Commands

| Command | What it does |
| --- | --- |
| `setup` | Runs the whole deployment and can be re-run. It shows the changes it will make and asks for confirmation before writing anything. `--dry-run` shows them and stops. |
| `status` | Read-only. Reports every check one line at a time. It replaces `doctor` and `verify`. |
| `start` | Runs the REST server in the foreground until Ctrl+C. |
| `connect <role>` | Registers one Assistant role's endpoints with Claude Code or Codex. |
| `reset` | Removes what the CLI created on the Gateway and locally. It is refused outside `dev`. |

There is no separate `plan` command, because `setup` always shows its plan first.

## 3. Interaction model

One engine resolves every input. It has two front ends.

1. When the flags supply every value, the command runs without asking anything. This is the path an AI agent uses. A `setup` run whose plan changes anything also needs `--yes` in this mode, which stands in for the wizard's confirmation. Without it the run fails before any write and names the flag. The coordinator added this on 2026-09-25 to resolve the conflict with section 2, and the owner may overrule it.
2. When a value is missing and stdin is a terminal, a wizard asks for the missing values only. Each question shows its default and its choices.
3. When a value is missing and stdin is not a terminal, the command exits with an error that lists every missing flag. It never waits for input.
4. When the wizard finishes, it prints the one-line command with the same values, so the run can be repeated or handed to an agent.
5. A wrong input is asked again. A path that does not exist, an unreadable file or a Gateway token the Gateway rejects gets the reason and a new prompt. The run does not exit and the operator does not start over.
6. Under mintty without a pseudo console, the wizard falls back to plain line prompts with the same questions and validation. Those prompts cannot turn terminal echo off, so a pasted secret shows on screen while it is typed. The question says so, and the secret still never reaches the CLI's output.
7. Output is in English.
8. `--json` never prompts, even when stdin is a terminal, because a program reads its output. A missing value fails as in item 3. The coordinator added this rule on 2026-09-25 after the P7-2 review, and the owner may overrule it.

## 4. Feedback contract

Every step reports its start and its end. The end line names the step, a status of `OK`, `CHANGED`, `SKIPPED` or `FAILED`, and the reason. A `FAILED` line also gives the next action as a command the operator can run.

An HTTP status alone is never a reason. A 403 from the MCP endpoint, for example, is reported as one of these causes when the CLI can tell them apart: no token was sent, the token's Security Level does not satisfy the Server Config's permissions, or the token requires a secure channel and the Gateway URL is `http`.

`--json` prints the same steps as structured data with stable error codes. The codes are defined in the CLI's own code and documented in the CLI guide. They are not part of `contracts/`, which describes Tools.

No step may be expected to fail. If a step cannot succeed in a given state, the CLI either makes the state right first or skips the step and says why.

## 5. Deployment environments and defaults

A setup run has a Deployment environment, `dev` or `prod`. `dev` is the default. The environment is stored with the deployment. It chooses defaults only; section 7 lists the rules that neither environment relaxes.

| Setting | `dev` default | `prod` default |
| --- | --- | --- |
| Assistant roles deployed | Analysis and Engineer | Analysis only |
| Security Levels | created | changed only with `--provision-security-levels` |
| Runtime and REST credentials | created for every deployed role | created only for the roles named with `--roles` |
| Server Config permissions tree | generated from the role | generated from the role |
| Runtime Target Policy allowlists | `*` for every Runtime Mutation Tool | empty |
| Alarm shelve cap | 3600 seconds | 3600 seconds |
| REST Mutation classes | `CONFIG` and `CONTROL` on, `ADMIN` off | all off |
| REST Target allowlists | `*` for the enabled classes | empty |
| REST project writer | on | off |
| REST bind address | `127.0.0.1:8000` | `127.0.0.1:8000` |
| Runtime token secure channel | not required when the Gateway URL is `http` | required |

Turning on `ADMIN` in `dev` needs its own Explicit acceptance, because it changes the Gateway's own configuration and the Engineer Assistant does not need it to develop projects.

The operator never writes a policy file or a permissions file. The CLI generates both from the environment and the role, stores them with the deployment and keeps them in step with the Gateway.

Changing a deployment's environment is allowed. Moving from `dev` to `prod` lists everything that narrows, such as allowlists emptied or a role removed, and writes only after confirmation.

## 6. Explicit acceptance

Every named risk and every legal term a run depends on is accepted inside that run. The operator is never sent to another screen or another file to accept it. A default never counts as acceptance.

The named items are the Module certificate, the Module EULA, a Gateway restart, an unencrypted token channel, a `*` Target allowlist, the `ADMIN` Mutation class, a non-loopback REST bind address, and overwriting a change someone made on the Gateway by hand.

In the wizard, each item is a yes or no question that states what it allows. In one-line mode, `--yes` accepts every item except the certificate and the EULA, which need `--accept-certificate` and `--accept-eula`. A missing acceptance in one-line mode fails the run before any write, and the error names the flag. Every accepted item appears in the run's report and in its `--json` output.

`setup` records in the deployment directory which risky values were accepted. `start` activates a risky value without asking when that record matches it, and lists it in its report as active together with the acceptance it rests on. A risky value without a matching record, for example after a hand edit of `deployment.toml`, needs Explicit acceptance in that `start` run. The coordinator added this on 2026-09-25 after the P7-4 review, and the owner may overrule it.

## 7. Rules no environment relaxes

- The reserved policy provider, `_types_`, Precondition tokens and Refused resource types behave as D30 decides.
- Secrets never appear in output, logs or the one-line command the wizard prints. A secret file is written with mode `0600` where the platform has POSIX modes, as D31 allows.
- A managed project is backed up before `setup` replaces it.
- An unmanaged project with the bundle's name is never taken over.
- Server Configs always list their Tools explicitly. `tools: "*"` is never written.
- The MCP Module is never downloaded. Only a local file whose SHA-256 matches the pinned build is installed.
- A lower Module build is always refused.

## 8. Assistant roles

A deployment serves two Assistant roles, as `CONTEXT.md` defines them.

| | Analysis Assistant | Engineer Assistant |
| --- | --- | --- |
| Runtime Tools | `readonly` profile | `full` profile |
| REST Tools | read Tools | read Tools, `CONFIG` and `CONTROL` Mutations |
| Server Config | `analysis` | `engineer` |
| MCP endpoint | `/data/mcp/analysis` | `/data/mcp/engineer` |
| Security Level | `Authenticated/IgnitionMcpAnalysis` | `Authenticated/IgnitionMcpEngineer` |
| Runtime credential | its own Gateway API token | its own Gateway API token |
| REST credential | a Named static token with `ignition.read` | a Named static token with `ignition.read`, `ignition.config` and `ignition.control` |

The profile becomes a property of the role. An operator chooses roles, not profiles.

One REST server process serves both roles. It runs with `static-token` authentication and both Named static tokens, so the scope of the token decides what each role may do.

## 9. Credentials and state

The operator creates the first Gateway API token by hand. Ignition 8.3's OpenAPI documents no login endpoint and no way to exchange a username and password for a token. The CLI tells the operator to create a new API key on the Gateway whose Security Level is ticked under every permission in Security > General Settings. It validates the pasted key against the Gateway at once and asks again when the key is rejected or lacks a permission.

`setup` then creates one more Gateway API token, `ignition-mcp-rest`, for the REST server's own calls to the Gateway. The operator's setup token is used during setup only and is never given to the REST server. In Phase 7 the `ignition-mcp-rest` token copies the Security Level of the setup key, and `setup` names that level in its plan. A dedicated level with only the permissions the REST server needs requires an edit of the Gateway's General Settings permissions, which is deferred to issue #79.

Each deployment is a named directory, `~/.config/ignition-mcp/deployments/<name>/`. It holds `deployment.toml` with the Gateway URL, the environment and the roles, the generated policy and permissions documents, and one file per secret. A re-run reads the directory instead of asking again. Several deployments can exist side by side, one per Gateway.

`setup` finds the Module file in `tests/fixtures/modules/` or `~/Downloads`, checks its SHA-256 against the pinned build and reports which file it used. When it finds none, it asks for a path. The owner allowed `setup` to use the fixture copy on 2026-09-25.

`setup` builds the Runtime bundle from the repository checkout with the existing deterministic builder. The operator never handles the ZIP or the manifest.

## 10. Re-run rules

- **A lost secret file.** When a local secret file is missing but its Gateway token still exists, `status` and `setup` report the mismatch. `setup` deletes the Gateway token and creates a new one after confirmation, or with `--recreate-tokens` in one-line mode.
- **A hand edit on the Gateway.** When someone changed a Server Config's permissions, the Runtime Target Policy or another managed resource by hand, `setup` reports what differs and restores the desired state after confirmation, because that overwrites the other person's change.
- **Permissions drift is a change.** A Server Config whose Tool list matches but whose permissions tree differs is reported as `CHANGED`, never `NO CHANGE`.

**Amendment, 2026-09-25 (closes issue #80).** The coordinator made this amendment, and the owner may overrule it.

- **The managed bundle project's content is covered.** The plan exports the managed project and compares its Tools, Text Resources and Prompts with the bundle archive it would import, even when the bundle version matches. A difference is a hand edit. The plan names each differing resource and its files, up to ten and then a count, and the restore needs the `overwrite_hand_edit` Explicit acceptance. The comparison removes only what the Gateway changes on import: a `resource.json` is compared as canonical JSON (sorted keys, type-sensitive, so `true` and `1` differ), because 8.3.8 re-serializes it. The one other exception is the git revision that setup stamps into the `bundleSourceRevision` assignment of `bundle_info`. `project.json` stays with the ownership marker, bundle version and inheritable checks.
- **Replacing the managed project follows D16.** `setup` takes the writer lock for the deployment's Gateway and the project, exports baseline A, stores it as the backup and computes `pcf1(A)`. A must equal the export the plan compared. Just before the import it exports A' and imports only when `pcf1(A')` equals `pcf1(A)`. Afterwards it exports the result and requires the bundle's content and marker. A mismatch fails with the error code `conflict`, names a Designer save or another writer as the cause, keeps the backup and imports nothing.
- **The Designer-session policy (coordinator ruling on 2026-09-25, which the owner may overrule).** Before a replace, setup reads the Gateway's Designer session listing with the same bounded reader the REST server uses. An active Designer session on the managed project, or a listing the Gateway cannot provide, refuses the replace in `prod` with error code `conflict`. The reason names the session, and the next action is to close the Designer and run setup again. In `dev` the plan names the session, and the replace needs the `overwrite_hand_edit` Explicit acceptance. Setup checks in the plan, before any write, and again under the lock just before the import. The second check compares with the sessions the plan listed. A session the plan did not list, or a listing that was readable in the plan and is not any more, refuses the import in either environment. A session the dev operator accepted and that is still open does not refuse it.
- **An import without a clear answer.** When the import ends in a transport error, a timeout or an HTTP 5xx, setup does not retry. A 4xx or a refusal inside the answer is a plain failure. Under the lock setup exports the current state C. C equal to the bundle means the import was applied: the step is `CHANGED` and says the import gave no clear answer. C equal to baseline A means it was not applied: the step is `FAILED`, and re-running setup is safe. Any other C is an unknown outcome: the step is `FAILED`, it names the backup, and setup does not retry.
- **The lock.** The lock is a file per Gateway ID and project under the deployment's `rest-data` directory. The REST server that `start` runs takes the same file for each project Mutation, and setup holds it only while it replaces the project. So two setup runs, or setup and that REST server, never write the project at the same time, and a running REST server does not keep setup out. The lock is process-local as D16 states: it does not cover another machine or the Designer, which the fingerprint check covers instead.
- **A dropped role removes only what setup created.** When an environment change drops a role, setup removes that role's Server Config, token and Security Level only when the deployment's `created` record names them, as `reset` does. Anything else is left in place and reported as `SKIPPED` with the reason. The role's secret file stays when its token stays.

## 11. Amendments to D20

| D20 rule | Now |
| --- | --- |
| Commands `doctor`, `plan`, `apply`, `verify`, `install-module` | Replaced by the commands in section 2. |
| The default profile is `readonly`; configurator and full are never deployed by default | Holds in `prod`. In `dev`, both roles are deployed and the Engineer Assistant gets `full`. |
| Security Levels are not modified by default | Holds in `prod`. In `dev`, `setup` creates the role levels. The edit keeps D20's procedure: read the singleton, make the minimal change with an optimistic precondition, write, then read it back and verify its structure. |
| A Runtime token is not created by default | Holds in `prod`. In `dev`, `setup` creates one per role. |
| A missing Module aborts `apply` with a remediation | `setup` installs it as a step, with the Explicit acceptance items for the certificate, the EULA and the restart. |
| No single command silently performs every privileged action | Kept. `dev` performs them in one command, but it shows the plan, names every accepted risk and reports every step. |

D20's remaining rules still hold: the explicit Tool inventory, no Gateway filesystem writes, no Module download or automatic upgrade, idempotent reconciliation, and the non-atomic ordered transaction model.

## 12. Out of scope

- Running the REST server in the background or as a system service.
- Seeding the first Gateway token from an environment variable for docker commissioning.
- Shipping the bundle ZIP inside the Python wheel.
- Diagnostic Tools written for the Analysis Assistant.
- Registering Named Queries for `database_query`. The registry is an environment variable of the Gateway process, which `setup` cannot set. `status` reports whether it is set.
- Migrating deployments made by `setup-native`. Every existing deployment is a playground.

## Consequences

- An operator can go from an empty playground Gateway to two connected Assistant roles with one command or one wizard run, without writing any file.
- An AI agent can run the same setup in one line and read a structured result.
- A `dev` deployment gives the Engineer Assistant write access to every Runtime and REST target that D30 does not refuse. That is intended for playground Gateways. A deployment that must not allow it uses `prod`.
- Operators who scripted `setup-native` must move to the new commands, because the old ones are deleted.

## Configuration

```yaml
decision: D32
status: DECIDED
amends: [D20]

cli:
  entry_point: ignition-mcp
  package: ignition-rest-mcp
  language: python
  prompts: questionary
  output: rich
  commands: [setup, status, start, connect, reset]
  replaces: [setup-native, scripts/deploy-runtime-bundle.sh]
  non_tty_missing_input: fail_listing_flags
  invalid_input: ask_again
  mintty_without_pty: plain_line_prompts
  output_language: en
  json_output: true

environments:
  default: dev
  values: [dev, prod]

acceptance:
  one_line_accept_all: --yes
  always_named: [--accept-certificate, --accept-eula]

roles:
  analysis:
    server_config: analysis
    security_level: Authenticated/IgnitionMcpAnalysis
    runtime_profile: readonly
    rest_scopes: [ignition.read]
  engineer:
    server_config: engineer
    security_level: Authenticated/IgnitionMcpEngineer
    runtime_profile: full
    rest_scopes: [ignition.read, ignition.config, ignition.control]

state:
  directory: ~/.config/ignition-mcp/deployments/<name>/
  rest_gateway_token: ignition-mcp-rest
```
