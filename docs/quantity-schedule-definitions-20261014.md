# Quantity schedule definitions and plan notes — 2026-10-14

Builds on the deployed schedule-to-plan links (main 6d87e91). Branch `feat/quantity-schedule-definitions-20261014`. This increment reads schedule values, labels kept inside blocks and plan notes from CAD. It compares them with extracted members. Values and counts are compared, never replaced, and no quantity is verified.

## Labels kept inside blocks (core/quantity_sheet_identity.py)
- Evidence now carries `block_texts`: texts inside ordinary blocks placed in model space, nested blocks included. A text on layer 0 takes its insert's layer. Each block definition is read once and placed per insert.
- Xrefs and title-block forms are not opened. Sheet identity still uses model-space texts only.
- Plan label counts include block labels; `in_blocks` records how many came from blocks.
- Cap: 20,000 block texts per drawing (`block_texts_truncated`).

## Schedule tables (core/quantity_schedules.py)
- **Label column** (column and beam schedules): row labels (부호, SIZE, MAIN BAR, HOOP BAR(MID/END), 상부근, 하부근, 늑근, ...) sit in the left column, often inside a table block. Each mark is one column. A position row (양단부 / 중앙부 / ALL / 전단면) splits a mark into sub-columns.
- **Header row** (wall and footing schedules): labels sit in a header row or a stacked header band. In a band, "1"/"2" under RE-BAR SCHEDULE become rebar 1/2 and "(mm)" under H becomes the height.
  - Wall tables: rows per floor range ("1F - 13F", "B2F - B1F") under a mark written above the header.
  - Footing tables: one row per mark.
  - "↑" takes the nearest value above. In a top row it takes the value below, and that case is flagged.
- **Level-prefixed schedule marks**: "-1G1" (B1) and "RG1" (roof) define the plain plan label "G1" on that level. This applies only when the plain label is drawn on a plan of that level and the prefixed mark is not.
- **Stored size**: schedule rows are stored once in `schedule_rows`, and many marks share them.

## Plan notes and foundation texts
- Multi-line numbered notes are joined.
- A note saying another area's plans must be read (for example "parking part: see the parking plans") makes that area's marks on this plan **reference copies**. A mark's area comes from the schedules that define it. Reference copies are listed but excluded from totals and from member count comparison.
- "미표기 X : MARK" notes mark the default member. Label counts for that mark are a lower bound.
- Foundation plans keep rebar, size and thickness texts, such as mat rebar and pit sizes. Non-plotting Defpoints texts are skipped. A pit size takes its nearby name.
- Plan status: labelled, reference copies only, foundation texts only, or no member labels.

## Member comparison
- **`schedule_evidence`** per member, refreshed at confirmation:
  - Columns: section, main bars and hoops.
  - Beams: section, top and bottom bars, and stirrups.
  - Walls: thickness and vertical/horizontal bars, using only the rows of the member's floors.
  - Footings: thickness, plan size and rebar.
  - The schedule section (동 / 주차장) must match the member section.
- **`plan_evidence`** adds four outcomes:
  - Same-floor plan has only reference copies of this mark.
  - Foundation plan has texts but no labels; the extracted rebar is checked against those texts.
  - Unlabelled-default note applies.
  - Foundation members compare against the foundation plan whatever floor is named.
- **Review UI** shows "CAD 일람표 비교" (⚠ when values differ).
- **Excel** adds two sheets, "일람표 정의" (one row per schedule row) and "도면 주기·문구". "부재 출처" gains schedule comparison columns, and "부재 배치 근거" lists reference copies as not counted.

## Real-input result (structural CAD, local run of the app path)
- **Schedule definitions:** 293 read from 12 schedule sheets (13 column, 38 beam, 240 wall, 2 footing). Architectural sheets produced no tables. 236 of 255 marks have schedule values.
- **Labels in blocks:** 691 plan labels were kept inside pasted blocks. Basement plans that showed no labels now have them, and one footing count went from 11 to 64.
- **Reference copies:** 539 labels on 22 marks were drawn for the other area (building vs parking) and are excluded from totals. Three unscheduled marks appear on both areas' plans and are flagged for review.
- **Prefixed marks:** level-prefixed beam marks now define two plain plan labels.
- **Notes:** 5 unlabelled-default notes and 36 reference notes were read.
- **Source differences surfaced:** for example, a footing schedule and a foundation-plan note give different rebar for the same mark.
- **Size and time:**
  - Link data: 219 KB (187 KB before; now with schedule values).
  - Structural ZIP parse: 22 s.
  - Architectural ZIP parse: 123.8 s (123.0 s before).
  - Link build: 0.01 s.

## Tests
21 new tests: unit, ZIP parse, review checklist, confirmation refresh and Excel. Results saved before this change still refresh without error. Full core suite passes. Python 3.10 grammar is checked.

## Remaining
- Schedules drawn as section details with rebar callouts (no table) give no values.
- Slab and stair schedules are not read.
- Detailed `rebar_layers` are not compared; only the representative fields are.
- Marks with no schedule area that appear on both areas' plans still count on both, with a flag.
- Accuracy against an authoritative manual takeoff is still required.
