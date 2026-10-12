"""Generic deterministic verifier for ruled table ROI candidates.

The verifier consumes normalized evidence produced by the canonical render
stream.  It deliberately does not know source filenames, drawing numbers, or
gold annotations.
"""

from __future__ import annotations


def _ratio(value, denominator):
    return float(value or 0) / max(float(denominator or 0), 1.0)


def verify_ruled_table_v2(evidence):
    """Classify one candidate using scale-normalized ruled-table evidence.

    ``evidence`` is intentionally a measurement contract, not a CAD object.
    The caller supplies counts and normalized ratios from the canonical entity
    index.  No pixel-specific threshold or source-specific exception is used.
    """
    e = dict(evidence or {})
    text = int(e.get("text_count", 0) or 0)
    lines = int(e.get("line_count", 0) or 0)
    intersections = int(e.get("line_intersections", 0) or 0)
    cells = int(e.get("closed_cell_candidates", 0) or 0)
    aligned_rows = int(e.get("aligned_row_bands", 0) or 0)
    aligned_cols = int(e.get("aligned_column_bands", 0) or 0)
    populated = int(e.get("text_in_cell_count", 0) or 0)
    line_families = int(e.get("parallel_line_families", 0) or 0)
    border = float(e.get("sheet_border_dominance", 0) or 0)
    title = float(e.get("title_block_dominance", 0) or 0)
    structural = float(e.get("structural_geometry_dominance", 0) or 0)
    dimension = float(e.get("dimension_leader_dominance", 0) or 0)
    empty = float(e.get("empty_area_ratio", 0) or 0)
    agreement = float(e.get("entity_key_agreement", 0) or 0)
    cross_sheet = bool(e.get("cross_sheet", False))

    normalized = {
        "text_density": _ratio(text, e.get("bbox_area_units", 1)),
        "line_density": _ratio(lines, e.get("bbox_area_units", 1)),
        "row_alignment_ratio": _ratio(aligned_rows, max(text, 1)),
        "column_alignment_ratio": _ratio(aligned_cols, max(text, 1)),
        "cell_occupancy_ratio": _ratio(populated, max(text, 1)),
        "intersection_per_line": _ratio(intersections, max(lines, 1)),
        "entity_key_agreement": agreement,
    }

    if cross_sheet:
        subtype, status, reason = "insufficient_evidence", "review", "cross_sheet"
    elif agreement < 0.95:
        subtype, status, reason = "insufficient_evidence", "review", "coordinate_mismatch"
    elif border >= 0.60:
        subtype, status, reason = "title_block_table", "reject", "sheet_border_dominant"
    elif title >= 0.60:
        subtype, status, reason = "title_block_table", "reject", "title_block_dominant"
    elif structural >= 0.60 or dimension >= 0.60:
        subtype, status, reason = "structural_grid", "reject", "structural_or_dimension_dominant"
    elif line_families < 2 or intersections < 2 or cells < 1:
        subtype, status, reason = "insufficient_evidence", "review", "insufficient_cell_topology"
    elif aligned_rows < 2 or aligned_cols < 2 or populated < 2:
        subtype, status, reason = "insufficient_evidence", "review", "insufficient_text_alignment"
    elif empty >= 0.80:
        subtype, status, reason = "insufficient_evidence", "review", "mostly_empty_bbox"
    else:
        subtype, status, reason = "quantity_ruled_table", "accept", "ruled_table_evidence"

    return {
        "status": status,
        "subtype": subtype,
        "first_failure_reason": None if status == "accept" else reason,
        "normalized_features": normalized,
        "evidence": {
            "text_count": text,
            "line_count": lines,
            "line_intersections": intersections,
            "closed_cell_candidates": cells,
            "aligned_row_bands": aligned_rows,
            "aligned_column_bands": aligned_cols,
            "text_in_cell_count": populated,
            "parallel_line_families": line_families,
        },
    }
