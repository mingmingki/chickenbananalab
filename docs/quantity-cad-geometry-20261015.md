# Quantity: beam and wall lengths measured from CAD — 2026-10-15

Builds on the schedule values and plan notes (main 0a165da). Branch `feat/quantity-cad-geometry-20261015`. Until now, member lengths came only from what the AI read in the PDFs. CAD evidence covered counts and schedule values. This increment measures beam and wall lengths from the plan linework itself and compares them with the extracted members. Values are compared, never replaced, and nothing here is a verified quantity.

## Method (core/quantity_geometry.py)
- **Collecting lines:** member lines are collected from model space and from ordinary blocks (pasted content), with transforms applied.
  - Beam and girder layers become `beams`; concrete, core and wall layers become `walls`.
  - Dry-wall, brick, ALC, block and partition layers are skipped.
  - Name-layer polylines are kept as leaders or extent lines.
  - Drawings with no member-name layer collect nothing.
- **Pieces:** two parallel lines whose distance is a plausible width give a piece (beams 150–1300 mm, walls 100–1000 mm). The piece length is the overlap and its width is the distance. Distances are measured on one line's normal, in frame-local coordinates, so lines drawn a hair apart in angle are still exact.
- **Spans and runs:**
  - Beam pieces touching end to end are one span. Spans break where both faces break, for example at columns.
  - Wall pieces on one line with the same thickness and gaps up to 2.5 m (junctions, doors) are one wall run. Run length is the union of its pieces, so openings and lines drawn twice do not count.
  - Short stubs and closed column outlines are not walls.
- **Naming:** a span or run takes the mark from, in priority order:
  1. a name-layer extent line drawn along it with the label at its end;
  2. a leader from the label ending on it;
  3. the nearest label of the same member family beside it (within 3 m).

  Labels of different marks along one run split it at midpoints. Everything else is reported as unlabelled, with its length per width.
- **Units and storage:** model units are millimetres. Sheet measurements stay local, like the rest of the sheet evidence, and never enter AI prompts.

## Member comparison
- **Placements:** placements (mark × plan) carry the measured runs, length and length per width. Each plan carries per-family totals and the unlabelled remainder.
- **`geometry_evidence`** compares the member's `length_m × count` with the CAD length on the same section and floors:
  - Outcomes: match within 5%, differs, CAD length only, lengths vary by floor, or no CAD length.
  - Widths: the note lists measured widths by length. When most of the length differs from the schedule width or thickness of that level, it asks to check the drawn width or the label assignment.
  - Refresh: it is refreshed when a review is confirmed.
- **Review UI:** shows "CAD 실측" (⚠ when lengths differ).
- **Excel:**
  - New sheet "CAD 실측 치수": one row per plan and mark, a row per plan for the unlabelled length, and reference copies marked as not counted.
  - "부재 출처" gains CAD measurement columns.

## Real-input result (structural CAD, local)
- **Beams:** 93–98% of measured length is named on parking and basement plans (for example 1,218 m on one parking floor, 95% named).
- **Walls:**
  - Typical floors: 92–97% named.
  - Parking and building basement plans: mostly unlabelled. Those plans also draw the other area for reference, and their wall labels name only some walls.
- **Units checked:** apartment column outlines measure exactly their schedule sizes.
- **A source difference surfaced:** on one parking floor, girders and beams are drawn 400 mm wide while their schedule rows say 500 × 600. The raw line spacing confirms 400.
- **Time:** structural parse 23.7 s (about 22 s before). Architectural parse 125.1 s (about 123–124 s before; it has no member-name layers, so nothing is measured). Link data is 307 KB.

## Tests
9 new tests on synthetic drawings:
- Beams: spans broken at a column, one run split between two marks, a beam inside a block, an unlabelled piece.
- Walls: door gap, dry wall, a line drawn twice, leader, extent line, column outline.
- Comparison with members, review checklist, confirmation refresh and Excel.

Full core suite passes. Python 3.10 grammar checked.

## Remaining
- On dense plans, a label beside two walls can claim the neighbouring wall. The width note flags these, and the schedule thickness is not yet used to pick between them.
- Floor heights are not read yet, so wall areas and beam/wall volumes from CAD lengths are not computed. That is the next step.
- Columns are counted by labels and unlabelled-default notes. Column outlines are not counted from geometry yet.
- Slabs (areas) are not measured.
