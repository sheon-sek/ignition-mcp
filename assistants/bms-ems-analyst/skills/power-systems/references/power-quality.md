# Power quality and metering

## Events

| Event | Signature | Common source |
| --- | --- | --- |
| Sag or dip | Voltage 10–90 % of nominal for 0.5 cycle to 1 min | Utility faults, large motor starts, transformer energisation. Many meters at once means the source is upstream. |
| Swell | Voltage above 110 % briefly | A large load dropped, a single line-to-ground fault on an ungrounded system |
| Interruption | Voltage below 10 % | Utility or breaker operation |
| Transient | µs to ms spikes | Capacitor bank switching, lightning, a vacuum breaker switching an inductive load |
| Harmonics | THD-V above about 5 %, THD-I high | Rectifier loads (UPS, VFDs, IT PSUs); resonance with power-factor correction capacitors |
| Frequency deviation | Hz outside ±0.5 Hz | On generator: governor response to a load step |

Compare against the ITIC (CBEMA) curve: IT PSUs ride through about 20 ms at 0 V. A sag that crashed servers but sits inside the curve points to something downstream (a PSU, the STS, a PDU) rather than the utility.

## Harmonic heating

Triplen harmonics (3rd, 9th) add up in the neutral. A hot neutral, a transformer running hot at moderate kVA, or a PF capacitor bank tripping on overcurrent are the classic signs of harmonics.

## Suspect metering

Before trusting a strange power reading, rule these out:

- **CT polarity reversed**: negative kW on one phase or a PF sign flip. Common after maintenance.
- **Wrong CT ratio or scaling**: the reading is off by a constant factor from the upstream and downstream meters that should sum to it. Check the Tag's scaling with `tag_get_config`.
- **Missing PT phase**: one voltage reads 0 or about 58 % while the load clearly runs.
- **Energy counter rollover or reset**: a sudden drop in kWh.

A bus should balance: the sum of the feeders ≈ the main, within 2–5 %. A persistent gap points to a metering fault or an unmetered load.
