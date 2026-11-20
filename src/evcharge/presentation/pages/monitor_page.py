# -*- coding: utf-8 -*-
"""实时监控页：车位卡片 + 详情控制 + 实时功率曲线。

详情面板保留控件引用并逐帧更新，避免“只有卡片在跳、详情数值不动”。
只保留普通充电流程：用户上电、用户断电、达到目标 SOC 自动结算。

管理员可在此添加/删除车位；车位用户只能查看自己的车位并上电/下电。

上电支持两种方式：直接「上电」（随机模拟车牌），或用「拍照识别 / 相册识别」
从图片里读出真实车牌号再上电。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import flet as ft

from evcharge.application.session_manager import SessionManager
from evcharge.domain.charger import normalize_spot_name, suggest_spot_name
from evcharge.application.services import VisionService
from evcharge.domain.models import Order
from evcharge.presentation.components import theme
from evcharge.presentation.components.monitor_detail import MonitorDetail
from evcharge.presentation.components.monitor_wall import MonitorWall
from evcharge.presentation.components.plate_capture import PlateCapture

logger = logging.getLogger(__name__)

def _spec_text_full(pile) -> str:
    """完整桩型说明（仅删除确认等需要说清规格的地方使用）。"""
    charger = pile.charger
    if charger.is_dc:
        return f"直流快充 {charger.nominal_power_kw:.0f}kW"
    return "交流慢充 7kW"

class MonitorPage(MonitorWall, MonitorDetail, PlateCapture):
    """站点侧实时监控：车位卡片墙 + 详情/控制 + 功率曲线。

    为控制单文件体积，按关注点拆到 ``components/`` 下的三个 Mixin：

    - :class:`MonitorWall` —— 卡片墙、分组、状态汇总、卡片刷新；
    - :class:`MonitorDetail` —— 详情面板、SOC/指标刷新、功率曲线、账单弹窗；
    - :class:`PlateCapture` —— 拍照/相册识别车牌并上电。

    三者都直接操作 ``self``（页面实例）的属性，并共享同一份页面状态。
    """

    def __init__(
        self,
        manager: SessionManager,
        is_admin: bool = False,
        on_add_spot: Optional[Callable[[str, str, float], None]] = None,
        on_delete_spot: Optional[Callable[[str], None]] = None,
        spot_filter: Optional[str] = None,
        settings_provider: Optional[Callable[[], object]] = None,
        vision: VisionService | None = None,
    ) -> None:
        self.manager = manager
        # 设置与识别能力都由外部注入（组合根负责装配），页面不碰基础设施
        self.settings_provider = settings_provider
        self.vision = vision
        self.is_admin = is_admin
        self.on_add_spot = on_add_spot
        self.on_delete_spot = on_delete_spot
        # 车位用户只能看到自己的车位
        self.spot_filter = str(spot_filter) if spot_filter else None
        visible = self._visible_piles()
        self.selected_pile_id: str | int | None = (
            visible[0].pile_id if visible else None
        )
        self.page: ft.Page | None = None
        self.visible = True
        self.cards_grid: ft.Column | None = None

        self.eyebrow = theme.eyebrow_label("")
        self.status_banner = ft.Text("系统就绪", size=12, color=theme.MUTED)
        self.cards_row: list[ft.Container] = []
        self.card_status: dict[str, ft.Text] = {}
        self.card_power: dict[str, ft.Text] = {}
        self.card_energy: dict[str, ft.Text] = {}
        self.card_soc: dict[str, ft.Container] = {}
        self.card_pill: dict[str, ft.Container] = {}
        self.summary_values: dict[str, ft.Text] = {}
        self.count_text: ft.Text | None = None
        self.wall_container: ft.Container | None = None

        # 详情面板
        self.detail_col = ft.Column(spacing=10, expand=True)
        self.detail_title = ft.Text(
            "", size=15, weight=ft.FontWeight.BOLD, color=theme.TEXT
        )
        self.detail_status = ft.Text("", size=12)
        self.detail_session = ft.Text("", size=13, color=theme.TEXT)
        self.detail_start = ft.Text("", size=10, color=theme.MUTED)
        self.soc_text = ft.Text(
            "SOC —", size=12, color=theme.CYAN, font_family=theme.MONO
        )
        self.soc_bar = ft.ProgressBar(
            value=0,
            bar_height=8,
            color=theme.CYAN,
            bgcolor=theme.LINE,
            border_radius=4,
        )
        self.metric_values = {
            key: ft.Text(
                "—",
                size=14,
                weight=ft.FontWeight.BOLD,
                color=theme.TEXT,
                font_family=theme.MONO,
            )
            for key in ("电压", "电流", "功率", "本次电量")
        }
        # ---------- 车牌识别（拍照 / 相册） ----------
        self.plate_hint = ft.Text("", size=10, color=theme.DIM, max_lines=2)
        self.file_picker: ft.FilePicker | None = None
        self.gallery_btn = ft.OutlinedButton(
            "相册识别",
            icon=ft.Icons.PHOTO_LIBRARY_OUTLINED,
            tooltip="选一张车牌照片，识别后直接上电",
            on_click=lambda e: self._start_gallery_task(),
        )
        self.camera_btn = ft.OutlinedButton(
            "拍照识别",
            icon=ft.Icons.PHOTO_CAMERA_OUTLINED,
            tooltip="调用本机摄像头拍照并识别车牌",
            on_click=lambda e: self._start_camera_task(),
        )

        self.start_btn = ft.FilledButton(
            "上电",
            icon=ft.Icons.PLAY_ARROW,
            bgcolor=theme.AMBER,
            color=theme.ON_ACCENT,
            on_click=lambda e: self._on_start(),
        )
        self.stop_btn = ft.FilledButton(
            "断电",
            icon=ft.Icons.STOP,
            bgcolor=theme.RED,
            color=theme.ON_ACCENT,
            tooltip="断电会立即结算并结束本次充电" if not is_admin else "管理员强制下电：立即结算并结束该车位的充电",
            on_click=lambda e: self._on_stop(),
        )
        self.delete_btn = ft.OutlinedButton(
            "删除",
            icon=ft.Icons.DELETE_OUTLINE,
            on_click=lambda e: self._on_delete_spot(),
        )
        self.add_spot_btn = ft.FilledButton(
            "添加车位",
            icon=ft.Icons.ADD,
            bgcolor=theme.ACCENT,
            color=theme.ON_ACCENT,
            on_click=lambda e: self._open_add_dialog(),
        )

        self.chart_container = theme.card(
            theme.empty_state(
                "暂无实时曲线",
                "上电后自动绘制",
                icon=ft.Icons.SHOW_CHART,
            ),
            padding=12,
            height=230,
        )


    def build(self, page: ft.Page) -> ft.Control:
        self.page = page
        self._mount_file_picker(page)
        self._build_detail()

        self.count_text = ft.Text(
            f"{len(self._visible_piles())} 个车位"
            if self.is_admin
            else f"车位 {self.spot_filter}",
            size=10,
            color=theme.MUTED,
        )
        header = theme.page_header(
            self.eyebrow,
            "实时监控",
            "",
            trailing=ft.Row(
                [
                    ft.Container(
                        width=7, height=7, border_radius=4, bgcolor=theme.GREEN
                    ),
                    self.count_text,
                ]
                + ([self.add_spot_btn] if self.is_admin else []),
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self.summary_values.clear()
        summary = ft.Row(
            [
                self._summary_item("空闲", theme.MUTED, "0"),
                self._summary_item("充电中", theme.GREEN, "0"),
                self._summary_item("合计功率", theme.CYAN, "0.0 kW"),
            ],
            spacing=18,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        status_card = theme.card(
            ft.Row(
                [
                    ft.Row(
                        [theme.dot(theme.GREEN, 7), self.status_banner],
                        spacing=10,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(expand=True),
                    summary,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            bgcolor=theme.SURFACE_2,
        )

        self.wall_container = ft.Container(content=self._build_wall(), expand=True)
        return ft.Column(
            [
                header,
                status_card,
                ft.Row(
                    [
                        self.wall_container,
                        theme.card(self.detail_col, width=396, padding=18),
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                self.chart_container,
            ],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )


    def _select(self, pile_id: str | int) -> None:
        self.selected_pile_id = pile_id
        self.refresh(self.page)

    # ---------------- 管理员车位管理 ----------------
    def reload(self, page: ft.Page | None = None) -> None:
        """车位增删后重建卡片墙与详情面板。"""
        if page is not None:
            self.page = page
        if self.page is None:
            return
        visible = self._visible_piles()
        if visible and (
            self.selected_pile_id is None
            or all(
                str(p.pile_id) != str(self.selected_pile_id) for p in visible
            )
        ):
            self.selected_pile_id = visible[0].pile_id
        if not visible:
            self.selected_pile_id = None
        if self.wall_container is not None:
            self.wall_container.content = self._build_wall()
        if self.count_text is not None:
            self.count_text.value = (
                f"{len(visible)} 个车位"
                if self.is_admin
                else f"车位 {self.spot_filter}"
            )
        self._build_detail()
        self.refresh(self.page)

    def _suggest_spot_name(self) -> str:
        """给出下一个可用车位名（A001 / A002 …，跳过已被占用的名字）。"""
        return suggest_spot_name([p.name for p in self.manager.piles])

    def _open_add_dialog(self) -> None:
        if self.page is None:
            return
        spot_input = ft.TextField(
            label="车位名称",
            hint_text="例如 A001",
            value=self._suggest_spot_name(),
            width=220,
        )
        type_dropdown = ft.Dropdown(
            label="充电类型",
            width=160,
            options=[
                ft.dropdown.Option("fast", "直流快充"),
                ft.dropdown.Option("slow", "交流慢充"),
            ],
            value="fast",
        )
        power_dropdown = ft.Dropdown(
            label="直流功率等级",
            width=160,
            options=[
                ft.dropdown.Option("60", "60 kW"),
                ft.dropdown.Option("120", "120 kW"),
                ft.dropdown.Option("240", "240 kW"),
            ],
            value="120",
        )

        def on_type_changed(e):
            power_dropdown.visible = type_dropdown.value == "fast"
            try:
                power_dropdown.update()
            except RuntimeError:  # 控件尚未挂载到页面时忽略（如无窗口测试）
                pass

        type_dropdown.on_change = on_type_changed
        power_dropdown.visible = type_dropdown.value == "fast"

        def submit(e):
            if self.page is None:
                return
            name = normalize_spot_name(spot_input.value)
            if not name:
                spot_input.error_text = "请输入车位名称"
                spot_input.update()
                return
            if any(p.name.casefold() == name.casefold() for p in self.manager.piles):
                spot_input.error_text = "车位名已存在"
                spot_input.update()
                return
            if self.on_add_spot is not None:
                power = (
                    float(power_dropdown.value)
                    if type_dropdown.value == "fast"
                    else None
                )
                try:
                    self.on_add_spot(name, type_dropdown.value, power)
                except Exception as exc:  # 创建失败时保留弹窗并提示，避免“点了没反应”
                    logger.exception("新增车位失败")
                    spot_input.error_text = f"创建失败：{exc}"
                    try:
                        spot_input.update()
                    except Exception:
                        pass
                    return
            self._close_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("添加充电车位"),
            content=ft.Column(
                [
                    ft.Text(
                        "创建后自动生成同名登录账号（默认密码 admin）",
                        size=12,
                        color=theme.MUTED,
                    ),
                    spot_input,
                    ft.Row([type_dropdown, power_dropdown], spacing=10),
                ],
                spacing=12,
                tight=True,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog(dlg)),
                ft.FilledButton("创建", on_click=submit),
            ],
        )
        # Flet 1.1：必须用 show_dialog 才会显示（直接赋值 page.dialog 无效）
        self.page.show_dialog(dlg)

    def _close_dialog(self, dlg: ft.AlertDialog) -> None:
        if self.page is not None:
            self.page.pop_dialog()

    def _on_delete_spot(self) -> None:
        if self.page is None or self.selected_pile_id is None:
            return
        pile = self.manager.pile(str(self.selected_pile_id))

        def confirm(e):
            if self.on_delete_spot is not None:
                self.on_delete_spot(pile.uid)
            self._close_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"删除车位 {pile.name}？"),
            content=ft.Text(
                f"{pile.name}（{_spec_text_full(pile)}）将被删除，不可撤销：\n"
                "· 充电中会先强制结算\n"
                "· 登录账号一并删除\n"
                "· 历史订单保留",
                size=12,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog(dlg)),
                ft.FilledButton(
                    "确认删除",
                    style=ft.ButtonStyle(bgcolor=theme.RED, color=theme.ON_ACCENT),
                    on_click=confirm,
                ),
            ],
        )
        self.page.show_dialog(dlg)

    def apply_branding(self, branding) -> None:
        """设置保存后更新页面眉标与强调色。"""
        self.eyebrow.value = branding.eyebrow("实时监盘")
        self.eyebrow.color = branding.accent

    def on_show(self, page: ft.Page | None = None) -> None:
        """切换到本页时补一次实时数据。"""
        self.visible = True
        self.refresh(page)

    def on_hide(self) -> None:
        """离开本页时停止重绘图表，避免后台空耗。"""
        self.visible = False

    # ---------------- 交互 ----------------
    def _on_start(self) -> None:
        if self.selected_pile_id is None:
            return
        try:
            session = self.manager.start_session(self.selected_pile_id)
            profile = session.profile
            self._set_status(
                f"✅ {session.plate}（{profile.model}，"
                f"{session.battery_kwh:.1f} kWh）"
                f"已在 {self.manager.pile(self.selected_pile_id).name} 开始充电："
                f"SOC {session.start_soc:.0f}%→{session.target_soc:.0f}%，"
                f"{session.charger.charger_type.value}",
                theme.GREEN,
            )
        except RuntimeError as exc:
            self._set_status(f"⚠ {exc}", theme.AMBER)
        self.refresh(self.page)

    def _on_stop(self) -> None:
        if self.selected_pile_id is None:
            return
        try:
            order = self.manager.stop_session(self.selected_pile_id)
        except RuntimeError as exc:
            self._set_status(f"⚠ {exc}", theme.AMBER)
        else:
            periods = "、".join(
                f"{p.period} {p.energy_kwh:.1f} 度" for p in order.bill.periods
            ) or "无"
            self._set_status(
                f"🧾 结算完成：{order.plate}（{order.vehicle_model}）"
                f"充电 {order.energy_kwh:.1f} 度，"
                f"SOC {order.start_soc:.0f}%→{order.end_soc:.0f}%，"
                f"电费 ¥{order.bill.energy_fee:.2f} + "
                f"服务费 ¥{order.bill.service_fee:.2f} = "
                f"合计 ¥{order.total_fee:.2f}（{periods}）",
                theme.CYAN,
            )
            self._show_bill(order)
        self.refresh(self.page)

    def notify_spot_created(self, name: str) -> None:
        self._set_status(
            f"已添加车位 {name}（单枪）；车位账号 {name} 已创建，默认密码 admin。",
            theme.GREEN,
        )

    def notify_spot_deleted(self, name: str) -> None:
        self._set_status(
            f"已删除车位 {name}：账号已停用，历史订单保留。",
            theme.RED,
        )

    def notify_auto_stop(self, order: Order) -> None:
        """由主循环在车位达到目标 SOC 自动结算时调用。"""
        self._set_status(
            f"🔌 车位 {order.spot_name or order.pile_id} · {order.plate}（{order.vehicle_model}）"
            f"已充到目标 SOC {order.end_soc:.0f}%，"
            f"自动结算 {order.energy_kwh:.1f} 度 / ¥{order.total_fee:.2f}",
            theme.CYAN,
        )

    def _set_status(self, msg: str, color) -> None:
        self.status_banner.value = msg
        self.status_banner.color = color

    # ---------------- 刷新 ----------------
    def refresh(self, page: ft.Page | None = None) -> None:
        self._refresh_cards()
        self._refresh_summary()
        self._refresh_detail()
        if self.visible:
            self._refresh_chart()
        if page is not None:
            try:
                page.update()
            except Exception:
                pass
