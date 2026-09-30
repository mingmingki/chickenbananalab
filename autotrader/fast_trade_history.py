"""FAST(SCALP LIVE) 과거 거래기록 읽기 전용 접근자(2026-08-30, FAST 서브시스템
완전 제거 작업).

FAST 실시간 매매 엔진 자체(fast_engine.py/fast_scheduler.py/fast_live_log.py의
쓰기·상태관리 기능)는 전부 삭제됐다 - 앞으로 이 파일에 새로운 레코드가 추가되는
일은 없다. 하지만 사용자 지시(section 10) - "과거 FAST 거래 기록은 절대로
삭제하지 마라. 회계 보존을 위해 남겨야 한다" - 에 따라 fast_live_trades.jsonl
자체와, pnl_reconciliation.py가 그 파일을 읽어 리포트에 historical 그룹으로
계속 표시하는 기능만 이 작은 모듈로 남겨둔다."""
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "fast_live_trades.jsonl")


def load_all_trades(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))
