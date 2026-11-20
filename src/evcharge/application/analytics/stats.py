# -*- coding: utf-8 -*-
"""Pandas 统计分析模块（analytics.stats）。

对订单数据做聚合分析，供"数据分析"页面与 AI 报告使用：
- 总体概览：总电量、总费用、订单数、平均综合单价、平均电池容量、直流快充占比
- 趋势分析：按日电量与费用
- 时段分布：峰/平/谷用电结构
- 车位分析：各车位订单量、电量、利用率
- 车型/电池分布：轿车/SUV 结构、电池容量直方图
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pandas as pd

_EMPTY_COLUMNS = [
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
    "periods",
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

_NUMERIC_COLUMNS = (
    "energy_kwh",
    "energy_fee",
    "service_fee",
    "total_fee",
    "duration_min",
    "battery_kwh",
    "start_soc",
    "end_soc",
    "peak_power_kw",
    "voltage_platform_v",
)


def to_dataframe(orders: List[Dict]) -> pd.DataFrame:
    """把订单字典列表转换为 DataFrame（含时间列解析）。"""
    if not orders:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)
    df = pd.DataFrame(orders)
    for column in _EMPTY_COLUMNS:
        if column not in df.columns:
            df[column] = None
    df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce")
    df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce")
    df = df.dropna(subset=["start_time"])  # 容错：时间无法解析的行剔除
    for col in _NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["date"] = df["start_time"].dt.date
    df["hour"] = df["start_time"].dt.hour
    # 缺失行在 DataFrame 里是 NaN（truthy），统一成空列表，避免分时段统计崩溃
    df["periods"] = df["periods"].apply(lambda v: v if isinstance(v, list) else [])
    return df


def overview(df: pd.DataFrame) -> Dict:
    """总体概览指标。"""
    if df.empty:
        return {
            "order_count": 0,
            "total_energy_kwh": 0.0,
            "total_fee": 0.0,
            "avg_price": 0.0,
            "avg_energy_per_order": 0.0,
            "avg_battery_kwh": 0.0,
            "dc_share": 0.0,
        }
    total_energy = float(df["energy_kwh"].sum())
    total_fee = float(df["total_fee"].sum())
    batteries = pd.to_numeric(df["battery_kwh"], errors="coerce").fillna(0.0)
    batteries = batteries[batteries > 0]
    charger_types = df["charger_type"].fillna("—")
    dc_share = float((charger_types == "直流快充").mean() * 100.0) if len(df) else 0.0
    return {
        "order_count": int(len(df)),
        "total_energy_kwh": round(total_energy, 2),
        "total_fee": round(total_fee, 2),
        # 含服务费的综合单价（不是纯电价）
        "avg_price": round(total_fee / total_energy, 3) if total_energy else 0.0,
        "avg_energy_per_order": round(total_energy / len(df), 2),
        "avg_battery_kwh": round(float(batteries.mean()), 1) if len(batteries) else 0.0,
        "dc_share": round(dc_share, 1),
    }


def daily_trend(df: pd.DataFrame) -> pd.DataFrame:
    """按日聚合电量与费用（用于折线/柱状图）。"""
    if df.empty:
        return pd.DataFrame(columns=["date", "energy_kwh", "total_fee", "order_count"])
    grouped = (
        df.groupby("date")
        .agg(
            energy_kwh=("energy_kwh", "sum"),
            total_fee=("total_fee", "sum"),
            order_count=("start_time", "count"),
        )
        .reset_index()
        .sort_values("date")
    )
    grouped["energy_kwh"] = grouped["energy_kwh"].round(2)
    grouped["total_fee"] = grouped["total_fee"].round(2)
    return grouped


def period_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """峰/平/谷电量结构分布。

    订单可能跨时段，periods 字段含分段明细；此处把各段电量累加到对应时段。
    """
    buckets = {"峰段": 0.0, "平段": 0.0, "谷段": 0.0}
    for periods in df.get("periods", pd.Series(dtype=object)):
        if not periods:
            continue
        for p in periods:
            name = p.get("period", "平段")
            buckets[name] = buckets.get(name, 0.0) + float(p.get("energy_kwh", 0.0))
    total = sum(buckets.values()) or 1.0
    return pd.DataFrame(
        {
            "period": list(buckets.keys()),
            "energy_kwh": [round(v, 2) for v in buckets.values()],
            "share_pct": [round(v / total * 100, 1) for v in buckets.values()],
        }
    )


def _natural_key(spot_name) -> tuple:
    """车位名的自然排序键：纯数字按数值，其余按字符串排在后面。"""
    text = str(spot_name)
    if text.isdigit():
        return (0, int(text), "", text)
    return (1, 0, text, text)


def pile_stats(df: pd.DataFrame) -> pd.DataFrame:
    """各车位订单量/电量/费用统计（用于柱状图与利用率）。

    按车位 ``pile_id``（uid）分组，同时带出展示名 ``spot_name`` 供图表/表格使用；
    没有名字的历史订单回退成 uid 本身。
    """
    columns = ["pile_id", "spot_name", "order_count", "energy_kwh", "total_fee"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    work = df if "spot_name" in df.columns else df.assign(spot_name="")
    work = work.copy()
    work["spot_name"] = work["spot_name"].fillna("").astype(str)
    work.loc[work["spot_name"] == "", "spot_name"] = work["pile_id"].astype(str)
    grouped = (
        work.groupby(["pile_id", "spot_name"], dropna=False)
        .agg(
            order_count=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            total_fee=("total_fee", "sum"),
        )
        .reset_index()
    )
    # 车位名是文本：纯数字按数值排（1,2,10），A001 之类按字典序排在后面
    grouped["_order"] = grouped["spot_name"].map(_natural_key)
    grouped = (
        grouped.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    )
    grouped["energy_kwh"] = grouped["energy_kwh"].round(2)
    grouped["total_fee"] = grouped["total_fee"].round(2)
    return grouped[columns]


def hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    """小时级用电画像（0~23 时平均电量），用于识别用电高峰。"""
    if df.empty:
        return pd.DataFrame(columns=["hour", "avg_energy_kwh"])
    grouped = (
        df.groupby("hour")["energy_kwh"]
        .mean()
        .reindex(range(24), fill_value=0.0)  # 补齐 0~23 时，缺失小时记 0
        .rename("avg_energy_kwh")
        .reset_index()
    )
    grouped["avg_energy_kwh"] = grouped["avg_energy_kwh"].round(3)
    return grouped


def battery_distribution(
    df: pd.DataFrame,
    bins: Optional[Sequence[float]] = None,
) -> pd.DataFrame:
    """电池容量分箱统计（用于柱状图）。"""
    if df.empty:
        return pd.DataFrame(columns=["label", "count"])
    edges = list(bins or (0, 50, 60, 70, 80, 90, 100, 120, 150, 200))
    batteries = pd.to_numeric(df["battery_kwh"], errors="coerce").dropna()
    batteries = batteries[batteries > 0]
    if batteries.empty:
        return pd.DataFrame(columns=["label", "count"])

    labels: List[str] = []
    counts: List[int] = []
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        if index == 0:
            label = f"{high:g} 以下"
            mask = (batteries < high)
        elif index == len(edges) - 2:
            label = f"{low:g}+"
            mask = batteries >= low
        else:
            label = f"{low:g}-{high:g}"
            mask = (batteries >= low) & (batteries < high)
        labels.append(label)
        counts.append(int(mask.sum()))
    return pd.DataFrame({"label": labels, "count": counts})


def vehicle_mix(df: pd.DataFrame) -> pd.DataFrame:
    """轿车/SUV 订单结构。"""
    if df.empty:
        return pd.DataFrame(columns=["category", "count"])
    categories = df["vehicle_category"].fillna("未知").replace("—", "未知")
    counts = categories.value_counts()
    return pd.DataFrame({"category": counts.index, "count": counts.values})


def charger_mix(df: pd.DataFrame) -> pd.DataFrame:
    """交流/直流订单结构。"""
    if df.empty:
        return pd.DataFrame(columns=["charger_type", "count"])
    types = df["charger_type"].fillna("未知").replace("—", "未知")
    counts = types.value_counts()
    return pd.DataFrame({"charger_type": counts.index, "count": counts.values})
