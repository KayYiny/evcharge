# -*- coding: utf-8 -*-
"""充电桩遥测模拟器（DataCollector 接口实现）。

实时遥测由 SOC 功率曲线驱动：
- 实际功率 = min(车辆峰值, 桩实测峰值, 电压×单枪电流上限) × SOC 系数；
- 电量按真实采样间隔积分，SOC 随电量同步更新；
- 达到目标 SOC 后由 SessionManager 自动结算。

接口封装为 DataCollector，后续可无缝替换为真实设备的 MQTT/HTTP 上报通道。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Protocol

from evcharge.application.collector.charging_model import (
    current_for,
    delivered_power_kw,
    operating_voltage,
)
from evcharge.domain.models import ChargingPile, ChargingSession, Telemetry


class DataCollector(Protocol):
    """设备数据采集接口：真实设备与模拟器共用此契约。"""

    def collect(self, pile: ChargingPile, session: ChargingSession | None) -> Telemetry: ...


class Simulator(DataCollector):
    """模拟器实现：按桩档案 + 车辆档案 + SOC 曲线生成遥测。"""

    def collect(self, pile: ChargingPile, session: ChargingSession | None) -> Telemetry:
        now = datetime.now()

        if session is None:
            # 空闲桩：无功率；直流桩无输出时电压为 0，交流桩保持 220V 待机
            voltage = 0.0 if pile.charger.is_dc else 220.0
            return Telemetry(now, voltage, 0.0, 0.0, 0.0, 0.0)

        # 真实采样间隔（UI 卡顿/休眠时最多补 10 秒）
        interval = 2.0
        if pile.latest is not None:
            interval = (now - pile.latest.timestamp).total_seconds()
        interval = max(0.0, min(interval, 10.0))

        power = delivered_power_kw(
            pile.charger,
            session.profile,
            session.soc,
        )

        # 最后一步不超过目标 SOC 所需电量，避免自动结算前 SOC 过冲
        remaining = max(
            0.0,
            session.battery_kwh * (session.target_soc - session.soc) / 100.0,
        )
        delta = min(power * interval / 3600.0, remaining)
        if delta > 0:
            session.add_energy(delta, power, at=now)

        voltage = operating_voltage(pile.charger, session.profile)
        current = current_for(power, voltage)
        return Telemetry(
            now,
            voltage,
            current,
            power,
            session.energy_kwh,
            session.soc,
        )


def make_plate(rng: random.Random | None = None) -> str:
    """生成一个随机的福建车牌号（虚拟用户，模拟扫码）。"""
    rng = rng or random.Random()
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return f"闽{rng.choice('ABCD')}·{rng.choice(letters)}{rng.randint(1000, 9999)}"
