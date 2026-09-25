# BMS / EMS analyst agent

A system prompt and five Agent Skills for a Cherry Studio Agent in the Analysis role, connected to `ignition-runtime-analysis` and `ignition-rest-analysis`.

- `system-prompt.md`: paste it into the Agent's system prompt field.
- `skills/<name>/`: import each folder as a skill. Only the `description` in each `SKILL.md` stays loaded all the time. The body loads when the skill fires, and `references/*.md` load only when a step points to them.

| Skill | Fires on |
| --- | --- |
| `fault-triage` | any alarm, trip or odd reading; the 7-step procedure |
| `ignition-evidence` | any data query: finding Tags, live reads, history, alarm rebuild, what changed |
| `power-systems` | UPS, generator, ATS/STS, switchgear, protection, power quality |
| `cooling-systems` | chillers, loops, CRAH/AHU, containment, control loops |
| `ot-data-layer` | Bad/stale/flat values, device offline, protocol faults |
