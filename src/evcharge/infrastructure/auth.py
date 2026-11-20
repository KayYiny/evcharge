# -*- coding: utf-8 -*-
"""认证与密码工具（infrastructure.auth）。

- 单管理员内置账号 admin/admin，密码可修改；
- 车位账号用户名 = 车位名（如 A001），默认密码 admin，管理员可逐个修改；
- 密码使用 PBKDF2-HMAC-SHA256 + 随机盐散列存储。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

DEFAULT_PASSWORD = "admin"
ADMIN_USERNAME = "admin"
ROLE_ADMIN = "admin"
ROLE_SPOT_USER = "spot_user"

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """返回形如 ``pbkdf2_sha256$iterations$salt_hex$hash_hex`` 的密码散列。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与存储散列是否匹配。"""
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(calc, digest)
    except (ValueError, AttributeError):
        return False
