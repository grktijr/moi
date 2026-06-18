"""moi-calibrate: CLI entry point for I-H calibration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import CryoCfg, MagnetCfg, CameraCfg
from ..psu_registry import PSU_REGISTRY
from ..experiments.calibrate import run_calibration


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="moi-calibrate",
        description="I-vs-H calibration via two-pass bipolar sweep",
    )
    p.add_argument("--psu", choices=sorted(PSU_REGISTRY.keys()), required=True)
    p.add_argument("--out", type=Path, required=True,
                   help="output directory for raw images and divided images")
    p.add_argument("--tag", default="calib",
                   help="name for the saved calibration JSON")
    p.add_argument("--t", type=float, required=True,
                   help="calibration temperature in K (should be above Tc)")
    p.add_argument("--I-max-pos", type=float, required=True,
                   help="max positive current in A (pass 1)")
    p.add_argument("--I-max-neg", type=float, required=True,
                   help="max current magnitude for negative pass (positive value)")
    p.add_argument("--n-steps", type=int, default=20,
                   help="number of points per polarity pass")
    p.add_argument("--coil-oe-per-a", type=float, default=333.3)

    # Camera params
    p.add_argument("--exposure-ms", type=int, default=100)
    p.add_argument("--navg", type=int, default=8)
    p.add_argument("--binning", type=int, nargs=2, default=(1, 1),
                   metavar=("BX", "BY"))
    p.add_argument("--roi", type=int, nargs=4, default=None,
                   metavar=("X", "Y", "W", "H"))

    # Fit options
    p.add_argument("--max-poly-order", type=int, default=9)
    p.add_argument("--region-fraction", type=float, default=0.5,
                   help="fraction of image (central crop) used for intensity averaging")

    # Where to save the calibration JSON
    p.add_argument("--save-to", type=Path, default=None,
                   help="directory to save calibration JSON; defaults to "
                        "user-config directory (APPDATA/moi_control/calibrations)")

    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

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

    json_path = run_calibration(
        cam_cfg=cam_cfg, mag_cfg=mag_cfg, cryo_cfg=cryo_cfg,
        t_K=args.t,
        I_max_pos_A=args.I_max_pos, I_max_neg_A=args.I_max_neg,
        n_steps=args.n_steps,
        out_dir=args.out, tag=args.tag,
        coil_Oe_per_a=args.coil_oe_per_a,
        region_fraction=args.region_fraction,
        max_poly_order=args.max_poly_order,
        calibration_save_dir=args.save_to,
    )
    print(f"\n[calibration complete] file: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
