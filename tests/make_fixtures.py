"""Generate synthetic placement fixtures for Phase 3 tests.

Derives exact design positions from COMPONENTS in make_synthetic_pcb.py
(pixel-space) converted to mm using mm_per_px=0.12.

Produces:
  tests/fixtures/synthetic_pcb.pos     — KiCad .pos format
  tests/fixtures/synthetic_pcb.csv     — Generic CSV format
  tests/fixtures/synthetic_pcb_fiducials.json  — fiducial positions in both spaces

SYNTHETIC — these are fabricated from script data, not a real PCB design.
DO NOT use as evidence of real-design support.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a script from the repo root
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

import _bootstrap  # noqa: F401  (adds backend/ to sys.path if not already there)

from make_synthetic_pcb import COMPONENTS, W, H

MM_PER_PX = 0.12   # must match the reference created in make_synthetic_pcb.py

# Package names chosen to match the package_classes.json table
_KIND_TO_PACKAGE = {
    "ic":        "SOIC-8",
    "resistor":  "R_0603",
    "cap":       "C_0805",
    "led":       "LED_D5",
    "connector": "PinHeader_1x06",
}

# Fiducials: taken from render_board() — the four corner circles at (55,55),
# (W-55,55), (55,H-55), (W-55,H-55) = (845,55), (55,585), (845,585)
# pixel coords  →  design mm (centre of board = 0,0 in mm space)
# We use board-space mm: top-left corner of the board image = (0, 0) mm
_FIDUCIALS = [
    ("FID1", (55, 55)),       # top-left
    ("FID2", (W - 55, 55)),   # top-right
    ("FID3", (55, H - 55)),   # bottom-left
    ("FID4", (W - 55, H - 55)), # bottom-right
]


def px_to_mm(x_px: int, y_px: int) -> tuple[float, float]:
    """Convert reference-image pixel coords to design mm (identity mm_per_px scale)."""
    return round(x_px * MM_PER_PX, 4), round(y_px * MM_PER_PX, 4)


def component_center_px(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2.0, y + h / 2.0


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- KiCad .pos -------------------------------------------------------
    pos_lines = [
        "# Phase 3 synthetic PCB placement fixture",
        "# SYNTHETIC — generated from make_synthetic_pcb.py COMPONENTS",
        "# mm_per_px=0.12 applied to pixel bboxes to get design mm coordinates",
        "# DO NOT use as evidence of real-design support.",
        "#",
        "Ref Val Package PosX PosY Rot Side",
    ]
    # Add fiducials first
    for fname, (fpx, fpy) in _FIDUCIALS:
        xmm, ymm = px_to_mm(fpx, fpy)
        pos_lines.append(f"{fname} fiducial FID_2mm {xmm} {ymm} 0 F")

    for name, box, kind in COMPONENTS:
        cx, cy = component_center_px(box)
        xmm, ymm = px_to_mm(int(cx), int(cy))
        pkg = _KIND_TO_PACKAGE.get(kind, "R_0603")
        val = kind.upper()
        pos_lines.append(f"{name} {val} {pkg} {xmm} {ymm} 0 F")

    (out_dir / "synthetic_pcb.pos").write_text("\n".join(pos_lines), "utf-8")
    print(f"wrote {out_dir / 'synthetic_pcb.pos'}")

    # ---- Generic CSV -------------------------------------------------------
    csv_lines = [
        "# Phase 3 synthetic PCB placement fixture (CSV)",
        "# SYNTHETIC — generated from make_synthetic_pcb.py COMPONENTS",
        "Ref,Value,Package,PosX,PosY,Rotation,Side",
    ]
    for fname, (fpx, fpy) in _FIDUCIALS:
        xmm, ymm = px_to_mm(fpx, fpy)
        csv_lines.append(f"{fname},fiducial,FID_2mm,{xmm},{ymm},0,F")
    for name, box, kind in COMPONENTS:
        cx, cy = component_center_px(box)
        xmm, ymm = px_to_mm(int(cx), int(cy))
        pkg = _KIND_TO_PACKAGE.get(kind, "R_0603")
        val = kind.upper()
        csv_lines.append(f"{name},{val},{pkg},{xmm},{ymm},0,F")

    (out_dir / "synthetic_pcb.csv").write_text("\n".join(csv_lines), "utf-8")
    print(f"wrote {out_dir / 'synthetic_pcb.csv'}")

    # ---- Fiducial correspondence JSON -------------------------------------
    fid_data = []
    for fname, (fpx, fpy) in _FIDUCIALS:
        xmm, ymm = px_to_mm(fpx, fpy)
        fid_data.append({
            "name": fname,
            "design_mm": [xmm, ymm],
            "ref_px": [fpx, fpy],
            "_note": "SYNTHETIC — pixel coords from render_board(); mm = px * 0.12"
        })
    (out_dir / "synthetic_pcb_fiducials.json").write_text(
        json.dumps({"_synthetic": True, "fiducials": fid_data}, indent=2), "utf-8"
    )
    print(f"wrote {out_dir / 'synthetic_pcb_fiducials.json'}")


if __name__ == "__main__":
    main()
