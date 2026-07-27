from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .simulator import historical_power_kw


class Storage:
    def __init__(self, path: str, timezone: str, max_expected_kw: float) -> None:
        self.path = path
        self.timezone = timezone
        self.max_expected_kw = max_expected_kw
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        with self._lock:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    ts REAL PRIMARY KEY,
                    active_import_w REAL NOT NULL,
                    active_export_w REAL NOT NULL,
                    reactive_inductive_var REAL NOT NULL,
                    reactive_capacitive_var REAL NOT NULL,
                    apparent_total_va REAL NOT NULL,
                    energy_import_wh REAL,
                    voltage_l1_v REAL NOT NULL,
                    voltage_l2_v REAL NOT NULL,
                    voltage_l3_v REAL NOT NULL,
                    current_l1_a REAL NOT NULL,
                    current_l2_a REAL NOT NULL,
                    current_l3_a REAL NOT NULL,
                    frequency_hz REAL NOT NULL,
                    power_factor REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
            """)
            self._connection.commit()

    def insert_sample(self, sample: dict[str, Any]) -> None:
        required = ("timestamp", "active_import_w", "active_export_w", "reactive_inductive_var", "reactive_capacitive_var", "apparent_total_va", "voltage_l1_v", "voltage_l2_v", "voltage_l3_v", "current_l1_a", "current_l2_a", "current_l3_a", "frequency_hz")
        for key in required:
            if key not in sample or not math.isfinite(float(sample[key])):
                raise ValueError(f"Invalid sample field: {key}")
        power_factor = _average_power_factor(sample)
        with self._lock:
            self._connection.execute("""
                INSERT OR REPLACE INTO samples (
                    ts, active_import_w, active_export_w, reactive_inductive_var,
                    reactive_capacitive_var, apparent_total_va, energy_import_wh,
                    voltage_l1_v, voltage_l2_v, voltage_l3_v,
                    current_l1_a, current_l2_a, current_l3_a,
                    frequency_hz, power_factor, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                float(sample["timestamp"]), float(sample["active_import_w"]), float(sample["active_export_w"]),
                float(sample["reactive_inductive_var"]), float(sample["reactive_capacitive_var"]), float(sample["apparent_total_va"]),
                _optional_float(sample.get("energy_import_1_wh")), float(sample["voltage_l1_v"]), float(sample["voltage_l2_v"]),
                float(sample["voltage_l3_v"]), float(sample["current_l1_a"]), float(sample["current_l2_a"]), float(sample["current_l3_a"]),
                float(sample["frequency_hz"]), power_factor, json.dumps(sample, ensure_ascii=False, separators=(",", ":")),
            ))
            self._connection.commit()

    def add_event(self, severity: str, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("Invalid event severity")
        with self._lock:
            self._connection.execute("INSERT INTO events(ts,severity,code,message,details_json) VALUES(?,?,?,?,?)", (time.time(), severity, code, message, json.dumps(details or {}, ensure_ascii=False)))
            self._connection.commit()

    def history(self, hours: int, bucket_minutes: int) -> list[dict[str, float]]:
        hours = max(1, min(hours, 24 * 90))
        bucket_minutes = max(1, min(bucket_minutes, 1440))
        start = time.time() - hours * 3600
        bucket_s = bucket_minutes * 60
        with self._lock:
            rows = self._connection.execute("""
                SELECT CAST(ts / ? AS INTEGER) * ? AS bucket_ts,
                       AVG(active_import_w) AS active_import_w,
                       AVG(reactive_inductive_var) AS reactive_inductive_var,
                       AVG(reactive_capacitive_var) AS reactive_capacitive_var,
                       AVG(voltage_l1_v) AS voltage_l1_v,
                       AVG(voltage_l2_v) AS voltage_l2_v,
                       AVG(voltage_l3_v) AS voltage_l3_v,
                       AVG(current_l1_a) AS current_l1_a,
                       AVG(current_l2_a) AS current_l2_a,
                       AVG(current_l3_a) AS current_l3_a,
                       AVG(power_factor) AS power_factor
                FROM samples WHERE ts >= ?
                GROUP BY CAST(ts / ? AS INTEGER)
                ORDER BY bucket_ts
            """, (bucket_s, bucket_s, start, bucket_s)).fetchall()
        return [dict(row) for row in rows]

    def analytics(self, days: int) -> dict[str, Any]:
        days = max(1, min(days, 90))
        start = time.time() - days * 86400
        with self._lock:
            rows = self._connection.execute("SELECT ts, active_import_w, energy_import_wh FROM samples WHERE ts >= ? ORDER BY ts", (start,)).fetchall()
        hourly = self._hourly_consumption(rows)
        heatmap = self._heatmap(hourly)
        ranked = sorted(hourly, key=lambda item: item["kwh"])
        return {"hourly": hourly[-72:], "heatmap": heatmap, "lowest": ranked[0] if ranked else None, "highest": ranked[-1] if ranked else None, "total_kwh": round(sum(float(item["kwh"]) for item in hourly), 3)}

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._connection.execute("SELECT ts,severity,code,message,details_json FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    def prune(self, retention_days: int = 30) -> int:
        cutoff = time.time() - max(1, retention_days) * 86400
        with self._lock:
            cursor = self._connection.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._connection.commit()
            return int(cursor.rowcount)

    def seed_simulator(self, days: int = 14, interval_minutes: int = 15) -> None:
        with self._lock:
            count = self._connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        if count > 0:
            return
        interval_s = interval_minutes * 60
        now = int(time.time() // interval_s) * interval_s
        start = now - days * 86400
        cumulative_wh = 1_000_000.0
        records: list[tuple[Any, ...]] = []
        previous_ts = start
        for ts in range(start, now, interval_s):
            power_kw = historical_power_kw(ts, self.timezone)
            cumulative_wh += power_kw * 1000.0 * ((ts - previous_ts) / 3600.0)
            previous_ts = ts
            pf = 0.93 + 0.035 * math.sin(ts / 4000.0)
            q_ind = power_kw * 1000.0 * 0.24
            q_cap = max(0.0, 900.0 + 500.0 * math.sin(ts / 3400.0))
            apparent = power_kw * 1000.0 / max(pf, 0.1)
            payload = {"timestamp": ts, "active_import_w": power_kw * 1000.0, "active_export_w": 0.0, "reactive_inductive_var": q_ind, "reactive_capacitive_var": q_cap, "apparent_total_va": apparent, "energy_import_1_wh": cumulative_wh}
            records.append((ts, power_kw * 1000.0, 0.0, q_ind, q_cap, apparent, cumulative_wh, 230.1, 231.0, 229.7, power_kw * 1.55, power_kw * 1.50, power_kw * 1.48, 50.0, pf, json.dumps(payload, separators=(",", ":"))))
        with self._lock:
            self._connection.executemany("INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records)
            self._connection.commit()

    def _hourly_consumption(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        buckets: dict[int, float] = {}
        previous: sqlite3.Row | None = None
        for row in rows:
            if previous is None:
                previous = row
                continue
            dt_h = max(0.0, min((row["ts"] - previous["ts"]) / 3600.0, 1.5))
            delta_kwh = _safe_energy_delta_kwh(previous["energy_import_wh"], row["energy_import_wh"], previous["active_import_w"], row["active_import_w"], dt_h, self.max_expected_kw)
            bucket = int(row["ts"] // 3600) * 3600
            buckets[bucket] = buckets.get(bucket, 0.0) + delta_kwh
            previous = row
        result: list[dict[str, Any]] = []
        zone = ZoneInfo(self.timezone)
        for bucket, kwh in sorted(buckets.items()):
            local = datetime.fromtimestamp(bucket, zone)
            result.append({"ts": bucket, "kwh": round(kwh, 3), "weekday": local.weekday(), "hour": local.hour, "label": local.strftime("%d.%m %H:00")})
        return result

    @staticmethod
    def _heatmap(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
        aggregation: dict[tuple[int, int], list[float]] = {}
        for item in hourly:
            aggregation.setdefault((item["weekday"], item["hour"]), []).append(float(item["kwh"]))
        return [{"weekday": day, "hour": hour, "kwh": round(sum(values) / len(values), 3)} for (day, hour), values in sorted(aggregation.items())]


def _safe_energy_delta_kwh(previous_wh: float | None, current_wh: float | None, previous_w: float, current_w: float, elapsed_h: float, max_expected_kw: float) -> float:
    if elapsed_h <= 0:
        return 0.0
    if previous_wh is not None and current_wh is not None:
        delta_wh = float(current_wh) - float(previous_wh)
        max_plausible_wh = max_expected_kw * 1000.0 * elapsed_h * 1.5
        if 0.0 <= delta_wh <= max_plausible_wh:
            return delta_wh / 1000.0
    average_kw = max(0.0, (float(previous_w) + float(current_w)) / 2000.0)
    return min(average_kw * elapsed_h, max_expected_kw * elapsed_h)


def _average_power_factor(sample: dict[str, Any]) -> float:
    values = [float(sample.get(key, 0.0)) for key in ("cos_l1", "cos_l2", "cos_l3")]
    finite = [value for value in values if math.isfinite(value)]
    return sum(finite) / len(finite) if finite else 0.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None
