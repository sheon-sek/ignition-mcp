---
name: ot-data-layer
description: Deciding whether a reading reflects the real plant or a fault in the data chain (sensor, controller, BACnet/Modbus/SNMP/OPC UA/IEC 61850 comms, Ignition Tag quality, Historian gaps, clocks). Use when a value is Bad, Uncertain, stale, flatlined, jumped, disagrees with its partner, or a device shows offline.
---

# OT data layer

The chain runs: field sensor → wiring → controller, meter or relay → protocol → gateway or integrator → Ignition device connection → Tag → Historian → screen. A wrong number can come from any link. Walk it from the Tag back toward the sensor, and stop at the first link that is broken.

1. **Scope.** Is one Tag affected, one device, one protocol driver, or everything? Read neighbouring Tags from the same device and from other devices in one `tag_read` call.
   - One Tag → the sensor, a point mapping or scaling.
   - One device → the device, its network drop, or its connection.
   - Many devices on one driver → the driver, the gateway, or the network segment.
   - Everything → the Gateway, its clock, or the Historian store.
2. **Quality and time.** Interpret the quality codes and timestamps with [references/tag-quality.md](references/tag-quality.md).
3. **Protocol signatures.** For the device's protocol, use [references/field-protocols.md](references/field-protocols.md).
4. **Plausibility.** Check the value against physics: energy balance, the redundant sensor, a value that should move with the load but stays flat.

Done when each key signal is marked **trusted** or **suspect**, with the link in the chain you blame and the evidence for it.
