import unittest
from log_readability import format_line,readable_logs,safety_reason

class ReadableLogTests(unittest.TestCase):
    def test_heartbeat_explains_pause_without_claiming_normal_operation(self):
        line="04:12:12 [INFO] [BTC/USDT:USDT] CORE heartbeat AI=gemini 상태=paused_or_clock 위험점검={'status': 'idle', 'reason': 'flat'}"
        text,_=format_line(line)
        self.assertIn('BTC',text); self.assertIn('신규진입 일시정지',text)
        self.assertIn('보유 포지션 없음',text); self.assertNotIn('{',text)
        self.assertNotIn('정상',text)
        self.assertIn('PI 손절 보호주문 확인 실패',safety_reason('PI/USDT:USDT unified protection unconfirmed'))

    def test_emergency_complete_never_becomes_entry_success(self):
        line='04:10:31 [INFO] [PI/USDT:USDT] CORE {"reason":"execution","side":"long","outcome":{"status":"complete","reason":"emergency_flat_kill_retained"}}'
        text,_=format_line(line)
        self.assertIn('긴급 청산 완료',text); self.assertIn('신규진입 차단',text)
        self.assertNotIn('진입 완료',text)

    def test_gemini_reason_and_confidence_remain_visible(self):
        line='04:10:23 [INFO] [PI/USDT:USDT] Gemini 판단: long (확신도 0.65) / regime=neutral (0.60) - 일시적인 반등 가능성을 시사합니다.'
        text,_=format_line(line)
        self.assertIn('롱 진입 판단',text); self.assertIn('65%',text)
        self.assertIn('시장 중립',text); self.assertIn('일시적인 반등 가능성',text)

    def test_candidate_c_non_execution_is_not_reported_as_updated(self):
        line="04:10:09 [INFO] [DOGE/USDT:USDT] Candidate C 사이클 결과: {'intent_kind':'StopUpdateIntent','executed':False,'reason':'stop_update_would_loosen_skipped'}"
        text,_=format_line(line)
        self.assertIn('기존 손절가격을 유지',text); self.assertNotIn('처리 실행',text)

    def test_only_identical_routine_messages_condense_and_raw_stays_intact(self):
        hb="04:12:12 [INFO] [BTC/USDT:USDT] CORE heartbeat AI=gemini 상태=gemini_decision 위험점검={'status':'idle','reason':'flat'}"
        error='04:12:13 [ERROR] 주문 오류 발생'
        lines=[hb,error,hb.replace('04:12:12','04:12:42'),error]
        original=list(lines); result=readable_logs(lines)
        self.assertEqual(lines,original); self.assertEqual(len(result),3)
        self.assertEqual(sum('주문 오류' in s for s in result),2)
        self.assertTrue(any('04:12:42' in s for s in result))

    def test_invalid_structured_log_has_visible_fallback(self):
        text,_=format_line('04:12:12 [INFO] CORE {broken')
        self.assertIn('확인 필요',text); self.assertIn('원문 보기',text)
        text,_=format_line('04:12:12 [INFO] CORE {"reason":"execution","outcome":{"status":"blocked","reason":"new_unknown_reason"}}')
        self.assertIn('주문 보류',text); self.assertIn('사유 확인 필요',text)
