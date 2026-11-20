# -*- coding: utf-8 -*-
"""修复 macOS 打包版 Flet 客户端的文件选择权限。

**问题**：`flet pack` 打包时会把内置 Flet 客户端改名/换图标后**重新 ad-hoc 签名**，
但它的签名命令没带权限声明（entitlements），于是原客户端自带的
``com.apple.security.files.user-selected.read-write`` 被丢掉了。
后果是打包版点「相册识别」弹出系统文件选择器时直接报：

    PlatformException(ENTITLEMENT_NOT_FOUND,
      Either the Read-Only or Read-Write entitlement is required for this action.)

**修法**：启动最早期（``ft.run`` 之前）检查缓存里的客户端
``~/.flet/client/flet-desktop-full-<版本>-<指纹>/<名字>.app``，
若缺该权限就用项目自带的 ``assets/macos_entitlements.plist`` 重新 ad-hoc 签名。
签名只做一次：已有权限时直接跳过，不额外耗时。

只影响 macOS；其它平台直接返回 ``None``。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 文件选择器需要的权限键
USER_SELECTED_ENTITLEMENT = "com.apple.security.files.user-selected.read-write"
SIGN_TIMEOUT_S = 120


def _entitlements_path() -> Optional[Path]:
    """项目自带的权限文件（随 assets 一起打包）。"""
    from evcharge.infrastructure.paths import ASSETS_DIR

    candidate = Path(ASSETS_DIR) / "macos_entitlements.plist"
    return candidate if candidate.exists() else None


def client_app_bundles(cache_dir: Path) -> list[Path]:
    """缓存目录下的客户端 .app（通常是 <产品名>.app）。"""
    if not cache_dir.is_dir():
        return []
    return sorted(p for p in cache_dir.glob("*.app") if p.is_dir())


def has_user_selected_entitlement(app_path: Path) -> Optional[bool]:
    """读取签名里的权限；读不到（如未签名）返回 None。"""
    if shutil.which("codesign") is None:
        return None
    try:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", "-", str(app_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        logger.debug("读取 %s 的签名信息失败", app_path, exc_info=True)
        return None
    output = (result.stdout or "") + (result.stderr or "")
    if not output.strip():
        return None
    return USER_SELECTED_ENTITLEMENT in output


def resign_with_entitlements(app_path: Path, plist: Path) -> bool:
    """用给定权限重新 ad-hoc 签名；成功返回 True。"""
    try:
        result = subprocess.run(
            [
                "codesign",
                "--force",
                "--deep",
                "--sign",
                "-",
                "--entitlements",
                str(plist),
                str(app_path),
            ],
            capture_output=True,
            text=True,
            timeout=SIGN_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        logger.warning("重新签名 %s 失败", app_path, exc_info=True)
        return False
    if result.returncode != 0:
        logger.warning(
            "重新签名 %s 失败（退出码 %s）：%s",
            app_path,
            result.returncode,
            (result.stderr or result.stdout or "").strip()[:300],
        )
        return False
    return True


def ensure_client_entitlements() -> Optional[str]:
    """确保 Flet 客户端具备文件选择权限。

    返回一句人类可读的状态（便于写日志）；不需要处理时返回 ``None``。
    """
    if sys.platform != "darwin":
        return None

    plist = _entitlements_path()
    if plist is None:
        logger.warning("未找到 assets/macos_entitlements.plist，跳过权限修复")
        return None

    try:
        from flet_desktop import ensure_client_cached
    except Exception:  # flet_desktop 缺失时无需处理
        return None

    try:
        # 本地已有缓存就直接用；打包版里客户端随包携带，解包也是本地的
        cache_dir = Path(ensure_client_cached())
    except Exception:
        logger.debug("定位 Flet 客户端缓存失败", exc_info=True)
        return None

    bundles = client_app_bundles(cache_dir)
    if not bundles:
        return None

    fixed: list[str] = []
    for app_path in bundles:
        status = has_user_selected_entitlement(app_path)
        if status is True:
            continue  # 已有权限，什么都不做
        if resign_with_entitlements(app_path, plist):
            if has_user_selected_entitlement(app_path) is True:
                fixed.append(app_path.name)
                logger.info("已为 Flet 客户端补回文件选择权限：%s", app_path)
            else:
                logger.warning("签名完成但权限未生效：%s", app_path)

    if fixed:
        return f"已修复 {len(fixed)} 个客户端的文件选择权限（相册识别可正常使用）"
    return None


__all__ = [
    "ensure_client_entitlements",
    "has_user_selected_entitlement",
    "resign_with_entitlements",
    "client_app_bundles",
    "USER_SELECTED_ENTITLEMENT",
]
