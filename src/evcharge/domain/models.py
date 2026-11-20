# -*- coding: utf-8 -*-
"""核心业务模型（OOP 封装）。

包含充电桩、充电会话、订单三类业务对象：
- ChargingPile：封装单个充电桩的状态、桩档案与实时遥测
- ChargingSession：封装一次"上电->充电->结算"的会话生命周期
- Order：一次充电的结算订单（含车辆、电池、SOC、分时账单）

车辆与桩的静态参数分别在 evcharge.domain.vehicle / evcharge.domain.charger，
功率曲线在 evcharge.application.collector.charging_model，本模块只负责业务状态与结算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional

from evcharge.domain.charger import DEFAULT_CHARGER, ChargerProfile
from evcharge.domain.tariff import BillResult, PeriodCharge, TariffEngine
from evcharge.domain.vehicle import VehicleProfile


class PileStatus(Enum):
    """充电桩状态。"""

    IDLE = "空闲"
    CHARGING = "充电中"


class SessionStatus(Enum):
    """充电会话状态。"""

    CHARGING = "充电中"
    FINISHED = "已结算"


@dataclass
class Telemetry:
    """一条设备遥测数据（由模拟器/真实设备上报）。"""

    timestamp: datetime
    voltage: float  # 电压 V
    current: float  # 电流 A
    power_kw: float  # 功率 kW
    energy_kwh: float  # 本次会话累计电量 kWh
    soc: float = 0.0  # 当前 SOC（%）

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "voltage": round(self.voltage, 1),
            "current": round(self.current, 2),
            "power_kw": round(self.power_kw, 3),
            "energy_kwh": round(self.energy_kwh, 3),
            "soc": round(self.soc, 1),
        }


class ChargingPile:
    """充电桩业务类（封装状态、桩档案与遥测历史）。

    ``pile_id`` 是车位的稳定标识（uid，创建时随机、终身不变），``name`` 是展示名
    兼登录名（如 ``A001``）。订单只记 ``pile_id``，所以同名车位重建也不会串历史。
    """

    def __init__(
        self,
        pile_id: str,
        name: str | None = None,
        charger: ChargerProfile | None = None,
    ) -> None:
        self.pile_id = str(pile_id)
        self.name = name or f"{pile_id} 号车位"
        self.charger = charger or DEFAULT_CHARGER
        self.status = PileStatus.IDLE
        self.latest: Optional[Telemetry] = None
        self.history: List[Telemetry] = []
        self.current_session: Optional["ChargingSession"] = None

    @property
    def uid(self) -> str:
        """车位稳定标识的别名（与 ``pile_id`` 同一个值）。"""
        return self.pile_id

    # ---------- 数据上报 ----------
    def push_telemetry(self, t: Telemetry, max_history: int = 1000) -> None:
        """接收一条遥测数据，更新实时值并保留历史（限长防内存膨胀）。"""
        self.latest = t
        self.history.append(t)
        if len(self.history) > max_history:
            self.history = self.history[-max_history:]

    def clear_history(self) -> None:
        """会话结束后清空曲线历史。"""
        self.history.clear()


@dataclass
class Order:
    """一次充电的结算订单。

    ``pile_id`` 是车位的稳定标识（uid）；``spot_name`` 是下单时的车位展示名快照，
    保证车位改名/删除后历史账单仍显示当时的名字。``order_id`` 默认由数据库自增分配
    （新建时保持 None）；车辆/电池/SOC 字段用于历史统计与账单展示，旧数据缺失时保持默认值。
    """

    pile_id: str
    plate: str
    start_time: datetime
    end_time: datetime
    energy_kwh: float
    bill: BillResult
    # 保留 status 字段兼容旧数据库；正常订单固定为「已完成」
    status: str = "已完成"
    order_id: Optional[int] = None
    spot_name: str = ""
    vehicle_category: str = "轿车"
    vehicle_segment: str = "中型"
    vehicle_model: str = "—"
    battery_kwh: float = 0.0
    start_soc: float = 0.0
    end_soc: float = 0.0
    peak_power_kw: float = 0.0
    charger_type: str = "直流快充"
    voltage_platform_v: int = 800

    @property
    def total_fee(self) -> float:
        return self.bill.total_fee

    @property
    def duration_min(self) -> float:
        return (self.end_time - self.start_time).total_seconds() / 60.0

    @property
    def tariff_periods_json(self) -> str:
        import json

        return json.dumps([p.to_dict() for p in self.bill.periods], ensure_ascii=False)

    def to_dict(self) -> Dict:
        return {
            "order_id": self.order_id,
            "pile_id": self.pile_id,
            "spot_name": self.spot_name,
            "plate": self.plate,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_min": round(self.duration_min, 1),
            "energy_kwh": round(self.energy_kwh, 3),
            "energy_fee": round(self.bill.energy_fee, 2),
            "service_fee": round(self.bill.service_fee, 2),
            "total_fee": round(self.total_fee, 2),
            "periods": [p.to_dict() for p in self.bill.periods],
            "status": self.status,
            "vehicle_category": self.vehicle_category,
            "vehicle_segment": self.vehicle_segment,
            "vehicle_model": self.vehicle_model,
            "battery_kwh": round(self.battery_kwh, 1),
            "start_soc": round(self.start_soc, 1),
            "end_soc": round(self.end_soc, 1),
            "peak_power_kw": round(self.peak_power_kw, 1),
            "charger_type": self.charger_type,
            "voltage_platform_v": self.voltage_platform_v,
        }


class ChargingSession:
    """充电会话：管理"上电 -> 充电 -> 断电/充满结算"的生命周期。"""

    def __init__(
        self,
        pile_id: str,
        plate: str,
        profile: VehicleProfile,
        start_time: datetime,
        tariff: TariffEngine,
        charger: ChargerProfile,
        start_soc: float,
        target_soc: float,
        spot_name: str = "",
    ) -> None:
        self.pile_id = str(pile_id)
        self.spot_name = spot_name
        self.plate = plate
        self.profile = profile
        self.vehicle = profile.label
        self.start_time = start_time
        self.tariff = tariff
        self.charger = charger
        self.battery_kwh = profile.battery_kwh
        self.start_soc = max(0.0, min(100.0, float(start_soc)))
        self.target_soc = max(self.start_soc, min(100.0, float(target_soc)))
        self.soc = self.start_soc
        self.energy_kwh = 0.0
        self.period_energy: Dict[str, float] = {}
        self.peak_power_kw = 0.0
        self.status = SessionStatus.CHARGING

    def add_energy(
        self, kwh: float, power_kw: float = 0.0, at: datetime | None = None
    ) -> None:
        """累计充电电量并同步 SOC / 峰值功率。

        传入 at 时按该时刻所属计价时段累计电量，结算即可按实际分段电量出账。
        """
        if kwh > 0:
            self.energy_kwh += kwh
            if at is not None:
                name, _ = self.tariff.period_of(at)
                self.period_energy[name] = self.period_energy.get(name, 0.0) + kwh
            if self.battery_kwh > 0:
                self.soc = min(
                    100.0,
                    self.start_soc + self.energy_kwh / self.battery_kwh * 100.0,
                )
        if power_kw > self.peak_power_kw:
            self.peak_power_kw = power_kw

    def _bill(self, end_time: datetime) -> BillResult:
        """优先按各时段实际电量结算；无时间戳的调用退回按时长分摊。"""
        total = sum(self.period_energy.values())
        if self.period_energy and abs(total - self.energy_kwh) <= 1e-6:
            prices = {p.name: p.price for p in self.tariff.profile.periods}
            charges = [
                PeriodCharge(
                    name,
                    prices.get(name, 0.0),
                    energy,
                    energy * prices.get(name, 0.0),
                )
                for name, energy in self.period_energy.items()
            ]
            return BillResult(
                self.energy_kwh,
                sum(c.fee for c in charges),
                self.energy_kwh * self.tariff.service_fee,
                charges,
            )
        return self.tariff.bill(self.start_time, end_time, self.energy_kwh)

    def settle(self, end_time: datetime) -> Order:
        """结束会话并生成订单（正常充电完成）。"""
        if self.status != SessionStatus.CHARGING:
            raise RuntimeError("会话已结束，不能重复结算")
        if end_time <= self.start_time:
            end_time = self.start_time + timedelta(seconds=1)
        bill = self._bill(end_time)
        self.status = SessionStatus.FINISHED
        return Order(
            pile_id=self.pile_id,
            plate=self.plate,
            start_time=self.start_time,
            end_time=end_time,
            energy_kwh=self.energy_kwh,
            bill=bill,
            status="已完成",
            spot_name=self.spot_name,
            vehicle_category=self.profile.category,
            vehicle_segment=self.profile.segment,
            vehicle_model=self.profile.model,
            battery_kwh=self.battery_kwh,
            start_soc=self.start_soc,
            end_soc=self.soc,
            peak_power_kw=self.peak_power_kw,
            charger_type=self.charger.charger_type.value,
            voltage_platform_v=self.profile.platform_v,
        )
