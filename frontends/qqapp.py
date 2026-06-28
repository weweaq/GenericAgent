import asyncio, hashlib, os, sys, threading, time
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentmain import GeneraticAgent
from chatapp_common import AgentChatMixin, ensure_single_instance, public_access, redirect_log, require_runtime, \
    split_text
from llmcore import mykeys

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage
except Exception:
    print("Please install qq-botpy to use QQ module: pip install qq-botpy")
    sys.exit(1)

import requests

agent = GeneraticAgent();
agent.verbose = False
APP_ID = str(mykeys.get("qq_app_id", "") or "").strip()
APP_SECRET = str(mykeys.get("qq_app_secret", "") or "").strip()
ALLOWED = {str(x).strip() for x in mykeys.get("qq_allowed_users", []) if str(x).strip()}
PROCESSED_IDS, USER_TASKS = deque(maxlen=1000), {}
SEQ_LOCK, MSG_SEQ = threading.Lock(), 1

# ── 图片处理配置 ──
QQ_MEDIA_DIR = Path(__file__).resolve().parent.parent / "temp" / "qq_media"
QQ_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}


def _next_msg_seq():
    global MSG_SEQ
    with SEQ_LOCK:
        MSG_SEQ += 1
        return MSG_SEQ


def _build_intents():
    try:
        return botpy.Intents(public_messages=True, direct_message=True)
    except Exception:
        intents = botpy.Intents.none() if hasattr(botpy.Intents, "none") else botpy.Intents()
        for attr in ("public_messages", "public_guild_messages", "direct_message", "direct_messages", "c2c_message",
                     "c2c_messages", "group_at_message", "group_at_messages"):
            if hasattr(intents, attr):
                try:
                    setattr(intents, attr, True)
                except Exception:
                    pass
        return intents


# ── 图片附件处理 ──

def _download_attachment(url, filename):
    """下载QQ附件（图片），返回本地文件路径或None"""
    if not url:
        return None
    # 从文件名或URL推断扩展名
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    if ext not in _IMAGE_EXTS:
        # 尝试从URL路径取扩展名
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in _IMAGE_EXTS:
            ext = ".jpg"  # 默认
    save_name = f"qq_{hashlib.md5(url.encode()).hexdigest()[:16]}{ext}"
    save_path = str(QQ_MEDIA_DIR / save_name)
    if os.path.exists(save_path):
        return save_path
    try:
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print(f"[QQ] 图片已下载: {save_path}")
        return save_path
    except Exception as e:
        print(f"[QQ] 下载图片失败 [{filename}]: {e}")
        return None


def _describe_image(file_path, filename):
    """调用Vision API描述图片，返回描述文本"""
    try:
        from memory.vision_api import ask_vision

        desc = ask_vision(file_path, prompt="请详细描述这张图片的内容，包括画面主体、文字、颜色、布局等。", timeout=60)
        if desc and not desc.startswith("Error"):
            return f"[图片: {filename}]\n📷 图片描述: {desc}"
        else:
            return f"[图片: {filename}]\n⚠️ 图片解析失败: {desc}"
    except Exception as e:
        return f"[图片: {filename}]\n⚠️ 图片解析异常: {e}"


def _process_image_attachments(attachments):
    """处理消息中的图片附件，返回描述文本列表（同步函数，在子线程中执行）"""
    if not attachments:
        return []
    descriptions = []
    for att in attachments:
        content_type = getattr(att, "content_type", "") or ""
        filename = getattr(att, "filename", "") or "unknown"
        url = getattr(att, "url", "") or ""
        # 判断是否为图片
        is_image = content_type.startswith("image/") or any(
            filename.lower().endswith(ext) for ext in _IMAGE_EXTS
        )
        if is_image:
            print(f"[QQ] 处理图片: {filename} ({content_type})")
            file_path = _download_attachment(url, filename) if url else None
            if file_path and os.path.exists(file_path):
                desc = _describe_image(file_path, filename)
                descriptions.append(desc)
            else:
                descriptions.append(f"[图片: {filename}]\n⚠️ 图片下载失败")
        else:
            print(f"[QQ] 跳过非图片附件: {content_type} {filename}")
    return descriptions


def _make_bot_class(app):
    class QQBot(botpy.Client):
        def __init__(self):
            super().__init__(intents=_build_intents(), ext_handlers=False)

        async def on_ready(self):
            print(f"[QQ] bot ready: {getattr(getattr(self, 'robot', None), 'name', 'QQBot')}")

        async def on_c2c_message_create(self, message: C2CMessage):
            await app.on_message(message, is_group=False)

        async def on_group_at_message_create(self, message: GroupMessage):
            await app.on_message(message, is_group=True)

        async def on_direct_message_create(self, message):
            await app.on_message(message, is_group=False)

    return QQBot


class QQApp(AgentChatMixin):
    label, source, split_limit = "QQ", "qq", 1500

    def __init__(self):
        super().__init__(agent, USER_TASKS)
        self.client = None

    async def send_text(self, chat_id, content, *, msg_id=None, is_group=False):
        if not self.client:
            return
        api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
        key = "group_openid" if is_group else "openid"
        for part in split_text(content, self.split_limit):
            seq = _next_msg_seq()
            try:
                await api(
                    **{key: chat_id, "msg_type": 2, "markdown": {"content": part}, "msg_id": msg_id, "msg_seq": seq})
            except Exception:
                await api(**{key: chat_id, "msg_type": 0, "content": part, "msg_id": msg_id, "msg_seq": seq})

    async def on_message(self, data, is_group=False):
        try:
            msg_id = getattr(data, "id", None)
            if msg_id in PROCESSED_IDS:
                return
            PROCESSED_IDS.append(msg_id)

            # 提取文本内容 + 附件
            content = (getattr(data, "content", "") or "").strip()
            attachments = getattr(data, "attachments", None) or []

            # 处理图片附件（同步操作在线程中执行）
            image_descs = []
            if attachments:
                image_descs = await asyncio.to_thread(_process_image_attachments, attachments)

            # 组合：文本 + 图片描述
            parts = []
            if content:
                parts.append(content)
            if image_descs:
                parts.extend(image_descs)
            full_content = "\n\n".join(parts).strip()

            # 没有文本也没有图片 → 跳过
            if not full_content:
                print(f"[QQ] 忽略空消息（无文本无图片）: msg_id={msg_id}")
                return

            author = getattr(data, "author", None)
            user_id = str(getattr(author, "member_openid" if is_group else "user_openid", "") or getattr(author, "id",
                                                                                                         "") or "unknown")
            chat_id = str(getattr(data, "group_openid", "") or user_id) if is_group else user_id
            if not public_access(ALLOWED) and user_id not in ALLOWED:
                print(f"[QQ] unauthorized user: {user_id}")
                return
            print(f"[QQ] message from {user_id} ({'group' if is_group else 'c2c'}): {full_content[:300]}")
            if full_content.startswith("/"):
                return await self.handle_command(chat_id, full_content, msg_id=msg_id, is_group=is_group)
            asyncio.create_task(self.run_agent(chat_id, full_content, msg_id=msg_id, is_group=is_group))
        except Exception:
            import traceback
            print("[QQ] handle_message error")
            traceback.print_exc()

    async def start(self):
        self.client = _make_bot_class(self)()
        delay, max_delay = 5, 300
        while True:
            started_at = time.monotonic()
            try:
                print(f"[QQ] bot starting... {time.strftime('%m-%d %H:%M')}")
                await self.client.start(appid=APP_ID, secret=APP_SECRET)
            except Exception as e:
                print(f"[QQ] bot error: {e}")
            if time.monotonic() - started_at >= 60:
                delay = 5
            print(f"[QQ] reconnect in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


if __name__ == "__main__":
    _LOCK_SOCK = ensure_single_instance(19528, "QQ")
    require_runtime(agent, "QQ", qq_app_id=APP_ID, qq_app_secret=APP_SECRET)
    redirect_log(__file__, "qqapp.log", "QQ", ALLOWED)
    threading.Thread(target=agent.run, daemon=True).start()
    asyncio.run(QQApp().start())
