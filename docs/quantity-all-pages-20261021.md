# Quantity: every page of the architectural set is read — 2026-10-21

Builds on main e2167d5. Branch `feat/quantity-all-pages-20261021`.

## Problem
- **Old cap:** the architectural analysis and the elevation/section review each sent the first 80 PDF pages in one request. Pages after 80 were dropped without any note.
- **Effect on the test set:** the set has 221 pages, and its elevations and sections sit on pages 100–221, so the elevation review never saw one of them.
- **Request size:** those 80 pages were already 22.7 MB of JPEG (about 30 MB as sent). That is above the 20 MB inline image size the Gemini image guide states.
- **Memory:** all 80 pages were rendered at once, about 670 MB.
- **Structural path:** it was already batched (8 pages per request), but stopped at page 80, with a note.

## Which page is which drawing (core/quantity_pdf_pages.py)
- **Drawing numbers on pages:** the PDF of a drawing set often keeps only the drawing number of each title block as text. Here it carries no Korean text and has numbers on 129 of the 221 pages.
- **Known pages:** a page is known when it prints exactly one drawing number with the set's prefix. Known pages must keep the drawing-number order (the longest increasing run is kept), so a drawing list, a window tag or one stray number cannot shift the pages after it.
- **Unknown pages:** a run of pages without a number gets the CAD sheets numbered between its neighbours, only when the number of sheets equals the number of pages. Runs before the first and after the last known page are handled the same way.
- **Result on the test set:** all 221 pages are identified, and every gap matched exactly.

## Reading
- **Architectural quantities:**
  - Every page is read, 8 pages per request, with each image labelled by page, drawing number and title (for example "117쪽 · A-410 101동 주동단면도").
  - Each item's note starts with its pages.
  - The summary warns that the same item can appear on several pages (plan and schedule), so the pages should be checked.
- **Elevation/section review:**
  - Only elevation and section pages are read: those whose CAD title says 입면 or 단면, else those whose PDF text names an elevation or section, else every page. The notes say which.
  - A floor read in several batches is kept once, with the first value; a different value is reported in the notes.
  - Identical opening rows are kept once, and the AI is asked to put the building and the elevation name in each opening's location.
- **Batches:**
  - Each batch is rendered on its own.
  - A failed batch is named in the results and not saved for a resume, so a resume asks again.
  - A cancel names the batches it skipped.
- **Ceiling:** the page ceiling is now `QTY_MAX_PDF_PAGES` (default 300 per drawing set). It only guards against a huge upload by mistake, and pages past it are named in the results. This applies to the structural set too.
- **Removed:** the all-at-once renderer (`pdf_to_images`) is gone.
- **Resumes:** saved steps now carry version 2, so steps saved before this change are not reused.

## Real set, AI replaced by a stub (local)
| | Before | Now |
|---|---|---|
| Architectural pages read | 80 of 221 | 221 (28 requests, ≤2.7 MB each) |
| Elevation/section pages read | 0 of 34 (they are past page 80) | 34 (5 requests) |
| Largest request | 22.7 MB of JPEG | 2.7 MB |

- Page identification takes 32 s and rendering about 0.35 s per page. Peak memory locally was 561 MB.
- **Estimated AI cost:**
  - Architectural reading goes from about $0.06–0.11 to $0.44–1.05. The CAD summary sent with each batch is the larger part.
  - The elevation review goes from about $0.04–0.07 to $0.06–0.14.

## Tests
10 new tests in `core/test_quantity_all_pages.py`:
- **Page map:** gap filling only when counts match; lists, stray tags and out-of-order numbers; leading and trailing pages.
- **Readers:** 8-page batches with drawing names; elevation pages chosen from CAD titles; floors kept once with differences reported; fallbacks to PDF text and to every page; the page ceiling; failed and cancelled batches; CAD-only sets; request text.
- **Resume:** the job-level resume tests now cover batches.

Full core suite passes (752 tests). Python 3.10 grammar checked.
