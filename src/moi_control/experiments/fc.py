"""Field-cooling experiment mode (single field per invocation).

One invocation cools at exactly one field, repeated --fc-cycles times. Scan
multiple fields by looping in the shell (one moi-acquire call per field),
the same pattern used by fczfc.

Two field modes, selected by whether --ac-amplitude is given:

  - DC (default): the field is a static DC current, ramped on with
    ramp_current. This is a field in Oe, converted to coil current with
    coil_Oe_per_a.

  - AC + DC offset (requires --psu k6221_ac, i.e. ctrl.supports_ac): the coil
    runs a continuous AC waveform (amplitude/frequency/shape fixed for the
    run) whose DC offset is the field. The offset is set with
    ctrl.set_dc_offset(), which re-arms the 6221 so the running waveform
    adopts it (a bare offset write is ignored while armed). The waveform runs
    through the high-T image, the cooldown, and the low-T image.

In AC mode the images time-average over many AC periods (exposure ~ms, AC
~kHz), so the recorded intensity reflects the DC-offset field for a symmetric
waveform. The AC presence matters for the physics of cooling through Tc, not
for the averaged image contrast; resolving AC-specific response would need
lock-in or phase-locked imaging (out of scope).
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


def run_fc(
    cam_cfg: CameraCfg, ctrl: MagnetControllerBase, mag_cfg: MagnetCfg,
    cryo: Cryostation, run_cfg: RunCfg, out_dir: Path,
    sample_ctrl=None,
) -> Optional[Path]:
    """FC at a single field (run_cfg.fc_field_Oe), repeated fc_cycles times.

    Each cycle:
      1. warm to T_high with zero field
      2. apply the field, image (fc_high)
      3. cool to T_low with field on, image (fc_low)

    DC mode ramps the field on with ramp_current. AC mode (ctrl.supports_ac
    and --ac-amplitude set) sets the field as the DC offset of a continuous
    AC waveform via ctrl.set_dc_offset().
    """
    if run_cfg.fc_field_Oe is None:
        raise ValueError("fc requires --fc-field")
    if run_cfg.t_high_K is None or run_cfg.t_low_K is None:
        if not run_cfg.skip_stabilize:
            raise ValueError(
                "fc requires --t-high and --t-low (unless --skip-stabilize)"
            )

    B = float(run_cfg.fc_field_Oe)
    n_cycles = max(1, int(run_cfg.fc_cycles))

    # ---- Sample current fork (orthogonal to the field mode) -----------------
    # A 2nd 6221 (sample_ctrl, GPIB 29) can source a CONTINUOUS AC current into
    # the sample, running throughout the whole fc run (on during imaging). This
    # forks BEFORE the DC-vs-(AC+DC) field decision and applies to both: the
    # field logic below is unchanged; the sample current is added on top. The
    # applied sample waveform is recorded per image in the sample_amp_mA /
    # sample_freq_Hz CSV columns.
    sample_on = (bool(run_cfg.use_sample_source)
                 and run_cfg.sample_amp_mA is not None)
    smp_amp_mA = run_cfg.sample_amp_mA if sample_on else None
    smp_offset_mA = run_cfg.sample_offset_mA if sample_on else None
    smp_freq_hz = run_cfg.sample_freq_hz if sample_on else None
    if sample_on and sample_ctrl is None:
        raise RuntimeError(
            "fc with --sample-current requires the 2nd 6221 sample source "
            "(none was opened)."
        )
    if sample_on and not getattr(sample_ctrl, "supports_ac", False):
        raise RuntimeError("sample source must be an AC-capable 6221")

    # ---- DC vs AC+DC mode ---------------------------------------------------
    ac_requested = (run_cfg.ac_amplitude_Oe is not None
                    and run_cfg.ac_amplitude_Oe > 0.0)
    ac_mode = ac_requested and ctrl.supports_ac
    if ac_requested and not ac_mode:
        raise RuntimeError(
            "AC field requested (--ac-amplitude) but the selected PSU "
            f"({mag_cfg.controller}) has no AC waveform support. "
            "Use --psu k6221_ac for AC field-cooling."
        )

    csv_path = out_dir / f"{run_cfg.tag}_fc_metadata.csv"
    original = out_dir / "original"
    original.mkdir(parents=True, exist_ok=True)

    I_field = field_to_current_a(B, run_cfg.coil_Oe_per_a)

    if run_cfg.dry_run:
        if ac_mode:
            desc = (f"AC+DC: {run_cfg.ac_amplitude_Oe:+.3f} Oe AC @ "
                    f"{run_cfg.ac_frequency_hz:.1f} Hz ({run_cfg.ac_shape}), "
                    f"DC offset {B:+.3f} Oe")
        else:
            desc = f"DC {B:+.3f} Oe"
        if sample_on:
            desc += (f" + sample AC {smp_amp_mA:+.3f} mA @ {smp_freq_hz:.1f} Hz "
                     f"({run_cfg.sample_shape})")
        print(f"[dry-run] fc {desc}, {n_cycles} cycle(s); CSV: {csv_path}")
        return None

    ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)

    # ---- Configure the AC waveform once (fixed amplitude/frequency/shape) ---
    if ac_mode:
        ac_amplitude_a = field_to_current_a(
            run_cfg.ac_amplitude_Oe, run_cfg.coil_Oe_per_a
        )
        peak_A = abs(I_field) + abs(ac_amplitude_a)
        if peak_A > mag_cfg.current_limit_a:
            raise RuntimeError(
                f"AC+DC peak current {peak_A:.4f} A "
                f"(|offset| {abs(I_field):.4f} + AC amp {abs(ac_amplitude_a):.4f}) "
                f"exceeds limit {mag_cfg.current_limit_a:.4f} A"
            )
        ctrl.set_ac_waveform(
            range_a=None,
            offset_a=I_field,
            amplitude_a=ac_amplitude_a,
            freq_hz=run_cfg.ac_frequency_hz,
            shape=run_cfg.ac_shape,
        )
        print(f"[fc] AC field: {run_cfg.ac_amplitude_Oe:+.3f} Oe "
              f"({ac_amplitude_a * 1e3:.3f} mA pk) @ "
              f"{run_cfg.ac_frequency_hz:.1f} Hz ({run_cfg.ac_shape}); "
              f"DC offset = {B:+.3f} Oe ({I_field * 1e3:.3f} mA)")
    else:
        if abs(I_field) > mag_cfg.current_limit_a:
            raise RuntimeError(
                f"field current {I_field:.4f} A exceeds limit "
                f"{mag_cfg.current_limit_a:.4f} A"
            )

    # Start at zero field.
    current_now = 0.0
    if ac_mode:
        pass  # stand by
    else:
        ctrl.set_current(0.0)
        ctrl.output_on()

    # Start the continuous sample AC current (runs through the whole run).
    if sample_on:
        smp_amp_a = float(smp_amp_mA) * 1e-3
        smp_offset_a = float(smp_amp_mA) * 1e-3
        sample_ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
        sample_ctrl.set_ac_waveform(
            amplitude_a=smp_amp_a, freq_hz=smp_freq_hz,
            shape=run_cfg.sample_shape, range_a=run_cfg.sample_source_range_a,
            offset_a=smp_offset_a
        )
        sample_ctrl.set_dc_offset(0.0)     # no DC offset on the sample current
        sample_ctrl.output_on()
        print(f"[fc] sample AC current ON: {smp_amp_mA:+.3f} mA @ "
              f"{smp_freq_hz:.1f} Hz ({run_cfg.sample_shape}) - continuous")

    with open(long_path(csv_path), "w", newline="") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(CSV_COLUMNS)

        for cyc in range(1, n_cycles + 1):
            note = " (DC offset)" if ac_mode else ""
            print(f"\n[fc cycle {cyc}/{n_cycles}] B = {B:+.3f} Oe{note}")

            # (1) Warm to T_high with zero field.
            if not run_cfg.skip_stabilize:
                cryo.stabilize_at(run_cfg.t_high_K, label=f"cycle {cyc} T_high")

            # (2) Apply the field.
            if ac_mode:
                ctrl.output_on()  # arm ac wave as configured
                current_now = I_field
            else:
                current_now = ramp_current(
                    ctrl, current_now, I_field,
                    mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
                )
            time.sleep(mag_cfg.settle_s)

            # Image at T_high (above Tc, with field) — fc_high
            idx_high = (cyc - 1) * 2 + 1
            acquire_one(
                idx=idx_high, phase=f"fc_high_B{B:+.3f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=B, I_target_A=I_field,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
                sample_amp_mA=smp_amp_mA, sample_freq_Hz=smp_freq_hz,
            )

            # (3) Cool through Tc with field on.
            if not run_cfg.skip_stabilize:
                cryo.stabilize_at(run_cfg.t_low_K,
                                  label=f"cycle {cyc} T_low (field on)")

            # Image at T_low (below Tc, with field) — fc_low
            idx_low = idx_high + 1
            acquire_one(
                idx=idx_low, phase=f"fc_low_B{B:+.3f}Oe", cycle=cyc,
                cam_cfg=cam_cfg, ctrl=ctrl, mag_cfg=mag_cfg, cryo=cryo,
                run_cfg=run_cfg,
                B_set_Oe=B, I_target_A=I_field,
                out_dir=original, csv_writer=writer, csv_f=csv_f,
                sample_amp_mA=smp_amp_mA, sample_freq_Hz=smp_freq_hz,
            )

    # ---- Ramp / turn off ----------------------------------------------------
    print("\n[fc done]")
    if sample_on:
        try: sample_ctrl.output_off()
        except Exception: pass
    if ac_mode:
        ctrl.output_off()            # abort waveform + output off
    else:
        ramp_current(ctrl, current_now, 0.0,
                     mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
    return original
