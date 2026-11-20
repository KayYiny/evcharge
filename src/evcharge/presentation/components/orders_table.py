# -*- coding: utf-8 -*-
"""订单页的台账表（表头 / 行 / 分页 / 空状态 / 详情弹窗）。
"""

from __future__ import annotations

import flet as ft

from evcharge.presentation.components import theme
from evcharge.presentation.components.order_detail import show_order_dialog

class OrdersTable:
    def _build_table(self) -> None:
        self.table_note = ft.Text("", size=10, color=theme.DIM)
        self.rows_col = ft.Column(spacing=0)

        self.page_info = ft.Text("", size=10, color=theme.MUTED)
        self.page_label = ft.Text("", size=10, color=theme.MUTED, font_family=theme.MONO)
        self.page_size_dd = ft.Dropdown(
            label="每页",
            value=str(self.page_size),
            options=[
                ft.dropdown.Option("20", "20 条"),
                ft.dropdown.Option("50", "50 条"),
                ft.dropdown.Option("100", "100 条"),
            ],
            width=104,
            on_select=lambda e: self._on_page_size(),
            **theme.field_style(),
        )
        self.prev_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            tooltip="上一页",
            icon_color=theme.TEXT,
            on_click=lambda e: self._change_page(-1),
        )
        self.next_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            tooltip="下一页",
            icon_color=theme.TEXT,
            on_click=lambda e: self._change_page(1),
        )
        footer = ft.Container(
            content=ft.Row(
                [
                    self.page_info,
                    ft.Container(expand=True),
                    self.page_size_dd,
                    self.prev_btn,
                    self.page_label,
                    self.next_btn,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            padding=ft.Padding.only(left=16, right=16, top=10, bottom=12),
        )

        title = ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("订单明细", size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                            ft.Text("点击行查看完整账单", size=10, color=theme.MUTED),
                        ],
                        spacing=2,
                    ),
                    ft.Container(expand=True),
                    self.table_note,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=18, right=18, top=14, bottom=12),
        )

        self.table_card = theme.card(
            ft.Column(
                [
                    title,
                    theme.hairline(),
                    self._header_row(),
                    self.rows_col,
                    theme.hairline(),
                    footer,
                ],
                spacing=0,
            ),
            padding=0,
        )

    def _header_row(self) -> ft.Container:
        columns = [
            ("订单 / 车牌", 150),
            ("车位 / 类型", 110),
            ("车型", 140),
            ("开始时间", 120),
            ("时长", 62),
            ("电量", 72),
            ("SOC", 96),
            ("合计", 86),
            ("", 24),
        ]
        return ft.Container(
            content=ft.Row(
                [
                    ft.Text(
                        label,
                        width=width,
                        size=9,
                        weight=ft.FontWeight.BOLD,
                        color=theme.MUTED,
                    )
                    for label, width in columns
                ],
                spacing=8,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            bgcolor=theme.SURFACE_2,
        )

    def _empty_state(self) -> ft.Container:
        return theme.empty_state(
            "没有匹配的订单",
            "调整筛选条件，或点「重置」",
            icon=ft.Icons.RECEIPT_LONG_OUTLINED,
        )

    def _order_row(self, order: dict, index: int) -> ft.Container:
        charger = str(order.get("charger_type") or "—")
        is_dc = charger == "直流快充"
        charger_color = theme.CYAN if is_dc else theme.AMBER
        start_text = str(order.get("start_time") or "")
        start_day = start_text[5:10] if len(start_text) >= 10 else start_text
        start_time = start_text[11:19] if len(start_text) >= 19 else ""
        start_soc = float(order.get("start_soc") or 0)
        end_soc = float(order.get("end_soc") or 0)
        base_bg = theme.SURFACE if index % 2 == 0 else theme.SURFACE_2

        row = ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                f'#{order.get("order_id", "—")}',
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                color=theme.TEXT,
                                font_family=theme.MONO,
                            ),
                            ft.Text(
                                str(order.get("plate") or "—"),
                                size=10,
                                color=theme.MUTED,
                            ),
                        ],
                        width=150,
                        spacing=1,
                    ),
                    ft.Row(
                        [
                            ft.Text(
                                f'{order.get("spot_name") or order.get("pile_id") or "—"}',
                                size=11,
                                color=theme.TEXT,
                                font_family=theme.MONO,
                            ),
                            theme.badge("DC" if is_dc else "AC", charger_color),
                        ],
                        width=110,
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                str(order.get("vehicle_model") or "—"),
                                size=11,
                                color=theme.TEXT,
                                max_lines=1,
                            ),
                            ft.Text(
                                f'{order.get("vehicle_category") or "—"} · '
                                f'{float(order.get("battery_kwh") or 0):.0f} kWh',
                                size=9,
                                color=theme.MUTED,
                            ),
                        ],
                        width=140,
                        spacing=1,
                    ),
                    ft.Column(
                        [
                            ft.Text(start_day, size=10, color=theme.MUTED, font_family=theme.MONO),
                            ft.Text(start_time, size=11, color=theme.TEXT, font_family=theme.MONO),
                        ],
                        width=120,
                        spacing=1,
                    ),
                    ft.Text(
                        f'{float(order.get("duration_min") or 0):.0f} 分',
                        width=62,
                        size=11,
                        color=theme.TEXT,
                        font_family=theme.MONO,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    ft.Text(
                        f'{float(order.get("energy_kwh") or 0):.1f}',
                        width=72,
                        size=11,
                        color=theme.TEXT,
                        font_family=theme.MONO,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                f"{start_soc:.0f}→{end_soc:.0f}%",
                                size=10,
                                color=theme.TEXT,
                                font_family=theme.MONO,
                            ),
                            theme.meter(
                                end_soc / 100.0,
                                width=80,
                                height=3,
                                color=theme.CYAN,
                                track=theme.LINE,
                            ),
                        ],
                        width=96,
                        spacing=3,
                    ),
                    ft.Text(
                        f'¥{float(order.get("total_fee") or 0):.1f}',
                        width=86,
                        size=11,
                        weight=ft.FontWeight.BOLD,
                        color=theme.AMBER,
                        font_family=theme.MONO,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14, color=theme.DIM, width=24),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            bgcolor=base_bg,
            ink=True,
            on_click=lambda e, o=order: self._show_order(o),
        )

        def _hover(e) -> None:
            # Flet 1.1.1 的 on_hover e.data 是 bool（True=进入）
            row.bgcolor = theme.SURFACE_3 if e.data else base_bg
            try:
                row.update()
            except Exception:
                pass

        row.on_hover = _hover
        return row

    def _on_page_size(self) -> None:
        try:
            self.page_size = int(self.page_size_dd.value or "20")
        except ValueError:
            self.page_size = 20
        self.page_index = 1
        self._fill(update=True, reload=False)

    def _change_page(self, delta: int) -> None:
        self.page_index += delta
        self._fill(update=True, reload=False)

    # ==================================================================
    # 订单详情
    # ==================================================================
    def _show_order(self, order: dict) -> None:
        if self.page is not None:
            show_order_dialog(self.page, order)
