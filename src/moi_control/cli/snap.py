"""Quick manual snapshot — camera only, no cryostat or PSU.

Opens the camera, applies port / exposure / ROI / binning / navg, grabs one
averaged image, saves a TIFF (and optional PNG preview), and exits. Nothing
else on the rig is touched.

Entry point: moi-snap  (see pyproject.toml [project.scripts])
    moi-snap
    moi-snap --exposure-ms 50 --navg 16 --port 2
    moi-snap --roi 600 600 1200 1200 --binning 2 2 --out D:\\data\\snaps
    moi-snap --port 0 --tag alignment-check
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import tifffile
from pyvcam import pvc

from ..config import CameraCfg
from ..acquisition import acquire_avg_sequence
from ..instruments.camera import PORT_NAME
from .._paths import long_path


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="moi-snap",
        description="Take a single averaged camera snapshot (no cryo/PSU).",
    )
    p.add_argument("--port", type=int, default=2, choices=sorted(PORT_NAME),
                   help="readout port: 0=Sensitivity 1=Speed 2=DynamicRange "
                        "3=SubElectron (default 2)")
    p.add_argument("--exposure-ms", type=int, default=100,
                   help="exposure per frame in ms (default 100)")
    p.add_argument("--navg", type=int, default=400,
                   help="frames to average (default 400)")
    p.add_argument("--binning", type=int, nargs=2, default=[2, 2],
                   metavar=("BX", "BY"), help="binning, e.g. 2 2")
    p.add_argument("--roi", type=int, nargs=4, default=None,
                   metavar=("X", "Y", "W", "H"),
                   help="ROI x y w h; omit for full sensor")
    p.add_argument("--out", type=str, default=".",
                   help="output directory (default current dir)")
    p.add_argument("--tag", type=str, default="snap",
                   help="filename tag (default 'snap')")
    p.add_argument("--camera-name", type=str, default=None,
                   help="explicit PVCAM camera name; omit to autodetect")
    p.add_argument("--png", action="store_true",
                   help="also save an 8-bit PNG preview (auto-scaled)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    cam_cfg = CameraCfg(
        camera_name=args.camera_name,
        port_id=args.port,
        exposure_ms=args.exposure_ms,
        navg=args.navg,
        binning=tuple(args.binning),
        roi=tuple(args.roi) if args.roi is not None else None,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    bx, by = cam_cfg.binning
    roi_tok = ("full" if cam_cfg.roi is None
               else "{}x{}+{}x{}".format(*cam_cfg.roi))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = (f"{args.tag}__{PORT_NAME[args.port].replace(' ', '')}"
            f"__exp-{args.exposure_ms}ms__bin-{bx}x{by}"
            f"__roi-{roi_tok}__navg-{args.navg}__{ts}")
    tif_path = out_dir / f"{stem}.tif"

    print(f"[snap] port={PORT_NAME[args.port]}  exp={args.exposure_ms} ms  "
          f"navg={args.navg}  bin={bx}x{by}  roi={roi_tok}", flush=True)

    pvc.init_pvcam()
    try:
        img = acquire_avg_sequence(cam_cfg, cam_cfg.exposure_ms, cam_cfg.navg)
    finally:
        try:
            pvc.uninit_pvcam()
        except Exception:
            pass

    tifffile.imwrite(long_path(tif_path), img, photometric="minisblack")
    print(f"[snap] saved {tif_path}", flush=True)
    print(f"[snap] shape={img.shape} dtype={img.dtype} "
          f"min={img.min()} max={img.max()} mean={img.mean():.1f}", flush=True)

    if args.png:
        lo, hi = np.percentile(img, [1, 99])
        span = max(hi - lo, 1.0)
        preview = np.clip((img.astype(np.float32) - lo) / span, 0, 1)
        preview8 = (preview * 255).astype(np.uint8)
        png_path = out_dir / f"{stem}.png"
        tifffile.imwrite(long_path(png_path), preview8)
        print(f"[snap] saved preview {png_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
