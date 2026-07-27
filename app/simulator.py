from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class EnergySimulator:
    timezone: str
    seed: int = 53
    _rng: random.Random = field(init=False)
    _last_ts: float = field(default_factory=time.time)
    _energy_import_wh: float = 1_842_300.0
    _energy_export_wh: float = 32_100.0
    _energy_inductive_varh: float = 481_900.0
    _energy_capacitive_varh: float = 126_400.0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def sample(self, timestamp: float | None = None) -> dict[str, float | int]:
        now = timestamp if timestamp is not None else time.time()
        local = datetime.fromtimestamp(now, ZoneInfo(self.timezone))
        hour = local.hour + local.minute / 60.0
        weekday_factor = 0.78 if local.weekday() >= 5 else 1.0
        work_curve = 0.16 + 0.84 * _sigmoid(hour - 7.2) * (1.0 - _sigmoid(hour - 18.5))
        lunch_dip = 1.0 - 0.12 * math.exp(-((hour - 12.7) ** 2) / 0.65)
        base_kw = 8.0 + 71.0 * work_curve * lunch_dip * weekday_factor
        ripple = 4.2 * math.sin(now / 51.0) + 1.7 * math.sin(now / 13.0)
        total_kw = max(3.0, base_kw + ripple + self._rng.gauss(0.0, 0.7))
        inductive_kvar = max(0.0, total_kw * (0.23 + 0.05 * math.sin(now / 83.0)))
        capacitive_kvar = max(0.0, 1.1 + 0.8 * math.sin(now / 120.0 + 1.4))
        apparent_kva = math.sqrt(total_kw * total_kw + (inductive_kvar - capacitive_kvar) ** 2)
        pf = min(1.0, total_kw / max(apparent_kva, 0.001))
        elapsed_h = max(0.0, min((now - self._last_ts) / 3600.0, 1.0))
        self._energy_import_wh += total_kw * 1000.0 * elapsed_h
        self._energy_export_wh += max(0.0, 0.25 * math.sin(now / 360.0)) * 1000.0 * elapsed_h
        self._energy_inductive_varh += inductive_kvar * 1000.0 * elapsed_h
        self._energy_capacitive_varh += capacitive_kvar * 1000.0 * elapsed_h
        self._last_ts = now
        phase_share = [0.34, 0.331, 0.329]
        voltages = [230.4 + 1.8 * math.sin(now / 64.0 + i * 2.1) for i in range(3)]
        phase_kw = [total_kw * share for share in phase_share]
        currents = [phase_kw[i] * 1000.0 / max(voltages[i] * pf, 1.0) for i in range(3)]
        phase_q = [inductive_kvar * 1000.0 * share for share in phase_share]
        phase_s = [math.sqrt((phase_kw[i] * 1000.0) ** 2 + phase_q[i] ** 2) for i in range(3)]
        return {
            "timestamp": now,
            "voltage_l1_v": voltages[0], "voltage_l2_v": voltages[1], "voltage_l3_v": voltages[2],
            "current_l1_a": currents[0], "current_l2_a": currents[1], "current_l3_a": currents[2],
            "current_n_a": abs(currents[0] - currents[1]) + abs(currents[1] - currents[2]),
            "voltage_l12_v": voltages[0] * math.sqrt(3.0), "voltage_l23_v": voltages[1] * math.sqrt(3.0), "voltage_l31_v": voltages[2] * math.sqrt(3.0),
            "active_l1_w": phase_kw[0] * 1000.0, "active_l2_w": phase_kw[1] * 1000.0, "active_l3_w": phase_kw[2] * 1000.0,
            "reactive_l1_var": phase_q[0], "reactive_l2_var": phase_q[1], "reactive_l3_var": phase_q[2],
            "apparent_l1_va": phase_s[0], "apparent_l2_va": phase_s[1], "apparent_l3_va": phase_s[2],
            "cos_l1": pf, "cos_l2": min(1.0, pf + 0.004), "cos_l3": max(0.0, pf - 0.003),
            "active_import_w": total_kw * 1000.0, "active_export_w": max(0.0, 250.0 * math.sin(now / 360.0)),
            "reactive_inductive_var": inductive_kvar * 1000.0, "reactive_capacitive_var": capacitive_kvar * 1000.0,
            "apparent_total_va": apparent_kva * 1000.0, "cos_inductive": pf, "cos_capacitive": -pf,
            "frequency_hz": 50.0 + 0.035 * math.sin(now / 22.0),
            "thd_voltage_l1_pct": 2.1 + 0.2 * math.sin(now / 75.0),
            "thd_voltage_l2_pct": 2.3 + 0.2 * math.sin(now / 75.0 + 1.0),
            "thd_voltage_l3_pct": 2.0 + 0.2 * math.sin(now / 75.0 + 2.0),
            "thd_current_l1_pct": 5.3 + 0.6 * math.sin(now / 58.0),
            "thd_current_l2_pct": 5.8 + 0.6 * math.sin(now / 58.0 + 1.0),
            "thd_current_l3_pct": 5.1 + 0.6 * math.sin(now / 58.0 + 2.0),
            "digital_inputs": 1,
            "energy_import_1_wh": int(self._energy_import_wh), "energy_export_1_wh": int(self._energy_export_wh),
            "energy_inductive_1_varh": int(self._energy_inductive_varh), "energy_capacitive_1_varh": int(self._energy_capacitive_varh),
            "energy_import_2_wh": 0, "energy_export_2_wh": 0, "energy_inductive_2_varh": 0, "energy_capacitive_2_varh": 0,
        }


def historical_power_kw(timestamp: float, timezone: str, seed: int = 53) -> float:
    local = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    hour = local.hour + local.minute / 60.0
    weekday_factor = 0.72 if local.weekday() >= 5 else 1.0
    work_curve = 0.18 + 0.82 * _sigmoid(hour - 7.0) * (1.0 - _sigmoid(hour - 18.8))
    lunch_dip = 1.0 - 0.13 * math.exp(-((hour - 12.6) ** 2) / 0.7)
    rng = random.Random(seed + int(timestamp // 900))
    return max(3.0, 8.0 + 70.0 * work_curve * lunch_dip * weekday_factor + rng.gauss(0.0, 2.0))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value * 1.35))
