"""Backward-compatible exports for the verified ENTES MPR-53S profile.

New integrations should load profiles through :mod:`app.device_profiles` instead of
importing this module directly.
"""
from __future__ import annotations

from .device_profiles import RegisterSpec, load_device_profile

_PROFILE = load_device_profile("entes-mpr53s")
_LIVE_BLOCK = next(block for block in _PROFILE.blocks if block.name == "live")
_ENERGY_BLOCK = next(block for block in _PROFILE.blocks if block.name == "energy")

LIVE_REGISTERS: tuple[RegisterSpec, ...] = _LIVE_BLOCK.registers
ENERGY_REGISTERS: tuple[RegisterSpec, ...] = _ENERGY_BLOCK.registers
LIVE_READ_START = _LIVE_BLOCK.start
LIVE_READ_COUNT = _LIVE_BLOCK.count
ENERGY_READ_START = _ENERGY_BLOCK.start
ENERGY_READ_COUNT = _ENERGY_BLOCK.count
