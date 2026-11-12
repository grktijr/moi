r"""Filter FC runs by parameter and assemble a stack for ImageJ.

Scans a directory of individual FC run folders, reads each run's metadata
CSV into one index table (typed columns — reliable, not name-parsing), then
selects the runs matching your fixed constraints, sorts by the varying axis,
and emits either a multi-page TIFF or a numbered folder for ImageJ.

Filtering is on CSV columns (B_set_Oe, T_set_K, etc.). AC amplitude/frequency
are only filterable if the CSV carries ac_amp_Oe / ac_freq_Hz columns; if it
doesn't (older runs), those axes are unavailable and the tool says so.

Entry point: moi-stack  (see pyproject.toml [project.scripts])

    # list what's available (index table + distinct values per column)
    moi-stack --root D:\data\Area1 --list

    # stack over DC field, fixing T; multi-page TIFF sorted by B
    moi-stack --root D:\data\Area1 --kind calibrated `
        --fix T_set_K=6.0 --vary B_set_Oe --out T6_Bscan.tif

    # numbered folder instead of a multipage TIFF
    moi-stack --root D:\data\Area1 --vary B_set_Oe --fix T_set_K=6.0 `
        --out T6_Bscan --folder
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import tifffile

from .._paths import long_path

# tolerance for float equality when matching --fix values
_ATOL = 1e-6


def _find_csv(run_dir: Path) -> Optional[Path]:
    cands = sorted(run_dir.glob("*_metadata.csv"))
    return cands[0] if cands else None


def build_index(root: Path, kind: str, glob: str) -> pd.DataFrame:
    """One row per run: the run's parameters + the path to its `kind` image.

    Uses the fc_low row (the measurement image) for the parameter values, and
    the first *.tif in <run>/<kind>/ as the image to stack.
    """
    rows = []
    for run_dir in sorted(root.glob(glob)):
        if not run_dir.is_dir():
            continue
        csv = _find_csv(run_dir)
        if csv is None:
            continue
        try:
            df = pd.read_csv(csv)
        except Exception:
            continue

        # measurement row: prefer fc_low, else fczfc, else first row
        if "phase" in df.columns:
            m = df[df["phase"].astype(str).str.startswith("fc_low")]
            if m.empty:
                m = df[df["phase"].astype(str).str.startswith("fczfc")]
            row = (m.iloc[0] if not m.empty else df.iloc[0])
        else:
            row = df.iloc[0]

        img_dir = run_dir / kind
        imgs = sorted(img_dir.glob("*.tif")) if img_dir.is_dir() else []
        if not imgs:
            continue

        rec = {"run": run_dir.name, "img": imgs[-1]}  # last = measurement image
        # carry every numeric-ish column through, rounded for stable matching
        for col in df.columns:
            if col in ("filename", "src", "idn", "phase"):
                continue
            val = row.get(col, np.nan)
            try:
                rec[col] = round(float(val), 6)
            except (TypeError, ValueError):
                rec[col] = val
        rows.append(rec)

    return pd.DataFrame(rows)


def parse_kv(items):
    """Parse ['col=val', ...] into {col: value}. Values are kept as float when
    numeric, else as the raw string (so string columns like ac_shape work)."""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--fix expects col=value, got {it!r}")
        k, v = it.split("=", 1)
        k, v = k.strip(), v.strip()
        try:
            out[k] = float(v)          # numeric fix (B_set_Oe=-0.4, ac_freq_Hz=100)
        except ValueError:
            out[k] = v                 # string fix (ac_shape=SIN)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="moi-stack",
        description="Filter FC runs by CSV metadata and build an ImageJ stack.",
    )
    p.add_argument("--root", required=True, help="directory containing run folders")
    p.add_argument("--glob", default="fc_*",
                   help="glob for run folders under root (default 'fc_*')")
    p.add_argument("--kind", default="divided",
                   choices=["calibrated", "multiplied", "divided", "original"],
                   help="which sub-folder's image to stack (default divided)")
    p.add_argument("--fix", nargs="*", default=[],
                   metavar="COL=VAL",
                   help="fix columns to values, e.g. --fix T_set_K=6.0 ac_amp_Oe=0.1")
    p.add_argument("--vary", default=None,
                   help="column to sort the stack by (the scan axis)")
    p.add_argument("--order", default="ascend",
                   choices=["ascend", "descend"],
                   help="sort direction along --vary (default ascend). DC "
                        "(no-AC) runs sort as f->+inf, i.e. the largest value.")
    p.add_argument("--allow-duplicates", action="store_true",
                   help="proceed even if --vary has duplicate values in the "
                        "selection (ties ordered by folder name). Without this, "
                        "an under-constrained selection is a hard error.")
    p.add_argument("--out", default=None,
                   help="output .tif (multi-page) or folder name (with --folder)")
    p.add_argument("--folder", action="store_true",
                   help="write a numbered folder instead of a multi-page TIFF")
    p.add_argument("--list", action="store_true",
                   help="just print the index table and distinct values, then exit")
    args = p.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    idx = build_index(root, args.kind, args.glob)
    if idx.empty:
        print(f"No runs with a '{args.kind}' image found under {root} "
              f"(glob '{args.glob}').", file=sys.stderr)
        return 1

    # --list: show what's filterable and the distinct values per column
    if args.list:
        print(f"{len(idx)} runs indexed under {root}\n")
        skip = {"run", "img"}
        for col in idx.columns:
            if col in skip:
                continue
            vals = idx[col].dropna().unique()
            if 0 < len(vals) <= 25:
                try:
                    vals = sorted(vals)
                except TypeError:
                    pass
                shown = ", ".join(str(v) for v in vals)
                print(f"  {col:12s}: {shown}")
            else:
                print(f"  {col:12s}: {len(vals)} distinct values")
        return 0

    # apply --fix constraints
    fixes = parse_kv(args.fix)
    sel = idx
    for col, val in fixes.items():
        if col not in sel.columns:
            print(f"error: column {col!r} not in CSV metadata. "
                  f"Available: {', '.join(c for c in idx.columns if c not in ('run','img'))}",
                  file=sys.stderr)
            return 2
        if isinstance(val, float):
            sel = sel[np.isclose(sel[col].astype(float), val, atol=_ATOL)]
        else:
            # string fix: exact match, tolerant of surrounding whitespace/case
            sel = sel[sel[col].astype(str).str.strip().str.upper() == val.strip().upper()]
    if sel.empty:
        print("No runs match the given --fix constraints.", file=sys.stderr)
        print("Try --list to see available values.", file=sys.stderr)
        return 1

    # sort by the varying axis
    ascending = (args.order == "ascend")
    if args.vary:
        if args.vary not in sel.columns:
            print(f"error: --vary column {args.vary!r} not found.", file=sys.stderr)
            return 2
        # DC (no-AC) runs have NaN in an AC column (e.g. ac_freq_Hz). Treat them
        # as f -> +inf so they sort as the largest value on the axis and obey
        # --order like any real frequency (top of ascend, bottom of descend).
        # A temporary sort key holds the +inf fill; the original column (with
        # NaN) is preserved for display and the sidecar.
        sort_key = "__sortkey__"
        sel = sel.copy()
        sel[sort_key] = pd.to_numeric(sel[args.vary], errors="coerce").fillna(np.inf)
        sel = sel.sort_values(sort_key, ascending=ascending).drop(columns=sort_key)
    else:
        sel = sel.sort_values("run", ascending=ascending)

    # Guard: if the vary axis has duplicate values among the selected runs,
    # the --fix constraints are insufficient to pick one run per axis value.
    # Identify the still-varying columns so the user knows what else to fix.
    if args.vary:
        vary_vals = sel[args.vary]
        if vary_vals.duplicated().any():
            dup_vals = sorted(set(
                v for v in vary_vals[vary_vals.duplicated(keep=False)]
                if pd.notna(v)
            ))
            # which other numeric columns still differ among the selection?
            free_cols = []
            for c in sel.columns:
                if c in (args.vary, "run", "img"):
                    continue
                try:
                    if sel[c].dropna().nunique() > 1:
                        free_cols.append(c)
                except TypeError:
                    pass
            print(f"error: --vary '{args.vary}' has duplicate values "
                  f"{dup_vals} among the {len(sel)} selected runs, so the "
                  f"stack order is ambiguous (more than one run per "
                  f"{args.vary} value).", file=sys.stderr)
            if free_cols:
                print(f"       These columns still vary in the selection; "
                      f"add one or more to --fix to disambiguate: "
                      f"{', '.join(free_cols)}", file=sys.stderr)
            print(f"       Or use --list to inspect, or --allow-duplicates to "
                  f"stack anyway (order within a tie is by folder name).",
                  file=sys.stderr)
            if not args.allow_duplicates:
                return 2

    print(f"Selected {len(sel)} runs"
          + (f", varying {args.vary}" if args.vary else "") + ":")
    cols_show = [c for c in (args.vary, "run") if c] or ["run"]
    for _, r in sel.iterrows():
        axis_val = f"{r[args.vary]}" if args.vary else ""
        print(f"  {axis_val:>12}  {r['run']}")

    if args.out is None:
        print("\n(no --out given; nothing written. Add --out to emit a stack.)")
        return 0

    # ---- emit ----
    paths = list(sel["img"])
    if args.folder:
        out_dir = root / args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, (path, (_, r)) in enumerate(zip(paths, sel.iterrows()), start=1):
            tag = (f"{r[args.vary]}" if args.vary else r["run"])
            dst = out_dir / f"{i:03d}_{tag}.tif"
            tifffile.imwrite(str(long_path(dst)),
                             tifffile.imread(str(long_path(path))),
                             photometric="minisblack")
        print(f"\nWrote {len(paths)} images to {out_dir} "
              f"(ImageJ: File > Import > Image Sequence)")
    else:
        out_path = root / args.out if not Path(args.out).is_absolute() else Path(args.out)
        imgs = [tifffile.imread(str(long_path(p))) for p in paths]
        shapes = {im.shape for im in imgs}
        if len(shapes) > 1:
            print(f"error: images have differing shapes {shapes}; "
                  f"use --folder for a sequence instead.", file=sys.stderr)
            return 1
        # Per-slice labels so ImageJ shows the varying parameter (not just N/8).
        # DC (no-AC) runs have NaN on the vary axis -> label them "DC".
        labels = []
        for _, r in sel.iterrows():
            if args.vary:
                v = r[args.vary]
                vtxt = "DC" if pd.isna(v) else f"{args.vary}={v:g}"
            else:
                vtxt = str(r["run"])
            labels.append(vtxt)
        tifffile.imwrite(
            str(long_path(out_path)),
            np.stack(imgs),
            imagej=True,
            metadata={"Labels": labels},
        )
        # sidecar index: slice -> varying value + run
        side = out_path.with_suffix(".index.csv")
        cols = [c for c in (args.vary, "run") if c] or ["run"]
        sel[cols].reset_index(drop=True).to_csv(side, index_label="slice")
        print(f"\nWrote {len(imgs)}-slice stack to {out_path}")
        print(f"Slice index -> value written to {side}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
