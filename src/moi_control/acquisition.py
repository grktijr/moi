"""Shared acquisition primitives used by every experiment mode.

This module is *the* primitives layer. Higher-level experiments in
experiments/ import from here. Anything that talks to the camera at the
single-frame level lives here. Anything that ramps current lives here.
Anything that constructs field sweep points lives here.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import tifffile

from pyvcam import pvc
from pyvcam.camera import Camera

from .config import CameraCfg, MagnetCfg, RunCfg
from .instruments.cryostat import Cryostation
from .instruments.magnet import MagnetControllerBase
from .instruments.camera import PORT_NAME


# ----------------------------------------------------------------------------
# CSV columns (single source of truth for all experiment modes)
# ----------------------------------------------------------------------------

CSV_COLUMNS = [
    "idx", "phase", "cycle",
    "T_set_K", "T_plat_K", "T_sample_K", "stab_K",
    "B_meas_Oe", "B_set_Oe", "Iset_A", "Imeas_A", "Vmeas_V",
    "exp_ms", "bin_x", "bin_y", "navg",
    "roi_x", "roi_y", "roi_w", "roi_h",
    "src", "idn", "filename",
]


# ----------------------------------------------------------------------------
# Camera acquisition primitive (with retry)
# ----------------------------------------------------------------------------

def acquire_avg_sequence(cam_cfg: CameraCfg, exp_ms: int, navg: int) -> np.ndarray:
    """Acquire navg frames and average them.

    Opens/closes a fresh Camera handle per call (PVCAM state hygiene) and
    retries on 'Frame timeout' errors only. Other RuntimeError types
    propagate immediately so unexpected failures don't get masked by
    retry logic.

    Assumes pvc.init_pvcam() was called at startup.
    """
    last_exc: Optional[RuntimeError] = None
    for attempt in range(cam_cfg.retry_max + 1):
        cam = None
        try:
            cam = (Camera(cam_cfg.camera_name) if cam_cfg.camera_name
                   else next(Camera.detect_camera()))
            cam.open()

            cam.readout_port = PORT_NAME[cam_cfg.port_id]
            cam.speed = 0
            cam.gain = 1
            bx, by = cam_cfg.binning
            try:
                cam.binning = (bx, by)
            except Exception:
                cam.binning = f"{bx}x{by}"
            if cam_cfg.roi is not None:
                cam.set_roi(*[int(x) for x in cam_cfg.roi])

            cam.exp_time = int(round(exp_ms))
            timeout_ms = int(20000 + exp_ms * 5)   # per-frame, not per-sequence
            stack = cam.get_sequence(navg, timeout_ms=timeout_ms)

            avg = stack.astype(np.float32).mean(axis=0)
            if np.issubdtype(stack.dtype, np.integer):
                avg = np.clip(np.rint(avg), 0, np.iinfo(stack.dtype).max).astype(stack.dtype)
            if attempt > 0:
                print(f"  [camera] recovered on attempt {attempt + 1}")
            return avg

        except RuntimeError as e:
            if "Frame timeout" not in str(e):
                raise   # not a known transient; let it propagate
            last_exc = e
            print(f"  [camera] frame timeout on attempt "
                  f"{attempt + 1}/{cam_cfg.retry_max + 1}: {e}")

        finally:
            if cam is not None:
                try:
                    cam.close()
                except Exception:
                    pass

        if attempt < cam_cfg.retry_max:
            print(f"  [camera] retrying in {cam_cfg.retry_delay_s:.1f} s...")
            time.sleep(cam_cfg.retry_delay_s)

    raise RuntimeError(
        f"camera frame timeout after {cam_cfg.retry_max + 1} attempts"
    ) from last_exc


# ----------------------------------------------------------------------------
# Magnet ramp primitive
# ----------------------------------------------------------------------------

def field_to_current_a(field_Oe: float, coil_Oe_per_a: float) -> float:
    return field_Oe / coil_Oe_per_a


def ramp_current(
    ctrl: MagnetControllerBase, current_from: float, current_to: float,
    step_a: float, step_delay_s: float,
) -> float:
    """Ramp current from `current_from` to `current_to` in steps. Returns final."""
    if current_from == current_to:
        return current_to
    direction = 1 if current_to > current_from else -1
    cur = current_from
    while abs(cur - current_to) > step_a / 2:
        cur = cur + direction * step_a
        if (direction > 0 and cur > current_to) or (direction < 0 and cur < current_to):
            cur = current_to
        ctrl.set_current(cur)
        time.sleep(step_delay_s)
    ctrl.set_current(current_to)
    return current_to


# ----------------------------------------------------------------------------
# Field-sweep point builder (also reused for T-sweep — generic linspace concat)
# ----------------------------------------------------------------------------

def build_field_points(knots: Tuple[float, ...], nums: Tuple[int, ...]) -> np.ndarray:
    """Concatenate linspace segments defined by knot points and per-segment counts.

    Example: knots=(0, 100, 0), nums=(10, 5) → 10 points 0→100, then 5 points 100→0.
    """
    if len(nums) != len(knots) - 1:
        raise ValueError(
            f"nums length ({len(nums)}) must equal knots length - 1 ({len(knots) - 1})"
        )
    segs = []
    for i, n in enumerate(nums):
        seg = np.linspace(knots[i], knots[i + 1], n, endpoint=(i == len(nums) - 1))
        segs.append(seg)
    return np.concatenate(segs)


# ----------------------------------------------------------------------------
# Filename construction
# ----------------------------------------------------------------------------

def build_filename(
    *,
    idx: int, phase: str, cycle: int,
    T_plat_K: float,
    B_set_Oe: float, B_meas_Oe: Optional[float],
    I_target_A: float, I_meas_A: Optional[float], V_meas_V: Optional[float],
    exposure_ms: int, binning: Tuple[int, int],
    roi: Optional[Tuple[int, int, int, int]],
    timestamp: Optional[str] = None,
    extension: str = ".tif",
) -> str:
    bx, by = binning
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [
        f"{idx:03d}",
        f"__{phase}",
        f"__cycle-{cycle:02d}",
        f"__T-{T_plat_K:.3f}K",
    ]
    if B_meas_Oe is not None:
        parts.append(f"__Bmeas-{B_meas_Oe:+.2f}Oe")
    parts += [
        f"__Bset-{B_set_Oe:+.2f}Oe",
        f"__exp-{exposure_ms:g}ms",
        f"__bin-{bx}x{by}",
    ]
    if roi is not None:
        rx, ry, rw, rh = roi
        parts.append(f"__roi-{rx}x{ry}+{rw}x{rh}")
    parts.append(f"__Iset-{I_target_A:+.4f}A")
    if I_meas_A is not None:
        parts.append(f"__Imeas-{I_meas_A:+.4f}A")
    if V_meas_V is not None:
        parts.append(f"__Vmeas-{V_meas_V:+.3f}V")
    parts += [f"__{ts}", extension]
    return "".join(parts)


# ----------------------------------------------------------------------------
# The unit-of-work function: one image, one CSV row
# ----------------------------------------------------------------------------

def acquire_one(
    *, idx: int, phase: str, cycle: int,
    cam_cfg: CameraCfg,
    ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation,
    run_cfg: RunCfg,
    B_set_Oe: float, I_target_A: float,
    out_dir: Path, csv_writer, csv_f,
) -> Path:
    """Acquire one averaged image and append a CSV row for it.

    Returns the path of the saved TIFF.
    """
    cryo_state = cryo.read_state()
    img = acquire_avg_sequence(cam_cfg, cam_cfg.exposure_ms, cam_cfg.navg)
    i_meas = ctrl.meas_current()
    v_meas = ctrl.meas_voltage()
    B_meas_Oe = (i_meas * run_cfg.coil_Oe_per_a) if i_meas is not None else None  # currently we don't measure field directly

    fname = build_filename(
        idx=idx, phase=phase, cycle=cycle,
        T_plat_K=cryo_state["T_plat_K"],
        B_set_Oe=B_set_Oe, B_meas_Oe=B_meas_Oe,
        I_target_A=I_target_A, I_meas_A=i_meas, V_meas_V=v_meas,
        exposure_ms=cam_cfg.exposure_ms, binning=cam_cfg.binning,
        roi=cam_cfg.roi,
    )
    out_path = out_dir / fname

    tifffile.imwrite(out_path, img, photometric="minisblack")

    bx, by = cam_cfg.binning
    if cam_cfg.roi is not None:
        rx, ry, rw, rh = cam_cfg.roi
    else:
        rx, ry, rw, rh = "", "", "", ""

    csv_writer.writerow([
        idx, phase, cycle,
        f"{cryo_state['T_set_K']:+.4f}",
        f"{cryo_state['T_plat_K']:+.4f}",
        f"{cryo_state['T_sample_K']:+.4f}",
        f"{cryo_state['stab_K']:+.5f}",
        ("N/A" if B_meas_Oe is None else f"{B_meas_Oe:+.4f}"),
        f"{B_set_Oe:+.4f}",
        f"{I_target_A:+.6f}",
        ("N/A" if i_meas is None else f"{i_meas:+.6f}"),
        ("N/A" if v_meas is None else f"{v_meas:+.6f}"),
        cam_cfg.exposure_ms, bx, by, cam_cfg.navg,
        rx, ry, rw, rh,
        phase, ctrl.idn(), out_path.name,
    ])
    csv_f.flush()
    print(f"  saved: {out_path.name}")
    return out_path
