---
name: graphene-evidence
description: How to find, read and look back through plant data with the Graphene data Tools. Covers locating equipment Tags, reading live values, Historian trends and aggregates, rebuilding an alarm, and finding what changed. Use before the first data query of any question, incident or routine report.
---

# Graphene evidence

Load the reference that matches the question:

| Question | Reference |
| --- | --- |
| Where is this equipment, and what is it reading now? | [references/locate-and-read.md](references/locate-and-read.md) |
| What did it do over time: an event, a trend, run hours, starts? | [references/history.md](references/history.md) |
| Why did this alarm fire, and what changed around it? | [references/alarms-and-changes.md](references/alarms-and-changes.md) |

Rules that apply to every query:

- Read a whole equipment group in one `tag_read` call so the timestamps line up.
- Query the redundant partner (A/B side, lead/lag unit) over the same window. A difference that shows on one side only points to that side.
- If the Tools stop answering, `bundle_info` confirms whether the Runtime server responds, and `operation_diagnose` with a `correlationId` from an earlier error shows how far that call got.
