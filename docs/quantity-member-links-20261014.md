# Quantity schedule-to-plan links — 2026-10-14

Builds on the deployed sheet identity (main cac4738). Branch `feat/quantity-member-links-20261014`. This increment links schedule definitions to plan label occurrences from CAD and compares them with extracted member counts. It does not replace counts or claim verified quantities.

## Method (core/quantity_member_links.py)
- Each identified CAD frame keeps its member-mark labels (`sheet["marks"]`, counted per layer; title-block text, rebar/size specs, floor ranges and sheet numbers excluded). This stays local and never enters AI prompts.
- A sheet is a schedule when its frame or index title says 일람표/schedule; a floor plan when it says 평면도 or 기초; 단위세대 plans are typical plans (listed, never counted as occurrences).
- Plan occurrences count only labels on member-name layers (`*COL*NAME`, `*WALL*NAME`, `*BEAM*/*GIRDER*NAME`, `*FDN*NAME`, ...). Grid bubbles and other layers never count.
- Section (동 / 주차장) and levels come from the plan title; a range (지상 3층~지상 13층) multiplies label totals. Roof/penthouse sheets without a range leave totals unknown.
- The mark category comes from the plan layer; schedule titles are a fallback. A schedule whose title names a different member kind is flagged.
- Only the primary frame of each indexed sheet is used; revision alternates are not counted twice.

## Member comparison
Members keep their extracted count. `plan_evidence` records: matched/different count on the same section and level, counts varying between floors, no plan on that floor, mark absent from plans, unknown zone, and a floor-repeat mismatch. Reviewed corrections are compared again at confirmation. The review UI shows the note (⚠ when counts differ); Excel adds "부재 배치 근거" (mark × schedule × plan rows) and comparison columns in "부재 출처".

## Real-input result (structural CAD, local)
- 252 marks; 236 defined in schedules and labelled on plans. 22 floor plans and 24 typical (unit/slab) plans; 29 plans carry member-name labels.
- Open items surfaced: 6 marks defined but never labelled on plans, 10 labelled but not defined, 39 marks on sheets without a level range, 17 marks from schedule frames titled as another member kind.
- Some plans (basement/foundation sheets of one building) have no member-name layer labels; those floors give no CAD evidence rather than a zero count.
- Sample comparison through the real ZIP: correct counts match; different counts are reported with the CAD sheet, never overwritten. Link build 0.01 s; stored matrix ≈190 KB.

## Tests
10 new tests (unit + pipeline + Excel). Full core suite: 681 passed. Python 3.10 grammar checked.

## Remaining
- Members drawn without labels (typical members, "unless noted") are not counted; label counts are evidence, not member counts.
- Slab marks and plans without member-name layers need another evidence source.
- Accuracy against an authoritative manual takeoff is still required.
