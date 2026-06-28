"""
AI分析引擎
对Trending项目进行智能分析 + 每日趋势总结
"""
import json
import os
import re
import requests
from datetime import date
from typing import Optional


def _fetch_readme(name: str) -> Optional[str]:
    """从GitHub抓取README内容"""
    parts = name.split('/')
    if len(parts) < 2:
        return None
    
    owner, repo = parts[0], parts[1]
    
    # sponsors/ 特殊处理——映射到实际仓库
    sponsor_map = {
        'sveltejs': ('sveltejs', 'svelte'),
        'vitejs': ('vitejs', 'vite'),
        'santifer': ('Santifer', 'Santifer'),
        'danielmiessler': ('danielmiessler', 'fabric'),
        'obra': ('obra', 'superpowers'),
    }
    
    if owner == 'sponsors' and repo in sponsor_map:
        owner, repo = sponsor_map[repo]
    
    for branch in ['main', 'master']:
        try:
            url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md'
            r = requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                text = r.text[:3000]
                return text
        except:
            pass
    return None


def _extract_tagline(readme: str) -> str:
    """从README提取项目标语"""
    if not readme:
        return ''
    
    # 去掉图片、HTML标签
    clean = re.sub(r'!\[.*?\]\(.*?\)', '', readme)
    clean = re.sub(r'<[^>]+>', '', clean)
    clean = re.sub(r'\[!\[.*?\]\(.*?\)\]\(.*?\)', '', clean)
    
    lines = [l.strip() for l in clean.split('\n') if l.strip()]
    
    for line in lines:
        # 跳过空行、链接、图片
        if line.startswith('#') or line.startswith('[') or line.startswith('!'):
            continue
        if line.startswith('http') or line.startswith('@') or line.startswith('*'):
            continue
        if len(line) > 20 and len(line) < 200:
            # 去掉尾部标点后返回
            line = line.strip('"\'`.,;:!?')
            if line and not line.startswith('<') and not line.startswith('-'):
                # 去掉markdown加粗标记
                line = line.replace('**', '').replace('__', '')
                return line[:150]
    return ''


def _extract_features(readme: str) -> list:
    """从README提取核心特性"""
    if not readme:
        return []
    
    features = []
    clean = re.sub(r'!\[.*?\]\(.*?\)', '', readme)
    clean = re.sub(r'<[^>]+>', '', clean)
    
    # 找列表项（以 - 或 * 开头）
    lines = clean.split('\n')
    in_features = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # 检测特性/功能/特点章节
        if re.match(r'^#{1,3}\s*(?:核心|主要)?(?:特性|功能|特点|亮点|Feature|Key )', stripped, re.IGNORECASE):
            in_features = True
            continue
        
        if in_features:
            if stripped.startswith('#'):
                break
            if re.match(r'^[-*]\s+', stripped):
                feat = re.sub(r'^[-*]\s+', '', stripped)
                feat = re.sub(r'`([^`]+)`', r'\1', feat)
                feat = re.sub(r'\*\*([^*]+)\*\*', r'\1', feat)
                # 截断过长
                if len(feat) > 120:
                    feat = feat[:117] + '...'
                if feat and len(feat) > 10:
                    features.append(feat)
    
    # 如果没找到特性章节，从项目描述中提取
    if len(features) < 2:
        features = []
        for line in lines:
            stripped = line.strip()
            if re.match(r'^[-*]\s+', stripped) and not stripped.startswith('- [') and 'badge' not in stripped.lower():
                feat = re.sub(r'^[-*]\s+', '', stripped)
                feat = re.sub(r'`([^`]+)`', r'\1', feat)
                feat = re.sub(r'\*\*([^*]+)\*\*', r'\1', feat)
                feat = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', feat)
                if len(feat) > 120:
                    feat = feat[:117] + '...'
                if feat and len(feat) > 15 and 'img' not in feat:
                    features.append(feat)
    
    return features[:6]


def analyze_project(project: dict) -> dict:
    """
    分析单个项目——从README生成详细中文分析
    返回: {summary, rating, tags, category, heat_level, growth_level}
    """
    name = project.get('name', '')
    desc = project.get('description', '') or ''
    lang = project.get('language', '') or ''
    stars = project.get('stars', 0) or 0
    stars_today = project.get('stars_today', 0) or 0
    
    # 1. 获取README
    readme = _fetch_readme(name)
    
    # 2. 提取标语
    tagline = _extract_tagline(readme) or desc[:120]
    
    # 3. 提取特性
    features = _extract_features(readme)
    
    # 4. 热度评级
    if stars > 20000:
        heat_level = '🔥🔥🔥 现象级'
    elif stars > 10000:
        heat_level = '🔥🔥 热门'
    elif stars > 5000:
        heat_level = '🔥 受关注'
    elif stars > 1000:
        heat_level = '⭐ 有潜力'
    else:
        heat_level = '🌱 新兴'
    
    growth_rate = round(stars_today / max(stars, 1) * 100, 1) if stars > 0 else 0
    if growth_rate > 5:
        growth_level = '📈 爆发式增长'
    elif growth_rate > 2:
        growth_level = '📈 快速增长'
    elif growth_rate > 0.5:
        growth_level = '📊 稳定增长'
    else:
        growth_level = '📊 温和增长'
    
    # 5. 分类
    desc_lower = desc.lower()
    if any(w in desc_lower for w in ['ai', 'llm', 'gpt', 'machine learning', 'deep learning', 'neural', 'agent']):
        category = '🤖 AI/ML'
    elif any(w in desc_lower for w in ['web', 'react', 'vue', 'frontend', 'ui', 'css', 'front-end']):
        category = '🌐 Web前端'
    elif any(w in desc_lower for w in ['backend', 'server', 'api', 'database', 'cloud']):
        category = '⚙️ 后端/云'
    elif any(w in desc_lower for w in ['rust', 'compiler', 'performance', 'system']):
        category = '🚀 系统/性能'
    elif any(w in desc_lower for w in ['data', 'analytics', 'visualization']):
        category = '📊 数据/分析'
    elif any(w in desc_lower for w in ['game', 'engine', '3d', 'graphics']):
        category = '🎮 游戏/图形'
    elif any(w in desc_lower for w in ['devops', 'deploy', 'docker', 'ci']):
        category = '🔧 DevOps'
    elif any(w in desc_lower for w in ['tutorial', 'learn', 'course', 'guide', 'example']):
        category = '📖 教程/学习'
    elif any(w in desc_lower for w in ['ocr', 'voice', 'speech', 'tts', 'recognition']):
        category = '🔊 语音/视觉'
    else:
        category = '🛠️ 工具/框架'
    
    # 6. 构建详细摘要（类似MemPalace风格）
    project_name = name.split('/')[-1]
    
    summary_parts = []
    summary_parts.append(f"🧠 项目定位")
    summary_parts.append("")
    
    # 标语
    if tagline:
        summary_parts.append(f'"{tagline}"')
        summary_parts.append("")
    
    # 一句话概述
    if desc:
        overview = f"{project_name} 是一个{'开源' if readme else ''}{'项目' if not readme else ''}"
        if readme:
            overview += f"【{lang or '多语言'}】" if lang else ""
            # 更自然的概述
            if len(desc) > 30:
                overview = f"{project_name} 是一个专注于 {desc[:80]} 的开源项目"
            else:
                overview = f"{project_name} 是一个 {desc[:80]} 的开源项目"
        summary_parts.append(overview)
    else:
        summary_parts.append(f"{project_name} 是一个{lang}开源项目")
    summary_parts.append("")
    
    # 核心特性
    if features:
        summary_parts.append("▌ 核心特性：")
        feature_num = 0
        emojis = ['🤖', '🔌', '⚡', '🛡️', '🎯', '📦']
        for i, feat in enumerate(features[:4]):
            if len(feat) > 15:
                emoji = emojis[i % len(emojis)]
                summary_parts.append(f"▌ {emoji} {feat.strip()[:100]}")
                feature_num += 1
        if feature_num < 2 and desc:
            summary_parts.append(f"▌ 📌 项目说明 — {desc[:120]}")
    else:
        # 用描述凑特性
        if desc:
            summary_parts.append(f"▌ 📌 项目简介 — {desc[:150]}")
            summary_parts.append(f"▌ ⭐ 社区热度 — {stars:,} 星标，今日增长 {stars_today}")
            summary_parts.append(f"▌ 🔗 开源许可 — 开源")
        else:
            summary_parts.append(f"▌ ⭐ 社区热度 — {stars:,} 星标，今日增长 {stars_today}")
    
    # 语言标签
    lang_tags = {
        'Python': 'AI/ML·数据科学', 'TypeScript': '前端·全栈', 'JavaScript': '前端·全栈',
        'Rust': '系统·高性能', 'Go': '云原生·微服务', 'C': '底层·嵌入式',
        'C++': '底层·游戏', 'Java': '企业级', 'Kotlin': 'Android',
        'Swift': 'iOS·macOS', 'Ruby': 'Web开发', 'PHP': 'Web开发',
        'Jupyter Notebook': '数据科学·教程', 'Shell': 'DevOps·自动化',
        'HTML': '前端·文档', 'CSS': '前端·设计',
    }
    lang_tag = lang_tags.get(lang, lang) if lang else '其他'
    tag_str = f"{category}, {lang_tag}, {heat_level}, {growth_level}"
    
    # 评分
    score = min(10, max(1, round(
        3 + min(3, stars / 10000) + min(2, stars_today / 200) + (1 if desc else 0) + (1 if lang else 0) + (1 if readme else 0)
    )))
    
    return {
        'summary': '\n'.join(summary_parts),
        'rating': score,
        'tags': tag_str,
        'category': category,
        'heat_level': heat_level,
        'growth_level': growth_level,
    }


def _get_recommendation(project: dict) -> str:
    """生成推荐理由"""
    stars = project.get('stars', 0)
    stars_today = project.get('stars_today', 0)
    desc = project.get('description', '')
    lang = project.get('language', '')
    
    reasons = []
    if stars > 10000:
        reasons.append('社区验证过的优质项目')
    if stars_today > 200:
        reasons.append('今日热度极高')
    if desc and len(desc) > 50:
        reasons.append('文档描述详尽')
    if lang in ('Python', 'TypeScript', 'Rust'):
        reasons.append(f'使用热门语言{lang}')
    
    return '、'.join(reasons) if reasons else '值得关注的新项目'


def generate_daily_summary(projects: list[dict]) -> tuple[str, str]:
    """生成每日趋势总结和预测"""
    if not projects:
        return '今日无新项目上榜', '暂无数据'
    
    langs = {}
    categories = {}
    total_stars = 0
    for p in projects:
        total_stars += p.get('stars', 0)
        lang = p.get('language', '其他')
        langs[lang] = langs.get(lang, 0) + 1
        desc = p.get('description', '').lower()
        cat = '其他'
        if any(w in desc for w in ['ai', 'llm', 'gpt', 'machine learning']):
            cat = 'AI'
        elif any(w in desc for w in ['web', 'react', 'vue', 'frontend', 'ui']):
            cat = 'Web'
        elif any(w in desc for w in ['rust', 'compiler', 'system']):
            cat = '系统'
        elif any(w in desc for w in ['data', 'analytics', 'database']):
            cat = '数据'
        categories[cat] = categories.get(cat, 0) + 1
    
    top_lang = sorted(langs.items(), key=lambda x: -x[1])[:3]
    top_cat = sorted(categories.items(), key=lambda x: -x[1])[:3]
    hottest = max(projects, key=lambda p: p.get('stars_today', 0))
    
    today_str = date.today().isoformat()
    
    summary_lines = [
        f"📅 **{today_str} GitHub Trending 日报**",
        "",
        f"今日上榜 **{len(projects)}** 个项目，总计 ⭐{total_stars:,} 星",
        "",
        f"**语言分布**：{' | '.join(f'{l}({c}个)' for l, c in top_lang)}",
        f"**类别分布**：{' | '.join(f'{l}({c}个)' for l, c in top_cat)}",
        f"**今日最热**：{hottest.get('name', '')} (+{hottest.get('stars_today', 0)}⭐)",
        "",
        "**重点项目速览**：",
    ]
    
    for p in projects[:5]:
        line = f"- [{p.get('name', '')}]({p.get('url', '')}) — {p.get('description', '')[:60]}"
        summary_lines.append(line)
    
    # 预测
    pred_parts = []
    ai_count = categories.get('AI', 0)
    web_count = categories.get('Web', 0)
    
    if ai_count >= 3:
        pred_parts.append(f"AI赛道依然火热（{ai_count}个项目），LLM工具链和AI Agent方向持续领跑")
    if web_count >= 2:
        pred_parts.append(f"Web前端持续活跃（{web_count}个项目），关注新框架和UI工具")
    if langs.get('Rust', 0) >= 2:
        pred_parts.append("Rust生态在系统工具领域持续扩张")
    
    if not pred_parts:
        pred_parts.append("今日项目分布较分散，无明显单一热点赛道")
    
    pred_parts.append("建议关注增速最快的项目，可能即将爆发")
    
    return '\n'.join(summary_lines), '\n'.join(pred_parts)


if __name__ == '__main__':
    # 测试
    sample = json.load(open('trending_sample.json', encoding='utf-8'))
    for p in sample[:3]:
        print(f"\n--- {p['name']} ---")
        result = analyze_project(p)
        print(f"评分: {result['rating']}/10")
        print(f"标签: {result['tags']}")
        print(f"摘要: {result['summary'][:300]}")
    
    summary, pred = generate_daily_summary(sample)
    print(f"\n\n{'='*50}")
    print(summary)
    print(f"\n🔮 预测:\n{pred}")
