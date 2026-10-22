"""Phase 4B step 4: additive schema fields, review_reasons, stage on every defect."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.inspection.decision_engine import decide
from app.schemas.defect import Defect, DefectType
from app.schemas.inspection import (
    AlignmentMetadata, InspectionResult, InspectionStatus, QualityReport, ReasonCode,
)

ALIGN = AlignmentMetadata(success=True, alignment_error_px=0.5)
QUAL = QualityReport(passed=True, blur_score=200, brightness=120, contrast=40, overexposed_frac=0)


def _d(t, **kw):
    return Defect(component="U1", type=t, confidence=0.7, **kw)


def test_old_defect_json_still_loads():
    d = Defect.model_validate({"component": "R1", "type": "MISSING_COMPONENT", "confidence": 0.9})
    assert d.certainty is None and d.signals == [] and d.stage_id is None
    assert d.expected_class is None and d.observed_class is None


def test_old_result_json_still_loads():
    r = InspectionResult.model_validate({
        "inspection_id": "x", "timestamp": "t", "reference_id": "r", "profile_kind": "PCB",
        "status": "GOOD", "reason": "OK", "overall_confidence": 1.0, "inspection_time_ms": 1,
        "quality": QUAL.model_dump(), "alignment": ALIGN.model_dump()})
    assert r.review_reasons == [] and r.warnings == []


def test_review_carries_one_reason_per_suspected_defect():
    ds = [_d(DefectType.SIZE_DEVIATION), Defect(component="EXTRA1", type=DefectType.UNEXPECTED_COMPONENT,
                                                confidence=0.5, certainty="suspected")]
    dec = decide(ALIGN, QUAL, [], ds, get_settings(), 0.9)
    assert dec.status == InspectionStatus.REVIEW
    assert [(r.code, r.component) for r in dec.review_reasons] == [
        (ReasonCode.SUSPECTED_DEFECT, "U1"), (ReasonCode.SUSPECTED_DEFECT, "EXTRA1")]


def test_ambiguous_orientation_reason_code():
    d = _d(DefectType.ROTATED_COMPONENT, certainty="suspected", signals=["orientation_ambiguous"])
    dec = decide(ALIGN, QUAL, [], [d], get_settings(), 0.9)
    assert dec.status == InspectionStatus.REVIEW
    assert dec.review_reasons[0].code == ReasonCode.AMBIGUOUS_ORIENTATION


def test_every_review_has_a_reason_and_good_defective_have_none():
    bad = decide(AlignmentMetadata(success=False, reason="x"), QUAL, [], [], get_settings())
    assert bad.status == InspectionStatus.REVIEW and bad.review_reasons
    dfc = decide(ALIGN, QUAL, [], [_d(DefectType.MISSING_COMPONENT)], get_settings(), 0.9)
    assert dfc.status == InspectionStatus.DEFECTIVE and dfc.review_reasons == []
    good = decide(ALIGN, QUAL, [], [], get_settings(), 0.9)
    assert good.status == InspectionStatus.GOOD and good.review_reasons == []


def test_stage_id_on_every_defect_and_reasons_in_result(reference):
    import make_synthetic_pcb as gen
    from app.inspection.pipeline import run_inspection
    from app.storage.reference_repository import ReferenceRepository
    env, _ = reference
    profile = ReferenceRepository(env).load_profile(None)
    profile.reference.stage_id = "STAGE_X"
    img = gen.perturb(gen.render_board(skip={"R17", "IC1"}, seed=3))
    res = run_inspection(img, profile, env)
    assert res.defects
    assert all(d.stage_id == "STAGE_X" for d in res.defects)
    rev = run_inspection(gen.perturb(gen.render_board(seed=1)), profile, env)
    if rev.status == InspectionStatus.REVIEW:
        assert rev.review_reasons
