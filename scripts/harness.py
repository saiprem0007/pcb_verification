"""Batch evaluation harness — shared library.

Provides:
  - Manifest schema (v1 + backward-compatible v0 / filename-fallback)
  - Manifest validation with clear error messages
  - Split logic (fixed-seed, deterministic, reproducible)
  - Metric computation (FALSE ACCEPTS primary, plus confusion / localization /
    per-defect-type recall / severity-presence / latency percentiles / memory)
  - Parity helpers (API upload vs CLI run comparison)

No inspection-pipeline logic lives here.  This module only reads, splits,
runs and measures.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import tracemalloc
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.inspection import InspectionResult

# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------

_DEFECT_TYPE_RE = re.compile(
    r"^[A-Za-z0-9_\-]+:"
    r"(MISSING_COMPONENT|SHIFTED_COMPONENT|ROTATED_COMPONENT|"
    r"SIZE_DEVIATION|VISUAL_ANOMALY|WRONG_COMPONENT|UNEXPECTED_COMPONENT)$"
)

VALID_LABELS = {"GOOD", "DEFECTIVE", "REVIEW"}
VALID_SOURCES = {"synthetic", "real"}
VALID_SPLITS = {"tuning", "heldout", "all"}


class ManifestError(ValueError):
    """Raised when a manifest fails validation."""


def _parse_expected_defect(raw) -> dict:
    """Normalise one expected_defect entry to {ref_des, type, bbox?}."""
    if isinstance(raw, str):
        if not _DEFECT_TYPE_RE.match(raw):
            raise ManifestError(
                f"expected_defect string must be 'REF_DES:DEFECT_TYPE', got: {raw!r}"
            )
        ref_des, dtype = raw.split(":", 1)
        return {"ref_des": ref_des, "type": dtype, "bbox": None}
    if isinstance(raw, dict):
        if "ref_des" not in raw or "type" not in raw:
            raise ManifestError(
                f"expected_defect dict must have 'ref_des' and 'type' keys, got: {raw}"
            )
        if not _DEFECT_TYPE_RE.match(f"{raw['ref_des']}:{raw['type']}"):
            raise ManifestError(
                f"expected_defect ref_des:type invalid: {raw['ref_des']}:{raw['type']}"
            )
        bbox = raw.get("bbox")
        if bbox is not None:
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                    and all(isinstance(v, (int, float)) for v in bbox)):
                raise ManifestError(
                    f"expected_defect bbox must be [x, y, w, h] with numeric values, got: {bbox}"
                )
        return {"ref_des": raw["ref_des"], "type": raw["type"], "bbox": bbox}
    raise ManifestError(f"expected_defect must be a string or dict, got {type(raw).__name__}: {raw!r}")


@dataclass
class ImageEntry:
    """One image in a dataset manifest."""
    file: str                           # relative path from dataset root
    label: str                          # GOOD | DEFECTIVE | REVIEW
    expected_defects: list[dict]        # normalised: [{ref_des, type, bbox?}]
    source: str = "synthetic"           # "synthetic" | "real"
    split: str | None = None            # "tuning" | "heldout" | None (unassigned)
    product: str | None = None          # optional product_id
    stage_id: str | None = None         # optional stage_id
    note: str = ""

    def compact_defects(self) -> set[str]:
        """Return compact 'REF_DES:TYPE' strings (backward-compat comparison)."""
        return {f"{d['ref_des']}:{d['type']}" for d in self.expected_defects}


@dataclass
class Manifest:
    """Parsed and validated manifest."""
    dataset: str
    reference_id: str | None
    source: str                         # dataset-level default source
    note: str
    images: list[ImageEntry]
    schema_version: int = 1


def _validate_manifest_data(data: dict) -> None:
    """Validate top-level manifest fields. Raises ManifestError on problems."""
    if not isinstance(data.get("images"), list):
        raise ManifestError("manifest.json must have an 'images' list")
    if not data["images"]:
        raise ManifestError("manifest.json 'images' list must not be empty")

    ds_source = data.get("source", "synthetic")
    if ds_source not in VALID_SOURCES:
        raise ManifestError(
            f"manifest top-level 'source' must be one of {VALID_SOURCES}, got {ds_source!r}"
        )

    for i, img in enumerate(data["images"]):
        if not isinstance(img, dict):
            raise ManifestError(f"images[{i}] must be a dict")
        if "file" not in img:
            raise ManifestError(f"images[{i}] missing 'file'")
        label = img.get("label", "")
        if label not in VALID_LABELS:
            raise ManifestError(
                f"images[{i}] ({img['file']!r}) 'label' must be one of {VALID_LABELS}, "
                f"got {label!r}"
            )
        img_source = img.get("source", ds_source)
        if img_source not in VALID_SOURCES:
            raise ManifestError(
                f"images[{i}] ({img['file']!r}) 'source' must be one of {VALID_SOURCES}, "
                f"got {img_source!r}"
            )
        for j, ed in enumerate(img.get("expected_defects", [])):
            try:
                _parse_expected_defect(ed)
            except ManifestError as exc:
                raise ManifestError(
                    f"images[{i}] ({img['file']!r}) expected_defects[{j}]: {exc}"
                ) from exc


def load_manifest(ds_dir: Path, *, strict: bool = True) -> Manifest:
    """Load a manifest from ``ds_dir/manifest.json``.

    Falls back to filename-prefix inference (good_/defect_/review_) if no
    manifest.json exists — preserving backward compatibility with Phase 0.
    """
    mf = ds_dir / "manifest.json"
    if not mf.is_file():
        _PREFIX = {"good": "GOOD", "defect": "DEFECTIVE", "review": "REVIEW"}
        img_dir = ds_dir / "images" if (ds_dir / "images").is_dir() else ds_dir
        imgs = [
            ImageEntry(
                file=str(p.relative_to(ds_dir)),
                label=_PREFIX.get(p.stem.split("_", 1)[0], ""),
                expected_defects=[],
                source="synthetic",
            )
            for p in sorted(img_dir.glob("*"))
            if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ]
        return Manifest(
            dataset=ds_dir.name, reference_id=None, source="synthetic",
            note="inferred from filename prefix (no manifest.json)",
            images=imgs, schema_version=0,
        )

    data = json.loads(mf.read_text("utf-8"))
    if strict:
        _validate_manifest_data(data)

    ds_source = data.get("source", "synthetic")
    entries: list[ImageEntry] = []
    for img in data["images"]:
        img_source = img.get("source", ds_source)
        raw_eds = img.get("expected_defects", [])
        eds = [_parse_expected_defect(ed) for ed in raw_eds]
        entries.append(ImageEntry(
            file=img["file"],
            label=img.get("label", ""),
            expected_defects=eds,
            source=img_source,
            split=img.get("split"),
            product=img.get("product"),
            stage_id=img.get("stage_id"),
            note=img.get("note", ""),
        ))

    return Manifest(
        dataset=data.get("dataset", ds_dir.name),
        reference_id=data.get("reference_id"),
        source=ds_source,
        note=data.get("note", ""),
        images=entries,
        schema_version=data.get("schema_version", 1),
    )


# ---------------------------------------------------------------------------
# Split logic
# ---------------------------------------------------------------------------

def _stable_hash(s: str) -> float:
    """Map a string to [0, 1) deterministically using SHA-256."""
    h = hashlib.sha256(s.encode()).hexdigest()
    return int(h, 16) / (2 ** 256)


def assign_splits(
    entries: list[ImageEntry],
    *,
    seed: int = 42,
    heldout_frac: float = 0.3,
    overwrite: bool = False,
) -> list[ImageEntry]:
    """Assign 'tuning' or 'heldout' split labels deterministically.

    Uses a stable hash of ``seed:filename`` so the split is reproducible
    across machines and Python versions without seeding random.
    """
    result = []
    for e in entries:
        if e.split and not overwrite:
            result.append(e)
            continue
        h = _stable_hash(f"{seed}:{e.file}")
        split_label = "heldout" if h < heldout_frac else "tuning"
        result.append(ImageEntry(
            file=e.file, label=e.label, expected_defects=e.expected_defects,
            source=e.source, split=split_label, product=e.product,
            stage_id=e.stage_id, note=e.note,
        ))
    return result


def filter_split(
    entries: list[ImageEntry],
    split: str,
    stage_id: str | None = None,
    limit: int | None = None,
) -> list[ImageEntry]:
    """Return entries matching split ('tuning'|'heldout'|'all') and optional stage."""
    if split not in VALID_SPLITS:
        raise ValueError(f"split must be one of {VALID_SPLITS}, got {split!r}")
    result = []
    for e in entries:
        if split != "all":
            entry_split = e.split or "tuning"  # unassigned → tuning
            if entry_split != split:
                continue
        if stage_id is not None and e.stage_id != stage_id:
            continue
        result.append(e)
    if limit is not None:
        result = result[:limit]
    return result


# ---------------------------------------------------------------------------
# Running and measurement
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Result of running the engine on one image entry."""
    entry: ImageEntry
    result: "InspectionResult"
    time_ms: int
    peak_memory_kb: int


def run_entry(entry: ImageEntry, ds_dir: Path, profile, cfg) -> RunResult:
    """Run the inspection pipeline on one ImageEntry, measuring time + memory."""
    from app.inspection.pipeline import run_inspection
    from app.sources import FileSource

    img_path = ds_dir / entry.file
    tracemalloc.start()
    t0 = time.perf_counter()
    result = run_inspection(FileSource(img_path).read(), profile, cfg)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return RunResult(
        entry=entry, result=result,
        time_ms=elapsed_ms, peak_memory_kb=peak // 1024,
    )


# ---------------------------------------------------------------------------
# Localization matching
# ---------------------------------------------------------------------------

def _iou(a: list, b: list) -> float:
    """IoU between two [x, y, w, h] boxes."""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def localization_match(expected: dict, result: "InspectionResult", *, iou_threshold: float = 0.3) -> bool:
    """Return True if the expected defect is matched in the result.

    Match criteria: ref_des AND type match; if expected bbox given, IoU >= threshold.
    """
    exp_ref = expected["ref_des"]
    exp_type = expected["type"]
    exp_bbox = expected.get("bbox")

    for d in result.defects:
        if d.component != exp_ref or d.type.value != exp_type:
            continue
        if exp_bbox is None:
            return True
        if d.bbox is not None:
            dx, dy = d.bbox[0], d.bbox[1]
            dw, dh = d.bbox[2] - d.bbox[0], d.bbox[3] - d.bbox[1]
            if _iou(exp_bbox, [dx, dy, dw, dh]) >= iou_threshold:
                return True
        return True   # ref_des+type match is enough when bbox comparison unavailable
    return False


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    """All computed metrics for a batch run."""
    n: int = 0
    source_breakdown: dict[str, int] = field(default_factory=dict)

    correct: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    false_accepts: list[str] = field(default_factory=list)
    false_positives_good: list[str] = field(default_factory=list)
    review_count: int = 0

    loc_total: int = 0
    loc_hits: int = 0
    type_expected: dict[str, int] = field(default_factory=dict)
    type_found: dict[str, int] = field(default_factory=dict)

    defects_with_severity: int = 0
    defects_without_severity: int = 0
    defects_with_action: int = 0
    defects_without_action: int = 0

    times_ms: list[int] = field(default_factory=list)
    peak_memory_kb_list: list[int] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def fpr(self) -> float:
        return self.fp / (self.fp + self.tn) if self.fp + self.tn else 0.0

    @property
    def fnr(self) -> float:
        return self.fn / (self.fn + self.tp) if self.fn + self.tp else 0.0

    @property
    def review_rate(self) -> float:
        return self.review_count / self.n if self.n else 0.0

    @property
    def localization_rate(self) -> float | None:
        return self.loc_hits / self.loc_total if self.loc_total else None

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.times_ms) if self.times_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.times_ms:
            return 0.0
        s = sorted(self.times_ms)
        idx = max(0, int(len(s) * 0.95) - 1)
        return float(s[idx])

    @property
    def peak_memory_kb(self) -> int:
        return max(self.peak_memory_kb_list) if self.peak_memory_kb_list else 0

    def per_type_recall(self) -> dict[str, float]:
        return {
            t: self.type_found.get(t, 0) / self.type_expected[t]
            for t in self.type_expected
            if self.type_expected[t] > 0
        }

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "source_breakdown": self.source_breakdown,
            "accuracy": round(self.accuracy, 4),
            "precision_defective": round(self.precision, 4),
            "recall_defective": round(self.recall, 4),
            "f1_defective": round(self.f1, 4),
            "false_positive_rate": round(self.fpr, 4),
            "false_negative_rate": round(self.fnr, 4),
            "review_rate": round(self.review_rate, 4),
            "FALSE_ACCEPTS": len(self.false_accepts),
            "false_accept_files": self.false_accepts,
            "false_positives_on_good": self.false_positives_good,
            "defect_localization": (
                round(self.localization_rate, 4) if self.localization_rate is not None else None
            ),
            "loc_hits": self.loc_hits,
            "loc_total": self.loc_total,
            "per_defect_type_recall": {k: round(v, 4) for k, v in self.per_type_recall().items()},
            "defects_with_severity": self.defects_with_severity,
            "defects_without_severity": self.defects_without_severity,
            "defects_with_corrective_action": self.defects_with_action,
            "defects_without_corrective_action": self.defects_without_action,
            "confusion": {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn},
            "latency_p50_ms": round(self.p50_ms, 1),
            "latency_p95_ms": round(self.p95_ms, 1),
            "peak_memory_kb": self.peak_memory_kb,
        }


def compute_metrics(run_results: list[RunResult]) -> Metrics:
    """Compute all metrics from a list of RunResult objects."""
    m = Metrics()
    for rr in run_results:
        e = rr.entry
        res = rr.result
        m.n += 1
        m.source_breakdown[e.source] = m.source_breakdown.get(e.source, 0) + 1
        m.times_ms.append(rr.time_ms)
        m.peak_memory_kb_list.append(rr.peak_memory_kb)

        got = res.status.value
        exp = e.label

        if got == exp:
            m.correct += 1
        if exp == "DEFECTIVE":
            if got == "DEFECTIVE":
                m.tp += 1
            else:
                m.fn += 1
                if got == "GOOD":
                    m.false_accepts.append(Path(e.file).name)
        else:
            if got == "DEFECTIVE":
                m.fp += 1
                if exp == "GOOD":
                    m.false_positives_good.append(Path(e.file).name)
            else:
                m.tn += 1
        if got == "REVIEW":
            m.review_count += 1

        for ed in e.expected_defects:
            dtype = ed["type"]
            m.type_expected[dtype] = m.type_expected.get(dtype, 0) + 1
            m.loc_total += 1
            if localization_match(ed, res):
                m.loc_hits += 1
                m.type_found[dtype] = m.type_found.get(dtype, 0) + 1

        for d in res.defects:
            if d.severity is not None:
                m.defects_with_severity += 1
            else:
                m.defects_without_severity += 1
            if d.corrective_action:
                m.defects_with_action += 1
            else:
                m.defects_without_action += 1

    return m


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _source_note(source: str) -> str:
    if source == "synthetic":
        return "[SYNTHETIC — smoke test only, not a real-world benchmark]"
    return "[REAL — field images]"


def metrics_to_markdown(
    m: Metrics,
    *,
    dataset: str,
    split: str,
    source: str,
    stage_id: str | None = None,
    rows: list[dict] | None = None,
) -> str:
    lines = [
        f"# Evaluation report — {dataset}",
        "",
        f"> **Source: {source.upper()}** {_source_note(source)}",
        f"> Split: `{split}` | Stage: `{stage_id or 'all'}` | N: {m.n}",
        "",
        "## Classification metrics",
        "",
        "| metric | value |",
        "|---|---|",
        f"| accuracy | {m.accuracy:.3f} ({m.correct}/{m.n}) |",
        f"| precision (DEFECTIVE) | {m.precision:.3f} |",
        f"| recall (DEFECTIVE) | {m.recall:.3f} |",
        f"| F1 (DEFECTIVE) | {m.f1:.3f} |",
        f"| false positive rate | {m.fpr:.3f} |",
        f"| false negative rate | {m.fnr:.3f} |",
        f"| REVIEW rate | {m.review_rate:.3f} ({m.review_count}/{m.n}) |",
        f"| **FALSE ACCEPTS** *(defective→GOOD, primary metric)* | **{len(m.false_accepts)}** |",
        f"| false positives on GOOD images | {len(m.false_positives_good)} |",
    ]
    if m.false_accepts:
        lines.append(f"| false-accept files | {', '.join(m.false_accepts)} |")
    lines += [
        "",
        "## Defect localization",
        "",
        "| metric | value |",
        "|---|---|",
        f"| localization rate (ref-des + type match) | "
        f"{'n/a' if m.localization_rate is None else f'{m.localization_rate:.3f} ({m.loc_hits}/{m.loc_total})'} |",
    ]
    if m.per_type_recall():
        lines += ["", "### Per-defect-type recall", "", "| type | recall |", "|---|---|"]
        for t, r in sorted(m.per_type_recall().items()):
            exp_n = m.type_expected.get(t, 0)
            fnd_n = m.type_found.get(t, 0)
            lines.append(f"| {t} | {r:.3f} ({fnd_n}/{exp_n}) |")
    lines += [
        "",
        "## Severity and corrective-action coverage",
        "",
        "| metric | value |",
        "|---|---|",
        f"| defects with severity annotated | {m.defects_with_severity} |",
        f"| defects WITHOUT severity | {m.defects_without_severity} |",
        f"| defects with corrective action | {m.defects_with_action} |",
        f"| defects WITHOUT corrective action | {m.defects_without_action} |",
        "",
        "## Performance",
        "",
        "| metric | value |",
        "|---|---|",
        f"| latency p50 | {m.p50_ms:.0f} ms |",
        f"| latency p95 | {m.p95_ms:.0f} ms |",
        f"| peak memory (max over images) | {m.peak_memory_kb} KB |",
    ]
    if rows:
        lines += [
            "",
            "## Per-image results",
            "",
            "| file | src | split | expected | predicted | reason | loc | defects found |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            mark = "" if r["expected"] == r["predicted"] else " ⚠"
            lines.append(
                f"| {r['file']} | {r['source']} | {r.get('split', '—')} "
                f"| {r['expected']} | {r['predicted']}{mark} | {r['reason']} "
                f"| {r['localization']} | {r['defects_found']} |"
            )
    return "\n".join(lines)
