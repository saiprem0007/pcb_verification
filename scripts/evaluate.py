"""Compute classification + localization metrics for a labelled dataset.

    py scripts/evaluate.py --dataset synthetic_v1
    py scripts/evaluate.py --dataset synthetic_v1 --split tuning
    py scripts/evaluate.py --dataset synthetic_v1 --split heldout --stage smt
    py scripts/evaluate.py --dataset synthetic_v1 --limit 5

Reads ``data/datasets/<name>/manifest.json`` (falls back to filename-prefix
labels: good_* / defect_* / review_*).  Runs the engine for real on every
image and reports accuracy / precision / recall / F1 / FP rate / FN rate,
defect localization, latency percentiles, peak memory and — highlighted
separately — **FALSE ACCEPTS** (a DEFECTIVE board classified GOOD, the
critical error class).

Writes ``outputs/eval/<dataset>/report.{json,md,csv}``.

SYNTHETIC results are labelled in every output section.
Deterministic: identical results on successive runs.
"""

from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from harness import (
    VALID_SPLITS,
    load_manifest,
    assign_splits,
    filter_split,
    run_entry,
    compute_metrics,
    metrics_to_markdown,
)
from app.config import get_settings
from app.storage.reference_repository import ReferenceRepository


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="synthetic_v1",
                    help="dataset subfolder under data/datasets/")
    ap.add_argument("--reference", default=None,
                    help="override reference_id (default: from manifest)")
    ap.add_argument("--split", default="all", choices=sorted(VALID_SPLITS),
                    help="which split to evaluate: tuning | heldout | all (default)")
    ap.add_argument("--stage", default=None,
                    help="filter to a specific stage_id (default: all stages)")
    ap.add_argument("--limit", type=int, default=None,
                    help="evaluate at most N images (useful for quick smoke tests)")
    args = ap.parse_args()

    cfg = get_settings()
    ds_dir = cfg.data_dir / "datasets" / args.dataset
    if not ds_dir.is_dir():
        raise SystemExit(
            f"dataset not found: {ds_dir}\n"
            "run: py scripts/make_synthetic_pcb.py  (or check --dataset name)"
        )

    manifest = load_manifest(ds_dir)
    entries = assign_splits(manifest.images)          # stable, deterministic
    entries = filter_split(entries, args.split, stage_id=args.stage, limit=args.limit)

    if not entries:
        raise SystemExit(
            f"No images matched split={args.split!r} stage={args.stage!r} limit={args.limit}"
        )

    profile = ReferenceRepository(cfg).load_profile(
        args.reference or manifest.reference_id
    )

    # ---- run engine on every entry ----
    run_results = []
    per_image_rows = []
    for e in entries:
        rr = run_entry(e, ds_dir, profile, cfg)
        run_results.append(rr)

        exp_def = e.compact_defects()
        got_def = {f"{d.component}:{d.type.value}" for d in rr.result.defects}
        loc_ok = None
        if e.expected_defects:
            from harness import localization_match
            hits = sum(1 for ed in e.expected_defects if localization_match(ed, rr.result))
            loc_ok = f"{hits}/{len(e.expected_defects)}"

        per_image_rows.append({
            "file": Path(e.file).name,
            "source": e.source,
            "split": e.split or "—",
            "stage_id": e.stage_id or "—",
            "expected": e.label,
            "predicted": rr.result.status.value,
            "reason": rr.result.reason.value,
            "localization": loc_ok or "—",
            "defects_found": "; ".join(sorted(got_def)) or "—",
            "expected_defects": "; ".join(sorted(exp_def)) or "—",
            "time_ms": rr.time_ms,
            "peak_mem_kb": rr.peak_memory_kb,
        })

    metrics = compute_metrics(run_results)

    # ---- determine dominant source for report header ----
    synth_n = metrics.source_breakdown.get("synthetic", 0)
    real_n  = metrics.source_breakdown.get("real", 0)
    dominant_source = "real" if real_n >= synth_n and real_n > 0 else "synthetic"

    # ---- outputs ----
    out = cfg.outputs_dir / "eval" / args.dataset
    out.mkdir(parents=True, exist_ok=True)

    # JSON ----------------------------------------------------------------
    report_dict = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "reference_id": profile.id,
        "split": args.split,
        "stage_filter": args.stage,
        "limit": args.limit,
        "schema_version": manifest.schema_version,
        **metrics.to_dict(),
        "per_image": per_image_rows,
    }
    (out / "report.json").write_text(json.dumps(report_dict, indent=2), "utf-8")

    # Markdown ------------------------------------------------------------
    md_text = metrics_to_markdown(
        metrics,
        dataset=args.dataset,
        split=args.split,
        source=dominant_source,
        stage_id=args.stage,
        rows=per_image_rows,
    )
    md_header = (
        f"\n> generated: {report_dict['generated_at']}  "
        f"| reference: `{profile.id}`\n"
    )
    full_md = md_text.split("\n", 1)
    full_md = full_md[0] + "\n" + md_header + "\n".join(full_md[1:])
    (out / "report.md").write_text(full_md, "utf-8")

    # CSV -----------------------------------------------------------------
    if per_image_rows:
        with (out / "report.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(per_image_rows[0].keys()))
            w.writeheader()
            w.writerows(per_image_rows)

    # ---- console output ----
    print(full_md)
    print(f"\nreport written to: {out}/report.{{json,md,csv}}")

    if metrics.false_accepts:
        print(
            f"\n!! {len(metrics.false_accepts)} FALSE ACCEPT(S): {metrics.false_accepts}"
            "\n   (DEFECTIVE images classified GOOD — primary failure mode)"
        )
    else:
        print("\nFALSE ACCEPTS: 0  ✓")


if __name__ == "__main__":
    main()
