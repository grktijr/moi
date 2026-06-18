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
        out_name = f"fc_div__B{B_key:+.2f}Oe__cyc{int(cyc):02d}.tif"
        Image.fromarray(ratio).save(divided_path / out_name)
        n_done += 1
    print(f"[fc-divide done] {n_done} written, {n_skip} skipped")

def process_fc_divide_and_scale_per_cycle(
    origin_path: Path,
    csv_path: Path,
    calibration_path: Path,
    region_fraction: float = 0.5,
) -> None:
    """For each FC cycle, divide low by high, then scale by (fc_high / cal_I_0).
    
    The result is in units of (intensity / calibration's I=0 reference), suitable
    for direct application of the calibration polynomial that maps I/I_0 → H.
    
    The scaling assumes:
    - The fc_high image (at T_high, field B_0) and the calibration's I=0 image
      (at T_high, field 0) differ only in the additive Faraday contribution
      from the FC anchor field. This is approximately true for a linearly-
      responding indicator at moderate fields.
    - The calibration's camera settings match the run's. A warning is printed
      if they don't.
    
    Output goes to <origin>/../multiplied/ with the same filenames as the
    fc_low images (no prefix). This means apply_calibration with
    source_dir=multiplied_path can find files by their CSV-listed names.
    """
    calibration = read_calibration(calibration_path)
    I_0_cal = get_reference_intensity(calibration)
    
    multiplied_path = origin_path.parent / "multiplied"
    multiplied_path.mkdir(parents=True, exist_ok=True)
    
    df = pd.read_csv(csv_path)
    df["B_key"] = df["B_set_Oe"].round(4)
    
    n_done = 0
    n_skip = 0
    
    print(f"[fc-scale] calibration I_0 = {I_0_cal:.1f} ADU")
    
    for (B_key, cyc), group in df.groupby(["B_key", "cycle"]):
        high_rows = group[group["phase"].str.startswith("fc_high")]
        low_rows = group[group["phase"].str.startswith("fc_low")]
        if len(high_rows) != 1 or len(low_rows) != 1:
            print(f"[fc-scale] B={B_key:+.2f} cyc={cyc}: got "
                  f"{len(high_rows)}+{len(low_rows)}; skipping")
            n_skip += 1
            continue
        
        try:
            high_img = np.array(Image.open(
                origin_path / high_rows.iloc[0]["filename"])).astype(np.float32)
            low_img = np.array(Image.open(
                origin_path / low_rows.iloc[0]["filename"])).astype(np.float32)
        except FileNotFoundError as e:
            print(f"[fc-scale] missing file: {e}")
            n_skip += 1
            continue
        
        # Multiplier: high-T image intensity / calibration's I=0 reference
        high_intensity = average_central_region(high_img, fraction=region_fraction)
        multiplier = high_intensity / max(I_0_cal, 1.0)
        
        # Divide low by high, then scale to calibration units
        eps = 1.0
        ratio = low_img / np.maximum(high_img, eps)
        scaled = ratio * multiplier
        
        # Save using the fc_low filename (so apply_calibration finds it via CSV)
        out_name = low_rows.iloc[0]["filename"]
        Image.fromarray(scaled).save(multiplied_path / out_name)
        
        print(f"[fc-scale] B={B_key:+.2f} cyc={cyc}: multiplier = {multiplier:.3f} "
              f"(high_I = {high_intensity:.1f} ADU)")
        n_done += 1
    
    print(f"[fc-scale] wrote {n_done} scaled images, skipped {n_skip}")


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