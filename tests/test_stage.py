"""Tests for Phase 1 stage model — schema round-trips, DB migration, repo queries.

Test coverage
-------------
(a) Legacy reference (no stage fields) loads correctly — no regression.
(b) Reference with stage fields round-trips through JSON serialisation.
(c) list_by_product() returns references sorted by stage_order.
(d) load_profile_by_stage() finds the correct reference and raises for misses.
(e) DB migration: opening an old-schema SQLite file adds the new columns
    without data loss.
(f) stage_id + stage_name appear in InspectionResult when reference has them.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.config import Settings
from backend.app.schemas.reference import InspectionMode, ProfileKind, Reference, ReferenceCreate
from backend.app.schemas.inspection import InspectionResult
from backend.app.storage.db import _ADDITIVE_COLUMNS, connect, init_db
from backend.app.storage.reference_repository import ReferenceRepository, ReferenceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_image(h: int = 100, w: int = 100) -> np.ndarray:
    """Return a plain grey BGR image for test references."""
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _make_cfg(tmp_path: Path) -> Settings:
    """Settings pointing data / outputs / db into a temp directory."""
    return Settings(
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "db" / "test.sqlite",
        severity_policy_path=Path(__file__).parents[1] / "data" / "policy" / "severity_actions.json",
    )


# ---------------------------------------------------------------------------
# (a) Legacy reference (no stage fields) loads without regression
# ---------------------------------------------------------------------------

def test_legacy_reference_loads_with_none_stage_fields(tmp_path: Path) -> None:
    """A meta.json without stage keys must deserialise with all stage fields = None."""
    ref_dir = tmp_path / "ref" / "LEGACY_001"
    ref_dir.mkdir(parents=True)

    # Write a meta.json that has NO stage fields (mimics an old reference)
    legacy_meta = {
        "id": "LEGACY_001",
        "name": "Legacy Board",
        "kind": "PCB",
        "inspection_mode": "regions",
        "active": False,
        "image_path": "/media/references/LEGACY_001/reference.png",
        "regions": [],
        "mm_per_px": None,
        "created_at": "2025-01-01T00:00:00+00:00",
        "notes": "",
    }
    (ref_dir / "meta.json").write_text(json.dumps(legacy_meta), "utf-8")

    ref = Reference.model_validate_json((ref_dir / "meta.json").read_text("utf-8"))
    assert ref.id == "LEGACY_001"
    assert ref.product_id is None
    assert ref.stage_id is None
    assert ref.stage_name is None
    assert ref.stage_order is None


# ---------------------------------------------------------------------------
# (b) Reference with stage fields round-trips through JSON
# ---------------------------------------------------------------------------

def test_reference_with_stage_fields_round_trips() -> None:
    """Stage fields survive a model_dump_json → model_validate_json round-trip."""
    ref = Reference(
        id="STAGE_REF",
        name="Stage Board",
        kind=ProfileKind.PCB,
        inspection_mode=InspectionMode.REGIONS,
        active=False,
        image_path="/media/references/STAGE_REF/reference.png",
        regions=[],
        mm_per_px=None,
        created_at="2025-06-01T10:00:00+00:00",
        notes="",
        product_id="PRODUCT_A",
        stage_id="stage_smt",
        stage_name="SMT Placement",
        stage_order=1,
    )
    serialised = ref.model_dump_json()
    restored = Reference.model_validate_json(serialised)

    assert restored.product_id == "PRODUCT_A"
    assert restored.stage_id == "stage_smt"
    assert restored.stage_name == "SMT Placement"
    assert restored.stage_order == 1


# ---------------------------------------------------------------------------
# (c) list_by_product() returns references sorted by stage_order
# ---------------------------------------------------------------------------

def test_list_by_product_returns_sorted_stages(tmp_path: Path) -> None:
    """list_by_product() must return only matching product_id refs, in stage_order."""
    cfg = _make_cfg(tmp_path)
    repo = ReferenceRepository(cfg)
    img = _blank_image()

    # Create three stages for PRODUCT_X in non-sequential order
    for stage_order, stage_id, stage_name in [
        (3, "final_test",   "Final Test"),
        (1, "smt",          "SMT Placement"),
        (2, "wave_solder",  "Wave Soldering"),
    ]:
        spec = ReferenceCreate(
            id=f"PRODUCT_X_{stage_id}",
            name=stage_name,
            product_id="PRODUCT_X",
            stage_id=stage_id,
            stage_name=stage_name,
            stage_order=stage_order,
        )
        repo.create(spec, img)

    # Create a reference for a different product — must NOT appear
    spec_other = ReferenceCreate(
        id="PRODUCT_Y_stage1",
        name="Other Product",
        product_id="PRODUCT_Y",
        stage_id="stage1",
        stage_name="Stage 1",
        stage_order=1,
    )
    repo.create(spec_other, img)

    result = repo.list_by_product("PRODUCT_X")
    assert len(result) == 3, f"Expected 3 stages, got {len(result)}"
    orders = [r.stage_order for r in result]
    assert orders == [1, 2, 3], f"Not sorted by stage_order: {orders}"
    ids = [r.stage_id for r in result]
    assert ids == ["smt", "wave_solder", "final_test"]

    # Other product not mixed in
    result_y = repo.list_by_product("PRODUCT_Y")
    assert len(result_y) == 1


# ---------------------------------------------------------------------------
# (d) load_profile_by_stage() finds correct reference; raises on miss
# ---------------------------------------------------------------------------

def test_load_profile_by_stage_finds_and_raises(tmp_path: Path) -> None:
    """load_profile_by_stage() returns the right profile and raises on bad stage."""
    cfg = _make_cfg(tmp_path)
    repo = ReferenceRepository(cfg)
    img = _blank_image()

    spec = ReferenceCreate(
        id="PROD_STAGE1",
        name="Stage 1",
        product_id="MY_PRODUCT",
        stage_id="stage_1",
        stage_name="Stage One",
        stage_order=1,
        inspection_mode=InspectionMode.SURFACE,
    )
    repo.create(spec, img)

    # Should find it
    profile = repo.load_profile_by_stage("MY_PRODUCT", "stage_1")
    assert profile.reference.stage_id == "stage_1"
    assert profile.reference.product_id == "MY_PRODUCT"

    # Wrong stage_id → ReferenceError
    with pytest.raises(ReferenceError, match="No reference found"):
        repo.load_profile_by_stage("MY_PRODUCT", "nonexistent_stage")

    # Wrong product_id → ReferenceError
    with pytest.raises(ReferenceError, match="No reference found"):
        repo.load_profile_by_stage("WRONG_PRODUCT", "stage_1")


# ---------------------------------------------------------------------------
# (e) DB migration: old schema → new columns added, existing rows intact
# ---------------------------------------------------------------------------

def test_db_migration_adds_columns_without_data_loss(tmp_path: Path) -> None:
    """Create a DB with the OLD schema, insert a row, then run init_db.
    The new columns must be present and the old row must survive intact.
    """
    db_path = tmp_path / "old.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    OLD_SCHEMA = """
    CREATE TABLE IF NOT EXISTS inspections (
        inspection_id      TEXT PRIMARY KEY,
        timestamp          TEXT NOT NULL,
        reference_id       TEXT NOT NULL,
        profile_kind       TEXT NOT NULL,
        status             TEXT NOT NULL,
        reason             TEXT NOT NULL,
        overall_confidence REAL NOT NULL,
        inspection_time_ms INTEGER NOT NULL,
        defect_count       INTEGER NOT NULL,
        result_json        TEXT NOT NULL
    );
    """
    # Seed with old schema
    conn = sqlite3.connect(str(db_path))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO inspections VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("INS-001", "2025-01-01T00:00:00+00:00", "REF1", "PCB",
         "GOOD", "OK", 0.95, 250, 0, '{"result_type":"single"}'),
    )
    conn.commit()
    conn.close()

    # Confirm new columns do NOT exist yet
    conn = sqlite3.connect(str(db_path))
    old_cols = {row[1] for row in conn.execute("PRAGMA table_info(inspections)").fetchall()}
    conn.close()
    for col_name, _ in _ADDITIVE_COLUMNS:
        assert col_name not in old_cols, f"Column {col_name} should not exist before migration"

    # Run init_db (performs migration)
    init_db(db_path)

    # Verify new columns exist
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    new_cols = {row[1] for row in conn.execute("PRAGMA table_info(inspections)").fetchall()}
    for col_name, _ in _ADDITIVE_COLUMNS:
        assert col_name in new_cols, f"Column {col_name} missing after migration"

    # Verify old row still intact and new columns are NULL (not corrupted)
    row = conn.execute("SELECT * FROM inspections WHERE inspection_id = 'INS-001'").fetchone()
    assert row is not None, "Old row lost after migration!"
    assert row["inspection_id"] == "INS-001"
    assert row["status"] == "GOOD"
    assert row["stage_id"] is None   # NULL — expected for migrated rows
    assert row["unit_id"]  is None
    conn.close()


def test_db_migration_is_idempotent(tmp_path: Path) -> None:
    """Running init_db twice on the same DB must not raise (columns already exist)."""
    db_path = tmp_path / "idempotent.sqlite"
    init_db(db_path)
    init_db(db_path)  # must not raise


# ---------------------------------------------------------------------------
# (f) stage_id + stage_name appear in InspectionResult
# ---------------------------------------------------------------------------

def test_inspection_result_carries_stage_from_reference(tmp_path: Path) -> None:
    """run_inspection must propagate stage_id and stage_name from the reference."""
    from backend.app.inspection.pipeline import run_inspection
    from backend.app.profiles.base import InspectionProfile

    cfg = _make_cfg(tmp_path)
    img = _blank_image(200, 200)

    # Build a reference with stage metadata
    ref_dir = tmp_path / "data" / "references" / "STAGED_REF"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    (ref_dir / "reference.png").write_bytes(buf.tobytes())

    ref = Reference(
        id="STAGED_REF",
        name="Staged Reference",
        kind=ProfileKind.PCB,
        inspection_mode=InspectionMode.SURFACE,
        active=True,
        image_path="/media/references/STAGED_REF/reference.png",
        regions=[],
        mm_per_px=None,
        created_at="2025-01-01T00:00:00+00:00",
        notes="",
        product_id="PROD_TEST",
        stage_id="stage_001",
        stage_name="First Stage",
        stage_order=1,
    )
    profile = InspectionProfile(reference=ref, reference_dir=ref_dir)

    result: InspectionResult = run_inspection(
        img, profile, cfg, unit_id="UNIT-42"
    )

    assert result.stage_id == "stage_001", f"Expected stage_001, got {result.stage_id}"
    assert result.stage_name == "First Stage", f"Expected 'First Stage', got {result.stage_name}"
    assert result.unit_id == "UNIT-42", f"Expected UNIT-42, got {result.unit_id}"


def test_inspection_result_stage_is_none_for_legacy_reference(tmp_path: Path) -> None:
    """A reference with no stage fields → stage_id/stage_name/unit_id are all None."""
    from backend.app.inspection.pipeline import run_inspection
    from backend.app.profiles.base import InspectionProfile

    cfg = _make_cfg(tmp_path)
    img = _blank_image(200, 200)

    ref_dir = tmp_path / "data" / "references" / "LEGACY_REF"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    (ref_dir / "reference.png").write_bytes(buf.tobytes())

    ref = Reference(
        id="LEGACY_REF",
        name="Legacy Reference",
        kind=ProfileKind.PCB,
        inspection_mode=InspectionMode.SURFACE,
        active=True,
        image_path="/media/references/LEGACY_REF/reference.png",
        regions=[],
        mm_per_px=None,
        created_at="2025-01-01T00:00:00+00:00",
        notes="",
        # No stage fields — all default to None
    )
    profile = InspectionProfile(reference=ref, reference_dir=ref_dir)

    result: InspectionResult = run_inspection(img, profile, cfg)

    assert result.stage_id is None
    assert result.stage_name is None
    assert result.unit_id is None
