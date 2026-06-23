"""Post-processing operations: per-mode image dividing and calibration apply.

The acquisition pipeline produces raw TIFFs in `original/` and a CSV with
metadata. Post-processing operates on these, producing derived outputs in
sibling directories.
"""

from __future__ import annotations

import csv
import glob
import os
from pathlib import Path
from typing import Optional, List

import numpy as np
import pandas as pd
import tifffile
from PIL import Image

from .calibration_db import (
    read_calibration, get_polynomial_coefficients, get_reference_intensity,
    get_camera_config, calibration_is_compatible,
)


# ----------------------------------------------------------------------------
# ZFC / tsweep divide: divide all by image 001
# ----------------------------------------------------------------------------

def process_divide(origin_path: Path) -> None:
    """Divide every image by the first one. Used for ZFC and T-sweep."""
    divided_path = origin_path.parent / "divided"
    divided_path.mkdir(parents=True, exist_ok=True)

    origin_files = sorted(
        glob.glob(str(origin_path / "*.tif")),
        key=lambda x: int(os.path.basename(x)[:3]),
    )
    if not origin_files:
        print("[divide] no TIFFs found in", origin_path)
        return

    base = np.array(Image.open(origin_files[0])).astype(np.float32)
    eps = 1.0
    for f in origin_files:
        img = np.array(Image.open(f)).astype(np.float32)
        divided = img / np.maximum(base, eps)
        out_name = "divid " + os.path.basename(f)
        Image.fromarray(divided).save(divided_path / out_name)
    print(f"[divide] wrote {len(origin_files)} divided images to {divided_path}")


# ----------------------------------------------------------------------------
# FC per-cycle divide: for each (B, cycle), divide low by high
# ----------------------------------------------------------------------------

def process_fc_divide_per_cycle(origin_path: Path, csv_path: Path) -> None:
    """For each (B_set_Oe, cycle), divide the fc_low image by the fc_high image.

    Writes one TIFF per pair into <origin>/../divided/.
    """
    divided_path = origin_path.parent / "divided"
    divided_path.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path)
    df["B_key"] = df["B_set_Oe"].round(4)

    n_done = 0
    n_skip = 0
    for (B_key, cyc), group in df.groupby(["B_key", "cycle"]):
        high_rows = group[group["phase"].str.startswith("fc_high")]
        low_rows  = group[group["phase"].str.startswith("fc_low")]
        if len(high_rows) != 1 or len(low_rows) != 1:
            print(f"[fc-divide] B={B_key:+.2f} cyc={cyc}: "
                  f"got {len(high_rows)} high + {len(low_rows)} low; skipping")
            n_skip += 1
            continue

        try:
            high_img = np.array(Image.open(
                origin_path / high_rows.iloc[0]["filename"])).astype(np.float32)
            low_img = np.array(Image.open(
                origin_path / low_rows.iloc[0]["filename"])).astype(np.float32)
        except FileNotFoundError as e:
            print(f"[fc-divide] missing file for B={B_key} cyc={cyc}: {e}")
            n_skip += 1
            continue

        eps = 1.0
        ratio = low_img / np.maximum(high_img, eps)
        out_name = f"fc_div__" + low_rows.iloc[0]["filename"]
        Image.fromarray(ratio).save(divided_path / out_name)
        n_done += 1
    print(f"[fc-divide done] {n_done} written, {n_skip} skipped")

def process_divide_and_scale_per_cycle(
    origin_path: Path,
    csv_path: Path,
    calibration_path: Path,
    mode: str,                            # "fc" or "fczfc"
    region_fraction: float = 0.5,
) -> None:
    """For each cycle, divide low-T images by the matching high-T reference,
    then scale by (high_T_intensity / cal_I_0).

    Result is in units of intensity ratio relative to the calibration's I=0
    reference, suitable for the polynomial that maps I/I_0 → H.

    Mode-specific behavior:
      - "fc": one fc_high + one fc_low per (B, cycle) pair; multiplier varies
        per B because there's a distinct fc_high image at each B value.
      - "fczfc": one fczfc_ref per cycle (taken at the FC anchor field), N
        fczfc sweep images at various B within that cycle; same multiplier
        used for every sweep image in the cycle (approximation that the
        indicator's high-T response varies slowly across the sweep range).

    Output: <origin>/../multiplied/, filenames matching the low-T (measurement)
    images so apply_calibration_per_pixel can find them by CSV row's filename.
    """
    if mode not in ("fc", "fczfc"):
        raise ValueError(f"unsupported mode: {mode}")

    calibration = read_calibration(calibration_path)
    I_0_cal = get_reference_intensity(calibration)

    multiplied_path = origin_path.parent / "multiplied"
    multiplied_path.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path)
    print(f"[{mode}-scale] calibration I_0 = {I_0_cal:.1f} ADU")

    n_done = 0
    n_skip = 0

    if mode == "fc":
        df["B_key"] = df["B_set_Oe"].round(4)
        for (B_key, cyc), group in df.groupby(["B_key", "cycle"]):
            ref_rows = group[group["phase"].str.startswith("fc_high")]
            data_rows = group[group["phase"].str.startswith("fc_low")]
            label = f"B={B_key:+.2f} cyc={cyc}"
            n_done_grp, n_skip_grp = _scale_group(
                ref_rows, data_rows, origin_path, multiplied_path,
                I_0_cal, region_fraction, mode, label,
            )
            n_done += n_done_grp
            n_skip += n_skip_grp

    else:  # fczfc
        for cyc, group in df.groupby("cycle"):
            ref_rows = group[group["phase"] == "fczfc_ref"]
            data_rows = group[group["phase"] == "fczfc"]
            label = f"cyc={cyc}"
            n_done_grp, n_skip_grp = _scale_group(
                ref_rows, data_rows, origin_path, multiplied_path,
                I_0_cal, region_fraction, mode, label,
            )
            n_done += n_done_grp
            n_skip += n_skip_grp

    print(f"[{mode}-scale] wrote {n_done} scaled images, skipped {n_skip}")


def _scale_group(
    ref_rows, data_rows,
    origin_path: Path, multiplied_path: Path,
    I_0_cal: float, region_fraction: float,
    mode: str, label: str,
) -> tuple[int, int]:
    """Inner helper: scale every data_row image using a single reference image.

    Returns (n_done, n_skip) for this group.
    """
    if len(ref_rows) != 1:
        print(f"[{mode}-scale] {label}: got {len(ref_rows)} refs; skipping group")
        return 0, len(data_rows)
    if len(data_rows) == 0:
        print(f"[{mode}-scale] {label}: no data images; skipping group")
        return 0, 0

    # Load the reference image once
    ref_filename = ref_rows.iloc[0]["filename"]
    try:
        ref_img = np.array(Image.open(
            origin_path / ref_filename)).astype(np.float32)
    except FileNotFoundError as e:
        print(f"[{mode}-scale] {label}: ref file missing: {e}")
        return 0, len(data_rows)

    # Compute the multiplier (one per group)
    ref_intensity = average_central_region(ref_img, fraction=region_fraction)
    multiplier = ref_intensity / max(I_0_cal, 1.0)
    print(f"[{mode}-scale] {label}: multiplier = {multiplier:.3f} "
          f"(ref_I = {ref_intensity:.1f} ADU)")

    eps = 1.0
    n_done = 0
    n_skip = 0
    for _, row in data_rows.iterrows():
        try:
            data_img = np.array(Image.open(
                origin_path / row["filename"])).astype(np.float32)
        except FileNotFoundError as e:
            print(f"[{mode}-scale] {label}: data file missing: {e}")
            n_skip += 1
            continue

        scaled = (data_img / np.maximum(ref_img, eps)) * multiplier
        out_name = row["filename"]
        Image.fromarray(scaled).save(multiplied_path / out_name)
        n_done += 1

    return n_done, n_skip


def process_fczfc_divide(origin_path: Path, csv_path: Path) -> None:
    divided_path = origin_path.parent / "divided"
    divided_path.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path)
    n_done = 0; n_skip = 0
    for cyc, group in df.groupby("cycle"):
        ref_rows = group[group["phase"] == "fczfc_ref"]
        if len(ref_rows) == 0:
            n_skip += len(group); continue
        ref_file = origin_path / ref_rows.iloc[0]["filename"]
        try:
            ref_img = np.array(Image.open(ref_file)).astype(np.float32)
        except FileNotFoundError:
            n_skip += len(group); continue
        eps = 1.0
        for _, row in group[group["phase"] == "fczfc"].iterrows():
            img_path = origin_path / row["filename"]
            if not img_path.exists():
                n_skip += 1; continue
            img = np.array(Image.open(img_path)).astype(np.float32)
            divided = img / np.maximum(ref_img, eps)
            Image.fromarray(divided).save(
                divided_path / ("fczfc_div__" + row["filename"])
            )
            n_done += 1
    print(f"[fczfc-divide] wrote {n_done}, skipped {n_skip}")


# ----------------------------------------------------------------------------
# Apply calibration: add calibrated H column to the CSV
# ----------------------------------------------------------------------------

def evaluate_polynomial(coefficients: List[float], x: float) -> float:
    """Evaluate polynomial sum(c_i * x^i) for i in 0..len(coefficients)-1."""
    result = 0.0
    for power, c in enumerate(coefficients):
        result += c * (x ** power)
    return result


def average_central_region(image: np.ndarray, fraction: float = 0.5) -> float:
    """Return mean intensity of the central fraction of the image."""
    h, w = image.shape[-2:]   # support both (H,W) and (1,H,W)
    fh, fw = int(h * fraction), int(w * fraction)
    y0 = (h - fh) // 2
    x0 = (w - fw) // 2
    img2d = image if image.ndim == 2 else image[0]
    return float(img2d[y0:y0 + fh, x0:x0 + fw].mean())


def apply_calibration(
    calibration_path: Path,
    source_dir: Path,
    output_dir: Path,
    current_camera_config=None,
) -> None:
    """Apply the calibration polynomial element-wise to every TIFF in source_dir.

    Writes one H-map TIFF per source image, with the same filename, to output_dir.
    Source images must be in I/I_0 units (e.g., divided/ for zfc or
    multiplied/ for fc/fczfc with --scale-for-calibration).
    """
    calibration = read_calibration(calibration_path)
    coeffs = get_polynomial_coefficients(calibration)

    if current_camera_config is not None:
        compatible, warnings = calibration_is_compatible(
            calibration, current_camera_config
        )
        for w in warnings:
            print(w)
        if not compatible:
            print("[calibration per-pixel] proceeding despite mismatches")

    output_dir.mkdir(parents=True, exist_ok=True)

    active_coeffs = list(coeffs)
    while len(active_coeffs) > 1 and active_coeffs[-1] == 0.0:
        active_coeffs.pop()

    print(f"[calib per-pixel] polynomial order: {len(active_coeffs) - 1}")
    print(f"[calib per-pixel] source: {source_dir}")
    print(f"[calib per-pixel] output: {output_dir}")

    source_files = sorted(source_dir.glob("*.tif"))
    if not source_files:
        print(f"[calib per-pixel] no TIFFs found in {source_dir}; nothing to do")
        return

    n_done = 0
    for img_path in source_files:
        ratio = np.array(Image.open(img_path)).astype(np.float32)
        H_map = np.zeros_like(ratio, dtype=np.float32)
        for power, c in enumerate(active_coeffs):
            H_map += c * (ratio ** power)
        tifffile.imwrite(output_dir / img_path.name, H_map,
                         photometric="minisblack")
        n_done += 1

    print(f"[calib per-pixel] wrote {n_done} field maps")