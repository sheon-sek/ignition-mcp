# D09 — Ignition Runtime MCP Permission and Security Model

**Status:** DECIDED

## Context
`ignition-runtime` runs inside the official Ignition MCP Module, so it does not assume the same per-tool OAuth model as `ignition-rest`.

Runtime security is implemented through:
- MCP Server Config permissions;
- dedicated Ignition Security Levels;
- explicit Tool inventories;
- Tool-handler target policy.

## Shared permission vocabulary
- `READ`
- `CONFIG`
- `CONTROL`
- `ADMIN`

These are semantic classes, not Runtime OAuth scopes.

## One Runtime MCP Bundle
Maintain one Runtime MCP Bundle project containing all supported primitives:
```text
tools/
resources/
prompts/
```
Do not duplicate read/operator/config bundles or primitives.

Tool permission profiles below are unchanged. Resource and Prompt inventories are part of the same bundle and are tracked for deployment verification, but they are not a substitute for Tool-level authorization.

## Standard profiles
### readonly
Explicit reviewed `READ` tools only.

### operator
Explicit reviewed:
- READ
- CONTROL

### configurator
Explicit reviewed:
- READ
- CONFIG

Does not automatically include CONTROL.

### full
Explicit reviewed:
- READ
- CONTROL
- CONFIG

### admin
No standard Runtime ADMIN profile in v1. Most Gateway administration belongs to `ignition-rest`.

## Default
Default installed profile: `readonly`.

Mutation-capable profiles must be explicitly selected.

## Multiple Runtime servers only when needed
Normal deployment remains:
```text
ignition-rest
ignition-runtime
```

If one Gateway serves differently privileged Agents, create separate Server Configs such as:
- `ignition-runtime-readonly`
- `ignition-runtime-operator`
- `ignition-runtime-configurator`

Use one Server Config per actual privilege profile, not multiple duplicated Tool bundles.

## Explicit Tool inventory
Production Server Configs must not use Tool wildcard `*`.
Each profile is a version-controlled explicit Tool allowlist.

A newly added Tool is not automatically included in any profile, even readonly.
Unexpected extra discovered Tools cause verification to fail closed.

## Expected Resource and Prompt inventory
Deployment verification additionally maintains the **exact expected Resource and Prompt inventory** of the bundle (`resources/list`, `prompts/list`). Unexpected extra primitives are reported, and the inventory is version-controlled alongside the Tool allowlist.

Do **not** invent a Resource/Prompt allowlist, wildcard, or authorization control. The target MCP Module has not been shown to expose Resource/Prompt-level authorization equivalent to Server Config Tool selection. Until verified per Module version, Resource/Prompt exposure is governed only by the Server Config and Security Level behavior the Module actually implements; a Resource/Prompt inventory is a verification artifact, not a new security boundary.

## Dedicated Security Levels
Use dedicated MCP Runtime Security Levels in production rather than a broad generic Authenticated level.
Exact Security Level tree compatibility must be verified per Ignition/MCP Module version because the module is Early Access.

## API keys
Prefer separate API credentials per privilege profile.
Avoid one shared Runtime super-key.

## Target enforcement
Server Security Level answers whether a caller can enter a Server/profile.
Tool handler policy answers whether the operation may target a specific Tag/provider/DB connection/etc.

Server permission alone is insufficient.

## `restricted` field
Do not treat an unverified Tool-resource `restricted` field as the security boundary.
Use verified Server Config permissions, explicit Tool selection, and handler policy.

## Caller identity
Do not fabricate/infer a human identity if the Runtime Tool context does not expose one reliably.

## Setup/security smoke test
Deployment verification must include:
1. Runtime Bundle project imported/saved.
2. Security Level/config present.
3. MCP initialize succeeds.
4. `tools/list` succeeds.
5. Actual Tool inventory exactly matches expected profile.
6. Expected Resource inventory matches (`resources/list`).
7. Expected Prompt inventory matches (`prompts/list`).
8. Read smoke Tool succeeds.
9. Security access behaves as expected.

Unexpected extra Tools fail closed.

```yaml
decision: D09
status: DECIDED
runtime_bundle_count: 1
runtime_bundle_primitives: [tools, text_resources, prompts]
default_profile: readonly
production_tool_wildcard: forbidden
explicit_tool_inventory: required
expected_resource_inventory: required
expected_prompt_inventory: required
resource_prompt_allowlist: unverified_module_capability
dedicated_security_level: required
target_policy_in_handler: required
```

## Pre-D26 consistency amendment
Renamed the “One Tool Bundle / Runtime Tool project” model to one **Runtime MCP Bundle** covering Tools + Text Resources + Prompts, added exact expected Resource/Prompt inventories to deployment verification, and recorded explicitly that no Resource/Prompt allowlist or authorization mechanism may be invented before the Module proves one. Permission profiles, Security Levels, API-key guidance, and target policy are unchanged.
