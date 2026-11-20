# -*- coding: utf-8 -*-
"""界面设计令牌与通用组件（evcharge.presentation.components.theme）。

视觉方向：扁平克制的 Material 风格。
- 页面背景浅灰（BG），卡片纯白（SURFACE）+ 1px 细边框，几乎不用阴影；
- 主强调色为 Material 蓝（ACCENT；AMBER 是兼容旧代码的别名）；
- 遥测数字用青绿（CYAN），状态色低饱和；圆角统一 8/12，留白克制。

各页面统一引用这里的 token，避免色值与间距散落。
"""

from __future__ import annotations

import flet as ft

# ---------------- 颜色 ----------------
BG = "#F5F6F8"
SURFACE = "#FFFFFF"
RAISED = "#FFFFFF"
SURFACE_2 = "#F2F4F7"
SURFACE_3 = "#FAFBFC"
INSET = "#F1F3F6"
LINE = "#E2E6EC"
LINE_SOFT = "#EDF0F4"
TEXT = "#1A1F26"
MUTED = "#5B6672"
DIM = "#8B95A1"
ACCENT = "#1F6FEB"  # 主强调色（Material 蓝）
AMBER = ACCENT  # 兼容旧代码里的 AMBER 名称，指向主强调色
CYAN = "#0E7490"
GREEN = "#157F3C"
RED = "#C43D3D"
PURPLE = "#5B54C7"
ON_ACCENT = "#FFFFFF"
MONO = "monospace"

PERIOD_COLORS = {"峰段": RED, "平段": CYAN, "谷段": GREEN}
SHADOW_DARK = "#0B1220"
SHADOW_LIGHT = "#FFFFFF"

# ---------------- 形状 ----------------
RADIUS_CARD = 12
RADIUS_CTRL = 8
RADIUS_PILL = 999


# ---------------- 阴影（扁平风格只保留极淡的一层） ----------------
def soft_shadow(
    blur: int = 10,
    offset: int = 2,
    strength: float = 1.0,
) -> list[ft.BoxShadow]:
    """扁平卡片阴影：单层、极淡、向下偏移。"""
    return [
        ft.BoxShadow(
            blur_radius=blur,
            color=ft.Colors.with_opacity(0.06 * strength, SHADOW_DARK),
            offset=ft.Offset(0, offset),
        )
    ]


def header_shadow() -> list[ft.BoxShadow]:
    return [
        ft.BoxShadow(
            blur_radius=10,
            color=ft.Colors.with_opacity(0.05, SHADOW_DARK),
            offset=ft.Offset(0, 2),
        )
    ]


def nav_shadow() -> list[ft.BoxShadow]:
    return [
        ft.BoxShadow(
            blur_radius=12,
            color=ft.Colors.with_opacity(0.06, SHADOW_DARK),
            offset=ft.Offset(2, 0),
        )
    ]


# ---------------- 基础容器 ----------------
def card(
    content,
    padding=18,
    bgcolor=SURFACE,
    radius=RADIUS_CARD,
    expand=None,
    shadow=False,
    bordered=True,
    **kwargs,
) -> ft.Container:
    """扁平卡片：白底 + 1px 细边框，阴影默认关闭。

    ``expand`` 只在需要拉伸时才传给 Container：Flet 1.x 中只要显式传入
    ``expand``（哪怕是 ``False``）该控件就会被包进 ``Expanded``，
    而 ``Expanded`` 不能作为 ``Row(wrap=True)`` 的子控件——那会让整块
    卡片墙渲染成灰色空块。因此这里默认不传，只有真需要拉伸时才传。
    """
    if shadow:
        kwargs.setdefault("shadow", soft_shadow())
    kwargs.setdefault("border", ft.Border.all(1, LINE) if bordered else None)
    if expand:
        kwargs.setdefault("expand", expand)
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=bgcolor,
        border_radius=radius,
        **kwargs,
    )


def hairline(color=LINE_SOFT) -> ft.Container:
    return ft.Container(height=1, bgcolor=color)


def divider(vertical: int = 12) -> ft.Container:
    return ft.Container(
        content=ft.Container(height=1, bgcolor=LINE),
        padding=ft.Padding.symmetric(vertical=vertical),
    )


def badge(text: str, color: str, filled: bool = False) -> ft.Container:
    """低饱和状态标签（胶囊）。"""
    return ft.Container(
        content=ft.Text(
            text,
            size=10,
            weight=ft.FontWeight.W_500,
            color=ON_ACCENT if filled else color,
            font_family=MONO if text.isascii() else None,
        ),
        padding=ft.Padding.symmetric(horizontal=9, vertical=4),
        border_radius=RADIUS_PILL,
        bgcolor=color if filled else ft.Colors.with_opacity(0.08, color),
        border=ft.Border.all(0 if filled else 1, ft.Colors.with_opacity(0.16, color)),
    )


def chip(text: str, color: str = ACCENT, icon: str | None = None) -> ft.Container:
    """信息胶囊：用于电价档、时段、运行状态等轻量标识。"""
    controls: list[ft.Control] = []
    if icon:
        controls.append(ft.Icon(icon, size=13, color=color))
    controls.append(ft.Text(text, size=10, weight=ft.FontWeight.W_500, color=color))
    return ft.Container(
        content=ft.Row(controls, spacing=5, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        border_radius=RADIUS_PILL,
        bgcolor=ft.Colors.with_opacity(0.07, color),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.16, color)),
    )


def meter(
    ratio: float,
    width: int = 80,
    height: int = 3,
    color: str = CYAN,
    track: str = LINE,
) -> ft.Container:
    """自绘进度条（轨道 + 填充），替代 ``ft.ProgressBar``。

    Flet 1.1 的 ``ft.ProgressBar`` 内部依赖 Flex 布局，一旦作为
    ``Row(wrap=True)`` 子项（例如定宽车位卡片）就会让整块区域渲染失败，
    界面上表现为一大片灰色空白。需要进度条时请统一用本函数。
    """
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        value = 0.0
    value = max(0.0, min(1.0, value))
    return ft.Container(
        content=ft.Container(
            width=width * value, height=height, bgcolor=color, border_radius=height
        ),
        width=width,
        height=height,
        bgcolor=track,
        border_radius=height,
        alignment=ft.Alignment.CENTER_LEFT,
    )


def dot(color: str, size: int = 7) -> ft.Container:
    return ft.Container(width=size, height=size, bgcolor=color, border_radius=size)


def eyebrow_label(text: str = "", color: str = ACCENT) -> ft.Text:
    """页面眉标文字（可作为控件复用，便于动态更新 value）。"""
    return ft.Text(text, size=10, weight=ft.FontWeight.W_600, color=color)


def section_title(title: str, subtitle: str = "", accent: str = ACCENT) -> ft.Column:
    """卡片内的小节标题：左侧 3px 色条 + 标题 + 说明。"""
    controls: list[ft.Control] = [
        ft.Row(
            [
                ft.Container(width=3, height=14, bgcolor=accent, border_radius=2),
                ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=TEXT),
            ],
            spacing=8,
        )
    ]
    if subtitle:
        controls.append(ft.Text(subtitle, size=11, color=MUTED))
    return ft.Column(controls, spacing=6)


def empty_state(
    title: str,
    hint: str = "",
    icon: str = ft.Icons.INBOX_OUTLINED,
    color: str = DIM,
) -> ft.Container:
    """统一空状态：居中图标 + 标题 + 提示。"""
    controls: list[ft.Control] = [
        ft.Icon(icon, size=28, color=color),
        ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=MUTED),
    ]
    if hint:
        controls.append(
            ft.Text(hint, size=11, color=DIM, text_align=ft.TextAlign.CENTER)
        )
    return ft.Container(
        content=ft.Column(
            controls, spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER
        ),
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.symmetric(vertical=36, horizontal=16),
    )


# ---------------- 指标卡 ----------------
def stat_card(
    label: str,
    value: str,
    unit: str = "",
    accent: str = CYAN,
    sub: str = "",
) -> ft.Container:
    """指标卡：顶部标签 + 等宽大数字 + 可选补充说明。"""
    controls: list[ft.Control] = [
        ft.Row([dot(accent, 6), ft.Text(label, size=11, color=MUTED)], spacing=7),
        ft.Row(
            [
                ft.Text(
                    value,
                    size=22,
                    weight=ft.FontWeight.W_600,
                    color=TEXT,
                    font_family=MONO,
                ),
                ft.Text(unit, size=10, color=MUTED),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.END,
        ),
    ]
    if sub:
        controls.append(ft.Text(sub, size=10, color=DIM))
    return card(ft.Column(controls, spacing=8), padding=16)


def page_header(
    eyebrow: str | ft.Text,
    title: str,
    subtitle: str = "",
    trailing: ft.Control | None = None,
) -> ft.Row:
    """页面标题：小号眉标 + 主标题 + 说明 + 右侧操作。"""
    eyebrow_control = (
        eyebrow if isinstance(eyebrow, ft.Control) else eyebrow_label(str(eyebrow))
    )
    left = ft.Column(
        [
            eyebrow_control,
            ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color=TEXT),
            ft.Text(subtitle, size=12, color=MUTED)
            if subtitle
            else ft.Container(height=0),
        ],
        spacing=4,
    )
    return ft.Row(
        [left, ft.Container(expand=True), trailing or ft.Container(width=0)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


# ---------------- 表单 ----------------
def field_style(**kwargs) -> dict:
    """Material 风格输入框：白底 + 细边框，聚焦时高亮强调色。"""
    base = dict(
        text_size=13,
        border=ft.OutlineInputBorder(border_radius=RADIUS_CTRL),
        border_radius=RADIUS_CTRL,
        border_color=LINE,
        border_width=1,
        focused_border_color=ACCENT,
        focused_border_width=1.5,
        filled=True,
        fill_color=SURFACE,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=12),
        label_style=ft.TextStyle(size=11, color=MUTED),
        hint_style=ft.TextStyle(size=11, color=DIM),
    )
    base.update(kwargs)
    return base


# ---------------- 按钮 ----------------
def primary_button(text: str, icon: str | None = None, **kwargs) -> ft.FilledButton:
    kwargs.setdefault("bgcolor", ACCENT)
    kwargs.setdefault("color", ON_ACCENT)
    kwargs.setdefault(
        "style", ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=RADIUS_CTRL))
    )
    return ft.FilledButton(text, icon=icon, **kwargs)


def secondary_button(text: str, icon: str | None = None, **kwargs) -> ft.OutlinedButton:
    kwargs.setdefault(
        "style", ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=RADIUS_CTRL))
    )
    return ft.OutlinedButton(text, icon=icon, **kwargs)
