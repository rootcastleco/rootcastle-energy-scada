from __future__ import annotations

import socket
import struct
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import count
from typing import Sequence

from .config import Settings
from .device_profiles import RegisterSpec


class ModbusError(RuntimeError):
    pass


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def decode_u16(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 1), "big", signed=False)


def decode_s16(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 1), "big", signed=True)


def decode_u32(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 2), "big", signed=False)


def decode_s32(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 2), "big", signed=True)


def decode_u64(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 4), "big", signed=False)


def decode_s64(registers: Sequence[int], offset: int) -> int:
    return int.from_bytes(_register_bytes(registers, offset, 4), "big", signed=True)


def decode_specs(registers: Sequence[int], block_start: int, specs: Sequence[RegisterSpec]) -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    for spec in specs:
        offset = spec.address - block_start
        payload = _register_bytes(registers, offset, spec.width)
        canonical = _canonicalize_bytes(payload, spec.byte_order)
        raw = _decode_payload(canonical, spec.value_type)
        values[spec.key] = raw if spec.scale == 1.0 else raw * spec.scale
    return values


def _decode_payload(payload: bytes, value_type: str) -> float | int:
    if value_type == "u16": return int.from_bytes(payload, "big", signed=False)
    if value_type == "s16": return int.from_bytes(payload, "big", signed=True)
    if value_type == "u32": return int.from_bytes(payload, "big", signed=False)
    if value_type == "s32": return int.from_bytes(payload, "big", signed=True)
    if value_type == "u64": return int.from_bytes(payload, "big", signed=False)
    if value_type == "s64": return int.from_bytes(payload, "big", signed=True)
    if value_type == "f32":
        if len(payload) != 4: raise ModbusError("f32 requires exactly 4 bytes")
        return float(struct.unpack(">f", payload)[0])
    if value_type == "f64":
        if len(payload) != 8: raise ModbusError("f64 requires exactly 8 bytes")
        return float(struct.unpack(">d", payload)[0])
    raise ModbusError(f"Unsupported value type: {value_type}")


def _register_bytes(registers: Sequence[int], offset: int, width: int) -> bytes:
    _require(registers, offset, width)
    payload = bytearray()
    for index in range(offset, offset + width):
        value = int(registers[index])
        if not 0 <= value <= 0xFFFF:
            raise ModbusError(f"Register value out of range at offset {index}")
        payload.extend(value.to_bytes(2, "big"))
    return bytes(payload)


def _canonicalize_bytes(payload: bytes, wire_order: str) -> bytes:
    symbols = "ABCDEFGH"[: len(payload)]
    order = wire_order.upper()
    if len(order) != len(payload) or set(order) != set(symbols):
        raise ModbusError(f"Invalid byte order '{wire_order}' for {len(payload)} bytes")
    positions = {symbol: index for index, symbol in enumerate(order)}
    return bytes(payload[positions[symbol]] for symbol in symbols)


def _require(registers: Sequence[int], offset: int, width: int) -> None:
    if offset < 0 or width <= 0: raise ValueError("Invalid register slice")
    if offset + width > len(registers): raise ModbusError("Incomplete register response")


class RegisterClient(ABC):
    @abstractmethod
    def read_registers(self, function_code: int, address: int, count_: int) -> list[int]:
        raise NotImplementedError
    def read_holding_registers(self, address: int, count_: int) -> list[int]: return self.read_registers(3, address, count_)
    def read_input_registers(self, address: int, count_: int) -> list[int]: return self.read_registers(4, address, count_)
    def close(self) -> None: return None


@dataclass(slots=True)
class TcpRtuClient(RegisterClient):
    host: str
    port: int
    slave_id: int
    timeout_s: float

    def read_registers(self, function_code: int, address: int, count_: int) -> list[int]:
        _validate_read(function_code, address, count_)
        pdu = struct.pack(">BBHH", self.slave_id, function_code, address, count_)
        request = pdu + struct.pack("<H", crc16_modbus(pdu))
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendall(request)
            response = _recv_rtu_frame_socket(sock, function_code)
        return _parse_rtu_response(response, self.slave_id, function_code, count_)


class ModbusTcpClient(RegisterClient):
    def __init__(self, host: str, port: int, slave_id: int, timeout_s: float) -> None:
        self.host, self.port, self.slave_id, self.timeout_s = host, port, slave_id, timeout_s
        self._transactions = count(1)
        self._lock = threading.Lock()

    def read_registers(self, function_code: int, address: int, count_: int) -> list[int]:
        _validate_read(function_code, address, count_)
        with self._lock: transaction_id = next(self._transactions) & 0xFFFF
        pdu = struct.pack(">BHH", function_code, address, count_)
        mbap = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, self.slave_id)
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendall(mbap + pdu)
            header = _recv_exact(sock, 7)
            rx_transaction, protocol_id, length, rx_unit = struct.unpack(">HHHB", header)
            if rx_transaction != transaction_id or protocol_id != 0 or rx_unit != self.slave_id: raise ModbusError("Invalid Modbus TCP response header")
            if length < 3 or length > 254: raise ModbusError("Invalid Modbus TCP response length")
            body = _recv_exact(sock, length - 1)
        if len(body) < 2: raise ModbusError("Truncated Modbus TCP response")
        function = body[0]
        if function == (function_code | 0x80): raise ModbusError(f"Device exception code {body[1]}")
        if function != function_code or body[1] != count_ * 2 or len(body) != count_ * 2 + 2: raise ModbusError("Unexpected Modbus TCP payload")
        return list(struct.unpack(f">{count_}H", body[2:]))


class SerialRtuClient(RegisterClient):
    def __init__(self, settings: Settings) -> None:
        try: import serial  # type: ignore
        except ImportError as exc: raise ModbusError("serial_rtu requires pyserial") from exc
        parity = {"N": serial.PARITY_NONE, "O": serial.PARITY_ODD, "E": serial.PARITY_EVEN}[settings.parity]
        self._serial = serial.Serial(port=settings.serial_port, baudrate=settings.baud_rate, bytesize=serial.EIGHTBITS, parity=parity, stopbits=settings.stop_bits, timeout=settings.request_timeout_s)
        self._slave_id = settings.slave_id
        self._lock = threading.Lock()

    def read_registers(self, function_code: int, address: int, count_: int) -> list[int]:
        _validate_read(function_code, address, count_)
        pdu = struct.pack(">BBHH", self._slave_id, function_code, address, count_)
        request = pdu + struct.pack("<H", crc16_modbus(pdu))
        with self._lock:
            self._serial.reset_input_buffer()
            written = self._serial.write(request)
            if written != len(request): raise ModbusError(f"Incomplete serial write: {written}/{len(request)} bytes")
            header = _serial_read_exact(self._serial, 3)
            response = header + _serial_read_exact(self._serial, _rtu_tail_size(header, function_code))
        return _parse_rtu_response(response, self._slave_id, function_code, count_)

    def close(self) -> None: self._serial.close()


def build_client(settings: Settings) -> RegisterClient:
    if settings.transport == "tcp_rtu": return TcpRtuClient(settings.host, settings.port, settings.slave_id, settings.request_timeout_s)
    if settings.transport == "modbus_tcp": return ModbusTcpClient(settings.host, settings.port, settings.slave_id, settings.request_timeout_s)
    if settings.transport == "serial_rtu": return SerialRtuClient(settings)
    raise ModbusError(f"Unsupported transport: {settings.transport}")


def _validate_read(function_code: int, address: int, count_: int) -> None:
    if function_code not in {3, 4}: raise ValueError("Only Modbus read functions 03 and 04 are permitted")
    if not 0 <= address <= 0xFFFF: raise ValueError("Register address out of range")
    if not 1 <= count_ <= 125: raise ValueError("Register count must be in the range 1..125")
    if address + count_ > 0x10000: raise ValueError("Register range overflows address space")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    if size <= 0 or size > 260: raise ValueError("Invalid receive size")
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk: raise ModbusError("Connection closed before complete response")
        chunks.extend(chunk)
    return bytes(chunks)


def _rtu_tail_size(header: bytes, expected_function: int = 3) -> int:
    if len(header) != 3: raise ModbusError("Incomplete Modbus RTU header")
    function = header[1]
    if function & 0x80:
        if function != (expected_function | 0x80): raise ModbusError(f"Unexpected exception function: 0x{function:02X}")
        return 2
    if function != expected_function: raise ModbusError(f"Unexpected Modbus RTU function: 0x{function:02X}")
    byte_count = header[2]
    if byte_count <= 0 or byte_count % 2 != 0 or byte_count > 250: raise ModbusError(f"Invalid Modbus RTU byte count: {byte_count}")
    return byte_count + 2


def _recv_rtu_frame_socket(sock: socket.socket, expected_function: int) -> bytes:
    header = _recv_exact(sock, 3)
    return header + _recv_exact(sock, _rtu_tail_size(header, expected_function))


def _serial_read_exact(serial_port: object, size: int) -> bytes:
    if size <= 0 or size > 260: raise ValueError("Invalid serial receive size")
    chunks = bytearray()
    while len(chunks) < size:
        chunk = serial_port.read(size - len(chunks))
        if not chunk: raise ModbusError(f"Serial timeout: expected {size} bytes, received {len(chunks)}")
        chunks.extend(chunk)
    return bytes(chunks)


def _parse_rtu_response(response: bytes, slave_id: int, expected_function: int, count_: int) -> list[int]:
    if len(response) < 5: raise ModbusError("Truncated Modbus RTU response")
    payload, received_crc = response[:-2], struct.unpack("<H", response[-2:])[0]
    if crc16_modbus(payload) != received_crc: raise ModbusError("CRC mismatch")
    if response[0] != slave_id: raise ModbusError("Unexpected slave address")
    if response[1] == (expected_function | 0x80): raise ModbusError(f"Device exception code {response[2]}")
    if response[1] != expected_function or response[2] != count_ * 2: raise ModbusError("Unexpected RTU response")
    if len(response) != 3 + count_ * 2 + 2: raise ModbusError("RTU response length mismatch")
    return list(struct.unpack(f">{count_}H", response[3:-2]))
