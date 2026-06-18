"""PVCAM/Kinetix helper functions.

The Camera object lifecycle (open/configure/close) is managed in
acquisition.py because we open/close per acquisition for reliability.
This module just provides the configuration constants and helpers.
"""

from __future__ import annotations


# Map port id → port name for PyVCAM
PORT_NAME = {
    0: "Sensitivity",
    1: "Speed",
    2: "Dynamic Range",
    3: "Sub-Electron",
}


def set_kinetix_port(cam, port_id: int) -> str:
    """Configure the Kinetix's readout port. Returns the port name set."""
    name = PORT_NAME[port_id]
    cam.readout_port = name
    cam.speed = 0
    cam.gain = 1
    return name


def set_binning(cam, binning) -> None:
    """Apply binning to a camera. Tries tuple form first, falls back to string."""
    bx, by = binning
    try:
        cam.binning = (bx, by)
    except Exception:
        cam.binning = f"{bx}x{by}"
