# -*- coding: utf-8 -*-
"""后台采集循环（evcharge.application.runtime）。

界面与「计时」解耦：循环体只做一件事——推进一次仿真、把自动结算的订单交出去。
这样带来两个好处：

1. **可单测**：不需要 Flet 窗口就能验证「异常不会中断循环」「达到目标会结算」；
2. **行为稳定**：切页、最小化、退出登录都不影响计时，因为循环不依赖任何页面控件。

页面刷新由调用方在 ``on_tick`` 回调里决定，循环本身不碰 UI。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, List, Optional

from evcharge.application.session_manager import SessionManager
from evcharge.domain.models import Order

logger = logging.getLogger(__name__)


class ChargingRuntime:
    """充电仿真节拍器。

    - :meth:`tick_once` —— 推进一步，返回本轮自动结算的订单（异常不外抛）；
    - :meth:`run_forever` —— 按 ``interval_s`` 连续跑，永不因单次失败而退出。
    """

    def __init__(
        self,
        manager: SessionManager,
        interval_s: float = 2.0,
        on_completed: Optional[Callable[[List[Order]], None]] = None,
        on_tick: Optional[Callable[[], None]] = None,
    ) -> None:
        self.manager = manager
        self.interval_s = max(0.5, float(interval_s))
        self._on_completed = on_completed
        self._on_tick = on_tick
        # 统计信息：便于日志/排障确认「循环真的在跑」
        self.ticks = 0
        self.errors = 0

    # ---------- 单步 ----------
    def tick_once(self) -> List[Order]:
        """推进一次仿真；任何异常都只记录，不向调用方抛出。"""
        try:
            completed = self.manager.tick()
        except Exception:  # 单次失败不能让充电计时停摆
            self.errors += 1
            logger.exception("采集循环单步失败（已跳过，不影响后续计时）")
            return []
        self.ticks += 1
        if completed and self._on_completed is not None:
            try:
                self._on_completed(completed)
            except Exception:
                logger.exception("处理自动结算订单失败")
        if self._on_tick is not None:
            try:
                self._on_tick()
            except Exception:
                logger.exception("刷新界面失败（计时不受影响）")
        return completed

    # ---------- 循环 ----------
    async def run_forever(self) -> None:
        """按固定间隔持续运行；只有进程退出才会结束。"""
        while True:
            self.tick_once()
            await asyncio.sleep(self.interval_s)


__all__ = ["ChargingRuntime"]
