# -*- coding: utf-8 -*-
"""设置页（evcharge.presentation.pages.settings_page）：站点/计费 + 通用 OpenAI 兼容 AI 配置。

站点：
- 项目名称（顶栏和窗口标题）
- 电价档（国网高速 / 福建居民）
- 服务费覆盖（留空使用电价档默认值）
- 界面刷新间隔（1/2/3/5 秒）

AI：
- 厂商 URL、API Key、模型名称
- AI 人设名称（默认“小充”）和人设提示词
- 本地汇总后再发给大模型（关闭则发送全部原始订单数据，开启则发送本地小总结）

车牌识别：
- 复用同一套 AI 配置（URL / API Key），只多填一个「视觉模型」
- 可在本卡片直接「测试识别」验证模型是否支持读图

配置与订单同库存放（data/charging.db 的 app_settings 表），API Key 落库前会做保护。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from evcharge.application.assistant import AIError, test_connection
from evcharge.application.errors import PlateRecognitionError
from evcharge.application.services import AccountService, SettingsService, VisionService
from evcharge.presentation.components import theme
from evcharge.presentation.components.settings_cards import SettingsCards
from evcharge.presentation.components.settings_data import SettingsData
from evcharge.presentation.components.settings_feedback import SettingsFeedback

# 展示用的静态文案与选项（纯数据，来自 domain，不涉及任何 IO）
from evcharge.domain.config import (
    DEFAULT_APP_TITLE,
    DEFAULT_PERSONA_NAME,
    DEFAULT_PERSONA_PROMPT,
    REFRESH_OPTIONS,
    TARIFF_LABELS,
    TARIFF_PROFILES,
    TARIFF_SHORT_LABELS,
    VISION_MODEL_HINT,
    identity_defaults,
)
from evcharge.domain.app_settings import AppSettings

_FIELD_WIDTH = 760

class SettingsPage(SettingsCards, SettingsData, SettingsFeedback):
    """站点与 AI 配置页面。"""

    def __init__(
        self,
        on_apply: Optional[Callable[[AppSettings], None]] = None,
        accounts: Optional[AccountService] = None,
        settings_service: Optional[SettingsService] = None,
        vision: Optional[VisionService] = None,
        maintenance=None,
    ) -> None:
        # 服务由组合根注入；缺失时退化为「只读展示」，账号卡不渲染
        self.accounts = accounts
        self.settings_service = settings_service
        self.vision = vision
        self.maintenance = maintenance
        settings = (
            settings_service.current() if settings_service is not None
            else AppSettings()
        )
        self.settings = settings
        self.on_apply = on_apply
        self.page: ft.Page | None = None
        self.accounts_col: ft.Column | None = None

        # ---------- 站点身份 ----------
        self.eyebrow = theme.eyebrow_label("")
        self.app_title = ft.TextField(
            label="项目名称",
            value=settings.app_title,
            hint_text=DEFAULT_APP_TITLE,
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.site_code = ft.TextField(
            label="站点徽标",
            value=settings.resolved_site_code,
            hint_text="如 G15",
            width=200,
            **theme.field_style(),
        )
        self.operator_name = ft.TextField(
            label="运营方",
            value=settings.resolved_operator_name,
            hint_text="如 国家电网",
            width=280,
            **theme.field_style(),
        )
        self.site_name = ft.TextField(
            label="站点名称",
            value=settings.resolved_site_name,
            hint_text="如 长深高速江苏段",
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )

        # ---------- 计费 ----------
        self.tariff_profile = ft.Dropdown(
            label="电价档",
            value=settings.tariff_profile,
            options=[
                ft.dropdown.Option(key, label) for key, label in TARIFF_LABELS.items()
            ],
            width=430,
            **theme.field_style(),
        )
        self.tariff_profile.on_select = lambda e: self._on_tariff_change()
        self._last_profile = settings.tariff_profile
        self.tariff_hint = ft.Text("", size=10, color=theme.DIM)
        self.service_fee = ft.TextField(
            label="服务费（元/度）",
            value=f"{settings.effective_service_fee:.2f}",
            hint_text="留空使用电价档默认值",
            width=150,
            **theme.field_style(),
        )
        refresh_value = (
            str(int(settings.refresh_interval_s))
            if int(settings.refresh_interval_s) in (1, 2, 3, 5)
            else "2"
        )
        self.refresh_interval = ft.Dropdown(
            label="界面刷新间隔",
            value=refresh_value,
            options=[
                ft.dropdown.Option(str(int(option)), f"{option:g} 秒")
                for option in REFRESH_OPTIONS
            ],
            width=150,
            **theme.field_style(),
        )

        # ---------- AI ----------
        self.base_url = ft.TextField(
            label="厂商接口 URL",
            value=settings.base_url,
            hint_text="OpenAI 兼容基础地址，例如 https://api.openai.com/v1、https://api.deepseek.com",
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.api_key = ft.TextField(
            label="API Key",
            value=settings.api_key,
            password=True,
            can_reveal_password=True,
            hint_text="填写对应厂商的 API Key",
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.model = ft.TextField(
            label="模型名称",
            value=settings.model,
            hint_text="例如 gpt-4o-mini、deepseek-chat、qwen-plus",
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.persona_name = ft.TextField(
            label="AI 人设名称",
            value=settings.persona_name,
            hint_text="例如 小充",
            width=370,
            **theme.field_style(),
        )
        self.persona_prompt = ft.TextField(
            label="AI 人设提示词",
            value=settings.persona_prompt,
            multiline=True,
            min_lines=4,
            max_lines=8,
            hint_text="AI 的身份与回答要求",
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.use_local_summary = ft.Switch(
            label="本地汇总后再发给大模型（关闭则发送原始订单）",
            value=settings.use_local_summary,
        )

        # ---------- 视觉模型（车牌识别用，共用上面的 URL / API Key） ----------
        self.vision_model = ft.TextField(
            label="视觉模型（车牌识别用，留空则复用上面的模型）",
            value=settings.vision_model,
            hint_text=VISION_MODEL_HINT,
            width=_FIELD_WIDTH,
            **theme.field_style(),
        )
        self.plate_test_btn = ft.OutlinedButton(
            "测试车牌识别",
            icon=ft.Icons.IMAGE_SEARCH,
            on_click=lambda e: self._on_plate_test(),
        )
        self.vision_file_picker: ft.FilePicker | None = None

        self.status = ft.Text("", size=12, color=theme.MUTED)
        self._progress_text: ft.Text | None = None
        settings_path = (
            settings_service.path if settings_service is not None else None
        )
        self.path_text = ft.Text(
            f"{settings_path}" if settings_path else "配置文件由运行环境决定",
            size=10,
            color=theme.DIM,
        )

    # ---------------- 构建 ----------------
    def build(self, page: ft.Page) -> ft.Control:
        self.page = page
        self._mount_vision_picker(page)
        if self.maintenance is not None:
            self._mount_data_picker(page)
        self._sync_tariff_hint()
        header = theme.page_header(
            self.eyebrow,
            "设置",
            "保存后顶栏与侧栏立即刷新",
        )
        cards = [
            header,
            self._identity_card(),
            self._tariff_card(),
            self._ai_card(),
        ]
        if self.maintenance is not None:
            cards.append(self._data_card())
        if self.accounts is not None:
            cards.append(self._accounts_card())
        return ft.Column(
            [
                *cards,
                ft.Row(
                    [
                        ft.FilledButton(
                            "保存设置",
                            icon=ft.Icons.SAVE,
                            bgcolor=theme.AMBER,
                            color=theme.ON_ACCENT,
                            on_click=lambda e: self._on_save(),
                        ),
                        ft.OutlinedButton(
                            "测试 AI 连接",
                            icon=ft.Icons.WIFI,
                            on_click=lambda e: self._on_test(),
                        ),
                        ft.TextButton(
                            "恢复默认人设",
                            icon=ft.Icons.RESTART_ALT,
                            on_click=lambda e: self._on_reset_persona(),
                        ),
                    ],
                    spacing=10,
                ),
                self.status,
                self.path_text,
                theme.card(
                    ft.Text(
                        "API Key 保存在本机数据库，请勿连同数据文件一起提交到仓库。",
                        size=10,
                        color=theme.AMBER,
                    ),
                    padding=12,
                    bgcolor=theme.SURFACE_2,
                    radius=10,
                ),
            ],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    def _rebuild_accounts(self) -> None:
        if self.accounts_col is None:
            return
        users = [
            u for u in self.accounts.spot_users()
        ]
        if not users:
            self.accounts_col.controls = [
                theme.empty_state(
                    "暂无车位账号",
                    "添加车位后自动创建",
                    icon=ft.Icons.BADGE_OUTLINED,
                )
            ]
            return
        rows = []
        for user in users:
            # 车位用户的登录名就是车位名（uid 只是订单归属的内部标识）
            username = user.get("username")

            def make_reset(u=user):
                def reset(e):
                    self._open_password_dialog(u)
                return reset

            rows.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.LOCAL_PARKING,
                                size=15,
                                color=theme.CYAN,
                            ),
                            ft.Text(
                                username,
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=theme.TEXT,
                                font_family=theme.MONO,
                            ),
                            ft.Text(
                                "车位账号",
                                size=10,
                                color=theme.MUTED,
                            ),
                            ft.Container(expand=True),
                            ft.OutlinedButton(
                                "重置密码",
                                icon=ft.Icons.KEY_OUTLINED,
                                height=30,
                                on_click=make_reset(),
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    border_radius=8,
                    bgcolor=theme.SURFACE_2,
                    border=ft.Border.all(1, theme.LINE),
                )
            )
        self.accounts_col.controls = rows

    def _open_password_dialog(self, user: dict) -> None:
        if self.page is None:
            return
        username = user.get("username")
        pw_input = ft.TextField(
            label=f"{username} 的新密码",
            value=self.accounts.default_password if self.accounts else "",
            hint_text=f"默认密码为 {self.accounts.default_password if self.accounts else ''}",
            password=True,
            can_reveal_password=True,
            width=260,
            **theme.field_style(),
        )

        def submit(e):
            self.accounts.reset_spot_password(username, pw_input.value.strip())
            self._rebuild_accounts()
            self._close_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"重置车位账号密码 · {username}"),
            content=pw_input,
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog(dlg)),
                ft.FilledButton("保存", on_click=submit),
            ],
        )
        # Flet 1.1：必须用 show_dialog 才会真正显示（直接赋值 page.dialog 无效）
        self.page.show_dialog(dlg)

    def _close_dialog(self, dlg: ft.AlertDialog) -> None:
        if self.page is not None:
            self.page.pop_dialog()

    def _mount_vision_picker(self, page: ft.Page) -> None:
        """把 FilePicker 注册成页面服务（Flet 1.x 的服务需挂在 page.services）。"""
        if self.vision_file_picker is not None:
            return
        self.vision_file_picker = ft.FilePicker()
        try:
            page.services.append(self.vision_file_picker)
        except (AttributeError, TypeError):
            page.overlay.append(self.vision_file_picker)  # 兼容旧版挂载位置

    def _on_plate_test(self) -> None:
        if self.page is not None:
            self.page.run_task(self._plate_test_async)

    async def _plate_test_async(self) -> None:
        """选一张图片做一次真实识别，验证视觉模型是否支持读图。"""
        if self.page is None:
            return
        try:
            settings = self._collect()
        except ValueError as exc:
            self._show_message(f"配置有误：{exc}", theme.AMBER, "无法测试")
            return
        if not settings.vision_configured:
            self._show_message(
                "请先填写 AI 接口 URL、API Key，并确认视觉模型非空"
                "（留空会复用对话模型）。",
                theme.AMBER,
                "还差配置",
            )
            return

        self._mount_vision_picker(self.page)
        try:
            files = await self.vision_file_picker.pick_files(
                dialog_title="选择一张车牌照片测试识别",
                file_type=ft.FilePickerFileType.IMAGE,
                allowed_extensions=["jpg", "jpeg", "png", "bmp", "webp"],
            )
        except Exception as exc:
            self._show_message(f"打开文件失败：{exc}", theme.RED, "测试失败")
            return
        if not files or not getattr(files[0], "path", None):
            return  # 用户取消了选图，不打扰

        self._show_progress("测试车牌识别", "正在识别，请稍候…")
        try:
            plate, _raw = await asyncio.to_thread(
                self.vision.recognize, files[0].path, settings
            )
        except PlateRecognitionError as exc:
            self._finish_progress(f"识别失败：{exc}", theme.RED, "测试失败")
        except Exception as exc:  # 兜底，避免异步任务异常中断页面
            self._finish_progress(f"测试异常：{exc}", theme.RED, "测试失败")
        else:
            self._finish_progress(f"识别成功，车牌号：{plate}", theme.GREEN, "测试通过")

    def _on_tariff_change(self) -> None:
        """切换电价档：默认身份与服务费自动预填（用户自定义值不会被覆盖）。"""
        new_key = self.tariff_profile.value or "state_grid"
        old_defaults = identity_defaults(self._last_profile)
        new_defaults = identity_defaults(new_key)
        for field, key in (
            (self.site_code, "site_code"),
            (self.site_name, "site_name"),
            (self.operator_name, "operator_name"),
        ):
            current = (field.value or "").strip()
            if not current or current == old_defaults[key]:
                field.value = new_defaults[key]

        old_profile = TARIFF_PROFILES.get(self._last_profile)
        profile = TARIFF_PROFILES.get(new_key)
        raw_fee = (self.service_fee.value or "").strip()
        old_default_fee = f"{old_profile.service_fee:.2f}" if old_profile else ""
        if profile is not None and (not raw_fee or raw_fee == old_default_fee):
            self.service_fee.value = f"{profile.service_fee:.2f}"

        self._last_profile = new_key
        self._sync_tariff_hint()
        self._update()

    def _sync_tariff_hint(self) -> None:
        """展示所选电价档的峰/平/谷价格与当前服务费。"""
        key = self.tariff_profile.value or "state_grid"
        profile = TARIFF_PROFILES.get(key)
        if profile is None:
            self.tariff_hint.value = ""
            return
        raw_fee = (self.service_fee.value or "").strip()
        try:
            fee = float(raw_fee) if raw_fee else profile.service_fee
        except ValueError:
            fee = profile.service_fee
        prices: dict[str, float] = {}
        for item in profile.periods:
            prices.setdefault(item.name, item.price)
        parts = " · ".join(
            f"{name} {prices[name]:.4f}" for name in ("峰段", "平段", "谷段") if name in prices
        )
        self.tariff_hint.value = (
            f"当前档位：{TARIFF_SHORT_LABELS.get(key, key)}"
            + (f"（{parts} 元/度）" if parts else "")
            + f" · 服务费 {fee:.2f} 元/度"
        )

    def _collect(self) -> AppSettings:
        raw_fee = (self.service_fee.value or "").strip()
        service_fee = None
        if raw_fee:
            try:
                service_fee = float(raw_fee)
            except ValueError as exc:
                raise ValueError("服务费必须是数字") from exc
            if service_fee < 0:
                raise ValueError("服务费不能为负数")

        try:
            refresh = float(self.refresh_interval.value or 2)
        except ValueError:
            refresh = 2.0

        return AppSettings(
            app_title=(self.app_title.value or "").strip(),
            site_code=(self.site_code.value or "").strip(),
            site_name=(self.site_name.value or "").strip(),
            operator_name=(self.operator_name.value or "").strip(),
            tariff_profile=self.tariff_profile.value or "state_grid",
            service_fee=service_fee,
            refresh_interval_s=refresh,
            use_local_summary=bool(self.use_local_summary.value),
            base_url=(self.base_url.value or "").strip(),
            api_key=(self.api_key.value or "").strip(),
            model=(self.model.value or "").strip(),
            persona_name=(self.persona_name.value or "").strip() or DEFAULT_PERSONA_NAME,
            persona_prompt=(self.persona_prompt.value or "").strip()
            or DEFAULT_PERSONA_PROMPT,
            temperature=self.settings.temperature,
            timeout_s=self.settings.timeout_s,
            vision_model=(self.vision_model.value or "").strip(),
        )

    def _on_save(self) -> None:
        try:
            settings = self._collect()
        except ValueError as exc:
            self._show_message(f"配置有误：{exc}", theme.AMBER, "保存失败")
            return

        if not settings.app_title:
            self._show_message("项目名称不能为空。", theme.AMBER, "保存失败")
            return
        if not settings.base_url:
            self._show_message("厂商接口 URL 不能为空。", theme.AMBER, "保存失败")
            return
        if not settings.model:
            self._show_message("模型名称不能为空。", theme.AMBER, "保存失败")
            return

        try:
            path = self.settings_service.save(settings)
        except OSError as exc:
            self._show_message(f"保存失败：{exc}", theme.RED, "保存失败")
            return

        self.settings = settings
        self.persona_name.value = settings.persona_name
        self.persona_prompt.value = settings.persona_prompt
        if self.on_apply is not None:
            try:
                self.on_apply(settings)
            except Exception as exc:  # 应用设置失败时仍保留已保存文件
                self._show_message(
                    f"已保存到 {path}，但应用到界面时出错：{exc}",
                    theme.AMBER,
                    "部分生效",
                )
                return

        if not settings.api_key:
            self._show_message(
                f"已保存到 {path}。\n但 API Key 为空，AI 报告与车牌识别仍不可用。",
                theme.AMBER,
                "已保存",
            )
        else:
            self._show_message(f"已保存到 {path}", theme.GREEN, "保存成功")

    def _on_test(self) -> None:
        if self.page is not None:
            self.page.run_task(self._test_async)

    async def _test_async(self) -> None:
        try:
            settings = self._collect()
        except ValueError as exc:
            self._show_message(f"配置有误：{exc}", theme.AMBER, "无法测试")
            return
        if not settings.configured:
            self._show_message(
                "请先填写接口 URL、API Key 和模型名称。",
                theme.AMBER,
                "还差配置",
            )
            return

        self._show_progress("测试 AI 连接", "正在连接，请稍候…")
        try:
            reply = await asyncio.to_thread(test_connection, settings)
        except AIError as exc:
            self._finish_progress(f"连接失败：{exc}", theme.RED, "测试失败")
        except Exception as exc:  # 兜底，避免异步任务异常中断页面
            self._finish_progress(f"测试失败：{exc}", theme.RED, "测试失败")
        else:
            self._finish_progress(
                f"连接成功，模型回复：{reply[:80]}", theme.GREEN, "测试通过"
            )

    def _on_reset_persona(self) -> None:
        self.persona_name.value = DEFAULT_PERSONA_NAME
        self.persona_prompt.value = DEFAULT_PERSONA_PROMPT
        self._show_message(
            f"已恢复默认人设「{DEFAULT_PERSONA_NAME}」，点「保存设置」后生效。",
            theme.CYAN,
            "已恢复默认",
        )

    def apply_branding(self, branding) -> None:
        """设置保存后更新页面眉标与强调色。"""
        self.eyebrow.value = branding.eyebrow("配置中心")
        self.eyebrow.color = branding.accent

    def on_show(self) -> None:
        """切换到设置页时刷新车位账号列表与状态文本（不覆盖编辑中的字段）。"""
        self._sync_tariff_hint()
        if self.accounts is not None:
            self._rebuild_accounts()
        self._update()
