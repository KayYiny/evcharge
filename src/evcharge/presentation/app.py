# -*- coding: utf-8 -*-
"""Flet 应用组装：登录 → 角色化外壳（管理员 / 车位用户）。

结构说明：
- 未登录：居中登录卡片（``pages.login_page``）；
- 管理员：5 页外壳（实时监控 / 订单 / 分析 / AI / 设置），可在首页增删车位；
- 车位用户：2 页外壳（我的车位 / 我的订单），只能看到自己的车位与订单；
- 背景采集循环 ``_loops`` 在登录态切换时始终运行：退出登录不影响正在进行的充电。

品牌文字（站点徽标 / 标题 / 副标题 / 页面眉标）全部来自「设置」，
保存后通过 ``BrandingHub`` 即时刷新，代码里不再写死站点信息。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from evcharge.application.runtime import ChargingRuntime
from evcharge.application.services import AppContext
from evcharge.domain.tariff import period_of
from evcharge.presentation.components import theme
from evcharge.presentation.components.branding import (
    Branding,
    BrandingHub,
    branding_from,
)
from evcharge.presentation.pages.ai_page import AIPage
from evcharge.presentation.pages.analysis_page import build_analysis_page
from evcharge.presentation.pages.login_page import build_login_view
from evcharge.presentation.pages.monitor_page import MonitorPage
from evcharge.presentation.pages.orders_page import OrdersPage
from evcharge.presentation.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)


def _apply_window_icon(page: ft.Page, assets_dir) -> None:
    """有 assets/icon.png 时设置窗口图标；缺失或平台不支持时静默跳过。"""
    icon = Path(assets_dir) / "icon.png"
    if not icon.exists():
        return
    try:
        page.window.icon = str(icon)
    except Exception:  # 某些平台不支持自定义窗口图标
        logger.debug("设置窗口图标失败", exc_info=True)


def build_app(page: ft.Page, ctx: AppContext) -> None:
    """构建整个 Flet 应用：登录 → 角色化外壳 + 常驻采集循环。

    ``ctx`` 由组合根 ``evcharge.bootstrap.build_context`` 组装，
    本模块只依赖应用层服务，不直接接触 SQLite / 设置文件 / 识别 SDK。
    """
    manager = ctx.manager
    current_settings = ctx.settings.current()
    branding_hub = BrandingHub(branding_from(current_settings))

    ctx.settings.apply_to(manager, current_settings)
    page.title = branding_hub.branding.window_title()
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=branding_hub.branding.accent)
    page.bgcolor = theme.BG
    page.padding = 0
    page.spacing = 0
    # Flet 1.x 中 page.width/height 是只读的，窗口尺寸要用 page.window
    page.window.width = 1360
    page.window.height = 860
    page.window.min_width = 1240
    page.window.min_height = 720
    _apply_window_icon(page, ctx.assets_dir)

    state: dict = {
        "refresh_interval_s": max(1.0, min(10.0, current_settings.refresh_interval_s)),
        "analysis_order_count": -1,
        "current": "monitor",
        "user": None,  # 当前登录用户（AccountService.authenticate 返回的 dict）
        "shell": None,  # 当前外壳控件引用（登录后才有）
    }

    # ---------------- 通用小工具 ----------------
    def _toast(message: str, color=theme.RED) -> None:
        """右下角提示（外壳内使用）。"""
        snack = ft.SnackBar(
            content=ft.Text(message, color=theme.ON_ACCENT, size=12),
            bgcolor=color,
        )
        try:
            page.show_dialog(snack)
        except Exception:
            logger.debug("提示弹出失败", exc_info=True)

    def _update_clock() -> None:
        shell = state["shell"]
        if shell is None:
            return
        now = datetime.now()
        name, price = period_of(now, manager.tariff.profile)
        color = theme.PERIOD_COLORS.get(name, theme.CYAN)
        shell["clock"].value = now.strftime("%Y-%m-%d  %H:%M:%S")
        shell["period_text"].value = f"{name} · {price:.3f} 元/度"
        shell["period_text"].color = color
        shell["period_chip"].bgcolor = ft.Colors.with_opacity(0.08, color)
        shell["period_chip"].border = ft.Border.all(
            1, ft.Colors.with_opacity(0.18, color)
        )
        for control in (shell["clock"], shell["period_text"], shell["period_chip"]):
            try:
                control.update()
            except Exception:
                pass

    def _paint_header(b: Branding) -> None:
        """把品牌快照画到顶栏/导航上（设置保存后由 BrandingHub 触达）。"""
        page.title = b.window_title()
        shell = state["shell"]
        if shell is None:
            return
        shell["header_title"].value = b.app_title
        shell["header_sub"].value = b.brand_line or b.tariff_short
        shell["brand_badge_text"].value = b.site_code
        shell["brand_badge_text"].color = b.accent
        shell["brand_badge"].bgcolor = ft.Colors.with_opacity(0.10, b.accent)
        shell["brand_badge"].border = ft.Border.all(
            1, ft.Colors.with_opacity(0.22, b.accent)
        )
        shell["nav_lead_icon"].color = b.accent
        shell["nav_lead_text"].value = b.site_code
        shell["nav_lead_text"].color = b.accent
        shell["rail"].indicator_color = ft.Colors.with_opacity(0.10, b.accent)
        shell["rail"].selected_label_text_style = ft.TextStyle(
            color=b.accent, size=10, weight=ft.FontWeight.W_600
        )
        page.theme = ft.Theme(color_scheme_seed=b.accent)
        fee = current_settings.effective_service_fee
        shell["tariff_text"].value = f"{b.tariff_short} · 服务费 {fee:.2f} 元/度"

    def _apply_settings(settings) -> None:
        nonlocal current_settings
        current_settings = settings
        ctx.settings.apply_to(manager, settings)
        state["refresh_interval_s"] = max(
            1.0, min(10.0, settings.refresh_interval_s)
        )
        branding_hub.publish(branding_from(settings))

        # 图表配色跟随电价档：分析页下次进入时重建
        shell = state["shell"]
        if shell is not None and "analysis" in shell["pages"]:
            shell["pages"]["analysis"] = None
            state["analysis_order_count"] = -1
            if state["current"] == "analysis":
                shell["pages"]["analysis"] = build_analysis_page(
                    page, ctx.orders, branding_hub.branding
                )
                state["analysis_order_count"] = ctx.orders.count()
                shell["body"].content = shell["pages"]["analysis"]

        _update_clock()
        try:
            page.update()
        except Exception:
            pass

    def _refresh_orders_page() -> None:
        """车位增删后刷新订单页（重新读库并同步车位/类型/车型筛选项）。"""
        refresher = state.get("refresh_orders")
        if refresher is None:
            return
        try:
            refresher()
            page.update()
        except Exception:  # 订单页尚未挂载时忽略
            logger.exception("刷新订单页失败")

    # ---------------- 管理员：车位增删 ----------------
    def _add_spot(name: str, charger_type: str, power_kw: Optional[float]) -> None:
        try:
            # 落库与建运行时对象由 SpotService 保证一致（订单认同一个 uid）
            pile = ctx.spots.add(name, charger_type, power_kw)
            display_name = pile.name
        except ValueError as exc:
            _toast(str(exc))
            return
        shell = state["shell"]
        if shell is not None:
            shell["monitor"].reload(page)
            shell["monitor"].notify_spot_created(display_name)
            _refresh_orders_page()

    def _delete_spot(key: str) -> None:
        try:
            pile = manager.pile(key)
        except KeyError:
            _toast(f"车位 {key} 不存在")
            return
        name = pile.name
        # 先强制结算（正在充电的车位会落一张订单），再删车位与账号。
        # 订单属于营收历史，删车位不删订单，仍可在订单页按该车位筛选。
        ctx.spots.remove(pile.uid)
        shell = state["shell"]
        if shell is not None:
            shell["monitor"].reload(page)
            shell["monitor"].notify_spot_deleted(name)
            _refresh_orders_page()

    # ---------------- 外壳骨架 ----------------
    def _mount_shell(
        *,
        user_label: str,
        destinations: list[ft.NavigationRailDestination],
        pages: dict[str, ft.Control | None],
        monitor: MonitorPage,
        refresh_hooks: dict[str, Callable[[], None]],
        brand_subscribers: list[Callable[[Branding], None]],
    ) -> None:
        """按角色挂载「侧栏 + 顶栏 + 内容区」外壳（登录后调用一次）。"""
        page.clean()
        branding_hub.reset()
        for subscriber in brand_subscribers:
            branding_hub.subscribe(subscriber)
        branding_hub.subscribe(_paint_header)

        # -------- 顶栏控件 --------
        clock = ft.Text("", size=12, color=theme.MUTED, font_family=theme.MONO)
        period_text = ft.Text(
            "", size=11, weight=ft.FontWeight.W_600, color=theme.CYAN
        )
        period_chip = ft.Container(
            content=period_text,
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=theme.RADIUS_PILL,
            bgcolor=ft.Colors.with_opacity(0.08, theme.CYAN),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.18, theme.CYAN)),
        )
        tariff_text = ft.Text("", size=11, color=theme.MUTED)
        tariff_chip = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.BOLT, size=13, color=theme.MUTED), tariff_text],
                spacing=5,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=theme.RADIUS_PILL,
            bgcolor=theme.SURFACE_2,
            border=ft.Border.all(1, theme.LINE),
        )
        header_title = ft.Text("", size=17, weight=ft.FontWeight.BOLD, color=theme.TEXT)
        header_sub = ft.Text("", size=11, color=theme.MUTED)
        brand_badge_text = ft.Text(
            "", size=13, weight=ft.FontWeight.BOLD, font_family=theme.MONO
        )
        brand_badge = ft.Container(
            content=brand_badge_text,
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            border_radius=theme.RADIUS_CTRL,
        )
        live_dot = theme.dot(theme.GREEN, 7)
        live_text = ft.Text("实时采集", size=10, color=theme.MUTED)

        user_chip = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.ACCOUNT_CIRCLE_OUTLINED, size=15, color=theme.MUTED
                    ),
                    ft.Text(user_label, size=11, color=theme.MUTED),
                ],
                spacing=5,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=theme.RADIUS_PILL,
            bgcolor=theme.SURFACE_2,
            border=ft.Border.all(1, theme.LINE),
        )
        logout_btn = ft.IconButton(
            icon=ft.Icons.LOGOUT,
            icon_size=17,
            icon_color=theme.MUTED,
            tooltip="退出登录",
            on_click=lambda e: _logout(),
        )

        # -------- 侧边导航 --------
        nav_lead_icon = ft.Icon(ft.Icons.ELECTRIC_BOLT, size=20, color=theme.ACCENT)
        nav_lead_text = ft.Text(
            "", size=10, weight=ft.FontWeight.W_600,
            color=theme.ACCENT, font_family=theme.MONO,
        )
        rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            extended=False,
            bgcolor=theme.SURFACE,
            indicator_color=ft.Colors.with_opacity(0.10, theme.ACCENT),
            selected_label_text_style=ft.TextStyle(
                color=theme.ACCENT, size=10, weight=ft.FontWeight.W_600
            ),
            unselected_label_text_style=ft.TextStyle(color=theme.MUTED, size=10),
            leading=ft.Container(
                content=ft.Column(
                    [nav_lead_icon, nav_lead_text],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.only(top=18, bottom=14),
            ),
            destinations=destinations,
        )

        keys = list(pages.keys())
        body = ft.Container(
            content=pages[keys[0]],
            expand=True,
            padding=ft.Padding.symmetric(horizontal=26, vertical=22),
            bgcolor=theme.BG,
        )

        state["shell"] = {
            "clock": clock,
            "period_text": period_text,
            "period_chip": period_chip,
            "tariff_text": tariff_text,
            "header_title": header_title,
            "header_sub": header_sub,
            "brand_badge_text": brand_badge_text,
            "brand_badge": brand_badge,
            "nav_lead_icon": nav_lead_icon,
            "nav_lead_text": nav_lead_text,
            "rail": rail,
            "pages": pages,
            "body": body,
            "monitor": monitor,
            "keys": keys,
        }
        state["current"] = keys[0]

        def _switch(e) -> None:
            key = keys[e.control.selected_index]
            state["current"] = key
            hook = refresh_hooks.get(key)
            if hook is not None:
                hook()
            if key == "monitor":
                monitor.on_show(page)
            else:
                monitor.on_hide()
            body.content = pages[key]
            page.update()

        rail.on_change = _switch

        header = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            brand_badge,
                            ft.Column([header_title, header_sub], spacing=2),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(expand=True),
                    ft.Row(
                        [
                            tariff_chip,
                            period_chip,
                            live_dot,
                            live_text,
                            clock,
                            user_chip,
                            logout_btn,
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.Padding.symmetric(horizontal=26, vertical=13),
            bgcolor=theme.SURFACE,
            border=ft.Border(bottom=ft.BorderSide(1, theme.LINE)),
        )

        page.add(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=rail,
                            bgcolor=theme.SURFACE,
                            border=ft.Border(right=ft.BorderSide(1, theme.LINE)),
                        ),
                        ft.Column([header, body], expand=True, spacing=0),
                    ],
                    expand=True,
                ),
                expand=True,
                bgcolor=theme.BG,
            )
        )
        _paint_header(branding_hub.branding)
        _update_clock()
        try:
            page.update()
        except Exception:
            pass

    # ---------------- 管理员外壳 ----------------
    def _build_admin_shell() -> None:
        monitor = MonitorPage(
            manager,
            is_admin=True,
            on_add_spot=_add_spot,
            on_delete_spot=_delete_spot,
            settings_provider=ctx.settings.current,
            vision=ctx.vision,
        )
        orders = OrdersPage(manager, ctx.orders, maintenance=ctx.maintenance)
        ai = AIPage(ctx.orders, settings_provider=ctx.settings.current)
        settings_page = SettingsPage(
            on_apply=_apply_settings,
            accounts=ctx.accounts,
            settings_service=ctx.settings,
            vision=ctx.vision,
            maintenance=ctx.maintenance,
        )

        pages: dict[str, ft.Control | None] = {
            "monitor": monitor.build(page),
            "orders": orders.build(page),
            "analysis": None,  # 首次切到该页时再聚合/制图，避免启动白屏
            "ai": ai.build(page),
            "settings": settings_page.build(page),
        }
        state["analysis_order_count"] = -1
        state["refresh_orders"] = orders.refresh

        def _show_orders() -> None:
            orders.refresh()

        def _show_analysis() -> None:
            count = ctx.orders.count()
            if pages["analysis"] is None or count != state["analysis_order_count"]:
                pages["analysis"] = build_analysis_page(
                    page, ctx.orders, branding_hub.branding
                )
                state["analysis_order_count"] = count

        def _show_ai() -> None:
            ai.on_show()

        def _show_settings() -> None:
            settings_page.on_show()

        _mount_shell(
            user_label="管理员 admin",
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.MONITOR_OUTLINED,
                    selected_icon=ft.Icons.MONITOR,
                    label="实时监控",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.RECEIPT_LONG_OUTLINED,
                    selected_icon=ft.Icons.RECEIPT_LONG,
                    label="订单管理",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.ANALYTICS_OUTLINED,
                    selected_icon=ft.Icons.ANALYTICS,
                    label="数据分析",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SMART_TOY_OUTLINED,
                    selected_icon=ft.Icons.SMART_TOY,
                    label="AI 报告",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label="设置",
                ),
            ],
            pages=pages,
            monitor=monitor,
            refresh_hooks={
                "orders": _show_orders,
                "analysis": _show_analysis,
                "ai": _show_ai,
                "settings": _show_settings,
            },
            brand_subscribers=[
                monitor.apply_branding,
                orders.apply_branding,
                ai.apply_branding,
                settings_page.apply_branding,
            ],
        )

    # ---------------- 车位用户外壳 ----------------
    def _build_spot_shell(spot_uid: str, spot_name: str) -> None:
        monitor = MonitorPage(
            manager,
            is_admin=False,
            spot_filter=spot_uid,
            settings_provider=ctx.settings.current,
            vision=ctx.vision,
        )
        orders = OrdersPage(manager, ctx.orders, spot_uid=spot_uid)

        pages: dict[str, ft.Control | None] = {
            "monitor": monitor.build(page),
            "orders": orders.build(page),
        }

        _mount_shell(
            user_label=f"车位 {spot_name}",
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.EV_STATION_OUTLINED,
                    selected_icon=ft.Icons.EV_STATION,
                    label="我的车位",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.RECEIPT_LONG_OUTLINED,
                    selected_icon=ft.Icons.RECEIPT_LONG,
                    label="我的订单",
                ),
            ],
            pages=pages,
            monitor=monitor,
            refresh_hooks={"orders": orders.refresh},
            brand_subscribers=[monitor.apply_branding, orders.apply_branding],
        )
        state["refresh_orders"] = orders.refresh

    # ---------------- 登录 / 退出 ----------------
    def _render_login() -> None:
        page.clean()
        page.add(build_login_view(page, branding_hub.branding, _attempt_login))
        try:
            page.update()
        except Exception:
            pass

    def _attempt_login(username: str, password: str) -> Optional[str]:
        user = ctx.accounts.authenticate(username, password)
        if user is None:
            return "用户名或密码错误，请重试"
        if not ctx.accounts.is_admin(user):
            # 车位用户用「车位名」登录，uid 才是订单/车位的真实身份
            spot_uid = str(user.get("spot_uid") or "")
            spot = ctx.accounts.spot(spot_uid) if spot_uid else None
            if spot is None:
                return "车位账号已失效，请联系管理员"
            state["user"] = user
            _build_spot_shell(str(spot["uid"]), str(spot["name"]))
            return None
        state["user"] = user
        _build_admin_shell()
        return None

    def _logout() -> None:
        state["shell"] = None
        state["user"] = None
        state["refresh_orders"] = None
        branding_hub.reset()
        page.title = branding_hub.branding.window_title()
        _render_login()

    # ---------------- 常驻采集循环 ----------------
    def _on_completed(orders) -> None:
        """自动结算回调：只更新界面提示，不参与计时。"""
        shell = state["shell"]
        if shell is None:
            return
        for order in orders:
            shell["monitor"].notify_auto_stop(order)

    def _on_tick() -> None:
        """每次 tick 后的界面刷新；最小化/切页/未登录时自动跳过重绘。"""
        shell = state["shell"]
        if shell is None:
            return
        _update_clock()
        # 车位被管理员删除后，车位用户的账号也随之失效，直接退回登录页
        user = state["user"] or {}
        spot_uid = user.get("spot_uid")
        if (
            not ctx.accounts.is_admin(user)
            and spot_uid
            and all(p.pile_id != str(spot_uid) for p in manager.piles)
        ):
            logger.info("车位 %s 已被删除，自动退出登录", spot_uid)
            _logout()
            return
        # visible=False 表示当前不在监控页：只记账不重绘，省 CPU
        if shell["monitor"].visible:
            shell["monitor"].refresh(page)

    runtime = ChargingRuntime(
        manager,
        interval_s=state["refresh_interval_s"],
        on_completed=_on_completed,
        on_tick=_on_tick,
    )

    async def _loops() -> None:
        """采集循环：与登录状态、页面切换都无关，充电持续计时。"""
        while True:
            # 间隔可在设置页调整，每轮同步一次
            runtime.interval_s = max(1.0, min(10.0, state["refresh_interval_s"]))
            runtime.tick_once()
            await asyncio.sleep(runtime.interval_s)

    # 开发/截图调试用：EVCHARGE_AUTOLOGIN=用户名（+ EVCHARGE_AUTOPASS）可跳过登录
    auto_login = os.environ.get("EVCHARGE_AUTOLOGIN", "").strip()
    if auto_login:
        error = _attempt_login(auto_login, os.environ.get("EVCHARGE_AUTOPASS", ""))
        if error:
            logger.warning("自动登录失败（%s），改为显示登录页", error)
            _render_login()
        else:
            logger.info("已按 EVCHARGE_AUTOLOGIN 自动登录：%s", auto_login)
    else:
        _render_login()
    page.run_task(_loops)
