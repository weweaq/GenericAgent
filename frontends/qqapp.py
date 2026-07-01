import asyncio, hashlib, json, os, re, sys, threading, time
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentmain import GeneraticAgent
from chatapp_common import AgentChatMixin, ensure_single_instance, public_access, redirect_log, require_runtime, \
    split_text, FILE_HINT
from llmcore import mykeys

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage
    # Monkey-patch: QQ 平台已将 GROUP_AT_MESSAGE_CREATE 更名为 GROUP_MESSAGE_CREATE，
    # botpy 尚未适配，此处补充解析器
    from botpy.connection import ConnectionState
    if not hasattr(ConnectionState, "parse_group_message_create"):
        def _parse_group_message_create(self, payload):
            _message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
            self._dispatch("group_message_create", _message)
        ConnectionState.parse_group_message_create = _parse_group_message_create
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

# ── 持久化 user_id → 身份名 映射 ──
_USER_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "qq_user_map.json"

def _load_user_map():
    """加载 user_id → 身份名 映射表"""
    try:
        if _USER_MAP_PATH.exists():
            with open(_USER_MAP_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[QQ] 加载用户映射失败: {e}")
    return {}

def _save_user_map(mapping):
    """保存 user_id → 身份名 映射表"""
    try:
        _USER_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_USER_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[QQ] 保存用户映射失败: {e}")

def _resolve_user_name(user_id):
    """根据 user_id 解析显示名；未注册返回 user_id 后 4 位"""
    mapping = _load_user_map()
    if user_id in mapping:
        return mapping[user_id]
    return user_id[-4:] if len(user_id) >= 4 else user_id
_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}


def _next_msg_seq():
    global MSG_SEQ
    with SEQ_LOCK:
        MSG_SEQ += 1
        return MSG_SEQ


def _build_intents():
    try:
        return botpy.Intents(public_guild_messages=True, direct_message=True, public_messages=True)
    except Exception:
        intents = botpy.Intents.none() if hasattr(botpy.Intents, "none") else botpy.Intents()
        for attr in ("public_guild_messages", "direct_message", "public_messages"):
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
    """调用Vision API + DeepFace情绪分析，返回描述文本"""
    import threading
    results = []

    print(f"[QQ DEBUG] _describe_image 开始: {filename}")

    # 🧠 DeepFace情绪分析（本地，带超时保护）
    try:
        from plugins.daily_selfie import process_selfie
        result_container = [None]
        exc_container = [None]

        def _run_deepface():
            try:
                result_container[0] = process_selfie(file_path, silent=True)
            except Exception as e:
                exc_container[0] = e

        t = threading.Thread(target=_run_deepface, daemon=True)
        t.start()
        t.join(timeout=30)

        if t.is_alive():
            print(f"[QQ DEBUG] DeepFace 超时(30s): {filename}")
            results.append("⏰ 情绪分析超时（模型可能正在下载）")
        elif exc_container[0]:
            print(f"[QQ DEBUG] DeepFace 异常: {exc_container[0]}")
            results.append(f"⚠️ 情绪分析失败: {exc_container[0]}")
        else:
            r = result_container[0]
            print(f"[QQ DEBUG] DeepFace 完成: {r[:120] if r else 'None'}")
            results.append(r)
    except Exception as e:
        print(f"[QQ DEBUG] DeepFace 导入失败: {e}")
        results.append(f"⚠️ 情绪分析模块异常: {e}")

    # 📷 Vision API图片描述（云端，带超时保护）
    try:
        from memory.vision_api import ask_vision
        result_container = [None]
        exc_container = [None]

        def _run_vision():
            try:
                result_container[0] = ask_vision(
                    file_path,
                    prompt="请详细描述这张图片的内容，包括画面主体、文字、颜色、布局等。",
                    timeout=60,
                )
            except Exception as e:
                exc_container[0] = e

        t = threading.Thread(target=_run_vision, daemon=True)
        t.start()
        t.join(timeout=90)

        if t.is_alive():
            print(f"[QQ DEBUG] Vision API 超时(90s): {filename}")
            results.append("⏰ 图片描述超时")
        elif exc_container[0]:
            print(f"[QQ DEBUG] Vision API 异常: {exc_container[0]}")
            results.append(f"⚠️ 图片解析异常: {exc_container[0]}")
        else:
            desc = result_container[0]
            print(f"[QQ DEBUG] Vision API 完成: {desc[:120] if desc else 'None'}")
            if desc and not desc.startswith("Error"):
                results.append(f"📷 图片描述: {desc}")
            else:
                results.append(f"⚠️ 图片解析失败: {desc}")
    except Exception as e:
        print(f"[QQ DEBUG] Vision API 导入失败: {e}")
        results.append(f"⚠️ 图片解析模块异常: {e}")

    final = f"[图片: {filename}]\n" + "\n".join(results)
    print(f"[QQ DEBUG] _describe_image 完成: {filename}, 结果长度={len(final)}")
    return final


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
            await app.notify_reboot_complete()

        async def on_c2c_message_create(self, message: C2CMessage):
            await app.on_message(message, is_group=False)

        async def on_group_at_message_create(self, message: GroupMessage):
            await app.on_message(message, is_group=True, is_at=True)

        async def on_group_message_create(self, message: GroupMessage):
            # QQ 平台新事件类型，等价于旧版 GROUP_AT_MESSAGE_CREATE
            await app.on_message(message, is_group=True, is_at=True)

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
        # 过滤技术日志和空占位符 —— 被过滤内容先记日志再丢弃
        raw = (content or "").strip()
        content = raw
        if not content or content == "...":
            print(f"[FILTERED] empty/dots: {raw!r}")
            return
        content = re.sub(r"^\s*\*?\*?LLM Running \(Turn \d+\) \.\.\.\*?\*?\s*$", "", content, flags=re.M).strip()
        # 过滤工具调用技术日志 (🛠️ Tool: `xxx` ... args 块 / 🛠️ xxx(...) 紧凑格式)
        content = re.sub(r"^🛠️ Tool:.*?\n````.*?````\n?", "", content, flags=re.M | re.DOTALL)
        content = re.sub(r"^\s*🛠️\s+.*$", "", content, flags=re.M)
        content = content.strip()
        if not content or content == "...":
            print(f"[FILTERED] only noise after strip: {raw!r} -> {content!r}")
            return
        api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
        key = "group_openid" if is_group else "openid"
        for part in split_text(content, self.split_limit):
            seq = _next_msg_seq()
            print(f"[QQ] reply to {chat_id} ({'group' if is_group else 'c2c'}): {part[:200]}")
            try:
                await api(
                    **{key: chat_id, "msg_type": 2, "markdown": {"content": part}, "msg_id": msg_id, "msg_seq": seq})
            except Exception as e:
                print(f"[QQ] markdown send failed, fallback to text: {e}")
                await api(**{key: chat_id, "msg_type": 0, "content": part, "msg_id": msg_id, "msg_seq": seq})

    async def _handle_face_register(self, data, content, attachments, *, msg_id=None, is_group=False):
        """Handle /facereg <name>: download image attachments → register face"""
        # Extract name
        parts = content.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""

        # Extract chat_id and user_id
        author = getattr(data, "author", None)
        user_id = str(getattr(author, "member_openid" if is_group else "user_openid", "")
                      or getattr(author, "id", "") or "unknown")
        chat_id = str(getattr(data, "group_openid", "") or user_id) if is_group else user_id

        # Parameter validation
        if not name:
            await self.send_text(chat_id, "用法: /facereg 姓名\n请同时发送一张或多张人脸照片",
                                 msg_id=msg_id, is_group=is_group)
            return
        if not attachments:
            await self.send_text(chat_id, "请同时发送人脸照片", msg_id=msg_id, is_group=is_group)
            return

        # Permission check
        if not public_access(ALLOWED) and user_id not in ALLOWED:
            print(f"[QQ] /facereg denied: unauthorized user={user_id}")
            return

        print(f"[QQ] /facereg: name={name}, user={user_id}")

        # Download image attachments (download only, no description)
        image_paths = []
        for att in attachments:
            ct = getattr(att, "content_type", "") or ""
            fname = getattr(att, "filename", "") or "unknown"
            url = getattr(att, "url", "") or ""
            is_image = ct.startswith("image/") or any(
                fname.lower().endswith(ext) for ext in _IMAGE_EXTS
            )
            if is_image and url:
                fp = _download_attachment(url, fname)
                if fp and os.path.exists(fp):
                    image_paths.append(fp)

        if not image_paths:
            await self.send_text(chat_id, "未能下载图片，请重试", msg_id=msg_id, is_group=is_group)
            return

        # Call register function (run in thread to avoid blocking event loop)
        try:
            from plugins.daily_selfie import register_face
            result = await asyncio.to_thread(register_face, name, image_paths)
            # 注册成功后写入持久化映射
            if not result.startswith("❌"):
                mapping = _load_user_map()
                mapping[user_id] = name
                _save_user_map(mapping)
                print(f"[QQ] 用户映射已保存: {user_id} → {name}")
        except Exception as e:
            result = f"❌ 注册失败: {e}"

        await self.send_text(chat_id, result, msg_id=msg_id, is_group=is_group)

    async def run_agent(self, chat_id, text, **ctx):
        """重写父类方法：去掉"思考中"和"还在处理中"等中间状态提示"""
        import queue as Q
        state = {"running": True}
        self.user_tasks[chat_id] = state
        try:
            dq = self.agent.put_task(f"{FILE_HINT}\n\n{text}", source=self.source)
            last_ping = time.time()
            while state["running"]:
                try:
                    item = await asyncio.to_thread(dq.get, True, 3)
                except Q.Empty:
                    if self.agent.is_running and time.time() - last_ping > self.ping_interval:
                        last_ping = time.time()
                    continue
                if "done" in item:
                    await self.send_done(chat_id, item.get("done", ""), **ctx)
                    break
            if not state["running"]:
                await self.send_text(chat_id, "⏹️ 已停止", **ctx)
        except Exception as e:
            import traceback
            print(f"[{self.label}] run_agent error: {e}")
            traceback.print_exc()
            await self.send_text(chat_id, f"❌ 错误: {e}", **ctx)
        finally:
            self.user_tasks.pop(chat_id, None)

    async def on_message(self, data, is_group=False, is_at=False):
        try:
            msg_id = getattr(data, "id", None)
            if msg_id in PROCESSED_IDS:
                return
            PROCESSED_IDS.append(msg_id)

            # 提取文本内容 + 附件
            content = (getattr(data, "content", "") or "").strip()
            attachments = getattr(data, "attachments", None) or []
            print(f"[QQ DEBUG] attachments raw: {[(getattr(a,'content_type','?'), getattr(a,'url','?')[:60] if getattr(a,'url','') else 'None', getattr(a,'filename','?')) for a in attachments]}")

            # ── /facereg <name> ──
            if content.startswith("/facereg"):
                await self._handle_face_register(data, content, attachments, msg_id=msg_id, is_group=is_group)
                return

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
            if author:
                # 调试：打印 author 所有属性，用于确定跨场景统一 ID
                author_fields = {k: str(getattr(author, k, ""))[:80] for k in dir(author) if not k.startswith("_")}
                print(f"[QQ DEBUG] author fields ({'group' if is_group else 'c2c'}, at={is_at}): {author_fields}")
            user_id = str(getattr(author, "member_openid" if is_group else "user_openid", "") or getattr(author, "id",
                                                                                                         "") or "unknown")
            chat_id = str(getattr(data, "group_openid", "") or user_id) if is_group else user_id
            if not public_access(ALLOWED) and user_id not in ALLOWED:
                print(f"[QQ] unauthorized user: {user_id}")
                return
            print(f"[QQ] message from {user_id} ({'group' if is_group else 'c2c'}): {full_content[:300]}")
            # 发送者标识，让 LLM 能区分不同说话人
            name = _resolve_user_name(user_id)
            suffix = " @我" if is_at else ""
            full_content = f"[{name}{suffix}]: {full_content}"
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
    redirect_log(__file__, "qqapp-{date}.log", "QQ", ALLOWED)
    threading.Thread(target=agent.run, daemon=True).start()
    asyncio.run(QQApp().start())
