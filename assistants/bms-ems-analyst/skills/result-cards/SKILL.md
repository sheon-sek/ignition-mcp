---
name: result-cards
description: How to present a finished result as an interactive HTML card drawn into the reply. Use before writing the final answer of any task that produced a diagnosis, analysis, comparison, trend, plan or other result that needs explaining.
---

# Result cards

A **card** is a self-contained HTML block in the reply that shows the result as a picture the operator can check: a timeline, a chart, a ranked list of causes, a status board. The chat client renders an `html` code block as a live card, so the operator reads the answer at a glance instead of working through paragraphs.

## 1. Decide whether the result earns a card

A card earns its place when the result has structure: several values over time, several items to compare, a sequence of events, a ranking, a power or water path. A single value, a yes or no, a one-line fact or a clarifying question stays plain text.

Done when you can name the one question the card answers, for example "What happened to UPS-A1 between 02:10 and 02:40?". If you cannot name it, answer in text and stop here.

## 2. Pick the form from the shape of the result

| Result shape | Card form |
| --- | --- |
| Incident verdict with evidence | Status banner, then evidence rows with Tag path, value, quality and timestamp |
| Sequence of events | Vertical timeline, first deviation marked, alarm time marked |
| Values over time | SVG line chart with the alarm limits drawn as lines and the event window shaded |
| Several units or A/B sides | Side-by-side table or grouped bars, the odd one out highlighted |
| Candidate causes | Ranked cause cards: confidence, evidence for, evidence against, the field check that settles it |
| Redundancy or power/water path | One-line diagram of the path, lost elements in the fault colour |
| Plant or fleet overview | Tile grid, one tile per unit, worst state first |
| Plan or field checks | Numbered checklist, each step with owner, redundancy used and expected reading per candidate |

Use the smallest form that answers the question. A card may combine two forms when the result has two shapes, for example a timeline above a chart. Add interaction only where it helps the operator check the result: hover to see a point's exact value and timestamp, tabs to switch between candidate causes, a toggle to show or hide the redundant partner's series.

Done when every form on the card maps to a part of the result.

## 3. Build the card

Read [references/card-kit.md](references/card-kit.md) for the base template, the colour tokens and the component snippets. Follow these rules:

- **Self-contained.** Inline CSS, inline SVG, plain JavaScript. The page loads nothing from the network: no CDN script, web font, image URL or `fetch`. Plant networks are often isolated, and a card that depends on the internet renders blank there.
- **Traceable.** Every number on the card comes from a Tool result in this conversation. Each value shows or reveals on hover its Tag path, quality and timestamp with the time zone. Mark values you did not read as unverified, in the same words the text uses.
- **Honest data.** Plot the points you have. If you thinned a series to fit the card, the caption says how, for example "1 point per minute, max within each minute". Draw a gap where the Historian has no data.
- **Status by colour and word.** Use the status colours from the kit, and always pair the colour with a text label (Normal, Warning, Alarm, Bad quality) so the card reads without colour.
- **Bounded.** At most about 400 plotted points per series and 30 KB of HTML per card. When the data is larger, aggregate it and say so on the card.
- **Graphene naming.** The card follows the naming rules in the system prompt.

Done when the HTML has no external reference, every number on it traces to a Tool result, and each status has a text label.

## 4. Place the card in the reply

1. The status line comes first, in plain text, so the answer stands even if the card does not render.
2. The card follows in one fenced code block tagged `html`.
3. After the card, the text gives what the operator acts on: confidence, field checks and recommended actions, unless the card already holds them as a checklist. Keep one copy of each fact: the text refers to the card for evidence it shows.

Done when a reader who cannot see the card still gets the verdict and the actions from the text.
