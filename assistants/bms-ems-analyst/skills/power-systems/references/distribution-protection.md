# Distribution and protection

## Breaker trips

Find out which trip unit or relay element operated. That tells you what kind of fault it was:

| Element | Points to | Check |
| --- | --- | --- |
| Long-time (overload) | Sustained load above the setting | Current trend for minutes to hours before the trip; recent IT load additions; a lost redundant feed that doubled the load |
| Short-time or instantaneous | A short circuit, or inrush on energisation | Peak current captured by the trip unit; whether equipment was switched on at that moment |
| Ground fault | Insulation failure, water, a damaged cable, a neutral–ground bond in the wrong place | A leak alarm nearby; recent work; a GF trip with no overcurrent |
| Shunt trip | An external command: EPO, fire alarm, interlock | Fire alarm panel and EPO status at that time; the cause-and-effect matrix |
| Undervoltage release | Loss of control voltage or supply | A matching sag on the upstream meter |

**Selectivity**: when an upstream breaker trips for a downstream fault, the coordination study and the settings are candidates. Ask whether the trip unit settings changed recently. That is a common result of maintenance.

## Transformers

Winding or oil temperature alarms: check the load, the harmonic content (K-factor load heats a transformer beyond its kVA rating), the cooling fans, and the room ventilation. Sudden gas or pressure relay operation (Buchholz, sudden pressure) means an internal fault. Keep it de-energised until it is tested.

## PDU, RPP and busway

- Phase imbalance above about 10–20 % on a PDU means single-phase loads were placed badly. It raises neutral current, and with non-linear loads the neutral current can exceed the phase current.
- For branch-circuit alarms, compare against 80 % of the breaker rating for continuous load, and check the A and B feed sum. In a 2N design, A plus B must stay within one feed's capacity or a failover will trip the survivor.
- Busway plug-in or joint hot spots show up only in thermal scans. A rising temperature Tag on a busway joint is urgent.

## Grounding

A voltage between neutral and ground at the load (above a few volts), or a ground-fault alarm with no trip, points to a bad or extra neutral–ground bond. It is common after a UPS or transformer is replaced.

## Safety

Everything behind a live-front panel needs a qualified person, energised-work justification, and PPE from the site's arc-flash labels. Never recommend closing onto a fault: identify and clear the cause before any reclose.
