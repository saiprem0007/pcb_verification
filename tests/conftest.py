"""Shared fixtures — an isolated data dir with one synthetic reference."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AOI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AOI_OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("AOI_DB_PATH", str(tmp_path / "data" / "db" / "t.sqlite"))

    from app.config import get_settings

    get_settings.cache_clear()
    cfg = get_settings()

    from app.api import deps

    deps.reference_repo.cache_clear()
    deps.inspection_repo.cache_clear()
    return cfg


@pytest.fixture()
def reference(env):
    """Create + activate the synthetic PCB reference in the isolated env."""
    import make_synthetic_pcb as gen
    from app.storage.reference_repository import ReferenceRepository
    from app.schemas.reference import ProfileKind, ReferenceCreate

    repo = ReferenceRepository(env)
    repo.create(
        ReferenceCreate(
            id=gen.REF_ID, name="Test PCB", kind=ProfileKind.PCB,
            regions=gen.regions(), mm_per_px=0.12,
        ),
        gen.render_board(seed=1),
    )
    repo.activate(gen.REF_ID)
    return env, gen


@pytest.fixture(scope="session")
def synthetic_v1_dir(tmp_path_factory):
    """SYNTHETIC dataset ``synthetic_v1`` generated into a temp dir.

    Tests no longer depend on a pre-generated, git-ignored ``data/datasets``.
    Generation is deterministic (seeded), same code as make_synthetic_pcb.py.
    """
    import make_synthetic_pcb as gen
    ds = tmp_path_factory.mktemp("datasets") / gen.DATASET
    cases = [
        (name, gen.photograph(gen.render_board(**bkw), **pkw), label, expected, note)
        for name, label, bkw, pkw, expected, note in gen.SCENES
    ]
    gen.write_dataset(ds, gen.DATASET, gen.REF_ID, "test-generated synthetic dataset", cases)
    return ds
