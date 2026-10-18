# Phase 4A — Deterministic Inspection Completeness: Analysis & Plan

> **STATUS: PLAN ONLY — awaiting your approval. No inspection logic, threshold,
> schema or test has been changed.** Everything below was measured on 2026-10-18 on
> scratch copies of `data/` outside the repo. The only things written inside the repo
> were gitignored generated files (the `synthetic_v1` images, caches, `node_modules`,
> `frontend/dist`) and this document.
>
> **Revision 2.** Supersedes the earlier draft of this file. Corrections to that draft
> are listed in §7.
>
> Labels: **[SYNTHETIC]** = computer-generated image, smoke test only, never a
> real-world benchmark · **SUPPORTED / PARTIAL / NOT SUPPORTED** · **UNVERIFIED** =
> reasoned but not measured.
>
> Acceptance targets: your message left the line `[EDIT THIS LINE …]` unedited, so all
> 7 targets in `docs/ACCEPTANCE.md` stay **PROPOSED**. Severity table, package table
> (`"_reviewed": false`) and stage progression are unchanged / deferred, as instructed.

---

## 0. Fix-ups (separate from Phase 4)

### Fix-up 1 — Vite and the Python schema

**Retraction:** the earlier claim that Vite "reads the Python schema" was wrong.
Vite/TypeScript never read the Python Pydantic models. `frontend/src/types.ts` is
hand-written and has to be kept in sync by hand.

**Check:** `Reference` in `frontend/src/types.ts` already has the field (line 171):

```ts
placement_transform?: number[][] | null;
```

so nothing needed adding. `npm run build` re-run this session (Node v22.22.0):

```
> tsc -b && vite build
✓ 45 modules transformed.   ✓ built in 2.06s      exit 0, 0 TypeScript errors
```

### Fix-up 2 — placement-region evaluation vs hand-made regions [SYNTHETIC]

`synthetic_v1`, **N = 10** (3 GOOD, 5 DEFECTIVE, 2 REVIEW). Same engine, same images,
same thresholds; only the region set differs. I reproduced both runs; they match the
numbers in your earlier report exactly.

| metric | hand-made regions | placement regions |
|---|---|---|
| accuracy | 1.000 (10/10) | 0.700 (7/10) |
| precision (DEFECTIVE) | 1.000 | 0.625 |
| recall (DEFECTIVE) | 1.000 | 1.000 |
| false-positive rate | 0.000 | 0.600 (3 GOOD → DEFECTIVE) |
| REVIEW rate | 0.200 | 0.200 |
| **FALSE ACCEPTS** | **0** | **0** |
| localization (REF_DES:TYPE found) | 7/7 | 2/7 |
| MISSING / SHIFTED / ROTATED recall | 3/3 · 2/2 · 2/2 | 2/3 · 0/2 · 0/2 |

| image | expected | hand-made | placement | expected defect (REF_DES:TYPE) | found hand / placement |
|---|---|---|---|---|---|
| good_01 | GOOD | GOOD | **DEFECTIVE** | — | — |
| good_02 | GOOD | GOOD | **DEFECTIVE** | — | — |
| good_03 | GOOD | GOOD | **DEFECTIVE** | — | — |
| defect_missing_R17 | DEFECTIVE | DEFECTIVE | DEFECTIVE | R17:MISSING_COMPONENT | 1/1 · 1/1 |
| defect_missing_C4 | DEFECTIVE | DEFECTIVE | DEFECTIVE | C4:MISSING_COMPONENT | 1/1 · 1/1 |
| defect_shifted_C4 | DEFECTIVE | DEFECTIVE | DEFECTIVE | C4:SHIFTED_COMPONENT | 1/1 · **0/1** |
| defect_rotated_IC2 | DEFECTIVE | DEFECTIVE | DEFECTIVE | IC2:ROTATED_COMPONENT | 1/1 · **0/1** |
| defect_multi | DEFECTIVE | DEFECTIVE | DEFECTIVE | C7:SHIFTED; IC1:ROTATED; R3:MISSING | 3/3 · **0/3** |
| review_blurred | REVIEW | REVIEW | REVIEW | — | — |
| review_off_fixture | REVIEW | REVIEW | REVIEW | — | — |

Primary metric holds (0 false accepts in both runs). In the placement run all 5
defective images are DEFECTIVE, but in 3 of them (shifted, rotated, multi) the real
defect is mis-typed as MISSING, and every image also carries spurious defects.

### Fix-up 3 — placement bbox size vs hand-made bbox size [SYNTHETIC]

Measured from `meta.json` of the hand-made reference vs the regions `import_placement`
generates from `tests/fixtures/synthetic_pcb.pos`:

| comp | hand-made W×H px | placement W×H px | W ratio | H ratio | **area ratio** |
|---|---|---|---|---|---|
| IC1 | 150×110 | 44×34 | 0.29 | 0.31 | **0.091** |
| IC2 | 120×90 | 44×34 | 0.37 | 0.38 | **0.139** |
| R1 / R2 / R3 | 70×26 | 15×8 | 0.21 | 0.31 | **0.066** |
| R17 | 26×70 | 15×8 | 0.58 | 0.11 | **0.066** |
| C1 / C4 / C7 | 46×46 | 20×12 | 0.43 | 0.26 | **0.113** |
| LED1 | 40×40 | 42×42 | 1.05 | 1.05 | 1.103 |
| J1 | 160×80 | 125×21 | 0.78 | 0.26 | 0.205 |

**Does it change detection? Yes.** The template and search window come from the region
box. A box at 7–15 % of the real area contains only part of the component, so the
template correlation falls under the missing threshold: good boards raise false
MISSING/SHIFTED, and real SHIFTED/ROTATED defects are reported as MISSING.

**Proof that size is the whole cause** [SYNTHETIC experiment, scratch only, thresholds
untouched]: I re-imported the same `.pos` using pad-inclusive sizes (the exact pixel
extents, as a calibrated table would give). Result: accuracy **1.000 (10/10)**,
false-positive rate 0.000, localization **7/7**, MISSING/SHIFTED/ROTATED recall all
1.000, FALSE ACCEPTS 0 — identical to the hand-made run. So the engine is fine; the
region boxes are not.

Three related findings:

1. The pad-inclusive entries already in `package_classes.json` (`_SYNTHETIC_PCB_DEMO`)
   are **nested under one key, so lookups never reach them** (`IC_SYNTH_150x110` falls
   to the generic 3×3 mm fallback).
2. Real KiCad footprint names (`Resistor_SMD:R_0603_1608Metric`) match nothing in the
   table and fall to the single-letter heuristic (resistor 2.0×1.0 mm).
3. `components_to_regions` ignores the design rotation when it builds the box, so a
   90° part gets a landscape box. The fixture marks R17 as rotation 0 although the
   hand-made R17 is portrait, so this is not exercised by any test.

**Proposed fix (no code changed; your choice needed — Decision D1):**

| Option | What | Pro | Con |
|---|---|---|---|
| **A. Size columns in the file** | Parser already reads optional W/H columns (cols 8–9 of the `.pos` path); the CSV path currently sets `size_mm=None` and would need the same | exact per footprint | needs a file that has them (standard KiCad `.pos` has no size columns — UNVERIFIED for your version) |
| **B. Size table calibrated to the reference image** | Un-nest/flatten the table, add a per-reference "visual footprint" scale (pads + silkscreen), and normalise KiCad `Library:Footprint_…` names to table keys | works with today's files; one-time calibration per product | approximation; needs a real-photo check |
| **C. Refine against the reference photo** | After import, grow each box by searching a ring around the placed centre in the reference image | adapts to the actual part | new pipeline step, more to test |

Recommendation: **B for the demo and for any file without sizes, A when the file has
them, C deferred.** Neither touches inspection thresholds.

### Fix-up 4 — "report mm values from the transform when one exists"

**Not implemented and not tested.**

- `pipeline.py` passes `profile.mm_per_px or cfg.mm_per_px` to the detector; the
  transform is never read there. `defect_detector.py:66` is the only place mm is
  computed (`dev_mm = offset_px * mm_per_px`) and only for SHIFTED.
- `import_placement` does **not** set the reference's `mm_per_px`. A reference that is
  created without a scalar and then gets a placement import therefore reports
  `deviation_mm = None` even though a transform exists.
- No test anywhere asserts `deviation_mm` (searched `tests/`).

**Proposed implementation (Phase 4B, if approved):**

1. If `reference.placement_transform` exists, rebuild the `DesignTransform` and derive
   mm from it. `scale_mm_to_px()` returns **pixels per mm**, so `mm_per_px =
   1 / scale_mm_to_px()` (a naive reuse would be inverted). For an anisotropic fit,
   convert the (dx, dy) offset per axis, then take the length.
2. Cross-check against the scalar; if they disagree by > 5 % record a warning in the
   result rather than silently choosing one.
3. Tests: transform present → mm from transform; transform absent → scalar unchanged;
   both present and disagreeing → warning; synthetic fixture (8.333 px/mm ⇔ 0.12
   mm/px) round-trips.
4. **UNVERIFIED risk:** the pipeline resizes the reference to ≤ 1280 px
   (`image_max_dim`) but regions and the transform are in original reference pixels.
   The synthetic reference is 900×640 so nothing breaks, but a larger real reference
   would put regions/mm on the wrong scale. I will test this in 4B before any mm value
   is trusted.

### Fix-up 5 — what `make_synthetic_pcb.py --force` changes under `data/references/`

Measured by running it against a copy and hashing every file before/after:

| path | changed? | what |
|---|---|---|
| `PCB_MODEL_001/meta.json` | **yes** | only `created_at` (`2026-10-17T04:36…` → now). `active` stays `true`; all 11 regions identical; `placement_transform` was `null` and stays `null` |
| `PCB_MODEL_001/reference.png` | rewritten, **byte-identical** | deterministic render |
| `BOX_MODEL_001/*`, `box/*` | no | `active` flags of the other references were already `false`, so activation did not touch them |

Caveats: `--force` is delete-then-create on the `PCB_MODEL_001` folder, so **anything
else you had added inside that folder, including an imported placement transform and
imported regions, is lost** (the 11 hand-made regions come back). The `--force` run
also regenerates `data/datasets/synthetic_v1/` (gitignored). Nothing was deleted by me.

---

## 1. Defect gap analysis (measured)

Method: the **real** pipeline run on `synthetic_v1`, `box_v1`, and extra synthetic
variants I generated in a scratch folder (each run on 2 photo conditions). Probe code
is not in the repo yet; §3 proposes committing it as proper generator extensions.

| Defect kind | Detected today? | Evidence | Gap | Status |
|---|---|---|---|---|
| **Missing component** | Yes | `synthetic_v1` MISSING recall 3/3; `box_v1` CAP/LABEL/BARCODE/SEAL 4/4 [SYNTHETIC] | none for these cases | **SUPPORTED** |
| **Shifted component** | Yes | C4 shifted ~26 px 1/1; label shifted 1/1 [SYNTHETIC]. mm only via the scalar (Fix-up 4) | mm from transform | **SUPPORTED** (px) |
| **Rotated component** | Only small angles | 12°/18° found 2/2 [SYNTHETIC]. Sweep is ±24°. **IC1 rotated 90° → reported MISSING (right verdict, wrong type); IC1 rotated 180° → GOOD, 2/2 photos** | large-angle and 180° flips | **PARTIAL** |
| **Wrong component** | Verdict by accident, type wrong | Part swapped for a different part (IC1→resistor, C4→resistor): DEFECTIVE but typed `MISSING_COMPONENT`, 4/4 runs. A true `WRONG_COMPONENT` exists only via YOLO fusion (`component_match.py:85`), tested with injected detections (`test_yolo_integration.py:77-89`); no detector ships | correct type without ML | **PARTIAL** |
| **Unexpected extra component** | **No — false accept** | Extra 46×46 cap, 110×70 IC, 22×9 resistor placed outside all regions: **GOOD in 6/6 runs**. YOLO path can emit it only with `yolo_flag_unexpected=True` (default off) and then `decide()` ignores it (see below) | no off-region check; decision hole | **NOT SUPPORTED** |
| **Size deviation** | **No producer** | `SIZE_DEVIATION` is defined, has a severity entry and a label, but nothing emits it. 60 % size → DEFECTIVE typed MISSING (4/4); 125 % → typed SHIFTED (2/2) | no extent measurement | **NOT SUPPORTED** |
| **Polarity / orientation** | **No — false accept at 180°** | IC1 rotated 180° → GOOD 2/2. The synthetic LED is a symmetric circle, so a flip is invisible and `check_rotation` is `false` for LED1. Design rotation (`DesignComponent.rotation_deg`) is parsed but never stored on regions or compared | design rotation not stored/compared; generator can't draw polarity | **NOT SUPPORTED** |
| **Surface anomaly** | Gross / colour only | Wrong cap colour → VISUAL_ANOMALY 1/1 [SYNTHETIC]. Small stain (r=18 px on IC1) → **GOOD 2/2**. Surface mode on the real `box` photo (thresholds: defect < 0.55, review < 0.63 SSIM): 120 px-radius stain → sim 0.976 GOOD; red 160×80 sticker → 0.956 GOOD; a completely different object → 0.666 GOOD [SYNTHETIC edits of a real photo] | local anomalies hide inside a global score | **PARTIAL** |
| **Crack / physical damage** | No | Thin dark line stand-in on IC1 → GOOD 2/2 (my drawing is a stand-in, not a real crack) | needs trained model + real images | **NOT SUPPORTED** → Phase 5 |
| **Solder defect** | No | Bridge stand-in between IC pins → GOOD 2/2 (stand-in only) | needs real high-resolution imagery / ML | **NOT SUPPORTED** → Phase 5 |
| **Structured expected vs observed** | Partial | free-text `expected`/`observed`; `expected_position`, `deviation_px`, `rotation_deg`; `deviation_mm` only for SHIFTED via scalar | machine-readable class/rotation/size, mm | **PARTIAL** |
| **Confirmed vs suspected** | No | no `certainty` field; every defect has one meaning | schema + decision rule | **NOT SUPPORTED** |
| **Stage on every defect** | Result-level only | `InspectionResult.stage_id` exists; `Defect` has none. `Defect.component` is the ref-des and `Defect.bbox` exists | `stage_id` per defect | **PARTIAL** |

### The decision-engine hole (found by these probes)

`decide()` only turns `CRITICAL_DEFECTS` (missing / shifted / rotated / wrong) and
`VISUAL_ANOMALY` into DEFECTIVE. A defect list containing **only**
`UNEXPECTED_COMPONENT` or **only** `SIZE_DEVIATION` falls through every branch and
returns **GOOD** (verified by calling `decide()` directly). Today that is latent
because nothing emits `SIZE_DEVIATION` and `UNEXPECTED_COMPONENT` needs a flag, but
the moment a producer is added the result would read "GOOD" with a defect listed.
This must be fixed **before** any new producer is added (see §4).

### Other measurements

On `synthetic_v1`: latency p50 434 ms, p95 456 ms, peak memory 89.5 MB (this sandbox).
`box_v1` against `BOX_MODEL_001` (region mode): 11/11, 0 false accepts, localization
7/7 [SYNTHETIC]. `box_v1` images against the real-photo `box` reference are a
mismatched pairing and are not used as evidence.

---

## 2. Deterministic approach per gap (and what is not realistic)

Feasibility numbers below are [SYNTHETIC]; the synthetic boards have much cleaner
noise than real ones, so **they show the approach can separate the cases, not what
error rate a real line will see.**

| Gap | Approach | Measured separation [SYNTHETIC] | Verdict it can safely give | Real-data need |
|---|---|---|---|---|
| **Unexpected extra component** | Difference of aligned image vs reference **outside the union of region boxes** (+14 px margin, minus a border band), grey blur, threshold, open, largest connected blob area; ignore anything under a min area | good boards: **0 px²** (9 board/photo combos); extra cap: **1,396–1,663 px²**; extra IC: **6,636–6,731 px²**; **tiny 22×9 resistor: 15 px² → not detected** (similar brightness to the board; a colour-space diff would be needed) | `suspected` → **REVIEW only**, never DEFECTIVE | Yes — reflections, shadows, stickers, flux will create blobs; threshold must be tuned on real boards |
| **Wrong component (no ML)** | When the template match fails, measure how much non-board material is in the region. Missing part → board visible; different part → material present | non-board fraction: present 1.0; **MISSING 0.0** (3/3 photos); **WRONG 1.0** (3/3 photos) | keep DEFECTIVE (both are blocking) but type it `WRONG_COMPONENT` with `observed_class = null` (class unknown without a detector) | Yes — on real boards a missing part leaves pads/solder, so the fraction is not 0 |
| **Wrong component (class known)** | existing YOLO fusion | tested with injected detections only | as today | model + real data |
| **Orientation / polarity** | Store the design rotation on the region. Compare the aligned patch to the reference patch at 0° and at 180° (and 90°/270°); a clearly better match at the flipped angle = orientation fault | IC1 normal: NCC@0 **0.81–0.94** vs @180 0.55–0.62; IC1 rotated 180°: NCC@0 0.56–0.63 vs @180 **0.79–0.89** (3/3 photos each way). Needs the margin rule `ncc@flip − ncc@0 > m` | confirmed DEFECTIVE when the margin is clear; REVIEW when both are close (symmetric parts) | Yes for the margin; polarity of really symmetric parts (resistors) is not decidable from shape |
| **Size deviation** | Extent (w×h) of the non-board blob in the region window ÷ reference extent | IC1: normal **0.99–1.01**, 60 % → **0.60/0.68**, 125 % → **1.21–1.26** (3/3 photos); **C4 is noisy** (an outlier 1.2 at normal size, 1.0 at 60 %) because silkscreen text merges into the blob | large parts only (ICs, connectors); `suspected` → REVIEW | Yes |
| **Local surface anomaly** | Per-region and per-tile diff in addition to the global SSIM | not measured — would need a tiled-diff probe first | `suspected` → REVIEW | Yes |
| **Structured expected-vs-observed** | Pure data plumbing from values already measured | n/a | none (no verdict change) | no |
| **Confirmed vs suspected** | Two independent signals agree → `confirmed`; one → `suspected` | n/a | see §4 rules | no |
| **Stage on defect** | copy `InspectionResult.stage_id` into each defect | n/a | none | no |
| **Cracks** | — | — | — | **Needs ML + real labelled images → Phase 5** |
| **Solder defects** | — | — | — | **Needs real high-res / X-ray-class imagery + ML → Phase 5** |

I will **not** claim crack, solder, tiny-part or small-stain detection in Phase 4.
They stay NOT SUPPORTED and must be listed as such in the docs and (proposed) on the
dashboard, because today those boards read GOOD.

---

## 3. Synthetic generator extensions (all labelled SYNTHETIC)

Changes go in `scripts/make_synthetic_pcb.py` (component drawing + a new scene list)
and `scripts/_scene.py` only if shared helpers are needed. **A new dataset
`synthetic_p4` is written so the existing `synthetic_v1` images and results stay
byte-for-byte unchanged** (regression gate). `_scene.py` exists today (earlier draft
said it did not).

| New image | Generator change | label | `expected_defects` |
|---|---|---|---|
| `defect_extra_cap` | draw an extra cap on bare board (e.g. 46×46 at 350,150) | REVIEW | `EXTRA1:UNEXPECTED_COMPONENT` |
| `defect_extra_ic` | extra IC-sized part (110×70) | REVIEW | `EXTRA1:UNEXPECTED_COMPONENT` |
| `defect_wrong_IC1` | draw a resistor body in the IC1 box | DEFECTIVE | `IC1:WRONG_COMPONENT` |
| `defect_wrong_C4` | draw a resistor body in the C4 box | DEFECTIVE | `C4:WRONG_COMPONENT` |
| `defect_size_IC1_small` | IC1 at 60 % of W×H | REVIEW | `IC1:SIZE_DEVIATION` |
| `defect_size_IC1_big` | IC1 at 125 % | REVIEW | `IC1:SIZE_DEVIATION` |
| `defect_flip_IC1` | IC1 rotated 180° (pin-1 mark moves) | DEFECTIVE | `IC1:ROTATED_COMPONENT` |
| `defect_polarity_LED1` | give the LED a cathode bar (the current glyph is symmetric) and rotate 180° | DEFECTIVE | `LED1:ROTATED_COMPONENT` |
| `good_p4_01..03` | clean boards incl. the new LED glyph and fresh noise seeds | GOOD | — |

Manifest notes: `expected_defects` accepts `REF_DES:TYPE` strings. The engine will
name off-region findings `EXTRA<n>` (stable order top-to-bottom, left-to-right) so the
existing ref-des pattern (`[A-Za-z0-9_-]+`) still validates; the dict form with a
`bbox` is used to pin position. Not representable in the generator, therefore **no
synthetic test for them**: crack, solder, real surface contamination.

**Harness bug to fix first (Decision D6):** `localization_match` in
`scripts/harness.py` never enforces the bbox IoU — a trailing `return True` makes any
ref-des+type match pass (verified: an expected box with IoU 0 still matches). No
current manifest uses a bbox, so no published number changes, but the new
`EXTRA<n>` entries rely on it.

---

## 4. Schema additions (all additive and optional)

`Defect` (`schemas/defect.py`) — new fields, all `None`/empty by default:

```python
certainty: Literal["confirmed", "suspected"] | None = None
signals: list[str] = []                      # e.g. ["template_corr", "orientation_ncc"]
stage_id: str | None = None
expected_class: str | None = None;   observed_class: str | None = None
expected_rotation_deg: float | None = None;  observed_rotation_deg: float | None = None
expected_size_px: tuple[float, float] | None = None;  observed_size_px: ... = None
expected_position_mm: tuple[float, float] | None = None; observed_position_mm: ... = None
# existing and reused: component (ref-des), bbox, expected_position, actual_position,
# deviation_px, deviation_mm, rotation_deg, allowed_*, severity, corrective_action
```

`Region` gets `expected_rotation_deg: float | None` (a property of the design, not a
tolerance — Decision D5; the earlier draft put it in `RegionTolerances`).
`InspectionResult` gets `review_reasons: list[{code, component?, detail}] = []` so
**every REVIEW carries a reason per cause**; new codes `SUSPECTED_DEFECT` and
`AMBIGUOUS_ORIENTATION` are added to `ReasonCode` (the frontend's `types.ts` copy
must be updated in the same change).

### Decision rules and invariants

```
alignment/quality failures                        -> REVIEW   (unchanged)
any confirmed defect of a blocking type            -> DEFECTIVE (unchanged behaviour)
any suspected defect, no confirmed one             -> REVIEW   (SUSPECTED_DEFECT + per-defect reason)
no defects, all checks passed                      -> GOOD
```

- **Severity can never change GOOD / DEFECTIVE / REVIEW.** Today this is true by
  construction (`apply_severity` runs before `decide()` and `decide()` never reads
  severity). The earlier draft's rule "suspected + CRITICAL severity → DEFECTIVE" would
  have broken this and is **removed**: status depends on defect **type and certainty
  only**. Test: shuffle/strip severity on the same defects → identical status.
- **Uncertain never becomes GOOD.** `decide()` becomes total: every `DefectType` must
  map to DEFECTIVE or REVIEW, and an unknown/new type defaults to **REVIEW**. Test:
  loop over **every** `DefectType` and assert the status is never GOOD — this test
  **fails on today's code** for `UNEXPECTED_COMPONENT` and `SIZE_DEVIATION`, which is
  the point.
- **Never downgrade an existing DEFECTIVE.** MISSING/SHIFTED/ROTATED/WRONG keep their
  current verdicts; new off-region and size checks may only add REVIEW, not remove a
  DEFECTIVE.
- Naming caution: `CRITICAL_DEFECTS` (decides status) and severity `CRITICAL` (policy
  label) are different things; shifted/rotated are in the former but `HIGH` in the
  latter. The plan keeps them separate and renames the set to `VERDICT_BLOCKING`
  (Decision D6).

---

## 5. Files, tests, acceptance, risks

### Files

| File | Change |
|---|---|
| `backend/app/inspection/decision_engine.py` | make `decide()` total; certainty rules; `review_reasons` |
| `backend/app/schemas/defect.py`, `schemas/reference.py`, `schemas/inspection.py` | additive fields above |
| `backend/app/inspection/defect_detector.py` | wrong-vs-missing refinement; orientation check; size check; populate structured fields + `stage_id` |
| **new** `backend/app/inspection/extras.py` | off-region difference ("unexpected component") |
| `backend/app/inspection/pipeline.py` | call the extras check; propagate `stage_id`; transform-derived mm (Fix-up 4) |
| `backend/app/design/mapping.py` / `parser.py` | flatten + normalise package table, size columns, rotation → store `expected_rotation_deg` (Fix-up 3, D1) |
| `backend/app/config.py` | new knobs (min blob area, orientation margin, size ratio band) — **new settings only; no existing threshold changes** |
| `scripts/make_synthetic_pcb.py`, `scripts/harness.py` | §3 images; IoU fix |
| `data/policy/severity_actions.json` | entries only if a new type is introduced (all 7 types already present) |
| `frontend/src/types.ts` | mirror new optional fields |
| docs: `ARCHITECTURE.md`, `ACCEPTANCE.md`, `README` | known-unsupported list; refresh stale module map |

### Tests (names indicative)

- `test_decision_engine_total` — every `DefectType` ≠ GOOD; unknown type → REVIEW.
- `test_severity_never_changes_status` — status identical with severity stripped/shuffled.
- `test_certainty_rules` — confirmed → DEFECTIVE; suspected only → REVIEW with reason; none → GOOD.
- `test_unexpected_component` — extra part above min area → `EXTRA1` suspected / REVIEW; clean boards (≥ 9 combos) → no blob; below-min part → nothing.
- `test_wrong_vs_missing` — empty region → MISSING; other part present → WRONG, still DEFECTIVE.
- `test_orientation_flip` — 180° IC → ROTATED with `expected/observed_rotation_deg`; symmetric/ambiguous → REVIEW.
- `test_size_deviation` — IC 60 % / 125 % → suspected → REVIEW; small passives skipped.
- `test_structured_fields` — class/rotation/size/position in px **and** mm when a transform exists.
- `test_deviation_mm_from_transform` — Fix-up 4 cases (incl. disagreement warning, resize risk).
- `test_stage_on_every_defect`.
- `test_localization_iou_enforced` — harness fix.
- `test_backward_compat` — all 148 existing tests unchanged; `synthetic_v1` statuses identical.

### Acceptance criteria (numbers from `docs/ACCEPTANCE.md`, all still PROPOSED)

| Check | Gate | Today [SYNTHETIC] |
|---|---|---|
| FALSE ACCEPTS on `synthetic_v1` | 0 | 0 |
| FALSE ACCEPTS on `synthetic_p4` (kinds this phase claims: extra, wrong, size, flip) | 0 | 8 of 8 probe runs are GOOD today (extra part 6, 180° flip 2); wrong-part and size probes are already DEFECTIVE (but mis-typed) |
| Uncertain → never GOOD | always (unit test over every `DefectType`) | **fails** for 2 types |
| `synthetic_v1` REVIEW rate | ≤ 0.20 | 0.20 (at the limit; new REVIEW-labelled images live in `synthetic_p4`, not `synthetic_v1`) |
| Severity / corrective action populated | 100 % | 100 % |
| Latency p50 / p95 | ≤ 2000 / ≤ 5000 ms | 434 / 456 ms; new checks budget +150 ms |
| Peak memory | ≤ 512 MB | 89.5 MB |
| Existing tests | 148/148 | 148/148 |
| `npm run build` | 0 TypeScript errors | 0 |

Recall ≥ 0.90, false-positive rate ≤ 0.15 and localization ≥ 0.70 are **real-image**
targets (≥ 30 images, ≥ 10 defective); they cannot be validated on synthetic data and
are not claimed here. Cracks, solder, tiny parts, small stains and bare-board scratches
are **excluded from the 0-false-accept claim and listed as known limitations.**

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Off-region check flags reflections, shadows, stickers, flux, board-edge clutter on real boards | **High** | REVIEW only (never DEFECTIVE); border/margin masks; min area from real data; configurable; ship disabled-by-default until tuned on real images |
| Real missing part leaves pads/solder → "missing vs wrong" fraction unreliable | Medium-High | verdict stays DEFECTIVE either way; only the type label changes; flagged UNVERIFIED |
| Size check noisy on small parts (measured on C4) | High | large parts only; REVIEW only |
| Orientation margin wrong for near-symmetric parts | Medium | ambiguous → REVIEW with reason, not GOOD |
| Making `decide()` total could raise REVIEW volume | Medium | only new/unknown types go to REVIEW; existing verdicts frozen; watch `synthetic_v1` = 0.20 |
| Schema additions break the frontend | Low | all fields optional; update `types.ts`; `npm run build` gate |
| Reference > 1280 px puts regions/mm on the wrong scale | UNVERIFIED | test in 4B first (Fix-up 4) |

---

## 6. Decisions needed before implementation

| # | Decision | Recommendation |
|---|---|---|
| **D1** | Bbox size fix: A (size columns), B (calibrated table + name normalisation), or both | B now + A when the file has sizes; C deferred |
| **D2** | mm from the transform: inside Phase 4B or as a separate small fix first | separate small fix first (it unblocks mm on every defect) |
| **D3** | Off-region "unexpected component" check: implement as REVIEW-only, or defer | implement, REVIEW-only, **off by default** until tuned on real images |
| **D4** | Size deviation: implement for ICs/connectors only, or defer | implement, large parts only, REVIEW-only |
| **D5** | Where the design rotation lives | `Region.expected_rotation_deg` (not tolerances) |
| **D6** | Fix the decision-engine hole + harness IoU bug **first**, as their own commit | yes — nothing else should land before this |
| **D7** | Emit `WRONG_COMPONENT` instead of `MISSING_COMPONENT` when the part is present but different (verdict unchanged) | yes |
| **D8** | Acceptance targets | your line is still the `[EDIT THIS LINE]` placeholder — please confirm "as written" or give changes |

### Found during this analysis, outside Phase 4A (not planned unless you ask)

- The KiCad `.pos` parser expects a plain header line and `F`/`B` sides; a file in the
  format I recall KiCad producing (`# Ref …` comment header, `top`/`bottom`) was
  **rejected** in my test — please check against a real export (UNVERIFIED).
- `col_map` in the generic CSV parser permanently appends to a module-level alias list.
- `ARCHITECTURE.md` and `CHECKLIST.md` are stale (nonexistent `profiles/registry.py`,
  `regions.json`, "22 passing").

## 7. Corrections to the earlier draft of this file

1. "`WRONG_COMPONENT` / `UNEXPECTED_COMPONENT` never emitted" — wrong: both are emitted
   by `component_match.fuse` when a detector is active (UNEXPECTED needs a flag).
2. "`_scene.py` does not exist" — it does.
3. Rule "suspected + CRITICAL severity → DEFECTIVE" — removed; it let severity change
   the verdict.
4. Fix-up 4 said to call `scale_mm_to_px()` for `mm_per_px` — that value is px per mm
   and must be inverted.
5. Fix-up 4 implied mm is always available through the scalar — it is only there if
   the reference was created with one.
6. `_SYNTHETIC_PCB_DEMO` was described as usable; it is unreachable.

---

> **R1 HARD STOP — Phase 4A plan only. No implementation started.**
> Awaiting your written approval and answers to D1–D8.
