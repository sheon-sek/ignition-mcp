---
name: result-cards
description: How to present a finished result as an interactive HTML card of charts and status indicators drawn into the reply. Use before writing the final answer of any task that produced a diagnosis, analysis, comparison, trend, status overview, plan or other result that needs explaining.
---

# Result cards

A **card** is a dashboard panel in the reply. It shows the result as charts and indicators an operator reads in seconds, the way a control-room screen does: a verdict banner, a few headline numbers, then one visual per question. A pinned renderer draws it, so you write only a data spec.

## 1. Decide whether the result earns a card

A card earns its place when the result has structure: several units, values over time, a sequence of events, a ranking, a comparison. A single value, a yes or no, a one-line fact or a clarifying question stays plain text.

Done when you can name the one question the card answers, for example "What state is each area in right now?". If you cannot name it, answer in text and stop here.

## 2. Plan the card as a screen

Lay the card out top to bottom:

1. **Status banner.** The verdict in one line with its level.
2. **KPIs.** Two to four numbers that carry the answer: units in alarm, worst temperature, load on the busiest module, data coverage.
3. **The main visual.** The one section that answers the question directly.
4. **Supporting visuals.** Pair small ones side by side with `half: true`.
5. **Causes and checks**, when the task was a diagnosis.
6. **Evidence.** Every reading behind the card, folded.

Pick each visual from the shape of its data:

| Data | Section |
| --- | --- |
| State of each area or unit | `tiles`: dots for status signals, a range bar for an analog value, a sparkline for history |
| A few headline percentages or temperatures | `gauges` |
| The same measure across units | `bars` with the limit line |
| Shares of a whole: quality coverage, alarm classes | `donut` |
| Many units, counted by state | `summary` |
| A value over time around an event | `line` with limits, event band and marks |
| Run, stop, battery, lead and lag over time | `states` |
| Events in order | `timeline` |
| Candidate causes | `causes` |
| Field checks and actions | `checks` |

Turn every raw reading into an indicator. `Alarm = false` becomes a green dot labelled 正常 or Normal. `Quality = Bad_NodeIdUnknown` becomes the `bad` level with the code in small text. A reading you did not take is `unknown`, never `ok`. Keep labels to a few words; the exact Tag path, value and timestamp go in `evidence` and in the `tag`, `quality` and `time` fields.

Use three to seven sections. Each section title names what it shows, such as "UPS 负载率" or "CDU-01 供水温度, 14:00 to 15:30".

Done when every section answers a part of the question and no section only restates another.

## 3. Write the spec

Read [references/card-spec.md](references/card-spec.md) and copy its frame: the container with fallback text, the pinned renderer script, one `GC.render` call. Then write the spec:

- Every number comes from a Tool result in this conversation, and every value that appears on the card also appears in `evidence`.
- Plot the points you have. If you thinned a series, the `caption` says how, for example "1 point per minute, maximum within each minute". Keep each series to about 400 points, and use `null` for a gap.
- Times on the card are in the Graphene server's time zone, and `asof` names it.
- The card follows the naming rules in the system prompt and the user's language (`lang`).

Done when the frame is copied unchanged, every level matches the evidence, and every value on the card is in `evidence`.

## 4. Place the card in the reply

1. The status line comes first, in plain text, so the answer stands if the card does not load.
2. The card follows in one fenced code block tagged `html`.
3. After the card, the text gives what the card cannot: confidence, the reasoning in two or three sentences, and the actions if the card has no `checks` section. Refer to the card instead of repeating its numbers.

Done when a reader who cannot see the card still gets the verdict and the actions from the text.
