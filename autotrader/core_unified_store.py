"""Durable reservations and exactly-once fill accounting in one local database."""
import json
import sqlite3
from contextlib import contextmanager
from threading import RLock
from core_unified_policy import number
from core_unified_state import completed_state


def encode(value):
    return json.dumps(value,sort_keys=True,default=str,allow_nan=False)


def quiescent_state(state):
    return state is None or (state.get('phase')=='FLAT' and
                             number(state['filled_qty'])==0 and not state.get('pending_intent'))


class Store:
    def __init__(self,path):
        self.lock = RLock()
        self.db = sqlite3.connect(path,timeout=5,isolation_level=None,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS ai_observations(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS ai_requests(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,
            generation TEXT NOT NULL,requested_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,
            status TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS ai_event_claims(symbol TEXT NOT NULL,event_id TEXT NOT NULL,
            request_id TEXT NOT NULL,PRIMARY KEY(symbol,event_id));
          CREATE TABLE IF NOT EXISTS ai_decisions(request_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,
            lifecycle_id TEXT,completed_ms INTEGER NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS states(symbol TEXT PRIMARY KEY, revision INTEGER, payload TEXT);
          CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, symbol TEXT, status TEXT, payload TEXT);
          CREATE TABLE IF NOT EXISTS fills(trade_id TEXT, intent_id TEXT, qty TEXT, price TEXT,
                                          PRIMARY KEY(intent_id,trade_id));
          CREATE UNIQUE INDEX IF NOT EXISTS one_pending ON intents(symbol) WHERE status != 'complete';
          CREATE TABLE IF NOT EXISTS accounting(trade_id TEXT,intent_id TEXT,payload TEXT,PRIMARY KEY(intent_id,trade_id));
          CREATE TABLE IF NOT EXISTS owners(symbol TEXT PRIMARY KEY, owner TEXT NOT NULL,
                                           generation INTEGER NOT NULL, evidence TEXT NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def state(self,symbol):
        with self.lock:
            row = self.db.execute('SELECT payload FROM states WHERE symbol=?',(symbol,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_state(self,symbol,state,expected_revision):
        with self.transaction():
            current = self.state(symbol)
            if current is None:
                if expected_revision is not None: return False
                revision = 0
            else:
                if current['revision'] != expected_revision or self.pending(symbol): return False
                revision = expected_revision+1
            payload = dict(state,revision=revision)
            self.db.execute('INSERT OR REPLACE INTO states VALUES(?,?,?)',
                            (symbol,revision,encode(payload)))
            return True

    def pending(self,symbol):
        with self.lock:
            row = self.db.execute("SELECT payload FROM intents WHERE symbol=? AND status!='complete'",
                                  (symbol,)).fetchone()
            return json.loads(row[0]) if row else None

    def reserve(self,symbol,intent,expected_revision):
        with self.transaction():
            state = self.state(symbol)
            if (state is None or state['revision'] != expected_revision or self.pending(symbol)
                or self.db.execute('SELECT 1 FROM intents WHERE id=?',(intent['id'],)).fetchone()):
                return False
            qty = number(intent['qty'],positive=True)
            if intent['kind'] in ('REDUCE','EXIT') and qty > number(state['filled_qty']):
                return False
            payload = dict(intent,filled='0',terminal=False)
            self.db.execute('INSERT INTO intents VALUES(?,?,?,?)',
                            (intent['id'],symbol,'reserved',encode(payload)))
            state.update(revision=expected_revision+1,pending_intent=payload)
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',
                            (state['revision'],encode(state),symbol))
            return True

    def apply_fill(self,intent_id,trade_id,qty,price,terminal=False,filled_ms=None):
        qty,price = number(qty,positive=True),number(price,positive=True)
        with self.transaction():
            if self.db.execute('SELECT 1 FROM fills WHERE intent_id=? AND trade_id=?',
                               (intent_id,trade_id)).fetchone(): return False
            row = self.db.execute('SELECT symbol,status,payload FROM intents WHERE id=?',
                                  (intent_id,)).fetchone()
            if not row or row[1] == 'complete': raise ValueError('unexpected_fill')
            symbol,_,payload = row
            i,state = json.loads(payload),self.state(symbol)
            total = number(i['filled'])+qty
            if total > number(i['qty']): raise ValueError('overfill')
            current = number(state['filled_qty'])
            reducing = i['kind'] in ('REDUCE','EXIT')
            if reducing and qty > current: raise ValueError('over_reduce')
            state['filled_qty'] = str(current-qty if reducing else current+qty)
            if 'contract_size' in state:
                used=number(state.get('entry_risk_used',0))
                if reducing:
                    state['entry_risk_used']=str(used*(current-qty)/current)
                else:
                    state['entry_risk_used']=str(used+qty*(abs(price-number(state['stop_price']))+price*number(state.get('cost_rate',0)))*number(state['contract_size']))
            state['revision'] += 1
            i.update(filled=str(total),terminal=bool(terminal))
            state['pending_intent'] = i
            self.db.execute('INSERT INTO fills VALUES(?,?,?,?)',(trade_id,intent_id,str(qty),str(price)))
            self._account_fill(symbol,state,i['kind'],intent_id,trade_id,qty,price,filled_ms)
            self.db.execute('UPDATE intents SET status=?,payload=? WHERE id=?',
                            ('filled' if terminal else 'partial',encode(i),intent_id))
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',
                            (state['revision'],encode(state),symbol))
            return True

    def mark_terminal(self, intent_id, filled):
        """Record terminal acknowledgement even if no new fills arrived."""
        with self.transaction():
            row = self.db.execute('SELECT symbol,status,payload FROM intents WHERE id=?',
                                  (intent_id,)).fetchone()
            if not row or row[1] == 'complete':
                raise ValueError('unexpected_terminal')
            symbol,_,payload = row
            i = json.loads(payload)
            if number(filled) != number(i['filled']):
                raise ValueError('terminal_fill_mismatch')
            i['terminal'] = True
            state = self.state(symbol)
            state.update(pending_intent=i,revision=state['revision']+1)
            self.db.execute('UPDATE intents SET status=?,payload=? WHERE id=?',
                            ('terminal',encode(i),intent_id))
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',
                            (state['revision'],encode(state),symbol))

    def complete(self,intent_id,*,protection_confirmed,terminal_partial_confirmed=False):
        with self.transaction():
            row = self.db.execute('SELECT symbol,status,payload FROM intents WHERE id=?',
                                  (intent_id,)).fetchone()
            if not row: raise ValueError('unknown_intent')
            if row[1] == 'complete': return
            symbol,_,payload = row
            i,state = json.loads(payload),self.state(symbol)
            if not i['terminal'] or protection_confirmed is not True:
                raise ValueError('protection_or_terminal_unknown')
            if number(i['filled']) != number(i['qty']) and not terminal_partial_confirmed:
                # Partial cancellation must be reconciled explicitly, not treated as full completion.
                raise ValueError('partial_terminal_requires_reconciliation')
            state = completed_state(state,i)
            # Executor has also verified exchange-flat and owned cleanup for exits.
            if number(state['filled_qty']) == 0:
                state['phase'] = 'FLAT'
            self.db.execute("UPDATE intents SET status='complete' WHERE id=?",(intent_id,))
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',
                            (state['revision'],encode(state),symbol))

    def owner_record(self,symbol):
        with self.lock:
            row=self.db.execute('SELECT owner,generation,evidence FROM owners WHERE symbol=?',
                                (symbol,)).fetchone()
            if row is None:
                state=self.state(symbol)
                if self.pending(symbol) or not quiescent_state(state):
                    raise ValueError('owner_missing_with_unified_exposure')
                return dict(owner='legacy',generation=0,evidence=None)
            if row[0] not in ('legacy','unified') or type(row[1]) is not int or row[1]<1:
                raise ValueError('ownership_corrupt')
            return dict(owner=row[0],generation=row[1],evidence=json.loads(row[2]))

    def transfer_owner(self,symbol,source,target,expected_generation,evidence):
        """Caller must retain the account lock over exchange proof and this CAS."""
        if {source,target}!={'legacy','unified'}:
            raise ValueError('owner_transition')
        with self.transaction():
            current=self.owner_record(symbol)
            state=self.state(symbol)
            if (current['owner']!=source or current['generation']!=expected_generation or
                self.pending(symbol) or not quiescent_state(state)):
                return None
            generation=current['generation']+1
            self.db.execute('INSERT OR REPLACE INTO owners VALUES(?,?,?,?)',
                            (symbol,target,generation,encode(evidence)))
            return generation

    def close(self):
        with self.lock: self.db.close()

    def patch_metadata(self,symbol,**fields):
        """Account-lock protected durable metadata; never changes exposure or revision."""
        forbidden={'revision','filled_qty','pending_intent','position_id','side'}
        if forbidden.intersection(fields): raise ValueError('exposure_patch_forbidden')
        with self.transaction():
            state=self.state(symbol)
            if state is None: raise ValueError('missing_state')
            state.update(fields)
            self.db.execute('UPDATE states SET payload=? WHERE symbol=?',(encode(state),symbol))

    def reserve_action(self,symbol,identifier,payload):
        """One-shot durable reservation for scoped protection/emergency mutations."""
        with self.transaction():
            self.db.execute('CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,symbol TEXT,payload TEXT)')
            row=self.db.execute('SELECT payload FROM actions WHERE id=?',(identifier,)).fetchone()
            if row: return False
            self.db.execute('INSERT INTO actions VALUES(?,?,?)',(identifier,symbol,encode(payload)))
            return True

    def has_intent(self,identifier):
        with self.lock:
            return self.db.execute('SELECT 1 FROM intents WHERE id=?',(identifier,)).fetchone() is not None

    def external_flat(self,symbol,revision,evidence):
        with self.transaction():
            state=self.state(symbol)
            if state['revision']!=revision or self.pending(symbol) or not evidence:
                raise ValueError('external_flat_conflict')
            if sum((number(e['qty'],positive=True) for e in evidence),number(0))!=number(state['filled_qty']):
                raise ValueError('external_flat_quantity')
            for trade in evidence:
                self._account_fill(symbol,state,'EXIT',trade['order_id'],trade['trade_id'],trade['qty'],trade['price'],trade.get('filled_ms'))
            state.update(filled_qty='0',entry_risk_used='0',phase='FLAT',revision=revision+1,
                         external_close_evidence=evidence)
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',(revision+1,encode(state),symbol))

    def _account_fill(self,symbol,state,kind,intent_id,trade_id,qty,price,filled_ms):
        payload=dict(trade_id=trade_id,intent_id=intent_id,symbol=symbol,kind=kind,
                     qty=str(qty),price=str(price),filled_ms=filled_ms,
                     position_id=state.get('position_id'),side=state.get('side'),
                     contract_size=state.get('contract_size'),cost_rate=state.get('cost_rate'),
                     fee_rate=state.get('cost_rate'),fee_source='conservative_roundtrip_estimate')
        self.db.execute('INSERT OR IGNORE INTO accounting VALUES(?,?,?)',(trade_id,intent_id,encode(payload)))

    def accounting_records(self):
        with self.lock:
            rows=[json.loads(row[0]) for row in self.db.execute('SELECT payload FROM accounting ORDER BY rowid')]
            for trade_id,intent_id in self.db.execute('SELECT trade_id,intent_id FROM fills'):
                if not any(r['trade_id']==trade_id and r['intent_id']==intent_id for r in rows):
                    raise ValueError('historical_accounting_missing')
            return rows

    def actions(self,symbol):
        with self.lock:
            self.db.execute('CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,symbol TEXT,payload TEXT)')
            return [dict(json.loads(row[1]),id=row[0]) for row in self.db.execute('SELECT id,payload FROM actions WHERE symbol=?',(symbol,))]

    def emergency_flat(self,symbol,intent_id,emergency_id,trades,residual_qty=0):
        with self.transaction():
            state=self.state(symbol); pending=self.pending(symbol)
            if not pending or pending['id']!=intent_id or not pending['terminal']:
                raise ValueError('original_order_not_terminal')
            qty=sum((number(t['qty'],positive=True) for t in trades),number(0))
            residual=number(residual_qty)
            before=number(state['filled_qty'])
            if residual<0 or qty+residual!=before: raise ValueError('emergency_quantity_mismatch')
            for trade in trades:
                self._account_fill(symbol,state,'EXIT',emergency_id,trade['id'],trade['qty'],trade['price'],trade.get('filled_ms'))
            state.update(filled_qty=str(residual),entry_risk_used=str(number(state.get('entry_risk_used',0))*residual/before if before else 0),
                         phase='EMERGENCY_RESIDUAL' if residual else 'FLAT',pending_intent=None,
                         revision=state['revision']+1,emergency_required=bool(residual),
                         emergency_evidence=dict(order_id=emergency_id,trades=trades))
            self.db.execute("UPDATE intents SET status='complete' WHERE id=?",(intent_id,))
            self.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',(state['revision'],encode(state),symbol))
