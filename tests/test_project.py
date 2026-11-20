# -*- coding: utf-8 -*-
"""项目单元测试（标准库 unittest，无需额外依赖）。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import matplotlib
import requests

matplotlib.use("Agg")  # 无界面渲染图表

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import flet as ft  # noqa: E402

from evcharge.application.assistant import AIError, answer_question, chat, system_prompt  # noqa: E402
from evcharge.infrastructure.settings import (  # noqa: E402
    DEFAULT_APP_TITLE,
    DEFAULT_PERSONA_NAME,
    AppSettings,
    load_settings,
    save_settings,
    tariff_engine,
)
from evcharge.application.analytics.report import (  # noqa: E402
    SITE_QUESTIONS,
    build_raw_data_text,
    build_site_summary,
)
from evcharge.application.analytics.stats import (  # noqa: E402
    battery_distribution,
    charger_mix,
    daily_trend,
    hourly_profile,
    overview,
    period_distribution,
    pile_stats,
    to_dataframe,
    vehicle_mix,
)
from evcharge.application.collector.charging_model import (  # noqa: E402
    delivered_power_kw,
    energy_for_soc,
    fast_charge_minutes,
    soc_factor,
)
from evcharge.application.session_manager import SessionManager  # noqa: E402
from evcharge.application.collector.simulator import Simulator, make_plate  # noqa: E402
from evcharge.domain.charger import (  # noqa: E402
    DEFAULT_STATION,
    ChargerType,
    SpotConfig,
    make_charger,
)
from evcharge.domain.models import (  # noqa: E402
    ChargingPile,
    ChargingSession,
    PileStatus,
    Telemetry,
)
from evcharge.domain.tariff import (  # noqa: E402
    FUJIAN_RESIDENTIAL,
    STATE_GRID_HIGHWAY,
    TariffEngine,
    period_of,
)
from evcharge.domain.vehicle import VehicleProfile, VehicleProfileGenerator  # noqa: E402
from scripts.gen_history import generate as generate_history  # noqa: E402
from evcharge.infrastructure.csv_export import export_orders, load_orders_csv  # noqa: E402
from evcharge.infrastructure.auth import (  # noqa: E402
    ADMIN_USERNAME,
    DEFAULT_PASSWORD,
    ROLE_ADMIN,
    ROLE_SPOT_USER,
    hash_password,
    verify_password,
)
from evcharge.application.runtime import ChargingRuntime  # noqa: E402
from evcharge.bootstrap import build_context  # noqa: E402
from evcharge.infrastructure import secrets as secret_store  # noqa: E402
from evcharge.infrastructure.database import Database  # noqa: E402
from evcharge.infrastructure.plate_recognition import (  # noqa: E402
    PlateRecognitionError,
    format_plate,
    is_plate_like,
    normalize_plate,
    recognition_available,
    recognize_plate,
)
from evcharge.presentation.pages.ai_page import AIPage  # noqa: E402
from evcharge.presentation.components.charts import bar_chart, line_chart, pie_chart  # noqa: E402
from evcharge.presentation.pages.monitor_page import MonitorPage  # noqa: E402
from evcharge.presentation.pages.analysis_page import build_analysis_page  # noqa: E402
from evcharge.presentation.components.branding import branding_from  # noqa: E402
from evcharge.presentation.components import theme  # noqa: E402
from evcharge.presentation.pages.orders_page import OrdersPage  # noqa: E402
from evcharge.presentation.pages.settings_page import SettingsPage  # noqa: E402


def make_vehicle(
    battery: float = 75.0,
    peak: float = 300.0,
    platform: int = 800,
    ac: float = 11.0,
    category: str = "轿车",
    segment: str = "中型",
    model: str = "测试车",
) -> VehicleProfile:
    return VehicleProfile(category, segment, model, battery, peak, platform, ac)


def make_order(
    pile_id: str = "1",
    charger=DEFAULT_STATION[2],
    vehicle: VehicleProfile | None = None,
    energy: float = 35.0,
    start: datetime | None = None,
    duration_min: int = 30,
    spot_name: str = "A001",
):
    """构造一条已结算订单（默认 10:00 谷段、30 分钟、35 度、直流 120kW 单枪桩）。"""
    start = start or datetime(2024, 1, 1, 10, 0)
    vehicle = vehicle or make_vehicle()
    session = ChargingSession(
        pile_id,
        "闽A·12345",
        vehicle,
        start,
        TariffEngine(),
        charger,
        30.0,
        80.0,
        spot_name=spot_name,
    )
    session.add_energy(energy, min(vehicle.peak_power_kw, charger.actual_power_kw))
    return session.settle(start + timedelta(minutes=duration_min))


class TariffTests(unittest.TestCase):
    """国网高速分时电价与福建旧档。"""

    def setUp(self) -> None:
        self.engine = TariffEngine()  # 默认国网高速档

    def test_highway_valley(self) -> None:
        bill = self.engine.bill(datetime(2024, 1, 1, 3), datetime(2024, 1, 1, 4), 10)
        self.assertEqual([p.period for p in bill.periods], ["谷段"])
        self.assertAlmostEqual(bill.energy_fee, 3.756, places=3)
        self.assertAlmostEqual(bill.service_fee, 7.0, places=2)
        self.assertAlmostEqual(bill.total_fee, 10.756, places=2)

    def test_highway_peak(self) -> None:
        bill = self.engine.bill(datetime(2024, 1, 1, 18), datetime(2024, 1, 1, 20), 40)
        self.assertEqual([p.period for p in bill.periods], ["峰段"])
        self.assertAlmostEqual(bill.energy_fee, 35.492, places=2)
        self.assertAlmostEqual(bill.service_fee, 28.0, places=2)

    def test_highway_cross_period(self) -> None:
        bill = self.engine.bill(datetime(2024, 1, 1, 21), datetime(2024, 1, 1, 23), 20)
        self.assertEqual([p.period for p in bill.periods], ["峰段", "平段"])
        self.assertAlmostEqual(bill.periods[0].energy_kwh, 10.0, places=3)
        self.assertAlmostEqual(bill.periods[1].energy_kwh, 10.0, places=3)
        self.assertAlmostEqual(bill.energy_fee, 14.923, places=2)
        self.assertAlmostEqual(bill.total_fee, 28.923, places=2)

    def test_session_bills_actual_energy_per_period(self) -> None:
        """带时间戳累计的电量按实际时段出账，而不是按时长占比。"""
        session = ChargingSession(
            1,
            "闽A·12345",
            make_vehicle(),
            datetime(2024, 1, 1, 21, 50),
            self.engine,
            DEFAULT_STATION[2],
            30.0,
            80.0,
        )
        session.add_energy(10.0, 100.0, at=datetime(2024, 1, 1, 21, 55))  # 峰段
        session.add_energy(20.0, 100.0, at=datetime(2024, 1, 1, 22, 5))  # 平段
        bill = session.settle(datetime(2024, 1, 1, 22, 10)).bill
        energies = {p.period: p.energy_kwh for p in bill.periods}
        self.assertAlmostEqual(energies["峰段"], 10.0, places=3)
        self.assertAlmostEqual(energies["平段"], 20.0, places=3)
        self.assertAlmostEqual(bill.energy_fee, 10 * 0.8873 + 20 * 0.6050, places=2)
        self.assertAlmostEqual(bill.service_fee, 30 * 0.70, places=2)

    def test_fujian_profile_still_available(self) -> None:
        engine = TariffEngine(profile=FUJIAN_RESIDENTIAL)
        bill = engine.bill(datetime(2024, 1, 1, 0), datetime(2024, 1, 1, 8), 10)
        self.assertEqual([p.period for p in bill.periods], ["谷段"])
        self.assertAlmostEqual(bill.energy_fee, 3.33, places=2)
        self.assertAlmostEqual(bill.service_fee, 4.0, places=2)

    def test_zero_energy(self) -> None:
        bill = self.engine.bill(datetime(2024, 1, 1, 10), datetime(2024, 1, 1, 11), 0)
        self.assertEqual(bill.total_fee, 0.0)
        self.assertEqual(bill.periods, [])

    def test_invalid_time_range(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.bill(datetime(2024, 1, 1, 12), datetime(2024, 1, 1, 11), 5)

    def test_highway_period_boundaries(self) -> None:
        cases = [
            ((1, 59), "平段"),
            ((2, 0), "谷段"),
            ((5, 59), "谷段"),
            ((6, 0), "平段"),
            ((9, 59), "平段"),
            ((10, 0), "谷段"),
            ((13, 59), "谷段"),
            ((14, 0), "平段"),
            ((14, 59), "平段"),
            ((15, 0), "峰段"),
            ((21, 59), "峰段"),
            ((22, 0), "平段"),
            ((23, 59), "平段"),
        ]
        for (hour, minute), expected in cases:
            with self.subTest(hour=hour, minute=minute):
                name, _price = period_of(datetime(2024, 1, 1, hour, minute))
                self.assertEqual(name, expected)

    def test_profile_metadata(self) -> None:
        self.assertEqual(STATE_GRID_HIGHWAY.service_fee, 0.70)
        self.assertIn("服务费 0.70", self.engine.describe_periods())


class VehicleTests(unittest.TestCase):
    """车型档案生成与物理约束。"""

    def test_generate_many_profiles(self) -> None:
        generator = VehicleProfileGenerator(rng=random.Random(42))
        categories = set()
        for _ in range(500):
            vehicle = generator.generate()
            categories.add(vehicle.category)
            self.assertIn(vehicle.category, {"轿车", "SUV"})
            self.assertGreaterEqual(vehicle.battery_kwh, 40.0)
            self.assertLessEqual(vehicle.battery_kwh, 140.0)
            self.assertIn(vehicle.platform_v, {400, 800, 1000})
            self.assertIn(vehicle.ac_power_kw, {7.0, 11.0, 22.0})
            self.assertLessEqual(vehicle.peak_power_kw, vehicle.battery_kwh * 10.0 + 1e-6)
            if vehicle.battery_kwh < 60:
                self.assertLessEqual(vehicle.peak_power_kw, 200.0)
            if vehicle.platform_v == 400:
                self.assertLessEqual(vehicle.peak_power_kw, 250.0)
            self.assertTrue(vehicle.model)
            self.assertTrue(vehicle.label)
        self.assertEqual(categories, {"轿车", "SUV"})

    def test_seed_reproducible(self) -> None:
        first = [VehicleProfileGenerator(rng=random.Random(7)).generate() for _ in range(3)]
        second = [VehicleProfileGenerator(rng=random.Random(7)).generate() for _ in range(3)]
        self.assertEqual(first, second)


class ChargingModelTests(unittest.TestCase):
    """SOC 曲线与功率封顶。"""

    def test_soc_factor(self) -> None:
        self.assertAlmostEqual(soc_factor(0), 0.8, places=6)
        self.assertAlmostEqual(soc_factor(15), 0.9, places=6)
        self.assertAlmostEqual(soc_factor(30), 1.0, places=6)
        self.assertAlmostEqual(soc_factor(80), 1.0, places=6)
        self.assertAlmostEqual(soc_factor(90), 0.6, places=6)
        self.assertAlmostEqual(soc_factor(100), 0.2, places=6)

    def test_400v_current_limit(self) -> None:
        charger = DEFAULT_STATION[2]  # DC 120kW，250A
        vehicle = make_vehicle(battery=75, peak=300, platform=400)
        power = delivered_power_kw(charger, vehicle, 50)
        self.assertAlmostEqual(power, 100.0, places=3)  # 400V × 250A
        self.assertAlmostEqual(delivered_power_kw(charger, vehicle, 0), 80.0, places=3)
        self.assertAlmostEqual(delivered_power_kw(charger, vehicle, 90), 60.0, places=3)

    def test_240kw_and_1000v(self) -> None:
        charger = DEFAULT_STATION[4]  # DC 240kW
        vehicle_800 = make_vehicle(battery=100, peak=400, platform=800)
        self.assertAlmostEqual(delivered_power_kw(charger, vehicle_800, 50), 200.0, places=3)
        vehicle_1000 = make_vehicle(battery=100, peak=600, platform=1000)
        self.assertAlmostEqual(delivered_power_kw(charger, vehicle_1000, 50), 216.0, places=3)

    def test_single_gun_capped_by_charger(self) -> None:
        """单枪车位：输出功率既不超过车辆峰值，也不超过桩的实测功率。"""
        charger = DEFAULT_STATION[4]
        vehicle = make_vehicle(battery=100, peak=1000, platform=1000)
        power = delivered_power_kw(charger, vehicle, 50)
        self.assertLessEqual(power, charger.actual_power_kw + 1e-6)
        self.assertGreater(power, 0.0)

    def test_ac_charging(self) -> None:
        charger = DEFAULT_STATION[0]
        vehicle = make_vehicle(battery=70, peak=200, platform=400, ac=11)
        self.assertAlmostEqual(delivered_power_kw(charger, vehicle, 50), 7.0, places=3)
        self.assertLess(delivered_power_kw(charger, vehicle, 98), 7.0)

    def test_fast_charge_time_ranges(self) -> None:
        charger_120 = DEFAULT_STATION[2]
        vehicle_400 = make_vehicle(battery=75, peak=300, platform=400)
        minutes = fast_charge_minutes(charger_120, vehicle_400, 10, 80)
        self.assertGreater(minutes, 20.0)
        self.assertLess(minutes, 50.0)

        charger_240 = DEFAULT_STATION[4]
        vehicle_800 = make_vehicle(battery=100, peak=400, platform=800)
        minutes_240 = fast_charge_minutes(charger_240, vehicle_800, 10, 80)
        self.assertGreater(minutes_240, 12.0)
        self.assertLess(minutes_240, 35.0)

    def test_energy_helper(self) -> None:
        vehicle = make_vehicle(battery=75)
        self.assertAlmostEqual(energy_for_soc(vehicle, 20, 80), 45.0, places=6)


class SimulatorTests(unittest.TestCase):
    """遥测生成与功率/电流。"""

    def setUp(self) -> None:
        self.sim = Simulator()

    def test_idle_telemetry(self) -> None:
        ac_pile = ChargingPile(1, charger=DEFAULT_STATION[0])
        dc_pile = ChargingPile(3, charger=DEFAULT_STATION[2])
        ac = self.sim.collect(ac_pile, None)
        dc = self.sim.collect(dc_pile, None)
        self.assertEqual(ac.power_kw, 0.0)
        self.assertEqual(ac.voltage, 220.0)
        self.assertEqual(dc.voltage, 0.0)
        self.assertEqual(dc.soc, 0.0)

    def test_charging_telemetry_and_no_false_alarm(self) -> None:
        pile = ChargingPile(3, charger=DEFAULT_STATION[2])
        session = ChargingSession(
            3,
            "闽A·1",
            make_vehicle(battery=75, peak=300, platform=400),
            datetime.now(),
            TariffEngine(),
            DEFAULT_STATION[2],
            20.0,
            80.0,
        )
        for _ in range(5):
            telemetry = self.sim.collect(pile, session)
            pile.push_telemetry(telemetry)
            self.assertGreater(telemetry.power_kw, 0.0)
            self.assertGreaterEqual(telemetry.current, 0.0)
        self.assertGreater(session.energy_kwh, 0.0)
        self.assertGreater(session.soc, 20.0)

    def test_make_plate(self) -> None:
        plate = make_plate(random.Random(1))
        self.assertRegex(plate, r"^闽[A-D]·[A-Z]\d{4}$")


class SessionManagerTests(unittest.TestCase):
    """会话状态机与自动充满。"""

    def setUp(self) -> None:
        self.orders = []
        self.manager = SessionManager(
            collector=Simulator(),
            on_order=self.orders.append,
        )

    def test_dc_start_parameters(self) -> None:
        session = self.manager.start_session(3)
        self.assertEqual(self.manager.pile(3).status, PileStatus.CHARGING)
        self.assertGreaterEqual(session.start_soc, 10.0)
        self.assertLessEqual(session.start_soc, 40.0)
        self.assertEqual(session.target_soc, 80.0)
        self.assertTrue(session.profile.model)
        self.assertTrue(session.charger.is_dc)

    def test_ac_start_parameters(self) -> None:
        session = self.manager.start_session(1)
        self.assertEqual(session.target_soc, 100.0)
        self.assertFalse(session.charger.is_dc)

    def test_double_start_rejected(self) -> None:
        self.manager.start_session(3)
        with self.assertRaises(RuntimeError):
            self.manager.start_session(3)

    def test_stop_without_session_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            self.manager.stop_session(2)

    def test_auto_stop_at_target_soc(self) -> None:
        session = self.manager.start_session(3)
        # 把电量推到目标前一点，下一轮 tick 应自动结算
        session.energy_kwh = (
            session.battery_kwh * (session.target_soc - session.start_soc) / 100.0
            - 0.001
        )
        session.soc = (
            session.start_soc
            + session.energy_kwh / session.battery_kwh * 100.0
        )
        completed = self.manager.tick()
        self.assertEqual(len(completed), 1)
        order = completed[0]
        self.assertEqual(order.status, "已完成")
        self.assertAlmostEqual(order.end_soc, 80.0, places=6)
        self.assertEqual(self.manager.pile(3).status, PileStatus.IDLE)
        self.assertEqual(self.orders, [order])

class DatabaseTests(unittest.TestCase):
    """SQLite 持久化、迁移、订单号与 CSV。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_order_fields_roundtrip(self) -> None:
        order = make_order()
        order_id = self.db.save_order(order)
        self.assertEqual(order.order_id, order_id)
        loaded = self.db.load_orders()[0]
        self.assertEqual(loaded["order_id"], order_id)
        self.assertEqual(loaded["vehicle_model"], "测试车")
        self.assertEqual(loaded["battery_kwh"], 75.0)
        self.assertEqual(loaded["charger_type"], "直流快充")
        self.assertAlmostEqual(loaded["start_soc"], 30.0, places=3)
        self.assertAlmostEqual(loaded["end_soc"], 76.7, places=1)
        self.assertEqual(loaded["duration_min"], 30.0)
        self.assertTrue(loaded["periods"])

    def test_order_ids_autoincrement_and_history_safe(self) -> None:
        history = make_order(start=datetime(2024, 1, 1, 10, 0))
        history.order_id = 1
        self.db.save_order(history)
        runtime = make_order(start=datetime(2024, 1, 2, 10, 0))
        self.db.save_order(runtime)
        self.assertNotEqual(runtime.order_id, 1)
        self.assertEqual(self.db.order_count(), 2)

    def test_explicit_order_id_idempotent(self) -> None:
        order = make_order()
        self.db.save_order(order)
        duplicate = make_order(start=datetime(2024, 1, 5, 10, 0))
        duplicate.order_id = order.order_id
        self.db.save_order(duplicate)
        self.assertEqual(self.db.order_count(), 1)

    def test_clear_orders(self) -> None:
        self.db.save_order(make_order())
        self.db.clear_orders()
        self.assertEqual(self.db.order_count(), 0)

    def test_csv_roundtrip_and_corrupt_rows(self) -> None:
        self.db.save_order(make_order())
        rows = self.db.load_orders()
        path = Path(self.tmp.name) / "orders.csv"
        export_orders(rows, path)
        loaded, skipped = load_orders_csv(path)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["vehicle_model"], "测试车")
        self.assertEqual(loaded[0]["battery_kwh"], 75.0)
        self.assertEqual(loaded[0]["charger_type"], "直流快充")

        with path.open("a", encoding="utf-8") as f:
            f.write("bad,row\n")
        loaded, skipped = load_orders_csv(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(skipped, 1)

    def test_legacy_schema_is_reset(self) -> None:
        old_path = Path(self.tmp.name) / "old.db"
        con = sqlite3.connect(old_path)
        con.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY, pile_id INTEGER NOT NULL, plate TEXT NOT NULL,
                start_time TEXT NOT NULL, end_time TEXT NOT NULL, energy_kwh REAL NOT NULL,
                energy_fee REAL NOT NULL, service_fee REAL NOT NULL, total_fee REAL NOT NULL,
                tariff_periods TEXT, status TEXT
            );
            INSERT INTO orders VALUES
                (1, 1, '闽A·OLD', '2024-01-01 10:00:00', '2024-01-01 10:30:00',
                 10.0, 3.7, 7.0, 10.7, '[]', '已完成');
            """
        )
        con.commit()
        con.close()

        # 旧结构（pile_id INTEGER / 无 spots、users 表）会被整体重置
        migrated = Database(old_path)
        self.assertEqual(migrated.load_orders(), [])
        self.assertEqual(migrated.load_spots(), [])
        self.assertIsNotNone(migrated.get_user("admin"))
        migrated.add_spot("A1", "fast", 120)
        self.assertEqual(len(migrated.load_spots()), 1)
        self.assertIsNotNone(migrated.get_user("A1"))
        migrated.save_order(make_order("A1", start=datetime(2024, 1, 2, 10, 0)))
        self.assertEqual(migrated.order_count(), 1)
        migrated.close()


class StatsTests(unittest.TestCase):
    """Pandas 聚合统计。"""

    def setUp(self) -> None:
        self.orders = [
            make_order(
                1,
                DEFAULT_STATION[2],
                make_vehicle(75, 300, 800, category="轿车"),
                energy=35,
                start=datetime(2024, 1, 1, 10, 30),
            ).to_dict(),
            make_order(
                2,
                DEFAULT_STATION[4],
                make_vehicle(100, 400, 800, category="SUV"),
                energy=50,
                start=datetime(2024, 1, 1, 18, 0),
                duration_min=35,
            ).to_dict(),
            make_order(
                3,
                DEFAULT_STATION[0],
                make_vehicle(60, 150, 400, category="轿车", segment="入门/紧凑"),
                energy=20,
                start=datetime(2024, 1, 2, 3, 0),
                duration_min=300,
            ).to_dict(),
        ]
        self.df = to_dataframe(self.orders)

    def test_overview(self) -> None:
        ov = overview(self.df)
        self.assertEqual(ov["order_count"], 3)
        self.assertAlmostEqual(ov["total_energy_kwh"], 105.0, places=2)
        self.assertAlmostEqual(ov["avg_battery_kwh"], 78.3, places=1)
        self.assertAlmostEqual(ov["dc_share"], 66.7, places=1)

    def test_daily_trend(self) -> None:
        daily = daily_trend(self.df)
        self.assertEqual(len(daily), 2)
        self.assertEqual(set(daily.columns), {"date", "energy_kwh", "total_fee", "order_count"})

    def test_period_distribution(self) -> None:
        periods = period_distribution(self.df)
        self.assertEqual(set(periods["period"]), {"峰段", "平段", "谷段"})
        self.assertAlmostEqual(periods["share_pct"].sum(), 100.0, delta=0.2)
        self.assertAlmostEqual(periods["energy_kwh"].sum(), 105.0, places=2)

    def test_pile_stats(self) -> None:
        piles = pile_stats(self.df)
        self.assertEqual(len(piles), 3)
        self.assertEqual(int(piles.loc[piles["pile_id"] == "1", "order_count"].iloc[0]), 1)

    def test_hourly_profile_covers_24_hours(self) -> None:
        hourly = hourly_profile(self.df)
        self.assertEqual(len(hourly), 24)
        self.assertEqual(hourly["hour"].tolist(), list(range(24)))

    def test_battery_and_mix_stats(self) -> None:
        battery = battery_distribution(self.df)
        self.assertEqual(int(battery["count"].sum()), 3)
        vehicles = vehicle_mix(self.df)
        self.assertEqual(int(vehicles["count"].sum()), 3)
        chargers = charger_mix(self.df)
        self.assertEqual(int(chargers["count"].sum()), 3)

    def test_empty_dataframe(self) -> None:
        empty = to_dataframe([])
        self.assertTrue(empty.empty)
        self.assertEqual(overview(empty)["order_count"], 0)
        self.assertEqual(overview(empty)["avg_battery_kwh"], 0.0)
        self.assertTrue(daily_trend(empty).empty)
        self.assertTrue(pile_stats(empty).empty)
        self.assertTrue(hourly_profile(empty).empty)
        self.assertAlmostEqual(period_distribution(empty)["energy_kwh"].sum(), 0.0)
        self.assertTrue(battery_distribution(empty).empty)
        self.assertTrue(vehicle_mix(empty).empty)


class AIConfigTests(unittest.TestCase):
    """通用 OpenAI 兼容配置与 URL 归一化。"""

    def test_defaults_and_url_normalization(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.persona_name, "小充")
        self.assertEqual(settings.app_title, DEFAULT_APP_TITLE)
        self.assertEqual(settings.tariff_profile, "state_grid")
        self.assertFalse(settings.use_local_summary)
        self.assertEqual(settings.effective_service_fee, 0.70)
        self.assertFalse(settings.configured)
        self.assertTrue(settings.chat_url.endswith("/chat/completions"))

        deepseek = AppSettings(
            base_url="https://api.deepseek.com/", api_key="k", model="deepseek-chat"
        )
        self.assertTrue(deepseek.configured)
        self.assertEqual(deepseek.chat_url, "https://api.deepseek.com/chat/completions")

        full = AppSettings(
            base_url="https://example.com/v1/chat/completions", api_key="k", model="m"
        )
        self.assertEqual(full.chat_url, "https://example.com/v1/chat/completions")

    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai.json"
            settings = AppSettings(
                app_title="测试高速充电站",
                tariff_profile="fujian",
                service_fee=0.50,
                refresh_interval_s=3.0,
                use_local_summary=True,
                base_url="https://api.example/v1",
                api_key="sk-test",
                model="test-model",
                persona_name="小充",
                persona_prompt="只回答数据问题",
            )
            saved = save_settings(settings, path)
            self.assertTrue(saved.exists())
            loaded = load_settings(path)
            self.assertEqual(loaded, settings)

    def test_invalid_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded.persona_name, DEFAULT_PERSONA_NAME)
            self.assertFalse(loaded.configured)

    def test_use_local_summary_legacy_mapping(self) -> None:
        self.assertTrue(
            AppSettings.from_dict({"send_local_summary": True}).use_local_summary
        )
        self.assertFalse(
            AppSettings.from_dict({"send_local_summary": False}).use_local_summary
        )
        self.assertFalse(AppSettings.from_dict({}).use_local_summary)

    def test_legacy_ai_settings_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "settings.json"
            legacy = Path(tmp) / "ai_settings.json"
            legacy.write_text(
                json.dumps(
                    {
                        "base_url": "https://api.legacy/v1",
                        "api_key": "sk-legacy",
                        "model": "legacy-model",
                        "persona_name": "老充",
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_settings(primary)
            self.assertEqual(loaded.base_url, "https://api.legacy/v1")
            self.assertEqual(loaded.api_key, "sk-legacy")
            self.assertEqual(loaded.model, "legacy-model")
            self.assertEqual(loaded.persona_name, "老充")
            self.assertEqual(loaded.app_title, DEFAULT_APP_TITLE)

    def test_system_prompt_contains_persona(self) -> None:
        settings = AppSettings(persona_name="阿充", persona_prompt="先给结论")
        prompt = system_prompt(settings)
        self.assertIn("阿充", prompt)
        self.assertIn("先给结论", prompt)

    def test_tariff_engine_uses_profile_and_override(self) -> None:
        fujian = tariff_engine(AppSettings(tariff_profile="fujian"))
        self.assertAlmostEqual(fujian.service_fee, 0.40, places=2)
        custom = tariff_engine(AppSettings(tariff_profile="state_grid", service_fee=0.55))
        self.assertAlmostEqual(custom.service_fee, 0.55, places=2)


class FakeResponse:
    def __init__(self, data=None, status: int = 200, text: str = "") -> None:
        self._data = data or {}
        self.status_code = status
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._data


class AssistantTests(unittest.TestCase):
    """通用 /chat/completions 调用、错误处理与提示词。"""

    def setUp(self) -> None:
        self.settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="sk-key",
            model="test-model",
            persona_name="小充",
        )

    def test_chat_success(self) -> None:
        response = FakeResponse({"choices": [{"message": {"content": "你好"}}]})
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response) as post:
            answer = chat(self.settings, [{"role": "user", "content": "hi"}])
        self.assertEqual(answer, "你好")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.example/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-key")
        self.assertEqual(kwargs["json"]["model"], "test-model")

    def test_chat_not_configured(self) -> None:
        with self.assertRaises(AIError):
            chat(AppSettings(), [{"role": "user", "content": "hi"}])

    def test_chat_content_list(self) -> None:
        response = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "A"},
                                {"type": "text", "text": "B"},
                            ]
                        }
                    }
                ]
            }
        )
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response):
            self.assertEqual(chat(self.settings, [{"role": "user", "content": "hi"}]), "AB")

    def test_answer_question_sends_summary_and_question(self) -> None:
        response = FakeResponse({"choices": [{"message": {"content": "分析结果"}}]})
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response) as post:
            result = answer_question(self.settings, "汇总内容", "哪个时段收益最高？")
        self.assertEqual(result, "分析结果")
        payload = post.call_args.kwargs["json"]
        self.assertIn("你的名字是「小充」", payload["messages"][0]["content"])
        user = payload["messages"][-1]["content"]
        self.assertIn("汇总内容", user)
        self.assertIn("哪个时段收益最高", user)

    def test_http_error_is_wrapped(self) -> None:
        response = FakeResponse({}, status=401, text='{"error":"unauthorized"}')
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response):
            with self.assertRaises(AIError) as ctx:
                chat(self.settings, [{"role": "user", "content": "hi"}])
        self.assertIn("401", str(ctx.exception))

    def test_answer_question_raw_kind(self) -> None:
        response = FakeResponse({"choices": [{"message": {"content": "原始数据分析"}}]})
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response) as post:
            result = answer_question(
                self.settings, "单号,桩位\n1,3", "哪个时段收益最高？", data_kind="raw"
            )
        self.assertEqual(result, "原始数据分析")
        system = post.call_args.kwargs["json"]["messages"][0]["content"]
        user = post.call_args.kwargs["json"]["messages"][-1]["content"]
        self.assertIn("原始订单数据", system)
        self.assertIn("全部订单原始数据", user)

    def test_answer_without_summary_uses_generic_prompt(self) -> None:
        response = FakeResponse({"choices": [{"message": {"content": "通用建议"}}]})
        with mock.patch("evcharge.application.assistant.requests.post", return_value=response) as post:
            result = answer_question(self.settings, "", "怎么优化？")
        self.assertEqual(result, "通用建议")
        user = post.call_args.kwargs["json"]["messages"][-1]["content"]
        self.assertIn("本次未向模型提供本站点数据", user)


class SiteSummaryTests(unittest.TestCase):
    """本地汇总与管理员问题结论。"""

    def setUp(self) -> None:
        self.orders = [
            make_order(
                1, DEFAULT_STATION[2],
                make_vehicle(75, 300, 800, category="轿车"),
                energy=35, start=datetime(2024, 1, 1, 10, 30),
            ).to_dict(),
            make_order(
                2, DEFAULT_STATION[4],
                make_vehicle(100, 400, 800, category="SUV"),
                energy=50, start=datetime(2024, 1, 1, 18, 0), duration_min=35,
            ).to_dict(),
            make_order(
                3, DEFAULT_STATION[0],
                make_vehicle(60, 150, 400, category="轿车", segment="入门/紧凑"),
                energy=20, start=datetime(2024, 1, 2, 3, 0), duration_min=300,
            ).to_dict(),
        ]

    def test_summary_statistics(self) -> None:
        summary = build_site_summary(self.orders)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.order_count, 3)
        self.assertAlmostEqual(summary.total_energy_kwh, 105.0, places=1)
        self.assertGreater(summary.total_fee, 0)
        self.assertIsNotNone(summary.top_period())
        self.assertIsNotNone(summary.top_model())
        self.assertIsNotNone(summary.top_pile())
        self.assertIsNotNone(summary.top_battery())
        self.assertIn("【总览】", summary.to_text())
        self.assertIn("【峰/平/谷收益】", summary.to_text())

    def test_every_question_has_local_answer(self) -> None:
        summary = build_site_summary(self.orders)
        assert summary is not None
        for question in SITE_QUESTIONS:
            answer = summary.local_answer(question["key"])
            self.assertIn("本地数据", answer)

    def test_empty_orders(self) -> None:
        self.assertIsNone(build_site_summary([]))

    def test_raw_data_contains_all_orders(self) -> None:
        raw = build_raw_data_text(self.orders)
        self.assertIn("全部订单原始数据", raw)
        self.assertIn("共 3 条", raw)
        self.assertIn("单号", raw)
        self.assertIn("测试车", raw)
        self.assertEqual(len([line for line in raw.splitlines() if line]), 5)  # 前缀+表头+3行
        self.assertIn("共 0 条", build_raw_data_text([]))


class ChartTests(unittest.TestCase):
    """图表空数据容错。"""

    def test_pie_with_all_zero_values_does_not_raise(self) -> None:
        image = pie_chart(["峰段", "平段", "谷段"], [0, 0, 0], width=200, height=100)
        self.assertIsInstance(image, ft.Image)

    def test_empty_line_and_bar(self) -> None:
        self.assertIsInstance(line_chart([], width=200, height=100), ft.Image)
        self.assertIsInstance(bar_chart([], [], width=200, height=100), ft.Image)

    def test_pie_with_data(self) -> None:
        image = pie_chart(["峰段", "平段", "谷段"], [50, 30, 20], width=200, height=100)
        self.assertIsInstance(image, ft.Image)


class _PickedFile:
    """模拟 flet 的 FilePickerFile：原生平台可能只有 bytes、没有 path。"""

    def __init__(self, path=None, data=None, name="plate.jpg") -> None:
        self.path = path
        self.bytes = data
        self.name = name


class FakeVision:
    """VisionService 替身：用固定结果或异常替代真实识别/拍照。"""

    def __init__(self, plate: str | None = None, error: Exception | None = None):
        self.plate = plate
        self.error = error
        self.captured = None
        self.calls: list = []

    def available(self, settings):
        """与真实实现一致的「先看配置」语义：没 Key 就不可用。"""
        if not (getattr(settings, "api_key", "") or "").strip():
            return False, "未配置 AI API Key（设置 → AI 服务）"
        if self.error is not None:
            return False, str(self.error)
        return True, ""

    def recognize(self, image_path, settings):
        self.calls.append(str(image_path))
        if self.error is not None:
            raise self.error
        return self.plate, self.plate

    def capture(self):
        self.captured = "captured.jpg"
        return None, "已打开系统相机"


class FakePage:
    """只记录调用的最小 Page 替身，用于无窗口测试 UI 逻辑。"""

    def __init__(self) -> None:
        self.updates = 0
        self.dialog = None
        # FilePicker 等 Flet 服务在 build 时会挂到这两个列表上
        self.services: list = []
        self.overlay: list = []
        self.tasks: list = []

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        self.dialog = dialog

    def pop_dialog(self):
        self.dialog = None
        return None

    def run_task(self, handler, *args, **kwargs):
        """不真正执行异步任务，只记录，避免测试里起事件循环。"""
        self.tasks.append((handler, args, kwargs))
        return None


class PlateRecognitionTests(unittest.TestCase):
    """车牌识别：文本清洗、图片校验、OpenAI 兼容视觉调用与错误处理。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / "plate.jpg"
        self.image.write_bytes(b"\xff\xd8\xff fake image bytes")
        # 车牌识别与 AI 报告共用一套配置
        self.settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="sk-test",
            model="text-model",
            vision_model="vision-model",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ---------- 文本清洗 ----------
    def test_normalize_common_formats(self) -> None:
        self.assertEqual(normalize_plate("闽D·G6592"), "闽D·G6592")
        self.assertEqual(normalize_plate("粤B88888"), "粤B·88888")
        self.assertEqual(normalize_plate("  京A 12345 "), "京A·12345")
        self.assertEqual(normalize_plate("车牌号为：浙A12345。"), "浙A·12345")
        self.assertEqual(normalize_plate("鲁AF12345"), "鲁A·F12345")

    def test_normalize_rejects_non_plate(self) -> None:
        for text in ("", "未识别到", "ABC", "12345678", "hello world"):
            self.assertEqual(normalize_plate(text), "", text)
            self.assertFalse(is_plate_like(text))

    def test_normalize_fixes_letter_digit_confusion(self) -> None:
        # 模型常把数字 0/1 认成字母 O/I
        self.assertEqual(normalize_plate("闽DO6592"), "闽D·06592")

    def test_format_plate(self) -> None:
        self.assertEqual(format_plate("闽DG6592"), "闽D·G6592")
        self.assertEqual(format_plate("闽A·12345"), "闽A·12345")

    # ---------- 前置校验 ----------
    def test_requires_url_and_key(self) -> None:
        ok, message = recognition_available(AppSettings(model="m"))
        self.assertFalse(ok)
        self.assertIn("API Key", message)

        with self.assertRaises(PlateRecognitionError) as ctx:
            recognize_plate(self.image, AppSettings(model="m"))
        self.assertIn("未配置 AI 接口", str(ctx.exception))

    def test_requires_vision_model(self) -> None:
        settings = AppSettings(base_url="https://api.example/v1", api_key="k")
        settings.model = ""
        settings.vision_model = ""
        ok, message = recognition_available(settings)
        self.assertFalse(ok)
        self.assertIn("视觉模型", message)

    def test_vision_model_falls_back_to_chat_model(self) -> None:
        settings = AppSettings(
            base_url="https://api.example/v1", api_key="k", model="gpt-4o-mini"
        )
        self.assertEqual(settings.effective_vision_model, "gpt-4o-mini")
        self.assertTrue(settings.vision_configured)
        ok, _message = recognition_available(settings)
        self.assertTrue(ok)

    def test_missing_and_unsupported_image(self) -> None:
        with self.assertRaises(PlateRecognitionError) as ctx:
            recognize_plate(Path(self.tmp.name) / "nope.jpg", self.settings)
        self.assertIn("不存在", str(ctx.exception))

        pdf = Path(self.tmp.name) / "plate.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with self.assertRaises(PlateRecognitionError) as ctx:
            recognize_plate(pdf, self.settings)
        self.assertIn("不支持", str(ctx.exception))

    def test_oversized_image_rejected(self) -> None:
        big = Path(self.tmp.name) / "big.jpg"
        big.write_bytes(b"0" * (9 * 1024 * 1024))
        with self.assertRaises(PlateRecognitionError) as ctx:
            recognize_plate(big, self.settings)
        self.assertIn("太大", str(ctx.exception))

    # ---------- 调用与解析 ----------
    def test_success_uses_chat_completions_and_vision_model(self) -> None:
        payload = {"choices": [{"message": {"content": "闽D·G6592"}}]}
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=FakeResponse(payload),
        ) as post:
            plate, raw = recognize_plate(self.image, self.settings)

        self.assertEqual(plate, "闽D·G6592")
        self.assertEqual(raw, "闽D·G6592")
        url = post.call_args.args[0]
        self.assertEqual(url, "https://api.example/v1/chat/completions")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "vision-model")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
        content = kwargs["json"]["messages"][0]["content"]
        self.assertTrue(any(part["type"] == "image_url" for part in content))
        image_url = next(p["image_url"]["url"] for p in content if p["type"] == "image_url")
        self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))

    def test_accepts_base_url_already_ending_with_path(self) -> None:
        settings = AppSettings(
            base_url="https://api.example/v1/chat/completions",
            api_key="k",
            vision_model="v",
        )
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "粤B·88888"}}]}
            ),
        ) as post:
            recognize_plate(self.image, settings)
        self.assertEqual(post.call_args.args[0], "https://api.example/v1/chat/completions")

    def test_segmented_content_is_joined(self) -> None:
        payload = {
            "choices": [{"message": {"content": [{"type": "text", "text": "闽D·G6592"}]}}]
        }
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=FakeResponse(payload),
        ):
            plate, _raw = recognize_plate(self.image, self.settings)
        self.assertEqual(plate, "闽D·G6592")

    def test_model_says_no_plate(self) -> None:
        payload = {"choices": [{"message": {"content": "未识别到"}}]}
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaises(PlateRecognitionError) as ctx:
                recognize_plate(self.image, self.settings)
        self.assertIn("没有识别到", str(ctx.exception))

    def test_http_error_is_wrapped(self) -> None:
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=FakeResponse({}, status=401, text="invalid api key"),
        ):
            with self.assertRaises(PlateRecognitionError) as ctx:
                recognize_plate(self.image, self.settings)
        self.assertIn("401", str(ctx.exception))

    def test_non_json_response_is_wrapped(self) -> None:
        class BadJson(FakeResponse):
            def json(self):
                raise ValueError("not json")

        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            return_value=BadJson({}, text="<html>oops</html>"),
        ):
            with self.assertRaises(PlateRecognitionError) as ctx:
                recognize_plate(self.image, self.settings)
        self.assertIn("不是 JSON", str(ctx.exception))

    def test_network_error_is_wrapped(self) -> None:
        with mock.patch(
            "evcharge.infrastructure.plate_recognition.requests.post",
            side_effect=requests.ConnectionError("boom"),
        ):
            with self.assertRaises(PlateRecognitionError) as ctx:
                recognize_plate(self.image, self.settings)
        self.assertIn("失败", str(ctx.exception))

    # ---------- 设置项 ----------
    def test_settings_roundtrip(self) -> None:
        settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="k",
            model="chat-model",
            vision_model="vision-model",
        )
        loaded = AppSettings.from_dict(settings.to_dict())
        self.assertEqual(loaded.vision_model, "vision-model")
        self.assertEqual(loaded.effective_vision_model, "vision-model")
        self.assertTrue(loaded.vision_configured)

        # 旧版 ocr_* 字段被忽略，不会污染新配置
        legacy = AppSettings.from_dict({"ocr_api_key": "old", "ocr_provider": "volcengine"})
        self.assertEqual(legacy.api_key, "")
        self.assertFalse(legacy.vision_configured)


class StartSessionWithPlateTests(unittest.TestCase):
    """上电时使用识别出的车牌。"""

    def setUp(self) -> None:
        self.manager = SessionManager(pile_count=6)

    def test_explicit_plate_is_used(self) -> None:
        session = self.manager.start_session(1, plate="闽D·G6592")
        self.assertEqual(session.plate, "闽D·G6592")
        # 结算后的订单也要带上这个车牌
        order = self.manager.stop_session(1)
        self.assertEqual(order.plate, "闽D·G6592")

    def test_blank_plate_falls_back_to_random(self) -> None:
        session = self.manager.start_session(1, plate="   ")
        self.assertRegex(session.plate, r"^闽[A-D]·[A-Z]\d{4}$")

    def test_plate_does_not_bypass_status_check(self) -> None:
        self.manager.start_session(1, plate="闽D·G6592")
        with self.assertRaises(RuntimeError):
            self.manager.start_session(1, plate="闽D·G6592")


class MonitorPageTests(unittest.TestCase):
    """监控页实时刷新与账单弹窗。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "monitor.db")
        self.manager = SessionManager(
            collector=Simulator(),
            on_order=self.db.save_order,
        )
        self.page = FakePage()
        self.monitor = MonitorPage(self.manager)
        self.monitor.page = self.page
        self.monitor.selected_pile_id = 3
        self.monitor._build_detail()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_detail_metrics_and_soc_refresh(self) -> None:
        session = self.manager.start_session(3)
        session.soc = 80.0
        self.manager.pile(3).push_telemetry(
            Telemetry(datetime.now(), 400.0, 250.0, 100.0, 35.0, 80.0)
        )
        self.monitor._refresh_detail()
        self.assertEqual(self.monitor.metric_values["电压"].value, "400.0 V")
        self.assertEqual(self.monitor.metric_values["功率"].value, "100.0 kW")
        self.assertEqual(self.monitor.metric_values["本次电量"].value, "35.00 kWh")
        self.assertIn("SOC 80.0%", self.monitor.soc_text.value)
        # 桩型放在标题，状态行只留功率与状态（界面精简后不再重复）
        self.assertIn("直流快充", self.monitor.detail_title.value)
        self.assertIn("120kW", self.monitor.detail_status.value)
        self.assertIn("充电中", self.monitor.detail_status.value)

    def test_bill_dialog_is_shown(self) -> None:
        self.manager.start_session(3)
        self.manager.tick()
        order = self.manager.stop_session(3)
        self.monitor._show_bill(order)
        self.assertIsNotNone(self.page.dialog)
        self.assertEqual(self.page.dialog.title.value, "充电结算账单")
        self.assertIn(order.vehicle_model, str(self.page.dialog.content.controls))

class MonitorPlateRecognitionTests(unittest.TestCase):
    """监控页：拍照/相册识别车牌后直接上电。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "monitor_ocr.db")
        self.manager = SessionManager(collector=Simulator(), on_order=self.db.save_order)
        self.page = FakePage()
        self.image = Path(self.tmp.name) / "plate.jpg"
        self.image.write_bytes(b"\xff\xd8\xff fake image bytes")
        self.vision = FakeVision(plate="闽D·G6592")
        self.settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="sk-test",
            vision_model="vision-model",
        )
        self.monitor = MonitorPage(
            self.manager,
            settings_provider=self._settings,
            vision=self.vision,
        )
        self.monitor.page = self.page
        self.monitor.selected_pile_id = 3
        self.monitor._build_detail()

    def _settings(self):
        """返回当前设置对象（测试里会替换 self.settings 模拟不同配置）。"""
        return self.settings

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_build_renders_recognition_buttons(self) -> None:
        """完整 build() 要能挂上 FilePicker 并渲染两个识别按钮。"""
        monitor = MonitorPage(
            self.manager,
            settings_provider=lambda: AppSettings(),
            vision=FakeVision(),
        )
        control = monitor.build(self.page)
        self.assertIsInstance(control, ft.Control)
        self.assertEqual(monitor.camera_btn.content, "拍照识别")
        self.assertEqual(monitor.gallery_btn.content, "相册识别")
        self.assertEqual(len(self.page.services), 1)

    def test_file_picker_is_registered_once(self) -> None:
        self.monitor._mount_file_picker(self.page)
        self.monitor._mount_file_picker(self.page)  # 重复挂载不应产生第二个实例
        self.assertEqual(len(self.page.services), 1)

    def test_unconfigured_key_warns_and_does_not_start(self) -> None:
        self.settings = AppSettings(api_key="")  # 未配 Key
        asyncio.run(self.monitor._recognize_and_start(str(self.image)))

        self.assertIn("未配置", self.monitor.plate_hint.value)
        self.assertEqual(self.manager.pile(3).status, PileStatus.IDLE)

    def test_successful_recognition_starts_with_plate(self) -> None:
        asyncio.run(self.monitor._recognize_and_start(str(self.image)))

        self.assertIn("闽D·G6592", self.monitor.plate_hint.value)
        session = self.manager.pile(3).current_session
        self.assertIsNotNone(session)
        self.assertEqual(session.plate, "闽D·G6592")

    def test_recognition_failure_keeps_pile_idle(self) -> None:
        self.vision.error = PlateRecognitionError("没有识别到车牌")
        asyncio.run(self.monitor._recognize_and_start(str(self.image)))

        self.assertIn("没有识别到", self.monitor.plate_hint.value)
        self.assertEqual(self.manager.pile(3).status, PileStatus.IDLE)

    def test_start_with_plate_uses_given_plate(self) -> None:
        self.monitor._start_with_plate("粤B·88888")
        session = self.manager.pile(3).current_session
        self.assertIsNotNone(session)
        self.assertEqual(session.plate, "粤B·88888")

    def test_start_with_plate_on_charging_pile_shows_warning(self) -> None:
        self.manager.start_session(3)
        self.monitor._start_with_plate("粤B·88888")
        self.assertIn("正在充电", self.monitor.plate_hint.value)


class HistoryGeneratorTests(unittest.TestCase):
    """历史数据排班、电量守恒与同桩不重叠。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spots = [
            SpotConfig("sp_a1", "A001", ChargerType.AC, 7.0),
            SpotConfig("sp_a2", "A002", ChargerType.AC, 7.0),
            SpotConfig("sp_d1", "B001", ChargerType.DC, 120.0),
            SpotConfig("sp_d2", "B002", ChargerType.DC, 240.0),
        ]
        cls.orders = generate_history(days=5, seed=1, spots=cls.spots)

    def test_orders_generated(self) -> None:
        self.assertGreater(len(self.orders), 20)
        self.assertTrue(any(o.charger_type == "交流慢充" for o in self.orders))
        self.assertTrue(any(o.charger_type == "直流快充" for o in self.orders))

    def test_energy_consistent_with_soc(self) -> None:
        for order in self.orders:
            expected = order.battery_kwh * (order.end_soc - order.start_soc) / 100.0
            self.assertAlmostEqual(order.energy_kwh, expected, places=6)
            self.assertGreater(order.energy_kwh, 0.0)
            self.assertGreater(order.duration_min, 0.0)
            self.assertTrue(order.vehicle_model)

    def test_no_overlap_per_pile(self) -> None:
        grouped = {}
        for order in self.orders:
            grouped.setdefault(order.pile_id, []).append(order)
        self.assertEqual(len(grouped), 4)
        for pile_orders in grouped.values():
            pile_orders.sort(key=lambda o: o.start_time)
            for previous, current in zip(pile_orders, pile_orders[1:]):
                self.assertGreaterEqual(current.start_time, previous.end_time)

    def test_orders_are_bounded(self) -> None:
        energies = [o.energy_kwh for o in self.orders]
        self.assertGreater(min(energies), 0.0)
        self.assertLess(max(energies), 150.0)
        for order in self.orders:
            self.assertGreaterEqual(order.start_soc, 0.0)
            self.assertLessEqual(order.start_soc, 100.0)
            self.assertLessEqual(order.end_soc, 100.0)


class AIPageTests(unittest.TestCase):
    """AI 页面：本地汇总、未配置提示、问题本地结论。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "ai.db")
        self.db.save_orders(
            [
                make_order(1, DEFAULT_STATION[2], make_vehicle(75, 300, 800), energy=35),
                make_order(
                    2,
                    DEFAULT_STATION[4],
                    make_vehicle(100, 400, 800, category="SUV"),
                    energy=50,
                    start=datetime(2024, 1, 1, 18, 0),
                ),
            ]
        )
        self.page = FakePage()
        self.settings = AppSettings()
        self.orders_service = build_context(db=self.db).orders
        self.ai = AIPage(self.orders_service, settings_provider=lambda: self.settings)
        self.ai.build(self.page)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_initial_provider_warning(self) -> None:
        self.assertIn("尚未配置", self.ai.provider_text.value)

    def test_summarize_raw_mode_does_not_build_summary(self) -> None:
        self.ai._summarize()
        self.assertIsNotNone(self.ai.orders)
        self.assertIsNone(self.ai.summary)
        self.assertGreater(self.page.updates, 0)
        self.assertIsNotNone(self.ai.answer_box)

    def test_ask_without_config_raw_mode(self) -> None:
        self.ai._summarize()
        asyncio.run(
            self.ai._ask("哪个时段收益最高？", "本地数据：峰段收益最高。", "时段收益")
        )
        column = self.ai.answer_box.content
        self.assertIsInstance(column, ft.Column)
        self.assertEqual(len(column.controls), 2)
        self.assertIn("不本地汇总", column.controls[0].value)
        self.assertIn("尚未配置", column.controls[1].value)

    def test_raw_mode_sends_all_orders_to_model(self) -> None:
        self.settings = AppSettings(
            base_url="https://api.example/v1", api_key="k", model="m"
        )
        with mock.patch(
            "evcharge.presentation.pages.ai_page.answer_question",
            return_value="AI 回答",
        ) as ask:
            self.ai._summarize()
            asyncio.run(
                self.ai._ask("哪个时段收益最高？", "", "时段收益")
            )
        args = ask.call_args.args
        self.assertEqual(args[3], "raw")
        self.assertIn("全部订单原始数据", args[1])
        self.assertIn("单号", args[1])

    def test_local_summary_mode_sends_summary(self) -> None:
        self.settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="k",
            model="m",
            use_local_summary=True,
        )
        with mock.patch(
            "evcharge.presentation.pages.ai_page.answer_question",
            return_value="AI 回答",
        ) as ask:
            self.ai._summarize()
            asyncio.run(
                self.ai._ask("哪个时段收益最高？", "本地事实", "时段收益")
            )
        args = ask.call_args.args
        self.assertEqual(args[3], "summary")
        self.assertIn("【总览】", args[1])

    def test_local_summary_mode_displays_local_fact(self) -> None:
        self.settings = AppSettings(use_local_summary=True)
        self.ai._summarize()
        asyncio.run(
            self.ai._ask("哪个时段收益最高？", "本地数据：峰段收益最高。", "时段收益")
        )
        column = self.ai.answer_box.content
        self.assertIn("峰段收益最高", column.controls[1].value)
        self.assertIn("尚未配置", column.controls[3].value)


class ChargingRuntimeTests(unittest.TestCase):
    """采集循环：单次异常不中断计时、自动结算回调、界面刷新隔离。"""

    def setUp(self) -> None:
        self.manager = SessionManager(pile_count=6)
        self.completed: list = []
        self.ticks: list = []

    def _runtime(self, **kwargs) -> ChargingRuntime:
        return ChargingRuntime(
            self.manager,
            on_completed=self.completed.append,
            on_tick=lambda: self.ticks.append(1),
            **kwargs,
        )

    def test_tick_once_counts_and_calls_hooks(self) -> None:
        runtime = self._runtime()
        runtime.tick_once()
        self.assertEqual(runtime.ticks, 1)
        self.assertEqual(runtime.errors, 0)
        self.assertEqual(len(self.ticks), 1)
        self.assertEqual(self.completed, [])  # 没有会话，不该有订单

    def test_manager_exception_does_not_propagate(self) -> None:
        runtime = self._runtime()
        with mock.patch.object(
            self.manager, "tick", side_effect=RuntimeError("boom")
        ):
            result = runtime.tick_once()  # 不抛异常
        self.assertEqual(result, [])
        self.assertEqual(runtime.errors, 1)
        self.assertEqual(runtime.ticks, 0)

    def test_ui_callback_exception_does_not_stop_ticking(self) -> None:
        runtime = ChargingRuntime(
            self.manager, on_tick=lambda: (_ for _ in ()).throw(RuntimeError("ui"))
        )
        runtime.tick_once()
        runtime.tick_once()  # 仍能继续推进
        self.assertEqual(runtime.ticks, 2)
        self.assertEqual(runtime.errors, 0)

    def test_auto_stop_order_reaches_callback(self) -> None:
        session = self.manager.start_session(3)
        session.energy_kwh = (
            session.battery_kwh * (session.target_soc - session.start_soc) / 100.0
            - 0.001
        )
        session.soc = (
            session.start_soc + session.energy_kwh / session.battery_kwh * 100.0
        )
        runtime = self._runtime()
        runtime.tick_once()
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(self.completed[0][0].status, "已完成")

    def test_run_forever_keeps_running_after_failures(self) -> None:
        """即使每步都抛异常，循环也不能退出（充电不能因界面问题停摆）。"""
        runtime = self._runtime()
        calls = {"n": 0}

        async def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise asyncio.CancelledError  # 用取消退出循环

        with mock.patch.object(
            self.manager, "tick", side_effect=RuntimeError("boom")
        ), mock.patch("evcharge.application.runtime.asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(runtime.run_forever())

        self.assertEqual(runtime.errors, 3)  # 三次都失败，但循环没停

    def test_interval_is_clamped(self) -> None:
        self.assertGreaterEqual(self._runtime(interval_s=0.01).interval_s, 0.5)
        self.assertEqual(self._runtime(interval_s=5).interval_s, 5)


class SecretsTests(unittest.TestCase):
    """API Key 落盘保护：钥匙串 / base64 混淆 / 老明文兼容。"""

    def test_obfuscate_roundtrip(self) -> None:
        text = "sk-测试-key-123"
        hidden = secret_store.obfuscate(text)
        self.assertNotIn("sk-", hidden)
        self.assertTrue(hidden.startswith(secret_store.PREFIX_B64))
        self.assertEqual(secret_store.deobfuscate(hidden), text)

    def test_empty_values(self) -> None:
        self.assertEqual(secret_store.obfuscate(""), "")
        self.assertEqual(secret_store.protect(""), "")
        self.assertEqual(secret_store.reveal(""), "")

    def test_plaintext_legacy_is_still_readable(self) -> None:
        self.assertEqual(secret_store.reveal("sk-old-plaintext"), "sk-old-plaintext")

    def test_broken_base64_does_not_crash(self) -> None:
        self.assertEqual(secret_store.reveal("b64:!!!!"), "")

    def test_protect_falls_back_to_base64_without_keyring(self) -> None:
        with mock.patch.object(secret_store, "_keyring", return_value=None):
            stored = secret_store.protect("sk-abc")
        self.assertTrue(stored.startswith(secret_store.PREFIX_B64))
        with mock.patch.object(secret_store, "_keyring", return_value=None):
            self.assertEqual(secret_store.reveal(stored), "sk-abc")

    def test_protect_uses_keyring_when_available(self) -> None:
        fake = mock.Mock()
        fake.set_password.return_value = None
        fake.get_password.return_value = "sk-from-keyring"
        with mock.patch.object(secret_store, "_keyring", return_value=fake):
            stored = secret_store.protect("sk-from-keyring")
            self.assertTrue(stored.startswith(secret_store.PREFIX_KEYRING))
            self.assertEqual(secret_store.reveal(stored), "sk-from-keyring")
        fake.set_password.assert_called_once()

    def test_keyring_unavailable_when_reading_reference(self) -> None:
        with mock.patch.object(secret_store, "_keyring", return_value=None):
            self.assertEqual(secret_store.reveal("keyring:ai_api_key"), "")

    def test_saved_settings_never_contain_plaintext_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(api_key="sk-plain-should-not-leak")
            with mock.patch.object(secret_store, "_keyring", return_value=None):
                save_settings(settings, path)
                raw = path.read_text(encoding="utf-8")
                self.assertNotIn("sk-plain-should-not-leak", raw)
                self.assertEqual(
                    load_settings(path).api_key, "sk-plain-should-not-leak"
                )


class DataDirTests(unittest.TestCase):
    """数据/资源目录：源码运行与打包运行都要落到正确位置。"""

    def _reload_paths(self):
        import importlib

        from evcharge.infrastructure import paths

        return importlib.reload(paths)

    def test_env_override_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"FLET_APP_STORAGE_DATA": tmp}):
                paths = self._reload_paths()
                try:
                    self.assertEqual(paths.DATA_DIR, Path(tmp))
                    self.assertEqual(
                        paths.SETTINGS_PATH, Path(tmp) / "settings.json"
                    )
                finally:
                    os.environ.pop("FLET_APP_STORAGE_DATA", None)
                    self._reload_paths()

    def test_default_is_project_data_dir(self) -> None:
        paths = self._reload_paths()
        self.assertEqual(paths.DATA_DIR.name, "data")
        self.assertFalse(paths.is_frozen())

    def test_frozen_uses_bundle_root_for_assets(self) -> None:
        """打包后随包资源在 _MEIPASS/assets，而不是项目目录。"""
        with tempfile.TemporaryDirectory() as meipass:
            with mock.patch.object(sys, "_MEIPASS", meipass, create=True), \
                 mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.dict(os.environ, {"FLET_APP_STORAGE_DATA": meipass}):
                paths = self._reload_paths()
                try:
                    self.assertTrue(paths.is_frozen())
                    self.assertEqual(paths.bundle_root(), Path(meipass))
                    self.assertEqual(paths.ASSETS_DIR, Path(meipass) / "assets")
                    self.assertEqual(paths.DATA_DIR, Path(meipass))
                finally:
                    os.environ.pop("FLET_APP_STORAGE_DATA", None)
                    self._reload_paths()

    def test_frozen_data_dir_is_writable_before_project_dir(self) -> None:
        """打包后即使没有 FLET_APP_STORAGE_DATA，也不该往程序目录写。"""
        with tempfile.TemporaryDirectory() as meipass, \
             tempfile.TemporaryDirectory() as fake_home:
            # Path.home() 读 $HOME；指到临时目录，保证用户数据目录可创建
            with mock.patch.object(sys, "_MEIPASS", meipass, create=True), \
                 mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.dict(os.environ, {"HOME": fake_home}):
                os.environ.pop("FLET_APP_STORAGE_DATA", None)
                paths = self._reload_paths()
                try:
                    self.assertIn(paths.APP_DIR_NAME, str(paths.DATA_DIR))
                    self.assertNotEqual(paths.DATA_DIR, Path(meipass))
                    self.assertIn("Library", str(paths.DATA_DIR))
                finally:
                    self._reload_paths()


class ArchitectureTests(unittest.TestCase):
    """分层约束：表现层不得直接依赖基础设施（防止重构后偷偷回退）。"""

    SRC = Path(__file__).resolve().parent.parent / "src" / "evcharge"

    def _imports(self, folder: str) -> list[tuple[str, str]]:
        found = []
        for path in (self.SRC / folder).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and "infrastructure" in stripped:
                    found.append((str(path.relative_to(self.SRC)), stripped))
        return found

    def test_presentation_does_not_import_infrastructure(self) -> None:
        offenders = self._imports("presentation")
        self.assertEqual(offenders, [], f"表现层不应直接依赖基础设施：{offenders}")

    def test_application_does_not_import_infrastructure(self) -> None:
        offenders = self._imports("application")
        self.assertEqual(offenders, [], f"应用层不应直接依赖基础设施：{offenders}")

    def test_domain_does_not_import_infrastructure(self) -> None:
        offenders = self._imports("domain")
        self.assertEqual(offenders, [], f"领域层不应直接依赖基础设施：{offenders}")

    def test_application_does_not_import_presentation(self) -> None:
        bad = []
        for path in (self.SRC / "application").rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "evcharge.presentation" in line:
                    bad.append(str(path.relative_to(self.SRC)))
        self.assertEqual(bad, [])

    def test_composition_root_exists_and_builds_context(self) -> None:
        from evcharge.application.services import AppContext

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "arch.db")
            try:
                ctx = build_context(db=db)
                self.assertIsInstance(ctx, AppContext)
                for attr in ("orders", "accounts", "spots", "settings", "vision"):
                    self.assertIsNotNone(getattr(ctx, attr), attr)
            finally:
                db.close()


class PackagingTests(unittest.TestCase):
    """打包产物：脚本齐备且指向正确入口。"""

    ROOT = Path(__file__).resolve().parent.parent

    def test_build_scripts_exist(self) -> None:
        for name in ("build_windows.ps1", "build_macos.sh"):
            self.assertTrue((self.ROOT / "scripts" / name).exists(), name)

    def test_macos_script_uses_entry_and_assets(self) -> None:
        text = (self.ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn("src/evcharge/cli.py", text)
        self.assertIn("assets:assets", text)
        # DMG 用双方案（diskutil 优先，hdiutil 退回），失败不中断构建
        self.assertIn("diskutil image create from", text)
        self.assertIn("hdiutil create", text)

    def test_macos_script_pins_flet_version(self) -> None:
        """与开发环境用同一 Flet 版本，才能复用已缓存的桌面客户端。"""
        text = (self.ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn("detect_flet_version", text)
        self.assertIn("flet-desktop-full-", text)

    def test_macos_script_avoids_bash32_empty_array_pitfall(self) -> None:
        """bash 3.2 在 set -u 下展开空数组会报 unbound variable。"""
        text = (self.ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn("${#ICON_ARGS[@]}", text)

    def test_macos_script_keeps_pyinstaller_cache_in_project(self) -> None:
        text = (self.ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn("PYINSTALLER_CONFIG_DIR", text)

    def test_macos_script_writes_bundle_version(self) -> None:
        text = (self.ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn("CFBundleShortVersionString", text)

    def test_windows_script_uses_entry_and_assets(self) -> None:
        text = (self.ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("src\\evcharge\\cli.py", text)
        self.assertIn("assets;assets", text)

    def test_optional_build_dependency_declared(self) -> None:
        text = (self.ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("pyinstaller", text)

    def test_icons_available_for_both_platforms(self) -> None:
        """Windows 用 .ico，macOS 用 .icns，都由 icon.png 生成。"""
        assets = self.ROOT / "assets"
        self.assertTrue((assets / "icon.png").exists())
        self.assertTrue((assets / "icon.ico").exists())
        self.assertTrue((assets / "icon.icns").exists())


class GalleryPickerTests(unittest.TestCase):
    """相册识别：打包后原生选择器可能只给 bytes 不给 path，必须能兜住。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "gallery.db")
        self.manager = SessionManager(collector=Simulator(), on_order=self.db.save_order)
        self.page = FakePage()
        self.vision = FakeVision(plate="闽D·G6592")
        self.settings = AppSettings(
            base_url="https://api.example/v1",
            api_key="sk-test",
            vision_model="vision-model",
        )
        self.monitor = MonitorPage(
            self.manager,
            settings_provider=lambda: self.settings,
            vision=self.vision,
        )
        self.monitor.page = self.page
        self.monitor.selected_pile_id = 3
        self.monitor._build_detail()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    # ---------- 选中结果解析 ----------
    def test_materialize_uses_path_when_present(self) -> None:
        image = Path(self.tmp.name) / "a.jpg"
        image.write_bytes(b"x")
        picked = _PickedFile(path=str(image), name="a.jpg")
        self.assertEqual(
            MonitorPage._materialize_selection(picked), image
        )

    def test_materialize_falls_back_to_bytes(self) -> None:
        """macOS 打包后选择器可能不返回路径，只返回内容。"""
        picked = _PickedFile(path=None, data=b"\xff\xd8\xff jpeg", name="plate.jpg")
        path = MonitorPage._materialize_selection(picked)
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())
        self.assertEqual(Path(path).read_bytes(), b"\xff\xd8\xff jpeg")

    def test_materialize_returns_none_without_data(self) -> None:
        picked = _PickedFile(path=None, data=None, name="")
        self.assertIsNone(MonitorPage._materialize_selection(picked))

    # ---------- 完整链路 ----------
    def test_gallery_with_bytes_only_still_recognizes(self) -> None:
        picked = _PickedFile(path=None, data=b"fake-image", name="p.jpg")
        fake_picker = mock.AsyncMock()
        fake_picker.pick_files.return_value = [picked]
        self.monitor.file_picker = fake_picker

        asyncio.run(self.monitor._gallery_task())

        self.assertEqual(len(self.vision.calls), 1)
        session = self.manager.pile(3).current_session
        self.assertIsNotNone(session)
        self.assertEqual(session.plate, "闽D·G6592")

    def test_gallery_requests_file_bytes(self) -> None:
        """必须显式 with_data=True，否则拿不到可用内容。"""
        fake_picker = mock.AsyncMock()
        fake_picker.pick_files.return_value = []
        self.monitor.file_picker = fake_picker

        asyncio.run(self.monitor._gallery_task())

        self.assertTrue(fake_picker.pick_files.call_args.kwargs["with_data"])
        self.assertIn("取消", self.monitor.plate_hint.value)

    def test_gallery_picker_exception_is_visible(self) -> None:
        fake_picker = mock.AsyncMock()
        fake_picker.pick_files.side_effect = RuntimeError("dialog failed")
        self.monitor.file_picker = fake_picker

        asyncio.run(self.monitor._gallery_task())

        self.assertIn("打开相册失败", self.monitor.plate_hint.value)
        self.assertEqual(self.manager.pile(3).status, PileStatus.IDLE)

    def test_mount_failure_shows_error_instead_of_silence(self) -> None:
        """挂载位置都不可用时，必须给用户看得见的提示。"""

        class NoServicePage(FakePage):
            def __init__(self) -> None:
                super().__init__()
                del self.services
                del self.overlay

        monitor = MonitorPage(
            self.manager,
            settings_provider=lambda: self.settings,
            vision=self.vision,
        )
        monitor.page = NoServicePage()
        monitor.selected_pile_id = 3
        monitor._build_detail()

        asyncio.run(monitor._gallery_task())
        self.assertIn("无法打开相册", monitor.plate_hint.value)

    def test_mount_prefers_services_then_overlay(self) -> None:
        self.monitor._mount_file_picker(self.page)
        self.assertEqual(len(self.page.services), 1)
        self.assertEqual(self.page.overlay, [])

    def test_mount_falls_back_to_overlay(self) -> None:
        class OverlayOnlyPage(FakePage):
            def __init__(self) -> None:
                super().__init__()
                del self.services

        page = OverlayOnlyPage()
        monitor = MonitorPage(self.manager, settings_provider=lambda: self.settings)
        monitor._mount_file_picker(page)
        self.assertEqual(len(page.overlay), 1)


class SplashAndBootTests(unittest.TestCase):
    """启动页与启动失败兜底页。"""

    def test_splash_view_builds(self) -> None:
        from evcharge.presentation.pages.splash import build_splash_view

        view = build_splash_view()
        self.assertIsInstance(view, ft.Container)
        self.assertTrue(view.expand)

    def test_splash_shows_message(self) -> None:
        from evcharge.presentation.pages.splash import build_splash_view

        view = build_splash_view(message="正在启动，请稍候…", title="我的站点")
        texts = [
            c.value
            for c in view.content.controls
            if isinstance(c, ft.Text)
        ]
        self.assertIn("正在启动，请稍候…", texts)
        self.assertIn("我的站点", texts)

    def test_boot_error_view_shows_reason_and_log(self) -> None:
        from evcharge.presentation.pages.splash import build_boot_error_view

        view = build_boot_error_view("ImportError: boom", "/tmp/e.log")
        body = str([c.value for c in view.content.controls if isinstance(c, ft.Text)])
        self.assertIn("启动失败", body)
        self.assertIn("/tmp/e.log", body)

    def test_early_setup_sets_mpl_cache_and_log(self) -> None:
        """启动最早期就要把字体缓存目录固定到数据目录，否则每次启动都重建。"""
        import importlib

        import evcharge.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"FLET_APP_STORAGE_DATA": tmp}):
                os.environ.pop("MPLCONFIGDIR", None)
                from evcharge.infrastructure import paths

                importlib.reload(paths)
                importlib.reload(cli)
                try:
                    data_dir, _assets, log_path = cli._early_setup()
                    self.assertEqual(Path(data_dir), Path(tmp))
                    self.assertEqual(
                        Path(os.environ["MPLCONFIGDIR"]), Path(tmp) / "mpl-cache"
                    )
                    self.assertEqual(Path(log_path), Path(tmp) / "evcharge.log")
                finally:
                    os.environ.pop("FLET_APP_STORAGE_DATA", None)
                    os.environ.pop("MPLCONFIGDIR", None)
                    importlib.reload(paths)
                    importlib.reload(cli)

    def test_logging_writes_file(self) -> None:
        import importlib

        import evcharge.cli as cli

        root = logging.getLogger()
        saved = list(root.handlers)
        for handler in saved:
            root.removeHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cli._setup_logging(Path(tmp), "t.log")
                logging.getLogger("charging.test").info("hello-splash")
                for handler in root.handlers:
                    handler.flush()
                content = (Path(tmp) / "t.log").read_text(encoding="utf-8")
                self.assertIn("hello-splash", content)
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
            for handler in saved:
                root.addHandler(handler)


class MacosClientEntitlementTests(unittest.TestCase):
    """macOS 打包版：Flet 客户端被重新签名后丢了文件选择权限，需要自动补回。"""

    def setUp(self) -> None:
        import evcharge.infrastructure.macos_client as mc

        self.mc = mc

    def _proc(self, returncode=0, stdout="", stderr=""):
        result = mock.Mock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    def test_client_bundles_only_apps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "充电监控.app").mkdir()
            (root / "notes.txt").write_text("x", encoding="utf-8")
            (root / ".last-used").write_text("", encoding="utf-8")
            names = [p.name for p in self.mc.client_app_bundles(root)]
            self.assertEqual(names, ["充电监控.app"])

    def test_has_entitlement_true(self) -> None:
        out = "<key>com.apple.security.files.user-selected.read-write</key><true/>"
        with mock.patch.object(
            self.mc.subprocess, "run", return_value=self._proc(stdout=out)
        ), mock.patch.object(self.mc.shutil, "which", return_value="/usr/bin/codesign"):
            self.assertTrue(self.mc.has_user_selected_entitlement(Path("/x.app")))

    def test_has_entitlement_false_when_empty(self) -> None:
        """flet pack 重签后就是这样：签名在，但没有任何权限声明。"""
        with mock.patch.object(
            self.mc.subprocess, "run", return_value=self._proc(stdout="")
        ), mock.patch.object(self.mc.shutil, "which", return_value="/usr/bin/codesign"):
            self.assertFalse(self.mc.has_user_selected_entitlement(Path("/x.app")))

    def test_has_entitlement_none_without_codesign(self) -> None:
        with mock.patch.object(self.mc.shutil, "which", return_value=None):
            self.assertIsNone(self.mc.has_user_selected_entitlement(Path("/x.app")))

    def test_resign_success_and_failure(self) -> None:
        with mock.patch.object(
            self.mc.subprocess, "run", return_value=self._proc(returncode=0)
        ) as run:
            self.assertTrue(
                self.mc.resign_with_entitlements(Path("/x.app"), Path("/p.plist"))
            )
        args = run.call_args.args[0]
        self.assertIn("--entitlements", args)
        self.assertIn("--deep", args)

        with mock.patch.object(
            self.mc.subprocess,
            "run",
            return_value=self._proc(returncode=1, stderr="Operation not permitted"),
        ):
            self.assertFalse(
                self.mc.resign_with_entitlements(Path("/x.app"), Path("/p.plist"))
            )

    def test_skip_on_non_macos(self) -> None:
        with mock.patch.object(self.mc.sys, "platform", "linux"):
            self.assertIsNone(self.mc.ensure_client_entitlements())

    def test_skip_when_plist_missing(self) -> None:
        with mock.patch.object(self.mc.sys, "platform", "darwin"), \
             mock.patch.object(self.mc, "_entitlements_path", return_value=None):
            self.assertIsNone(self.mc.ensure_client_entitlements())

    def test_fixes_client_missing_entitlement(self) -> None:
        """缺权限 → 重新签名；已有权限 → 什么都不做（幂等）。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "充电监控.app"
            app.mkdir()
            plist = Path(tmp) / "e.plist"
            plist.write_text("<plist/>", encoding="utf-8")

            # 第一次：签名里没有权限
            with mock.patch.object(self.mc.sys, "platform", "darwin"), \
                 mock.patch.object(self.mc, "_entitlements_path", return_value=plist), \
                 mock.patch(
                     "flet_desktop.ensure_client_cached", return_value=tmp
                 ), \
                 mock.patch.object(
                     self.mc, "has_user_selected_entitlement", side_effect=[False, True]
                 ), \
                 mock.patch.object(
                     self.mc, "resign_with_entitlements", return_value=True
                 ) as resign:
                status = self.mc.ensure_client_entitlements()

            self.assertIn("已修复", status)
            resign.assert_called_once()

            # 第二次：已经有权限，不再签名
            with mock.patch.object(self.mc.sys, "platform", "darwin"), \
                 mock.patch.object(self.mc, "_entitlements_path", return_value=plist), \
                 mock.patch(
                     "flet_desktop.ensure_client_cached", return_value=tmp
                 ), \
                 mock.patch.object(
                     self.mc, "has_user_selected_entitlement", return_value=True
                 ), \
                 mock.patch.object(
                     self.mc, "resign_with_entitlements"
                 ) as resign2:
                self.assertIsNone(self.mc.ensure_client_entitlements())
            resign2.assert_not_called()

    def test_no_cache_dir_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(self.mc.sys, "platform", "darwin"), \
             mock.patch.object(
                 self.mc, "_entitlements_path", return_value=Path(tmp) / "e.plist"
             ), \
             mock.patch("flet_desktop.ensure_client_cached", return_value=tmp):
            self.assertIsNone(self.mc.ensure_client_entitlements())


class SettingsInDatabaseTests(unittest.TestCase):
    """设置（含 API Key）与订单同库：不再是明文 JSON 文件。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "app.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_settings_live_in_database(self) -> None:
        save_settings(AppSettings(api_key="sk-db", model="m"), self.db)
        self.assertIsNotNone(self.db.load_settings_blob())
        self.assertEqual(load_settings(self.db).api_key, "sk-db")

    def test_api_key_not_stored_in_plaintext(self) -> None:
        save_settings(AppSettings(api_key="sk-plain-leak"), self.db)
        blob = self.db._conn.execute("SELECT data FROM app_settings").fetchone()[0]
        self.assertNotIn("sk-plain-leak", blob)
        self.assertIn("b64:", blob)

    def test_no_settings_file_is_created(self) -> None:
        target = Path(self.tmp.name) / "settings.json"
        save_settings(AppSettings(api_key="k"), self.db)
        self.assertFalse(target.exists())

    def test_legacy_file_migrates_into_default_db_only(self) -> None:
        """旧 settings.json 只在「默认库」上迁移，临时库不受影响。"""
        from evcharge.infrastructure import settings as settings_mod

        legacy = Path(self.tmp.name) / "settings.json"
        legacy.write_text(
            json.dumps({"api_key": "sk-legacy-plain", "model": "legacy-model"}),
            encoding="utf-8",
        )
        # 临时库：不应迁移
        self.assertEqual(load_settings(self.db).model, AppSettings().model)

        # 默认库：迁移并把文件里的明文换掉
        with mock.patch.object(settings_mod, "SETTINGS_PATH", legacy), \
             mock.patch.object(
                 settings_mod, "DEFAULT_DB_PATH", Path(self.tmp.name) / "app.db"
             ), \
             mock.patch.object(settings_mod, "_shared_db", self.db), \
             mock.patch.object(settings_mod, "_is_default_database", return_value=True):
            loaded = load_settings(self.db)
            self.assertEqual(loaded.api_key, "sk-legacy-plain")
            self.assertEqual(loaded.model, "legacy-model")
        raw = legacy.read_text(encoding="utf-8")
        self.assertNotIn("sk-legacy-plain", raw)

    def test_settings_service_uses_same_db(self) -> None:
        ctx = build_context(db=self.db)
        ctx.settings.save(AppSettings(api_key="sk-ctx", model="m"))
        self.assertEqual(ctx.settings.current().api_key, "sk-ctx")
        self.assertIsNotNone(self.db.load_settings_blob())


class MaintenanceServiceTests(unittest.TestCase):
    """备份 / 恢复 / 清空 / CSV 导入。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "charging.db")
        self.ctx = build_context(db=self.db)
        self.maint = self.ctx.maintenance
        # 造一点数据
        self.db.add_spot("A001", "fast", 120.0)
        self.db.save_order(make_order())
        save_settings(AppSettings(api_key="sk-backup-test", model="m"), self.db)

    def tearDown(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    # ---------- 概况 ----------
    def test_summary(self) -> None:
        info = self.maint.summary()
        self.assertEqual(info["orders"], 1)
        self.assertEqual(info["spots"], 1)
        self.assertGreater(info["size_bytes"], 0)
        self.assertTrue(info["path"].endswith("charging.db"))

    # ---------- 备份 ----------
    def test_backup_creates_zip_with_manifest(self) -> None:
        import zipfile

        target = self.root / "out" / "backup.zip"
        out = self.maint.backup_to(target)
        self.assertTrue(out.exists())
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("charging.db", names)
            self.assertIn("backup.json", names)
            manifest = json.loads(zf.read("backup.json"))
        self.assertEqual(manifest["orders"], 1)

    # ---------- 恢复 ----------
    def test_restore_replaces_data_and_backs_up_old(self) -> None:
        backup = self.maint.backup_to(self.root / "b.zip")

        # 改坏当前数据：加一条订单
        self.db.save_order(make_order(start=datetime(2024, 3, 3, 9, 0)))
        self.assertEqual(self.db.order_count(), 2)

        # 恢复备份 → 回到 1 条
        old_backup = self.maint.restore_from(backup)
        self.assertTrue(Path(old_backup).exists())
        reopened = Database(self.root / "charging.db")
        try:
            self.assertEqual(reopened.order_count(), 1)
        finally:
            reopened.close()

    def test_restore_accepts_plain_db(self) -> None:
        src = self.root / "plain.db"
        import shutil

        shutil.copy2(self.root / "charging.db", src)
        self.maint.restore_from(src)  # 不抛异常即可

    def test_restore_rejects_non_app_database(self) -> None:
        bad = self.root / "bad.db"
        bad.write_bytes(b"not a database")
        with self.assertRaises(ValueError):
            self.maint.restore_from(bad)

    def test_restore_rejects_zip_without_db(self) -> None:
        import zipfile

        bad = self.root / "empty.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "hello")
        with self.assertRaises(ValueError):
            self.maint.restore_from(bad)

    # ---------- 清空 ----------
    def test_clear_all_wipes_data_and_api_key(self) -> None:
        self.maint.clear_all()
        self.assertEqual(self.db.order_count(), 0)
        self.assertEqual(self.db.load_spots(), [])
        self.assertIsNone(self.db.load_settings_blob())
        # 默认管理员仍在，否则清空后无法登录
        admin = self.db.get_user(ADMIN_USERNAME)
        self.assertIsNotNone(admin)
        self.assertEqual(admin["role"], ROLE_ADMIN)
        # 设置回到默认值（没有 Key）
        self.assertEqual(load_settings(self.db).api_key, "")

    # ---------- CSV 导入（追加） ----------
    def test_import_csv_appends_and_skips_duplicates(self) -> None:
        rows = self.db.load_orders()
        csv_path = self.root / "orders.csv"
        export_orders(rows, csv_path)

        imported, skipped = self.maint.import_csv(csv_path)
        # 导出的就是库里那一条：按 (车位, 车牌, 开始时间) 判定为重复 → 全部跳过
        self.assertEqual((imported, skipped), (0, 1))

        # 换一个不重复的 CSV 行 → 追加成功
        import csv as csv_mod

        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv_mod.writer(fh)
            writer.writerow(["pile_id", "plate", "start_time", "end_time",
                             "energy_kwh", "energy_fee", "service_fee", "total_fee"])
            writer.writerow(["A001", "闽D·TEST1", "2024-05-01 10:00:00",
                             "2024-05-01 10:30:00", "20", "10", "5", "15"])
        imported2, skipped2 = self.maint.import_csv(csv_path)
        self.assertEqual((imported2, skipped2), (1, 0))
        self.assertEqual(self.db.order_count(), 2)

        # 再导一次同一份 → 全跳过，不会重复
        imported3, skipped3 = self.maint.import_csv(csv_path)
        self.assertEqual((imported3, skipped3), (0, 1))


class SettingsPageTests(unittest.TestCase):
    """设置页：AI 配置保存、默认人设。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        # 设置已改为存进 SQLite（与订单同库）；这里仍把旧路径指到临时目录，
        # 避免测试意外读到/迁移真实数据
        self.path = Path(self.tmp.name) / "settings.json"
        self.patcher = mock.patch(
            "evcharge.infrastructure.settings.SETTINGS_PATH", self.path
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.page = FakePage()
        self.db = Database(Path(self.tmp.name) / "settings.db")
        self.ctx = build_context(db=self.db)
        self.store = self.db  # 设置与订单同库：断言直接读这个库
        self.settings_page = SettingsPage(
            accounts=self.ctx.accounts,
            settings_service=self.ctx.settings,
            vision=self.ctx.vision,
        )
        self.settings_page.build(self.page)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_save_config(self) -> None:
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = "sk-test"
        sp.model.value = "test-model"
        sp.persona_name.value = "小充"
        sp.persona_prompt.value = "先结论后建议"
        sp._on_save()
        # 设置写进数据库（不再生成 settings.json），库里能查到即成功
        self.assertIsNotNone(self.db.load_settings_blob())
        loaded = load_settings(self.store)
        self.assertEqual(loaded.base_url, "https://api.example/v1")
        self.assertEqual(loaded.api_key, "sk-test")
        self.assertEqual(loaded.model, "test-model")
        self.assertIn("已保存", sp.status.value)

    # ---------- 结果反馈走弹窗（而不是页面底部文本） ----------
    def test_save_shows_dialog(self) -> None:
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = "sk-test"
        sp.model.value = "m"
        sp._on_save()

        self.assertIsNotNone(self.page.dialog, "保存后应弹出结果弹窗")
        self.assertIsInstance(self.page.dialog, ft.AlertDialog)
        body = self.page.dialog.content.content.value
        self.assertIn("已保存", body)
        # 底部状态文本仍同步，兼容原有断言
        self.assertIn("已保存", sp.status.value)

    def test_save_validation_error_shows_dialog(self) -> None:
        sp = self.settings_page
        sp.app_title.value = ""
        sp._on_save()

        self.assertIsNotNone(self.page.dialog)
        self.assertIn("项目名称不能为空", self.page.dialog.content.content.value)

    def test_show_message_then_close(self) -> None:
        sp = self.settings_page
        sp._show_message("测试消息", theme.GREEN, "标题")
        self.assertIsNotNone(self.page.dialog)
        self.assertEqual(self.page.dialog.title.controls[1].value, "标题")
        sp._close_result_dialog()
        self.assertIsNone(self.page.dialog)

    def test_progress_dialog_is_replaced_by_result(self) -> None:
        sp = self.settings_page
        sp._show_progress("测试车牌识别", "正在识别…")
        self.assertIsNotNone(self.page.dialog)
        self.assertIn("正在识别", sp._progress_text.value)
        self.assertEqual(self.page.dialog.actions, [])

        sp._finish_progress("识别成功，车牌号：闽D·G6592", theme.GREEN, "测试通过")
        self.assertIsNotNone(self.page.dialog)
        self.assertIn("闽D·G6592", self.page.dialog.content.content.value)
        self.assertEqual(self.page.dialog.title.controls[1].value, "测试通过")

    def test_plate_test_without_config_shows_dialog(self) -> None:
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = ""
        asyncio.run(sp._plate_test_async())

        self.assertIsNotNone(self.page.dialog)
        self.assertIn("API Key", self.page.dialog.content.content.value)

    def test_reset_persona_shows_dialog(self) -> None:
        sp = self.settings_page
        sp.persona_name.value = "别的名字"
        sp._on_reset_persona()
        self.assertIsNotNone(self.page.dialog)
        self.assertIn("已恢复默认人设", self.page.dialog.content.content.value)

    def test_save_vision_model(self) -> None:
        """视觉模型和 AI 配置一起保存（共用同一套 URL / Key）。"""
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = "sk-test"
        sp.model.value = "chat-model"
        sp.vision_model.value = "vision-model"
        sp._on_save()

        loaded = load_settings(self.store)
        self.assertEqual(loaded.model, "chat-model")
        self.assertEqual(loaded.vision_model, "vision-model")
        self.assertEqual(loaded.effective_vision_model, "vision-model")
        self.assertTrue(loaded.vision_configured)
        self.assertIn("已保存", sp.status.value)

    def test_vision_model_is_optional(self) -> None:
        """留空时复用对话模型，不需要单独配置。"""
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = "sk-test"
        sp.model.value = "gpt-4o-mini"
        sp.vision_model.value = ""
        sp._on_save()

        loaded = load_settings(self.store)
        self.assertEqual(loaded.vision_model, "")
        self.assertEqual(loaded.effective_vision_model, "gpt-4o-mini")

    def test_ai_card_contains_plate_recognition(self) -> None:
        """车牌识别并入 AI 卡片，不再有独立的服务商/Key 输入。"""
        sp = self.settings_page
        self.assertIsNotNone(sp.vision_model)
        self.assertFalse(hasattr(sp, "ocr_provider"))
        self.assertFalse(hasattr(sp, "ocr_api_key"))
        card = sp._ai_card()
        self.assertIsInstance(card, ft.Container)

    def test_plate_test_requires_ai_config(self) -> None:
        sp = self.settings_page
        sp.base_url.value = "https://api.example/v1"
        sp.api_key.value = ""
        asyncio.run(sp._plate_test_async())
        self.assertIn("API Key", sp.status.value)

    def test_save_site_settings(self) -> None:
        sp = self.settings_page
        sp.app_title.value = "我的高速充电站"
        sp.tariff_profile.value = "fujian"
        sp.service_fee.value = "0.50"
        sp.refresh_interval.value = "3"
        sp.use_local_summary.value = True
        sp._on_save()
        loaded = load_settings(self.store)
        self.assertEqual(loaded.app_title, "我的高速充电站")
        self.assertEqual(loaded.tariff_profile, "fujian")
        self.assertAlmostEqual(loaded.service_fee, 0.50, places=2)
        self.assertAlmostEqual(loaded.refresh_interval_s, 3.0, places=1)
        self.assertTrue(loaded.use_local_summary)

    def test_requires_base_url(self) -> None:
        sp = self.settings_page
        sp.base_url.value = ""
        sp._on_save()
        self.assertIn("不能为空", sp.status.value)
        self.assertFalse(self.path.exists())

    def test_reset_persona(self) -> None:
        sp = self.settings_page
        sp.persona_name.value = "别的名字"
        sp._on_reset_persona()
        self.assertEqual(sp.persona_name.value, "小充")
        self.assertIn("默认人设", sp.status.value)


class OrdersPageTests(unittest.TestCase):
    """订单页筛选、分页、导出与详情。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "orders.db")
        self.manager = SessionManager()
        orders = [
            make_order(
                1, DEFAULT_STATION[2],
                make_vehicle(75, 300, 800, category="轿车", model="小鹏G6"),
                energy=35, start=datetime(2024, 1, 1, 10, 0),
            ),
            make_order(
                2, DEFAULT_STATION[0],
                make_vehicle(90, 400, 800, category="SUV", model="比亚迪"),
                energy=20, start=datetime(2024, 1, 2, 18, 0), duration_min=300,
            ),
            make_order(
                1, DEFAULT_STATION[2],
                make_vehicle(70, 280, 800, category="轿车", model="小鹏G6"),
                energy=40, start=datetime(2024, 1, 3, 11, 0),
            ),
        ]
        self.db.save_orders(orders)
        self.page = FakePage()
        self.orders_page = OrdersPage(
            self.manager,
            build_context(db=self.db).orders,
            export_path=Path(self.tmp.name) / "export.csv",
        )
        self.orders_page.build(self.page)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_search_and_combination_filter(self) -> None:
        page = self.orders_page
        page.search.value = "小鹏"
        page._on_filters_changed()
        self.assertEqual(len(page.filtered), 2)
        self.assertEqual(len(page.rows_col.controls), 2)
        self.assertIn("小鹏", page.filter_note.value)

        page.search.value = ""
        page.filter_type.value = "交流慢充"
        page._on_filters_changed()
        self.assertEqual(len(page.filtered), 1)
        self.assertEqual(page.filtered[0]["pile_id"], "2")

    def test_date_filters(self) -> None:
        page = self.orders_page
        page.filter_range.value = "今天"
        page._on_filters_changed()
        self.assertEqual(len(page.filtered), 0)
        page.filter_range.value = "全部时间"
        page.start_date.value = "2024-01-02"
        page.end_date.value = "2024-01-03"
        page._on_filters_changed()
        self.assertEqual(len(page.filtered), 2)
        self.assertIn("2024-01-02", page.filter_note.value)

    def test_invalid_date_shows_warning(self) -> None:
        page = self.orders_page
        page.start_date.value = "2024/01/01"
        page._on_filters_changed()
        self.assertIn("格式", page.filter_note.value)

    def test_pagination(self) -> None:
        page = self.orders_page
        page.page_size = 2
        page.page_index = 1
        page._fill(update=True)
        self.assertEqual(len(page.rows_col.controls), 2)
        self.assertIn("1 / 2", page.page_label.value)
        page._change_page(1)
        self.assertEqual(len(page.rows_col.controls), 1)
        self.assertTrue(page.next_btn.disabled)
        self.assertFalse(page.prev_btn.disabled)

    def test_export_filtered(self) -> None:
        page = self.orders_page
        page.filter_type.value = "交流慢充"
        page._on_filters_changed()
        page._export()
        out = Path(self.tmp.name) / "export.csv"
        self.assertTrue(out.exists())
        self.assertIn("已导出 1 条", page.status_text.value)
        self.assertIn("比亚迪", out.read_text(encoding="utf-8-sig"))

    def test_detail_dialog(self) -> None:
        page = self.orders_page
        page._show_order(page.filtered[0])
        self.assertIsNotNone(self.page.dialog)
        self.assertIn("订单 #", self.page.dialog.title.value)
        self.page.pop_dialog()
        self.assertIsNone(self.page.dialog)

class AuthTests(unittest.TestCase):
    """密码散列与校验。"""

    def test_hash_and_verify(self) -> None:
        stored = hash_password("s3cret")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("s3cret", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_salt_is_random(self) -> None:
        first = hash_password(DEFAULT_PASSWORD)
        second = hash_password(DEFAULT_PASSWORD)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(DEFAULT_PASSWORD, first))

    def test_bad_format_is_rejected(self) -> None:
        self.assertFalse(verify_password("admin", "not-a-hash"))


class SpotAccountTests(unittest.TestCase):
    """新增/删除车位与车位账号联动、订单归属筛选。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "spots.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_admin_account_seeded(self) -> None:
        admin = self.db.get_user(ADMIN_USERNAME)
        self.assertIsNotNone(admin)
        self.assertEqual(admin["role"], ROLE_ADMIN)
        self.assertTrue(verify_password(DEFAULT_PASSWORD, admin["password_hash"]))

    def test_initial_station_has_no_spots(self) -> None:
        self.assertEqual(self.db.load_spots(), [])
        self.assertEqual(self.db.spot_count(), 0)

    def _spots(self) -> dict[str, dict]:
        return {row["name"]: row for row in self.db.load_spots()}

    def test_add_spot_creates_account(self) -> None:
        uid = self.db.add_spot("A001", "fast", 120)
        self.db.add_spot("B002", "slow")
        spots = self._spots()
        self.assertEqual(set(spots), {"A001", "B002"})
        self.assertEqual(spots["A001"]["charger_type"], "fast")
        self.assertEqual(spots["B002"]["charger_type"], "slow")
        self.assertEqual(spots["A001"]["uid"], uid)
        self.assertTrue(uid.startswith("sp_"))
        user = self.db.get_user("A001")
        self.assertEqual(user["role"], ROLE_SPOT_USER)
        self.assertEqual(user["spot_uid"], uid)
        self.assertTrue(verify_password(DEFAULT_PASSWORD, user["password_hash"]))

    def test_spot_name_is_unique_ignore_case(self) -> None:
        """车位名同时是登录名，不能重复（忽略大小写）。"""
        self.db.add_spot("A001", "fast", 120)
        with self.assertRaises(ValueError):
            self.db.add_spot("a001", "slow")
        self.assertEqual(self.db.spot_count(), 1)

    def test_spot_power_stored_as_nominal(self) -> None:
        """库里必须存铭牌功率；存实测值会让重启时二次打折。"""
        self.db.add_spot("A001", "fast", 240)
        self.db.add_spot("B002", "slow")
        spots = self._spots()
        self.assertAlmostEqual(spots["A001"]["power_kw"], 240.0)
        self.assertAlmostEqual(spots["B002"]["power_kw"], 7.0)

    def test_spot_power_does_not_decay_across_restarts(self) -> None:
        """反复「写入 → 重开库 → 构建桩档案」，实测功率必须恒为 216kW。"""
        self.db.add_spot("A001", "fast", 240)
        uid = self._spots()["A001"]["uid"]
        for _ in range(3):
            self.db.close()
            self.db = Database(self.db.path)
            row = self._spots()["A001"]
            self.assertEqual(row["uid"], uid)  # uid 跨重启不变
            self.assertAlmostEqual(row["power_kw"], 240.0)
            profile = make_charger("A001", row["charger_type"], row["power_kw"])
            self.assertAlmostEqual(profile.nominal_power_kw, 240.0)
            self.assertAlmostEqual(profile.actual_power_kw, 216.0)

    def test_legacy_actual_power_is_migrated_to_nominal(self) -> None:
        """旧库（存的是 216/108/54 这类实测值）打开后自动还原铭牌功率。"""
        path = Path(self.tmp.name) / "legacy_power.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE spots (spot_no TEXT PRIMARY KEY, charger_type TEXT NOT NULL,"
            " power_kw REAL NOT NULL);"
        )
        conn.executemany(
            "INSERT INTO spots (spot_no, charger_type, power_kw) VALUES (?, ?, ?)",
            [
                ("X01", "fast", 216.0),
                ("X02", "fast", 108.0),
                ("X03", "slow", 7.0),
                ("X04", "fast", 194.4),  # 已衰减过一轮的脏数据，也吸附回 240
            ],
        )
        conn.commit()
        conn.close()

        for _ in range(2):  # 第二次打开不应再折算
            legacy = Database(path)
            spots = {row["name"]: row for row in legacy.load_spots()}
            self.assertAlmostEqual(spots["X01"]["power_kw"], 240.0)
            self.assertAlmostEqual(spots["X02"]["power_kw"], 120.0)
            self.assertAlmostEqual(spots["X04"]["power_kw"], 240.0)
            self.assertAlmostEqual(spots["X03"]["power_kw"], 7.0)
            legacy.close()

    def test_legacy_numeric_names_migrate_to_uid_and_readable_names(self) -> None:
        """v2 库（车位编号 1/2 单主键）升级：分配 uid、数字编号换成 A001 风格、账号同步改名。"""
        path = Path(self.tmp.name) / "legacy_v2.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE spots (spot_no TEXT PRIMARY KEY, charger_type TEXT NOT NULL,"
            " power_kw REAL NOT NULL);"
            "CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,"
            " role TEXT NOT NULL, spot_no TEXT);"
            "CREATE TABLE orders (order_id INTEGER PRIMARY KEY, pile_id TEXT NOT NULL,"
            " plate TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,"
            " energy_kwh REAL NOT NULL, energy_fee REAL NOT NULL,"
            " service_fee REAL NOT NULL, total_fee REAL NOT NULL, tariff_periods TEXT,"
            " status TEXT);"
        )
        conn.executemany(
            "INSERT INTO spots (spot_no, charger_type, power_kw) VALUES (?, ?, ?)",
            [("1", "slow", 7.0), ("2", "fast", 240.0)],
        )
        conn.executemany(
            "INSERT INTO users (username, password_hash, role, spot_no) VALUES (?, ?, ?, ?)",
            [("1", "hash-a", "spot_user", "1"), ("2", "hash-b", "spot_user", "2")],
        )
        conn.executemany(
            "INSERT INTO orders (order_id, pile_id, plate, start_time, end_time,"
            " energy_kwh, energy_fee, service_fee, total_fee, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "1", "闽A·1", "2024-01-01 10:00:00", "2024-01-01 11:00:00",
                 20.0, 10.0, 5.0, 15.0, "已完成"),
                (2, "2", "闽A·2", "2024-01-02 10:00:00", "2024-01-02 11:00:00",
                 30.0, 15.0, 5.0, 20.0, "已完成"),
            ],
        )
        conn.commit()
        conn.close()

        db = Database(path)
        spots = {row["name"]: row for row in db.load_spots()}
        self.assertEqual(set(spots), {"A001", "A002"})
        self.assertEqual(len({row["uid"] for row in spots.values()}), 2)
        # 账号名跟着改名，密码散列原样保留
        self.assertIsNotNone(db.get_user("A001"))
        self.assertIsNotNone(db.get_user("A002"))
        self.assertIsNone(db.get_user("1"))
        self.assertEqual(db.get_user("A001")["password_hash"], "hash-a")
        self.assertEqual(db.get_user("A001")["spot_uid"], spots["A001"]["uid"])
        # 订单 pile_id 改成 uid，展示名换成新车位名
        orders = db.load_orders()
        self.assertEqual(
            {row["pile_id"] for row in orders},
            {spots["A001"]["uid"], spots["A002"]["uid"]},
        )
        self.assertEqual(sorted({row["spot_name"] for row in orders}), ["A001", "A002"])
        db.close()

    def test_reloaded_spot_keeps_rated_power(self) -> None:
        """按 DB 配置重建会话管理器后，240kW 桩仍是 216kW 实测。"""
        self.db.add_spot("A001", "fast", 240)
        manager = SessionManager(spots=[])
        for row in self.db.load_spots():
            manager.add_spot(row["name"], row["charger_type"], row["power_kw"], uid=row["uid"])
        pile = manager.pile("A001")
        self.assertAlmostEqual(pile.charger.nominal_power_kw, 240.0)
        self.assertAlmostEqual(pile.charger.actual_power_kw, 216.0)

    def test_remove_spot_keeps_order_history(self) -> None:
        """删车位只删车位与账号，历史订单作为营收记录保留。"""
        uid_a = self.db.add_spot("A001", "fast", 120)
        uid_b = self.db.add_spot("B002", "slow")
        self.db.save_order(make_order(uid_a, spot_name="A001"))
        self.db.save_order(make_order(uid_b, spot_name="B002"))
        self.db.remove_spot("A001")
        self.assertEqual([r["name"] for r in self.db.load_spots()], ["B002"])
        self.assertIsNone(self.db.get_user("A001"))
        self.assertEqual(self.db.order_count(), 2)
        self.assertEqual(len(self.db.load_orders(spot_uid=uid_a)), 1)
        self.assertEqual(len(self.db.load_orders(spot_uid=uid_b)), 1)
        # 需要彻底清空该车位订单时走显式接口
        self.assertEqual(self.db.delete_orders_by_spot(uid_a), 1)
        self.assertEqual(self.db.order_count(), 1)

    def test_recreated_same_name_does_not_inherit_history(self) -> None:
        """删掉 A001 再建一个同名 A001：新车位看不到老车位的订单。"""
        old_uid = self.db.add_spot("A001", "fast", 120)
        self.db.save_order(make_order(old_uid, spot_name="A001"))
        self.db.remove_spot("A001")
        new_uid = self.db.add_spot("A001", "slow")
        self.assertNotEqual(old_uid, new_uid)
        self.assertEqual(len(self.db.load_orders(spot_uid=old_uid)), 1)
        self.assertEqual(len(self.db.load_orders(spot_uid=new_uid)), 0)
        # 同名是"两个不同车位"，历史订单仍挂在旧 uid 上
        self.assertEqual(self.db.get_user("A001")["spot_uid"], new_uid)

    def test_set_password_changes_login(self) -> None:
        self.db.add_spot("A001", "fast", 120)
        self.db.set_password("A001", "new-pass")
        self.assertFalse(
            verify_password(DEFAULT_PASSWORD, self.db.get_user("A001")["password_hash"])
        )
        self.assertTrue(
            verify_password("new-pass", self.db.get_user("A001")["password_hash"])
        )


class DynamicSpotSessionTests(unittest.TestCase):
    """SessionManager 动态车位：单枪、可增可删。"""

    def setUp(self) -> None:
        self.manager = SessionManager(spots=[])

    def test_spots_start_empty(self) -> None:
        self.assertEqual(self.manager.piles, [])

    def test_add_and_remove_spot(self) -> None:
        a1 = self.manager.add_spot("A001", "fast", 120)
        b2 = self.manager.add_spot("B002", "slow")
        self.assertEqual([p.name for p in self.manager.piles], ["A001", "B002"])
        self.assertNotEqual(a1.uid, b2.uid)
        self.assertTrue(a1.uid.startswith("sp_"))
        self.assertTrue(self.manager.pile("A001").charger.is_dc)
        self.assertFalse(self.manager.pile("B002").charger.is_dc)
        # uid 与名字都能定位同一个车位
        self.assertIs(self.manager.pile(a1.uid), self.manager.pile("a001".upper()))
        self.assertIsNone(self.manager.remove_spot("B002"))
        self.assertEqual([p.name for p in self.manager.piles], ["A001"])

    def test_duplicate_spot_rejected(self) -> None:
        self.manager.add_spot("A001", "fast", 120)
        with self.assertRaises(ValueError):
            self.manager.add_spot("A001", "slow")
        with self.assertRaises(ValueError):
            self.manager.add_spot("a001", "slow")  # 忽略大小写

    def test_recreated_spot_gets_new_uid(self) -> None:
        first = self.manager.add_spot("A001", "fast", 120)
        self.manager.remove_spot(first.uid)
        second = self.manager.add_spot("A001", "fast", 120)
        self.assertNotEqual(first.uid, second.uid)

    def test_remove_charging_spot_force_settles(self) -> None:
        pile = self.manager.add_spot("A001", "fast", 120)
        self.manager.start_session("A001")
        for _ in range(5):
            self.manager.tick()
        order = self.manager.remove_spot("A001")
        self.assertIsNotNone(order)
        self.assertEqual(order.pile_id, pile.uid)
        self.assertEqual(order.spot_name, "A001")
        self.assertGreater(order.energy_kwh, 0.0)

    def test_single_gun_piles(self) -> None:
        self.manager.add_spot("A001", "fast", 240)
        pile = self.manager.pile("A001")
        self.assertFalse(hasattr(pile.charger, "gun_count"))
        self.assertGreater(pile.charger.actual_power_kw, 0.0)


class TextSpotIdTests(unittest.TestCase):
    """车位名可以是文本（A1/7/10），uid 也是文本；展示名与订单归属必须分开。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "textspots.db")
        for name, kind, power in (
            ("A1", "fast", 120),
            ("7", "slow", None),
            ("10", "fast", 240),
        ):
            self.db.add_spot(name, kind, power)
        self.uids = {row["name"]: row["uid"] for row in self.db.load_spots()}
        self.manager = SessionManager(on_order=self.db.save_order, spots=[])
        for row in self.db.load_spots():
            self.manager.add_spot(
                row["name"], row["charger_type"], row["power_kw"], uid=row["uid"]
            )
        for name in ("A1", "7", "10"):
            self.manager.start_session(name)
            for _ in range(3):
                self.manager.tick()
            self.manager.stop_session(name)
        self.rows = self.db.load_orders()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_orders_keep_text_uid(self) -> None:
        """订单记的是 uid（仍是字符串），展示名单独存快照。"""
        self.assertEqual({row["pile_id"] for row in self.rows}, set(self.uids.values()))
        self.assertTrue(all(isinstance(row["pile_id"], str) for row in self.rows))
        self.assertEqual(
            sorted({row["spot_name"] for row in self.rows}), ["10", "7", "A1"]
        )

    def test_pile_stats_sorted_naturally(self) -> None:
        piles = pile_stats(to_dataframe(self.rows))
        # 纯数字车位名按数值排（7 < 10），字母名排在后面
        self.assertEqual(piles["spot_name"].tolist(), ["7", "10", "A1"])
        self.assertEqual(piles.loc[0, "pile_id"], self.uids["7"])

    def test_site_summary_handles_text_spot(self) -> None:
        summary = build_site_summary(self.rows)
        self.assertIn("【车位】", summary.to_text())
        self.assertEqual(
            sorted(stat["spot_name"] for stat in summary.pile_stats),
            ["10", "7", "A1"],
        )

    def test_csv_roundtrip_keeps_text_spot(self) -> None:
        path = Path(self.tmp.name) / "text.csv"
        export_orders(self.rows, path)
        loaded, skipped = load_orders_csv(path)
        self.assertEqual(skipped, 0)
        self.assertEqual(
            sorted(row["pile_id"] for row in loaded), sorted(self.uids.values())
        )
        self.assertEqual(sorted(row["spot_name"] for row in loaded), ["10", "7", "A1"])

    def test_analysis_page_builds(self) -> None:
        page = build_analysis_page(
            FakePage(),
            build_context(db=self.db).orders,
            branding_from(load_settings()),
        )
        self.assertIsNotNone(page)


class OrdersFilterOptionsTests(unittest.TestCase):
    """订单页的车位/类型/车型下拉必须动态来自数据，不能写死。

    车位下拉的 key 是 uid（订单归属），label 是车位名（给人看）。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "filters.db")
        self.db.add_spot("3", "fast", 120)
        self.db.add_spot("7", "slow")
        self.uids = {row["name"]: row["uid"] for row in self.db.load_spots()}
        self.manager = SessionManager(on_order=self.db.save_order, spots=[])
        for row in self.db.load_spots():
            self.manager.add_spot(
                row["name"], row["charger_type"], row["power_kw"], uid=row["uid"]
            )
        self.page = OrdersPage(self.manager, build_context(db=self.db).orders)
        self.page.page = FakePage()
        self.page.build(self.page.page)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _keys(self, dropdown) -> list[str]:
        return [option.key for option in dropdown.options]

    def _labels(self, dropdown) -> list[str]:
        return [option.text for option in dropdown.options]

    def test_initial_options_come_from_spots(self) -> None:
        self.assertEqual(self._labels(self.page.filter_pile), ["全部车位", "车位 3", "车位 7"])
        self.assertEqual(
            self._keys(self.page.filter_pile),
            ["全部", self.uids["3"], self.uids["7"]],
        )
        self.assertIn("交流慢充", self._keys(self.page.filter_type))
        self.assertIn("SUV", self._keys(self.page.filter_category))

    def test_new_spot_appears_after_refresh(self) -> None:
        uid = self.db.add_spot("A001", "fast", 240)
        self.manager.add_spot("A001", "fast", 240, uid=uid)
        self.page.refresh()
        self.assertEqual(
            self._labels(self.page.filter_pile),
            ["全部车位", "车位 3", "车位 7", "车位 A001"],
        )

    def test_deleted_spot_disappears_and_selection_resets(self) -> None:
        uid7 = self.uids["7"]
        self.page.filter_pile.value = uid7
        self.manager.remove_spot(uid7)
        self.db.remove_spot(uid7)
        self.page.refresh()
        self.assertEqual(self._labels(self.page.filter_pile), ["全部车位", "车位 3"])
        self.assertEqual(self.page.filter_pile.value, "全部")

    def test_deleted_spot_with_history_stays_filterable(self) -> None:
        """删车位不删订单：有历史订单的车位仍可作为筛选条件（标注已删除）。"""
        uid7 = self.uids["7"]
        self.db.save_order(make_order(uid7, spot_name="7"))
        self.page.refresh()
        self.manager.remove_spot(uid7)
        self.db.remove_spot(uid7)
        self.page.refresh()
        self.assertIn("车位 7（已删除）", self._labels(self.page.filter_pile))
        self.assertIn(uid7, self._keys(self.page.filter_pile))
        self.assertEqual(len(self.db.load_orders(spot_uid=uid7)), 1)

    def test_deleted_spot_does_not_share_filter_with_new_same_name(self) -> None:
        """同名重建后，旧订单与新车位是两个不同的筛选值。"""
        uid7 = self.uids["7"]
        self.db.save_order(make_order(uid7, spot_name="7"))
        self.manager.remove_spot(uid7)
        self.db.remove_spot(uid7)
        new_uid = self.db.add_spot("7", "slow")
        self.manager.add_spot("7", "slow", None, uid=new_uid)
        self.page.refresh()
        keys = self._keys(self.page.filter_pile)
        self.assertIn(uid7, keys)
        self.assertIn(new_uid, keys)
        self.assertNotEqual(uid7, new_uid)

    def test_selection_is_kept_when_still_valid(self) -> None:
        uid7 = self.uids["7"]
        self.page.filter_pile.value = uid7
        self.page.refresh()
        self.assertEqual(self.page.filter_pile.value, uid7)

    def test_spot_user_sees_only_own_spot(self) -> None:
        page = OrdersPage(
            self.manager, build_context(db=self.db).orders, spot_uid=self.uids["3"]
        )
        page.page = FakePage()
        page.build(page.page)
        self.assertEqual(self._labels(page.filter_pile), ["全部车位", "车位 3"])


if __name__ == "__main__":
    unittest.main()
