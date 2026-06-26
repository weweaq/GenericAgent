# Vision API SOP

## ⚠️ 前置规则（必须遵守）

1. **先枚举窗口**：调用 vision 前必须先用 `pygetwindow` 枚举窗口标题，确认目标窗口存在且已激活到前台。窗口不存在就不要截图。
2. **🚫 禁止全屏截图**：必须先利用ljqCtrl截取窗口区域。能截局部（如标题栏）就不截整窗口，能截窗口就绝不全屏。全屏截图在任何场景下都不允许。
3. **能不用 vision 就不用**：如果窗口标题/本地 OCR（`ocr_utils.py`）能获取所需信息，就不要调用 vision API，省 token 且更可靠。Vision 是最后手段。

## 快速用法

```python
from vision_api import ask_vision
result = ask_vision(image, prompt="描述图片内容", timeout=60, max_pixels=1_440_000)
# image: 文件路径(str/Path) 或 PIL Image
# 返回 str：成功为模型回复，失败为 'Error: ...'
```

## 已探测成功的配置 (2026-05-09)

- **后端**: ModelScope 免费推理
- **端点**: `https://api-inference.modelscope.cn/v1/chat/completions`
- **模型**: `Qwen/Qwen3-VL-235B-A22B-Instruct`
- **认证**: token 已内置在 `vision_sop.py` 中
- **前提**: 必须绑定阿里云账号（在 ModelScope token 页面操作）

## 初次构建/重建

1. 直接使用 `memory/vision_sop.py`（已内置 ModelScope 配置，自包含）
2. 若 token 失效：去 `https://modelscope.cn/my/myaccesstoken` 更新 token，替换 `vision_sop.py` 中的 `_MODELSCOPE_API_KEY`
