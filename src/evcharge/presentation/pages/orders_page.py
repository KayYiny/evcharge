# -*- coding: utf-8 -*-
"""订单管理页（evcharge.presentation.pages.orders_page）。

订单查询与运营台账：
- 组合筛选：关键词、车位、充电类型、车型、状态、时间范围/自定义日期；
  车位 / 充电类型 / 车型三个下拉的候选值都从数据库与当前车位配置动态生成，
  管理员新增（如 A1、7 号）或删除车位后会自动出现在筛选项里；
- 排序、分页、每页条数、筛选结果统计、导出筛选结果；
- 点击任意订单行查看完整账单；
- 视觉：浅色卡片、SOC 迷你进度条、状态/类型标签、等宽数字列。
"""

from __future__ import annotations

import asyncio
import math
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import flet as ft

from evcharge.application.services import OrderService
from evcharge.application.session_manager import SessionManager
from evcharge.presentation.components import theme
from evcharge.presentation.components.orders_filters import OrdersFilters
from evcharge.presentation.components.orders_table import OrdersTable

# 筛选项常量与排序键在 filters mixin 里（唯一来源）
from evcharge.presentation.components.orders_filters import _ALL  # noqa: E402

class OrdersPage(OrdersFilters, OrdersTable):
    """订单查询、筛选、分页与账单导出页面。"""

    def __init__(
        self,
        manager: SessionManager,
        orders: OrderService,
        export_path: str | Path | None = None,
        spot_uid: str | None = None,
        maintenance=None,
    ) -> None:
        self.manager = manager
        # 数据维护服务（CSV 导入）；车位用户不提供时为 None
        self.maintenance = maintenance
        self.import_picker: ft.FilePicker | None = None
        self.orders_service = orders
        # 车位用户只能看自己的订单：按车位 uid 过滤（uid 才是订单的真实归属）
        self.spot_uid = spot_uid
        self._spot_labels: dict[str, str] = {}
        self.page: ft.Page | None = None
        self.export_path = (
            Path(export_path)
            if export_path
            else self.orders_service.default_export_path
        )

        self.orders_all: List[dict] = []
        self.filtered: List[dict] = []
        self.page_size = 20
        self.page_index = 1
        self._date_warning = ""

        self._build_filters()
        self._build_summary()
        self._build_table()

    def build(self, page: ft.Page) -> ft.Control:
        self.page = page
        self.eyebrow = theme.eyebrow_label("")
        self._fill()
        header = theme.page_header(
            self.eyebrow,
            "订单管理",
            "组合筛选与导出。",
            trailing=ft.TextButton(
                "刷新数据",
                icon=ft.Icons.REFRESH,
                on_click=lambda e: self._reload(),
            ),
        )
        return ft.Column(
            [header, self.filter_card, self.summary_row, self.table_card],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    # ==================================================================
    # 筛选逻辑
    # ==================================================================
    @staticmethod
    def _parse_date(value: str) -> Optional[date]:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _order_date(order: dict) -> Optional[date]:
        try:
            return datetime.fromisoformat(str(order.get("start_time"))).date()
        except (TypeError, ValueError):
            return None

    def _filtered_orders(self) -> List[dict]:
        self._date_warning = ""
        start_text = (self.start_date.value or "").strip()
        end_text = (self.end_date.value or "").strip()
        start_dt = self._parse_date(start_text) if start_text else None
        end_dt = self._parse_date(end_text) if end_text else None
        if start_text and start_dt is None:
            self._date_warning = "开始日期格式应为 YYYY-MM-DD"
        if end_text and end_dt is None:
            self._date_warning = "结束日期格式应为 YYYY-MM-DD"
        custom_range = bool(start_text or end_text)

        query = (self.search.value or "").strip().lower()
        pile = self.filter_pile.value or _ALL
        charger = self.filter_type.value or _ALL
        category = self.filter_category.value or _ALL
        preset = self.filter_range.value or "全部时间"
        today = date.today()

        result: List[dict] = []
        for order in self.orders_all:
            if query and query not in order.get("_hay", ""):
                continue
            if pile != _ALL and str(order.get("pile_id")) != pile:
                continue
            if charger != _ALL and order.get("charger_type") != charger:
                continue
            if category != _ALL and order.get("vehicle_category") != category:
                continue
            order_day = order.get("_day")
            if order_day is None:
                continue
            if custom_range:
                if start_dt is not None and order_day < start_dt:
                    continue
                if end_dt is not None and order_day > end_dt:
                    continue
            else:
                if preset == "今天" and order_day != today:
                    continue
                if preset == "近7天" and order_day < today - timedelta(days=6):
                    continue
                if preset == "近30天" and order_day < today - timedelta(days=29):
                    continue
                if preset == "本月" and (
                    order_day.year != today.year or order_day.month != today.month
                ):
                    continue
            result.append(order)

        sort_key = self.sort.value or "时间最新"
        if sort_key == "时间最早":
            result.sort(key=lambda o: str(o.get("start_time", "")))
        elif sort_key == "金额最高":
            result.sort(key=lambda o: float(o.get("total_fee") or 0), reverse=True)
        elif sort_key == "电量最高":
            result.sort(key=lambda o: float(o.get("energy_kwh") or 0), reverse=True)
        elif sort_key == "时长最长":
            result.sort(key=lambda o: float(o.get("duration_min") or 0), reverse=True)
        else:
            result.sort(key=lambda o: str(o.get("start_time", "")), reverse=True)
        return result

    def _fill(self, update: bool = False, reload: bool = True) -> None:
        if reload:
            self.orders_all = self.orders_service.list_orders(spot_uid=self.spot_uid)
            # 预计算日期与搜索串：筛选/翻页只走内存，不再逐次查库和解析时间
            self._sync_filter_options()
            self._spot_labels = dict(self._spot_choices())
            for order in self.orders_all:
                order["_day"] = self._order_date(order)
                order["_hay"] = " ".join(
                    str(order.get(key, ""))
                    for key in (
                        "plate",
                        "spot_name",
                        "vehicle_model",
                        "vehicle_category",
                        "order_id",
                    )
                ).lower()
        self.filtered = self._filtered_orders()
        total = len(self.filtered)
        pages = max(1, math.ceil(total / self.page_size))
        self.page_index = min(max(1, self.page_index), pages)

        offset = (self.page_index - 1) * self.page_size
        page_orders = self.filtered[offset : offset + self.page_size]

        # 汇总
        total_energy = sum(float(o.get("energy_kwh") or 0) for o in self.filtered)
        total_fee = sum(float(o.get("total_fee") or 0) for o in self.filtered)
        avg_price = total_fee / total_energy if total_energy else 0.0
        self.count_value.value = str(total)
        self.count_sub.value = f"全部 {len(self.orders_all)} 条"
        self.energy_value.value = f"{total_energy:.1f}"
        self.fee_value.value = f"¥{total_fee:.2f}"
        self.price_value.value = f"{avg_price:.3f}"

        # 表格
        self.rows_col.controls.clear()
        if not page_orders:
            self.rows_col.controls.append(self._empty_state())
        else:
            for index, order in enumerate(page_orders):
                self.rows_col.controls.append(self._order_row(order, index))

        # 分页/说明
        self.page_info.value = f"筛选结果 {total} 条 · 全部 {len(self.orders_all)} 条"
        self.page_label.value = f"{self.page_index} / {pages}"
        self.table_note.value = f"第 {self.page_index} / {pages} 页"
        self.prev_btn.disabled = self.page_index <= 1
        self.next_btn.disabled = self.page_index >= pages

        self.filter_note.value = self._filter_description()
        self.filter_note.color = theme.AMBER if self._date_warning else theme.DIM

        if update and self.page is not None:
            self.page.update()

    def _reload(self) -> None:
        self._fill(update=True)
        self.status_text.value = f"已刷新 · {len(self.orders_all)} 条订单"
        self.status_text.color = theme.MUTED
        if self.page is not None:
            self.page.update()

    # ---------------- CSV 导入（管理员） ----------------
    def _mount_import_picker(self, page: ft.Page) -> None:
        """挂载文件选择器（与车牌识别同样的 services / overlay 双保险）。"""
        if self.import_picker is not None:
            return
        picker = ft.FilePicker()
        for attr in ("services", "overlay"):
            container = getattr(page, attr, None)
            if container is None:
                continue
            try:
                container.append(picker)
                self.import_picker = picker
                return
            except Exception:
                continue
        raise RuntimeError("无法挂载文件选择器")

    def _on_import_csv(self) -> None:
        if self.page is not None and self.maintenance is not None:
            self.page.run_task(self._import_csv_async)

    async def _import_csv_async(self) -> None:
        """选一个订单 CSV 追加进库（不覆盖已有订单）。"""
        if self.page is None or self.maintenance is None:
            return
        if self.import_picker is None:
            try:
                self._mount_import_picker(self.page)
            except Exception as exc:
                self._show_result("无法打开文件对话框", str(exc), theme.RED)
                return
        try:
            await self.page.window.to_front()
        except Exception:
            pass

        try:
            files = await self.import_picker.pick_files(
                dialog_title="选择订单 CSV（导出得到的文件即可）",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["csv"],
                with_data=True,
            )
        except Exception as exc:
            self._show_result("打开文件对话框失败", str(exc), theme.RED)
            return
        if not files:
            self.status_text.value = "已取消导入"
            self.status_text.color = theme.DIM
            self.page.update()
            return

        path = self._materialize_csv(files[0])
        if path is None:
            self._show_result(
                "读不到所选文件内容", "请重新导出 CSV 后再试。", theme.RED
            )
            return

        try:
            imported, skipped = await asyncio.to_thread(
                self.maintenance.import_csv, path
            )
        except Exception as exc:
            self._show_result("导入失败", str(exc), theme.RED)
            return

        self._fill(update=True)
        self.status_text.value = f"✅ 导入 {imported} 条，跳过 {skipped} 条（重复或缺字段）"
        self.status_text.color = theme.GREEN if imported else theme.AMBER
        self._show_result(
            "导入完成",
            f"新增 {imported} 条订单，跳过 {skipped} 条"
            "（车牌/单号/开始时间重复，或缺少必要字段）。\n"
            "已有订单不会被覆盖。",
            theme.GREEN if imported else theme.AMBER,
        )

    @staticmethod
    def _materialize_csv(picked):
        """把选择结果变成可读路径（原生平台可能只给 bytes）。"""
        raw = getattr(picked, "path", None)
        if isinstance(raw, (str, Path)) and str(raw).strip():
            candidate = Path(raw)
            if candidate.exists():
                return candidate
        data = getattr(picked, "bytes", None)
        if isinstance(data, (bytes, bytearray)) and data:
            target = Path(tempfile.gettempdir()) / "evcharge_import.csv"
            try:
                target.write_bytes(data)
            except OSError:
                return None
            return target
        return None

    def _show_result(self, title: str, message: str, color: str = theme.GREEN) -> None:
        """结果弹窗（和设置页保持一致的反馈方式）。"""
        if self.page is None:
            return
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Text(
                title, size=15, weight=ft.FontWeight.BOLD, color=theme.TEXT
            ),
            content=ft.Container(
                content=ft.Text(message, size=12, color=theme.TEXT, selectable=True),
                width=480,
            ),
            actions=[
                ft.FilledButton(
                    "知道了",
                    bgcolor=theme.ACCENT,
                    color=theme.ON_ACCENT,
                    on_click=lambda e: self.page.pop_dialog(),
                )
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def _export(self) -> None:
        if not self.filtered:
            self.status_text.value = "⚠ 当前筛选没有可导出的订单"
            self.status_text.color = theme.AMBER
        else:
            try:
                out = self.orders_service.export_csv(self.filtered, self.export_path)
                self.status_text.value = f"✅ 已导出 {len(self.filtered)} 条 → {out.name}"
                self.status_text.color = theme.GREEN
            except OSError as exc:
                self.status_text.value = f"❌ 导出失败：{exc}"
                self.status_text.color = theme.RED
        if self.page is not None:
            self.page.update()

    def apply_branding(self, branding) -> None:
        """设置保存后更新页面眉标与强调色。"""
        if not hasattr(self, "eyebrow"):
            return
        self.eyebrow.value = branding.eyebrow("运营台账")
        self.eyebrow.color = branding.accent

    def refresh(self) -> None:
        """由主界面切换到订单页时调用。"""
        self._fill()
