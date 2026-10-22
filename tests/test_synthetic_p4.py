"""SYNTHETIC dataset generator checks (Phase 4B step 5)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

import make_synthetic_p4 as p4
import make_synthetic_pcb as g


def test_synthetic_v1_flat_boards_unchanged():
    """Regression gate: the v1 scenes render byte-identically to the pre-4B generator."""
    want = json.loads((Path(__file__).parent / "fixtures" / "synthetic_v1_flat_boards.sha256.json").read_text())
    got = {n: hashlib.sha256(g.render_board(**b).tobytes()).hexdigest() for n, l, b, p, e, nt in g.SCENES}
    assert got == want


def test_p4_scene_inventory_and_labels():
    names = [s[0] for s in p4.SCENES_P4]
    assert len(names) == 11 and len(set(names)) == 11
    assert sum(1 for s in p4.SCENES_P4 if s[1] == "GOOD") == 3
    for s in p4.SCENES_P4:
        assert s[1] in {"GOOD", "DEFECTIVE", "REVIEW"}
        assert (s[1] == "GOOD") == (s[4] == [])


def test_generator_options_change_pixels_only_where_intended():
    base = g.render_board(seed=5)
    ext = g.render_board(seed=5, extra=p4.EXTRA_CAP)
    diff = np.abs(base.astype(int) - ext.astype(int)).sum(axis=2) > 12
    ys, xs = np.nonzero(diff)
    assert xs.min() >= 350 - 2 and xs.max() <= 350 + 46 + 2
    assert ys.min() >= 150 - 2 and ys.max() <= 150 + 46 + 2
    # polarity glyph differs from the plain one only inside the LED box
    a = g.render_board(seed=5, polarity=True)
    d = np.abs(base.astype(int) - a.astype(int)).sum(axis=2) > 12
    ys, xs = np.nonzero(d)
    assert xs.min() >= 700 and xs.max() <= 740 and ys.min() >= 300 and ys.max() <= 340


def test_flip_moves_the_pin1_mark_and_polarity_bar():
    plain = p4.render_p4(seed=5)
    flip = p4.render_p4(seed=5, rotate={"IC1": 180.0})
    # pin-1 dot at (x+14,y+14) of IC1 -> bright on plain, dark after the flip
    assert plain[110 + 14, 120 + 14].mean() > 150 > flip[110 + 14, 120 + 14].mean()
    led = p4.render_p4(seed=5, rotate={"LED1": 180.0})
    assert plain[320, 704].mean() > 200 > led[320, 704].mean()


def test_p4_regions_carry_design_rotation_and_led_checks_rotation():
    rs = {r.name: r for r in p4.regions_p4()}
    assert all(r.expected_rotation_deg == 0.0 for r in rs.values())
    assert rs["LED1"].tolerances.check_rotation is True
