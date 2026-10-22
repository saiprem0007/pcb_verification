"""Reference board + inspection region definitions."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ProfileKind(str, Enum):
    PCB = "PCB"
    BOX = "BOX"
    GENERIC = "GENERIC"


class InspectionMode(str, Enum):
    REGIONS = "regions"   # per-component checks (needs regions defined)
    SURFACE = "surface"   # whole-object: does the surface match the reference?


class RegionTolerances(BaseModel):
    """Per-region overrides. Any field left ``None`` falls back to global config."""

    position_tol_px: float | None = None
    rotation_tol_deg: float | None = None
    missing_corr: float | None = None
    ssim_anomaly: float | None = None
    search_margin_px: int | None = None
    # If the component is not expected to have a meaningful orientation
    # (e.g. a round pad), skip the rotation check.
    check_rotation: bool = True


class Region(BaseModel):
    name: str = Field(description="Component / region id, e.g. 'R17', 'CAP1'")
    # Region box in REFERENCE image pixel coords: [x, y, w, h]
    bbox: tuple[int, int, int, int]
    expected_class: str | None = None
    # Design orientation of the part, degrees, COUNTER-CLOCKWISE POSITIVE as seen on
    # the reference image on screen (OpenCV getRotationMatrix2D convention); 0 = the
    # package's native orientation.  None = not known (hand-made regions).  Set by
    # placement import from the file rotation + the transform handedness (see
    # app.design.mapping.screen_rotation_deg).  A property of the design, not a
    # tolerance.  Additive/optional (Phase 4B, D5).
    expected_rotation_deg: float | None = None
    tolerances: RegionTolerances = Field(default_factory=RegionTolerances)

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)


class Reference(BaseModel):
    id: str = Field(description="Stable identifier, e.g. 'PCB_MODEL_001'")
    name: str
    kind: ProfileKind = ProfileKind.PCB
    inspection_mode: InspectionMode = InspectionMode.REGIONS
    active: bool = False
    image_path: str
    regions: list[Region] = Field(default_factory=list)
    mm_per_px: float | None = None
    created_at: str
    notes: str = ""

    # ---- Stage/Product metadata (additive — all nullable) -------------------
    # References that pre-date this field have None values and behave as a
    # single implicit stage.  Set product_id + stage_id to make a reference
    # part of an ordered multi-stage inspection sequence.
    product_id: str | None = None
    """Groups this reference under a product, e.g. ``'PCB_LINE_A'``."""
    stage_id: str | None = None
    """Identifier for this stage within the product, e.g. ``'smt_placement'``."""
    stage_name: str | None = None
    """Human-readable stage name, e.g. ``'SMT Component Placement'``."""
    stage_order: int | None = None
    """Numeric sort key (lower = earlier in assembly). None = unordered."""

    # ---- Design-file / placement transform (additive — Phase 3) ------------
    # Serialised 2×3 affine matrix [[a,b,c],[d,e,f]] (design mm → reference px).
    # None when no placement file has been imported for this reference.
    placement_transform: list[list[float]] | None = None
    """Affine transform matrix (2×3) mapping design-mm → reference-image pixels.
    Populated by scripts/import_placement.py or the import_placement API.
    None = no placement file imported; scalar mm_per_px is used for mm reporting."""


class ReferenceCreate(BaseModel):
    id: str
    name: str
    kind: ProfileKind = ProfileKind.PCB
    inspection_mode: InspectionMode = InspectionMode.REGIONS
    regions: list[Region] = Field(default_factory=list)
    mm_per_px: float | None = None
    notes: str = ""
    # Stage fields (optional)
    product_id: str | None = None
    stage_id: str | None = None
    stage_name: str | None = None
    stage_order: int | None = None
    # Placement transform (optional — set by import_placement)
    placement_transform: list[list[float]] | None = None

