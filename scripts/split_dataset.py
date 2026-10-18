"""Assign deterministic tuning/heldout split labels to a dataset manifest.

    py scripts/split_dataset.py --dataset synthetic_v1 --dry-run
    py scripts/split_dataset.py --dataset synthetic_v1 --seed 42 --heldout-frac 0.3
    py scripts/split_dataset.py --dataset synthetic_v1 --seed 42 --overwrite

Split is determined by  SHA-256(seed:filename)  so it is reproducible across
machines, Python versions and OS resets without seeding ``random``.

Writes ``data/datasets/<name>/split_manifest.json`` (or updates in-place with
``--in-place``).  The original manifest.json is NEVER modified unless you
explicitly pass ``--in-place``.

Printed table shows seed + file → split assignment for audit.
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

from harness import load_manifest, assign_splits
from app.config import get_settings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="synthetic_v1",
                    help="dataset subfolder under data/datasets/")
    ap.add_argument("--seed", type=int, default=42,
                    help="integer seed (default: 42)")
    ap.add_argument("--heldout-frac", type=float, default=0.3,
                    help="fraction assigned to heldout split (default: 0.3 = 30%%)")
    ap.add_argument("--overwrite", action="store_true",
                    help="overwrite any existing split annotations in the manifest")
    ap.add_argument("--in-place", action="store_true",
                    help="write split labels back into manifest.json instead of split_manifest.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the assignment table but do NOT write any file")
    args = ap.parse_args()

    if not (0.0 < args.heldout_frac < 1.0):
        raise SystemExit("--heldout-frac must be between 0 and 1 (exclusive)")

    cfg = get_settings()
    ds_dir = cfg.data_dir / "datasets" / args.dataset
    if not ds_dir.is_dir():
        raise SystemExit(f"dataset not found: {ds_dir}")

    manifest = load_manifest(ds_dir)
    entries = assign_splits(
        manifest.images,
        seed=args.seed,
        heldout_frac=args.heldout_frac,
        overwrite=args.overwrite,
    )

    # ---- print audit table ----
    tuning_n  = sum(1 for e in entries if e.split == "tuning")
    heldout_n = sum(1 for e in entries if e.split == "heldout")
    print(f"dataset : {args.dataset}")
    print(f"seed    : {args.seed}")
    print(f"heldout : {args.heldout_frac:.0%}  →  {heldout_n} images")
    print(f"tuning  : {(1-args.heldout_frac):.0%}  →  {tuning_n} images")
    print(f"total   : {len(entries)}")
    print()
    col1 = max(len(e.file) for e in entries) + 2
    print(f"{'file':<{col1}} {'label':<12} {'source':<12} split")
    print("-" * (col1 + 30))
    for e in entries:
        print(f"{e.file:<{col1}} {e.label:<12} {e.source:<12} {e.split}")

    if args.dry_run:
        print("\n[dry-run] no files written.")
        return

    # ---- serialise entries back to JSON ----
    # Reload original manifest data so we preserve all existing top-level keys.
    mf_path = ds_dir / "manifest.json"
    if mf_path.is_file():
        original = json.loads(mf_path.read_text("utf-8"))
    else:
        original = {
            "dataset": manifest.dataset,
            "reference_id": manifest.reference_id,
            "source": manifest.source,
            "note": manifest.note,
            "images": [],
        }

    # Build a lookup: file → split
    split_map = {e.file: e.split for e in entries}

    updated_images = []
    for img in original.get("images", []):
        img_copy = dict(img)
        if img_copy["file"] in split_map:
            img_copy["split"] = split_map[img_copy["file"]]
        updated_images.append(img_copy)

    output_data = dict(original)
    output_data["images"] = updated_images
    output_data["_split_seed"] = args.seed
    output_data["_split_heldout_frac"] = args.heldout_frac

    if args.in_place:
        out_path = mf_path
        print(f"\nupdating manifest in-place: {out_path}")
    else:
        out_path = ds_dir / "split_manifest.json"
        print(f"\nwriting: {out_path}")

    out_path.write_text(json.dumps(output_data, indent=2), "utf-8")
    print("done.")


if __name__ == "__main__":
    main()
