# Set up the REST server

> 中文版：[`setup-rest.zh-CN.md`](setup-rest.zh-CN.md)

This guide takes you from nothing to an AI agent that can read your Gateway through the
`ignition-rest` server. The first part sets up read-only access, which is safe to try on any
Gateway. The second part turns on writes one at a time.

Not sure this is the server you need? See [Which server do I need?](how-it-works.md#which-server-do-i-need)

## What you need

- An Ignition 8.3 Gateway that your computer can reach, and an administrator login for its web
  interface. The project was tested on 8.3.8 and 8.3.9.
- A computer running Linux, macOS or Windows. The server runs there and needs no installation on the
  Gateway.
- About 15 minutes.

The Windows steps have not been run on Windows yet. See
[Notes for Windows](#notes-for-windows).

## Step 1: Install Git and uv

Git downloads the code. `uv` installs Python and the project's packages for you, so you do not have
to install Python yourself.

On Linux or macOS, open a terminal and run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git --version        # if this fails, install Git from https://git-scm.com
```

On Windows, open PowerShell 7 and run:

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Git.Git -e
```

Close the terminal and open a new one, so it finds the new programs.

## Step 2: Download the project

```bash
git clone https://github.com/sheon-sek/ignition-mcp.git
cd ignition-mcp
uv sync --all-packages
```

`uv sync` downloads Python 3.11 or newer if needed, and every package the project uses. Run the
rest of this guide from the `ignition-mcp` folder.

## Step 3: Create an API token on the Gateway

The server uses an Ignition API token to call the Gateway. Agents never see this token.

1. Open the Gateway web interface, usually `http://<gateway-address>:8088`, and log in.
2. Go to the Security section and create a new API token.
3. Give it a security level that the Gateway allows to **read** its configuration. For writes later
   on, it also needs **write** access.
4. Copy the token when Ignition shows it. It looks like `name:long-random-text`, and Ignition shows
   it only once.

## Step 4: Write a settings file

Create a file called `ignition-rest.env` in the `ignition-mcp` folder:

```ini
IGNITION_MCP_GATEWAY_URL=http://127.0.0.1:8088
IGNITION_MCP_GATEWAY_API_TOKEN=paste-your-token-here
IGNITION_MCP_DATA_DIR=/home/you/ignition-mcp-data
```

- `IGNITION_MCP_GATEWAY_URL` is the address you open the Gateway with, without anything after the
  port.
- `IGNITION_MCP_DATA_DIR` is a folder where the server keeps its records. Use a full path. On
  Windows, put the path in single quotes, like `IGNITION_MCP_DATA_DIR='C:\ProgramData\ignition-mcp'`.
  The server creates the folder.

Put single quotes around any value that contains `"`, `{` or `\`, such as the JSON values later in
this guide. Without them the settings file loses the double quotes and the server rejects the value.

The file holds a secret, so keep it private. On Linux and macOS run `chmod 600 ignition-rest.env`.
Do not commit it to Git.

Every other setting is optional. They are all listed in the
[Configuration reference](configuration.md#rest-server-settings).

## Step 5: Start the server

```bash
uv run --no-sync --env-file ignition-rest.env ignition-rest-mcp
```

Leave this terminal open. The server runs until you press Ctrl-C.

In a second terminal, check that it is ready:

```bash
curl http://127.0.0.1:8000/health/ready
```

You want `"ready":true`. If you see `"registryState":"UNAVAILABLE"`, the server cannot reach the
Gateway or the token was refused. See [Common problems](#common-problems).

## Step 6: Connect your AI application

The MCP address is `http://127.0.0.1:8000/mcp`. The transport is Streamable HTTP.

With Claude Code:

```bash
claude mcp add --transport http ignition-rest http://127.0.0.1:8000/mcp
```

Most other clients take a JSON entry like this one. The exact key names differ between clients, so
check your client's MCP documentation:

```json
{
  "mcpServers": {
    "ignition-rest": {"url": "http://127.0.0.1:8000/mcp"}
  }
}
```

## Step 7: Try it

Ask the agent "Which Ignition version is my Gateway running?" It should call `gateway_info`. Then
try "List the projects on the Gateway" or "Which database connections are configured?"

The agent now has every read Tool. The [Tool catalog](tools.md#read-tools) lists them.

## Turn on authentication

Without authentication anyone who can reach the address can read, and nobody can write. Before you
share the server with other people or turn on writes, give each caller a token. Add to
`ignition-rest.env`:

```ini
IGNITION_MCP_AUTH_MODE=static-token
IGNITION_MCP_STATIC_TOKENS='{"my-laptop":{"token":"make-up-a-long-random-secret","scopes":["ignition.read"]}}'
```

To make a random secret, run `uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Restart the server. The client now has to send the secret as a header:

```bash
claude mcp remove ignition-rest
claude mcp add --transport http ignition-rest http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer make-up-a-long-random-secret"
```

In a JSON client configuration, add `"headers": {"Authorization": "Bearer make-up-a-long-random-secret"}`
next to `"url"`.

To let other machines connect, also set `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` and
`IGNITION_MCP_HOST=0.0.0.0`. The `development` profile only accepts connections from the same
computer.

## Turn on one write

Take one Tool and one target at a time. This example lets the agent change one database connection
called `MES`.

1. Give the caller's token the `ignition.config` scope:

   ```ini
   IGNITION_MCP_STATIC_TOKENS='{"my-laptop":{"token":"...","scopes":["ignition.read","ignition.config"]}}'
   ```

2. Turn on configuration writes, name the Tool, and name the target:

   ```ini
   IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
   IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
   IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"]}'
   ```

3. Make sure the Gateway API token from Step 3 has write access.
4. Restart the server and reconnect the client. `config_resource_update` now shows in the Tool list.
5. Ask the agent to make the change. It first reads the connection with `config_resource_get`, then
   sends the update with the `signature` it got.
6. Check the result in the Gateway web interface before you allow more.

To allow more Tools later, add them to both lists, separated by commas:

```ini
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update,config_resource_create
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"],"config_resource_create":["ignition/database-connection/MES_Test"]}'
```

The target format of every write Tool is in the [Tool catalog](tools.md#write-tools).

### Project and Perspective writes

`project_import` and the Perspective write Tools also need the Project writer:

```ini
IGNITION_MCP_PROJECT_WRITER_ENABLED=true
IGNITION_MCP_GATEWAY_ID=plant-gateway-1
IGNITION_MCP_MUTATION_OPERATIONS=perspective_view_upsert
IGNITION_MCP_MUTATION_TARGETS='{"perspective_view_upsert":["MES"]}'
```

Here the target is the project name. Before every project write the server saves a backup of the
project, which it keeps for 7 days.

### Exports

`project_export` and `tag_config_export` need `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`. The
result gives a download address on the server, `/artifacts/<id>`. Download it with the same
`Authorization` header the client uses.

## Run it as a service

To keep the server running after you log out, run the same command from your system's service
manager, such as systemd on Linux or a scheduled task or service wrapper on Windows. Use a data folder
outside temporary folders, and set `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` or `secured`.

## Common problems

| What you see | Cause | Fix |
| --- | --- | --- |
| The server stops at start with `ConfigurationError` | A setting is missing or wrong. The message names it. | Fix that line in `ignition-rest.env`. |
| `IGNITION_MCP_DATA_DIR is required` | The data folder is not set. | Add `IGNITION_MCP_DATA_DIR` with a full path. |
| `development profile may bind only to loopback` | `IGNITION_MCP_HOST` is not `127.0.0.1`. | Also set `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal`. |
| `/health/ready` shows `"registryState":"UNAVAILABLE"` | The server cannot reach the Gateway, or the token was refused. | Open the Gateway URL in a browser from the same machine. Check the token. Ask the agent to run `gateway_diagnose`. |
| A Tool is missing from the list | A requirement is missing. | Find the Tool in the [Tool catalog](tools.md) and check each requirement. |
| `permission_denied` | The caller's token lacks the scope, or the target is not in `IGNITION_MCP_MUTATION_TARGETS`. | Add the scope or the target, then restart. |
| `operation_disabled` | A switch is off, or the Tool is not in `IGNITION_MCP_MUTATION_OPERATIONS`. | Turn it on, then restart. |
| `conflict` | Someone changed the target after the agent read it. | Let the agent read it again. |

For any error, pass its `correlationId` to `operation_diagnose`, or search the server's log for it.

## Notes for Windows

These instructions have not been run on Windows yet. See
[D31](../decisions/D31-windows-support-scope.md) for what is known.

- Use PowerShell 7.
- The settings file and `uv run --env-file` work the same way.
- On Windows the server does not check file permissions on the data folder. Set the folder's
  permissions so only the account that runs the server can read it.
- If you cloned the repository before `.gitattributes` was added, some checks fail with
  `must use LF line endings`. Run `git rm --cached -rq . && git reset --hard` once, or clone again.
  Both discard changes you have not committed.
