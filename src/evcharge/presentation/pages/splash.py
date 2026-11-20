# -*- coding: utf-8 -*-
"""启动画面（presentation.pages.splash）。

打包成 .app / .exe 后，首次导入 pandas、matplotlib 等依赖需要几秒，
这段时间窗口还没内容，用户会以为「点了没反应」。这里先显示一个轻量启动页，
等真正界面构建完成后再由 ``build_app`` 覆盖掉。

启动页只依赖 flet 与主题色，不 import 任何重模块，保证能立刻画出来。
"""

from __future__ import annotations

from typing import Optional

import flet as ft

from evcharge.presentation.components import theme

DEFAULT_MESSAGE = "正在启动，请稍候…"


def build_splash_view(
    message: str = DEFAULT_MESSAGE,
    title: str = "",
    subtitle: str = "",
) -> ft.Control:
    """构造启动画面：图标占位 + 标题 + 进度圈 + 提示语。"""
    badge = ft.Container(
        content=ft.Icon(ft.Icons.EV_STATION, size=34, color=theme.ON_ACCENT),
        width=68,
        height=68,
        border_radius=18,
        bgcolor=theme.ACCENT,
        alignment=ft.Alignment.CENTER,
    )
    return ft.Container(
        content=ft.Column(
            [
                badge,
                ft.Text(
                    title or "充电监控与计费系统",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=theme.TEXT,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    subtitle or "国网高速服务区",
                    size=11,
                    color=theme.MUTED,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=8),
                ft.ProgressRing(width=22, height=22, stroke_width=2.5),
                ft.Text(message, size=12, color=theme.MUTED),
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
        bgcolor=theme.BG,
    )


def build_boot_error_view(message: str, log_path: Optional[str] = None) -> ft.Control:
    """启动失败时的兜底界面（打包后没有控制台，必须显示在窗口里）。"""
    rows: list[ft.Control] = [
        ft.Icon(ft.Icons.ERROR_OUTLINE, size=32, color=theme.RED),
        ft.Text(
            "启动失败",
            size=17,
            weight=ft.FontWeight.BOLD,
            color=theme.TEXT,
        ),
        ft.Container(
            content=ft.Text(
                message, size=12, color=theme.TEXT, selectable=True
            ),
            width=560,
            padding=12,
            border_radius=8,
            bgcolor=theme.SURFACE_2,
        ),
    ]
    if log_path:
        rows.append(
            ft.Text(f"详细日志：{log_path}", size=10, color=theme.MUTED, selectable=True)
        )
    rows.append(
        ft.Text(
            "可把上面的信息发给开发者；关闭窗口后重试一次通常可恢复。",
            size=10,
            color=theme.DIM,
        )
    )
    return ft.Container(
        content=ft.Column(
            rows,
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
        bgcolor=theme.BG,
        padding=24,
    )


__all__ = ["build_splash_view", "build_boot_error_view", "DEFAULT_MESSAGE"]
