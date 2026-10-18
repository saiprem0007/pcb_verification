# ACCEPTANCE.md — Proposed acceptance targets

> **STATUS: PROPOSAL — awaiting user confirmation.**
> No target below is accepted until you explicitly confirm it.
> Do not use these numbers as final gates.

> All synthetic numbers in this file are labelled `[SYNTHETIC]`.
> Synthetic results are smoke tests; they cannot substitute for real-image benchmarks.

---

## Background

The primary failure mode for this system is a **false accept**: a DEFECTIVE
board is classified as GOOD and passes to the next stage.  This is the only
metric that has a hard target of **zero** in the requirements.

All other targets below are proposals based on common manufacturing inspection
standards.  They will need to be revised once real-image data is available.

---

## Proposed targets

### Critical (hard gate — system MUST NOT release without meeting these)

| metric | proposed target | rationale |
|---|---|---|
| **FALSE ACCEPTS** (DEFECTIVE → GOOD) | **0** on heldout | Any false accept is a safety/quality failure. One miss = production defect ships. |
| System must refuse to give GOOD on uncertain input | REVIEW, never GOOD | R9 in project rules — uncertain → REVIEW |

### Primary quality metrics (on real heldout set — PROPOSED)

| metric | proposed target | rationale |
|---|---|---|
| Recall (DEFECTIVE detection rate) | ≥ 0.90 | Miss at most 10% of defective boards |
| False positive rate (GOOD → DEFECTIVE) | ≤ 0.15 | Limit unnecessary rework overhead |
| Defect localization rate (ref-des + type match) | ≥ 0.70 | Actionable defect reports |
| REVIEW rate | ≤ 0.20 | Too many REVIEWs defeats automation |

> **Note:** These targets are for a REAL-image heldout set of ≥ 30 images
> (≥ 10 DEFECTIVE). They cannot be validated on synthetic data.

### Performance targets (PROPOSED)

| metric | proposed target | rationale |
|---|---|---|
| Latency p50 (laptop, CPU) | ≤ 2000 ms | Feasible for a 1-image-per-2s line |
| Latency p95 (laptop, CPU) | ≤ 5000 ms | No single image should stall the line |
| Peak memory per image | ≤ 512 MB | Fits within Pi 5 / typical edge device |

> Pi 5 / Hailo targets: UNVERIFIED — hardware not yet available. Defer to Phase O12.

### Coverage targets (PROPOSED)

| metric | proposed target | rationale |
|---|---|---|
| Severity field populated | 100% of detected defects | Severity table is implemented and mandatory |
| Corrective action populated | 100% of detected defects | Actionable output is a core requirement |

---

## Current synthetic_v1 baseline [SYNTHETIC — smoke test]

> [SYNTHETIC — smoke test only, not a real-world benchmark]
> These numbers come from 10 computer-generated images.
> They are reported here only for regression detection.

| metric | Phase 0 baseline [SYNTHETIC] |
|---|---|
| n | 10 |
| FALSE ACCEPTS | 0 |
| statuses unchanged after Phase 2 | yes |

Run `py scripts/evaluate.py --dataset synthetic_v1` to regenerate.

---

## Acceptance gate process (PROPOSED)

1. Collect a real-image dataset (see [DATA.md](DATA.md)).
2. Run `py scripts/split_dataset.py --dataset <name> --seed 42 --heldout-frac 0.3`.
3. Tune / adjust thresholds on the `tuning` split **only**.
4. When satisfied, run `py scripts/evaluate.py --dataset <name> --split heldout` **once**.
5. Report the heldout result to the user.  The user confirms whether targets are met.
6. The heldout set must **not** be inspected or tuned against before this step.

---

## Open questions for user confirmation

1. **False accept hard target**: 0 on heldout is proposed.  Is this the correct gate?
2. **Recall target**: 0.90 proposed.  Is 0.95 or 0.99 required given your production context?
3. **False positive rate**: 0.15 proposed (some false alarms acceptable to avoid false accepts).  Acceptable?
4. **Minimum heldout set size**: 30 images / 10 DEFECTIVE proposed.  Is this sufficient?
5. **Performance targets**: 2 s p50 on laptop CPU proposed.  Actual line throughput requirement?
6. **Localization target**: 0.70 ref-des+type match rate proposed.  Is exact bbox IoU also required?
7. **Pi 5 / Hailo targets**: left as UNVERIFIED.  When should these be set?

**Please reply with your decisions on the above.  Until confirmed, all targets
remain PROPOSED and must not be cited as acceptance criteria.**
