# -*- coding: utf-8 -*-
"""历史数据生成脚本（国网高速服务区场景）。

用法：
    python scripts/gen_history.py [--days 75] [--seed 42] [--db PATH] [--reset]

生成规则：
- 车位来自数据库中的车位配置（spots 表）；数据库还没有车位时，
  自动创建一套示例车位（2 个交流慢充 + 4 个直流快充：120/120/240/240kW）；
- 每个车位只有单个充电枪；
- 交流桩：每天 1~2 单，18:00-23:00 开始，过夜/办公，目标 SOC 100%；
- 直流桩：每天 3~6 单，07:00-22:00 开始，目标 SOC 80%；
- 每单抽样车辆档案（电池容量/峰值功率/电压平台/交流功率）；
- 用与实时模拟器相同的 SOC 曲线与桩限流计算时长和电量；
- 同一个车位的相邻订单不重叠；
- 按国网高速分时电价（默认）逐单结算。

生成后建议先用 --reset 清空旧订单。
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Sequence

# 允许直接以脚本方式运行：把 src/ 加入 sys.path（无需先安装包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evcharge.application.collector.charging_model import delivered_power_kw  # noqa: E402
from evcharge.application.collector.simulator import make_plate  # noqa: E402
from evcharge.domain.charger import (  # noqa: E402
    ChargerProfile,
    ChargerType,
    SpotConfig,
    coerce_type,
    make_charger,
)
from evcharge.domain.models import ChargingSession, Order  # noqa: E402
from evcharge.domain.tariff import TariffEngine  # noqa: E402
from evcharge.domain.vehicle import VehicleProfileGenerator  # noqa: E402
from evcharge.infrastructure.database import Database  # noqa: E402
from evcharge.infrastructure.settings import load_settings, tariff_engine  # noqa: E402

# 示例车位（数据库为空时自动创建）：车位名 / 类型 / 直流功率
# 命名规则 A001…Z999，车位名同时是车位账号的登录名。
# 示例车位统一用 A001~A006 命名（与界面「添加车位」的默认建议一致）：
# 前两个交流慢充 7kW，后面四个标准直流快充 120/120/240/240kW。
_SAMPLE_SPOTS: tuple[tuple[str, str, float | None], ...] = (
    ("A001", "slow", None),
    ("A002", "slow", None),
    ("A003", "fast", 120.0),
    ("A004", "fast", 120.0),
    ("A005", "fast", 240.0),
    ("A006", "fast", 240.0),
)

# 直流开始小时权重（07:00~21:00），午间/傍晚偏高
_DC_HOURS = list(range(7, 22))
_DC_WEIGHTS = [2, 3, 5, 6, 7, 6, 5, 4, 5, 7, 8, 6, 4, 3, 2]
# 交流开始小时权重（18:00~23:00）
_AC_HOURS = list(range(18, 24))
_AC_WEIGHTS = [3, 5, 7, 8, 6, 4]


def _simulate_session(
    pile_id: str,
    charger: ChargerProfile,
    profile,
    start: datetime,
    start_soc: float,
    target_soc: float,
    tariff: TariffEngine,
    rng: random.Random,
    spot_name: str = "",
) -> Order:
    """按 SOC 曲线逐步积分，返回结算订单。"""
    session = ChargingSession(
        pile_id=str(pile_id),
        spot_name=spot_name,
        plate=make_plate(rng),
        profile=profile,
        start_time=start,
        tariff=tariff,
        charger=charger,
        start_soc=start_soc,
        target_soc=target_soc,
    )
    # 交流慢充用 1 分钟步长，直流快充用 10 秒步长
    step_seconds = 60 if charger.charger_type == ChargerType.AC else 10
    cursor = start
    max_seconds = 24 * 3600  # 单次会话最长 24 小时，防止异常死循环
    while session.soc < target_soc - 1e-6 and (cursor - start).total_seconds() < max_seconds:
        power = delivered_power_kw(charger, profile, session.soc)
        remaining = max(
            0.0, session.battery_kwh * (target_soc - session.soc) / 100.0
        )
        delta = min(power * step_seconds / 3600.0, remaining)
        if delta <= 0:
            break
        session.add_energy(delta, power, at=cursor)
        cursor += timedelta(seconds=step_seconds)

    return session.settle(cursor)


def generate(
    days: int,
    seed: int = 42,
    spots: Sequence[SpotConfig] | None = None,
    tariff: TariffEngine | None = None,
) -> List[Order]:
    """生成历史订单；未指定 tariff 时使用当前设置页的电价档。"""
    rng = random.Random(seed)
    tariff = tariff or tariff_engine(load_settings())
    vehicle_gen = VehicleProfileGenerator(rng=rng)
    configs = list(spots or [])
    if not configs:
        raise ValueError("没有任何车位，无法生成历史订单")
    base_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    orders: List[Order] = []

    # 每个车位独立时间线，跨天保留，保证同车位不重叠（按 uid 区分）
    last_end = {
        cfg.uid: base_day - timedelta(days=days + 1) for cfg in configs
    }

    chargers = {
        cfg.uid: make_charger(cfg.name, cfg.charger_type, cfg.power_kw)
        for cfg in configs
    }

    for day_offset in range(days, 0, -1):
        day = base_day - timedelta(days=day_offset)
        for cfg in configs:
            charger = chargers[cfg.uid]
            pile_id = cfg.uid
            if charger.charger_type == ChargerType.AC:
                sessions_today = rng.randint(1, 2)
            else:
                sessions_today = rng.randint(3, 6)
            for _ in range(sessions_today):
                if charger.charger_type == ChargerType.AC:
                    hour = rng.choices(_AC_HOURS, weights=_AC_WEIGHTS, k=1)[0]
                    start_soc = rng.uniform(20.0, 50.0)
                    target_soc = 100.0
                else:
                    hour = rng.choices(_DC_HOURS, weights=_DC_WEIGHTS, k=1)[0]
                    start_soc = rng.uniform(10.0, 40.0)
                    target_soc = 80.0

                start = day + timedelta(hours=hour, minutes=rng.randint(0, 59))
                gap = timedelta(minutes=rng.randint(5, 15))
                if start < last_end[pile_id] + gap:
                    start = last_end[pile_id] + gap
                # 当天排不下就跳过（AC 过夜单允许延续到次日）
                if start > day + timedelta(hours=23, minutes=30):
                    continue

                profile = vehicle_gen.generate(rng=rng)
                order = _simulate_session(
                    pile_id=pile_id,
                    spot_name=cfg.name,
                    charger=charger,
                    profile=profile,
                    start=start,
                    start_soc=start_soc,
                    target_soc=target_soc,
                    tariff=tariff,
                    rng=rng,
                )
                orders.append(order)
                last_end[pile_id] = order.end_time

    orders.sort(key=lambda o: o.start_time)
    return orders


def _spots_from_db(db: Database) -> List[SpotConfig]:
    return [
        SpotConfig(
            uid=str(row["uid"]),
            name=str(row["name"]),
            charger_type=coerce_type(row["charger_type"]),
            power_kw=float(row["power_kw"]),
        )
        for row in db.load_spots()
    ]


def _seed_sample_spots(db: Database, verbose: bool = True) -> List[SpotConfig]:
    """数据库还没有车位时创建一套示例车位（连同车位账号）。"""
    for name, charger_type, power in _SAMPLE_SPOTS:
        try:
            db.add_spot(name, charger_type, power)
        except ValueError:
            pass  # 已存在则跳过
    if verbose:
        print(
            "已创建示例车位：A001、A002 交流慢充 7kW；"
            "A003、A004 直流快充 120kW；A005、A006 直流快充 240kW。"
            "车位名即车位账号，密码为默认密码。"
        )
    return _spots_from_db(db)


def _print_summary(orders: List[Order], count: int, db_path: Path, days: int) -> None:
    if not orders:
        print(f"未生成订单（{days} 天），数据库：{db_path}")
        return
    energies = [o.energy_kwh for o in orders]
    batteries = [o.battery_kwh for o in orders if o.battery_kwh > 0]
    durations = [o.duration_min for o in orders]
    dc = sum(1 for o in orders if o.charger_type == "直流快充")
    ac = len(orders) - dc
    spots = sorted({str(o.spot_name or o.pile_id) for o in orders})
    print(f"生成并写入 {count} 条历史订单（{days} 天），数据库：{db_path}")
    print(
        f"  单均电量 {sum(energies) / len(energies):.1f} 度"
        f"（{min(energies):.1f}~{max(energies):.1f}）"
    )
    print(f"  交流/直流：{ac}/{dc}；覆盖车位 {len(spots)} 个（{'、'.join(spots)}）")
    if batteries:
        print(
            f"  平均电池 {sum(batteries) / len(batteries):.1f} kWh；"
            f"平均时长 {sum(durations) / len(durations):.0f} 分钟"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成国网高速服务区历史充电订单")
    parser.add_argument("--days", type=int, default=75, help="生成天数（默认 75 天）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，保证可复现）")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite 文件路径（默认 data/charging.db）",
    )
    parser.add_argument("--reset", action="store_true", help="清空已有订单后重新生成")
    parser.add_argument(
        "--reset-spots",
        action="store_true",
        help="同时重建示例车位（A001~A006，删掉现有车位与车位账号）",
    )
    parser.add_argument(
        "--seed-spots",
        action="store_true",
        help="数据库没有车位时自动创建示例车位（默认开启；显式传入便于说明）",
    )
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days 必须大于 0")

    db = Database(args.db)
    tariff = tariff_engine(load_settings())

    if args.reset_spots:
        db.clear_spots()
        print("已清空现有车位与车位账号。")

    spots = _spots_from_db(db)
    if not spots:
        spots = _seed_sample_spots(db)

    orders = generate(days=args.days, seed=args.seed, spots=spots, tariff=tariff)
    # 先生成再清库：生成失败时不会先丢掉已有订单
    if args.reset:
        db.clear_orders()
        print("已清空历史订单。")
    count = db.save_orders(orders)
    print(f"  电价档：{tariff.profile.name}；服务费 {tariff.service_fee:.2f} 元/度")
    _print_summary(orders, count, db.path, args.days)
    db.close()


if __name__ == "__main__":
    main()
