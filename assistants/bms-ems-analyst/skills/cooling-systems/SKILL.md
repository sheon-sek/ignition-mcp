---
name: cooling-systems
description: Failure modes and diagnostic signatures of data centre and building cooling (chillers, cooling towers, pumps, CHW/CW loops, economisers, CRAH/CRAC, AHUs, containment, humidity) and of the BMS control loops, valves, dampers and VFDs that run them. Use when a BMS alarm, temperature, pressure, flow or HVAC behaviour is involved.
---

# Cooling systems

Principles for every cooling fault:

- **Follow the heat.** IT load → air → coil → chilled water → chiller → condenser water → tower or dry cooler → atmosphere. Find the first link where the temperature difference or the flow is wrong. The fault sits at that link or just before it.
- **Delta-T tells the story.** A low chilled-water ΔT (flow too high for the load), a high supply-air ΔT, or a condenser approach creeping up each point to a specific problem before any alarm fires.
- **Control or equipment.** When a unit misbehaves, first ask whether it did what its controller told it (a command against feedback mismatch means the equipment) or the controller told it the wrong thing (a sequence, setpoint or sensor problem).
- **Load step or capacity drop.** Check the IT kW trend in the same window. A cooling alarm that coincides with a load step has a different cause from one at constant load.

Load the reference for the part of the plant involved:

| Area | Reference |
| --- | --- |
| Chillers, towers, dry coolers, pumps, CHW/CW loops, free cooling, thermal storage | [references/water-side.md](references/water-side.md) |
| CRAH/CRAC, AHU, fan walls, containment, rack inlet, humidity, room pressure | [references/air-side.md](references/air-side.md) |
| PID loops, sensors, valves, dampers, VFDs, lead/lag sequences, BMS controllers | [references/control-loops.md](references/control-loops.md) |
