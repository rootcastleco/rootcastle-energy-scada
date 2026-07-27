from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    mode: str
    device_profile: str
    transport: str
    host: str
    port: int
    serial_port: str
    slave_id: int
    baud_rate: int
    parity: str
    stop_bits: int
    poll_interval_s: float
    energy_poll_interval_s: float
    persist_interval_s: float
    request_timeout_s: float
    database_path: str
    timezone: str
    max_expected_kw: float

    @staticmethod
    def from_env() -> "Settings":
        settings = Settings(
            mode=os.getenv("SCADA_MODE", "simulator").strip().lower(),
            device_profile=os.getenv("DEVICE_PROFILE", "entes-mpr53s").strip().lower(),
            transport=os.getenv("MODBUS_TRANSPORT", "tcp_rtu").strip().lower(),
            host=os.getenv("MODBUS_HOST", "192.168.1.50").strip(),
            port=int(os.getenv("MODBUS_PORT", "5020")),
            serial_port=os.getenv("MODBUS_SERIAL_PORT", "/dev/ttyUSB0").strip(),
            slave_id=int(os.getenv("MODBUS_SLAVE_ID", "1")),
            baud_rate=int(os.getenv("MODBUS_BAUD", "9600")),
            parity=os.getenv("MODBUS_PARITY", "N").strip().upper(),
            stop_bits=int(os.getenv("MODBUS_STOP_BITS", "2")),
            poll_interval_s=float(os.getenv("POLL_INTERVAL_SECONDS", "2")),
            energy_poll_interval_s=float(os.getenv("ENERGY_POLL_INTERVAL_SECONDS", "30")),
            persist_interval_s=float(os.getenv("PERSIST_INTERVAL_SECONDS", "10")),
            request_timeout_s=float(os.getenv("MODBUS_TIMEOUT_SECONDS", "2")),
            database_path=os.getenv("DATABASE_PATH", "./data/energy-scada.sqlite3").strip(),
            timezone=os.getenv("SCADA_TIMEZONE", "Europe/Istanbul").strip(),
            max_expected_kw=float(os.getenv("MAX_EXPECTED_KW", "500")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"simulator", "device"}:
            raise ValueError("SCADA_MODE must be simulator or device")
        if not self.device_profile:
            raise ValueError("DEVICE_PROFILE must not be empty")
        if self.transport not in {"tcp_rtu", "modbus_tcp", "serial_rtu"}:
            raise ValueError("MODBUS_TRANSPORT must be tcp_rtu, modbus_tcp, or serial_rtu")
        if not 1 <= self.slave_id <= 247:
            raise ValueError("MODBUS_SLAVE_ID must be in the range 1..247")
        if self.baud_rate not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise ValueError("MODBUS_BAUD is outside the supported gateway range")
        if self.parity not in {"N", "O", "E"}:
            raise ValueError("MODBUS_PARITY must be N, O, or E")
        if self.stop_bits not in {1, 2}:
            raise ValueError("MODBUS_STOP_BITS must be 1 or 2")
        if not 1 <= self.port <= 65535:
            raise ValueError("MODBUS_PORT must be in the range 1..65535")
        if self.poll_interval_s < 0.5:
            raise ValueError("POLL_INTERVAL_SECONDS must be >= 0.5")
        if self.energy_poll_interval_s < self.poll_interval_s:
            raise ValueError("ENERGY_POLL_INTERVAL_SECONDS must be >= POLL_INTERVAL_SECONDS")
        if self.persist_interval_s < self.poll_interval_s:
            raise ValueError("PERSIST_INTERVAL_SECONDS must be >= POLL_INTERVAL_SECONDS")
        if self.request_timeout_s <= 0 or self.request_timeout_s > 30:
            raise ValueError("MODBUS_TIMEOUT_SECONDS must be in (0, 30]")
        if self.max_expected_kw <= 0:
            raise ValueError("MAX_EXPECTED_KW must be positive")
