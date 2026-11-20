# -*- coding: utf-8 -*-
"""充电功率曲线与功率封顶（collector.charging_model）。

模拟国网高速直流桩/交流桩的实际输出：
1. 桩额定功率与实测修正（标准直流桩约 0.9 倍铭牌）；
2. 车辆峰值功率；
3. 电压平台 × 单枪最大电流（例如 400V × 250A ≈ 100kW）；
4. SOC 曲线：30% 以下爬坡、30%~80% 平台、80% 以后快速衰减；
5. 每个车位单枪。

历史生成器与实时模拟器都调用这里的函数，保证两套数据口径一致。
"""

from __future__ import annotations

from evcharge.domain.charger import ChargerProfile, ChargerType
from evcharge.domain.vehicle import VehicleProfile


def soc_factor(soc_pct: float) -> float:
    """SOC 功率系数，输入 0~100。

    - <30%：0.8 → 1.0 爬坡（低 SOC 电芯需要预热/限流）
    - 30~80%：1.0 平台
    - >80%：从 1.0 线性降到 0.2（接近满电时 CV 段）
    """
    soc = max(0.0, min(100.0, float(soc_pct))) / 100.0
    if soc < 0.3:
        return 0.8 + 0.2 * (soc / 0.3)
    if soc <= 0.8:
        return 1.0
    return max(0.2, 1.0 - (soc - 0.8) * 4.0)


def operating_voltage(charger: ChargerProfile, vehicle: VehicleProfile) -> float:
    """实际充电电压：交流固定 220V；直流取车辆平台与桩最高电压的较小值。"""
    if charger.charger_type == ChargerType.AC:
        return 220.0
    return float(min(vehicle.platform_v, charger.max_voltage_v))


def current_for(power_kw: float, voltage_v: float) -> float:
    if voltage_v <= 0:
        return 0.0
    return power_kw * 1000.0 / voltage_v


def delivered_power_kw(
    charger: ChargerProfile,
    vehicle: VehicleProfile,
    soc_pct: float,
) -> float:
    """当前 SOC 下，桩实际交付给车辆的功率（kW）。"""
    if charger.charger_type == ChargerType.AC:
        base = min(vehicle.ac_power_kw, charger.actual_power_kw)
        taper = 1.0 if soc_pct < 95.0 else max(0.1, 1.0 - (soc_pct - 95.0) / 5.0 * 0.9)
        return max(0.0, base * taper)

    voltage = operating_voltage(charger, vehicle)
    current_cap = charger.max_current_a * voltage / 1000.0
    base = min(vehicle.peak_power_kw, charger.actual_power_kw, current_cap)
    return max(0.0, base * soc_factor(soc_pct))


def fast_charge_minutes(
    charger: ChargerProfile,
    vehicle: VehicleProfile,
    start_soc: float = 10.0,
    end_soc: float = 80.0,
    step: float = 1.0,
) -> float:
    """估算从 start_soc 充到 end_soc 所需分钟数。"""
    if end_soc <= start_soc:
        return 0.0
    total_minutes = 0.0
    soc = start_soc
    while soc < end_soc - 1e-9:
        power = delivered_power_kw(charger, vehicle, soc)
        if power <= 0:
            break
        d_soc = min(step, end_soc - soc)
        energy = vehicle.battery_kwh * d_soc / 100.0
        total_minutes += energy / power * 60.0
        soc += d_soc
    return total_minutes


def energy_for_soc(vehicle: VehicleProfile, start_soc: float, end_soc: float) -> float:
    """两 SOC 之间需要的电量（kWh）。"""
    return max(0.0, vehicle.battery_kwh * (end_soc - start_soc) / 100.0)
