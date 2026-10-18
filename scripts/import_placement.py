"""Import a placement file into an existing reference, generating regions.

    py scripts/import_placement.py \\
        --reference PCB_MODEL_001 \\
        --placement tests/fixtures/synthetic_pcb.pos \\
        --fiducials tests/fixtures/synthetic_pcb_fiducials.json \\
        [--side F] [--fmt kicad|csv] [--dry-run] [--overwrite-regions]

What it does:
  1. Parse the placement file (KiCad .pos or generic CSV, auto-detected).
  2. Load fiducial correspondences from a JSON file (design_mm ↔ ref_px).
  3. Fit an affine transform from design mm to reference-image pixels.
  4. Print the mapping-residual report (per-fiducial error + RMS).
  5. Generate Regions using the package→class table (data/policy/package_classes.json).
  6. Save the transform and regions to the reference (unless --dry-run).

Unsupported formats: Gerber, ODB++, STEP, PDF drawings.
Manual region editing remains fully functional after import.
"""

from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.config import get_settings
from app.design.parser import parse_placement_file, PlacementParseError
from app.design.mapping import (
    Fiducial, compute_transform, components_to_regions, TransformError,
    _load_package_table,
)
from app.schemas.reference import Region, RegionTolerances
from app.storage.reference_repository import ReferenceRepository, ReferenceError


def load_fiducials(path: Path) -> list[Fiducial]:
    """Load fiducials from a JSON file.

    Expected format::

        {
          "fiducials": [
            {"name": "FID1", "design_mm": [3.0, 4.0], "ref_px": [45, 60]},
            ...
          ]
        }

    Raises ValueError on schema errors.
    """
    try:
        data = json.loads(path.read_text("utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read fiducials file {path}: {exc}") from exc

    raw = data.get("fiducials", [])
    if not raw:
        raise ValueError(
            f"Fiducials file {path} has no 'fiducials' list or it is empty."
        )
    fids: list[Fiducial] = []
    for i, entry in enumerate(raw):
        try:
            name = entry["name"]
            dxdy = entry["design_mm"]
            rxry = entry["ref_px"]
            fids.append(Fiducial(
                name=name,
                design_mm=(float(dxdy[0]), float(dxdy[1])),
                ref_px=(float(rxry[0]), float(rxry[1])),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"fiducials[{i}] is malformed: {exc}") from exc
    return fids


def print_residual_report(transform, components_used: int, fallback_pkgs: list[str]) -> None:
    """Print the mapping-residual report to stdout."""
    print("\n── Fiducial mapping residuals ──────────────────────────────────")
    print(f"  {'fiducial':<12} {'predicted (px)':<22} {'actual (px)':<22} error")
    print(f"  {'─'*12:<12} {'─'*22:<22} {'─'*22:<22} ─────")
    for r in transform.residuals:
        pred = f"({r.predicted_px[0]:.1f}, {r.predicted_px[1]:.1f})"
        act  = f"({r.actual_px[0]:.1f}, {r.actual_px[1]:.1f})"
        print(f"  {r.name:<12} {pred:<22} {act:<22} {r.error_px:.2f} px")
    print(f"  RMS residual : {transform.rms_error_px:.3f} px")
    print(f"  Max residual : {transform.max_error_px:.3f} px")
    print(f"  Scale (px/mm): {transform.scale_mm_to_px():.4f}")
    print(f"  Components   : {components_used}")
    if fallback_pkgs:
        print(f"\n  ⚠ Unknown packages (fallback size used): {sorted(set(fallback_pkgs))}")
    print("────────────────────────────────────────────────────────────────\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True,
                    help="Reference ID to update (must exist)")
    ap.add_argument("--placement", required=True,
                    help="Path to placement file (.pos or .csv)")
    ap.add_argument("--fiducials", required=True,
                    help="Path to fiducials JSON (design_mm ↔ ref_px)")
    ap.add_argument("--side", default="F", choices=["F", "B"],
                    help="Board side to import (default: F)")
    ap.add_argument("--fmt", default=None, choices=["kicad", "csv"],
                    help="Force format (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and compute transform but do NOT write to the reference")
    ap.add_argument("--overwrite-regions", action="store_true",
                    help="Replace all existing regions (default: APPEND/replace by name)")
    ap.add_argument("--max-rms-px", type=float, default=3.0,
                    help="Reject transform if RMS residual > this (px, default: 3.0)")
    ap.add_argument("--max-residual-px", type=float, default=5.0,
                    help="Reject transform if any fiducial > this (px, default: 5.0)")
    args = ap.parse_args()

    cfg = get_settings()
    repo = ReferenceRepository(cfg)

    # 1. Check reference exists
    try:
        ref = repo.get(args.reference)
    except ReferenceError as exc:
        raise SystemExit(f"Reference error: {exc}") from exc

    print(f"reference    : {ref.id} ({ref.name})")
    print(f"placement    : {args.placement}")
    print(f"fiducials    : {args.fiducials}")
    print(f"side filter  : {args.side}")

    # 2. Parse placement file
    try:
        components = parse_placement_file(
            Path(args.placement), fmt=args.fmt
        )
    except PlacementParseError as exc:
        raise SystemExit(f"Placement parse error: {exc}") from exc

    print(f"parsed       : {len(components)} components")

    # 3. Load fiducials
    try:
        fiducials = load_fiducials(Path(args.fiducials))
    except ValueError as exc:
        raise SystemExit(f"Fiducial load error: {exc}") from exc

    print(f"fiducials    : {len(fiducials)}")

    # 4. Fit transform
    try:
        transform = compute_transform(
            fiducials,
            max_rms_px=args.max_rms_px,
            max_residual_px=args.max_residual_px,
        )
    except TransformError as exc:
        raise SystemExit(f"Transform error: {exc}") from exc

    # 5. Generate regions
    package_table = _load_package_table(cfg.package_classes_path)
    raw_regions = components_to_regions(
        [c for c in components if not c.is_fiducial()],
        transform,
        package_table,
        side_filter=args.side,
    )

    fallback_pkgs = [r["_package"] for r in raw_regions if r.get("_pkg_fallback")]

    # Print residual report
    print_residual_report(transform, len(raw_regions), fallback_pkgs)

    # 6. Convert to Region objects
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

    print(f"regions generated : {len(new_regions)}")
    for r in new_regions:
        print(f"  {r.name:<8} bbox={r.bbox}  class={r.expected_class}")

    if args.dry_run:
        print("\n[dry-run] no changes written to the reference.")
        return

    # 7. Merge or replace regions
    if args.overwrite_regions:
        final_regions = new_regions
        print(f"\noverwriting all regions ({len(final_regions)} total)")
    else:
        # Merge: replace by name, keep manual extras
        existing = {r.name: r for r in ref.regions}
        for nr in new_regions:
            existing[nr.name] = nr
        final_regions = list(existing.values())
        print(f"\nmerging regions: {len(new_regions)} imported, "
              f"{len(final_regions)} total (manual extras preserved)")

    # 8. Write transform + regions
    repo.set_placement_transform(ref.id, transform.matrix_list)
    repo.set_regions(ref.id, final_regions)

    print(f"transform saved → reference '{ref.id}'")
    print(f"regions saved   → {len(final_regions)} regions")
    print("\nDone. Manual region overrides can be applied via PUT /api/references/{id}/regions.")


if __name__ == "__main__":
    main()
