# Quantity: floor heights from CAD and a CAD-based concrete reference — 2026-10-15

Builds on the measured beam and wall lengths (main 4db9cbe). Branch `feat/quantity-floor-heights-20261015`. Storey heights used to come only from the AI reading architectural elevations. This increment reads levels from the CAD drawings, compares member heights with them, and builds a gross concrete reference from drawing evidence alone. Values are compared, never replaced, and nothing here is a verified quantity.

## Levels (core/quantity_levels.py)
- **Elevation marks:** a floor name is paired with an elevation, for example "3층 바닥" beside "(EL+22.30)", or a single text such as "GL±0=EL+16.20". These appear on building elevations and sections. Elevations written in millimetres are converted.
- **Section-detail marks:** floor names on the level lines of a section detail ("지하1층 SL", "주차장 지붕 SL", "GL."). Storey height is the drawn distance between neighbouring lines.
- **Sections:** the sheet title gives the section (101동, 지하주차장, 주차장). A detail titled 주동 applies to every building.
- **Storey height** = the elevation of the next floor up minus the floor's own elevation. Only neighbouring floors are used, roof to penthouse roof included.
- **Open levels:** a level is left open, and reported, in three cases:
  1. one drawing gives it several elevations (for example parking roofs at several heights);
  2. drawings disagree without a clear majority;
  3. the resulting height is not between 0 and 12 m.
- **Lookup order:** the same section name first, then a partial match. Buildings fall back to the 주동 detail. A member without a known section gets a height only when every named section agrees.
- **Inputs:** the structural upload, plus the architectural upload when it is given. Levels stay local evidence and never enter AI prompts.

## Member height evidence
- **Scope:** walls and columns compare `height_m` with the typical storey height of their floors (the most common one); other storeys in the range are named.
- **Outcomes:** same; lower by up to 0.35 m (consistent with a height measured without the slab); differs; or CAD only.
- **Display:** the review UI shows "CAD 층고" (⚠ when it differs). The evidence is refreshed at confirmation.

## CAD-based concrete reference (core/quantity_cad_estimate.py)
- **Beams:** the schedule section of that level × measured span length.
- **Walls:** thickness (the schedule row of that level, else the measured thickness) × measured run length × storey height.
- **Columns:** labels × schedule section × storey height.
- **Footings:** labels × schedule Lx × Ly × D.
- **Limits:** gross values. Openings and beam/slab or column/wall overlaps are not deducted. Unlabelled members are not included.
- **Storage:** rebuilt from the stored links when Excel is made, so it takes no storage and never goes stale.

## Excel
- "CAD 층고": section, floor, floor elevation, storey height, source and drawings, the height the AI read, and the open-level reason.
- "CAD 기반 물량(참고)": rows per mark and plan. Totals per category are shown beside the AI-based calculation with the difference. The rows left out are counted by reason.

## Real-input result (structural + architectural CAD, local)
- **101동 and 102동:** B2 3.5 m, B1 5.7 m, typical floors 2.85 m, 13th floor 2.95 m, roof 2.90 m.
  - The building elevations and the structural basement-wall details agree.
  - Floor elevations from the elevations (1st floor EL+16.60) minus the detail heights reproduce the B1 and B2 elevations written on the architectural sections.
- **Parking:** B2 3.5 m, B1 4.1 m from the structural details. The parking sections give several roof elevations in one drawing, so that roof stays open there.
- **Concrete reference:** 853 mark-and-plan rows. Rows left out are counted by reason: 58 with an unknown floor range, 157 without a measured length, 80 without a schedule section.
- **Size and time:** link data 315 KB. Parse times are unchanged (structural 24.8 s, architectural 128.0 s).

## Tests
8 new tests:
- level names and elevations, and elevation and detail heights;
- open levels and lookup fallbacks;
- height evidence, the reference estimate, pipeline, refresh and Excel.

Full core suite passes. Python 3.10 grammar checked.

## Remaining
- Slab areas and stair schedules are not read; this upload has slab rebar drawings, not slab tables. Slab thickness notes (for example THK.210 / 180 / 150) are visible on plans but not yet tied to areas.
- Deductions (openings, overlaps) are not applied to the reference.
- Roof and penthouse plans without a floor range are left out of the reference.
- Accuracy against an authoritative manual takeoff is still required.
