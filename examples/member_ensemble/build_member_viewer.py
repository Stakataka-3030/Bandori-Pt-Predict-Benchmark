"""Build a self-contained interactive member-track viewer from a replay JSON."""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>逐报点成员预测</title>
<style>
:root{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#26323b;background:#f5f6f3}
*{box-sizing:border-box}body{margin:0}.shell{max-width:1480px;margin:auto;padding:28px 30px 32px}
.header{display:flex;justify-content:space-between;align-items:end;gap:20px;flex-wrap:wrap}.eyebrow{font-size:13px;letter-spacing:.14em;color:#688272;font-weight:700}.header h1{font-size:31px;line-height:1.25;margin:8px 0 5px}.sub{color:#687681;font-size:14px;line-height:1.6;margin:0}.badge{border:1px solid #c8d7cb;background:#e8f1e9;color:#467154;border-radius:30px;padding:8px 13px;font-size:13px;font-weight:700}
.panel{background:#fff;border:1px solid #e4e8e3;border-radius:20px;box-shadow:0 10px 34px #26323b0a;margin-top:22px;padding:20px 22px 14px}
.toolbar{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}.group{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.group-label{font-size:13px;color:#78858b;margin-right:7px}.group button{appearance:none;border:1px solid #dbe2dd;background:#f9faf8;color:#586971;border-radius:9px;font:inherit;font-size:13px;font-weight:700;padding:8px 12px;cursor:pointer}.group button.active{background:#e8f1e9;border-color:#70a386;color:#315d45}.group button:hover{border-color:#70a386}
.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:19px 0 6px}.stat{border:1px solid #e5eae5;background:#fafbf9;border-radius:12px;padding:11px 13px}.stat span{display:block;font-size:12px;color:#6c7a82}.stat strong{font-size:21px;display:block;margin-top:3px;white-space:nowrap}.stat small{font-size:11px;color:#7b898f}
#chart{display:block;width:100%;height:auto;min-height:380px}.legend{display:flex;justify-content:center;gap:20px;flex-wrap:wrap;font-size:12px;color:#5f6e76;padding:0 0 12px}.swatch{display:inline-block;width:22px;height:3px;vertical-align:middle;margin-right:6px}.note{font-size:12px;color:#718087;border-top:1px solid #edf0ec;padding-top:11px;line-height:1.6}.tooltip{position:fixed;display:none;pointer-events:none;background:#233139;color:white;padding:9px 11px;border-radius:8px;font-size:12px;box-shadow:0 6px 20px #0003;z-index:10;line-height:1.6}
.terminal-title{font-size:17px;font-weight:700;margin:18px 0 2px;padding-top:18px;border-top:1px solid #e7ebe7}.terminal-sub{font-size:12px;color:#6f7e85;margin:0 0 5px}#terminal{display:block;width:100%;height:auto}
@media(max-width:850px){.shell{padding:17px}.header h1{font-size:24px}.stats{grid-template-columns:repeat(2,1fr)}.stat strong{font-size:17px}.panel{padding:14px 10px}.legend{gap:10px}#chart{min-height:280px}}
</style></head><body><main class="shell"><div class="header"><div><div class="eyebrow">BANDORI PT · SEQUENTIAL ENSEMBLE</div><h1 id="title">50 成员逐报点预测线</h1><p class="sub">选择截点，查看当时可见档线、50 条未来成员轨迹及下一报点后的剪枝结果。</p></div><div class="badge">历史因果回放 · 研究原型</div></div>
<section class="panel"><div class="toolbar"><div class="group"><span class="group-label">预报截点</span><div class="group" id="horizons"></div></div><div class="group"><span class="group-label">档位</span><div class="group" id="tiers"></div></div></div>
<div class="stats"><div class="stat"><span>最新可见档线</span><strong id="current"></strong><small id="issue"></small></div><div class="stat"><span>当前主模型终值</span><strong id="control"></strong><small>控制线 · 不强迫等于成员中位数</small></div><div class="stat"><span>成员加权中位数</span><strong id="median"></strong><small>研究输出 · 未替代主模型</small></div><div class="stat"><span>有效成员数</span><strong id="ess"></strong><small id="pruned"></small></div><div class="stat"><span>已完成新制度样本</span><strong id="history-count"></strong><small>50 条成员由这些活动轨迹构造</small></div></div>
<svg id="chart" viewBox="0 0 1200 530" role="img" aria-label="成员档线轨迹图"></svg>
<div class="legend"><span><i class="swatch" style="background:#26323b"></i>真实已知档线</span><span><i class="swatch" style="background:#8063a4"></i>当前主模型</span><span><i class="swatch" style="background:#d18349"></i>成员加权中位数</span><span><i class="swatch" style="background:#70a89e"></i>前段增长成员</span><span><i class="swatch" style="background:#c6a66b"></i>后段加速成员</span></div>
<h2 class="terminal-title">终值预测 · 分档对照</h2><p class="terminal-sub">每档显示成员终值加权 10% 分位、主模型预测、成员终值加权 90% 分位。T1500* 是新增新制度专项档位，未进入旧 CN-Core 评分；成员分位尚未经覆盖率校准。</p><svg id="terminal" viewBox="0 0 1200 465" role="img" aria-label="各档终值预测分组柱状图"></svg>
<div class="note">每个截点只使用此前已完成的新制度活动与该截点可见的 tracker。蓝色／橙色尾部线来自历史轨迹偏离，不是手工添加的极端倍率。后续报点按匹配程度调整权重，低权重成员逐渐淡出；50 条线并非 50 个独立历史活动。10%／90% 是成员分位，尚未经过概率覆盖率校准。</div></section></main><div class="tooltip" id="tip"></div>
<script>const DATA=__DATA__;
let step=0,tier=500;const $=s=>document.querySelector(s),svgNS='http://www.w3.org/2000/svg';
const fmt=n=>Math.round(n).toLocaleString('zh-CN');const date=ms=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(ms));
function node(tag,attrs={},parent){let e=document.createElementNS(svgNS,tag);for(let [k,v] of Object.entries(attrs))e.setAttribute(k,v);parent.appendChild(e);return e}
function textNode(parent,x,y,s,attrs={}){let e=node('text',{x,y,fill:'#69767d','font-size':12,...attrs},parent);e.textContent=s;return e}
function curve(m,tier,at){let a=m.paths[tier];if(at<=a[0][0])return a[0][1];for(let i=1;i<a.length;i++){if(at<=a[i][0]){let f=(at-a[i-1][0])/(a[i][0]-a[i-1][0]);return a[i-1][1]+f*(a[i][1]-a[i-1][1])}}return a[a.length-1][1]}
function wq(pairs,q){pairs.sort((a,b)=>a[0]-b[0]);let goal=q*pairs.reduce((s,p)=>s+p[1],0),sum=0;for(let p of pairs){sum+=p[1];if(sum>=goal)return p[0]}return pairs[pairs.length-1][0]}
function path(points,X,Y){return points.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join(' ')}
function renderBars(s){let out=$('#terminal');out.replaceChildren();let tiers=Object.keys(s.current).map(Number),max=Math.max(...tiers.map(t=>s.member_p90[t]),...tiers.map(t=>s.control[t]))*1.1;let left=190,width=850;let rows=[['10%',s.member_p10,'#75a9a2'],['主预测',s.control,'#8063a4'],['90%',s.member_p90,'#d2a35d']];for(let [i,t] of tiers.entries()){let base=34+i*106;textNode(out,25,base+32,'T'+t+(t===1500?'*':''),{'font-size':19,'font-weight':700,fill:'#2d3a40'});rows.forEach(([label,series,color],j)=>{let y=base+j*28,val=series[t],bar=val/max*width;textNode(out,left-12,y+15,label,{'text-anchor':'end','font-size':13});node('rect',{x:left,y,width:bar,height:18,rx:4,fill:color},out);textNode(out,left+bar+9,y+15,fmt(val),{'font-size':13,'font-weight':700,fill:'#2d3a40'})});if(i<tiers.length-1)node('line',{x1:20,x2:1160,y1:base+88,y2:base+88,stroke:'#edf0ed'},out)}}
function render(){let s=DATA.snapshots[step],hist=s.visible_history[tier],end=s.end_at,issue=s.issued_at,cur=s.current[tier],ctrl=s.control[tier],med=s.member_median[tier],p10=s.member_p10[tier],p90=s.member_p90[tier],members=s.members;
$('#title').textContent=DATA.event_id+' 活动 · 50 成员逐报点预测线';$('#current').textContent=fmt(cur)+' PT';$('#issue').textContent=date(issue)+' 发布';$('#control').textContent=fmt(ctrl)+' PT';$('#median').textContent=fmt(med)+' PT';let d=s.diagnostics;$('#ess').textContent=d.prior_ess==null?'50 / 50':d.prior_ess.toFixed(1)+' / 50';$('#pruned').textContent=d.assimilated?'低权重成员 '+d.soft_pruned+' 条':'尚无下一报点';$('#history-count').textContent=d.template_count+' 场';
document.querySelectorAll('#horizons button').forEach((b,i)=>b.classList.toggle('active',i===step));document.querySelectorAll('#tiers button').forEach(b=>b.classList.toggle('active',+b.dataset.tier===tier));
let svg=$('#chart');svg.replaceChildren();let L=70,R=1130,T=24,B=465;let x0=hist[0].time,ymax=Math.max(ctrl,med,...members.map(m=>m.terminals[tier]),...hist.map(p=>p.ep))*1.1;let X=x=>L+(x-x0)/(end-x0)*(R-L),Y=y=>B-y/ymax*(B-T);
for(let k=0;k<=5;k++){let val=ymax*k/5,y=Y(val);node('line',{x1:L,x2:R,y1:y,y2:y,stroke:'#e6eae7','stroke-width':1},svg);textNode(svg,L-9,y+4,fmt(val/10000)+'万',{'text-anchor':'end'})}
for(let k=0;k<=5;k++){let at=x0+(end-x0)*k/5,x=X(at);node('line',{x1:x,x2:x,y1:B,y2:B+5,stroke:'#aeb9b6'},svg);textNode(svg,x,B+22,date(at),{'text-anchor':'middle'})}
node('line',{x1:X(issue),x2:X(issue),y1:T,y2:B,stroke:'#abb9b3','stroke-dasharray':'3 5'},svg);
let ordered=[...members].sort((a,b)=>a.weight-b.weight);for(let m of ordered){let endpoint=m.terminals[tier],tail=endpoint<p10||endpoint>p90,col=endpoint<p10?'#557bba':endpoint>p90?'#d27b63':'#8aa5a0';let p=node('path',{d:path(m.paths[tier],X,Y),fill:'none',stroke:col,'stroke-width':tail?1.9:1.15,'stroke-opacity':Math.min(tail?.8:.55,.15+m.weight*12)},svg);p.addEventListener('mousemove',ev=>{let tip=$('#tip');tip.style.display='block';tip.style.left=(ev.clientX+13)+'px';tip.style.top=(ev.clientY+10)+'px';tip.innerHTML=m.member_id+'<br>终值 '+fmt(endpoint)+' PT<br>权重 '+(m.weight*100).toFixed(1)+'%<br>来源活动 '+m.source_event_ids.join(' / ')});p.addEventListener('mouseleave',()=>$('#tip').style.display='none')}
node('path',{d:path(hist.map(p=>[p.time,p.ep]),X,Y),fill:'none',stroke:'#26323b','stroke-width':3.1,'stroke-linejoin':'round'},svg);
node('path',{d:path(s.control_paths[tier],X,Y),fill:'none',stroke:'#8063a4','stroke-width':2.5,'stroke-dasharray':'9 5'},svg);let medpath=[];for(let k=0;k<=32;k++){let at=issue+(end-issue)*k/32;medpath.push([at,wq(members.map(m=>[curve(m,tier,at),m.weight]),.5)])}node('path',{d:path(medpath,X,Y),fill:'none',stroke:'#d18349','stroke-width':2.5,'stroke-dasharray':'3 5'},svg);node('circle',{cx:X(issue),cy:Y(cur),r:5,fill:'#26323b'},svg);node('circle',{cx:X(end),cy:Y(ctrl),r:5,fill:'#8063a4'},svg);for(let [val,label,color] of [[p10,'10%','#3e8880'],[p90,'90%','#a8793e']]){node('circle',{cx:X(end),cy:Y(val),r:4,fill:color},svg);textNode(svg,R-10,Y(val)-8,label,{'text-anchor':'end','font-size':13,'font-weight':700,fill:color})}textNode(svg,X(issue)+8,Y(cur)-10,'当前 '+fmt(cur),{'font-size':13,fill:'#26323b','font-weight':700});textNode(svg,R-3,T-3,'结束 '+date(end),{'text-anchor':'end','font-size':12});renderBars(s)}
DATA.snapshots.forEach((s,i)=>{let b=document.createElement('button');b.textContent=s.horizon_hours+'h';b.onclick=()=>{step=i;render()};$('#horizons').appendChild(b)});
Object.keys(DATA.snapshots[0].current).forEach(t=>{let b=document.createElement('button');b.textContent='T'+t+(+t===1500?'*':'');b.dataset.tier=t;b.onclick=()=>{tier=+t;render()};$('#tiers').appendChild(b)});render();
</script></body></html>'''


def build(source: Path, target: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    literal = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    target.write_text(HTML.replace("__DATA__", literal), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a self-contained member forecast viewer")
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    build(args.replay, args.out or args.replay.with_suffix(".html"))
