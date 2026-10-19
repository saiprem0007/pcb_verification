"""Defect types and the per-defect record."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class DefectType(str, Enum):
    MISSING_COMPONENT = "MISSING_COMPONENT"
    SHIFTED_COMPONENT = "SHIFTED_COMPONENT"
    ROTATED_COMPONENT = "ROTATED_COMPONENT"
    SIZE_DEVIATION = "SIZE_DEVIATION"
    VISUAL_ANOMALY = "VISUAL_ANOMALY"
    WRONG_COMPONENT = "WRONG_COMPONENT"        # detected class != expected class (YOLO)
    UNEXPECTED_COMPONENT = "UNEXPECTED_COMPONENT"  # component where none is expected (YOLO)


# Defect types that force a DEFECTIVE verdict when confirmed beyond tolerance.
# (Named for what it DECIDES — the verdict. This is unrelated to the severity
# label "CRITICAL" from the policy table: shifted/rotated are verdict-blocking
# but severity HIGH. Status depends on type + certainty only, never on severity.)
VERDICT_BLOCKING: frozenset[DefectType] = frozenset(
    {
        DefectType.MISSING_COMPONENT,
        DefectType.SHIFTED_COMPONENT,
        DefectType.ROTATED_COMPONENT,
        DefectType.WRONG_COMPONENT,
    }
)

# Backward-compatible alias — existing imports keep working.
CRITICAL_DEFECTS = VERDICT_BLOCKING

# Defect types that, on their own, send the board to a human (REVIEW), never GOOD
# and never DEFECTIVE: they come from heuristic checks (size, off-region diff).
REVIEW_ONLY_DEFECTS: frozenset[DefectType] = frozenset(
    {
        DefectType.SIZE_DEVIATION,
        DefectType.UNEXPECTED_COMPONENT,
    }
)

SeverityLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
"""
Severity classification for a detected defect.

CRITICAL — must stop production; component absent or wrong type; line stop required.
HIGH     — serious deviation; likely affects function; rework before advancing.
MEDIUM   — notable deviation; may affect function; re-inspect and document.
LOW      — minor; cosmetic or borderline; monitor but may advance with sign-off.
"""


class Defect(BaseModel):
    component: str = Field(description="Region / component identifier, e.g. 'R17'")
    type: DefectType
    confidence: float = Field(ge=0.0, le=1.0)

    # What / where / why -----------------------------------------------------
    expected: str = ""
    observed: str = ""
    expected_position: tuple[float, float] | None = None
    actual_position: tuple[float, float] | None = None
    deviation_px: float | None = None
    deviation_mm: float | None = None
    allowed_px: float | None = None
    rotation_deg: float | None = None
    allowed_rotation_deg: float | None = None

    # Axis-aligned box in aligned-image pixel coords: [x1, y1, x2, y2]
    bbox: tuple[int, int, int, int] | None = None

    # Severity and corrective action (populated by inspection/severity.py) ----
    # Both are optional: existing code constructing Defect without them works
    # unchanged. severity.apply_severity() fills them after detection.
    severity: SeverityLevel | None = None
    corrective_action: str = ""

