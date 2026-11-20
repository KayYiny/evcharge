# -*- coding: utf-8 -*-
"""设置页的「数据管理」卡片（components.settings_data）。

所有数据（订单 / 车位 / 账号 / 设置与 API Key）都在同一个 SQLite 文件里，
所以这里提供三件事：

- **导出备份**：把数据库打成 zip，自带走一整个应用的数据；
- **导入备份**：选 zip/db → 二次确认 → 自动备份现有数据 → 覆盖 → 提示重启；
- **清空所有数据**：二次确认后清空业务数据与设置（含 API Key），保留默认管理员。

界面上只依赖 :class:`evcharge.application.services.MaintenanceService`。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import flet as ft

from evcharge.presentation.components import theme

logger = logging.getLogger(__name__)


class SettingsData:
    """数据管理卡片（由 ``pages.settings_page.SettingsPage`` 继承使用）。"""

    # ---------------- 卡片 ----------------
    def _data_card(self) -> ft.Container:
        self.data_summary_text = ft.Text("", size=10, color=theme.MUTED, selectable=True)
        self.data_status_text = ft.Text("", size=10, color=theme.DIM)
        self.export_data_btn = ft.OutlinedButton(
            "导出备份…",
            icon=ft.Icons.SAVE_ALT,
            on_click=lambda e: self._on_backup_export(),
        )
        self.import_data_btn = ft.OutlinedButton(
            "导入备份…",
            icon=ft.Icons.RESTORE,
            on_click=lambda e: self._on_backup_import(),
        )
        self.clear_data_btn = ft.TextButton(
            "清空所有数据…",
            icon=ft.Icons.DELETE_FOREVER,
            style=ft.ButtonStyle(color=theme.RED),
            on_click=lambda e: self._on_clear_all(),
        )
        if getattr(self, "data_file_picker", None) is None:
            self.data_file_picker = None
        self._refresh_data_summary()

        return theme.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                width=3, height=14, bgcolor=theme.RED, border_radius=2
                            ),
                            ft.Text(
                                "数据管理（备份 / 导入 / 清空）",
                                size=15,
                                weight=ft.FontWeight.BOLD,
                                color=theme.TEXT,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        "订单、车位、账号、设置与 API Key 都存在同一个数据库文件里；"
                        "备份就是这一个文件，换电脑时导入即可。",
                        size=10,
                        color=theme.MUTED,
                    ),
                    self.data_summary_text,
                    ft.Row(
                        [
                            self.export_data_btn,
                            self.import_data_btn,
                            self.clear_data_btn,
                        ],
                        spacing=8,
                    ),
                    self.data_status_text,
                ],
                spacing=12,
            ),
            padding=20,
            width=820,
        )

    # ---------------- 概况 ----------------
    def _refresh_data_summary(self) -> None:
        if getattr(self, "maintenance", None) is None:
            return
        try:
            info = self.maintenance.summary()
        except Exception:
            logger.debug("读取数据概况失败", exc_info=True)
            return
        size_mb = info["size_bytes"] / 1024 / 1024
        self.data_summary_text.value = (
            f"数据文件：{info['path']}\n"
            f"大小 {size_mb:.2f} MB · 订单 {info['orders']} 条 · 车位 {info['spots']} 个"
        )

    def _set_data_status(self, text: str, color: str = theme.DIM) -> None:
        self.data_status_text.value = text
        self.data_status_text.color = color
        self._update()

    # ---------------- 文件选择器 ----------------
    def _mount_data_picker(self, page: ft.Page) -> None:
        if getattr(self, "data_file_picker", None) is not None:
            return
        picker = ft.FilePicker()
        mounted = False
        for attr in ("services", "overlay"):
            container = getattr(page, attr, None)
            if container is None:
                continue
            try:
                container.append(picker)
                mounted = True
                break
            except Exception:
                logger.debug("数据管理 FilePicker 挂载到 page.%s 失败", attr, exc_info=True)
        if not mounted:
            raise RuntimeError("无法挂载文件选择器")
        self.data_file_picker = picker

    async def _focus_window(self) -> None:
        try:
            await self.page.window.to_front()
        except Exception:
            logger.debug("窗口置前失败", exc_info=True)

    # ---------------- 导出 ----------------
    def _on_backup_export(self) -> None:
        if self.page is not None:
            self.page.run_task(self._backup_export_async)

    async def _backup_export_async(self) -> None:
        if self.page is None or getattr(self, "maintenance", None) is None:
            return
        try:
            self._mount_data_picker(self.page)
        except Exception as exc:
            self._show_message(f"无法打开文件对话框：{exc}", theme.RED, "导出失败")
            return
        await self._focus_window()
        default_name = f"充电监控备份-{datetime.now():%Y%m%d-%H%M}.zip"
        try:
            target = await self.data_file_picker.save_file(
                dialog_title="保存数据备份",
                file_name=default_name,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["zip"],
            )
        except Exception as exc:
            self._show_message(f"打开保存对话框失败：{exc}", theme.RED, "导出失败")
            return
        if not target:
            self._set_data_status("已取消导出", theme.DIM)
            return
        if not str(target).lower().endswith(".zip"):
            target = f"{target}.zip"

        self._show_progress("导出备份", "正在打包数据库…")
        try:
            out = await _to_thread(self.maintenance.backup_to, target)
        except Exception as exc:
            logger.exception("导出备份失败")
            self._finish_progress(f"导出失败：{exc}", theme.RED, "导出失败")
            return
        self._set_data_status(f"已导出备份：{out}", theme.GREEN)
        self._finish_progress(f"备份已保存到：\n{out}", theme.GREEN, "导出成功")

    # ---------------- 导入 ----------------
    def _on_backup_import(self) -> None:
        if self.page is not None:
            self.page.run_task(self._backup_import_async)

    async def _backup_import_async(self) -> None:
        if self.page is None or getattr(self, "maintenance", None) is None:
            return
        try:
            self._mount_data_picker(self.page)
        except Exception as exc:
            self._show_message(f"无法打开文件对话框：{exc}", theme.RED, "导入失败")
            return
        await self._focus_window()
        try:
            files = await self.data_file_picker.pick_files(
                dialog_title="选择备份文件（.zip 或 .db）",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["zip", "db"],
                with_data=True,
            )
        except Exception as exc:
            self._show_message(f"打开文件对话框失败：{exc}", theme.RED, "导入失败")
            return
        if not files:
            self._set_data_status("已取消导入", theme.DIM)
            return
        path = _materialize(files[0])
        if path is None:
            self._show_message(
                "读不到所选文件内容，请重试或改用「导出」得到的 zip。",
                theme.RED,
                "导入失败",
            )
            return

        # 第一次确认
        info = self.maintenance.summary()
        self._show_confirm(
            title="确认导入备份？",
            message=(
                f"将用所选文件覆盖当前数据：\n\n"
                f"当前：订单 {info['orders']} 条、车位 {info['spots']} 个\n"
                f"来源：{path}\n\n"
                "覆盖前会自动把现有数据另存一份到 data/backups/。"
            ),
            confirm_text="继续",
            on_confirm=lambda: self._confirm_import_second(path),
            danger=True,
        )

    def _confirm_import_second(self, path: Path) -> None:
        """第二次确认：覆盖是不可逆操作，问两遍。"""
        self._show_confirm(
            title="再确认一次",
            message=(
                "导入后当前所有订单、车位、账号和设置（含 API Key）都会被替换，\n"
                "并且需要重启程序才会生效。\n\n确定继续吗？"
            ),
            confirm_text="确定覆盖",
            on_confirm=lambda: self._do_import(path),
            danger=True,
        )

    def _do_import(self, path: Path) -> None:
        self._set_data_status("正在导入…", theme.MUTED)
        try:
            backup = self.maintenance.restore_from(path)
        except Exception as exc:
            logger.exception("导入备份失败")
            self._set_data_status(f"导入失败：{exc}", theme.RED)
            self._show_message(f"导入失败：{exc}", theme.RED, "导入失败")
            return
        extra = f"\n覆盖前的数据已备份到：\n{backup}" if str(backup) else ""
        self._set_data_status("导入完成，请重启程序", theme.GREEN)
        self._show_message(
            "导入完成。请关闭并重新打开程序以生效。" + extra,
            theme.GREEN,
            "导入成功",
        )

    # ---------------- 清空 ----------------
    def _on_clear_all(self) -> None:
        if getattr(self, "maintenance", None) is None:
            return
        info = self.maintenance.summary()
        self._show_confirm(
            title="清空所有数据？",
            message=(
                f"将删除：订单 {info['orders']} 条、车位 {info['spots']} 个、"
                "全部账号、以及所有设置和 API Key。\n\n"
                "管理员账号会恢复为默认（admin / admin）。\n"
                "清理后本机的 API Key 也会一并删除。\n\n确定继续吗？"
            ),
            confirm_text="继续",
            on_confirm=self._confirm_clear_second,
            danger=True,
        )

    def _confirm_clear_second(self) -> None:
        self._show_confirm(
            title="再确认一次",
            message="清空后无法恢复（除非你之前导出过备份）。确定清空全部数据吗？",
            confirm_text="确定清空",
            on_confirm=self._do_clear_all,
            danger=True,
        )

    def _do_clear_all(self) -> None:
        try:
            self.maintenance.clear_all()
        except Exception as exc:
            logger.exception("清空数据失败")
            self._show_message(f"清空失败：{exc}", theme.RED, "清空失败")
            return
        self._refresh_data_summary()
        self._set_data_status("已清空，请重启程序", theme.GREEN)
        self._show_message(
            "所有数据与设置已清空（管理员恢复为 admin / admin）。\n请重启程序。",
            theme.GREEN,
            "已清空",
        )

    # ---------------- 确认对话框 ----------------
    def _show_confirm(
        self,
        title: str,
        message: str,
        confirm_text: str,
        on_confirm,
        danger: bool = False,
    ) -> None:
        if self.page is None:
            return
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Text(
                title, size=15, weight=ft.FontWeight.BOLD, color=theme.TEXT
            ),
            content=ft.Container(
                content=ft.Text(
                    message, size=12, color=theme.TEXT, selectable=True
                ),
                width=520,
            ),
            actions=[
                ft.TextButton(
                    "取消",
                    on_click=lambda e: self._close_result_dialog(),
                ),
                ft.FilledButton(
                    confirm_text,
                    bgcolor=theme.RED if danger else theme.ACCENT,
                    color=theme.ON_ACCENT,
                    on_click=lambda e: (
                        self._close_result_dialog(),
                        on_confirm(),
                    ),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)


def _materialize(picked) -> Optional[Path]:
    """把 FilePicker 的选择结果变成可读路径（原生平台可能只给 bytes）。"""
    raw = getattr(picked, "path", None)
    if isinstance(raw, (str, Path)) and str(raw).strip():
        candidate = Path(raw)
        if candidate.exists():
            return candidate
    data = getattr(picked, "bytes", None)
    if isinstance(data, (bytes, bytearray)) and data:
        import tempfile

        name = getattr(picked, "name", "") or "backup.zip"
        suffix = Path(name).suffix or ".zip"
        target = Path(tempfile.gettempdir()) / f"evcharge_restore{suffix}"
        try:
            target.write_bytes(data)
        except OSError:
            return None
        return target
    return None


async def _to_thread(func, *args):
    """轻量包装：避免本模块直接依赖 asyncio 的写法散落各处。"""
    import asyncio

    return await asyncio.to_thread(func, *args)


__all__ = ["SettingsData"]
