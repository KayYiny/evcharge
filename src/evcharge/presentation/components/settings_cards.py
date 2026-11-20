# -*- coding: utf-8 -*-
"""设置页的卡片构建（settings 页的「表单」部分）。

站点身份 / 电价档 / AI 与服务 / 车牌识别 / 账号，各自一个卡片构建方法；
只负责摆控件，读写与校验仍在 ``pages.settings_page.SettingsPage`` 里。
"""

from __future__ import annotations

import flet as ft

from evcharge.presentation.components import theme

class SettingsCards:
    # ---------------- 账号管理 ----------------
    def _accounts_card(self) -> ft.Container:
        """管理员账号密码 + 车位账号密码管理卡片。"""
        admin_pw = ft.TextField(
            label="新管理员密码",
            value="",
            hint_text="留空则不修改 admin 密码",
            password=True,
            can_reveal_password=True,
            width=230,
            **theme.field_style(),
        )
        account_status = ft.Text("", size=11, color=theme.MUTED)

        def save_admin(e):
            pw = admin_pw.value.strip()
            if not pw:
                account_status.value = "未修改管理员密码"
                account_status.color = theme.MUTED
                self._show_message(
                    "「新管理员密码」留空，未做修改。",
                    theme.MUTED,
                    "未修改",
                )
                return
            self.accounts.set_admin_password(pw)
            admin_pw.value = ""
            account_status.value = "✅ 管理员密码已更新"
            account_status.color = theme.GREEN
            self._show_message(
                f"管理员 {self.accounts.admin_username} 的密码已更新，下次登录请使用新密码。",
                theme.GREEN,
                "密码已更新",
            )

        self.accounts_col = ft.Column(spacing=8)
        self._rebuild_accounts()

        return theme.card(
            ft.Column(
                [
                    ft.Text(
                        "账号与车位密码管理",
                        size=13,
                        weight=ft.FontWeight.BOLD,
                        color=theme.TEXT,
                    ),
                    ft.Text(
                        "用户名即车位名，默认密码 admin",
                        size=11,
                        color=theme.DIM,
                    ),
                    ft.Row(
                        [
                            admin_pw,
                            ft.FilledButton(
                                "更新管理员密码",
                                icon=ft.Icons.PASSWORD,
                                bgcolor=theme.ACCENT,
                                color=theme.ON_ACCENT,
                                on_click=save_admin,
                            ),
                            account_status,
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=4, color=theme.LINE),
                    ft.Text(
                        "车位账号",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=theme.TEXT,
                    ),
                    self.accounts_col,
                ],
                spacing=10,
            ),
            padding=16,
        )

    def _identity_card(self) -> ft.Container:
        """站点身份：决定顶栏徽标、标题与各页眉标显示什么。"""
        return theme.card(
            ft.Column(
                [
                    theme.section_title(
                        "站点身份",
                        "徽标、运营方和站点名称会同步到顶栏、侧栏与各页眉标；"
                        "项目名称同步到窗口标题和顶栏主标题。",
                    ),
                    self.app_title,
                    ft.Row([self.site_code, self.operator_name], spacing=12),
                    self.site_name,
                ],
                spacing=12,
            ),
            padding=22,
            width=860,
        )

    def _tariff_card(self) -> ft.Container:
        """电价档与服务费：作用于新会话和历史数据再生成。"""
        return theme.card(
            ft.Column(
                [
                    theme.section_title(
                        "电价档与服务费",
                        "切换电价档会自动预填该档的默认站点身份与服务费；"
                        "已有订单保留原账单，新会话按当前电价档计费。",
                        accent=theme.CYAN,
                    ),
                    self.tariff_profile,
                    self.tariff_hint,
                    ft.Row([self.service_fee, self.refresh_interval], spacing=12),
                    ft.Text(
                        "服务费留空表示使用所选电价档的默认值；"
                        "界面刷新间隔只影响实时监控页的重绘频率。",
                        size=10,
                        color=theme.DIM,
                    ),
                ],
                spacing=12,
            ),
            padding=22,
            width=860,
        )

    def _ai_card(self) -> ft.Container:
        return theme.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(width=3, height=14, bgcolor=theme.CYAN, border_radius=2),
                            ft.Text(
                                "AI 配置（OpenAI 兼容）",
                                size=15,
                                weight=ft.FontWeight.BOLD,
                                color=theme.TEXT,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        "AI 报告与车牌识别共用这一套配置：不同厂商只需替换 URL、"
                        "API Key 和模型名称；程序会在基础地址末尾补 /chat/completions。",
                        size=10,
                        color=theme.MUTED,
                    ),
                    self.base_url,
                    ft.Text(
                        "如果已填写到 /chat/completions 的完整地址，则直接使用。",
                        size=10,
                        color=theme.DIM,
                    ),
                    self.api_key,
                    self.model,
                    theme.divider(),
                    theme.section_title(
                        "车牌识别（拍照 / 相册）",
                        "监控页识别到的车牌会直接用于上电；识别失败只提示，"
                        "不会产生订单，也可照常点「上电」用随机车牌。",
                        accent=theme.PURPLE,
                    ),
                    self.vision_model,
                    ft.Text(
                        "需要该模型支持图片输入。留空则复用上面的模型；"
                        "若对话模型是纯文本模型（如 deepseek-chat），请在此单独填视觉模型。",
                        size=10,
                        color=theme.DIM,
                    ),
                    ft.Row([self.plate_test_btn], spacing=8),
                    theme.divider(),
                    theme.section_title(
                        "AI 人设与数据范围",
                        "",
                        accent=theme.CYAN,
                    ),
                    ft.Row([self.persona_name], spacing=12),
                    self.persona_prompt,
                    self.use_local_summary,
                    ft.Text(
                        "关闭（默认）：不本地汇总，点击问题后把全部订单原始记录发给模型；"
                        "开启：先生成本地小总结，再把总结发给模型。"
                        "原始数据量较大，若模型上下文不足可开启本地汇总。",
                        size=10,
                        color=theme.DIM,
                    ),
                ],
                spacing=12,
            ),
            padding=20,
            width=820,
        )
