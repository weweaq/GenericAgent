"""
GitHub Trending 数据库模型
SQLite + sqlite3 标准库，零依赖
"""
import sqlite3
import os, json
from datetime import date, datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), 'trending.db')


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    """初始化数据库表"""
    conn = get_conn()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            language TEXT DEFAULT '',
            stars INTEGER DEFAULT 0,
            forks INTEGER DEFAULT 0,
            stars_today INTEGER DEFAULT 0,
            trend_date TEXT NOT NULL,
            analyzed_at TEXT,
            ai_summary TEXT DEFAULT '',
            ai_rating REAL DEFAULT 0,
            tags TEXT DEFAULT '',
            details TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 10),
            comment TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL UNIQUE,
            summary TEXT DEFAULT '',
            prediction TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_projects_date ON projects(trend_date);
        CREATE INDEX IF NOT EXISTS idx_ratings_project ON ratings(project_id);
    ''')
    conn.commit()
    conn.close()
    print(f"[OK] DB initialized: {DB_PATH}")


# --- Project CRUD ---

def save_projects(projects: list[dict], trend_date: str = None) -> list[int]:
    """批量保存项目，返回ID列表"""
    conn = get_conn()
    c = conn.cursor()
    ids = []
    today = trend_date or date.today().isoformat()
    for p in projects:
        details_json = json.dumps(p.get('details', {}), ensure_ascii=False)
        c.execute('''
            INSERT OR REPLACE INTO projects
            (name, url, description, language, stars, forks, stars_today, trend_date,
             ai_summary, ai_rating, tags, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            p['name'], p['url'], p.get('description', ''),
            p.get('language', ''), p.get('stars', 0),
            p.get('forks', 0), p.get('stars_today', 0), today,
            p.get('ai_summary', p.get('ai_analysis', '')), p.get('ai_rating', 0.0),
            p.get('tags', ''), details_json
        ))
        ids.append(c.lastrowid)
    conn.commit()
    conn.close()
    return ids


def get_today_projects(today: str = None) -> list[dict]:
    """获取指定日期的项目列表"""
    today = today or date.today().isoformat()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM projects WHERE trend_date = ? ORDER BY stars DESC', (today,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    # 解析details JSON
    for r in rows:
        if r.get('details') and isinstance(r['details'], str):
            try:
                r['details'] = json.loads(r['details'])
            except:
                r['details'] = {}
        elif not r.get('details'):
            r['details'] = {}
    return rows


def get_project_history(name: str) -> list[dict]:
    """获取某个项目的历史记录"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM projects WHERE name = ? ORDER BY trend_date DESC', (name,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_ai_analysis(project_id: int, summary: str, rating: float, tags: str):
    """更新AI分析结果"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        UPDATE projects SET ai_summary=?, ai_rating=?, tags=?, analyzed_at=?
        WHERE id=?
    ''', (summary, rating, tags, datetime.now().isoformat(), project_id))
    conn.commit()
    conn.close()


# --- Rating CRUD ---

def add_rating(project_id: int, rating: int, comment: str = '') -> int:
    """添加评分，返回rating id"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT INTO ratings (project_id, created_at, rating, comment)
        VALUES (?, ?, ?, ?)
    ''', (project_id, datetime.now().isoformat(), rating, comment))
    conn.commit()
    rid = c.lastrowid
    conn.close()
    return rid


def get_ratings(project_id: int) -> list[dict]:
    """获取项目的所有评分"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM ratings WHERE project_id = ? ORDER BY created_at DESC', (project_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# --- Daily Report ---

def save_daily_report(summary: str, prediction: str):
    """保存每日报告"""
    today = date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO daily_reports (report_date, summary, prediction, created_at)
        VALUES (?, ?, ?, ?)
    ''', (today, summary, prediction, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_daily_report(report_date: str = None) -> Optional[dict]:
    """获取某日的报告"""
    if not report_date:
        report_date = date.today().isoformat()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM daily_reports WHERE report_date = ?', (report_date,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def clear_today_data(today: str = None):
    """清除今日已存数据（用于刷新）"""
    today = today or date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    # 获取今日项目ID
    c.execute('SELECT id FROM projects WHERE trend_date = ?', (today,))
    ids = [r[0] for r in c.fetchall()]
    # 删除关联评分
    for pid in ids:
        c.execute('DELETE FROM ratings WHERE project_id = ?', (pid,))
    # 删除项目
    c.execute('DELETE FROM projects WHERE trend_date = ?', (today,))
    # 删除日报
    c.execute('DELETE FROM daily_reports WHERE report_date = ?', (today,))
    conn.commit()
    conn.close()


def get_project_by_id(project_id: int) -> Optional[dict]:
    """根据ID获取项目"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_projects() -> list[dict]:
    """获取所有项目"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM projects ORDER BY trend_date DESC, stars DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def save_report(report_date: str, summary: str, prediction: str):
    """保存每日报告（兼容app.py调用名）"""
    return save_daily_report(summary, prediction)


def get_all_reports() -> list[dict]:
    """获取所有报告"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM daily_reports ORDER BY report_date DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


if __name__ == '__main__':
    init_db()
    print("✅ 数据库初始化完成，表已创建")
