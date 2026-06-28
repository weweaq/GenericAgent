# GitHub Trending追踪系统 实施计划

**目标**：每日自动抓取GitHub Trending项目 → 网页展示+AI分析 → 支持评分评论 → 趋势总结预测

**架构**：Flask后端(SQLite) + Vue 3 SPA(CDN加载，免构建)

**部署路径**：`../github_trending/`

---

## 架构概要

```
github_trending/
├── app.py                 # Flask主入口 + API路由
├── trending_scraper.py    # GitHub Trending爬虫
├── analyzer.py            # AI分析引擎（调用LLM分析项目）
├── models.py              # SQLite数据模型
├── templates/
│   └── index.html         # Vue 3 SPA (CDN: vue.global.prod.js)
├── static/
│   ├── app.js             # Vue 3 应用逻辑
│   └── style.css          # 样式
├── trending.db            # SQLite数据库
├── requirements.txt       # Python依赖
└── sche_tasks/
    └── github_trending.json  # 定时任务定义
```

**技术折衷**：npm环境受限(npm可用但PATH未配)，Vue 3通过CDN加载(ESM方式)，免构建步骤。

---

## 数据库Schema

```sql
-- 项目表
projects (id, name, url, description, language, stars, forks, 
          stars_today, analyzed_at, ai_summary, ai_rating, tags)

-- 评分记录
ratings (id, project_id, created_at, rating(1-10), comment)

-- 每日趋势报告
daily_reports (id, date, summary, prediction, created_at)
```

---

## 任务列表（每任务2-5分钟，独立subagent执行）

### Task 1: 项目骨架 + 数据模型
- [ ] Step 1: 创建 `github_trending/` 目录结构和 `models.py`
- [ ] Step 2: 实现SQLite建表+CRUD操作
- [ ] Step 3: 创建 `requirements.txt`（flask, requests）
- [ ] Step 4: 运行测试确认模型可用

### Task 2: GitHub Trending爬虫
- [ ] Step 1: 创建 `trending_scraper.py` 用requests抓 `https://github.com/trending`
- [ ] Step 2: 解析HTML提取：项目名/描述/语言/星数/今日星增
- [ ] Step 3: 处理爬虫异常+UA伪装
- [ ] Step 4: 测试：运行爬虫验证输出JSON格式

### Task 3: AI分析引擎
- [ ] Step 1: 创建 `analyzer.py` — 对每个项目调用LLM生成分析
- [ ] Step 2: 分析维度：项目定位/技术亮点/适用场景/竞品对比
- [ ] Step 3: 生成每日趋势总结+预测
- [ ] Step 4: 测试：对一个mock项目运行分析

### Task 4: Flask API后端
- [ ] Step 1: 创建 `app.py` — GET `/api/trending` 返回今日项目
- [ ] Step 2: 实现 POST `/api/rate` — 提交评分+评论
- [ ] Step 3: 实现 GET `/api/history` — 历史趋势数据
- [ ] Step 4: 实现 GET `/api/report` — 每日总结报告
- [ ] Step 5: 静态文件路由 + Vue SPA入口 `index.html`
- [ ] Step 6: 测试API：curl验证所有端点

### Task 5: Vue 3 SPA — 项目列表页
- [ ] Step 1: 创建 `templates/index.html` — Vue 3 CDN引入+挂载点
- [ ] Step 2: 创建 `static/app.js` — 项目列表卡片组件
- [ ] Step 3: 显示：项目名/描述/语言/星数/AI分析摘要
- [ ] Step 4: 测试：Flask启动，浏览器打开验证列表渲染

### Task 6: Vue 3 — 评分+评论功能
- [ ] Step 1: 实现星标评分(1-10)组件
- [ ] Step 2: 评论输入框+提交按钮 → POST `/api/rate`
- [ ] Step 3: 显示历史评分记录+平均分
- [ ] Step 4: 测试：提交评分后刷新验证持久化

### Task 7: Vue 3 — 历史趋势页
- [ ] Step 1: 趋势概览页：按日期查看历史项目
- [ ] Step 2: 每日报告展示（summary + prediction）
- [ ] Step 3: 简单统计图表（语言分布/热度趋势）
- [ ] Step 4: 测试：切换日期查看历史数据

### Task 8: Vue 3 — 样式美化
- [ ] Step 1: 创建 `static/style.css` — GitHub暗色风格主题
- [ ] Step 2: 响应式布局（PC/平板适配）
- [ ] Step 3: 卡片动效、加载状态、空状态
- [ ] Step 4: 验收：视觉一致性检查

### Task 9: 定时任务接入
- [ ] Step 1: 创建 `sche_tasks/github_trending.json`
- [ ] Step 2: 任务定义：每日07:00触发, prompt含抓取+分析+生成HTML
- [ ] Step 3: 编写任务执行入口脚本 `run_daily.py`
- [ ] Step 4: 测试：手动触发一次验证全流程

### Task 10: 首次全流程运行 + 验证
- [ ] Step 1: 启动Flask服务，打开浏览器
- [ ] Step 2: 运行爬虫抓取今天Trending
- [ ] Step 3: 验证网页展示正常
- [ ] Step 4: 提交一条评分评论验证持久化

---

## 执行顺序

```
Task 1 (骨架+模型)
  ↓
Task 2 (爬虫) → Task 3 (分析引擎)
  ↓
Task 4 (Flask API) ← 依赖T1,T2,T3
  ↓
Task 5 (Vue列表) ─→ Task 6 (评分) ─→ Task 7 (历史) ─→ Task 8 (样式)
  ↓
Task 9 (定时任务)
  ↓
Task 10 (全流程验证)
```

每条任务结束后：**两阶段审查**（规范审查→质量审查），通过后再开始下一条。
