# Validation record — 2026-10-01

Source: existing local autotrader directory. Runtime source bytes were preserved;
no trading implementation was modified to suppress existing failures.

Python 3.11.16; pytest 9.1.1; credentials removed and network blocked during tests.
Each historical suite ran in a separate process, avoiding duplicate module imports.

## Results

- 21 failed, 701 passed, 27 subtests passed in 8.22s
- 2 failed, 228 passed, 38 subtests passed in 5.20s
- 41 passed, 7 subtests passed in 1.28s
- 37 passed, 12 subtests passed in 0.14s
- 2 failed, 255 passed, 46 subtests passed in 5.31s

Total: 1262 passed, 25 failed, plus 130 passing subtests. Release publication is blocked.
Infrastructure tests: 13 passed (packaging, isolation, read-only SSH).
Tracked source inventory: 396 source/test files; 159 runtime release entries.
Shell syntax and workflow YAML parsing pass. Fresh dependency installation and
GitHub Actions execution are not yet verified. No production deployment performed.

## Existing failures (not hidden or marked expected)

- `tests/test_core_medium_reversal.py::test_medium_reversal_accepts_confirmed_long_to_short`
- `tests/test_core_medium_reversal.py::test_medium_reversal_rejects_low_gemini_confidence`
- `tests/test_core_medium_reversal.py::test_medium_reversal_rejects_low_gpt_confidence`
- `tests/test_core_medium_reversal.py::test_medium_reversal_rejects_unconfirmed_5m_weakness`
- `tests/test_core_medium_reversal.py::test_medium_reversal_is_only_long_to_short`
- `tests/test_core_medium_reversal.py::test_reversal_flat_confirmation_rejects_any_open_position`
- `tests/test_core_profit_lock.py::test_profit_lock_eligible_after_one_r_profit_and_extreme_move`
- `tests/test_core_profit_lock.py::test_profit_lock_is_symmetric_for_short`
- `tests/test_core_profit_lock.py::test_profit_lock_rejects_profit_below_one_r`
- `tests/test_core_profit_lock.py::test_profit_lock_rejects_non_extreme_move`
- `tests/test_core_profit_lock.py::test_profit_lock_opens_fast_reduce_gate_while_position_is_profitable`
- `tests/test_live.py::ControllerTests::test_duplicate_does_not_resubmit`
- `tests/test_live.py::ControllerTests::test_entry25_bound_actual_identity_and_protected`
- `tests/test_live.py::ControllerTests::test_external_stop_exact_fill_proof_releases_flat`
- `tests/test_live.py::EmergencyTests::test_duplicate_does_not_resubmit`
- `tests/test_live.py::EmergencyTests::test_entry25_bound_actual_identity_and_protected`
- `tests/test_live.py::EmergencyTests::test_external_stop_exact_fill_proof_releases_flat`
- `tests/test_live.py::EmergencyTests::test_known_emergency_flat_resolves_original_pending`
- `tests/test_live.py::EmergencyTests::test_terminal_emergency_partial_releases_manual_residual`
- `tests/test_service.py::ServiceTests::test_control_change_discards_finished_approval`
- `tests/test_service.py::ServiceTests::test_waiting_ai_does_not_block_risk_and_stop_invalidates`
- `gemini_checks/test_event_settings.py::EventSettingsTests::test_actual_settings_handler_bool_and_omission`
- `gemini_checks/test_settings_route.py::SettingsRouteTests::test_off_on_and_invalid_values_use_saved_config`
- `rollback_full_checks/test_event_settings.py::EventSettingsTests::test_actual_settings_handler_bool_and_omission`
- `rollback_full_checks/test_settings_route.py::SettingsRouteTests::test_off_on_and_invalid_values_use_saved_config`

The primary suite references removed medium-reversal/profit-lock functions and
has live-controller/service fixtures that no longer match current interfaces.
Historical settings tests assert behavior differing from the current handler.
Changing trading semantics solely to make old tests pass is outside this CI transition.

## Publication and connectivity

- Isolated transition commit created; original workspace and branch are unchanged.
- Direct GitHub network access failed DNS resolution (`Could not resolve host`).
- GitHub connector reads succeed, but the first Git blob write was rejected with
  `MCP tool call requires approval, but approval policy is never`.
- No GitHub write succeeded. No remote branch or Actions run was created.
- Actions query for the transition branch returned `total_count: 0`.
- DC was also denied by the same approval policy. No server command was executed.
- Existing gcloud configuration has an account and project; no evidence of a login
  expiry was observed. Do not request or share cloud/API credentials in chat.
- Required continuation: a session permitting GitHub writes/workflow execution and
  external network, then verification of the configured SSH variables/secrets.

Local candidate archive validation (not a published release): 159 regular files,
exact manifest match, SHA256
`5fbf78b69b2ab650ad7c28dd6d09a5a9617de013ec99530e70f8da85dbdee585`.
