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

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
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
# Package table (design name -> class + nominal size in mm)
# ---------------------------------------------------------------------------
#
# Size semantics: ``w_mm`` x ``h_mm`` is the nominal footprint extent of the part at
# design rotation 0 (its native orientation), in millimetres.  Boxes are converted
# to reference pixels through the fitted design transform, so they scale with the
# image.  Sizes in the shipped table are UNREVIEWED approximations (``_reviewed:
# false``) -- a real product needs its own calibrated values, or per-part size
# columns in the placement file (which take priority).


def _is_package_entry(v) -> bool:
    return isinstance(v, dict) and "expected_class" in v


def _flatten_package_table(raw: dict) -> dict:
    """Flat ``{package: {expected_class, w_mm, h_mm}}`` from the shipped format.

    Tolerates the legacy layout where entries sat inside a nested group (e.g.
    ``_SYNTHETIC_PCB_DEMO``), which made them unreachable.  Metadata keys
    (``_note``, ``_reviewed``, ...) are dropped.  Top-level entries win over nested.
    """
    flat: dict = {}
    for key, val in raw.items():
        if _is_package_entry(val):
            flat[key] = val
    for key, val in raw.items():
        if isinstance(val, dict) and not _is_package_entry(val):
            for k2, v2 in val.items():
                if _is_package_entry(v2):
                    flat.setdefault(k2, v2)
    return flat


def _load_package_table(table_path) -> dict:
    """Load + flatten the package table JSON.  Missing file -> ``{}``."""
    p = Path(table_path)
    if not p.is_file():
        return {}
    return _flatten_package_table(json.loads(p.read_text("utf-8")))


# KiCad footprint names are "Library:Footprint_with_suffixes".  These steps peel the
# suffixes in order and each intermediate form is tried against the table.
_NORMALISE_STEPS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"_\d{4}Metric.*$"), ""),                       # R_0603_1608Metric
    (re.compile(r"_\d+(?:\.\d+)?x\d+(?:\.\d+)?mm.*$"), ""),     # SOIC-8_3.9x4.9mm_P1.27mm
    (re.compile(r"_P\d+(?:\.\d+)?mm.*$"), ""),                  # PinHeader_1x06_P2.54mm_Vertical
    (re.compile(r"(_D\d+)\.\d+mm.*$"), r"\1"),                  # LED_D5.0mm -> LED_D5
    (re.compile(r"_(?:Vertical|Horizontal|SMD|THT)$", re.I), ""),
    (re.compile(r"^(?:LQ|TQ|L|T)(?=QFP-)"), ""),               # LQFP-48 -> QFP-48
]


def normalise_package_candidates(name: str) -> list[str]:
    """Candidate table keys for a footprint name, most specific first."""
    n = (name or "").strip()
    if ":" in n:
        n = n.rsplit(":", 1)[1]            # drop the KiCad library prefix
    cands = [n]
    cur = n
    for pat, repl in _NORMALISE_STEPS:
        nxt = pat.sub(repl, cur)
        if nxt and nxt != cur:
            cur = nxt
            cands.append(cur)
    seen: set[str] = set()
    return [c for c in cands if c and not (c in seen or seen.add(c))]


_DEFAULT_CLASS = "ic"
_DEFAULT_SIZE_MM = (3.0, 3.0)   # fallback when package unknown and no size column

# Class-initial heuristic for packages the table does not know (flagged fallback).
_HEURISTIC = {
    "R": ("resistor", 2.0, 1.0),
    "C": ("cap",      2.0, 1.2),
    "U": ("ic",       8.0, 5.0),
    "Q": ("ic",       4.0, 4.0),
    "D": ("led",      2.5, 2.5),
    "J": ("connector", 10.0, 5.0),
    "L": ("ic",       3.0, 3.0),  # inductor -> ic-like
}


def _resolve_package(package: str, table: dict) -> dict:
    """Look up a package in the table; fall back gracefully (flagged ``_is_fallback``).

    Order: exact -> normalised KiCad name -> longest key that prefixes the name ->
    first-letter heuristic -> generic default.  The matched table key is returned
    as ``_key`` when one was found.
    """
    if not package:
        return {"expected_class": _DEFAULT_CLASS, "w_mm": _DEFAULT_SIZE_MM[0],
                "h_mm": _DEFAULT_SIZE_MM[1], "_is_fallback": True}

    lower = {k.lower(): k for k, v in table.items() if _is_package_entry(v)}
    cands = normalise_package_candidates(package)
    for c in cands:
        key = c if c in table and _is_package_entry(table[c]) else lower.get(c.lower())
        if key is not None:
            return {**table[key], "_key": key}

    # longest table key that prefixes the (normalised) name, on a token boundary
    base = cands[-1]
    for key in sorted(lower.values(), key=len, reverse=True):
        if base.lower().startswith(key.lower()):
            rest = base[len(key):len(key) + 1]
            if rest == "" or not rest.isalnum():
                return {**table[key], "_key": key}

    first = package.split(":")[-1].upper().lstrip("0123456789")[:1] or "X"
    if first in _HEURISTIC:
        cls, wm, hm = _HEURISTIC[first]
        return {"expected_class": cls, "w_mm": wm, "h_mm": hm, "_is_fallback": True}
    return {"expected_class": _DEFAULT_CLASS, "w_mm": _DEFAULT_SIZE_MM[0],
            "h_mm": _DEFAULT_SIZE_MM[1], "_is_fallback": True}


# ---------------------------------------------------------------------------
# Region generation from DesignComponents
# ---------------------------------------------------------------------------
#
# Angle convention (Region.expected_rotation_deg)
# -----------------------------------------------
# The placement file's rotation is counter-clockwise-positive in the board's own
# plane (KiCad convention, viewed from the top).  ``Region.expected_rotation_deg``
# is the same angle expressed on the REFERENCE IMAGE AS SEEN ON SCREEN: counter-
# clockwise positive, 0 = the package's native orientation (the OpenCV
# ``getRotationMatrix2D`` convention that the synthetic generator uses).
#
# The two agree when the design->image mapping flips handedness (y-up CAD ->
# y-down image, det < 0) and are opposite when it does not (det > 0, e.g. a file
# whose Y already grows downward, like the synthetic fixtures).  Hence
#     expected_rotation_deg = +rot_file  if det(A) < 0  else  -rot_file
# UNVERIFIED on real boards / a real KiCad export.


def screen_rotation_deg(rot_file_deg: float, transform: "DesignTransform") -> float:
    """File rotation (CCW, board plane) -> CCW-positive on-screen angle, in (-180, 180]."""
    det = float(np.linalg.det(transform.matrix[:, :2]))
    a = rot_file_deg if det < 0 else -rot_file_deg
    a = (a + 180.0) % 360.0 - 180.0
    if a == -180.0:
        a = 180.0
    return round(a + 0.0, 4)


def _box_px(
    transform: "DesignTransform", cx_mm: float, cy_mm: float,
    w_mm: float, h_mm: float, rot_file_deg: float,
) -> tuple[int, int, int, int]:
    """Axis-aligned pixel box of a (w x h) mm rectangle rotated about its centre.

    All four corners go through the full affine, so anisotropic scale, board
    rotation and flips are handled exactly (the old code used one isotropic
    scale and ignored the part's own rotation).
    """
    th = math.radians(rot_file_deg)
    c, s = math.cos(th), math.sin(th)
    xs: list[float] = []
    ys: list[float] = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, ly = sx * w_mm / 2.0, sy * h_mm / 2.0
        px, py = transform.apply_mm(cx_mm + lx * c - ly * s, cy_mm + lx * s + ly * c)
        xs.append(px)
        ys.append(py)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return (int(round(x0)), int(round(y0)),
            max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0))))


def components_to_regions(
    components,
    transform: "DesignTransform",
    package_table: dict,
    *,
    side_filter: str = "F",
) -> list[dict]:
    """Convert DesignComponents to Region dicts (design mm -> reference px).

    Size priority (D1): (A) ``comp.size_mm`` from the placement file, else (B) the
    package table (after KiCad-name normalisation), else a flagged fallback.  Both
    are in mm at rotation 0 and are mapped to pixels through ``transform``.

    Extra keys (underscore-prefixed) record provenance: ``_size_source``
    (``file``/``table``/``fallback``), ``_size_mm``, ``_package``, ``_pkg_fallback``.
    """
    regions = []
    for comp in components:
        if comp.side.upper() != side_filter.upper():
            continue

        pkg_info = _resolve_package(comp.package, package_table)
        expected_class = pkg_info["expected_class"]

        if comp.size_mm is not None:
            w_mm, h_mm = comp.size_mm
            size_source = "file"
        else:
            w_mm = pkg_info.get("w_mm", _DEFAULT_SIZE_MM[0])
            h_mm = pkg_info.get("h_mm", _DEFAULT_SIZE_MM[1])
            size_source = "fallback" if pkg_info.get("_is_fallback") else "table"

        x, y, w, h = _box_px(transform, comp.pos_x_mm, comp.pos_y_mm, w_mm, h_mm,
                             comp.rotation_deg)

        regions.append({
            "name": comp.ref_des,
            "bbox": (x, y, w, h),
            "expected_class": expected_class,
            "expected_rotation_deg": screen_rotation_deg(comp.rotation_deg, transform),
            "tolerances": {"check_rotation": expected_class != "led"},
            "_from_placement": True,
            "_package": comp.package,
            "_pkg_fallback": pkg_info.get("_is_fallback", False),
            "_size_source": size_source,
            "_size_mm": (round(float(w_mm), 4), round(float(h_mm), 4)),
        })

    return regions


def regions_from_dicts(raw_regions: list[dict]) -> list:
    """Build :class:`~app.schemas.reference.Region` objects from the dicts above."""
    from ..schemas.reference import Region, RegionTolerances

    return [
        Region(
            name=r["name"],
            bbox=r["bbox"],
            expected_class=r["expected_class"],
            expected_rotation_deg=r.get("expected_rotation_deg"),
            tolerances=RegionTolerances(
                check_rotation=r["tolerances"].get("check_rotation", True)
            ),
        )
        for r in raw_regions
    ]
