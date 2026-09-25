# Risk and redundancy

Settle this before looking for a root cause. A correct diagnosis delivered after the load drops is worthless.

## State the redundancy

- Name the topology for the affected path: N, N+1, 2N, 2(N+1), distributed or block redundant. Read it from the Tag structure (A and B sides, lead, lag and standby units) or ask the user.
- State what is left now: "Cooling N+1 → N: CRAH-03 tripped, the remaining five carry 100 % of the design load." "Power 2N → N on rows C–D: UPS-B on bypass."
- Single-corded IT load, or dual-corded load with a failed PSU, sits on one path whatever the topology says. Ask about it when one side of the power is degraded.
- Look for **common mode**: a shared chilled-water header, a shared controls panel or 24 V supply, a shared network switch, one BMS controller running several units, one fuel system feeding every generator. A second failure that removes both sides usually comes through one of these.

## Estimate the time to impact

- **UPS on battery**: the remaining runtime from the UPS Tag. If there is none, estimate from the battery load percentage and the design autonomy, and say the figure is an estimate. Check whether the generator is running and has transferred.
- **Cooling loss**: the rate of rise of rack inlet or supply air temperature from the last 10–15 min of Historian data, projected to the site limit (often 27 °C recommended, 32–35 °C allowable, per ASHRAE class; ask the site value). With chilled water, thermal storage or the loop's thermal mass buys minutes. Check the tank level or temperature if there is one.
- **Generator**: fuel level against the burn rate at the current load, and coolant temperature or oil pressure trends.
- **Humidity or dew point**: rarely urgent within minutes, unless condensation reaches the IT space.

## Systems that cross boundaries

- **Fire alarm and suppression** (VESDA, clean agent, pre-action sprinklers): a fire alarm can shut down HVAC through dampers and fan stops, and can trip power through EPO or shunt trips by design. Confirm what the cause-and-effect matrix says before treating the shutdown as a fault.
- **Leak detection**: a leak alarm under a CRAH or near a PDU is an electrical risk as well as a water one.
- **EPO**: an EPO event is deliberate or a fault in the EPO circuit. Ask how it was activated before any restoration talk.

## Urgency words

- **Immediate**: the load is at risk within 30 min, or people are at risk. Lead the reply with it.
- **Urgent**: redundancy is lost, with no load impact yet. Fix within the shift.
- **Planned**: degraded performance or a latent fault. Fix through normal change control.
