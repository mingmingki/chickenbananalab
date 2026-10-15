"""CAD-based concrete reference built only from drawing evidence.

  beams        schedule section (b X h of that level) x measured span length on each floor
  walls        thickness (schedule row of that level, else the measured one) x measured run
               length x storey height of that floor
  columns      labels on the plan x schedule section x storey height
  foundations  labels on the plan x schedule size (Lx x Ly x D)

Lengths come from plan linework, counts from member-name labels, sections from schedule
tables and storey heights from level marks. Nothing is deducted (openings, beam/slab and
column/wall overlaps) and unlabelled members are not included, so the result is a gross
reference for cross-checking, never the takeoff itself.
"""
from __future__ import annotations

import re
from collections import Counter

from . import quantity_levels as levels_mod
from . import quantity_member_links as links
from . import quantity_schedules as schedules

CATEGORIES = ("foundations", "columns", "beams", "walls")
LABELS = {"foundations": "기초", "columns": "기둥", "beams": "보", "walls": "벽체"}


def _section(text):
    """(b, h) in mm in the written order of "b X h"."""
    numbers = re.findall(r"\d[\d,]*", str(text or ""))
    if len(numbers) < 2 or not re.search(r"[X×x*]", str(text or "")):
        return None
    return int(numbers[0].replace(",", "")), int(numbers[1].replace(",", ""))


def _rows_for_level(row, matrix, level):
    """Schedule rows of a mark that apply to one level (prefixed specs and floor ranges respected)."""
    found = []
    for spec in row.get("specs") or []:
        if spec.get("levels") and level not in spec["levels"]:
            continue
        for table_row in links.spec_rows(spec, matrix):
            if table_row.get("levels_text"):
                applies = schedules.parse_level_text(table_row["levels_text"]) or []
                if level not in applies:
                    continue
            found.append(table_row)
    return found


def _main_width(measured):
    widths = (measured or {}).get("widths") or {}
    return int(max(widths, key=widths.get)) if widths else None


def estimate(matrix, table):
    """Gross concrete per mark and plan, totals per category, and why some marks are left out."""
    plans = (matrix or {}).get("plans") or []
    rows, totals, left_out = [], Counter(), Counter()
    for row in (matrix or {}).get("marks") or []:
        category = row.get("category")
        if category not in CATEGORIES:
            continue
        for occurrence in row.get("occurrences") or []:
            placed = links._place(occurrence, plans)
            levels = placed.get("levels") or []
            if not levels:
                left_out["floor_count_unknown"] += 1
                continue
            volume, heights, sections, notes = 0.0, [], set(), []
            measured = occurrence.get("measured") or {}
            length_m = measured.get("length_mm", 0) / 1000.0 if measured else None
            count = occurrence.get("count") or 0
            usable = True
            for level in levels:
                table_rows = _rows_for_level(row, matrix, level)
                if category == "foundations":
                    values = next((r["values"] for r in table_rows if r["values"].get("length_x") or r["values"].get("depth")), None)
                    dims = [schedules.parse_mm((values or {}).get(k)) for k in ("length_x", "length_y", "depth")]
                    if not all(dims):
                        usable = False
                        left_out["no_schedule_size"] += 1
                        break
                    sections.add(f"{dims[0]}×{dims[1]}×{dims[2]}")
                    volume += count * dims[0] * dims[1] * dims[2] / 1e9
                    continue
                if category == "beams":
                    section = next((s for s in (_section(r["values"].get("size")) for r in table_rows) if s), None)
                    if not length_m or not section:
                        usable = False
                        left_out["no_cad_length" if not length_m else "no_schedule_section"] += 1
                        break
                    sections.add(f"{section[0]}×{section[1]}")
                    volume += section[0] * section[1] / 1e6 * length_m
                    continue
                height, _source = levels_mod.storey_height(table, placed.get("section"), level)
                if height is None:
                    usable = False
                    left_out["no_storey_height"] += 1
                    break
                heights.append(height)
                if category == "columns":
                    section = next((s for s in (_section(r["values"].get("size")) for r in table_rows) if s), None)
                    if not section:
                        usable = False
                        left_out["no_schedule_section"] += 1
                        break
                    sections.add(f"{section[0]}×{section[1]}")
                    volume += count * section[0] * section[1] / 1e6 * height
                else:  # walls
                    thickness = next((schedules.parse_mm(r["values"].get("thickness")) for r in table_rows
                                      if schedules.parse_mm(r["values"].get("thickness"))), None)
                    if thickness is None:
                        thickness = _main_width(measured)
                        if thickness:
                            notes.append("measured_thickness")
                    if not length_m or not thickness:
                        usable = False
                        left_out["no_cad_length" if not length_m else "no_thickness"] += 1
                        break
                    sections.add(f"T{thickness}")
                    volume += thickness / 1000.0 * length_m * height
            if not usable:
                continue
            volume = round(volume, 2)
            totals[category] += volume
            rows.append({"category": category, "mark": row["mark"], "drawing_number": occurrence.get("drawing_number"),
                         "section": placed.get("section"), "levels": [levels[0], levels[-1]] if len(levels) > 1 else levels,
                         "floors": len(levels), "count": count if category in ("columns", "foundations") else None,
                         "length_m": round(length_m, 1) if category in ("beams", "walls") and length_m else None,
                         "member_section": ", ".join(sorted(sections)), "height_m": sorted(set(heights)) or None,
                         "concrete_m3": volume, "notes": sorted(set(notes))})
    return {"schema": "quantity_cad_estimate_v1", "rows": rows,
            "totals_m3": {category: round(value, 2) for category, value in totals.items()},
            "left_out": dict(left_out), "deductions_applied": False, "quantity_verified": False}
