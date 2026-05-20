[readme.md](https://github.com/user-attachments/files/28073210/readme.md)
# moi_acquire

Automated acquisition script for magneto-optical imaging (MOI) experiments.
Integrates thermal control (Montana Instruments Cryostation), magnetic field
control (configurable PSU), and image acquisition (Photometrics Kinetix sCMOS
camera) into a single command-line tool.

Supports zero-field-cooling (ZFC) and field-cooling (FC) measurement modes
with automated temperature stabilization, multi-cycle averaging, and
metadata logging.


## Hardware setup

- **Cryostat**: Montana Instruments Cryostation Gen 1/2, controlled via TCP
  (default `192.168.0.2:7773`) over a direct private Ethernet link to the
  controller laptop running Montana's Cryostation software.
- **Camera**: Photometrics Kinetix sCMOS via USB 3.2 / PVCAM. Default config
  is port 2 (Dynamic Range, 16-bit), speed 0, gain 1.
- **Magnet PSU**: one of:
  - Keysight E36234A (USB VISA, dual-channel)
  - HP 6542A (GPIB)
  - Keithley 2400 SourceMeter (GPIB), in current-source mode
- **Coil calibration**: defaults to 333.3 Oe/A (override via `--coil-oe-per-a`)


## Installation

This script targets Python 3.11+. Tested on Windows 11 with a
miniconda environment.

```powershell
conda create -n moi python=3.13
conda activate moi
pip install pyvisa pyvcam tifffile numpy pillow pandas
```

Additional system dependencies:
- PVCAM SDK (from Photometrics) for camera access
- NI-VISA or Keysight IO Libraries for instrument communication
- A live network or direct cable connection to the Cryostation controller

The script must run on the same machine as the magnet PSU and camera USB
connections. The Cryostation controller is reached over TCP, so it can be
a separate machine on the same network or a private cable link.


## Quick start

Pick the PSU, set warm and cold temperatures, give an output directory:

```powershell
# Zero-field cooling: cool to T_low at zero field, then sweep field
python moi_acquire.py zfc --psu keithley `
    --t-high 12 --t-low 4 `
    --field-knots 0 15 --field-nums 16 `
    --out "D:\data\20260520\sample_A\zfc1" --tag zfc1

# Field cooling: at each B, image at T_high, cool to T_low with field on, image again
python moi_acquire.py fc --psu keithley `
    --t-high 12 --t-low 4 `
    --fc-fields 5 10 15 --fc-cycles 3 `
    --out "D:\data\20260520\sample_A\fc1" --tag fc1
```

Always do a dry-run first to verify the plan without touching hardware:

```powershell
python moi_acquire.py fc --psu keithley --t-high 12 --t-low 4 `
    --fc-fields 5 10 15 --fc-cycles 3 --out D:\tmp --tag test --dry-run
```


## Modes

### `zfc` — Zero-field cooling

1. Stabilize at `--t-high` (above Tc) with zero field
2. Stabilize at `--t-low` (measurement T) with zero field
3. Sweep field through points defined by `--field-knots` and `--field-nums`
4. Acquire one averaged image per field point

### `fc` — Field cooling

For each field in `--fc-fields`, repeated `--fc-cycles` times:

1. Ramp field to 0, warm to `--t-high`, stabilize
2. Apply target field, acquire one image (`fc_high` phase)
3. Cool to `--t-low` with field on, stabilize
4. Acquire one image (`fc_low` phase)


## CLI flags

### Shared between modes (`zfc` and `fc`)

| Flag | Default | Description |
|---|---|---|
| `--psu {keysight,hp,keithley}` | required | Which PSU to use; resolves driver, VISA address, and per-PSU defaults from `PSU_REGISTRY` |
| `--out PATH` | required | Output directory; created if missing |
| `--tag STR` | `run` | Filename and CSV tag |
| `--t-high K` | required | Warm setpoint (above Tc), Kelvin |
| `--t-low K` | required | Cold setpoint (measurement T), Kelvin |
| `--coil-oe-per-a F` | 333.3 | Coil calibration (Oe per amp) |
| `--exposure-ms N` | 100 | Per-frame exposure (integer ms) |
| `--navg N` | 8 | Frames averaged per image |
| `--no-divide` | off | Skip the post-processing divide step |
| `--dry-run` | off | Print planned actions without touching hardware |

### `zfc`-specific

| Flag | Default | Description |
|---|---|---|
| `--field-knots F [F...]` | `0 15` | Knot field values (Oe); e.g. `0 5 15` for two segments |
| `--field-nums N [N...]` | `16` | Points per segment; length must equal `len(knots) - 1` |

### `fc`-specific

| Flag | Default | Description |
|---|---|---|
| `--fc-fields F [F...]` | required | Field values (Oe) to cycle through |
| `--fc-cycles N` | 1 | Cycles per field for cycle-to-cycle statistics |


## Output structure

Each run produces a directory tree like:

<out_dir>/
├── original/
│   ├── 001__zfc__cycle-01__T-3.500K__Bset-+0.00Oe__exp-100ms__...tif
│   ├── 002__zfc__cycle-01__T-3.500K__Bset-+1.00Oe__exp-100ms__...tif
│   └── ...
├── divided/
│   ├── divid 001__...tif      # for ZFC: divided by image 001
│   ├── fc_div__B+5.00Oe__cyc01.tif    # for FC: low / high at same (B, cycle)
│   └── ...
└── <tag>_<mode>_metadata.csv

Filenames encode acquisition metadata (index, phase, cycle, temperature,
field set, field measured, exposure, binning, current, voltage, timestamp).

The CSV contains one row per image with columns:
`idx, phase, cycle, T_set_K, T_plat_K, T_sample_K, stab_K, B_meas_Oe,
B_set_Oe, Iset_A, Imeas_A, Vmeas_V, exp_ms, bin_x, bin_y, navg, src, idn,
filename`


## Configuring hardware

Most hardware-specific settings live in dataclasses near the top of the
script. The PSU registry resolves `--psu` to a driver class and VISA
resource:

```python
PSU_REGISTRY = {
    "keysight": {
        "driver": "keysight_e36234a",
        "visa":   "USB0::0x2A8D::0x3402::MY59001913::INSTR",
        "extras": {"keysight_channel": 2},
    },
    "hp": {
        "driver": "hp_6542a",
        "visa":   "GPIB0::5::INSTR",
        "extras": {},
    },
    "keithley": {
        "driver": "keithley_2400",
        "visa":   "GPIB0::24::INSTR",
        "extras": {"keithley_current_range_a": 0.1},
    },
}
```

To find the VISA address for a new instrument:

```python
import pyvisa
print(pyvisa.ResourceManager().list_resources())
```

Other configurable defaults (in `CryoCfg`, `CameraCfg`, `MagnetCfg`):

- Cryostation host/port, stabilization tolerances and timeout
- Camera port, binning, ROI
- Magnet voltage limit, ramp step size, settle time


## Stabilization behavior

`stabilize_at(target_K)` polls the cryostat until the platform temperature
is within tolerance of the setpoint AND the stability metric is below a
threshold, sustained for a dwell period:

- `tol_K = 0.05` — |T − target| must be below this
- `stab_K = 0.1` — stability metric must be below this
- `dwell_s = 30` — both must hold continuously for this long
- `stabilize_timeout_s = 600` — soft fail (proceed anyway) after this

A soft-fail timeout means the experiment proceeds with whatever T was
achieved; the actual platform temperature is recorded in the CSV and
filename. Post-hoc, you can filter on `stab_K` to identify points where
stabilization didn't fully converge.


## Camera reliability

PyVCAM has known issues with long-running scripts that perform many
sequential `get_sequence` calls. To mitigate:

1. The camera is opened fresh for each acquisition rather than reused
2. A per-frame timeout (`timeout_ms = 5000 + exp_ms * 5`) converts hangs
   into catchable exceptions
3. Up to 2 retries are attempted on `Frame timeout` errors specifically;
   other exceptions propagate immediately and trigger cleanup

This adds ~200-500 ms overhead per acquisition but allows unattended
overnight runs to complete reliably. Other camera failures (cable
unplug, hardware fault) fail fast rather than retrying.


## Safety and cleanup

The script's outermost `finally` block always runs on exit, regardless of
how the script terminates (success, exception, KeyboardInterrupt):

1. Magnet current ramped to 0 and output disabled
2. VISA resource manager closed
3. Camera handle closed and PVCAM uninitialized
4. Cryostation TCP connection closed

The Cryostation **continues regulating at the last setpoint sent** after
the script exits. The controller is autonomous and does not require the
script to maintain temperature. To return the system to room temperature,
use the Cryostation GUI on the controller laptop or send `STSP295` via
your own TCP client.

If the script must be force-killed (Task Manager, closing PowerShell
window), the `finally` block does **not** run. In that case, manually:

1. Zero the magnet from the PSU's front panel
2. Verify cryostat state via the Cryostation GUI


## Adding a new PSU

1. Implement a subclass of `MagnetControllerBase` with `set_voltage_limit`,
   `set_current`, `output_on`, `output_off`, `meas_current`, `meas_voltage`.
2. Add a dispatch branch to `open_magnet()`.
3. Add a `PSU_REGISTRY` entry mapping a short name to the driver, VISA
   address, and per-PSU keyword arguments.

The CLI picks up the new key automatically.


## Known limitations

- Targets Windows; not tested on Linux or macOS
- PVCAM SDK and NI-VISA / Keysight IO Libraries are external installs
- Cryostation Gen 1/2 protocol only; Gen 3 (Galaxy software) uses REST and
  would require a new client class
- Camera SCPI / VISA timeouts are tuned for typical exposures (~50–500 ms);
  longer exposures may need formula adjustment in `acquire_avg_sequence`


## Citation

If this code is used in published work, please reach out — Ren, tren.kasa1996@gmail.com
