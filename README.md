# moi-control

Automated **magneto-optical imaging (MOI)** and **electrical transport** for
superconducting thin films. The package drives the cryostat, field magnet,
sample current source, nanovoltmeter, and camera from a set of command-line
tools, writes per-image/per-point CSV metadata, and post-processes the images
(reference division + flat-field calibration).

Version `0.1.0`.

---

## Hardware

| Role | Instrument | Default address |
|------|------------|-----------------|
| Cryostat | Montana Cryostation (older ASCII protocol) | TCP `192.168.0.2:7773` |
| Field magnet PSU | Keithley 6221 (DC or AC waveform) | GPIB `30` |
| Sample current source | 2nd Keithley 6221 | GPIB `29` |
| Nanovoltmeter | Keithley 2182A | GPIB `9` |
| Camera | Photometrics Kinetix (PVCAM) | — |

Other field PSUs (Keysight E36234A, HP 6542A, Keithley 2400) are also
supported; see **PSU registry** below.

---

## Install

```bash
conda activate moi
pip install -e .
```

Requires PVCAM + `pyvcam`, a VISA backend (NI-VISA) for `pyvisa`, plus
`numpy`, `tifffile`, `Pillow`, and `matplotlib`. The package is src-layout
(`src/moi_control/`); the commands below are the console entry points.

---

## PSU registry

Field PSUs are referenced by short names via the `--psu` flag. The mapping
(name → driver, VISA address, options) lives in
[`psu_registry.py`](src/moi_control/psu_registry.py) — **edit that file when
hardware or cabling changes**:

| `--psu` | Instrument | Address |
|---------|-----------|---------|
| `k6221_dc` | Keithley 6221, DC current source | GPIB 30 |
| `k6221_ac` | Keithley 6221, AC waveform source | GPIB 30 |
| `k2400` | Keithley 2400 | GPIB 24 |
| `keysight` | Keysight E36234A | USB |
| `hp` | HP 6542A | GPIB 5 |

The **sample** 6221 (GPIB 29) is not in the registry; it is addressed
directly with `--sample-visa` and opened in AC or DC mode depending on the
experiment.

---

## Imaging: `moi-acquire`

One command with subcommands; each is an acquisition *mode*. Common flags
(`--psu`, `--out`, `--tag`, `--t-high`, `--t-low`, `--coil-oe-per-a`,
`--exposure-ms`, `--navg`, `--binning`, `--roi`, `--dry-run`,
`--skip-stabilize`, calibration options) apply to every mode.

| Mode | What it does |
|------|--------------|
| `zfc` | Zero-field-cooled: cool in zero field, step the field, image each step. |
| `fc` | Field-cooled: apply field above Tc, cool through Tc in field, image. |
| `fczfc` | Combined FC + ZFC cycle. |
| `fc_acsweep` | FC-cool in a near-DC field, then sweep one **AC field** axis (amplitude or frequency) at T_low. |
| `fc_sample_acsweep` | Real DC field (magnet) **plus AC sample current** (2nd 6221, mA), swept by amplitude or frequency; image under the pure DC field after each anchor. |
| `fc_sample_dcpulse` | Real DC field **plus a list of DC sample-current pulses** (mA); image under the pure DC field after each pulse. Images the pulse's remanent after-effect. |
| `calibrate` | Flat-field / calibration capture (also `moi-calibrate`). |

Example — field-cooled with a DC sample-current pulse train:

```bash
moi-acquire fc_sample_dcpulse --psu k6221_dc --fc-field 0.5 \
  --t-high 9 --t-low 7 --coil-oe-per-a 333.3 \
  --sample-values 1 2 5 10 20 --sample-dwell 3 \
  --sample-visa GPIB0::29::INSTR --sample-range-a 0.02 \
  --out D:\data\run --tag dcpulse
```

In the sample-current modes the swept value is recorded in mA (there is no
coil on the sample source — mA is mA, not Oe).

---

## Transport & utilities (standalone CLIs)

Each opens only the instruments it needs, independent of the imaging stack.

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `moi-iv` | I-V at fixed T: two zero-origin branches (`0→+Imax`, `0→−Imax`) from the virgin SC state. Optional field-cool and pulsed current; auto-rescan refines the step around the Ic jump. | `--temp`, `--i-max`, `--i-step`/`--points`, `--pulsed`, `--auto-rescan`, `--v-jump`, optional `--psu`/`--field`/`--t-high` |
| `moi-rt` | R-T: small DC sample current, step the cryostat at a set rate, read V at each T (current-reversal delta mode cancels thermal EMF). Live plot. | `--ramp-rate`, `--both`, `--sample-visa`, `--voltmeter-nplc` |
| `moi-cryo` | Stabilize the cryostat to a target temperature (no camera/PSU). | `--temp` |
| `moi-snap` | Single averaged camera snapshot (no cryo/PSU). | `--out`, `--tag` |
| `moi-stack` | Filter finished FC runs by CSV metadata and build an ImageJ stack. | filter/`--fixed` args |

Example — field-cooled I-V with auto-rescan around Ic:

```bash
moi-iv --temp 7.6 --i-max 100 --i-step 1 --auto-rescan --v-jump 2e-6 \
  --psu k6221_dc --field 0.5 --t-high 9 --coil-oe-per-a 333.3 \
  --out D:\data\iv --tag bridge4
```

PowerShell orchestrators (e.g. a field scan looping `moi-iv` over a field
list) live alongside the package in `scripts/`.

---

## Output & post-processing

Each run writes to `--out`:

- `original/` — raw averaged TIFFs, one per image.
- `<tag>_<mode>_metadata.csv` — one row per image: index, phase, cycle, set
  field/current, measured temperatures, stability, and (transport) sample
  V/R.
- `divided/` — reference-divided images (the high-T reference divides the
  FC/FCZFC series), produced automatically unless `--no-divide`.
- `calibrated/` — flat-field-applied images when `--apply-calibration` is set.

Transport CLIs (`moi-iv`, `moi-rt`) write their own CSV (per current/temperature
point) plus a live PNG plot.

---

## Configuration

Runtime parameters are dataclasses in
[`config.py`](src/moi_control/config.py), built fresh per invocation and
overridden by CLI flags:

- `CryoCfg` — cryostat host/port and stabilization thresholds
  (`tol_K=0.05`, `stab_K=0.1`, `dwell_s=30`).
- `MagnetCfg` — field PSU limits and ramp (`voltage_limit_v`,
  `current_limit_a`, ramp step/delay), `coil_Oe_per_a=333.3`.
- `CameraCfg` — Kinetix readout port, exposure, averaging, ROI, binning.
- `RunCfg` — per-run mode parameters, including the sample-source fields
  (`sample_values`, `sample_dwell_s`, `sample_axis`, `sample_amp_mA`,
  `sample_freq_hz`, …).

---

## Project layout

```
src/moi_control/
  cli/            moi-acquire, moi-iv, moi-rt, moi-cryo, moi-snap,
                  moi-stack, moi-calibrate
  experiments/    zfc, fc, fczfc, fc_acsweep, fc_sample_acsweep,
                  fc_sample_dcpulse, tsweep, calibrate
  instruments/    cryostat, magnet (6221 DC/AC + sample source),
                  voltmeter (2182A), camera (PVCAM)
  acquisition.py  shared image acquisition + CSV schema
  postprocess.py  reference division, flat-field calibration
  config.py       dataclass configuration
  psu_registry.py field-PSU name → driver/address map
  _paths.py       Windows long-path (MAX_PATH) helper
```

---

## Notes

- **Mains frequency:** NPLC-based integration assumes 60 Hz; adjust
  `--voltmeter-nplc` for your line.
- **Windows paths:** long output paths are handled via `_paths.long_path()`
  (UNC `\\?\` prefix); no action needed.
- **Instrument cleanup:** the field magnet is always disarmed (ramped to 0,
  output off) before the shared VISA ResourceManager is closed, so an aborted
  or failed run does not leave the magnet energized. A hard process kill
  (not an exception) still bypasses cleanup — the next run's `*RST` clears it.
```
