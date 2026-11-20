# -*- coding: utf-8 -*-
"""纯电车型档案与随机生成（evcharge.domain.vehicle）。

数据来源：用户提供的 2025-2026 主流纯电轿车/SUV 参数汇总表及
《新能源汽车充电电池相关数据汇总表.xlsx》。

生成逻辑：
- 先按类别（轿车 55% / SUV 45%）抽样；
- 再按车型权重抽样具体车型；
- 电池容量在该车型的配置区间内做截断正态，峰值功率在区间内均匀；
- 最后做物理约束：小电池不超高功率、C 率不超过 10C、400V 车型峰值不超过 250kW 等。

注意：这是“高速服务区”场景，车型以中长途出行的紧凑型/中型/中大型车为主，
不加入宏光 MINIEV 这类微型代步车；因此整体平均电池容量会高于行业全口径均值。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class VehicleProfile:
    """一辆车的静态档案。"""

    category: str  # 轿车 / SUV
    segment: str  # 入门/紧凑、中型、中大型/旗舰 等
    model: str  # 参考车型名
    battery_kwh: float
    peak_power_kw: float  # 车辆侧峰值充电功率
    platform_v: int  # 400 / 800 / 1000
    ac_power_kw: float  # 交流慢充功率 7/11/22

    @property
    def label(self) -> str:
        return f"{self.model}（{self.category}/{self.segment}）"


@dataclass(frozen=True)
class ModelSpec:
    """车型目录条目：电池/峰值功率允许一个配置区间。"""

    category: str
    segment: str
    name: str
    battery_low: float
    battery_high: float
    peak_low: float
    peak_high: float
    platform_v: int
    weight: float


# ---------------------------------------------------------------------------
# 车型目录（参考 2025-2026 纯电轿车/SUV 参数表与 xlsx 汇总）
# 权重是粗略市场份额，后续集中在 ModelSpec 里调整即可。
# ---------------------------------------------------------------------------
MODEL_CATALOG: Tuple[ModelSpec, ...] = (
    # ---------------- 轿车：入门/紧凑 ----------------
    ModelSpec("轿车", "入门/紧凑", "比亚迪秦PLUS EV", 47.5, 57.6, 90, 150, 400, 5),
    ModelSpec("轿车", "入门/紧凑", "比亚迪海豚", 44.9, 45.3, 60, 100, 400, 2),
    ModelSpec("轿车", "入门/紧凑", "小鹏M03", 51.8, 62.4, 120, 160, 400, 4),
    ModelSpec("轿车", "入门/紧凑", "深蓝L07", 56.12, 56.12, 150, 180, 400, 2),
    ModelSpec("轿车", "入门/紧凑", "比亚迪海豹06 EV", 60.5, 72.8, 120, 180, 400, 3),
    # ---------------- 轿车：中型 ----------------
    ModelSpec("轿车", "中型", "特斯拉Model 3", 60.0, 78.4, 170, 250, 400, 6),
    ModelSpec("轿车", "中型", "小鹏G6", 66.0, 87.5, 280, 382, 800, 5),
    ModelSpec("轿车", "中型", "比亚迪汉EV", 65.5, 85.4, 120, 200, 400, 5),
    ModelSpec("轿车", "中型", "比亚迪海豹EV", 61.4, 82.6, 230, 350, 800, 4),
    ModelSpec("轿车", "中型", "极氪007", 75.0, 100.0, 350, 500, 800, 4),
    ModelSpec("轿车", "中型", "智己L6", 75.0, 100.0, 300, 400, 800, 3),
    # ---------------- 轿车：中大型/旗舰 ----------------
    ModelSpec("轿车", "中大型/旗舰", "小米SU7", 94.3, 94.3, 480, 480, 800, 3),
    ModelSpec("轿车", "中大型/旗舰", "智界S7", 100.0, 100.0, 350, 450, 800, 3),
    ModelSpec("轿车", "中大型/旗舰", "红旗EH7", 111.0, 111.0, 350, 450, 800, 2),
    ModelSpec("轿车", "中大型/旗舰", "奔驰EQS", 122.0, 122.0, 200, 200, 400, 1),
    ModelSpec("轿车", "中大型/旗舰", "仰望U7", 135.5, 135.5, 500, 500, 800, 1),
    ModelSpec("轿车", "中大型/旗舰", "比亚迪汉L EV", 100.0, 100.0, 1000, 1000, 1000, 2),
    # ---------------- SUV：小型/入门 ----------------
    ModelSpec("SUV", "小型/入门", "比亚迪元PLUS", 49.9, 60.5, 120, 150, 400, 4),
    ModelSpec("SUV", "小型/入门", "吉利银河E5", 49.5, 60.2, 120, 150, 400, 3),
    ModelSpec("SUV", "小型/入门", "零跑B10", 69.7, 69.7, 210, 210, 800, 2),
    ModelSpec("SUV", "小型/入门", "MG 4X", 53.9, 64.2, 120, 150, 400, 2),
    # ---------------- SUV：中型 ----------------
    ModelSpec("SUV", "中型", "特斯拉Model Y", 60.0, 78.4, 250, 250, 400, 6),
    ModelSpec("SUV", "中型", "小鹏G6", 68.5, 80.8, 382, 451, 800, 4),
    ModelSpec("SUV", "中型", "腾势N7", 91.3, 91.3, 230, 350, 800, 3),
    ModelSpec("SUV", "中型", "小鹏G9", 79.1, 79.1, 530, 530, 800, 3),
    ModelSpec("SUV", "中型", "奕派M8", 77.38, 77.38, 210, 210, 800, 2),
    # ---------------- SUV：中大型/旗舰 ----------------
    ModelSpec("SUV", "中大型/旗舰", "比亚迪大唐EV", 130.15, 130.15, 500, 600, 800, 2),
    ModelSpec("SUV", "中大型/旗舰", "极氪7X", 103.0, 103.0, 400, 500, 800, 3),
    ModelSpec("SUV", "中大型/旗舰", "智己LS6", 100.0, 100.0, 400, 400, 800, 3),
    ModelSpec("SUV", "中大型/旗舰", "蔚来ES8", 102.0, 102.0, 500, 500, 800, 2),
    ModelSpec("SUV", "中大型/旗舰", "阿维塔11", 116.79, 116.79, 350, 450, 800, 2),
)


def _normal_in_range(low: float, high: float, rng: random.Random) -> float:
    """在 [low, high] 内生成截断正态；区间为 0 时返回该值。"""
    if high <= low:
        return float(low)
    mean = (low + high) / 2.0
    sd = max((high - low) / 4.0, 0.3)
    return min(high, max(low, rng.gauss(mean, sd)))


def _uniform_in_range(low: float, high: float, rng: random.Random) -> float:
    if high <= low:
        return float(low)
    return rng.uniform(low, high)


class VehicleProfileGenerator:
    """按车型目录生成车辆档案。"""

    def __init__(
        self,
        rng: random.Random | None = None,
        category_weights: Tuple[float, float] = (0.55, 0.45),
    ) -> None:
        self._rng = rng or random.Random()
        self.category_weights = category_weights

    def generate(self, rng: random.Random | None = None) -> VehicleProfile:
        rng = rng or self._rng
        category = rng.choices(("轿车", "SUV"), weights=self.category_weights, k=1)[0]
        models = [m for m in MODEL_CATALOG if m.category == category]
        spec = rng.choices(models, weights=[m.weight for m in models], k=1)[0]

        battery = round(_normal_in_range(spec.battery_low, spec.battery_high, rng), 1)
        peak = _uniform_in_range(spec.peak_low, spec.peak_high, rng)

        # 物理约束：小电池不承受超高功率；C 率不超过 10C；400V 峰值不超过 250kW。
        if battery < 60:
            peak = min(peak, 200.0)
        elif battery < 80:
            peak = min(peak, 450.0)
        else:
            peak = min(peak, 1000.0)
        peak = min(peak, battery * 10.0)
        if spec.platform_v == 400:
            peak = min(peak, 250.0)
        elif spec.platform_v == 800:
            peak = min(peak, 600.0)
        peak = round(max(peak, 30.0), 0)

        ac_power = self._sample_ac_power(spec.segment, rng)
        return VehicleProfile(
            category=spec.category,
            segment=spec.segment,
            model=spec.name,
            battery_kwh=battery,
            peak_power_kw=peak,
            platform_v=spec.platform_v,
            ac_power_kw=ac_power,
        )

    @staticmethod
    def _sample_ac_power(segment: str, rng: random.Random) -> float:
        if "旗舰" in segment or "中大型" in segment:
            options = ((11.0, 0.5), (22.0, 0.5))
        elif "中型" in segment:
            options = ((7.0, 0.5), (11.0, 0.4), (22.0, 0.1))
        else:
            options = ((7.0, 0.8), (11.0, 0.2))
        values = [v for v, _w in options]
        weights = [w for _v, w in options]
        return rng.choices(values, weights=weights, k=1)[0]

