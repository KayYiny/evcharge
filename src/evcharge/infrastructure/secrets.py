# -*- coding: utf-8 -*-
"""API Key 的本机保护（evcharge.infrastructure.secrets）。

设置里要存 API Key，明文写入后任何能打开数据文件的人都能看到。这里做两层：

1. **优先用系统钥匙串**（macOS Keychain / Windows Credential Locker）。
   装了 ``keyring`` 且能用时，库里只留一个引用（``keyring:<服务名>``），
   真实 Key 交给操作系统保管。
2. **退化方案：base64 混淆**（``b64:<...>``）。这**不是加密**，只是让人
   随手翻看数据文件时看不到明文——写清楚，避免误解成安全措施。

兼容性：旧文件/旧库里的明文 Key 仍然可以正常读取（原样返回），
下次保存时会按当前能力自动升级成上面两种形式之一。
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "evcharge"
KEYRING_USERNAME = "ai_api_key"

PREFIX_KEYRING = "keyring:"
PREFIX_B64 = "b64:"


def _keyring():
    """按需导入 keyring；没装或不工作时返回 None。"""
    try:
        import keyring  # type: ignore

        backend = keyring.get_keyring()
        name = type(backend).__name__
        # 明确排除「不可用」的后端（keyring 会用它兜底，存了也读不回来）
        if "fail" in name.lower() or "null" in name.lower():
            return None
        return keyring
    except Exception:  # 未安装 / 无桌面环境
        logger.debug("keyring 不可用，退回 base64 混淆", exc_info=True)
        return None


def obfuscate(value: str) -> str:
    """base64 混淆（可逆，仅用于避免明文肉眼可见）。"""
    if not value:
        return ""
    raw = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{PREFIX_B64}{raw}"


def deobfuscate(text: str) -> Optional[str]:
    """还原 base64 混淆值；不是该格式则返回 None。"""
    if not text.startswith(PREFIX_B64):
        return None
    try:
        return base64.b64decode(text[len(PREFIX_B64):].encode("ascii")).decode("utf-8")
    except Exception:
        logger.warning("API Key 解码失败（可能被手工改坏），按空值处理")
        return ""


def protect(value: str) -> str:
    """保存前调用：优先钥匙串，否则 base64 混淆。"""
    if not value:
        return ""
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value)
            return f"{PREFIX_KEYRING}{KEYRING_USERNAME}"
        except Exception:
            logger.debug("写入钥匙串失败，退回 base64 混淆", exc_info=True)
    return obfuscate(value)


def reveal(stored: str) -> str:
    """读取时调用：把存下来的形式还原成真实 Key（明文就原样返回）。"""
    text = stored or ""
    if text.startswith(PREFIX_KEYRING):
        kr = _keyring()
        if kr is None:
            logger.warning("该 Key 存在钥匙串里，但当前环境读不到 keyring")
            return ""
        try:
            return kr.get_password(KEYRING_SERVICE, text[len(PREFIX_KEYRING):]) or ""
        except Exception:
            logger.warning("从钥匙串读取 API Key 失败", exc_info=True)
            return ""
    decoded = deobfuscate(text)
    if decoded is not None:
        return decoded
    return text  # 老文件里的明文


__all__ = ["protect", "reveal", "obfuscate", "deobfuscate"]
