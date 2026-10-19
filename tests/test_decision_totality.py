"""Phase 4B step 1 (D6) — the decision engine must be TOTAL.

Every ``DefectType`` must map to DEFECTIVE or REVIEW, never GOOD; an unknown type
defaults to REVIEW; severity can never change the verdict.

These tests were written first and run against the pre-fix engine: the totality /
unknown-type / alias tests FAIL there (``decide()`` returned GOOD for a defect list
containing only UNEXPECTED_COMPONENT or SIZE_DEVIATION).  All inputs are hand-built
objects — no imaging — so nothing here is a synthetic-image result.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.inspection.decision_engine import decide
from app.inspection.severity import apply_severity
from app.schemas.defect import Defect, DefectType
from app.schemas.inspection import (
    AlignmentMetadata,
    InspectionStatus,
    QualityReport,
    ReasonCode,
    RegionMeasurement,
)

CFG = Settings()
OK_ALIGN = AlignmentMetadata(success=True, alignment_error_px=0.5, inliers=200)
OK_QUALITY = QualityReport(passed=True, blur_score=200, brightness=120, contrast=45,
                           overexposed_frac=0.0)

# Verdicts that existed BEFORE Phase 4B and must not change.
FROZEN_DEFECTIVE = [
    DefectType.MISSING_COMPONENT,
    DefectType.SHIFTED_COMPONENT,
    DefectType.ROTATED_COMPONENT,
    DefectType.WRONG_COMPONENT,
    DefectType.VISUAL_ANOMALY,
]
REVIEW_ONLY = [DefectType.SIZE_DEVIATION, DefectType.UNEXPECTED_COMPONENT]


def _m(name: str, ssim: float = 0.95) -> RegionMeasurement:
    return RegionMeasurement(name=name, found=True, peak_corr=0.9, offset_px=1.0,
                             rotation_deg=0.5, ssim=ssim, expected_center=(10, 10),
                             observed_center=(10, 10), bbox=(0, 0, 20, 20))


def _defect(dtype, component: str = "X1") -> Defect:
    return Defect(component=component, type=dtype, confidence=0.9,
                  expected="expected", observed="observed")


def _decide(defects):
    return decide(OK_ALIGN, OK_QUALITY, [_m("X1")], defects, CFG)


# --------------------------------------------------------------------------- totality
@pytest.mark.parametrize("dtype", list(DefectType), ids=lambda t: t.value)
def test_every_defect_type_is_never_good(dtype):
    """The core invariant: a defect list is never reported GOOD, for ANY type."""
    out = _decide([_defect(dtype)])
    assert out.status in (InspectionStatus.DEFECTIVE, InspectionStatus.REVIEW)
    assert out.status != InspectionStatus.GOOD


def test_unknown_defect_type_defaults_to_review():
    """A type this engine has never heard of must go to a human, not to GOOD."""
    # model_construct bypasses enum validation to simulate a future/unknown type.
    ghost = Defect.model_construct(
        component="X1", type="FUTURE_DEFECT_TYPE", confidence=0.9,
        expected="e", observed="o",
    )
    out = _decide([ghost])
    assert out.status == InspectionStatus.REVIEW


@pytest.mark.parametrize("dtype", FROZEN_DEFECTIVE, ids=lambda t: t.value)
def test_existing_defective_verdicts_are_unchanged(dtype):
    out = _decide([_defect(dtype)])
    assert out.status == InspectionStatus.DEFECTIVE


@pytest.mark.parametrize("dtype", REVIEW_ONLY, ids=lambda t: t.value)
def test_review_only_types_give_review_with_a_reason(dtype):
    out = _decide([_defect(dtype)])
    assert out.status == InspectionStatus.REVIEW
    assert out.reason == ReasonCode.SUSPECTED_DEFECT
    assert "X1" in out.explanation  # the reason names the component


def test_confirmed_defect_still_beats_review_only_defect():
    """DEFECTIVE is never downgraded to REVIEW by an extra review-only finding."""
    out = _decide([_defect(DefectType.MISSING_COMPONENT, "X1"),
                   _defect(DefectType.UNEXPECTED_COMPONENT, "EXTRA1")])
    assert out.status == InspectionStatus.DEFECTIVE
    assert out.reason == ReasonCode.CRITICAL_DEFECT


def test_no_defects_is_still_good():
    """Backward compat: a clean board is still GOOD."""
    out = _decide([])
    assert out.status == InspectionStatus.GOOD and out.reason == ReasonCode.OK


# --------------------------------------------------------------------------- alias
def test_critical_defects_alias_is_kept():
    """Old imports keep working: CRITICAL_DEFECTS is an alias of VERDICT_BLOCKING."""
    from app.schemas.defect import CRITICAL_DEFECTS, VERDICT_BLOCKING

    assert CRITICAL_DEFECTS == VERDICT_BLOCKING
    assert DefectType.MISSING_COMPONENT in VERDICT_BLOCKING
    # size / unexpected are deliberately NOT verdict-blocking on their own
    assert DefectType.SIZE_DEVIATION not in VERDICT_BLOCKING
    assert DefectType.UNEXPECTED_COMPONENT not in VERDICT_BLOCKING


# --------------------------------------------------------------------------- severity
@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
def test_severity_never_changes_status(tmp_path, level):
    """Same defects, severity table forced to any level -> identical status/reason.

    (This guards the invariant; on the old engine it passed trivially for the two
    types that fell through to GOOD, so it is NOT a failing-before test.)
    """
    policy = {"_reviewed": False}
    for t in DefectType:
        policy[t.value] = {"severity": level, "corrective_action": "x {component}"}
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(policy), "utf-8")

    for dtype in DefectType:
        raw = [_defect(dtype)]
        with_sev = apply_severity(raw, p)
        assert with_sev[0].severity == level            # severity really was applied
        a, b = _decide(raw), _decide(with_sev)
        assert (a.status, a.reason) == (b.status, b.reason), dtype


def test_status_depends_on_type_not_on_severity_field():
    """Strip / randomise the severity field on the same defect -> same verdict."""
    base = _defect(DefectType.SIZE_DEVIATION)
    for sev in (None, "LOW", "CRITICAL"):
        out = _decide([base.model_copy(update={"severity": sev})])
        assert out.status == InspectionStatus.REVIEW
