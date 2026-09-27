# Card kit

A card is one static HTML document: the stylesheet below, then components. It has no `<script>`, loads nothing from the network, and draws charts with CSS widths and inline SVG whose numbers you compute. Hover text comes from `title` attributes.

## Frame and stylesheet

Copy this frame and the whole `<style>` block unchanged. Put the components inside `<div class="g">`.

```html
<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#151a21;--raise:#1b212a;--line:#262d37;--fg:#e8ecf1;--sub:#aab3bf;--mute:#6e7886;
--ok:#34d399;--warn:#fbbf24;--alarm:#f87171;--bad:#a78bfa;--unknown:#7b8594;--off:#4b5563;--info:#60a5fa;--s2:#f472b6}
*{box-sizing:border-box}body{margin:0;background:var(--bg)}
.g{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:24px;font-variant-numeric:tabular-nums}
.ok{--c:var(--ok)}.warn{--c:var(--warn)}.alarm{--c:var(--alarm)}.bad{--c:var(--bad)}.unknown{--c:var(--unknown)}.off{--c:var(--off)}.info{--c:var(--info)}
.hd{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:20px}
.hd h1{font-size:20px;font-weight:650;margin:0}.hd p{color:var(--sub);font-size:13px;margin:4px 0 0}
.chip{flex:none;font-size:12px;color:var(--sub);background:var(--raise);border:1px solid var(--line);padding:4px 10px;border-radius:999px;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:2px 9px 2px 8px;border-radius:999px;white-space:nowrap;color:var(--c);background:color-mix(in srgb,var(--c) 13%,transparent);border:1px solid color-mix(in srgb,var(--c) 28%,transparent)}
.pill:before,.dot{content:"";width:7px;height:7px;border-radius:50%;background:var(--c);flex:none;display:inline-block}
.dot{width:8px;height:8px;box-shadow:0 0 0 3px color-mix(in srgb,var(--c) 18%,transparent)}
.ban{display:flex;gap:14px;align-items:center;padding:14px 18px;border-radius:12px;margin-bottom:20px;background:color-mix(in srgb,var(--c) 8%,transparent);border:1px solid color-mix(in srgb,var(--c) 30%,transparent)}
.ban b{font-size:15px;font-weight:600}.ban div div{color:var(--sub);font-size:13px;margin-top:2px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--c);border-radius:14px;padding:16px 18px}
.kpi span{font-size:12px;color:var(--sub)}.kpi b{display:block;font-size:28px;font-weight:650;margin-top:6px;line-height:1.1}
.kpi b small,.big small{font-size:13px;color:var(--mute);font-weight:500;margin-left:3px}.kpi i{display:block;font-style:normal;font-size:12px;color:var(--mute);margin-top:6px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.full{grid-column:1/-1}
.sec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;min-width:0}
.sec h2{font-size:13px;font-weight:600;color:var(--sub);margin:0 0 14px}.cap{color:var(--mute);font-size:12px;margin-top:10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.tile{background:var(--raise);border:1px solid var(--line);border-top:3px solid var(--c);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.th{display:flex;justify-content:space-between;align-items:center;gap:8px}.th b{font-weight:600}
.big{font-size:24px;font-weight:650;line-height:1}.lbl{font-size:12px;color:var(--mute)}
.sig{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--sub)}.sig span:last-child{margin-left:auto;color:var(--fg)}
.foot{font-size:11.5px;color:var(--mute);border-top:1px solid var(--line);padding-top:8px}
.rb{position:relative;height:6px;border-radius:999px;background:var(--line)}
.rb .band{position:absolute;top:0;bottom:0;border-radius:999px;background:color-mix(in srgb,var(--ok) 28%,transparent)}
.rb .mk{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;background:var(--c);transform:translateX(-50%)}
.rl{display:flex;justify-content:space-between;font-size:11px;color:var(--mute);margin-top:5px}
.bar{display:grid;grid-template-columns:minmax(80px,30%) 1fr 64px;gap:12px;align-items:center;font-size:13px;margin-bottom:12px}
.bar>span:first-child{color:var(--sub)}.bar>span:last-child{text-align:right;font-weight:600}
.bt{position:relative;height:10px;background:var(--raise);border-radius:999px}.bt i{position:absolute;left:0;top:0;bottom:0;border-radius:999px;background:var(--c)}
.bt em{position:absolute;top:-4px;bottom:-4px;border-left:2px dashed var(--mute)}
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;text-align:center}
.gauges svg{width:100%;max-width:160px}.gauges div div{font-size:13px;color:var(--sub)}
.donut{display:flex;align-items:center;gap:24px;flex-wrap:wrap}.donut svg{width:128px;flex:none}
.leg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;color:var(--sub);margin-top:12px}.leg span{display:inline-flex;align-items:center;gap:6px}
.rows{flex:1;min-width:140px;display:flex;flex-direction:column;gap:10px;font-size:13px}.rows div{display:flex;align-items:center;gap:8px}.rows b{margin-left:auto}
.chart{display:grid;grid-template-columns:40px 1fr;gap:0 8px}.ya{display:flex;flex-direction:column;justify-content:space-between;font-size:11px;color:var(--mute);text-align:right;height:200px}
.plot{position:relative;height:200px;background:repeating-linear-gradient(to bottom,var(--line) 0 1px,transparent 1px 25%);border-bottom:1px solid var(--line)}
.plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.xa{grid-column:2;display:flex;justify-content:space-between;font-size:11px;color:var(--mute);margin-top:6px}
.lim{position:absolute;left:0;right:0;border-top:1.5px dashed var(--alarm);font-size:10.5px;color:var(--alarm);text-align:right}
.st{display:grid;grid-template-columns:minmax(70px,22%) 1fr;gap:12px;align-items:center;font-size:13px;margin-bottom:10px}
.st>span{color:var(--sub)}.sb{position:relative;height:18px;background:var(--raise);border-radius:5px;overflow:hidden}.sb i{position:absolute;top:0;bottom:0;background:var(--c)}
.tl{list-style:none;margin:0;padding:0 0 0 26px;border-left:2px solid var(--line);margin-left:5px}
.tl li{position:relative;padding-bottom:16px}.tl li:before{content:"";position:absolute;left:-33px;top:5px;width:12px;height:12px;border-radius:50%;background:var(--c);border:2px solid var(--card)}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--mute)}
.cz{background:var(--raise);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.cz .th span:first-child{display:flex;gap:10px;align-items:center;font-weight:600}.n{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:var(--line);font-size:13px;font-weight:700}
.meter{display:inline-flex;gap:3px;align-items:center;font-size:12px;color:var(--sub)}.meter i{width:14px;height:6px;border-radius:2px;background:var(--line)}.meter i.on{background:var(--c)}
dl{display:grid;grid-template-columns:64px 1fr;gap:6px 12px;font-size:13px;margin:12px 0 0}dt{color:var(--mute)}dd{margin:0}
.ck{list-style:none;margin:0;padding:0;counter-reset:k}.ck li{counter-increment:k;display:grid;grid-template-columns:28px 1fr;gap:12px;background:var(--raise);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:10px}
.ck li:before{content:counter(k);width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:var(--line);font-weight:700;font-size:13px}
.ck .m{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--mute);margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;font-weight:600;color:var(--mute);font-size:12px;padding:0 10px 8px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:0}.sc{overflow-x:auto}
details{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 20px}summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--sub)}
@media(max-width:620px){.g{padding:16px}.grid{grid-template-columns:1fr}.hd{flex-direction:column}}
</style></head><body><div class="g">
<!-- components -->
</div></body></html>
```

Set `lang` on `<html>` to the user's language.

## Levels

A level is a class: `ok`, `warn`, `alarm`, `bad`, `unknown`, `off`, `info`. It sets the colour through `--c` for the element that carries it and everything inside.

| Class | Means | Label zh / en |
| --- | --- | --- |
| `ok` | Within limits, no alarm | 正常 / Normal |
| `warn` | Near a limit, reduced redundancy | 预警 / Warning |
| `alarm` | Alarm active, limit crossed, load at risk | 告警 / Alarm |
| `bad` | Quality not Good; the plant state is unknown | 数据异常 / Bad data |
| `unknown` | Not read in this task | 未核实 / Unverified |
| `off` | Stopped or out of service by design | 停用 / Off |
| `info` | Neutral series or fact | 信息 / Info |

A pill always carries a word, so the card reads without colour: `<span class="pill ok">正常</span>`.

## Header, banner, KPIs

```html
<div class="hd"><div><h1>各区域状态</h1><p>已读 5 个区域的代表点位，不是活动告警清单</p></div><span class="chip">2026-09-27 15:18 SGT</span></div>
<div class="ban bad"><span class="pill">需关注</span><div><b>CDU-01 点位读取失败，状态未核实</b><div>其余 4 个区域的代表点位未见告警。</div></div></div>
<div class="kpis">
  <div class="kpi ok"><span>区域未见告警</span><b>4 / 5</b></div>
  <div class="kpi bad"><span>数据异常点位</span><b>4</b><i>均在 CDU-01</i></div>
  <div class="kpi ok" title="[DemoTwin]CRAC/G_CRAC1/RAT · Good · 15:18:41"><span>CRAC 回风温度</span><b>22.7<small>°C</small></b><i>G_CRAC1 · 15:18</i></div>
</div>
```

After the KPIs, open `<div class="grid">`. Each section is `<section class="sec full">` for the full width, or `<section class="sec">` to sit beside the next half-width section. Close the grid before the evidence.

## Tiles

One tile per unit or area. Status signals are dot rows. An analog value gets a big number and a range bar.

```html
<section class="sec full"><h2>区域状态</h2><div class="tiles">
  <div class="tile ok"><div class="th"><b>UPS</b><span class="pill">正常</span></div>
    <div class="sig ok"><i class="dot"></i><span>总告警</span><span>正常</span></div>
    <div class="sig ok"><i class="dot"></i><span>输出故障</span><span>正常</span></div>
    <div class="foot">Good · 14:12:35</div></div>
  <div class="tile ok" title="[DemoTwin]CRAC/G_CRAC1/RAT"><div class="th"><b>CRAC</b><span class="pill">正常</span></div>
    <div><div class="big">22.7<small>°C</small></div><div class="lbl">G_CRAC1 回风温度</div></div>
    <div><div class="rb"><i class="band" style="left:15%;width:45%"></i><i class="mk" style="left:38.7%"></i></div>
      <div class="rl"><span>15</span><span>18 – 27</span><span>35</span></div></div>
    <div class="foot">Good · 15:18:41</div></div>
</div></section>
```

Range bar: for a scale from `min` to `max`, a position is `(x - min) / (max - min) × 100` percent. The band runs from the low to the high limit; the marker sits at the value.

## Bars

```html
<section class="sec"><h2>UPS 负载率</h2>
  <div class="bar ok"><span>UPS-A1</span><div class="bt"><i style="width:51%"></i><em style="left:88.9%"></em></div><span>46 %</span></div>
  <div class="bar warn"><span>UPS-B1</span><div class="bt"><i style="width:92.2%"></i><em style="left:88.9%"></em></div><span>83 %</span></div>
  <div class="leg"><span>┆ 设计上限 80 %</span></div></section>
```

Pick a scale maximum a little above the largest value or the limit, here 90. Width is `value / scale × 100` percent, and the dashed limit sits at `limit / scale × 100`.

## Gauges

A semicircle whose path is 100 units long, so the dash length is the percentage.

```html
<section class="sec"><h2>关键指标</h2><div class="gauges">
  <div class="ok"><svg viewBox="0 0 120 70"><path d="M10 60 A50 50 0 0 1 110 60" pathLength="100" fill="none" stroke="#262d37" stroke-width="10" stroke-linecap="round"/>
    <path d="M10 60 A50 50 0 0 1 110 60" pathLength="100" fill="none" stroke="var(--c)" stroke-width="10" stroke-linecap="round" stroke-dasharray="98 100"/>
    <text x="60" y="55" text-anchor="middle" font-size="20" font-weight="650" fill="#e8ecf1">98</text><text x="60" y="68" text-anchor="middle" font-size="10" fill="#6e7886">%</text></svg><div>电池 SOC</div></div>
</div></section>
```

For a value on a scale from `min` to `max`, the first dasharray number is `(value - min) / (max - min) × 100`.

## Donut

Circles 100 units long, rotated to start at the top. Each part's dasharray is `share 100` and its dashoffset is minus the sum of the shares before it.

```html
<section class="sec"><h2>点位质量</h2><div class="donut">
  <svg viewBox="0 0 120 120"><g transform="rotate(-90 60 60)" fill="none" stroke-width="14">
    <circle cx="60" cy="60" r="44" stroke="#1b212a"/>
    <circle class="ok" cx="60" cy="60" r="44" pathLength="100" stroke="var(--c)" stroke-dasharray="81.8 100"/>
    <circle class="bad" cx="60" cy="60" r="44" pathLength="100" stroke="var(--c)" stroke-dasharray="18.2 100" stroke-dashoffset="-81.8"/></g>
    <text x="60" y="62" text-anchor="middle" font-size="22" font-weight="650" fill="#e8ecf1">82%</text><text x="60" y="78" text-anchor="middle" font-size="10" fill="#6e7886">Good</text></svg>
  <div class="rows"><div class="ok"><i class="dot"></i>Good<b>18</b></div><div class="bad"><i class="dot"></i>Bad_NodeIdUnknown<b>4</b></div></div>
</div></section>
```

## Trend

The SVG uses `viewBox="0 0 100 100"` stretched over the plot. For a window from `t0` to `t1` and a y scale from `ymin` to `ymax`:

- x = `(t - t0) / (t1 - t0) × 100`
- y = `100 - (v - ymin) / (ymax - ymin) × 100`

Round to one decimal. Pick `ymin` and `ymax` a little outside the data and the limits so the lines do not touch the edges, and write five y-axis labels from `ymax` down to `ymin`. A gap in the data starts a new `polyline`. Limits and the event band are HTML positioned with the same percentages.

```html
<section class="sec full"><h2>UPS-A1 输入电压，13:15 至 13:55</h2>
  <div class="leg" style="margin:0 0 10px"><span class="info"><i class="dot"></i>UPS-A1</span><span style="--c:var(--s2)"><i class="dot"></i>UPS-A2</span></div>
  <div class="chart"><div class="ya"><span>260</span><span>245</span><span>230</span><span>215</span><span>200</span></div>
    <div class="plot">
      <div style="position:absolute;top:0;bottom:0;left:37.5%;width:25%;background:color-mix(in srgb,var(--alarm) 10%,transparent)" title="事件区间 13:30 至 13:40"></div>
      <div class="lim" style="top:11.7%">253 V</div><div class="lim" style="top:88.3%">207 V</div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none">
        <polyline points="0,51.7 12.5,48.3 25,50 37.5,56.7 50,63.3 62.5,55 75,48.3 87.5,46.7 100,45" fill="none" stroke="var(--info)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
        <polyline points="0,56.7 12.5,55 25,56.7 37.5,60 50,65 62.5,58.3 75,55 87.5,53.3 100,51.7" fill="none" stroke="var(--s2)" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>
      </svg></div>
    <div class="xa"><span>13:15</span><span>13:25</span><span>13:35</span><span>13:45</span><span>13:55</span></div></div>
  <div class="cap">Historian 原始点，每 5 分钟 1 点。虚线为报警限值，红色区间为事件窗口。</div></section>
```

A limit's `top` is the same y formula. Keep each series to about 60 points; thin a longer series and say how in the caption.

## State bars

```html
<section class="sec full"><h2>运行状态</h2>
  <div class="st"><span>UPS-A1</span><div class="sb"><i class="ok" style="left:0;width:45%" title="市电 13:15 至 13:33"></i><i class="warn" style="left:45%;width:10%" title="电池 13:33 至 13:37"></i><i class="ok" style="left:55%;width:45%"></i></div></div>
  <div class="st"><span></span><div class="xa" style="margin:0"><span>13:15</span><span>13:35</span><span>13:55</span></div></div>
  <div class="leg"><span class="ok"><i class="dot"></i>市电</span><span class="warn"><i class="dot"></i>电池</span></div></section>
```

## Timeline

```html
<section class="sec"><h2>时间线</h2><ol class="tl">
  <li class="warn"><span class="mono">14:12:35</span> <span class="pill">首次偏离</span><div>CDU-01 点位开始返回 Bad</div><div class="mono">[DemoTwin]CDU/CDU-01/Alarm</div></li>
  <li class="bad"><span class="mono">15:13:02</span><div>最后一次读取仍为 Bad_NodeIdUnknown</div></li>
</ol></section>
```

## Causes

`meter` shows confidence as three steps: three `on` for high, two for medium, one for low. Put the most likely cause first.

```html
<section class="sec"><h2>可能原因</h2>
  <div class="cz warn"><div class="th"><span><i class="n">1</i>OPC 点位映射错误</span><span class="meter"><i class="on"></i><i class="on"></i><i></i>中</span></div>
    <dl><dt>支持</dt><dd>只有 CDU-01 报 NodeIdUnknown</dd><dt>反对</dt><dd>近期无配置变更记录</dd><dt>验证</dt><dd>核对设备地址</dd></dl></div>
</section>
```

## Checks

```html
<section class="sec full"><h2>下一步</h2><ol class="ck">
  <li><div><div>核对 CDU-01 的 OPC 点位映射</div><div class="m"><span>执行 · Graphene 维护</span><span>预期 · 映射修正后质量恢复 Good</span><span class="pill warn">尽快</span></div></div></li>
</ol></section>
```

## Evidence

After `</div>` closes the grid:

```html
<details><summary>证据 (2)</summary><div class="sc"><table>
  <tr><th>Tag 路径</th><th>值</th><th>质量</th><th>时间</th></tr>
  <tr><td class="mono">[DemoTwin]CDU/CDU-01/Leak</td><td>Error</td><td><span class="pill bad">Bad_NodeIdUnknown</span></td><td class="mono">15:13:02</td></tr>
</table></div></details>
```
