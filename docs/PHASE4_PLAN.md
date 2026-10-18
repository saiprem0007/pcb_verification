# Phase 4A — Deterministic Inspection Completeness: Analysis & Plan

> **STATUS: PLAN ONLY — awaiting user approval. No code has been changed.**
> Label key: **IMPLEMENTED-AND-TESTED** | **IMPLEMENTED-UNTESTED** | **PLANNED** | **UNVERIFIED** | **NOT SUPPORTED**

---

## 0. Fix-up deliverables (separate from Phase 4)

### Fix-up 1 — `frontend/src/types.ts` correction

**Correction:** The previous report incorrectly stated "Vite reads the Python schema."
This is wrong. Vite/TypeScript do not read the Python schema at all.

**What actually happened:** The `placement_transform` field was present in the Python
`Reference` Pydantic model (serialised to JSON by FastAPI), but it was **missing**
from `frontend/src/types.ts`. The TypeScript compiler did not error because TypeScript
interface fields that are absent from an incoming JSON object are simply `undefined`
at runtime, not a compile-time error when no explicit type check was made.

**Fix applied (IMPLEMENTED-AND-TESTED):**

Added to the `Reference` interface in `frontend/src/types.ts`:
```typescript
// Phase 3: affine transform matrix [[a,b,c],[d,e,f]] from design-mm to ref-px;
//          null when no placement file has been imported
placement_transform?: number[][] | null;
```

Frontend build after fix:
```
> tsc -b && vite build
✓ 45 modules transformed.  built in 1.08s   exit 0
```
0 TypeScript errors.

---

### Fix-up 2 — Placement-region eval vs. hand-made-region eval [SYNTHETIC]

> [SYNTHETIC — smoke test only, not a real-world benchmark]
> N=10 (3 GOOD, 5 DEFECTIVE, 2 REVIEW).  Run 2026-10-17.

| metric | Hand-made regions | Placement regions | delta |
|---|---|---|---|
| **N** | 10 | 10 | — |
| **FALSE ACCEPTS** | **0** | **0** | = |
| Accuracy | 1.000 (10/10) | 0.700 (7/10) | −0.300 |
| Precision (DEFECTIVE) | 1.000 | 0.625 | −0.375 |
| Recall (DEFECTIVE) | 1.000 | 1.000 | = |
| False positive rate | 0.000 | 0.600 | +0.600 |
| REVIEW rate | 0.200 | 0.200 | = |
| Localization rate | 1.000 (7/7) | 0.286 (2/7) | −0.714 |
| MISSING recall | 1.000 (3/3) | 0.667 (2/3) | −0.333 |
| SHIFTED recall | 1.000 (2/2) | 0.000 (0/2) | −1.000 |
| ROTATED recall | 1.000 (2/2) | 0.000 (0/2) | −1.000 |

**Per-image comparison (synthetic):**

| file | expected | hand-made result | placement result | note |
|---|---|---|---|---|
| good_01.png | GOOD | ✅ GOOD | ❌ DEFECTIVE (6 false defects) | FP caused by tiny bboxes |
| good_02.png | GOOD | ✅ GOOD | ❌ DEFECTIVE (7 false defects) | FP |
| good_03.png | GOOD | ✅ GOOD | ❌ DEFECTIVE (7 false defects) | FP |
| defect_missing_R17.png | R17:MISSING | ✅ loc=1/1 | ✅ loc=1/1 | R17 center correct |
| defect_missing_C4.png | C4:MISSING | ✅ loc=1/1 | ✅ loc=1/1 | C4 center correct |
| defect_shifted_C4.png | C4:SHIFTED | ✅ loc=1/1 | ❌ loc=0/1 (C4 shown as MISSING) | bbox too small |
| defect_rotated_IC2.png | IC2:ROTATED | ✅ loc=1/1 | ❌ loc=0/1 (IC2 shown as MISSING) | bbox too small |
| defect_multi.png | 3 defects | ✅ loc=3/3 | ❌ loc=0/3 (all shown as MISSING) | bbox too small |
| review_blurred.png | REVIEW | ✅ REVIEW | ✅ REVIEW | = |
| review_off_fixture.png | REVIEW | ✅ REVIEW | ✅ REVIEW | = |

**Key finding:** FALSE ACCEPTS=0 in both runs (primary metric satisfied). The placement
regions cause 3 false positives on GOOD images and degrade localization, entirely because
the placement bboxes are 7–15x smaller in area than the hand-made regions. See Fix-up 3.

---

### Fix-up 3 — Bbox size ratios: placement vs. hand-made [SYNTHETIC]

> Synthetic board: mm_per_px = 0.12, affine scale = 8.333 px/mm.
> Hand-made bboxes include PCB pads and silkscreen markings.
> Placement bboxes use standard EDA footprint body sizes (no pads).

| Component | Hand-made W×H (px) | Placement W×H (px) | W ratio | H ratio | Area ratio |
|---|---|---|---|---|---|
| IC1 | 150×110 | 44×34 | 0.29 | 0.31 | **0.091** |
| IC2 | 120×90 | 44×34 | 0.37 | 0.38 | **0.139** |
| R1 | 70×26 | 15×8 | 0.21 | 0.31 | **0.066** |
| R2 | 70×26 | 15×8 | 0.21 | 0.31 | **0.066** |
| R3 | 70×26 | 15×8 | 0.21 | 0.31 | **0.066** |
| R17 | 26×70 | 15×8 | 0.58 | 0.11 | **0.066** |
| C1 | 46×46 | 20×12 | 0.43 | 0.26 | **0.113** |
| C4 | 46×46 | 20×12 | 0.43 | 0.26 | **0.113** |
| C7 | 46×46 | 20×12 | 0.43 | 0.26 | **0.113** |
| LED1 | 40×40 | 42×42 | 1.05 | 1.05 | **1.103** (close — LED body = footprint body) |
| J1 | 160×80 | 125×21 | 0.78 | 0.26 | **0.205** |

**Detection impact:** The template search window is derived from the region bbox.
A window at 10% of the correct area misses the component's pads and generates false
"MISSING" signals (correlation drops below threshold). This is why SHIFTED and ROTATED
defects become MISSING in the placement run — the template match fails on the tiny window.

**Three candidate fixes (proposed — no code changed; your choice required):**

| Option | What it does | Pros | Cons |
|---|---|---|---|
| **A — Size columns in placement file** | KiCad can export W_mm/H_mm per footprint. Parser already accepts cols 7–8 optional. Plumb through to region bbox. | Exact per-footprint; no guessing | User must re-export; fixture needs update |
| **B — Improved package_classes.json** | Increase sizes in `data/policy/package_classes.json` to pad-inclusive footprint sizes (already `_reviewed: false`) | Works now; no re-export needed | Sizes vary by manufacturer; approximation |
| **C — Reference-photo refinement** | After importing placement centers, auto-expand each bbox by searching a ring around the center in the reference image | Adapts to actual photo | Adds a new pipeline step; more complex |

**Recommendation:** Option B as an immediate demo fix (revise the package table);
Option A for production use (requires user to re-export with size columns). Option C deferred.

> **Decision 1 needed:** Which option(s) should Phase 4 implement?

---

### Fix-up 4 — mm values from transform: implementation status

**Status: PARTIALLY IMPLEMENTED — `deviation_mm` populated via scalar `mm_per_px`, NOT via the affine transform.**

From `backend/app/inspection/defect_detector.py` line 66:
```python
dev_mm = round(m.offset_px * mm_per_px, 3) if mm_per_px > 0 else None
```

This correctly converts `offset_px` to mm using the reference's scalar `mm_per_px`.
However, the pipeline does NOT reconstruct the `DesignTransform` from
`reference.placement_transform` to derive an accurate scale.

**Gap:** When a placement file has been imported, the transform's `scale_mm_to_px()`
would give a more accurate, potentially anisotropic scale. The scalar `mm_per_px`
is still used instead.

**Proposed fix (PLANNED — not yet implemented):**
- In `pipeline.py`, if `reference.placement_transform is not None`, reconstruct a
  `DesignTransform` and call `scale_mm_to_px()` to derive `mm_per_px` from the transform.
- Pass the transform-derived scale to `detect_all()` as `mm_per_px`.
- No schema changes. No threshold changes.
- One new test: `test_deviation_mm_uses_transform_scale`.

> **Decision 2 needed:** Implement this in Phase 4 alongside other changes, or as a
> separate small fix before Phase 4?

---

### Fix-up 5 — Files changed by `make_synthetic_pcb.py --force`

| File path | Changed? | What changes |
|---|---|---|
| `data/references/PCB_MODEL_001/meta.json` | **YES** | All fields rewritten: 11 hand-made regions, `active: true`, new `created_at`, `placement_transform: null` |
| `data/references/PCB_MODEL_001/reference.png` | **YES** | Deterministically regenerated from COMPONENTS dict; identical pixels but file is rewritten |
| `data/references/box/meta.json` | **NO** | Not touched |
| `data/references/box/reference.png` | **NO** | Not touched |
| `data/references/BOX_MODEL_001/meta.json` | **NO** | Not touched |
| `data/references/BOX_MODEL_001/reference.png` | **NO** | Not touched |

The `active` flag is stored inside each reference's own `meta.json` (`"active": true/false`);
there is no separate flag file. The `box` / `BOX_MODEL_001` references were completely
unaffected. `data/datasets/synthetic_v1/` images and `manifest.json` were also regenerated
(10 PNGs, outside `data/references/`). Nothing was deleted.

---

## 1. Defect gap analysis

> Measured on `synthetic_v1` (hand-made regions) and `box_v1` (surface mode).
> [SYNTHETIC — smoke test only, not a real-world benchmark]

| Defect kind | Detected today? | Evidence | Status |
|---|---|---|---|
| **Missing component** | YES — correlation < threshold | `defect_missing_R17.png` loc=1/1 [SYNTHETIC] | Supported |
| **Shifted component** | YES — `offset_px > pos_tol` | `defect_shifted_C4.png` loc=1/1 [SYNTHETIC] | Supported |
| **Rotated component** | YES — phase-correlation rotation | `defect_rotated_IC2.png` loc=1/1 [SYNTHETIC] | Supported |
| **Wrong component** | NO — `WRONG_COMPONENT` defined but never emitted | No test image; `detector_agreement == "conflict"` not surfaced | Not supported (deterministic approach feasible with YOLO) |
| **Unexpected extra component** | NO — `UNEXPECTED_COMPONENT` defined but never emitted | No test image; no diff-outside-regions detector | Not supported (deterministic approach feasible; high FP risk) |
| **Size deviation** | NO — `SIZE_DEVIATION` defined but never emitted | No test image; no extent measurement | Not supported (deterministic approach feasible for large components) |
| **Polarity / orientation** | Partial — rotation measured; no design-expected-rotation comparison; no polarity-specific logic | `check_rotation` flag exists; LED1 has `check_rotation: false` in synthetic | Partially supported |
| **Surface anomaly** | Partial — VISUAL_ANOMALY via SSIM + colour delta; works in surface mode | `box_v1` VISUAL_ANOMALY [SYNTHETIC] | Partially supported (needs real calibration) |
| **Crack / physical damage** | NO | No detector; requires trained model | Needs ML / real data → Phase 5 |
| **Solder defect** | NO | No detector; requires microscope/X-ray level imaging | Needs ML / real data → Phase 5 |
| **Structured expected-vs-observed** | Partial — free strings + position fields; `deviation_mm` populated via scalar only | See Fix-up 4 | Partially supported |
| **Confirmed vs. suspected** | NO — no `certainty` field | All detections have same confidence semantics | Not supported (schema addition needed) |
| **Stage on each defect** | NO — `stage_id` absent from Defect | `Defect.bbox` and `Defect.component` present; `stage_id` absent | Partially supported |

---

## 2. Deterministic approach per gap

### 2A — Wrong component
When YOLO is active: `RegionMeasurement.detector_agreement == "conflict"` already means
the detected class does not match `Region.expected_class`. Gate on `detector_confidence >= 0.50`.
Emit `WRONG_COMPONENT` as `certainty="suspected"`. Without YOLO: no change (emit nothing).

### 2B — Unexpected extra component
Diff image (aligned actual vs. reference) outside the union of all region bboxes.
Threshold on connected-component area > `AOI_UNEXPECTED_MIN_AREA_PX` (default 200 px²).
Emit `UNEXPECTED_COMPONENT` as `certainty="suspected"` → triggers REVIEW only.
High FP risk on real boards (reflections, shadows). Needs real-image tuning before production.

> **Decision 3 needed:** Implement in Phase 4 (as REVIEW-only / suspected), or defer?

### 2C — Size deviation
Measure matched template extent vs. expected region WxH.
Emit `SIZE_DEVIATION` as `certainty="suspected"` for large components only (ICs, connectors;
skip resistors/caps where bbox extent measurement is too noisy).

> **Decision 4 needed:** Implement in Phase 4 (limited to large components), or defer?

### 2D — Polarity / orientation from design rotation
Add `expected_rotation_deg` to `RegionTolerances` (populated by `import_placement`).
Compare to `m.rotation_deg`. For `expected_class in {"led","cap"}` and measured deviation
near 180°, label as polarity reversal in the `observed` string.

### 2E — Structured expected-vs-observed fields
Add machine-readable fields to `Defect` (see §4A). Populate from region and measurement.

### 2F — Confirmed vs. suspected
Two independent signals agree → `confirmed`; one signal → `suspected`.
Decision engine: `suspected` alone → REVIEW (unless CRITICAL severity → DEFECTIVE).
Invariants: no defect ever becomes GOOD; uncertain never becomes GOOD.

### 2G — Stage on every defect
Propagate `InspectionResult.stage_id` into each `Defect.stage_id` in pipeline.

### 2H — Cracks / solder defects → Phase 5
Not deterministically feasible. Require trained CNNs. No implementation in Phase 4.

---

## 3. Synthetic generator extensions

> All synthetic data labelled [SYNTHETIC].
> Existing 10 images in `synthetic_v1` remain **unchanged**.

### New defect images (added under `--include-phase4` flag)

| New image | Defect type | Generator change | `expected_defects` |
|---|---|---|---|
| `defect_wrong_IC1.png` | WRONG_COMPONENT | Paint resistor silhouette in IC1 region | `["IC1:WRONG_COMPONENT"]` |
| `defect_extra.png` | UNEXPECTED_COMPONENT | Paint extra component outside all regions | `["EXTRA:UNEXPECTED_COMPONENT"]` |
| `defect_size_IC1.png` | SIZE_DEVIATION | Draw IC1 at 60% of normal WxH | `["IC1:SIZE_DEVIATION"]` |
| `defect_polarity_LED1.png` | ROTATED_COMPONENT (polarity) | Draw LED1 rotated 180° | `["LED1:ROTATED_COMPONENT"]` |

Generator changes go into `scripts/make_synthetic_pcb.py` only. `_scene.py` does not
currently exist; scene drawing is inline. Will add `_draw_defect()` helpers.

---

## 4. Schema additions (all additive/optional)

### 4A — `Defect` in `backend/app/schemas/defect.py`

```python
# All new fields: additive, nullable, default None
certainty: Literal["confirmed", "suspected"] | None = None
expected_class: str | None = None
expected_rotation_deg: float | None = None
observed_class: str | None = None
observed_rotation_deg: float | None = None
stage_id: str | None = None
```

### 4B — `RegionTolerances` in `backend/app/schemas/reference.py`

```python
expected_rotation_deg: float | None = None  # design-space rotation; None = use reference image orientation
```

### 4C — Decision engine certainty rules

```
confirmed defect → DEFECTIVE (existing behaviour preserved)
suspected defect + severity == CRITICAL → DEFECTIVE
suspected defect + severity < CRITICAL → REVIEW (reason: SUSPECTED_DEFECT)
no defects → GOOD
invariant: GOOD never issued with any defect present
invariant: certainty field alone never changes GOOD/DEFECTIVE/REVIEW
```

### 4D — `frontend/src/types.ts` additions to `Defect`

```typescript
certainty?: "confirmed" | "suspected" | null;
expected_class?: string | null;
expected_rotation_deg?: number | null;
observed_class?: string | null;
observed_rotation_deg?: number | null;
stage_id?: string | null;
```

---

## 5. Files to change or add

### Modified files

| File | Change |
|---|---|
| `backend/app/schemas/defect.py` | Add 6 new optional fields |
| `backend/app/schemas/reference.py` | Add `expected_rotation_deg` to `RegionTolerances` |
| `backend/app/inspection/defect_detector.py` | Emit WRONG_COMPONENT; emit UNEXPECTED_COMPONENT; emit SIZE_DEVIATION; populate new fields; certainty logic |
| `backend/app/inspection/decision_engine.py` | Add suspected-only → REVIEW rule |
| `backend/app/inspection/pipeline.py` | Propagate `stage_id` to Defects; use transform-derived scale for `mm_per_px` when available |
| `frontend/src/types.ts` | Add 6 new optional fields to `Defect` interface |
| `data/policy/severity_actions.json` | Add WRONG_COMPONENT, UNEXPECTED_COMPONENT, SIZE_DEVIATION entries |
| `scripts/make_synthetic_pcb.py` | Add 4 new defect images under `--include-phase4` flag |

### New files

| File | Purpose |
|---|---|
| `tests/test_phase4_detectors.py` | Tests for new detectors (wrong, unexpected, size, polarity) |
| `tests/test_decision_engine_certainty.py` | Certainty → REVIEW/DEFECTIVE rule tests |
| `docs/PHASE4_PLAN.md` | This document |

---

## 6. Tests to write

| Test class | Coverage |
|---|---|
| `TestWrongComponentDetection` | conflict + confidence ≥ 0.50 → WRONG_COMPONENT; no YOLO → no wrong defect |
| `TestUnexpectedComponentDetection` | diff above threshold → UNEXPECTED_COMPONENT suspected; below → nothing |
| `TestSizeDeviation` | large component at 60% size → SIZE_DEVIATION; small resistor → no SIZE_DEVIATION |
| `TestPolarityFlip` | LED rotated 180° → ROTATED_COMPONENT with polarity message |
| `TestCertaintyField` | two signals → confirmed; one signal → suspected |
| `TestDecisionEngineCertainty` | suspected + < CRITICAL → REVIEW; suspected + CRITICAL → DEFECTIVE; confirmed → DEFECTIVE; no defect → GOOD; GOOD never with any defect |
| `TestDeviationMmFromTransform` | transform-derived scale used when placement_transform set |
| `TestStageOnDefect` | stage_id propagated from inspection to each defect |
| `TestBackwardCompat` | All 148 existing tests still pass; new fields default None |

---

## 7. Acceptance criteria

> All targets inherit the PROPOSED status from ACCEPTANCE.md.

| Check | Gate |
|---|---|
| FALSE ACCEPTS on synthetic_v1 | 0 (smoke) |
| Severity populated — WRONG, UNEXPECTED, SIZE | 100% |
| Corrective action populated — new types | 100% |
| `suspected` alone never → GOOD | Always (unit test) |
| Existing 148 tests | 148/148 pass |
| New tests | All pass |
| `npm run build` | 0 TypeScript errors |

---

## 8. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| UNEXPECTED_COMPONENT FP on real boards | High | suspected only → REVIEW not DEFECTIVE; configurable threshold; document as needing real-image tuning |
| WRONG_COMPONENT FP with low-confidence YOLO | Medium | Gate on confidence ≥ 0.50; suspected label; silent in DETERMINISTIC_ONLY mode |
| SIZE_DEVIATION noisy on small components | High | Limit to large components (area > threshold); suspected only |
| certainty rule increases REVIEW rate above 0.20 | Medium | Existing confirmed defects stay DEFECTIVE; only new suspected defects add to REVIEW; monitor synthetic_v1 REVIEW rate |
| Schema additions break existing tests | Low | All fields additive/nullable; Pydantic default=None; existing assertions don't check new fields |

---

## 9. Open decisions needed before implementation

1. **Fix-up 3 (bbox sizes):** Option A (size from placement file), B (improve package table), or both?
2. **Fix-up 4 (mm from transform):** Phase 4 or separate small fix before Phase 4?
3. **UNEXPECTED_COMPONENT:** Implement in Phase 4 (suspected / REVIEW only) or defer?
4. **SIZE_DEVIATION:** Implement in Phase 4 (large components only) or defer?
5. **`expected_rotation_deg` on Region:** Store design-space rotation in `RegionTolerances`, or keep it only in the placement transform metadata?

---

> **R1 HARD STOP — Phase 4A plan only. No implementation started.**
> Awaiting written approval and answers to the 5 decisions above before any code changes.
