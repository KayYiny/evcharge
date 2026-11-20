# -*- coding: utf-8 -*-
"""应用设置模型（evcharge.domain.app_settings）。

纯数据结构：不读文件、不联网，只有字段与派生属性。
持久化由 ``infrastructure.settings`` 负责（默认存数据库），
这样表现层可以放心引用它做类型标注。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from evcharge.domain import config
from evcharge.domain.tariff import STATE_GRID_HIGHWAY, TariffProfile

DEFAULT_APP_TITLE = config.DEFAULT_APP_TITLE
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_PERSONA_NAME = config.DEFAULT_PERSONA_NAME
DEFAULT_PERSONA_PROMPT = config.DEFAULT_PERSONA_PROMPT
DEFAULT_REFRESH_INTERVAL_S = config.DEFAULT_REFRESH_INTERVAL_S
TARIFF_PROFILES = config.TARIFF_PROFILES
TARIFF_LABELS = config.TARIFF_LABELS
TARIFF_SHORT_LABELS = config.TARIFF_SHORT_LABELS
identity_defaults = config.identity_defaults


@dataclass
class AppSettings:
    """站点 + AI 的统一设置。"""

    # 站点/界面
    app_title: str = DEFAULT_APP_TITLE
    # 站点身份（留空则按电价档回落到 TARIFF_IDENTITY_DEFAULTS）
    site_code: str = ""
    site_name: str = ""
    operator_name: str = ""
    tariff_profile: str = "state_grid"
    service_fee: Optional[float] = None  # None 表示使用电价档默认服务费
    refresh_interval_s: float = DEFAULT_REFRESH_INTERVAL_S
    # True：本地小总结后把总结发给模型；False：不汇总，把全部订单原始数据发给模型
    use_local_summary: bool = False

    # AI
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    persona_name: str = DEFAULT_PERSONA_NAME
    persona_prompt: str = DEFAULT_PERSONA_PROMPT
    temperature: float = 0.4
    timeout_s: int = 60

    # 视觉模型（车牌识别用，复用上面的 base_url / api_key）
    # 留空则复用对话模型 model（该模型需支持图片输入）
    vision_model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.api_key.strip() and self.model.strip())

    @property
    def chat_url(self) -> str:
        """把厂商基础地址归一化成 /chat/completions 完整地址。"""
        url = (self.base_url or "").strip().rstrip("/")
        if not url:
            return ""
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    @property
    def effective_vision_model(self) -> str:
        """车牌识别实际使用的模型：优先 vision_model，留空复用对话模型。"""
        return (self.vision_model or "").strip() or self.model.strip()

    @property
    def vision_configured(self) -> bool:
        """车牌识别是否可用（与 AI 报告共用 URL / API Key / 模型）。"""
        return bool(
            self.base_url.strip() and self.api_key.strip()
            and self.effective_vision_model
        )

    @property
    def tariff_profile_obj(self) -> TariffProfile:
        return TARIFF_PROFILES.get(self.tariff_profile, STATE_GRID_HIGHWAY)

    @property
    def effective_service_fee(self) -> float:
        if self.service_fee is not None:
            return float(self.service_fee)
        return self.tariff_profile_obj.service_fee

    # ---------- 站点身份（留空自动回落到电价档默认值） ----------
    @property
    def resolved_site_code(self) -> str:
        return (self.site_code or "").strip() or identity_defaults(self.tariff_profile)[
            "site_code"
        ]

    @property
    def resolved_site_name(self) -> str:
        return (self.site_name or "").strip() or identity_defaults(self.tariff_profile)[
            "site_name"
        ]

    @property
    def resolved_operator_name(self) -> str:
        return (self.operator_name or "").strip() or identity_defaults(
            self.tariff_profile
        )["operator_name"]

    @property
    def tariff_short_label(self) -> str:
        return TARIFF_SHORT_LABELS.get(self.tariff_profile, "国网高速服务区")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppSettings":
        defaults = cls()

        raw_fee = data.get("service_fee")
        service_fee: Optional[float]
        if raw_fee in (None, ""):
            service_fee = None
        else:
            try:
                service_fee = float(raw_fee)
            except (TypeError, ValueError):
                service_fee = None

        try:
            refresh = float(data.get("refresh_interval_s", defaults.refresh_interval_s))
        except (TypeError, ValueError):
            refresh = defaults.refresh_interval_s
        refresh = max(1.0, min(10.0, refresh))

        tariff = str(data.get("tariff_profile") or defaults.tariff_profile)
        if tariff not in TARIFF_PROFILES:
            tariff = defaults.tariff_profile

        # 兼容上一版字段：send_local_summary=False 曾表示“不发送汇总”，
        # 但按最新语义，关闭本地汇总应发送全部原始数据。这里仅做配置迁移：
        # use_local_summary 缺失时读取旧的 send_local_summary。
        use_summary = data.get("use_local_summary")
        if use_summary is None:
            use_summary = data.get("send_local_summary", defaults.use_local_summary)

        raw_temp = data.get("temperature")
        temperature = (
            defaults.temperature if raw_temp in (None, "") else float(raw_temp)
        )
        raw_timeout = data.get("timeout_s")
        timeout_s = defaults.timeout_s if raw_timeout in (None, "") else int(raw_timeout)

        return cls(
            app_title=str(data.get("app_title") or defaults.app_title).strip()
            or defaults.app_title,
            site_code=str(data.get("site_code") or "").strip(),
            site_name=str(data.get("site_name") or "").strip(),
            operator_name=str(data.get("operator_name") or "").strip(),
            tariff_profile=tariff,
            service_fee=service_fee,
            refresh_interval_s=refresh,
            use_local_summary=bool(use_summary),
            base_url=str(data.get("base_url") or defaults.base_url).strip(),
            api_key=str(data.get("api_key") or ""),
            model=str(data.get("model") or defaults.model).strip(),
            persona_name=str(data.get("persona_name") or defaults.persona_name).strip(),
            persona_prompt=str(data.get("persona_prompt") or defaults.persona_prompt),
            temperature=temperature,
            timeout_s=timeout_s,
            vision_model=str(data.get("vision_model") or "").strip(),
        )
