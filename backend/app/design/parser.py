"""Placement-file parser.

Supports:
  - KiCad .pos format (space/tab-delimited with a specific header)
  - Generic CSV with configurable column names

Unsupported (out of scope for Phase 3):
  - Gerber / RS-274X
  - ODB++ (directory structure)
  - STEP / IGES (3-D CAD)
  - PDF drawings
  - Altium .csv with non-standard headers (use the generic CSV path)

The parser returns a list of :class:`DesignComponent` objects with all
coordinates in millimetres (design space).  No pixel conversion happens here;
that is done by :mod:`app.design.mapping`.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DesignComponent:
    """One component as read from the placement file (design-space coordinates)."""
    ref_des: str           # e.g. "R17", "C4", "IC2"
    value: str             # e.g. "10k", "100nF" — may be empty
    package: str           # e.g. "R_0402", "SOIC-8" — may be empty
    pos_x_mm: float        # center X in mm (design coordinates)
    pos_y_mm: float        # center Y in mm (design coordinates)
    rotation_deg: float    # 0–360, CCW-positive (KiCad convention)
    side: str              # "F" (front/top) or "B" (back/bottom)
    # Optional size from placement file (width, height in mm); None = use package table
    size_mm: tuple[float, float] | None = field(default=None)

    def is_fiducial(self) -> bool:
        """Return True when ref_des looks like a fiducial (FID*, FIDUCIAL*, FD*)."""
        return bool(re.match(r"^(FID|FIDUCIAL|FD)\d*$", self.ref_des, re.IGNORECASE))


class PlacementParseError(ValueError):
    """Raised when the placement file cannot be parsed cleanly."""


# ---------------------------------------------------------------------------
# KiCad .pos parser
# ---------------------------------------------------------------------------

_KICAD_COLUMNS = ("Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side")

# Side spellings -> "F" / "B".  KiCad's own export writes "top" / "bottom".
_SIDE_ALIASES = {
    "F": "F", "FRONT": "F", "TOP": "F", "T": "F",
    "B": "B", "BACK": "B", "BOTTOM": "B",
}


def _size_pair(w, h) -> tuple[float, float] | None:
    """(w, h) in mm if both are finite and > 0, else None (malformed -> ignored)."""
    try:
        wf, hf = float(w), float(h)
    except (TypeError, ValueError):
        return None
    if not (wf > 0 and hf > 0) or wf != wf or hf != hf or wf == float("inf") or hf == float("inf"):
        return None
    return (wf, hf)


def _parse_kicad_pos(text: str, source_name: str = "<unknown>") -> list[DesignComponent]:
    """Parse a KiCad ``.pos`` file.

    Two layouts are accepted:

    * **KiCad export style** (hand-built from the documented format; real-export
      support is UNVERIFIED): ``###`` / ``##`` comment lines, the column header as a
      comment ``# Ref  Val  Package  PosX  PosY  Rot  Side``, data rows with Side
      ``top`` / ``bottom``, and a trailing ``## End``.
    * **Plain style** (earlier fixtures): the header ``Ref Val Package PosX PosY Rot
      Side`` as the first non-comment line.

    Side accepts ``F``/``B`` and ``top``/``bottom`` (also front/back), any case.
    Optional columns 8 and 9 are the part size ``SizeX SizeY`` in mm at rotation 0
    (used in preference to the package table).

    Raises :class:`PlacementParseError` on format errors.
    """
    data_lines: list[str] = []
    header_found = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # "# Ref Val Package PosX PosY Rot Side" (KiCad) is a comment-style header
            if not header_found and stripped.lstrip("#").split()[:7] == list(_KICAD_COLUMNS):
                header_found = True
            continue                     # all other '#', '##', '###' lines are comments
        if not header_found:
            cols = stripped.split()
            if cols[:7] == list(_KICAD_COLUMNS):
                header_found = True
                continue
            raise PlacementParseError(
                f"{source_name}: expected KiCad .pos header "
                f"'{' '.join(_KICAD_COLUMNS)}', got: {stripped!r}"
            )
        data_lines.append(stripped)

    if not header_found:
        raise PlacementParseError(
            f"{source_name}: no KiCad .pos header found — "
            f"expected columns: {' '.join(_KICAD_COLUMNS)}"
        )
    if not data_lines:
        raise PlacementParseError(f"{source_name}: KiCad .pos file has a header but no data rows")

    components: list[DesignComponent] = []
    for lineno, line in enumerate(data_lines, start=1):
        parts = line.split()
        if len(parts) < 7:
            raise PlacementParseError(
                f"{source_name}: line {lineno}: expected ≥7 columns, got {len(parts)}: {line!r}"
            )
        ref, val, pkg = parts[0], parts[1], parts[2]
        try:
            px = float(parts[3])
            py = float(parts[4])
            rot = float(parts[5]) % 360.0
        except ValueError as exc:
            raise PlacementParseError(
                f"{source_name}: line {lineno}: cannot parse numeric values: {exc}"
            ) from exc
        side = _SIDE_ALIASES.get(parts[6].upper())
        if side is None:
            raise PlacementParseError(
                f"{source_name}: line {lineno}: Side must be 'F'/'B' (or top/bottom), "
                f"got {parts[6]!r}"
            )
        size = _size_pair(parts[7], parts[8]) if len(parts) >= 9 else None

        components.append(DesignComponent(
            ref_des=ref, value=val, package=pkg,
            pos_x_mm=px, pos_y_mm=py, rotation_deg=rot,
            side=side, size_mm=size,
        ))

    return components


# ---------------------------------------------------------------------------
# Generic CSV parser
# ---------------------------------------------------------------------------

# Default column-name guesses (case-insensitive) for generic CSVs
_CSV_COL_ALIASES: dict[str, list[str]] = {
    "ref_des": ["ref", "refdes", "designator", "reference", "ref_des"],
    "value":   ["value", "val", "component_value"],
    "package": ["package", "footprint", "pkg", "fp"],
    "pos_x":   ["posx", "pos_x", "x", "center_x", "x_mm", "posx_mm"],
    "pos_y":   ["posy", "pos_y", "y", "center_y", "y_mm", "posy_mm"],
    "rotation":["rotation", "rot", "angle", "rotation_deg"],
    "side":    ["side", "layer", "board_side"],
    # optional part size in mm at rotation 0 (used in preference to the package table)
    "size_w":  ["w_mm", "width_mm", "width", "size_x", "sizex", "size_w"],
    "size_h":  ["h_mm", "height_mm", "height", "size_y", "sizey", "size_h"],
}


def _find_col(header: list[str], canonical: str,
              alias_map: dict[str, list[str]] | None = None) -> int | None:
    """Return the index of a column in header matching one of the known aliases.

    ``alias_map`` is a per-call copy; the module-level table is never mutated.
    """
    aliases = (alias_map or _CSV_COL_ALIASES).get(canonical, [canonical])
    for i, col in enumerate(header):
        if col.lower().replace(" ", "_").replace("-", "_") in aliases:
            return i
    return None


def _parse_generic_csv(
    text: str,
    source_name: str = "<unknown>",
    col_map: dict[str, str] | None = None,
) -> list[DesignComponent]:
    """Parse a generic placement CSV file.

    ``col_map`` maps canonical field names to actual column names in the CSV
    (useful when the CSV uses non-standard headers).  If None, the parser
    attempts to auto-detect columns using :data:`_CSV_COL_ALIASES`.

    Raises :class:`PlacementParseError` on format or missing-column errors.
    """
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # Skip blank / comment rows at the top
    header_row_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip().startswith("#"):
            continue
        if row and any(cell.strip() for cell in row):
            header_row_idx = i
            break
    if header_row_idx is None:
        raise PlacementParseError(f"{source_name}: CSV has no non-comment, non-blank rows")

    raw_header = [c.strip() for c in rows[header_row_idx]]

    # Caller-supplied column aliases go into a PER-CALL copy (the module-level table
    # must never be mutated -- it used to grow on every call).
    alias_map: dict[str, list[str]] = {k: list(v) for k, v in _CSV_COL_ALIASES.items()}
    if col_map:
        for canonical, actual in col_map.items():
            if canonical in alias_map:
                alias_map[canonical].insert(
                    0, actual.lower().replace(" ", "_").replace("-", "_")
                )

    col_idx: dict[str, int | None] = {
        f: _find_col(raw_header, f, alias_map)
        for f in ("ref_des", "value", "package", "pos_x", "pos_y", "rotation", "side",
                  "size_w", "size_h")
    }
    required = ("ref_des", "pos_x", "pos_y")
    missing = [f for f in required if col_idx[f] is None]
    if missing:
        raise PlacementParseError(
            f"{source_name}: CSV missing required columns: {missing}. "
            f"Header found: {raw_header}. "
            f"Provide a col_map or rename columns to match: "
            + ", ".join(f"'{k}': {v}" for k, v in alias_map.items())
        )

    components: list[DesignComponent] = []
    for lineno, row in enumerate(rows[header_row_idx + 1:], start=header_row_idx + 2):
        if not row or all(not c.strip() for c in row):
            continue
        if row[0].strip().startswith("#"):
            continue

        def _get(field: str, default: str = "") -> str:
            idx = col_idx.get(field)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip()

        ref = _get("ref_des")
        if not ref:
            continue  # skip rows without a ref

        val = _get("value")
        pkg = _get("package")
        side = _SIDE_ALIASES.get(_get("side", "F").upper(), "F")   # unknown -> F (lenient)

        try:
            px = float(_get("pos_x", "0"))
            py = float(_get("pos_y", "0"))
        except ValueError as exc:
            raise PlacementParseError(
                f"{source_name}: line {lineno}: cannot parse X/Y: {exc}"
            ) from exc

        try:
            rot = float(_get("rotation", "0")) % 360.0
        except ValueError:
            rot = 0.0

        size = (
            _size_pair(_get("size_w"), _get("size_h"))
            if col_idx["size_w"] is not None and col_idx["size_h"] is not None else None
        )

        components.append(DesignComponent(
            ref_des=ref, value=val, package=pkg,
            pos_x_mm=px, pos_y_mm=py, rotation_deg=rot,
            side=side, size_mm=size,
        ))

    if not components:
        raise PlacementParseError(f"{source_name}: CSV parsed but produced 0 components")
    return components


# ---------------------------------------------------------------------------
# Auto-detect format and dispatch
# ---------------------------------------------------------------------------

def _looks_like_kicad_pos(text: str) -> bool:
    """Heuristic: does this text look like a KiCad .pos file?"""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # KiCad's export carries the column header as a comment line
            if stripped.lstrip("#").split()[:7] == list(_KICAD_COLUMNS):
                return True
            continue
        return stripped.split()[:7] == list(_KICAD_COLUMNS)
    return False


def parse_placement(
    text: str,
    *,
    filename: str = "",
    fmt: str | None = None,
    col_map: dict[str, str] | None = None,
) -> list[DesignComponent]:
    """Parse a placement file from its text content.

    Format detection order:
    1. If ``fmt`` is given ('kicad' or 'csv'), use that.
    2. If ``filename`` ends with '.pos', use KiCad parser.
    3. Heuristic: if the first data line matches KiCad header, use KiCad.
    4. Fall back to generic CSV.

    Returns a list of :class:`DesignComponent`.
    Raises :class:`PlacementParseError` on any parsing problem.
    """
    source = filename or "<text>"
    if fmt:
        fmt = fmt.lower()
        if fmt == "kicad":
            return _parse_kicad_pos(text, source)
        if fmt == "csv":
            return _parse_generic_csv(text, source, col_map)
        raise PlacementParseError(f"Unknown format {fmt!r}; use 'kicad' or 'csv'")

    ext = Path(filename).suffix.lower() if filename else ""
    if ext == ".pos" or _looks_like_kicad_pos(text):
        return _parse_kicad_pos(text, source)
    return _parse_generic_csv(text, source, col_map)


def parse_placement_file(
    path: Path,
    *,
    fmt: str | None = None,
    col_map: dict[str, str] | None = None,
) -> list[DesignComponent]:
    """Load a placement file from disk and parse it.

    Raises :class:`PlacementParseError` on read or parse errors.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PlacementParseError(f"Cannot read file {path}: {exc}") from exc
    return parse_placement(text, filename=path.name, fmt=fmt, col_map=col_map)
