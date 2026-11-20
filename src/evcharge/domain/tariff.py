# -*- coding: utf-8 -*-
"""分时电价计费引擎（TariffEngine）。

内置两套电价档：
- STATE_GRID_HIGHWAY：国网高速服务区（2026 实测口径，默认）
  * 谷段 0.3756、平段 0.6050、峰段 0.8873 元/度
  * 服务费 0.70 元/度
  * 谷段 02:00-06:00、10:00-14:00；峰段 15:00-22:00；其余为平段
- FUJIAN_RESIDENTIAL：原福建居民合表分时电价（保留用于对比）
  * 谷 0.333、平 0.533、峰 0.843 元/度；服务费 0.40 元/度

计费口径：正常下单时由 ``ChargingSession`` 按各时段**实际充入电量**出账
（见 ``models.ChargingSession._bill``）；只有在没有逐段电量记录（如导入历史数据、
手工构造会话）时，才回退到本模块 ``bill()`` 的“按各时段时长占比分摊电量”。
服务费按总电量计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# 福建居民合表基准电价（元/千瓦时）
BASE_PRICE = 0.533

PERIOD_NAMES = ("峰段", "平段", "谷段")


@dataclass(frozen=True)
class TariffPeriod:
    """一个分时价格段（小时粒度）。"""

    start: int
    end: int
    name: str
    price: float


@dataclass(frozen=True)
class TariffProfile:
    """一套完整的电价档。"""

    name: str
    periods: Tuple[TariffPeriod, ...]
    service_fee: float

    def period_of(self, dt: datetime) -> Tuple[str, float]:
        for item in self.periods:
            if item.start <= dt.hour < item.end:
                return item.name, item.price
        fallback = next((p.price for p in self.periods if p.name == "平段"), 0.0)
        return "平段", fallback

    def next_boundary(self, dt: datetime) -> datetime:
        for item in self.periods:
            if item.start <= dt.hour < item.end:
                if item.end >= 24:
                    return (dt + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                return dt.replace(hour=item.end, minute=0, second=0, microsecond=0)
        return (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


# 福建居民合表分时（原项目口径）
FUJIAN_RESIDENTIAL = TariffProfile(
    name="福建居民合表分时",
    periods=(
        TariffPeriod(0, 8, "谷段", 0.333),
        TariffPeriod(8, 10, "平段", BASE_PRICE),
        TariffPeriod(10, 12, "峰段", 0.843),
        TariffPeriod(12, 15, "平段", BASE_PRICE),
        TariffPeriod(15, 20, "峰段", 0.843),
        TariffPeriod(20, 21, "平段", BASE_PRICE),
        TariffPeriod(21, 22, "峰段", 0.843),
        TariffPeriod(22, 24, "平段", BASE_PRICE),
    ),
    service_fee=0.40,
)

# 国网高速服务区（长深高速江苏段实测口径）
STATE_GRID_HIGHWAY = TariffProfile(
    name="国网高速服务区",
    periods=(
        TariffPeriod(0, 2, "平段", 0.6050),
        TariffPeriod(2, 6, "谷段", 0.3756),
        TariffPeriod(6, 10, "平段", 0.6050),
        TariffPeriod(10, 14, "谷段", 0.3756),
        TariffPeriod(14, 15, "平段", 0.6050),
        TariffPeriod(15, 22, "峰段", 0.8873),
        TariffPeriod(22, 24, "平段", 0.6050),
    ),
    service_fee=0.70,
)

DEFAULT_PROFILE = STATE_GRID_HIGHWAY

def period_of(dt: datetime, profile: Optional[TariffProfile] = None) -> Tuple[str, float]:
    """返回指定时刻所属的时段名称与电价（默认国网高速档）。"""
    return (profile or DEFAULT_PROFILE).period_of(dt)


@dataclass
class PeriodCharge:
    """单个计价时段内的电量与费用明细。"""

    period: str
    price: float  # 元/千瓦时（不含服务费）
    energy_kwh: float
    fee: float  # 电费小计（不含服务费）

    def to_dict(self) -> Dict[str, float | str]:
        return {
            "period": self.period,
            "price": round(self.price, 3),
            "energy_kwh": round(self.energy_kwh, 3),
            "fee": round(self.fee, 2),
        }


@dataclass
class BillResult:
    """一次充电会话的结算账单。"""

    energy_kwh: float
    energy_fee: float  # 电费合计
    service_fee: float  # 服务费合计
    periods: List[PeriodCharge] = field(default_factory=list)

    @property
    def total_fee(self) -> float:
        return self.energy_fee + self.service_fee

    def to_dict(self) -> Dict:
        return {
            "energy_kwh": round(self.energy_kwh, 3),
            "energy_fee": round(self.energy_fee, 2),
            "service_fee": round(self.service_fee, 2),
            "total_fee": round(self.total_fee, 2),
            "periods": [p.to_dict() for p in self.periods],
        }


class TariffEngine:
    """分时电价计费引擎。"""

    def __init__(
        self,
        service_fee: Optional[float] = None,
        profile: Optional[TariffProfile] = None,
    ) -> None:
        self.profile = profile or DEFAULT_PROFILE
        self.service_fee = self.profile.service_fee if service_fee is None else service_fee

    def period_of(self, dt: datetime) -> Tuple[str, float]:
        return self.profile.period_of(dt)

    def bill(self, start: datetime, end: datetime, energy_kwh: float) -> BillResult:
        """按分时电价结算一次充电会话（无逐段电量时的回退路径）。

        电量按各时段"时长占比"分摊（充电功率近似恒定的假设下成立）；
        有逐段实测电量时请优先走 ``ChargingSession._bill``。
        """
        if energy_kwh <= 0:
            return BillResult(0.0, 0.0, 0.0, [])
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")

        total_seconds = (end - start).total_seconds()
        charges: Dict[str, PeriodCharge] = {}

        cursor = start
        while cursor < end:
            boundary = self.profile.next_boundary(cursor)
            if boundary > end:
                boundary = end
            seg_seconds = (boundary - cursor).total_seconds()
            share = seg_seconds / total_seconds
            seg_energy = energy_kwh * share
            name, price = self.profile.period_of(cursor)

            item = charges.get(name)
            if item is None:
                item = PeriodCharge(name, price, 0.0, 0.0)
                charges[name] = item
            item.energy_kwh += seg_energy
            item.fee += seg_energy * price

            cursor = boundary

        periods = [charges[n] for n in PERIOD_NAMES if n in charges]
        energy_fee = sum(p.fee for p in periods)
        service_fee = energy_kwh * self.service_fee
        return BillResult(energy_kwh, energy_fee, service_fee, periods)

    def describe_periods(self) -> str:
        """返回当前电价档的时段价格说明文本。"""
        rows = [
            f"{p.name} {p.start:02d}:00-{p.end:02d}:00 {p.price:.3f} 元/度"
            for p in self.profile.periods
        ]
        rows.append(f"服务费 {self.service_fee:.2f} 元/度")
        return "\n".join(rows)
