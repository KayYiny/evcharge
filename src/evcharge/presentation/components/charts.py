# -*- coding: utf-8 -*-
"""matplotlib 图表工具（components.charts）。

Flet 1.1.1 已移除原生折线/柱状图组件，改用 matplotlib 渲染 PNG 字节流，
以 ft.Image 嵌入界面。

**延迟导入**：matplotlib 首次导入要建字体缓存，冷启动能占好几秒。
这里改成「第一次画图时才 import」，让主窗口先出来，图表稍后就绪。
"""

from __future__ import annotations

from typing import Sequence

import flet as ft

from evcharge.presentation.components import theme

_plt = None  # 首次使用时初始化，见 _pyplot()


def _pyplot():
    """懒加载 matplotlib.pyplot，并只在第一次配置样式。"""
    global _plt
    if _plt is not None:
        return _plt

    import matplotlib

    matplotlib.use("Agg")  # 无界面后端，适合嵌入渲染
    import matplotlib.pyplot as plt

    # 统一的浅色小字号样式，适配新拟态卡片背景
    plt.rcParams.update(
        {
            "figure.facecolor": "none",
            "axes.facecolor": "none",
            "savefig.facecolor": "none",
            "axes.edgecolor": theme.LINE,
            "axes.labelcolor": theme.MUTED,
            "xtick.color": theme.DIM,
            "ytick.color": theme.DIM,
            "text.color": theme.TEXT,
            "grid.color": theme.LINE_SOFT,
            "font.size": 10,
        }
    )
    # 中文字体回退链（macOS / Windows / Linux 跨平台）
    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Hiragino Sans GB",
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题
    _plt = plt
    return plt


def _fig_to_image(fig, width: int = 520, height: int = 240) -> ft.Image:
    """把 matplotlib Figure 转为 Flet Image 控件（PNG 字节流）。"""
    import io

    plt = _pyplot()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return ft.Image(
        src=buf.read(),
        width=width,
        height=height,
        fit=ft.BoxFit.CONTAIN,
    )


def _empty_image(title: str, width: int, height: int, message: str = "暂无数据") -> ft.Image:
    """空数据占位图：避免饼图全零时 matplotlib 抛 ValueError 导致整页崩溃。"""
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(width / 100, height / 100))
    ax.axis("off")
    if title:
        ax.set_title(title, color=theme.TEXT, fontsize=11)
    ax.text(0.5, 0.5, message, ha="center", va="center", color=theme.DIM,
            fontsize=12, transform=ax.transAxes)
    fig.tight_layout()
    return _fig_to_image(fig, width, height)


def line_chart(
    values: Sequence[float],
    title: str = "",
    ylabel: str = "",
    color: str = theme.CYAN,
    width: int = 520,
    height: int = 240,
) -> ft.Image:
    """渲染折线图（实时功率曲线、小时画像等）。"""
    if len(values) == 0:
        return _empty_image(title, width, height)
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(width / 100, height / 100))
    ax.plot(range(len(values)), list(values), color=color, linewidth=2)
    ax.fill_between(range(len(values)), list(values), color=color, alpha=0.18)
    ax.grid(True, linestyle="--", alpha=0.35)
    if title:
        ax.set_title(title, color=theme.TEXT, fontsize=11)
    if ylabel:
        ax.set_ylabel(ylabel, color=theme.DIM, fontsize=9)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    return _fig_to_image(fig, width, height)


def bar_chart(
    labels: Sequence[str],
    values: Sequence[float],
    title: str = "",
    ylabel: str = "",
    colors: Sequence[str] | None = None,
    width: int = 520,
    height: int = 240,
) -> ft.Image:
    """渲染柱状图（每日费用、各桩订单量、时段占比等）。"""
    if len(values) == 0:
        return _empty_image(title, width, height)
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(width / 100, height / 100))
    bar_colors = list(colors) if colors else [theme.PURPLE] * len(values)
    ax.bar(range(len(values)), list(values), color=bar_colors, width=0.62)
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([str(label) for label in labels], rotation=45, ha="right", fontsize=7)
    if title:
        ax.set_title(title, color=theme.TEXT, fontsize=11)
    if ylabel:
        ax.set_ylabel(ylabel, color=theme.DIM, fontsize=9)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    return _fig_to_image(fig, width, height)


def pie_chart(
    labels: Sequence[str],
    values: Sequence[float],
    title: str = "",
    colors: Sequence[str] | None = None,
    width: int = 520,
    height: int = 240,
) -> ft.Image:
    """渲染饼图（峰/平/谷用电结构）。"""
    if len(values) == 0 or sum(float(v) for v in values) <= 0:
        return _empty_image(title, width, height)
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(width / 100, height / 100))
    default_colors = [
        theme.PERIOD_COLORS.get(name, theme.PURPLE)
        for name in ("峰段", "平段", "谷段")
    ]
    pie_colors = list(colors) if colors else default_colors[: len(values)]
    wedges, _texts, autotexts = ax.pie(
        list(values),
        colors=pie_colors[: len(values)],
        autopct="%1.1f%%",
        startangle=90,
        textprops={"color": theme.ON_ACCENT, "fontsize": 9},
    )
    ax.legend(
        wedges,
        [str(label) for label in labels],
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=8,
        frameon=False,
    )
    if title:
        ax.set_title(title, color=theme.TEXT, fontsize=11)
    fig.tight_layout()
    return _fig_to_image(fig, width, height)
