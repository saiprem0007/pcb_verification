"""Millimetre scale for reporting deviations (Phase 4B, D2).

Where does "mm" come from?  In priority order:

1. ``reference.placement_transform`` — the fiducial-fitted affine (design mm →
   reference px).  Exact for translation, rotation, anisotropic scale and shear.
2. ``reference.mm_per_px`` — a hand-entered scalar.
3. ``Settings.mm_per_px`` — a global fallback (``AOI_MM_PER_PX``).
4. none — mm fields stay ``None`` (never invented).

Units, because this is easy to get backwards
--------------------------------------------
``DesignTransform.scale_mm_to_px()`` returns **pixels per millimetre**.  A
millimetre-per-pixel value is its **inverse** (``1 / scale``).  For a displacement
vector this module does not use the isotropic approximation at all: it maps the
pixel vector through the inverse of the transform's 2×2 linear part, which is exact
for any affine.

Frames
------
Regions and the stored transform live in ORIGINAL reference-image pixels.  The
pipeline works on a reference downscaled to ``image_max_dim`` (default 1280), i.e.
a *working frame*, ``frame_scale = working_px / original_px`` (≤ 1).  All
displacement vectors handed to this module are in WORKING-frame pixels and are
divided by ``frame_scale`` before use.  The global ``Settings.mm_per_px`` fallback
has no known frame and is applied to working pixels exactly as before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Disagreement between the transform-derived and the hand-entered scalar scale
# above which a warning is recorded (relative to the scalar).
DISAGREE_TOLERANCE = 0.05


@dataclass
class MmScale:
    """A resolved px→mm conversion for one inspection (working-frame pixels)."""

    source: str = "none"                     # "transform" | "scalar" | "config" | "none"
    mm_per_px: float = 0.0                   # isotropic, per WORKING px; 0.0 = unknown
    warning: str | None = None
    # transform path only:
    matrix: np.ndarray | None = field(default=None, repr=False)  # 2x3 design mm -> ORIGINAL ref px
    frame_scale: float = 1.0                 # working px / original px

    # ------------------------------------------------------------------ queries
    @property
    def known(self) -> bool:
        return self.source != "none" and self.mm_per_px > 0

    def offset_mm(self, dx_px: float, dy_px: float) -> float | None:
        """Length in mm of a displacement given in WORKING-frame pixels."""
        if not self.known:
            return None
        if self.matrix is not None:
            v = self._design_vector(dx_px, dy_px)
            return round(math.hypot(v[0], v[1]), 3)
        return round(math.hypot(dx_px, dy_px) * self.mm_per_px, 3)

    def distance_mm(self, length_px: float) -> float | None:
        """mm length of a pixel length (isotropic; used for sizes, e.g. width)."""
        if not self.known:
            return None
        return round(length_px * self.mm_per_px, 3)

    def point_mm(self, x_px: float, y_px: float) -> tuple[float, float] | None:
        """Absolute design-space (mm) position of a WORKING-frame pixel.

        Only meaningful with a transform (a bare scalar has no origin) -> else None.
        """
        if self.matrix is None or not self.known:
            return None
        a = self.matrix[:, :2]
        t = self.matrix[:, 2]
        p = np.array([x_px, y_px], dtype=np.float64) / self.frame_scale
        mm = np.linalg.solve(a, p - t)
        return (round(float(mm[0]), 3), round(float(mm[1]), 3))

    def _design_vector(self, dx_px: float, dy_px: float) -> np.ndarray:
        a = self.matrix[:, :2]
        v = np.array([dx_px, dy_px], dtype=np.float64) / self.frame_scale
        return np.linalg.solve(a, v)


def px_per_mm(matrix) -> float:
    """Geometric-mean pixels-per-millimetre of a 2x3 affine (matches
    ``DesignTransform.scale_mm_to_px``).  0.0 when degenerate."""
    m = np.asarray(matrix, dtype=np.float64)
    sx = math.hypot(m[0, 0], m[1, 0])
    sy = math.hypot(m[0, 1], m[1, 1])
    return math.sqrt(sx * sy) if sx > 0 and sy > 0 else 0.0


def _valid_matrix(matrix) -> np.ndarray | None:
    """Return the matrix as float64 2x3 if it is finite and invertible, else None."""
    try:
        m = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if m.shape != (2, 3) or not np.all(np.isfinite(m)):
        return None
    det = m[0, 0] * m[1, 1] - m[0, 1] * m[1, 0]
    if abs(det) < 1e-9 or px_per_mm(m) <= 0:
        return None
    return m


def resolve_mm_scale(reference, config_mm_per_px: float = 0.0,
                     frame_scale: float = 1.0) -> MmScale:
    """Pick the mm source for one inspection.  Never raises."""
    f = frame_scale if frame_scale and frame_scale > 0 else 1.0
    scalar = reference.mm_per_px if getattr(reference, "mm_per_px", None) else None
    raw = getattr(reference, "placement_transform", None)
    warning: str | None = None

    if raw:
        m = _valid_matrix(raw)
        if m is None:
            warning = ("placement_transform is invalid (not a finite, invertible 2x3 "
                       "matrix); ignored for mm reporting")
        else:
            mm_orig = 1.0 / px_per_mm(m)            # mm per ORIGINAL ref px (inverse!)
            if scalar:
                # round away float noise (1e-6 = 0.0001 %) so "exactly 5 %" is not "more than 5 %"
                rel = round(abs(mm_orig - scalar) / scalar, 6)
                if rel > DISAGREE_TOLERANCE:
                    warning = (
                        f"mm scale from the placement transform ({mm_orig:.4f} mm/px) and "
                        f"the reference mm_per_px ({scalar:.4f} mm/px) differ by "
                        f"{rel * 100:.1f}% (> {DISAGREE_TOLERANCE * 100:.0f}%); "
                        "using the transform"
                    )
            return MmScale(source="transform", mm_per_px=mm_orig / f, warning=warning,
                           matrix=m, frame_scale=f)

    if scalar:
        return MmScale(source="scalar", mm_per_px=float(scalar) / f, warning=warning)
    if config_mm_per_px and config_mm_per_px > 0:
        return MmScale(source="config", mm_per_px=float(config_mm_per_px), warning=warning)
    return MmScale(source="none", mm_per_px=0.0, warning=warning)
