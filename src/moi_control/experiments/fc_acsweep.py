"""FC with an internal AC-axis sweep (amplitude or frequency).

Imitates field-cooling in a DC field, then sweeps one AC axis at low-T:

  1. warm to T_high
  2. set AC: offset = the DC field, amplitude = near-DC floor, freq = fixed
  3. take ONE high-T base image (effectively a DC field-cool reference)
  4. cool to T_low under the same near-DC AC field
  5. at T_low, sweep the chosen axis (amplitude OR frequency), taking one
     image per point:
       - amp sweep:  offset fixed, freq fixed, amplitude steps through values
       - freq sweep: offset fixed, amplitude fixed, freq steps through values
  6. ramp offset to 0, stop the waveform

The high-T image is the single base for all low-T sweep images (fczfc model).
Phases are written as "fczfc_ref" (base) and "fczfc" (sweep points) so the
existing fczfc post-processing (divide-by-base + scale) applies unchanged.

B_set_Oe is written as the DC OFFSET on every row, so the fczfc scaling step
uses the offset as the static field for the calibration I_0 lookup — i.e. the
divided image is scaled as if cooled in that DC field.

The swept AC value (amplitude in Oe, or frequency in Hz) is recorded per row
in the ac_amp_Oe / ac_freq_Hz CSV columns, so moi-stack can filter and stack
along the swept axis.

Requires --psu k6221_ac (ctrl.supports_ac).
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Optional

from ..acquisition import (
    CSV_COLUMNS, acquire_one, field_to_current_a,
)
from ..config import CameraCfg, MagnetCfg, RunCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import MagnetControllerBase

from .._paths import long_path


def run_fc_acsweep(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
) -> Optional[Path]:
    if not ctrl.supports_ac:
        raise RuntimeError(
            "fc_acsweep requires an AC-capable PSU (--psu k6221_ac); "
            f"got {mag_cfg.controller}"
        )
    axis = run_cfg.acsweep_axis
    if axis not in ("amp", "freq"):
        raise ValueError(f"acsweep_axis must be 'amp' or 'freq', got {axis!r}")
    if not run_cfg.acsweep_values:
        raise ValueError("fc_acsweep requires sweep values (--acsweep-values)")
    if axis == "freq" and run_cfg.acsweep_amp_Oe is None:
        raise ValueError("freq sweep requires a fixed amplitude (--acsweep-amp)")

    offset_Oe = float(run_cfg.acsweep_offset_Oe)
    offset_a = field_to_current_a(offset_Oe, run_cfg.coil_Oe_per_a)
    cool_amp_a = float(run_cfg.acsweep_cool_amp_a)
    base_freq = float(run_cfg.acsweep_cool_freq_hz)

    # Peak current across the whole sweep, to size / check the pinned range.
    if axis == "amp":
        max_amp_Oe = max(abs(v) for v in run_cfg.acsweep_values)
    else:
        max_amp_Oe = abs(run_cfg.acsweep_amp_Oe)
    max_amp_a = field_to_current_a(max_amp_Oe, run_cfg.coil_Oe_per_a)
    peak_a = abs(offset_a) + abs(max_amp_a)
    if peak_a > mag_cfg.current_limit_a:
        raise RuntimeError(
            f"peak current {peak_a:.4f} A (|offset| {abs(offset_a):.4f} + "
            f"max amp {abs(max_amp_a):.4f}) exceeds limit "
            f"{mag_cfg.current_limit_a:.4f} A"
        )
    range_a = run_cfg.acsweep_range_a or mag_cfg.current_limit_a

    csv_path = out_dir / f"{run_cfg.tag}_fczfc_metadata.csv"   # fczfc schema
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    unit = "Oe" if axis == "amp" else "Hz"
    print(f"[fc_acsweep] axis={axis}  offset(DC field)={offset_Oe:+.3f} Oe  "
          f"cool_amp={cool_amp_a*1e3:.3f} mA base_freq={base_freq:.3f} Hz (near-DC)")
    if axis == "amp":
        print(f"[fc_acsweep] fixed freq={run_cfg.acsweep_freq_hz:.1f} Hz  "
              f"amplitude sweep (Oe): {list(run_cfg.acsweep_values)}")
    else:
        print(f"[fc_acsweep] fixed amp={run_cfg.acsweep_amp_Oe:+.3f} Oe  "
              f"frequency sweep (Hz): {list(run_cfg.acsweep_values)}")

    if run_cfg.dry_run:
        print(f"[dry-run] fc_acsweep: 1 base + {len(run_cfg.acsweep_values)} "
              f"low-T {axis} points; CSV: {csv_path}")
        return None

    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)

    # Configure the pseudo-DC base/cool waveform: floor amplitude at a very
    # low frequency (acsweep_cool_freq_hz, e.g. 0.001 Hz) so the field is
    # quasi-static during the base image and the cooldown — a proper DC
    # imitation. This is independent of the sweep values; the sweep itself
    # proceeds in the user-supplied order.
    
    ctrl.set_ac_waveform(
        range_a=range_a,
        offset_a=offset_a,
        amplitude_a=cool_amp_a,
        freq_hz=base_freq,
        shape=run_cfg.ac_shape,
    )
    ctrl.output_on()

    with open(long_path(csv_path), "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        # 1-2-3. Warm to T_high, take the near-DC high-T BASE image.
        if not run_cfg.skip_stabilize:
            if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
                raise ValueError("fc_acsweep requires --t-high and --t-low")
            cryo.stabilize_at(run_cfg.t_high_K, label="T_high (near-DC base)")
        time.sleep(mag_cfg.settle_s)


        # Base recorded as fczfc_ref; AC columns show the near-DC cool amp.
        cool_amp_Oe = cool_amp_a * run_cfg.coil_Oe_per_a
        acquire_one(
            idx=0, phase="fczfc_ref", cycle=1,
            cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
            run_cfg=run_cfg,
            B_set_Oe=offset_Oe, I_target_A=offset_a,     # offset = static field
            out_dir=original, csv_writer=writer, csv_f=csv_f,
            ac_amp_Oe_override=cool_amp_Oe, ac_freq_Hz_override=base_freq,
        )

        # 4. Cool to T_low under the same near-DC AC field.
        if not run_cfg.skip_stabilize:
            cryo.stabilize_at(run_cfg.t_low_K,
                              label="T_low (near-DC field on)")

        # 4b. Low-T pseudo-DC point: image at the near-DC floor BEFORE any AC
        #     is applied at low-T. Tagged "fczfc" (a real sweep point: divided
        #     by the high-T base), with ac_amp = the floor value so it sorts
        #     FIRST on the amplitude axis in moi-stack — the AC~0 baseline.
        time.sleep(mag_cfg.settle_s)
        base_amp_for_sort = cool_amp_Oe   # floor amplitude in Oe (smallest)
        base_freq_for_sort = base_freq
        print(f"[fc_acsweep 0] low-T pseudo-DC baseline at floor "
              f"{cool_amp_Oe:.4f} Oe @ {base_freq_for_sort:g} Hz")
        acquire_one(
            idx=1, phase="fczfc", cycle=1,
            cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
            run_cfg=run_cfg,
            B_set_Oe=offset_Oe, I_target_A=offset_a,
            out_dir=original, csv_writer=writer, csv_f=csv_f,
            ac_amp_Oe_override=base_amp_for_sort,
            ac_freq_Hz_override=base_freq_for_sort,
            ac_dwell_s=0.0,   # no AC applied for this baseline point
        )

        # 5. Sweep the chosen axis at T_low (indices start at 2; the low-T
        #    pseudo-DC baseline above is index 1). At each anchor: apply the AC for
        #    ac_dwell_s (the AC "does work" on the sample), drop the amplitude
        #    back to the near-DC floor, then image — so every photo is taken
        #    under static DC (the offset), imaging the AC's after-effect.
        #    The CSV/filename record the ANCHOR value (what was applied), not
        #    the floor amplitude present during the shot.
        n = len(run_cfg.acsweep_values)
        cool_amp_a = float(run_cfg.acsweep_cool_amp_a)
        for i, val in enumerate(run_cfg.acsweep_values, start=2):
            if axis == "amp":
                amp_Oe = float(val)
                amp_a = field_to_current_a(amp_Oe, run_cfg.coil_Oe_per_a)
                freq_hz = run_cfg.acsweep_freq_hz
            else:
                amp_Oe = float(run_cfg.acsweep_amp_Oe)
                amp_a = field_to_current_a(amp_Oe, run_cfg.coil_Oe_per_a)
                freq_hz = float(val)

            # (a) Apply the real AC for ac_dwell_s.
            ctrl.set_ac_frequency(freq_hz)
            time.sleep(mag_cfg.settle_s)
            ctrl.set_ac_amplitude(amp_a)
            
            print(f"[fc_acsweep {i-1}/{n}] applying amp={amp_Oe:+.3f} Oe "
                  f"({amp_a*1e3:.3f} mA) @ {freq_hz:.1f} Hz for "
                  f"{run_cfg.acsweep_dwell_s:.1f} s")
            time.sleep(run_cfg.acsweep_dwell_s)

            # (b) Drop to the near-DC floor for imaging: floor amplitude AND
            #     the very-low cool frequency, so the image is taken under the
            #     same quasi-static condition as the base/cooldown (not at the
            #     anchor's sweep frequency). set_ac_frequency is a bare write
            #     (hot-changeable); set_ac_amplitude too.
            ctrl.set_ac_amplitude(cool_amp_a)            # -> floor amplitude
            time.sleep(mag_cfg.settle_s)
            ctrl.set_ac_frequency(base_freq)             # -> cool freq (e.g. 0.01 Hz)
            

            # (c) Image (near-DC). Record the ANCHOR amp/freq, not the floor.
            acquire_one(
                idx=i, phase="fczfc", cycle=1,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=offset_Oe, I_target_A=offset_a,  # offset = static field
                out_dir=original, csv_writer=writer, csv_f=csv_f,
                ac_amp_Oe_override=amp_Oe, ac_freq_Hz_override=freq_hz,
                ac_dwell_s=run_cfg.acsweep_dwell_s,
            )

    print("\n[fc_acsweep done]")
    ctrl.output_off()
    return original