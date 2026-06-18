"""I-vs-H calibration via two-pass bipolar sweep with live-plot supervision.

Flow:
  1. Open a matplotlib figure for real-time monitoring
  2. Stabilize above Tc
  3. Sweep current from 0 to I_max_pos; image at each step, plot live
  4. Ramp to 0
  5. Prompt user to manually reverse polarity (terminal input)
  6. Sweep current from 0 to I_max_neg; image at each step, plot live
  7. Post-process: divide all by I=0 reference
  8. Fit polynomial (auto-order via AIC, max 9th order)
  9. Save calibration JSON

User can press 'q' (with the plot window focused) at any point to abort the
current pass. The acquired data is preserved and processed normally.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import tifffile
from PIL import Image

from pyvcam import pvc

from ..acquisition import (
    acquire_avg_sequence, field_to_current_a, ramp_current,
)
from ..config import CameraCfg, MagnetCfg, CryoCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import open_magnet
from ..postprocess import average_central_region
from ..calibration_db import (
    write_calibration, ensure_calibration_dir, calibration_default_dir,
)


# ----------------------------------------------------------------------------
# Polynomial fitting with AIC auto-order
# ----------------------------------------------------------------------------

def fit_polynomial_with_aic(
    I_ratio: np.ndarray, H_Oe: np.ndarray, max_order: int = 9,
) -> dict:
    """Fit polynomials of orders 1..max_order; return AIC-optimal fit + all fits.

    Convention: H_Oe = sum(coeffs[i] * I_ratio**i) for i in 0..order.
    Coefficients padded to length 10 for uniform serialization.
    """
    n = len(I_ratio)
    if n < 2:
        raise ValueError(f"need at least 2 calibration points for fitting; got {n}")
    effective_max = min(max_order, n - 1)
    fits = []
    for order in range(1, effective_max + 1):
        coeffs_high_to_low = np.polyfit(I_ratio, H_Oe, order)
        coeffs_low_to_high = list(coeffs_high_to_low[::-1])
        residuals = H_Oe - np.polyval(coeffs_high_to_low, I_ratio)
        rss = float(np.sum(residuals ** 2))
        k = order + 1
        aic = float(n * np.log(rss / n) + 2 * k) if rss > 0 else float("-inf")
        rmse = float(np.sqrt(rss / n))
        coeffs_padded = coeffs_low_to_high + [0.0] * (10 - len(coeffs_low_to_high))
        fits.append({
            "order": order,
            "coeffs": [float(c) for c in coeffs_padded],
            "aic": aic, "rss": rss, "rmse": rmse,
        })
    best = min(fits, key=lambda f: f["aic"])
    return {"best_fit": best, "all_fits": fits}


# ----------------------------------------------------------------------------
# Live-plot helpers
# ----------------------------------------------------------------------------

class _CalibrationMonitor:
    """Manages the live-updating matplotlib figure and keyboard abort handling.

    Usage:
        with _CalibrationMonitor() as monitor:
            for I in currents:
                ... acquire ...
                monitor.add_point(H, I_ratio)
                if monitor.abort_requested:
                    break
    """

    def __init__(self, title: str = "MOI Calibration"):
        # Ensure interactive backend
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(9, 6))
        self.line, = self.ax.plot([], [], 'o-', color='tab:blue', markersize=6)
        self.ax.axvline(0, color='gray', linestyle='--', linewidth=0.5)
        self.ax.axhline(1, color='gray', linestyle='--', linewidth=0.5)
        self.ax.set_xlabel('H (Oe)')
        self.ax.set_ylabel('I / I_0')
        self.ax.set_title(f"{title}\n(press 'q' in this window to abort current pass)")
        self.ax.grid(True, alpha=0.3)
        self.H_data = []
        self.I_ratio_data = []
        self.abort_requested = False
        self._connect_id = self.fig.canvas.mpl_connect('key_press_event', self._on_key)
        self.fig.show()
        self.fig.canvas.draw()
        plt.pause(0.05)   # let the window actually open

    def _on_key(self, event):
        if event.key in ('q', 'escape', 'Q'):
            if not self.abort_requested:
                self.abort_requested = True
                print("\n[calib GUI] abort requested — will stop after current point")

    def add_point(self, H_Oe: float, I_ratio: float) -> None:
        self.H_data.append(H_Oe)
        self.I_ratio_data.append(I_ratio)
        self.line.set_data(self.H_data, self.I_ratio_data)
        self.ax.relim()
        self.ax.autoscale_view()
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
        plt.pause(0.01)   # let GUI events process

    def annotate_polarity_switch(self) -> None:
        """Mark the polarity-switch point on the plot."""
        if self.H_data:
            self.ax.axvline(self.H_data[-1], color='red', linestyle=':',
                            linewidth=1, label='polarity switch')
            self.ax.legend(loc='best', fontsize=9)
            self.fig.canvas.draw()

    def show_fit(self, I_ratio_fit: np.ndarray, H_fit: np.ndarray,
                 coeffs: list, order: int, rmse: float) -> None:
        """Overlay the polynomial fit on the plot after calibration completes."""
        # Sort the kept range by I_ratio for a smooth fit line
        I_smooth = np.linspace(min(I_ratio_fit), max(I_ratio_fit), 200)
        # Evaluate polynomial: H = sum(c_i * I_ratio**i)
        H_smooth = sum(c * (I_smooth ** i) for i, c in enumerate(coeffs[:order + 1]))
        # Plot H vs I_ratio... but our axes are H vs I_ratio, so we need to
        # invert: we want to show the fit prediction overlaid on the data.
        # Plot the fit as a function of I_ratio, on the same axes.
        self.ax.plot(H_smooth, I_smooth, '-', color='tab:orange',
                     linewidth=2,
                     label=f'poly order {order}, RMSE={rmse:.2f} Oe')
        self.ax.legend(loc='best', fontsize=9)
        self.fig.canvas.draw()

    def close(self) -> None:
        if self._connect_id is not None:
            self.fig.canvas.mpl_disconnect(self._connect_id)
            self._connect_id = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Don't auto-close on exit; user inspects plot, closes when done
        return False


# ----------------------------------------------------------------------------
# The calibration sequence (interactive with live plot)
# ----------------------------------------------------------------------------

def run_calibration(
    *, cam_cfg: CameraCfg, mag_cfg: MagnetCfg, cryo_cfg: CryoCfg,
    t_K: float,
    I_max_pos_A: float, I_max_neg_A: float, n_steps: int,
    out_dir: Path, tag: str,
    coil_Oe_per_a: float,
    region_fraction: float = 0.5,
    max_poly_order: int = 9,
    calibration_save_dir: Optional[Path] = None,
) -> Path:
    """Run the two-pass calibration with live-plot supervision.

    The plot window updates after each acquired point. Press 'q' in the
    plot window to abort the current sweep. Acquired data is preserved
    and processed normally regardless of when you abort.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    original_dir = out_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    divided_dir = out_dir / "divided"
    divided_dir.mkdir(parents=True, exist_ok=True)

    cryo = Cryostation(cryo_cfg)
    cryo.open()
    print(f"[cryo] connected; state: {cryo.read_state()}")

    pvc.init_pvcam()
    ctrl, rm = open_magnet(mag_cfg)
    print(f"[magnet] {mag_cfg.controller} -> {ctrl.idn()}")

    pass1_records = []
    pass2_records = []

    monitor = _CalibrationMonitor(title=f"Calibration: {tag}")

    try:
        cryo.stabilize_at(t_K, label=f"calibration T={t_K}K")

        ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
        ctrl.set_current(0.0)
        ctrl.output_on()
        current_now = 0.0

        # ---- Pass 1: positive polarity --------------------------------------
        print(f"\n[calib] Pass 1: 0 → {I_max_pos_A:.4f} A in {n_steps} steps")
        print("       (press 'q' in the plot window to abort pass 1 early)")
        pass1_currents = np.linspace(0, I_max_pos_A, n_steps)
        aborted_in_pass1 = False
        for idx, I in enumerate(pass1_currents, start=1):
            print(f"[calib pass1 {idx}/{n_steps}] I = {I:+.5f} A")
            current_now = ramp_current(
                ctrl, current_now, float(I),
                mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
            )
            time.sleep(mag_cfg.settle_s)

            img = acquire_avg_sequence(cam_cfg, cam_cfg.exposure_ms, cam_cfg.navg)
            i_meas = ctrl.meas_current()
            v_meas = ctrl.meas_voltage()

            fname = f"pass1_{idx:03d}__Iset-{I:+.5f}A.tif"
            tifffile.imwrite(original_dir / fname, img, photometric="minisblack")

            pass1_records.append({
                "idx": idx, "pass": 1, "I_set_A": float(I),
                "I_meas_A": i_meas, "V_meas_V": v_meas,
                "filename": fname,
                "image": img,  # keep in memory for the live plot update
            })

            # Update live plot
            H_this = float(I) * coil_Oe_per_a
            # The reference for I_ratio is the I=0 image (first of pass 1).
            # We compute on-the-fly so the plot is meaningful from the second point.
            if idx == 1:
                ref_intensity_running = average_central_region(
                    img.astype(np.float32), fraction=region_fraction
                )
            this_intensity = average_central_region(
                img.astype(np.float32), fraction=region_fraction
            )
            I_ratio_this = this_intensity / max(ref_intensity_running, 1e-9)
            monitor.add_point(H_this, I_ratio_this)

            if monitor.abort_requested:
                print(f"[calib pass1] aborted by user after point {idx}")
                aborted_in_pass1 = True
                break

        current_now = ramp_current(
            ctrl, current_now, 0.0,
            mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
        )

        # ---- Polarity switch (only if not aborted) --------------------------
        if not aborted_in_pass1:
            print("\n" + "=" * 72)
            print("MANUAL POLARITY SWITCH REQUIRED")
            print("=" * 72)
            print("  1. Disable the magnet output (or confirm I = 0)")
            print("  2. Physically swap the coil leads to reverse polarity")
            print("  3. Re-enable the magnet output if you disabled it")
            print("=" * 72)
            input("Press Enter when ready to resume with reversed polarity: ")

            monitor.annotate_polarity_switch()
            # Reset the abort flag for pass 2 (user might want to abort that too)
            monitor.abort_requested = False

            # ---- Pass 2: negative polarity ----------------------------------
            print(f"\n[calib] Pass 2: 0 → {I_max_neg_A:.4f} A "
                  f"(physically negative polarity)")
            print("       (press 'q' in the plot window to abort pass 2 early)")
            pass2_currents = np.linspace(0, abs(I_max_neg_A), n_steps)
            for idx, I in enumerate(pass2_currents, start=1):
                print(f"[calib pass2 {idx}/{n_steps}] I = {I:+.5f} A "
                      f"(physically {-I:+.5f} A)")
                current_now = ramp_current(
                    ctrl, current_now, float(I),
                    mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s,
                )
                time.sleep(mag_cfg.settle_s)

                img = acquire_avg_sequence(cam_cfg, cam_cfg.exposure_ms, cam_cfg.navg)
                i_meas = ctrl.meas_current()
                v_meas = ctrl.meas_voltage()

                fname = f"pass2_{idx:03d}__Iset-{-I:+.5f}A.tif"
                tifffile.imwrite(original_dir / fname, img, photometric="minisblack")

                pass2_records.append({
                    "idx": idx, "pass": 2, "I_set_A": -float(I),
                    "I_meas_A": (None if i_meas is None else -i_meas),
                    "V_meas_V": v_meas,
                    "filename": fname,
                    "image": img,
                })

                # Update plot — H is now negative
                H_this = -float(I) * coil_Oe_per_a
                this_intensity = average_central_region(
                    img.astype(np.float32), fraction=region_fraction
                )
                I_ratio_this = this_intensity / max(ref_intensity_running, 1e-9)
                monitor.add_point(H_this, I_ratio_this)

                if monitor.abort_requested:
                    print(f"[calib pass2] aborted by user after point {idx}")
                    break

            ramp_current(ctrl, current_now, 0.0,
                         mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)

    finally:
        try: ctrl.set_current(0.0); ctrl.output_off(); ctrl.inst.close()
        except Exception: pass
        try: rm.close()
        except Exception: pass
        try: pvc.uninit_pvcam()
        except Exception: pass
        try: cryo.close()
        except Exception: pass

    # ---- Post-process ------------------------------------------------------
    print("\n[calib] post-processing: divide by I=0 reference, fit polynomial")
    if not pass1_records:
        raise RuntimeError("no points acquired — calibration has no data to process")

    ref_path = original_dir / pass1_records[0]["filename"]
    ref_img = np.array(Image.open(ref_path)).astype(np.float32)
    ref_intensity = average_central_region(ref_img, fraction=region_fraction)
    print(f"[calib] reference intensity (I=0): {ref_intensity:.1f} ADU")

    all_records = pass1_records + pass2_records
    I_set = np.array([r["I_set_A"] for r in all_records])
    I_ratio = np.zeros_like(I_set)

    for i, rec in enumerate(all_records):
        img = np.array(Image.open(original_dir / rec["filename"])).astype(np.float32)
        divided = img / np.maximum(ref_img, 1.0)
        tifffile.imwrite(divided_dir / f"divided_{rec['filename']}",
                         divided, photometric="minisblack")
        I_ratio[i] = average_central_region(img, fraction=region_fraction) / ref_intensity

    H_Oe = I_set * coil_Oe_per_a
    order = np.argsort(H_Oe)
    H_sorted = H_Oe[order]
    Iratio_sorted = I_ratio[order]

    fit_result = fit_polynomial_with_aic(Iratio_sorted, H_sorted, max_order=max_poly_order)
    best = fit_result["best_fit"]
    print(f"\n[calib] best fit: order {best['order']}, RMSE = {best['rmse']:.3f} Oe")
    print(f"[calib] coefficients m0..m{best['order']}: "
          f"{[f'{c:+.4e}' for c in best['coeffs'][:best['order']+1]]}")

    # Overlay fit on the live plot
    try:
        monitor.show_fit(Iratio_sorted, H_sorted, best["coeffs"],
                         best["order"], best["rmse"])
    except Exception as e:
        print(f"[calib GUI] could not overlay fit on plot: {e}")

    # ---- Save JSON ---------------------------------------------------------
    save_dir = calibration_save_dir or calibration_default_dir()
    ensure_calibration_dir(save_dir)
    json_path = save_dir / f"{tag}.json"

    # Strip the in-memory image arrays before serializing
    data_points = [
        {"I_set_A": float(r["I_set_A"]),
         "I_meas_A": r["I_meas_A"],
         "I_ratio": float(I_ratio[idx]),
         "H_Oe": float(H_Oe[idx])}
        for idx, r in enumerate(all_records)
    ]

    write_calibration(
        json_path,
        tag=tag, temperature_K=t_K, coil_Oe_per_a=coil_Oe_per_a,
        reference_intensity_ADU=ref_intensity,
        camera_config=cam_cfg, magnet_config=mag_cfg,
        best_fit=best, all_fits=fit_result["all_fits"],
        data_points=data_points,
    )
    print(f"[calib] saved: {json_path}")

    # Keep the figure open so user can inspect; print instructions
    print("\n[calib GUI] plot window will remain open. Close it when done.")
    try:
        plt.ioff()
        plt.show()   # blocks until user closes the window
    except Exception:
        pass

    # Remind user to switch back to normal polarity
    print("\n" + "=" * 72)
    print("CALIBRATION COMPLETE — POLARITY REMINDER")
    print("=" * 72)
    print("  The magnet leads are currently in REVERSED polarity from pass 2.")
    print("  Before running any measurement (zfc, fc, fczfc, tsweep):")
    print("    1. Disable the magnet output (or confirm I = 0)")
    print("    2. Swap the coil leads back to NORMAL polarity")
    print("  Failure to do this will invert the sign of all measurement fields.")
    print("=" * 72)
    input("Press Enter once you have restored normal polarity: ")

    return json_path