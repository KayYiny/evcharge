# -*- coding: utf-8 -*-
"""CSV 导出/读取（evcharge.infrastructure.csv_export）。

- export_orders：把订单导出为 CSV（账单存档/报表）
- load_orders_csv：读取 CSV 订单（历史数据导入），损坏行跳过计数（容错）
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

CSV_FIELDS = [
    "order_id",
    "pile_id",
    "spot_name",
    "plate",
    "start_time",
    "end_time",
    "duration_min",
    "energy_kwh",
    "energy_fee",
    "service_fee",
    "total_fee",
    "status",
    "vehicle_category",
    "vehicle_segment",
    "vehicle_model",
    "battery_kwh",
    "start_soc",
    "end_soc",
    "peak_power_kw",
    "charger_type",
    "voltage_platform_v",
]


def export_orders(orders: List[Dict], path: str | Path) -> Path:
    """把订单字典列表导出为 CSV，返回文件路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # 带 BOM，Excel 直接打开不乱码
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for order in orders:
            row = dict(order)
            # 车位列给人看，写展示名；uid 继续保留在 pile_id 里
            row["spot_name"] = row.get("spot_name") or row.get("pile_id", "")
            writer.writerow(row)
    return path


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def load_orders_csv(path: str | Path) -> Tuple[List[Dict], int]:
    """从 CSV 读取订单，返回 (订单列表, 跳过的损坏行数)。

    容错策略：必填字段缺失/解析失败的行跳过，新增字段缺失时使用默认值，
    兼容旧版本导出的 CSV。
    """
    path = Path(path)
    orders: List[Dict] = []
    skipped = 0
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                orders.append(
                    {
                        # 单号在导入时会重新分配，手工编辑过的 CSV 可能没有这一列
                        "order_id": _as_int(row.get("order_id")),
                        "pile_id": str(row["pile_id"]),
                        "spot_name": row.get("spot_name") or str(row["pile_id"]),
                        "plate": row["plate"],
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "duration_min": _as_float(row.get("duration_min")),
                        "energy_kwh": float(row["energy_kwh"]),
                        "energy_fee": float(row["energy_fee"]),
                        "service_fee": float(row["service_fee"]),
                        "total_fee": float(row["total_fee"]),
                        "status": row.get("status") or "已完成",
                        "periods": [],
                        "vehicle_category": row.get("vehicle_category") or "—",
                        "vehicle_segment": row.get("vehicle_segment") or "—",
                        "vehicle_model": row.get("vehicle_model") or "—",
                        "battery_kwh": _as_float(row.get("battery_kwh")),
                        "start_soc": _as_float(row.get("start_soc")),
                        "end_soc": _as_float(row.get("end_soc")),
                        "peak_power_kw": _as_float(row.get("peak_power_kw")),
                        "charger_type": row.get("charger_type") or "—",
                        "voltage_platform_v": _as_int(row.get("voltage_platform_v")),
                    }
                )
            except (KeyError, TypeError, ValueError):
                skipped += 1  # 损坏行跳过，保证历史读取不中断
    return orders, skipped
