---
name: power-systems
description: Failure modes and diagnostic signatures of critical-power equipment (UPS, batteries, generators, ATS/STS, switchgear, breakers and protection relays, transformers, PDUs/RPPs, busway, power quality and metering). Use when an EPMS alarm, trip, transfer or electrical reading is involved.
---

# Power systems

Principles for every electrical fault:

- **Upstream first.** One event seen on many meters at the same moment comes from a common source: the utility, the main switchboard or a transformer. Find the highest point in the single-line diagram where the event appears.
- **A trip is a symptom.** The protection operated correctly until the evidence proves otherwise. Ask which element operated (overcurrent, ground fault, differential, under- or over-voltage, reverse power) and what current it saw.
- **Transfers expose latent faults.** A dead battery string, a stuck ATS or a generator that fails to start usually shows up only on demand. Check when each item last passed a test.
- **Compare A and B.** A difference between two redundant paths under the same load points to the path that differs.

Load the reference for the equipment involved:

| Equipment | Reference |
| --- | --- |
| UPS, rectifier, inverter, static bypass, battery strings, BMS for batteries | [references/ups-battery.md](references/ups-battery.md) |
| Generator, ATS, STS, paralleling gear, fuel | [references/generator-transfer.md](references/generator-transfer.md) |
| Switchgear, breakers, protection relays, transformers, PDU/RPP, busway, grounding | [references/distribution-protection.md](references/distribution-protection.md) |
| Sags, swells, harmonics, transients, power factor, suspect meter readings | [references/power-quality.md](references/power-quality.md) |
