// 앤이 묻는다 — 오른쪽 위 질문 카드.
// 플레이어의 답은 '마음속 목소리'의 조언이다. 앤은 대개 따르지만 성격대로 할 때도 있고,
// 시간 안에 답이 없으면 선택지 중 아무거나 고른다.
import { MIN_YEAR } from '../state.js';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const CAT = { love: '사랑', family: '가족', work: '일', money: '돈', school: '학교', life: '인생', friend: '친구', health: '건강', home: '집', power: '정치' };
const MODES = [['all', '모두 물어보기', '작은 고민부터 인생의 큰 갈림길까지'], ['big', '큰 결정만', '연애·결혼·아이·일·이주·은퇴 같은 갈림길만'], ['off', '묻지 않기', '앤들이 모두 알아서 정합니다']];

export class AskPanel {
  constructor({ state, api, onAnn, sound }) {
    Object.assign(this, { state, api, onAnn, sound });
    this.asks = [];
    this.log = [];
    this.mode = 'all';
    this.idx = 0;
    this.view = 'ask'; // ask · log · mode
    this.open = false;
    this.result = null;
    this.busy = false;
    this.card = document.getElementById('ask-card');
    this.btn = document.getElementById('btn-ask');
    this.badge = document.getElementById('ask-badge');
    this.btn.classList.remove('hidden');
    this.card.onclick = e => this.onClick(e);
    this.timer = setInterval(() => this.tick(), 1000);
    this.poll = setInterval(() => this.load(), 60_000);
    // 알림을 눌러 돌아왔거나 앱을 다시 열었을 때: 기다리는 질문이 있으면 바로 보여 준다
    this.onVis = () => { if (document.visibilityState === 'visible') this.load(true); };
    document.addEventListener('visibilitychange', this.onVis);
    this.load(true);
  }
  destroy() {
    clearInterval(this.timer); clearInterval(this.poll);
    document.removeEventListener('visibilitychange', this.onVis);
    this.card.classList.add('hidden'); this.card.innerHTML = '';
    this.btn.classList.add('hidden');
  }

  async load(first = false) {
    const r = await this.api.list().catch(() => null);
    if (!r) return;
    this.apply(r);
    if (first && this.asks.length) this.show(true); // 들어오자마자, 또는 알림을 눌러 들어왔을 때
    this.render();
  }
  apply(r) {
    this.asks = (r.asks || []).sort((a, b) => a.until - b.until);
    this.log = r.log || [];
    this.mode = r.mode || 'all';
    if (this.idx >= this.asks.length) this.idx = 0;
    this.updateBadge();
  }
  updateBadge() {
    const n = this.asks.length;
    this.badge.textContent = String(n);
    this.badge.classList.toggle('hidden', !n);
    this.btn.classList.toggle('pulse', n > 0 && !this.open);
  }

  /** 서버가 보내는 알림 중 질문·답 결과 */
  onNotes(notes) {
    let added = false;
    for (const n of notes) {
      if (n.ask && !this.asks.some(q => q.id === n.ask.id)) { this.asks.push(n.ask); added = true; }
      if (n.askDone) {
        const i = this.asks.findIndex(q => q.id === n.askDone.id);
        if (i >= 0) { this.asks.splice(i, 1); if (this.idx >= this.asks.length) this.idx = 0; }
        if (!this.result || this.result.id !== n.askDone.id) this.load();
      }
    }
    this.asks.sort((a, b) => a.until - b.until);
    if (added) { this.sound?.sfx('ask'); if (!this.open && !this.closedAt) this.show(true); }
    this.updateBadge();
    this.render();
  }
  /** 알림 토스트로 보여 주지 않을 알림 (카드가 대신 보여 준다) */
  handles(n) { return !!(n.ask || n.askDone); }

  toggle() { this.show(!this.open); }
  show(on) {
    this.open = on;
    if (!on) this.closedAt = Date.now(); else this.closedAt = 0;
    if (on && !this.asks.length && this.view === 'ask' && !this.result) this.view = 'log';
    this.updateBadge();
    this.render();
  }

  remain(q) {
    const st = this.state, now = st.now();
    const game = Math.max(0, q.until - now);
    if (!st.scale) return '⏸ 멈춤';
    const real = game / st.scale; // 실제 분
    const gh = Math.floor(game / 60);
    const rt = real >= 60 ? `${Math.floor(real / 60)}시간 ${Math.round(real % 60)}분` : real >= 1 ? `${Math.ceil(real)}분` : `${Math.max(1, Math.round(real * 60))}초`;
    return `⏳ ${rt}${gh >= 1 ? ` <span class="sub small" style="margin:0">(게임 속 ${gh}시간)</span>` : ''}`;
  }
  tick() {
    if (!this.open || this.view !== 'ask') return;
    // 세계 정보가 늦게 오면 나이를 채우려고 한 번 더 그린다
    const na = this.card.querySelector('[data-noage]');
    if (na && this.state.anns.size) { this.render(); return; }
    const q = this.asks[this.idx];
    const el = this.card.querySelector('[data-remain]');
    if (q && el) el.innerHTML = this.remain(q);
  }

  place() {
    // 위쪽 막대 바로 아래, 오른쪽 패널이 열려 있으면 그 옆에
    const tb = document.querySelector('#hud .topbar');
    const top = tb ? tb.getBoundingClientRect().bottom + 6 : 64;
    this.card.style.top = top + 'px';
    const right = document.getElementById('right');
    const wide = innerWidth > 860;
    this.card.style.right = wide && right && !right.classList.contains('hidden') ? (right.getBoundingClientRect().width + 24) + 'px' : '';
  }

  render() {
    const c = this.card;
    if (!this.open) { c.classList.add('hidden'); return; }
    c.classList.remove('hidden');
    this.place();
    const tabs = `<div class="ask-tabs">${[['ask', `질문${this.asks.length ? ` ${this.asks.length}` : ''}`], ['log', '지난 답'], ['mode', '설정']].map(([k, n]) => `<button data-view="${k}" class="${this.view === k ? 'on' : ''}">${n}</button>`).join('')}<button class="x" data-ask-close title="닫기">✕</button></div>`;
    let body = '';
    if (this.view === 'ask') {
      if (this.result) body = this.resultHTML(this.result);
      else if (!this.asks.length) body = `<p class="sub">지금은 고민 중인 앤이 없어요. 앤이 인생의 갈림길에 서면 여기서 당신에게 묻습니다.</p>`;
      else body = this.askHTML(this.asks[this.idx], this.idx, this.asks.length);
    } else if (this.view === 'log') {
      body = this.log.length ? `<div class="ask-log">${this.log.map(L => this.logHTML(L)).join('')}</div>` : '<p class="sub">아직 지난 답이 없어요.</p>';
    } else {
      body = `<p class="sub small">당신의 답은 명령이 아니라 앤의 '마음속 목소리'입니다. 앤은 대개 따르지만, 마음과 크게 어긋나면 성격대로 하기도 합니다. 시간 안에 답이 없으면 앤은 선택지 중 <b>아무거나</b> 고릅니다.</p>` +
        MODES.map(([k, n, d]) => `<label class="ask-mode ${this.mode === k ? 'on' : ''}"><input type="radio" name="askmode" value="${k}" ${this.mode === k ? 'checked' : ''}><div><b>${n}</b><div class="sub small" style="margin:0">${d}</div></div></label>`).join('');
    }
    c.innerHTML = tabs + `<div class="ask-body">${body}</div>`;
  }

  annLine(q) {
    const a = this.state.anns.get(q.ann);
    const age = a ? Math.floor((this.state.now() - a.b) / MIN_YEAR) : null;
    return `<span class="linklike" data-ann="${q.ann}">${esc(q.name)}</span>${age != null ? ` <span class="sub small" style="margin:0">${a.g === 'M' ? '♂' : '♀'} ${age}세</span>` : '<i data-noage></i>'}`;
  }
  askHTML(q, i, n) {
    return `<div class="ask-q ${q.big ? 'big' : ''}">
      <div class="ask-head"><span class="ic">${q.icon || '❓'}</span><div>${this.annLine(q)}<div class="sub small" style="margin:0">${CAT[q.cat] || '고민'}${q.big ? ' · 인생의 갈림길' : ''}</div></div><span class="remain" data-remain>${this.remain(q)}</span></div>
      <div class="ask-text">“${esc(q.q)}”</div>
      <div class="ask-opts">${q.opts.map(o => `<button class="btn" data-opt="${esc(o.k)}" ${this.busy ? 'disabled' : ''}>${esc(o.t)}</button>`).join('')}</div>
      <div class="ask-foot"><span class="sub small" style="margin:0">마음속 목소리로 조언합니다 — 앤은 성격대로 할 수도 있어요</span>${n > 1 ? `<span class="nav"><button data-nav="-1">‹</button>${i + 1}/${n}<button data-nav="1">›</button></span>` : ''}</div>
    </div>`;
  }
  verdict(L) {
    if (!L.did) return ['🌫️', '없던 일이 되었다'];
    if (L.ans == null) return L.rnd ? ['🎲', '답이 없어 아무거나 골랐다'] : ['🌿', '스스로 정했다'];
    if (L.heed) return ['🤝', '조언을 따랐다'];
    return ['🙅', '자기 뜻대로 했다'];
  }
  resultHTML(L) {
    const [ic, v] = this.verdict(L);
    const adv = L.ans && L.opts?.find(o => o.k === L.ans)?.t;
    return `<div class="ask-result ${L.heed === false ? 'refused' : ''}">
      <div class="ask-head"><span class="ic">${ic}</span><div>${this.annLine(L)}<div class="sub small" style="margin:0">${v}</div></div></div>
      <div class="ask-text">“${esc(L.q)}”</div>
      ${adv ? `<div class="sub small" style="margin:4px 0 0">당신의 조언: <b>${esc(adv)}</b></div>` : ''}
      ${L.didT ? `<div class="did">→ ${esc(L.didT)}</div>` : ''}${L.text ? `<div class="out">${esc(L.text)}</div>` : ''}
      <div class="ask-foot"><span></span><button class="btn small" data-next>${this.asks.length ? `다음 질문 (${this.asks.length})` : '확인'}</button></div>
    </div>`;
  }
  logHTML(L) {
    const [ic, v] = this.verdict(L);
    const adv = L.ans && L.opts?.find(o => o.k === L.ans)?.t;
    return `<div class="ask-li"><span class="ic">${ic}</span><div><div><b class="linklike" data-ann="${L.ann}">${esc(L.name)}</b> <span class="sub small" style="margin:0">${v}</span></div>
      <div class="q">${esc(L.q)}</div>
      <div class="sub small" style="margin:0">${adv ? `조언 「${esc(adv)}」 → ` : ''}${L.didT ? `「${esc(L.didT)}」` : ''}${L.text ? ` · ${esc(L.text)}` : ''}</div></div></div>`;
  }

  async answer(k) {
    const q = this.asks[this.idx];
    if (!q || this.busy) return;
    this.busy = true;
    this.sound?.sfx('click');
    try {
      const r = await this.api.answer(q.id, k);
      this.apply(r);
      this.result = r.result;
      this.sound?.sfx(r.result?.heed ? 'note' : 'open');
    } catch (e) {
      this.result = { ...q, ans: k, did: null, text: e.message };
      this.load();
    }
    this.busy = false;
    this.render();
  }
  async setMode(m) {
    const r = await this.api.mode(m).catch(() => null);
    if (r) { this.apply(r); this.render(); }
  }

  onClick(e) {
    const t = e.target;
    const ann = t.closest('[data-ann]');
    if (ann) { this.onAnn?.(+ann.dataset.ann); return; }
    if (t.closest('[data-ask-close]')) { this.show(false); return; }
    const v = t.closest('[data-view]');
    if (v) { this.view = v.dataset.view; this.result = this.view === 'ask' ? this.result : null; this.render(); return; }
    const o = t.closest('[data-opt]');
    if (o) { this.answer(o.dataset.opt); return; }
    const nv = t.closest('[data-nav]');
    if (nv && this.asks.length) { this.idx = (this.idx + +nv.dataset.nav + this.asks.length) % this.asks.length; this.render(); return; }
    if (t.closest('[data-next]')) { this.result = null; this.idx = 0; if (!this.asks.length) this.show(false); else this.render(); return; }
    const m = t.closest('input[name=askmode]');
    if (m) this.setMode(m.value);
  }
}
