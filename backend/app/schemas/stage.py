"""Assembly stage schema.

A *product* is a logical grouping of references (one per assembly stage).
Each reference can be tagged with optional stage metadata so that the
inspection endpoint can select the correct reference by stage rather than
by reference_id directly.

Backward-compatibility:  references that pre-date this schema have
``product_id=None``, ``stage_id=None``, ``stage_order=None``.  They are
treated as a single implicit stage and continue to work unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageInfo(BaseModel):
    """Stage metadata embedded on a Reference (optional, all fields nullable)."""

    product_id: str | None = None
    """Stable identifier for the product this stage belongs to, e.g. ``'PCB_MODEL_001'``."""

    stage_id: str | None = None
    """Identifier for this specific stage, e.g. ``'smt_placement'`` or ``'stage_01'``."""

    stage_name: str | None = None
    """Human-readable stage name, e.g. ``'SMT Component Placement'``."""

    stage_order: int | None = None
    """Numeric sort key for progression order (lower = earlier in assembly)."""

    description: str = ""
    """Optional description of what is inspected at this stage."""


class StageRef(BaseModel):
    """A reference summarised with its stage metadata — returned by the stages API."""

    reference_id: str
    stage_id: str | None = None
    stage_name: str | None = None
    stage_order: int | None = None


class ProductStages(BaseModel):
    """All stages belonging to one product, in assembly order."""

    product_id: str
    stages: list[StageRef] = Field(default_factory=list)
