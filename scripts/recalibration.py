# recalibrate_with_new_json.py
"""Re-apply per-pixel calibration to existing multiplied/ folders.

Iterates over a grid of (FCField, TLow) combinations, matching the naming
convention produced by the PowerShell orchestrator: fczfc_FC{X.X}Oe_T{X.XX}K.
"""

from pathlib import Path

from moi_control.postprocess import apply_calibration


# --------------------------------------------------------------------------
# Edit these
# --------------------------------------------------------------------------
CAL = Path(
    r"D:\Users\Ren\20260623\pair2\Area5-20x\negative\calibration"
    r"\calib_20260623_1838.json"
)

BASE = Path(r"D:\Users\Ren\20260623\pair2\Area5-20x\negative")

FC_FIELDS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]
T_LOWS    = [7.50, 7.70, 8.00]


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------
n_done = 0
n_skip = 0

for T in T_LOWS:
    for FC in FC_FIELDS:
        run_dir = BASE / f"fczfc_FC{FC:.1f}Oe_T{T:.2f}K"
        multiplied = run_dir / "multiplied"

        if not multiplied.is_dir():
            print(f"[skip] {run_dir.name}: no multiplied/ folder")
            n_skip += 1
            continue

        print(f"\n=== {run_dir.name} ===")
        apply_calibration(
            CAL,
            source_dir=multiplied,
            output_dir=run_dir / "calibrated",
        )
        n_done += 1

print(f"\n=== done: {n_done} recalibrated, {n_skip} skipped ===")