"""Phase 4B step 1 (D6) — harness ``localization_match`` must enforce bbox IoU.

Before the fix a trailing ``return True`` made ANY ref-des + type match count as a
localization hit even when the expected box was nowhere near the defect.  The
"far away" test below FAILS on the old code.  Hand-built objects only (no imaging).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.schemas.defect import Defect, DefectType
from harness import localization_match


def _result(*defects):
    return SimpleNamespace(defects=list(defects))


def _defect(component="C4", dtype=DefectType.MISSING_COMPONENT, bbox=(100, 100, 150, 150)):
    # Defect.bbox is x1,y1,x2,y2 in aligned-image pixels.
    return Defect(component=component, type=dtype, confidence=0.9, bbox=bbox)


def _exp(bbox, ref="C4", dtype="MISSING_COMPONENT"):
    # manifest bbox is x,y,w,h in the same pixel space.
    return {"ref_des": ref, "type": dtype, "bbox": bbox}


def test_far_away_bbox_does_not_match():
    """IoU = 0 -> not a localization hit.  (FAILS on the pre-fix harness.)"""
    assert localization_match(_exp([600, 400, 50, 50]), _result(_defect())) is False


def test_overlapping_bbox_matches():
    # expected [100,100,50,50] == defect (100,100)-(150,150): IoU 1.0
    assert localization_match(_exp([100, 100, 50, 50]), _result(_defect())) is True


def test_partial_overlap_below_threshold_does_not_match():
    # expected box shifted so IoU is well under the 0.3 default threshold
    assert localization_match(_exp([140, 140, 50, 50]), _result(_defect())) is False


def test_partial_overlap_above_threshold_matches():
    # 10px offset on 50px boxes -> IoU = 1600 / 3400 = 0.47 >= 0.3
    assert localization_match(_exp([110, 110, 50, 50]), _result(_defect())) is True


def test_threshold_is_respected():
    r = _result(_defect())
    assert localization_match(_exp([110, 110, 50, 50]), r, iou_threshold=0.9) is False


def test_no_expected_bbox_matches_on_refdes_and_type():
    assert localization_match({"ref_des": "C4", "type": "MISSING_COMPONENT", "bbox": None},
                              _result(_defect(bbox=(0, 0, 5, 5)))) is True


def test_wrong_type_or_refdes_never_matches():
    r = _result(_defect())
    assert localization_match(_exp([100, 100, 50, 50], dtype="SHIFTED_COMPONENT"), r) is False
    assert localization_match(_exp([100, 100, 50, 50], ref="R1"), r) is False


def test_defect_without_bbox_falls_back_to_refdes_and_type():
    """Documented fallback: a defect that carries no bbox cannot be compared."""
    d = Defect(component="C4", type=DefectType.MISSING_COMPONENT, confidence=0.9, bbox=None)
    assert localization_match(_exp([600, 400, 50, 50]), _result(d)) is True


def test_a_later_overlapping_defect_still_matches():
    """First same-type defect is far away, a second one overlaps -> hit."""
    far = _defect(bbox=(700, 500, 750, 550))
    near = _defect(bbox=(100, 100, 150, 150))
    assert localization_match(_exp([100, 100, 50, 50]), _result(far, near)) is True
