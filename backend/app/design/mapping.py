"""Fiducial-based affine transform: design mm → reference image pixels.

The transform requires at least 3 fiducial correspondences:
    (design_x_mm, design_y_mm)  ↔  (ref_px_x, ref_px_y)

The solution is an affine transform (2 × 3 matrix) that maps design mm
coordinates to reference-image pixel coordinates.  It handles translation,
rotation, and (anisotropic) scale — good for accurately placed boards.

Usage:
    fids = [
        Fiducial("FID1", design_mm=(3.0, 4.0), ref_px=(45, 60)),
        Fiducial("FID2", design_mm=(95.0, 4.0), ref_px=(830, 58)),
        Fiducial("FID3", design_mm=(3.0, 61.0), ref_px=(44, 572)),
    ]
    transform = compute_transform(fids)
    px_xy = transform.apply_mm(design_x_mm, design_y_mm)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Fiducial:
    """One fiducial: known position in both design and reference-image spaces."""
    name: str
    design_mm: tuple[float, float]    # (x, y) in design / CAD millimetres
    ref_px: tuple[float, float]       # (x, y) in reference image pixels


@dataclass
class FiducialResidual:
    """Per-fiducial residual after fitting the affine transform."""
    name: str
    predicted_px: tuple[float, float]
    actual_px: tuple[float, float]
    error_px: float


@dataclass
class DesignTransform:
    """Affine transform from design-mm to reference-px.

    Stores the 2×3 matrix and the per-fiducial residuals so the caller can
    audit accuracy and decide whether to accept or reject the transform.
    """
    matrix: np.ndarray              # shape (2, 3), float64
    residuals: list[FiducialResidual]
    rms_error_px: float
    max_error_px: float

    # Serialisable snapshot (for storage in meta.json)
    matrix_list: list[list[float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.matrix_list = self.matrix.tolist()

    def apply_mm(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Map one (x_mm, y_mm) design point → (px_x, px_y) reference pixels."""
        pt = np.array([x_mm, y_mm, 1.0], dtype=np.float64)
        result = self.matrix @ pt          # shape (2,)
        return (float(result[0]), float(result[1]))

    def scale_mm_to_px(self) -> float:
        """Approximate isotropic scale (mm → px), as the geometric mean of Sx, Sy."""
        sx = math.hypot(self.matrix[0, 0], self.matrix[1, 0])
        sy = math.hypot(self.matrix[0, 1], self.matrix[1, 1])
        return math.sqrt(sx * sy) if sx > 0 and sy > 0 else 0.0

    @classmethod
    def from_matrix_list(cls, mat: list[list[float]], residuals: list[FiducialResidual],
                         rms: float, maxe: float) -> "DesignTransform":
        return cls(np.array(mat, dtype=np.float64), residuals, rms, maxe)


class TransformError(ValueError):
    """Raised when the fiducial set is degenerate or produces bad residuals."""


# ---------------------------------------------------------------------------
# Transform computation
# ---------------------------------------------------------------------------

_MIN_FIDUCIALS = 3


def compute_transform(
    fiducials: Sequence[Fiducial],
    *,
    max_rms_px: float = 3.0,
    max_residual_px: float = 5.0,
) -> DesignTransform:
    """Fit a least-squares affine transform from design-mm to reference-px.

    Parameters
    ----------
    fiducials:
        At least 3 :class:`Fiducial` objects with known design and pixel positions.
    max_rms_px:
        Reject the fit if the RMS reprojection error exceeds this (pixels).
    max_residual_px:
        Reject the fit if any single fiducial error exceeds this (pixels).

    Returns
    -------
    :class:`DesignTransform`

    Raises
    ------
    TransformError
        When fewer than 3 fiducials are given, the design points are degenerate
        (collinear / coincident), or the residuals are too large.
    """
    n = len(fiducials)
    if n < _MIN_FIDUCIALS:
        raise TransformError(
            f"At least {_MIN_FIDUCIALS} fiducials required; got {n}. "
            "Add more fiducial correspondences."
        )

    # Check for duplicate design positions
    design_pts = [(f.design_mm[0], f.design_mm[1]) for f in fiducials]
    if len(set(design_pts)) < n:
        raise TransformError(
            "Duplicate design positions detected among fiducials. "
            "Each fiducial must have a unique (x_mm, y_mm) position."
        )

    # Build the least-squares system: for each fiducial
    #   [x_mm  y_mm  1  0     0     0] [a, b, c, d, e, f]^T = px_x
    #   [0     0     0  x_mm  y_mm  1]                       = px_y
    A = np.zeros((2 * n, 6), dtype=np.float64)
    b = np.zeros(2 * n, dtype=np.float64)
    for i, fid in enumerate(fiducials):
        xd, yd = fid.design_mm
        A[2 * i, :3]     = [xd, yd, 1.0]
        A[2 * i + 1, 3:] = [xd, yd, 1.0]
        b[2 * i]     = fid.ref_px[0]
        b[2 * i + 1] = fid.ref_px[1]

    # Solve using least-squares (works with n > 3 as overdetermined system)
    result, residuals_ls, rank, _ = np.linalg.lstsq(A, b, rcond=None)

    if rank < 6:
        raise TransformError(
            f"Design fiducial positions are degenerate (rank={rank}). "
            "Fiducials may be collinear or coincident. Use non-collinear fiducials."
        )

    a, b_c, c, d, e, f = result
    mat = np.array([[a, b_c, c], [d, e, f]], dtype=np.float64)

    # Compute per-fiducial residuals
    fid_residuals: list[FiducialResidual] = []
    errors: list[float] = []
    for fid in fiducials:
        px = mat @ np.array([fid.design_mm[0], fid.design_mm[1], 1.0])
        err = math.hypot(px[0] - fid.ref_px[0], px[1] - fid.ref_px[1])
        fid_residuals.append(FiducialResidual(
            name=fid.name,
            predicted_px=(float(px[0]), float(px[1])),
            actual_px=fid.ref_px,
            error_px=round(err, 3),
        ))
        errors.append(err)

    rms = math.sqrt(sum(e * e for e in errors) / len(errors))
    max_err = max(errors)

    if max_err > max_residual_px:
        worst = fid_residuals[errors.index(max_err)]
        raise TransformError(
            f"Fiducial '{worst.name}' has a residual of {worst.error_px:.2f} px "
            f"(limit {max_residual_px:.1f} px). "
            "Check fiducial positions or remove the outlier."
        )
    if rms > max_rms_px:
        raise TransformError(
            f"RMS fiducial residual {rms:.2f} px exceeds limit {max_rms_px:.1f} px. "
            "Fiducial positions may be inaccurate or the design is distorted."
        )

    return DesignTransform(
        matrix=mat,
        residuals=fid_residuals,
        rms_error_px=round(rms, 3),
        max_error_px=round(max_err, 3),
    )


# ---------------------------------------------------------------------------
# Region generation from DesignComponents
# ---------------------------------------------------------------------------

def _load_package_table(table_path) -> dict:
    """Load package → {expected_class, w_mm, h_mm} table from a JSON file."""
    import json
    from pathlib import Path
    p = Path(table_path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text("utf-8"))


_DEFAULT_CLASS = "ic"
_DEFAULT_SIZE_MM = (3.0, 3.0)   # fallback when package unknown and no size column


def components_to_regions(
    components,
    transform: "DesignTransform",
    package_table: dict,
    *,
    side_filter: str = "F",
) -> list[dict]:
    """Convert DesignComponents to Region dicts (design mm → reference px).

    Parameters
    ----------
    components:
        Iterable of :class:`~app.design.parser.DesignComponent`.
    transform:
        A fitted :class:`DesignTransform`.
    package_table:
        Dict mapping package name (or prefix) → ``{expected_class, w_mm, h_mm}``.
    side_filter:
        Only include components on this board side ('F' or 'B').

    Returns a list of dicts suitable for :class:`~app.schemas.reference.Region`.
    """
    regions = []
    for comp in components:
        if comp.side.upper() != side_filter.upper():
            continue

        # Resolve package info
        pkg_info = _resolve_package(comp.package, package_table)
        expected_class = pkg_info["expected_class"]

        # Determine size in mm
        if comp.size_mm is not None:
            w_mm, h_mm = comp.size_mm
        else:
            w_mm = pkg_info.get("w_mm", _DEFAULT_SIZE_MM[0])
            h_mm = pkg_info.get("h_mm", _DEFAULT_SIZE_MM[1])
            if pkg_info.get("_is_fallback"):
                # Mark for caller so they can report unknown packages
                pass  # expected_class already flagged

        # Convert center + half-sizes to four corners in mm, then map to px
        cx_px, cy_px = transform.apply_mm(comp.pos_x_mm, comp.pos_y_mm)
        # Scale mm sizes to px using the transform's scale
        scale = transform.scale_mm_to_px()
        w_px = w_mm * scale
        h_px = h_mm * scale

        x = int(round(cx_px - w_px / 2))
        y = int(round(cy_px - h_px / 2))
        w = max(1, int(round(w_px)))
        h = max(1, int(round(h_px)))

        regions.append({
            "name": comp.ref_des,
            "bbox": (x, y, w, h),
            "expected_class": expected_class,
            "tolerances": {"check_rotation": expected_class != "led"},
            "_from_placement": True,
            "_package": comp.package,
            "_pkg_fallback": pkg_info.get("_is_fallback", False),
        })

    return regions


def _resolve_package(package: str, table: dict) -> dict:
    """Look up a package name in the table; fall back gracefully."""
    if not package:
        return {"expected_class": _DEFAULT_CLASS, "w_mm": _DEFAULT_SIZE_MM[0],
                "h_mm": _DEFAULT_SIZE_MM[1], "_is_fallback": True}

    # Exact match
    if package in table:
        return table[package]

    # Prefix match (e.g. "R_0402" matches key "R_")
    for key, val in table.items():
        if package.startswith(key):
            return val

    # Class-initial heuristic (R→resistor, C→capacitor, IC/U→ic, D→led, J→connector)
    first = package.upper().lstrip("0123456789")[0] if package.strip("0123456789") else "X"
    _heuristic = {
        "R": ("resistor", 2.0, 1.0),
        "C": ("cap",      2.0, 1.2),
        "U": ("ic",       8.0, 5.0),
        "Q": ("ic",       4.0, 4.0),
        "D": ("led",      2.5, 2.5),
        "J": ("connector", 10.0, 5.0),
        "L": ("ic",       3.0, 3.0),  # inductor → ic-like
    }
    if first in _heuristic:
        cls, wm, hm = _heuristic[first]
        return {"expected_class": cls, "w_mm": wm, "h_mm": hm, "_is_fallback": True}

    return {"expected_class": _DEFAULT_CLASS, "w_mm": _DEFAULT_SIZE_MM[0],
            "h_mm": _DEFAULT_SIZE_MM[1], "_is_fallback": True}
