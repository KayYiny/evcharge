# -*- coding: utf-8 -*-
"""车牌识别（evcharge.infrastructure.plate_recognition）。

上电前让用户拍照/选图，把识别出的车牌直接用作本次会话的车牌号，
替代默认的随机车牌。

识别走**和 AI 报告完全相同的 OpenAI 兼容接口**（``/chat/completions``）：
同一个 ``base_url`` 与 ``api_key``，只是这里把图片以 data URI 形式塞进多模态
消息里，让视觉模型只回一个车牌号。所以设置页只需要维护一套 AI 配置。

需要该模型支持图片输入（豆包视觉 / GPT-4o / Qwen-VL 等）；
建议在设置页填「视觉模型」，留空则复用对话模型。

失败一律抛 :class:`PlateRecognitionError`，并带上服务商原始返回片段，
方便对照排查（例如模型不支持图片、Key 无权限）。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Tuple

import requests

from evcharge.application.errors import PlateRecognitionError

# 支持 jpg/png/bmp/webp，最大 8MB（多数多模态接口的通用上限）
MAX_IMAGE_BYTES = 8 * 1024 * 1024
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

PLATE_PROMPT = (
    "这是一张车辆照片。请只输出图片中的车牌号码，"
    "不要输出任何解释、标点或换行。"
    "如果图片中没有车牌，只回复四个字：未识别到。"
)

# 中国车牌：省份简称 + 发牌机关字母 + 5~6 位字母数字（含新能源 6 位）
_PROVINCES = "京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新"
_PLATE_RE = re.compile(rf"[{_PROVINCES}][A-Z][A-Z0-9]{{5,6}}")


# 异常定义在应用层（见 evcharge.application.errors），这里 import 后即可直接抛出，
# 依赖方向保持 infrastructure → application。


# ----------------------------------------------------------------------
# 文本清洗
# ----------------------------------------------------------------------
def format_plate(raw: str) -> str:
    """把 ``闽DG6592`` 格式化成 ``闽D·G6592``（与模拟车牌的展示风格一致）。"""
    text = re.sub(r"[\s·.\-]+", "", str(raw or "").upper())
    if len(text) < 3:
        return text
    return f"{text[0]}{text[1]}·{text[2:]}"


def normalize_plate(text: str) -> str:
    """把模型输出清洗成标准车牌文本（找不到车牌样式则返回空串）。

    处理常见噪声：空格、``·``、小写字母、``O/0`` 与 ``I/1`` 混淆。
    """
    if not text:
        return ""
    raw = str(text).upper()
    raw = raw.replace("·", "").replace("．", "").replace(".", "")
    raw = re.sub(r"[\s\-_—,，。:：;；'\"、]+", "", raw)
    for bad, good in (("O", "0"), ("I", "1"), ("Ｚ", "Z"), ("０", "0"), ("１", "1")):
        raw = raw.replace(bad, good)

    match = _PLATE_RE.search(raw)
    return format_plate(match.group(0)) if match else ""


def is_plate_like(text: str) -> bool:
    return bool(normalize_plate(text))


# ----------------------------------------------------------------------
# 图片读取
# ----------------------------------------------------------------------
def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".bmp":
        return "image/bmp"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _load_image_base64(image_path: str | Path) -> Tuple[str, str]:
    """返回 ``(base64, mime)``；图片不合法时抛 :class:`PlateRecognitionError`。"""
    path = Path(image_path)
    if not path.exists():
        raise PlateRecognitionError(f"图片不存在：{path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise PlateRecognitionError(
            f"不支持的图片格式：{path.suffix}（支持 jpg/png/bmp/webp）"
        )
    size = path.stat().st_size
    if size <= 0:
        raise PlateRecognitionError("图片是空文件")
    if size > MAX_IMAGE_BYTES:
        raise PlateRecognitionError(
            f"图片太大（{size / 1024 / 1024:.1f}MB），请压缩到 8MB 以内"
        )
    return base64.b64encode(path.read_bytes()).decode("utf-8"), _image_mime(path)


def _snippet(text: str, limit: int = 300) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


# ----------------------------------------------------------------------
# 调用 OpenAI 兼容视觉接口
# ----------------------------------------------------------------------
def _chat_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _extract_text(data: dict) -> str:
    """从 /chat/completions 返回里取出文本内容（兼容 str 与分段两种 content）。"""
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 少数服务商返回分段结构
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def recognize_plate(image_path: str | Path, settings) -> Tuple[str, str]:
    """识别图片里的车牌。

    成功返回 ``(车牌号, 模型原始文本)``；失败抛 :class:`PlateRecognitionError`。
    ``settings`` 是 :class:`evcharge.infrastructure.settings.AppSettings`，
    复用其中的 ``base_url`` / ``api_key`` / ``effective_vision_model``。
    """
    base_url = (getattr(settings, "base_url", "") or "").strip()
    api_key = (getattr(settings, "api_key", "") or "").strip()
    if not (base_url and api_key):
        raise PlateRecognitionError(
            "未配置 AI 接口（URL + API Key），请先到「设置 → AI 服务」填写"
        )

    model = (
        getattr(settings, "effective_vision_model", "")
        or getattr(settings, "model", "")
        or ""
    ).strip()
    if not model:
        raise PlateRecognitionError("未配置视觉模型名称，请到「设置 → AI 服务」填写")

    image_b64, mime = _load_image_base64(image_path)
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PLATE_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout_s = int(getattr(settings, "timeout_s", 60) or 60)

    try:
        response = requests.post(
            _chat_url(base_url), headers=headers, json=payload, timeout=timeout_s
        )
    except requests.RequestException as exc:
        raise PlateRecognitionError(f"请求视觉模型失败：{exc}") from exc

    if response.status_code >= 400:
        raise PlateRecognitionError(
            f"视觉模型返回 HTTP {response.status_code}：{_snippet(response.text)}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise PlateRecognitionError(
            f"视觉模型返回的不是 JSON：{_snippet(response.text)}"
        ) from exc

    raw = _extract_text(data)
    plate = normalize_plate(raw)
    if not plate:
        hint = ""
        if raw and _has_cjk(raw):
            hint = f"（模型回复：{_snippet(raw, 60)}）"
        raise PlateRecognitionError(
            f"没有识别到车牌：请换一张更清晰、车牌正对镜头的照片再试{hint}"
        )
    return plate, raw


def recognition_available(settings) -> Tuple[bool, str]:
    """返回 ``(是否可用, 提示文本)``，供界面做前置校验。"""
    if not (getattr(settings, "base_url", "") or "").strip():
        return False, "未配置 AI 接口 URL（设置 → AI 服务）"
    if not (getattr(settings, "api_key", "") or "").strip():
        return False, "未配置 AI API Key（设置 → AI 服务）"
    if not (getattr(settings, "effective_vision_model", "") or "").strip():
        return False, "未配置视觉模型（设置 → AI 服务）"
    return True, ""


__all__ = [
    "PlateRecognitionError",
    "recognize_plate",
    "normalize_plate",
    "format_plate",
    "is_plate_like",
    "recognition_available",
    "SUPPORTED_SUFFIXES",
]
