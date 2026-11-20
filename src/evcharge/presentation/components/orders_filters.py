# -*- coding: utf-8 -*-
"""订单页的筛选区（筛选卡 + 下拉候选 + 结果说明）。

车位 / 充电类型 / 车型的候选值全部来自数据，不写死；
筛选与分页只在内存里跑，切条件不会重新查库。
"""

from __future__ import annotations

from typing import List

import flet as ft

from evcharge.presentation.components import theme

_RANGE_OPTIONS = ["全部时间", "今天", "近7天", "近30天", "本月"]
_SORT_OPTIONS = ["时间最新", "时间最早", "金额最高", "电量最高", "时长最长"]
_ALL = "全部"


def _spot_sort_key(spot_name: str) -> tuple:
    """车位名排序：纯数字按数值（1,2,10），其余（A001、B003…）按字典序排在后面。"""
    text = str(spot_name)
    if text.isdigit():
        return (0, int(text), "", text)
    return (1, 0, text, text)


class OrdersFilters:
    # ==================================================================
    # 构建
    # ==================================================================
    def _build_filters(self) -> None:
        field = theme.field_style
        self.search = ft.TextField(
            label="搜索",
            hint_text="车牌 / 车型 / 单号",
            prefix_icon=ft.Icons.SEARCH,
            width=236,
            on_change=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.filter_pile = ft.Dropdown(
            label="车位",
            value=_ALL,
            options=self._pile_options(),
            width=124,
            on_select=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.filter_type = ft.Dropdown(
            label="充电类型",
            value=_ALL,
            options=self._charger_type_options(),
            width=132,
            on_select=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.filter_category = ft.Dropdown(
            label="车型",
            value=_ALL,
            options=self._category_options(),
            width=108,
            on_select=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.filter_range = ft.Dropdown(
            label="时间范围",
            value="全部时间",
            options=[ft.dropdown.Option(k, k) for k in _RANGE_OPTIONS],
            width=124,
            on_select=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.sort = ft.Dropdown(
            label="排序",
            value="时间最新",
            options=[ft.dropdown.Option(k, k) for k in _SORT_OPTIONS],
            width=128,
            on_select=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.start_date = ft.TextField(
            label="开始日期",
            hint_text="YYYY-MM-DD",
            width=134,
            on_change=lambda e: self._on_filters_changed(),
            **field(),
        )
        self.end_date = ft.TextField(
            label="结束日期",
            hint_text="YYYY-MM-DD",
            width=134,
            on_change=lambda e: self._on_filters_changed(),
            **field(),
        )

        self.reset_btn = ft.TextButton(
            "重置",
            icon=ft.Icons.RESTART_ALT,
            on_click=lambda e: self._reset_filters(),
        )
        self.export_btn = ft.FilledButton(
            "导出筛选结果",
            icon=ft.Icons.DOWNLOAD,
            bgcolor=theme.AMBER,
            color=theme.ON_ACCENT,
            on_click=lambda e: self._export(),
        )
        # 导入只对管理员开放（车位用户没有维护服务）
        self.import_btn = ft.OutlinedButton(
            "导入 CSV…",
            icon=ft.Icons.UPLOAD_FILE,
            on_click=lambda e: self._on_import_csv(),
        ) if getattr(self, "maintenance", None) is not None else None
        self.filter_note = ft.Text("", size=10, color=theme.DIM)
        self.status_text = ft.Text("", size=10, color=theme.DIM)

        filter_row = ft.Row(
            [
                self.search,
                self.filter_pile,
                self.filter_type,
                self.filter_category,
                self.filter_range,
                self.start_date,
                self.end_date,
                self.sort,
            ],
            wrap=True,
            spacing=10,
            run_spacing=10,
        )
        title_row = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(
                            "筛选与检索",
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color=theme.TEXT,
                        ),
                        ft.Text(
                            "筛选实时生效，点击行查看账单",
                            size=10,
                            color=theme.MUTED,
                        ),
                    ],
                    spacing=2,
                ),
                ft.Container(expand=True),
                self.reset_btn,
                *([self.import_btn] if self.import_btn is not None else []),
                self.export_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        note_row = ft.Row(
            [self.filter_note, ft.Container(expand=True), self.status_text],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.filter_card = theme.card(
            ft.Column([title_row, filter_row, note_row], spacing=12),
            padding=18,
        )

    def _build_summary(self) -> None:
        self.count_card, self.count_value, self.count_sub = self._stat(
            "筛选订单", "单", theme.AMBER, "全部 0 条"
        )
        self.energy_card, self.energy_value, self.energy_sub = self._stat(
            "累计电量", "度", theme.CYAN, "当前筛选结果"
        )
        self.fee_card, self.fee_value, self.fee_sub = self._stat(
            "累计费用", "", theme.GREEN, "电费 + 服务费"
        )
        self.price_card, self.price_value, self.price_sub = self._stat(
            "平均综合单价", "元/度", theme.PURPLE, "总费用 / 总电量"
        )
        self.summary_row = ft.Row(
            [self.count_card, self.energy_card, self.fee_card, self.price_card],
            spacing=10,
        )

    def _stat(
        self, label: str, unit: str, accent: str, sub_text: str
    ) -> tuple[ft.Container, ft.Text, ft.Text]:
        value = ft.Text(
            "0",
            size=21,
            weight=ft.FontWeight.BOLD,
            color=theme.TEXT,
            font_family=theme.MONO,
        )
        sub = ft.Text(sub_text, size=9, color=theme.DIM)
        card = theme.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(width=3, height=12, bgcolor=accent, border_radius=2),
                            ft.Text(label, size=10, color=theme.MUTED),
                        ],
                        spacing=7,
                    ),
                    ft.Row(
                        [
                            value,
                            ft.Text(unit, size=9, color=theme.MUTED),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    sub,
                ],
                spacing=7,
            ),
            padding=14,
            expand=True,
        )
        return card, value, sub

    def _filter_description(self) -> str:
        parts: List[str] = []
        query = (self.search.value or "").strip()
        if query:
            parts.append(f"关键词「{query}」")
        if self.filter_pile.value and self.filter_pile.value != _ALL:
            parts.append(
                f"车位 {self._spot_labels.get(self.filter_pile.value, self.filter_pile.value)}"
            )
        if self.filter_type.value and self.filter_type.value != _ALL:
            parts.append(str(self.filter_type.value))
        if self.filter_category.value and self.filter_category.value != _ALL:
            parts.append(str(self.filter_category.value))
        start_text = (self.start_date.value or "").strip()
        end_text = (self.end_date.value or "").strip()
        if start_text or end_text:
            parts.append(f"{start_text or '不限'} ~ {end_text or '不限'}")
        elif self.filter_range.value and self.filter_range.value != "全部时间":
            parts.append(str(self.filter_range.value))
        if self.sort.value and self.sort.value != "时间最新":
            parts.append(f"排序：{self.sort.value}")
        if self._date_warning:
            return f"⚠ {self._date_warning} · " + (" · ".join(parts) if parts else "全部订单")
        return " · ".join(parts) if parts else "未设置筛选 · 显示全部订单"

    # ==================================================================
    # 数据填充
    # ==================================================================
    # ------------------------------------------------------------------
    # 筛选选项（全部动态生成，避免把车位/类型/车型写死）
    # ------------------------------------------------------------------
    def _spot_choices(self) -> List[tuple[str, str]]:
        """可选车位：``(值 = uid, 标签 = 车位名)``。

        - 内存车位 / 数据库车位：标签是车位名（如 ``A001``）；
        - 只存在于历史订单里的车位（已删除）：标签加「（已删除）」后缀，
          这类车位仍可筛选历史订单，但 uid 与新同名车位互不相干。
        """
        live: dict[str, str] = {}
        try:
            live = dict(self.orders_service.spot_map())
        except Exception:  # 仓储异常时不影响已有选项
            pass
        for pile in self.manager.piles:
            live[pile.pile_id] = pile.name

        if self.spot_uid:
            uid = str(self.spot_uid)
            return [(uid, live.get(uid, uid))]

        choices = list(live.items())
        seen = set(live)
        for order in self.orders_all:
            pile_id = order.get("pile_id")
            if pile_id is None:
                continue
            key = str(pile_id)
            if key in seen:
                continue
            seen.add(key)
            label = str(order.get("spot_name") or key)
            choices.append((key, f"{label}（已删除）"))
        choices.sort(key=lambda item: _spot_sort_key(item[1].replace("（已删除）", "")))
        return choices

    def _pile_options(self) -> List[ft.dropdown.Option]:
        options = [ft.dropdown.Option(_ALL, "全部车位")]
        options += [
            ft.dropdown.Option(uid, f"车位 {label}")
            for uid, label in self._spot_choices()
        ]
        return options

    def _charger_types(self) -> List[str]:
        """充电类型候选：来自当前车位配置与订单数据，按固定顺序排列。"""
        found = {
            str(p.charger.charger_type.value) for p in self.manager.piles
        }
        found |= {
            str(order.get("charger_type"))
            for order in self.orders_all
            if order.get("charger_type")
        }
        order = ["直流快充", "交流慢充"]
        return [name for name in order if name in found] + sorted(
            found - set(order)
        )

    def _charger_type_options(self) -> List[ft.dropdown.Option]:
        options = [ft.dropdown.Option(_ALL, "全部类型")]
        options += [ft.dropdown.Option(name, name) for name in self._charger_types()]
        return options

    def _categories(self) -> List[str]:
        """车型候选：订单里出现过的车型 ∪ 车型库分类。"""
        from evcharge.domain.vehicle import MODEL_CATALOG

        found = {
            str(order.get("vehicle_category"))
            for order in self.orders_all
            if order.get("vehicle_category")
        }
        found |= {spec.category for spec in MODEL_CATALOG}
        return sorted(found)

    def _category_options(self) -> List[ft.dropdown.Option]:
        options = [ft.dropdown.Option(_ALL, "全部车型")]
        options += [ft.dropdown.Option(name, name) for name in self._categories()]
        return options

    @staticmethod
    def _keep_selection(dropdown: ft.Dropdown, valid: set[str]) -> None:
        """选项刷新后尽量保留用户当前选择，失效则回到「全部」。"""
        current = dropdown.value
        dropdown.value = current if current in valid else _ALL

    def _sync_filter_options(self) -> None:
        """车位/类型/车型有增删时刷新下拉选项（保留当前选择）。"""
        pile_options = self._pile_options()
        self.filter_pile.options = pile_options
        self._keep_selection(
            self.filter_pile, {opt.key for opt in pile_options}
        )

        type_options = self._charger_type_options()
        self.filter_type.options = type_options
        self._keep_selection(
            self.filter_type, {opt.key for opt in type_options}
        )

        category_options = self._category_options()
        self.filter_category.options = category_options
        self._keep_selection(
            self.filter_category, {opt.key for opt in category_options}
        )

    # ==================================================================
    # 交互
    # ==================================================================
    def _on_filters_changed(self) -> None:
        self.page_index = 1
        self.status_text.value = ""
        self._fill(update=True, reload=False)

    def _reset_filters(self) -> None:
        self.search.value = ""
        self._sync_filter_options()
        self.filter_pile.value = _ALL
        self.filter_type.value = _ALL
        self.filter_category.value = _ALL
        self.filter_range.value = "全部时间"
        self.sort.value = "时间最新"
        self.start_date.value = ""
        self.end_date.value = ""
        self.page_index = 1
        self.status_text.value = "筛选条件已重置"
        self.status_text.color = theme.CYAN
        self._fill(update=True, reload=False)
