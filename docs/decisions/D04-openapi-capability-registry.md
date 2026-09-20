# D04 — OpenAPI Capability Registry Lifecycle

**Status:** DECIDED

## Source of truth
The latest successfully parsed target-Gateway `/openapi.json` snapshot is the authoritative capability source. Gateway version and installed/healthy module information are lightweight change signals only.

## Snapshot
Immutable snapshot containing at least:
- Gateway version;
- module inventory/versions;
- environment fingerprint;
- OpenAPI SHA-256;
- endpoint/resource inventory;
- semantic capabilities;
- fetched time;
- registry generation;
- state.

Refresh builds a complete new snapshot and swaps atomically only after successful validation.

## States
- `READY`: valid current snapshot.
- `STALE`: previous valid snapshot exists, but current refresh/fingerprint check failed.
- `UNAVAILABLE`: no successful snapshot since process start.

Transient Gateway outage does not erase capabilities. In `STALE`, tools remain based on last-known capability while calls may fail with `gateway_unavailable`.

## Refresh
- Startup: full OpenAPI fetch.
- Watcher default: 60 s lightweight environment fingerprint check.
- Full refresh on startup, fingerprint change, Gateway upgrade/recovery/restore, relevant module change, manual refresh, or detected schema/capability mismatch.
- Refresh is singleflight; no unbounded refresh queue.
- Failed refresh preserves last valid snapshot.
- Watcher/task must cancel cleanly on shutdown.

## Cold start failure
FastMCP stays online in `UNAVAILABLE`, exposing only basic diagnostics/connectivity/capability-status surfaces.
Old disk OpenAPI is not authoritative for enabling tools on cold start.

## Dynamic visibility
Visible tools are:
```text
implemented ∩ gateway-supported ∩ deployment-enabled ∩ caller-authorized
```
Unsupported tools are hidden from `tools/list`, but call-time capability enforcement is still mandatory.

Send list-changed notifications only when the public semantic tool/resource set actually changes.

## Mutation mismatch
A refresh may follow mutation failure, but mutation requests are never automatically replayed.

```yaml
decision: D04
status: DECIDED
source_of_truth: latest-successful-openapi-snapshot
snapshot_immutable: true
atomic_swap: true
refresh_singleflight: true
watcher_interval_seconds: 60
states: [READY, STALE, UNAVAILABLE]
disk_cache_authoritative_on_cold_start: false
hide_unsupported_tools: true
call_time_capability_check: true
automatic_mutation_replay: false
```
