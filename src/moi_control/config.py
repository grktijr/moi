"""Hardware and experiment configuration dataclasses.

These are pure data containers. They're constructed at script startup from
CLI args (in cli/) and passed down through the experiment functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class CryoCfg:
    """Montana Cryostation TCP connection + stabilization parameters."""
    host: str = "192.168.0.2"
    port: int = 7773
    socket_timeout_s: float = 5.0
    poll_s: float = 1.0
    # Stabilization gating
    tol_K: float = 0.05            # |T - target| must be below this
    stab_K: float = 0.1            # GSS stability metric must be below this
    dwell_s: float = 30.0          # both conditions must hold continuously for this long
    stabilize_timeout_s: float = 600.0  # soft fail (proceed anyway) after this
    # Sentinel: below this T, treat as "no setpoint; ride existing thermal state"
    unregulated_below_K: float = 2.5


@dataclass
class CameraCfg:
    """Photometrics Kinetix / PVCAM settings.

    The camera is opened fresh per acquisition (see acquisition.py), so these
    settings are reapplied each time. Default port 2 = Dynamic Range, 16-bit.
    """
    camera_name: Optional[str] = None       # None → autodetect first available
    port_id: int = 2                         # 0=Sens 1=Speed 2=DyRange 3=SubElectron
    exposure_ms: int = 100                   # integer (PyVCAM requires int)
    navg: int = 8
    binning: Tuple[int, int] = (1, 1)
    roi: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h), None = full sensor
    # Retry behavior for transient frame timeouts
    retry_max: int = 5                       # number of retries (total attempts = max + 1)
    retry_delay_s: float = 2.0


@dataclass
class MagnetCfg:
    """Magnet power supply (current source) configuration."""
    controller: str = "keithley_2400"        # driver key (resolved by open_magnet)
    visa_resource: str = ""                  # VISA address string
    voltage_limit_v: float = 10.0
    current_limit_a: float = 1.0
    ramp_step_a: float = 0.005
    ramp_step_delay_s: float = 0.02
    settle_s: float = 0.3                    # post-set wait for indicator to settle
    # PSU-specific extras (passed as **kwargs to the driver constructor)
    keysight_channel: Optional[int] = None
    keithley_current_range_a: Optional[float] = None


@dataclass
class RunCfg:
    """Per-run experiment configuration."""
    out_dir: Path = Path(".")
    tag: str = "run"
    coil_Oe_per_a: float = 333.3

    # Temperature setpoints (mode-specific use)
    t_high_K: Optional[float] = None
    t_low_K: Optional[float] = None

    # ZFC field sweep
    zfc_field_knots: Tuple[float, ...] = (0.0, 15.0)
    zfc_field_nums: Tuple[int, ...]   = (16,)

    # FC fields and cycles
    fc_fields_Oe: Tuple[float, ...] = ()
    fc_cycles: int = 1

    # FCZFC field and cycles:
    fczfc_field_Oe: Tuple[float, ...] = ()
    fczfc_sweep_knots: Tuple[float, ...] = (0.0, 200.0)
    fczfc_sweep_nums:  Tuple[int, ...]   = (16,)
    fczfc_cycles: int = 1

    # T-sweep
    tsweep_t_knots: Tuple[float, ...] = (4.0, 12.0)
    tsweep_t_nums:  Tuple[int, ...]   = (16,)
    tsweep_field_Oe: float = 0.0
    tsweep_prep_K:  Optional[float] = None

    # Post-processing flags
    do_divide: bool = True
    dry_run: bool = False
    skip_stabilize: bool = False

    # Auto-calibration
    apply_calibration: bool = False
    calibration_file: Optional[Path] = None
    scale_for_calibration: bool = False
