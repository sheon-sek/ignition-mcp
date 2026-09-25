# Tag quality, timestamps and Historian gaps

## Common Ignition quality codes

The code names vary slightly by Gateway version. Use the name the Gateway reports.

| Quality | Meaning | Next step |
| --- | --- | --- |
| `Good` | The device answered and the value was accepted | Trust the transport. The sensor can still be wrong. |
| `Uncertain_LastKnownValue` and other `Uncertain_*` | Ignition is showing the last good value; the live source is not currently giving a new one | Treat the value as history, not as the present. Check the device connection. |
| `Bad_Stale` | The Tag has not been updated within its expected scan period | A scan class or poll overrun, or the device stopped answering |
| `Bad_NotConnected`, `Bad_GatewayCommOff` | The device connection is down, or comms are turned off | `[System]Gateway` connection status; `config_resource_get` on the device connection |
| `Bad_Disabled` | The Tag or its device is disabled | Who disabled it, and when? `audit_query` |
| `Bad_NotFound`, `Error_Configuration` | The address does not exist or the Tag is set up wrong | `tag_get_config` for the OPC item path, BACnet object or register; a recent device firmware or point-map change |
| `Bad_AccessDenied` or `Bad_Unauthorized` | Security on the device or the OPC server | A recent password or certificate change |

## Timestamps

- A Tag's timestamp is the time of the last value change, or of the last scan when the Tag is configured that way. A slowly changing value (a setpoint, a counter at rest) can have an old timestamp and still be healthy. Compare it with a Tag on the same device that should be changing.
- All Tags on one device sharing the same frozen timestamp → the device or its connection stopped.
- Timestamps in the future, or events out of order between systems → clock drift. Check the Gateway's clock-drift data in `[System]`, and NTP on the controllers and meters. An EPMS event recorder with a drifted clock can reorder the sequence of an incident by seconds. Say so when you rebuild a timeline across systems.

## Historian gaps and artefacts

- **Gaps**: the Gateway was down, the store-and-forward buffer overflowed, the Tag had history disabled for a while, or the quality was Bad for that span. Use `PctBad` over the window, and compare with a `[System]` Tag's history for Gateway downtime.
- **Straight lines between points** can be interpolation across a gap, or deadband compression. `tag_get_config` shows the history deadband and the sample mode. A 1 °C deadband hides a 0.8 °C oscillation.
- **Min/max hidden by aggregation**: an average over a window hides spikes. Use `Maximum` and `Minimum`, or raw points around the event.

## Physically implausible values

- An exact round number, or the range limit (0, −1, 32767, 65535, −40, 999.9): a sensor, mapping or scaling fault.
- The value is correct but in the wrong unit: °F against °C, kW against W, the wrong multiplier for a Modbus register.
- The value is right but on the wrong point: a mapping swap after a controller replacement. Two neighbouring points exchanging behaviour at the time of the change is the tell.
