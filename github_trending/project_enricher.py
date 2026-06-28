"""
GitHub 项目详情孵化器 — 抓取仓库页面的结构化数据
解析: License · 标签 · 发行版 · 贡献者 · Watching · Issues/PRs · 语言分布 · 主页 · 分支 · Commits
"""
import requests
import re
import json
import time
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 缓存已抓取详情，避免重复请求
_detail_cache = {}

def fetch_project_details(repo_url: str) -> dict:
    """抓取GitHub仓库详情页，返回结构化数据"""
    if repo_url in _detail_cache:
        return _detail_cache[repo_url]

    result = {
        'license': '',
        'topics': [],
        'latest_release': '',
        'latest_release_date': '',
        'contributors': 0,
        'watching': 0,
        'open_issues': 0,
        'open_prs': 0,
        'language_pct': {},   # {"Python": "94.5%", "HTML": "2.0%"}
        'homepage': '',
        'default_branch': '',
        'branches': 0,
        'tags': 0,
        'commits': 0,
    }

    try:
        resp = requests.get(repo_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️ {repo_url} → HTTP {resp.status_code}")
            _detail_cache[repo_url] = result
            return result

        soup = BeautifulSoup(resp.text, 'lxml')

        # --- 1. License ---
        license_el = soup.select_one('a[href*="/license"] span, a[data-testid="license"]')
        if not license_el:
            license_el = soup.select_one('a[href*="/blob/"][href*="LICENSE"] span')
        if not license_el:
            # 尝试从sidebar找License链接
            license_el = soup.select_one('[data-testid="sidebar-section"] a[href*="license"]')
        if license_el:
            result['license'] = license_el.get_text(strip=True)
        # Also try from repo description area
        if not result['license']:
            m = re.search(r'License[^<]*?([A-Z][\w\s\-\.]+?)(?:</|\s{2,})', resp.text, re.I)
            if m:
                result['license'] = m.group(1).strip()

        # --- 2. Topics / Tags ---
        topic_els = soup.select('[data-testid="topic-tag"] a, .topic-tag, a[href*="/topics/"]')
        if not topic_els:
            topic_els = soup.select('a[href^="/topics/"]')
        for el in topic_els:
            t = el.get_text(strip=True)
            if t and len(t) < 30:
                result['topics'].append(t)
        result['topics'] = result['topics'][:10]

        # --- 3. Latest Release ---
        release_el = soup.select_one('.lh-condensed a[href*="/releases/tag/"]')
        if not release_el:
            release_el = soup.select_one('a[href*="/releases"] .css-truncate')
        if release_el:
            result['latest_release'] = release_el.get_text(strip=True)
        # Release date
        release_date_el = soup.select_one('relative-time[datetime]')
        if release_date_el and release_date_el.has_attr('datetime'):
            result['latest_release_date'] = release_date_el['datetime'][:10]
        # Fallback: look for release date in sidebar
        if not result['latest_release_date']:
            date_m = re.search(r'releases[^<]*?(\d{4}-\d{2}-\d{2})', resp.text)
            if date_m:
                result['latest_release_date'] = date_m.group(1)

        # --- 4. Contributors ---
        contrib_el = soup.select_one('a[href*="/graphs/contributors"] span, '
                                      '[data-testid="contributors"] span')
        if not contrib_el:
            contrib_el = soup.select_one('span.Link--secondary a[href*="contributors"]')
        if contrib_el:
            txt = contrib_el.get_text(strip=True).replace(',', '')
            nums = re.findall(r'(\d[\d,]*\d|\d)', txt)
            if nums:
                result['contributors'] = int(nums[0].replace(',', ''))
        # Try from alt text
        if not result['contributors']:
            m = re.search(r'(\d[\d,]*)\s*contributors', resp.text, re.I)
            if m:
                result['contributors'] = int(m.group(1).replace(',', ''))

        # --- 5. Watching ---
        watch_el = soup.select_one('[data-testid="watchers-count"], '
                                    'a[href*="/watchers"] strong, '
                                    '#repo-notifications strong')
        if not watch_el:
            watch_el = soup.select_one('[aria-label*="watchers"]')
        if watch_el:
            txt = watch_el.get_text(strip=True).replace(',', '')
            nums = re.findall(r'(\d+)', txt)
            if nums:
                result['watching'] = int(nums[0])

        # --- 6. Issues / PRs ---
        # Issues
        for a in soup.select('a[href*="/issues"]'):
            txt = a.get_text(strip=True)
            m = re.search(r'(\d[\d,]*\d|\d+)\s*(?:Open|open|Issues|issues)', txt)
            if m:
                result['open_issues'] = int(m.group(1).replace(',', ''))
                break
        # PRs
        for a in soup.select('a[href*="/pulls"]'):
            txt = a.get_text(strip=True)
            m = re.search(r'(\d[\d,]*\d|\d+)\s*(?:Open|open|Pull)', txt)
            if m:
                result['open_prs'] = int(m.group(1).replace(',', ''))
                break

        # --- 7. Language breakdown ---
        lang_items = soup.select('[data-testid="language-item"], .language-color, '
                                  '[data-testid="languages"] li, .Progress-item')
        if not lang_items or len(lang_items) == 0:
            # Look for the language bar
            lang_bar = soup.select_one('[data-testid="languages"]')
            if lang_bar:
                lang_items = lang_bar.select('li, span[aria-label]')
        if not lang_items or len(lang_items) == 0:
            # Try regex fallback for language percentages
            lang_matches = re.findall(
                r'aria-label="([^"]+?):\s*(\d+\.?\d*)%\s*"',
                resp.text
            )
            for name, pct in lang_matches[:8]:
                result['language_pct'][name] = f"{pct}%"

        for el in lang_items:
            text = el.get('aria-label', '') or el.get_text(strip=True)
            m = re.search(r'([\w#\+\-\.]+)\s*:\s*(\d+\.?\d*)\s*%', text)
            if m:
                result['language_pct'][m.group(1)] = f"{m.group(2)}%"

        # --- 8. Homepage ---
        hp_el = soup.select_one('[data-testid="sidebar-section"] a[href*="://"], '
                                 '.lh-default a[href*="://"]')
        if not hp_el:
            hp_el = soup.select_one('a[rel="nofollow"]')
        if hp_el:
            href = hp_el.get('href', '')
            if href.startswith('http') and 'github.com' not in href:
                result['homepage'] = href

        # --- 9. Default Branch + branches count ---
        branch_el = soup.select_one('[data-testid="branch-select-menu"] summary span, '
                                     '[id*="branch"] summary span, '
                                     '.css-truncate[data-ref]')
        if branch_el:
            result['default_branch'] = branch_el.get_text(strip=True)
        # Branches count
        branch_count_el = soup.select_one('[data-testid="branch-select-menu"] span.Counter, '
                                           '[data-testid="branch-select-menu"] .count')
        if branch_count_el:
            txt = branch_count_el.get_text(strip=True).replace(',', '')
            nums = re.findall(r'(\d+)', txt)
            if nums:
                result['branches'] = int(nums[0])
        if not result['branches']:
            m = re.search(r'(\d+)\s*(?:branches?|tags?)', resp.text, re.I)
            if m:
                result['branches'] = int(m.group(1))
        # Tags
        m = re.search(r'(\d+)\s*tags?', resp.text, re.I)
        if m:
            result['tags'] = int(m.group(1))
        # Try from releases page link
        tags_el = soup.select_one('a[href*="/tags"] span, a[href*="/releases"] span')
        if tags_el and not result['tags']:
            txt = tags_el.get_text(strip=True)
            nums = re.findall(r'(\d+)', txt)
            if nums:
                result['tags'] = int(nums[0])

        # --- 10. Commits ---
        commits_el = soup.select_one('[data-testid="commits-count"], '
                                      'span[data-testid="commits-count"], '
                                      'strong[data-testid="commits-count"]')
        if not commits_el:
            commits_el = soup.select_one('a[href*="/commits/"] strong, '
                                          'a[href*="/commits/"] span')
        if commits_el:
            txt = commits_el.get_text(strip=True).replace(',', '').replace('commits', '').strip()
            nums = re.findall(r'(\d+)', txt)
            if nums:
                result['commits'] = int(nums[0])
        if not result['commits']:
            m = re.search(r'(\d[\d,]*)\s*commits', resp.text, re.I)
            if m:
                result['commits'] = int(m.group(1).replace(',', ''))

        print(f"  ✅ {repo_url.split('/')[-1]} → "
              f"⭐已有关 {result['watching']} 👀 "
              f"📝{result['license'] or 'N/A'} "
              f"🏷️{len(result['topics'])}标签 "
              f"📦v{result['latest_release'] or 'N/A'}"
              f"👥{result['contributors']}人")

    except Exception as e:
        print(f"  ❌ {repo_url} → {e}")

    _detail_cache[repo_url] = result
    return result


def enrich_projects(projects: list) -> list:
    """批量丰富项目详情（带限速）"""
    for p in projects:
        url = p.get('url', '')
        if not url or not url.startswith('http'):
            continue
        details = fetch_project_details(url)
        p['details'] = details
        # 顺带把话题同步到tags（如果原来空的）
        if not p.get('tags') and details.get('topics'):
            p['tags'] = ','.join(details['topics'])
        time.sleep(1.5)  # 礼貌限速，避免被ban
    return projects


if __name__ == '__main__':
    # 简单测试
    result = fetch_project_details('https://github.com/MemPalace/mempalace')
    print(json.dumps(result, ensure_ascii=False, indent=2))
