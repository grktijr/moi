"""Magnet power supply (current source) drivers.

All controllers expose a common interface (set_voltage_limit, set_current,
output_on/off, meas_current, meas_voltage). Specific subclasses implement
the per-instrument SCPI dialect. open_magnet() resolves a controller name
string to the right class.
"""

from __future__ import annotations

from typing import Optional, Tuple

import pyvisa

from ..config import MagnetCfg


class MagnetControllerBase:
    """Common base for magnet PSU drivers."""

    def __init__(self, inst):
        self.inst = inst
        self.inst.timeout = 10_000  # 10 s per SCPI query

    def idn(self) -> str:
        try:
            return self.inst.query("*IDN?").strip()
        except Exception:
            return "unknown"

    # Subclasses must implement these:
    def set_voltage_limit(self, v: float) -> None: raise NotImplementedError
    def set_current(self, a: float) -> None: raise NotImplementedError
    def output_on(self) -> None: raise NotImplementedError
    def output_off(self) -> None: raise NotImplementedError
    def meas_current(self) -> Optional[float]: raise NotImplementedError
    def meas_voltage(self) -> Optional[float]: raise NotImplementedError


class KeysightE36234A(MagnetControllerBase):
    """Keysight E36234A dual-channel PSU; channel-aware."""

    def __init__(self, inst, channel: int = 1):
        super().__init__(inst)
        self.channel = channel

    def _ch_token(self) -> str:
        return f"(@{self.channel})"

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"VOLT {v:.4f}, {self._ch_token()}")

    def set_current(self, a: float) -> None:
        self.inst.write(f"CURR {a:.6f}, {self._ch_token()}")

    def output_on(self) -> None:
        self.inst.write(f"OUTP ON, {self._ch_token()}")

    def output_off(self) -> None:
        self.inst.write(f"OUTP OFF, {self._ch_token()}")

    def meas_current(self) -> Optional[float]:
        try:
            return float(self.inst.query(f"MEAS:CURR? {self._ch_token()}"))
        except Exception:
            return None

    def meas_voltage(self) -> Optional[float]:
        try:
            return float(self.inst.query(f"MEAS:VOLT? {self._ch_token()}"))
        except Exception:
            return None


class HP6542A(MagnetControllerBase):
    """HP/Agilent 6542A single-channel PSU."""

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"VOLT {v:.4f}")

    def set_current(self, a: float) -> None:
        self.inst.write(f"CURR {a:.6f}")

    def output_on(self) -> None:
        self.inst.write("OUTP ON")

    def output_off(self) -> None:
        self.inst.write("OUTP OFF")

    def meas_current(self) -> Optional[float]:
        try:
            return float(self.inst.query("MEAS:CURR?"))
        except Exception:
            return None

    def meas_voltage(self) -> Optional[float]:
        try:
            return float(self.inst.query("MEAS:VOLT?"))
        except Exception:
            return None


class KeithleyA2400(MagnetControllerBase):
    """Keithley 2400 SourceMeter in current-source mode.

    Uses :READ? with FORM:ELEM VOLT,CURR for concurrent V+I measurement.
    """

    def __init__(self, inst, current_range_a: Optional[float] = None):
        super().__init__(inst)
        self.inst.write("*RST")
        self.inst.write(":SOUR:FUNC CURR")
        self.inst.write(":SENS:FUNC 'VOLT','CURR'")
        self.inst.write(":FORM:ELEM VOLT,CURR")
        if current_range_a is not None:
            self.inst.write(f":SOUR:CURR:RANG {current_range_a}")
        else:
            self.inst.write(":SOUR:CURR:RANG:AUTO ON")
        self.inst.write(":SENS:VOLT:RANG:AUTO ON")
        self.inst.write(":SENS:CURR:RANG:AUTO ON")
        self._meas_warned = False

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f":SENS:VOLT:PROT {v}")

    def set_current(self, a: float) -> None:
        self.inst.write(f":SOUR:CURR:LEV {a}")

    def output_on(self) -> None:
        self.inst.write(":OUTP ON")

    def output_off(self) -> None:
        self.inst.write(":OUTP OFF")

    def _read_vi(self) -> Optional[Tuple[float, float]]:
        try:
            raw = self.inst.query(":READ?").strip()
            parts = raw.split(",")
            if len(parts) < 2:
                return None
            return float(parts[0]), float(parts[1])
        except Exception:
            return None

    def meas_current(self) -> Optional[float]:
        vi = self._read_vi()
        if vi is None and not self._meas_warned:
            print("  [warn] Keithley 2400 measurement readback failing; "
                  "continuing without V/I readback")
            self._meas_warned = True
        return vi[1] if vi is not None else None

    def meas_voltage(self) -> Optional[float]:
        vi = self._read_vi()
        return vi[0] if vi is not None else None


# ----------------------------------------------------------------------------
# Factory: turn a MagnetCfg into an opened controller
# ----------------------------------------------------------------------------

def open_magnet(cfg: MagnetCfg) -> Tuple[MagnetControllerBase, pyvisa.ResourceManager]:
    """Open the VISA resource and return (controller, resource_manager)."""
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(cfg.visa_resource)

    if cfg.controller == "keysight_e36234a":
        ctrl: MagnetControllerBase = KeysightE36234A(
            inst, channel=(cfg.keysight_channel or 1)
        )
    elif cfg.controller == "hp_6542a":
        ctrl = HP6542A(inst)
    elif cfg.controller == "keithley_2400":
        ctrl = KeithleyA2400(
            inst, current_range_a=cfg.keithley_current_range_a
        )
    else:
        rm.close()
        raise ValueError(f"unknown magnet controller: {cfg.controller}")

    return ctrl, rm
