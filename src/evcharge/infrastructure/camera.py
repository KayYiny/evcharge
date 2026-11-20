# -*- coding: utf-8 -*-
"""调用本机摄像头拍照（evcharge.infrastructure.camera）。

Desktop 端没有跨平台的「实时预览 + 快门」标准接口，所以这里走最小可用路径：
调用系统自带的拍照工具把照片存成文件，再交给车牌识别。

- macOS：优先 ``imagesnap``（``brew install imagesnap``），没装则打开「拍照台」；
- Windows：打开系统「相机」应用；
- Linux：优先 ``fswebcam``，否则打开 ``cheese``。

返回 ``(图片路径, 提示文本)``：
- 路径不为 None 表示已经拍到照片，可以直接送去识别；
- 路径为 None 表示只打开了拍照应用，用户拍完需要点「相册识别」选那张照片。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Tuple

CAPTURE_TIMEOUT_S = 25


def _capture_macos(dest: Path) -> Tuple[Path | None, str]:
    if shutil.which("imagesnap"):
        try:
            subprocess.run(
                ["imagesnap", "-q", str(dest)],
                check=True,
                timeout=CAPTURE_TIMEOUT_S,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if dest.exists() and dest.stat().st_size > 0:
                return dest, "已用摄像头拍照"
        except (subprocess.SubprocessError, OSError):
            pass  # 落到打开拍照台的兜底分支

    try:
        subprocess.Popen(["open", "-a", "Photo Booth"])
    except OSError:
        return None, "没有可用的摄像头工具：可执行 `brew install imagesnap` 后重试"
    return None, "已打开「拍照台」：拍好后保存图片，再点「相册识别」选择它"


def _capture_windows(dest: Path) -> Tuple[Path | None, str]:
    # Windows 没有稳定的命令行抓图工具，直接拉起系统相机应用
    try:
        subprocess.Popen(["cmd", "/c", "start", "microsoft.windows.camera:"])
    except OSError:
        return None, "无法启动系统相机：请手动拍照后用「相册识别」选择图片"
    return None, "已打开系统「相机」：拍好后保存图片，再点「相册识别」选择它"


def _capture_linux(dest: Path) -> Tuple[Path | None, str]:
    if shutil.which("fswebcam"):
        try:
            subprocess.run(
                ["fswebcam", "-q", "--no-banner", str(dest)],
                check=True,
                timeout=CAPTURE_TIMEOUT_S,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if dest.exists() and dest.stat().st_size > 0:
                return dest, "已用摄像头拍照"
        except (subprocess.SubprocessError, OSError):
            pass

    if shutil.which("cheese"):
        try:
            subprocess.Popen(["cheese"])
        except OSError:
            pass
        return None, "已打开「Cheese」：拍好后保存图片，再点「相册识别」选择它"
    return None, "没有可用的摄像头工具：可执行 `sudo apt install fswebcam` 后重试"


def capture_photo(dest_dir: str | Path | None = None) -> Tuple[Path | None, str]:
    """拍照并返回 ``(图片路径或 None, 给用户看的提示文本)``。"""
    folder = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path(tempfile.gettempdir())
    dest = folder / "evcharge_camera.jpg"

    if sys.platform == "darwin":
        return _capture_macos(dest)
    if sys.platform.startswith("win"):
        return _capture_windows(dest)
    return _capture_linux(dest)


__all__ = ["capture_photo"]
