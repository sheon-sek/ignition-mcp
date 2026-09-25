# Ignition MCP

> 中文版：[`README.zh-CN.md`](README.zh-CN.md)

Ignition MCP lets an AI assistant, such as Claude, work with an
[Inductive Automation Ignition](https://inductiveautomation.com/) 8.3 Gateway. The assistant can read
Tags, history, configuration and projects. It can also change them, but only the things you allow,
one switch at a time.

It uses MCP, the Model Context Protocol, the standard way AI applications connect to outside Tools.
Any MCP client that supports Streamable HTTP works, such as Claude Code, Cursor or VS Code.

## What you can ask

Once it is set up, you can ask the assistant things like:

- "What is the current temperature on all the AHU Tags under `[default]Plant`?"
- "Show the average and maximum of this Tag over the last 24 hours."
- "Which alarms are shelved right now?"
- "Which database connections are configured on the Gateway?"
- "Add a Perspective View that shows these three Tags." (after you turn on writes)

## Two servers

The project has two MCP servers. You can install one or both.

| | `ignition-rest` | `ignition-runtime` |
| --- | --- | --- |
| Covers | Gateway information, configuration resources, projects, Perspective Views, audit logs, alarm notification pipelines | Tag values and configuration, UDTs, Tag history, alarm shelving, approved database queries |
| Runs | as its own program, on any machine that can reach the Gateway | inside the Gateway, through the official Ignition MCP Module |
| Setup guide | [Quick start](docs/guide/quick-start.md) | [Quick start](docs/guide/quick-start.md) |

If you are unsure, start with the Analysis Assistant role. It reads Tags and history, which is the
most common request, and it changes nothing. [How it works](docs/guide/how-it-works.md#which-server-do-i-need)
has a longer table.

## Documentation

| I want to... | Read |
| --- | --- |
| understand what the servers do and how a call works | [How it works](docs/guide/how-it-works.md) |
| install both servers, step by step | [Quick start](docs/guide/quick-start.md) |
| find out why a Tool is missing, or what a Tool needs | [Tool catalog](docs/guide/tools.md) |
| look up a setting | [Configuration reference](docs/guide/configuration.md) |
| upgrade, change the policy, or understand a `status` line or an exit code | [Operations runbook](docs/operations/runbook.md) |
| look up an error code | [How it works: Errors](docs/guide/how-it-works.md#errors) |

## Safety

The assistant starts with read access only. Every kind of change stays off until you turn it on.

- **Writes are off by default.** A `prod` deployment leaves every write off. A `dev` deployment turns
  on the Mutations the Engineer role needs, and only for that role's token.
- **You list what may change.** Each write Tool can only touch the targets you list, such as one
  Tag folder or one project. A Tool with an empty list can change nothing.
- **Changes need a fresh read.** Most change Tools need a token from a recent read. If someone else
  changed the target in the meantime, the call fails with `conflict` and changes nothing.
- **Nothing is retried blindly.** If the server cannot tell whether a change happened, it says
  `outcome_unknown` and does not try again.
- **Some things are always off-limits.** API tokens, security settings, user sources, module
  settings and the servers' own policy cannot be changed through any Tool.
- **No free-form SQL or web requests.** The assistant can only run database queries you approved by
  name, and only call the Gateway through the Tools listed in the catalog.
- **Every answer has a size limit.** An answer that is too large fails with `limit_exceeded`. It is
  never cut short without notice.
- **Secrets stay out.** Tokens and passwords never appear in Tool answers, logs or audit records.

## Status

Version 1 is complete. The live tests passed on Ignition 8.3.8 and 8.3.9 with MCP Module
`1.3.5.2026021307-SNAPSHOT`. The project records these as `VERIFIED` test results and does not claim
production support for any version. Details are in
[How it works: Tested versions](docs/guide/how-it-works.md#tested-versions).

Known limitations in v1:

- The alarm status, alarm journal and alarm acknowledge Tools are switched off. Ignition's alarm
  query functions cannot limit how many rows they return, so these Tools cannot promise a bounded
  answer.
- Windows has not been tested. The runbook includes Windows steps marked as not run on Windows yet.
- The MCP Module does not publish output schemas for Runtime Tools. This repository publishes them
  in `contracts/schemas/`.

## For contributors

| Path | Contents |
| --- | --- |
| `packages/ignition-rest-mcp/` | The REST server and the `ignition-mcp` command. Its [README](packages/ignition-rest-mcp/README.md) describes the Tools' detailed behavior. |
| `packages/ignition-runtime-bundle/` | The Runtime Tools as an Ignition project. See its [README](packages/ignition-runtime-bundle/README.md). |
| `contracts/` | The Tool contracts, output schemas and shared error codes that both servers are checked against. |
| `tooling/` | The bundle builder, the contract linter and CI helpers. |
| `tests/` | Live Gateway test setups and the recorded test evidence. |
| `docs/decisions/` | Architecture decision records, kept by the contributors. They are binding. |
| `docs/development/` | Development records, kept by the contributors, one per delivery. |

Checks to run before a change:

```bash
uv sync --all-packages
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
```

The test suite needs Java 11 to run the Runtime scripts under Jython. Contributor rules are in
[`AGENTS.md`](AGENTS.md), and the project vocabulary is in [`CONTEXT.md`](CONTEXT.md).

## License

GPL-3.0-only. See [`LICENSE`](LICENSE).
