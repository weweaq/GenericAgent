"""
GitHub Trending 追踪系统 - Flask 后端
API: /api/trending | /api/rate | /api/history | /api/report
"""
import os, sys, json, logging

# 解决Windows控制台emoji编码问题
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass
os.environ['PYTHONIOENCODING'] = 'utf-8'

from datetime import date
from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(__file__))
from models import init_db, save_projects, get_today_projects, get_project_by_id
from models import add_rating, get_ratings, get_all_projects, get_all_reports
from models import save_report, clear_today_data
from trending_scraper import fetch_trending
from analyzer import analyze_project, generate_daily_summary

app = Flask(__name__, static_folder='static', template_folder='templates')
logging.basicConfig(level=logging.INFO)

# ========== API ==========

@app.route('/api/trending')
def api_trending():
    """获取今日Trending项目（带AI分析）"""
    today = str(date.today())
    projects = get_today_projects(today)
    
    if not projects:
        # 首次运行：抓取+分析
        try:
            raw = fetch_trending()
        except Exception as e:
            return jsonify({'error': f'抓取失败: {e}', 'projects': []})
        
        analyzed = []
        for p in raw:
            analysis = analyze_project(p)
            p['ai_summary'] = analysis['summary']
            p['ai_rating'] = analysis['rating']
            p['tags'] = analysis['tags']
            p['category'] = analysis.get('category', '')
            p['heat_level'] = analysis.get('heat_level', '')
            p['growth_level'] = analysis.get('growth_level', '')
            p['date'] = today
            analyzed.append(p)
        
        # 存库
        save_projects(analyzed, today)
        
        # 生成日报+预测
        summary, prediction = generate_daily_summary(raw)
        save_report(today, summary, prediction)
        
        projects = analyzed
    else:
        # 已有数据：从tags字段回填展示字段
        for p in projects:
            ratings = get_ratings(p['id'])
            p['user_ratings'] = [{'rating': r['rating'], 'comment': r['comment']} for r in ratings]
            # 从tags解析category/heat/growth（DB没存独立列，存在tags字符串里）
            tags_str = p.get('tags', '') or ''
            parts = [t.strip() for t in tags_str.split(',')]
            p['category'] = parts[0] if len(parts) > 0 else ''
            p['language'] = p.get('language', '')
            # 从tags取后两个字段作为heat/growth
            if len(parts) >= 3:
                p['heat_level'] = parts[-2].strip()
                p['growth_level'] = parts[-1].strip()
            else:
                p['heat_level'] = parts[1].strip() if len(parts) >= 2 else ''
                p['growth_level'] = ''
    
    return jsonify({'date': today, 'projects': projects})


@app.route('/api/rate', methods=['POST'])
def api_rate():
    """提交评分和评论"""
    data = request.json
    project_id = data.get('project_id')
    rating = data.get('rating')
    comment = data.get('comment', '')
    
    if not project_id or not rating:
        return jsonify({'error': '缺少 project_id 或 rating'}), 400
    
    try:
        rating = int(rating)
        if rating < 1 or rating > 10:
            return jsonify({'error': '评分必须在1-10之间'}), 400
    except ValueError:
        return jsonify({'error': '评分必须是数字'}), 400
    
    rid = add_rating(project_id, rating, comment)
    return jsonify({'success': True, 'rating_id': rid})


@app.route('/api/history')
def api_history():
    """获取历史数据"""
    all_projects = get_all_projects()
    reports = get_all_reports()
    
    # 按日期分组
    by_date = {}
    for p in all_projects:
        d = p.get('trend_date', p.get('date', ''))
        if d not in by_date:
            by_date[d] = {'date': d, 'projects': [], 'report': None}
        # 加载该项目的评分
        ratings = get_ratings(p['id'])
        p['user_ratings'] = [{'rating': r['rating'], 'comment': r['comment']} for r in ratings]
        by_date[d]['projects'].append(p)
    
    for r in reports:
        d = r['report_date']
        if d in by_date:
            by_date[d]['report'] = {'summary': r['summary'], 'prediction': r['prediction']}
    
    return jsonify({'history': sorted(by_date.values(), key=lambda x: x['date'], reverse=True)})


@app.route('/api/report')
def api_report():
    """获取今日日报+预测"""
    today = str(date.today())
    reports = get_all_reports()
    
    for r in reports:
        if r['report_date'] == today:
            return jsonify({
                'date': today,
                'summary': r['summary'],
                'prediction': r['prediction']
            })
    
    return jsonify({'date': today, 'summary': '今日尚未抓取数据', 'prediction': '请先访问首页触发抓取'})


@app.route('/api/refresh', methods=['GET', 'POST'])
def api_refresh():
    """强制刷新今日数据（GET浏览器直接访问也可触发）"""
    today = str(date.today())
    clear_today_data(today)
    
    try:
        raw = fetch_trending()
    except Exception as e:
        return jsonify({'error': f'抓取失败: {e}'}), 500
    
    analyzed = []
    for p in raw:
        analysis = analyze_project(p)
        p['ai_summary'] = analysis['summary']
        p['ai_rating'] = analysis['rating']
        p['tags'] = analysis['tags']
        p['category'] = analysis.get('category', '')
        p['heat_level'] = analysis.get('heat_level', '')
        p['growth_level'] = analysis.get('growth_level', '')
        p['date'] = today
        analyzed.append(p)
    
    save_projects(analyzed, today)
    summary, prediction = generate_daily_summary(raw)
    save_report(today, summary, prediction)
    
    return jsonify({'success': True, 'count': len(analyzed), 'date': today})


# ========== 前端页面 ==========

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')


@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ========== 启动 ==========

if __name__ == '__main__':
    init_db()
    print(f"🚀 GitHub Trending Tracker starting...")
    print(f"   🌐 http://127.0.0.1:5721")
    app.run(host='127.0.0.1', port=5721, debug=True)
