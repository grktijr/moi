"""Field-cooling experiment mode with multi-cycle support."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Optional

from ..acquisition import (
    CSV_COLUMNS, acquire_one, field_to_current_a, ramp_current,
)
from ..config import CameraCfg, MagnetCfg, RunCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import MagnetControllerBase


def run_fc(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
) -> Optional[Path]:
    """FC: for each B in run_cfg.fc_fields_Oe (with per-field cycle counts):
       1. warm to T_high, ramp field to 0
       2. apply target field, image (fc_high)
       3. cool to T_low with field on, image (fc_low)
    """
    if not run_cfg.fc_fields_Oe:
        raise ValueError("fc requires --fc-fields")
    if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
        if not run_cfg.skip_stabilize:
            raise ValueError("fc requires --t-high and --t-low (unless --skip-stabilize)")

    csv_path = out_dir / f"{run_cfg.tag}_fc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    if run_cfg.dry_run:
        n_cycles = len(run_cfg.fc_fields_Oe)
        print(f"[dry-run] would do {n_cycles} FC cycles across "
              f"fields {sorted(set(run_cfg.fc_fields_Oe))}; CSV: {csv_path}")
        return None

    current_now = 0.0
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    field_counts = {}   # tracks cycle index per field for filename tagging

    with open(csv_path, "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        for global_cyc, B in enumerate(run_cfg.fc_fields_Oe, start=1):
            cyc = field_counts.get(B, 0) + 1
            field_counts[B] = cyc
            print(f"\n[fc cycle {global_cyc}/{len(run_cfg.fc_fields_Oe)}] "
                  f"B = {B:+.2f} Oe (cycle {cyc} at this field)")

            # Warm above Tc with zero field
            current_now = ramp_current(
                ctrl, current_now, 0.0,
                mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
            )
            if not run_cfg.skip_stabilize:
                cryo.stabilize_at(run_cfg.t_high_K,
                                  label=f"cycle {cyc} T_high")

            # Apply target field
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

            # Image at T_high (above Tc, with field) — fc_high
            idx_high = (global_cyc - 1) * 2 + 1
            acquire_one(
                idx=idx_high, phase=f"fc_high_B{B:+.2f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

            # Cool through Tc with field on
            if not run_cfg.skip_stabilize:
                cryo.stabilize_at(run_cfg.t_low_K,
                                  label=f"cycle {cyc} T_low (field on)")

            # Image at T_low (below Tc, with field) — fc_low
            idx_low = idx_high + 1
            acquire_one(
                idx=idx_low, phase=f"fc_low_B{B:+.2f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=float(B), I_target_A=I_target,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
            )

    print("\n[fc done] ramping back to 0 A")
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
