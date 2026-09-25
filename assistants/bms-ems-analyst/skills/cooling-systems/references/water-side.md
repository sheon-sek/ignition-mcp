# Water side: chillers, towers, pumps, loops

## Chiller trips and alarms

| Alarm | Likely causes | Evidence |
| --- | --- | --- |
| High condenser pressure or high head | Towers not rejecting (fans off, scale, high wet-bulb), low condenser-water flow, fouled condenser tubes, non-condensables | CW supply temperature against the design value; tower fan status and speed; the condenser approach trend (rising over weeks = fouling, a sudden rise = flow or tower) |
| Low evaporator pressure or low refrigerant temperature | Low chilled-water flow, low refrigerant charge, a CHW setpoint too low, a fouled evaporator | CHW flow and ΔP; the evaporator approach; the flow switch history |
| Flow switch or loss of flow | A pump tripped, a valve closed (isolation or a failed 2-way valve), air in the loop, a strainer blocked | Pump status and VFD speed; loop ΔP; the pressure difference across the strainer if metered |
| Chiller will not load or surges (centrifugal) | Low load against minimum capacity, a high lift at low load, a guide-vane or VFD fault | Chiller % load and lift (condenser minus evaporator saturation temperature); a surge counter if it exists |
| Short-cycling | Oversized plant at low load, a too-narrow staging deadband, a low-ΔT loop | CountOn per hour; the staging logic setpoints |
| Compressor or motor overload, oil alarms | Mechanical fault, VFD or starter fault | Motor current trend; the fault code from the chiller controller |

## Low ΔT syndrome

The chilled-water ΔT stays well below the design value (for example 3 K against a 6 K design). More chillers and pumps run than the load needs, and the plant stages up early. Causes: coil valves that leak through or are stuck open, three-way valves, a bypass open, a coil too dirty to transfer heat, a supply setpoint set too low. Evidence: plant flow against the load kW, and valve positions against the coil leaving-air temperature.

## Pumps and loop

- A primary–secondary decoupler with reverse flow means secondary flow exceeds primary. Warm return water mixes into the supply.
- A pump running with no rise in ΔP means cavitation, a broken coupling, rotation reversed after maintenance, or air binding.
- A VFD pinned at 100 % speed while ΔP is below setpoint means too many valves open (a load problem or low ΔT), a failed ΔP sensor, or a clogged strainer.

## Economiser and free cooling

Changeover failures happen at the boundary conditions. Check the wet-bulb or dry-bulb temperature against the changeover setpoints and the heat-exchanger approach. Freeze protection trips on dry coolers and towers in cold weather mean the glycol concentration, the sump heaters or the bypass logic are at fault.

## Thermal storage

Tank level and temperature, and whether it discharged during the event. A tank that was at the wrong temperature before the event buys no time.
