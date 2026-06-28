"""
Social greeting tool.

Sends a random greeting to the configured Feishu group.
Can be triggered manually (/social [name]) or via background timer.
Patterned after claw1's social implementation.
"""

import json
import os
import random
import threading
import time

_CONFIG_FILE = None
_group_id = ""
_mention_users: dict[str, str] = {}
_default_mention = ""
_greetings = []
_enabled = True
_interval = 1800
_initial_delay = 300
_send_message_func = None


def _get_config_file():
    global _CONFIG_FILE
    if _CONFIG_FILE is None:
        _CONFIG_FILE = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sche_tasks", "social.json"
        )
    return _CONFIG_FILE


def _load_config():
    cfg = {}
    try:
        with open(_get_config_file(), 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        pass
    return cfg


def set_social_config(group_id="", mention_users=None, default_mention="", greetings=None, interval=1800, initial_delay=300, send_func=None):
    global _group_id, _mention_users, _default_mention, _greetings, _interval, _initial_delay, _send_message_func
    _group_id = group_id
    _mention_users = mention_users or {}
    _default_mention = default_mention
    _greetings = greetings or []
    _interval = interval
    _initial_delay = initial_delay
    if send_func is not None:
        _send_message_func = send_func


def _resolve_mention_open_id(mention_name: str) -> str:
    if mention_name and mention_name in _mention_users:
        return _mention_users[mention_name]
    if _default_mention in _mention_users:
        return _mention_users[_default_mention]
    if _mention_users:
        return next(iter(_mention_users.values()))
    return ""


def _resolve_mention_name(arg_name: str) -> str:
    if arg_name:
        return arg_name
    if _default_mention in _mention_users:
        return _default_mention
    if _mention_users:
        return next(iter(_mention_users))
    return ""


def _reload_state(cfg: dict):
    """Update module-level state from config dict (in-place)."""
    global _mention_users, _default_mention, _group_id, _greetings, _enabled, _interval, _initial_delay
    if "mention_users" in cfg:
        _mention_users = cfg["mention_users"]
    if "default_mention" in cfg:
        _default_mention = cfg["default_mention"]
    if "group_id" in cfg:
        _group_id = cfg["group_id"]
    if "greetings" in cfg:
        _greetings = cfg["greetings"]
    if "enabled" in cfg:
        _enabled = cfg["enabled"]
    if "interval_seconds" in cfg:
        _interval = cfg["interval_seconds"]
    if "initial_delay_seconds" in cfg:
        _initial_delay = cfg["initial_delay_seconds"]


def tool_send_social_greeting(message: str = "", mention_name: str = "") -> str:
    """Send a social greeting to the configured group."""
    send_message = _send_message_func
    if send_message is None:
        return "Error: send_message not initialized. Call set_social_config(send_func=...) first."

    cfg = _load_config()
    _reload_state(cfg)
    group_id = cfg.get("group_id", _group_id)
    greetings = cfg.get("greetings", _greetings)

    if not group_id:
        return "Error: No social group configured"

    if mention_name and mention_name not in _mention_users:
        names = "、".join(_mention_users.keys()) if _mention_users else "（无配置）"
        return f"Error: 没有叫「{mention_name}」的人。可用人选：{names}"

    target = _resolve_mention_name(mention_name)
    target_open_id = _resolve_mention_open_id(mention_name)

    if not message:
        template = random.choice(greetings) if greetings else "你好！"
        message = template

    try:
        send_message(group_id, message, receive_id_type="chat_id", to_mention_open_id=target_open_id)
        return f"Social greeting sent to {target or '(no @)'}: {message[:60]}"
    except Exception as e:
        return f"Error sending social greeting: {e}"


def start_social_timer(stop_event=None):
    """Start background social greeting timer."""
    cfg = _load_config()
    _reload_state(cfg)
    initial_delay = cfg.get("initial_delay_seconds", _initial_delay)
    interval = cfg.get("interval_seconds", _interval)
    enabled = cfg.get("enabled", _enabled)
    greetings = cfg.get("greetings", _greetings)
    group_id = cfg.get("group_id", _group_id)

    if not enabled:
        print("[social_timer] disabled by config")
        return
    if not group_id:
        print("[social_timer] group_id not set, timer skipped")
        return

    target = _resolve_mention_name("")
    target_open_id = _resolve_mention_open_id("")

    send_message = _send_message_func

    def _loop():
        time.sleep(initial_delay)
        num = 0
        while not (stop_event and stop_event.is_set()):
            try:
                if not greetings or send_message is None:
                    time.sleep(interval)
                    continue
                template = random.choice(greetings)
                send_message(group_id, template, receive_id_type="chat_id", to_mention_open_id=target_open_id)
                num += 1
                print(f"[social_timer] #{num} greeting sent to {target or '(no @)'}: {template[:40]}")
            except Exception as e:
                print(f"[social_timer] error: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, daemon=True, name="social_timer")
    thread.start()
    print(f"[social_timer] started: interval={interval}s, initial_delay={initial_delay}s, target={target or '(no @)'}")


def get_social_mention_names() -> str:
    """Get available mention names as a comma-separated string."""
    cfg = _load_config()
    _reload_state(cfg)
    if not _mention_users:
        return ""
    return "、".join(_mention_users.keys())
