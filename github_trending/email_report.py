#!/usr/bin/env python
"""GitHub Trending 增强版邮件日报 - 发送HTML格式详细日报到QQ邮箱"""
import smtplib, sys, requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from collections import defaultdict

sys.path.insert(0, r'D:\AAAmyPrj\github\myrepos\GenericAgent\memory')
from keychain import keys

API = 'http://127.0.0.1:5721/api'
TO = '1773465183@qq.com'

def fmt_stars(n):
    if n >= 100000: return f"{n//10000}万+ ⭐"
    if n >= 10000: return f"{n/10000:.1f}万 ⭐"
    if n >= 1000: return f"{n//1000}.{n%1000//100}k ⭐"
    return f"{n} ⭐"

def send_report():
    today = str(date.today())
    
    r = requests.get(f'{API}/trending', timeout=6)
    projs = r.json()['projects']

    cats = defaultdict(list)
    for p in projs:
        cats[p.get('category', '🎨 其他')].append(p)

    top_today = max(projs, key=lambda p: p.get('stars_today', 0) or 0)

    # TOP10 行
    rows = []
    for i, p in enumerate(projs[:10], 1):
        summary = (p.get('ai_summary','') or '')[:80].replace('\n',' ').strip()
        lang = (p.get('language','') or '')
        try:
            stars_s = '⭐' * max(1, min(5, round(float(p.get('ai_rating',3) or 3)/2)))
        except:
            stars_s = '⭐⭐⭐'
        ht = p.get('heat_level','')
        st = p.get('stars_today',0) or 0
        rows.append(f'''<tr>
        <td style="width:30px;text-align:center;color:#58a6ff;font-weight:bold">{i}</td>
        <td><b style="color:#f0e6d2">{p['name']}</b>{f'<br><span style="background:#21262d;color:#8b949e;border-radius:3px;padding:0 6px;font-size:11px">{lang}</span>' if lang else ''}
        <div style="color:#8b949e;font-size:12px;margin-top:2px">{summary}</div></td>
        <td style="text-align:center">{stars_s}</td>
        <td style="text-align:center;color:{"#f78166" if "现象" in ht else "#d29922" if "热门" in ht else "#58a6ff"}">{ht}</td>
        <td style="text-align:center;color:#3fb950">+{st}</td>
        </tr>''')

    # 全列表
    all_rows = []
    for i, p in enumerate(projs, 1):
        grow = p.get('growth_level','')
        gcol = '#3fb950' if '稳定' in grow else '#d29922' if '温和' in grow else '#8b949e'
        stars = fmt_stars(p.get('stars',0) or 0)
        all_rows.append(f'''<tr style="font-size:13px">
        <td style="color:#8b949e">{i}</td>
        <td><a href="https://github.com/{p['name']}" style="color:#c9d1d9">{p['name']}</a></td>
        <td>{"⭐"*max(1,min(5,round(float(p.get("ai_rating",3) or 3)/2)))}</td>
        <td style="color:{"#f78166" if "现象" in p.get('heat_level','') else "#d29922"}">{p.get('heat_level','')}</td>
        <td style="color:{gcol}">{grow}</td>
        <td style="color:#8b949e">{stars}</td>
        </tr>''')

    # 分类浏览
    cat_blocks = ''
    for cn in ['🤖 AI/ML', '🛠️ 工具/框架', '💻 系统', '🎨 其他']:
        cp = cats.get(cn, [])
        if cp:
            cat_blocks += f'''<div style="margin:8px 0"><b style="color:#f0e6d2">{cn}</b> ({len(cp)}个)<br>
            <span style="color:#8b949e;font-size:13px">{' · '.join(f'<a href="https://github.com/{p["name"]}" style="color:#58a6ff">{p["name"]}</a> ({fmt_stars(p.get("stars",0))})' for p in cp[:6])}</span></div>'''

    ai_count = len(cats.get('🤖 AI/ML', []))
    top_name = top_today['name']
    top_stars = top_today.get('stars_today', 0)

    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{font-family:"Microsoft YaHei",Arial,sans-serif;max-width:720px;margin:0 auto;padding:20px;background:#0d1117;color:#c9d1d9;line-height:1.6}}
h1{{color:#f78166;font-size:26px;border-left:4px solid #f78166;padding-left:12px}}
h2{{color:#f0e6d2;font-size:18px;border-bottom:1px solid #30363d;padding-bottom:6px;margin-top:26px}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}}
.stat-box{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 16px;min-width:100px;flex:1}}
.stat-label{{color:#8b949e;font-size:11px;text-transform:uppercase}}
.stat-val{{font-size:22px;font-weight:bold}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}}
th{{background:#21262d;color:#8b949e;font-size:11px;padding:8px 10px;text-align:left;border-bottom:2px solid #30363d;text-transform:uppercase}}
td{{padding:8px 10px;border-bottom:1px solid #21262d;font-size:13px}}
tr:hover td{{background:#161b22}}
.insight{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:12px 0;color:#e6e6e6;font-size:13px;line-height:1.5}}
.insight b{{color:#f78166}}
.footer{{margin-top:28px;color:#8b949e;font-size:11px;border-top:1px solid #30363d;padding-top:12px;text-align:center}}
</style></head><body>
<h1>🚀 GitHub Trending 日报</h1>
<div style="color:#8b949e;font-size:14px;margin-bottom:4px">{today} · 追踪 <b>{len(projs)}</b> 个项目</div>

<div class="stats">
<div class="stat-box"><div class="stat-label">📊 总数</div><div class="stat-val">{len(projs)}</div></div>
<div class="stat-box"><div class="stat-label">🤖 AI/ML</div><div class="stat-val">{ai_count}</div></div>
<div class="stat-box"><div class="stat-label">🛠️ 工具/框架</div><div class="stat-val">{len(cats.get('🛠️ 工具/框架',[]))}</div></div>
<div class="stat-box"><div class="stat-label">🔥 现象级</div><div class="stat-val" style="color:#f78166">{sum(1 for p in projs if '现象' in str(p.get('heat_level','')))}</div></div>
</div>

<h2>🏆 TOP 10 排行</h2>
<table><tr><th>#</th><th>项目 · 简介</th><th>评分</th><th>热度</th><th>今日+</th></tr>
{"".join(rows)}
</table>

<div class="insight"><b>🔥 今日之星</b> → <a href="https://github.com/{top_name}" style="color:#58a6ff;font-weight:bold">{top_name}</a>
今日新增 <b style="color:#3fb950;font-size:16px">+{top_stars}</b> 星标，领跑全场！</div>

<div class="insight"><b>📊 AI 趋势解读</b><br>
AI/ML项目占比 {ai_count*100//max(len(projs),1)}%，AI Agent/RAG相关项目持续热度不减。其他工具类项目表现稳健。</div>

<h2>📂 分类浏览</h2>
{cat_blocks}

<h2>📜 全部 {len(projs)} 个项目</h2>
<table><tr><th>#</th><th>项目</th><th>评分</th><th>热度</th><th>增长</th><th>总星</th></tr>
{"".join(all_rows)}
</table>

<div class="footer">
<p>🤖 由 <b style="color:#f78166">大大怪将军</b> 自动生成 · 每日08:00自动推送</p>
<p>🌐 <a href="http://127.0.0.1:5721">打开网页版</a> · 📧 {TO}</p>
<p style="color:#484f58;font-size:10px">GitHub Trending Tracker · {today}</p>
</div>
</body></html>'''

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'🚀 GitHub Trending 日报 {today} · 共{len(projs)}个项目 · 详细分析版'
    msg['From'] = TO
    msg['To'] = TO
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    server = smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=15)
    server.login(TO, keys.qq_smtp_auth.use())
    server.sendmail(TO, TO, msg.as_string())
    server.quit()
    print(f'[Email] 发送成功 -> {TO} ({len(projs)}个, {len(html)}B)')
    return True

if __name__ == '__main__':
    sys.exit(0 if send_report() else 1)
