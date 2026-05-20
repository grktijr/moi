"""
MOI acquisition: ZFC and FC modes with integrated thermal, magnetic, and camera control.

USAGE
-----
  # Zero-field cooling: stabilize at T_high, cool to T_low, ramp field at T_low taking images
  python moi_acquire.py zfc --t-high 12 --t-low 9 --out D:/data/run1 --tag zfc1

  # Field cooling: at each B, take one image at T_high then cool to T_low and take another
  python moi_acquire.py fc --t-high 12 --t-low 9 --fc-fields 5 10 15 --out D:/data/fc1 --tag fc1

  # Dry run to preview the sequence without moving anything
  python moi_acquire.py zfc --t-high 12 --t-low 9 --dry-run

Other useful flags:
  --field-knots 0 15            knot points for the in-mode field sweep (used by zfc)
  --field-nums 16               number of points between knots
  --coil-oe-per-a 333.3         coil calibration
  --exposure-ms 100             camera exposure
  --navg 8                      frames to average per image
  --no-divide                   skip post-process divide step

Hardware assumptions (edit class defaults near top of file if different):
  - Cryostation Gen 1/2 at 192.168.0.2:7773 on private cable link
  - Camera: PVCAM Kinetix port 2 (Dynamic Range), gain 1, speed 0
  - Magnet: Keysight E36234A channel 2 on USB VISA
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import socket
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple, List

import numpy as np
import tifffile
import pyvisa
from PIL import Image

from pyvcam import pvc
from pyvcam.camera import Camera


# ====================================================================
# Configuration dataclasses (defaults; overridden by CLI where exposed)
# ====================================================================

@dataclass
class CryoCfg:
    host: str = "192.168.0.2"
    port: int = 7773
    socket_timeout_s: float = 5.0

    # Safety: refuse setpoints outside this range without an explicit flag.
    min_K: float = 2.0
    max_K: float = 50.0

    # Stabilization criteria
    tol_K: float = 0.05            # |T - target| must be below this
    stab_K: float = 0.1           # stability metric must be below this
    dwell_s: float = 30.0          # both conditions must hold for this long
    poll_s: float = 1.0            # polling interval during stabilize
    stabilize_timeout_s: float = 600.0   # 10 minutes max per setpoint


@dataclass
class CameraCfg:
    camera_name: Optional[str] = None  # None -> first detected
    port_id: int = 2                   # Dynamic Range
    exposure_ms: int = 100
    navg: int = 8
    binning: Tuple[int, int] = (1, 1)
    roi: Optional[Tuple[int, int, int, int]] = None


@dataclass
class MagnetCfg:
    controller: str = ""
    visa_resource: str = ""

    # PSU-speciaties
    keysight_channel: int = 2
    keithley_current_range_a: Optional[float] = 1.0

    voltage_limit_v: float = 20.0
    current_limit_a: float = 10.0

    ramp_step_a: float = 0.05
    ramp_step_delay_s: float = 0.2
    settle_s: float = 0.7


# --------------------------------------------------------------------
# PSU registry: maps the --psu CLI key to driver + VISA + per-PSU knobs.
# Edit when you change a physical PSU or its cable/address.
# --------------------------------------------------------------------
PSU_REGISTRY: dict = {
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
    "keithley": {
        "driver": "keithley_2400",
        "visa":   "GPIB0::23::INSTR",
        "extras": {"keithley_current_range_a": 1.0},
    },
}


@dataclass
class RunCfg:
    out_dir: Path = Path(r"D:\data")
    tag: str = "run"
    coil_Oe_per_a: float = 333.3

    # ZFC: field sweep at T_low
    zfc_field_knots: Tuple[float, ...] = (0.0, 15.0)
    zfc_field_nums:  Tuple[int, ...]   = (16,)

    # FC: explicit list of field values (Oe) to cycle through
    fc_fields_Oe: Tuple[float, ...] = (5.0, 10.0, 15.0)

    # Temperature setpoints (Kelvin)
    t_high_K: float = 9.0
    t_low_K:  float =  3.5

    measure_iv: bool = True
    do_divide:  bool = True
    dry_run:    bool = False


# ====================================================================
# Cryostation Gen 1/2 client (length-prefixed ASCII over TCP)
# ====================================================================

class Cryostation:
    """Minimal Gen 1/2 Cryostation client over TCP."""

    def __init__(self, cfg: CryoCfg):
        self.cfg = cfg
        self.sock: Optional[socket.socket] = None

    # ---- connection lifecycle ----
    def open(self) -> None:
        self.sock = socket.create_connection(
            (self.cfg.host, self.cfg.port), timeout=self.cfg.socket_timeout_s,
        )
        self.sock.settimeout(self.cfg.socket_timeout_s)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    # ---- protocol primitive ----
    def _send(self, cmd: str) -> str:
        assert self.sock is not None, "Cryostation not connected"
        msg = f"{len(cmd):02d}{cmd}".encode("ascii")
        self.sock.sendall(msg)

        header = b""
        while len(header) < 2:
            chunk = self.sock.recv(2 - len(header))
            if not chunk:
                raise ConnectionError("Cryostation closed while reading length")
            header += chunk
        n = int(header.decode("ascii"))

        body = b""
        while len(body) < n:
            chunk = self.sock.recv(n - len(body))
            if not chunk:
                raise ConnectionError("Cryostation closed mid-message")
            body += chunk
        return body.decode("ascii")

    def _get_float(self, cmd: str) -> float:
        raw = self._send(cmd)
        try:
            return float(raw)
        except ValueError:
            raise RuntimeError(f"{cmd} returned non-numeric reply: {raw!r}")

    # ---- public reads ----
    def platform_K(self) -> float: return self._get_float("GPT")
    def sample_K(self)   -> float: return self._get_float("GST")
    def stability_K(self) -> float: return self._get_float("GSS")
    def setpoint_K(self)  -> float: return self._get_float("GTSP")

    def read_state(self) -> dict:
        """Snapshot of relevant scalars (one network round-trip per field)."""
        return {
            "T_set_K":    self.setpoint_K(),
            "T_plat_K":   self.platform_K(),
            "T_sample_K": self.sample_K(),
            "stab_K":     self.stability_K(),
        }

    # ---- writes ----
    def set_setpoint_K(self, target_K: float) -> None:
        if not (self.cfg.min_K <= target_K <= self.cfg.max_K):
            raise ValueError(
                f"target {target_K} K outside safety window "
                f"[{self.cfg.min_K}, {self.cfg.max_K}]"
            )
        reply = self._send(f"STSP{target_K:.3f}")
        # Soft readback check: warn but do not raise.
        try:
            sp = self.setpoint_K()
            if abs(sp - target_K) > 0.02:
                print(f"  [warn] setpoint readback {sp:.3f} K != target "
                      f"{target_K:.3f} K (STSP reply: {reply!r}); continuing")
        except Exception as e:
            print(f"  [warn] could not read back setpoint: {e}; continuing")

    # ---- high-level wait ----
    def stabilize_at(self, target_K: float, *, label: str = "") -> dict:
        """Set the setpoint and block until temperature is stable, or raise on timeout."""
        c = self.cfg
        self.set_setpoint_K(target_K)
        t_start = time.monotonic()
        t_in_window: Optional[float] = None

        prefix = f"[stabilize {label}]" if label else "[stabilize]"
        print(f"{prefix} target {target_K:.3f} K, "
              f"tol={c.tol_K} K, stab<{c.stab_K} K, dwell={c.dwell_s:.0f} s, "
              f"timeout={c.stabilize_timeout_s:.0f} s")

        while True:
            t_el = time.monotonic() - t_start
            pt = self.platform_K()
            ss = self.stability_K()
            dT = abs(pt - target_K)
            in_window = (dT < c.tol_K) and (ss < c.stab_K)

            if in_window:
                if t_in_window is None:
                    t_in_window = time.monotonic()
                held = time.monotonic() - t_in_window
                print(f"  t={t_el:6.1f}s  T={pt:7.4f}K  stab={ss:7.5f}K  "
                      f"in-window {held:5.1f}/{c.dwell_s:.0f}s")
                if held >= c.dwell_s:
                    state = self.read_state()
                    print(f"{prefix} stable at {pt:.4f} K after {t_el:.1f} s")
                    return state
            else:
                t_in_window = None
                print(f"  t={t_el:6.1f}s  T={pt:7.4f}K  stab={ss:7.5f}K  "
                      f"|dT|={dT:.4f}K")

            if t_el > c.stabilize_timeout_s:
                state = self.read_state()
                print(f"{prefix} [warn] timeout after {t_el:.0f} s; "
                      f"target {target_K:.3f} K not stably reached "
                      f"(last T={pt:.4f} K, stab={ss:.5f} K). "
                      f"Proceeding with experiment anyway.")
                return state
            time.sleep(c.poll_s)


# ====================================================================
# Magnet controllers (unchanged from your original)
# ====================================================================

class MagnetControllerBase:
    def __init__(self, inst):
        self.inst = inst
        self.inst.timeout = 10_000
        try:
            self.inst.write_termination = "\n"
            self.inst.read_termination = "\n"
        except Exception:
            pass

    def idn(self) -> str:
        try:
            return str(self.inst.query("*IDN?")).strip()
        except Exception:
            return "UNKNOWN"

    def output_on(self) -> None: raise NotImplementedError
    def output_off(self) -> None: raise NotImplementedError
    def set_voltage_limit(self, v: float) -> None: raise NotImplementedError
    def set_current(self, a: float) -> None: raise NotImplementedError
    def meas_current(self) -> Optional[float]: return None
    def meas_voltage(self) -> Optional[float]: return None


class KeysightE36234A(MagnetControllerBase):
    def __init__(self, inst, channel: int = 2):
        super().__init__(inst)
        if channel not in (1, 2):
            raise ValueError("Keysight channel must be 1 or 2")
        self.channel = channel

    def _chanlist(self) -> str: return f"(@{self.channel})"
    def _ch_token(self) -> str: return "CH1" if self.channel == 1 else "CH2"

    def set_voltage_limit(self, v): self.inst.write(f"VOLT {v}, {self._chanlist()}")
    def set_current(self, a):       self.inst.write(f"CURR {a}, {self._chanlist()}")
    def output_on(self):            self.inst.write(f"OUTP ON, {self._chanlist()}")
    def output_off(self):           self.inst.write(f"OUTP OFF, {self._chanlist()}")

    def meas_current(self):
        try: return float(self.inst.query(f"MEAS:CURR? {self._ch_token()}"))
        except Exception: return None
    def meas_voltage(self):
        try: return float(self.inst.query(f"MEAS:VOLT? {self._ch_token()}"))
        except Exception: return None


class HP6542A(MagnetControllerBase):
    def set_voltage_limit(self, v): self.inst.write(f"VOLT {v}")
    def set_current(self, a):       self.inst.write(f"CURR {a}")
    def output_on(self):            self.inst.write("OUTP ON")
    def output_off(self):           self.inst.write("OUTP OFF")
    def meas_current(self):
        try: return float(self.inst.query("MEAS:CURR?"))
        except Exception: return None
    def meas_voltage(self):
        try: return float(self.inst.query("MEAS:VOLT?"))
        except Exception: return None

class Keithley2400(MagnetControllerBase):
    def __init__(self, inst, current_range_a: Optional[float] = None):
        super().__init__(inst)

        self.inst.write("*RST")
        self.inst.write(":SOUR:FUNC CURR")
        self.inst.write(":SENS:FUNC 'VOLT','CURR'")
        self.inst.write(":FORM:ELEM VOLT,CURR")

        if current_range_a is not None:
            # Fixed source range avoids click-y auto-range changes mid-ramp.
            self.inst.write(f":SOUR:CURR:RANG {current_range_a}")
        else:
            self.inst.write(":SOUR:CURR:RANG:AUTO ON")

        self.inst.write(":SENS:VOLT:RANG:AUTO ON")
        self.inst.write(":SENS:CURR:RANG:AUTO ON")

    def set_voltage_limit(self, v): self.inst.write(f":SENS:VOLT:PROT {v}")
    def set_current(self, a):       self.inst.write(f":SOUR:CURR:LEV {a}")
    def output_on(self):            self.inst.write(":OUTP ON")
    def output_off(self):           self.inst.write(":OUTP OFF")

    def _read_vi(self):
        """One :READ? trigger; returns (V, I) or None on failure."""
        try:
            raw = self.inst.query(":READ?").strip()
            parts = raw.split(",")
            if len(parts) < 2:
                return None
            return float(parts[0]), float(parts[1])
        except Exception:
            return None

    def meas_current(self):
        vi = self._read_vi()
        return vi[1] if vi is not None else None

    def meas_voltage(self):
        vi = self._read_vi()
        return vi[0] if vi is not None else None

def open_magnet(cfg: MagnetCfg) -> Tuple[MagnetControllerBase, pyvisa.ResourceManager]:
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(cfg.visa_resource)
    which = cfg.controller.lower().strip()
    if which == "keysight_e36234a":
        return KeysightE36234A(inst, channel=cfg.keysight_channel), rm
    elif which == "hp_6542a":
        return HP6542A(inst), rm
    elif which == "keithley_2400":
        return Keithley2400(inst, current_range_a=cfg.keithley_current_range_a), rm
    rm.close()
    raise ValueError("controller must be 'keysight_e36234a' or 'hp_6542a' or 'keithley_2400'")


# ====================================================================
# Camera (unchanged in spirit)
# ====================================================================

PORT_NAME = {0: "Sensitivity", 1: "Speed", 2: "Dynamic Range", 3: "Sub-Electron"}


def set_kinetix_port(cam: Camera, port_id: int) -> str:
    name = PORT_NAME[port_id]
    cam.readout_port = name
    cam.speed = 0
    cam.gain = 1
    return name


def set_binning(cam: Camera, binning: Tuple[int, int]) -> None:
    bx, by = binning
    try: cam.binning = (bx, by); return
    except Exception: pass
    cam.binning = f"{bx}x{by}"


def acquire_avg_sequence(cam_cfg: CameraCfg, exp_ms: int, navg: int) -> np.ndarray:
    """Acquire navg frames, average them. Opens/closes the camera around the
    sequence to avoid PVCAM state accumulation.

    Retries up to MAX_RETRIES times on PyVCAM 'Frame timeout' errors only.
    Other exceptions propagate immediately. Assumes pvc.init_pvcam() was called.
    """
    MAX_RETRIES = 2          # 2 retries means up to 3 total attempts
    RETRY_DELAY_S = 2.0

    last_exc: Optional[RuntimeError] = None
    for attempt in range(MAX_RETRIES + 1):
        cam = None
        try:
            cam = (Camera(cam_cfg.camera_name) if cam_cfg.camera_name
                   else next(Camera.detect_camera()))
            cam.open()

            cam.readout_port = PORT_NAME[cam_cfg.port_id]
            cam.speed = 0
            cam.gain = 1
            bx, by = cam_cfg.binning
            try: cam.binning = (bx, by)
            except Exception: cam.binning = f"{bx}x{by}"
            if cam_cfg.roi is not None:
                cam.set_roi(*[int(x) for x in cam_cfg.roi])

            cam.exp_time = int(round(exp_ms))
            timeout_ms = int(5000 + exp_ms * 5)
            stack = cam.get_sequence(navg, timeout_ms=timeout_ms)

            # Success: do the averaging and return
            avg = stack.astype(np.float32).mean(axis=0)
            if np.issubdtype(stack.dtype, np.integer):
                avg = np.clip(np.rint(avg), 0, np.iinfo(stack.dtype).max).astype(stack.dtype)

            if attempt > 0:
                print(f"  [camera] recovered on attempt {attempt + 1}")
            return avg

        except RuntimeError as e:
            # Only retry on frame timeouts; let other RuntimeErrors propagate
            if "Frame timeout" not in str(e):
                raise
            last_exc = e
            print(f"  [camera] frame timeout on attempt {attempt + 1}/{MAX_RETRIES + 1}: {e}")
            # Fall through to teardown + retry

        finally:
            if cam is not None:
                try: cam.close()
                except Exception: pass

        if attempt < MAX_RETRIES:
            print(f"  [camera] retrying in {RETRY_DELAY_S:.1f} s...")
            time.sleep(RETRY_DELAY_S)

    # All retries exhausted
    raise RuntimeError(
        f"camera frame timeout after {MAX_RETRIES + 1} attempts"
    ) from last_exc


# ====================================================================
# Ramp / field helpers
# ====================================================================

def field_to_current_a(field_Oe: float, coil_Oe_per_a: float) -> float:
    if coil_Oe_per_a == 0:
        raise ValueError("coil_Oe_per_a must be non-zero")
    return field_Oe / coil_Oe_per_a


def ramp_current(ctrl: MagnetControllerBase, current_now: float, target: float,
                 step_a: float, delay_s: float) -> float:
    if step_a <= 0:
        ctrl.set_current(target); time.sleep(delay_s); return target
    delta = target - current_now
    if abs(delta) <= step_a:
        ctrl.set_current(target); time.sleep(delay_s); return target

    sign = 1.0 if delta > 0 else -1.0
    n = int(abs(delta) // step_a)
    for k in range(1, n + 1):
        ctrl.set_current(current_now + sign * step_a * k)
        time.sleep(delay_s)
    ctrl.set_current(target); time.sleep(delay_s)
    return target


def build_field_points(knots: Sequence[float], nums: Sequence[int]) -> Tuple[float, ...]:
    if len(nums) != len(knots) - 1:
        raise ValueError(f"need len(nums) == len(knots)-1, got {len(nums)} vs {len(knots)-1}")
    parts = []
    for i in range(len(nums)):
        parts.append(np.linspace(knots[i], knots[i+1], nums[i],
                                 endpoint=(i == len(nums)-1)))
    return tuple(float(x) for x in np.concatenate(parts))


# ====================================================================
# Acquisition primitive: one image, one CSV row
# ====================================================================

def acquire_one(
    *, idx: int, phase: str, cycle: int,
    cam_cfg: CameraCfg,
    ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation,
    run_cfg: RunCfg,
    B_set_Oe: float, I_target_A: float,
    out_dir: Path, csv_writer: csv.writer, csv_f,
) -> Path:
    """Take one averaged image, write TIFF + CSV row, return file path."""

    # Image
    img = acquire_avg_sequence(cam_cfg, cam_cfg.exposure_ms, cam_cfg.navg)

    # Instrument readbacks
    i_meas = ctrl.meas_current() if run_cfg.measure_iv else None
    v_meas = ctrl.meas_voltage() if run_cfg.measure_iv else None
    B_meas_Oe = (i_meas * run_cfg.coil_Oe_per_a) if i_meas is not None else None
    cryo_state = cryo.read_state()

    # Filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bx, by = cam_cfg.binning
    src_tag = mag_cfg.controller.lower()
    if src_tag == "keysight_e36234a":
        src_tag += f"_ch{mag_cfg.keysight_channel}"

    fname = "".join([
        f"{idx:03d}",
        f"__{phase}",
        f"__cycle-{cycle:02d}",
        f"__T-{cryo_state['T_plat_K']:.3f}K",
        (f"__Bmeas-{B_meas_Oe:+.2f}Oe" if B_meas_Oe is not None else ""),
        f"__Bset-{B_set_Oe:+.2f}Oe",
        f"__exp-{cam_cfg.exposure_ms:g}ms",
        f"__bin-{bx}x{by}",
        f"__Iset-{I_target_A:+.4f}A",
        (f"__Imeas-{i_meas:+.4f}A" if i_meas is not None else ""),
        (f"__Vmeas-{v_meas:+.3f}V" if v_meas is not None else ""),
        f"__{ts}",
        ".tif",
    ])
    out_path = out_dir / fname
    tifffile.imwrite(out_path, img, photometric="minisblack")

    csv_writer.writerow([
        idx, phase, cycle,
        f"{cryo_state['T_set_K']:+.4f}",
        f"{cryo_state['T_plat_K']:+.4f}",
        f"{cryo_state['T_sample_K']:+.4f}",
        f"{cryo_state['stab_K']:+.5f}",
        f"{B_meas_Oe:+.6f}" if B_meas_Oe is not None else "N/A",
        f"{B_set_Oe:+.6f}",
        f"{I_target_A:+.6f}",
        f"{i_meas:+.6f}" if i_meas is not None else "N/A",
        f"{v_meas:+.6f}" if v_meas is not None else "N/A",
        cam_cfg.exposure_ms, bx, by, cam_cfg.navg,
        src_tag, ctrl.idn(), out_path.name,
    ])
    csv_f.flush()
    print(f"  saved: {out_path.name}")
    return out_path


CSV_COLUMNS = [
    "idx", "phase", "cycle",
    "T_set_K", "T_plat_K", "T_sample_K", "stab_K",
    "B_meas_Oe", "B_set_Oe", "Iset_A", "Imeas_A", "Vmeas_V",
    "exp_ms", "bin_x", "bin_y", "navg",
    "src", "idn", "filename",
]


# ====================================================================
# Modes: ZFC and FC
# ====================================================================

def run_zfc(cam, cam_cfg, ctrl, mag_cfg, cryo, run_cfg: RunCfg, out_dir: Path):
    """Stabilize at T_high, cool to T_low, then sweep field at T_low taking images."""

    field_points = build_field_points(run_cfg.zfc_field_knots, run_cfg.zfc_field_nums)
    print(f"[zfc] field points (Oe): {field_points}")

    csv_path = out_dir / f"{run_cfg.tag}_zfc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    if run_cfg.dry_run:
        print(f"[dry-run] would stabilize at {run_cfg.t_high_K} K, "
              f"cool to {run_cfg.t_low_K} K, then sweep "
              f"{len(field_points)} field points and save to {csv_path}")
        return

    # 1. Stabilize at T_high (above Tc)
    cryo.stabilize_at(run_cfg.t_high_K, label="T_high (warm-up)")

    # 2. Cool to T_low (still at zero field)
    cryo.stabilize_at(run_cfg.t_low_K, label="T_low (cooldown, zero field)")

    # 3. Field sweep at T_low
    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f); writer.writerow(CSV_COLUMNS)
        for idx, B in enumerate(field_points, start=1):
            I_target = field_to_current_a(B, run_cfg.coil_Oe_per_a)
            if abs(I_target) > mag_cfg.current_limit_a:
                raise RuntimeError(f"target current {I_target:.4f}A exceeds limit")
            print(f"[zfc {idx}/{len(field_points)}] B={B:+.2f} Oe -> I={I_target:+.4f} A")
            current_now = ramp_current(ctrl, current_now, I_target,
                                       mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
            time.sleep(mag_cfg.settle_s)
            acquire_one(
                idx=idx, phase="zfc", cycle=1,
                cam_cfg=cam_cfg,
                ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

    print(f"[zfc done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0, mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original


def run_fc(cam, cam_cfg, ctrl, mag_cfg, cryo, run_cfg: RunCfg, out_dir: Path):
    """For each B in fc_fields_Oe: warm-up, apply B, image, cool, image, zero field."""

    csv_path = out_dir / f"{run_cfg.tag}_fc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    print(f"[fc] field cycles (Oe): {run_cfg.fc_fields_Oe}")
    if run_cfg.dry_run:
        print(f"[dry-run] for each B in {run_cfg.fc_fields_Oe}: warm to "
              f"{run_cfg.t_high_K} K, apply B, image; cool to {run_cfg.t_low_K} K, "
              f"image; zero field. CSV: {csv_path}")
        return

    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f); writer.writerow(CSV_COLUMNS)
        idx = 0
        field_counts = {}
        for global_cyc, B in enumerate(run_cfg.fc_fields_Oe, start=1):
            cyc = field_counts.get(B, 0) + 1
            field_counts[B] = cyc
            print(f"\n[fc cycle {global_cyc}/{len(run_cfg.fc_fields_Oe)}] "
                  f"B = {B:+.2f} Oe (cycle {cyc} at this field)")
            I_target = field_to_current_a(float(B), run_cfg.coil_Oe_per_a)
            if abs(I_target) > mag_cfg.current_limit_a:
                raise RuntimeError(f"target current {I_target:.4f}A exceeds limit")

            # a) Warm to T_high (zero or current field; we zero first for cleanliness)
            current_now = ramp_current(ctrl, current_now, 0.0,
                                       mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
            cryo.stabilize_at(run_cfg.t_high_K, label=f"cycle {cyc} T_high")

            # b) Apply field at T_high, image
            current_now = ramp_current(ctrl, current_now, I_target,
                                       mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
            time.sleep(mag_cfg.settle_s)
            idx += 1
            acquire_one(
                idx=idx, phase=f"fc_high_B{B:+.2f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg, B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

            # c) Cool to T_low while field persists, then image
            cryo.stabilize_at(run_cfg.t_low_K, label=f"cycle {cyc} T_low (field on)")
            idx += 1
            acquire_one(
                idx=idx, phase=f"fc_low_B{B:+.2f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg, B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

    print(f"\n[fc done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0, mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original


# ====================================================================
# Post-process
# ====================================================================

def process_divide(origin_path: Path):
    divided_path = origin_path.parent / "divided"
    divided_path.mkdir(parents=True, exist_ok=True)
    origin_files = sorted(
        glob.glob(os.path.join(str(origin_path), "*.tif")),
        key=lambda x: int(os.path.basename(x)[:3]),
    )
    if not origin_files:
        print("[divide] no TIFFs found"); return
    base_array = np.array(Image.open(origin_files[0]))
    for f in origin_files:
        name = os.path.basename(f)
        img = np.array(Image.open(f))
        divided = img / base_array
        Image.fromarray(divided).save(os.path.join(divided_path, f"divid {name}"))
        print(f"[divide] {name}")
    print("[divide] done")



def process_fc_divide(origin_path: Path, csv_path: Path):
    """For each (B, cycle) pair, divide the low-T image by the high-T image.

    Writes one TIFF per pair into <origin>/divided/. Skips groups that don't
    have exactly one high and one low row.
    """
    import pandas as pd

    divided_path = origin_path.parent / "divided"
    divided_path.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path)
    df["B_key"] = df["B_set_Oe"].round(4)

    n_done = 0
    n_skip = 0
    for (B_key, cyc), group in df.groupby(["B_key", "cycle"]):
        high_rows = group[group["phase"].str.startswith("fc_high")]
        low_rows  = group[group["phase"].str.startswith("fc_low")]
        if len(high_rows) != 1 or len(low_rows) != 1:
            print(f"[fc-divide] B={B_key:+.2f} cyc={cyc}: "
                  f"got {len(high_rows)} high + {len(low_rows)} low; skipping")
            n_skip += 1
            continue

        try:
            high_img = np.array(Image.open(
                origin_path / high_rows.iloc[0]["filename"])).astype(np.float32)
            low_img  = np.array(Image.open(
                origin_path / low_rows.iloc[0]["filename"])).astype(np.float32)
        except FileNotFoundError as e:
            print(f"[fc-divide] missing file for B={B_key} cyc={cyc}: {e}")
            n_skip += 1
            continue

        eps = 1.0
        ratio = low_img / np.maximum(high_img, eps)

        out_name = f"fc_div__B{B_key:+.2f}Oe__cyc{int(cyc):02d}.tif"
        Image.fromarray(ratio).save(divided_path / out_name)
        print(f"[fc-divide] saved {out_name}")
        n_done += 1

    print(f"[fc-divide done] {n_done} written, {n_skip} skipped")

# ====================================================================
# CLI
# ====================================================================

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MOI acquisition: ZFC / FC")
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, required=True, help="output directory")
    common.add_argument("--tag", type=str, default="run", help="filename tag")
    common.add_argument("--psu", type=str, required=True,
                        choices=sorted(PSU_REGISTRY.keys()),
                        help="which magnet power supply to use; resolves driver, "
                             "VISA address, and per-PSU defaults from PSU_REGISTRY")
    common.add_argument("--t-high", type=float, required=True,
                        help="warm setpoint (K), above Tc")
    common.add_argument("--t-low", type=float, required=True,
                        help="cold setpoint (K), measurement temperature")
    common.add_argument("--coil-oe-per-a", type=float, default=333.3,
                        help="coil calibration Oe/A")
    common.add_argument("--exposure-ms", type=int, default=100,
                        help="exposure time in milliseconds (integer; PyVCAM constraint)")
    common.add_argument("--navg", type=int, default=8)
    common.add_argument("--no-divide", action="store_true",
                        help="skip post-process divide step")
    common.add_argument("--dry-run", action="store_true",
                        help="print planned actions without moving anything")

    z = sub.add_parser("zfc", parents=[common],
                       help="Zero-field cooling: cool then sweep field at T_low")
    z.add_argument("--field-knots", type=float, nargs="+", default=[0.0, 15.0],
                   help="knot field values in Oe (e.g. 0 5 15)")
    z.add_argument("--field-nums", type=int, nargs="+", default=[16],
                   help="number of points per segment (len = knots-1)")

    f = sub.add_parser("fc", parents=[common],
                       help="Field cooling: at each B, image at T_high then cool to T_low")
    f.add_argument("--fc-fields", type=float, nargs="+", required=True,
                   help="field values (Oe) to cycle through, e.g. 5 10 15")
    f.add_argument("--fc-cycles", type=int, default=1,
                   help="number of FC cycles per field value (default 1)")

    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    entry = PSU_REGISTRY[args.psu]
    mag_cfg = MagnetCfg(
        controller=entry["driver"],
        visa_resource=entry["visa"],
        **entry["extras"],
    )

    cam_cfg = CameraCfg(exposure_ms=args.exposure_ms, navg=args.navg)
    cryo_cfg = CryoCfg()

    # Per-mode RunCfg
    run_cfg = RunCfg(
        out_dir=args.out, tag=args.tag,
        coil_Oe_per_a=args.coil_oe_per_a,
        t_high_K=args.t_high, t_low_K=args.t_low,
        do_divide=not args.no_divide,
        dry_run=args.dry_run,
    )
    if args.mode == "zfc":
        run_cfg = replace(
            run_cfg,
            zfc_field_knots=tuple(args.field_knots),
            zfc_field_nums=tuple(args.field_nums),
        )
    elif args.mode == "fc":
        expanded = tuple(B for B in args.fc_fields for _ in range(args.fc_cycles))
        run_cfg = replace(run_cfg, fc_fields_Oe=expanded)

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    # Dry run path: don't touch hardware
    if args.dry_run:
        cryo = type("FakeCryo", (), {})()  # no-op stub; modes check dry_run themselves
        print(f"[dry-run] mode={args.mode}, out={out_dir}, "
              f"T_high={run_cfg.t_high_K} K, T_low={run_cfg.t_low_K} K")
        if args.mode == "zfc":
            print(f"[dry-run] field knots={run_cfg.zfc_field_knots}, "
                  f"nums={run_cfg.zfc_field_nums}")
        else:
            print(f"[dry-run] FC fields={run_cfg.fc_fields_Oe}")
        return 0

    # --- live run ---
    cam: Optional[Camera] = None
    ctrl: Optional[MagnetControllerBase] = None
    rm: Optional[pyvisa.ResourceManager] = None
    cryo = Cryostation(cryo_cfg)

    try:
        # Cryostat (cheapest to verify; do first so a wire problem fails fast)
        cryo.open()
        print(f"[cryo] connected {cryo_cfg.host}:{cryo_cfg.port}, state: {cryo.read_state()}")

        # Camera
        pvc.init_pvcam()
        print(f"[camera] PVCAM initialized; camera will be opened per acquisition")
        cam = None

        # Magnet
        ctrl, rm = open_magnet(mag_cfg)
        print(f"[magnet] {mag_cfg.controller} -> {ctrl.idn()}")

        # Dispatch
        if args.mode == "zfc":
            origin = run_zfc(cam, cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fc":
            origin = run_fc(cam, cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        else:
            raise RuntimeError(f"unknown mode {args.mode}")

        if origin is not None and run_cfg.do_divide:
            if args.mode == "zfc":
                process_divide(origin)
            elif args.mode == "fc":
                csv_path = origin.parent / f"{run_cfg.tag}_fc_metadata.csv"
                process_fc_divide(origin, csv_path)

    except KeyboardInterrupt:
        print("\n[abort] KeyboardInterrupt; shutting down safely")
    finally:
        # Magnet: zero current and turn off output before disconnecting
        try:
            if ctrl is not None:
                try: ctrl.set_current(0.0)
                except Exception: pass
                try: ctrl.output_off()
                except Exception: pass
                try: ctrl.inst.close()
                except Exception: pass
        finally:
            try:
                if rm is not None: rm.close()
            except Exception: pass

        try:
            if cam is not None: cam.close()
        except Exception: pass
        try: pvc.uninit_pvcam()
        except Exception: pass

        try: cryo.close()
        except Exception: pass

        print("[cleanup] complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
