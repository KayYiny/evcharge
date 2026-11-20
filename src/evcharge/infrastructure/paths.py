# -*- coding: utf-8 -*-
"""项目路径常量（唯一可信来源）。

目录约定（源码运行）::

    <repo>/                             项目根目录 PROJECT_ROOT
    ├── data/                           运行期数据（已 gitignore）
    ├── assets/                         界面静态资源（图标等，随包提交）
    ├── docs/  scripts/  tests/
    └── src/                            源码根 SRC_DIR
        └── evcharge/                   应用包 PACKAGE_DIR
            ├── domain/  application/  infrastructure/  presentation/
            └── infrastructure/paths.py 本文件

打包后（PyInstaller / flet pack）目录结构不同，这里统一抹平：

- **随包资源**（图标等）在 ``_MEIPASS/assets``，属于只读区；
- **运行期数据**（数据库、设置、导出）必须写到用户可写目录：
  优先 ``FLET_APP_STORAGE_DATA``（Flet 提供），否则用系统用户数据目录。

这样同一份代码在开发、打包两种形态下都能找到正确位置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "evcharge"

# src/evcharge/infrastructure
INFRA_DIR: Path = Path(__file__).resolve().parent
# src/evcharge
PACKAGE_DIR: Path = INFRA_DIR.parent
# src（打包后为 _MEIPASS）
SRC_DIR: Path = PACKAGE_DIR.parent
# 仓库根目录（打包后为 _MEIPASS 的上一级，仅用于显示，不写文件）
PROJECT_ROOT: Path = SRC_DIR.parent


def is_frozen() -> bool:
    """是否运行在打包后的程序里（PyInstaller / flet pack）。"""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """随包资源根目录。

    - 打包后：``sys._MEIPASS``（PyInstaller 解包目录）；
    - 源码运行：仓库根目录。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return PROJECT_ROOT


def _user_data_dir() -> Path:
    """系统用户数据目录（程序目录不可写时的兜底）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def _data_dir() -> Path:
    """运行期数据目录。

    优先级：``FLET_APP_STORAGE_DATA`` → 打包后用户数据目录 → 项目内 ``data/``。
    """
    candidates: list[Path] = []
    override = os.environ.get("FLET_APP_STORAGE_DATA", "").strip()
    if override:
        candidates.append(Path(override))
    if is_frozen():
        candidates.append(_user_data_dir())
    candidates.append(PROJECT_ROOT / "data")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return PROJECT_ROOT / "data"


DATA_DIR: Path = _data_dir()
ASSETS_DIR: Path = bundle_root() / "assets"

SETTINGS_PATH: Path = DATA_DIR / "settings.json"
LEGACY_AI_SETTINGS_PATH: Path = DATA_DIR / "ai_settings.json"
DEFAULT_DB_PATH: Path = DATA_DIR / "charging.db"
DEFAULT_EXPORT_PATH: Path = DATA_DIR / "orders_export.csv"


def ensure_data_dir() -> Path:
    """确保数据目录存在并返回其路径（写库/导出前调用）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


__all__ = [
    "INFRA_DIR",
    "PACKAGE_DIR",
    "SRC_DIR",
    "PROJECT_ROOT",
    "DATA_DIR",
    "ASSETS_DIR",
    "SETTINGS_PATH",
    "LEGACY_AI_SETTINGS_PATH",
    "DEFAULT_DB_PATH",
    "DEFAULT_EXPORT_PATH",
    "is_frozen",
    "bundle_root",
    "ensure_data_dir",
]
