"""Standalone, explicitly point-only T report; never borrows member intervals."""
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path


def build(snapshot, target: Path):
    detail = snapshot['topology_diagnostics']
    usage = {'exact': '已评估时距', 'nearest_horizon_approximation': '最近时距近似',
             'outside_evaluated_range': '不足 6 小时，超出已评估范围'}[detail['horizon_usage']]
    issue = datetime.fromtimestamp(snapshot['issued_at']/1000, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    pieces = []
    for tier in (500, 1000, 1500, 2000):
        history = snapshot['visible_history'][str(tier)]
        current, final = snapshot['current'][tier], snapshot['control'][tier]
        start, end = history[0]['time'], snapshot['end_at']
        top = max(current, final, 1) * 1.12
        x = lambda t: 40 + 1000 * (t-start)/max(end-start, 1)
        y = lambda p: 220 - 185*p/top
        points = ' '.join(f'{x(p["time"]):.3f},{y(p["ep"]):.3f}' for p in history)
        pieces.append(f'''<section><h2>T{tier}</h2><div class="values">当前 {current:,.0f} PT <strong>终值 {final:,.0f} PT</strong></div>
        <svg viewBox="0 0 1100 250" role="img" aria-label="T{tier} 已知档线和终值点预测">
        <polyline points="{points}" fill="none" stroke="#283d48" stroke-width="3"/>
        <line x1="{x(snapshot['issued_at']):.3f}" y1="{y(current):.3f}" x2="{x(end):.3f}" y2="{y(final):.3f}" stroke="#80669f" stroke-width="3" stroke-dasharray="8 6"/>
        <circle cx="{x(end):.3f}" cy="{y(final):.3f}" r="6" fill="#80669f"/></svg></section>''')
    target.write_text(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Nanami 点预测</title><style>body{{font:16px system-ui,sans-serif;color:#27323b;background:#fbfaf7;max-width:1150px;margin:24px auto;padding:0 20px}}section{{background:white;border:1px solid #ddd;border-radius:14px;padding:18px;margin:16px 0}}h1{{margin-bottom:8px}}h2{{margin:0}}strong{{float:right;color:#80669f}}svg{{width:100%}}p{{line-height:1.7}}.values{{margin-top:14px}}</style>
    <h1>Nanami · 四档联合点预测</h1><p>#{int(snapshot['event_id'])} · {escape(issue)} 起报（UTC+8）<br>
    {usage}，参考 {int(detail['nearest_evaluated_horizon_hours'])} 小时模型；{int(detail['sample_count'])} 场历史校准<br>
    不提供概率区间。黑线为已知档线；紫色虚线仅连接当前值与终值，不代表预测增长轨迹。</p>
    {''.join(pieces)}</html>''', encoding='utf-8')
