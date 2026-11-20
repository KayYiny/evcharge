# -*- coding: utf-8 -*-
"""数据分析页（evcharge.presentation.pages.analysis_page）：Pandas 统计 + matplotlib 图表（Flet 1.1.1）。"""

from __future__ import annotations

import flet as ft

from evcharge.application.analytics.stats import (
    battery_distribution,
    daily_trend,
    hourly_profile,
    overview,
    period_distribution,
    pile_stats,
    to_dataframe,
    vehicle_mix,
)
from evcharge.application.services import OrderService
from evcharge.presentation.components import theme
from evcharge.presentation.components.charts import bar_chart, line_chart, pie_chart


def build_analysis_page(
    page: ft.Page, orders: OrderService, branding=None
) -> ft.Control:
    """构建数据分析页：概览指标 + 6 组图表。

    ``branding`` 由 app 层传入（站点徽标/电价档），电价档切换后本页会重建，
    因此眉标与图表配色都能跟随设置变化。
    """
    eyebrow_text = (
        branding.eyebrow("经营分析") if branding is not None else "经营分析"
    )
    eyebrow_color = branding.accent if branding is not None else theme.ACCENT
    order_rows = orders.list_orders()
    df = to_dataframe(order_rows)
    ov = overview(df)

    metric_defs = [
        ("总订单数", f"{ov['order_count']}", "单", theme.AMBER),
        ("累计充电量", f"{ov['total_energy_kwh']:.1f}", "度", theme.CYAN),
        ("累计费用", f"¥{ov['total_fee']:.2f}", "", theme.GREEN),
        ("平均综合单价", f"{ov['avg_price']:.3f}", "元/度", theme.PURPLE),
        ("单均电量", f"{ov['avg_energy_per_order']:.1f}", "度", theme.CYAN),
        ("平均电池容量", f"{ov['avg_battery_kwh']:.1f}", "kWh", theme.AMBER),
        ("直流快充占比", f"{ov['dc_share']:.0f}", "%", theme.GREEN),
    ]
    metric_cards = []
    for label, value, unit, accent in metric_defs:
        metric_card = theme.stat_card(label, value, unit, accent)
        metric_card.expand = True
        metric_cards.append(metric_card)
    metrics = ft.Row(metric_cards, spacing=8)

    # ---------- 图表数据 ----------
    daily = daily_trend(df)
    daily_labels = [str(r["date"])[5:] for _, r in daily.iterrows()][-31:]  # MM-DD，最多 31 天
    daily_values = [float(r["total_fee"]) for _, r in daily.iterrows()][-31:]
    daily_chart = bar_chart(
        daily_labels, daily_values,
        title="每日费用趋势（近 31 天）", ylabel="费用 ¥",
        colors=[theme.AMBER], width=540,
    )

    periods = period_distribution(df)
    period_labels = [str(r["period"]) for _, r in periods.iterrows()]
    period_values = [float(r["share_pct"]) for _, r in periods.iterrows()]
    period_chart = pie_chart(
        period_labels,
        period_values,
        title="峰/平/谷用电结构",
        colors=[theme.RED, theme.AMBER, theme.GREEN],
        width=540,
    )

    piles = pile_stats(df)
    # 车位名是文本（如 A001、7），不能转 int，否则数据分析页整页报错
    pile_labels = [
        f'车位 {r["spot_name"] or r["pile_id"]}' for _, r in piles.iterrows()
    ]
    pile_values = [int(r["order_count"]) for _, r in piles.iterrows()]
    pile_chart = bar_chart(
        pile_labels, pile_values,
        title="各车位订单量分布", ylabel="订单数",
        colors=[theme.CYAN], width=540,
    )

    hourly = hourly_profile(df)
    hourly_values = [float(v) for v in hourly["avg_energy_kwh"].tolist()]
    hourly_chart = line_chart(
        hourly_values,
        title="小时用电画像（0~23 时平均电量）",
        ylabel="平均电量 度",
        color=theme.AMBER,
        width=540,
    )

    battery = battery_distribution(df)
    battery_chart = bar_chart(
        [str(r["label"]) for _, r in battery.iterrows()],
        [int(r["count"]) for _, r in battery.iterrows()],
        title="电池容量分布（kWh）",
        ylabel="订单数",
        colors=[theme.PURPLE] * len(battery),
        width=540,
    )

    vehicles = vehicle_mix(df)
    vehicle_chart = pie_chart(
        [str(r["category"]) for _, r in vehicles.iterrows()],
        [int(r["count"]) for _, r in vehicles.iterrows()],
        title="车型结构（轿车/SUV）",
        colors=[theme.PURPLE, theme.CYAN],
        width=540,
    )

    def _chart_card(title: str, chart: ft.Control, note: str = "") -> ft.Container:
        return theme.card(
            ft.Column(
                [
                    ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                    ft.Text(note, size=10, color=theme.MUTED) if note else ft.Container(height=0),
                    chart,
                ],
                spacing=8,
            ),
            padding=14,
            expand=True,
        )

    empty_hint = (
        theme.card(
            ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=theme.ACCENT),
                    ft.Text(
                        "暂无订单数据：先去「实时监控」上电产生订单，或生成历史数据。",
                        size=12,
                        color=theme.MUTED,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            padding=14,
            bgcolor=theme.SURFACE_2,
        )
        if df.empty
        else ft.Container(height=0)
    )

    header = theme.page_header(
        theme.eyebrow_label(eyebrow_text, color=eyebrow_color),
        "数据分析",
        "平均单价 = 总费用 / 总电量",
        trailing=ft.Text(f"{ov['order_count']} 条订单", size=10, color=theme.MUTED),
    )
    return ft.Column(
        [
            header,
            empty_hint,
            metrics,
            ft.Text(
                "图表随订单数据实时更新",
                size=10,
                color=theme.MUTED,
            ),
            ft.Row(
                [
                    _chart_card("每日费用趋势", daily_chart),
                    _chart_card("峰/平/谷用电结构", period_chart, "谷段占比越高越省钱"),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    _chart_card("各车位订单量分布", pile_chart),
                    _chart_card("小时用电画像", hourly_chart, "识别充电高峰时段"),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    _chart_card("电池容量分布", battery_chart, "来自车型电池容量字段"),
                    _chart_card("车型结构", vehicle_chart, "高速场景以中长途车型为主"),
                ],
                spacing=12,
            ),
        ],
        spacing=14,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
