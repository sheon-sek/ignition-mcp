# Quick start

> 中文版：[`quick-start.zh-CN.md`](quick-start.zh-CN.md)

`ignition-mcp` is the one command that sets up and runs both MCP servers for an Ignition Gateway. It
installs the MCP Module and deploys the Runtime bundle, creates every Security Level, credential and
document both servers need, starts the REST server, and registers the endpoints with Claude Code or
Codex. An operator runs it as a wizard, and an agent runs it in one line.

A deployment is a named directory, `~/.config/ignition-mcp/deployments/<name>/`. It holds
`deployment.toml` with the Gateway URL, the environment and the roles, the generated policy and
permissions documents, and one `*.secret` file per secret. A re-run reads this directory instead of
asking again, so `setup` is safe to run more than once. Several deployments can sit side by side, one
per Gateway. Every command takes `--deployment NAME` to pick one; the default is `default`.

The commands are `setup`, `status`, `start`, `connect <role>` and `reset`.

## Install

`ignition-mcp` ships in this repository, so you run it from a checkout:

```bash
git clone https://github.com/sheon-sek/ignition-mcp.git
cd ignition-mcp
uv sync --locked --package ignition-rest-mcp
source .venv/bin/activate
```

`uv` installs Python 3.11 or newer for you if the machine has none. On Windows, activate with
`.venv\Scripts\activate`. Every command below runs from the checkout with that environment active;
`uv run --no-sync ignition-mcp` does the same without activating.

## The wizard

Run a command with no flags and stdin attached to a terminal. The wizard asks for the values that are
still missing and leaves the rest alone. Each question shows its default and its choices.

- A bad answer, a path that does not exist, an unreadable file or a Gateway key the Gateway rejects
  gets the reason and the question again. You do not start over.
- When the run finishes, the wizard prints the one-line command with the same values, so you can
  repeat it or hand it to an agent. Secrets never appear in that line.
- Under mintty without a pseudo console, the wizard falls back to plain line prompts with the same
  questions and the same checks. Those prompts cannot switch terminal echo off, so a pasted secret
  shows on screen while you type it. The secret still never reaches the CLI's output or logs.

## The one-line form

Give every flag and the command asks nothing. This is the path an agent uses. When the plan changes
anything, `setup` also needs `--yes`, which stands in for the wizard's confirmation. Without it the
run stops before the first write and names the flag.

```bash
ignition-mcp setup \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --roles analysis,engineer \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --accept-certificate --accept-eula \
  --yes
```

Two acceptances are never covered by `--yes`. The Module certificate needs `--accept-certificate` and
the Module license needs `--accept-eula`, so a first run against an empty Gateway passes both. A
missing acceptance stops the run before any write. The `--json` output never prompts, even on a
terminal, because a program reads it. Every value a run accepts, and every risky value it turns on,
appears in the report.

Create the Gateway key by hand first. Ignition 8.3 offers no way to exchange a name and password for
a key, so make a new API key in the Gateway web interface whose Security Level is ticked under every
permission in Security > General Settings. The file holds the key on one line as `<name>:<key>`, where
the name is the key's own name on the Gateway:

```bash
mkdir -p ~/.config/ignition-mcp
umask 077
printf '%s\n' 'mcp-setup:<your-ignition-api-key>' > ~/.config/ignition-mcp/gateway-token
chmod 0600 ~/.config/ignition-mcp/gateway-token
```

The CLI checks the key against the Gateway at once and asks again if the Gateway refuses it or the
key lacks a permission. The setup key is used during setup only. It is never given to the REST
server, which gets its own token, `ignition-mcp-rest`.

## The two Assistant roles

A deployment serves two roles. An operator chooses roles, not profiles: each role carries its own
Runtime profile and its own REST scopes.

| | Analysis Assistant | Engineer Assistant |
| --- | --- | --- |
| Runtime Tools | `readonly` profile | `full` profile |
| REST Tools | read Tools | read Tools, plus `CONFIG` and `CONTROL` Mutations |
| Server Config | `analysis` | `engineer` |
| MCP endpoint | `<gateway-url>/data/mcp/analysis` | `<gateway-url>/data/mcp/engineer` |
| Security Level | `Authenticated/IgnitionMcpAnalysis` | `Authenticated/IgnitionMcpEngineer` |
| Runtime credential | its own Gateway API token | its own Gateway API token |
| REST credential | a Named static token with `ignition.read` | a Named static token with `ignition.read`, `ignition.config` and `ignition.control` |

One REST server process serves both roles. It runs with `static-token` authentication and both Named
static tokens, so the scope of the token decides what each role may do.

## `dev` and `prod` defaults

A setup run has a Deployment environment, `dev` or `prod`. `dev` is the default, and the environment
is stored with the deployment. It chooses defaults only.

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

Turning on `ADMIN` in `dev` means adding `admin` to `--rest-mutation-classes`; the run then needs the
ADMIN Mutation class acceptance, because it changes the Gateway's own configuration, and `--yes`
covers that acceptance in one-line mode. The Engineer Assistant does not need `ADMIN` to develop
projects.

The operator never writes a policy file or a permissions file. The CLI generates both from the
environment and the role, stores them with the deployment, and keeps them in step with the Gateway.
`prod` defaults every write off, through the three REST flags in the table below and an empty Runtime
Target Policy; each of those defaults can still be turned on deliberately. Switching a deployment
from `dev` to `prod` lists everything that narrows, such as allowlists emptied or a role removed, and
writes only after confirmation.

## `setup`

`setup` deploys both planes for the chosen roles. It reads the current state, shows the changes it
will make, accepts each named risk, and asks for confirmation before writing anything. `--dry-run`
shows the plan and stops. It is safe to re-run.

| Flag | Meaning |
| --- | --- |
| `--gateway-url URL` | The Gateway's web address, for example `http://127.0.0.1:8088`. |
| `--environment dev\|prod` | The Deployment environment. Default `dev`. |
| `--roles LIST` | The Assistant roles to deploy, a comma-separated subset of `analysis` and `engineer`. Default `analysis,engineer` in `dev` and `analysis` in `prod`. |
| `--gateway-token-file PATH` | A file holding one `<name>:<key>` line, where the name is the API key's name on the Gateway. On Linux and macOS its mode must be `0600`. |
| `--module-file PATH` | The MCP Module `.modl` file. `setup` looks in `tests/fixtures/modules/` and `~/Downloads` first and uses a file whose SHA-256 matches the pinned build. |
| `--recreate-tokens` | Delete and recreate every managed token whose local secret file is lost. Use this after a lost secret file. |
| `--provision-security-levels` | In `prod`, create the roles' missing Security Levels. `dev` does this already. |
| `--rest-mutation-classes LIST` | Which REST Mutation classes the server may offer: `none`, or a comma-separated list of `config`, `control` and `admin`. Default `config,control` in `dev` and `none` in `prod`. Adding `admin` needs the ADMIN acceptance, which `--yes` covers. |
| `--rest-target-allowlist *\|none` | Whether the enabled REST Mutation classes may target anything (`*`) or nothing (`none`). Default `*` in `dev` and `none` in `prod`. `*` with a class enabled needs the wildcard acceptance, which `--yes` covers. |
| `--rest-project-writer on\|off` | Whether the REST server may import a Project. Default `on` in `dev` and `off` in `prod`. |
| `--dry-run` | Show the plan and stop before any write. |

The steps run in a fixed order, and each step reports `OK`, `CHANGED`, `SKIPPED` or `FAILED` with its
reason:

1. Install the MCP Module from the local file. The same build already installed is `OK` with nothing
   uploaded. A lower build is always refused.
2. Create the roles' Security Levels. `dev` creates them; in `prod` this needs
   `--provision-security-levels`.
3. Create each role's Runtime token and the `ignition-mcp-rest` token, and save each secret to its own
   `*.secret` file with mode `0600`.
4. Deploy the Runtime bundle project. A managed project is backed up before it is replaced. A project
   with the bundle's name that `setup` did not create is never taken over.
5. Create one Server Config per role with an explicit Tool list and its permissions tree.
6. Write the Runtime Target Policy to the `IgnitionMCPPolicy` Tag provider.
7. Write the REST server settings from the role and the environment.

```bash
ignition-mcp setup --deployment default \
  --gateway-url http://127.0.0.1:8088 \
  --environment dev \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token \
  --accept-certificate --accept-eula --yes
```

## `status`

`status` changes nothing. It reads the Gateway and the deployment directory and prints one line per
check, each with a status. Run it after every Gateway restart, and after any change someone made on
the Gateway by hand.

It takes `--gateway-url` and the setup key file, `--gateway-token-file`. It reports the deployment
directory, the Gateway, the Module build, the bundle project, four lines per role (its Security Level,
its token, its Server Config and its endpoint), the Runtime Target Policy, the REST token and static
tokens, the REST settings, whether the Named Query registry is set, and any leftover file.

```bash
ignition-mcp status \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

## `start`

`start` runs the REST server in the foreground until you press Ctrl+C. It prints the health result and
one endpoint line per deployed role before it serves, so you can see that it is ready. Agents connect
to `http://<bind>/mcp`.

| Flag | Meaning |
| --- | --- |
| `--bind HOST:PORT` | The address the REST server listens on. Default `127.0.0.1:8000`. A `bind` entry saved in `deployment.toml` is used when `--bind` is absent. Another host needs `--yes`. |

```bash
ignition-mcp start
```

```bash
ignition-mcp start --bind 127.0.0.1:8000 --yes
```

Leave `start` running in its own terminal while the agents work. It is not started as a background
service.

## `connect`

`connect <role>` registers one role's endpoints with an agent client. The role is `analysis` or
`engineer`. It registers two MCP servers for that role: `ignition-runtime-<role>` at
`<gateway-url>/data/mcp/<role>` and `ignition-rest-<role>` at the REST server's `/mcp` address. It
needs the deployment to be set up already, and `start` to be running for the REST endpoint.

| Flag | Meaning |
| --- | --- |
| `--client claude\|codex\|none` | The client to register with. `claude` is Claude Code, `codex` is Codex, `none` registers nothing. A client that is not installed is offered as an option you cannot choose, and the reason names the missing binary. |

```bash
ignition-mcp connect analysis --client claude
```

```bash
ignition-mcp connect engineer --client codex
```

## `reset`

`reset` removes what `setup` created, on the Gateway and locally. It deletes only a resource the
deployment's own record says `setup` created, and it lists anything else it finds as left in place. It
is refused outside `dev`.

```bash
ignition-mcp reset \
  --gateway-url http://127.0.0.1:8088 \
  --gateway-token-file ~/.config/ignition-mcp/gateway-token
```

## Exit codes and error codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | A step failed, or the Gateway could not be reached. |
| 2 | A problem with the flags or the answers. Nothing was written. |

With `--json` the report is one document with stable error codes. A code never changes meaning once
released, so a script can match on it.

| Code | Exit | Meaning |
| --- | --- | --- |
| `missing_input` | 2 | A value has no flag, no saved value and no terminal to ask on. |
| `invalid_input` | 2 | A flag or saved value failed a check and there is no terminal to ask again on. |
| `acceptance_required` | 2 | A named risk or legal term was not accepted. |
| `gateway_token_rejected` | 2 | The Gateway answered and refused the setup key. |
| `deployment_unreadable` | 2 | `deployment.toml` exists but cannot be read or parsed. |
| `secret_file_invalid` | 2 | A secret file is missing, unreadable, too open, or has the wrong shape. |
| `step_failed` | 1 | A step could not finish; its reason says why. |
| `not_implemented` | 1 | The command is registered but its body is not written yet. |
| `interrupted` | 2 | You pressed Ctrl+C or closed the prompt. Steps that ended before it are kept. |
| `unexpected_error` | 1 | A bug. Only the exception type is reported, so no secret can leak. |
| `gateway_unreachable` | 1 | DNS, TCP, TLS or a timeout: the Gateway never answered, so nothing was judged. |
| `not_confirmed` | 2 | The plan was shown and not confirmed. Nothing was written. |
| `conflict` | 1 | Another setup run or the deployment's REST server holds the project lock, or the managed project changed on the Gateway between the baseline export and the import, for example through a Designer save. Nothing was imported; run `setup` again to see the new plan. |

## Common problems

| Symptom | What it means and what to do |
| --- | --- |
| The Gateway rejects the setup key. | The key is wrong or its Security Level is missing a permission. Create a new API key whose Security Level is ticked under every permission in Security > General Settings, save it to the file as `<name>:<key>`, and run `setup` again. |
| A Runtime or REST call answers HTTP 403 and the CLI reports that no token was sent. | The role's secret file holds no usable secret. Run `setup --recreate-tokens` to delete the Gateway token and create a new one. |
| A Runtime call answers HTTP 403 and the CLI reports that the token's Security Level does not satisfy the Server Config's permissions. | The token and the Server Config drifted apart, usually after a hand edit. Run `setup` again; it restores the Server Config's permissions tree and reports the change. |
| A Runtime call answers HTTP 403 and the CLI reports that the token requires a secure channel and the Gateway URL is `http`. | The token was created for `https` but the deployment points at `http`. Use the Gateway's `https` address, or use `dev`, which does not require a secure channel on `http`. |
| The Gateway is unreachable. | The address is wrong, or the Gateway is down. Check that `--gateway-url` opens in a browser, then run `status` again. |
| A value is missing and stdin is not a terminal. | The command fails with `missing_input`, exit 2, and lists every missing flag. Pass those flags, or run the command on a terminal to get the wizard. Nothing was written. |
| The Module file is not found, its SHA-256 does not match, or its build is lower than the Gateway's. | `setup` installs only a local `.modl` file whose SHA-256 matches the pinned build, and it refuses a lower build. Pass the right file with `--module-file`, or put it in `~/Downloads`. |
| A local secret file is lost. | `status` and `setup` report the mismatch: the Gateway token still exists but the file that held it is gone. Run `setup --recreate-tokens`; it deletes the Gateway token and makes a new one. |
| Someone changed a managed resource on the Gateway by hand. | `setup` reports what differs and restores the desired state after confirmation, because that overwrites the other person's change. Accepted risks that no longer match their record are accepted again in the run that turns them on. |

## Where to read next

- [Tool catalog](tools.md): every Tool, and what it needs before an agent can see it.
- [Configuration reference](configuration.md): every setting the REST server reads, the fields of the
  Runtime Target Policy, and the Named Query registry.
- [Operations runbook](../operations/runbook.md): upgrades, the Runtime Target Policy, turning on
  writes, and reading `operation_diagnose` output.
