# GenericAgent 架构指南：模块分解、数据通路与阅读顺序

> 本文档面向想深入理解 GenericAgent 源码的开发者。读完你将能回答：**用户一句话如何变成 Agent 的多轮工具调用？LLM 怎么适配？记忆怎么沉淀？**

---

## 一、项目模块地图

```
GenericAgent/
├── agentmain.py          # [入口] 主控制器：任务队列、LLM管理、启动分发
├── agent_loop.py         # [核心] Agent 执行循环（~100行）
├── ga.py                 # [工具] 9个原子工具的实现 + Handler
├── llmcore.py            # [适配] LLM 多厂商适配、消息格式转换、历史管理
├── TMWebDriver.py        # [浏览器] CDP协议的浏览器注入驱动
├── simphtml.py           # [浏览器] 网页简化/过滤/JS执行
│
├── frontends/            # [前端] 所有用户交互入口
│   ├── chatapp_common.py     # 聊天前端公共逻辑（所有Bot复用）
│   ├── stapp.py/stapp2.py    # Streamlit 桌面UI
│   ├── tuiapp.py             # Textual 终端TUI
│   ├── qtapp.py              # Qt 桌面应用
│   ├── tgapp.py              # Telegram Bot
│   ├── qqapp.py              # QQ Bot
│   ├── wechatapp.py          # 微信个人Bot
│   ├── fsapp.py              # 飞书Bot
│   ├── wecomapp.py           # 企业微信Bot
│   ├── dingtalkapp.py        # 钉钉Bot
│   ├── dcapp.py              # Discord Bot
│   ├── genericagent_acp_bridge.py  # Codeg ACP桥接
│   └── desktop_pet.pyw       # 桌面宠物
│
├── reflect/              # [高级] 反射/计划/自治模式
│   ├── scheduler.py          # 定时任务调度器（cron-like）
│   ├── goal_mode.py          # 持续自驱模式（预算制）
│   ├── autonomous.py         # 闲置自治探索
│   └── agent_team_worker.py  # 多Agent协作（BBS接单）
│
├── memory/               # [记忆] 分层记忆系统 + 技能库
│   ├── global_mem.txt        # L2 全局事实
│   ├── global_mem_insight.txt    # L1 记忆索引
│   ├── memory_management_sop.md  # L0 记忆管理元规则
│   ├── *_sop.md              # L3 各类任务SOP
│   ├── L4_raw_sessions/      # L4 会话归档
│   ├── skill_search/         # 远程Skill检索引擎
│   └── *.py                  # 辅助工具（OCR/ADB/UI检测等）
│
├── plugins/              # [插件] 可观测性
│   └── langfuse_tracing.py   # Langfuse 追踪（monkey-patch方式）
│
├── assets/               # [资源] Schema/提示词/模板
│   ├── tools_schema.json     # 9个工具定义
│   ├── sys_prompt.txt        # 系统提示词
│   └── tmwd_cdp_bridge/      # Chrome扩展（CDP桥接）
```

---

## 二、核心数据通路：一条用户请求的完整旅程

```
┌─────────────┐
│  用户输入    │  "帮我在桌面创建 hello.txt, 内容是 Hello World"
└──────┬──────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: 入口 — agentmain.py                                │
│                                                             │
│  agent.put_task(query)          # 入队到 task_queue         │
│  agent.run()                    # 后台线程消费队列           │
│  get_system_prompt()            # 组装 system prompt        │
│    = sys_prompt.txt + global_memory(L1/L2)                  │
│  GenericAgentHandler(agent, history, cwd='./temp')          │
│  → agent_runner_loop(client, system_prompt, query,          │
│                       handler, TOOLS_SCHEMA)                │
└──────┬──────────────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: 执行循环 — agent_loop.py (agent_runner_loop)       │
│                                                             │
│  messages = [                                               │
│    {role:"system", content: system_prompt},                 │
│    {role:"user",   content: user_input}                     │
│  ]                                                          │
│                                                             │
│  while turn < max_turns:                                    │
│    ┌─ ① response = client.chat(messages, tools)  # 调LLM   │
│    │                                                         │
│    ├─ ② tool_calls = 解析 response 中的工具调用              │
│    │    无工具调用 → 自动补 "no_tool"                         │
│    │                                                         │
│    ├─ ③ for each tool_call:                                 │
│    │     outcome = handler.dispatch(tool_name, args)         │
│    │     # StepOutcome(data, next_prompt, should_exit)       │
│    │     tool_results.append({tool_use_id, content})         │
│    │                                                         │
│    └─ ④ next_prompt = handler.turn_end_callback(...)        │
│        messages = [{role:"user", content:next_prompt,       │
│                      tool_results}]  # 每次只发1条新消息      │
│                                                             │
│  关键设计：完整历史存在 client.backend.history 中，          │
│  agent_runner_loop 只传最新一轮的增量消息                    │
└──────┬──────────────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 3: LLM 调用 — llmcore.py                              │
│                                                             │
│  client.chat(messages, tools) 分两条路径：                   │
│                                                             │
│  ┌─ 路径A: ToolClient（文本协议）                             │
│  │  _build_protocol_prompt()               # 拼接完整prompt │
│  │    = system_prompt + 工具定义JSON + 交互协议 + 历史        │
│  │  backend.ask(full_prompt)               # 调LLM          │
│  │  _parse_mixed_response(raw_text)        # 解析工具调用    │
│  │    匹配 <tool_use>{json}</tool_use>                        │
│  │  return MockResponse(thinking, content, tool_calls)       │
│  │                                                           │
│  └─ 路径B: NativeToolClient（原生协议）                        │
│      backend.tools = tools                 # 注入原生工具定义 │
│      backend.system = thinking_prompt                       │
│      backend.ask(merged_message)           # 调LLM           │
│      return MockResponse(thinking, content, tool_calls)       │
│        # Claude: 从 content_blocks 提取 tool_use              │
│        # OpenAI: 从 choices[0].message.tool_calls 提取        │
└──────┬──────────────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 4: 工具执行 — ga.py (GenericAgentHandler)              │
│                                                             │
│  handler.dispatch(tool_name, args)                           │
│    → do_{tool_name}(args, response)   # 自动映射             │
│                                                             │
│  9个工具的执行逻辑：                                          │
│  ┌──────────────────────────────────────────────────┐       │
│  │ code_run()      → subprocess.Popen 子进程执行     │       │
│  │ file_read()     → open().read() 直接读文件        │       │
│  │ file_write()    → open().write() 写文件           │       │
│  │ file_patch()    → str.replace() 精确替换          │       │
│  │ web_scan()      → TMWebDriver + simphtml 获取HTML │       │
│  │ web_execute_js()→ CDP Runtime.evaluate 执行JS     │       │
│  │ ask_user()      → 返回 INTERRUPT 中断信号         │       │
│  │ update_working_checkpoint() → 更新 handler.working│       │
│  │ start_long_term_update() → 注入记忆沉淀 prompt    │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  返回: StepOutcome(data, next_prompt, should_exit)          │
│    data:       工具执行结果                                  │
│    next_prompt: 下轮 prompt（含 working memory + history）   │
│    should_exit: True 时终止循环                              │
└──────┬──────────────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 5: turn_end_callback — 轮次结束处理                    │
│                                                             │
│  ga.py: GenericAgentHandler.turn_end_callback():            │
│    ① 提取 <summary> → 写入 history_info                     │
│       "[Agent] 调用工具file_write, args: ..."               │
│    ② 拼接 next_prompt:                                       │
│       working memory + 最近30轮history + key_info            │
│    ③ 注入防无限循环警告 (turn%7==0, turn%65==0)              │
│    ④ 注入全局记忆 (turn%10==0)                               │
│    ⑤ Plan模式提示 (如果处于plan模式)                          │
│    ⑥ 检查 _keyinfo / _intervene 文件（主控干预通道）          │
└──────┬──────────────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 6: 输出到前端                                         │
│                                                             │
│  display_queue.put({'next': chunk})    # 流式增量           │
│  display_queue.put({'done': full_resp}) # 最终结果           │
│                                                             │
│  前端轮询队列 → 渲染给用户                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、各模块详解与阅读指引

### 3.1 agent_loop.py — 执行循环（125行）⭐ 第一优先级

**职责**：Agent 多轮推理→执行→反馈的循环骨架。

**关键代码位置**：

| 位置 | 内容 | 说明 |
|------|------|------|
| `:1-12` | `StepOutcome`, `BaseHandler` | 核心数据结构和基类 |
| `:18-29` | `BaseHandler.dispatch()` | 反射调用 `do_{tool_name}`，支持 generator 协程 |
| `:42-99` | `agent_runner_loop()` | **主循环**：LLM 调用 → 工具执行 → 结果反馈 |

**阅读要点**：
- `StepOutcome(data, next_prompt, should_exit)` 是工具和循环之间的**唯一通讯协议**
- `BaseHandler` 是所有 Handler 的父类，前端自己的 Handler 也可以继承它
- 循环每轮只发 **1条** user 消息（包含 `tool_results`），完整历史由 `client.backend.history` 管理
- generator 模式支持工具执行过程中实时 yield 日志

**怎么阅读**：
```python
# 核心流程简化版（agent_loop.py:42-98）
def agent_runner_loop(client, system_prompt, user_input, handler, tools_schema):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]
    turn = 0
    while turn < handler.max_turns:
        turn += 1
        response = client.chat(messages, tools)         # ① 调LLM
        tool_calls = response.tool_calls or [no_tool]   # ② 解析工具
        
        tool_results = []
        for tc in tool_calls:
            outcome = handler.dispatch(tc.tool_name, tc.args)  # ③ 执行工具
            if outcome.should_exit: break                       # 中断信号
            tool_results.append({tool_use_id: tc.id, content: outcome.data})
        
        next_prompt = handler.turn_end_callback(...)     # ④ 组装下一轮prompt
        messages = [{"role": "user", "content": next_prompt, "tool_results": tool_results}]
```

---

### 3.2 ga.py — 工具集与 Handler（582行）⭐ 第一优先级

**职责**：9个原子工具的完整实现 + 工具调用的 orchestration。

**关键代码位置**：

| 位置 | 工具/函数 | 说明 |
|------|-----------|------|
| `:12-89` | `code_run()` | 子进程执行 Python/PowerShell/Bash |
| `:93-96` | `ask_user()` | 返回 INTERRUPT 中断信号 |
| `:113-142` | `web_scan()` | 浏览器页面扫描（HTML简化 + 标签页列表） |
| `:163-172` | `web_execute_js()` | 浏览器 JS 注入执行 |
| `:188-201` | `file_patch()` | 文件精确块替换 |
| `:210-247` | `file_read()` | 文件读取（行号/关键字搜索/模糊匹配） |
| `:261-511` | `GenericAgentHandler` | **Handler类**：工具dispatch + 工作记忆管理 |
| `:306-311` | `do_ask_user()` | 中断并等待用户输入 |
| `:402-419` | `do_file_read()` | 读取文件 + 记忆访问日志 |
| `:443-493` | `do_no_tool()` | 无工具调用时的二次确认/plan完成检测 |
| `:495-510` | `do_start_long_term_update()` | 触发记忆沉淀流程 |
| `:526-569` | `turn_end_callback()` | 每轮结束：提取summary、注入警告、拼接prompt |

**阅读要点**：

1. **`GenericAgentHandler` 的继承链**：`BaseHandler` → `GenericAgentHandler`。所有 `do_xxx` 方法都遵循 `(self, args, response) → StepOutcome` 签名。

2. **generator 协程模式**：工具函数使用 `yield` 输出日志，`return StepOutcome(...)` 返回结果。`BaseHandler.dispatch()` 用 `yield from` 串联整个调用链。

3. **working memory 机制**（`:431-441`）：
```python
self.working = {
    'key_info': '',      # 当前任务的关键信息
    'related_sop': '',   # 相关SOP名称
    'in_plan_mode': None, # plan模式的文件路径
    'passed_sessions': 0  # 跨了多少个对话
}
```
这个 dict 会在 `turn_end_callback` 中自动注入到每轮的 prompt 里。

4. **记忆沉淀流程**（`:495-510`）：
```
Agent 调用 start_long_term_update
  → 系统读取 L0 的 memory_management_sop.md
  → 注入 get_global_memory() (L1索引 + L2事实)
  → Agent 按 SOP 判断记忆类型
  → file_read 现有记忆 → file_patch 最小化更新
```

**怎么阅读**：
- 先看 `do_code_run()` (最常用的工具)：理解代码块提取 → 子进程执行 → 输出整理的完整链路
- 再看 `do_no_tool()`：理解为什么需要"无工具调用"这个伪工具（空回复检测、代码块遗漏检测、plan完成验证）
- 最后看 `turn_end_callback()`：理解**每轮结束时注入的上下文**是什么

---

### 3.3 llmcore.py — LLM 适配层（1022行）⭐ 第二优先级

**职责**：多厂商 LLM 统一适配、消息格式转换、历史压缩、重试与故障转移。

**关键代码位置**：

| 位置 | 类/函数 | 说明 |
|------|---------|------|
| `:6-27` | `_load_mykeys()` / `reload_mykeys()` | 从 mykey.py/json 加载配置，热重载 |
| `:33-64` | `compress_history_tags()` | 压缩旧消息中的 thinking/tool 内容 |
| `:90-102` | `trim_messages_history()` | 上下文超出时裁剪旧消息 |
| `:509-537` | `BaseSession` | Session 基类：api_key, api_base, model, proxy, 超时等 |
| `:587-603` | `ClaudeSession` | Anthropic /messages 端点 |
| `:605-607` | `LLMSession` | OpenAI 兼容 /chat/completions |
| `:628-696` | `NativeClaudeSession` | Claude 原生 tool_use (content_block 格式) |
| `:698-702` | `NativeOAISession` | OpenAI 原生 tool_calls |
| `:730-839` | `ToolClient` | **文本协议客户端**：协议拼接 + 解析 |
| `:892-951` | `MixinSession` | **多Session故障转移**：轮询重试 + 弹簧回归 |
| `:964-1006` | `NativeToolClient` | **原生协议客户端**：使用 API 原生工具调用 |
| `:1008-1017` | `resolve_client()` | 配置名 → 客户端 的路由决策函数 |

**两种工具调用模式的对比**：

| 特性 | ToolClient（文本协议） | NativeToolClient（原生协议） |
|------|----------------------|---------------------------|
| 工具注入 | JSON 嵌入 prompt 文本 | API 原生 `tools` 参数 |
| 工具调用格式 | `<tool_use>{"name":"xxx","arguments":...}</tool_use>` | content_block `{type:"tool_use", ...}` |
| 适用场景 | 任何支持 chat 的模型 | 支持 function calling 的模型 |
| 上下文开销 | 高（每轮都传完整工具JSON） | 低（利用 API 缓存） |
| 确定方式 | 变量名不含 `native` | 变量名含 `native` |

**配置名路由规则**（`:1008-1017`）：

```python
def resolve_client(cfg_name):
    if 'native' in cfg_name and 'claude' in cfg_name:
        return NativeToolClient(NativeClaudeSession)
    if 'native' in cfg_name and 'oai' in cfg_name:
        return NativeToolClient(NativeOAISession)
    if 'claude' in cfg_name:
        return ToolClient(ClaudeSession)
    if 'oai' in cfg_name:
        return ToolClient(LLMSession)
```

**消息格式统一**：内部统一使用 Claude content-block 格式：
```python
# 内部格式 (所有session通用)
[
    {"type": "text", "text": "..."},
    {"type": "thinking", "thinking": "...", "signature": "..."},
    {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
    {"type": "tool_result", "tool_use_id": "...", "content": "..."}
]

# 转发给 OpenAI 时通过 _msgs_claude2oai() 转换 (llmcore.py:462-506)
```

**MixinSession 故障转移机制**（`:892-951`）：
```
1. 配置多个 LLM session（如 [claude_session, oai_session]）
2. 请求先发给 primary (session[0])
3. 失败 → 自动切到下一个 session
4. 默认 300 秒后弹簧回归 primary
5. 指数退避延迟：base_delay * 1.5^round，上限 30s
```

**怎么阅读**：
1. 先看 `BaseSession.__init__()`（`:510-537`）理解配置参数
2. 再看 `ToolClient.chat()`（`:739-757`）理解文本协议模式
3. 最后看 `NativeToolClient.chat()`（`:977-1006`）对比原生协议模式

---

### 3.4 agentmain.py — 主入口（270行）⭐ 第二优先级

**职责**：任务调度、LLM 管理、启动模式分发。

**关键代码位置**：

| 位置 | 内容 | 说明 |
|------|------|------|
| `:42-53` | `GenericAgent.__init__()` | 初始化任务队列、线程锁、LLM客户端 |
| `:55-76` | `load_llm_sessions()` | 从 mykey 加载所有 LLM 配置，处理 mixin |
| `:78-88` | `next_llm()` | 切换到下一个 LLM（支持 `/llm n` 命令） |
| `:98-107` | `abort()` / `put_task()` | 中断当前任务 / 提交新任务 |
| `:110-123` | `_handle_slash_cmd()` | 处理 `/session.xxx=yyy` 等运行时参数注入 |
| `:125-169` | `run()` | **主循环**：取任务→创建Handler→启动loop→输出结果 |
| `:173-270` | `__main__` | CLI入口：task/reflect/interactive三种模式 |

**三种运行模式**：

| 模式 | 命令 | 用途 |
|------|------|------|
| 交互模式 | `python agentmain.py` | 命令行直接对话 |
| 任务模式 | `python agentmain.py --task IODIR` | 文件IO驱动（后台进程） |
| 反射模式 | `python agentmain.py --reflect SCRIPT` | 外部脚本定期触发 |

**任务模式下文件IO协议**（`:200-221`）：
```
temp/{task_name}/
  input.txt       ← 用户写入任务，Agent 自动检测并执行
  output.txt      → Agent 输出结果
  output1.txt     → 多轮对话的后续输出
  reply.txt       → 用户写入后续指令，Agent 自动检测
  _stop           → 创建此文件可中断 Agent
  _keyinfo        → 写入关键信息注入 Agent 的工作记忆
  _intervene      → 注入任意 prompt 到 Agent 下一轮
  _history.json   → 恢复历史对话
```

---

### 3.5 frontends/ — 前端层（参考即可）

**职责**：提供多种用户交互方式，所有前端共享 `AgentChatMixin` 基类。

**关键代码位置**：

| 文件 | 说明 |
|------|------|
| `frontends/chatapp_common.py` | **聊天前端公共逻辑**：命令解析、会话恢复、格式处理 |
| `:248-331` | `AgentChatMixin` | 公共基类：命令处理 + Agent 运行 |
| `:263-303` | `handle_command()` | `/help`, `/stop`, `/status`, `/llm`, `/new`, `/continue` |
| `:305-331` | `run_agent()` | 提交任务到 agent → 轮询 display_queue → 发送结果 |

**所有前端统一的数据交互接口**：
```python
# 前端提交任务
dq = agent.put_task(query, source="chat")

# 前端接收结果（轮询模式）
while True:
    item = dq.get()
    if 'next' in item:   # 增量流式输出 -> 实时显示
        send_chunk(item['next'])
    if 'done' in item:   # 最终结果 -> 显示完成态
        send_final(item['done'])
        break
```

**怎么阅读**：只需看 `chatapp_common.py` 即可理解所有前端的公共逻辑。

---

### 3.6 reflect/ — 高级模式层（按需阅读）

**职责**：反射模式、定时任务、目标驱动自治。

| 文件 | 行数 | 机制 |
|------|------|------|
| `reflect/scheduler.py` | 131 | 读 `sche_tasks/*.json` → cron规则匹配 → 触发任务 |
| `reflect/goal_mode.py` | 95 | JSON state 文件驱动 → 预算计时 → 收口 |
| `reflect/autonomous.py` | 6 | 30分钟无交互 → 触发自主探索 |
| `reflect/agent_team_worker.py` | 45 | BBS 接单 → 多 Agent 协作 |

**统一接口**：
```python
# 每个 reflect 脚本必须实现
INTERVAL = 60           # 检查间隔（秒）
ONCE = False            # 是否只执行一次
def check() -> str|None:  # 返回 None=不唤醒, 返回 str=作为下一轮用户输入
def on_done(result) -> None:  # 可选，任务完成回调
```

**启动方式**：`python agentmain.py --reflect reflect/scheduler.py`

---

### 3.7 memory/ — 记忆系统

**分层记忆模型**：

| 层级 | 文件位置 | 更新时机 | 注入时机 |
|------|---------|---------|---------|
| L0 元规则 | `memory/memory_management_sop.md` | 手动 | 调用 `start_long_term_update` 时 |
| L1 索引 | `memory/global_mem_insight.txt` | Agent 自动 | 每 10 轮 |
| L2 事实 | `memory/global_mem.txt` | Agent `file_patch` | 每 10 轮 |
| L3 SOP | `memory/*_sop.md` | Agent `file_write` 创建 | 按需 `file_read` |
| L4 归档 | `memory/L4_raw_sessions/` | Scheduler 定时压缩 | 语义检索 |

---

### 3.8 plugins/ — 可观测性

**`plugins/langfuse_tracing.py`**（122行）：使用 monkey-patch 方式无侵入注入追踪。

```
补丁点：
  llmcore._write_llm_log    → 拦截 Prompt/Response 日志 → 创建 generation span
  BaseHandler.tool_before    → 拦截工具调用前 → 创建 tool span
  BaseHandler.tool_after     → 拦截工具调用后 → 结束 tool span
  agent_runner_loop          → 拦截任务循环 → 创建 agent trace
  _parse_claude_sse          → 拦截 SSE 流 → 提取 usage 信息
  _parse_openai_sse          → 同上
```

在 `mykey.py` 中配置：
```python
langfuse_config = {
    "secret_key": "sk-lf-...",
    "public_key": "pk-lf-...",
    "host": "https://cloud.langfuse.com"
}
```

---

## 四、推荐阅读顺序（从易到难）

### 第1天：理解核心链路（~3小时）

```
1. agent_loop.py（125行）       ← 先看这个！理解 while-turn 循环
2. agentmain.py（270行）        ← 理解入口和任务调度
3. ga.py 的 GenericAgentHandler 类（~300行） ← 理解工具如何 dispatch
4. 手动画一条 "创建hello.txt" 的调用链路
```

### 第2天：理解 LLM 适配层（~4小时）

```
5. llmcore.py: BaseSession（:510-537）    ← 配置参数
6. llmcore.py: ToolClient（:730-839）     ← 文本协议模式
7. llmcore.py: LLMSession + ClaudeSession ← 两种API端点
8. llmcore.py: NativeToolClient（:964-1006） ← 原生协议模式
9. llmcore.py: MixinSession（:892-951）   ← 故障转移
10. llmcore.py: 消息格式转换（:462-506）    ← claude2oai 转换
```

### 第3天：理解记忆和进化（~3小时）

```
11. assets/sys_prompt.txt（6行）           ← L0 行为约束
12. memory/memory_management_sop.md       ← 记忆管理元规则
13. ga.py: do_start_long_term_update()    ← 记忆沉淀触发
14. ga.py: get_global_memory()            ← L1+L2 注入
15. ga.py: turn_end_callback()            ← 每一帧的上下文拼装
16. reflect/scheduler.py                  ← L4 归档触发
```

### 第4天：按需深入

```
17. frontends/chatapp_common.py           ← 理解前端协议
18. reflect/goal_mode.py                  ← 持续自驱模式
19. TMWebDriver.py + simphtml.py          ← 浏览器控制
20. plugins/langfuse_tracing.py           ← 可观测性
21. memory/skill_search/engine.py         ← 远程Skill检索
```

---

## 五、关键设计模式与技巧

### 5.1 Generator 协程用于工具执行
```python
# ga.py:306-311
def do_ask_user(self, args, response):
    question = args.get("question")
    yield f"Waiting for your answer ...\n"   # 实时日志
    return StepOutcome(result, next_prompt="", should_exit=True)

# agent_loop.py:75-80 — dispatch 适配 generator
gen = handler.dispatch(tool_name, args, response)
outcome = (yield from proxy())           # yield from 串联
```

### 5.2 StepOutcome — 唯一的跨层协议
```python
@dataclass
class StepOutcome:
    data: Any                    # 工具结果 (dict/str/list)
    next_prompt: Optional[str]   # None=任务完成, str=继续
    should_exit: bool = False    # 中断循环
```

### 5.3 Monkey-patch 实现无侵入插件
```python
# plugins/langfuse_tracing.py:82-102
_orig_before = agent_loop.BaseHandler.tool_before_callback
def _patched_before(self, tool_name, args, response):
    # ... 追踪逻辑 ...
    return _orig_before(self, tool_name, args, response)
agent_loop.BaseHandler.tool_before_callback = _patched_before
```

### 5.4 配置名约定驱动行为
```python
# llmcore.py:1011-1013 — 变量名决定接口类型
cfg_name = 'oai_minimax_config'    # → LLMSession (OpenAI 兼容)
cfg_name = 'claude_config'         # → ClaudeSession (Anthropic)
cfg_name = 'native_claude_config'  # → NativeClaudeSession (原生 tool_use)
cfg_name = 'mixin_config'          # → MixinSession (多实例故障转移)
```

### 5.5 上下文效率策略
```
1. 分层记忆 (L0-L4): 不同粒度、不同频率注入
2. 消息压缩: compress_history_tags() 截断旧 thinking/tool 内容
3. 上下文裁剪: trim_messages_history() 超过 3x context_win 时丢弃旧消息
4. 工具去重: 每 10 轮重置工具描述 (client.last_tools = '')
5. 增量消息: 每轮只发 1 条新 user 消息，历史在 session 中管理
```

---

## 六、调试与排错

### 查看 LLM 请求/响应日志
```bash
# 日志位于
temp/model_responses/model_responses_XXXXXX.txt
# 格式:
=== Prompt === 2026-01-01 12:00:00
{prompt内容}

=== Response === 2026-01-01 12:00:05
{response内容}
```

### 理解模型切换工作流
```
/llm        → 列出所有已配置的 LLM
/llm 2      → 切换到第 2 个 LLM
/session.model=claude-sonnet-4-20250514  → 运行时改 model
/session.temperature=0.5                 → 运行时改 temperature
```

### 理解 agent 状态
```
/status     → 显示 Agent 运行状态 + 当前使用的 LLM
/stop       → 中断当前任务
/new        → 清空对话历史
/continue   → 恢复历史对话
```
