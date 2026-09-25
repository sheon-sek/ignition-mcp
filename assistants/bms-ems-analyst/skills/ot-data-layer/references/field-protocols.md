# Field protocol signatures

Read the device connection with `config_resource_search` (`opc`, `device`, `bacnet`, `modbus`) and `config_resource_get`, and the live status in the `[System]` provider.

## BACnet (IP and MS/TP) — typical BMS

- **MS/TP trunk**: one device fault (a duplicate MAC, a wrong baud rate, missing or doubled termination, a reversed polarity) can take down the whole trunk or make devices come and go. Many devices on one MS/TP router going Bad together points at the trunk or the router, not the devices.
- **BACnet/IP**: broadcast issues across subnets (BBMD misconfigured), a duplicate Device Instance after a controller replacement, foreign device registration expired.
- **COV subscriptions** that lapse after a device restart: the values freeze while the connection looks up. Compare with a polled point.
- **Priority array**: a point held at a higher priority (manual operator override, often at priority 8, or a life-safety priority from the fire system) ignores the BMS command. That explains "commanded but did not move" without any equipment fault. Ask for the priority array on site if it is not mapped.

## Modbus (RTU and TCP) — meters, UPS, generators, VFDs

- **Timeouts on one slave** on a shared RTU line: an address conflict, the baud rate or parity, termination, or a gateway that has reached its polling limit.
- **Values off by a factor** (10, 100, 1000) or nonsense: the wrong scaling, the register offset (base 0 against base 1), word or byte order for 32-bit values. A plausible value that jumps between two magnitudes points at word order.
- **Serial-to-TCP gateways** time out when too many masters poll them at once. Ask whether a laptop or another system was added.

## SNMP — UPS, PDUs, STS, CRAC network cards

- A community string or SNMPv3 credential changed during a firmware update.
- A network management card that hangs keeps the device running but freezes the data. A web-page timeout on the card confirms it. Restarting the card does not affect the load; confirm that with the vendor documentation for the model first.
- Traps are lost silently. Polling is the reliable source.

## OPC UA (to PLCs or integrator servers)

- Certificate expiry or a trust change: the connection fails at a clean moment, often at a round date. Check the date against when the certificate was issued.
- Session or subscription limits on the server: some Tags go Bad while others stay Good.
- An upstream OPC server that is up but lost its own field connection returns Bad or Uncertain for its whole namespace.

## IEC 61850 and DNP3 — protection relays, substations, some EPMS

- GOOSE is for protection and interlocks; MMS carries data to SCADA. A loss of MMS data does not mean the protection failed, and the reverse.
- Relay event records (SOE) keep millisecond timing that the Historian does not have. Ask for the relay event report for the sequence of any trip.
- For DNP3, check for event buffer overflow and time sync on the outstation.

## Network

- A switch or a ring (for example a Moxa or Hirschmann ring) that reconverged: a burst of Bad quality across devices on one switch for a few seconds.
- A duplicate IP after equipment was replaced: two devices flicker in turn.
- The site's OT firewall or a VLAN change: check the change log with the user.
