# -*- coding: utf-8 -*-
"""品牌与站点身份（evcharge.presentation.components.branding）。

界面不把“G15 / 国家电网 / 高速服务区”写死在代码里，统一从设置读取：

- 站点徽标（顶栏 logo 位）→ ``site_code``，例如 G15 / 闽A
- 顶栏主标题 → ``app_title``
- 顶栏副标题 → ``operator`` + ``site_name``
- 页面眉标 → ``site_code · 电价档简称 / 页面名``
- 强调色 → 随电价档切换（国网蓝 / 福建墨绿）

设置页保存后通过 ``BrandingHub.publish`` 广播，各页面调用 ``apply_branding`` 即时更新，
无需重启界面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

# 只做展示映射，不依赖基础设施；设置对象按「鸭子类型」读取属性
DEFAULT_APP_TITLE = "国网高速服务区充电监控与计费系统"

# 每个电价档的默认站点身份（与设置里的缺省值保持一致）
_IDENTITY_DEFAULTS = {
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
_TARIFF_SHORT = {"state_grid": "国网高速服务区", "fujian": "福建居民合表"}

# 每个电价档的强调色：国网=Material 蓝，福建=墨绿
TARIFF_ACCENTS = {"state_grid": "#1F6FEB", "fujian": "#0F7B6C"}


@dataclass(frozen=True)
class Branding:
    """一份不可变的品牌快照。"""

    app_title: str = DEFAULT_APP_TITLE
    site_code: str = "G15"
    site_name: str = ""
    operator: str = ""
    tariff_key: str = "state_grid"
    tariff_short: str = "国网高速服务区"
    accent: str = "#1F6FEB"

    # ---------- 派生文本 ----------
    @property
    def brand_line(self) -> str:
        """顶栏副标题：运营方 · 站点名。"""
        parts = [self.operator, self.site_name]
        return " · ".join(part for part in parts if part)

    def eyebrow(self, page_label: str = "") -> str:
        """页面眉标：G15 · 国网高速服务区 / 实时监盘。"""
        head = f"{self.site_code} · {self.tariff_short}" if self.site_code else self.tariff_short
        if page_label:
            return f"{head} / {page_label}"
        return head

    def window_title(self) -> str:
        return self.app_title or DEFAULT_APP_TITLE


def branding_from(settings) -> Branding:
    """由设置生成品牌快照。

    ``settings`` 按鸭子类型读取（只需有 app_title / site_code / site_name /
    operator_name / tariff_profile 这几个属性），因此本模块不 import 基础设施。
    """
    tariff_key = str(getattr(settings, "tariff_profile", "") or "state_grid")
    defaults = _IDENTITY_DEFAULTS.get(tariff_key, _IDENTITY_DEFAULTS["state_grid"])
    app_title = (getattr(settings, "app_title", "") or "").strip() or DEFAULT_APP_TITLE
    return Branding(
        app_title=app_title,
        site_code=(getattr(settings, "site_code", "") or "").strip()
        or defaults["site_code"],
        site_name=(getattr(settings, "site_name", "") or "").strip()
        or defaults["site_name"],
        operator=(getattr(settings, "operator_name", "") or "").strip()
        or defaults["operator_name"],
        tariff_key=tariff_key,
        tariff_short=_TARIFF_SHORT.get(tariff_key, "国网高速服务区"),
        accent=TARIFF_ACCENTS.get(tariff_key, "#1F6FEB"),
    )


class BrandingHub:
    """品牌订阅中心：设置保存后广播，各页面即时刷新眉标/徽标。"""

    def __init__(self, branding: Optional[Branding] = None) -> None:
        self._branding = branding or Branding()
        self._subscribers: List[Callable[[Branding], None]] = []

    @property
    def branding(self) -> Branding:
        return self._branding

    def subscribe(self, callback: Callable[[Branding], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def reset(self) -> None:
        """退出登录时清空订阅，避免旧页面控件被持续持有。"""
        self._subscribers.clear()

    def publish(self, branding: Branding) -> None:
        self._branding = branding
        for callback in list(self._subscribers):
            try:
                callback(branding)
            except Exception:  # 单个页面刷新失败不应影响其它页面
                continue
