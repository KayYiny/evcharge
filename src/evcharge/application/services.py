# -*- coding: utf-8 -*-
"""应用层用例服务（evcharge.application.services）。

表现层只依赖这里的服务对象，不再直接 import ``infrastructure``：

- :class:`OrderService` —— 读订单、读车位名、导出 CSV；
- :class:`AccountService` —— 登录校验、车位账号、改密码；
- :class:`SettingsService` —— 读取/保存设置、把电价档注入计费引擎；
- :class:`VisionService` —— 车牌识别（拍照/相册），复用 AI 配置；
- :class:`MaintenanceService` —— 数据备份 / 恢复 / 清空 / CSV 导入；
- :class:`AppContext` —— 把上面这些 + ``SessionManager`` 打成一个包，
  由组合根 ``evcharge.bootstrap`` 组装后传给 ``build_app``。

服务不持有 Flet 控件，也不打印日志，便于单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from evcharge.application.ports import (
    AccountRepository,
    OrderRepository,
    SpotRepository,
)
from evcharge.application.session_manager import SessionManager

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_NAME = "orders_export.csv"


# ----------------------------------------------------------------------
# 订单
# ----------------------------------------------------------------------
class OrderService:
    """订单读取与导出（表现层唯一入口）。"""

    def __init__(
        self,
        orders: OrderRepository,
        export_func: Callable[[List[dict], str | Path], Path],
        default_export_path: Path,
        export_name: str = DEFAULT_EXPORT_NAME,
    ) -> None:
        self._orders = orders
        self._export = export_func
        self.default_export_path = Path(default_export_path)
        self.export_name = export_name

    def list_orders(
        self, spot_uid: Optional[str] = None, limit: Optional[int] = None
    ) -> List[dict]:
        return self._orders.load_orders(limit=limit, spot_uid=spot_uid)

    def count(self) -> int:
        return self._orders.order_count()

    def spot_map(self) -> dict:
        """车位 ``uid -> 展示名`` 映射（订单页筛选项）。"""
        result: dict = {}
        for row in self._orders.load_spots():
            uid = row.get("uid")
            if uid is None:
                continue
            result[str(uid)] = str(row.get("name") or uid)
        return result

    def export_csv(
        self, orders: List[dict], path: str | Path | None = None
    ) -> Path:
        """把给定订单导出成 CSV，返回实际文件路径。"""
        target = Path(path) if path else self.default_export_path
        return self._export(orders, target)


# ----------------------------------------------------------------------
# 账号
# ----------------------------------------------------------------------
class AccountService:
    """登录校验与账号管理。"""

    def __init__(
        self,
        accounts: AccountRepository,
        verify: Callable[[str, str], bool],
        admin_username: str,
        role_admin: str,
        role_spot_user: str,
        default_password: str,
    ) -> None:
        self._accounts = accounts
        self._verify = verify
        self.admin_username = admin_username
        self.role_admin = role_admin
        self.role_spot_user = role_spot_user
        self.default_password = default_password

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """用户名密码正确则返回账号，否则返回 None。"""
        user = self._accounts.get_user(username)
        if user is None:
            return None
        if not self._verify(password, user.get("password_hash") or ""):
            return None
        return user

    def is_admin(self, user: Optional[dict]) -> bool:
        return bool(user) and user.get("role") == self.role_admin

    def spot_users(self) -> List[dict]:
        return [
            u
            for u in self._accounts.load_users()
            if u.get("role") == self.role_spot_user
        ]

    def spot(self, uid: str) -> Optional[dict]:
        return self._accounts.find_spot(uid)

    def set_admin_password(self, password: str) -> None:
        self._accounts.set_password(self.admin_username, password)

    def reset_spot_password(self, username: str, password: str = "") -> None:
        """重置某个车位账号密码；传入空串则恢复默认密码。"""
        self._accounts.set_password(username, password or self.default_password)


# ----------------------------------------------------------------------
# 车位
# ----------------------------------------------------------------------
class SpotService:
    """车位的「持久化 + 运行时」一致性用例。

    车位要同时存在于数据库（重启后还在、可登录）和 ``SessionManager``（内存中的
    实时对象）里，两边必须一起成功/失败。集中在这里，界面就只调一个方法。
    """

    def __init__(self, spots: SpotRepository, manager: SessionManager) -> None:
        self._spots = spots
        self._manager = manager

    def list_configs(self) -> List[dict]:
        return self._spots.load_spots()

    def find(self, key: str) -> Optional[dict]:
        return self._spots.find_spot(key)

    def add(self, name: str, charger_type, power_kw: float):
        """新增车位：先落库拿 uid，再建运行时对象；失败时回滚数据库。"""
        uid = self._spots.add_spot(name, charger_type, power_kw)
        try:
            return self._manager.add_spot(
                name, charger_type, power_kw, uid=str(uid)
            )
        except Exception:
            self._spots.remove_spot(str(uid))  # 运行时失败则撤销落库，避免脏数据
            raise

    def remove(self, key: str):
        """删除车位：运行时先强制结算，再从库里删（保留历史订单）。"""
        order = self._manager.remove_spot(key)
        self._spots.remove_spot(key)
        return order

    def spot_count(self) -> int:
        return len(self._spots.load_spots())


# ----------------------------------------------------------------------
# 设置
# ----------------------------------------------------------------------
class SettingsService:
    """设置的读取与保存，并把电价档同步进计费引擎。"""

    def __init__(
        self,
        loader: Callable[[], "object"],
        saver: Callable[["object"], Path],
        engine_factory: Callable[["object"], "object"],
        path: Optional[Path] = None,
    ) -> None:
        self._loader = loader
        self._saver = saver
        self._engine_factory = engine_factory
        # 仅用于界面展示「配置保存在哪」，不参与读写
        self.path = Path(path) if path else None

    def current(self):
        return self._loader()

    def save(self, settings) -> Path:
        return self._saver(settings)

    def apply_to(self, manager: SessionManager, settings) -> None:
        """把设置里的电价档注入会话管理器（新订单按新电价结算）。"""
        manager.tariff = self._engine_factory(settings)

    def apply(self, manager: SessionManager) -> None:
        """读取当前设置并应用（应用启动时用）。"""
        self.apply_to(manager, self.current())


# ----------------------------------------------------------------------
# 车牌识别
# ----------------------------------------------------------------------
class VisionService:
    """车牌识别用例：把图片路径 + 当前设置交给识别实现。

    拍照也收进来：界面只问「能不能识别 / 拍一张 / 识别这张图」，
    不需要知道调用的是系统相机命令还是云端 OCR。
    """

    def __init__(
        self,
        recognize: Callable[[str | Path, object], Tuple[str, str]],
        available: Callable[[object], Tuple[bool, str]],
        capture: Optional[Callable[[], Tuple[Optional[Path], str]]] = None,
    ) -> None:
        self._recognize = recognize
        self._available = available
        self._capture = capture

    def available(self, settings) -> Tuple[bool, str]:
        return self._available(settings)

    def recognize(self, image_path: str | Path, settings) -> Tuple[str, str]:
        return self._recognize(image_path, settings)

    def capture(self) -> Tuple[Optional[Path], str]:
        """调用系统摄像头拍照，返回 ``(图片路径或 None, 提示文本)``。"""
        if self._capture is None:
            return None, "当前平台不支持直接拍照，请用「相册识别」选择图片"
        return self._capture()


# ----------------------------------------------------------------------
# 组合根产物
# ----------------------------------------------------------------------
@dataclass
class AppContext:
    """一次运行所需的全部依赖（由 bootstrap 组装，传给 build_app）。"""

    manager: SessionManager
    orders: OrderService
    accounts: AccountService
    spots: SpotService
    settings: SettingsService
    vision: VisionService
    maintenance: "MaintenanceService"
    assets_dir: Path
    app_title_default: str = ""
    # 关闭底层资源（数据库连接等）的可选回调，由组合根注入
    closer: Optional[Callable[[], None]] = None

    def close(self) -> None:
        """关闭底层资源；没有注入 closer 时什么也不做。"""
        if self.closer is None:
            return
        try:
            self.closer()
        except Exception:  # 关闭失败不影响退出
            logger.debug("关闭底层资源失败", exc_info=True)


# ----------------------------------------------------------------------
# 数据维护（备份 / 恢复 / 清空 / CSV 导入）
# ----------------------------------------------------------------------
class MaintenanceService:
    """整个应用数据的备份、恢复、清空与 CSV 导入。

    所有数据（订单 / 车位 / 账号 / 设置与 API Key）都在同一个 SQLite 文件里，
    所以「备份」= 复制这一个文件，「清空」= 清掉库里所有表。
    """

    BACKUP_DIR_NAME = "backups"
    BACKUP_INNER_NAME = "charging.db"
    MANIFEST_NAME = "backup.json"

    def __init__(
        self,
        db,
        app_version: str = "",
        csv_loader: Optional[Callable[[str | Path], Tuple[List[dict], int]]] = None,
    ) -> None:
        self._db = db
        self.app_version = app_version
        # CSV 读取实现由组合根注入（应用层不 import 基础设施）
        self._csv_loader = csv_loader

    # ---------- 概况 ----------
    @property
    def database_path(self) -> Path:
        return Path(getattr(self._db, "path", ""))

    def summary(self) -> dict:
        """数据概况：路径、大小、订单/车位/账号数量。"""
        path = self.database_path
        try:
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            size = 0
        try:
            orders = self._db.order_count()
        except Exception:
            orders = 0
        try:
            spots = len(self._db.load_spots())
        except Exception:
            spots = 0
        return {
            "path": str(path),
            "size_bytes": size,
            "orders": orders,
            "spots": spots,
        }

    # ---------- 备份 ----------
    def backup_to(self, target: str | Path) -> Path:
        """把数据文件打包成 zip（含一份说明文件），返回 zip 路径。"""
        import json
        import zipfile

        src = self.database_path
        if not src.exists():
            raise FileNotFoundError(f"找不到数据文件：{src}")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "app": "evcharge",
            "version": self.app_version,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **{k: v for k, v in self.summary().items() if k != "path"},
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(src, self.BACKUP_INNER_NAME)
            zf.writestr(
                self.MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        return target

    def _extract_backup(self, source: Path, workdir: Path) -> Path:
        """把 .zip 或 .db 取出为可用的 .db 路径。"""
        import zipfile

        source = Path(source)
        if source.suffix.lower() == ".db":
            return source
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as zf:
                names = [n for n in zf.namelist() if n.endswith(".db")]
                if not names:
                    raise ValueError("压缩包里没有 .db 数据文件")
                inner = names[0]
                out = workdir / Path(inner).name
                with zf.open(inner) as fh, open(out, "wb") as dest:
                    dest.write(fh.read())
            return out
        raise ValueError("只支持 .zip 备份包或 .db 数据文件")

    @staticmethod
    def _validate_database(candidate: Path) -> None:
        """确认是个能用的库：能打开、有 orders 表。"""
        import sqlite3

        if not candidate.exists() or candidate.stat().st_size == 0:
            raise ValueError("导入的文件是空的")
        try:
            conn = sqlite3.connect(str(candidate))
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"不是有效的 SQLite 数据库：{exc}") from exc
        if "orders" not in tables:
            raise ValueError("该数据库不是本程序的备份（缺少 orders 表）")

    def restore_from(self, source: str | Path) -> Path:
        """用备份覆盖当前数据；覆盖前自动把现有库再备份一份。

        返回「现有数据的备份路径」。调用方应提示用户重启程序。
        """
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            candidate = self._extract_backup(Path(source), Path(tmp))
            self._validate_database(candidate)

            backup_path = Path("")
            if self.database_path.exists():
                backup_dir = self.database_path.parent / self.BACKUP_DIR_NAME
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = backup_dir / f"charging-before-restore-{stamp}.db"
                shutil.copy2(self.database_path, backup_path)

            # 关闭连接后再覆盖，避免 Windows 上文件被占用
            close = getattr(self._db, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("关闭数据库连接失败", exc_info=True)
            shutil.copy2(candidate, self.database_path)
        return backup_path

    # ---------- 清空 ----------
    def clear_all(self) -> None:
        """清空所有业务数据与设置（含 API Key），并保留默认管理员账号。"""
        self._db.clear_all()

    # ---------- CSV 导入 ----------
    def import_csv(self, path: str | Path) -> tuple[int, int]:
        """把订单 CSV 追加进库，返回 ``(导入条数, 跳过条数)``。"""
        if self._csv_loader is None:
            raise RuntimeError("未配置 CSV 读取器，无法导入")
        rows, bad_rows = self._csv_loader(path)
        imported, skipped = self._db.import_orders(rows)
        # 读取阶段就坏掉的行（缺字段/类型错）也算跳过
        return imported, skipped + bad_rows


__all__ = [
    "OrderService",
    "AccountService",
    "SpotService",
    "SettingsService",
    "VisionService",
    "MaintenanceService",
    "AppContext",
]
