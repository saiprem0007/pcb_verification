"""Reference (known-good board) management endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from ..logging_config import get_logger
from ..schemas.reference import (
    InspectionMode,
    ProfileKind,
    Reference,
    ReferenceCreate,
    Region,
)
from ..schemas.stage import ProductStages, StageRef
from ..sources import SourceError, UploadSource
from ..sources.camera_hub import get_hub
from ..storage.reference_repository import ReferenceError
from .deps import reference_repo

router = APIRouter(prefix="/api/references", tags=["reference"])
log = get_logger(__name__)


@router.get("", response_model=list[Reference])
def list_references() -> list[Reference]:
    return reference_repo().list()


@router.get("/active", response_model=Reference | None)
def active_reference() -> Reference | None:
    return reference_repo().get_active()


# ---- Product / stage discovery (must come BEFORE /{reference_id}) ----------

@router.get("/products", response_model=list[ProductStages])
def list_products() -> list[ProductStages]:
    """Return all distinct products (references with a product_id set),
    each with their stages sorted by stage_order.
    """
    all_refs = reference_repo().list()
    product_ids: list[str] = []
    seen: set[str] = set()
    for r in all_refs:
        if r.product_id and r.product_id not in seen:
            product_ids.append(r.product_id)
            seen.add(r.product_id)

    result: list[ProductStages] = []
    for pid in product_ids:
        stages_refs = reference_repo().list_by_product(pid)
        stages = [
            StageRef(
                reference_id=r.id,
                stage_id=r.stage_id,
                stage_name=r.stage_name,
                stage_order=r.stage_order,
            )
            for r in stages_refs
        ]
        result.append(ProductStages(product_id=pid, stages=stages))
    return result


@router.get("/product/{product_id}/stages", response_model=ProductStages)
def get_product_stages(product_id: str) -> ProductStages:
    """Return all stages for a single product, sorted by stage_order."""
    stages_refs = reference_repo().list_by_product(product_id)
    if not stages_refs:
        raise HTTPException(
            status_code=404,
            detail=f"No references found for product '{product_id}'"
        )
    stages = [
        StageRef(
            reference_id=r.id,
            stage_id=r.stage_id,
            stage_name=r.stage_name,
            stage_order=r.stage_order,
        )
        for r in stages_refs
    ]
    return ProductStages(product_id=product_id, stages=stages)

@router.get("/{reference_id}", response_model=Reference)
def get_reference(reference_id: str) -> Reference:
    try:
        return reference_repo().get(reference_id)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@router.post("", response_model=Reference, status_code=201)
async def create_reference(
    file: UploadFile = File(...),
    id: str = Form(...),
    name: str = Form(...),
    kind: ProfileKind = Form(default=ProfileKind.PCB),
    inspection_mode: InspectionMode = Form(default=InspectionMode.REGIONS),
    regions: str = Form(default="[]"),
    mm_per_px: float | None = Form(default=None),
    notes: str = Form(default=""),
    activate: bool = Form(default=False),
) -> Reference:
    try:
        region_list = [Region.model_validate(r) for r in json.loads(regions or "[]")]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid regions JSON: {exc}") from exc

    try:
        image = UploadSource(await file.read(), file.filename or "reference").read()
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    spec = ReferenceCreate(
        id=id, name=name, kind=kind, inspection_mode=inspection_mode,
        regions=region_list, mm_per_px=mm_per_px, notes=notes,
    )
    try:
        ref = reference_repo().create(spec, image)
        if activate:
            ref = reference_repo().activate(ref.id)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ref


_CLASS_PREFIX = {"resistor": "R", "cap": "C", "ic": "U", "led": "D", "connector": "J"}


@router.post("/{reference_id}/autodetect_regions", response_model=list[Region])
def autodetect_regions(reference_id: str) -> list[Region]:
    """Propose inspection regions for a reference using the YOLO detector.

    Returns candidates only — the caller reviews/edits and PUTs them back.
    400 with 'MODEL NOT CONFIGURED' if no detector is available.
    """
    from ..config import get_settings
    from ..models import MODEL_NOT_CONFIGURED, build_component_detector
    from ..schemas.reference import RegionTolerances

    cfg = get_settings()
    detector = build_component_detector(cfg)
    if detector.status == MODEL_NOT_CONFIGURED:
        raise HTTPException(status_code=400, detail="MODEL NOT CONFIGURED")

    try:
        profile = reference_repo().load_profile(reference_id)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    dets = sorted(detector.detect(profile.load_reference_image()),
                  key=lambda d: (d.bbox[1], d.bbox[0]))
    counters: dict[str, int] = {}
    regions: list[Region] = []
    for d in dets:
        prefix = _CLASS_PREFIX.get(d.label, "X")
        counters[prefix] = counters.get(prefix, 0) + 1
        x1, y1, x2, y2 = d.bbox
        regions.append(
            Region(
                name=f"{prefix}{counters[prefix]}",
                bbox=(x1, y1, x2 - x1, y2 - y1),
                expected_class=d.label,
                tolerances=RegionTolerances(check_rotation=d.label != "led"),
            )
        )
    return regions


class CaptureReferenceRequest(BaseModel):
    id: str
    name: str
    source: str                         # camera index ("0") or stream URL
    kind: ProfileKind = ProfileKind.PCB
    inspection_mode: InspectionMode = InspectionMode.SURFACE  # capture -> usually a whole object
    mm_per_px: float | None = None
    activate: bool = True


@router.post("/capture", response_model=Reference, status_code=201)
def capture_reference(req: CaptureReferenceRequest) -> Reference:
    """Grab one frame from a live camera and save it as a new reference.

    Use this to inspect a REAL object: the synthetic demo references only match
    the synthetic images. Capture your known-good part through the same camera you
    will inspect with, then add regions.
    """
    try:
        frame = get_hub().frame(req.source, timeout_s=6.0)
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    spec = ReferenceCreate(
        id=req.id, name=req.name, kind=req.kind, inspection_mode=req.inspection_mode,
        regions=[], mm_per_px=req.mm_per_px,
        notes=f"captured from camera source '{req.source}'",
    )
    try:
        ref = reference_repo().create(spec, frame)
        if req.activate:
            ref = reference_repo().activate(ref.id)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ref


@router.put("/{reference_id}/regions", response_model=Reference)
def set_regions(reference_id: str, regions: list[Region]) -> Reference:
    try:
        return reference_repo().set_regions(reference_id, regions)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{reference_id}/activate", response_model=Reference)
def activate_reference(reference_id: str) -> Reference:
    try:
        return reference_repo().activate(reference_id)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{reference_id}")
def delete_reference(reference_id: str) -> Response:
    try:
        reference_repo().delete(reference_id)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Phase 3: placement-file import
# ---------------------------------------------------------------------------

class FiducialIn(BaseModel):
    """One fiducial correspondence for the import_placement endpoint."""
    name: str
    design_mm: tuple[float, float]
    ref_px: tuple[float, float]


class ImportPlacementResult(BaseModel):
    """Response from POST /{reference_id}/import_placement."""
    reference_id: str
    components_parsed: int
    regions_generated: int
    rms_error_px: float
    max_error_px: float
    scale_px_per_mm: float
    fallback_packages: list[str]
    residuals: list[dict]
    dry_run: bool


@router.post("/{reference_id}/import_placement", response_model=ImportPlacementResult)
async def import_placement(
    reference_id: str,
    placement_file: UploadFile = File(..., description="KiCad .pos or generic CSV"),
    fiducials_json: str = Form(..., description="JSON list of {name, design_mm, ref_px}"),
    side: str = Form(default="F"),
    fmt: str | None = Form(default=None),
    dry_run: bool = Form(default=False),
    overwrite_regions: bool = Form(default=False),
    max_rms_px: float = Form(default=3.0),
    max_residual_px: float = Form(default=5.0),
) -> ImportPlacementResult:
    """Import a placement file and generate regions from it.

    Fits a fiducial-based affine transform (design mm → reference px),
    generates Region objects, and optionally writes them back.

    Unsupported: Gerber, ODB++, STEP, PDF drawings.
    """
    from ..config import get_settings
    from ..design.parser import parse_placement, PlacementParseError
    from ..design.mapping import (
        Fiducial, compute_transform, components_to_regions, TransformError,
        _load_package_table,
    )
    from ..schemas.reference import RegionTolerances

    cfg = get_settings()

    # Validate reference exists
    try:
        ref = reference_repo().get(reference_id)
    except ReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Parse placement file
    try:
        text = (await placement_file.read()).decode("utf-8", errors="replace")
        components = parse_placement(
            text, filename=placement_file.filename or "", fmt=fmt
        )
    except PlacementParseError as exc:
        raise HTTPException(status_code=422, detail=f"Placement parse error: {exc}") from exc

    # Parse fiducials
    try:
        fid_raw = json.loads(fiducials_json)
        if not isinstance(fid_raw, list):
            raise ValueError("fiducials_json must be a JSON list")
        fiducials = [
            Fiducial(
                name=f["name"],
                design_mm=(float(f["design_mm"][0]), float(f["design_mm"][1])),
                ref_px=(float(f["ref_px"][0]), float(f["ref_px"][1])),
            )
            for f in fid_raw
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid fiducials_json: {exc}") from exc

    # Fit transform
    try:
        transform = compute_transform(
            fiducials, max_rms_px=max_rms_px, max_residual_px=max_residual_px
        )
    except TransformError as exc:
        raise HTTPException(status_code=422, detail=f"Transform error: {exc}") from exc

    # Generate regions
    package_table = _load_package_table(cfg.package_classes_path)
    non_fid = [c for c in components if not c.is_fiducial()]
    raw_regions = components_to_regions(non_fid, transform, package_table, side_filter=side)
    fallback_pkgs = sorted({r["_package"] for r in raw_regions if r.get("_pkg_fallback")})

    new_regions = [
        Region(
            name=r["name"],
            bbox=r["bbox"],
            expected_class=r["expected_class"],
            tolerances=RegionTolerances(
                check_rotation=r["tolerances"].get("check_rotation", True)
            ),
        )
        for r in raw_regions
    ]

    if not dry_run:
        if overwrite_regions:
            final_regions = new_regions
        else:
            existing = {r.name: r for r in ref.regions}
            for nr in new_regions:
                existing[nr.name] = nr
            final_regions = list(existing.values())
        reference_repo().set_placement_transform(ref.id, transform.matrix_list)
        reference_repo().set_regions(ref.id, final_regions)
        log.info(
            "import_placement: %s → %d regions, rms=%.3f px",
            ref.id, len(final_regions), transform.rms_error_px,
        )

    return ImportPlacementResult(
        reference_id=reference_id,
        components_parsed=len(components),
        regions_generated=len(new_regions),
        rms_error_px=transform.rms_error_px,
        max_error_px=transform.max_error_px,
        scale_px_per_mm=round(transform.scale_mm_to_px(), 4),
        fallback_packages=fallback_pkgs,
        residuals=[
            {
                "name": r.name,
                "predicted_px": list(r.predicted_px),
                "actual_px": list(r.actual_px),
                "error_px": r.error_px,
            }
            for r in transform.residuals
        ],
        dry_run=dry_run,
    )
