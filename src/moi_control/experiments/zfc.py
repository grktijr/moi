"""Zero-field-cooling experiment mode."""

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


def run_zfc(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
) -> Optional[Path]:
    """ZFC: warm above Tc, cool to T_low with zero field, sweep field, image at each.

    Returns the path to the `original/` directory of saved TIFFs, or None for dry-run.
    """
    field_points = build_field_points(run_cfg.zfc_field_knots, run_cfg.zfc_field_nums)
    print(f"[zfc] field points (Oe): {field_points}")

    csv_path = out_dir / f"{run_cfg.tag}_zfc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    if run_cfg.dry_run:
        print(f"[dry-run] would stabilize at {run_cfg.t_high_K} K, then "
              f"{run_cfg.t_low_K} K, then sweep {len(field_points)} field points; "
              f"CSV: {csv_path}")
        return None

    # Stabilization (skippable for manual pull-down workflow)
    if run_cfg.skip_stabilize:
        state = cryo.read_state()
        print("=" * 60)
        print("--skip-stabilize active; using current cryostat state")
        print(f"  T_plat = {state['T_plat_K']:.4f} K, stab = {state['stab_K']:.5f}")
        print("=" * 60)
    else:
        if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
            raise ValueError("zfc requires --t-high and --t-low")
        cryo.stabilize_at(run_cfg.t_high_K, label="T_high (warm-up)")
        cryo.stabilize_at(run_cfg.t_low_K, label="T_low (cooldown, zero field)")

    # Magnet setup
    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        for idx, B in enumerate(field_points, start=1):
            print(f"[zfc {idx}/{len(field_points)}] B = {B:+.2f} Oe")
            I_target = field_to_current_a(float(B), run_cfg.coil_Oe_per_a)
            if abs(I_target) > mag_cfg.current_limit_a:
                raise RuntimeError(
                    f"target current {I_target:.4f} A exceeds limit "
                    f"{mag_cfg.current_limit_a:.4f} A"
                )
            current_now = ramp_current(
                ctrl, current_now, I_target,
                mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
            )
            time.sleep(mag_cfg.settle_s)

            acquire_one(
                idx=idx, phase="zfc", cycle=1,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

    print("[zfc done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
