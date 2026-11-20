# -*- coding: utf-8 -*-
"""设置页的结果反馈（弹窗 / 进度 / 关闭）。

所有操作结果都通过弹窗展示，避免用户在长页面里滚到底部才看到反馈。
"""

from __future__ import annotations

import flet as ft

from evcharge.presentation.components import theme

class SettingsFeedback:
    # ---------------- 结果反馈（弹窗） ----------------
    def _show_message(
        self,
        message: str,
        color: str = theme.MUTED,
        title: str = "操作结果",
    ) -> None:
        """把操作结果显示成弹窗。

        设置页较长，结果如果只写在底部状态文本里，用户点完按钮往往看不到。
        """
        self.status.value = message
        self.status.color = color
        self._update()
        if self.page is None:
            return
        if color == theme.GREEN:
            icon = "✅"
        elif color == theme.RED:
            icon = "❌"
        elif color == theme.AMBER:
            icon = "⚠"
        else:
            icon = "ℹ"
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Row(
                [
                    ft.Text(icon, size=16),
                    ft.Text(
                        title,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=theme.TEXT,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Text(
                    message, size=12, color=theme.TEXT, selectable=True
                ),
                width=460,
            ),
            actions=[
                ft.FilledButton(
                    "知道了",
                    bgcolor=theme.ACCENT,
                    color=theme.ON_ACCENT,
                    on_click=lambda e: self._close_result_dialog(),
                )
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def _show_progress(self, title: str, message: str) -> None:
        """打开一个「进行中」弹窗，任务结束后用 _finish_progress 替换成结果。"""
        self.status.value = message
        self.status.color = theme.MUTED
        self._update()
        if self.page is None:
            return
        self._progress_text = ft.Text(message, size=12, color=theme.MUTED)
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Text(
                title, size=15, weight=ft.FontWeight.BOLD, color=theme.TEXT
            ),
            content=ft.Container(
                content=ft.Row(
                    [
                        ft.ProgressRing(width=16, height=16, stroke_width=2),
                        self._progress_text,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                width=460,
            ),
            actions=[],
        )
        self.page.show_dialog(dialog)

    def _finish_progress(self, message: str, color: str, title: str) -> None:
        """关掉进度弹窗，弹出最终结果。"""
        self._close_result_dialog()
        self._show_message(message, color, title)

    def _close_result_dialog(self) -> None:
        """关闭当前结果弹窗（与页面里车位对话框的 _close_dialog 区分）。"""
        if self.page is None:
            return
        try:
            self.page.pop_dialog()
        except Exception:
            pass

    def _update(self) -> None:
        if self.page is not None:
            try:
                self.page.update()
            except Exception:
                pass
