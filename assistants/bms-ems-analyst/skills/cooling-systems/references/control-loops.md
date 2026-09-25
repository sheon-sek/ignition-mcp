# Control loops and field devices

## Command against feedback

For every actuated device, read the **command** (the output from the BMS) and the **feedback** (the actual position, speed or status) together:

| Command | Feedback | Meaning |
| --- | --- | --- |
| On or 100 % | Off, 0 %, or stuck at an old value | Equipment side: a tripped VFD, a local HOA switch in OFF or HAND, a failed actuator, a lost power or control fuse |
| Changes | Follows with a large lag or overshoot | A slow actuator, a mechanical bind, a badly tuned loop |
| Flat or odd | Normal | Controller side: the sequence, a setpoint, an override, the input sensor |
| No feedback point exists | — | The status is unproven. Say so, and ask for a field check. |

A HAND/OFF/AUTO switch left in HAND is one of the most common "the BMS lost control" causes after maintenance.

## PID behaviour signatures (Historian)

- **Hunting**: a regular oscillation with a constant period. Gain too high or integral too fast, or a valve oversized for the load. Check `StdDev` over the window and the period in the raw series.
- **Sticky valve (stiction)**: a sawtooth on the output with square-ish steps in the process variable.
- **Wind-up**: the output pinned at 0 or 100 % long after the error reversed.
- **Offset**: steady-state error with P-only control, or an integral term disabled.
- **Two loops fighting**: two loops on the same process variable (a supply-air loop and a return-air loop, two units' humidity loops) oscillate in anti-phase.

## Sensor faults

- **Flatline** at an exact value, or at the range limit (for example −40 or 150 °C, 0 or 4 mA equivalent): an open or shorted sensor, or a wiring fault.
- **Drift**: a slow divergence from the redundant or neighbouring sensor over weeks. Compare daily averages with `historian_query_aggregate` per day.
- **Noise or spikes**: loose termination, a missing shield, EMI from a VFD cable run alongside it.
- **Placement**: a supply-air sensor in stratified air, or a return-air sensor near the hot exhaust of a single rack. The reading is correct but not representative.

## VFDs

A fault code comes first (overcurrent, overvoltage on decel, ground fault, overtemperature, loss of phase). Repeated overvoltage trips happen when a fan decelerates too fast with no braking. Overtemperature points to the VFD's cooling fan or a hot room. A drive in local or hand control ignores the BMS speed command.

## Sequences and failover

- **Lead/lag rotation**: check that the lag unit really starts when the lead fails. A lag that has never run since commissioning is a latent fault. Run hours from `DurationOn` show it.
- **Failover delays**: the time from the lead failure alarm to the lag proving flow. Compare it with the design value.
- **Controller restart**: after a controller power loss, units may return in a default mode (off, or local setpoints). Check the timestamps of all of the controller's points for a shared gap.
