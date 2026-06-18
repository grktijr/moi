"""FC-then-ZFC experiment: apply field at T_high, take reference image,
cool to T_low with field on, then sweep field at T_low."""

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


def run_fczfc(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
) -> Optional[Path]:
    """For each entry in fczfc_field_Oe (the expanded cycle list):
    warm to T_high, ramp to anchor, ref image, cool, sweep at T_low.
    """
    if not run_cfg.fczfc_field_Oe:
        raise ValueError("fczfc requires --fc-field")

    sweep_points = build_field_points(
        run_cfg.fczfc_sweep_knots, run_cfg.fczfc_sweep_nums,
    )
    n_per_cycle = 1 + len(sweep_points)   # 1 ref + N sweep

    print(f"[fczfc] anchor schedule: {run_cfg.fczfc_field_Oe}")
    print(f"[fczfc] sweep points (Oe): {sweep_points}")

    csv_path = out_dir / f"{run_cfg.tag}_fczfc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    if run_cfg.dry_run:
        n = len(run_cfg.fczfc_field_Oe)
        print(f"[dry-run] would do {n} fczfc cycles, "
              f"each with 1 ref + {len(sweep_points)} sweep; CSV: {csv_path}")
        return None

    # Magnet setup
    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    field_counts = {}   # per-anchor cycle index, mirrors FC pattern

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        for global_cyc, fc_anchor_Oe in enumerate(run_cfg.fczfc_field_Oe, start=1):
            cyc = field_counts.get(fc_anchor_Oe, 0) + 1
            field_counts[fc_anchor_Oe] = cyc
            print(f"\n[fczfc {global_cyc}/{len(run_cfg.fczfc_field_Oe)}] "
                  f"anchor {fc_anchor_Oe:+.2f} Oe (cycle {cyc} at this anchor)")

            # 1. Ramp to 0, warm to T_high
            current_now = ramp_current(
                ctrl, current_now, 0.0,
                mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
            )
            if not run_cfg.skip_stabilize:
                if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
                    raise ValueError("fczfc requires --t-high and --t-low")
                cryo.stabilize_at(run_cfg.t_high_K,
                                  label=f"cycle {cyc} T_high (zero field)")

            # 2. Ramp to FC anchor
            I_fc = field_to_current_a(fc_anchor_Oe, run_cfg.coil_Oe_per_a)
            if abs(I_fc) > mag_cfg.current_limit_a:
                raise RuntimeError(
                    f"FC anchor current {I_fc:.4f} A exceeds limit "
                    f"{mag_cfg.current_limit_a:.4f} A"
                )
            print(f"[fczfc] ramping to FC anchor {fc_anchor_Oe:+.2f} Oe "
                  f"(I = {I_fc:+.5f} A)")
            current_now = ramp_current(
                ctrl, current_now, I_fc,
                mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
            )
            time.sleep(mag_cfg.settle_s)

            # 3. Reference image at T_high
            ref_idx = (global_cyc - 1) * n_per_cycle
            acquire_one(
                idx=ref_idx, phase="fczfc_ref", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=fc_anchor_Oe, I_target_A=I_fc,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

            # 4. Cool to T_low with FC anchor field on
            if not run_cfg.skip_stabilize:
                cryo.stabilize_at(run_cfg.t_low_K,
                                  label=f"cycle {cyc} T_low (FC, field on)")

            # 5. Sweep at T_low
            for sweep_idx, B in enumerate(sweep_points, start=1):
                idx = ref_idx + sweep_idx
                print(f"[fczfc cyc {cyc} sweep {sweep_idx}/{len(sweep_points)}] "
                      f"B = {B:+.2f} Oe")
                I_target = field_to_current_a(float(B), run_cfg.coil_Oe_per_a)
                if abs(I_target) > mag_cfg.current_limit_a:
                    raise RuntimeError(
                        f"target current {I_target:.4f} A exceeds limit"
                    )
                current_now = ramp_current(
                    ctrl, current_now, I_target,
                    mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
                )
                time.sleep(mag_cfg.settle_s)

                acquire_one(
                    idx=idx, phase="fczfc", cycle=cyc,
                    cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                    run_cfg=run_cfg,
                    B_set_Oe=float(B), I_target_A=I_target,
                    out_dir=original, csv_writer=writer, csv_f=csv_f,
                )

    print("[fczfc done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original