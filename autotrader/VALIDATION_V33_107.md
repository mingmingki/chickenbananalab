# 치킨바나나 v33-107 조회 지연 수정 · 2026-10-23 KST

## 확인한 원인과 수정
- 실현손익 API 응답 8.141초가 화면의 8초 제한을 넘었고, 오류가 조용히 무시돼 값이 나타나지 않았습니다.
- Candidate C 화면과 관찰용 분석에서 약 47MB의 과거 epoch 파일을 반복 파싱했습니다.
- 손익 정정 로더가 거래마다 계정 경로를 반복 해석했습니다.
- 같은 입력의 읽기 전용 투영 결과를 재사용하며, 파일 전체 SHA-256과 메타데이터를 확인해 변경 즉시 무효화합니다. 계정/용도별 동시 조회를 별도 잠금으로 처리합니다.
- 손익 정정 경로 해석은 기존 캐시 안에서 한 번만 수행합니다.
- 실현손익 조회 제한은 15초이며, 실패하면 사유를 표시하고 마지막 성공값을 유지합니다.
- 주문/청산 판단, 위험 수치, 거래 원본은 수정하지 않았습니다.

## 운영 확인
- 최종 배포: /opt/autotrader-releases/dashboard_latency_v33_107_20261023KST
- 최종 PID: 756526
- CORE·Candidate C LIVE 실행 복원 확인. 배포 후 읽기 전용 OKX 검사에서 보유 6개 포지션의 보호주문 확인.
- 동시 API 조회 5라운드/15회 모두 HTTP 200. 매 라운드 CORE 실행, Candidate C 양쪽 관측 신선도, 손익 검산 확인.

| API | 수정 전 1회 | 수정 후 중앙값 (5회) | 수정 후 범위 |
|---|---:|---:|---:|
| /api/state | 7.587s | 0.494s | 0.383–1.624s |
| /api/pnl_summary | 8.141s | 1.830s | 1.689–3.579s |
| /api/candidate_c_state | 5.311s | 1.862s | 1.761–3.654s |

- 별도 부하 조회 종료 후 CPU 10초 표본: 10.2%. 측정 구간 새 Waitress 큐 경고 0건, Traceback 0건. 짧은 운영 표본이며 장기 부하를 보장하지 않습니다.
- 초기 부팅의 첫 조회는 파일 캐시 구성 때문에 더 오래 걸릴 수 있습니다.

## 데이터 보존과 검증
- 같은 원본 1,440개 실현 이벤트로 수정 전후 비교: 손익/기간별 집계/포지션 표시/활성 epoch 전체 결과 SHA-256 완전 일치.
- 비교 해시: 4b1e4cfecf702e9cd85457c3e01b43540aad723e6b7dfb0155246c2b179fb98c
- 관련 회귀 34개 통과, 배포/복원 검사 45개 통과, 실제 JavaScript 함수의 시간초과·HTTP 오류 표시 2개 통과.
- 전체 tests/: 수정 전 2,494통과/29실패 → 수정 후 2,500통과/동일 29실패. 새 실패 0. 기존 실패는 아래에 모두 기록합니다. Node 미설치, 과거 증거/소스 해시/옛 UI 기대값 등 기존 기준 문제를 포함하며, 이번 작업에서 수정하지 않았습니다.
- bare pytest는 보관된 검사 디렉터리의 중복 모듈 때문에 52개 수집 오류가 발생하므로 실제 tests/를 비교했습니다.

## 배포 중 권한 문제와 복구
- 최초 배포 명령을 root로 실행해 600 권한 복원 파일이 root 소유가 되었고 서비스 계정이 읽지 못했습니다. 09:58:23 재시작 후 자동 복원이 차단됐습니다.
- 복원 파일 소유권을 bagmingi로 교정하고 10:00:20 다시 시작했습니다. 10:00:34 CORE, 10:00:37 Candidate C 시작 로그 및 이후 신선한 관측을 확인했으며 최종 배포 게이트도 통과했습니다. 이 동안 거래소 보호주문은 유지됐습니다.
- 다음 배포는 서비스 사용자로 배포 스크립트를 실행해야 합니다. 새 릴리스의 동일 스크립트는 bagmingi가 읽고 실행할 수 있도록 복사했습니다.

## 기존 실패 전체 목록

- tests/test_analysis_ops_retired.py::test_browser_renderer_reports_retired_and_unknown_capabilities_without_epoch_date
- tests/test_candidate_c_partial_tp_dashboard.py::test_partial_tp_reason_has_human_readable_label
- tests/test_candidate_c_profit_proof_v33_72.py::test_integrity_and_25_original_reduce_events
- tests/test_candidate_c_profit_proof_v33_72.py::test_secure_endpoint_rejects_guest_and_supports_user
- tests/test_candidate_c_tail_loss_shadow_v33_73.py::test_source_journal_sample_and_negative_strategy_choice
- tests/test_candidate_c_tail_loss_shadow_v33_73.py::test_authenticated_endpoint_and_no_trade_code_mutation
- tests/test_candidate_c_tail_loss_shadow_v33_73.py::test_previous_execution_code_bitwise_unchanged
- tests/test_core_discretionary_reduce_fee_floor_v33_76.py::test_risk_and_candidate_c_modules_unchanged
- tests/test_core_early_negative_review_v33_75.py::test_no_other_executing_module_changed
- tests/test_core_entry_economics_v33_77.py::test_replay_decision_cutoff_precedes_fill_and_has_explicit_timezone
- tests/test_core_flat_reduce_safety_v33_16.py::test_flat_race_is_resolved_once_without_global_stop
- tests/test_core_flat_reduce_safety_v33_16.py::test_unconfirmed_flat_or_failed_reconciliation_keeps_kill_switch[False-True-False]
- tests/test_core_flat_reduce_safety_v33_16.py::test_unconfirmed_flat_or_failed_reconciliation_keeps_kill_switch[True-False-False]
- tests/test_core_flat_reduce_safety_v33_16.py::test_unconfirmed_flat_or_failed_reconciliation_keeps_kill_switch[False-True-True]
- tests/test_core_profit_risk_allocator_v33_74.py::test_candidate_c_trading_source_bitwise_unchanged
- tests/test_dashboard_manual_entry.py::test_manual_entry_ui_confirms_current_core_sizing_and_skips_ai_only
- tests/test_gpt_entry_gate_dashboard.py::test_dashboard_labels_entry_gate_not_shadow
- tests/test_observability_fix_20260927.py::test_dashboard_separates_gpt_reduce_proposal_from_execution_gate
- tests/test_okx_margin_return_reporting.py::test_periodic_api_uses_historical_fixed_margin_return
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.0-0-1.0-long]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.0-0-1.0-short]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.0-0-0.5-long]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.0-0-0.5-short]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.3-3-1.0-long]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.3-3-1.0-short]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.48-4-1.0-long]
- tests/test_profit_retention_v27.py::test_authority_mfe_uses_real_fixed_initial_quantity_reducer[0.48-4-1.0-short]
- tests/test_recovery_ui_final.py::test_actual_ui_renders_cost_metadata_without_inventing_unknown_retries
- tests/test_recovery_ui_final.py::test_actual_entry_ui_never_renders_approval_as_fill_or_interprets_reason_html

## 적용 파일

- core_pnl_correction_v33_67.py
- candidate_c_breakout_shadow.py
- web_app.py
- templates/dashboard.html
- display_read_cache.py
- tests/test_dashboard_latency_v33_107.py
