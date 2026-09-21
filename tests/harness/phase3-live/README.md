# Phase 3 live Gateway harness (G3)

Ephemeral CI-owned evidence for the D08 + D18 mutation-safety chain and the
D16 Project transaction machinery (Phase 3 / G3). The harness provisions an
exact-patch Ignition Gateway (8.3.8 required row, 8.3.9 compatibility
candidate), installs the checksum-pinned official MCP Module and the exact
`release`-built Runtime Bundle ZIP (its SHA-256 and manifest are recorded in
the evidence row), and a run-unique disposable Project
`mcp_g3_<run_id>_<attempt>`.

## What the driver proves live

1. L5 guards: explicit CI marker + Gateway identity match + run-unique
   disposable project + writer-scoped allowlists. The driver checks all of these before any import and fails
   closed when one is missing.
2. `setup-native doctor / plan / verify` against the real Gateway and the
   deployed bundle (`plan` reports only NO CHANGE for provisioned items).
3. Sensitive exports ON: exact 16-Tool REST inventory (zero mutation Tools),
   `project_export` → READY CONFIDENTIAL/EXPORT artifact, `GET` body
   SHA-256 == metadata == quoted `ETag`, `Repr-Digest`, HEAD/GET header
   parity, `artifact_list`/`artifact_info` visibility, `tag_config_export`
   JSON artifact, `operation_diagnose` same-principal record and unknown-ID
   `not_found`, D18 audit triple for the export.
4. Fingerprint stability: two exports with no change ⇒ identical `pcf1`
   (instability is recorded honestly and marks Project mutation
   not-concurrency-safe on that row).
5. Authn/authz layers live: invalid/expired JWT rejected; a read-only JWT
   and a config JWT against a non-allowlisted project are each denied at the
   executor with exactly one `decision` audit row, zero `attempt` rows and
   the Gateway Project fingerprint unchanged.
6. D16 transactions through the internal service and the guarded executor:
   `NO_CHANGE` (no backup, no dispatch), `COMMITTED` (verified by re-export,
   recovery artifact persisted with the lock released), `CONFLICTED` from a
   real injected external drift between A and A′ (transaction `attempt` row
   absent, `import_dispatched=false`, recovery lock released per the D16
   release set).
7. Sensitive exports OFF: the two export Tools disappear from discovery and
   an explicit call is refused at call time; inventory stays mutation-free.

## Layout

- `docker-compose.yml`: Gateway-only compose (no database is needed).
  `GATEWAY_IMAGE` must be an exact patch tag, ports bound to 127.0.0.1 only.
- `driver.py`: the G3 driver (library consumer of `ignition_rest_mcp`;
  exit 0 = row verified, 3 = verified with the recorded binding limitation,
  2 = failed stage, recorded honestly in `observations.json`).
- `inventory_gate_off.py`: the gate-off discovery and call-time probe.
- `harness_common.py`: frozen expected inventories + the MCP wire client.
- `fixture-project/`: the disposable Project template (loaded by directory
  copy; content is CI-only).
- `gateway-config/`: the `phase3-runtime` MCP server-config resource.

The workflow `.github/workflows/phase3-live-g3.yml` runs only from trusted
pull requests under the `phase3-live` environment (no protection rules by
owner decision; the deviation is recorded in every row). Harness
provisioning is test-only and is not a second installer; `apply` and
`install-module` land in later phases. No evidence row may promote a tuple
to `SUPPORTED`, and G0–G2 evidence is never rewritten.
