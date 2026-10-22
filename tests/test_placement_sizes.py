"""Phase 4B step 3 (D1) — placement box sizes, parser fixes, angle convention.

[SYNTHETIC] marks computer-generated boards: smoke tests, not real-world benchmarks.
[UNVERIFIED] marks behaviour that has not been checked against a real KiCad export
or a real product.

Covers:
  * package table: flattened, KiCad "Library:Footprint" names normalised, case, fallback
  * sizes: file columns (A, .pos and CSV) over table (B) over flagged fallback; mm -> px
    via the design transform (scales with the image, anisotropy, flips, part rotation)
  * Region.expected_rotation_deg sign convention (documented in app.design.mapping)
  * parser: real KiCad export style (comment header, ###/##, ## End, top/bottom) and
    the col_map module-state leak
  * end to end [SYNTHETIC]: placement regions == hand-made regions, and the pipeline
    gives the same verdicts as with hand-made regions
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from app.design import parser as parser_mod
from app.design.mapping import (
    Fiducial,
    DesignTransform,
    compute_transform,
    components_to_regions,
    normalise_package_candidates,
    regions_from_dicts,
    screen_rotation_deg,
    _flatten_package_table,
    _load_package_table,
    _resolve_package,
)
from app.design.parser import (
    DesignComponent,
    PlacementParseError,
    parse_placement,
    parse_placement_file,
)

FIX = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]
TABLE_PATH = REPO / "data" / "policy" / "package_classes.json"


def _transform(scale: float = 8.333333, sx_flip: bool = False, aniso=(1.0, 1.0),
               rot_deg: float = 0.0, offset=(0.0, 0.0)) -> DesignTransform:
    th = math.radians(rot_deg)
    c, s = math.cos(th), math.sin(th)
    a = np.array([[scale * aniso[0] * c, -scale * aniso[1] * s],
                  [scale * aniso[0] * s, scale * aniso[1] * c]])
    if sx_flip:
        a[1, :] *= -1.0
    m = np.hstack([a, np.array(offset).reshape(2, 1)])
    return DesignTransform(matrix=m, residuals=[], rms_error_px=0.0, max_error_px=0.0)


@pytest.fixture(scope="module")
def table() -> dict:
    return _load_package_table(TABLE_PATH)


def _comp(pkg="SOIC-8", x=10.0, y=10.0, rot=0.0, size=None, ref="U1", side="F"):
    return DesignComponent(ref, "v", pkg, x, y, rot, side, size)


# ============================================================================ table
class TestPackageTable:
    def test_shipped_table_has_no_nested_groups_and_stays_unreviewed(self):
        raw = json.loads(TABLE_PATH.read_text("utf-8"))
        assert raw["_reviewed"] is False               # D8: stays unreviewed
        nested = [k for k, v in raw.items()
                  if isinstance(v, dict) and "expected_class" not in v]
        assert nested == []                            # _SYNTHETIC_PCB_DEMO is flattened

    def test_synthetic_entries_are_reachable(self, table):
        """(fails on old code) the *_SYNTH_* sizes were nested and unreachable."""
        r = _resolve_package("IC_SYNTH_150x110", table)
        assert not r.get("_is_fallback")
        assert (r["w_mm"], r["h_mm"]) == (18.0, 13.2)

    def test_legacy_nested_layout_is_still_flattened(self):
        raw = {"_note": "x", "R_1": {"expected_class": "resistor", "w_mm": 1, "h_mm": 1},
               "_GROUP": {"_note": "n", "C_1": {"expected_class": "cap", "w_mm": 2, "h_mm": 2}}}
        flat = _flatten_package_table(raw)
        assert set(flat) == {"R_1", "C_1"}

    @pytest.mark.parametrize("kicad,key", [
        ("Resistor_SMD:R_0603_1608Metric", "R_0603"),
        ("Capacitor_SMD:C_0805_2012Metric", "C_0805"),
        ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8"),
        ("Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", "PinHeader_1x06"),
        ("LED_THT:LED_D5.0mm", "LED_D5"),
        ("Package_QFP:LQFP-100_14x14mm_P0.5mm", "QFP-100"),
        ("r_0603_1608metric", "R_0603"),                    # case-insensitive
    ])
    def test_kicad_footprint_names_resolve_to_table_keys(self, table, kicad, key):
        """(fails on old code) KiCad names fell through to the one-letter heuristic."""
        r = _resolve_package(kicad, table)
        assert r.get("_key") == key and not r.get("_is_fallback"), (kicad, r)

    def test_normalisation_candidates_are_ordered_most_specific_first(self):
        c = normalise_package_candidates("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        assert c[0] == "SOIC-8_3.9x4.9mm_P1.27mm" and c[-1] == "SOIC-8"

    def test_unknown_footprint_is_a_flagged_fallback(self, table):
        r = _resolve_package("Weird_Lib:Frobnicator_9000", table)
        assert r["_is_fallback"] is True and r["expected_class"]

    def test_no_false_prefix_match_inside_a_token(self, table):
        # "C_0805x" must not match key "C_0805" (token boundary); falls back flagged
        assert _resolve_package("C_0805X", table).get("_is_fallback") is True


# ============================================================================ sizes
class TestSizes:
    def test_file_size_beats_table_beats_fallback(self, table):
        t = _transform()
        file_ = components_to_regions([_comp("SOIC-8", size=(18.0, 13.2))], t, table)[0]
        tab = components_to_regions([_comp("SOIC-8")], t, table)[0]
        fb = components_to_regions([_comp("Weird:Thing")], t, table)[0]
        assert (file_["_size_source"], tab["_size_source"], fb["_size_source"]) == (
            "file", "table", "fallback")
        assert file_["bbox"][2:] == (150, 110)          # 18.0 x 13.2 mm at 8.3333 px/mm
        assert tab["bbox"][2:] == (44, 34)              # SOIC-8 body 5.3 x 4.1 mm
        assert file_["_size_mm"] == (18.0, 13.2)

    def test_boxes_scale_with_the_image_through_the_transform(self, table):
        """mm -> px uses the transform scale: a 2x image gives 2x boxes, same centre mm."""
        a = components_to_regions([_comp("SOIC-8")], _transform(8.333333), table)[0]["bbox"]
        b = components_to_regions([_comp("SOIC-8")], _transform(16.666666), table)[0]["bbox"]
        assert b[2] == pytest.approx(2 * a[2], abs=1) and b[3] == pytest.approx(2 * a[3], abs=1)

    def test_anisotropic_transform_scales_each_axis(self, table):
        t = _transform(10.0, aniso=(1.0, 0.5))          # 10 px/mm in x, 5 px/mm in y
        w, h = components_to_regions([_comp(size=(4.0, 4.0))], t, table)[0]["bbox"][2:]
        assert (w, h) == (40, 20)

    def test_part_rotation_90_swaps_width_and_height(self, table):
        """(fails on old code) part rotation was ignored when building the box."""
        t = _transform(10.0)
        r0 = components_to_regions([_comp(size=(6.0, 2.0), rot=0)], t, table)[0]["bbox"]
        r90 = components_to_regions([_comp(size=(6.0, 2.0), rot=90)], t, table)[0]["bbox"]
        r270 = components_to_regions([_comp(size=(6.0, 2.0), rot=270)], t, table)[0]["bbox"]
        assert r0[2:] == (60, 20) and r90[2:] == (20, 60) and r270[2:] == (20, 60)
        # centre unchanged
        c = lambda b: (b[0] + b[2] / 2, b[1] + b[3] / 2)  # noqa: E731
        assert c(r0) == pytest.approx(c(r90), abs=1)

    def test_part_rotation_45_gives_larger_axis_aligned_box(self, table):
        t = _transform(10.0)
        w, h = components_to_regions([_comp(size=(6.0, 2.0), rot=45)], t, table)[0]["bbox"][2:]
        assert w == h == round((6 + 2) / math.sqrt(2) * 10)

    def test_flipped_transform_gives_positive_boxes_and_same_size(self, table):
        t = _transform(10.0, sx_flip=True, offset=(0.0, 500.0))
        x, y, w, h = components_to_regions([_comp(size=(6.0, 2.0))], t, table)[0]["bbox"]
        assert (w, h) == (60, 20) and w > 0 and h > 0

    def test_board_rotated_90_in_the_image_swaps_the_box(self, table):
        t = _transform(10.0, rot_deg=90.0)
        w, h = components_to_regions([_comp(size=(6.0, 2.0))], t, table)[0]["bbox"][2:]
        assert (w, h) == (20, 60)


# ============================================================================ angle
class TestAngleConvention:
    """Region.expected_rotation_deg = CCW-positive as seen on the reference image.

    File angle is CCW-positive in the board plane.  Mapping that flips handedness
    (y-up CAD -> y-down image, det<0) keeps the sense; one that does not (det>0) reverses
    it.  [UNVERIFIED on real boards.]
    """

    def test_flipped_mapping_keeps_the_sense(self):
        t = _transform(sx_flip=True)
        assert np.linalg.det(t.matrix[:, :2]) < 0
        assert screen_rotation_deg(90.0, t) == 90.0
        assert screen_rotation_deg(270.0, t) == -90.0

    def test_unflipped_mapping_reverses_the_sense(self):
        t = _transform()
        assert np.linalg.det(t.matrix[:, :2]) > 0
        assert screen_rotation_deg(90.0, t) == -90.0
        assert screen_rotation_deg(270.0, t) == 90.0

    def test_zero_and_180_are_unambiguous_and_never_negative_zero(self):
        for t in (_transform(), _transform(sx_flip=True)):
            z = screen_rotation_deg(0.0, t)
            assert z == 0.0 and math.copysign(1.0, z) == 1.0
            assert screen_rotation_deg(180.0, t) == 180.0

    def test_region_carries_the_angle(self, table):
        raw = components_to_regions([_comp(rot=90.0)], _transform(sx_flip=True), table)
        reg = regions_from_dicts(raw)[0]
        assert reg.expected_rotation_deg == 90.0

    def test_hand_made_regions_have_no_expected_rotation(self):
        import make_synthetic_pcb as gen
        assert all(r.expected_rotation_deg is None for r in gen.regions())


# ============================================================================ parser
class TestKiCadExportStyle:
    """[UNVERIFIED] fixture built BY HAND from the documented KiCad .pos layout; a real
    export has not been tested - please supply one."""

    path = FIX / "kicad_style_handwritten.pos"

    def test_comment_header_comments_end_marker_and_top_bottom(self):
        """(fails on old code) the real layout was rejected at the first line."""
        comps = parse_placement_file(self.path)
        by = {c.ref_des: c for c in comps}
        assert len(comps) == 7
        assert by["R1"].side == "F" and by["C9"].side == "B"      # top / bottom
        assert by["U1"].rotation_deg == 180.0 and by["C9"].rotation_deg == 270.0
        assert by["R1"].package == "Resistor_SMD:R_0603_1608Metric"
        assert by["FID1"].is_fiducial()

    def test_autodetected_without_a_pos_filename(self):
        comps = parse_placement(self.path.read_text("utf-8"), filename="export.txt")
        assert len(comps) == 7

    @pytest.mark.parametrize("word,expect", [("top", "F"), ("TOP", "F"), ("front", "F"),
                                             ("bottom", "B"), ("Back", "B"), ("F", "F"), ("B", "B")])
    def test_side_spellings(self, word, expect):
        text = f"# Ref Val Package PosX PosY Rot Side\nR1 1k R_0603 1 2 0 {word}\n## End\n"
        assert parse_placement(text, fmt="kicad")[0].side == expect

    def test_bad_side_still_raises(self):
        text = "# Ref Val Package PosX PosY Rot Side\nR1 1k R_0603 1 2 0 sideways\n"
        with pytest.raises(PlacementParseError, match="Side"):
            parse_placement(text, fmt="kicad")

    def test_export_style_with_no_header_still_raises(self):
        with pytest.raises(PlacementParseError, match="header"):
            parse_placement("## just comments\n## End\n", fmt="kicad")

    def test_end_to_end_regions_resolve_kicad_names_and_rotation(self, table):
        comps = [c for c in parse_placement_file(self.path) if not c.is_fiducial()]
        regs = {r["name"]: r for r in components_to_regions(comps, _transform(sx_flip=True), table)}
        assert regs["R1"]["_pkg_fallback"] is False and regs["R1"]["expected_class"] == "resistor"
        assert regs["R1"]["bbox"][3] > regs["R1"]["bbox"][2]     # 90 deg: portrait
        assert regs["U1"]["expected_class"] == "ic" and regs["LED1"]["expected_class"] == "led"
        assert "C9" not in regs                                  # bottom side filtered out


class TestSizeColumns:
    def test_pos_size_columns(self):
        c = parse_placement_file(FIX / "synthetic_pcb_sized.pos")
        by = {x.ref_des: x for x in c}
        assert by["IC1"].size_mm == (18.0, 13.2) and by["FID1"].size_mm is None

    def test_csv_size_columns(self):
        """(fails on old code) the CSV path always set size_mm=None."""
        c = parse_placement_file(FIX / "synthetic_pcb_sized.csv")
        by = {x.ref_des: x for x in c}
        assert by["R17"].size_mm == (3.12, 8.4) and by["FID1"].size_mm is None

    @pytest.mark.parametrize("w,h", [("0", "1"), ("-2", "1"), ("abc", "1"), ("nan", "1"),
                                     ("inf", "1"), ("", "")])
    def test_bad_sizes_are_ignored_not_fatal(self, w, h):
        pos = f"Ref Val Package PosX PosY Rot Side SizeX SizeY\nR1 1k R_0603 1 2 0 F {w or 'x'} {h or 'x'}\n"
        csv = f"Ref,Package,PosX,PosY,w_mm,h_mm\nR1,R_0603,1,2,{w},{h}\n"
        assert parse_placement(pos, fmt="kicad")[0].size_mm is None
        assert parse_placement(csv, fmt="csv")[0].size_mm is None

    def test_csv_without_size_columns_is_unchanged(self):
        c = parse_placement_file(FIX / "synthetic_pcb.csv")
        assert all(x.size_mm is None for x in c)


class TestColMapDoesNotLeak:
    def test_module_alias_table_is_not_mutated(self):
        """(fails on old code) col_map inserted into the module-level list on every call."""
        before = copy.deepcopy(parser_mod._CSV_COL_ALIASES)
        csv = "Part,X,Y\nR1,1,2\n"
        for _ in range(3):
            parse_placement(csv, fmt="csv", col_map={"ref_des": "Part"})
        assert parser_mod._CSV_COL_ALIASES == before

    def test_alias_from_one_call_does_not_affect_the_next(self):
        csv = "Part,X,Y\nR1,1,2\n"
        parse_placement(csv, fmt="csv", col_map={"ref_des": "Part"})
        with pytest.raises(PlacementParseError, match="missing required"):
            parse_placement(csv, fmt="csv")             # no col_map -> 'Part' is unknown again

    def test_col_map_still_works_within_its_call(self):
        out = parse_placement("Part,X,Y\nR1,1,2\n", fmt="csv", col_map={"ref_des": "Part"})
        assert out[0].ref_des == "R1"


# ============================================================================ e2e
def _fids():
    d = json.loads((FIX / "synthetic_pcb_fiducials.json").read_text("utf-8"))["fiducials"]
    return compute_transform([Fiducial(f["name"], tuple(f["design_mm"]), tuple(f["ref_px"])) for f in d])


def _regions_from(fixture: str, table: dict):
    comps = [c for c in parse_placement_file(FIX / fixture) if not c.is_fiducial()]
    return {r.name: r for r in regions_from_dicts(components_to_regions(comps, _fids(), table))}


@pytest.mark.parametrize("fixture,source", [
    ("synthetic_pcb_sized.pos", "file"),
    ("synthetic_pcb_sized.csv", "file"),
    ("synthetic_pcb_synthpkg.pos", "table"),
])
def test_placement_regions_equal_hand_made_regions(table, fixture, source):
    """[SYNTHETIC] D1 acceptance: every generated box equals the hand-made box (<= 1 px)."""
    import make_synthetic_pcb as gen
    mine = _regions_from(fixture, table)
    for h in gen.regions():
        g = mine[h.name]
        assert g.expected_class == h.expected_class, h.name
        for a, b in zip(g.bbox, h.bbox):
            assert abs(a - b) <= 1, (fixture, h.name, g.bbox, h.bbox)
    assert len(mine) == 11


def test_original_fixture_with_real_package_names_still_has_small_boxes(table):
    """[SYNTHETIC] Honest limit: real package names -> unreviewed BODY sizes, which are
    much smaller than the hand-made pad-inclusive boxes.  This is a table-calibration
    question (or use size columns); it is documented, not hidden."""
    import make_synthetic_pcb as gen
    mine = _regions_from("synthetic_pcb.pos", table)
    hand = {r.name: r for r in gen.regions()}
    ratios = [mine[n].bbox[2] * mine[n].bbox[3] / (hand[n].bbox[2] * hand[n].bbox[3]) for n in hand]
    assert min(ratios) < 0.2 and max(ratios) > 0.9      # R/C/IC tiny, LED close


@pytest.fixture()
def placement_reference(reference, table):
    """The synthetic reference, but with regions generated from a placement file."""
    from app.storage.reference_repository import ReferenceRepository

    def make(fixture: str):
        env, gen = reference
        repo = ReferenceRepository(env)
        regs = list(_regions_from(fixture, table).values())
        repo.set_regions(gen.REF_ID, regs)
        repo.set_placement_transform(gen.REF_ID, _fids().matrix_list)
        return env, gen, repo.load_profile(gen.REF_ID)
    return make


@pytest.mark.parametrize("fixture", ["synthetic_pcb_sized.pos", "synthetic_pcb_synthpkg.pos"])
def test_pipeline_verdicts_with_placement_regions_match_hand_made(placement_reference, fixture):
    """[SYNTHETIC] good boards stay GOOD and real defects keep their TYPE (D1 acceptance)."""
    from app.inspection.pipeline import run_inspection

    env, gen, profile = placement_reference(fixture)
    good = run_inspection(gen.photograph(gen.render_board(seed=2), seed=101, angle=6), profile, env)
    assert good.status.value == "GOOD" and not good.defects, [d.component for d in good.defects]

    shifted = run_inspection(
        gen.photograph(gen.render_board(seed=4, shift={"C4": (22, 14)}), seed=106, angle=4), profile, env)
    assert shifted.status.value == "DEFECTIVE"
    assert {(d.component, d.type.value) for d in shifted.defects} == {("C4", "SHIFTED_COMPONENT")}

    missing = run_inspection(
        gen.photograph(gen.render_board(seed=3, skip={"R17"}), seed=104, angle=5), profile, env)
    assert {(d.component, d.type.value) for d in missing.defects} == {("R17", "MISSING_COMPONENT")}


def test_api_import_reports_size_sources(reference):
    """The import endpoint says where each region's size came from."""
    cfg, gen = reference
    fid = json.loads((FIX / "synthetic_pcb_fiducials.json").read_text("utf-8"))["fiducials"]
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.post(
        f"/api/references/{gen.REF_ID}/import_placement",
        data={"fiducials_json": json.dumps(fid), "dry_run": "true"},
        files={"placement_file": ("s.pos", (FIX / "synthetic_pcb_sized.pos").read_bytes(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["size_sources"] == {"file": 11, "table": 0, "fallback": 0}
