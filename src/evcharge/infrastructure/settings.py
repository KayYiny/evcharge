# -*- coding: utf-8 -*-
"""应用设置（evcharge.infrastructure.settings）。

统一保存：
- 站点：项目名称、站点身份、电价档（国网高速 / 福建居民）、服务费覆盖、刷新间隔；
- AI：厂商 URL、API Key、对话模型、视觉模型（车牌识别）、AI 人设、是否本地汇总后再发送；
  车牌识别与 AI 报告共用同一套 URL / API Key，不再单独配置；
**存储位置**：与订单/车位/账号一起存在同一个 SQLite 文件里
（``data/charging.db`` 的 ``app_settings`` 表，单行 JSON）。
这样整个应用的数据只有一个文件，备份、迁移、一键清空都简单。

- 首次升级时会自动把旧的 ``data/settings.json`` 迁移进数据库，
  并把文件里的 API Key 就地替换成受保护形式（不再留明文）；
- API Key 落库前经过 ``secrets.protect``：优先系统钥匙串，否则 base64 混淆（不是加密）；
- 测试/导入导出仍可用文件存储，见 :class:`FileSettingsStore`。

电价档/服务费用于之后的新会话和重新生成的历史数据；已有订单保留原账单。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Protocol, Union

from evcharge.domain import config
from evcharge.domain.tariff import TariffEngine
from evcharge.infrastructure.paths import DEFAULT_DB_PATH, SETTINGS_PATH
from evcharge.infrastructure.secrets import protect, reveal

logger = logging.getLogger(__name__)

# 默认值、电价档标签、站点身份等**纯数据**放在 domain.config；
# 本模块负责校验与读写设置：默认存 SQLite，文件存储用于测试与旧数据迁移。
DEFAULT_APP_TITLE = config.DEFAULT_APP_TITLE
DEFAULT_PERSONA_NAME = config.DEFAULT_PERSONA_NAME
DEFAULT_PERSONA_PROMPT = config.DEFAULT_PERSONA_PROMPT
DEFAULT_REFRESH_INTERVAL_S = config.DEFAULT_REFRESH_INTERVAL_S
REFRESH_OPTIONS = config.REFRESH_OPTIONS
TARIFF_PROFILES = config.TARIFF_PROFILES
TARIFF_LABELS = config.TARIFF_LABELS
TARIFF_SHORT_LABELS = config.TARIFF_SHORT_LABELS
TARIFF_IDENTITY_DEFAULTS = config.TARIFF_IDENTITY_DEFAULTS
VISION_MODEL_HINT = config.VISION_MODEL_HINT
identity_defaults = config.identity_defaults

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


# 设置模型放在 domain（纯数据），这里只做读写；再导出保持旧 import 路径可用
from evcharge.domain.app_settings import AppSettings  # noqa: E402

# ----------------------------------------------------------------------
# 存储：默认放数据库，也支持文件（测试 / 导入导出 / 旧数据迁移）
# ----------------------------------------------------------------------
class SettingsStore(Protocol):
    """设置存取接口。"""

    def load(self) -> Optional[dict]:
        """返回原始设置字典；没有记录时返回 None。"""

    def save(self, data: dict) -> None:
        """整份覆盖写入。"""

    def location(self) -> str:
        """人类可读的存放位置（界面展示用）。"""


class DbSettingsStore:
    """与订单同库：``app_settings`` 表单行 JSON。"""

    def __init__(self, db) -> None:
        self._db = db

    def load(self) -> Optional[dict]:
        return self._db.load_settings_blob()

    def save(self, data: dict) -> None:
        self._db.save_settings_blob(data)

    def location(self) -> str:
        path = getattr(self._db, "path", "")
        return str(path) if path else "（数据库）"


class FileSettingsStore:
    """JSON 文件存储（用于测试、导入导出、旧版本兼容）。"""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    def load(self) -> Optional[dict]:
        target = self.path
        if not target.exists():
            # 兼容旧版仅 AI 配置的 ai_settings.json
            legacy = target.with_name("ai_settings.json")
            if not legacy.exists():
                return None
            target = legacy
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("设置文件解析失败，按默认值处理：%s", target)
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass  # Windows / 受限文件系统忽略

    def location(self) -> str:
        return str(self.path)


_shared_db = None


def _default_store() -> SettingsStore:
    """默认存储：与订单共用的那个 SQLite 文件。"""
    global _shared_db
    if _shared_db is None:
        from evcharge.infrastructure.database import Database

        _shared_db = Database()
    return DbSettingsStore(_shared_db)


def reset_default_store() -> None:
    """丢弃缓存的默认数据库连接（测试或换库后调用）。"""
    global _shared_db
    _shared_db = None


def resolve_store(
    store: Union[SettingsStore, str, Path, None] = None,
) -> SettingsStore:
    """把各种入参归一成 SettingsStore。

    - ``None`` → 默认数据库；
    - ``Path``/``str`` → 文件存储（测试与导入导出用）；
    - 带 ``load_settings_blob`` 的对象（Database）→ 数据库存储；
    - 其它对象 → 视为已实现协议的存储。
    """
    if store is None:
        return _default_store()
    if isinstance(store, (str, Path)):
        return FileSettingsStore(store)
    if hasattr(store, "load_settings_blob") and hasattr(store, "save_settings_blob"):
        return DbSettingsStore(store)
    return store  # type: ignore[return-value]


# ----------------------------------------------------------------------
# 读 / 写
# ----------------------------------------------------------------------
def _is_default_database(store: SettingsStore) -> bool:
    """该存储是否就是默认的那个数据库文件（迁移只对它生效）。"""
    try:
        return (
            Path(store.location()).expanduser().resolve()
            == Path(DEFAULT_DB_PATH).expanduser().resolve()
        )
    except (OSError, RuntimeError):
        return False


def _migrate_legacy_file(store: SettingsStore) -> None:
    """把旧的 data/settings.json 迁进数据库，并抹掉文件里的明文 Key。

    只在「默认数据库 + 库里还没有设置 + 旧文件存在」时执行一次；
    临时库（测试、gen_history --db 等）不会被别的站点设置污染。
    失败只记日志，不影响启动。
    """
    if not _is_default_database(store):
        return
    if FileSettingsStore(SETTINGS_PATH).load() is None:
        return
    try:
        # settings.json 不存在但 ai_settings.json 存在时，FileSettingsStore 会自动回退
        legacy = FileSettingsStore(SETTINGS_PATH)
        raw = legacy.load() or {}
        protected = dict(raw)
        protected["api_key"] = protect(str(raw.get("api_key") or ""))
        store.save(protected)
        # 文件里不再保留明文 Key（保留文件本身作为备份）
        legacy.save(protected)
        logger.info("已把 %s 迁移进数据库，并清除文件中的明文 Key", SETTINGS_PATH)
    except Exception:
        logger.warning("旧版设置迁移失败，继续使用默认值", exc_info=True)


def load_settings(
    store: Union[SettingsStore, str, Path, None] = None,
) -> AppSettings:
    """读取设置；没有记录或解析失败时返回默认值。"""
    resolved = resolve_store(store)
    data = resolved.load()
    if data is None and isinstance(resolved, DbSettingsStore):
        # 数据库里还没有设置：尝试从旧版 settings.json 迁移一次
        _migrate_legacy_file(resolved)
        data = resolved.load()
    if not isinstance(data, dict):
        return AppSettings()

    settings = AppSettings.from_dict(data)
    # 存储里可能是钥匙串引用 / base64 混淆 / 老式明文，统一还原成真实 Key
    settings.api_key = reveal(settings.api_key)
    return settings


def save_settings(
    settings: AppSettings,
    store: Union[SettingsStore, str, Path, None] = None,
) -> Path:
    """保存设置，返回存放位置（数据库文件或 JSON 文件的路径）。"""
    resolved = resolve_store(store)
    data = settings.to_dict()
    # 落库/落盘前保护 API Key：优先系统钥匙串，否则 base64 混淆（不存明文）
    data["api_key"] = protect(settings.api_key)
    resolved.save(data)
    return Path(resolved.location())


def settings_location(
    store: Union[SettingsStore, str, Path, None] = None,
) -> str:
    """设置的存放位置（界面展示用）。"""
    return resolve_store(store).location()


def tariff_engine(settings: AppSettings) -> TariffEngine:
    """按设置创建计费引擎；service_fee=None 时使用电价档默认服务费。"""
    return TariffEngine(
        profile=settings.tariff_profile_obj,
        service_fee=settings.service_fee,
    )
