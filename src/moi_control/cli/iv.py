"""I-V transport measurement — standalone (cryostat + sample 6221 + 2182A).

Fixes the cryostation temperature, sweeps the sample DC current bipolar
symmetric (-I_max -> 0 -> +I_max) via the 2nd 6221 (GPIB 29), and reads the
sample voltage (2182A, GPIB 9) at each current point. Plots V vs I live so the
I-V curve (and any nonlinearity / critical current) is visible as it runs; a
PNG is saved at the end.

No current reversal here (unlike R-T): the sweep spans both polarities, so the
raw V at each actual I is the datum. R = V/I is recorded per point for
convenience (undefined at I=0, left blank).

Optionally FIELD-COOLED: if --psu (the magnet, GPIB 30) and --field are
given, a field is applied above Tc (--t-high) and the sample is cooled through
Tc with the field on, then the I-V is swept at --temp with the field held on.
The field ramps to 0 at the end. One field per invocation (loop fields in the
shell).

No camera — independent of moi-acquire. Peer of moi-snap / moi-cryo /
moi-stack / moi-rt.

Entry point: moi-iv  (see pyproject.toml [project.scripts])
    moi-iv --temp 6.0 --i-max 10 --i-step 0.25 --out D:\\data\\iv --tag s1_6K
    moi-iv --temp 6.0 --i-max 10 --field -50 --psu k6221_dc --t-high 9 \\
        --coil-oe-per-a 10 --out D:\\data\\iv --tag s1_6K_fc
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import List, Optional

import matplotlib
import matplotlib.pyplot as plt

from ..config import CryoCfg, MagnetCfg
from ..instruments.cryostat import Cryostation
from ..instruments.magnet import Keithley6221DC, open_magnet
from ..instruments.voltmeter import Keithley2182A
from ..psu_registry import PSU_REGISTRY
from .._paths import long_path

import pyvisa


IV_CSV_COLUMNS = [
    "idx", "branch", "pass", "B_set_Oe", "I_field_A",
    "T_set_K", "T_plat_K", "T_sample_K", "stab_K",
    "I_sample_A", "V_V", "R_ohm", "t_elapsed_s",
]


# Field helpers (inlined to keep this standalone tool free of the camera-coupled
# acquisition module, which imports pyvcam/tifffile at top).
def field_to_current_a(field_Oe: float, coil_Oe_per_a: float) -> float:
    return field_Oe / coil_Oe_per_a


def ramp_current(ctrl, current_from: float, current_to: float,
                 step_a: float, step_delay_s: float) -> float:
    """Ramp current from -> to in steps of step_a. Returns the final current."""
    if current_from == current_to:
        return current_to
    direction = 1 if current_to > current_from else -1
    cur = current_from
    while abs(cur - current_to) > step_a / 2:
        cur = cur + direction * step_a
        if (direction > 0 and cur > current_to) or (direction < 0 and cur < current_to):
            cur = current_to
        ctrl.set_current(cur)
        time.sleep(step_delay_s)
    return current_to


# --------------------------------------------------------------------------
# Live plot
# --------------------------------------------------------------------------
class _IVMonitor:
    """Live V-vs-I plot: two zero-origin branches (0->+Imax, 0->-Imax) on one
    canvas, each starting from the virgin superconducting state. 'q' aborts."""

    def __init__(self, title: str = "I-V"):
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(9, 6))
        self.line_pos, = self.ax.plot([], [], 'o-', color='tab:red',
                                      markersize=4, label='0 -> +Imax')
        self.line_neg, = self.ax.plot([], [], 's-', color='tab:blue',
                                      markersize=4, label='0 -> -Imax')
        self.ax.axhline(0.0, color='0.8', lw=0.8, zorder=0)
        self.ax.axvline(0.0, color='0.8', lw=0.8, zorder=0)
        self.ax.set_xlabel('I (mA)')
        self.ax.set_ylabel('V (V)')
        self.ax.set_title(f"{title}\n(press 'q' in this window to abort)")
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='best', fontsize=9)
        self.I_pos: List[float] = []; self.V_pos: List[float] = []
        self.I_neg: List[float] = []; self.V_neg: List[float] = []
        self.abort_requested = False
        self._cid = self.fig.canvas.mpl_connect('key_press_event', self._on_key)
        self.fig.show(); self.fig.canvas.draw(); plt.pause(0.05)

    def _on_key(self, event):
        if event.key in ('q', 'escape', 'Q') and not self.abort_requested:
            self.abort_requested = True
            print("\n[I-V GUI] abort requested - will stop after current point")

    def add_point(self, I_mA: float, V: Optional[float], branch: str):
        if V is None:
            return
        if branch == "pos":
            self.I_pos.append(I_mA); self.V_pos.append(V)
            self.line_pos.set_data(self.I_pos, self.V_pos)
        else:
            self.I_neg.append(I_mA); self.V_neg.append(V)
            self.line_neg.set_data(self.I_neg, self.V_neg)
        self.ax.relim(); self.ax.autoscale_view()
        self.fig.canvas.draw(); self.fig.canvas.flush_events(); plt.pause(0.01)

    def drop_window(self, branch: str, lo_mA: float, hi_mA: float):
        """Remove already-plotted points of *branch* whose |I| falls in
        [lo_mA, hi_mA] (used when a fine rescan replaces coarse points)."""
        if branch == "pos":
            keep = [(i, v) for i, v in zip(self.I_pos, self.V_pos)
                    if not (lo_mA <= abs(i) <= hi_mA)]
            self.I_pos = [i for i, _ in keep]; self.V_pos = [v for _, v in keep]
            self.line_pos.set_data(self.I_pos, self.V_pos)
        else:
            keep = [(i, v) for i, v in zip(self.I_neg, self.V_neg)
                    if not (lo_mA <= abs(i) <= hi_mA)]
            self.I_neg = [i for i, _ in keep]; self.V_neg = [v for _, v in keep]
            self.line_neg.set_data(self.I_neg, self.V_neg)
        self.ax.relim(); self.ax.autoscale_view()
        self.fig.canvas.draw(); self.fig.canvas.flush_events(); plt.pause(0.01)

    def save_png(self, path: Path):
        self.fig.savefig(str(long_path(path)), dpi=150, bbox_inches='tight')
        print(f"[I-V] plot saved: {path}")

    def close(self):
        if self._cid is not None:
            self.fig.canvas.mpl_disconnect(self._cid); self._cid = None


# --------------------------------------------------------------------------
# Current list: one zero-origin branch 0 -> +I_max (magnitudes), stepped.
# The experiment runs this for +branch and its negation for -branch.
# --------------------------------------------------------------------------
def _branch_currents(i_max_mA: float, i_step_mA: Optional[float],
                     points: Optional[int]) -> List[float]:
    """Magnitudes 0, step, 2*step, ..., I_max (mA). Each branch sweeps OUTWARD
    from 0 (the virgin superconducting state) so Ic is reached without a prior
    opposite-polarity excursion."""
    imax = abs(float(i_max_mA))
    if points is not None and points >= 2:
        import numpy as np
        return list(np.linspace(0.0, imax, int(points)))
    step = abs(float(i_step_mA)) if i_step_mA else imax / 20.0
    nstep = int(round(imax / step))
    return [k * step for k in range(0, nstep + 1)]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="moi-iv",
        description="I-V at fixed T: two zero-origin branches (0->+Imax, 0->-Imax) "
                    "each from the virgin SC state; optional pulsed current to "
                    "cut self-heating. cryo + sample 6221 DC + 2182A.",
    )
    p.add_argument("--temp", type=float, required=True,
                   help="measurement temperature (K); I-V is swept here")
    # ---- Optional field-cooled front-end (engage the magnet, GPIB 30) ----
    p.add_argument("--psu", default=None, choices=sorted(PSU_REGISTRY.keys()),
                   help="field PSU (e.g. k6221_dc, GPIB 30). If given, a field "
                        "is applied above Tc and the sample is cooled through "
                        "Tc with the field on (true field-cooled) before the "
                        "I-V sweep.")
    p.add_argument("--field", type=float, default=None,
                   help="field-cool field in Oe (requires --psu and --t-high)")
    p.add_argument("--coil-oe-per-a", type=float, default=333.3,
                   help="coil constant Oe/A for field->current (default 333.3)")
    p.add_argument("--t-high", type=float, default=None,
                   help="above-Tc temperature (K) to apply the field at, before "
                        "cooling to --temp (requires --field)")
    p.add_argument("--i-max", type=float, required=True,
                   help="max sample current magnitude in mA (sweep -i_max..+i_max)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--i-step", type=float, default=None,
                   help="current step in mA (default i_max/10)")
    g.add_argument("--points", type=int, default=None,
                   help="total number of current points (odd -> 0 included)")
    p.add_argument("--settle", type=float, default=0.3,
                   help="settle after setting each current before reading V (s)")
    # ---- Self-heating control ----
    p.add_argument("--pulsed", action="store_true",
                   help="pulsed current: at each point pulse to I, read V, "
                        "return to 0 between points (full thermal relax). "
                        "Without this the current is held continuously.")
    p.add_argument("--pulse-width", type=float, default=0.05,
                   help="seconds to hold the current pulse before reading V "
                        "(pulsed mode; default 0.05)")
    p.add_argument("--dwell", type=float, default=0.5,
                   help="seconds at 0 between pulses for thermal recovery "
                        "(pulsed mode; default 0.5)")
    # ---- Auto-rescan around the Ic jump ----
    p.add_argument("--auto-rescan", action="store_true",
                   help="after the coarse branch detects a voltage JUMP "
                        "(dV > --v-jump between consecutive points), re-sweep "
                        "a +/- window around it with fine steps; the fine "
                        "points REPLACE the coarse ones in that window.")
    p.add_argument("--v-jump", type=float, default=5e-6,
                   help="voltage jump threshold in V between consecutive "
                        "coarse points that triggers the rescan (default 5e-6)")
    p.add_argument("--rescan-window", type=int, default=1,
                   help="rescan +/- this many COARSE steps around the jump "
                        "(default 1 = the step interval bracketing the jump)")
    p.add_argument("--i-step-fine", type=float, default=None,
                   help="fine current step (mA) for the rescan "
                        "(default --i-step / 10)")
    p.add_argument("--n-reads", type=int, default=1,
                   help="average this many V readings per current point (default 1)")
    p.add_argument("--compliance", type=float, default=10.0,
                   help="sample-source voltage compliance (V)")
    p.add_argument("--sample-visa", default="GPIB0::29::INSTR")
    p.add_argument("--sample-range-a", type=float, default=None)
    p.add_argument("--voltmeter-visa", default="GPIB0::9::INSTR")
    p.add_argument("--voltmeter-nplc", type=float, default=5.0)
    p.add_argument("--probe-mode", default="2-probe",
                   choices=["2-probe", "4-probe"],
                   help="wiring metadata (2182A is identical either way)")
    p.add_argument("--out", type=str, default=".", help="output directory")
    p.add_argument("--tag", type=str, default="iv", help="filename tag")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and exit; no hardware")
    return p.parse_args(argv)


def _read_voltage_avg(voltmeter, n_reads: int) -> Optional[float]:
    vals = []
    for _ in range(max(1, n_reads)):
        v = voltmeter.read_voltage()
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return sum(vals) / len(vals)


def main(argv=None) -> int:
    args = parse_args(argv)
    T = float(args.temp)
    branch_mA = _branch_currents(args.i_max, args.i_step, args.points)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{args.tag}_iv.csv"
    png_path = out_dir / f"{args.tag}_iv.png"

    # ---- Field-cool setup ----
    field_on = args.field is not None and args.psu is not None
    B_Oe = float(args.field) if field_on else 0.0
    I_field_a = (field_to_current_a(B_Oe, args.coil_oe_per_a) if field_on else 0.0)
    if args.field is not None and args.psu is None:
        print("error: --field requires --psu (the field PSU, e.g. k6221_dc)",
              file=sys.stderr)
        return 2
    if field_on and args.t_high is None:
        print("error: field-cool requires --t-high (above-Tc temperature)",
              file=sys.stderr)
        return 2

    mode = "PULSED (return to 0 between points)" if args.pulsed else "continuous"
    print(f"[I-V] T={T:.3f} K (fixed), zero-origin dual sweep "
          f"(0->+{abs(args.i_max):.3f} and 0->-{abs(args.i_max):.3f} mA), "
          f"{len(branch_mA)} points/branch, {mode}, compliance {args.compliance} V")
    if field_on:
        print(f"[I-V] FIELD-COOLED: B={B_Oe:+.3f} Oe ({I_field_a*1e3:.3f} mA) "
              f"applied at T_high={args.t_high:.3f} K, cool to {T:.3f} K with "
              f"field on, then sweep I")

    if args.dry_run:
        mode = "pulsed" if args.pulsed else "continuous"
        print(f"[dry-run] {mode}; +branch 0->{abs(args.i_max):+.3f} then "
              f"-branch 0->{-abs(args.i_max):+.3f} mA; "
              f"magnitudes (mA): {[round(c,4) for c in branch_mA]}")
        if field_on:
            print(f"[dry-run] field-cool: B={B_Oe:+.3f} Oe at T_high="
                  f"{args.t_high} K -> cool to {T} K -> I-V")
        print(f"[dry-run] CSV {csv_path}, PNG {png_path}")
        return 0

    # ---- Open instruments (cryo + optional magnet + DC sample source + 2182A) ----
    cryo = Cryostation(CryoCfg()); cryo.open()
    print(f"[cryo] connected; state: {cryo.read_state()}")

    mag_ctrl = mag_rm = mag_cfg = None
    if field_on:
        entry = PSU_REGISTRY[args.psu]
        mag_cfg = MagnetCfg(controller=entry["driver"],
                            visa_resource=entry["visa"], **entry["extras"])
        mag_ctrl, mag_rm = open_magnet(mag_cfg)
        print(f"[magnet] {args.psu} -> {mag_ctrl.idn()}")
        if abs(I_field_a) > mag_cfg.current_limit_a:
            print(f"error: field current {I_field_a:.4f} A exceeds magnet limit "
                  f"{mag_cfg.current_limit_a:.4f} A", file=sys.stderr)
            try: mag_ctrl.inst.close(); mag_rm.close(); cryo.close()
            except Exception: pass
            return 2

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
    monitor = _IVMonitor(title=f"I-V  ({args.tag}, T={T:.2f} K)")
    try:
        # ---- Field-cool (if requested): field on above Tc, cool through Tc ----
        field_now = 0.0
        if field_on:
            mag_ctrl.set_voltage_limit(mag_cfg.voltage_limit_v)
            mag_ctrl.set_current(0.0); mag_ctrl.output_on()
            print(f"[I-V] warming to T_high={args.t_high:.3f} K ...")
            cryo.stabilize_at(args.t_high, label="I-V T_high")
            print(f"[I-V] applying field B={B_Oe:+.3f} Oe above Tc ...")
            field_now = ramp_current(mag_ctrl, 0.0, I_field_a,
                                     mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
            print(f"[I-V] cooling to {T:.3f} K with field on (field-cooled) ...")
            cryo.stabilize_at(T, label="I-V T_meas (field on)")
        else:
            print(f"[I-V] stabilizing at T={T:.3f} K ...")
            cryo.stabilize_at(T, label="I-V fixed T")

        with open(long_path(csv_path), "w", newline="") as csv_f:
            writer = csv.writer(csv_f)
            writer.writerow(IV_CSV_COLUMNS)

            idx = 0

            def _measure(I_a):
                """One point: pulse-or-hold, read V. Returns (V, state)."""
                if args.pulsed:
                    sample.set_current(I_a)
                    time.sleep(args.pulse_width)
                    V = _read_voltage_avg(voltmeter, args.n_reads)
                    sample.set_current(0.0)
                    time.sleep(args.dwell)
                else:
                    sample.set_current(I_a)
                    time.sleep(args.settle)
                    V = _read_voltage_avg(voltmeter, args.n_reads)
                return V, cryo.read_state()

            def _record(I_mA, V, st, branch, pass_label, plot=False):
                """Write one CSV row (idx auto-increments). Plotting is done by
                the caller for live feedback, so plot=False by default; the fine
                pass sets plot=True since those points aren't plotted elsewhere."""
                nonlocal idx
                I_a = I_mA * 1e-3
                R = (V / I_a) if (V is not None and I_a != 0.0) else None
                now = time.monotonic()
                writer.writerow([
                    idx, branch, pass_label,
                    f"{B_Oe:+.4f}", f"{I_field_a:+.6e}",
                    f"{st['T_set_K']:+.4f}", f"{st['T_plat_K']:+.4f}",
                    f"{st['T_sample_K']:+.4f}", f"{st['stab_K']:+.5f}",
                    f"{I_a:+.6e}",
                    ("" if V is None else f"{V:+.9e}"),
                    ("" if R is None else f"{R:+.6e}"),
                    f"{now - t0:.2f}",
                ])
                csv_f.flush()
                if plot:
                    monitor.add_point(I_mA, V, branch)
                print(f"  [{idx}|{branch}|{pass_label}] I={I_mA:+.4f} mA  "
                      f"V={'--' if V is None else f'{V:+.6g} V'}  "
                      f"T={st['T_plat_K']:.3f}K", flush=True)
                idx += 1

            coarse_step = (abs(args.i_step) if args.i_step
                           else abs(args.i_max) / 20.0)
            fine_step = (abs(args.i_step_fine) if args.i_step_fine
                         else coarse_step / 10.0)

            # Two branches, each OUTWARD from 0 (virgin SC state).
            for branch, sign in (("pos", +1.0), ("neg", -1.0)):
                print(f"\n[I-V branch {branch}] 0 -> {sign*abs(args.i_max):+.3f} mA")
                sample.set_current(0.0)
                time.sleep(args.dwell if args.pulsed else args.settle)

                # ---- Coarse pass: measure + plot live, BUFFER for CSV ----
                # The jump window is retroactive (a jump at point k reaches
                # back to k-1 +/- N steps), so we can't decide which coarse
                # points to keep until the whole pass is done. Buffer them,
                # plot live for feedback, then commit the out-of-window ones.
                coarse_buf = []              # (I_mag, V, st)
                prev_V = None
                jump_lo = jump_hi = None      # magnitudes bracketing the jump
                for I_mag in branch_mA:
                    I_mA = sign * I_mag
                    V, st = _measure(I_mA * 1e-3)
                    coarse_buf.append((I_mag, V, st))
                    monitor.add_point(I_mA, V, branch)   # live feedback
                    print(f"  [coarse|{branch}] I={I_mA:+.4f} mA  "
                          f"V={'--' if V is None else f'{V:+.6g} V'}", flush=True)
                    if (args.auto_rescan and jump_lo is None
                            and prev_V is not None and V is not None
                            and abs(V - prev_V) > args.v_jump):
                        jump_lo, jump_hi = prev_mag_j, I_mag
                        print(f"  >> jump detected between {sign*prev_mag_j:+.4f} and "
                              f"{sign*I_mag:+.4f} mA (dV={abs(V-prev_V):.3g} V)")
                    prev_mag_j, prev_V = I_mag, V
                    if monitor.abort_requested:
                        break

                # Window magnitudes (if a jump fired); else no exclusion.
                if args.auto_rescan and jump_lo is not None:
                    w = args.rescan_window * coarse_step
                    win_lo = max(0.0, jump_lo - w)
                    win_hi = min(abs(args.i_max), jump_hi + w)
                else:
                    win_lo = win_hi = None

                # Commit coarse points, skipping any inside the rescan window
                # (the fine pass below replaces those), and drop them from
                # the live plot so the curve shows fine-only in the window.
                for I_mag, V, st in coarse_buf:
                    if (win_lo is not None) and (win_lo <= I_mag <= win_hi):
                        continue
                    _record(sign * I_mag, V, st, branch, "coarse")
                if win_lo is not None:
                    monitor.drop_window(branch, win_lo, win_hi)

                # ---- Fine rescan of the +/- window around the jump ----
                if (not monitor.abort_requested and args.auto_rescan
                        and win_lo is not None):
                    nfine = int(round((win_hi - win_lo) / fine_step))
                    fine_mags = [win_lo + k * fine_step
                                 for k in range(0, nfine + 1)]
                    print(f"  >> fine rescan {sign*win_lo:+.4f} -> "
                          f"{sign*win_hi:+.4f} mA at step {fine_step:.4f} mA "
                          f"({len(fine_mags)} points)")
                    sample.set_current(0.0)
                    time.sleep(args.dwell if args.pulsed else args.settle)
                    for I_mag in fine_mags:
                        I_mA = sign * I_mag
                        V, st = _measure(I_mA * 1e-3)
                        _record(I_mA, V, st, branch, "fine", plot=True)
                        if monitor.abort_requested:
                            break

                if monitor.abort_requested:
                    print(f"[I-V] aborted")
                    break

            monitor.save_png(png_path)
    finally:
        try: sample.set_current(0.0); sample.output_off()
        except Exception: pass
        try: voltmeter.close()
        except Exception: pass
        # Ramp the field to 0 and close the magnet.
        try:
            if mag_ctrl is not None:
                ramp_current(mag_ctrl, I_field_a, 0.0,
                             mag_cfg.ramp_step_a, mag_cfg.ramp_step_delay_s)
        except Exception: pass
        try:
            if mag_ctrl is not None:
                mag_ctrl.set_current(0.0)
                mag_ctrl.output_off()
                mag_ctrl.inst.close()
        except Exception: pass
        try: rm.close()
        except Exception: pass
        try: cryo.set_setpoint(args.t_high)
        except Exception: pass
        try: cryo.close()
        except Exception: pass

    print(f"\n[I-V done] data: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
