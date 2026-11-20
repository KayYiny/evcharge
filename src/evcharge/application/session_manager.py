# -*- coding: utf-8 -*-
"""会话控制管理器（SessionManager）。

管理站点各充电车位的会话生命周期：
- 用户上电：按桩类型抽样车辆档案、起始 SOC、目标 SOC；
- 充电中：模拟器按 SOC 曲线更新功率/电量/SOC，达到目标 SOC 自动结算；
- 用户断电：按当前电量与分时电价结算。

车位由管理员在后台动态增删；每个车位只有单个充电枪。
每个车位有两个标识：``uid``（随机、终身不变，订单只认它）与
``name``（展示名兼登录名，如 ``A001``，站内唯一）。车位可通过其中任意一个定位。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Callable, List, Sequence

from evcharge.application.collector.simulator import DataCollector, Simulator, make_plate
from evcharge.domain.charger import (
    DEFAULT_STATION,
    ChargerProfile,
    ChargerType,
    SpotConfig,
    make_charger,
    new_spot_uid,
    normalize_spot_name,
)
from evcharge.domain.models import ChargingPile, ChargingSession, Order, PileStatus
from evcharge.domain.tariff import TariffEngine
from evcharge.domain.vehicle import VehicleProfileGenerator


class SessionManager:
    """站点级会话管理器。

    ``spots`` 为空时站点没有任何车位；传入 ``station`` / ``pile_count``
    会退化为使用固定的桩型目录（历史测试用）。
    """

    def __init__(
        self,
        tariff: TariffEngine | None = None,
        collector: DataCollector | None = None,
        pile_count: int | None = None,
        on_order: Callable[[Order], None] | None = None,
        vehicle_generator: VehicleProfileGenerator | None = None,
        station: Sequence[ChargerProfile] | None = None,
        spots: Sequence[SpotConfig] | None = None,
    ) -> None:
        self.tariff = tariff or TariffEngine()
        self.collector = collector or Simulator()
        self.vehicle_generator = vehicle_generator or VehicleProfileGenerator()
        self.orders: List[Order] = []
        self._rng = random.Random()
        self._on_order = on_order
        self.piles: List[ChargingPile] = []

        if spots is not None:
            for cfg in spots:
                self.add_spot(
                    cfg.name,
                    cfg.charger_type,
                    cfg.power_kw,
                    uid=cfg.uid or None,
                )
        else:
            profiles = list(station or DEFAULT_STATION)
            if pile_count is not None:
                profiles = profiles[: max(0, pile_count)]
            self.piles = [
                ChargingPile(i + 1, charger=profiles[i]) for i in range(len(profiles))
            ]

    # ---------- 车位管理 ----------
    def add_spot(
        self,
        name: str,
        charger_type: ChargerType | str,
        power_kw: float | None = None,
        uid: str | None = None,
    ) -> ChargingPile:
        """新增一个车位（单枪）。

        ``name`` 是展示名兼登录名，站内不可重复（忽略大小写）；``uid`` 不传时随机生成。
        返回新车位，调用方可用 ``pile.uid`` 去持久化。
        """
        name = normalize_spot_name(name)
        if not name:
            raise ValueError("车位名称不能为空")
        uid = str(uid).strip() if uid else new_spot_uid()
        if any(p.pile_id == uid for p in self.piles):
            raise ValueError(f"车位标识 {uid} 已存在")
        if any(p.name.casefold() == name.casefold() for p in self.piles):
            raise ValueError(f"车位 {name} 已存在")
        profile = make_charger(name, charger_type, power_kw)
        pile = ChargingPile(uid, name=name, charger=profile)
        self.piles.append(pile)
        return pile

    def remove_spot(self, key: str) -> Order | None:
        """删除车位（``key`` 可以是 uid 或展示名）。

        若车位正在充电，先强制结算（按当前电量与电价），再移除车位。
        返回因强制结算产生的订单（无充电则返回 None）。订单通过 ``on_order``
        回调落库，不会因删车位而消失。
        """
        pile = self.pile(key)
        order = None
        if pile.status == PileStatus.CHARGING and pile.current_session is not None:
            order = pile.current_session.settle(datetime.now())
            self._record(order)
        self.piles = [p for p in self.piles if p.pile_id != pile.pile_id]
        return order

    # ---------- 查询 ----------
    def pile(self, key: str) -> ChargingPile:
        """按 uid 或展示名定位车位（先按 uid 精确匹配，再按名字忽略大小写匹配）。"""
        text = str(key)
        for p in self.piles:
            if p.pile_id == text:
                return p
        for p in self.piles:
            if p.name.casefold() == text.casefold():
                return p
        raise KeyError(f"不存在的车位: {key}")

    def spot_name(self, key: str) -> str:
        """把 uid / 名字归一成展示名（找不到时原样返回，便于显示历史订单）。"""
        try:
            return self.pile(key).name
        except KeyError:
            return str(key)

    # ---------- 会话操作 ----------
    def start_session(self, key: str, plate: str | None = None) -> ChargingSession:
        """用户上电：抽样车辆与充电参数，开始充电。

        ``plate`` 传入时使用该车牌（例如摄像头/相册识别结果），
        不传则随机生成一个模拟车牌。
        """
        pile = self.pile(key)
        if pile.status == PileStatus.CHARGING:
            raise RuntimeError(f"{pile.name} 正在充电中，不能重复上电")

        plate = (plate or "").strip() or make_plate(self._rng)

        profile = self.vehicle_generator.generate()
        charger = pile.charger
        if charger.is_dc:
            start_soc = self._rng.uniform(10.0, 40.0)
            target_soc = 80.0
        else:
            start_soc = self._rng.uniform(20.0, 50.0)
            target_soc = 100.0

        session = ChargingSession(
            pile_id=pile.pile_id,
            spot_name=pile.name,
            plate=plate,
            profile=profile,
            start_time=datetime.now(),
            tariff=self.tariff,
            charger=charger,
            start_soc=start_soc,
            target_soc=target_soc,
        )
        pile.current_session = session
        pile.status = PileStatus.CHARGING
        pile.clear_history()
        pile.latest = None
        return session

    def stop_session(self, key: str) -> Order:
        """用户断电：结束充电并结算账单。"""
        pile = self.pile(key)
        if pile.status != PileStatus.CHARGING or pile.current_session is None:
            raise RuntimeError(f"{pile.name} 当前没有进行中的充电会话")

        order = pile.current_session.settle(datetime.now())
        pile.status = PileStatus.IDLE
        pile.current_session = None
        pile.clear_history()
        pile.latest = None
        self._record(order)
        return order

    def _record(self, order: Order) -> None:
        """订单入内存列表并通知持久化回调。"""
        self.orders.append(order)
        if self._on_order:
            self._on_order(order)

    # ---------- 数据采集 ----------
    def tick(self) -> List[Order]:
        """定时上报；返回本轮因达到目标 SOC 自动结算的订单。"""
        completed: List[Order] = []
        for pile in self.piles:
            session = pile.current_session
            if session is None:
                continue

            telemetry = self.collector.collect(pile, session)
            pile.push_telemetry(telemetry)
            if session.soc >= session.target_soc - 1e-6:
                order = session.settle(datetime.now())
                pile.status = PileStatus.IDLE
                pile.current_session = None
                pile.clear_history()
                pile.latest = None
                self._record(order)
                completed.append(order)
        return completed
