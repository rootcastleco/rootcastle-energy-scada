import json
import struct
from pathlib import Path

import pytest

from app.device_profiles import RegisterSpec, list_profile_ids, load_device_profile
from app.modbus import decode_specs


def test_verified_profile_loads() -> None:
    assert "entes-mpr53s" in list_profile_ids()
    profile = load_device_profile("entes-mpr53s")
    assert profile.verification == "verified"
    assert profile.live_blocks
    assert profile.energy_blocks
    assert "active_import_w" in profile.mandatory_fields


def test_float32_word_swap_decode() -> None:
    canonical = struct.pack(">f", 230.5)
    wire = canonical[2:4] + canonical[0:2]
    registers = [int.from_bytes(wire[0:2], "big"), int.from_bytes(wire[2:4], "big")]
    spec = RegisterSpec("voltage_l1_v", 100, "f32", 1.0, "V", "L1", "CDAB")
    decoded = decode_specs(registers, 100, (spec,))
    assert decoded["voltage_l1_v"] == pytest.approx(230.5)


def test_invalid_profile_is_rejected(tmp_path: Path) -> None:
    profile = {
        "schema_version": 1,
        "id": "broken",
        "manufacturer": "Test",
        "model": "Broken",
        "protocol": "modbus",
        "mandatory_fields": ["voltage_l1_v"],
        "blocks": [{
            "name": "live",
            "function_code": 6,
            "start": 0,
            "count": 2,
            "poll_class": "live",
            "registers": [{"key": "voltage_l1_v", "address": 0, "value_type": "u32"}],
        }],
    }
    (tmp_path / "broken.json").write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="function 03/04"):
        load_device_profile("broken", tmp_path)
