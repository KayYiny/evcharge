# -*- coding: utf-8 -*-
"""monitor_detail：右侧详情面板与功率曲线（monitor 页的「详情」部分）。


"""

from __future__ import annotations

import flet as ft

from evcharge.domain.models import Order, PileStatus
from evcharge.presentation.components import theme
from evcharge.presentation.components.monitor_palette import STATUS_COLORS
from evcharge.presentation.components.charts import line_chart
from evcharge.presentation.components.order_detail import show_order_dialog


class MonitorDetail:
    def _build_detail(self) -> None:
        self.detail_col.controls = [
            self.detail_title,
            self.detail_status,
            self.detail_session,
            self.detail_start,
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "SOC",
                                size=10,
                                color=theme.MUTED,
                                font_family=theme.MONO,
                            ),
                            self.soc_text,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self.soc_bar,
                ],
                spacing=4,
            ),
            ft.Row(
                [
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(key, size=10, color=theme.MUTED),
                                self.metric_values[key],
                            ],
                            spacing=2,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=8,
                        border_radius=8,
                        bgcolor=theme.SURFACE_2,
                        expand=True,
                    )
                    for key in self.metric_values
                ],
                spacing=6,
            ),
            ft.Row(
                [self.start_btn, self.stop_btn]
                + ([self.delete_btn] if self.is_admin else []),
                spacing=8,
            ),
            ft.Row([self.camera_btn, self.gallery_btn], spacing=8),
            self.plate_hint,
        ]

    def _show_bill(self, order: Order) -> None:
        """结算后弹出账单明细（与订单页共用同一套账单渲染）。"""
        if self.page is not None:
            show_order_dialog(self.page, order.to_dict(), title="充电结算账单")

    def _refresh_detail(self) -> None:
        if self.selected_pile_id is None:
            self.detail_title.value = "暂无车位"
            self.detail_status.value = "请先添加车位"
            self.detail_status.color = theme.MUTED
            self.detail_session.value = "请先在右上角添加车位"
            self.detail_start.value = ""
            self.soc_text.value = "SOC —"
            self.soc_bar.value = 0
            for text in self.metric_values.values():
                text.value = "—"
            self.start_btn.disabled = True
            self.stop_btn.disabled = True
            self.delete_btn.disabled = True
            self.chart_container.content = theme.empty_state(
                "暂无车位", "先添加车位", icon=ft.Icons.ADD_ROAD
            )
            return
        pile = self.manager.pile(self.selected_pile_id)
        self.detail_title.value = (
            f"车位 {pile.name} · "
            f"{'直流快充' if pile.charger.is_dc else '交流慢充'}"
        )
        self.detail_status.value = (
            f"{pile.charger.nominal_power_kw:.0f}kW · {pile.status.value}"
        )
        self.detail_status.color = STATUS_COLORS.get(pile.status, theme.MUTED)

        session = pile.current_session
        if session is not None:
            profile = session.profile
            self.detail_session.value = (
                f"车牌：{session.plate} · {profile.model}"
                f"（{profile.category}/{profile.segment}）"
            )
            self.detail_start.value = (
                f"电池 {session.battery_kwh:.1f} kWh · "
                f"峰值 {profile.peak_power_kw:.0f} kW · "
                f"{profile.platform_v}V · "
                f"{session.start_time.strftime('%H:%M')} 开始"
            )
            self.soc_text.value = (
                f"SOC {session.soc:.1f}% / 目标 {session.target_soc:.0f}% · "
                f"已充 {session.energy_kwh:.1f} 度"
            )
            self.soc_bar.value = max(0.0, min(1.0, session.soc / 100.0))
        else:
            self.detail_session.value = "无进行中的会话"
            self.detail_start.value = ""
            self.soc_text.value = "SOC —"
            self.soc_bar.value = 0

        telemetry = pile.latest
        values = {
            "电压": f"{telemetry.voltage:.1f} V" if telemetry else "—",
            "电流": f"{telemetry.current:.1f} A" if telemetry else "—",
            "功率": f"{telemetry.power_kw:.1f} kW" if telemetry else "—",
            "本次电量": f"{telemetry.energy_kwh:.2f} kWh" if telemetry else "—",
        }
        for key, text in self.metric_values.items():
            text.value = values[key]

        self.start_btn.disabled = pile.status != PileStatus.IDLE
        self.stop_btn.disabled = pile.status != PileStatus.CHARGING
        self.delete_btn.disabled = False

    def _refresh_chart(self) -> None:
        if self.selected_pile_id is None:
            self.chart_container.content = theme.empty_state(
                "暂无车位", "先添加车位", icon=ft.Icons.SHOW_CHART,
            )
            return
        pile = self.manager.pile(self.selected_pile_id)
        history = pile.history[-60:]
        if len(history) < 2:
            self.chart_container.content = theme.empty_state(
                "等待遥测数据",
                "上电后曲线实时刷新",
                icon=ft.Icons.SHOW_CHART,
            )
            return
        values = [t.power_kw for t in history]
        self.chart_container.content = ft.Column(
            [
                ft.Text(
                    f"{pile.name} · 功率曲线（最近 {len(values)} 点）",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=theme.TEXT,
                ),
                line_chart(
                    values,
                    ylabel="功率 kW",
                    color=theme.CYAN,
                    width=980,
                    height=180,
                ),
            ],
            spacing=6,
        )
