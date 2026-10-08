import unittest
from core_unified_review import ReviewBroker,validate_event_decision

def decision(action='long',fraction=.5):
    return dict(action=action,confidence=.8,market_regime='bullish',regime_confidence=.8,
        reasoning='확정봉 변화로 판단',entry_fraction=fraction,thesis='상승 지속',changed_evidence='범위 이탈',
        next_confirmation_price=None,invalidation_price=99,allocation_reason='추세와 진입 위치, 반대 근거와 비용 검토')
class EventReviewTests(unittest.TestCase):
    def test_fraction_validation(self):
        for f in [.25,.5,.75,1]:self.assertEqual(validate_event_decision({},dict(position=None),decision(fraction=f))['entry_fraction'],f)
        for f in [None,True,.9,float('nan')]:
            with self.assertRaises(ValueError):validate_event_decision({},dict(position=None),decision(fraction=f))
    def test_hold_publishes_validated_completion_without_order(self):
        b=ReviewBroker(lambda c,s:decision('hold',None),lambda c,s:self.fail('GPT'),lambda:2000,require_gpt=lambda:False)
        c=dict(id='r',symbol='X',side='undecided',purpose='DECIDE',decision_source='gemini_event',
            review_mode='gemini',signal_ms=1000,expires_ms=61000,generation=1,snapshot_id='s')
        self.assertTrue(b.offer(c,dict(position=None)));b.close()
        self.assertFalse(b.drain())
        self.assertTrue(any(e['reason']=='ai_review_complete' for e in b.drain_events()))
    def test_entry_carries_fraction_and_invalid_response_cannot_approve(self):
        for fraction in [1,.9]:
            b=ReviewBroker(lambda c,s:decision(fraction=fraction),lambda c,s:self.fail('GPT'),lambda:2000,require_gpt=lambda:False)
            c=dict(id='r',symbol='X',side='undecided',purpose='DECIDE',decision_source='gemini_event',
                review_mode='gemini',signal_ms=1000,expires_ms=61000,generation=1,snapshot_id='s')
            b.offer(c,dict(position=None));b.close();a=b.drain()
            if fraction==1:self.assertEqual(a[0]['resolved_candidate']['entry_fraction'],1)
            else:self.assertFalse(a)
