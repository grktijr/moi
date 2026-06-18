"""Cryostation Gen 1/2 TCP driver.

The Cryostation Windows software exposes a length-prefixed ASCII protocol
on TCP. This module wraps that protocol plus a stabilization routine that
polls platform T and stability metric until both meet thresholds.
"""

from __future__ import annotations

import socket
import time
from typing import Optional

from ..config import CryoCfg


class Cryostation:
    """TCP client for Cryostation Gen 1/2 controller software."""

    def __init__(self, cfg: CryoCfg):
        self.cfg = cfg
        self.sock: Optional[socket.socket] = None

    def open(self) -> None:
        self.sock = socket.create_connection(
            (self.cfg.host, self.cfg.port), timeout=self.cfg.socket_timeout_s,
        )
        self.sock.settimeout(self.cfg.socket_timeout_s)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    # ---- Low-level protocol ------------------------------------------------

    def _send(self, cmd: str) -> str:
        """Send a command with the length prefix, return the response payload."""
        if self.sock is None:
            raise RuntimeError("Cryostation socket not open")
        body = cmd.encode("ascii")
        framed = f"{len(body):02d}".encode("ascii") + body
        self.sock.sendall(framed)

        # Read 2-byte length, then payload
        header = self._recv_exact(2)
        n = int(header.decode("ascii"))
        if n == 0:
            return ""
        return self._recv_exact(n).decode("ascii")

    def _recv_exact(self, n: int) -> bytes:
        if self.sock is None:
            raise RuntimeError("socket closed")
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RuntimeError("Cryostation socket closed mid-read")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # ---- High-level helpers ------------------------------------------------

    def platform_K(self) -> float:
        return float(self._send("GPT"))

    def sample_K(self) -> float:
        return float(self._send("GST"))

    def stability_K(self) -> float:
        return float(self._send("GSS"))

    def get_setpoint(self) -> float:
        return float(self._send("GTSP"))

    def set_setpoint(self, T_K: float) -> None:
        self._send(f"STSP{T_K:.3f}")

    def read_state(self) -> dict:
        """Return a dict with all four standard readings, robust to errors."""
        out = {}
        for key, cmd in (
            ("T_set_K", "GTSP"),
            ("T_plat_K", "GPT"),
            ("T_sample_K", "GST"),
            ("stab_K", "GSS"),
        ):
            try:
                out[key] = float(self._send(cmd))
            except Exception:
                out[key] = float("nan")
        return out

    # ---- Stabilization -----------------------------------------------------

    def stabilize_at(self, target_K: float, *, label: str = "") -> dict:
        """Set the setpoint and block until temperature is stable, or timeout."""
        c = self.cfg
        self.set_setpoint(target_K)
        prefix = f"[stabilize {label}]" if label else "[stabilize]"
        print(f"{prefix} target {target_K:.3f} K, tol={c.tol_K} K, "
              f"stab<{c.stab_K} K, dwell={c.dwell_s:.0f}s, "
              f"timeout={c.stabilize_timeout_s:.0f} s")

        t_start = time.monotonic()
        in_window_since: Optional[float] = None

        while True:
            t_el = time.monotonic() - t_start
            pt = self.platform_K()
            ss = self.stability_K()
            in_window = abs(pt - target_K) < c.tol_K and ss < c.stab_K

            if in_window:
                if in_window_since is None:
                    in_window_since = time.monotonic()
                dwell_so_far = time.monotonic() - in_window_since
                print(f"  t={t_el:6.1f}s  T={pt:7.4f}K  stab={ss:7.5f}K  "
                      f"in-window  {dwell_so_far:4.1f}/{c.dwell_s:.0f}s")
                if dwell_so_far >= c.dwell_s:
                    state = self.read_state()
                    print(f"{prefix} stable at {pt:.4f} K after {t_el:.1f} s")
                    return state
            else:
                in_window_since = None
                print(f"  t={t_el:6.1f}s  T={pt:7.4f}K  stab={ss:7.5f}K  "
                      f"|dT|={abs(pt-target_K):.4f}K")

            if t_el > c.stabilize_timeout_s:
                state = self.read_state()
                print(f"{prefix} [warn] timeout after {t_el:.0f} s; "
                      f"target {target_K:.3f} K not stably reached "
                      f"(last T={pt:.4f} K, stab={ss:.5f} K). "
                      f"Proceeding with experiment anyway.")
                return state

            time.sleep(c.poll_s)
