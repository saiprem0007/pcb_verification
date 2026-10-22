"""SYNTHETIC dataset ``synthetic_p4`` + reference ``PCB_MODEL_P4`` (Phase 4B).

Everything here is simulated. It shows that the deterministic detectors can separate
the cases on clean synthetic boards; it says nothing about real-board error rates.
``synthetic_v1`` is untouched (separate dataset, separate reference).

    py scripts/make_synthetic_p4.py [--force]
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401
import make_synthetic_pcb as base
from app.config import get_settings
from app.schemas.reference import ProfileKind, Region, ReferenceCreate, RegionTolerances
from app.storage.reference_repository import ReferenceRepository

P4_REF_ID = "PCB_MODEL_P4"
P4_DATASET = "synthetic_p4"
P4_REF_SEED = 1


def render_p4(**kw):
    """Board with the asymmetric (polarity-marked) LED glyph."""
    kw.setdefault("polarity", True)
    return base.render_board(**kw)


def regions_p4() -> list[Region]:
    """Same layout as v1. LED1 now checks rotation (its glyph has a polarity mark);
    every region carries its design rotation (0 deg on this synthetic board)."""
    out: list[Region] = []
    for name, box, kind in base.COMPONENTS:
        tol = RegionTolerances(check_rotation=True)
        if kind == "connector":
            tol.position_tol_px = 12.0
        out.append(Region(name=name, bbox=box, expected_class=kind, tolerances=tol,
                          expected_rotation_deg=0.0))
    return out


EXTRA_CAP = [("EXTRA1", (350, 150, 46, 46), "cap")]
EXTRA_IC = [("EXTRA1", (330, 130, 110, 70), "ic")]

# name, label, board kwargs, photograph kwargs, expected_defects, note
SCENES_P4: list[tuple] = [
    ("good_p4_01", "GOOD", dict(seed=21), dict(seed=301, angle=5), [], "clean, polarity LED"),
    ("good_p4_02", "GOOD", dict(seed=22), dict(seed=302, angle=-7, scale=0.55), [], "smaller in frame"),
    ("good_p4_03", "GOOD", dict(seed=23), dict(seed=303, angle=3, glare=75), [], "stronger glare"),
    ("defect_extra_cap", "REVIEW", dict(extra=EXTRA_CAP, seed=24), dict(seed=304, angle=4),
     ["EXTRA1:UNEXPECTED_COMPONENT"], "extra 46x46 cap on bare board"),
    ("defect_extra_ic", "REVIEW", dict(extra=EXTRA_IC, seed=25), dict(seed=305, angle=-5),
     ["EXTRA1:UNEXPECTED_COMPONENT"], "extra IC-sized part"),
    ("defect_wrong_IC1", "DEFECTIVE", dict(replace={"IC1": "resistor"}, seed=26),
     dict(seed=306, angle=6), ["IC1:WRONG_COMPONENT"], "resistor body in the IC1 box"),
    ("defect_wrong_C4", "DEFECTIVE", dict(replace={"C4": "resistor"}, seed=27),
     dict(seed=307, angle=-4), ["C4:WRONG_COMPONENT"], "resistor body in the C4 box"),
    ("defect_size_IC1_small", "REVIEW", dict(size_scale={"IC1": 0.6}, seed=28),
     dict(seed=308, angle=5), ["IC1:SIZE_DEVIATION"], "IC1 at 60% size"),
    ("defect_size_IC1_big", "REVIEW", dict(size_scale={"IC1": 1.25}, seed=29),
     dict(seed=309, angle=-6), ["IC1:SIZE_DEVIATION"], "IC1 at 125% size"),
    ("defect_flip_IC1", "DEFECTIVE", dict(rotate={"IC1": 180.0}, seed=30),
     dict(seed=310, angle=4), ["IC1:ROTATED_COMPONENT"], "IC1 rotated 180 (pin-1 moves)"),
    ("defect_polarity_LED1", "DEFECTIVE", dict(rotate={"LED1": 180.0}, seed=31),
     dict(seed=311, angle=-3), ["LED1:ROTATED_COMPONENT"], "LED cathode bar flipped"),
]


def make_reference(repo: ReferenceRepository, force: bool) -> None:
    if force:
        try:
            repo.delete(P4_REF_ID)
        except Exception:  # noqa: BLE001
            pass
    try:
        repo.get(P4_REF_ID)
        print(f"reference {P4_REF_ID} already exists (use --force to recreate)")
    except Exception:  # noqa: BLE001
        repo.create(
            ReferenceCreate(id=P4_REF_ID, name="Synthetic PCB P4", kind=ProfileKind.PCB,
                            regions=regions_p4(), mm_per_px=0.12,
                            notes="SYNTHETIC board for Phase 4B detectors."),
            render_p4(seed=P4_REF_SEED),
        )
        print(f"created reference {P4_REF_ID}")


def build_cases():
    return [
        (n, base.photograph(render_p4(**bkw), **pkw), label, expected, note)
        for n, label, bkw, pkw, expected, note in SCENES_P4
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    cfg = get_settings()
    make_reference(ReferenceRepository(cfg), args.force)
    base.write_dataset(cfg.data_dir / "datasets" / P4_DATASET, P4_DATASET, P4_REF_ID,
                       "SYNTHETIC Phase 4B cases. Not a benchmark; not real boards.", build_cases())


if __name__ == "__main__":
    main()
