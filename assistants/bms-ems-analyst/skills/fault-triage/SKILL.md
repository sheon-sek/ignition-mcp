---
name: fault-triage
description: Procedure for diagnosing an alarm, fault, trip or abnormal reading in a BMS, EPMS or data centre plant from Ignition data. Use for any incident, alarm flood, unexplained trend, or a "why did X happen" question.
---

# Fault triage

Work through the steps in order. A step is finished only when its **Done when** line is met.

1. **Frame.** Record the equipment, the alarm text, the time it was first seen and the reported impact.
   Done when you can state the incident in one sentence with a time anchor. If the anchor is missing, ask for it.

2. **Locate.** Find the Tag paths for every piece of equipment named, plus the equipment directly upstream and downstream of it. Use the `ignition-evidence` skill.
   Done when each item has its paths, or is marked "no Tag found".

3. **Risk and redundancy.** Read [references/risk-and-redundancy.md](references/risk-and-redundancy.md).
   Done when you have stated the current redundancy state (for example "2N reduced to N on PDU-A2") and a time to impact, or "no active risk" together with the reads that show it.

4. **Data or plant.** If any key signal has quality other than Good, is flat, steps suddenly, or disagrees with its redundant partner, load the `ot-data-layer` skill.
   Done when each key signal is marked **trusted** or **suspect**, with the reason.

5. **Timeline.** Query the Historian from before the alarm to now. Check what changed in the same window with the `ignition-evidence` alarms-and-changes reference.
   Done when the timeline starts at the **first deviation**, not at the alarm time, and every change in the window is listed or confirmed absent.

6. **Differential.** Load `power-systems` or `cooling-systems` for the failure-mode tables. List at least three candidate causes, with at least one physical and at least one in controls or data.
   Done when every candidate is either ruled out by cited evidence or has a named field check that would settle it.

7. **Report.** Write the field checks and the verdict using [references/incident-report.md](references/incident-report.md).
   Done when each field check gives the expected reading for each remaining candidate.
