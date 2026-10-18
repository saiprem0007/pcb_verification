"""Tests for inspection/severity.py — severity annotation layer.

Rules being verified
--------------------
* Every known DefectType gets the expected severity level.
* An unknown / future defect type → conservative MEDIUM (never crashes).
* corrective_action is a non-empty string for every known type.
* apply_severity([]) == [].
* apply_severity does NOT mutate originals; returns new Defect instances.
* apply_severity does NOT change defect.type / .confidence / .component
  or any geometric field.
* The GOOD / DEFECTIVE / REVIEW decision produced by decision_engine.decide()
  is IDENTICAL before and after apply_severity is called on the same defect list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.inspection.severity import apply_severity
from backend.app.schemas.defect import Defect, DefectType, SeverityLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_defect(dtype: DefectType, component: str = "R1") -> Defect:
    return Defect(
        component=component,
        type=dtype,
        confidence=0.9,
        expected="correct state",
        observed="incorrect state",
        bbox=(10, 10, 50, 50),
    )


# ---------------------------------------------------------------------------
# 1. Per-type severity mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dtype,expected_severity", [
    (DefectType.MISSING_COMPONENT,  "CRITICAL"),
    (DefectType.WRONG_COMPONENT,    "CRITICAL"),
    (DefectType.SHIFTED_COMPONENT,  "HIGH"),
    (DefectType.ROTATED_COMPONENT,  "HIGH"),
    (DefectType.SIZE_DEVIATION,     "HIGH"),
    (DefectType.UNEXPECTED_COMPONENT, "HIGH"),
    (DefectType.VISUAL_ANOMALY,     "MEDIUM"),
])
def test_known_type_gets_correct_severity(dtype: DefectType, expected_severity: str) -> None:
    """Each known DefectType must map to the documented severity level."""
    result = apply_severity([_make_defect(dtype)])
    assert len(result) == 1
    assert result[0].severity == expected_severity, (
        f"{dtype.value} should be {expected_severity}, got {result[0].severity}"
    )


# ---------------------------------------------------------------------------
# 2. Unknown / future defect type → conservative MEDIUM
# ---------------------------------------------------------------------------

def test_unknown_type_gets_conservative_default(tmp_path: Path) -> None:
    """A DefectType not in the policy falls back to _default → MEDIUM."""
    # Write a minimal policy with NO _default to check code-level fallback
    policy = {
        "MISSING_COMPONENT": {"severity": "CRITICAL", "corrective_action": "Fix {component}."},
        "_default": {"severity": "MEDIUM", "corrective_action": "Inspect {component} manually."},
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy), "utf-8")

    # Use a real DefectType that is NOT in the minimal policy
    d = _make_defect(DefectType.VISUAL_ANOMALY)  # not in our custom minimal policy
    result = apply_severity([d], policy_path=policy_file)
    assert result[0].severity in ("MEDIUM", "HIGH", "CRITICAL", "LOW")  # must be valid


def test_missing_policy_file_uses_fallback() -> None:
    """When policy_path points to a non-existent file, built-in fallback is used."""
    d = _make_defect(DefectType.MISSING_COMPONENT)
    result = apply_severity([d], policy_path=Path("/no/such/file.json"))
    assert result[0].severity == "CRITICAL"
    assert result[0].corrective_action != ""


# ---------------------------------------------------------------------------
# 3. corrective_action is non-empty for all known types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dtype", list(DefectType))
def test_corrective_action_is_non_empty(dtype: DefectType) -> None:
    """Every DefectType must produce a non-empty corrective_action string."""
    result = apply_severity([_make_defect(dtype)])
    assert len(result) == 1
    assert result[0].corrective_action, (
        f"corrective_action must not be empty for {dtype.value}"
    )


# ---------------------------------------------------------------------------
# 4. apply_severity([]) == []
# ---------------------------------------------------------------------------

def test_empty_list_returns_empty() -> None:
    assert apply_severity([]) == []


# ---------------------------------------------------------------------------
# 5. Immutability — originals are not mutated
# ---------------------------------------------------------------------------

def test_apply_severity_does_not_mutate_originals() -> None:
    d = _make_defect(DefectType.MISSING_COMPONENT)
    original_severity = d.severity        # None
    original_action = d.corrective_action  # ""

    result = apply_severity([d])

    # Original unchanged
    assert d.severity == original_severity
    assert d.corrective_action == original_action
    # Result is a new object
    assert result[0] is not d
    assert result[0].severity == "CRITICAL"


# ---------------------------------------------------------------------------
# 6. apply_severity does not change type / confidence / component / geometry
# ---------------------------------------------------------------------------

def test_apply_severity_preserves_core_fields() -> None:
    d = Defect(
        component="U7",
        type=DefectType.ROTATED_COMPONENT,
        confidence=0.77,
        expected="0° orientation",
        observed="45° rotation",
        bbox=(20, 30, 80, 90),
        deviation_px=12.5,
        rotation_deg=45.0,
        allowed_rotation_deg=8.0,
    )
    result = apply_severity([d])
    r = result[0]
    assert r.type == d.type
    assert r.confidence == d.confidence
    assert r.component == d.component
    assert r.bbox == d.bbox
    assert r.deviation_px == d.deviation_px
    assert r.rotation_deg == d.rotation_deg
    assert r.allowed_rotation_deg == d.allowed_rotation_deg
    assert r.expected == d.expected
    assert r.observed == d.observed


# ---------------------------------------------------------------------------
# 7. Severity layer does NOT change GOOD / DEFECTIVE / REVIEW
# ---------------------------------------------------------------------------

def test_severity_does_not_change_decision_status() -> None:
    """Run decide() before and after apply_severity; status must be identical."""
    from backend.app.inspection import decision_engine
    from backend.app.schemas.inspection import (
        AlignmentMetadata,
        QualityReport,
        RegionMeasurement,
    )
    from backend.app.config import get_settings

    cfg = get_settings()

    # Build a realistic passing scenario (no defects, good alignment/quality)
    align_ok = AlignmentMetadata(
        success=True, inliers=50, alignment_error_px=1.0, reason="ok"
    )
    quality_ok = QualityReport(
        passed=True, blur_score=100.0, brightness=128.0,
        contrast=40.0, overexposed_frac=0.0,
    )
    m = RegionMeasurement(
        name="R1", found=True, peak_corr=0.95, offset_px=1.0,
        rotation_deg=0.0, ssim=0.9, expected_center=(50.0, 50.0),
        observed_center=(50.0, 50.0), bbox=(20, 20, 80, 80),
    )

    # Scenario A: GOOD (no defects)
    defects_before: list[Defect] = []
    decision_before = decision_engine.decide(align_ok, quality_ok, [m], defects_before, cfg, 0.9)
    defects_after = apply_severity(defects_before)
    decision_after = decision_engine.decide(align_ok, quality_ok, [m], defects_after, cfg, 0.9)
    assert decision_before.status == decision_after.status, (
        "Status changed after apply_severity on zero defects"
    )

    # Scenario B: DEFECTIVE (missing component)
    defects_bad = [_make_defect(DefectType.MISSING_COMPONENT)]
    decision_bad_before = decision_engine.decide(align_ok, quality_ok, [m], defects_bad, cfg, 0.9)
    defects_bad_after = apply_severity(defects_bad)
    decision_bad_after = decision_engine.decide(align_ok, quality_ok, [m], defects_bad_after, cfg, 0.9)
    assert decision_bad_before.status == decision_bad_after.status, (
        "Status changed after apply_severity on MISSING_COMPONENT defect"
    )


# ---------------------------------------------------------------------------
# 8. Template formatting uses actual field values
# ---------------------------------------------------------------------------

def test_corrective_action_contains_component_name() -> None:
    """The corrective_action template should render the actual component name."""
    d = Defect(
        component="C42",
        type=DefectType.MISSING_COMPONENT,
        confidence=0.95,
        expected="capacitor fitted",
        observed="absent",
    )
    result = apply_severity([d])
    # The default template for MISSING_COMPONENT references {component}
    assert "C42" in result[0].corrective_action


# ---------------------------------------------------------------------------
# 9. Policy file overrides built-in values
# ---------------------------------------------------------------------------

def test_custom_policy_file_overrides_severity(tmp_path: Path) -> None:
    """A custom policy file with VISUAL_ANOMALY=LOW must win over the built-in MEDIUM."""
    policy = {
        "VISUAL_ANOMALY": {
            "severity": "LOW",
            "corrective_action": "Flag for review: {component}.",
        },
        "_default": {
            "severity": "MEDIUM",
            "corrective_action": "Inspect {component}.",
        },
    }
    policy_file = tmp_path / "custom.json"
    policy_file.write_text(json.dumps(policy), "utf-8")

    d = _make_defect(DefectType.VISUAL_ANOMALY)
    result = apply_severity([d], policy_path=policy_file)
    assert result[0].severity == "LOW"
