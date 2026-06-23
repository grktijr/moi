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
from ..instruments.magnet import open_magnet
from ..experiments import run_zfc, run_fc, run_tsweep, run_fczfc, run_calibration
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
    f.add_argument("--fc-fields", type=float, nargs="+", required=True)
    f.add_argument("--fc-cycles", type=int, default=1)

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

    # ---- T-sweep subcommand -------------------------------------------------
    t = sub.add_parser("tsweep", parents=[common])
    t.add_argument("--t-knots", type=float, nargs="+", default=[4.0, 12.0])
    t.add_argument("--t-nums", type=int, nargs="+", default=[16])
    t.add_argument("--t-sweep-field", type=float, default=0.0)
    t.add_argument("--t-prep", type=float, default=None)

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
        expanded = tuple(B for B in args.fc_fields for _ in range(args.fc_cycles))
        run_cfg = replace(run_cfg, fc_fields_Oe=expanded, fc_cycles=args.fc_cycles)
    elif args.mode == "tsweep":
        run_cfg = replace(
            run_cfg,
            tsweep_t_knots=tuple(args.t_knots),
            tsweep_t_nums=tuple(args.t_nums),
            tsweep_field_Oe=args.t_sweep_field,
            tsweep_prep_K=args.t_prep,
        )
    elif args.mode == "fczfc":
        expanded = tuple(args.fc_field for _ in range(args.fczfc_cycles))
        run_cfg = replace(
            run_cfg,
            fczfc_field_Oe=expanded,
            fczfc_sweep_knots=tuple(args.field_knots),
            fczfc_sweep_nums=tuple(args.field_nums),
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Dry-run path: skip hardware ----------------------------------------
    if args.dry_run:
        if args.mode == "zfc":
            run_zfc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        elif args.mode == "fc":
            run_fc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        elif args.mode == "fczfc":
            run_fczfc(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        elif args.mode == "tsweep":
            run_tsweep(cam_cfg, None, mag_cfg, None, run_cfg, out_dir)
        return 0

    # ---- Hardware connect ---------------------------------------------------
    cryo = Cryostation(cryo_cfg)
    cryo.open()
    print(f"[cryo] connected; state: {cryo.read_state()}")

    pvc.init_pvcam()
    print(f"[camera] PVCAM initialized; opening per acquisition")

    ctrl, rm = open_magnet(mag_cfg)
    print(f"[magnet] {mag_cfg.controller} -> {ctrl.idn()}")

    origin = None
    try:
        if args.mode == "zfc":
            origin = run_zfc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fc":
            origin = run_fc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "fczfc":
            origin = run_fczfc(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
        elif args.mode == "tsweep":
            origin = run_tsweep(cam_cfg, ctrl, mag_cfg, cryo, run_cfg, out_dir)
    finally:
        try: ctrl.set_current(0.0); ctrl.output_off(); ctrl.inst.close()
        except Exception: pass
        try: rm.close()
        except Exception: pass
        try: pvc.uninit_pvcam()
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
        elif args.mode == "fczfc":
            csv_path = origin.parent / f"{run_cfg.tag}_fczfc_metadata.csv"
            process_fczfc_divide(origin, csv_path)
        elif args.mode == "tsweep":
            process_divide(origin)

    # ---- Auto-calibration apply ---------------------------------------------
    if origin is not None and run_cfg.apply_calibration:
        cal_path = resolve_calibration_file(run_cfg.calibration_file, use_default=True)
        if cal_path is None:
            print("[calibration] no calibration file found; skipping")
        else:
            csv_path = origin.parent / f"{run_cfg.tag}_{args.mode}_metadata.csv"
            calibrated_dir = origin.parent / "calibrated"

            if run_cfg.scale_for_calibration and args.mode in ("fc", "fczfc"):
                process_divide_and_scale_per_cycle(origin, csv_path, cal_path, mode=args.mode)
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
