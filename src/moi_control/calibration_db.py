"""Calibration file persistence.

Calibration JSON files contain polynomial coefficients (m0..m9) for mapping
intensity ratio (I/I_0) → magnetic field H, plus a metadata header recording
the camera/magnet settings used. This module is the only place that knows
the on-disk format.

Default location: %APPDATA%/moi_control/calibrations/ on Windows,
                  ~/.config/moi_control/calibrations/ otherwise.

The acquire CLI can pass `--apply-calibration` (use most recent in default
location) or `--calibration-file PATH` (explicit override).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


# ----------------------------------------------------------------------------
# Default storage location
# ----------------------------------------------------------------------------

def calibration_default_dir() -> Path:
    """Return the default directory for calibration JSON files."""
    # Windows: %APPDATA%/moi_control/calibrations
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "moi_control" / "calibrations"
    # Unix/Mac: ~/.config/moi_control/calibrations
    return Path.home() / ".config" / "moi_control" / "calibrations"


def ensure_calibration_dir(path: Optional[Path] = None) -> Path:
    """Create the calibration dir if needed; return its path."""
    p = path or calibration_default_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ----------------------------------------------------------------------------
# Locate calibration files
# ----------------------------------------------------------------------------

def list_calibrations(directory: Optional[Path] = None) -> List[Path]:
    """List all JSON calibration files in a directory, sorted by mtime descending."""
    d = directory or calibration_default_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def most_recent_calibration(directory: Optional[Path] = None) -> Optional[Path]:
    """Return the path of the most recently modified calibration file, or None."""
    files = list_calibrations(directory)
    return files[0] if files else None


def resolve_calibration_file(
    explicit_path: Optional[Path], use_default: bool = True
) -> Optional[Path]:
    """Resolve a calibration file path with the priority:
       1. explicit_path if given
       2. most recent in default dir (if use_default)
       3. None
    """
    if explicit_path is not None:
        if not explicit_path.exists():
            raise FileNotFoundError(f"calibration file not found: {explicit_path}")
        return explicit_path
    if use_default:
        return most_recent_calibration()
    return None


# ----------------------------------------------------------------------------
# Read / write
# ----------------------------------------------------------------------------

def write_calibration(
    out_path: Path,
    *,
    tag: str,
    temperature_K: float,
    coil_Oe_per_a: float,
    reference_intensity_ADU: float,
    camera_config: Any,           # CameraCfg (dataclass)
    magnet_config: Any,           # MagnetCfg (dataclass)
    best_fit: Dict[str, Any],     # {order, coeffs (length 10), aic, rss, rmse_Oe}
    all_fits: List[Dict[str, Any]],
    data_points: List[Dict[str, Any]],
) -> None:
    """Write a calibration to a JSON file in the canonical format."""
    payload = {
        "calibration_date": datetime.now().isoformat(),
        "tag": tag,
        "temperature_K": temperature_K,
        "coil_Oe_per_a": coil_Oe_per_a,
        "reference_intensity_ADU": reference_intensity_ADU,
        "camera_config": asdict(camera_config) if is_dataclass(camera_config) else camera_config,
        "magnet_config": asdict(magnet_config) if is_dataclass(magnet_config) else magnet_config,
        "fit": {
            "best_order": best_fit["order"],
            "best_coefficients": best_fit["coeffs"],
            "best_aic": best_fit["aic"],
            "best_rss": best_fit["rss"],
            "best_rmse_Oe": best_fit["rmse"],
            "all_orders": all_fits,
        },
        "data_points": data_points,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def read_calibration(path: Path) -> Dict[str, Any]:
    """Load a calibration JSON file. Returns the raw dict."""
    with open(path, "r") as f:
        return json.load(f)


def get_polynomial_coefficients(calibration: Dict[str, Any]) -> List[float]:
    """Extract the polynomial coefficients (lowest-order first) from a loaded calibration."""
    fit = calibration.get("fit", {})
    coeffs = fit.get("best_coefficients")
    if coeffs is None:
        raise ValueError("calibration file has no best_coefficients")
    return list(coeffs)


def get_reference_intensity(calibration: Dict[str, Any]) -> float:
    """Extract the I=0 reference intensity used in calibration."""
    ri = calibration.get("reference_intensity_ADU")
    if ri is None:
        raise ValueError("calibration file has no reference_intensity_ADU")
    return float(ri)


def get_camera_config(calibration: Dict[str, Any]) -> Dict[str, Any]:
    """Extract camera config (as dict) from a loaded calibration."""
    cc = calibration.get("camera_config")
    if cc is None:
        raise ValueError("calibration file has no camera_config")
    return dict(cc)


def calibration_is_compatible(
    calibration: Dict[str, Any], current_camera_config: Any,
    strict: bool = False,
) -> tuple[bool, List[str]]:
    """Check whether the loaded calibration matches the current camera config.

    Returns (compatible, list_of_warnings). If strict, any mismatch returns False.
    Otherwise only mismatches in critical settings (port, binning, ROI) fail.
    """
    cal_cam = get_camera_config(calibration)
    cur_cam = asdict(current_camera_config) if is_dataclass(current_camera_config) else dict(current_camera_config)

    warnings = []
    critical_keys = ["port_id", "binning", "roi", "exposure_ms"]
    non_critical_keys = ["navg"]

    compatible = True
    for key in critical_keys:
        if cal_cam.get(key) != cur_cam.get(key):
            warnings.append(
                f"[calibration warning] {key} mismatch: "
                f"calibration={cal_cam.get(key)} vs current={cur_cam.get(key)} "
                f"(critical — calibration may be inapplicable)"
            )
            compatible = False

    for key in non_critical_keys:
        if cal_cam.get(key) != cur_cam.get(key):
            warnings.append(
                f"[calibration note] {key} differs: "
                f"calibration={cal_cam.get(key)} vs current={cur_cam.get(key)}"
            )
            if strict:
                compatible = False

    return compatible, warnings
