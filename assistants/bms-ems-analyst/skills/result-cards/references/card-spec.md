# Card spec

A card is three parts: a container with fallback text, the pinned renderer, and one `GC.render` call with the spec. Copy this frame exactly and write only the spec:

```html
<div id="gc"><p style="font:13px system-ui;color:#8a94a3">The card did not load. The conclusion is in the text below.</p></div>
<script src="https://cdn.jsdelivr.net/gh/sheon-sek/ignition-mcp@24b7f2505c4832df398a376161e61de5f3460e40/assistants/bms-ems-analyst/skills/result-cards/assets/graphene-card.js"></script>
<script>
GC.render("gc", { /* spec */ });
</script>
```

Write the fallback sentence in the user's language. The spec is a JavaScript object literal, so plain numbers, strings, arrays and `null` only.

## Levels

Every coloured element takes a `level`. The renderer picks the colour and the text label from it.

| Level | Means | Label (zh / en) |
| --- | --- | --- |
| `ok` | Within limits, no alarm | 正常 / Normal |
| `warn` | Approaching a limit, degraded redundancy, needs attention | 预警 / Warning |
| `alarm` | Alarm active, limit crossed, load at risk | 告警 / Alarm |
| `bad` | Quality not Good: the data chain is at fault, the plant state is unknown | 数据异常 / Bad data |
| `unknown` | Not read in this task | 未核实 / Unverified |
| `off` | Stopped or out of service by design | 停用 / Off |
| `info` | Neutral series or fact | 信息 / Info |

A text field named `state`, `tag` or `text` next to a level replaces the default label, for example `{"level":"ok","text":"运行"}`.

## Top level

| Field | Content |
| --- | --- |
| `title` | The question the card answers, as a short headline |
| `subtitle` | Scope in one line: what was read and what was not |
| `asof` | Read time with time zone, for example `2026-09-27 15:18 SGT` |
| `lang` | `zh` or `en`, the user's language; sets the built-in labels |
| `theme` | Leave it out for dark. `light` only when the user asks |
| `status` | `{level, tag, text, detail}`: the verdict banner. `tag` is a 2 to 4 character pill word, `text` the one-line verdict, `detail` one supporting sentence |
| `kpis` | 2 to 4 headline numbers: `{label, value, unit, note, level, digits}` |
| `sections` | The visuals, in reading order. Each takes `title`, optional `caption`, optional `meta` (small text right of the title) and `half: true` to sit beside the next half-width section |
| `evidence` | Every reading behind the card: `{tag, value, quality, time}`. Shown folded at the bottom |

## Section types

**`tiles`**: one tile per piece of equipment or area. `items: [{name, level, state, value, unit, digits, label, range:[min,max], lo, hi, spark:[[ms,v],...], signals:[{label, level, text}], note, quality, time, tag}]`. Use `value` with `range`, `lo` and `hi` for an analog reading, which draws a range bar with the normal band. Use `signals` for status points, one dot per signal. Add `spark` when you read history.

**`gauges`**: dials for a few headline percentages or temperatures. `items: [{name, value, unit, min, max, hi, level, digits}]`. `hi` shades the alarm zone.

**`bars`**: one value per unit, compared. `items: [{name, value, level}]`, plus `unit`, `limit`, `limitLabel`, `max`, `digits`. Use it for load across UPS modules, temperatures across CRAHs, run hours across lead and lag units.

**`donut`**: shares of a whole. `parts: [{label, value, level}]`, `center`, `centerLabel`. Use it for data quality coverage or alarm counts by class.

**`summary`**: a segmented bar of counts by level. `counts: {ok, warn, alarm, bad, unknown, off}`, `unit`. Use it for fleet overviews with many units.

**`line`**: a trend. `series: [{name, tag, points:[[ms,v],...], level, dash}]`, plus `unit`, `lo`, `hi`, `band:[ms,ms]`, `bandLevel`, `marks:[{time:ms, label, level}]`, `height`. Times are epoch milliseconds. Use `null` for a gap. `band` shades the event window, `marks` draws the alarm time or a change. Hover shows each series' value.

**`states`**: state over time, one row per unit. `from`, `to` in ms, `rows: [{name, segments:[[ms_start, ms_end, level, label],...]}]`. Use it for run and stop, utility and battery, open and closed, lead and lag rotation.

**`timeline`**: events in order. `items: [{time, level, tagline, text, source}]`. `tagline` is a pill such as 首次偏离 or 告警; `source` is the Tag path, "audit log" or "reported by user".

**`causes`**: the differential, most likely first. `items: [{name, confidence:"high"|"medium"|"low", for, against, check}]`.

**`checks`**: field checks or actions. `items: [{text, owner, expect, level, urgency}]`.

**`table`**: anything tabular that fits no chart. `columns: [...]`, `rows: [[cell,...]]`. A cell may be `{level, text}` for a pill or `{text}` for monospace.

**`note`**: one short paragraph, `text`. Use it rarely.

## Example

```js
GC.render("gc", {
  lang: "zh", title: "各区域状态", subtitle: "已读 5 个区域的代表点位，不是活动告警清单", asof: "2026-09-27 15:18 SGT",
  status: {level: "bad", tag: "需关注", text: "CDU-01 点位读取失败，状态未核实", detail: "其余 4 个区域的代表点位未见告警。"},
  kpis: [
    {label: "区域未见告警", value: "4 / 5", level: "ok"},
    {label: "数据异常点位", value: 4, note: "均在 CDU-01", level: "bad"},
    {label: "CRAC 回风温度", value: 22.73, unit: "°C", note: "G_CRAC1 · 15:18", level: "ok"}
  ],
  sections: [
    {type: "tiles", title: "区域状态", items: [
      {name: "UPS", level: "ok", signals: [{label: "总告警", level: "ok"}, {label: "输出故障", level: "ok"}], quality: "Good", time: "14:12:35"},
      {name: "CRAC", level: "ok", value: 22.73, unit: "°C", label: "G_CRAC1 回风温度", range: [15, 35], lo: 18, hi: 27, quality: "Good", time: "15:18:41"},
      {name: "CDU-01", level: "bad", state: "未核实", signals: [{label: "漏液", level: "bad", text: "Error"}], note: "OPC 返回 Bad_NodeIdUnknown", quality: "Bad_NodeIdUnknown", time: "15:13"}
    ]},
    {type: "donut", half: true, title: "点位质量", center: "82%", centerLabel: "Good",
     parts: [{label: "Good", value: 18, level: "ok"}, {label: "Bad_NodeIdUnknown", value: 4, level: "bad"}]},
    {type: "bars", half: true, title: "UPS 负载率", unit: "%", limit: 80, limitLabel: "设计上限",
     items: [{name: "UPS-A1", value: 46, level: "ok"}, {name: "UPS-B1", value: 83, level: "warn"}]},
    {type: "checks", title: "下一步", items: [
      {text: "核对 CDU-01 的 OPC 点位映射", owner: "Graphene 维护", expect: "映射修正后质量恢复 Good", level: "warn", urgency: "尽快"}
    ]}
  ],
  evidence: [
    {tag: "[DemoTwin]CDU/CDU-01/Leak", value: "Error", quality: "Bad_NodeIdUnknown", time: "15:13:02"},
    {tag: "[DemoTwin]CRAC/G_CRAC1/RAT", value: "22.73 °C", quality: "Good", time: "15:18:41"}
  ]
});
```
