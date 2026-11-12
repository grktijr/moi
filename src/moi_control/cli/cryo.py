"""Quick manual temperature control — cryostat only, no camera or PSU.

Sets the Cryostation setpoint and blocks until the platform is stable
(reusing Cryostation.stabilize_at), then optionally holds while printing
status. Nothing else on the rig is touched.

Entry point: moi-cryo  (see pyproject.toml [project.scripts])
    moi-cryo 4.0                 # stabilize at 4.0 K, then exit
    moi-cryo 6.5 --hold 300      # stabilize, then hold + print status 300 s
    moi-cryo 9 --dwell 60 --tol 0.02   # stricter gating
    moi-cryo --status            # just read + print current state, change nothing
    moi-cryo --setpoint-only 7.0 # set the setpoint and exit (do NOT wait)
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from ..config import CryoCfg
from ..instruments.cryostat import Cryostation


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="moi-cryo",
        description="Stabilize the cryostat to a target temperature (no camera/PSU).",
    )
    p.add_argument("target_K", type=float, nargs="?", default=None,
                   help="target temperature in K")
    p.add_argument("--status", action="store_true",
                   help="just read and print current state; change nothing")
    p.add_argument("--hold", action="store_true",
                   help="after stabilizing, keep printing status")
    # Gating overrides (default to CryoCfg values when omitted)
    p.add_argument("--tol", type=float, default=None,
                   help="|T-target| window in K (default CryoCfg.tol_K)")
    p.add_argument("--stab", type=float, default=None,
                   help="GSS stability threshold in K (default CryoCfg.stab_K)")
    p.add_argument("--dwell", type=float, default=None,
                   help="seconds both conditions must hold (default CryoCfg.dwell_s)")
    p.add_argument("--timeout", type=float, default=None,
                   help="soft-fail timeout in s (default CryoCfg.stabilize_timeout_s)")
    p.add_argument("--host", type=str, default=None,
                   help="cryostat host (default CryoCfg.host)")
    p.add_argument("--port", type=int, default=None,
                   help="cryostat TCP port (default CryoCfg.port)")
    return p.parse_args(argv)


def _print_state(cryo: Cryostation) -> None:
    s = cryo.read_state()
    print(f"  T_set={s['T_set_K']:.3f}K  T_plat={s['T_plat_K']:.4f}K  "
          f"T_sample={s['T_sample_K']:.4f}K  stab={s['stab_K']:.5f}K",
          flush=True)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.status and args.target_K is None:
        print("error: a target temperature is required (or use --status)",
              file=sys.stderr)
        return 2

    # Build config with optional overrides
    overrides = {}
    if args.host is not None:    overrides["host"] = args.host
    if args.port is not None:    overrides["port"] = args.port
    if args.tol is not None:     overrides["tol_K"] = args.tol
    if args.stab is not None:    overrides["stab_K"] = args.stab
    if args.dwell is not None:   overrides["dwell_s"] = args.dwell
    if args.timeout is not None: overrides["stabilize_timeout_s"] = args.timeout
    cryo_cfg = CryoCfg(**overrides)

    cryo = Cryostation(cryo_cfg)
    print(f"[cryo] connecting to {cryo_cfg.host}:{cryo_cfg.port} ...", flush=True)
    cryo.open()
    try:
        # --status: read and exit
        if args.status:
            _print_state(cryo)
            return 0

        # Normal: set and exit, no wait
        cryo.set_setpoint(args.target_K)
        print(f"[cryo] setpoint set to {args.target_K:.3f} K (not waiting)",
        flush=True)
        _print_state(cryo)

        # hold: stabilize (blocks until stable or soft-timeout)
        if args.hold:
            
            cryo.stabilize_at(args.target_K, label="moi-cryo")
            print(f"[cryo] holding at {args.target_K:.3f} K for {args.hold:.0f} s "
                  f"(Ctrl+C to stop) ...", flush=True)
            t0 = time.monotonic()
            try:
                while time.monotonic() - t0 < args.hold:
                    _print_state(cryo)
                    time.sleep(max(cryo_cfg.poll_s, 1.0))
            except KeyboardInterrupt:
                print("\n[cryo] hold interrupted", flush=True)
            return 0

        
            

        return 0
    finally:
        try:
            cryo.close()
        except Exception:
            pass
        print("[cryo] connection closed.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
