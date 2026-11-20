# -*- coding: utf-8 -*-
"""通用 OpenAI 兼容 AI 客户端（evcharge.application.assistant）。

不绑定具体厂商，只依赖标准的 `/chat/completions` 协议：
    POST {base_url}/chat/completions
    Authorization: Bearer {api_key}
    {"model": ..., "messages": [...], "temperature": ...}

数据由 analytics.report 生成（本地小总结或全部订单 CSV）；这里只负责把
“数据文本 + 管理员问题 + 可编辑人设”发给大模型，并解析返回内容。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from evcharge.domain.app_settings import AppSettings
from evcharge.domain.config import DEFAULT_PERSONA_NAME, DEFAULT_PERSONA_PROMPT


class AIError(RuntimeError):
    """AI 接口调用失败（未配置/网络/HTTP/返回格式）。"""


def system_prompt(settings: AppSettings) -> str:
    """构造带人设名字的系统提示词。"""
    name = (settings.persona_name or DEFAULT_PERSONA_NAME).strip()
    prompt = (settings.persona_prompt or DEFAULT_PERSONA_PROMPT).strip()
    return f"你的名字是「{name}」。{prompt}"


def _extract_content(data: Dict[str, Any]) -> str:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("接口返回格式不符合 OpenAI 兼容格式（缺少 choices/message）") from exc

    content = message.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        content = "".join(parts)
    if not content:
        content = message.get("reasoning_content") or ""
    text = str(content).strip()
    if not text:
        raise AIError("模型返回内容为空")
    return text


def chat(
    settings: AppSettings,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    timeout_s: Optional[int] = None,
) -> str:
    """调用 OpenAI 兼容 /chat/completions，返回文本内容。"""
    if not settings.configured:
        raise AIError("尚未配置 AI 接口，请先在「设置」页填写厂商 URL、API Key 和模型名称")
    url = settings.chat_url
    if not url:
        raise AIError("厂商接口 URL 为空")

    payload: Dict[str, Any] = {
        "model": settings.model.strip(),
        "messages": messages,
        "temperature": settings.temperature if temperature is None else temperature,
    }
    headers = {
        "Authorization": f"Bearer {settings.api_key.strip()}",
        "Content-Type": "application/json",
    }
    timeout = timeout_s or settings.timeout_s

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        body = (exc.response.text or "")[:300] if exc.response is not None else ""
        raise AIError(f"接口返回 HTTP {status}：{body or exc}") from exc
    except requests.RequestException as exc:
        raise AIError(f"网络请求失败：{exc}") from exc
    except ValueError as exc:
        raise AIError(f"接口返回不是合法 JSON：{exc}") from exc

    return _extract_content(data)


def test_connection(settings: AppSettings) -> str:
    """用一条极短请求测试配置是否可用。"""
    return chat(
        settings,
        [
            {"role": "system", "content": system_prompt(settings)},
            {"role": "user", "content": "请只回复四个字：连接成功"},
        ],
        temperature=0.0,
        timeout_s=min(settings.timeout_s, 30),
    )


def answer_question(
    settings: AppSettings,
    data_text: str,
    question: str,
    data_kind: str = "summary",
) -> str:
    """把数据和管理员问题一起发给大模型。

    data_kind="raw"：data_text 是全部订单原始数据；
    data_kind="summary"：data_text 是本地汇总。
    """
    question = (question or "").strip()
    if not question:
        raise AIError("问题不能为空")

    if data_text.strip():
        if data_kind == "raw":
            data_title = "【本站点全部订单原始数据（逐条 CSV）】"
            data_instruction = (
                "这是原始订单数据，不是预先聚合的结论；请先自行统计/归纳再回答，"
                "不要编造不存在的数据；如果某个指标缺失，请明确说明。"
            )
        else:
            data_title = "【本站点数据汇总（本地计算，未经过大模型）】"
            data_instruction = (
                "请严格基于汇总中的数字回答，不要编造不存在的数据；"
                "如果某个指标缺失，请明确说明。"
            )
        data_block = f"{data_title}\n{data_text}\n\n"
    else:
        data_block = "【说明】本次未向模型提供本站点数据。\n\n"
        data_instruction = (
            "由于没有具体数据，只能给出通用的运营分析框架，"
            "并明确说明缺少哪些数据、建议管理员补充什么信息。"
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt(settings) + "\n" + data_instruction,
        },
        {
            "role": "user",
            "content": (
                data_block
                + "【管理员问题】\n"
                + f"{question}\n\n"
                + "回答要求：先给结论，再列 2~4 条数据依据，最后给 2~3 条可执行建议。"
                + "用中文分点回答，不要重复整份汇总。"
            ),
        },
    ]
    return chat(settings, messages)
