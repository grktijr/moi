"""
Minimal Cryostation setpoint script.

Sequence:
    1. Connect.
    2. Read current state (setpoint, platform T, sample T, stability) and PRINT.
    3. Confirm with the user before sending any SET command.
    4. Send STSP <target>.
    5. Poll platform T and stability every POLL_S seconds until either:
         (a) |T - target| < TOL_K  AND  stability < STAB_K  for DWELL_S seconds, or
         (b) TIMEOUT_S elapses.
    6. Print a brief summary and exit. Does NOT change the setpoint on exit.

Safety:
    - Will not send a setpoint outside [MIN_K, MAX_K] without manual code edit.
    - Asks for keyboard confirmation before the SET command.
    - On Ctrl+C, the controller keeps regulating at the last setpoint sent.
      Abort from the Cryostation GUI on the laptop if needed.
"""

import socket
import sys
import time

HOST = "192.168.0.2"
PORT = 7773
TIMEOUT_NET = 3.0          # network socket timeout, seconds

# --- experiment parameters ---
TARGET_K   = 10.0          # target platform temperature, Kelvin
TOL_K      = 0.05          # |T - target| must be below this
STAB_K     = 0.05          # stability metric must be below this
DWELL_S    = 30.0          # both conditions must hold for this long, seconds
POLL_S     = 1.0           # polling interval, seconds
TIMEOUT_S  = 600.0         # give up after this many seconds

# --- safety bounds ---
MIN_K      = 3.0
MAX_K      = 50.0          # raise this only after you've verified behavior


def send(sock: socket.socket, cmd: str) -> str:
    """Send one length-prefixed ASCII command; return the response body."""
    msg = f"{len(cmd):02d}{cmd}".encode("ascii")
    sock.sendall(msg)

    header = b""
    while len(header) < 2:
        chunk = sock.recv(2 - len(header))
        if not chunk:
            raise ConnectionError("connection closed reading length header")
        header += chunk
    n = int(header.decode("ascii"))

    body = b""
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            raise ConnectionError("connection closed mid-message")
        body += chunk
    return body.decode("ascii")


def get_float(sock: socket.socket, cmd: str) -> float:
    """Send a GET command and parse the reply as a float. Raises on error."""
    raw = send(sock, cmd)
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{cmd} returned non-numeric reply: {raw!r}")


def main() -> int:
    # --- bounds check on TARGET_K before opening the socket ---
    if not (MIN_K <= TARGET_K <= MAX_K):
        print(f"TARGET_K={TARGET_K} K is outside the safety window "
              f"[{MIN_K}, {MAX_K}]. Edit the script to widen the window "
              f"only after deliberate review.")
        return 1

    with socket.create_connection((HOST, PORT), timeout=TIMEOUT_NET) as s:
        s.settimeout(TIMEOUT_NET)
        print(f"connected to {HOST}:{PORT}\n")

        # --- initial state ---
        sp0  = get_float(s, "GTSP")
        pt0  = get_float(s, "GPT")
        st0  = get_float(s, "GST")
        ss0  = get_float(s, "GSS")
        print(f"current setpoint  : {sp0:8.3f} K")
        print(f"platform temp     : {pt0:8.3f} K")
        print(f"sample   temp     : {st0:8.3f} K")
        print(f"stability         : {ss0:8.5f} K")
        print()
        print(f"will SET setpoint -> {TARGET_K:.3f} K")
        print(f"  tolerance       : {TOL_K} K")
        print(f"  stability bound : {STAB_K} K")
        print(f"  dwell           : {DWELL_S:.0f} s")
        print(f"  timeout         : {TIMEOUT_S:.0f} s")
        print()

        ans = input("proceed? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted; no setpoint sent.")
            return 0

        # --- send setpoint ---
        # The legacy protocol expects "STSP<value>" with no separator.
        # Format with enough precision; firmware tolerates trailing zeros.
        set_payload = f"STSP{TARGET_K:.3f}"
        reply = send(s, set_payload)
        print(f"STSP reply: {reply!r}")

        # Read back the setpoint so we can confirm the firmware accepted it.
        sp_now = get_float(s, "GTSP")
        print(f"setpoint readback: {sp_now:.3f} K")
        if abs(sp_now - TARGET_K) > 0.01:
            print(f"WARNING: readback ({sp_now}) differs from target ({TARGET_K}). "
                  f"Stopping; check the Cryostation GUI on the laptop.")
            return 2
        print()

        # --- poll loop ---
        t_start = time.monotonic()
        t_in_window = None        # time we first entered the (tol & stab) window

        print(f"{'t[s]':>8} {'T_plat':>9} {'stab':>9} {'|dT|':>9}  state")
        while True:
            t_elapsed = time.monotonic() - t_start
            pt = get_float(s, "GPT")
            ss = get_float(s, "GSS")
            dT = abs(pt - TARGET_K)
            in_window = (dT < TOL_K) and (ss < STAB_K)

            if in_window:
                if t_in_window is None:
                    t_in_window = time.monotonic()
                t_held = time.monotonic() - t_in_window
                state = f"in-window ({t_held:5.1f}/{DWELL_S:.0f} s)"
            else:
                t_in_window = None
                state = "ramping/settling"

            print(f"{t_elapsed:8.1f} {pt:9.4f} {ss:9.5f} {dT:9.4f}  {state}")

            if in_window and (time.monotonic() - t_in_window) >= DWELL_S:
                print(f"\nstable at {pt:.4f} K (target {TARGET_K} K) "
                      f"after {t_elapsed:.1f} s. ✓")
                return 0

            if t_elapsed > TIMEOUT_S:
                print(f"\nTIMEOUT after {t_elapsed:.1f} s. last T={pt:.4f}, "
                      f"stability={ss:.5f}. Setpoint left at {TARGET_K} K.")
                return 3

            time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted by user. Controller is still regulating at the "
              "last setpoint sent; abort from the Cryostation GUI if needed.")
        sys.exit(130)
