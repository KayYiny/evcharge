# -*- coding: utf-8 -*-
"""evcharge — 充电监控与计费系统（Flet + 分时电价）。

分层结构（依赖只能从外向内：presentation → application → domain）：

    domain          领域模型：桩档案、会话/订单、分时电价、车型（无 IO）
    application     应用服务：充电模拟、会话管理、统计/报告、LLM 助手
    infrastructure  基础设施：SQLite、CSV 导出、配置持久化、认证、路径
    presentation    界面层：pages（各页面） + components（主题/图表/品牌组件）

    cli.py          程序入口（组装各层）

运行方式::

    pip install -e .
    evcharge            # 或 python -m evcharge
"""

from __future__ import annotations

__version__ = "1.1.0"
__all__ = ["__version__"]
