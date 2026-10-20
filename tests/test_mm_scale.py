"""Phase 4B step 2 (D2) — millimetres from the placement transform.

Unit tests use hand-built matrices (no imaging).  Pipeline tests run the real engine
on SYNTHETIC boards ([SYNTHETIC] = computer-generated, smoke test only, not a real
benchmark).

Written before the pipeline wiring: the tests marked "(fails on old code)" failed
against the pre-D2 pipeline.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from app.design.scale import DISAGREE_TOLERANCE, MmScale, px_per_mm, resolve_mm_scale
from app.inspection.frame import frame_scales, scale_regions
from app.schemas.reference import Region, RegionTolerances

# 8.3333 px/mm  <=>  0.12 mm/px  (the synthetic board)
M_ISO = [[8.333333, 0.0, 0.0], [0.0, 8.333333, 0.0]]


class _Ref:
    def __init__(self, mm_per_px=None, placement_transform=None):
        self.mm_per_px = mm_per_px
        self.placement_transform = placement_transform


# ----------------------------------------------------------------------- unit: scale
def test_px_per_mm_is_inverted_to_get_mm_per_px():
    """scale_mm_to_px() is px/mm; mm/px is its INVERSE (the trap flagged in Phase 4A)."""
    assert px_per_mm(M_ISO) == pytest.approx(8.333333, rel=1e-6)
    s = resolve_mm_scale(_Ref(placement_transform=M_ISO))
    assert s.source == "transform"
    assert s.mm_per_px == pytest.approx(0.12, rel=1e-5)           # NOT 8.33
    assert s.offset_mm(10, 0) == pytest.approx(1.2, abs=1e-3)


def test_transform_without_scalar_gives_mm():
    s = resolve_mm_scale(_Ref(mm_per_px=None, placement_transform=M_ISO))
    assert s.known and s.source == "transform" and s.warning is None


def test_anisotropic_transform_is_exact_per_axis():
    # 10 px/mm in x, 5 px/mm in y: an isotropic average would be wrong on both axes
    s = resolve_mm_scale(_Ref(placement_transform=[[10.0, 0, 0], [0, 5.0, 0]]))
    assert s.offset_mm(10, 0) == pytest.approx(1.0, abs=1e-3)
    assert s.offset_mm(0, 5) == pytest.approx(1.0, abs=1e-3)
    assert s.offset_mm(10, 5) == pytest.approx(math.sqrt(2), abs=1e-3)


def test_rotated_and_flipped_transform_preserves_length():
    th = math.radians(30)
    c, s_ = math.cos(th) * 8.0, math.sin(th) * 8.0
    rot = [[c, -s_, 5.0], [s_, c, 7.0]]
    flip = [[8.0, 0, 0], [0, -8.0, 100.0]]           # y-up CAD -> y-down image
    for m in (rot, flip):
        sc = resolve_mm_scale(_Ref(placement_transform=m))
        assert sc.offset_mm(8, 0) == pytest.approx(1.0, abs=1e-3)
        assert sc.offset_mm(0, 8) == pytest.approx(1.0, abs=1e-3)


def test_point_mm_round_trips_the_transform():
    m = np.array([[8.0, 0.5, 20.0], [-0.3, 7.5, 40.0]])
    s = resolve_mm_scale(_Ref(placement_transform=m.tolist()))
    design = np.array([12.5, 33.0])
    px = m[:, :2] @ design + m[:, 2]
    got = s.point_mm(px[0], px[1])
    assert got == pytest.approx((12.5, 33.0), abs=1e-2)


def test_disagreement_over_5_percent_warns_and_transform_wins():
    s = resolve_mm_scale(_Ref(mm_per_px=0.20, placement_transform=M_ISO))
    assert s.source == "transform"
    assert s.mm_per_px == pytest.approx(0.12, rel=1e-5)
    assert s.warning and "differ" in s.warning and "0.2000" in s.warning


@pytest.mark.parametrize("scalar,warns", [
    (0.12, False),
    (0.12 * 1.02, False),            # 2 % apart
    (0.12 / 1.05, False),            # exactly 5 % of the scalar: not "more than"
    (0.12 * 1.10, True),             # ~9 % apart
    (0.12 * 0.80, True),
])
def test_five_percent_boundary(scalar, warns):
    s = resolve_mm_scale(_Ref(mm_per_px=scalar, placement_transform=M_ISO))
    assert (s.warning is not None) == warns
    assert DISAGREE_TOLERANCE == 0.05


def test_scalar_only_is_unchanged_behaviour():
    s = resolve_mm_scale(_Ref(mm_per_px=0.12))
    assert s.source == "scalar" and s.mm_per_px == pytest.approx(0.12)
    assert s.offset_mm(30, 40) == pytest.approx(50 * 0.12, abs=1e-3)   # px * scalar
    assert s.point_mm(1, 1) is None                                  # a scalar has no origin


def test_config_fallback_and_none():
    assert resolve_mm_scale(_Ref(), config_mm_per_px=0.2).source == "config"
    none = resolve_mm_scale(_Ref(), config_mm_per_px=0.0)
    assert none.source == "none" and none.offset_mm(10, 10) is None and not none.known


@pytest.mark.parametrize("bad", [
    [[0, 0, 0], [0, 0, 0]],                 # singular
    [[1, 2, 3]],                            # wrong shape
    [[float("nan"), 0, 0], [0, 1, 0]],      # non-finite
    [[1, 1, 0], [1, 1, 0]],                 # collinear (det 0)
])
def test_invalid_transform_falls_back_with_warning(bad):
    s = resolve_mm_scale(_Ref(mm_per_px=0.12, placement_transform=bad))
    assert s.source == "scalar" and s.warning and "invalid" in s.warning


def test_frame_scale_converts_working_pixels():
    """Reference shrunk to half size => one WORKING px is two ORIGINAL px."""
    full = resolve_mm_scale(_Ref(mm_per_px=0.12))
    half = resolve_mm_scale(_Ref(mm_per_px=0.12), frame_scale=0.5)
    assert half.mm_per_px == pytest.approx(2 * full.mm_per_px)
    t = resolve_mm_scale(_Ref(placement_transform=M_ISO), frame_scale=0.5)
    assert t.offset_mm(10, 0) == pytest.approx(2.4, abs=1e-3)       # 20 original px * 0.12


# ----------------------------------------------------------------------- unit: frame
def test_scale_regions_is_identity_when_reference_fits():
    regs = [Region(name="R1", bbox=(10, 20, 30, 40))]
    assert scale_regions(regs, 1.0, 1.0) is regs


def test_scale_regions_scales_geometry_and_pixel_tolerances_only():
    regs = [Region(name="R1", bbox=(100, 200, 40, 20),
                   tolerances=RegionTolerances(position_tol_px=12.0, search_margin_px=40,
                                               missing_corr=0.5, rotation_tol_deg=7.0))]
    out = scale_regions(regs, 0.5, 0.5)[0]
    assert out.bbox == (50, 100, 20, 10)
    assert out.tolerances.position_tol_px == pytest.approx(6.0)
    assert out.tolerances.search_margin_px == 20
    # non-pixel thresholds untouched
    assert out.tolerances.missing_corr == 0.5 and out.tolerances.rotation_tol_deg == 7.0
    assert regs[0].bbox == (100, 200, 40, 20)                       # input not mutated


def test_frame_scales_helper():
    assert frame_scales((1280, 1800), (910, 1280)) == pytest.approx((1280 / 1800, 910 / 1280))


# ------------------------------------------------------------ pipeline [SYNTHETIC]
def _make_reference(env, gen, ref_id, k, *, scalar, transform):
    from app.schemas.reference import ProfileKind, ReferenceCreate
    from app.storage.reference_repository import ReferenceRepository

    regs = []
    for r in gen.regions():
        x, y, w, h = r.bbox
        t = r.tolerances.model_copy()
        if t.position_tol_px is not None:
            t.position_tol_px *= k
        regs.append(Region(name=r.name, bbox=(round(x * k), round(y * k), round(w * k), round(h * k)),
                           expected_class=r.expected_class, tolerances=t))
    img = gen.render_board(seed=1)
    if k != 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    repo = ReferenceRepository(env)
    repo.create(ReferenceCreate(id=ref_id, name=ref_id, kind=ProfileKind.PCB,
                                regions=regs, mm_per_px=scalar), img)
    if transform:
        # px = mm * (8.3333 * k)  <=>  mm_per_px = 0.12 / k
        s = 8.333333 * k
        repo.set_placement_transform(ref_id, [[s, 0.0, 0.0], [0.0, s, 0.0]])
    return repo.load_profile(ref_id)


def _photo(gen, k, **bkw):
    flat = gen.render_board(**bkw)
    if k != 1:
        flat = cv2.resize(flat, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    return gen.photograph(flat, seed=101, angle=6, out_long=2400 if k > 1 else 1280)


def _shifted_c4(profile, env, gen, k, shift=(22, 14)):
    from app.inspection.pipeline import run_inspection

    res = run_inspection(_photo(gen, k, seed=4, shift={"C4": shift}), profile, env)
    d = [x for x in res.defects if x.component == "C4" and x.deviation_px is not None]
    assert d, f"C4 shift not found; defects={[(x.component, x.type.value) for x in res.defects]}"
    return res, d[0]


def test_pipeline_reports_mm_from_transform_when_reference_has_no_scalar(reference):
    """(fails on old code) transform present, mm_per_px absent -> deviation_mm filled."""
    env, gen = reference
    profile = _make_reference(env, gen, "T_ONLY", 1, scalar=None, transform=True)
    res, d = _shifted_c4(profile, env, gen, 1)
    assert d.deviation_mm is not None
    assert d.deviation_mm == pytest.approx(d.deviation_px * 0.12, rel=0.02)
    assert res.mm_scale_source == "transform"
    assert res.mm_per_px == pytest.approx(0.12, rel=1e-3)
    assert res.warnings == []


def test_pipeline_scalar_only_still_works_and_is_labelled(reference):
    env, gen = reference
    profile = _make_reference(env, gen, "S_ONLY", 1, scalar=0.12, transform=False)
    res, d = _shifted_c4(profile, env, gen, 1)
    assert d.deviation_mm == pytest.approx(d.deviation_px * 0.12, rel=0.02)
    assert res.mm_scale_source == "scalar"


def test_pipeline_records_warning_when_transform_and_scalar_disagree(reference):
    env, gen = reference
    profile = _make_reference(env, gen, "BOTH_BAD", 1, scalar=0.30, transform=True)
    res, d = _shifted_c4(profile, env, gen, 1)
    assert res.mm_scale_source == "transform"
    assert res.warnings and "differ" in res.warnings[0]
    assert d.deviation_mm == pytest.approx(d.deviation_px * 0.12, rel=0.02)   # transform won


def test_no_mm_scale_means_no_mm_values(reference, monkeypatch):
    env, gen = reference
    profile = _make_reference(env, gen, "NO_MM", 1, scalar=None, transform=False)
    res, d = _shifted_c4(profile, env, gen, 1)
    assert d.deviation_mm is None and res.mm_scale_source in (None, "none")


def test_large_reference_good_board_is_good(reference):
    """(fails on old code) A reference > image_max_dim (1280 px) must not break regions.

    [SYNTHETIC] the 900x640 board scaled 2x -> 1800x1280.  Before the fix a GOOD
    board read DEFECTIVE with spurious MISSING/SHIFTED/ROTATED defects.
    """
    from app.inspection.pipeline import run_inspection

    env, gen = reference
    profile = _make_reference(env, gen, "BIG", 2, scalar=0.06, transform=True)
    assert profile.load_reference_image().shape[1] > env.image_max_dim
    res = run_inspection(_photo(gen, 2, seed=2), profile, env)
    assert res.status.value == "GOOD", [(d.component, d.type.value) for d in res.defects]


def test_mm_is_consistent_between_normal_and_large_reference(reference):
    """(fails on old code) The same physical shift must read about the same mm whether
    the reference is 900 px or 1800 px wide (image_max_dim resize risk).

    The shift is kept INSIDE the template search window (region_search_margin_px = 18
    working px; a larger shift is under-measured by the existing matcher, which is a
    detector limit and not a unit-conversion error).  (10, 6) base px = 1.40 mm: 11.7
    working px at 1x and 16.6 working px at 2x, both above the 8 px position tolerance
    and below the 18 px window.
    """
    env, gen = reference
    small = _make_reference(env, gen, "SMALL", 1, scalar=None, transform=True)
    big = _make_reference(env, gen, "BIG2", 2, scalar=None, transform=True)
    _, d1 = _shifted_c4(small, env, gen, 1, shift=(10, 6))
    _, d2 = _shifted_c4(big, env, gen, 2, shift=(10, 6))
    true_mm = math.hypot(10, 6) * 0.12               # 1.40 mm
    assert d1.deviation_mm == pytest.approx(true_mm, abs=0.35)
    assert d2.deviation_mm == pytest.approx(true_mm, abs=0.35)
    assert d2.deviation_mm == pytest.approx(d1.deviation_mm, rel=0.25)


def test_mm_matches_measured_pixels_through_the_frame_scale(reference):
    """The conversion itself is exact: deviation_mm == measured px / frame_scale / (px per mm)."""
    env, gen = reference
    big = _make_reference(env, gen, "BIG3", 2, scalar=None, transform=True)
    res, d = _shifted_c4(big, env, gen, 2, shift=(10, 6))
    ref_orig_w = big.load_reference_image().shape[1]            # 1800
    frame = min(1.0, env.image_max_dim / max(big.load_reference_image().shape[:2]))
    assert frame < 1.0 and ref_orig_w > env.image_max_dim
    m = next(x for x in res.regions if x.name == "C4")
    dx = m.observed_center[0] - m.expected_center[0]
    dy = m.observed_center[1] - m.expected_center[1]
    expected = math.hypot(dx, dy) / frame / (8.333333 * 2)      # px/mm = 8.3333 * k
    assert d.deviation_mm == pytest.approx(expected, abs=2e-3)
