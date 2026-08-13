# reprocess_fc_run.py
"""Run FC post-processing on a partially-completed acquisition.

For a run that acquired some data before failing (or that never got past
the acquisition step), this script picks up from the raw TIFFs in original/
and produces divided/, multiplied/, and calibrated/ outputs.
"""

from pathlib import Path

from moi_control.postprocess import (
    process_fc_divide_per_cycle,
    process_divide_and_scale_per_cycle,
    apply_calibration,
)


# --------------------------------------------------------------------------
# Edit these to point at the failed run
# --------------------------------------------------------------------------
OUT_DIR = Path(r"D:\Users\Ren\20260812\MIT-SR\Area11-10x\fc-multifield_fc_T3.50K")
TAG     = "fc_T3.50K"        # matches --tag from the failed orchestrator call

# Calibration file (from the calibration step that ran before the FC run)
CAL = Path(r"D:\Users\Ren\20260812\MIT-SR\Area11-10x\calibration\calib_20260812_1449.json")

# What to run
DO_DIVIDE = True             # divide fc_low by fc_high
DO_SCALE  = True             # scale divided images to I/I_0_cal (needs calibration)
DO_CALIBRATE = True          # apply polynomial per-pixel to get H maps


# --------------------------------------------------------------------------
# Derived paths
# --------------------------------------------------------------------------
origin_dir   = OUT_DIR / "original"
csv_path     = OUT_DIR / f"{TAG}_fc_metadata.csv"

if not origin_dir.is_dir():
    raise FileNotFoundError(f"{origin_dir} does not exist")
if not csv_path.is_file():
    raise FileNotFoundError(f"{csv_path} does not exist")

print(f"Run:        {OUT_DIR.name}")
print(f"Origin:     {origin_dir}")
print(f"CSV:        {csv_path}")
print(f"Calibration: {CAL.name if CAL.exists() else CAL} (exists={CAL.exists()})")

# --------------------------------------------------------------------------
# Step 1: divide fc_low by fc_high, per (B, cycle) pair
# --------------------------------------------------------------------------
if DO_DIVIDE:
    print("\n[step 1] divide fc_low by fc_high per (B, cycle)")
    process_fc_divide_per_cycle(origin_dir, csv_path)

# --------------------------------------------------------------------------
# Step 2: scale divided images to calibration I_0 reference
# --------------------------------------------------------------------------
if DO_SCALE:
    if not CAL.exists():
        raise FileNotFoundError(f"calibration not found: {CAL}")
    print("\n[step 2] scale divided images to I/I_0_cal units")
    process_divide_and_scale_per_cycle(origin_dir, csv_path, CAL, mode="fc")

# --------------------------------------------------------------------------
# Step 3: apply polynomial per-pixel
# --------------------------------------------------------------------------
if DO_CALIBRATE:
    if not CAL.exists():
        raise FileNotFoundError(f"calibration not found: {CAL}")
    print("\n[step 3] apply calibration polynomial per-pixel")
    source_dir = OUT_DIR / ("multiplied" if DO_SCALE else "divided")
    apply_calibration(
        CAL,
        source_dir=source_dir,
        output_dir=OUT_DIR / "calibrated",
    )

print("\n[done]")