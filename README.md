# moi-control

Automated acquisition and calibration package for magneto-optical imaging
(MOI) experiments. Integrates a Montana Instruments Cryostation, configurable
magnet PSU, and a Photometrics Kinetix sCMOS camera into a unified Python
package with two CLI entry points.

## Modes

- **ZFC**: zero-field cooling, sweep field at base temperature
- **FC**: field-cooling, with multi-cycle support
- **T-sweep**: stabilize at each T point, image, repeat
- **Calibration**: two-pass bipolar I-vs-H polynomial calibration

## Install

From the repository root:

```powershell
conda activate moi
pip install -e .
```

This makes `moi-acquire` and `moi-calibrate` available as shell commands.

## Quick examples

```powershell
# ZFC
moi-acquire zfc --psu keysight --t-high 9 --t-low 4 `
    --field-knots 0 200 --field-nums 21 `
    --binning 2 2 --roi 700 700 1000 1000 `
    --out D:\data\zfc1 --tag zfc1

# FC with 9 cycles per field
moi-acquire fc --psu keithley --t-high 9 --t-low 3.5 `
    --fc-fields 5 10 20 --fc-cycles 9 `
    --out D:\data\fc1 --tag fc1

# Calibration (interactive: pauses for manual polarity switch)
moi-calibrate --psu keysight --t 12 --I-max-pos 0.6 --I-max-neg 0.3 `
    --n-steps 20 --navg 16 --binning 2 2 --roi 700 700 1000 1000 `
    --out D:\calib\jan --tag calib_jan

# ZFC with auto-calibration applied to results
moi-acquire zfc --psu keysight --t-high 9 --t-low 4 `
    --field-knots 0 200 --field-nums 21 `
    --binning 2 2 --roi 700 700 1000 1000 `
    --apply-calibration `
    --out D:\data\zfc2 --tag zfc2
```

## Output

```
<out_dir>/
├── original/               # raw TIFFs
├── divided/                # post-processed (divide by reference)
├── <tag>_<mode>_metadata.csv
└── <tag>_<mode>_metadata_calibrated.csv   # if --apply-calibration
```

The `_calibrated.csv` adds two columns: `I_ratio` (intensity/reference) and
`H_Oe_calibrated` (polynomial-mapped field in Oe).

## Calibration storage

Calibration JSON files live in:
- Windows: `%APPDATA%\moi_control\calibrations\`
- Unix: `~/.config/moi_control/calibrations/`

Override the save location with `moi-calibrate --save-to PATH`. Override the
calibration used for `--apply-calibration` with `--calibration-file PATH`.

When `--apply-calibration` is given without a path, the script uses the most
recently modified file in the default directory.

## Hardware compatibility checks

When applying a calibration, the script verifies that critical camera
settings (port, binning, ROI) match what was used during the calibration
session. Mismatches print warnings; processing proceeds with a noted
caveat that H values may be inaccurate.

## Package layout

```
src/moi_control/
├── config.py              # dataclass configs
├── psu_registry.py        # PSU → driver + VISA + extras
├── instruments/
│   ├── cryostat.py        # Cryostation TCP driver
│   ├── magnet.py          # Keithley / Keysight / HP drivers
│   └── camera.py          # PVCAM helpers
├── acquisition.py         # acquire_one, ramp_current, build_field_points
├── experiments/
│   ├── zfc.py
│   ├── fc.py
│   ├── tsweep.py
│   └── calibrate.py
├── postprocess.py         # divide steps, apply_calibration
├── calibration_db.py      # JSON read/write/locate
└── cli/
    ├── acquire.py         # moi-acquire entry point
    └── calibrate.py       # moi-calibrate entry point
```

## Dependency graph (top to bottom)

```
cli/  →  experiments/  →  acquisition.py  →  instruments/
              ↓                ↓                  ↓
        postprocess.py    config.py + psu_registry.py
        calibration_db.py
```
