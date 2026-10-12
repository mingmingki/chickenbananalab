# Quantity completion progress — 2026-10-12

Approved scope: integrate the preserved quantity automation with the maintained structural upload/review/calculation/export workflow. This increment restores three reusable table modules and fixes task persistence and incomplete-input disclosure. It does not claim verified final quantities for v143.

## Implemented
- Restore table_cell_model, table_semantic_model, table_roi_verifier and their original tests from wip/local-20260929; wire evidence-preserving scalar parsing into current DXF input metadata.
- Preserve explicit $INSUNITS units; report every CAD file skipped by the 60-file cap.
- Share review, extraction, progress, result and cancellation state through SQLite at QUANTITY_STATE_DB or BASE_DIR/var/quantity/state.sqlite3. Keep results/reviews/extractions for seven days.
- Bind jobs to their staff owner. Deduplicate identical kickoffs, reject ID reuse for different input, and preserve confirmation retry results.
- Atomically commit confirmed results, receipt, extraction consumption and lock release. Reject malformed edits without consuming extraction. Report dead background workers as interrupted; automatic restart of running AI work is not implemented.
- Expose owner-only recent main-job history and a saved-work button in the existing modal. Resume retained member review or display retained results without another Gemini call.
- Make /quantity/ open the current modal flow; disclose partial results and file coverage in UI and the analysis-coverage Excel sheet.

## Validation
See quantity-final-receipt-20261012.json for current commands, exit codes and original-input comparisons. The 561-test baseline was green. Added behavior tests reproduce missing modules, GET polling, process-shared state, confirmation races, invalid edits, job ownership, duplicate kickoff, skipped files, raw units, partial export and rendered controls before their fixes.

## Remaining
- v143 scene still has 309 unreviewed groups, 1,363 original faces without group mapping, and no construction classification for any object. Do not bulk-approve this data or silently total unsupported quantities.
- Restore and adapt the broader PDF/CAD identity and schedule pipeline from the preserved branch; its old ODA routes and unrelated work must not overwrite current CAD.
- Compare the full structural quantities with an authoritative manual takeoff. Six CAD replay comparisons and unit metadata cannot establish all-project quantity accuracy.
- Detailed physical 3D and finish-work scope remain separate. This branch has not been deployed to the server.

## Operating notes
State storage is shared only across workers on the same server and the same path. A new release must preserve that directory. Retention is seven days, not permanent archival. A killed AI worker must be restarted with a new job ID; completed extraction/review/result state can be resumed. Original upload files still need to be selected again when starting new analysis. Saved result/member review can be reopened directly.
