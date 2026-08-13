# src/moi_control/_paths.py
import os
import sys


def long_path(p) -> str:
    """Windows-safe absolute path that bypasses MAX_PATH.
    
    Prepends \\?\ on Windows, no-op elsewhere.
    """
    s = os.fspath(p)
    if sys.platform != "win32":
        return s
    abs_path = os.path.abspath(s)
    if abs_path.startswith("\\\\?\\"):
        return abs_path
    if abs_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + abs_path[2:]
    return "\\\\?\\" + abs_path