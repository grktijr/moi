"""FC (real DC field) with a DC-current PULSE train on the sample (2nd 6221).

Two instruments, separate jobs:
  - Magnet PSU (ctrl, e.g. k6221_dc): the DC field, ramped on like fc mode,
    steady throughout.
  - Sample source (sample_ctrl, 2nd 6221 at GPIB 29, DC mode): a DC current
    PULSE driven directly into the sample, in mA (no coil -- mA is mA). The
    pulse amplitude is the swept axis.

Sequence (mirrors fc_sample_acsweep, but the sample stimulus is a DC pulse,
not an AC waveform):

  1. warm to T_high, ramp the DC field on (magnet)
  2. high-T base image (field on, sample off)          -> phase fczfc_ref
  3. cool to T_low under the DC field
  4. low-T baseline image (field on, sample off)        -> phase fczfc (pulse 0)
  5. for each pulse current I (mA) in --sample-values:
       drive the sample DC current to I, hold for sample_dwell_s, return the
       sample to 0, settle, then image under the pure DC field.    -> fczfc
  6. ramp the DC field to 0, stop thesample_ctrl sample source.

The DC field is steady throughout (never pulsed). Only the SAMPLE current is
pulsed: driven to I for the dwell, then returned to 0 so each image is taken
under the pure DC field -- imaging the DC pulse's after-effect (the remanent
flux state it leaves). The high-T base divides all fczfc images.

This is a SOFTWARE-TIMED DC pulse via the 6221 DC source (set level -> hold ->
return to 0), seconds-scale, which is what per-pulse imaging needs. It is NOT
the 6221's hardware pulse-delta (PDELta) mode -- that needs a 2182A trigger
link and emits sub-second pulses you cannot image one at a time.

B_set_Oe is the DC field on every row. The pulse current (mA) is recorded in
the sample_amp_mA column; sample_freq_Hz is 0 (DC, no frequency). The pulse
list may be signed for bipolar pulses (the 6221 DC source is bipolar).

Requires --psu k6221_dc (or any field PSU) for the magnet AND the 2nd 6221
opened in DC mode for the sample pulse (acquire.py opens it with driver="dc").
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


def run_fc_sample_dcpulse(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
    sample_ctrl=None,
) -> Optional[Path]:
    if sample_ctrl is None:
        raise RuntimeError(
            "fc_sample_dcpulse requires the 2nd 6221 sample source "
            "(--sample-source); none was opened."
        )
    # Must be the DC source, not the AC-waveform driver: set_current on the AC
    # driver writes a waveform offset (meaningful only while a wave is armed),
    # so it cannot source a plain DC pulse. acquire.py opens this driver="dc".
    if getattr(sample_ctrl, "supports_ac", False):
        raise RuntimeError(
            "fc_sample_dcpulse needs the sample 6221 in DC mode "
            "(open_sample_source(..., driver='dc')); got an AC driver."
        )

    if not run_cfg.sample_values:
        raise ValueError("fc_sample_dcpulse requires --sample-values (pulse mA)")
    if run_cfg.fc_field_Oe is None:
        raise ValueError("fc_sample_dcpulse requires --fc-field (the DC field)")

    B = float(run_cfg.fc_field_Oe)                           # DC field (Oe)
    I_field = field_to_current_a(B, run_cfg.coil_Oe_per_a)   # magnet current (A)
    if abs(I_field) > mag_cfg.current_limit_a:
        raise RuntimeError(
            f"field current {I_field:.4f} A exceeds magnet limit "
            f"{mag_cfg.current_limit_a:.4f} A"
        )

    def mA_to_A(ma):
        return float(ma) * 1e-3

    csv_path = out_dir / f"{run_cfg.tag}_fczfc_metadata.csv"   # fczfc schema
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    print(f"[fc_sample_dcpulse] DC field B={B:+.3f} Oe ({I_field*1e3:.3f} mA magnet)")
    print(f"[fc_sample_dcpulse] sample DC pulses (mA): {list(run_cfg.sample_values)}, "
          f"hold {run_cfg.sample_dwell_s:.1f} s each")

    if run_cfg.dry_run:
        print(f"[dry-run] fc_sample_dcpulse: 1 base + 1 baseline + "
              f"{len(run_cfg.sample_values)} DC pulse(s); CSV: {csv_path}")
        return None

    # ---- Field on (magnet, DC) ---------------------------------------------
    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    ctrl.set_current(0.0)
    ctrl.output_on()

    # ---- Sample source: DC, compliance set, output OFF for base/baseline ----
    sample_ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
    sample_ctrl.set_current(0.0)
    # output stays OFF until the pulse sweep begins.

    current_now = 0.0

    with open(long_path(csv_path), "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        # 1-2. Warm to T_high, ramp field on, high-T BASE image (sample off).
        if not run_cfg.skip_stabilize:
            if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
                raise ValueError("fc_sample_dcpulse requires --t-high and --t-low")
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
            ac_amp_Oe_override=0.0, ac_freq_Hz_override=0.0,   # no sample stimulus
            sample_amp_mA=0.0, sample_freq_Hz=0.0,
        )

        # 3. Cool to T_low under the DC field.
        if not run_cfg.skip_stabilize:
            cryo.stabilize_at(run_cfg.t_low_K, label="T_low (field on)")

        # 4. Low-T baseline image (sample off) -> sorts first (pulse 0).
        time.sleep(mag_cfg.settle_s)
        print(f"[fc_sample_dcpulse 0] low-T baseline (sample off)")
        acquire_one(
            idx=1, phase="fczfc", cycle=1,
            cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
            run_cfg=run_cfg,
            B_set_Oe=B, I_target_A=I_field,
            out_dir=original, csv_writer=writer, csv_f=csv_f,
            ac_amp_Oe_override=0.0, ac_freq_Hz_override=0.0,
            sample_amp_mA=0.0, sample_freq_Hz=0.0,
            ac_dwell_s=0.0,
        )

        # 5. Pulse the sample DC current at T_low, one image after each pulse.
        #    Arm the DC output once (sourcing 0 A); pulse by changing the level
        #    to I, holding, then returning to 0 -- no relay toggling per pulse.
        sample_ctrl.output_on()
        n = len(run_cfg.sample_values)
        for i, val in enumerate(run_cfg.sample_values, start=2):
            pulse_mA = float(val)
            pulse_a = mA_to_A(pulse_mA)

            # (a) Drive the DC pulse and hold.
            sample_ctrl.set_current(pulse_a)
            print(f"[fc_sample_dcpulse {i-1}/{n}] pulse {pulse_mA:+.3f} mA "
                  f"for {run_cfg.sample_dwell_s:.1f} s")
            time.sleep(run_cfg.sample_dwell_s)

            # (b) Return the sample to 0 A -> image under the pure DC field.
            sample_ctrl.set_current(0.0)
            time.sleep(mag_cfg.settle_s)

            # (c) Image (field on, sample at 0). Record the pulse current (mA).
            acquire_one(
                idx=i, phase="fczfc", cycle=1,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=B, I_target_A=I_field,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
                sample_amp_mA=pulse_mA,     # NB: mA of DC sample pulse (not Oe)
                sample_freq_Hz=0.0,         # DC: no frequency
                ac_dwell_s=run_cfg.sample_dwell_s,
            )

    print("\n[fc_sample_dcpulse done]")
    try:
        sample_ctrl.set_current(0.0)
        sample_ctrl.output_off()
    except Exception:
        pass
    ramp_current(ctrl, current_now, 0.0,
                 mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
