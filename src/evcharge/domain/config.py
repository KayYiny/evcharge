# -*- coding: utf-8 -*-
"""静态配置元数据（evcharge.domain.config）。

只放**纯数据与文案**，不含任何 IO：

- 默认标题 / 人设文案 / 刷新间隔等默认值；
- 电价档的中文标签与默认站点身份；
- 车牌识别视觉模型的填写提示。

这样表现层可以直接引用这些常量做展示，而 ``infrastructure.settings``
只负责「读写 JSON」这一件事，两边不必互相 import。
"""

from __future__ import annotations

from typing import Dict

from evcharge.domain.tariff import FUJIAN_RESIDENTIAL, STATE_GRID_HIGHWAY

# ---------- 默认值 ----------
DEFAULT_APP_TITLE = "国网高速服务区充电监控与计费系统"
DEFAULT_PERSONA_NAME = "小充"
DEFAULT_PERSONA_PROMPT = (
    "你是国网高速服务区充电站的值班管理员助手。"
    "你熟悉分时电价、直流快充、车型电池和桩位运营。"
    "回答时先给结论，再列数据依据，最后给出 2~3 条可执行的运营建议；"
    "只基于提供的数据，不要编造；用中文分点、简洁。"
)
DEFAULT_REFRESH_INTERVAL_S = 2.0
REFRESH_OPTIONS = (1.0, 2.0, 3.0, 5.0)

# ---------- 电价档 ----------
TARIFF_PROFILES = {
    "state_grid": STATE_GRID_HIGHWAY,
    "fujian": FUJIAN_RESIDENTIAL,
}
TARIFF_LABELS = {
    "state_grid": "国网高速服务区（谷 0.3756 / 平 0.6050 / 峰 0.8873 + 服务费 0.70）",
    "fujian": "福建居民合表（谷 0.333 / 平 0.533 / 峰 0.843 + 服务费 0.40）",
}
TARIFF_SHORT_LABELS = {
    "state_grid": "国网高速服务区",
    "fujian": "福建居民合表",
}
TARIFF_IDENTITY_DEFAULTS: Dict[str, Dict[str, str]] = {
    "state_grid": {
        "site_code": "G15",
        "site_name": "长深高速 · 江苏段服务区",
        "operator_name": "国家电网",
    },
    "fujian": {
        "site_code": "闽A",
        "site_name": "福州 · 居民合表社区充电站",
        "operator_name": "国网福建",
    },
}

# ---------- 车牌识别 ----------
VISION_MODEL_HINT = "例如 doubao-1.5-vision-pro-32k-250115、gpt-4o-mini、qwen-vl-plus"


def identity_defaults(tariff_profile: str) -> Dict[str, str]:
    """返回电价档对应的默认站点身份。"""
    return dict(
        TARIFF_IDENTITY_DEFAULTS.get(
            tariff_profile, TARIFF_IDENTITY_DEFAULTS["state_grid"]
        )
    )


__all__ = [
    "DEFAULT_APP_TITLE",
    "DEFAULT_PERSONA_NAME",
    "DEFAULT_PERSONA_PROMPT",
    "DEFAULT_REFRESH_INTERVAL_S",
    "REFRESH_OPTIONS",
    "TARIFF_PROFILES",
    "TARIFF_LABELS",
    "TARIFF_SHORT_LABELS",
    "TARIFF_IDENTITY_DEFAULTS",
    "VISION_MODEL_HINT",
    "identity_defaults",
]
