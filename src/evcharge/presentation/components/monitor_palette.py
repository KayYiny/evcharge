# -*- coding: utf-8 -*-
"""监控页状态配色（components.monitor_palette）。

页面与 Mixin 共用同一份映射，避免两边各写一套色值而失同步。
"""

from __future__ import annotations

from evcharge.domain.models import PileStatus
from evcharge.presentation.components import theme

# 车位卡片：文字色 / 背景 / 充电中卡片底色
STATUS_COLORS = {
    PileStatus.IDLE: theme.MUTED,
    PileStatus.CHARGING: theme.GREEN,
}
STATUS_BG = {
    PileStatus.IDLE: theme.SURFACE,
    PileStatus.CHARGING: "#F2F9F5",
}
# 卡片内 SOC 进度条宽度（卡片定宽 218 - 左右 padding 14*2）
SOC_METER_WIDTH = 190

__all__ = ["STATUS_COLORS", "STATUS_BG", "SOC_METER_WIDTH"]
