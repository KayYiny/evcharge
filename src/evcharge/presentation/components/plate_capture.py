# -*- coding: utf-8 -*-
"""plate_capture：车牌识别上电（拍照 / 相册）。


把手持设备拍照、文件选择、识别与上电串成一条链路；识别失败只提示，
不会产生订单，用户仍可用「上电」随机车牌。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

import flet as ft

from evcharge.application.errors import PlateRecognitionError
from evcharge.presentation.components import theme

logger = logging.getLogger(__name__)


class PlateCapture:
    # ---------------- 车牌识别上电 ----------------
    def _mount_file_picker(self, page: ft.Page) -> None:
        """把 FilePicker 注册成页面服务（Flet 1.x 的服务需挂在 page.services）。

        打包后若挂载位置不可用，这里会抛出异常，由调用方转成界面提示 ——
        不能让 FilePicker 拿不到就静默失败（用户会看到「点了没反应」）。
        """
        if self.file_picker is not None:
            return

        picker = ft.FilePicker()
        mounted = False
        for attr in ("services", "overlay"):
            container = getattr(page, attr, None)
            if container is None:
                continue
            try:
                container.append(picker)
                mounted = True
                logger.debug("FilePicker 已挂载到 page.%s", attr)
                break
            except Exception:  # 换下一个位置
                logger.debug("FilePicker 挂载到 page.%s 失败", attr, exc_info=True)
        if not mounted:
            raise RuntimeError(
                "无法挂载文件选择器（page.services / page.overlay 都不可用）"
            )
        self.file_picker = picker

    async def _focus_window(self) -> None:
        """把窗口提到最前：打包后的原生文件对话框有时会藏在窗口后面。"""
        try:
            await self.page.window.to_front()
        except Exception:  # 某些平台/版本不支持，忽略即可
            logger.debug("窗口置前失败", exc_info=True)

    @staticmethod
    def _materialize_selection(picked) -> Optional[Path]:
        """把用户选中的文件变成可读的本地路径。

        Flet 文档明确：原生平台的 ``path`` **可能为 None**（例如 macOS 打包后
        走系统的安全文件选择器，只给内容不暴露路径）。这种情况用 ``bytes``
        落到临时文件，识别流程就能照常走。
        """
        raw_path = getattr(picked, "path", None)
        # 只有真正的字符串/路径对象才当路径用（其它类型一律忽略，避免误用）
        if isinstance(raw_path, (str, Path)) and str(raw_path).strip():
            candidate = Path(raw_path)
            if candidate.exists():
                return candidate
            logger.warning("选择器返回的路径不存在：%s", raw_path)

        data = getattr(picked, "bytes", None)
        if isinstance(data, (bytes, bytearray)) and data:
            name = getattr(picked, "name", "") or "plate.jpg"
            suffix = Path(name).suffix or ".jpg"
            target = Path(tempfile.gettempdir()) / f"evcharge_plate{suffix}"
            try:
                target.write_bytes(data)
                logger.info("选择器未给路径，已把 %d 字节写入 %s", len(data), target)
                return target
            except OSError:
                logger.exception("写入临时图片失败")
                return None
        return None

    def _set_plate_hint(
        self, text: str, color: str = theme.DIM, banner: bool = False
    ) -> None:
        """更新详情区提示；banner=True 时同时推到顶部状态条。

        顶部状态条始终可见，识别失败这类关键反馈必须让它露出来，
        否则用户点完按钮会觉得「没反应」。
        """
        self.plate_hint.value = text
        self.plate_hint.color = color
        if banner:
            self._set_status(text, color)
        if self.page is not None:
            try:
                self.page.update()
            except Exception:
                pass

    def _start_gallery_task(self) -> None:
        if self.page is None:
            return
        self.page.run_task(self._gallery_task)

    def _start_camera_task(self) -> None:
        if self.page is None:
            return
        self.page.run_task(self._camera_task)

    async def _gallery_task(self) -> None:
        """从相册/文件里选一张图，识别车牌后直接上电。"""
        if self.page is None:
            return

        # 先给反馈：文件对话框可能藏在窗口后面，用户会以为「点了没反应」
        self._set_plate_hint("📂 请在弹出的窗口中选择车牌照片…", theme.MUTED, banner=True)
        try:
            self._mount_file_picker(self.page)
        except Exception as exc:
            logger.exception("文件选择器挂载失败")
            self._set_plate_hint(
                f"❌ 无法打开相册：{exc}（可改用「拍照识别」或直接「上电」）",
                theme.RED,
                banner=True,
            )
            return

        await self._focus_window()
        try:
            files = await self.file_picker.pick_files(
                dialog_title="选择车牌照片",
                file_type=ft.FilePickerFileType.IMAGE,
                allowed_extensions=["jpg", "jpeg", "png", "bmp", "webp"],
                # 关键：原生平台可能只返回内容、不返回路径，必须同时要 bytes
                with_data=True,
            )
        except Exception as exc:
            logger.exception("打开文件对话框失败")
            self._set_plate_hint(
                f"❌ 打开相册失败：{type(exc).__name__}: {exc}",
                theme.RED,
                banner=True,
            )
            return

        if not files:
            self._set_plate_hint("已取消选择照片", theme.MUTED)
            return

        path = self._materialize_selection(files[0])
        if path is None:
            self._set_plate_hint(
                "❌ 读不到所选照片（系统没有返回文件内容），请换一张或改用「拍照识别」",
                theme.RED,
                banner=True,
            )
            return
        await self._recognize_and_start(str(path))

    async def _camera_task(self) -> None:
        """调用系统相机拍照；拍到了就直接识别，只打开应用则提示改用相册。"""
        self._set_plate_hint("📷 正在调用摄像头…", theme.MUTED)
        if self.vision is None:
            self._set_plate_hint(
                "⚠ 当前未启用车牌识别服务，可直接点「上电」用随机车牌",
                theme.AMBER,
                banner=True,
            )
            return
        path, message = await asyncio.to_thread(self.vision.capture)
        if path is None:
            self._set_plate_hint(f"ℹ {message}", theme.MUTED)
            return
        await self._recognize_and_start(path)

    async def _recognize_and_start(self, image_path: str) -> None:
        """识别图片里的车牌，成功后用该车牌上电。"""
        if self.selected_pile_id is None:
            self._set_plate_hint("⚠ 请先选择一个车位", theme.AMBER)
            return

        if self.vision is None or self.settings_provider is None:
            self._set_plate_hint(
                "⚠ 当前未启用车牌识别服务；可直接点「上电」用随机车牌",
                theme.AMBER,
                banner=True,
            )
            return

        settings = self.settings_provider()
        available, reason = self.vision.available(settings)
        if not available:
            self._set_plate_hint(
                f"⚠ {reason}；或直接用「上电」随机车牌",
                theme.AMBER,
                banner=True,
            )
            return

        self._set_plate_hint("🔍 正在识别车牌…", theme.MUTED)
        try:
            plate, _raw = await asyncio.to_thread(
                self.vision.recognize, image_path, settings
            )
        except PlateRecognitionError as exc:
            self._set_plate_hint(
                f"❌ {exc}（可直接点「上电」用随机车牌）", theme.RED, banner=True
            )
            return
        except Exception as exc:  # 兜底：识别异常不影响正常上电流程
            logger.exception("车牌识别失败：%s", exc)
            self._set_plate_hint(f"❌ 识别失败：{exc}", theme.RED, banner=True)
            return

        self._set_plate_hint(f"✅ 识别成功：{plate}", theme.GREEN)
        self._start_with_plate(plate)

    def _start_with_plate(self, plate: str) -> None:
        """用指定车牌上电（识别成功后的落地动作）。"""
        if self.selected_pile_id is None:
            return
        try:
            session = self.manager.start_session(self.selected_pile_id, plate=plate)
        except RuntimeError as exc:
            self._set_plate_hint(f"⚠ {exc}", theme.AMBER)
            return
        profile = session.profile
        self._set_status(
            f"🚗 车牌识别上电：{session.plate}（{profile.model}，"
            f"{session.battery_kwh:.1f} kWh）"
            f"已在 {self.manager.pile(self.selected_pile_id).name} 开始充电："
            f"SOC {session.start_soc:.0f}%→{session.target_soc:.0f}%",
            theme.GREEN,
        )
        self.refresh(self.page)
