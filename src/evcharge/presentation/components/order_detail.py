# -*- coding: utf-8 -*-
"""订单详情弹窗（evcharge.presentation.components.order_detail）。

把订单字典渲染成完整的车辆 / 充电 / 费用账单，供订单管理页点击行时调用。
"""

from __future__ import annotations

import flet as ft

from evcharge.presentation.components import theme


def _detail_row(label: str, value: str, mono: bool = False) -> ft.Row:
    text_kwargs = {"font_family": theme.MONO} if mono else {}
    return ft.Row(
        [
            ft.Text(label, width=76, size=11, color=theme.MUTED),
            ft.Text(value, size=11, color=theme.TEXT, selectable=True, **text_kwargs),
        ],
        spacing=8,
    )


def _section(title: str, rows: list[ft.Control]) -> ft.Column:
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Container(width=3, height=11, bgcolor=theme.AMBER, border_radius=2),
                    ft.Text(title, size=10, weight=ft.FontWeight.BOLD, color=theme.AMBER),
                ],
                spacing=6,
            ),
            *rows,
        ],
        spacing=6,
    )


def _period_text(order: dict) -> str:
    periods = order.get("periods") or []
    return "；".join(
        f'{p.get("period", "—")} {float(p.get("energy_kwh") or 0):.1f} 度'
        f' / ¥{float(p.get("fee") or 0):.2f}'
        for p in periods
    ) or "—"


def show_order_dialog(
    page: ft.Page, order: dict, title: str | None = None
) -> None:
    """在 page 上打开订单完整账单弹窗。

    title 为空时显示「订单 #单号」；结算即时弹窗可传自定义标题。
    """
    content = ft.Column(
        [
            _section(
                "车辆",
                [
                    _detail_row("车牌", str(order.get("plate") or "—")),
                    _detail_row("车型", str(order.get("vehicle_model") or "—")),
                    _detail_row(
                        "类别",
                        f'{order.get("vehicle_category") or "—"} / '
                        f'{order.get("vehicle_segment") or "—"}',
                    ),
                    _detail_row(
                        "电池", f'{float(order.get("battery_kwh") or 0):.1f} kWh'
                    ),
                    _detail_row(
                        "平台", f'{order.get("voltage_platform_v") or "—"} V'
                    ),
                ],
            ),
            _section(
                "充电",
                [
                    _detail_row(
                        "车位",
                        f'{order.get("spot_name") or order.get("pile_id")} 车位 · '
                        f'{order.get("charger_type")}',
                    ),
                    _detail_row(
                        "开始", str(order.get("start_time") or "—"), mono=True
                    ),
                    _detail_row(
                        "结束", str(order.get("end_time") or "—"), mono=True
                    ),
                    _detail_row(
                        "时长", f'{float(order.get("duration_min") or 0):.0f} 分钟'
                    ),
                    _detail_row(
                        "电量", f'{float(order.get("energy_kwh") or 0):.2f} 度'
                    ),
                    _detail_row(
                        "SOC",
                        f'{float(order.get("start_soc") or 0):.0f}% → '
                        f'{float(order.get("end_soc") or 0):.0f}%',
                        mono=True,
                    ),
                    _detail_row(
                        "峰值",
                        f'{float(order.get("peak_power_kw") or 0):.0f} kW',
                        mono=True,
                    ),
                ],
            ),
            _section(
                "费用",
                [
                    _detail_row(
                        "电费",
                        f'¥{float(order.get("energy_fee") or 0):.2f}',
                        mono=True,
                    ),
                    _detail_row(
                        "服务费",
                        f'¥{float(order.get("service_fee") or 0):.2f}',
                        mono=True,
                    ),
                    _detail_row(
                        "合计",
                        f'¥{float(order.get("total_fee") or 0):.2f}',
                        mono=True,
                    ),
                    _detail_row("峰平谷", _period_text(order)),
                ],
            ),
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        width=560,
    )
    dialog = ft.AlertDialog(
        modal=True,
        scrollable=True,
        bgcolor=theme.SURFACE,
        title=ft.Text(
            title or f'订单 #{order.get("order_id", "—")}',
            size=15,
            weight=ft.FontWeight.BOLD,
            color=theme.TEXT,
        ),
        content=content,
        actions=[ft.TextButton("关闭", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
