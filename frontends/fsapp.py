import argparse, asyncio, importlib.util, json, os, queue as Q, re, sys, threading, time, uuid
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import traceback
import requests as _requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import *


def _ensure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_root_dir():
    root = os.environ.get("GA_WORKSPACE_ROOT")
    if root:
        return _ensure_dir(Path(root).expanduser().resolve())
    return _ensure_dir(Path(PROJECT_ROOT).resolve())


def _workspace_config_dir(root=None):
    base = Path(root).expanduser().resolve() if root else _workspace_root_dir()
    if base.name == "ga_config":
        return _ensure_dir(base)
    return _ensure_dir(base / "ga_config")


def _load_dict_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.suffix == ".py":
            mod_name = f"_fs_mykey_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            data = {k: v for k, v in vars(module).items() if not k.startswith("_")}
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[ERROR] load config failed {path}: {e}")
        return None


def _resolve_mykey_path():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    candidates = [
        config_root / "mykey.json",
        config_root / "mykey.py",
        workspace_root / "mykey.json",
        workspace_root / "mykey.py",
        Path(PROJECT_ROOT) / "mykey.json",
        Path(PROJECT_ROOT) / "mykey.py",
    ]
    for candidate in candidates:
        if _load_dict_config(candidate):
            return candidate
    return candidates[0]


def _ensure_runtime_paths():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    os.environ.setdefault("GA_WORKSPACE_ROOT", str(workspace_root))
    os.environ.setdefault("GA_USER_DATA_DIR", str(config_root))
    return str(workspace_root), str(config_root)


_ensure_runtime_paths()
from agentmain import GeneraticAgent
from frontends.chatapp_common import AgentChatMixin, FILE_HINT, split_text

_TOKEN_CACHE = {"token": None, "expire_at": 0.0}

def _get_tenant_token():
    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expire_at"] - 300:
        return _TOKEN_CACHE["token"]
    try:
        resp = _requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=10
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"[get_tenant_token] 失败: {data}")
            return None
        _TOKEN_CACHE["token"] = data["tenant_access_token"]
        _TOKEN_CACHE["expire_at"] = time.time() + data.get("expire", 7200)
        return _TOKEN_CACHE["token"]
    except Exception as e:
        print(f"[get_tenant_token] 异常: {e}")
        return None

def get_chat_history(chat_id, page_size=50, start_time=None, end_time=None):
    token = _get_tenant_token()
    if not token:
        return {"code": -1, "msg": "获取 tenant_access_token 失败"}
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": page_size,
        "sort_type": "ByCreateTimeAsc",
    }
    if start_time is not None: params["start_time"] = str(start_time)
    if end_time is not None:   params["end_time"]   = str(end_time)
    items = []
    while True:
        try:
            resp = _requests.get(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15
            )
            data = resp.json()
        except Exception as e:
            return {"code": -1, "msg": f"请求异常: {e}"}
        if data.get("code") != 0:
            return data
        items.extend(data["data"]["items"])
        if not data["data"].get("has_more"):
            break
        params["page_token"] = data["data"]["page_token"]
    return {"code": 0, "data": {"items": items}}

# ── Agent互聊模式 ──
AGENT_CHAT_FILE = os.path.join(PROJECT_ROOT, "temp", "agentchat.json")
_agent_chat_enabled = None

def _get_agentchat():
    global _agent_chat_enabled
    if _agent_chat_enabled is not None:
        return _agent_chat_enabled
    try:
        with open(AGENT_CHAT_FILE, 'r', encoding='utf-8') as f:
            _agent_chat_enabled = json.load(f).get("enabled", False)
    except:
        _agent_chat_enabled = False
    return _agent_chat_enabled

def _set_agentchat(enabled):
    global _agent_chat_enabled
    _agent_chat_enabled = enabled
    os.makedirs(os.path.dirname(AGENT_CHAT_FILE), exist_ok=True)
    with open(AGENT_CHAT_FILE, 'w', encoding='utf-8') as f:
        json.dump({"enabled": enabled, "updated_at": time.time()}, f, ensure_ascii=False)

# ── 已发送消息 ID 追踪（防自消息 echo）──
_sent_msg_ids = set()
_sent_msg_ids_lock = threading.Lock()
_SENT_MSG_MAX = 500

def _track_sent(msg_id):
    if msg_id:
        with _sent_msg_ids_lock:
            _sent_msg_ids.add(msg_id)
            if len(_sent_msg_ids) > _SENT_MSG_MAX * 2:
                _sent_msg_ids.clear()

def _is_self_sent(msg_id):
    with _sent_msg_ids_lock:
        return msg_id in _sent_msg_ids

_poll_last_seen_time = "0"
_POLL_CFG_FILE = os.path.join(PROJECT_ROOT, "sche_tasks", "agentchat_poll.json")

def _load_poll_cfg():
    try:
        with open(_POLL_CFG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def _poll_agentchat_loop():
    """后台线程：轮询群消息，检测其他Agent的新消息"""
    global _poll_last_seen_time
    cfg = _load_poll_cfg()
    poll_interval = int(cfg.get("poll_interval_seconds", 15))
    page_size = int(cfg.get("page_size", 10))
    start_time = cfg.get("start_time", 1678812018)
    end_time = cfg.get("end_time", 1778812018)
    enabled = cfg.get("enabled", True)
    if not enabled:
        print("[poll_agentchat] 已禁用 (sche_tasks/agentchat_poll.json enabled=false)", flush=True)
        return
    print(f"[poll_agentchat] 轮询线程已启动，{poll_interval}s间隔", flush=True)
    time.sleep(10)
    while True:
        try:
            if not _get_agentchat():
                _poll_last_seen_time = "0"
                time.sleep(10)
                continue
            r = get_chat_history(SOCIAL_GROUP_ID, page_size=page_size,
                                  start_time=start_time, end_time=end_time)
            if r.get("code") != 0:
                time.sleep(poll_interval)
                continue
            items = r.get("data", {}).get("items", [])
            if not items:
                time.sleep(poll_interval)
                continue
            latest_time = items[-1]["create_time"]
            if latest_time <= _poll_last_seen_time and _poll_last_seen_time != "0":
                time.sleep(poll_interval)
                continue
            for msg in items:
                if msg["create_time"] <= _poll_last_seen_time:
                    continue
                s = msg.get("sender", {})
                if s.get("sender_type") == "app" and s.get("id") != APP_ID:
                    content = msg.get("body", {}).get("content", "")
                    print(f"[poll_agentchat] 检测到小小怪新消息: {content[:60]}", flush=True)
                    agent.put_task(f"[群消息-轮询] 小小怪发了新消息: {content}\n你在群里@它，请处理。", source="feishu")
                    break
            _poll_last_seen_time = latest_time
        except Exception as e:
            print(f"[poll_agentchat] error: {e}", flush=True)
        time.sleep(poll_interval)

_TAG_PATS = [r"<" + t + r">.*?</" + t + r">" for t in ("thinking", "summary", "tool_use", "file_content")]
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
_AUDIO_EXTS = {".opus", ".mp3", ".wav", ".m4a", ".aac"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_MSG_TYPE_MAP = {"image": "[image]", "audio": "[audio]", "file": "[file]", "media": "[media]", "sticker": "[sticker]"}

TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")
MEDIA_DIR = os.path.join(TEMP_DIR, "feishu_media")
os.makedirs(MEDIA_DIR, exist_ok=True)


_TRUNC_TAIL = 300  # 截断兜底时保留原文尾部字符数
_DEDUP_TTL_SEC = 10 * 60
_DEDUP_MAX = 2000
_DEDUP_LOCK = threading.Lock()
_SEEN_MESSAGES = {}


def _claim_message_once(message_id):
    """Best-effort cross-platform dedup for Feishu reconnect redeliveries."""
    if not message_id:
        return True
    now = time.time()
    with _DEDUP_LOCK:
        expired = [mid for mid, ts in _SEEN_MESSAGES.items() if now - ts > _DEDUP_TTL_SEC]
        for mid in expired:
            _SEEN_MESSAGES.pop(mid, None)
        if len(_SEEN_MESSAGES) > _DEDUP_MAX:
            for mid, _ in sorted(_SEEN_MESSAGES.items(), key=lambda item: item[1])[:len(_SEEN_MESSAGES) - _DEDUP_MAX]:
                _SEEN_MESSAGES.pop(mid, None)
        if message_id in _SEEN_MESSAGES:
            return False
        _SEEN_MESSAGES[message_id] = now
        return True


def _clean(text):
    for pat in _TAG_PATS:
        text = re.sub(pat, "", text or "", flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_files(text):
    return re.findall(r"\[FILE:([^\]]+)\]", text or "")


def _strip_files(text):
    return re.sub(r"\[FILE:[^\]]+\]", "", text or "").strip()


def _display_text(text):
    cleaned = _strip_files(_clean(text))
    if cleaned:
        return cleaned
    tail = (text or "").strip()[-_TRUNC_TAIL:]
    return "⚠️ 模型输出被截断或为空" + (f"\n…{tail}" if tail else "")


def _to_allowed_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        value = [value]
    return {str(x).strip() for x in value if str(x).strip()}


def _parse_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _persist_recipient(chat_id, open_id):
    """保存飞书接收者ID到文件，供定时任务等主动推送使用"""
    import json
    try:
        fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'temp', 'last_feishu_recipient.json')
        data = {"chat_id": chat_id or "", "open_id": open_id, "updated_at": time.time()}
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[persist] 保存接收者ID失败: {e}")

def _extract_share_card_content(content_json, msg_type):
    parts = []
    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")
    return "\n".join([p for p in parts if p]).strip() or f"[{msg_type}]"


def _extract_interactive_content(content):
    parts = []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return [content] if content.strip() else []
    if not isinstance(content, dict):
        return parts
    title = content.get("title")
    if isinstance(title, dict):
        title_text = title.get("content", "") or title.get("text", "")
        if title_text:
            parts.append(f"title: {title_text}")
    elif isinstance(title, str) and title:
        parts.append(f"title: {title}")
    elements = content.get("elements", [])
    if isinstance(elements, list):
        for row in elements:
            if isinstance(row, dict):
                parts.extend(_extract_element_content(row))
            elif isinstance(row, list):
                for el in row:
                    parts.extend(_extract_element_content(el))
    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))
    header = content.get("header", {})
    if isinstance(header, dict):
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")
    return [p for p in parts if p]


def _extract_element_content(element):
    parts = []
    if not isinstance(element, dict):
        return parts
    tag = element.get("tag", "")
    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)
    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str) and text:
            parts.append(text)
        for field in element.get("fields", []) or []:
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    content = field_text.get("content", "") or field_text.get("text", "")
                    if content:
                        parts.append(content)
    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)
    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            content = text.get("content", "") or text.get("text", "")
            if content:
                parts.append(content)
        url = element.get("url", "") or (element.get("multi_url", {}) or {}).get("url", "")
        if url:
            parts.append(f"link: {url}")
    elif tag == "img":
        alt = element.get("alt", {})
        if isinstance(alt, dict):
            parts.append(alt.get("content", "[image]") or "[image]")
        else:
            parts.append("[image]")
    for child in element.get("elements", []) or []:
        parts.extend(_extract_element_content(child))
    for col in element.get("columns", []) or []:
        for child in (col.get("elements", []) if isinstance(col, dict) else []):
            parts.extend(_extract_element_content(child))
    return parts


def _extract_post_content(content_json):
    def _parse_block(block):
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if block.get("title"):
            texts.append(block.get("title"))
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and el.get("image_key"):
                    images.append(el["image_key"])
        text = " ".join([t for t in texts if t]).strip()
        return text or None, images

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs
    return "", []


AGENT_TIMEOUT_SEC = 900

agent = None
agent_error = None
agent_thread = None
client, user_tasks, app = None, {}, None
agent_lock = threading.Lock()


def _load_config():
    path = _resolve_mykey_path()
    if not path or not path.exists():
        return {}, str(path or "")
    try:
        data = _load_dict_config(path)
        return data if isinstance(data, dict) else {}, str(path)
    except Exception as e:
        print(f"[ERROR] load mykey failed {path}: {e}")
        return {}, str(path)


def _feishu_config():
    cfg, path = _load_config()
    app_id = str(cfg.get("fs_app_id", "") or "").strip()
    app_secret = str(cfg.get("fs_app_secret", "") or "").strip()
    allowed = _to_allowed_set(cfg.get("fs_allowed_users", []))
    return app_id, app_secret, allowed, (not allowed or "*" in allowed), path


APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH = _feishu_config()


def get_agent():
    global agent, agent_error, agent_thread
    with agent_lock:
        if agent is not None:
            return agent
        if agent_error:
            raise RuntimeError(agent_error)
        try:
            agent = GeneraticAgent()
            agent_thread = threading.Thread(target=agent.run, daemon=True)
            agent_thread.start()
            return agent
        except Exception as e:
            agent_error = str(e)
            raise


# ── 主动社交配置（从 sche_tasks/ 集中读取）──
_SOCIAL_CFG = {}
_SOCIAL_CFG_FILE = os.path.join(PROJECT_ROOT, "sche_tasks", "social.json")
try:
    with open(_SOCIAL_CFG_FILE, 'r', encoding='utf-8') as _f:
        _SOCIAL_CFG = json.load(_f)
except:
    pass
SOCIAL_GROUP_ID = _SOCIAL_CFG.get("group_id", "oc_bd650e1d5ccf82d1cd9f7afaa5fccb4a")
SOCIAL_INTERVAL = int(_SOCIAL_CFG.get("interval_seconds", 1800))
SOCIAL_INITIAL_DELAY = int(_SOCIAL_CFG.get("initial_delay_seconds", 300))
_SOCIAL_ENABLED = _SOCIAL_CFG.get("enabled", True)


def create_client():
    return lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).log_level(lark.LogLevel.INFO).build()


def _mask_secret(value):
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def check_config(init_agent=False):
    app_id, app_secret, allowed, public_access, path = _feishu_config()
    result = {
        "config_path": path,
        "app_id": app_id,
        "app_secret": _mask_secret(app_secret),
        "app_secret_present": bool(app_secret),
        "public_access": public_access,
        "allowed_users": sorted(allowed),
        "ready": bool(app_id and app_secret),
    }
    if init_agent:
        try:
            ga = get_agent()
            result["agent_ready"] = True
            result["llm_count"] = len(ga.list_llms()) if hasattr(ga, "list_llms") else 0
            result["current_llm"] = ga.get_llm_name() if getattr(ga, "llmclient", None) else ""
        except Exception as e:
            result["agent_ready"] = False
            result["agent_error"] = str(e)
    return result


def _card_raw(elements):
    return json.dumps({
        "schema": "2.0",
        "config": {"streaming_mode": False, "width_mode": "fill"},
        "body": {"elements": elements},
    }, ensure_ascii=False)


def _card(text):
    return _card_raw([{"tag": "markdown", "content": text}])


def _send_raw(receive_id, payload, msg_type, rtype):
    try:
        body = CreateMessageRequest.builder().receive_id_type(rtype).request_body(
            CreateMessageRequestBody.builder().receive_id(receive_id).msg_type(msg_type).content(payload).build()
        ).build()
        r = client.im.v1.message.create(body)
        if r.success():
            return r.data.message_id if r.data else None
        print(f"发送失败: {r.code}, {r.msg}")
    except Exception as e:
        print(f"[ERROR] send_message failed: {e}")
        traceback.print_exc()
    return None


def _patch_card(message_id, card_json):
    try:
        body = PatchMessageRequest.builder().message_id(message_id).request_body(
            PatchMessageRequestBody.builder().content(card_json).build()
        ).build()
        r = client.im.v1.message.patch(body)
        if not r.success():
            print(f"[ERROR] patch_card 失败: {r.code}, {r.msg}")
        return r.success()
    except Exception as e:
        print(f"[ERROR] patch_card exception: {e}")
        traceback.print_exc()
        return False


def send_message(receive_id, content, msg_type="text", use_card=False, receive_id_type="open_id", to_mention_open_id=None):
    if use_card:
        msg_id = _send_raw(receive_id, _card(content), "interactive", receive_id_type)
    elif msg_type == "text":
        if to_mention_open_id:
            text_content = json.dumps({
                "text": f"@_user_1 {content}",
                "mentions": [{"key": "@_user_1", "id": {"open_id": to_mention_open_id}}]
            }, ensure_ascii=False)
        else:
            text_content = json.dumps({"text": content}, ensure_ascii=False)
        msg_id = _send_raw(receive_id, text_content, "text", receive_id_type)
    else:
        msg_id = _send_raw(receive_id, content, msg_type, receive_id_type)
    _track_sent(msg_id)
    return msg_id


def send_group_message(text, to_mention_open_id=""):
    """向配置的社交群主动发送文本消息（供agent tool调用）"""
    return send_message(SOCIAL_GROUP_ID, text, receive_id_type="chat_id", to_mention_open_id=to_mention_open_id)


# 向 tools.social 注入 send_message 引用，避免模块重入导致 client 被重置
from tools.social import set_social_config as _set_social_config
_set_social_config(send_func=send_message)


def update_message(message_id, content):
    return _patch_card(message_id, _card(content))


def _upload_image_sync(file_path):
    try:
        with open(file_path, "rb") as f:
            request = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(f).build()
            ).build()
            response = client.im.v1.image.create(request)
            if response.success():
                return response.data.image_key
            print(f"[ERROR] upload image failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] upload image failed {file_path}: {e}")
    return None


def _upload_file_sync(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    file_type = _FILE_TYPE_MAP.get(ext, "stream")
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            request = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder().file_type(file_type).file_name(file_name).file(f).build()
            ).build()
            response = client.im.v1.file.create(request)
            if response.success():
                return response.data.file_key
            print(f"[ERROR] upload file failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] upload file failed {file_path}: {e}")
    return None


def _download_image_sync(message_id, image_key):
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(image_key).type("image").build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        print(f"[ERROR] download image failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] download image failed {image_key}: {e}")
    return None, None


def _download_file_sync(message_id, file_key, resource_type="file"):
    if resource_type == "audio":
        resource_type = "file"
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(resource_type).build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        print(f"[ERROR] download {resource_type} failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] download {resource_type} failed {file_key}: {e}")
    return None, None


def _download_and_save_media(msg_type, content_json, message_id):
    data, filename = None, None
    if msg_type == "image":
        image_key = content_json.get("image_key")
        if image_key and message_id:
            data, filename = _download_image_sync(message_id, image_key)
            if not filename:
                filename = f"{image_key[:16]}.jpg"
    elif msg_type in ("audio", "file", "media"):
        file_key = content_json.get("file_key")
        if file_key and message_id:
            data, filename = _download_file_sync(message_id, file_key, msg_type)
            if not filename:
                filename = file_key[:16]
            if msg_type == "audio" and filename and not filename.endswith(".opus"):
                filename = f"{filename}.opus"
    if data and filename:
        file_path = os.path.join(MEDIA_DIR, os.path.basename(filename))
        with open(file_path, "wb") as f:
            f.write(data)
        return file_path, filename
    return None, None


def _describe_media(msg_type, file_path, filename):
    if msg_type == "image":
        return f"[image: {filename}]\n[Image: source: {file_path}]"
    if msg_type == "audio":
        return f"[audio: {filename}]\n[File: source: {file_path}]"
    if msg_type in ("file", "media"):
        return f"[{msg_type}: {filename}]\n[File: source: {file_path}]"
    return f"[{msg_type}]\n[File: source: {file_path}]"


def _send_local_file(receive_id, file_path, receive_id_type="open_id"):
    if not os.path.isfile(file_path):
        send_message(receive_id, f"⚠️ 文件不存在: {file_path}", receive_id_type=receive_id_type)
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _IMAGE_EXTS:
        image_key = _upload_image_sync(file_path)
        if image_key:
            send_message(receive_id, json.dumps({"image_key": image_key}, ensure_ascii=False), msg_type="image", receive_id_type=receive_id_type)
            return True
    else:
        file_key = _upload_file_sync(file_path)
        if file_key:
            msg_type = "media" if ext in _AUDIO_EXTS or ext in _VIDEO_EXTS else "file"
            send_message(receive_id, json.dumps({"file_key": file_key}, ensure_ascii=False), msg_type=msg_type, receive_id_type=receive_id_type)
            return True
    send_message(receive_id, f"⚠️ 文件发送失败: {os.path.basename(file_path)}", receive_id_type=receive_id_type)
    return False


def _send_generated_files(receive_id, raw_text, receive_id_type="open_id"):
    for file_path in _extract_files(raw_text):
        _send_local_file(receive_id, file_path, receive_id_type)


def _build_user_message(message):
    msg_type = message.message_type
    message_id = message.message_id
    content_json = _parse_json(message.content)
    parts, image_paths = [], []
    if msg_type == "text":
        text = str(content_json.get("text", "") or "").strip()
        if text:
            parts.append(text)
    elif msg_type == "post":
        text, image_keys = _extract_post_content(content_json)
        if text:
            parts.append(text)
        for image_key in image_keys:
            file_path, filename = _download_and_save_media("image", {"image_key": image_key}, message_id)
            if file_path and filename:
                parts.append(_describe_media("image", file_path, filename))
                image_paths.append(file_path)
            else:
                parts.append("[image: download failed]")
    elif msg_type in ("image", "audio", "file", "media"):
        file_path, filename = _download_and_save_media(msg_type, content_json, message_id)
        if file_path and filename:
            parts.append(_describe_media(msg_type, file_path, filename))
            if msg_type == "image":
                image_paths.append(file_path)
        else:
            parts.append(f"[{msg_type}: download failed]")
    elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
        parts.append(_extract_share_card_content(content_json, msg_type))
    else:
        parts.append(_MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))
    return "\n".join([p for p in parts if p]).strip(), image_paths


def _fmt_tool_call(tc):
    name = tc.get('tool_name', '?')
    args = {k: v for k, v in (tc.get('args') or {}).items() if not k.startswith('_')}
    return f"- `{name}`({json.dumps(args, ensure_ascii=False)[:200]})"


def _build_step_detail(resp, tool_calls):
    """从 LLM response + tool_calls 组装单步展开详情（纯函数）。"""
    parts = []
    thinking = (getattr(resp, 'thinking', '') or '').strip() if resp else ''
    if thinking:
        parts.append(f"### 💭 Thinking\n{thinking}")
    if tool_calls:
        parts.append("### 🛠 Tool Calls\n" + "\n".join(_fmt_tool_call(tc) for tc in tool_calls))
    content = _display_text((getattr(resp, 'content', '') or '')).strip() if resp else ''
    if content and content != '...':
        parts.append(f"### 📝 Output\n{content}")
    return "\n\n".join(parts)


class _TaskCard:
    """飞书任务卡片：单卡片持续 patch；每步一个独立折叠面板（header 显示 summary，展开看详情）。"""
    _DETAIL_LIMIT = 8000

    def __init__(self, receive_id, rid_type):
        self.rid, self.rtype = receive_id, rid_type
        self.steps = []          # [(summary, detail), ...]
        self.status = "🤔 思考中..."
        self.final = None
        self.msg_id = None
        self.start_fallback_sent = False
        self.final_fallback_sent = False

    def _step_panel(self, idx, summary, detail):
        detail = detail or "_(无输出)_"
        if len(detail) > self._DETAIL_LIMIT:
            detail = detail[:self._DETAIL_LIMIT] + f"\n\n…(已截断,共 {len(detail)} 字符)"
        return {
            "tag": "collapsible_panel", "expanded": False,
            "header": {"title": {"tag": "plain_text", "content": f"Turn {idx} · {summary}"}},
            "elements": [{"tag": "markdown", "content": detail}],
        }

    def _build(self):
        els = [{"tag": "markdown", "content": f"**{self.status}**"}]
        for i, (s, d) in enumerate(self.steps, 1):
            els.append(self._step_panel(i, s, d))
        if self.final:
            els += [{"tag": "hr"}, {"tag": "markdown", "content": self.final}]
        return _card_raw(els)

    def _push(self):
        card = self._build()
        if self.msg_id:
            ok = _patch_card(self.msg_id, card)
        else:
            self.msg_id = _send_raw(self.rid, card, "interactive", self.rtype)
            ok = bool(self.msg_id)
        return ok

    def _fallback_text(self, text, *, final=False):
        attr = "final_fallback_sent" if final else "start_fallback_sent"
        if getattr(self, attr):
            return
        setattr(self, attr, True)
        send_message(self.rid, text, receive_id_type=self.rtype)

    # ── 公开接口 ──

    def start(self):
        if not self._push():
            self._fallback_text("🤔 思考中...")

    def step(self, summary, detail=""):
        self.steps.append((summary, detail))
        self.status = f"⏳ 工作中 · Turn {len(self.steps)}"
        self._push()

    def done(self, text):
        self.status = "✅ 已完成"
        self.final = text or "_(无文本输出)_"
        if not self._push():
            self._fallback_text(_display_text(text), final=True)

    def fail(self, msg):
        self.status = f"❌ {msg}"
        if not self._push():
            self._fallback_text(f"❌ {msg}", final=True)


def _make_task_hook(card, task_id, on_final):
    """飞书任务 hook：每轮 patch 卡片状态；结束触发 on_final(raw) 处理附件。"""
    def hook(ctx):
        try:
            parent = getattr(ctx.get("self"), "parent", None)
            if getattr(parent, "_fs_active_task_id", None) != task_id:
                return
            if ctx.get('exit_reason'):
                resp = ctx.get('response')
                raw = resp.content if hasattr(resp, 'content') else str(resp)
                on_final(raw)
            elif ctx.get('summary'):
                detail = _build_step_detail(ctx.get('response'), ctx.get('tool_calls') or [])
                card.step(ctx['summary'], detail)
        except Exception as e:
            print(f"[fs hook] error: {e}")
    return hook


class FeishuApp(AgentChatMixin):
    label, source, split_limit = "Feishu", "feishu", 4000

    async def handle_command(self, chat_id, cmd, **ctx):
        parts = (cmd or "").split()
        op = (parts[0] if parts else "").lower()
        if op == "/social":
            from tools.social import tool_send_social_greeting
            mention_name = parts[1] if len(parts) > 1 else ""
            result = tool_send_social_greeting(mention_name=mention_name)
            await self.send_text(chat_id, result, **ctx)
        elif op == "/history":
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
            result = get_chat_history(
                SOCIAL_GROUP_ID,
                page_size=min(n, 50),
                start_time=1678812018,
                end_time=1778812018
            )
            if result.get("code") != 0:
                await self.send_text(chat_id, f"获取历史消息失败: {result.get('msg', repr(result))}", **ctx)
                return
            items = result["data"]["items"]
            if not items:
                await self.send_text(chat_id, "该时间范围内没有消息记录。", **ctx)
                return
            text = f"[群聊历史] 共 {len(items)} 条消息\n群ID: {SOCIAL_GROUP_ID}\n\n"
            for msg in items:
                sender = msg.get("sender", {})
                text += f"[{msg.get('create_time','?')}] {sender.get('sender_type','?')}/{sender.get('id','?')} ({msg.get('msg_type','?')}): {msg.get('body',{}).get('content','')}\n"
            await self.send_text(chat_id, "历史消息已获取，正在分析...", **ctx)
            self.agent.put_task(f"以下是群聊历史消息，请分析：\n{text}", source="feishu")
        elif op == "/agentchat":
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "on":
                _set_agentchat(True)
                await self.send_text(chat_id, "🤖 Agent互聊模式已开启！将自动回复@自己的其他Agent。", **ctx)
                self.agent.put_task("[系统] Agent互聊模式已开启，你可以主动回复群里@你的其他Agent。", source="system")
            elif sub == "off":
                _set_agentchat(False)
                await self.send_text(chat_id, "🤖 Agent互聊模式已关闭。", **ctx)
            else:
                st = "已开启" if _get_agentchat() else "已关闭"
                await self.send_text(chat_id, f"🤖 Agent互聊模式: {st}\n用法: /agentchat on|off", **ctx)
        else:
            await super().handle_command(chat_id, cmd, **ctx)

    async def send_text(self, chat_id, content, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        for part in split_text(content, self.split_limit):
            await asyncio.to_thread(send_message, rid, part, "text", False, receive_id_type)

    async def send_done(self, chat_id, raw_text, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        text = _display_text(raw_text)
        await asyncio.to_thread(send_message, rid, text, "text", False, receive_id_type)
        await asyncio.to_thread(_send_generated_files, rid, raw_text, receive_id_type)

    async def run_agent(self, chat_id, text, *, receive_id=None, receive_id_type="open_id", images=None, **_):
        if self.user_tasks:
            await self.send_text(chat_id, "当前会话已有任务在运行，请等待完成或发送 /stop 后再试。", receive_id=receive_id, receive_id_type=receive_id_type)
            return
        state = {"running": True}
        self.user_tasks[chat_id] = state
        rid = receive_id or chat_id
        task_id = f"{chat_id}_{uuid.uuid4().hex}"
        hook_key = f"fs_{task_id}"
        card = _TaskCard(rid, receive_id_type)
        result = {"raw": None, "sent": False}
        finish_lock = threading.Lock()

        def _finish(raw):
            with finish_lock:
                if result["sent"]:
                    return
                result["raw"] = raw
                result["sent"] = True
            card.done(_display_text(raw))
            _send_generated_files(rid, raw, receive_id_type=receive_id_type)

        try:
            await asyncio.to_thread(card.start)
            if not hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks = {}
            self.agent._turn_end_hooks[hook_key] = _make_task_hook(card, task_id, _finish)
            self.agent._fs_active_task_id = task_id
            dq = self.agent.put_task(f"{FILE_HINT}\n\n{text}", source=self.source, images=images or None)
            start = time.time()
            while state["running"] and not result["sent"]:
                try:
                    item = await asyncio.to_thread(dq.get, True, 1)
                except Q.Empty:
                    item = None
                if item and "done" in item:
                    await asyncio.to_thread(_finish, item.get("done", ""))
                    break
                if time.time() - start > AGENT_TIMEOUT_SEC:
                    self.agent.abort()
                    await asyncio.to_thread(card.fail, "任务超时")
                    break
            if not state["running"] and not result["sent"]:
                self.agent.abort()
                await asyncio.to_thread(card.fail, "已停止")
        except Exception as e:
            traceback.print_exc()
            await asyncio.to_thread(card.fail, f"错误: {e}")
        finally:
            if getattr(self.agent, "_fs_active_task_id", None) == task_id:
                try:
                    delattr(self.agent, "_fs_active_task_id")
                except AttributeError:
                    pass
            if hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks.pop(hook_key, None)
            self.user_tasks.pop(chat_id, None)


def get_app():
    global app
    if app is None:
        app = FeishuApp(get_agent(), user_tasks)
    return app


def _run_async(coro):
    try:
        asyncio.run(coro)
    except Exception:
        traceback.print_exc()


def handle_message(data):
    event, message, sender = data.event, data.event.message, data.event.sender
    message_id = getattr(message, "message_id", "") or ""
    if not _claim_message_once(message_id):
        print(f"忽略重复飞书消息: {message_id}")
        return
    if _is_self_sent(message_id):
        print(f"[fsapp] 跳过自己发的消息 (msg_id={message_id[:20]})")
        return
    open_id = sender.sender_id.open_id
    chat_id = message.chat_id
    sender_type = sender.sender_type
    # 持久化接收者ID，供定时任务等主动推送使用
    _persist_recipient(chat_id, open_id)
    # 权限检查 + Agent 聊天模式
    if not PUBLIC_ACCESS and open_id not in ALLOWED_USERS:
        if not (_get_agentchat() and sender_type == "app"):
            print(f"未授权用户: {open_id}")
            return
    user_input, image_paths = _build_user_message(message)
    # 提取消息中被 @ 的人（群聊中需要 @ 回去）
    # 群聊中有人 @ 了 bot → 回复时 @ 回去
    mention_open_id = open_id if (chat_id and message.mentions) else ""
    if not user_input:
        if chat_id:
            send_message(chat_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}", receive_id_type="chat_id", to_mention_open_id=mention_open_id)
        else:
            send_message(open_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}", to_mention_open_id=mention_open_id)
        return
    print(f"收到消息 [{open_id}] ({message.message_type}, {len(image_paths)} images): {user_input[:200]}")
    receive_id = chat_id or open_id
    receive_id_type = "chat_id" if chat_id else "open_id"
    chat_key = receive_id
    if message.message_type == "text" and user_input.startswith("/"):
        threading.Thread(
            target=_run_async,
            args=(get_app().handle_command(chat_key, user_input, receive_id=receive_id, receive_id_type=receive_id_type, mention_open_id=mention_open_id),),
            daemon=True,
        ).start()
        return
    threading.Thread(
        target=_run_async,
        args=(get_app().run_agent(chat_key, user_input, receive_id=receive_id, receive_id_type=receive_id_type, images=image_paths),),
        daemon=True,
    ).start()


def _initiative_social_timer():
    """后台线程：定时主动在群里找小小怪聊天。直接发消息，不依赖LLM执行code。"""
    from tools.social import start_social_timer
    start_social_timer(stop_event=None)


def main():
    global client, APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH
    APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH = _feishu_config()
    if not APP_ID or not APP_SECRET:
        print(f"错误: 请在 mykey 配置中填写 fs_app_id 和 fs_app_secret\n配置文件: {CONFIG_PATH}", flush=True)
        sys.exit(1)
    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(handle_message).build()
    print("=" * 50 + "\n飞书 Agent 已启动（长连接模式）\n" + f"App ID: {APP_ID}\n配置: {CONFIG_PATH}\n等待消息...\n" + "=" * 50, flush=True)
    if _SOCIAL_ENABLED:
        threading.Thread(target=_initiative_social_timer, daemon=True, name="social_timer").start()
        print("[main] 社交问候已启用", flush=True)
        print(f"[main] 社交定时器: 初始延时{SOCIAL_INITIAL_DELAY}s, 间隔{SOCIAL_INTERVAL}s", flush=True)
    else:
        print("[main] 社交问候已禁用 (sche_tasks/social.json enabled=false)", flush=True)
    threading.Thread(target=_poll_agentchat_loop, daemon=True, name="agentchat_poll").start()
    retry_delay = 5
    while True:
        try:
            client = create_client()
            cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
            print("[main] 飞书长连接已连接", flush=True)
            cli.start()
            retry_delay = 5
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[WARN] 飞书长连接断开或启动失败: {e}", flush=True)
            traceback.print_exc()
        print(f"[INFO] {retry_delay}s 后重连飞书长连接...", flush=True)
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A3Agent Feishu frontend")
    parser.add_argument("--check", action="store_true", help="只检查飞书配置，不启动长连接")
    parser.add_argument("--check-agent", action="store_true", help="检查配置并初始化 Agent/LLM")
    args = parser.parse_args()
    if args.check or args.check_agent:
        print(json.dumps(check_config(init_agent=args.check_agent), ensure_ascii=False, indent=2), flush=True)
    else:
        main()
