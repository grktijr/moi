"""Temperature-sweep experiment mode."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Optional

from ..acquisition import (
    CSV_COLUMNS, acquire_one, build_field_points,
    field_to_current_a, ramp_current,
)
from ..config import CameraCfg, MagnetCfg, RunCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import MagnetControllerBase


def run_tsweep(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
) -> Optional[Path]:
    """T-sweep: optional preamble at zero field, apply field, then sweep T points."""
    t_points = build_field_points(run_cfg.tsweep_t_knots, run_cfg.tsweep_t_nums)
    print(f"[tsweep] T points (K): {t_points}")
    print(f"[tsweep] field during sweep: {run_cfg.tsweep_field_Oe:+.2f} Oe")

    csv_path = out_dir / f"{run_cfg.tag}_tsweep_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    if run_cfg.dry_run:
        prep = run_cfg.tsweep_prep_K
        prep_str = f"prep at {prep} K then " if prep is not None else ""
        print(f"[dry-run] would {prep_str}apply {run_cfg.tsweep_field_Oe} Oe, "
              f"then sweep {len(t_points)} T points; CSV: {csv_path}")
        return None

    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    if run_cfg.tsweep_prep_K is not None:
        cryo.stabilize_at(run_cfg.tsweep_prep_K, label="prep (zero field)")

    I_target = field_to_current_a(run_cfg.tsweep_field_Oe, run_cfg.coil_Oe_per_a)
    if abs(I_target) > mag_cfg.current_limit_a:
        raise RuntimeError(f"target current {I_target:.4f} A exceeds limit")
    current_now = ramp_current(ctrl, current_now, I_target,
                               mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    time.sleep(mag_cfg.settle_s)

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        for idx, T in enumerate(t_points, start=1):
            print(f"[tsweep {idx}/{len(t_points)}] T = {T:.3f} K")
            cryo.stabilize_at(float(T), label=f"point {idx}/{len(t_points)}")

            acquire_one(
                idx=idx, phase="tsweep", cycle=1,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=run_cfg.tsweep_field_Oe, I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

    print("[tsweep done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
