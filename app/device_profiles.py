from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ValueType = Literal["u16", "s16", "u32", "s32", "u64", "s64", "f32", "f64"]
PollClass = Literal["live", "energy"]

_VALUE_WIDTHS: dict[str, int] = {
    "u16": 1,
    "s16": 1,
    "u32": 2,
    "s32": 2,
    "f32": 2,
    "u64": 4,
    "s64": 4,
    "f64": 4,
}


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    key: str
    address: int
    value_type: ValueType
    scale: float
    unit: str
    label: str
    byte_order: str

    @property
    def width(self) -> int:
        return _VALUE_WIDTHS[self.value_type]


@dataclass(frozen=True, slots=True)
class RegisterBlock:
    name: str
    function_code: int
    start: int
    count: int
    poll_class: PollClass
    registers: tuple[RegisterSpec, ...]


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    schema_version: int
    profile_id: str
    manufacturer: str
    model: str
    category: str
    protocol: str
    verification: str
    notes: str
    defaults: dict[str, int | str]
    mandatory_fields: tuple[str, ...]
    blocks: tuple[RegisterBlock, ...]
    source_path: Path

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.model}".strip()

    @property
    def live_blocks(self) -> tuple[RegisterBlock, ...]:
        return tuple(block for block in self.blocks if block.poll_class == "live")

    @property
    def energy_blocks(self) -> tuple[RegisterBlock, ...]:
        return tuple(block for block in self.blocks if block.poll_class == "energy")

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "display_name": self.display_name,
            "category": self.category,
            "protocol": self.protocol,
            "verification": self.verification,
            "notes": self.notes,
            "defaults": dict(self.defaults),
            "blocks": [
                {
                    "name": block.name,
                    "function_code": block.function_code,
                    "start": block.start,
                    "count": block.count,
                    "poll_class": block.poll_class,
                    "field_count": len(block.registers),
                }
                for block in self.blocks
            ],
        }


def load_device_profile(profile_id: str, profiles_dir: Path | None = None) -> DeviceProfile:
    normalized = profile_id.strip().lower()
    if not normalized or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized):
        raise ValueError("DEVICE_PROFILE contains invalid characters")
    root = profiles_dir or Path(__file__).resolve().parent.parent / "profiles"
    path = (root / f"{normalized}.json").resolve()
    if path.parent != root.resolve():
        raise ValueError("DEVICE_PROFILE escaped the profiles directory")
    if not path.is_file():
        available = ", ".join(list_profile_ids(root)) or "none"
        raise FileNotFoundError(f"Device profile '{normalized}' not found. Available: {available}")
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    profile = _parse_profile(raw, path)
    _validate_profile(profile)
    return profile


def list_profile_ids(profiles_dir: Path | None = None) -> tuple[str, ...]:
    root = profiles_dir or Path(__file__).resolve().parent.parent / "profiles"
    if not root.exists():
        return ()
    return tuple(sorted(path.stem for path in root.glob("*.json") if path.is_file()))


def _parse_profile(raw: dict[str, Any], path: Path) -> DeviceProfile:
    blocks: list[RegisterBlock] = []
    for block_raw in _require_list(raw, "blocks"):
        registers: list[RegisterSpec] = []
        for spec_raw in _require_list(block_raw, "registers"):
            value_type = str(spec_raw["value_type"])
            registers.append(
                RegisterSpec(
                    key=str(spec_raw["key"]),
                    address=int(spec_raw["address"]),
                    value_type=value_type,
                    scale=float(spec_raw.get("scale", 1.0)),
                    unit=str(spec_raw.get("unit", "")),
                    label=str(spec_raw.get("label", spec_raw["key"])),
                    byte_order=str(spec_raw.get("byte_order", _default_byte_order(value_type))).upper(),
                )
            )
        blocks.append(
            RegisterBlock(
                name=str(block_raw["name"]),
                function_code=int(block_raw.get("function_code", 3)),
                start=int(block_raw["start"]),
                count=int(block_raw["count"]),
                poll_class=str(block_raw.get("poll_class", "live")),
                registers=tuple(registers),
            )
        )
    defaults_raw = raw.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        raise ValueError("Profile defaults must be an object")
    return DeviceProfile(
        schema_version=int(raw.get("schema_version", 1)),
        profile_id=str(raw["id"]),
        manufacturer=str(raw["manufacturer"]),
        model=str(raw["model"]),
        category=str(raw.get("category", "energy-meter")),
        protocol=str(raw.get("protocol", "modbus")),
        verification=str(raw.get("verification", "experimental")),
        notes=str(raw.get("notes", "")),
        defaults={str(key): value for key, value in defaults_raw.items()},
        mandatory_fields=tuple(str(item) for item in raw.get("mandatory_fields", ())),
        blocks=tuple(blocks),
        source_path=path,
    )


def _validate_profile(profile: DeviceProfile) -> None:
    if profile.schema_version != 1:
        raise ValueError(f"Unsupported profile schema version: {profile.schema_version}")
    if not profile.profile_id or profile.profile_id != profile.source_path.stem:
        raise ValueError("Profile id must match its filename")
    if profile.protocol != "modbus":
        raise ValueError("Only Modbus profiles are currently supported")
    if not profile.blocks:
        raise ValueError("Profile must contain at least one register block")
    keys: set[str] = set()
    for block in profile.blocks:
        if block.function_code not in {3, 4}:
            raise ValueError(f"Block {block.name}: only Modbus function 03/04 are allowed")
        if block.poll_class not in {"live", "energy"}:
            raise ValueError(f"Block {block.name}: invalid poll_class")
        if not 0 <= block.start <= 0xFFFF or not 1 <= block.count <= 125:
            raise ValueError(f"Block {block.name}: invalid start/count")
        if block.start + block.count > 0x10000:
            raise ValueError(f"Block {block.name}: register range overflow")
        if not block.registers:
            raise ValueError(f"Block {block.name}: no register specifications")
        for spec in block.registers:
            if spec.value_type not in _VALUE_WIDTHS:
                raise ValueError(f"Field {spec.key}: unsupported value type {spec.value_type}")
            if spec.key in keys:
                raise ValueError(f"Duplicate canonical field: {spec.key}")
            keys.add(spec.key)
            if spec.address < block.start or spec.address + spec.width > block.start + block.count:
                raise ValueError(f"Field {spec.key}: outside block {block.name}")
            expected_bytes = spec.width * 2
            expected_symbols = "ABCDEFGH"[:expected_bytes]
            if len(spec.byte_order) != expected_bytes or set(spec.byte_order) != set(expected_symbols):
                raise ValueError(f"Field {spec.key}: invalid byte_order {spec.byte_order}")
    missing = [key for key in profile.mandatory_fields if key not in keys]
    if missing:
        raise ValueError(f"Mandatory fields missing from profile: {', '.join(missing)}")


def _require_list(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Profile field '{key}' must be a list of objects")
    return value


def _default_byte_order(value_type: str) -> str:
    width = _VALUE_WIDTHS.get(value_type)
    if width is None:
        raise ValueError(f"Unsupported value type: {value_type}")
    return "ABCDEFGH"[: width * 2]
