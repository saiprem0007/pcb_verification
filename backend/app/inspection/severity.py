"""Stage F.6 — severity classification and corrective-action assignment.

This module is a *pure function layer*: it reads a policy table and annotates
:class:`~app.schemas.defect.Defect` instances with ``severity`` and
``corrective_action``.  It never modifies the inspection status
(GOOD / DEFECTIVE / REVIEW) or the defect list itself.

Policy loading
--------------
The default policy lives at ``data/policy/severity_actions.json`` relative to
the repo root.  The path is overridable via ``AOI_SEVERITY_POLICY_PATH``.  If
the file is absent the built-in fallback table is used (MEDIUM severity,
generic corrective-action text) so the pipeline never fails on a missing file.

Format guarantee
----------------
Every :class:`Defect` returned by :func:`apply_severity` has:

* ``severity``          — one of ``CRITICAL | HIGH | MEDIUM | LOW``
* ``corrective_action`` — a non-empty human-readable string

The function is idempotent: re-applying it overwrites any existing values.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

from ..logging_config import get_logger
from ..schemas.defect import Defect, DefectType, SeverityLevel

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Built-in fallback (used when policy file is absent or unparseable)
# ---------------------------------------------------------------------------
_FALLBACK_POLICY: dict[str, dict[str, str]] = {
    DefectType.MISSING_COMPONENT.value: {
        "severity": "CRITICAL",
        "corrective_action": (
            "Component {component} is absent. Fit the component per the assembly "
            "drawing. Do not advance to the next stage until re-inspection passes."
        ),
    },
    DefectType.WRONG_COMPONENT.value: {
        "severity": "CRITICAL",
        "corrective_action": (
            "Wrong component at {component} (found: {observed}; required: {expected}). "
            "Remove the incorrect part, fit the correct component, and re-inspect."
        ),
    },
    DefectType.SHIFTED_COMPONENT.value: {
        "severity": "HIGH",
        "corrective_action": (
            "Component {component} is out of position ({observed}; tolerance {expected}). "
            "Reposition within tolerance and re-inspect."
        ),
    },
    DefectType.ROTATED_COMPONENT.value: {
        "severity": "HIGH",
        "corrective_action": (
            "Component {component} is incorrectly oriented ({observed}; tolerance {expected}). "
            "Correct the orientation and re-inspect."
        ),
    },
    DefectType.SIZE_DEVIATION.value: {
        "severity": "HIGH",
        "corrective_action": (
            "Component {component} shows a size deviation ({observed}; expected {expected}). "
            "Confirm the correct part is fitted and re-inspect."
        ),
    },
    DefectType.UNEXPECTED_COMPONENT.value: {
        "severity": "HIGH",
        "corrective_action": (
            "Unexpected component found at or near {component} ({observed}). "
            "Remove the extra part and re-inspect."
        ),
    },
    DefectType.VISUAL_ANOMALY.value: {
        "severity": "MEDIUM",
        "corrective_action": (
            "Visual anomaly at {component} ({observed}). "
            "Inspect manually against the reference image. "
            "If damage or contamination is confirmed, escalate to rework."
        ),
    },
    "_default": {
        "severity": "MEDIUM",
        "corrective_action": (
            "Defect detected at {component}: {observed}. "
            "Inspect manually against the reference image and document findings."
        ),
    },
}

_VALID_SEVERITIES: frozenset[str] = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})
_CONSERVATIVE_SEVERITY: SeverityLevel = "MEDIUM"


def _load_policy(path: Path) -> dict[str, dict[str, str]]:
    """Load the JSON policy file; fall back to the built-in table on any error."""
    if not path.is_file():
        log.debug("severity policy file not found at %s — using built-in defaults", path)
        return _FALLBACK_POLICY
    try:
        raw: dict = json.loads(path.read_text("utf-8"))
        # Strip comment/meta keys (start with _)
        return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not parse severity policy %s (%s) — using built-in defaults", path, exc)
        return _FALLBACK_POLICY


def _resolve_policy(loaded: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Merge loaded policy with fallback; ensure _default always exists."""
    merged = dict(_FALLBACK_POLICY)   # start with fallback
    merged.update(loaded)             # loaded file wins
    return merged


def _safe_format(template: str, component: str, observed: str, expected: str) -> str:
    """Format the corrective-action template; never raises."""
    try:
        return template.format(
            component=component or "—",
            observed=observed or "—",
            expected=expected or "—",
        )
    except (KeyError, ValueError):
        # Template uses an unknown placeholder — return as-is, better than crashing.
        return template


def _classify(
    defect_type: str,
    policy: dict[str, dict[str, str]],
) -> tuple[SeverityLevel, str]:
    """Return (severity, corrective_action_template) for a defect type.

    Unknown types → ``_default`` entry → conservative ``MEDIUM``.
    """
    rule = policy.get(defect_type) or policy.get("_default") or {}
    severity_raw = rule.get("severity", _CONSERVATIVE_SEVERITY)
    # Guard: only accept valid severity strings; fall back conservatively.
    severity: SeverityLevel = (
        severity_raw if severity_raw in _VALID_SEVERITIES else _CONSERVATIVE_SEVERITY
    )
    action_template = rule.get("corrective_action", "Inspect {component} manually.")
    return severity, action_template  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_severity(
    defects: list[Defect],
    policy_path: Path | None = None,
) -> list[Defect]:
    """Return a new list of :class:`Defect` instances with ``severity`` and
    ``corrective_action`` populated from the policy table.

    Parameters
    ----------
    defects:
        List of defects produced by the detection stages.
    policy_path:
        Path to the ``severity_actions.json`` file.  ``None`` → built-in
        fallback policy.

    Returns
    -------
    list[Defect]
        New :class:`Defect` objects (``model_copy`` with the two new fields
        set).  The original objects are not mutated.  The list order is
        preserved.  If ``defects`` is empty, an empty list is returned.

    Invariant
    ---------
    This function never changes ``defect.type``, ``defect.confidence``,
    ``defect.component``, or any geometric field.  It only adds
    ``severity`` and ``corrective_action``.  The caller's GOOD / DEFECTIVE /
    REVIEW status is unaffected.
    """
    if not defects:
        return []

    raw_policy = _load_policy(policy_path) if policy_path else _FALLBACK_POLICY
    policy = _resolve_policy(raw_policy)

    result: list[Defect] = []
    for d in defects:
        severity, action_template = _classify(d.type.value, policy)
        corrective_action = _safe_format(
            action_template,
            component=d.component,
            observed=d.observed,
            expected=d.expected,
        )
        result.append(d.model_copy(update={
            "severity": severity,
            "corrective_action": corrective_action,
        }))
    return result
