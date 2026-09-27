/* graphene-card v1 */
var GC=(function(){
var TH={dark:{bg:"#0d1117",card:"#151a21",raise:"#1b212a",line:"#262d37",fg:"#e8ecf1",sub:"#aab3bf",mute:"#6e7886",
ok:"#34d399",warn:"#fbbf24",alarm:"#f87171",bad:"#a78bfa",unknown:"#7b8594",off:"#4b5563",info:"#60a5fa",s2:"#f472b6",s3:"#2dd4bf",s4:"#fb923c"},
light:{bg:"#f6f8fa",card:"#ffffff",raise:"#f1f4f7",line:"#e2e7ed",fg:"#111827",sub:"#4b5563",mute:"#8a94a3",
ok:"#059669",warn:"#d97706",alarm:"#dc2626",bad:"#7c3aed",unknown:"#6b7280",off:"#9ca3af",info:"#2563eb",s2:"#db2777",s3:"#0d9488",s4:"#ea580c"}};
var L={zh:{ok:"正常",warn:"预警",alarm:"告警",bad:"数据异常",unknown:"未核实",off:"停用",info:"信息",evidence:"证据",quality:"质量",
time:"时间",value:"值",path:"Tag 路径",for:"支持",against:"反对",check:"验证",owner:"执行",expect:"预期",conf:{high:"高",medium:"中",low:"低"}},
en:{ok:"Normal",warn:"Warning",alarm:"Alarm",bad:"Bad data",unknown:"Unverified",off:"Off",info:"Info",evidence:"Evidence",quality:"Quality",
time:"Time",value:"Value",path:"Tag path",for:"For",against:"Against",check:"Settles it",owner:"Owner",expect:"Expected",conf:{high:"High",medium:"Medium",low:"Low"}}};
var ORDER=["alarm","bad","warn","unknown","off","ok","info"],SER=["info","s2","s3","s4","warn","ok"];
var T,W,CW=720,uid=0;
function e(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function c(l){return T[l]||T.unknown}
function lab(l){return W[l]||l}
function n(v,d){if(v==null||v!==v)return"–";var a=Math.abs(v);if(d==null)d=v%1===0||a>=100?0:a>=10?1:2;return Number(v).toFixed(d)}
function pill(l,t){return'<span class="gc-pill" style="color:'+c(l)+';background:'+c(l)+'1f;border-color:'+c(l)+'40"><i style="background:'+c(l)+'"></i>'+e(t||lab(l))+'</span>'}
function dot(l){return'<i class="gc-dot" style="background:'+c(l)+';box-shadow:0 0 0 3px '+c(l)+'29"></i>'}
function hm(t){var d=new Date(t);return("0"+d.getHours()).slice(-2)+":"+("0"+d.getMinutes()).slice(-2)}
function src(o){var a=[];if(o.tag)a.push(o.tag);if(o.quality)a.push(o.quality);if(o.time)a.push(o.time);return a.join(" · ")}
function css(){return'.gc{--bg:'+T.bg+';--card:'+T.card+';--raise:'+T.raise+';--line:'+T.line+';--fg:'+T.fg+';--sub:'+T.sub+';--mute:'+T.mute+';'+
'background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:24px;border-radius:16px;box-sizing:border-box;font-variant-numeric:tabular-nums}'+
'.gc *{box-sizing:border-box}.gc svg{display:block;overflow:visible}'+
'.gc-hd{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:20px}'+
'.gc-t{font-size:20px;font-weight:650;letter-spacing:-.01em;margin:0}.gc-st{color:var(--sub);font-size:13px;margin-top:4px}'+
'.gc-chip{flex:none;font-size:12px;color:var(--sub);background:var(--raise);border:1px solid var(--line);padding:4px 10px;border-radius:999px;white-space:nowrap}'+
'.gc-ban{display:flex;gap:14px;align-items:center;padding:14px 18px;border-radius:12px;margin-bottom:20px;border:1px solid}'+
'.gc-ban b{font-size:15px;font-weight:600}.gc-ban div div{color:var(--sub);font-size:13px;margin-top:2px}'+
'.gc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.gc-full{grid-column:1/-1}'+
'@media(max-width:620px){.gc{padding:16px}.gc-grid{grid-template-columns:1fr}.gc-hd{flex-direction:column}}'+
'.gc-sec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;min-width:0}'+
'.gc-sh{font-size:13px;font-weight:600;color:var(--sub);margin:0 0 14px;display:flex;justify-content:space-between;gap:8px}'+
'.gc-cap{color:var(--mute);font-size:12px;margin-top:10px}'+
'.gc-pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:2px 9px 2px 8px;border-radius:999px;border:1px solid;white-space:nowrap}'+
'.gc-pill i{width:6px;height:6px;border-radius:50%}'+
'.gc-dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:none}'+
'.gc-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}'+
'.gc-kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;position:relative;overflow:hidden}'+
'.gc-kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--k)}'+
'.gc-kl{font-size:12px;color:var(--sub)}.gc-kv{font-size:28px;font-weight:650;letter-spacing:-.02em;margin-top:6px;line-height:1.1}'+
'.gc-kv small{font-size:13px;color:var(--mute);font-weight:500;margin-left:3px}.gc-kn{font-size:12px;color:var(--mute);margin-top:6px}'+
'.gc-seg{display:flex;height:10px;border-radius:999px;overflow:hidden;gap:2px;background:var(--raise)}'+
'.gc-leg{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:12px;font-size:12px;color:var(--sub)}.gc-leg span{display:inline-flex;align-items:center;gap:6px}'+
'.gc-leg b{color:var(--fg);font-weight:600}'+
'.gc-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}'+
'.gc-tile{background:var(--raise);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:10px}'+
'.gc-th{display:flex;justify-content:space-between;align-items:center;gap:8px}.gc-tn{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'+
'.gc-tv{font-size:24px;font-weight:650;letter-spacing:-.02em;line-height:1}.gc-tv small{font-size:12px;color:var(--mute);font-weight:500;margin-left:3px}'+
'.gc-sig{display:flex;flex-direction:column;gap:6px;font-size:12.5px}.gc-sig div{display:flex;align-items:center;gap:8px;color:var(--sub)}.gc-sig div span:last-child{margin-left:auto;color:var(--fg)}'+
'.gc-tf{font-size:11.5px;color:var(--mute);border-top:1px solid var(--line);padding-top:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'+
'.gc-rb{position:relative;height:6px;border-radius:999px;background:var(--line)}.gc-rb span{position:absolute;top:0;bottom:0;border-radius:999px}'+
'.gc-rb em{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;background:var(--fg);transform:translateX(-50%);box-shadow:0 0 0 2px var(--raise)}'+
'.gc-rl{display:flex;justify-content:space-between;font-size:11px;color:var(--mute);margin-top:5px}'+
'.gc-bars{display:flex;flex-direction:column;gap:12px}.gc-bar{display:grid;grid-template-columns:minmax(80px,30%) 1fr 72px;gap:12px;align-items:center;font-size:13px}'+
'.gc-bar>div:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--sub)}.gc-bt{position:relative;height:10px;background:var(--raise);border-radius:999px}'+
'.gc-bt span{position:absolute;left:0;top:0;bottom:0;border-radius:999px}.gc-bt em{position:absolute;top:-4px;bottom:-4px;width:0;border-left:2px dashed var(--mute)}.gc-bar>div:last-child{text-align:right;font-weight:600}'+
'.gc-gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.gc-g{text-align:center}.gc-g .gc-tn{margin-top:4px;font-size:13px;color:var(--sub);font-weight:500}'+
'.gc-tl{list-style:none;margin:0;padding:0;position:relative}.gc-tl:before{content:"";position:absolute;left:5px;top:6px;bottom:6px;width:2px;background:var(--line)}'+
'.gc-tl li{position:relative;padding:0 0 16px 26px}.gc-tl li:last-child{padding-bottom:0}.gc-tl li>i{position:absolute;left:0;top:5px;width:12px;height:12px;border-radius:50%;border:2px solid var(--card)}'+
'.gc-tt{font-size:12px;color:var(--mute);font-family:ui-monospace,Consolas,monospace}.gc-tx{margin-top:2px}.gc-ts{font-size:11.5px;color:var(--mute);margin-top:2px}'+
'.gc-cz{display:flex;flex-direction:column;gap:10px}.gc-c{background:var(--raise);border:1px solid var(--line);border-radius:12px;padding:14px 16px}'+
'.gc-ch{display:flex;align-items:center;gap:12px}.gc-cr{flex:none;width:26px;height:26px;border-radius:8px;display:grid;place-items:center;font-weight:700;font-size:13px;background:var(--line)}'+
'.gc-cm{display:flex;gap:3px;margin-left:auto;align-items:center;font-size:12px;color:var(--sub)}.gc-cm i{width:14px;height:6px;border-radius:2px;background:var(--line)}'+
'.gc-cd{display:grid;grid-template-columns:72px 1fr;gap:6px 12px;font-size:13px;margin-top:12px}.gc-cd dt{color:var(--mute)}.gc-cd dd{margin:0}'+
'.gc-ck{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;counter-reset:k}'+
'.gc-ck li{counter-increment:k;display:grid;grid-template-columns:28px 1fr;gap:12px;background:var(--raise);border:1px solid var(--line);border-radius:12px;padding:12px 14px}'+
'.gc-ck li:before{content:counter(k);width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:var(--line);font-weight:700;font-size:13px}'+
'.gc-ck .gc-m{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--mute);margin-top:6px}'+
'.gc-tb{width:100%;border-collapse:collapse;font-size:13px}.gc-tb th{text-align:left;font-weight:600;color:var(--mute);font-size:12px;padding:0 10px 8px;border-bottom:1px solid var(--line)}'+
'.gc-tb td{padding:9px 10px;border-bottom:1px solid var(--line)}.gc-tb tr:last-child td{border-bottom:0}.gc-sc{overflow-x:auto}'+
'.gc-mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--sub)}'+
'.gc-ev{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 20px}.gc-ev summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--sub)}'+
'.gc-ev .gc-sc{margin-top:12px}.gc-ch2{position:relative}.gc-tip{position:absolute;top:0;pointer-events:none;background:var(--raise);border:1px solid var(--line);border-radius:10px;padding:8px 10px;font-size:12px;display:none;box-shadow:0 8px 24px #0006;min-width:140px;z-index:2}'+
'.gc-tip div{display:flex;align-items:center;gap:6px}.gc-tip b{margin-left:auto;padding-left:10px}'+
'.gc-p{margin:0;color:var(--sub);font-size:13.5px}'}
function rangebar(v,min,max,lo,hi,l){if(min==null||max==null||v==null)return"";var p=function(x){return Math.max(0,Math.min(100,(x-min)/(max-min)*100))};
var a=lo==null?min:lo,b=hi==null?max:hi,s='<div class="gc-rb"><span style="left:'+p(a)+'%;width:'+(p(b)-p(a))+'%;background:'+T.ok+'40"></span><em style="left:'+p(v)+'%;background:'+c(l)+'"></em></div>';
return s+'<div class="gc-rl"><span>'+n(min)+'</span>'+(lo!=null||hi!=null?'<span>'+(lo!=null?n(lo):"")+(lo!=null&&hi!=null?" – ":"")+(hi!=null?n(hi):"")+'</span>':"")+'<span>'+n(max)+'</span></div>'}
function spark(pts,l,h){if(!pts||pts.length<2)return"";h=h||34;var ys=pts.map(function(p){return p[1]}).filter(function(v){return v!=null}),y0=Math.min.apply(0,ys),y1=Math.max.apply(0,ys),x0=pts[0][0],x1=pts[pts.length-1][0];
var X=function(t){return(t-x0)/(x1-x0||1)*200},Y=function(v){return h-2-(v-y0)/(y1-y0||1)*(h-4)},d="",a="",pen=false,id="gs"+(++uid);
pts.forEach(function(p){if(p[1]==null){pen=false;return}d+=(pen?"L":"M")+X(p[0]).toFixed(1)+" "+Y(p[1]).toFixed(1);pen=true});
var last=pts.filter(function(p){return p[1]!=null}).pop();
return'<svg viewBox="0 0 200 '+h+'" preserveAspectRatio="none" style="width:100%;height:'+h+'px"><defs><linearGradient id="'+id+'" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="'+c(l)+'" stop-opacity=".28"/><stop offset="1" stop-color="'+c(l)+'" stop-opacity="0"/></linearGradient></defs>'+
'<path d="'+d+'L'+X(last[0]).toFixed(1)+' '+h+'L'+X(pts[0][0])+' '+h+'Z" fill="url(#'+id+')" stroke="none"/><path d="'+d+'" fill="none" stroke="'+c(l)+'" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>'}
function sec(o,inner){return'<section class="gc-sec'+(o.half?"":" gc-full")+'">'+(o.title?'<h4 class="gc-sh"><span>'+e(o.title)+'</span>'+(o.meta?'<span style="font-weight:500;color:var(--mute)">'+e(o.meta)+'</span>':"")+'</h4>':"")+inner+(o.caption?'<div class="gc-cap">'+e(o.caption)+'</div>':"")+'</section>'}
var R={};
R.summary=function(o){var tot=0,s="",lg="";ORDER.forEach(function(l){var v=o.counts[l];if(!v)return;tot+=v});
ORDER.forEach(function(l){var v=o.counts[l];if(!v)return;s+='<span style="flex:'+v+';background:'+c(l)+'" title="'+e(lab(l))+' '+v+'"></span>';lg+='<span>'+dot(l)+e(lab(l))+' <b>'+v+'</b></span>'});
return sec(o,'<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:12px"><span style="font-size:28px;font-weight:650">'+tot+'</span><span style="color:var(--mute);font-size:13px">'+e(o.unit||"")+'</span></div><div class="gc-seg">'+s+'</div><div class="gc-leg">'+lg+'</div>')};
R.tiles=function(o){var s='<div class="gc-tiles">';o.items.forEach(function(t){var l=t.level||"unknown";
s+='<div class="gc-tile" style="border-top:3px solid '+c(l)+'"><div class="gc-th"><span class="gc-tn" title="'+e(t.name)+'">'+e(t.name)+'</span>'+pill(l,t.state)+'</div>';
if(t.value!=null)s+='<div class="gc-tv">'+e(typeof t.value=="number"?n(t.value,t.digits):t.value)+'<small>'+e(t.unit||"")+'</small>'+(t.label?'<div style="font-size:12px;color:var(--mute);font-weight:500;letter-spacing:0;margin-top:6px">'+e(t.label)+'</div>':"")+'</div>';
if(t.range)s+='<div>'+rangebar(t.value,t.range[0],t.range[1],t.lo,t.hi,l)+'</div>';
if(t.spark)s+=spark(t.spark,l==="ok"?"info":l);
if(t.signals){s+='<div class="gc-sig">';t.signals.forEach(function(g){s+='<div title="'+e(src(g))+'">'+dot(g.level||"unknown")+'<span>'+e(g.label)+'</span><span>'+e(g.text||lab(g.level||"unknown"))+'</span></div>'});s+='</div>'}
if(t.note)s+='<div style="font-size:12.5px;color:var(--sub)">'+e(t.note)+'</div>';
var f=[t.quality,t.time].filter(Boolean).join(" · ");if(f||t.tag)s+='<div class="gc-tf" title="'+e(src(t))+'">'+e(f||t.tag)+'</div>';s+='</div>'});return sec(o,s+'</div>')};
R.gauges=function(o){var s='<div class="gc-gauges">';o.items.forEach(function(g){var l=g.level||"ok",min=g.min||0,max=g.max==null?100:g.max,f=Math.max(0,Math.min(1,(g.value-min)/(max-min))),r=52,cx=64,cy=62;
function pt(a){return[cx-r*Math.cos(a*Math.PI),cy-r*Math.sin(a*Math.PI)]}function arc(a,b,col,w,o2){var p=pt(a),q=pt(b);return'<path d="M'+p[0].toFixed(1)+' '+p[1].toFixed(1)+'A'+r+' '+r+' 0 0 1 '+q[0].toFixed(1)+' '+q[1].toFixed(1)+'" stroke="'+col+'" stroke-width="'+w+'" fill="none" stroke-linecap="round"'+(o2?' opacity="'+o2+'"':"")+'/>'}
var s2='<svg viewBox="0 0 128 76" style="width:100%;max-width:170px;margin:0 auto">'+arc(0,1,T.line,10);
if(g.hi!=null){var h=(g.hi-min)/(max-min);s2+=arc(Math.max(0,Math.min(1,h)),1,T.alarm,10,.35)}
if(f>0)s2+=arc(0,Math.max(f,.001),c(l),10);
s2+='<text x="64" y="58" text-anchor="middle" font-size="20" font-weight="650" fill="'+T.fg+'">'+e(n(g.value,g.digits))+'</text><text x="64" y="72" text-anchor="middle" font-size="10" fill="'+T.mute+'">'+e(g.unit||"")+'</text></svg>';
s+='<div class="gc-g" title="'+e(src(g))+'">'+s2+'<div class="gc-tn">'+e(g.name)+'</div></div>'});return sec(o,s+'</div>')};
R.bars=function(o){var mx=o.max;if(mx==null){mx=0;o.items.forEach(function(b){mx=Math.max(mx,b.value)});if(o.limit!=null)mx=Math.max(mx,o.limit);mx*=1.08}
var s='<div class="gc-bars">';o.items.forEach(function(b){var l=b.level||"info";s+='<div class="gc-bar" title="'+e(src(b))+'"><div>'+e(b.name)+'</div><div class="gc-bt"><span style="width:'+Math.max(0,Math.min(100,b.value/mx*100))+'%;background:'+c(l)+'"></span>'+(o.limit!=null?'<em style="left:'+o.limit/mx*100+'%"></em>':"")+'</div><div>'+e(n(b.value,o.digits))+'<span style="color:var(--mute);font-weight:500;font-size:12px"> '+e(o.unit||"")+'</span></div></div>'});
return sec(o,s+'</div>'+(o.limit!=null?'<div class="gc-leg"><span><i style="width:14px;border-top:2px dashed '+T.mute+'"></i>'+e(o.limitLabel||"")+' '+e(n(o.limit))+' '+e(o.unit||"")+'</span></div>':""))};
R.donut=function(o){var tot=0;o.parts.forEach(function(p){tot+=p.value});var a=-Math.PI/2,r=44,s='<svg viewBox="0 0 120 120" style="width:132px;flex:none">';
s+='<circle cx="60" cy="60" r="'+r+'" fill="none" stroke="'+T.raise+'" stroke-width="14"/>';
o.parts.forEach(function(p){if(!p.value)return;var da=p.value/tot*Math.PI*2,gap=o.parts.length>1?.04:0,b=a+da-gap,l=da-gap>Math.PI?1:0;
s+='<path d="M'+(60+r*Math.cos(a)).toFixed(2)+' '+(60+r*Math.sin(a)).toFixed(2)+'A'+r+' '+r+' 0 '+l+' 1 '+(60+r*Math.cos(b)).toFixed(2)+' '+(60+r*Math.sin(b)).toFixed(2)+'" stroke="'+c(p.level||"info")+'" stroke-width="14" fill="none"/>';a+=da});
s+='<text x="60" y="60" text-anchor="middle" font-size="22" font-weight="650" fill="'+T.fg+'">'+e(o.center!=null?o.center:tot)+'</text><text x="60" y="76" text-anchor="middle" font-size="10" fill="'+T.mute+'">'+e(o.centerLabel||"")+'</text></svg>';
var lg='<div style="display:flex;flex-direction:column;gap:10px;font-size:13px;flex:1;min-width:0">';o.parts.forEach(function(p){lg+='<div style="display:flex;align-items:center;gap:8px">'+dot(p.level||"info")+'<span style="color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+e(p.label)+'</span><b style="margin-left:auto;padding-left:12px">'+e(p.value)+'</b></div>'});
return sec(o,'<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">'+s+lg+'</div></div>')};
R.line=function(o){var id="gl"+(++uid),H=o.height||220,Wd=Math.max(300,Math.min(900,CW-(CW<620?74:90))),P={l:44,r:14,t:10,b:26},all=[],xs=[];
o.series.forEach(function(sr){sr.points.forEach(function(p){xs.push(p[0]);if(p[1]!=null)all.push(p[1])})});
if(o.lo!=null)all.push(o.lo);if(o.hi!=null)all.push(o.hi);
var x0=Math.min.apply(0,xs),x1=Math.max.apply(0,xs),y0=Math.min.apply(0,all),y1=Math.max.apply(0,all),pd=(y1-y0)*.12||1;y0-=pd;y1+=pd;
var X=function(t){return P.l+(t-x0)/(x1-x0||1)*(Wd-P.l-P.r)},Y=function(v){return H-P.b-(v-y0)/(y1-y0)*(H-P.t-P.b)};
var s='<svg viewBox="0 0 '+Wd+' '+H+'" style="width:100%;height:auto">';
if(o.band)s+='<rect x="'+X(o.band[0])+'" y="'+P.t+'" width="'+Math.max(1,X(o.band[1])-X(o.band[0]))+'" height="'+(H-P.t-P.b)+'" fill="'+c(o.bandLevel||"alarm")+'" opacity=".1"/>';
for(var i=0;i<=4;i++){var v=y0+(y1-y0)*i/4;s+='<line x1="'+P.l+'" x2="'+(Wd-P.r)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="'+T.line+'"/><text x="'+(P.l-8)+'" y="'+(Y(v)+4)+'" text-anchor="end" font-size="11" fill="'+T.mute+'">'+n(v)+'</text>'}
for(var j=0;j<=4;j++){var t=x0+(x1-x0)*j/4;s+='<text x="'+X(t)+'" y="'+(H-6)+'" text-anchor="middle" font-size="11" fill="'+T.mute+'">'+hm(t)+'</text>'}
[["lo",o.lo],["hi",o.hi]].forEach(function(k){if(k[1]!=null)s+='<line x1="'+P.l+'" x2="'+(Wd-P.r)+'" y1="'+Y(k[1])+'" y2="'+Y(k[1])+'" stroke="'+T.alarm+'" stroke-dasharray="5 5" opacity=".8"/><text x="'+(Wd-P.r)+'" y="'+(Y(k[1])-5)+'" text-anchor="end" font-size="10.5" fill="'+T.alarm+'">'+e(n(k[1]))+' '+e(o.unit||"")+'</text>'});
(o.marks||[]).forEach(function(m){s+='<line x1="'+X(m.time)+'" x2="'+X(m.time)+'" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="'+c(m.level||"alarm")+'" stroke-width="1.5"/><text x="'+(X(m.time)+5)+'" y="'+(P.t+11)+'" font-size="10.5" fill="'+c(m.level||"alarm")+'">'+e(m.label)+'</text>'});
o.series.forEach(function(sr,k){var col=c(sr.level||SER[k%SER.length]),d="",pen=false;sr.points.forEach(function(p){if(p[1]==null){pen=false;return}d+=(pen?"L":"M")+X(p[0]).toFixed(1)+" "+Y(p[1]).toFixed(1);pen=true});
s+='<path d="'+d+'" fill="none" stroke="'+col+'" stroke-width="2" stroke-linejoin="round"'+(sr.dash?' stroke-dasharray="6 4"':"")+'/>'});
s+='<line class="gc-x" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="'+T.mute+'" visibility="hidden"/></svg><div class="gc-tip"></div>';
var lg='<div class="gc-leg" style="margin:0 0 10px">';o.series.forEach(function(sr,k){lg+='<span title="'+e(sr.tag||"")+'"><i style="width:14px;height:3px;border-radius:2px;background:'+c(sr.level||SER[k%SER.length])+'"></i>'+e(sr.name)+'</span>'});lg+='</div>';
setTimeout(function(){var el=document.getElementById(id);if(!el)return;var sv=el.querySelector("svg"),x=el.querySelector(".gc-x"),tp=el.querySelector(".gc-tip");
sv.addEventListener("mousemove",function(ev){var r=sv.getBoundingClientRect(),t=x0+((ev.clientX-r.left)/r.width*Wd-P.l)/(Wd-P.l-P.r)*(x1-x0),h="",bt=null;
o.series.forEach(function(sr,k){var b=null;sr.points.forEach(function(p){if(p[1]!=null&&(!b||Math.abs(p[0]-t)<Math.abs(b[0]-t)))b=p});if(!b)return;if(bt==null)bt=b[0];
h+='<div>'+dot(sr.level||SER[k%SER.length])+'<span>'+e(sr.name)+'</span><b>'+n(b[1])+' '+e(o.unit||"")+'</b></div>'});if(bt==null)return;
x.setAttribute("x1",X(bt));x.setAttribute("x2",X(bt));x.setAttribute("visibility","visible");tp.innerHTML='<div class="gc-mono" style="margin-bottom:4px">'+new Date(bt).toLocaleString()+'</div>'+h;tp.style.display="block";
tp.style.left=Math.max(0,Math.min(X(bt)/Wd*r.width+12,r.width-tp.offsetWidth))+"px";tp.style.top="36px"});
sv.addEventListener("mouseleave",function(){x.setAttribute("visibility","hidden");tp.style.display="none"})},0);
return sec(o,lg+'<div class="gc-ch2" id="'+id+'">'+s+'</div>')};
R.states=function(o){var x0=o.from,x1=o.to,s='<div style="display:flex;flex-direction:column;gap:10px">',p=function(t){return(t-x0)/(x1-x0)*100};
o.rows.forEach(function(r){s+='<div style="display:grid;grid-template-columns:minmax(70px,22%) 1fr;gap:12px;align-items:center;font-size:13px"><div style="color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+e(r.name)+'</div><div style="position:relative;height:18px;background:var(--raise);border-radius:5px;overflow:hidden">';
r.segments.forEach(function(g){s+='<span title="'+e((g[3]||lab(g[2]))+" "+hm(g[0])+"–"+hm(g[1]))+'" style="position:absolute;top:0;bottom:0;left:'+p(g[0])+'%;width:'+Math.max(.4,p(g[1])-p(g[0]))+'%;background:'+c(g[2])+';opacity:.85"></span>'});s+='</div></div>'});
s+='<div style="display:grid;grid-template-columns:minmax(70px,22%) 1fr;gap:12px"><span></span><div style="display:flex;justify-content:space-between;font-size:11px;color:var(--mute)">';for(var i=0;i<=4;i++)s+='<span>'+hm(x0+(x1-x0)*i/4)+'</span>';s+='</div></div></div>';
var seen={},lg='<div class="gc-leg">';o.rows.forEach(function(r){r.segments.forEach(function(g){var k=g[2]+"|"+(g[3]||"");if(seen[k])return;seen[k]=1;lg+='<span>'+dot(g[2])+e(g[3]||lab(g[2]))+'</span>'})});
return sec(o,s+lg+'</div>')};
R.timeline=function(o){var s='<ol class="gc-tl">';o.items.forEach(function(t){var l=t.level||"info";s+='<li><i style="background:'+c(l)+';box-shadow:0 0 0 3px '+c(l)+'33"></i><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="gc-tt">'+e(t.time)+'</span>'+(t.tagline?pill(l,t.tagline):"")+'</div><div class="gc-tx">'+e(t.text)+'</div>'+(t.source?'<div class="gc-ts gc-mono">'+e(t.source)+'</div>':"")+'</li>'});return sec(o,s+'</ol>')};
R.causes=function(o){var s='<div class="gc-cz">',m={high:3,medium:2,low:1},mc={high:T.alarm,medium:T.warn,low:T.unknown};o.items.forEach(function(k,i){var q=m[k.confidence]||1,bar="";for(var j=1;j<=3;j++)bar+='<i style="'+(j<=q?"background:"+mc[k.confidence]:"")+'"></i>';
s+='<div class="gc-c"'+(i===0?' style="border-color:'+mc[k.confidence]+'66"':"")+'><div class="gc-ch"><span class="gc-cr">'+(i+1)+'</span><b style="font-weight:600">'+e(k.name)+'</b><span class="gc-cm">'+bar+'<span style="margin-left:6px">'+e(W.conf[k.confidence]||k.confidence)+'</span></span></div><dl class="gc-cd">'+
(k.for?'<dt>'+W["for"]+'</dt><dd>'+e(k.for)+'</dd>':"")+(k.against?'<dt>'+W.against+'</dt><dd>'+e(k.against)+'</dd>':"")+(k.check?'<dt>'+W.check+'</dt><dd>'+e(k.check)+'</dd>':"")+'</dl></div>'});return sec(o,s+'</div>')};
R.checks=function(o){var s='<ol class="gc-ck">';o.items.forEach(function(k){s+='<li><div><div>'+e(k.text)+'</div><div class="gc-m">'+(k.owner?'<span>'+W.owner+' · '+e(k.owner)+'</span>':"")+(k.expect?'<span>'+W.expect+' · '+e(k.expect)+'</span>':"")+(k.level?pill(k.level,k.urgency):"")+'</div></div></li>'});return sec(o,s+'</ol>')};
R.table=function(o){var s='<div class="gc-sc"><table class="gc-tb"><tr>';o.columns.forEach(function(h){s+='<th>'+e(h)+'</th>'});s+='</tr>';
o.rows.forEach(function(r){s+='<tr>';r.forEach(function(v){s+='<td>'+(v&&typeof v=="object"?(v.level?pill(v.level,v.text):'<span class="gc-mono">'+e(v.text)+'</span>'):e(v))+'</td>'});s+='</tr>'});return sec(o,s+'</table></div>')};
R.note=function(o){return sec(o,'<p class="gc-p">'+e(o.text)+'</p>')};
function render(elId,sp){CW=document.getElementById(elId).clientWidth||720;T=TH[sp.theme==="light"?"light":"dark"];W=L[sp.lang==="en"?"en":"zh"];var s='<style>'+css()+'</style><div class="gc">';
s+='<div class="gc-hd"><div><h3 class="gc-t">'+e(sp.title)+'</h3>'+(sp.subtitle?'<div class="gc-st">'+e(sp.subtitle)+'</div>':"")+'</div>'+(sp.asof?'<span class="gc-chip">'+e(sp.asof)+'</span>':"")+'</div>';
if(sp.status){var l=sp.status.level||"info";s+='<div class="gc-ban" style="background:'+c(l)+'14;border-color:'+c(l)+'4d">'+pill(l,sp.status.tag)+'<div><b>'+e(sp.status.text)+'</b>'+(sp.status.detail?'<div>'+e(sp.status.detail)+'</div>':"")+'</div></div>'}
if(sp.kpis){s+='<div class="gc-kpis">';sp.kpis.forEach(function(k){s+='<div class="gc-kpi" style="--k:'+c(k.level||"info")+'" title="'+e(src(k))+'"><div class="gc-kl">'+e(k.label)+'</div><div class="gc-kv">'+e(typeof k.value=="number"?n(k.value,k.digits):k.value)+'<small>'+e(k.unit||"")+'</small></div>'+(k.note?'<div class="gc-kn">'+e(k.note)+'</div>':"")+'</div>'});s+='</div>'}
s+='<div class="gc-grid">';(sp.sections||[]).forEach(function(o){var f=R[o.type];if(f)s+=f(o)});s+='</div>';
if(sp.evidence&&sp.evidence.length){s+='<details class="gc-ev"><summary>'+W.evidence+' ('+sp.evidence.length+')</summary><div class="gc-sc"><table class="gc-tb"><tr><th>'+W.path+'</th><th>'+W.value+'</th><th>'+W.quality+'</th><th>'+W.time+'</th></tr>';
sp.evidence.forEach(function(v){s+='<tr><td class="gc-mono">'+e(v.tag)+'</td><td>'+e(v.value)+'</td><td>'+pill(v.level||(/^good/i.test(v.quality||"")?"ok":"bad"),v.quality)+'</td><td class="gc-mono">'+e(v.time)+'</td></tr>'});s+='</table></div></details>'}
document.getElementById(elId).innerHTML=s+'</div>'}
return{render:render}})();
