# Quantity source links continuation

Goal: continue the approved quantity workflow restoration on feat/quantity-completion-20261012. Preserve current free CAD conversion and the original uploads. No fabricated classification or bulk approval of legacy geometry.

## Task 1: Source identity and context pages
Create core/quantity_sources.py with explicit title/number parsing adapted from the preserved role definitions. Source IDs derive from document/member SHA plus page/path, never filename guesses. A drawing index, body reference, missing text, or conflicting number cannot establish a unique sheet link. Match CAD to PDF only when both have one explicit matching number and compatible roles. Keep unresolved inputs visible.
Tests in core/test_quantity_sources.py: arbitrary sheet IDs, spaced Korean titles, index versus actual sheet, body reference, conflicting identities, empty/missing text, ambiguous CAD duplicates, valid exact matching, and a real small PDF through Poppler.

## Task 2: Member review and export
Connect source manifest after extraction and before persisted review. Retain document/page/location references as provenance, not verified measurement claims. Distinguish schedule-only definitions and missing/invalid location from located candidates. Preserve different physical member locations during merge; collapse only identical bbox occurrences with consistent quantities. Show evidence warnings in checklist and add drawing-source/member-source Excel sheets. Keep provenance through correction, confirmation, and reopening.
Tests: separate instances cannot disappear during duplicate removal; repeated exact occurrences collapse; differing counts require review; source references survive actual confirmation/result storage and Excel export. Render controls through the existing escaped UI.

## Task 3: Legacy audit and verification
Add a read-only diagnostic of the 309 groups and 2,062 objects to document technical blockers and missing construction/source evidence. Do not rewrite those objects or total diagnostic geometry. Recheck the current full core suite, Django, migration, Python 3.10 syntax and JS syntax. Run original PDF/CAD evidence reads without paid AI. Save aggregate validation receipt and update the existing draft PR with final scope. Record any missing manual reference as a remaining accuracy dependency, not a test failure or inferred correctness.

Global constraints: no unrelated main/autotrader changes, no ODA restoration, no original input mutation, no server deployment claim, no extra Gemini requests for reopening or source indexing. Continue inline using the existing linked worktree. Tests use real source identity/count/Excel behavior; external extraction calls may be stubbed only at the boundary.
