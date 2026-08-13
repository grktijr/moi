"""Maps short PSU names (used in CLI --psu flag) to driver classes,
VISA addresses, and per-PSU keyword arguments.

Edit this file when adding new PSUs or when hardware changes (cable
swaps that change VISA addresses, channel reconfigurations, etc.).
"""

from __future__ import annotations

from typing import Dict, Any


PSU_REGISTRY: Dict[str, Dict[str, Any]] = {
    "keysight": {
        "driver": "keysight_e36234a",
        "visa":   "USB0::0x2A8D::0x3402::MY59001913::INSTR",
        "extras": {"keysight_channel": 2},
    },
    "hp": {
        "driver": "hp_6542a",
        "visa":   "GPIB0::5::INSTR",
        "extras": {},
    },
    "k2400": {
        "driver": "keithley_2400",
        "visa":   "GPIB0::24::INSTR",
        "extras": {"keithley_current_range_a": 0.1},
    },
    "k6221_dc": {
        "driver": "keithley_6221_dc",
        "visa": "GPIB0::30::INSTR",
        "extras": {"keithley_current_range_a": 0.1},
    },
    "k6221_ac": {
        "driver": "keithley_6221_ac",
        "visa": "GPIB0::30::INSTR",
        "extras": {"keithley_current_range_a": 0.1},
    }
}
