# Generators and transfer equipment

## Sequence to rebuild

Utility loss → ATS or controller sees the loss after its delay (typically 1–3 s) → start signal → crank → reaches rated voltage and frequency (≤10 s for NFPA 110 Level 1, Type 10) → ATS transfers → UPS goes back from battery to rectifier, often with a walk-in ramp. Retransfer happens after utility returns plus a stability timer (often 5–30 min), then the cooldown.

Put a timestamp on each step from the Historian. The step with the missing or late timestamp is where the failure is.

## Failure modes

| Symptom | Likely causes | Evidence |
| --- | --- | --- |
| Fails to start | Starting battery or charger, fuel (valve closed, day tank empty, gelled or contaminated fuel), control switch not in AUTO, active shutdown not reset | Starting battery voltage trend; charger alarm; day tank level; controller mode Tag; the last test record |
| Starts, then shuts down | Overspeed, low oil pressure, high coolant temperature, overcrank, fuel starvation under load | The shutdown code on the controller; coolant and oil trends; how long it ran before the stop |
| Runs, ATS does not transfer | Voltage or frequency outside the ATS acceptance window, ATS in inhibit or test, a stuck motor operator, an ATS control power fault | Generator voltage and Hz at the time; ATS position and mode Tags |
| Unstable frequency or voltage on load | Governor or AVR fault, a large motor or UPS rectifier step, a paralleling load-share fault | Frequency trend against load steps; per-set kW in paralleled systems |
| Paralleled sets trip on reverse power | Load-share or sync failure, a governor fault on one set | Per-set kW that goes negative on one set just before the trip |
| Wet stacking or poor load response | Long periods of light-load running | Run hours at <30 % load from aggregates |

## STS (static transfer switch)

An STS transfers in ¼ cycle between two sources that must be in sync. Frequent transfers point to one source sagging. A failure to transfer (inhibited because the sources are out of phase, or an SCR fault) leaves the load on a degraded source. Read the preferred source, the active source, the sync status and the transfer counters.

## Fuel

Burn rate at the current load against the tank level gives the endurance. Check the fuel polishing and water-in-fuel alarms, and the history of the transfer pump from bulk tank to day tank (CountOn, DurationOn).
