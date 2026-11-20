# -*- coding: utf-8 -*-
"""国网高速服务区充电监控与计费系统 — 程序入口。

运行方式（安装后）：
    pip install -e .
    evcharge            # 或 python -m evcharge

首次运行建议先生成历史数据：
    python scripts/gen_history.py --reset

启动流程（打包后也能立刻看到反馈）：

1. ``_early_setup()``：定好数据目录、日志文件、matplotlib 缓存目录 —— 只依赖轻量模块；
2. ``ft.run`` 起窗口，先画一张「正在启动，请稍候…」的启动页；
   （macOS 上还会先补一次 Flet 客户端的文件选择权限，见 infrastructure.macos_client）
3. 后台任务里再导入并构建真正的界面，完成后覆盖启动页；
4. 构建失败则显示错误页 + 日志路径（打包后没有控制台，必须显示在窗口里）。
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional, Tuple

import flet as ft

logger = logging.getLogger("charging")

# 启动页至少显示这么久，避免一闪而过（感知上更稳）
SPLASH_MIN_SECONDS = 0.6


def _early_setup() -> Tuple[Path, Path, Path]:
    """初始化数据目录、日志与 matplotlib 缓存。

    必须在导入 pandas / matplotlib 之前调用：``MPLCONFIGDIR`` 决定字体缓存位置，
    指到持久目录后，第二次启动就不用再重建缓存（重建要好几秒）。
    """
    from evcharge.infrastructure.paths import ASSETS_DIR, DATA_DIR

    data_dir = Path(DATA_DIR)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # 字体缓存持久化，避免每次启动重建（这是打包后"启动慢"的主因之一）
    mpl_cache = data_dir / "mpl-cache"
    try:
        mpl_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache))
    except OSError:
        pass

    _setup_logging(data_dir)

    # macOS 打包版：补回 Flet 客户端的文件选择权限（否则相册识别报 ENTITLEMENT_NOT_FOUND）
    try:
        from evcharge.infrastructure.macos_client import ensure_client_entitlements

        status = ensure_client_entitlements()
        if status:
            logger.info(status)
    except Exception:  # 修不了也不能拦住启动
        logger.warning("检查 Flet 客户端权限时出错", exc_info=True)

    return data_dir, Path(ASSETS_DIR), data_dir / "evcharge.log"


def _setup_logging(data_dir: Path, log_name: str = "evcharge.log") -> None:
    """控制台 + 滚动文件双通道；打包后没有控制台时，日志是唯一排障入口。"""
    root = logging.getLogger()
    if root.handlers:  # 避免重复初始化
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            data_dir / log_name,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        logger.warning("无法写入日志文件，仅输出到控制台", exc_info=True)


def _show_splash(page: ft.Page, title: str = "", subtitle: str = "") -> None:
    """画启动页（只 import 轻量模块，保证立刻可见）。"""
    from evcharge.presentation.components import theme
    from evcharge.presentation.pages.splash import build_splash_view

    page.title = title or "正在启动…"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = theme.BG
    page.padding = 0
    page.spacing = 0
    page.window.width = 1360
    page.window.height = 860
    page.window.min_width = 1240
    page.window.min_height = 720
    page.clean()
    page.add(build_splash_view(title=title, subtitle=subtitle))
    try:
        page.update()
    except Exception:  # 启动页画不出来也不该阻断真正启动
        logger.debug("启动页渲染失败", exc_info=True)


def _show_boot_error(page: ft.Page, message: str, log_path: Optional[Path]) -> None:
    from evcharge.presentation.pages.splash import build_boot_error_view

    page.clean()
    page.add(build_boot_error_view(message, str(log_path) if log_path else None))
    try:
        page.update()
    except Exception:
        logger.debug("错误页渲染失败", exc_info=True)


def main() -> None:
    """Flet 应用入口：先出启动页，再在后台装配界面。"""
    data_dir, assets_dir, log_path = _early_setup()
    logger.info("启动中：数据目录 %s", data_dir)

    def _app(page: ft.Page) -> None:
        _show_splash(page)
        page.run_task(_boot, page, log_path)

    ft.run(_app, assets_dir=str(assets_dir))


async def _boot(page: ft.Page, log_path: Optional[Path] = None) -> None:
    """在启动页之后装配真正的界面，并保证启动页至少停留一小会儿。"""
    started = asyncio.get_event_loop().time()
    try:
        # 重模块（pandas / matplotlib / Flet 页面）都在这里才导入
        from evcharge.bootstrap import build_context
        from evcharge.presentation.app import build_app

        context = await asyncio.to_thread(build_context)
        build_app(page, context)
        logger.info("界面已就绪，启动耗时 %.2fs", _elapsed(started))
    except Exception as exc:  # 打包后没有控制台，必须把原因显示在窗口里
        logger.exception("启动失败")
        _show_boot_error(page, f"{type(exc).__name__}: {exc}", log_path)
        return

    # 启动得很快时也让启动页停留一小会儿，避免闪一下
    remaining = SPLASH_MIN_SECONDS - _elapsed(started)
    if remaining > 0:
        await asyncio.sleep(remaining)


def _elapsed(started: float) -> float:
    try:
        return asyncio.get_event_loop().time() - started
    except RuntimeError:
        return SPLASH_MIN_SECONDS


if __name__ == "__main__":
    main()
