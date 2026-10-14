# Quantity drawing identity — 2026-10-14

Continues the quantity workflow on `feat/quantity-drawing-identity-20261014` (from main 95a89e5). This increment identifies every PDF page and CAD sheet without paid AI. It does not claim verified quantities.

## Why pages were unidentified
AutoCAD-plotted PDFs keep ASCII sheet numbers in the text layer but drop Korean text (CID font without a Unicode map). The previous parser needed Korean field labels in the PDF text. CAD drawings hold several sheets per file, each framed by a shared title-block form (xref or bound block) placed in model space; the previous model gave one identity per file.

## Method (core/quantity_sheet_identity.py)
1. CAD sheets: a frame is an insert of a title-block form. Cells (number, title, scale) come from the form's own labels (도면번호/DRAWING NO., 도면명, 축척), even when the form is a separate xref file in the upload or a bound, scaled, rotated block. A frame block without a readable form is accepted only when every insert carries exactly one sheet number at the same insert-local position (learned cell); numbers used once cannot be reused by a second xref at the same offset.
2. Sheet order: CAD drawing-index sheets (title contains 도면목록) define the drawing set and its order. Same-number sheets are told apart by title; a revision with the same title in another file is one slot with alternates. Without an index, sheet-number order is used.
3. PDF pages: a sheet number at the document's dominant title-block text position (learned from at least two pages) identifies the page. Between two such pages, page order names the remaining pages only when the page count equals the index slots in between; these pages are `inferred_order`, an open issue, never verified.
4. File names, index rows alone, body references and paid AI never establish identity. Sheet evidence stays local: it is excluded from every AI prompt (`_ai_cad_summary`).

## Real-input result (original 4 inputs, local run of the app path)
- Structural: 79/79 PDF pages identified — 22 by title-block number, 57 by bounded page order. 79 CAD frames, all with number and title, ordered by a 79-row CAD index.
- Architectural: 221/221 pages — 129 by title-block number, 92 by bounded order. 257 CAD frames; 35 sheets exist in two file versions (collapsed with alternates).
- Content cross-check (IDF-weighted ASCII tokens, PDF page vs CAD frame): 0 strong contradictions among inferred pages; 33 checkable structural inferred pages matched their frame best; most architectural inferred pages carry too little ASCII text to check.
- Source-drawing defects surfaced as warnings, not corrected: a title block repeating the previous sheet number, two different sheets sharing numbers across files, frame titles that differ from the index (e.g. schedule kind), sheets listed in the index without a CAD frame, and a frame missing from the index.
- Processing (local): identity resolution 1.3 s for 55 architectural files; manifest build 12 s / 52 s (PDF text extraction dominates).

## Tests
33 new tests (unit + integration through parse_dwg_from_zip, _build_source_manifest, member source refs and Excel). Full core suite: 671 passed. Python 3.10 grammar checked.

## Remaining
- Schedule definition → plan occurrence linkage (counts come from plans, not schedule rows).
- Authoritative manual takeoff comparison for accuracy.
- Architectural role vocabulary is coarse (many titles map to `other`).
- In-request ZIP check (90 s budget) may not finish large architectural ZIPs on the server; the background job budget (600 s) does.
