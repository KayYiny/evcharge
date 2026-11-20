# -*- coding: utf-8 -*-
"""应用层业务异常（evcharge.application.errors）。

异常定义放在应用层，基础设施（识别实现、仓储）引用它，
表现层只捕获它——这样界面既不需要 import infrastructure，
也能对「可预期的业务失败」给出友好提示。
"""

from __future__ import annotations


class PlateRecognitionError(RuntimeError):
    """车牌识别失败（未配置 / 图片不合法 / 网络 / 鉴权 / 未识别到车牌）。"""


__all__ = ["PlateRecognitionError"]
