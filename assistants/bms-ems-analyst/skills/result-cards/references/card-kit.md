# Card kit

Copy the base template, then add the components the result needs. Replace every sample value with values from Tool results.

## Base template

The tokens give light and dark colours. The status colours have fixed meanings across every card.

```html
<div class="gc">
<style>
.gc{--bg:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d9dee4;--panel:#f5f7f9;
--ok:#1f7a3d;--warn:#a15c00;--alarm:#c62828;--bad:#6a3fb5;--info:#1d5fa8;
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg);background:var(--bg);
max-width:760px;padding:16px;border:1px solid var(--line);border-radius:10px;box-sizing:border-box}
@media (prefers-color-scheme:dark){.gc{--bg:#16191d;--fg:#e6e9ed;--muted:#9aa4b0;--line:#323840;--panel:#1f2328;
--ok:#4cc27a;--warn:#f0a53a;--alarm:#ff6b6b;--bad:#b392f0;--info:#6aa8f0}}
.gc *{box-sizing:border-box}
.gc h3{margin:0 0 4px;font-size:16px}
.gc .sub{color:var(--muted);font-size:12px;margin-bottom:12px}
.gc .tag{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.gc .pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff}
.gc .ok{background:var(--ok)}.gc .warn{background:var(--warn)}.gc .alarm{background:var(--alarm)}
.gc .bad{background:var(--bad)}.gc .info{background:var(--info)}
.gc table{width:100%;border-collapse:collapse;font-size:13px}
.gc th,.gc td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.gc th{color:var(--muted);font-weight:600}
.gc .scroll{overflow-x:auto}
.gc .cap{color:var(--muted);font-size:12px;margin-top:6px}
</style>
<h3>UPS-A1 transferred to battery at 02:14</h3>
<div class="sub">Graphene live data and Historian, read 2026-09-27 03:05 CST (UTC+8)</div>
<!-- components go here -->
</div>
```

## Status banner

```html
<div style="display:flex;gap:10px;align-items:center;padding:10px 12px;border-radius:8px;background:var(--panel);border-left:4px solid var(--alarm)">
  <span class="pill alarm">Alarm</span>
  <div><b>Load on single path: PDU-A2 on UPS-B1 only.</b> Battery time to impact about 18 min.</div>
</div>
```

Use the border colour and pill of the worst active state.

## Evidence rows

```html
<div class="scroll"><table>
<tr><th>Signal</th><th>Value</th><th>Quality</th><th>Time</th></tr>
<tr><td class="tag">[DemoTwin]Site/DH1/UPS-A1/Input/Voltage</td><td>0 V</td>
<td><span class="pill ok">Good</span></td><td>02:14:07</td></tr>
<tr><td class="tag">[DemoTwin]Site/DH1/UPS-A1/Battery/Runtime</td><td>18 min</td>
<td><span class="pill bad">Uncertain</span></td><td>02:41:55</td></tr>
</table></div>
```

## Timeline

```html
<ol style="list-style:none;margin:0;padding:0 0 0 14px;border-left:2px solid var(--line)">
  <li style="margin:0 0 10px;position:relative">
    <span style="position:absolute;left:-21px;top:4px;width:12px;height:12px;border-radius:50%;background:var(--warn)"></span>
    <b>02:09:40</b> <span class="pill warn">First deviation</span> Input voltage starts to sag, 228 V to 211 V
    <div class="cap tag">[DemoTwin]Site/DH1/UPS-A1/Input/Voltage</div>
  </li>
  <li style="margin:0 0 10px;position:relative">
    <span style="position:absolute;left:-21px;top:4px;width:12px;height:12px;border-radius:50%;background:var(--alarm)"></span>
    <b>02:14:07</b> <span class="pill alarm">Alarm</span> UPS-A1 on battery
  </li>
</ol>
```

Mark the first deviation and the alarm time. Put the source under each event: a Tag path, "audit log" or "reported by user".

## Line chart with hover

Put the points in `data` as `[epoch_ms, value]`. Use `null` for a gap. Set `lo` and `hi` to the alarm limits from `tag_get_config`, or `null` when the Tag has none. The shaded band is the event window.

```html
<div id="ch1" style="position:relative"></div>
<div class="cap">UPS-A1 input voltage, raw Historian points, 02:00 to 02:30 CST. Dashed lines are the alarm limits.</div>
<script>
(function(){
var data=[[1790460000000,229],[1790460060000,228],[1790460120000,null],[1790460180000,211]];
var lo=207,hi=253,unit="V",tag="[DemoTwin]Site/DH1/UPS-A1/Input/Voltage";
var ev=[1790460060000,1790460180000];
var W=720,H=240,P={l:44,r:12,t:10,b:26},el=document.getElementById("ch1");
var pts=data.filter(function(d){return d[1]!==null});
var xs=data.map(function(d){return d[0]}),ys=pts.map(function(d){return d[1]});
if(lo!==null)ys.push(lo);if(hi!==null)ys.push(hi);
var x0=Math.min.apply(0,xs),x1=Math.max.apply(0,xs),y0=Math.min.apply(0,ys),y1=Math.max.apply(0,ys);
var pad=(y1-y0)*0.08||1;y0-=pad;y1+=pad;
function X(t){return P.l+(t-x0)/(x1-x0||1)*(W-P.l-P.r)}
function Y(v){return H-P.b-(v-y0)/(y1-y0)*(H-P.t-P.b)}
function hm(t){var d=new Date(t);return("0"+d.getHours()).slice(-2)+":"+("0"+d.getMinutes()).slice(-2)}
var s='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;display:block" role="img" aria-label="'+tag+'">';
if(ev)s+='<rect x="'+X(ev[0])+'" y="'+P.t+'" width="'+(X(ev[1])-X(ev[0]))+'" height="'+(H-P.t-P.b)+'" fill="var(--alarm)" opacity=".1"/>';
for(var i=0;i<=4;i++){var v=y0+(y1-y0)*i/4;s+='<line x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="var(--line)"/><text x="'+(P.l-6)+'" y="'+(Y(v)+4)+'" text-anchor="end" font-size="11" fill="var(--muted)">'+v.toFixed(0)+'</text>'}
for(var j=0;j<=4;j++){var t=x0+(x1-x0)*j/4;s+='<text x="'+X(t)+'" y="'+(H-8)+'" text-anchor="middle" font-size="11" fill="var(--muted)">'+hm(t)+'</text>'}
[lo,hi].forEach(function(l){if(l!==null)s+='<line x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+Y(l)+'" y2="'+Y(l)+'" stroke="var(--alarm)" stroke-dasharray="5 4"/>'});
var d="",pen=false;data.forEach(function(p){if(p[1]===null){pen=false;return}d+=(pen?"L":"M")+X(p[0]).toFixed(1)+" "+Y(p[1]).toFixed(1);pen=true});
s+='<path d="'+d+'" fill="none" stroke="var(--info)" stroke-width="2"/>';
s+='<line id="ch1x" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="var(--muted)" visibility="hidden"/></svg>';
s+='<div id="ch1t" style="position:absolute;top:0;display:none;padding:4px 8px;border-radius:6px;background:var(--panel);border:1px solid var(--line);font-size:12px;pointer-events:none"></div>';
el.innerHTML=s;
var svg=el.querySelector("svg"),cx=el.querySelector("#ch1x"),tip=el.querySelector("#ch1t");
svg.addEventListener("mousemove",function(e){var r=svg.getBoundingClientRect(),t=x0+((e.clientX-r.left)/r.width*W-P.l)/(W-P.l-P.r)*(x1-x0);
var b=pts[0];pts.forEach(function(p){if(Math.abs(p[0]-t)<Math.abs(b[0]-t))b=p});
cx.setAttribute("x1",X(b[0]));cx.setAttribute("x2",X(b[0]));cx.setAttribute("visibility","visible");
tip.style.display="block";tip.style.left=Math.min(X(b[0])/W*r.width+8,r.width-200)+"px";
tip.innerHTML='<b>'+b[1]+' '+unit+'</b> at '+new Date(b[0]).toLocaleTimeString()+'<br><span class="tag">'+tag+'</span>'});
svg.addEventListener("mouseleave",function(){cx.setAttribute("visibility","hidden");tip.style.display="none"});
})();
</script>
```

For two series, such as the A and B sides, draw a second path in `var(--warn)` and add a small legend above the chart with the Tag path of each colour. Give each chart on a card its own element ids.

## Ranked causes with tabs

```html
<div id="cz">
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
  <button data-k="0">1. Utility sag · Medium</button>
  <button data-k="1">2. Input breaker · Low</button>
</div>
<div data-p="0"><p><b>For:</b> input voltage fell on both UPS-A1 and UPS-A2 at 02:09.</p>
<p><b>Against:</b> the utility meter shows no event.</p>
<p><b>Settles it:</b> field check 1, the ATS event log.</p></div>
<div data-p="1" hidden><p>...</p></div>
</div>
<style>#cz button{font:inherit;padding:4px 10px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--fg);cursor:pointer}
#cz button[aria-pressed=true]{border-color:var(--info);outline:2px solid var(--info)}</style>
<script>(function(){var r=document.getElementById("cz");function sel(k){r.querySelectorAll("[data-p]").forEach(function(p){p.hidden=p.dataset.p!==k});
r.querySelectorAll("button").forEach(function(b){b.setAttribute("aria-pressed",b.dataset.k===k)})}
r.querySelectorAll("button").forEach(function(b){b.onclick=function(){sel(b.dataset.k)}});sel("0")})();</script>
```

Order the tabs by likelihood. Each tab names the evidence for, the evidence against and the field check that settles it.

## Tile grid

```html
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px">
  <div style="padding:10px;border-radius:8px;background:var(--panel);border-top:3px solid var(--alarm)">
    <div class="tag">CRAH-03</div><div style="font-size:20px;font-weight:700">29.4 °C</div>
    <span class="pill alarm">Alarm</span> <span class="cap">supply air, 03:02</span>
  </div>
</div>
```

Sort the tiles worst state first.

## One-line path diagram

Draw the path left to right as boxes joined by lines in inline SVG. Colour each element by state: `var(--ok)` in service, `var(--alarm)` lost, `var(--muted)` open or out of service by design. Label each element with its equipment tag and state in text.
