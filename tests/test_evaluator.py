"""Tests for the Phase 1 batch evaluation harness (harness.py).

Coverage:
  - Metric math: hand-built confusion matrix → exact TP/FP/FN/TN / false-accepts
  - Manifest loading: valid v1, invalid label, invalid defect string, missing
    'file' key, legacy filename-prefix fallback (no manifest.json)
  - Split determinism: same seed → same split; different seed → (likely) different
  - API-vs-CLI parity: one GOOD and one DEFECTIVE image from synthetic_v1 must
    give identical status + defect set whether run through the FastAPI TestClient
    (HTTP upload) or through harness.run_entry() (direct file call)
  - Import smoke: harness imports cleanly alongside the existing test suite

All results that come from synthetic_v1 images are labelled SYNTHETIC.
These are smoke tests, NOT real-world accuracy benchmarks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# harness imports
# ---------------------------------------------------------------------------
from harness import (
    ImageEntry,
    Manifest,
    ManifestError,
    Metrics,
    RunResult,
    assign_splits,
    compute_metrics,
    filter_split,
    load_manifest,
)

# ---------------------------------------------------------------------------
# Helpers — build a fake InspectionResult for metric tests
# ---------------------------------------------------------------------------

def _make_result(status: str, defects=None):
    """Return a minimal MagicMock shaped like InspectionResult."""
    from app.schemas.inspection import InspectionStatus
    r = MagicMock()
    r.status.value = status
    r.defects = defects or []
    return r


def _make_rr(label: str, predicted: str, *, file="img.png",
              defects=None) -> RunResult:
    """Build a RunResult with a MagicMock result for metric testing."""
    entry = ImageEntry(
        file=file, label=label, expected_defects=[],
        source="synthetic", split="tuning",
    )
    result = _make_result(predicted, defects)
    return RunResult(entry=entry, result=result, time_ms=10, peak_memory_kb=100)


# ===========================================================================
# 1. Metric math on a hand-built confusion matrix
# ===========================================================================

class TestMetricsConfusionMath:
    """Exactly 5 images: 2 TP, 1 FP, 1 FN, 1 TN.

    Expected:
        TP=2  FP=1  FN=1  TN=1
        precision = 2/(2+1) = 0.6667
        recall    = 2/(2+1) = 0.6667
        F1        = 0.6667
        accuracy  = 3/5 = 0.6
        false_accepts = 1  (DEFECTIVE → GOOD)
        false_positives_on_good = 1  (GOOD → DEFECTIVE)
    """

    @pytest.fixture()
    def metrics(self):
        rrs = [
            _make_rr("DEFECTIVE", "DEFECTIVE", file="tp1.png"),   # TP
            _make_rr("DEFECTIVE", "DEFECTIVE", file="tp2.png"),   # TP
            _make_rr("GOOD",      "DEFECTIVE", file="fp1.png"),   # FP (false positive on good)
            _make_rr("DEFECTIVE", "GOOD",      file="fn1.png"),   # FN (false accept!)
            _make_rr("GOOD",      "GOOD",      file="tn1.png"),   # TN
        ]
        return compute_metrics(rrs)

    def test_confusion_counts(self, metrics):
        assert metrics.tp == 2
        assert metrics.fp == 1
        assert metrics.fn == 1
        assert metrics.tn == 1

    def test_false_accepts(self, metrics):
        assert len(metrics.false_accepts) == 1
        assert metrics.false_accepts[0] == "fn1.png"

    def test_false_positives_on_good(self, metrics):
        assert len(metrics.false_positives_good) == 1
        assert metrics.false_positives_good[0] == "fp1.png"

    def test_correct_count(self, metrics):
        assert metrics.correct == 3  # tp1, tp2, tn1
        assert metrics.n == 5

    def test_accuracy(self, metrics):
        assert abs(metrics.accuracy - 3 / 5) < 1e-9

    def test_precision(self, metrics):
        assert abs(metrics.precision - 2 / 3) < 1e-9

    def test_recall(self, metrics):
        assert abs(metrics.recall - 2 / 3) < 1e-9

    def test_f1(self, metrics):
        p, r = 2 / 3, 2 / 3
        expected_f1 = 2 * p * r / (p + r)
        assert abs(metrics.f1 - expected_f1) < 1e-9

    def test_to_dict_keys(self, metrics):
        d = metrics.to_dict()
        for key in ("FALSE_ACCEPTS", "confusion", "accuracy", "precision_defective",
                    "recall_defective", "f1_defective", "false_positive_rate",
                    "false_negative_rate", "latency_p50_ms", "peak_memory_kb"):
            assert key in d, f"missing key: {key}"

    def test_source_breakdown(self, metrics):
        assert metrics.source_breakdown == {"synthetic": 5}

    def test_latency_fields(self, metrics):
        # All time_ms = 10 → p50 = 10
        assert metrics.p50_ms == 10.0

    def test_empty_run_results(self):
        m = compute_metrics([])
        assert m.n == 0
        assert m.accuracy == 0.0
        assert m.precision == 0.0


# ===========================================================================
# 2. Manifest loading
# ===========================================================================

class TestManifestLoading:

    def test_valid_manifest_loads(self, synthetic_v1_dir):
        """Load generated synthetic_v1 manifest — must return 10 entries."""
        ds_dir = synthetic_v1_dir
        m = load_manifest(ds_dir)
        assert isinstance(m, Manifest)
        assert len(m.images) == 10
        assert m.dataset == "synthetic_v1"
        assert m.source == "synthetic"

    def test_invalid_label_raises(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "bad", "source": "synthetic",
            "images": [{"file": "a.png", "label": "INVALID", "expected_defects": []}]
        }), "utf-8")
        with pytest.raises(ManifestError, match="label"):
            load_manifest(ds)

    def test_invalid_defect_string_raises(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "bad", "source": "synthetic",
            "images": [{"file": "a.png", "label": "DEFECTIVE",
                         "expected_defects": ["R1:NOT_A_REAL_TYPE"]}]
        }), "utf-8")
        with pytest.raises(ManifestError, match="expected_defect"):
            load_manifest(ds)

    def test_missing_file_key_raises(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "bad", "source": "synthetic",
            "images": [{"label": "GOOD", "expected_defects": []}]
        }), "utf-8")
        with pytest.raises(ManifestError, match="'file'"):
            load_manifest(ds)

    def test_invalid_top_level_source_raises(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "bad", "source": "UNKNOWN",
            "images": [{"file": "a.png", "label": "GOOD", "expected_defects": []}]
        }), "utf-8")
        with pytest.raises(ManifestError, match="source"):
            load_manifest(ds)

    def test_empty_images_list_raises(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "empty", "source": "synthetic", "images": []
        }), "utf-8")
        with pytest.raises(ManifestError, match="empty"):
            load_manifest(ds)

    def test_valid_defect_dict_format(self, tmp_path):
        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "manifest.json").write_text(json.dumps({
            "dataset": "ok", "source": "synthetic",
            "images": [{
                "file": "a.png", "label": "DEFECTIVE",
                "expected_defects": [
                    {"ref_des": "R1", "type": "MISSING_COMPONENT"}
                ]
            }]
        }), "utf-8")
        m = load_manifest(ds)
        assert len(m.images) == 1
        assert m.images[0].expected_defects[0]["ref_des"] == "R1"

    def test_legacy_filename_prefix_fallback(self, tmp_path):
        """Directory with no manifest.json but good_/defect_ prefix files."""
        ds = tmp_path / "legacy"
        imgs = ds / "images"
        imgs.mkdir(parents=True)
        (imgs / "good_01.png").touch()
        (imgs / "good_02.png").touch()
        (imgs / "defect_missing_R1.png").touch()
        # no manifest.json
        m = load_manifest(ds)
        assert m.schema_version == 0
        labels = {e.label for e in m.images}
        assert "GOOD" in labels
        assert "DEFECTIVE" in labels
        assert len(m.images) == 3

    def test_compact_defects_helper(self, tmp_path):
        entry = ImageEntry(
            file="x.png", label="DEFECTIVE",
            expected_defects=[
                {"ref_des": "R1", "type": "MISSING_COMPONENT", "bbox": None},
                {"ref_des": "C4", "type": "SHIFTED_COMPONENT", "bbox": None},
            ],
            source="synthetic",
        )
        assert entry.compact_defects() == {"R1:MISSING_COMPONENT", "C4:SHIFTED_COMPONENT"}


# ===========================================================================
# 3. Split determinism
# ===========================================================================

class TestSplitDeterminism:

    def _entries(self, n=20):
        return [
            ImageEntry(file=f"img_{i:02d}.png", label="GOOD",
                       expected_defects=[], source="synthetic")
            for i in range(n)
        ]

    def test_same_seed_same_split(self):
        entries = self._entries()
        s1 = assign_splits(entries, seed=42)
        s2 = assign_splits(entries, seed=42)
        assert [e.split for e in s1] == [e.split for e in s2]

    def test_different_seed_different_split(self):
        entries = self._entries(30)
        s42 = [e.split for e in assign_splits(entries, seed=42)]
        s99 = [e.split for e in assign_splits(entries, seed=99)]
        # With 30 items two different seeds will almost certainly differ somewhere.
        assert s42 != s99, "Different seeds produced identical splits — very unlikely"

    def test_heldout_fraction_approximate(self):
        entries = self._entries(100)
        splits = assign_splits(entries, seed=42, heldout_frac=0.3)
        heldout_n = sum(1 for e in splits if e.split == "heldout")
        # Allow ±10% tolerance on fraction
        assert 20 <= heldout_n <= 40, f"heldout_n={heldout_n} outside expected range"

    def test_filter_split_all(self):
        entries = self._entries(10)
        entries = assign_splits(entries, seed=42)
        result = filter_split(entries, "all")
        assert len(result) == 10

    def test_filter_split_tuning_subset(self):
        entries = assign_splits(self._entries(20), seed=42)
        tuning = filter_split(entries, "tuning")
        heldout = filter_split(entries, "heldout")
        assert len(tuning) + len(heldout) == 20
        assert all(e.split == "tuning" for e in tuning)
        assert all(e.split == "heldout" for e in heldout)

    def test_filter_limit(self):
        entries = assign_splits(self._entries(20), seed=42)
        limited = filter_split(entries, "all", limit=3)
        assert len(limited) == 3

    def test_overwrite_false_preserves_existing(self):
        entries = [
            ImageEntry(file="a.png", label="GOOD", expected_defects=[],
                       source="synthetic", split="heldout"),
        ]
        result = assign_splits(entries, seed=42, overwrite=False)
        assert result[0].split == "heldout"  # unchanged

    def test_overwrite_true_reassigns(self):
        entries = [
            ImageEntry(file="b.png", label="GOOD", expected_defects=[],
                       source="synthetic", split="heldout"),
        ]
        result = assign_splits(entries, seed=42, overwrite=True)
        # After overwrite the split must be 'tuning' or 'heldout' (algorithm decides)
        assert result[0].split in ("tuning", "heldout")


# ===========================================================================
# 4. API-vs-CLI parity
#    One GOOD and one DEFECTIVE image from synthetic_v1 must produce identical
#    status + defect set whether run through the FastAPI TestClient or through
#    harness.run_entry().
#    SYNTHETIC data — smoke test.
# ===========================================================================

class TestApiVsCliParity:
    """SYNTHETIC — smoke test only."""

    @pytest.fixture()
    def setup(self, reference):
        """Requires the 'reference' fixture (isolated env + synthetic PCB ref)."""
        cfg, gen = reference
        from app.config import get_settings
        get_settings.cache_clear()
        return cfg, gen

    def _cli_result(self, entry, ds_dir, cfg):
        from app.storage.reference_repository import ReferenceRepository
        profile = ReferenceRepository(cfg).load_profile(None)
        rr = run_entry(entry, ds_dir, profile, cfg)
        return rr.result

    def _api_result(self, img_path: Path, cfg):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        with open(img_path, "rb") as fh:
            resp = client.post(
                "/api/inspect",
                files={"image": (img_path.name, fh, "image/png")},
            )
        assert resp.status_code == 200, f"API error: {resp.status_code} {resp.text}"
        return resp.json()

    @pytest.mark.parametrize("label", ["GOOD", "DEFECTIVE"])
    def test_status_parity(self, label, reference, synthetic_v1_dir):
        """SYNTHETIC — API and CLI must produce identical status for same image.

        Uses the real synthetic_v1 dataset images so the pipeline has a real
        image to process. The reference fixture provides the PCB_MODEL_001
        reference in the isolated env.
        """
        cfg, gen = reference

        real_ds_dir = synthetic_v1_dir

        m = load_manifest(real_ds_dir)
        target_entry = None
        for entry in m.images:
            if entry.label == label and (real_ds_dir / entry.file).is_file():
                target_entry = entry
                break
        assert target_entry is not None, f"No {label} image found in synthetic_v1"

        img_path = real_ds_dir / target_entry.file

        # --- CLI path (direct file, uses reference fixture env) ---
        from harness import run_entry as _run_entry
        from app.storage.reference_repository import ReferenceRepository
        profile = ReferenceRepository(cfg).load_profile(None)
        rr = _run_entry(target_entry, real_ds_dir, profile, cfg)
        cli_status = rr.result.status.value
        cli_defects = {f"{d.component}:{d.type.value}" for d in rr.result.defects}

        # --- API path (TestClient with same env via create_app) ---
        from fastapi.testclient import TestClient
        from app.main import create_app
        client = TestClient(create_app())
        with open(img_path, "rb") as fh:
            resp = client.post(
                "/api/inspections/inspect",
                files={"file": (img_path.name, fh, "image/png")},
            )
        assert resp.status_code == 200, f"API returned {resp.status_code}: {resp.text}"
        api_data = resp.json()
        api_status = api_data["status"]
        api_defects = {f"{d['component']}:{d['type']}" for d in api_data.get("defects", [])}

        # PARITY assertion — same image must give identical status + defect set
        assert cli_status == api_status, (
            f"[SYNTHETIC] label={label}: CLI={cli_status} API={api_status} — parity failure"
        )
        assert cli_defects == api_defects, (
            f"[SYNTHETIC] label={label}: CLI defects={cli_defects} API defects={api_defects}"
        )


# ===========================================================================
# 5. Import smoke test
# ===========================================================================

class TestHarnessImport:

    def test_harness_imports_cleanly(self):
        """harness module must import without side-effects or errors."""
        import harness as h
        assert hasattr(h, "load_manifest")
        assert hasattr(h, "compute_metrics")
        assert hasattr(h, "assign_splits")
        assert hasattr(h, "filter_split")
        assert hasattr(h, "run_entry")
        assert hasattr(h, "metrics_to_markdown")
        assert hasattr(h, "ManifestError")

    def test_manifest_error_is_value_error(self):
        with pytest.raises(ValueError):
            raise ManifestError("test")
