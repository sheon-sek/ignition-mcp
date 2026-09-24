# Set up the Runtime server

> 中文版：[`setup-runtime.zh-CN.md`](setup-runtime.zh-CN.md)

This guide installs the `ignition-runtime` server on your Gateway and connects an AI agent to it.
The Runtime server gives the agent Tag values, Tag history, Tag configuration, alarm shelving and
approved database queries.

Unlike the REST server, the Runtime server runs **inside the Gateway**. You install two things on
the Gateway:

1. The official Ignition MCP Module from Inductive Automation. It runs the MCP endpoint.
2. This repository's Tools, packed as an Ignition project called the bundle.

A command-line tool from this repository, `ignition-mcp setup-native`, does both through the
Gateway's web API. You run it from your own computer.

Not sure this is the server you need? See [Which server do I need?](how-it-works.md#which-server-do-i-need)

## What you need

- An Ignition 8.3 Gateway, tested on 8.3.8 and 8.3.9, where you may install a module and restart
  the Gateway.
- The MCP Module file, `MCP-module-1.3.5.2026021307-SNAPSHOT.modl`, from Inductive Automation. The
  repository keeps a copy for its automated tests in `tests/fixtures/modules/`. Its record says it
  was supplied for testing and makes no redistribution claim, so check your Inductive Automation
  terms before you use that copy.
- A computer with Git and `uv`. Follow Steps 1 and 2 of [Set up the REST server](setup-rest.md#step-1-install-git-and-uv)
  to install them and download the project.
- About 30 minutes, including one Gateway restart.

## How the setup works

`setup-native` has five subcommands. You run them in this order:

| Subcommand | What it does | Changes the Gateway? |
| --- | --- | --- |
| `install-module` | Checks the `.modl` file's checksum, uploads it, accepts its certificate and license, installs it, and restarts the Gateway. | yes |
| `doctor` | Checks the Gateway, the Module and any earlier deployment, and prints one line per check. | no |
| `plan` | Prints what `apply` would create or change. | no |
| `apply` | Deploys the bundle, the MCP endpoint and the Runtime Target Policy, then runs `verify`. | yes |
| `verify` | Connects to the MCP endpoint and checks that it offers exactly the expected Tools. | no |

`apply` creates three things on the Gateway:

- The bundle project, called `ignition_runtime` unless you choose another name.
- A **Server Config**, the Module's record of one MCP endpoint. Its name becomes part of the
  endpoint address, `<gateway-url>/data/mcp/<name>`. It lists the Tools of your chosen profile and
  who may connect.
- The **Runtime Target Policy**, which lists what the write Tools may change.

If you ask it to, `apply` also creates a security level and an API token for the agent.

## Step 1: Create a Gateway API token for the setup

`setup-native` needs an API token that may change the Gateway's configuration. This is different
from the token the agent will use later, which you create in Step 5.

1. Open the Gateway web interface and log in as an administrator.
2. Go to the Security section and create an API token with read and write access.
3. Save the token in a file with nothing else in it:

   ```bash
   mkdir -p ~/.config/ignition-mcp
   printf '%s\n' 'paste-your-token-here' > ~/.config/ignition-mcp/gateway.token
   chmod 600 ~/.config/ignition-mcp/gateway.token
   ```

   The file must hold exactly one line and, on Linux and macOS, be readable only by you. The command
   refuses a file anyone else can read.

## Step 2: Choose a profile

The profile decides which Tools the agent gets. Start with `readonly`. You can change it later by
running `apply` again.

| Profile | The agent can |
| --- | --- |
| `readonly` | read Tags, Tag configuration, UDTs, history, shelved alarms, and run approved queries |
| `operator` | also write Tag values and shelve or unshelve alarms |
| `configurator` | also create, change, copy, move, rename and delete Tags |
| `full` | everything above |

The full list per profile is in the [Tool catalog](tools.md#profiles-decide-which-tools-exist).

## Step 3: Write the policy file

Create `policy.json` in the `ignition-mcp` folder. This version allows no writes at all, which is
right for `readonly`:

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "allowlists": {}
}
```

`serviceIdentity` is the name that appears in Ignition's audit log for changes the agent makes. You
fill in `allowlists` later, when you turn on writes.

## Step 4: Write the permissions file

The permissions file says which security level a caller needs to use the endpoint. Create
`permissions.json`:

```json
{
  "type": "AllOf",
  "securityLevels": [
    {
      "name": "Authenticated",
      "children": [
        {"name": "IgnitionMcpRuntimeReadonly", "children": []}
      ]
    }
  ]
}
```

`IgnitionMcpRuntimeReadonly` is the security level that `apply` creates for the `readonly` profile
in the next step. For another profile, the name is `IgnitionMcpRuntimeOperator`,
`IgnitionMcpRuntimeConfigurator` or `IgnitionMcpRuntimeFull`.

## Step 5: Deploy

You can deploy with a guided wizard or with the commands one by one. Both do the same thing.

### Option A: the wizard

The wizard asks for each value, explains each step, and saves your answers in `.env` so you can run
it again. It needs `bash`, so on Windows use Git Bash or WSL.

```bash
./scripts/deploy-runtime-bundle.sh
```

Answer `y` when it asks whether to create the Runtime Security Level and the Runtime API token. Give
it the paths of your `permissions.json` and `policy.json`. When it finishes, it prints the MCP
endpoint address. Continue with [Step 6](#step-6-connect-your-ai-application).

The wizard has two gaps. It cannot pass `--runtime-token-insecure-channel`, so if your Gateway address
starts with `http://`, use Option B instead. It also runs its last `verify` without the agent token,
so that step can fail with HTTP 401 or 403 even though the deployment worked. Run the `verify` command
from [Step 7](#step-7-try-it) to check.

### Option B: the commands

Run these from the `ignition-mcp` folder. First set two values the commands share:

```bash
export IGNITION_MCP_SETUP_GATEWAY_URL=http://127.0.0.1:8088
V=$(cat packages/ignition-runtime-bundle/BUNDLE_VERSION)
```

Build the bundle. This writes three files to `dist/release/`: the ZIP, a manifest that describes
it, and a checksum file.

```bash
uv run --no-sync python -m tooling.native.cli release \
  --project-dir packages/ignition-runtime-bundle/project \
  --out-dir dist/release \
  --source-revision "$(git rev-parse HEAD)" \
  --evidence-dir tests/compatibility/evidence
```

Install the MCP Module. Run it once without the last line to read the certificate and license it
asks you to accept. Then run it again with the last line. The Gateway restarts, and the command
waits up to 10 minutes for it to come back.

```bash
uv run --no-sync ignition-mcp setup-native install-module \
  --file ~/Downloads/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365 \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --accept-certificate --accept-eula --restart
```

If the Module is already installed, the command prints `NO CHANGE` and does nothing.

Check the Gateway and preview the changes. Neither command changes anything. Before the first
deploy, `doctor` reports `FAIL` on `server-config-presence` and `mcp-initialize`, because the
endpoint does not exist yet, and skips the checks that depend on it. Make sure `gateway-info` and
`module-installed` pass.

```bash
uv run --no-sync ignition-mcp setup-native doctor \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly

uv run --no-sync ignition-mcp setup-native plan \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly \
  --policy-file policy.json
```

`plan` prints one line per change, such as `CREATE bundle-project ignition_runtime: ...`. A `BLOCKED` line
means `apply` would refuse, and the line says why. The last line is always
`No changes have been applied.`

Deploy:

```bash
uv run --no-sync ignition-mcp setup-native apply \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --server-config-name production --profile readonly \
  --policy-file policy.json \
  --server-config-permissions-file permissions.json \
  --backup-dir backups \
  --provision-security-levels \
  --create-runtime-token --runtime-token-file ~/.config/ignition-mcp/runtime.token
```

`apply` has no undo. `--backup-dir` saves a copy of the old bundle project before a later run
replaces it. `apply` ends with a summary line such as `apply: wrote=5 skipped=0 failed=0 => exit 0`.
`exit 0` means everything was written and verified.

`--create-runtime-token` saves the agent's API token in `~/.config/ignition-mcp/runtime.token`. The
command never prints it. Running `apply` again does not replace it.

By default the new token only works over `https`. If your Gateway address starts with `http://`, add
`--runtime-token-insecure-channel` to the command. Do this only on a network you trust, because the
token then travels unencrypted.

`apply` finishes by running `verify`. The first time, `verify` has no agent token yet, so it may
report HTTP 401 or 403 on `mcp-initialize` and `apply` exits with 1. The deployment is written all the
same. Run `verify` as shown in Step 7, with `--mcp-token-file`, to confirm it.

## Step 6: Connect your AI application

The endpoint address is `<gateway-url>/data/mcp/<server-config-name>`, for example
`http://127.0.0.1:8088/data/mcp/production`. The transport is Streamable HTTP.

The agent logs in with the token from `runtime.token`, sent in the `X-Ignition-API-Token` header.
Show the token with `cat ~/.config/ignition-mcp/runtime.token`.

With Claude Code:

```bash
claude mcp add --transport http ignition-runtime http://127.0.0.1:8088/data/mcp/production \
  --header "X-Ignition-API-Token: <contents of runtime.token>"
```

For other clients, add the same address and header in their MCP configuration, for example:

```json
{
  "mcpServers": {
    "ignition-runtime": {
      "url": "http://127.0.0.1:8088/data/mcp/production",
      "headers": {"X-Ignition-API-Token": "<contents of runtime.token>"}
    }
  }
}
```

## Step 7: Try it

Ask the agent "Which Tag providers and top-level folders are on the Gateway?" It should call
`tag_browse`. Then try "Read the current value of `[default]<some Tag path>`" or "Show the last hour of
history for that Tag".

Run `verify` whenever you want to check the deployment, and after every Gateway restart:

```bash
uv run --no-sync ignition-mcp setup-native verify \
  --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
  --gateway-token-file ~/.config/ignition-mcp/gateway.token \
  --mcp-token-file ~/.config/ignition-mcp/runtime.token \
  --server-config-name production --profile readonly
```

## Optional: approve database queries

`database_query` runs only Named Queries you list in the environment variable
`IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON` on the Gateway. Create the Named Query in an Ignition
project first, then follow the [Named Query registry](tools.md#the-named-query-registry) example.
The variable belongs to the Gateway's own process, so set it where the Gateway is started and
restart the Gateway.

## Turn on writes

Two things decide whether the agent can change something: the profile has to include the Tool, and
the policy has to list the target.

1. Add the targets to `policy.json`. This example lets the agent write Tag values under one air
   handler and shelve its alarms for up to an hour:

   ```json
   {
     "schemaVersion": 1,
     "serviceIdentity": "ignition-mcp-service",
     "auditMode": "best_effort",
     "allowlists": {
       "tag_write": ["[default]Plant/AHU1"],
       "alarm_shelve": ["prov:default:/tag:Plant/AHU1"],
       "alarm_unshelve": ["prov:default:/tag:Plant/AHU1"]
     },
     "alarmShelveMaxSeconds": 3600
   }
   ```

   How entries match is explained in the [Tool catalog](tools.md#what-every-runtime-write-tool-needs).

2. Run `plan`, then `apply`, with the new profile and without the two "create" flags. Your existing
   token and permissions stay as they are:

   ```bash
   uv run --no-sync ignition-mcp setup-native apply \
     --bundle-manifest dist/release/ignition-runtime-bundle-$V.manifest.json \
     --bundle-zip dist/release/ignition-runtime-bundle-$V.zip \
     --gateway-token-file ~/.config/ignition-mcp/gateway.token \
     --server-config-name production --profile operator \
     --policy-file policy.json \
     --backup-dir backups
   ```

3. Reconnect the AI application so it loads the new Tool list.
4. Try one write, then check the result in Ignition before you widen the allowlist.

To change only the policy, edit `policy.json` and run `plan` and `apply` again with the same profile.

## Common problems

| What you see | Cause | Fix |
| --- | --- | --- |
| `gateway-info` FAIL with HTTP 401 | The Gateway refused the setup token. | Check the token file and the token's access in the Gateway. |
| Exit code 2 with a message about the token file | The file has more than one line, or others can read it. | Keep one line, run `chmod 600` on it. |
| `module-installed` FAIL | The MCP Module is missing or not running. | Run `install-module`, or check the module list in the Gateway web interface. |
| `plan` shows `BLOCKED mcp-module` | Same as above. | Install the Module first. |
| `bundle-project` FAIL `UNMANAGED_SAME_NAME` or `MARKER_INVALID` | Another project already uses the name. | Pass `--bundle-project` with a new name. The command never takes over a project it did not create. |
| `BLOCKED server-config` about permissions | The endpoint is new and no permissions file was given. | Add `--server-config-permissions-file permissions.json`. |
| `inventory-tools` FAIL with `missing=[...]` or `extra=[...]` | The endpoint offers a different Tool list than the profile you named. | Pass the same `--profile` you deployed with, or run `apply` with the profile you want. |
| `verify` or `doctor` shows HTTP 401 or 403 on `mcp-initialize` | The endpoint needs a login. | Add `--mcp-token-file ~/.config/ignition-mcp/runtime.token`. |
| The agent cannot log in, although the token is right | The token only works over `https`, and the Gateway uses `http`. | Use the Gateway's `https` address. Or, on a trusted network, delete the token in the Gateway and the `runtime.token` file, then run `apply` again with `--create-runtime-token --runtime-token-insecure-channel`. |
| `compatibility` UNKNOWN | Your Gateway version was not tested. | Expected on other versions. It does not block anything. |
| A write Tool answers `operation_disabled` | The policy is missing or invalid. | Run `plan` and `apply` with a valid `--policy-file`. |
| A write Tool answers `permission_denied` | The target is not in the policy's allowlist for that Tool. | Add it to `policy.json` and run `apply`. |
| `database_query_list` returns nothing | The Named Query registry variable is not set on the Gateway. | Set it and restart the Gateway. |

The [runbook](../operations/runbook.md) explains every `doctor` line, every `plan` action and every
exit code.

## Notes for Windows

These instructions have not been run on Windows yet. See
[D31](../decisions/D31-windows-support-scope.md) for what is known.

- The wizard needs Git Bash or WSL. The `uv run --no-sync ignition-mcp setup-native ...` commands work
  in PowerShell. Replace the `\` line endings with a backtick, `` ` ``.
- In PowerShell, set the shared values with `$env:IGNITION_MCP_SETUP_GATEWAY_URL = "http://127.0.0.1:8088"`,
  `$V = Get-Content packages/ignition-runtime-bundle/BUNDLE_VERSION` and `$rev = git rev-parse HEAD`,
  then pass `--source-revision $rev`.
- Save the token file with `Set-Content "$env:USERPROFILE\.config\ignition-mcp\gateway.token" -Value "<token>"`.
  Windows does not check the file's permissions, so set them so only your account can read it.
- Windows has no `sha256sum`. To check a file, run `Get-FileHash <file> -Algorithm SHA256` and compare
  the result with the value in the `.sha256` file.
- If you cloned the repository before `.gitattributes` was added, the build fails with
  `must use LF line endings`. Run `git rm --cached -rq . && git reset --hard` once, or clone again.
  Both discard changes you have not committed.
