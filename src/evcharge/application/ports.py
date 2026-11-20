# -*- coding: utf-8 -*-
"""应用层的仓储端口（evcharge.application.ports）。

表现层不该知道 SQLite / CSV 的存在，只通过这里定义的**协议**使用存储能力；
基础设施层（``infrastructure.database`` 等）在结构上实现这些协议，
由组合根 ``evcharge.bootstrap`` 注入。

用 ``Protocol`` 而不是抽象基类：不需要显式继承，``Database`` 不必依赖应用层，
依赖方向保持 ``infrastructure → application`` 单向。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class OrderRepository(Protocol):
    """订单与车位配置的读取接口。"""

    def load_orders(
        self, limit: Optional[int] = None, spot_uid: Optional[str] = None
    ) -> List[dict]:
        """按开始时间倒序返回订单字典列表。"""

    def order_count(self) -> int:
        """订单总数。"""

    def load_spots(self) -> List[dict]:
        """返回全部车位配置。"""


@runtime_checkable
class AccountRepository(Protocol):
    """账号与车位的管理接口。"""

    def get_user(self, username: str) -> Optional[dict]:
        """按用户名取账号（含 ``password_hash`` / ``role`` / ``spot_uid``）。"""

    def load_users(self) -> List[dict]:
        """全部账号。"""

    def set_password(self, username: str, password: str) -> None:
        """重置某个账号的密码。"""

    def find_spot(self, key: str) -> Optional[dict]:
        """按 uid 或车位名查找车位。"""


@runtime_checkable
class SpotRepository(Protocol):
    """车位配置的增删改查接口。"""

    def add_spot(self, name: str, charger_type, power_kw: float, uid=None) -> None:
        """新增车位（含同名的登录账号）。"""

    def remove_spot(self, key: str) -> None:
        """删除车位及其账号。"""

    def load_spots(self) -> List[dict]:
        """全部车位配置。"""

    def find_spot(self, key: str) -> Optional[dict]:
        """按 uid 或车位名查找车位。"""


@runtime_checkable
class OrderExporter(Protocol):
    """订单导出接口（CSV / 未来的 Excel 都实现它）。"""

    def __call__(self, orders: List[dict], path: str | Path) -> Path: ...
