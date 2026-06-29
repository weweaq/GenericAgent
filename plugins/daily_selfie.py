"""
📸 Daily Selfie — 海绵宝宝每日自拍情绪&人脸识别模块

功能：
  1. process_selfie(image_path) — 分析一张自拍（情绪+人脸识别）
  2. register_face(name, image_paths) — 注册人脸到图库
  3. get_emotion_stats(days=7) — 查询近期情绪统计

用法（用户发消息）：
  - "[图片]" → 自动调用 process_selfie()
  - "注册人脸 小香" + [图片] → 调用 register_face()
  - "情绪周报" → 调用 get_emotion_stats()
"""

import os
import csv
import shutil
import json
from datetime import datetime
from typing import Optional

import numpy as np

# ── 路径配置 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELFIE_DIR = os.path.join(PROJECT_ROOT, "daily_selfies")     # 自拍原图存档
FACE_DB_DIR = os.path.join(PROJECT_ROOT, "face_db")           # 人脸注册库
LOG_PATH = os.path.join(PROJECT_ROOT, "emotion_log.csv")      # 分析记录

# 确保目录存在
os.makedirs(SELFIE_DIR, exist_ok=True)
os.makedirs(FACE_DB_DIR, exist_ok=True)

# ── CSV 文件头 ────────────────────────────────────────────
CSV_HEADER = [
    "timestamp", "identity", "emotion", "emotion_confidence",
    "age", "gender", "race", "image_path"
]

if not os.path.exists(LOG_PATH):
    with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)


# ═══════════════════════════════════════════════════════════
#  🧠  核心函数
# ═══════════════════════════════════════════════════════════

def process_selfie(
    image_path: str,
    silent: bool = True,
) -> str:
    """
    分析一张自拍图片，返回给用户看的文字报告。

    参数:
        image_path: 图片文件路径
        silent: 是否静默（减少DeepFace日志）

    返回:
        格式化的结果字符串
    """
    # 1️⃣ 存档图片
    timestamp = datetime.now()
    stem = timestamp.strftime("%Y-%m-%d_%H%M%S")
    ext = os.path.splitext(image_path)[1] or ".jpg"
    archive_name = f"{stem}{ext}"
    archive_path = os.path.join(SELFIE_DIR, archive_name)
    try:
        shutil.copy2(image_path, archive_path)
    except Exception as e:
        archive_path = image_path  # 复制失败就用原路径
        print(f"[daily_selfie] 存档失败: {e}")

    # 2️⃣ 导入DeepFace（延迟导入，避免污染启动）
    from deepface import DeepFace

    result_lines = []
    emotion_result = None
    identity_result = None

    # 3️⃣ 情绪分析（analyze）
    try:
        objs = DeepFace.analyze(
            img_path=image_path,
            actions=["emotion", "age", "gender", "race"],
            enforce_detection=False,
            silent=silent,
        )
        # analyze 返回 list[dict]
        if isinstance(objs, list) and len(objs) > 0:
            obj = objs[0]
        else:
            obj = objs

        emotion = obj.get("dominant_emotion", "unknown")
        emotion_confidence = obj.get("emotion", {}).get(emotion, 0)
        age = obj.get("age", "?")
        gender = obj.get("dominant_gender", "?")
        race = obj.get("dominant_race", "?")

        emotion_emoji = {
            "happy": "😊", "sad": "😢", "angry": "😠",
            "fear": "😨", "surprise": "😲", "disgust": "🤢",
            "neutral": "😐",
        }.get(emotion, "🤔")

        result_lines.append(
            f"{emotion_emoji} 情绪: **{emotion}** ({emotion_confidence:.1f}%)"
        )
        result_lines.append(f"👤 年龄估计: {age}岁 | 性别: {gender} | 种族: {race}")

        emotion_result = {
            "emotion": emotion,
            "confidence": round(emotion_confidence, 1),
            "age": age, "gender": gender, "race": race,
        }

    except Exception as e:
        result_lines.append(f"⚠️ 情绪分析失败（可能没检出人脸）: {e}")
        emotion_result = {"emotion": "error", "confidence": 0,
                         "age": "?", "gender": "?", "race": "?"}

    # 4️⃣ 人脸识别（find）
    identity = "unknown"
    try:
        df_list = DeepFace.find(
            img_path=image_path,
            db_path=FACE_DB_DIR,
            enforce_detection=False,
            silent=silent,
            refresh_database=True,
        )

        if df_list and len(df_list) > 0 and not df_list[0].empty:
            top_match = df_list[0].iloc[0]
            identity_path = top_match.get("identity", "")
            distance = top_match.get("distance", 1.0)
            # 从路径提取人名（face_db/NAME/xxx.jpg）
            rel_path = os.path.relpath(str(identity_path), FACE_DB_DIR)
            identity = rel_path.split(os.sep)[0] if os.sep in rel_path else "someone"
            similarity = max(0, round((1 - float(distance)) * 100, 1))

            result_lines.append(
                f"🔍 识别为: **{identity}** (相似度 {similarity}%)"
            )
            identity_result = {"identity": identity, "similarity": similarity}
        else:
            result_lines.append("👤 未在人脸库中找到匹配（新面孔？）")
            identity_result = {"identity": "unknown", "similarity": 0}

    except Exception as e:
        result_lines.append(f"⚠️ 人脸识别跳过（库为空或出错）: {e}")
        identity_result = {"identity": "unknown", "similarity": 0}

    # 5️⃣ 写入 CSV 持久化
    _append_log(
        timestamp=timestamp.isoformat(),
        identity=identity,
        emotion=emotion_result.get("emotion", "?"),
        confidence=emotion_result.get("confidence", 0),
        age=emotion_result.get("age", "?"),
        gender=emotion_result.get("gender", "?"),
        race=emotion_result.get("race", "?"),
        image_path=archive_path,
    )

    # 6️⃣ 组装返回消息
    header = f"📸 **海绵宝宝自拍分析** ({timestamp.strftime('%H:%M')})\n" + "─" * 25 + "\n"
    body = "\n".join(result_lines)
    footer = (
        "\n" + "─" * 25 + f"\n📝 记录已保存到 emotion_log.csv"
    )

    return header + body + footer


def register_face(
    name: str,
    image_paths: list[str],
) -> str:
    """
    注册一个人脸到图库。

    参数:
        name: 人名/昵称
        image_paths: 1张或多张不同角度的照片路径

    返回:
        注册结果文字
    """
    person_dir = os.path.join(FACE_DB_DIR, name)
    os.makedirs(person_dir, exist_ok=True)

    saved = 0
    for i, img_path in enumerate(image_paths, 1):
        if not os.path.isfile(img_path):
            continue
        ext = os.path.splitext(img_path)[1] or ".jpg"
        dst = os.path.join(person_dir, f"register_{i:02d}{ext}")
        shutil.copy2(img_path, dst)
        saved += 1

    if saved > 0:
        return (
            f"✅ **人脸注册成功！**\n"
            f"   姓名: {name}\n"
            f"   照片: {saved} 张\n"
            f"   目录: face_db/{name}/\n\n"
            f"下次拍自拍时我就能认出你啦～ 🧽✨"
        )
    else:
        return "❌ 注册失败：没有有效的图片文件"


def get_emotion_stats(days: int = 7) -> str:
    """
    查询最近几天的情绪统计。

    参数:
        days: 查询天数（默认7天）

    返回:
        统计报表文字
    """
    if not os.path.exists(LOG_PATH):
        return "📊 还没有记录数据哦，先拍张自拍吧～"

    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)

    records = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts >= cutoff:
                    records.append(row)
            except (ValueError, KeyError):
                continue

    if not records:
        return f"📊 最近{days}天还没有记录～"

    # 统计情绪分布
    emotion_count = {}
    for r in records:
        e = r.get("emotion", "unknown")
        emotion_count[e] = emotion_count.get(e, 0) + 1

    total = len(records)
    lines = [f"📊 **最近{days}天情绪报告** (共{total}条记录)\n"]

    # 按出现次数排序
    sorted_emotions = sorted(emotion_count.items(), key=lambda x: -x[1])
    for emotion, count in sorted_emotions:
        pct = count / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"  {emotion:10s} {bar} {pct:.0f}% ({count}次)")

    # 最常出现的情绪
    top_emotion = sorted_emotions[0][0]
    emoji_map = {
        "happy": "😊", "sad": "😢", "angry": "😠",
        "fear": "😨", "surprise": "😲", "disgust": "🤢",
        "neutral": "😐",
    }
    emoji = emoji_map.get(top_emotion, "🤔")
    lines.append(f"\n✨ 这周最常出现的情绪: {emoji} **{top_emotion}**")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  🛠️  内部工具函数
# ═══════════════════════════════════════════════════════════

def _append_log(
    timestamp: str,
    identity: str,
    emotion: str,
    confidence: float,
    age,
    gender: str,
    race: str,
    image_path: str,
):
    """线程安全地追加一条记录到 CSV"""
    import threading
    _lock = threading.Lock()
    with _lock:
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp, identity, emotion, confidence,
                age, gender, race, image_path,
            ])


def list_face_db() -> list[str]:
    """列出人脸库中所有已注册的人名"""
    if not os.path.isdir(FACE_DB_DIR):
        return []
    return [
        d for d in os.listdir(FACE_DB_DIR)
        if os.path.isdir(os.path.join(FACE_DB_DIR, d))
    ]


# ═══════════════════════════════════════════════════════════
#  🧪  命令行测试（python -m plugins.daily_selfie <图片路径>）
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        img = sys.argv[1]
        if os.path.isfile(img):
            print(process_selfie(img, silent=False))
        else:
            print(f"❌ 文件不存在: {img}")
    else:
        print("📸 Daily Selfie 模块")
        print(f"   自拍目录: {SELFIE_DIR}")
        print(f"   人脸库:   {FACE_DB_DIR}")
        print(f"   记录文件: {LOG_PATH}")
        print(f"\n   已注册人脸: {list_face_db()}")
