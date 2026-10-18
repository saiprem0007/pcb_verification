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


def _parse_kicad_pos(text: str, source_name: str = "<unknown>") -> list[DesignComponent]:
    """Parse a KiCad .pos file.

    KiCad .pos files begin with comment lines starting with '#', then a header
    line "Ref  Val  Package  PosX  PosY  Rot  Side" and then data rows.

    Raises :class:`PlacementParseError` on format errors.
    """
    lines = text.splitlines()
    data_lines: list[str] = []
    header_found = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not header_found:
            # The first non-comment line must be the header
            cols = stripped.split()
            if cols[:7] == list(_KICAD_COLUMNS):
                header_found = True
                continue
            else:
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
        side = parts[6].upper()
        if side not in ("F", "B"):
            raise PlacementParseError(
                f"{source_name}: line {lineno}: Side must be 'F' or 'B', got {side!r}"
            )
        # Optional size columns (col 7=W_mm, col 8=H_mm)
        size: tuple[float, float] | None = None
        if len(parts) >= 9:
            try:
                size = (float(parts[7]), float(parts[8]))
            except ValueError:
                pass  # ignore malformed optional columns

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
}


def _find_col(header: list[str], canonical: str) -> int | None:
    """Return the index of a column in header matching one of the known aliases."""
    aliases = _CSV_COL_ALIASES.get(canonical, [canonical])
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

    # Apply caller-supplied column aliases
    if col_map:
        for canonical, actual in col_map.items():
            if canonical in _CSV_COL_ALIASES:
                _CSV_COL_ALIASES[canonical].insert(0, actual.lower())

    col_idx: dict[str, int | None] = {
        f: _find_col(raw_header, f)
        for f in ("ref_des", "value", "package", "pos_x", "pos_y", "rotation", "side")
    }
    required = ("ref_des", "pos_x", "pos_y")
    missing = [f for f in required if col_idx[f] is None]
    if missing:
        raise PlacementParseError(
            f"{source_name}: CSV missing required columns: {missing}. "
            f"Header found: {raw_header}. "
            f"Provide a col_map or rename columns to match: "
            + ", ".join(f"'{k}': {v}" for k, v in _CSV_COL_ALIASES.items())
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
        side_raw = _get("side", "F").upper()
        side = side_raw[0] if side_raw else "F"
        if side not in ("F", "B"):
            side = "F"

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

        components.append(DesignComponent(
            ref_des=ref, value=val, package=pkg,
            pos_x_mm=px, pos_y_mm=py, rotation_deg=rot,
            side=side, size_mm=None,
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
        if not stripped or stripped.startswith("#"):
            continue
        cols = stripped.split()
        return cols[:7] == list(_KICAD_COLUMNS)
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
