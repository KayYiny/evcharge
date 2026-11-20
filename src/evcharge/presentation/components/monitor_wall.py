# -*- coding: utf-8 -*-
"""monitor_wall：车位卡片墙与状态汇总（monitor 页的「卡片」部分）。


由 ``pages.monitor_page.MonitorPage`` 继承使用；方法直接操作页面实例的属性
（``self.cards_row`` / ``self.manager`` 等），与页面共享同一份状态。
"""

from __future__ import annotations

import flet as ft

from evcharge.domain.models import PileStatus
from evcharge.presentation.components import theme
from evcharge.presentation.components.monitor_palette import (
    SOC_METER_WIDTH,
    STATUS_BG,
    STATUS_COLORS,
)


class MonitorWall:
    def _visible_piles(self):
        """当前账号可见的车位列表（管理员看全部，车位用户只看自己）。"""
        if self.spot_filter is None:
            return list(self.manager.piles)
        return [
            p for p in self.manager.piles if str(p.pile_id) == self.spot_filter
        ]

    # ---------------- 构建 ----------------
    def _grouped_piles(self) -> list[tuple[str, list, str]]:
        """按充电类型分组（直流在前、交流在后），空组不返回。"""
        piles = self._visible_piles()
        dc = [p for p in piles if p.charger.is_dc]
        ac = [p for p in piles if not p.charger.is_dc]
        groups: list[tuple[str, list, str]] = []
        if dc:
            groups.append(("直流快充", dc, theme.ACCENT))
        if ac:
            groups.append(("交流慢充", ac, theme.CYAN))
        return groups

    @staticmethod
    def _spec_text(pile) -> str:
        """卡片右上角的桩规格：只显示功率档。"""
        charger = pile.charger
        if charger.is_dc:
            return f"{charger.nominal_power_kw:.0f}kW"
        return "7kW"

    def _summary_item(self, label: str, color: str, value: str) -> ft.Control:
        """状态卡右侧的小指标（空闲 / 充电中 / 合计功率）。"""
        value_text = ft.Text(
            value,
            size=12,
            weight=ft.FontWeight.W_600,
            color=color,
            font_family=theme.MONO,
        )
        self.summary_values[label] = value_text
        return ft.Row(
            [
                theme.dot(color, 6),
                ft.Text(label, size=10, color=theme.MUTED),
                value_text,
            ],
            spacing=6,
            tight=True,
        )

    def _build_wall(self) -> ft.Control:
        """车位卡片墙：按快充/慢充分组，窗口变宽时卡片自动换行。"""
        self.cards_row = []
        self.card_status.clear()
        self.card_power.clear()
        self.card_energy.clear()
        self.card_soc.clear()
        self.card_pill.clear()

        blocks: list[ft.Control] = []
        for title, piles, accent in self._grouped_piles():
            group_cards = [self._build_card(p) for p in piles]
            blocks.append(self._group_block(title, len(piles), group_cards, accent))

        if not blocks:
            self.cards_grid = None
            return self._empty_spots_card()

        self.cards_grid = ft.Column(blocks, spacing=16)
        return self.cards_grid

    def _group_block(
        self, title: str, count: int, cards: list[ft.Control], accent: str
    ) -> ft.Control:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(width=3, height=13, border_radius=2, bgcolor=accent),
                        ft.Text(
                            title,
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=theme.TEXT,
                        ),
                        ft.Text(f"{count} 个车位", size=10, color=theme.DIM),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(cards, wrap=True, spacing=12, run_spacing=12),
            ],
            spacing=10,
        )

    @staticmethod
    def _soc_meter() -> tuple[ft.Container, ft.Container]:
        """自绘 SOC 进度条（轨道 + 填充）。

        Flet 1.1 的 ``ft.ProgressBar`` 内部依赖 Flex 布局：把它放进
        ``Row(wrap=True)`` 的定宽卡片里会让整张卡片渲染失败（界面表现为
        一大块灰色空白）。因此卡片内统一使用两个 Container 拼出来的进度条。
        返回 ``(填充条, 轨道)``。
        """
        fill = ft.Container(
            width=0, height=4, bgcolor=theme.CYAN, border_radius=2
        )
        track = ft.Container(
            content=fill,
            height=4,
            bgcolor=theme.LINE,
            border_radius=2,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        return fill, track

    def _build_card(self, pile) -> ft.Container:
        """单个车位卡片：车位名 + 状态 + 实时功率 + 电量/SOC 进度（内部按 uid 索引）。"""
        pile_id = pile.pile_id

        pill_text = ft.Text(
            pile.status.value, size=10, weight=ft.FontWeight.W_500
        )
        status_pill = ft.Container(
            content=pill_text,
            padding=ft.Padding.symmetric(horizontal=9, vertical=3),
            border_radius=theme.RADIUS_PILL,
        )

        id_badge = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.EV_STATION, size=13, color=theme.ACCENT),
                    ft.Text(
                        pile.name,
                        size=13,
                        weight=ft.FontWeight.BOLD,
                        color=theme.ACCENT,
                        font_family=theme.MONO,
                    ),
                ],
                spacing=5,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=theme.RADIUS_CTRL,
            bgcolor=ft.Colors.with_opacity(0.08, theme.ACCENT),
        )

        power_text = ft.Text(
            "0.00",
            size=26,
            weight=ft.FontWeight.W_600,
            color=theme.TEXT,
            font_family=theme.MONO,
        )
        unit_text = ft.Text("kW", size=10, color=theme.MUTED)
        spec_text = ft.Text(self._spec_text(pile), size=10, color=theme.DIM)
        energy_text = ft.Text("0.0 度 · SOC 0%", size=10, color=theme.DIM)
        soc_fill, soc_bar = self._soc_meter()

        # 注意：卡片会被放进 Row(wrap=True)，这里不能使用 expand 占位符
        # （Wrap 的子控件被包进 Expanded / Expanded 内嵌套 Expanded 都会让整行渲染失败），
        # 因此用 SPACE_BETWEEN 对齐代替 spacer。
        card = theme.card(
            ft.Column(
                [
                    ft.Row(
                        [id_badge, status_pill],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            ft.Row(
                                [power_text, unit_text],
                                spacing=3,
                                tight=True,
                                vertical_alignment=ft.CrossAxisAlignment.END,
                            ),
                            spec_text,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    energy_text,
                    soc_bar,
                ],
                spacing=9,
            ),
            padding=14,
            width=218,
            on_click=lambda e, pid=pile_id: self._select(pid),
        )
        card.data = pile_id
        self.cards_row.append(card)
        self.card_pill[pile_id] = status_pill
        self.card_status[pile_id] = pill_text
        self.card_power[pile_id] = power_text
        self.card_energy[pile_id] = energy_text
        self.card_soc[pile_id] = soc_fill
        return card

    def _empty_spots_card(self) -> ft.Control:
        """空站点引导：管理员可直接在卡片里创建第一个车位。"""
        controls: list[ft.Control] = [
            theme.empty_state(
                "站点还没有车位",
                "添加后自动创建同名登录账号" if self.is_admin else "请联系管理员添加",
                icon=ft.Icons.EV_STATION,
            )
        ]
        if self.is_admin:
            controls.append(
                ft.Row(
                    [
                        ft.FilledButton(
                            "添加车位",
                            icon=ft.Icons.ADD,
                            bgcolor=theme.ACCENT,
                            color=theme.ON_ACCENT,
                            on_click=lambda e: self._open_add_dialog(),
                        )
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            )
        return theme.card(
            ft.Column(controls, spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=28,
            expand=True,
        )

    def _refresh_summary(self) -> None:
        """状态卡右侧：空闲 / 充电中 / 合计功率。"""
        if not self.summary_values:
            return
        piles = self._visible_piles()
        idle = sum(1 for p in piles if p.status == PileStatus.IDLE)
        charging = sum(1 for p in piles if p.status == PileStatus.CHARGING)
        total = sum((p.latest.power_kw if p.latest else 0.0) for p in piles)
        if "空闲" in self.summary_values:
            self.summary_values["空闲"].value = str(idle)
        if "充电中" in self.summary_values:
            self.summary_values["充电中"].value = str(charging)
        if "合计功率" in self.summary_values:
            self.summary_values["合计功率"].value = f"{total:.1f} kW"

    def _refresh_cards(self) -> None:
        for card in self.cards_row:
            try:
                pile = self.manager.pile(card.data)
            except KeyError:
                # 车位被管理员删除后，卡片墙会在 reload 时重建；此处跳过避免报错
                continue
            color = STATUS_COLORS.get(pile.status, theme.MUTED)
            status_text = self.card_status[pile.pile_id]
            status_text.value = pile.status.value
            status_text.color = color
            pill = self.card_pill.get(pile.pile_id)
            if pill is not None:
                pill.bgcolor = ft.Colors.with_opacity(0.10, color)
                pill.border = ft.Border.all(
                    1, ft.Colors.with_opacity(0.20, color)
                )

            telemetry = pile.latest
            self.card_power[pile.pile_id].value = (
                f"{telemetry.power_kw:.2f}" if telemetry else "0.00"
            )
            self.card_energy[pile.pile_id].value = (
                f"{telemetry.energy_kwh:.1f} 度 · SOC {telemetry.soc:.0f}%"
                if telemetry
                else "0.0 度 · SOC 0%"
            )
            soc_fill = self.card_soc.get(pile.pile_id)
            if soc_fill is not None:
                ratio = (telemetry.soc if telemetry else 0.0) / 100.0
                soc_fill.width = max(0.0, min(1.0, ratio)) * SOC_METER_WIDTH

            selected = pile.pile_id == self.selected_pile_id
            card.bgcolor = STATUS_BG.get(pile.status, theme.SURFACE)
            card.border = ft.Border.all(
                1.5 if selected else 1, theme.ACCENT if selected else theme.LINE
            )
