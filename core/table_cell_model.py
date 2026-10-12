"""CanonicalTableCellModelV1: ruled-table topology and text accounting only.

This module intentionally stops at cells and provenance.  It does not interpret
marks, reinforcement, specifications, or quantities.
"""

from __future__ import annotations


def _clip(value, low, high):
    return max(low, min(high, float(value)))


def normalize_segments(segments, roi, *, tolerance):
    """Clip line segments to ``roi`` and merge collinear near-duplicates."""
    x0, y0, x1, y1 = [float(v) for v in roi]
    result = []
    for segment in segments or []:
        sx0, sy0, sx1, sy1 = [float(v) for v in segment["bbox"]]
        sx0, sx1 = sorted((_clip(sx0, x0, x1), _clip(sx1, x0, x1)))
        sy0, sy1 = sorted((_clip(sy0, y0, y1), _clip(sy1, y0, y1)))
        width, height = sx1 - sx0, sy1 - sy0
        if max(width, height) <= 0:
            continue
        orientation = "vertical" if height > width else "horizontal"
        coordinate = (sx0 + sx1) / 2 if orientation == "vertical" else (sy0 + sy1) / 2
        result.append({**segment, "bbox": [sx0, sy0, sx1, sy1],
                       "orientation": orientation, "coordinate": coordinate})
    merged = []
    for segment in sorted(result, key=lambda item: (item["orientation"], item["coordinate"])):
        if merged and merged[-1]["orientation"] == segment["orientation"] and abs(
                merged[-1]["coordinate"] - segment["coordinate"]) <= tolerance:
            previous = merged[-1]
            previous["bbox"] = [min(previous["bbox"][0], segment["bbox"][0]),
                                 min(previous["bbox"][1], segment["bbox"][1]),
                                 max(previous["bbox"][2], segment["bbox"][2]),
                                 max(previous["bbox"][3], segment["bbox"][3])]
            previous["source_entity_keys"] = sorted(set(previous.get("source_entity_keys", [])) |
                                                       set(segment.get("source_entity_keys", [])))
            previous["coordinate"] = (previous["bbox"][0] + previous["bbox"][2]) / 2 \
                if previous["orientation"] == "vertical" else (previous["bbox"][1] + previous["bbox"][3]) / 2
        else:
            merged.append(dict(segment))
    return merged


def _coordinates(segments, orientation):
    return sorted({round(float(s["coordinate"]), 6) for s in segments if s["orientation"] == orientation})


def build_grid_topology(segments, roi, *, tolerance):
    """Build deterministic row/column faces from normalized line families."""
    normalized = normalize_segments(segments, roi, tolerance=tolerance)
    x0, y0, x1, y1 = [float(v) for v in roi]
    xs = [x0] + _coordinates(normalized, "vertical") + [x1]
    ys = [y0] + _coordinates(normalized, "horizontal") + [y1]
    xs = sorted(set(xs)); ys = sorted(set(ys))
    cells = []
    for row in range(len(ys) - 1):
        for column in range(len(xs) - 1):
            if xs[column + 1] - xs[column] <= tolerance or ys[row + 1] - ys[row] <= tolerance:
                continue
            boundary_keys = set()
            for segment in normalized:
                if segment["orientation"] == "vertical" and (
                        abs(segment["coordinate"] - xs[column]) <= tolerance or
                        abs(segment["coordinate"] - xs[column + 1]) <= tolerance):
                    boundary_keys.update(segment.get("source_entity_keys", []))
                if segment["orientation"] == "horizontal" and (
                        abs(segment["coordinate"] - ys[row]) <= tolerance or
                        abs(segment["coordinate"] - ys[row + 1]) <= tolerance):
                    boundary_keys.update(segment.get("source_entity_keys", []))
            cells.append({"row": row, "column": column,
                          "row_span": 1, "column_span": 1,
                          "bbox": [xs[column], ys[row], xs[column + 1], ys[row + 1]],
                          "source_line_entity_keys": sorted(boundary_keys)})
    return {"segments": normalized, "x_boundaries": xs, "y_boundaries": ys,
            "cells": cells, "merged_cells": []}


def assign_text_occurrences(texts, cells, *, tolerance):
    """Assign each canonical text occurrence to exactly one terminal class."""
    output = []
    for text in texts or []:
        bbox = [float(v) for v in text["bbox"]]
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        candidates = []
        for cell in cells or []:
            x0, y0, x1, y1 = cell["bbox"]
            if x0 - tolerance <= cx <= x1 + tolerance and y0 - tolerance <= cy <= y1 + tolerance:
                candidates.append(cell)
        if len(candidates) == 1:
            status, cell = "assigned_single_cell", candidates[0]
        elif len(candidates) > 1:
            # A spanning/header occurrence is one terminal assignment, never a
            # multi-active assignment.  Keep the smallest containing cell.
            cell = min(candidates, key=lambda c: (c["bbox"][2] - c["bbox"][0]) *
                       (c["bbox"][3] - c["bbox"][1]))
            status = "assigned_spanning_cell"
        else:
            status, cell = "unresolved_assignment", None
        output.append({"canonical_entity_key": text["canonical_entity_key"],
                       "cell": cell, "terminal_status": status,
                       "raw_text": text.get("raw_text", ""),
                       "provenance": text.get("provenance", {})})
    return output
