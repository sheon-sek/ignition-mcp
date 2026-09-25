# UPS and batteries

## Operating modes to read first

Normal (double conversion or eco mode), on battery, static bypass, maintenance bypass, off. The mode Tag and the last transfer time come before anything else.

## Failure modes

| Symptom | Likely causes | Evidence that separates them |
| --- | --- | --- |
| Transfer to battery, input normal on the EPMS meter | Input breaker tripped, rectifier fault, input out of the UPS's tolerance window (frequency on generator) | UPS input Tags against the upstream meter; UPS event log; generator frequency trend at that time |
| Transfer to static bypass | Inverter overload, inverter fault, overtemperature, DC bus fault | Output load % before the event (above 100–110 %?); inverter or heatsink temperature trend; a load step at that time on the PDUs downstream |
| Battery runtime far below design | Aged or failed strings, one open string, a high-resistance joint, cold or hot battery room | Per-string current during discharge (one string near 0 A = open); cell or block voltage spread; battery room temperature history; battery age against the 3–5 year VRLA design life |
| Battery temperature alarm, room normal | Thermal runaway starting, a failing cell, a failed ventilation fan | Float current rising at constant voltage is the warning sign of runaway. Escalate at once. |
| Frequent short transfers | Input sags (see power quality), a loose input connection, a sensitive input window setting | Correlate the transfer times with upstream voltage minima; check whether other UPS units on the same source transfer too |
| Parallel system shares load unevenly | A module fault, a failed sync or share signal, a module in a different mode | Per-module output kW; module mode Tags |

## Battery facts

- VRLA float voltage is about 2.25–2.27 V per cell at 25 °C. Life halves for about every 8–10 °C above 25 °C.
- For lithium-ion, the battery's own BMS limits current and trips on cell imbalance or temperature. Read its alarm or status Tags before blaming the UPS.
- Discharge gives the only real proof of capacity. A float voltage that looks fine does not show capacity.

## Safety

The DC bus and battery strings stay live when the UPS is off. Battery work needs insulated tools, a check for hydrogen or ventilation (VRLA), and a qualified person.
