# Quantity continuation — 2026-10-13

This continues the existing feat/quantity-completion-20261012 branch and draft PR. This increment adds traceable source locations and repairs duplicate member handling. It does not claim completed quantity automation or production deployment.

## Changes
- Same-mark members at different valid page/bbox locations are preserved. Only exact valid locations with compatible values are collapsed. Missing/invalid locations and inconsistent quantities are explicitly flagged for review.
- Original CAD ZIP-member hashes, paths, text handles and positions are retained. Explicit PDF/CAD title/number fields are indexed without inferring identity from filenames, body references, drawing-index entries or truncated source text.
- Source page/file identity and review state survive stored extraction, user corrections, confirmation, result reopening and Excel export. Schedule/detail locations warn that a definition does not establish an occurrence count. A page-only or file-only reference does not become a verified occurrence.
- The detailed source text index is kept out of the three CAD-bearing AI prompt paths, preserving the bounded request summary. Indexing and reopening do not issue additional Gemini calls.
- Source rows and member evidence are shown in the existing escaped review UI and two Excel sheets. Formula-like source text is exported as literal text.
- Legacy diagnostics are read-only and preserve the source artifact hash.

## Verification evidence
The validation receipt records the final suite and command exit codes. Added tests reproduce physical-location/count losses, ambiguous/false identity, incomplete CAD evidence, malformed source fields and invalid boxes before their fixes. Tests exercise original ZIP hashes/text handles, a generated two-page PDF through Poppler, actual confirmation/persistence and XLSX contents. Original input hashes and six saved CAD conversions were rechecked without paid AI.

## Actual original-input result
All 79 structural PDF pages and 221 architectural PDF pages are accounted for. Their explicit title identity remains unresolved: the exported text layer does not expose the drawing-title fields. These pages are not treated as verified identities, non-drawing pages or finished quantities. The six replayed CAD files also do not establish unique explicit title/number identities with this bounded parser. The role vocabulary is restored, but the broader grouped-sheet/CAD-block/spatial-title/vision identity pipeline remains to be adapted.

## Legacy work that remains
309 groups comprise 305 review-required groups and 4 technical blockers. There are 1,363 original faces without group mapping; all 2,062 objects lack construction classification and source-evidence rows, so supported quantity rows remain zero. Technical-only flags in the old artifact mark only two groups; the diagnostic separately counts all four technical blocker reasons.

Before completed takeoff can be claimed, restore the grouped-sheet source context and definition-to-occurrence linkage, verify full structural takeoff against an authoritative manual reference, and check deployment/persistent state on the server. Current review confirmation remains a human action; source location alone never verifies dimension, count or reinforcement correctness.

## Review and small deferred item
A separate static reviewer identified five Important issues; each was reproduced by new regression tests and corrected. Full-suite verification follows the complete fix pass. No Critical issue was identified in that review.
Excel source sheets show page/file identities. Exact bbox coordinates remain in retained member JSON; adding coordinates to the Excel sheet is deferred.
