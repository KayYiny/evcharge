# -*- coding: utf-8 -*-
"""组合根（evcharge.bootstrap）——全项目唯一允许「什么都 import」的地方。

它把基础设施（SQLite、CSV、设置存储、车牌识别）装配成应用层服务，
再交给表现层。这样：

- ``presentation`` 只依赖 ``application``（服务与协议），看不到 SQLite / requests；
- ``application`` 只依赖 ``domain`` 与自身协议，不关心具体存储；
- ``infrastructure`` 实现协议，且不反向依赖上层。

换存储（比如 SQLite → MySQL）只需要改这里 + 一个新的仓储实现，界面一行不动。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from evcharge.application.services import (
    AccountService,
    AppContext,
    MaintenanceService,
    OrderService,
    SettingsService,
    SpotService,
    VisionService,
)
from evcharge.application.session_manager import SessionManager
from evcharge.domain.charger import SpotConfig, coerce_type
from evcharge.domain.models import Order
from evcharge.infrastructure.auth import (
    ADMIN_USERNAME,
    DEFAULT_PASSWORD,
    ROLE_ADMIN,
    ROLE_SPOT_USER,
    verify_password,
)
from evcharge.infrastructure.csv_export import export_orders, load_orders_csv
from evcharge.infrastructure.database import Database
from pathlib import Path

from evcharge.infrastructure.paths import (
    ASSETS_DIR,
    DEFAULT_EXPORT_PATH,
    SETTINGS_PATH,
)
from evcharge.infrastructure.camera import capture_photo
from evcharge.infrastructure.plate_recognition import (
    recognize_plate,
    recognition_available,
)

logger = logging.getLogger("charging")


def load_spots(db: Database) -> List[SpotConfig]:
    """从数据库读取车位配置，作为 SessionManager 的初始车位。

    ``uid`` 是订单认的稳定标识，``name`` 是展示名兼登录名；
    ``spots.power_kw`` 存铭牌功率，``make_charger`` 会再折算实测值。
    """
    return [
        SpotConfig(
            uid=str(row["uid"]),
            name=str(row["name"]),
            charger_type=coerce_type(row["charger_type"]),
            power_kw=float(row["power_kw"]),
        )
        for row in db.load_spots()
    ]


def build_context(
    db: Optional[Database] = None,
    on_order: Optional[callable] = None,
) -> AppContext:
    """组装一次运行所需的全部依赖。"""
    database = db or Database()

    def _persist(order: Order) -> None:
        # 订单统一在组合根落库，界面层不再重复写库，避免双重写入
        try:
            database.save_order(order)
        except Exception:
            logger.exception("订单入库失败（内存中仍保留该订单）")
        if on_order is not None:
            on_order(order)

    manager = SessionManager(on_order=_persist, spots=load_spots(database))

    orders = OrderService(
        orders=database,
        export_func=export_orders,
        default_export_path=DEFAULT_EXPORT_PATH,
    )
    accounts = AccountService(
        accounts=database,
        verify=verify_password,
        admin_username=ADMIN_USERNAME,
        role_admin=ROLE_ADMIN,
        role_spot_user=ROLE_SPOT_USER,
        default_password=DEFAULT_PASSWORD,
    )

    # 设置的读写与电价引擎都来自基础设施，这里注入成函数，服务本身不 import。
    # 设置与订单同库：绑定到本次装配的 database，保证「一个应用一个 .db 文件」。
    from evcharge.infrastructure.settings import (
        load_settings,
        save_settings,
        tariff_engine,
    )

    settings = SettingsService(
        loader=lambda: load_settings(database),
        saver=lambda value: save_settings(value, database),
        engine_factory=tariff_engine,
        path=Path(getattr(database, "path", SETTINGS_PATH)),
    )
    vision = VisionService(
        recognize=recognize_plate,
        available=recognition_available,
        capture=capture_photo,
    )
    spots = SpotService(spots=database, manager=manager)

    try:
        from evcharge import __version__ as app_version
    except Exception:  # 版本号拿不到不影响使用
        app_version = ""
    maintenance = MaintenanceService(
        database,
        app_version=app_version,
        csv_loader=load_orders_csv,
    )

    return AppContext(
        manager=manager,
        orders=orders,
        accounts=accounts,
        spots=spots,
        settings=settings,
        maintenance=maintenance,
        vision=vision,
        assets_dir=ASSETS_DIR,
        closer=database.close,
    )


__all__ = ["build_context", "load_spots", "AppContext"]
