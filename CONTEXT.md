# Ignition MCP

Two MCP servers that let AI agents read and, under layered safety rules, change an Inductive Automation Ignition Gateway. `ignition-rest` wraps Native REST; `ignition-runtime` is a bundle of Jython Tools hosted by the official MCP Module.

## Language

### Planes and delivery

**Plane**:
One of the two capability surfaces: the REST plane (`ignition-rest`) or the Runtime plane (`ignition-runtime`). An operation belongs to exactly one plane.
_Avoid_: server side, backend

**Gate**:
A binding phase exit (G0–G6) that needs live-Gateway evidence before the next phase may start.
_Avoid_: milestone (when binding is meant)

**Milestone**:
A non-binding checkpoint inside a phase that produces its own live evidence but does not open or close a Gate.
_Avoid_: sub-gate, G4a

**Module install**:
Putting a trusted local MCP Module file on a Gateway through `setup-native install-module`. The operator names the file's hash and accepts its certificate and EULA explicitly.
_Avoid_: module deploy, module upgrade (when a first install is meant)

**Module upgrade**:
A Module install that replaces an installed MCP Module with a higher build. It needs explicit acknowledgement, and a lower build is always refused.
_Avoid_: update, reinstall

**Bundle upgrade**:
An `apply` that replaces a managed Runtime Bundle Project with a newer bundle version. It needs explicit acknowledgement. The v1 "upgrade path" means this, not a Module upgrade.
_Avoid_: redeploy, bundle update

### Setup

**Deployment environment**:
The named stance a setup run takes, `dev` or `prod`. It decides the defaults only: `dev` defaults to the widest Profile and generates every credential and document the endpoint needs; `prod` keeps the conservative defaults. The safety rules apply in both.
_Avoid_: mode, stage, preset

**Explicit acceptance**:
The operator's recorded yes to one named risk or legal term (a certificate, a EULA, a wide Target allowlist, an unencrypted token channel), given in the same run that needs it. A default never counts as acceptance, and nothing the operator accepted is left out of the run's report.
_Avoid_: confirmation (when a named risk is meant), consent, opt-in

**Assistant role**:
A kind of AI agent the deployment serves, with its own endpoint, Security Level and credential. There are two: the Analysis Assistant and the Engineer Assistant.
_Avoid_: persona, agent type, user

**Analysis Assistant**:
The Assistant role that inspects and troubleshoots a Gateway as an engineer would. It reads and diagnoses; it never performs a Mutation.
_Avoid_: read-only agent, monitor agent

**Engineer Assistant**:
The Assistant role that develops Ignition projects as an Application Engineer would. It may perform Mutations within the Target allowlists.
_Avoid_: developer agent, admin agent

### Mutation safety

**Mutation**:
A Tool call that can change Gateway state. Every Mutation has exactly one Mutation class.
_Avoid_: write (except for `tag_write`), action

**Mutation class**:
The effect category of a Mutation: `CONFIG`, `CONTROL` or `ADMIN`. It decides which scope and which deployment enablement the Mutation needs.
_Avoid_: permission level, mutation type

**Target allowlist**:
The deployment-owned list of targets (projects, Tag paths, Alarm paths, config resources) a Mutation may touch. Empty means none; allowing all needs an explicit `*`.
_Avoid_: whitelist, target filter

**Refused resource type**:
A Gateway config resource type that generic config Mutations never touch, whatever the Target allowlist says. Types not yet classified are treated as refused.
_Avoid_: blacklist, admin type

**Preflight**:
The check of every item in a Mutation batch (input, Target allowlist, Precondition token) before any of them executes. If any item fails, none executes.
_Avoid_: dry run, validation pass

**Runtime Target Policy**:
The deployment-owned document on the Gateway, outside the Runtime bundle, that holds the Runtime plane's Target allowlists. A Runtime Mutation fails closed when it is missing or malformed.
_Avoid_: bundle config, runtime allowlist file

**Precondition token**:
The value a caller passes with a Mutation that is valid only for the target state the caller last read. On a mismatch the Mutation is refused with `conflict`. There are three kinds: Resource signature, Tag config fingerprint and Project fingerprint.
_Avoid_: version, ETag, revision

**Resource signature**:
Ignition's own Precondition token for a Gateway config resource. The Gateway enforces it itself.
_Avoid_: hash, checksum

**Tag config fingerprint**:
A repo-defined Precondition token computed over a Tag's configuration. Because Ignition does not enforce it, a small race window remains between the check and the change.
_Avoid_: tag hash, tag version

**Project fingerprint**:
The repo-defined `pcf1` Precondition token over a Project's logical content (D16).
_Avoid_: ZIP hash, project checksum

**Service identity**:
The configured, non-human identity a Runtime Mutation is attributed to when the Module exposes no verified caller. It is never supplied by the caller.
_Avoid_: service account, ack user

**Mutation principal**:
The verified caller identity a Mutation is attributed to and authorized against. A `jwt` subject or a named static token can be a Mutation principal; an `auth=none` caller never is.
_Avoid_: user, actor, caller identity

**Named static token**:
A deployment-configured `static-token` credential with its own name and scope set (D07 Phase 4 amendment). Its name is its Mutation principal; its value is a secret that never leaves the authentication module.
_Avoid_: API key, shared secret

**Native outcome**:
The per-item result Ignition itself reports for a Mutation, such as a Tag write QualityCode. For `tag_write` it is the item's outcome.
_Avoid_: result code, status

**Observed state**:
A bounded read-back taken after a Mutation and reported as data. It does not by itself decide success, because live values may legitimately differ from what was written.
_Avoid_: verification result, confirmed value

**Outcome unknown**:
The outcome of a Mutation that may have executed but whose final state could not be established. It is never automatically replayed.
_Avoid_: timeout, failed

### Perspective authoring

**Logical resource path**:
The name a caller uses for a Perspective resource, such as `Pages/Overview` for a View. The server maps it to the archive; callers never pass archive or filesystem paths.
_Avoid_: file path, ZIP path, resource path

**Local resource**:
A resource defined in the Project itself, so it appears in that Project's export. Perspective Mutations only ever change Local resources.
_Avoid_: own resource, child resource

**Inherited resource**:
A resource that a Project gets from an ancestor Project and does not define locally. A Mutation that would create a local override of one is refused, never performed silently.
_Avoid_: parent resource, override
