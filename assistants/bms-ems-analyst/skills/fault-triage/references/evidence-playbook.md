# Evidence playbook: getting facts out of Ignition

## Finding Tag paths

- Start broad: `tag_browse` on the provider root (for example `[default]`) and walk the folders. Sites usually file Tags by building, then system, then equipment (`Site/DH1/CRAH-01/...`).
- Search by name: `tag_query` with `provider` and `namePattern` (`*UPS*`, `*CRAH*`, `*ATS*`, `*CH-0*`). Filter with `tagType=UdtInstance` to find the equipment objects themselves.
- Learn one equipment type: `udt_type_list`, then `udt_type_get` on the matching type (`UPS`, `Chiller`, `Breaker`). The UDT lists every member Tag an instance has, which tells you what can be measured without guessing.
- `tag_get_config` on a single Tag shows its source (OPC item path, BACnet object, expression or memory), its scaling, its history settings and its **alarm definitions** (mode, setpoint, deadband, delays, priority). A memory or expression Tag is not a field measurement. Note that when you weigh it.

## Reading the present

- `tag_read` takes up to 500 paths per call. Read the whole equipment group in one call so the timestamps line up.
- Read the `[System]` provider for Gateway-side health: browse `[System]Gateway` for device and OPC connection status, database status, and Gateway performance (CPU, memory, clock drift).
- A Tag whose timestamp is much older than its neighbours is either stale or unchanging. `ot-data-layer` tells you how to tell which.

## Reading the past

- `historian_browse` confirms the path is historised and gives the exact historical path.
- `historian_query_series` returns raw points: at most 50 paths, 7 days, and 25,000 points in total (paths × sampleCount). Use it for the minutes around an event, with high `sampleCount` on few paths.
- `historian_query_aggregate` computes over the **whole** range, not in buckets. To get a trend over a long period, call it once per consecutive window (for example one call per day). Useful aggregates:
  - `Minimum`, `Maximum` and `Range` for excursions.
  - `CountOn` and `DurationOn` on a status Tag for starts, run hours and short-cycling.
  - `PctBad` for how much of the window the data itself was bad.
  - `StdDev` for hunting control loops.
- To compare, query the same window on the redundant partner (A and B side, the lead and lag unit). A difference that shows on one side only points to that side.

## Rebuilding an alarm

1. Get the alarm text and time from the user.
2. Read the alarm definition with `tag_get_config` on the source Tag: setpoint, mode, deadband, on-delay.
3. Pull the Historian series around the alarm time. Check whether the value really crossed the setpoint for longer than the on-delay.
4. Check `alarm_shelved_list`. A shelved alarm on related equipment can hide the real first event.
5. Check `alarm_pipeline_status` to see whether notifications went out, and to whom.

If the definition does not match what the value did, the alarm configuration is a candidate cause.

## What changed

- `audit_query` shows Gateway changes, Tag writes and configuration edits by user and time. Changes are the most common root cause, so check it on every incident.
- `config_resource_search` with keywords (`opc`, `device`, `database`, `historian`), then `config_resource_list` or `config_resource_get`, shows how device connections and data sources are configured. Compare them with what the symptoms need.
- `database_query_list` shows the approved queries. The site may have added alarm history, work orders, or maintenance logs there. Run the relevant one with `database_query`.

## When the Tools themselves fail

- `gateway_diagnose` shows whether the REST server can reach the Gateway and what it knows about the Gateway's API.
- `bundle_info` confirms that the Runtime server responds.
- `operation_diagnose` with a `correlationId` from an earlier error shows how far that call got.
