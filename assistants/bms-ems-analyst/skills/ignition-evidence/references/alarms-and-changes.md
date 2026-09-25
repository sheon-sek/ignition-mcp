# Alarms and changes

## Rebuilding an alarm

No Tool lists active alarms or alarm history, unless the site added an approved Named Query for it (`database_query_list`).

1. Get the alarm text and time from the user.
2. Read the alarm definition with `tag_get_config` on the source Tag: setpoint, mode, deadband, on-delay.
3. Pull the Historian series around the alarm time. Check whether the value really crossed the setpoint for longer than the on-delay.
4. Check `alarm_shelved_list`. A shelved alarm on related equipment can hide the real first event.
5. Check `alarm_pipeline_status` to see whether notifications went out, and to whom.

If the definition does not match what the value did, the alarm configuration is a candidate cause.

## What changed

Changes are the most common root cause, so check them on every incident.

- `audit_query` shows Gateway changes, Tag writes and configuration edits by user and time.
- `config_resource_get` on the device connections and data sources the symptoms depend on. Compare the settings with what the symptoms need.
- `tag_get_config` on the affected Tags: scaling, source address, alarm setpoints.
- Operator actions, maintenance and IT moves that the user reports.
