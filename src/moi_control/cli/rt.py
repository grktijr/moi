"""R-T transport measurement — standalone (cryostat + sample 6221 + 2182A).

Feeds a small DC current through the sample (2nd 6221, GPIB 29), ramps the
cryostation temperature by setpoint-stepping at a controlled rate, and reads
the sample voltage (2182A, GPIB 9) at each step. Resistance R = V / I, with
software CURRENT REVERSAL (delta mode) to cancel thermal EMF:
    +I -> V+,  -I -> V-,  V_delta = (V+ - V-)/2,  R = V_delta / I.

Up-sweep (and optional down-sweep with --both) are plotted live on one canvas
so reversibility can be judged as it runs; a PNG is saved at the end.

No camera, no field, no magnet PSU — independent of moi-acquire. Peer of
moi-snap / moi-cryo / moi-stack.

Entry point: moi-rt  (see pyproject.toml [project.scripts])
    moi-rt --t-start 6 --t-end 10 --i-sample 0.5 --ramp-rate 2 --t-step 0.25 --out D:\\data\\rt --tag s1
    moi-rt --t-start 6 --t-end 10 --both --ramp-rate 1 --t-step 0.1 --out D:\\data\\rt --tag s1
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt

from ..config import CryoCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import Keithley6221DC
from ..instruments.voltmeter import Keithley2182A
from .._paths import long_path

import pyvisa


RT_CSV_COLUMNS = [
    "idx", "direction", "T_set_K", "T_plat_K", "T_sample_K", "stab_K",
    "I_sample_A", "V_plus_V", "V_minus_V", "V_delta_V", "R_ohm", "t_elapsed_s",
]


# --------------------------------------------------------------------------
# Live plot
# --------------------------------------------------------------------------
class _RTMonitor:
    """Live R-T plot: up-sweep and down-sweep on one canvas. 'q' aborts."""

    def __init__(self, title: str = "R-T"):
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(9, 6))
        self.line_up, = self.ax.plot([], [], 'o-', color='tab:red',
                                     markersize=4, label='warming (up)')
        self.line_dn, = self.ax.plot([], [], 's-', color='tab:blue',
                                     markersize=4, label='cooling (down)')
        self.ax.set_xlabel('T (K)')
        self.ax.set_ylabel('R (Ohm)')
        self.ax.set_title(f"{title}\n(press 'q' in this window to abort)")
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='best', fontsize=9)
        self.T_up, self.R_up = [], []
        self.T_dn, self.R_dn = [], []
        self.abort_requested = False
        self._cid = self.fig.canvas.mpl_connect('key_press_event', self._on_key)
        self.fig.show(); self.fig.canvas.draw(); plt.pause(0.05)

    def _on_key(self, event):
        if event.key in ('q', 'escape', 'Q') and not self.abort_requested:
            self.abort_requested = True
            print("\n[R-T GUI] abort requested — will stop after current point")

    def add_point(self, T_K: float, R_ohm: Optional[float], direction: str):
        if R_ohm is None:
            return
        if direction == "up":
            self.T_up.append(T_K); self.R_up.append(R_ohm)
            self.line_up.set_data(self.T_up, self.R_up)
        else:
            self.T_dn.append(T_K); self.R_dn.append(R_ohm)
            self.line_dn.set_data(self.T_dn, self.R_dn)
        self.ax.relim(); self.ax.autoscale_view()
        self.fig.canvas.draw(); self.fig.canvas.flush_events(); plt.pause(0.01)

    def save_png(self, path: Path):
        self.fig.savefig(str(long_path(path)), dpi=150, bbox_inches='tight')
        print(f"[R-T] plot saved: {path}")

    def close(self):
        if self._cid is not None:
            self.fig.canvas.mpl_disconnect(self._cid); self._cid = None


# --------------------------------------------------------------------------
# Measurement core
# --------------------------------------------------------------------------
def _delta_read(sample, voltmeter, I_a, settle_s):
    """One current-reversal reading: +I -> V+, -I -> V-.
    Returns (V+, V-, V_delta, R); V_delta cancels thermal EMF, R = V_delta/I."""
    sample.set_current(+I_a); time.sleep(settle_s)
    v_plus = voltmeter.read_voltage()
    sample.set_current(-I_a); time.sleep(settle_s)
    v_minus = voltmeter.read_voltage()
    if v_plus is None or v_minus is None:
        return v_plus, v_minus, None, None
    v_delta = (v_plus - v_minus) / 2.0
    R = v_delta / I_a if I_a != 0 else None
    return v_plus, v_minus, v_delta, R


def _ramp_leg(cryo, sample, voltmeter, writer, csv_f, monitor, args,
              direction, T_from, T_to, I_a, idx0, t0):
    """One temperature leg by SETPOINT-STEPPING at a controlled rate (the
    ASCII-protocol Montana has no native ramp). Walks the setpoint in t_step
    increments; dwell per step = step/rate so the mean rate = ramp_rate."""
    rate = max(float(args.ramp_rate), 1e-6)   # K/min
    step = abs(float(args.t_step))
    dwell_s = (step / rate) * 60.0
    going_up = T_to >= T_from
    signed = step if going_up else -step
    # Step through the interior points (all but the final target); the endpoint
    # T_to is measured after a true stabilization below.
    nsteps = int(round(abs(T_to - T_from) / step))      # excludes the endpoint
    print(f"\n[R-T {direction}] {T_from:.3f} -> {T_to:.3f} K  step {step:.3f} K, "
          f"dwell {dwell_s:.1f} s (rate {rate:.3f} K/min), "
          f"{nsteps} ramp points + 1 stabilized endpoint")
    idx = idx0

    def _record(st, label_extra=""):
        nonlocal idx
        T = st["T_plat_K"]
        vp, vm, vd, R = _delta_read(sample, voltmeter, I_a, args.reversal_settle)
        now = time.monotonic()
        writer.writerow([
            idx, direction,
            f"{st['T_set_K']:+.4f}", f"{st['T_plat_K']:+.4f}",
            f"{st['T_sample_K']:+.4f}", f"{st['stab_K']:+.5f}",
            f"{I_a:+.6e}",
            ("" if vp is None else f"{vp:+.9e}"),
            ("" if vm is None else f"{vm:+.9e}"),
            ("" if vd is None else f"{vd:+.9e}"),
            ("" if R is None else f"{R:+.6e}"),
            f"{now - t0:.2f}",
        ])
        csv_f.flush()
        monitor.add_point(T, R, direction)
        print(f"  [{direction} {idx}]{label_extra} T={T:.3f}K  "
              f"R={'--' if R is None else f'{R:.5g} Ohm'}", flush=True)
        idx += 1

    # Interior ramp points (on the fly, no per-point stabilization).
    T_sp = T_from
    for _ in range(nsteps):
        cryo.set_setpoint(T_sp)
        time.sleep(dwell_s)
        st = cryo.read_state()
        print(f"  (ramp T_sp={T_sp:.3f}K)", end="")
        _record(st)
        if monitor.abort_requested:
            print(f"[R-T {direction}] aborted at T={st['T_plat_K']:.3f}K")
            return idx
        T_sp += signed

    # Endpoint: STABILIZE at T_to (tol/stab/dwell from CryoCfg, not CLI), then
    # take one settled reading — so each leg truly ends at an equilibrated T.
    st = cryo.stabilize_at(T_to, label=f"R-T {direction} endpoint")
    _record(st, label_extra=" [stabilized]")
    return idx


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="moi-rt",
        description="R-T transport measurement (cryo + sample 6221 DC + 2182A). "
                    "No camera/field; independent of moi-acquire.",
    )
    p.add_argument("--t-start", type=float, required=True, help="sweep start T (K)")
    p.add_argument("--t-end", type=float, required=True, help="sweep end T (K)")
    p.add_argument("--i-sample", type=float, default=0.1,
                   help="DC sample current in mA (default 0.1)")
    p.add_argument("--t-step", type=float, default=0.1,
                   help="setpoint step size (K) for the controlled ramp (default 0.1)")
    p.add_argument("--ramp-rate", type=float, default=1.0,
                   help="ramp rate (K/min) via setpoint-stepping (default 1)")
    p.add_argument("--both", action="store_true",
                   help="two passes (up then down), overplotted (default single pass)")
    p.add_argument("--reversal-settle", type=float, default=0.3,
                   help="settle after each +/-I reversal before reading V (s)")
    p.add_argument("--compliance", type=float, default=10.0,
                   help="sample-source voltage compliance (V)")
    p.add_argument("--sample-visa", default="GPIB0::29::INSTR")
    p.add_argument("--sample-range-a", type=float, default=None,
                   help="sample 6221 current range in A (None = auto)")
    p.add_argument("--voltmeter-visa", default="GPIB0::9::INSTR")
    p.add_argument("--voltmeter-nplc", type=float, default=5.0,
                   help="2182A integration time in power-line cycles (default 5)")
    p.add_argument("--probe-mode", default="2-probe",
                   choices=["2-probe", "4-probe"],
                   help="wiring metadata (2182A is identical either way)")
    p.add_argument("--out", type=str, default=".", help="output directory")
    p.add_argument("--tag", type=str, default="rt", help="filename tag")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and exit; no hardware")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    I_a = float(args.i_sample) * 1e-3
    T_lo, T_hi = float(args.t_start), float(args.t_end)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{args.tag}_rt.csv"
    png_path = out_dir / f"{args.tag}_rt.png"

    step = abs(float(args.t_step))
    npts = int(round(abs(T_hi - T_lo) / step)) + 1
    legs = 2 if args.both else 1
    print(f"[R-T] I={args.i_sample} mA, {T_lo}->{T_hi} K"
          + (" ->back" if args.both else "")
          + f", step {step} K, rate {args.ramp_rate} K/min, "
          f"{npts} pts/leg x {legs} leg(s), reversal delta mode")

    if args.dry_run:
        print(f"[dry-run] CSV {csv_path}, PNG {png_path}")
        return 0

    # ---- Open instruments (cryo + DC sample source + 2182A) ----
    cryo = Cryostation(CryoCfg()); cryo.open()
    print(f"[cryo] connected; state: {cryo.read_state()}")

    rm = pyvisa.ResourceManager()
    sample_inst = rm.open_resource(args.sample_visa)
    sample = Keithley6221DC(sample_inst, current_range_a=args.sample_range_a)
    print(f"[sample-src DC] {args.sample_visa} -> {sample.idn()}")

    vm_inst = rm.open_resource(args.voltmeter_visa)
    voltmeter = Keithley2182A(vm_inst, probe_mode=args.probe_mode,
                              nplc=args.voltmeter_nplc)
    print(f"[voltmeter] {args.voltmeter_visa} -> {voltmeter.idn()} "
          f"({voltmeter.probe_mode})")

    sample.set_voltage_limit(args.compliance)
    sample.set_current(0.0)
    sample.output_on()

    t0 = time.monotonic()
    monitor = _RTMonitor(title=f"R-T  ({args.tag})")
    try:
        with open(long_path(csv_path), "w", newline="") as csv_f:
            writer = csv.writer(csv_f)
            writer.writerow(RT_CSV_COLUMNS)

            print(f"[R-T] cooling to start T={T_lo:.3f} K ...")
            cryo.stabilize_at(T_lo, label="R-T start")

            idx = 0
            idx = _ramp_leg(cryo, sample, voltmeter, writer, csv_f, monitor,
                            args, "up", T_lo, T_hi, I_a, idx, t0)
            if args.both and not monitor.abort_requested:
                idx = _ramp_leg(cryo, sample, voltmeter, writer, csv_f, monitor,
                                args, "down", T_hi, T_lo, I_a, idx, t0)

            monitor.save_png(png_path)
    finally:
        try: sample.set_current(0.0); sample.output_off(); sample.inst.close()
        except Exception: pass
        try: voltmeter.close()
        except Exception: pass
        try: rm.close()
        except Exception: pass
        try: cryo.close()
        except Exception: pass

    print(f"\n[R-T done] data: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
