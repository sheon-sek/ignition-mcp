---
name: result-cards
description: How to present a finished result as a static HTML card of charts and status indicators in the reply. Use before writing the final answer of any task that produced a diagnosis, analysis, comparison, trend, status overview, plan or other result that needs explaining.
---

# Result cards

A **card** is a dashboard panel in the reply. It shows the result as charts and indicators an operator reads in seconds, the way a control-room screen does: a verdict banner, a few headline numbers, then one visual per question. It is one static HTML document: a fixed stylesheet, components, and charts drawn with CSS widths and inline SVG whose numbers you compute.

## 1. Decide whether the result earns a card

A card earns its place when the result has structure: several units, values over time, a sequence of events, a ranking, a comparison. A single value, a yes or no, a one-line fact or a clarifying question stays plain text.

Done when you can name the one question the card answers, for example "What state is each area in right now?". If you cannot name it, answer in text and stop here.

## 2. Plan the card as a screen

Lay the card out top to bottom:

1. **Status banner.** The verdict in one line with its level.
2. **KPIs.** Two to four numbers that carry the answer: units in alarm, worst temperature, load on the busiest module, data coverage.
3. **The main visual.** The one section that answers the question directly.
4. **Supporting visuals.** Pair two small ones side by side as half-width sections.
5. **Causes and checks**, when the task was a diagnosis.
6. **Evidence.** Every reading behind the card, folded.

Pick each visual from the shape of its data:

| Data | Component |
| --- | --- |
| State of each area or unit | Tiles: dots for status signals, a range bar for an analog value |
| A few headline percentages or temperatures | Gauges |
| The same measure across units | Bars with the limit line |
| Shares of a whole: quality coverage, alarm classes | Donut |
| A value over time around an event | Trend with limits and the event band |
| Run, stop, battery, lead and lag over time | State bars |
| Events in order | Timeline |
| Candidate causes | Causes |
| Field checks and actions | Checks |

Turn every raw reading into an indicator. `Alarm = false` becomes a green dot labelled 正常 or Normal. `Quality = Bad_NodeIdUnknown` becomes the `bad` level with the code in small text. A reading you did not take is `unknown`, never `ok`. Keep labels to a few words; the exact Tag path, value and timestamp go in the evidence table and in `title` attributes for hover.

Use three to seven sections. Each section title names what it shows, such as "UPS 负载率" or "CDU-01 供水温度, 14:00 to 15:30".

Done when every section answers a part of the question and no section only restates another.

## 3. Write the HTML

Read [references/card-kit.md](references/card-kit.md). Copy its frame and stylesheet unchanged, then build the card from its components:

- The card has no `<script>` and loads nothing from the network: no CDN, web font, image URL or link to another file.
- Compute every chart coordinate with the formulas in the kit, rounded to one decimal, and check that each position lies between 0 and 100.
- Every number comes from a Tool result in this conversation, and every value on the card also appears in the evidence table.
- Plot the points you have. Keep each trend series to about 60 points; if you thinned it, the caption says how, for example "1 point per 5 minutes, maximum within each interval". A gap in the data starts a new polyline.
- Times on the card are in the Graphene server's time zone, and the header chip names it.
- The card follows the naming rules in the system prompt and the user's language.

Done when the stylesheet is unchanged, the HTML has no script or external reference, every level matches the evidence, and every value on the card is in the evidence table.

## 4. Place the card in the reply

1. The status line comes first, in plain text, so the answer stands if the card does not load.
2. The card follows as one complete HTML document in a fenced code block tagged `html`.
3. After the card, the text gives what the card cannot: confidence, the reasoning in two or three sentences, and the actions if the card has no `checks` section. Refer to the card instead of repeating its numbers.

Done when a reader who cannot see the card still gets the verdict and the actions from the text.
