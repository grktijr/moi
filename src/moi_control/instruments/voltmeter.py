"""Nanovoltmeter drivers (measurement instruments, not current sources).

Separate from magnet.py: these READ voltage, they do not source current. A
2182A pairs with a current source to give resistance: R = V / I.
"""

from __future__ import annotations

from typing import Optional

import pyvisa


class Keithley2182A:
    """Keithley 2182A nanovoltmeter for sample voltage / resistance monitoring.

    Measures voltage on channel 1 (CH2 available for a future 4-probe /
    differential upgrade). The 2182A does not know the source current, so
    resistance is computed by the caller as V / I.

    probe_mode is advisory metadata ("2-probe" now, "4-probe" later). The 2182A
    is wired identically either way — the difference is in the SAMPLE wiring
    (shared vs. separate I/V leads). Flip probe_mode (and, for a differential
    4-probe, channel) when the sample is rewired.
    """

    def __init__(self, inst, channel: int = 1, probe_mode: str = "2-probe",
                 nplc: float = 5.0):
        self.inst = inst
        self.channel = channel
        self.probe_mode = probe_mode
        self.inst.write("*RST")
        self.inst.query("*OPC?")
        self.inst.write("*CLS")
        self.inst.write("SENS:FUNC 'VOLT'")
        self.inst.write(f"SENS:CHAN {channel}")
        self.inst.write(f"SENS:VOLT:NPLC {nplc:.2f}")
        self.inst.write("SENS:VOLT:RANG:AUTO ON")
        self.inst.query("*OPC?")

    def idn(self) -> str:
        return self.inst.query("*IDN?").strip()

    def read_voltage(self) -> Optional[float]:
        """Single voltage reading in volts (None on failure)."""
        try:
            return float(self.inst.query("READ?").strip())
        except Exception:
            return None

    def read_resistance(self, current_a: Optional[float]) -> Optional[float]:
        """R = read_voltage() / current_a (None if V read fails or I is 0/None)."""
        if current_a is None or current_a == 0.0:
            return None
        v = self.read_voltage()
        return None if v is None else v / current_a

    def close(self) -> None:
        try:
            self.inst.close()
        except Exception:
            pass


def open_voltmeter(visa_resource: str, channel: int = 1,
                   probe_mode: str = "2-probe", nplc: float = 5.0):
    """Open a 2182A as a standalone measurement instrument.
    Returns (driver, resource_manager)."""
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(visa_resource)
    ctrl = Keithley2182A(inst, channel=channel, probe_mode=probe_mode, nplc=nplc)
    return ctrl, rm
