"""FC (real DC field) with an AC SAMPLE-current sweep from a second 6221.

Two instruments, separate jobs:
  - Magnet PSU (ctrl, e.g. k6221_dc): the DC field, ramped on like fc mode.
  - Sample source (sample_ctrl, 2nd 6221 at GPIB 29): AC current driven
    directly into the sample, in mA (no coil — mA is mA), the swept axis.

Sequence (mirrors fc_acsweep, but field=DC magnet, AC=sample source):

  1. warm to T_high, ramp the DC field on (magnet)
  2. high-T base image (field on, sample AC off)      -> phase fczfc_ref
  3. cool to T_low under the DC field
  4. low-T baseline image (field on, sample AC off)    -> phase fczfc (AC~0)
  5. sweep the sample AC axis (amplitude in mA, or frequency):
       per anchor: set freq -> settle -> set amplitude (sample 6221),
                   hold for sample_dwell_s, turn sample AC OFF,
                   image under the pure DC field.                -> phase fczfc
  6. ramp the DC field to 0, stop the sample source.

The DC field is steady throughout (never pulsed). Only the sample AC pulses:
applied for the dwell, then turned fully off (output_off on the 2nd 6221) so
each image is taken under the pure DC field — imaging the sample AC's
after-effect. The high-T base divides all fczfc images (fczfc post-processing).

B_set_Oe is the DC field on every row. The swept sample value (mA or Hz) is
recorded in ac_amp_Oe / ac_freq_Hz columns (ac_amp here means mA of sample
current, not Oe — there is no field coil on the sample source).

Requires --psu k6221_dc (or any field PSU) for the magnet AND --sample-source
(2nd 6221, ctrl.supports_ac) for the sample current.
"""

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

from .._paths import long_path


def run_fc_sample_acsweep(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
    sample_ctrl=None,
) -> Optional[Path]:
    if sample_ctrl is None:
        raise RuntimeError(
            "fc_sample_acsweep requires the 2nd 6221 sample source "
            "(--sample-source); none was opened."
        )
    if not sample_ctrl.supports_ac:
        raise RuntimeError("sample source must be an AC-capable 6221")

    axis = run_cfg.sample_axis
    if axis not in ("amp", "freq"):
        raise ValueError(f"sample_axis must be 'amp' or 'freq', got {axis!r}")
    if not run_cfg.sample_values:
        raise ValueError("fc_sample_acsweep requires --sample-values")
    if axis == "freq" and run_cfg.sample_amp_mA is None:
        raise ValueError("freq sweep requires a fixed sample amplitude (--sample-amp-ma)")
    if run_cfg.fc_field_Oe is None:
        raise ValueError("fc_sample_acsweep requires --fc-field (the DC field)")

    B = float(run_cfg.fc_field_Oe)                       # DC field (Oe)
    I_field = field_to_current_a(B, run_cfg.coil_Oe_per_a)   # magnet current (A)
    if abs(I_field) > mag_cfg.current_limit_a:
        raise RuntimeError(
            f"field current {I_field:.4f} A exceeds magnet limit "
            f"{mag_cfg.current_limit_a:.4f} A"
        )

    # Sample AC parameters (mA -> A for the driver).
    def mA_to_A(ma):
        return float(ma) * 1e-3

    csv_path = out_dir / f"{run_cfg.tag}_fczfc_metadata.csv"   # fczfc schema
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    print(f"[fc_sample_acsweep] DC field B={B:+.3f} Oe ({I_field*1e3:.3f} mA magnet)")
    if axis == "amp":
        print(f"[fc_sample_acsweep] sample: fixed freq={run_cfg.sample_freq_hz:.1f} Hz, "
              f"amplitude sweep (mA): {list(run_cfg.sample_values)}")
    else:
        print(f"[fc_sample_acsweep] sample: fixed amp={run_cfg.sample_amp_mA} mA, "
              f"frequency sweep (Hz): {list(run_cfg.sample_values)}")

    if run_cfg.dry_run:
        print(f"[dry-run] fc_sample_acsweep: 1 base + 1 baseline + "
              f"{len(run_cfg.sample_values)} sample {axis} points; CSV: {csv_path}")
        return None

    # ---- Field on (magnet, DC) ---------------------------------------------
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    # ---- Sample source configured, output OFF for the base/baseline --------
    sample_ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)  # or a sample-specific compliance
    # waveform is configured per anchor; keep it off until the sweep.

    current_now = 0.0

    with open(long_path(csv_path), "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        # 1-2. Warm to T_high, ramp field on, high-T BASE image (sample AC off).
        if not run_cfg.skip_stabilize:
            if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
                raise ValueError("fc_sample_acsweep requires --t-high and --t-low")
            cryo.stabilize_at(run_cfg.t_high_K, label="T_high (field on)")
        current_now = ramp_current(
            ctrl, current_now, I_field,
            mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
        )
        time.sleep(mag_cfg.settle_s)

        acquire_one(
            idx=0, phase="fczfc_ref", cycle=1,
            cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
            run_cfg=run_cfg,
            B_set_Oe=B, I_target_A=I_field,
            out_dir=original, csv_writer=writer, csv_f=csv_f,
            ac_amp_Oe_override=0.0, ac_freq_Hz_override=0.0,   # no sample AC
        )

        # 3. Cool to T_low under the DC field.
        if not run_cfg.skip_stabilize:
            cryo.stabilize_at(run_cfg.t_low_K, label="T_low (field on)")

        # 4. Low-T baseline image (sample AC off) -> sorts first (sample amp 0).
        time.sleep(mag_cfg.settle_s)
        print(f"[fc_sample_acsweep 0] low-T baseline (sample AC off)")
        acquire_one(
            idx=1, phase="fczfc", cycle=1,
            cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
            run_cfg=run_cfg,
            B_set_Oe=B, I_target_A=I_field,
            out_dir=original, csv_writer=writer, csv_f=csv_f,
            ac_amp_Oe_override=0.0, ac_freq_Hz_override=0.0,
            ac_dwell_s=0.0,
        )

        # 5. Sweep the sample AC axis at T_low.
        n = len(run_cfg.sample_values)
        for i, val in enumerate(run_cfg.sample_values, start=2):
            if axis == "amp":
                amp_mA = float(val)
                freq_hz = run_cfg.sample_freq_hz
            else:
                amp_mA = float(run_cfg.sample_amp_mA)
                freq_hz = float(val)
            amp_a = mA_to_A(amp_mA)

            # (a) Configure + start sample AC: frequency first (at off/low), then
            #     amplitude, so the sample never sees a large-amp low-freq
            #     transient. Configure the waveform then output_on.
            sample_ctrl.set_ac_waveform(
                amplitude_a=amp_a,
                freq_hz=freq_hz,
                shape=run_cfg.sample_shape,
                offset_a=0.0,
                range_a=run_cfg.sample_source_range_a,
            )

            sample_ctrl.output_on()
            print(f"[fc_sample_acsweep {i-1}/{n}] sample amp={amp_mA:.3f} mA "
                  f"@ {freq_hz:.1f} Hz for {run_cfg.sample_dwell_s:.1f} s")
            time.sleep(run_cfg.sample_dwell_s)

            # (b) Turn the sample AC fully OFF -> image under pure DC field.
            sample_ctrl.output_off()
            time.sleep(mag_cfg.settle_s)

            # (c) Image (field on, sample AC off). Record the sample anchor value.
            acquire_one(
                idx=i, phase="fczfc", cycle=1,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=B, I_target_A=I_field,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
                sample_amp_mA=amp_mA,       # NB: mA of sample current (not Oe)
                sample_freq_Hz=freq_hz,
                ac_dwell_s=run_cfg.sample_dwell_s,
            )

    print("\n[fc_sample_acsweep done]")
    try:
        sample_ctrl.output_off()
    except Exception:
        pass
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
