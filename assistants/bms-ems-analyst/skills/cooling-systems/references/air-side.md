# Air side: CRAH, CRAC, AHU, whitespace

## High rack inlet temperature

| Pattern | Likely causes | Evidence |
| --- | --- | --- |
| One rack or a few racks, the rest normal | Missing blanking panels, a gap in the containment door, a new high-density rack, a tile or grille missing or badly placed, cables blocking the floor void | Inlet at the top of the rack against the bottom (top hotter = recirculation over the top); a recent IT move or install; the kW per rack |
| A whole row or zone | A CRAH down or in a derated mode, containment breached, a floor void pressure drop, zone-wide hot air mixing | Unit status and fan speed per CRAH in the zone; underfloor or duct static pressure trend; containment door alarms |
| The whole room rising slowly | Chilled-water supply temperature rising (see water side), total load above N capacity, a stuck economiser damper | The supply-air temperature on every unit; CHW supply temperature; IT kW |
| Sudden rise after a power event | CRAH fans restarting in stages, chillers restarting (a centrifugal restart can take 5–15 min) | The restart timeline; whether the CRAH fans are on UPS power (they should be at many sites) |

## CRAH and CRAC faults

- **Supply air temperature cannot reach setpoint, valve at 100 %**: warm chilled-water supply, low CHW flow to this unit (balancing valve, strainer, air lock), or a fouled coil or filter. Compare the coil ΔT and the valve position with its neighbours.
- **Fans at high speed, airflow low**: dirty filters (ΔP alarm), a belt slipping (belt-driven units), an EC fan module failed in a fan array.
- **DX CRAC high head pressure or low suction**: condenser fouling or a fan fault outdoors, refrigerant charge, an expansion valve fault. Short-cycling on a low-pressure switch in winter points to head-pressure control.
- **Units fighting**: one unit humidifies while its neighbour dehumidifies, or one heats while another cools. Setpoints or sensor calibration differ between the units. Compare the setpoints and the return-air readings across the group.

## Humidity and dew point

Control the dew point, not relative humidity: RH alone changes with temperature. ASHRAE's recommended range is roughly −9 to 15 °C dew point with RH ≤ 60 %. Condensation risk exists where a chilled surface sits below the space dew point. Check the CHW supply against the room dew point in humid climates.

## Pressure and containment

- **Hot or cold aisle containment**: the ΔP between the aisles should be slightly positive toward the cold aisle, a few pascals. Near zero or negative means hot air leaking back into the cold aisle.
- **Room pressurisation**: negative pressure pulls in unconditioned or dusty air through the doors.
