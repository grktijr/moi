"""moi-acquire: CLI entry point for ZFC/FC/T-sweep acquisition.

This is a thin argparse layer that builds configs from CLI flags and
dispatches to the right experiment runner.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from pyvcam import pvc

from ..config import CryoCfg, MagnetCfg, CameraCfg, RunCfg
from ..psu_registry import PSU_REGISTRY
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import open_magnet, open_sample_source
from ..experiments import (run_zfc, run_fc, run_fczfc, run_fc_acsweep,
                           run_fc_sample_acsweep, run_fc_sample_dcpulse,
                           run_calibration)
from ..postprocess import (
    process_divide, process_fc_divide_per_cycle,
    process_fczfc_divide, process_divide_and_scale_per_cycle,
    apply_calibration,
)
from ..calibration_db import resolve_calibration_file


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="moi-acquire",
        description="MOI acquisition with thermal, magnet, and camera control",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # ---- Common arguments (parent parser) -----------------------------------
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--psu", choices=sorted(PSU_REGISTRY.keys()), required=True)
    common.add_argument("--out", type=Path, required=True)
    common.add_argument("--tag", default="run")
    common.add_argument("--t-high", type=float, default=None)
    common.add_argument("--t-low", type=float, default=None)
    common.add_argument("--coil-oe-per-a", type=float, default=333.3)
    common.add_argument("--exposure-ms", type=int, default=100)
    common.add_argument("--navg", type=int, default=8)
    common.add_argument("--binning", type=int, nargs=2, default=(1, 1),
                        metavar=("BX", "BY"))
    common.add_argument("--roi", type=int, nargs=4, default=None,
                        metavar=("X", "Y", "W", "H"))
    common.add_argument("--no-divide", action="store_true")
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--skip-stabilize", action="store_true")
    # Auto-calibration
    common.add_argument("--apply-calibration", action="store_true",
                        help="apply most recent calibration in default dir")
    common.add_argument("--calibration-file", type=Path, default=None,
                        help="explicit path to a calibration JSON; overrides default lookup")
    common.add_argument("--scale-for-calibration", action="store_true",
                        help="for fc/fczfc modes: rescale divided images by "
                            "(fc_high_intensity / calibration_I_0) so calibration "
                            "polynomial applies. Required for calibrated H values "
                            "in fc/fczfc.")

    # ---- ZFC subcommand -----------------------------------------------------
    z = sub.add_parser("zfc", parents=[common])
    z.add_argument("--field-knots", type=float, nargs="+", default=[0.0, 15.0])
    z.add_argument("--field-nums", type=int, nargs="+", default=[16])

    # ---- FC subcommand ------------------------------------------------------
    f = sub.add_parser("fc", parents=[common])
    f.add_argument("--fc-field", type=float, required=True,
                   help="FC field (Oe); one value. Loop in the shell to scan.")
    f.add_argument("--fc-cycles", type=int, default=1)
    f.add_argument("--ac-amplitude", type=float, default=None,
                   help="AC amplitude in Oe. If set, the FC field becomes "
                        "the DC offset of an AC waveform (requires --psu "
                        "k6221_ac). Converted to current with --coil-oe-per-a.")
    f.add_argument("--ac-frequency", type=float, default=1000.0,
                   help="AC frequency in Hz (default 1000).")
    f.add_argument("--ac-shape", default="SIN",
                   choices=["SIN", "SQU", "TRI", "RAMP"],
                   help="AC waveform shape (default SIN).")
    f.add_argument("--sample-current", action="store_true",
                   help="also source a continuous AC current into the sample "
                        "from the 2nd 6221 (GPIB 29), on throughout the run.")
    f.add_argument("--sample-amp-ma", type=float, default=None,
                   help="sample AC current amplitude in mA (with --sample-current)")
    f.add_argument("--sample-freq", type=float, default=1000.0,
                   help="sample AC current frequency in Hz (default 1000)")
    f.add_argument("--sample-shape", default="SIN",
                   choices=["SIN", "SQU", "TRI", "RAMP"],
                   help="sample AC current waveform shape (default SIN)")
    f.add_argument("--sample-visa", default="GPIB0::29::INSTR")
    f.add_argument("--sample-range-a", type=float, default=None)

    # ---- FCZFC subcommand ---------------------------------------------------
    fz = sub.add_parser("fczfc", parents=[common],
                        help="FC at T_high → high-T ref → cool → sweep field at T_low")
    fz.add_argument("--fc-field", type=float, required=True,
                    help="FC anchor field (Oe); applied at T_high before cooling")
    fz.add_argument("--field-knots", type=float, nargs="+", default=[0.0, 200.0],
                    help="sweep field knots (Oe), e.g. 5 200 0")
    fz.add_argument("--field-nums", type=int, nargs="+", default=[16])
    fz.add_argument("--fczfc-cycles", type=int, default=1,
                    help="number of warm/cool/sweep cycles (default 1)")

    # fc_acsweep: FC-style cool in a near-DC field, then sweep one AC axis at T_low
    fa = sub.add_parser("fc_acsweep", parents=[common],
                        help="FC (near-DC cool) then sweep AC amplitude or "
                             "frequency at T_low; requires --psu k6221_ac")
    fa.add_argument("--axis", choices=["amp", "freq"], required=True,
                    help="which AC axis to sweep")
    fa.add_argument("--offset", type=float, default=0.0,
                    help="DC offset (Oe) = the static FC field the sample cools in")
    fa.add_argument("--values", type=float, nargs="+", required=True,
                    help="swept-axis values: Oe for --axis amp, Hz for --axis freq")
    fa.add_argument("--amp", type=float, default=None,
                    help="fixed AC amplitude (Oe) when --axis freq")
    fa.add_argument("--freq", type=float, default=1000.0,
                    help="fixed AC frequency (Hz) when --axis amp (default 1000)")
    fa.add_argument("--shape", default="SIN",
                    choices=["SIN", "SQU", "TRI", "RAMP"],
                    help="AC waveform shape (default SIN)")
    fa.add_argument("--cool-amp-a", type=float, default=1.1e-4,
                    help="near-DC cool/imaging amplitude in A (range floor; default 1.1e-4 = 0.11 mA)")
    fa.add_argument("--ac-dwell", type=float, default=5.0,
                    help="seconds to apply the AC at each anchor before dropping "
                         "to the floor and imaging (default 5)")
    fa.add_argument("--cool-freq", type=float, default=0.001,
                    help="near-DC base/cool frequency in Hz (very slow = "
                         "quasi-static; default 0.01)")
    fa.add_argument("--wave-range-a", type=float, default=None,
                    help="pinned current range in A (default: current_limit_a)")

    # fc_sample_acsweep: DC field (magnet, fc-style) + AC sample current (2nd 6221, mA)
    fs = sub.add_parser("fc_sample_acsweep", parents=[common],
                        help="FC (real DC field via --psu) then sweep AC SAMPLE "
                             "current (amp mA or freq) from a 2nd 6221 (GPIB 29); "
                             "image under pure DC field (sample AC off)")
    fs.add_argument("--fc-field", type=float, required=True,
                    help="DC field (Oe), ramped on by the magnet PSU")
    fs.add_argument("--fc-cycles", type=int, default=1)
    fs.add_argument("--sample-axis", choices=["amp", "freq"], required=True,
                    help="which sample-AC axis to sweep")
    fs.add_argument("--sample-values", type=float, nargs="+", required=True,
                    help="swept values: mA for --sample-axis amp, Hz for freq")
    fs.add_argument("--sample-amp-ma", type=float, default=None,
                    help="fixed sample AC amplitude (mA) when --sample-axis freq")
    fs.add_argument("--sample-freq", type=float, default=1000.0,
                    help="fixed sample AC frequency (Hz) when --sample-axis amp")
    fs.add_argument("--sample-shape", default="SIN",
                    choices=["SIN", "SQU", "TRI", "RAMP"])
    fs.add_argument("--sample-dwell", type=float, default=5.0,
                    help="seconds to apply sample AC per anchor before imaging")
    fs.add_argument("--sample-visa", default="GPIB0::29::INSTR")
    fs.add_argument("--sample-range-a", type=float, default=None,
                    help="2nd 6221 current range in A")

    # fc_sample_dcpulse: DC field (magnet, fc-style) + DC-current PULSE train on
    # the sample (2nd 6221, mA); image under pure DC field after each pulse.
    fp = sub.add_parser("fc_sample_dcpulse", parents=[common],
                        help="FC (real DC field via --psu) then PULSE a list of "
                             "DC SAMPLE currents (mA) from a 2nd 6221 (GPIB 29); "
                             "image under pure DC field after each pulse")
    fp.add_argument("--fc-field", type=float, required=True,
                    help="DC field (Oe), ramped on by the magnet PSU")
    fp.add_argument("--fc-cycles", type=int, default=1)
    fp.add_argument("--sample-values", type=float, nargs="+", required=True,
                    help="DC pulse currents in mA (signed ok); one image per pulse")
    fp.add_argument("--sample-dwell", type=float, default=5.0,
                    help="seconds to hold each DC pulse before returning to 0 + imaging")
    fp.add_argument("--sample-visa", default="GPIB0::29::INSTR")
    fp.add_argument("--sample-range-a", type=float, default=None,
                    help="2nd 6221 current range in A")


    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # ---- Build configs ------------------------------------------------------
    entry = PSU_REGISTRY[args.psu]
    mag_cfg = MagnetCfg(
        controller=entry["driver"],
        visa_resource=entry["visa"],
        **entry["extras"],
    )
    cam_cfg = CameraCfg(
        exposure_ms=args.exposure_ms,
        navg=args.navg,
        binning=tuple(args.binning),
        roi=tuple(args.roi) if args.roi is not None else None,
    )
    cryo_cfg = CryoCfg()

    run_cfg = RunCfg(
        out_dir=args.out, tag=args.tag,
        coil_Oe_per_a=args.coil_oe_per_a,
        t_high_K=args.t_high, t_low_K=args.t_low,
        do_divide=not args.no_divide,
        dry_run=args.dry_run,
        skip_stabilize=args.skip_stabilize,
        apply_calibration=args.apply_calibration,
        calibration_file=args.calibration_file,
        scale_for_calibration=args.scale_for_calibration,
    )

    # Mode-specific propagation
    if args.mode == "zfc":
        run_cfg = replace(
            run_cfg,
            zfc_field_knots=tuple(args.field_knots),
            zfc_field_nums=tuple(args.field_nums),
        )
    elif args.mode == "fc":
        run_cfg = replace(
            run_cfg,
            fc_field_Oe=args.fc_field,
            fc_cycles=args.fc_cycles,
            ac_amplitude_Oe=args.ac_amplitude,
            ac_frequency_hz=args.ac_frequency,
            ac_shape=args.ac_shape,
            use_sample_source=args.sample_current,
            sample_source_visa=args.sample_visa,
            sample_source_range_a=args.sample_range_a,
            sample_amp_mA=args.sample_amp_ma,
            sample_freq_hz=args.sample_freq,
            sample_shape=args.sample_shape,
        )
    elif args.mode == "fczfc":
        expanded = tuple(args.fc_field for _ in range(args.fczfc_cycles))
        run_cfg = replace(
            run_cfg,
            fczfc_field_Oe=expanded,
            fczfc_sweep_knots=tuple(args.field_knots),
            fczfc_sweep_nums=tuple(args.field_nums),
        )

    elif args.mode == "fc_acsweep":
        # For a freq sweep, the fixed frequency used during setup/base/launch
        # is the MAX of the sweep values (amplitude is launched at worst-case
        # dI/dt); for an amp sweep it is just --freq.
        fixed_freq = (max(args.values) if args.axis == "freq" else args.freq)
        run_cfg = replace(
            run_cfg,
            acsweep_axis=args.axis,
            acsweep_offset_Oe=args.offset,
            acsweep_amp_Oe=args.amp,
            acsweep_freq_hz=fixed_freq,
            acsweep_values=tuple(args.values),
            acsweep_cool_amp_a=args.cool_amp_a,
            acsweep_cool_freq_hz=args.cool_freq,
            acsweep_dwell_s=args.ac_dwell,
            acsweep_range_a=args.wave_range_a,
            ac_shape=args.shape,
            ac_amplitude_Oe=(args.amp if args.axis == "freq"
                             else max(args.values)),  # marks it an AC run
            ac_frequency_hz=fixed_freq,
        )

    elif args.mode == "fc_sample_acsweep":
        run_cfg = replace(
            run_cfg,
            fc_field_Oe=args.fc_field,
            fc_cycles=args.fc_cycles,
            use_sample_source=True,
            sample_source_visa=args.sample_visa,
            sample_source_range_a=args.sample_range_a,
            sample_axis=args.sample_axis,
            sample_values=tuple(args.sample_values),
            sample_amp_mA=args.sample_amp_ma,
            sample_freq_hz=args.sample_freq,
            sample_shape=args.sample_shape,
            sample_dwell_s=args.sample_dwell,
        )

    elif args.mode == "fc_sample_dcpulse":
        run_cfg = replace(
            run_cfg,
            fc_field_Oe=args.fc_field,
            fc_cycles=args.fc_cycles,
            use_sample_source=True,
            sample_source_visa=args.sample_visa,
            sample_source_range_a=args.sample_range_a,
            sample_values=tuple(args.sample_values),
            sample_dwell_s=args.sample_dwell,
        )
 
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Dry-run path: skip hardware ----------------------------------------
    if args.dry_run:
        if args.mode == "zfc":
            run_zfc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        elif args.mode == "fc":
            run_fc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir, sample_ctrl=None)
        elif args.mode == "fczfc":
            run_fczfc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        elif args.mode == "fc_acsweep":
            run_fc_acsweep(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        return 0

    # ---- Hardware connect ---------------------------------------------------
    cryo = Cryostation(cryo_cfg)
    cryo.open()
    print(f"[cryo] connected; state: {cryo.read_state()}")

    pvc.init_pvcam()
    print(f"[camera] PVCAM initialized; opening per acquisition")

    ctrl, rm = open_magnet(mag_cfg)
    print(f"[magnet] {mag_cfg.controller} -> {ctrl.idn()}")

    sample_ctrl = sample_rm = None
    if getattr(run_cfg, "use_sample_source", False):
        _sample_driver = "dc" if args.mode == "fc_sample_dcpulse" else "ac"
        sample_ctrl, sample_rm = open_sample_source(
            run_cfg.sample_source_visa, current_range_a=run_cfg.sample_source_range_a,
            driver=_sample_driver)

    origin = None
    try:
        if args.mode == "zfc":
            origin = run_zfc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fc":
            origin = run_fc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir, sample_ctrl=sample_ctrl)
        elif args.mode == "fczfc":
            origin = run_fczfc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fc_acsweep":
            origin = run_fc_acsweep(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fc_sample_acsweep":
            origin = run_fc_sample_acsweep(cam_cfg, ctrl, mag_cfg, cryo, run_cfg,
                                        out_dir, sample_ctrl=sample_ctrl)
        elif args.mode == "fc_sample_dcpulse":
            origin = run_fc_sample_dcpulse(cam_cfg, ctrl, mag_cfg, cryo, run_cfg,
                                        out_dir, sample_ctrl=sample_ctrl)
    finally:
        try:
            if sample_ctrl is not None:
                sample_ctrl.output_off(); sample_ctrl.inst.close()
        except Exception: pass
        try:
            if ctrl is not None:
                ctrl.set_current(0.0); ctrl.output_off(); ctrl.inst.close()
        except Exception: pass
        try: rm.close()
        except Exception: pass
        try: pvc.uninit_pvcam()
        except Exception: pass
        try: cryo.set_setpoint(args.t_high)
        except Exception: pass
        try: cryo.close()
        except Exception: pass
        print("[cleanup] complete")

    # ---- Post-processing ----------------------------------------------------
    if origin is not None and run_cfg.do_divide:
        if args.mode == "zfc":
            process_divide(origin)
        elif args.mode == "fc":
            csv_path = origin.parent / f"{run_cfg.tag}_fc_metadata.csv"
            process_fc_divide_per_cycle(origin, csv_path)
        elif args.mode in ("fczfc", "fc_acsweep"):
            csv_path = origin.parent / f"{run_cfg.tag}_fczfc_metadata.csv"
            process_fczfc_divide(origin, csv_path)
        elif args.mode in ("fczfc", "fc_acsweep", "fc_sample_acsweep", "fc_sample_dcpulse"):
            csv_path = origin.parent / f"{run_cfg.tag}_fczfc_metadata.csv"
            process_fczfc_divide(origin, csv_path)

    # ---- Auto-calibration apply ---------------------------------------------
    if origin is not None and run_cfg.apply_calibration:
        cal_path = resolve_calibration_file(run_cfg.calibration_file, use_default=True)
        if cal_path is None:
            print("[calibration] no calibration file found; skipping")
        else:
            _pp_mode = "fczfc" if args.mode == "fc_acsweep" else args.mode
            csv_path = origin.parent / f"{run_cfg.tag}_{_pp_mode}_metadata.csv"
            calibrated_dir = origin.parent / "calibrated"

            if run_cfg.scale_for_calibration and args.mode in ("fc", "fczfc", "fc_acsweep"):
                process_divide_and_scale_per_cycle(origin, csv_path, cal_path, mode=_pp_mode)
                source_dir = origin.parent / "multiplied"
            else:
                source_dir = origin.parent / "divided"

            apply_calibration(
                cal_path,
                source_dir=source_dir,
                output_dir=calibrated_dir,
                current_camera_config=cam_cfg,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())