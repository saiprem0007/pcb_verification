"""Working-frame geometry (Phase 4B, D2).

Regions are stored in ORIGINAL reference-image pixels, but the pipeline compares the
reference *after* ``resize_max(image_max_dim)`` (default 1280).  For a reference
whose longest side is larger than that, the unscaled regions pointed at the wrong
pixels (measured: a good board read DEFECTIVE with spurious defects).  These helpers
scale region geometry into the working frame.  This changes GEOMETRY only — no
detection threshold is touched, and it is an exact no-op when the reference already
fits (``frame_scale == 1``).
"""

from __future__ import annotations

from ..schemas.reference import Region


def frame_scales(original_hw: tuple[int, int], working_hw: tuple[int, int]) -> tuple[float, float]:
    """(sx, sy) = working / original for width and height."""
    oh, ow = original_hw
    wh, ww = working_hw
    return (ww / ow if ow else 1.0, wh / oh if oh else 1.0)


def scale_regions(regions: list[Region], sx: float, sy: float) -> list[Region]:
    """Return regions expressed in the working frame.  Same objects when sx=sy=1."""
    if sx == 1.0 and sy == 1.0:
        return regions
    f = (sx + sy) / 2.0
    out: list[Region] = []
    for r in regions:
        x, y, w, h = r.bbox
        bbox = (
            int(round(x * sx)),
            int(round(y * sy)),
            max(1, int(round(w * sx))),
            max(1, int(round(h * sy))),
        )
        tol = r.tolerances.model_copy()
        # per-region pixel tolerances are defined in original reference pixels
        if tol.position_tol_px is not None:
            tol.position_tol_px = tol.position_tol_px * f
        if tol.search_margin_px is not None:
            tol.search_margin_px = max(1, int(round(tol.search_margin_px * f)))
        out.append(r.model_copy(update={"bbox": bbox, "tolerances": tol}))
    return out
