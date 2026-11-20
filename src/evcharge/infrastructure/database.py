# -*- coding: utf-8 -*-
"""SQLite 持久化层（evcharge.infrastructure.database）。

v3 数据结构（车位「双主键」）：
- ``spots``：车位配置表，``uid`` 为随机生成的稳定标识（主键），``name`` 是展示名兼
  登录名（``COLLATE NOCASE UNIQUE``，如 ``A001``），``power_kw`` 存**铭牌功率**；
- ``orders``：订单表，``pile_id`` 存车位 **uid**，``spot_name`` 是下单时的展示名快照；
- ``users``：账号表，管理员 admin + 每个车位的登录账号（``username`` = 车位 name，
  ``spot_uid`` 指向该车位）；
- ``meta``：结构/口径迁移标记（如 ``spots_power_basis``）。

订单只认 uid，所以删掉车位后重建一个同名车位，两者的历史订单不会串。
删除车位只删 ``spots``/``users``，**保留订单**；确需清空订单走 ``delete_orders_by_spot``。

自动迁移：
- v1（pile_id INTEGER / 含 gun_shared）→ 丢弃旧表按新结构重建；
- v2（spots.spot_no / users.spot_no）→ 为每个车位生成 uid；纯数字编号统一换成
  ``A001`` 之类的规范名（其他自定义名保留），账号名同步；订单 ``pile_id`` 重写为
  uid，展示名记入 ``spot_name``；
- 旧库若把“实测功率”写进了 ``spots.power_kw``，首次打开会自动还原成铭牌功率。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from evcharge.domain.charger import (
    DC_HEALTH_FACTOR,
    DC_POWER_TIERS,
    ChargerType,
    new_spot_uid,
    normalize_spot_name,
    suggest_spot_name,
)
from evcharge.domain.models import Order
from evcharge.infrastructure.auth import (
    ADMIN_USERNAME,
    DEFAULT_PASSWORD,
    ROLE_ADMIN,
    ROLE_SPOT_USER,
    hash_password,
)
from evcharge.infrastructure.paths import DEFAULT_DB_PATH

DEFAULT_DB = DEFAULT_DB_PATH  # 兼容旧引用；实际定义在 evcharge.paths

_ORDER_COLUMNS = [
    "order_id",
    "pile_id",
    "spot_name",
    "plate",
    "start_time",
    "end_time",
    "energy_kwh",
    "energy_fee",
    "service_fee",
    "total_fee",
    "tariff_periods",
    "status",
    "vehicle_category",
    "vehicle_segment",
    "vehicle_model",
    "battery_kwh",
    "start_soc",
    "end_soc",
    "peak_power_kw",
    "charger_type",
    "voltage_platform_v",
]

_ORDER_DEFAULTS = {
    "spot_name": "—",
    "vehicle_category": "—",
    "vehicle_segment": "—",
    "vehicle_model": "—",
    "battery_kwh": 0.0,
    "start_soc": 0.0,
    "end_soc": 0.0,
    "peak_power_kw": 0.0,
    "charger_type": "—",
    "voltage_platform_v": 0,
}

# 车位功率口径迁移标记：早期版本把「实测功率」（铭牌 × 0.9）写进 spots.power_kw，
# 重新加载时又被当成铭牌再乘一次 0.9，导致每重启一次功率就衰减一轮。
_SPOT_POWER_BASIS_KEY = "spots_power_basis"
_SPOT_POWER_BASIS_NOMINAL = "nominal"


def _snap_dc_tier(power_kw: float) -> float:
    """把直流功率吸附到最近的铭牌档（60/120/240），用于旧库口径还原。"""
    return min(DC_POWER_TIERS, key=lambda tier: abs(tier - float(power_kw)))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY,
    pile_id TEXT NOT NULL,
    spot_name TEXT,
    plate TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    energy_kwh REAL NOT NULL,
    energy_fee REAL NOT NULL,
    service_fee REAL NOT NULL,
    total_fee REAL NOT NULL,
    tariff_periods TEXT,
    status TEXT,
    vehicle_category TEXT,
    vehicle_segment TEXT,
    vehicle_model TEXT,
    battery_kwh REAL,
    start_soc REAL,
    end_soc REAL,
    peak_power_kw REAL,
    charger_type TEXT,
    voltage_platform_v INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_start ON orders(start_time);
CREATE INDEX IF NOT EXISTS idx_orders_pile ON orders(pile_id);

CREATE TABLE IF NOT EXISTS spots (
    uid TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    charger_type TEXT NOT NULL,
    power_kw REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    spot_uid TEXT
);

-- 结构/口径迁移标记（例如 spots.power_kw 从“实测功率”改为“铭牌功率”）
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 应用设置（站点 / 电价 / AI 配置）：单行表，data 是 JSON 文本。
-- 放在同一个 .db 里，整个应用的数据就只有一个文件，便于备份/迁移/清理。
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _normalize_type(charger_type: ChargerType | str) -> str:
    """把 ChargerType / '直流' / '交流' 等归一成 ``'fast'`` / ``'slow'``。"""
    if isinstance(charger_type, ChargerType):
        return "fast" if charger_type == ChargerType.DC else "slow"
    text = str(charger_type).strip().lower()
    if text in {"dc", "fast", "直流", "直流快充", "快充"}:
        return "fast"
    if text in {"ac", "slow", "交流", "交流慢充", "慢充"}:
        return "slow"
    return text or "slow"


class Database:
    """SQLite 订单 / 车位 / 账号持久化封装。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ---------- 初始化 / 迁移 ----------
    def _init_schema(self) -> None:
        self._reset_if_legacy()
        self._conn.executescript(_SCHEMA)
        self._migrate_v2_to_v3()
        self._migrate_spot_power_to_nominal()
        self._seed_admin()
        self._conn.commit()

    def _table_columns(self, table: str) -> set[str]:
        try:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.Error:
            return set()
        return {str(row[1]).lower() for row in rows}

    def _migrate_v2_to_v3(self) -> None:
        """v2（车位单主键 = 编号）→ v3（uid + name 双键）。

        - 每个旧车位分配一个随机 uid；
        - 纯数字编号（1、2、3…）统一换成 ``A001`` 之类的规范名，其他自定义名保留，
          车位账号用户名同步改名，密码散列不动；
        - 订单 ``pile_id`` 重写为 uid，展示名写入 ``spot_name`` 快照；
        - 用户表 ``spot_no`` → ``spot_uid``。
        """
        if "spot_no" not in self._table_columns("spots"):
            return  # 已是 v3（或空库）
        spots = self._conn.execute(
            "SELECT spot_no, charger_type, power_kw FROM spots"
        ).fetchall()
        users = (
            self._conn.execute(
                "SELECT username, password_hash, role, spot_no FROM users"
            ).fetchall()
            if "spot_no" in self._table_columns("users")
            else []
        )
        orders = [dict(row) for row in self._conn.execute("SELECT * FROM orders")]

        # 旧编号 -> 新车位名：纯数字编号（1、2、3…）统一换成 A001 之类的规范名，
        # 其他自定义名字原样保留，避免认不出自己的车位。
        renamed: dict[str, str] = {}
        used = {
            str(row["spot_no"]).casefold()
            for row in spots
            if not str(row["spot_no"]).isdigit()
        }
        for row in spots:
            old = str(row["spot_no"])
            if old.isdigit():
                name = suggest_spot_name(used)
                used.add(name.casefold())
                renamed[old.casefold()] = name
            else:
                renamed[old.casefold()] = old

        mapping: dict[str, tuple[str, str]] = {}  # 旧编号(casefold) -> (uid, name)
        for row in spots:
            old = str(row["spot_no"])
            mapping[old.casefold()] = (new_spot_uid(), renamed[old.casefold()])

        self._conn.execute("DROP TABLE IF EXISTS orders")
        self._conn.execute("DROP TABLE IF EXISTS users")
        self._conn.execute("DROP TABLE IF EXISTS spots")
        self._conn.executescript(_SCHEMA)

        self._conn.executemany(
            "INSERT INTO spots (uid, name, charger_type, power_kw) VALUES (?, ?, ?, ?)",
            [
                (
                    mapping[str(row["spot_no"]).casefold()][0],
                    mapping[str(row["spot_no"]).casefold()][1],
                    row["charger_type"],
                    float(row["power_kw"]),
                )
                for row in spots
            ],
        )
        self._conn.executemany(
            "INSERT OR REPLACE INTO users (username, password_hash, role, spot_uid) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    (
                        mapping[str(row["spot_no"]).casefold()][1]
                        if str(row["spot_no"] or "").casefold() in mapping
                        else row["username"]
                    ),
                    row["password_hash"],
                    row["role"],
                    mapping.get(str(row["spot_no"] or "").casefold(), (None, None))[0],
                )
                for row in users
            ],
        )
        for item in orders:
            old_pile = str(item.get("pile_id", ""))
            uid, name = mapping.get(old_pile.casefold(), (old_pile, old_pile))
            item["pile_id"] = uid
            item["spot_name"] = name
            columns = [c for c in _ORDER_COLUMNS if c in item]
            self._conn.execute(
                f"INSERT OR REPLACE INTO orders ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(item[c] for c in columns),
            )

    def _migrate_spot_power_to_nominal(self) -> None:
        """把旧库的 spots.power_kw 从「实测功率」还原成「铭牌功率」（只做一次）。

        旧口径写入的是 nominal × 0.9（如 240kW → 216），本函数反算并吸附到最近的
        铭牌档，再用 meta 标记，避免重复折算。
        """
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_SPOT_POWER_BASIS_KEY,)
        ).fetchone()
        if row is not None and str(row["value"]) == _SPOT_POWER_BASIS_NOMINAL:
            return
        for item in self._conn.execute(
            "SELECT uid, charger_type, power_kw FROM spots"
        ).fetchall():
            if _normalize_type(item["charger_type"]) != "fast":
                continue
            stored = float(item["power_kw"])
            nominal = _snap_dc_tier(stored / DC_HEALTH_FACTOR)
            if abs(nominal - stored) > 1e-9:
                self._conn.execute(
                    "UPDATE spots SET power_kw = ? WHERE uid = ?",
                    (nominal, item["uid"]),
                )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (_SPOT_POWER_BASIS_KEY, _SPOT_POWER_BASIS_NOMINAL),
        )

    def _reset_if_legacy(self) -> None:
        """检测旧版库结构，必要时整体重置（用户选择 reset 策略）。"""
        try:
            table = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
            ).fetchone()
        except sqlite3.Error:
            return
        if table is None:
            return
        cols = {
            row[1]: str(row[2]).upper()
            for row in self._conn.execute("PRAGMA table_info(orders)")
        }
        pile_type = cols.get("pile_id", "")
        legacy = "gun_shared" in cols or (pile_type and "TEXT" not in pile_type)
        if legacy:
            self._conn.execute("DROP TABLE IF EXISTS orders")
            self._conn.execute("DROP TABLE IF EXISTS spots")
            self._conn.execute("DROP TABLE IF EXISTS users")
            self._conn.execute("DROP TABLE IF EXISTS meta")
            self._conn.commit()

    def _seed_admin(self) -> None:
        row = self._conn.execute(
            "SELECT username FROM users WHERE role = ?", (ROLE_ADMIN,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT OR REPLACE INTO users (username, password_hash, role, spot_uid) "
                "VALUES (?, ?, ?, NULL)",
                (ADMIN_USERNAME, hash_password(DEFAULT_PASSWORD), ROLE_ADMIN),
            )

    # ---------- 订单 ----------
    def _values(self, order: Order) -> tuple:
        return (
            order.order_id,
            str(order.pile_id),
            order.spot_name,
            order.plate,
            order.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            order.end_time.strftime("%Y-%m-%d %H:%M:%S"),
            order.energy_kwh,
            order.bill.energy_fee,
            order.bill.service_fee,
            order.total_fee,
            order.tariff_periods_json,
            order.status,
            order.vehicle_category,
            order.vehicle_segment,
            order.vehicle_model,
            order.battery_kwh,
            order.start_soc,
            order.end_soc,
            order.peak_power_kw,
            order.charger_type,
            order.voltage_platform_v,
        )

    def save_order(self, order: Order) -> int:
        """追加/更新一条订单，返回数据库分配的单号。

        order_id 为 None 时由 SQLite 自增分配；已有单号时幂等覆盖。
        """
        placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
        columns = ", ".join(_ORDER_COLUMNS)
        try:
            cursor = self._conn.execute(
                f"INSERT OR REPLACE INTO orders ({columns}) VALUES ({placeholders})",
                self._values(order),
            )
            if order.order_id is None:
                order.order_id = int(cursor.lastrowid)
            self._conn.commit()
            return order.order_id
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def save_orders(self, orders: Iterable[Order]) -> int:
        """批量写入订单（单事务），返回写入条数。"""
        rows = [self._values(order) for order in orders]
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
        columns = ", ".join(_ORDER_COLUMNS)
        try:
            self._conn.executemany(
                f"INSERT OR REPLACE INTO orders ({columns}) VALUES ({placeholders})",
                rows,
            )
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise
        return len(rows)

    def load_orders(
        self, limit: Optional[int] = None, spot_uid: Optional[str] = None
    ) -> List[dict]:
        """读取历史订单（按开始时间倒序），损坏行自动跳过。

        ``spot_uid`` 按车位稳定标识（uid）过滤；不传则返回全部订单。
        """
        sql = "SELECT * FROM orders"
        params: tuple = ()
        if spot_uid is not None:
            sql += " WHERE pile_id = ?"
            params = (str(spot_uid),)
        sql += " ORDER BY start_time DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        result: List[dict] = []
        for row in rows:
            try:
                item = dict(row)
                periods = json.loads(item.pop("tariff_periods") or "[]")
                item["periods"] = periods if isinstance(periods, list) else []
                start = datetime.fromisoformat(item["start_time"])
                end = datetime.fromisoformat(item["end_time"])
                item["duration_min"] = round((end - start).total_seconds() / 60.0, 1)
                for key, default in _ORDER_DEFAULTS.items():
                    if item.get(key) is None:
                        item[key] = default
                result.append(item)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue  # 损坏行跳过（容错）
        return result

    def order_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()
        return int(row["n"])

    def delete_orders_by_spot(self, spot_uid: str) -> int:
        """按车位 uid 删除该车位的全部订单，返回删除条数。"""
        cursor = self._conn.execute(
            "DELETE FROM orders WHERE pile_id = ?", (str(spot_uid),)
        )
        self._conn.commit()
        return int(cursor.rowcount)

    def clear_orders(self) -> None:
        """清空订单（重新生成历史数据前调用）。"""
        self._conn.execute("DELETE FROM orders")
        self._conn.commit()

    # ---------- 应用设置（与订单同库，单行 JSON） ----------
    def load_settings_blob(self) -> Optional[dict]:
        """读取设置字典；没有任何记录时返回 None。"""
        row = self._conn.execute(
            "SELECT data FROM app_settings WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def save_settings_blob(self, data: dict) -> None:
        """写入设置（覆盖单行）。"""
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        self._conn.execute(
            """
            INSERT INTO app_settings (id, data, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data,
                                          updated_at = excluded.updated_at
            """,
            (payload, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        self._conn.commit()

    def delete_settings(self) -> None:
        """删除设置行（清空数据时用，之后按默认值运行）。"""
        self._conn.execute("DELETE FROM app_settings")
        self._conn.commit()

    def clear_spots(self) -> None:
        """清空车位与车位账号（保留管理员与设置），用于重建示例车位。"""
        self._conn.execute("DELETE FROM spots")
        self._conn.execute("DELETE FROM users WHERE role != ?", (ROLE_ADMIN,))
        self._conn.commit()

    def import_orders(self, rows: Iterable[dict]) -> tuple[int, int]:
        """把 CSV 行追加进库，返回 ``(导入条数, 跳过条数)``。

        规则：
        - 必须有 ``pile_id`` / ``plate`` / ``start_time``（缺失即跳过）；
        - 单号由数据库重新分配，不吃 CSV 里的旧号（避免覆盖历史）；
        - 同一车位 + 车牌 + 开始时间视为重复，跳过，便于重复导入同一份 CSV。
        """
        existing = {
            (str(r["pile_id"]), str(r["plate"]), str(r["start_time"]))
            for r in self._conn.execute(
                "SELECT pile_id, plate, start_time FROM orders"
            )
        }
        placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
        columns = ", ".join(_ORDER_COLUMNS)
        imported = skipped = 0
        for row in rows:
            pile_id = str(row.get("pile_id") or "").strip()
            plate = str(row.get("plate") or "").strip()
            start_time = str(row.get("start_time") or "").strip()
            if not (pile_id and plate and start_time):
                skipped += 1
                continue
            key = (pile_id, plate, start_time)
            if key in existing:
                skipped += 1
                continue

            def _num(name: str, default: float = 0.0) -> float:
                try:
                    return float(row.get(name) or default)
                except (TypeError, ValueError):
                    return default

            periods = row.get("periods")
            if periods is None and row.get("tariff_periods"):
                try:
                    periods = json.loads(str(row["tariff_periods"]))
                except json.JSONDecodeError:
                    periods = []
            values = (
                None,  # order_id 交给数据库自增
                pile_id,
                row.get("spot_name") or pile_id,
                plate,
                start_time,
                str(row.get("end_time") or start_time),
                _num("energy_kwh"),
                _num("energy_fee"),
                _num("service_fee"),
                _num("total_fee"),
                json.dumps(periods or [], ensure_ascii=False),
                row.get("status") or "已完成",
                row.get("vehicle_category") or "—",
                row.get("vehicle_segment") or "—",
                row.get("vehicle_model") or "—",
                _num("battery_kwh"),
                _num("start_soc"),
                _num("end_soc"),
                _num("peak_power_kw"),
                row.get("charger_type") or "—",
                int(_num("voltage_platform_v")),
            )
            self._conn.execute(
                f"INSERT INTO orders ({columns}) VALUES ({placeholders})", values
            )
            existing.add(key)
            imported += 1
        self._conn.commit()
        return imported, skipped

    # ---------- 全量清理 ----------
    def clear_all(self) -> None:
        """清空全部业务数据与设置，并重建默认管理员账号。

        - 订单、车位、账号、设置（含 API Key）全部删除；
        - 之后重新种入默认管理员，保证清空后仍能登录；
        - 车辆/电价等代码内置数据不受影响。
        """
        try:
            self._conn.execute("DELETE FROM orders")
            self._conn.execute("DELETE FROM spots")
            self._conn.execute("DELETE FROM users")
            self._conn.execute("DELETE FROM app_settings")
            self._conn.execute("DELETE FROM meta")
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise
        self._seed_admin()

    # ---------- 车位 ----------
    def add_spot(
        self,
        name: str,
        charger_type: ChargerType | str,
        power_kw: float | None = None,
        uid: str | None = None,
    ) -> str:
        """新增车位配置，并同步创建车位账号（用户名=车位名，默认密码）。

        返回车位的稳定标识 ``uid``（不传时随机生成）。``name`` 站内唯一（忽略大小写），
        重名会抛 ``ValueError``。

        注意：库里存的是**铭牌功率**（``nominal_power_kw``），实测折减在
        ``make_charger`` 里做。存实测值会导致每次重启再折一次。
        """
        from evcharge.domain.charger import make_charger

        name = normalize_spot_name(name)
        if not name:
            raise ValueError("车位名称不能为空")
        uid = str(uid).strip() if uid else new_spot_uid()
        profile = make_charger(name, charger_type, power_kw)
        try:
            self._conn.execute(
                "INSERT INTO spots (uid, name, charger_type, power_kw) VALUES (?, ?, ?, ?)",
                (
                    uid,
                    name,
                    _normalize_type(profile.charger_type),
                    profile.nominal_power_kw,
                ),
            )
            self._conn.execute(
                "INSERT INTO users (username, password_hash, role, spot_uid) "
                "VALUES (?, ?, ?, ?)",
                (name, hash_password(DEFAULT_PASSWORD), ROLE_SPOT_USER, uid),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            raise ValueError(f"车位 {name} 已存在")
        return uid

    def remove_spot(self, key: str) -> None:
        """删除车位及其账号（``key`` 可以是 uid 或车位名），**保留历史订单**。

        需要连订单一起清空时，显式调用 :meth:`delete_orders_by_spot`。
        """
        row = self.find_spot(key)
        if row is None:
            return
        self._conn.execute("DELETE FROM spots WHERE uid = ?", (row["uid"],))
        self._conn.execute(
            "DELETE FROM users WHERE spot_uid = ? OR username = ?",
            (row["uid"], row["name"]),
        )
        self._conn.commit()

    def find_spot(self, key: str) -> dict | None:
        """按 uid 或车位名（忽略大小写）查一个车位配置。"""
        text = str(key).strip()
        row = self._conn.execute(
            "SELECT * FROM spots WHERE uid = ? OR name = ? LIMIT 1", (text, text)
        ).fetchone()
        return dict(row) if row else None

    def load_spots(self) -> List[dict]:
        """读取全部车位配置（按车位名排序）。"""
        rows = self._conn.execute(
            "SELECT * FROM spots ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]

    def spot_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM spots").fetchone()
        return int(row["n"])

    # ---------- 账号 ----------
    def create_user(
        self, username: str, password: str, role: str, spot_uid: str | None = None
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO users (username, password_hash, role, spot_uid) "
            "VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, spot_uid),
        )
        self._conn.commit()

    def set_password(self, username: str, password: str) -> None:
        """修改账号密码（管理员或用户本人）。"""
        self._conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        self._conn.commit()

    def get_user(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None

    def load_users(self) -> List[dict]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
