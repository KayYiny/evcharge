# -*- coding: utf-8 -*-
"""登录页：管理员 / 车位用户统一入口。

- 管理员账号：admin（默认密码 admin，可在设置页修改）
- 车位账号：用户名 = 车位名（如 A001，默认密码 admin，管理员可在设置页重置）
"""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from evcharge.presentation.components import theme


def build_login_view(
    page: ft.Page,
    branding,
    on_login: Callable[[str, str], Optional[str]],
) -> ft.Control:
    """返回登录界面控件树。

    ``on_login(username, password)`` 由组装层处理：登录成功返回 ``None``
    （此时页面已被替换为角色外壳），失败返回错误文案在本卡片内展示。
    """
    username = ft.TextField(
        label="用户名",
        hint_text="admin 或车位名",
        prefix_icon=ft.Icons.PERSON_OUTLINE,
        autofocus=True,
        width=320,
        **theme.field_style(),
    )
    password = ft.TextField(
        label="密码",
        hint_text="默认密码 admin",
        prefix_icon=ft.Icons.LOCK_OUTLINE,
        password=True,
        can_reveal_password=True,
        width=320,
        **theme.field_style(),
    )
    error = ft.Text("", size=11, color=theme.RED, visible=False)

    def submit(e=None) -> None:
        name = (username.value or "").strip()
        pwd = password.value or ""
        if not name or not pwd:
            error.value = "请输入用户名和密码"
            error.visible = True
            page.update()
            return
        error.visible = False
        page.update()
        message = on_login(name, pwd)
        if message:
            error.value = message
            error.visible = True
            try:
                page.update()
            except Exception:
                pass

    username.on_submit = submit
    password.on_submit = submit

    badge_text = ft.Text(
        branding.site_code,
        size=13,
        weight=ft.FontWeight.BOLD,
        color=branding.accent,
        font_family=theme.MONO,
    )
    badge = ft.Container(
        content=badge_text,
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=theme.RADIUS_CTRL,
        bgcolor=ft.Colors.with_opacity(0.10, branding.accent),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.22, branding.accent)),
    )

    card = theme.card(
        ft.Column(
            [
                ft.Row(
                    [
                        badge,
                        ft.Column(
                            [
                                ft.Text(
                                    branding.app_title,
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=theme.TEXT,
                                ),
                                ft.Text(
                                    branding.brand_line or branding.site_name or "充电监控与计费系统",
                                    size=11,
                                    color=theme.MUTED,
                                ),
                            ],
                            spacing=2,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(height=8, color=theme.LINE),
                username,
                password,
                error,
                ft.FilledButton(
                    "登录",
                    icon=ft.Icons.LOGIN,
                    width=320,
                    height=42,
                    bgcolor=branding.accent,
                    color=theme.ON_ACCENT,
                    on_click=submit,
                ),
                ft.Text(
                    "admin / admin　·　车位名 / admin",
                    size=10,
                    color=theme.DIM,
                ),
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=28,
        width=420,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(expand=True),
                ft.Row([card], alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(expand=True),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        expand=True,
        bgcolor=theme.BG,
        padding=24,
        alignment=ft.Alignment.CENTER,
    )
