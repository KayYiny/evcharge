# -*- coding: utf-8 -*-
"""AI 分析页（evcharge.presentation.pages.ai_page）：管理员问题 + OpenAI 兼容接口。

数据模式由设置页的「本地汇总后再发给大模型」开关决定：
- 关闭（默认）：不做本地汇总，点击问题后把**全部订单原始记录** + 问题发给模型；
- 开启：先生成本地小总结，再把总结 + 问题发给模型，并在页面展示汇总。

两种模式都会在问题下方显示模型分析；开启本地汇总时还会显示对应问题的本地结论。
"""

from __future__ import annotations

import asyncio
from typing import Callable, List

import flet as ft

from evcharge.application.assistant import AIError, answer_question
from evcharge.application.analytics.report import (
    SITE_QUESTIONS,
    SiteSummary,
    build_raw_data_text,
    build_site_summary,
)
from evcharge.application.services import OrderService
from evcharge.presentation.components import theme


class AIPage:
    """AI 分析页面（保留已加载的订单/汇总/回答，切换页面不丢失）。"""

    def __init__(
        self,
        orders: OrderService,
        settings_provider: Callable[[], object],
    ) -> None:
        self.orders_service = orders
        # 设置在每次 on_show / 提问时通过 provider 读取，刚保存的配置立即生效
        self._settings_provider = settings_provider
        self.page: ft.Page | None = None
        self.orders: List[dict] | None = None
        self.summary: SiteSummary | None = None
        self.answer_box: ft.Container | None = None
        self.custom_question: ft.TextField | None = None
        self._rendered_use_summary = False
        self.eyebrow = theme.eyebrow_label("")
        self.mode_hint = ft.Text("", size=10, color=theme.DIM)

        self.provider_text = ft.Text("", size=10, color=theme.MUTED)
        self.generate_btn = ft.FilledButton(
            "生成 AI 报告",
            icon=ft.Icons.AUTO_AWESOME,
            bgcolor=theme.AMBER,
            color=theme.ON_ACCENT,
        )
        self.result_box = ft.Container(
            content=ft.Text(
                "点击「生成 AI 报告」读取订单，再选问题提问。",
                size=12,
                color=theme.MUTED,
            ),
            padding=20,
            border_radius=theme.RADIUS_CARD,
            bgcolor=theme.SURFACE,
            border=ft.Border.all(1, theme.LINE),
            expand=True,
        )

    def _settings(self):
        """读当前设置（provider 由组合根注入）。"""
        return self._settings_provider()

    # ---------------- 构建 ----------------
    def build(self, page: ft.Page) -> ft.Control:
        self.page = page
        self.generate_btn.on_click = lambda e: self._summarize()
        self._refresh_provider()
        header = theme.page_header(
            self.eyebrow,
            "AI 报告",
            "选择问题后由模型分析订单数据",
            trailing=self.generate_btn,
        )
        return ft.Column(
            [
                header,
                self.mode_hint,
                self.provider_text,
                self.result_box,
            ],
            spacing=12,
            expand=True,
        )

    def apply_branding(self, branding) -> None:
        """设置保存后更新页面眉标与强调色。"""
        self.eyebrow.value = branding.eyebrow("AI 简报")
        self.eyebrow.color = branding.accent

    def on_show(self) -> None:
        """切换到 AI 页时刷新接口状态；若本地汇总模式变化则重新渲染。"""
        settings = self._settings()
        self._refresh_provider()
        if (
            self.orders is not None
            and settings.use_local_summary != self._rendered_use_summary
        ):
            self._render_summary()

    # ---------------- 接口状态 ----------------
    def _refresh_provider(self) -> None:
        settings = self._settings()
        mode = "本地汇总" if settings.use_local_summary else "全部原始订单"
        self.mode_hint.value = f"数据模式：{mode}"
        if settings.configured:
            self.provider_text.value = (
                f"● 已连接配置：{settings.base_url} · 模型 {settings.model} · "
                f"人设 {settings.persona_name}"
            )
            self.provider_text.color = theme.GREEN
        else:
            self.provider_text.value = "● 尚未配置 AI 接口（左侧「设置」）"
            self.provider_text.color = theme.AMBER

    # ---------------- 载入数据 ----------------
    def _summarize(self) -> None:
        orders = self.orders_service.list_orders()
        if not orders:
            self.result_box.content = theme.empty_state(
                "暂无订单数据",
                "先去「实时监控」上电产生订单",
                icon=ft.Icons.RECEIPT_LONG_OUTLINED,
            )
            self._update()
            return

        self.orders = orders
        settings = self._settings()
        self.summary = build_site_summary(orders) if settings.use_local_summary else None
        self._render_summary()

    def _render_summary(self) -> None:
        orders = self.orders
        if orders is None:
            return
        settings = self._settings()
        use_summary = settings.use_local_summary
        if use_summary and self.summary is None:
            self.summary = build_site_summary(orders)
        if use_summary and self.summary is None:
            self.result_box.content = ft.Text("⚠ 订单数据无法聚合。", color=theme.RED)
            self._update()
            return
        self._rendered_use_summary = use_summary
        persona = settings.persona_name or "小充"

        self.custom_question = ft.TextField(
            label="自定义问题",
            hint_text="例如：如何提高谷段充电比例？",
            expand=True,
            on_submit=lambda e: self._start_custom(),
        )
        ask_btn = ft.FilledButton(
            "提问",
            icon=ft.Icons.SEND,
            on_click=lambda e: self._start_custom(),
        )
        self.answer_box = ft.Container(
            content=ft.Text(
                "选择问题后由 AI 回答。"
                if settings.configured
                else "未配置 AI 时，开启本地汇总仍可看到本地结论。",
                size=12,
                color=theme.MUTED,
            ),
            padding=14,
            border_radius=12,
            bgcolor=theme.SURFACE_2,
        )

        question_buttons = [
            ft.OutlinedButton(
                question["label"],
                on_click=lambda e, q=question: self._start_question(q),
            )
            for question in SITE_QUESTIONS
        ]

        if use_summary and self.summary is not None:
            title = "📊 本地小总结（随问题一起发送）"
            intro = ft.Text(self.summary.to_text(), size=11, selectable=True)
        else:
            title = "📦 已准备全部原始订单"
            intro = ft.Text(
                f"已读取 {len(orders)} 条订单，点击问题后发送给 {persona}。",
                size=12,
                selectable=True,
            )

        self.result_box.content = ft.Column(
            [
                ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=theme.CYAN),
                intro,
                ft.Divider(height=1, color=theme.LINE),
                ft.Text(
                    f"🤖 选择一个管理员问题，让 {persona} 分析",
                    size=13,
                    weight=ft.FontWeight.BOLD,
                    color=theme.CYAN,
                ),
                ft.Row(question_buttons, wrap=True, spacing=8, run_spacing=8),
                ft.Row([self.custom_question, ask_btn], spacing=8),
                self.answer_box,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self._update()

    # ---------------- 提问 ----------------
    def _start_question(self, question: dict) -> None:
        if self.orders is None:
            self._summarize()
        if self.orders is None or self.page is None:
            return
        settings = self._settings()
        local_fact = ""
        if settings.use_local_summary:
            if self.summary is None:
                self.summary = build_site_summary(self.orders)
            if self.summary is not None:
                local_fact = self.summary.local_answer(question["key"])
        self.page.run_task(
            self._ask,
            question["question"],
            local_fact,
            question["label"],
        )

    def _start_custom(self) -> None:
        question = (self.custom_question.value or "").strip() if self.custom_question else ""
        if not question:
            if self.answer_box is not None:
                self.answer_box.content = ft.Text(
                    "⚠ 请先输入问题。", size=12, color=theme.AMBER
                )
                self._update()
            return
        if self.orders is None:
            self._summarize()
        if self.orders is None or self.page is None:
            return
        local_fact = "（自定义问题，请结合本地汇总回答）"
        self.page.run_task(self._ask, question, local_fact, "自定义问题")

    async def _ask(self, question: str, local_fact: str, label: str) -> None:
        if self.orders is None or self.answer_box is None:
            return
        settings = self._settings()
        self._refresh_provider()
        use_summary = settings.use_local_summary
        persona = settings.persona_name or "小充"

        if use_summary:
            if self.summary is None:
                self.summary = build_site_summary(self.orders)
            if self.summary is None:
                self.answer_box.content = ft.Text(
                    "⚠ 本地汇总失败。", size=12, color=theme.RED
                )
                self._update()
                return
            data_text = self.summary.to_text()
            data_kind = "summary"
        else:
            data_text = build_raw_data_text(self.orders)
            data_kind = "raw"

        base_parts: list[ft.Control] = []
        if use_summary:
            base_parts.extend(
                [
                    ft.Text(
                        "本地数据结论",
                        size=12,
                        weight=ft.FontWeight.BOLD,
                        color=theme.CYAN,
                    ),
                    ft.Text(local_fact, size=12, selectable=True),
                    ft.Divider(height=1, color=theme.LINE),
                ]
            )
        else:
            base_parts.append(
                ft.Text(
                    f"本次不本地汇总：{len(self.orders)} 条订单 + 问题将发送给 {persona}。",
                    size=11,
                    color=theme.MUTED,
                )
            )

        if not settings.configured:
            base_parts.append(
                ft.Text(
                    "⚠ 尚未配置 AI 接口，请到「设置」页填写。",
                    size=12,
                    color=theme.AMBER,
                )
            )
            self.answer_box.content = ft.Column(base_parts, spacing=8)
            self._update()
            return

        progress_text = (
            f"{persona} 正在分析本地汇总…"
            if use_summary
            else f"{persona} 正在分析 {len(self.orders)} 条原始订单…"
        )
        base_parts.append(
            ft.Row(
                [
                    ft.ProgressRing(width=18, height=18),
                    ft.Text(progress_text, size=12),
                ],
                spacing=8,
            )
        )
        self.answer_box.content = ft.Column(base_parts, spacing=8)
        self._update()

        try:
            answer = await asyncio.to_thread(
                answer_question,
                settings,
                data_text,
                question,
                data_kind,
            )
        except AIError as exc:
            answer = f"⚠ {exc}"
        except Exception as exc:  # 兜底，避免 AI 异常导致页面不可用
            answer = f"⚠ 调用 AI 失败：{exc}"

        result_parts: list[ft.Control] = []
        if use_summary:
            result_parts.extend(
                [
                    ft.Text(
                        "本地数据结论",
                        size=12,
                        weight=ft.FontWeight.BOLD,
                        color=theme.CYAN,
                    ),
                    ft.Text(local_fact, size=12, selectable=True),
                    ft.Divider(height=1, color=theme.LINE),
                ]
            )
        else:
            result_parts.append(
                ft.Text(
                    f"已发送 {len(self.orders)} 条订单数据。",
                    size=11,
                    color=theme.MUTED,
                )
            )
        result_parts.extend(
            [
                ft.Text(
                    f"{persona} 的分析 · {label}",
                    size=13,
                    weight=ft.FontWeight.BOLD,
                    color=theme.CYAN,
                ),
                ft.Markdown(answer, selectable=True),
            ]
        )
        self.answer_box.content = ft.Column(result_parts, spacing=8)
        self._update()

    def _update(self) -> None:
        if self.page is not None:
            try:
                self.page.update()
            except Exception:
                pass
