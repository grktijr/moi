"""Magnet power supply (current source) drivers.

All controllers expose a common interface (set_voltage_limit, set_current,
output_on/off, meas_current, meas_voltage). Specific subclasses implement
the per-instrument SCPI dialect. open_magnet() resolves a controller name
string to the right class.
"""

from __future__ import annotations

import time

from typing import Optional, Tuple

import pyvisa

from ..config import MagnetCfg


class MagnetControllerBase:
    """Common base for magnet PSU drivers."""
    
    # Subclasses override this to declare bipolar capability
    is_bipolar: bool = False
    supports_ac: bool = False
    
    def __init__(self, inst):
        self.inst = inst
        self.inst.timeout = 10_000
    
    def idn(self) -> str:
        try:
            return self.inst.query("*IDN?").strip()
        except Exception:
            return "unknown"
    
    def set_current(self, a: float) -> None:
        """Set the source current in A.
        
        For bipolar PSUs, the signed value is sent as-is to the hardware.
        For unipolar PSUs, the magnitude is sent; the sign is presumed to
        be provided by the user manually reversing coil leads.
        """
        if self.is_bipolar:
            self._write_current(a)
        else:
            self._write_current(abs(a))
    
    # Subclasses must implement these:
    def _write_current(self, a: float) -> None: raise NotImplementedError
    def set_voltage_limit(self, v: float) -> None: raise NotImplementedError
    def output_on(self) -> None: raise NotImplementedError
    def output_off(self) -> None: raise NotImplementedError
    def meas_current(self) -> Optional[float]: raise NotImplementedError
    def meas_voltage(self) -> Optional[float]: raise NotImplementedError


class KeysightE36234A(MagnetControllerBase):
    """Keysight E36234A dual-channel PSU; channel-aware."""
    is_bipolar = False

    def __init__(self, inst, channel: int = 1):
        super().__init__(inst)
        self.channel = channel

    def _ch_token(self) -> str:
        return f"(@{self.channel})"

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"VOLT {v:.4f}, {self._ch_token()}")

    def _write_current(self, a: float) -> None:
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
    is_bipolar = False

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"VOLT {v:.4f}")

    def _write_current(self, a: float) -> None:
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
    is_bipolar = True

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

    def _write_current(self, a: float) -> None:
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
# Keithley 6221 DC and AC current source
# 
# Two operating modes exposed as separate driver classes:
#   - Keithley6221DC: ordinary DC current source.
#   - Keithley6221AC: AC waveform with optional DC offset.
# ----------------------------------------------------------------------------


class Keithley6221DC(MagnetControllerBase):
    """Keithley 6221 in DC current source mode."""
    is_bipolar = True

    def __init__(self, inst, current_range_a: float | None = None):
        """
        Parameters
        ----------
        inst : pyvisa resource
            Already-opened VISA instrument handle.
        current_range_a : float, optional
            Manual current range in A (e.g., 0.02 for 20 mA range, 0.1 for
            100 mA range). If None, autorange is enabled.
        """
        super().__init__(inst)
        self.inst.write("*RST")
        self.inst.write("*CLS")
        if current_range_a is None:
            self.inst.write("SOUR:CURR:RANG:AUTO ON")
        else:
            self.inst.write("SOUR:CURR:RANG:AUTO OFF")
            self.inst.write(f"SOUR:CURR:RANG {current_range_a:.6f}")
        self.inst.write("SOUR:CURR:LEV 0.0")

    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"SOUR:CURR:COMP {v:.4f}")

    def _write_current(self, i: float) -> None:
        self.inst.write(f"SOUR:CURR:LEV {i:.6f}")

    def meas_current(self) -> float | None:
        # The 6221 in DC mode doesn't measure its actual output current; it
        # only reports the commanded setpoint. For real measurement, pair
        # with a Keithley 2182A nanovoltmeter via the trigger link, or use
        # a DMM in series. For now, echo back the setpoint.
        try:
            return float(self.inst.query("SOUR:CURR:LEV?").strip())
        except Exception:
            return None

    def meas_voltage(self) -> float | None:
        # 6221 has no built-in load-voltage measurement.
        return None

    def output_on(self) -> None:
        self.inst.write("OUTP ON")

    def output_off(self) -> None:
        self.inst.write("OUTP OFF")


class Keithley6221AC(MagnetControllerBase):
    """Keithley 6221 in AC waveform mode (sine, square, triangle, etc.)
    with optional DC offset.

    Usage pattern:
        ctrl = Keithley6221AC(inst, current_range_a=0.02)
        ctrl.set_voltage_limit(10.0)
        ctrl.set_ac_waveform(amplitude_a=0.001, freq_hz=1000, shape="SIN")
        ctrl.set_current(0.0)         # DC offset, optional
        ctrl.output_on()              # arm and start
        ...
        ctrl.output_off()             # abort and disable

    Calling set_current() on this class sets the DC offset of the waveform.
    The AC amplitude is set separately via set_ac_waveform().
    """
    is_bipolar = True
    supports_ac = True

    def __init__(self, inst, current_range_a: float | None = None):
        super().__init__(inst)
        self.inst.write("*RST")
        self.inst.query("*OPC?")          # let reset finish before configuring
        self.inst.write("*CLS")
        self._range_a = current_range_a   # kept for WAVE:RANG selection
        # Seed default waveform parameters; experiments override via
        # set_ac_waveform before output_on. RANG BEST lets the waveform
        # generator pick a range that fits the amplitude (otherwise the
        # amplitude can clamp to 0).
        self.inst.write("SOUR:WAVE:FUNC SIN")
        self.inst.write("SOUR:WAVE:FREQ 1e-3")
        self.inst.write("SOUR:WAVE:AMPL 1e-4")
        self.inst.write("SOUR:WAVE:OFFS 0.0")
        self.inst.write("SOUR:WAVE:RANG BEST")
        self.inst.write("SOUR:WAVE:DUR:TIME INF")
        self.inst.query("*OPC?")          # config settled
        # Tracked waveform state. A re-arm re-latches ALL parameters from
        # scratch, so every re-arm must re-write offset+amplitude+frequency+
        # function together (the verified test re-wrote offset with amplitude);
        # writing only the changed one loses the others. _rearm() does this.
        self._shape = "SIN"
        self._freq_hz = 1e-3
        self._amplitude_a = 1e-4
        self._offset_a = 0.0
 
    def set_voltage_limit(self, v: float) -> None:
        self.inst.write(f"SOUR:CURR:COMP {v:.4f}")
 
    def _write_current(self, i: float) -> None:
        """Set the DC offset of the AC waveform."""
        self.inst.write(f"SOUR:WAVE:OFFS {i:.6f}")
 
    def set_ac_waveform(
        self,
        range_a: float,
        offset_a: float,
        amplitude_a: float,
        freq_hz: float,
        shape: str = "SIN",
    ) -> None:
        """Configure the AC waveform. Call before output_on().
 
        Parameters
        ----------
        amplitude_a : float
            Peak amplitude of the AC component in A.
        freq_hz : float
            Frequency in Hz.
        shape : str
            One of "SIN", "SQU", "TRI", "RAMP". Default "SIN".
        """
        if shape not in ("SIN", "SQU", "TRI", "RAMP"):
            raise ValueError(f"unsupported waveform shape: {shape}")
        self._shape = shape
        self._freq_hz = freq_hz
        self._range_a = range_a
        self._amplitude_a = amplitude_a
        self._offset_a = offset_a
        self.inst.write(f"SOUR:WAVE:FUNC {shape}")
        self.inst.write(f"SOUR:WAVE:FREQ {freq_hz:.6f}")
        self.inst.write(f"SOUR:WAVE:AMPL {amplitude_a:.6e}")
        self.inst.write(f"SOUR:WAVE:OFFS {offset_a:.6f}")
        # NOTE: RANG BEST is fine here (pre-arm config). For sweeps that change
        # amplitude after arming, pin the range with set_wave_range() first so
        # a re-arm never crosses a range boundary.
        if self._range_a is None:
            self.inst.write("SOUR:WAVE:RANG BEST")
        else:
            self.inst.write(f"SOUR:CURR:RANG {range_a:.6f}")
            self.inst.write(f"SOUR:WAVE:RANG FIX")
        self.inst.write("SOUR:WAVE:DUR:TIME INF")
        self.inst.query("*OPC?")                 # params stored before arm

    def set_dc_offset(self, offset_a: float) -> None:
        """Set the DC offset of the waveform and (re)arm it.

        OFFSET IS LATCHED at arm time on this 6221 (verified): a bare
        SOUR:WAVE:OFFS write on a running waveform is ignored, so offset
        changes must abort -> write -> re-arm, with no *OPC? between the OFFS
        write and ARM (an intervening *OPC? flushes the staged offset).

        Amplitude and frequency are re-written here too so a re-arm doesn't
        drop them (a re-arm re-latches all params from scratch).
        """
        self._offset_a = offset_a
        self.inst.write("SOUR:WAVE:ABOR")
        time.sleep(0.2)
        self.inst.write(f"SOUR:WAVE:FUNC {self._shape}")
        self.inst.write(f"SOUR:WAVE:FREQ {self._freq_hz:.6f}")
        self.inst.write(f"SOUR:WAVE:AMPL {self._amplitude_a:.6e}")
        self.inst.write(f"SOUR:WAVE:OFFS {offset_a:.6f}")
        self.inst.write("SOUR:WAVE:ARM")
        self.inst.write("SOUR:WAVE:INIT")

    def set_ac_amplitude(self, amplitude_a: float) -> None:
        """Change AC amplitude on a RUNNING waveform with a bare write.

        AMPLITUDE IS HOT-CHANGEABLE on this 6221 (verified): writing
        SOUR:WAVE:AMPL takes effect immediately on the running waveform, with
        offset and frequency undisturbed and NO re-arm. Pin the range with
        set_wave_range() before a sweep so the amplitude never crosses a range
        boundary (which would itself force a re-arm).
        """
        self._amplitude_a = amplitude_a
        self.inst.write(f"SOUR:WAVE:AMPL {amplitude_a:.6e}")

    def set_ac_frequency(self, freq_hz: float) -> None:
        """Change AC frequency on a RUNNING waveform with a bare write.

        Treated as hot-changeable like amplitude (SOUR:WAVE:FREQ on the
        running waveform). If a future test shows frequency is latched,
        change this to the abort/re-arm form used by set_dc_offset.
        """
        self._freq_hz = freq_hz
        self.inst.write(f"SOUR:WAVE:FREQ {freq_hz:.6f}")

    def set_wave_range(self, range_a: float) -> None:
        """Pin the waveform current range (disables auto/BEST ranging).

        Call before an amplitude sweep so raising the amplitude never crosses
        a range boundary (which would force an extra re-arm). range_a must
        cover |offset| + max amplitude, within the 6221's 105% headroom.
        """
        self._range_a = range_a
        self.inst.write("SOUR:CURR:RANG:AUTO OFF")
        self.inst.write(f"SOUR:CURR:RANG {range_a:.6f}")
        self.inst.write(f"SOUR:WAVE:RANG FIX")
 
    def meas_current(self) -> float | None:
        # Like the DC class, no actual measurement available.
        # In AC mode, the "level" query doesn't apply; return None.
        return None
 
    def meas_voltage(self) -> float | None:
        return None
 
    def output_on(self) -> None:
        """Arm the waveform (wait for arming), then start it.
 
        Uses SOUR:WAVE:INIT (the waveform-generator initiate), not the
        generic INIT:IMM, and an *OPC? barrier after ARM. This matches the
        sequence verified to start the waveform on this 6221; INIT:IMM
        initiates the trigger model instead and leaves the wave at 0.
        """
        self.inst.write("SOUR:WAVE:ARM")
        self.inst.query("*OPC?")          # arming complete before init
        self.inst.write("SOUR:WAVE:INIT")
 
    def output_off(self) -> None:
        """Abort the waveform and disable output."""
        self.inst.write("SOUR:WAVE:ABOR")
        time.sleep(0.2)                   # let abort unwind (no *OPC? on INF wave)
        self.inst.write("OUTP OFF")

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
    elif cfg.controller == "keithley_6221_dc":
        ctrl = Keithley6221DC(
            inst, current_range_a=cfg.keithley_current_range_a
        )
    elif cfg.controller == "keithley_6221_ac":
        ctrl = Keithley6221AC(
            inst, current_range_a=cfg.keithley_current_range_a
        )
    else:
        rm.close()
        raise ValueError(f"unknown magnet controller: {cfg.controller}")

    return ctrl, rm


def open_sample_source(visa_resource: str, current_range_a: float | None = None,
                       driver: str = "ac"
                       ) -> Tuple[MagnetControllerBase, pyvisa.ResourceManager]:
    """Open a second 6221 as a direct current source into the sample
    (not a magnet/field source).
 
    driver="ac" -> Keithley6221AC (waveform mode, for AC sample sweeps).
    driver="dc" -> Keithley6221DC (plain DC source, for DC pulse trains / I-V).
 
    Returns (driver, resource_manager).
    """
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(visa_resource)
    if driver == "dc":
        ctrl: MagnetControllerBase = Keithley6221DC(inst, current_range_a=current_range_a)
    elif driver == "ac":
        ctrl = Keithley6221AC(inst, current_range_a=current_range_a)
    else:
        rm.close()
        raise ValueError(f"unknown sample source driver: {driver!r}")
    return ctrl, rm