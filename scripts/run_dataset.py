"""Run the inspection engine over a whole dataset — one command, full artefacts.

    py scripts/run_dataset.py --dataset synthetic_v1
    py scripts/run_dataset.py --dataset synthetic_v1 --split tuning
    py scripts/run_dataset.py --dataset synthetic_v1 --stage smt --limit 5

For every image writes, under ``outputs/datasets/<dataset>/<stem>/``::

    original.png  cropped.png  aligned.png  annotated.png  montage.png  result.json

Plus a dataset-level ``summary.csv``, ``summary.md`` and ``contact_sheet.png``
(all per-image montages stacked vertically).

No fake predictions — the engine runs for real on each image.
SYNTHETIC results are labelled in every report section.
Results are deterministic: identical on successive runs with the same split.
"""

from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import csv
import json
import shutil
from pathlib import Path

import _bootstrap  # noqa: F401
import cv2
import numpy as np

from harness import (
    VALID_SPLITS,
    load_manifest,
    assign_splits,
    filter_split,
    run_entry,
    compute_metrics,
    localization_match,
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
                    help="which split to run: tuning | heldout | all (default)")
    ap.add_argument("--stage", default=None,
                    help="filter to a specific stage_id (default: all stages)")
    ap.add_argument("--limit", type=int, default=None,
                    help="run at most N images")
    ap.add_argument("--no-wipe", action="store_true",
                    help="do not delete existing output directory before running")
    args = ap.parse_args()

    cfg = get_settings()
    ds_dir = cfg.data_dir / "datasets" / args.dataset
    if not ds_dir.is_dir():
        raise SystemExit(
            f"dataset not found: {ds_dir}\n"
            "run: py scripts/make_synthetic_pcb.py  (or check --dataset name)"
        )

    manifest = load_manifest(ds_dir)
    entries = assign_splits(manifest.images)
    entries = filter_split(entries, args.split, stage_id=args.stage, limit=args.limit)

    if not entries:
        raise SystemExit(
            f"No images matched split={args.split!r} stage={args.stage!r} limit={args.limit}"
        )

    profile = ReferenceRepository(cfg).load_profile(
        args.reference or manifest.reference_id
    )

    out_root = cfg.outputs_dir / "datasets" / args.dataset
    if not args.no_wipe and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    montages: list[np.ndarray] = []
    run_results = []

    for entry in entries:
        stem = Path(entry.file).stem
        img_path = ds_dir / entry.file
        rr = run_entry(entry, ds_dir, profile, cfg)
        run_results.append(rr)
        result = rr.result

        # ---- copy per-image artefacts ----
        dest = out_root / stem
        dest.mkdir(parents=True, exist_ok=True)
        src = cfg.outputs_dir / result.inspection_id
        for f in ("original.png", "cropped.png", "aligned.png", "annotated.png",
                   "montage.png", "result.json"):
            if (src / f).is_file():
                shutil.copy(src / f, dest / f)

        exp_def = entry.compact_defects()
        got_def = {f"{d.component}:{d.type.value}" for d in result.defects}

        loc_hits = 0
        if entry.expected_defects:
            loc_hits = sum(
                1 for ed in entry.expected_defects
                if localization_match(ed, result)
            )
        loc_str = (
            f"{loc_hits}/{len(entry.expected_defects)}"
            if entry.expected_defects else "—"
        )

        rows.append({
            "name": stem,
            "source": entry.source,
            "split": entry.split or "—",
            "stage_id": entry.stage_id or "—",
            "expected": entry.label,
            "predicted": result.status.value,
            "match": "ok" if entry.label == result.status.value else "MISMATCH",
            "reason": result.reason.value,
            "detect": f"{result.detection.method}/{result.detection.coverage}",
            "align": "ok" if result.alignment.success else "fail",
            "align_err_px": result.alignment.alignment_error_px,
            "board_ssim": result.board_similarity,
            "conf": round(result.overall_confidence, 3),
            "time_ms": rr.time_ms,
            "peak_mem_kb": rr.peak_memory_kb,
            "localization": loc_str,
            "expected_defects": "; ".join(sorted(exp_def)) or "—",
            "found_defects": "; ".join(sorted(got_def)) or "—",
        })

        if (dest / "montage.png").is_file():
            img = cv2.imread(str(dest / "montage.png"))
            if img is not None:
                montages.append(img)

    # ---- summary.csv ----
    with (out_root / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ---- aggregate metrics via harness ----
    metrics = compute_metrics(run_results)
    n = len(rows)
    status_ok = sum(1 for r in rows if r["match"] == "ok")
    synth_n = metrics.source_breakdown.get("synthetic", 0)
    src_note = (
        "[SYNTHETIC — smoke test only, not a real-world benchmark]"
        if synth_n == n else "[MIXED synthetic/real]"
    )

    # ---- summary.md ----
    md = [
        f"# Dataset run — {args.dataset}",
        "",
        f"> **Source: {manifest.source.upper()}** {src_note}",
        f"> Split: `{args.split}` | Stage: `{args.stage or 'all'}` | N: {n}",
        "",
        f"- reference: `{profile.id}`",
        f"- status correct: {status_ok}/{n}",
        f"- **FALSE ACCEPTS (DEFECTIVE→GOOD): {len(metrics.false_accepts)}**",
        f"- board detected: {sum(1 for r in rows if not r['detect'].startswith('none'))}/{n}",
        f"- alignment ok: {sum(1 for r in rows if r['align'] == 'ok')}/{n}",
        f"- latency p50: {metrics.p50_ms:.0f} ms  |  p95: {metrics.p95_ms:.0f} ms",
        "",
        "| image | src | split | expected | predicted | reason | detect | align | loc | found defects |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        mark = "" if r["match"] == "ok" else " ⚠"
        md.append(
            f"| {r['name']} | {r['source']} | {r['split']} "
            f"| {r['expected']} | {r['predicted']}{mark} | {r['reason']} "
            f"| {r['detect']} | {r['align']} | {r['localization']} "
            f"| {r['found_defects']} |"
        )
    (out_root / "summary.md").write_text("\n".join(md), "utf-8")

    # ---- contact_sheet.png ----
    if montages:
        max_w = max(m.shape[1] for m in montages)
        padded = [
            cv2.copyMakeBorder(
                m, 0, 12, 0, max_w - m.shape[1],
                cv2.BORDER_CONSTANT, value=(20, 20, 20)
            )
            for m in montages
        ]
        cv2.imwrite(str(out_root / "contact_sheet.png"), cv2.vconcat(padded))

    # ---- console summary ----
    print("\n".join(md[:14]))
    print(f"\nartefacts: {out_root}")
    print("  summary.md · summary.csv · contact_sheet.png · <name>/montage.png")

    if metrics.false_accepts:
        print(
            f"\n!! {len(metrics.false_accepts)} FALSE ACCEPT(S): {metrics.false_accepts}"
            "\n   (DEFECTIVE images classified GOOD — primary failure mode)"
        )
    else:
        print("\nFALSE ACCEPTS: 0  ✓")


if __name__ == "__main__":
    main()
