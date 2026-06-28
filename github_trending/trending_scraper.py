"""
GitHub Trending 爬虫
使用 requests 直接抓取，遵循 news_fetch_sop 原则
"""
import re
import requests
from typing import Optional
from project_enricher import enrich_projects


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def fetch_trending(language: str = '', since: str = 'daily') -> list[dict]:
    """
    抓取 GitHub Trending 项目列表
    
    Args:
        language: 语言筛选，如 'python', '' 表示所有
        since: 时间范围 'daily' | 'weekly' | 'monthly'
    
    Returns:
        list[dict]: 项目列表 [{name, url, description, language, stars, forks, stars_today}]
    """
    url = 'https://github.com/trending'
    if language:
        url += f'/{language}'
    url += f'?since={since}'
    
    print(f"🌐 Fetching: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text
    
    projects = _parse_trending_html(html)
    print(f"✅ 解析到 {len(projects)} 个项目")

    # 抓取每个项目的仓库详情（License/标签/发行版等）
    print("🔍 正在抓取仓库详情...")
    projects = enrich_projects(projects)
    print(f"✅ 详情抓取完成，{len(projects)} 个仓库已丰富")

    return projects


def _parse_trending_html(html: str) -> list[dict]:
    """解析 Trending 页面 HTML"""
    projects = []
    
    # 定位每个项目文章块
    # GitHub trending 用 <article class="Box-row"> 包裹每个项目
    articles = re.findall(
        r'<article\s+class="Box-row"[^>]*>(.*?)</article>',
        html,
        re.DOTALL
    )
    
    if not articles:
        # 备用：尝试用 h2 定位
        articles = re.findall(
            r'<h2[^>]*class="h3 lh-condensed"[^>]*>(.*?)</h2>',
            html,
            re.DOTALL
        )
        # 如果仍为空，尝试从整个页面提取
        if not articles:
            return _parse_fallback(html)
    
    for article_html in articles:
        project = _extract_single_project(article_html)
        if project and project.get('name'):
            projects.append(project)
    
    return projects


def _extract_single_project(html_fragment: str) -> Optional[dict]:
    """从单个项目 HTML 片段提取信息"""
    try:
        # 项目名: href="/owner/repo"
        name_match = re.search(
            r'href="/([^/"]+/[^/"]+)"(?:\s+[^>]*)?>\s*<span[^>]*data-view-component="true"[^>]*class="[^"]*text-normal[^"]*"[^>]*>(.*?)</span>',
            html_fragment, re.DOTALL
        )
        if not name_match:
            # 备用模式：只匹配 owner/repo 格式，排除 login?/sponsor? 等非仓库链接
            name_match = re.search(
                r'href="/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)"[^>]*>', html_fragment
            )
            if not name_match:
                return None
            full_name = name_match.group(1).strip()
        else:
            owner = name_match.group(1)
            repo_part = name_match.group(2).strip() if name_match.group(2) else ''
            full_name = f"{owner}/{repo_part}" if repo_part else owner
        
        # 描述
        desc_match = re.search(
            r'<p[^>]*class="col-9[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>',
            html_fragment, re.DOTALL
        )
        description = ''
        if desc_match:
            description = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()
        
        # 语言
        lang_match = re.search(
            r'<span[^>]*itemprop="programmingLanguage"[^>]*>(.*?)</span>',
            html_fragment, re.DOTALL
        )
        language = lang_match.group(1).strip() if lang_match else ''
        
        # 星数
        stars = 0
        stars_match = re.findall(
            r'<a[^>]*href="/([^/"]+/[^/"]+)/stargazers"[^>]*>\s*<svg[^>]*octicon-star[^>]*>.*?</svg>\s*([\d,]+)\s*</a>',
            html_fragment, re.DOTALL
        )
        if stars_match:
            stars = int(stars_match[0][1].replace(',', ''))
        
        # Forks
        forks = 0
        forks_match = re.findall(
            r'<a[^>]*href="/([^/"]+/[^/"]+)/forks"[^>]*>\s*<svg[^>]*octicon-repo-forked[^>]*>.*?</svg>\s*([\d,]+)\s*</a>',
            html_fragment, re.DOTALL
        )
        if forks_match:
            forks = int(forks_match[0][1].replace(',', ''))
        
        # 今日星增
        stars_today = 0
        today_match = re.search(
            r'<span[^>]*class="d-inline-block float-sm-right"[^>]*>\s*<svg[^>]*octicon-star[^>]*>.*?</svg>\s*([\d,]+)\s*(stars?|stars?\s+today)',
            html_fragment, re.DOTALL
        )
        if today_match:
            stars_today = int(today_match.group(1).replace(',', ''))
        
        # URL 清理
        name_clean = full_name.split('/')[-1] if '/' in full_name else full_name
        url = f"https://github.com/{full_name}" if 'github.com' not in full_name else full_name
        if not full_name.startswith('http'):
            url = f"https://github.com/{full_name}"
        
        return {
            'name': full_name.strip('/'),
            'url': url,
            'description': description,
            'language': language,
            'stars': stars,
            'forks': forks,
            'stars_today': stars_today,
        }
    except Exception as e:
        print(f"⚠️  解析项目失败: {e}")
        return None


def _parse_fallback(html: str) -> list[dict]:
    """兜底解析：从整个HTML提取趋势项目"""
    projects = []
    
    # 尝试从 script 标签中的 JSON 数据提取
    json_matches = re.findall(
        r'"repoName":\s*"([^"]+)"',
        html
    )
    
    for name in json_matches:
        if not any(p['name'] == name for p in projects):
            projects.append({
                'name': name,
                'url': f'https://github.com/{name}',
                'description': '',
                'language': '',
                'stars': 0,
                'forks': 0,
                'stars_today': 0,
            })
    
    return projects


if __name__ == '__main__':
    import json
    data = fetch_trending()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\n🎯 共抓取 {len(data)} 个项目")
