# -*- coding: utf-8 -*-
"""充电桩档案与站点配置（domain.charger）。

口径说明：
- 直流快充统一按“标准新桩”建模，实测约为铭牌的 0.9 倍；
- 慢充为 7kW 交流桩；
- 每个车位只配置单枪；
- 站点车位由管理员在首页动态添加/删除，这里只提供“档案工厂”。

车位身份（两条独立的主键）：
- ``uid``：创建时随机生成、终身不变的稳定标识，订单只认它，所以删掉车位再
  建一个同名车位，两者的历史订单不会串；
- ``name``：展示名兼登录名（如 ``A001``），站内不允许重复。

车辆实际拿到的功率还要叠加 SOC 曲线与车辆峰值，见
application.collector.charging_model。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Tuple


class ChargerType(Enum):
    """充电桩类型。"""

    AC = "交流慢充"
    DC = "直流快充"


@dataclass(frozen=True)
class ChargerProfile:
    """一根充电桩（单个车位）的静态档案。"""

    charger_type: ChargerType
    nominal_power_kw: float
    health_factor: float
    max_voltage_v: float  # 桩侧最高输出电压
    voltage_platform_v: int  # 标称平台（展示/兼容用）：220/400/800/1000
    max_current_a: float  # 单枪最大电流
    label: str

    @property
    def actual_power_kw(self) -> float:
        """计入实测修正后的实际可用峰值功率。"""
        return round(self.nominal_power_kw * self.health_factor, 1)

    @property
    def is_dc(self) -> bool:
        return self.charger_type == ChargerType.DC

    def describe(self) -> str:
        if self.charger_type == ChargerType.AC:
            return f"{self.label} 7kW 交流慢充"
        return f"{self.label} {self.nominal_power_kw:.0f}kW（实测约 {self.actual_power_kw:.0f}kW）"


@dataclass(frozen=True)
class SpotConfig:
    """一个车位的配置（管理员在首页添加/删除的最小单元）。

    ``uid`` 是创建时随机生成、终身不变的稳定标识；``name`` 是展示名兼登录名
    （如 ``A001``），站内不可重复。订单只记 ``uid``，因此删掉车位后重建同名车位，
    两者的历史订单不会混在一起。

    ``power_kw`` 是**铭牌功率**（60/120/240 档），不是计入实测修正后的功率；
    实测功率由 ``make_charger`` 按 ``health_factor`` 折算，切勿把折算结果写回这里，
    否则每重启一次就会被重复打折。
    """

    uid: str
    name: str
    charger_type: ChargerType
    power_kw: float = 7.0


DC_POWER_TIERS = (60.0, 120.0, 240.0)
DC_HEALTH_FACTOR = 0.90  # 标准新桩实测约为铭牌的 0.9 倍
AC_POWER_KW = 7.0

# 车位展示名可用字母（去掉易混淆的 I / O），命名规则：A001 ~ Z999
SPOT_NAME_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
SPOT_NAME_WIDTH = 1000


def new_spot_uid() -> str:
    """生成车位稳定标识（随机、不可预测，删除重建也不会复用）。"""
    return "sp_" + uuid.uuid4().hex[:10]


def normalize_spot_name(name: str) -> str:
    """车位展示名的标准形态：去空白、转大写（A001 而不是 a001）。"""
    return str(name or "").strip().upper()


def suggest_spot_name(existing: Iterable[str]) -> str:
    """按 ``A001`` / ``A002`` … ``Z999`` 规则推荐一个未被占用的展示名。

    只保证“不与 existing 冲突”，真正的唯一性仍由数据库约束兜底。
    """
    used = {normalize_spot_name(name) for name in existing}
    for letter in SPOT_NAME_LETTERS:
        for index in range(1, SPOT_NAME_WIDTH):
            candidate = f"{letter}{index:03d}"
            if candidate not in used:
                return candidate
    raise ValueError("车位名称已用尽（A001~Z999）")


def coerce_type(charger_type: ChargerType | str) -> ChargerType:
    """把 ``ChargerType`` / ``'fast'`` / ``'slow'`` / ``'直流'`` 归一为枚举。"""
    if isinstance(charger_type, ChargerType):
        return charger_type
    text = str(charger_type).strip().lower()
    if text in {"ac", "slow", "交流", "交流慢充", "慢充"}:
        return ChargerType.AC
    return ChargerType.DC


def make_charger(
    name: str,
    charger_type: ChargerType | str,
    power_kw: float | None = None,
) -> ChargerProfile:
    """按车位展示名与类型/功率档生成单枪桩档案（慢充忽略功率档）。"""
    charger_type = coerce_type(charger_type)
    if charger_type == ChargerType.AC:
        return ChargerProfile(
            charger_type=ChargerType.AC,
            nominal_power_kw=AC_POWER_KW,
            health_factor=1.0,
            max_voltage_v=242.0,
            voltage_platform_v=220,
            max_current_a=32.0,
            label=f"{name} 车位",
        )
    kw = float(power_kw) if power_kw else 120.0
    return ChargerProfile(
        charger_type=ChargerType.DC,
        nominal_power_kw=kw,
        health_factor=DC_HEALTH_FACTOR,
        max_voltage_v=1000.0,
        voltage_platform_v=800,
        max_current_a=250.0,
        label=f"{name} 车位",
    )


# 兼容测试/旧脚本的固定档案目录（单枪版）。业务运行时不再默认使用该目录，
# 而是从数据库中的车位配置动态构建。
def _ac_legacy(label: str) -> ChargerProfile:
    return make_charger(label.split()[0], ChargerType.AC, AC_POWER_KW)


def _dc_legacy(label: str, nominal_kw: float) -> ChargerProfile:
    return make_charger(label.split()[0], ChargerType.DC, nominal_kw)


DEFAULT_STATION: Tuple[ChargerProfile, ...] = (
    _ac_legacy("1 号交流车位"),
    _ac_legacy("2 号交流车位"),
    _dc_legacy("3 号直流车位", 120.0),
    _dc_legacy("4 号直流车位", 120.0),
    _dc_legacy("5 号直流车位", 240.0),
    _dc_legacy("6 号直流车位", 240.0),
)

DEFAULT_CHARGER = DEFAULT_STATION[0]
