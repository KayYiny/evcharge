# -*- coding: utf-8 -*-
"""站点本地汇总与原始数据导出（analytics.report）。

AI 页面有两种数据模式：
- 关闭本地汇总：把全部订单逐条导出为 CSV，不生成任何统计结论；
- 开启本地汇总：先生成 SiteSummary，再把小总结发给模型。

本模块只负责本地统计/格式化，不调用大模型；未配置 AI 时，开启本地汇总
仍可在页面查看客观统计结论。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from evcharge.application.analytics.stats import (
    battery_distribution,
    daily_trend,
    overview,
    to_dataframe,
)

PERIOD_ORDER = ("峰段", "平段", "谷段")


def _f(value: float, digits: int = 1) -> float:
    return round(float(value), digits)


@dataclass
class SiteSummary:
    """一份站点级本地汇总。"""

    order_count: int
    total_energy_kwh: float
    energy_fee: float
    service_fee: float
    total_fee: float
    avg_price: float
    avg_energy_kwh: float
    avg_duration_min: float
    avg_battery_kwh: float
    dc_orders: int
    ac_orders: int
    dc_share_pct: float
    period_stats: List[Dict] = field(default_factory=list)
    hour_stats: List[Dict] = field(default_factory=list)
    model_stats: List[Dict] = field(default_factory=list)
    category_stats: List[Dict] = field(default_factory=list)
    pile_stats: List[Dict] = field(default_factory=list)
    battery_stats: List[Dict] = field(default_factory=list)
    charger_stats: List[Dict] = field(default_factory=list)
    day_count: int = 0
    avg_daily_fee: float = 0.0
    top_day: Optional[Dict] = None

    # ---------- 便捷查询 ----------
    def top_period(self) -> Optional[Dict]:
        rows = [p for p in self.period_stats if p["revenue"] > 0]
        return max(rows, key=lambda p: p["revenue"]) if rows else None

    def top_model(self) -> Optional[Dict]:
        return max(self.model_stats, key=lambda m: (m["orders"], m["fee"]), default=None)

    def top_pile(self) -> Optional[Dict]:
        return max(self.pile_stats, key=lambda p: p["fee"], default=None)

    def top_hour_by_orders(self) -> Optional[Dict]:
        return max(self.hour_stats, key=lambda h: h["orders"], default=None)

    def top_hour_by_fee(self) -> Optional[Dict]:
        return max(self.hour_stats, key=lambda h: h["fee"], default=None)

    def top_battery(self) -> Optional[Dict]:
        return max(self.battery_stats, key=lambda b: b["count"], default=None)

    def charger_stat(self, name: str) -> Optional[Dict]:
        return next((c for c in self.charger_stats if c["charger_type"] == name), None)

    # ---------- 本地结论 ----------
    def local_answer(self, key: str) -> str:
        if key == "revenue_period":
            top = self.top_period()
            if not top:
                return "本地数据：当前订单没有可用的分时段明细。"
            detail = "；".join(
                f"{p['period']} {p['revenue']:.2f} 元（{p['share_pct']:.1f}%）"
                for p in self.period_stats
            )
            return f"本地数据：{top['period']}收益最高，约 {top['revenue']:.2f} 元；{detail}。"

        if key == "top_model":
            top = self.top_model()
            if not top:
                return "本地数据：暂无车型字段，无法统计车型结构。"
            top5 = "、".join(
                f"{m['model']} {m['orders']} 单/{m['fee']:.0f} 元"
                for m in self.model_stats[:5]
            )
            categories = "；".join(
                f"{c['category']} {c['orders']} 单/{c['fee']:.0f} 元"
                for c in self.category_stats
            )
            return (
                f"本地数据：订单最多的是 {top['model']}（{top['orders']} 单，"
                f"{top['fee']:.2f} 元）。TOP5：{top5}。类别：{categories}。"
            )

        if key == "top_pile":
            top = self.top_pile()
            if not top:
                return "本地数据：暂无车位收益数据。"
            lowest = min(self.pile_stats, key=lambda p: p["fee"])
            return (
                f"本地数据：车位 {top['spot_name']} 收益最高（{top['orders']} 单，"
                f"{top['fee']:.2f} 元）；车位 {lowest['spot_name']} 相对最低"
                f"（{lowest['orders']} 单，{lowest['fee']:.2f} 元）。"
            )

        if key == "charger_mix":
            dc = self.charger_stat("直流快充")
            ac = self.charger_stat("交流慢充")
            def _one(item: Optional[Dict]) -> str:
                if not item:
                    return "无数据"
                return (
                    f"{item['orders']} 单，{item['energy_kwh']:.1f} 度，"
                    f"{item['fee']:.2f} 元，单均 {item['avg_energy_kwh']:.1f} 度"
                )
            return f"本地数据：直流快充 {_one(dc)}；交流慢充 {_one(ac)}。"

        if key == "battery":
            top = self.top_battery()
            if not top:
                return "本地数据：暂无电池容量字段。"
            dist = "、".join(
                f"{b['label']} {b['count']} 单" for b in self.battery_stats if b["count"] > 0
            )
            return f"本地数据：电池容量主要集中在 {top['label']}（{top['count']} 单）。分布：{dist}。"

        if key == "peak_hour":
            by_orders = self.top_hour_by_orders()
            by_fee = self.top_hour_by_fee()
            if not by_orders or not by_fee:
                return "本地数据：暂无小时级订单。"
            return (
                f"本地数据：订单最多的小时是 {by_orders['hour']}:00"
                f"（{by_orders['orders']} 单，{by_orders['fee']:.2f} 元）；"
                f"收益最高的小时是 {by_fee['hour']}:00"
                f"（{by_fee['orders']} 单，{by_fee['fee']:.2f} 元）。"
            )

        if key == "optimize":
            top = self.top_period()
            peak = next((p for p in self.period_stats if p["period"] == "峰段"), None)
            valley = next((p for p in self.period_stats if p["period"] == "谷段"), None)
            unit = lambda p: (p["energy_fee"] / p["energy_kwh"]) if p and p["energy_kwh"] else 0.0
            spread = unit(peak) - unit(valley) if peak and valley else 0.0
            return (
                f"本地数据：峰段收益占比 {peak['share_pct'] if peak else 0:.1f}%，"
                f"谷段 {valley['share_pct'] if valley else 0:.1f}%；"
                f"峰谷电费差约 {spread:.3f} 元/度；直流快充占比 {self.dc_share_pct:.1f}%。"
                "可据此评估引导谷段充电、调整车位和服务费的收益空间。"
            )

        return "本地数据：暂无对应结论。"

    # ---------- AI 提示词用的完整文本 ----------
    def to_text(self) -> str:
        lines: List[str] = [
            "【总览】",
            f"订单 {self.order_count} 单；总电量 {self.total_energy_kwh:.1f} 度；"
            f"总费用 {self.total_fee:.2f} 元（电费 {self.energy_fee:.2f} + "
            f"服务费 {self.service_fee:.2f}）；平均综合单价 {self.avg_price:.3f} 元/度；"
            f"单均电量 {self.avg_energy_kwh:.1f} 度；平均时长 {self.avg_duration_min:.1f} 分钟；"
            f"平均电池 {self.avg_battery_kwh:.1f} kWh。",
            f"直流快充 {self.dc_orders} 单（{self.dc_share_pct:.1f}%），"
            f"交流慢充 {self.ac_orders} 单。",
            "",
            "【峰/平/谷收益】",
        ]
        for item in self.period_stats:
            lines.append(
                f"- {item['period']}：电量 {item['energy_kwh']:.1f} 度，"
                f"电费 {item['energy_fee']:.2f} 元，服务费 {item['service_fee']:.2f} 元，"
                f"收益 {item['revenue']:.2f} 元（{item['share_pct']:.1f}%），"
                f"涉及 {item['order_count']} 单"
            )

        by_orders = self.top_hour_by_orders()
        by_fee = self.top_hour_by_fee()
        if by_orders and by_fee:
            lines.extend(
                [
                    "",
                    "【时段】",
                    f"订单最多：{by_orders['hour']}:00（{by_orders['orders']} 单，"
                    f"{by_orders['fee']:.2f} 元）；"
                    f"收益最高：{by_fee['hour']}:00（{by_fee['orders']} 单，"
                    f"{by_fee['fee']:.2f} 元）。",
                ]
            )

        if self.model_stats:
            lines.extend(["", "【车型】"])
            lines.append(
                "TOP5：" + "；".join(
                    f"{m['model']} {m['orders']} 单/{m['fee']:.0f} 元"
                    for m in self.model_stats[:5]
                )
            )
            lines.append(
                "类别：" + "；".join(
                    f"{c['category']} {c['orders']} 单/{c['fee']:.0f} 元"
                    for c in self.category_stats
                )
            )

        if self.pile_stats:
            lines.extend(["", "【车位】"])
            lines.append(
                "；".join(
                    f"车位 {p['spot_name']} {p['orders']} 单/{p['fee']:.0f} 元"
                    for p in self.pile_stats
                )
            )

        if self.battery_stats:
            lines.extend(["", "【电池容量】"])
            lines.append(
                "；".join(
                    f"{b['label']} {b['count']} 单" for b in self.battery_stats if b["count"]
                )
            )

        if self.charger_stats:
            lines.extend(["", "【交直流】"])
            for item in self.charger_stats:
                lines.append(
                    f"- {item['charger_type']}：{item['orders']} 单，"
                    f"{item['energy_kwh']:.1f} 度，{item['fee']:.2f} 元，"
                    f"单均 {item['avg_energy_kwh']:.1f} 度"
                )

        lines.extend(
            [
                "",
                "【日均】",
                f"覆盖 {self.day_count} 天，日均 {self.order_count / max(self.day_count, 1):.1f} 单，"
                f"日均收益 {self.avg_daily_fee:.2f} 元。",
            ]
        )
        if self.top_day:
            lines.append(
                f"收益最高日期：{self.top_day['date']}（{self.top_day['total_fee']:.2f} 元，"
                f"{self.top_day['order_count']} 单）。"
            )
        return "\n".join(lines)


def build_site_summary(orders: List[Dict]) -> Optional[SiteSummary]:
    """把订单列表聚合成 SiteSummary；无订单返回 None。"""
    df = to_dataframe(orders)
    if df.empty:
        return None

    ov = overview(df)
    energy_fee = float(df["energy_fee"].sum())
    service_fee = float(df["service_fee"].sum())
    total_fee = float(df["total_fee"].sum())
    dc_orders = int((df["charger_type"] == "直流快充").sum())
    ac_orders = int((df["charger_type"] == "交流慢充").sum())

    # ---- 峰/平/谷：服务费按各段电量占比分摊 ----
    buckets: Dict[str, Dict] = {}

    def _bucket(name: str) -> Dict:
        if name not in buckets:
            buckets[name] = {
                "period": name,
                "energy_kwh": 0.0,
                "energy_fee": 0.0,
                "service_fee": 0.0,
                "revenue": 0.0,
                "order_count": 0,
            }
        return buckets[name]

    for _, row in df.iterrows():
        periods = row.get("periods") or []
        order_energy = sum(float(p.get("energy_kwh", 0.0)) for p in periods)
        order_service = float(row.get("service_fee", 0.0) or 0.0)
        seen = set()
        if not periods:
            item = _bucket("未知")
            item["energy_kwh"] += float(row.get("energy_kwh", 0.0))
            item["energy_fee"] += float(row.get("energy_fee", 0.0))
            item["service_fee"] += order_service
            item["revenue"] += float(row.get("total_fee", 0.0))
            item["order_count"] += 1
            continue
        for p in periods:
            name = str(p.get("period") or "平段")
            energy = float(p.get("energy_kwh", 0.0) or 0.0)
            fee = float(p.get("fee", 0.0) or 0.0)
            service = order_service * (energy / order_energy) if order_energy > 0 else 0.0
            item = _bucket(name)
            item["energy_kwh"] += energy
            item["energy_fee"] += fee
            item["service_fee"] += service
            item["revenue"] += fee + service
            if name not in seen:
                item["order_count"] += 1
                seen.add(name)

    total_period_revenue = sum(v["revenue"] for v in buckets.values())
    for item in buckets.values():
        item["share_pct"] = (
            item["revenue"] / total_period_revenue * 100.0 if total_period_revenue else 0.0
        )
    period_stats = [
        buckets[name] for name in PERIOD_ORDER if name in buckets
    ]
    if "未知" in buckets:
        period_stats.append(buckets["未知"])

    # ---- 小时 ----
    hourly = (
        df.groupby("hour")
        .agg(
            orders=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            fee=("total_fee", "sum"),
        )
        .reset_index()
        .sort_values("hour")
    )
    hour_stats = [
        {
            "hour": int(r["hour"]),
            "orders": int(r["orders"]),
            "energy_kwh": float(r["energy_kwh"]),
            "fee": float(r["fee"]),
        }
        for _, r in hourly.iterrows()
    ]

    # ---- 车型/类别 ----
    models = df[df["vehicle_model"].notna() & (df["vehicle_model"] != "—")]
    model_group = (
        models.groupby("vehicle_model")
        .agg(
            orders=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            fee=("total_fee", "sum"),
        )
        .reset_index()
        .sort_values(["orders", "fee"], ascending=False)
    )
    model_stats = [
        {
            "model": str(r["vehicle_model"]),
            "orders": int(r["orders"]),
            "energy_kwh": float(r["energy_kwh"]),
            "fee": float(r["fee"]),
        }
        for _, r in model_group.iterrows()
    ]
    category_group = (
        df.assign(category=df["vehicle_category"].fillna("未知").replace("—", "未知"))
        .groupby("category")
        .agg(
            orders=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            fee=("total_fee", "sum"),
        )
        .reset_index()
        .sort_values("orders", ascending=False)
    )
    category_stats = [
        {
            "category": str(r["category"]),
            "orders": int(r["orders"]),
            "energy_kwh": float(r["energy_kwh"]),
            "fee": float(r["fee"]),
        }
        for _, r in category_group.iterrows()
    ]

    # ---- 车位（按 uid 分组，展示名取下单时的快照）----
    if "spot_name" not in df.columns:
        df = df.assign(spot_name="")
    df["spot_name"] = df["spot_name"].fillna("").astype(str)
    df.loc[df["spot_name"] == "", "spot_name"] = df["pile_id"].astype(str)
    pile_group = (
        df.groupby("pile_id", dropna=False)
        .agg(
            spot_name=("spot_name", "first"),
            orders=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            fee=("total_fee", "sum"),
        )
        .reset_index()
        .sort_values("fee", ascending=False)
    )
    pile_stats = [
        {
            "pile_id": str(r["pile_id"]),
            "spot_name": str(r["spot_name"]),
            "orders": int(r["orders"]),
            "energy_kwh": float(r["energy_kwh"]),
            "fee": float(r["fee"]),
        }
        for _, r in pile_group.iterrows()
    ]

    # ---- 电池 ----
    battery_stats = battery_distribution(df).to_dict("records")

    # ---- 交直流 ----
    charger_group = (
        df.assign(charger=df["charger_type"].fillna("未知").replace("—", "未知"))
        .groupby("charger")
        .agg(
            orders=("start_time", "count"),
            energy_kwh=("energy_kwh", "sum"),
            fee=("total_fee", "sum"),
        )
        .reset_index()
        .sort_values("orders", ascending=False)
    )
    charger_stats = [
        {
            "charger_type": str(r["charger"]),
            "orders": int(r["orders"]),
            "energy_kwh": float(r["energy_kwh"]),
            "fee": float(r["fee"]),
            "avg_energy_kwh": float(r["energy_kwh"] / r["orders"]) if r["orders"] else 0.0,
        }
        for _, r in charger_group.iterrows()
    ]

    daily = daily_trend(df)
    day_count = int(len(daily))
    avg_daily_fee = float(daily["total_fee"].mean()) if day_count else 0.0
    top_day = None
    if day_count:
        top_row = daily.sort_values("total_fee", ascending=False).iloc[0]
        top_day = {
            "date": str(top_row["date"]),
            "total_fee": float(top_row["total_fee"]),
            "order_count": int(top_row["order_count"]),
        }

    return SiteSummary(
        order_count=int(len(df)),
        total_energy_kwh=_f(ov["total_energy_kwh"]),
        energy_fee=_f(energy_fee, 2),
        service_fee=_f(service_fee, 2),
        total_fee=_f(total_fee, 2),
        avg_price=float(ov["avg_price"]),
        avg_energy_kwh=float(ov["avg_energy_per_order"]),
        avg_duration_min=_f(df["duration_min"].mean()),
        avg_battery_kwh=float(ov["avg_battery_kwh"]),
        dc_orders=dc_orders,
        ac_orders=ac_orders,
        dc_share_pct=float(ov["dc_share"]),
        period_stats=period_stats,
        hour_stats=hour_stats,
        model_stats=model_stats,
        category_stats=category_stats,
        pile_stats=pile_stats,
        battery_stats=battery_stats,
        charger_stats=charger_stats,
        day_count=day_count,
        avg_daily_fee=_f(avg_daily_fee, 2),
        top_day=top_day,
    )


RAW_HEADERS = [
    "单号", "车位", "车牌", "车型", "类别", "定位", "电池kWh",
    "开始时间", "结束时间", "时长min", "电量kWh", "起始SOC", "结束SOC",
    "峰值kW", "充电类型", "平台V", "电费", "服务费", "合计", "状态",
    "峰段电量", "平段电量", "谷段电量", "峰段电费", "平段电费", "谷段电费",
]


def build_raw_data_text(orders: List[Dict]) -> str:
    """把全部订单逐条导出为紧凑 CSV 文本，供“不本地汇总”模式直接发给大模型。

    只做格式转换，不做任何统计汇总；列包含订单、车辆、电池、SOC、电量、费用、
    峰平谷分段电量/电费等全部关键原始字段。
    """
    if not orders:
        return "【本站点全部订单原始数据】共 0 条订单。"

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(RAW_HEADERS)
    for order in orders:
        energy = {"峰段": 0.0, "平段": 0.0, "谷段": 0.0}
        fee = {"峰段": 0.0, "平段": 0.0, "谷段": 0.0}
        for item in order.get("periods") or []:
            name = str(item.get("period") or "平段")
            if name in energy:
                energy[name] += float(item.get("energy_kwh", 0.0) or 0.0)
                fee[name] += float(item.get("fee", 0.0) or 0.0)
        writer.writerow(
            [
                order.get("order_id", ""),
                order.get("spot_name") or order.get("pile_id", ""),
                order.get("plate", ""),
                order.get("vehicle_model", "—"),
                order.get("vehicle_category", "—"),
                order.get("vehicle_segment", "—"),
                f'{float(order.get("battery_kwh") or 0):.1f}',
                order.get("start_time", ""),
                order.get("end_time", ""),
                f'{float(order.get("duration_min") or 0):.1f}',
                f'{float(order.get("energy_kwh") or 0):.2f}',
                f'{float(order.get("start_soc") or 0):.1f}',
                f'{float(order.get("end_soc") or 0):.1f}',
                f'{float(order.get("peak_power_kw") or 0):.1f}',
                order.get("charger_type", "—"),
                order.get("voltage_platform_v", ""),
                f'{float(order.get("energy_fee") or 0):.2f}',
                f'{float(order.get("service_fee") or 0):.2f}',
                f'{float(order.get("total_fee") or 0):.2f}',
                order.get("status", ""),
                f'{energy["峰段"]:.2f}',
                f'{energy["平段"]:.2f}',
                f'{energy["谷段"]:.2f}',
                f'{fee["峰段"]:.2f}',
                f'{fee["平段"]:.2f}',
                f'{fee["谷段"]:.2f}',
            ]
        )
    return (
        f"【本站点全部订单原始数据】共 {len(orders)} 条，逐条 CSV，字段顺序："
        + "、".join(RAW_HEADERS)
        + "\n"
        + buffer.getvalue()
    )


# 管理员视角的问题选项：label 用于按钮，question 发给大模型，key 对应本地结论
SITE_QUESTIONS = [
    {
        "key": "revenue_period",
        "label": "哪个时段收益最高？",
        "question": "本站峰/平/谷哪个时段收益最高？各时段的电量、电费、服务费和收益占比是多少？",
    },
    {
        "key": "top_model",
        "label": "哪个车型最多？",
        "question": "本站订单最多、收益贡献最大的车型是哪个？轿车和 SUV 的结构如何？",
    },
    {
        "key": "top_pile",
        "label": "哪个车位最赚钱？",
        "question": "哪个车位的收益和利用率最高？哪些车位相对偏低，可能的原因是什么？",
    },
    {
        "key": "charger_mix",
        "label": "直流 vs 交流",
        "question": "直流快充和交流慢充的订单、电量、收益和单均电量对比如何？",
    },
    {
        "key": "battery",
        "label": "电池容量分布",
        "question": "本站车辆的电池容量主要集中在哪些区间？对桩位配置和服务有什么启示？",
    },
    {
        "key": "peak_hour",
        "label": "高峰时段",
        "question": "订单最多和收益最高的小时分别是哪个？高峰时段应如何调度？",
    },
    {
        "key": "optimize",
        "label": "怎么优化？",
        "question": "从站点管理员角度，本站有哪些可以优化的地方？请给出 2~3 条可执行建议。",
    },
]
