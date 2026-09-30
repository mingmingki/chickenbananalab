"""Durable review memory. All mutations share the existing Store transaction lock."""
import json
from core_unified_store import encode

class EventMemory:
    def __init__(self,store):self.store=store
    def context(self,symbol,lifecycle_id):
        with self.store.lock:
            db=self.store.db
            row=db.execute('SELECT payload FROM ai_observations WHERE symbol=?',(symbol,)).fetchone()
            m=json.loads(row[0]) if row else {}
            rows=db.execute('SELECT request_id,lifecycle_id,completed_ms,payload FROM ai_decisions WHERE symbol=? ORDER BY completed_ms DESC LIMIT 12',(symbol,)).fetchall()
            if rows:
                rid,life,stamp,payload=rows[0];g=json.loads(payload)
                m.update(last_success_ms=stamp,last_success_price=g['_price'],last_success_atr5=g['_atr5'],basis_version=rid)
            decisions=[dict(json.loads(p),completed_ms=t) for _,life,t,p in rows if life==lifecycle_id][:3]
            m['decisions']=decisions
            if decisions:
                g=decisions[0]
                for k in ('thesis','next_confirmation_price','invalidation_price'):m[k]=g.get(k)
            else:
                for k in ('thesis','next_confirmation_price','invalidation_price'):m.pop(k,None)
            closed=next((json.loads(p) for _,_,_,p in rows if json.loads(p).get('action')=='close'),None)
            if closed:m['last_close_reason']=closed.get('reasoning','')
            if lifecycle_id:
                state=self.store.state(symbol)
                if state and state.get('position_id')==lifecycle_id and state.get('entry_thesis'):
                    m['entry_thesis']=state['entry_thesis']
                    if not decisions:
                        m.update(thesis=state['entry_thesis'],next_confirmation_price=state.get('next_confirmation_price'),invalidation_price=state.get('invalidation_price'))
            return m
    def observe(self,symbol,state):
        # Persist only detector state, never overwrite concurrently completed decisions.
        payload={k:state[k] for k in ('detector_state','waiting_events') if k in state}
        with self.store.transaction():
            self.store.db.execute('INSERT OR REPLACE INTO ai_observations VALUES(?,?)',(symbol,encode(payload)))
    def retry_after(self,symbol):
        with self.store.lock:
            rows=self.store.db.execute("SELECT requested_ms,status,payload FROM ai_requests WHERE symbol=? AND (status='complete' OR status LIKE 'failed:%') ORDER BY requested_ms DESC LIMIT 16",(symbol,)).fetchall()
            failures=[]
            for row in rows:
                if row[1]=='complete':break
                failures.append(row)
            if not failures:return 0
            stamp,_,payload=failures[0]
            ended=json.loads(payload).get('failed_ms',stamp)
            return ended+min(1800000,300000*(2**min(len(failures)-1,3)))

    def reserve(self,candidate,events,now_ms,risk_priority):
        with self.store.transaction():
            db=self.store.db;symbol=candidate['symbol']
            def no(reason,ready=None):return dict(allowed=False,reason=reason,next_allowed_ms=ready)
            if db.execute('SELECT 1 FROM ai_requests WHERE id=?',(candidate['id'],)).fetchone():return no('event_consumed')
            if db.execute("SELECT 1 FROM ai_requests WHERE symbol=? AND status='pending'",(symbol,)).fetchone():return no('ai_pending')
            ready=self.retry_after(symbol)
            if now_ms<ready:return no('event_error_backoff',ready)
            stamps=[r[0] for r in db.execute('SELECT requested_ms FROM ai_requests WHERE symbol=? AND requested_ms>? ORDER BY requested_ms',(symbol,now_ms-3600000))]
            if stamps and now_ms<stamps[-1]+60000:return no('event_call_spacing',stamps[-1]+60000)
            cap=18 if risk_priority else 12
            if len(stamps)>=cap:return no('event_call_budget',stamps[len(stamps)-cap]+3600000)
            if any(db.execute('SELECT 1 FROM ai_event_claims WHERE symbol=? AND event_id=?',(symbol,e['id'])).fetchone() for e in events):return no('event_consumed')
            payload=dict(candidate=candidate,events=events)
            db.execute('INSERT INTO ai_requests VALUES(?,?,?,?,?,?,?)',(candidate['id'],symbol,str(candidate['generation']),now_ms,candidate['expires_ms'],'pending',encode(payload)))
            for e in events:db.execute('INSERT INTO ai_event_claims VALUES(?,?,?)',(symbol,e['id'],candidate['id']))
            return dict(allowed=True,reason='ai_pending',next_allowed_ms=now_ms+60000)
    def finish(self,candidate_id,decision,now_ms):
        with self.store.transaction():
            db=self.store.db
            r=db.execute('SELECT symbol,requested_ms,expires_ms,status,payload FROM ai_requests WHERE id=?',(candidate_id,)).fetchone()
            if not r or r[3]!='pending' or not r[1]<=now_ms<r[2]:return False
            c=json.loads(r[4])['candidate']
            g=dict(decision,_price=c['reference_price'],_atr5=c['atr5'])
            db.execute('INSERT INTO ai_decisions VALUES(?,?,?,?,?)',(candidate_id,r[0],c.get('decision_position_id'),now_ms,encode(g)))
            db.execute("UPDATE ai_requests SET status='complete' WHERE id=?",(candidate_id,))
            return True
    def fail(self,candidate_id,reason,now_ms):
        with self.store.transaction():
            row=self.store.db.execute("SELECT payload FROM ai_requests WHERE id=? AND status='pending'",(candidate_id,)).fetchone()
            if row:
                payload=json.loads(row[0]);payload['failed_ms']=now_ms
                self.store.db.execute("UPDATE ai_requests SET status=?,payload=? WHERE id=? AND status='pending'",('failed:'+reason,encode(payload),candidate_id))
    def invalidate(self,symbol,generation):
        with self.store.transaction():
            self.store.db.execute("UPDATE ai_requests SET status='abandoned' WHERE symbol=? AND status='pending'",(symbol,))
            # Old pending events cannot outlive a settings change/restart.
            self.store.db.execute('DELETE FROM ai_observations WHERE symbol=?',(symbol,))
    def proof(self,candidate):
        with self.store.lock:
            row=self.store.db.execute("SELECT r.payload,d.payload FROM ai_requests r JOIN ai_decisions d ON d.request_id=r.id WHERE r.id=? AND r.status='complete'",(candidate['id'],)).fetchone()
            if not row:return None
            request,decision=map(json.loads,row);original=request['candidate']
            for k in ('symbol','generation','signal_ms','snapshot_id','decision_position_id','event_ids'):
                if original.get(k)!=candidate.get(k):return None
            if candidate.get('entry_fraction')!=decision.get('entry_fraction'):return None
            return dict(events=request['events'],decision={k:v for k,v in decision.items() if not k.startswith('_')})
    def unclaimed(self,symbol,events):
        with self.store.lock:
            return [e for e in events if not self.store.db.execute(
                'SELECT 1 FROM ai_event_claims WHERE symbol=? AND event_id=?',(symbol,e['id'])).fetchone()]
    def retry_events(self,symbol,after_ms):
        with self.store.lock:
            row=self.store.db.execute("SELECT payload FROM ai_requests WHERE symbol=? AND requested_ms>=? AND status LIKE 'failed:%' ORDER BY requested_ms DESC LIMIT 1",(symbol,after_ms)).fetchone()
            return json.loads(row[0])['events'] if row else []
    def counts(self,symbol,now_ms):
        with self.store.lock:
            rows=[r[0] for r in self.store.db.execute('SELECT requested_ms FROM ai_requests WHERE symbol=? AND requested_ms>? ORDER BY requested_ms',(symbol,now_ms-3600000))]
            return dict(request_count_1h=len(rows),next_allowed_ms=rows[-1]+60000 if rows else now_ms)
