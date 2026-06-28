"""
Auto Tool/SOP Usage Logger — 真·黑匣子插件

利用 agent_loop 的 _hook('tool_after') 自动记录每次工具调用，
写入 tool_usage_logs/YYYY-MM-DD.json（JSON数组，追加写入）。

手动补充场景/SOP上下文可调用:
    from memory.tool_usage_log import log_context
    log_context(scenario="分析代码", sops=["codegraph_sop"])

用法统计:
    python memory/tool_usage_log_analyzer.py
"""

import os, json, datetime, threading, re

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tool_usage_logs")
_WRITE_LOCK = threading.Lock()


def _today_path() -> str:
    """返回今日日志文件路径"""
    os.makedirs(_LOG_DIR, exist_ok=True)
    return os.path.join(_LOG_DIR, f"{datetime.date.today().isoformat()}.json")


def _append_record(record: dict):
    """线程安全地追加一条记录到今日JSON文件"""
    path = _today_path()
    with _WRITE_LOCK:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = []
            data.append(record)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 绝不让日志异常影响主流程
            import sys
            sys.stderr.write(f"[tool_usage_logger] write error: {e}\n")


def _sanitize_args(args: dict) -> dict:
    """清理参数：去掉大文本、隐藏敏感字段，只保留摘要"""
    if not args:
        return {}
    safe = {}
    SKIP_KEYS = {"script", "content", "old_content", "new_content"}
    TRUNCATE_KEYS = {"question", "key_info"}
    for k, v in args.items():
        if k.startswith("_"):
            continue  # 内部字段不记
        if k in SKIP_KEYS:
            safe[k] = f"<{len(str(v))} chars>"
        elif k in TRUNCATE_KEYS and isinstance(v, str) and len(v) > 80:
            safe[k] = v[:80] + "..."
        elif isinstance(v, (str, int, float, bool)):
            safe[k] = v
        elif v is None:
            safe[k] = None
        elif isinstance(v, (list, tuple)):
            safe[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            safe[k] = f"{{{len(v)} keys}}"
        else:
            safe[k] = str(v)[:60]
    return safe


# ── SOP 路径匹配模式 ──────────────────────────────────────────
# 只有文件名以 _sop 结尾的 .md/.py 文件才算真正的 SOP
_SOP_PATTERNS = [
    re.compile(r"memory[/\\].*_sop\.md$"),          # memory/**/*_sop.md
    re.compile(r"memory[/\\].*_sop\.py$"),          # memory/**/*_sop.py
    re.compile(r"memory[/\\].*\.sop$"),             # memory/**/*.sop
]
_SOP_EXCLUDE = re.compile(r"memory[/\\]L4_raw_sessions[/\\]")


def _detect_sops(tool_name: str, args: dict) -> list[str]:
    """自动从 file_read 参数中识别 SOP/工具模块文件，返回 SOP 名称列表"""
    if tool_name != "file_read":
        return []
    path = args.get("path", "")
    if not isinstance(path, str):
        return []
    # 统一路径分隔符
    norm_path = path.replace("\\", "/")
    # 排除 L4_raw_sessions
    if _SOP_EXCLUDE.search(norm_path):
        return []
    # 匹配 SOP 模式
    for pat in _SOP_PATTERNS:
        if pat.search(norm_path):
            # 提取文件名（去掉扩展名）
            basename = os.path.basename(norm_path)
            name = os.path.splitext(basename)[0]
            return [name]
    return []


def _update_stats():
    """每次工具调用后刷新 stats 文件"""
    try:
        from memory.tool_usage_log_analyzer import analyze, _write_stats
        report = analyze()
        if "error" not in report:
            _write_stats(report)
    except Exception as e:
        import sys
        sys.stderr.write(f"[tool_usage_logger] stats update error: {e}\n")


# ── 注册 tool_after 钩子 ──────────────────────────────────────
import plugins.hooks as hooks


@hooks.register("tool_after")
def _on_tool_after(ctx):
    """工具调用后自动记录"""
    try:
        tool_name = ctx.get("tool_name", "?")
        args = ctx.get("args", {})
        ret = ctx.get("ret")

        # 判断结果
        has_error = False
        if ret is not None:
            has_error = ret.should_exit and ret.data is None
        elif tool_name == "bad_json":
            has_error = True

        record = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "auto_hook",
            "tool_name": tool_name,
            "args_keys": list(args.keys()) if args else [],
            "args_summary": _sanitize_args(args),
            "result": "error" if has_error else "success",
            "turn": ctx.get("index", 0) + 1,  # approximate
        }
        # 自动识别 file_read 读到的 SOP/工具模块
        sop_names = _detect_sops(tool_name, args)
        if sop_names:
            record["sop_names"] = sop_names
        _append_record(record)
        # 每次工具调用后刷新 stats（保持统计实时更新）
        _update_stats()
    except Exception as e:
        import sys
        sys.stderr.write(f"[tool_usage_logger] hook error: {e}\n")
