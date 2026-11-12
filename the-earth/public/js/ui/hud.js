// 관찰 화면: 앤은 조종할 수 없다. 대신 그들의 속마음과 인생을 들여다볼 수 있다.
import { fmtDate, fmtTime, fmtDateTime, localHour, MIN_YEAR } from '../state.js';
import { avatarHTML } from './avatar.js';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = v => Math.max(0, Math.min(100, v));
const bar = (v, color = 'var(--accent)') => `<div class="bar"><i style="width:${pct(v)}%;background:${color}"></i></div>`;
const relCls = k => ['partner', 'spouse', 'crush', 'ex'].includes(k) ? 'love' : ['parent', 'child', 'sibling'].includes(k) ? 'fam' : ['friend', 'best'].includes(k) ? 'friend' : ['rival', 'enemy'].includes(k) ? 'bad' : '';
const ago = (now, t) => { const m = now - t; if (m < 60) return '방금'; if (m < 1440) return `${Math.floor(m / 60)}시간 전`; if (m < 1440 * 30) return `${Math.floor(m / 1440)}일 전`; return fmtDate(t); };

export class Hud {
  constructor({ state, w3d, live, me, meta, onDeployMore, onExit }) {
    Object.assign(this, { state, w3d, live, me, meta, onDeployMore, onExit });
    this.el = document.getElementById('hud');
    this.left = document.getElementById('left-body');
    this.right = document.getElementById('right');
    this.tab = me ? 'mine' : 'all';
    this.innerTab = 'mind';
    this.selected = null;
    this.detail = null;
    this.filter = '';
    this.newsFilter = 'all';
    this.lastEventId = state.events.length ? state.events[state.events.length - 1].id : 0;
    this.el.classList.remove('hidden');
    document.querySelectorAll('#left-tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === this.tab));
    document.getElementById('btn-deploy-more').classList.toggle('hidden', !me);
    this.bind();
    this.renderTop();
    this.renderLeft(true);
    this.timer = setInterval(() => this.renderTop(), 250);
  }

  destroy() {
    clearInterval(this.timer);
    this.el.classList.add('hidden');
    this.right.classList.add('hidden');
    this.el.onclick = null;
  }

  bind() {
    this.left.onpointerdown = e => {
      const ann = e.target.closest('[data-ann]');
      if (ann && e.button === 0) { e.preventDefault(); this.select(+ann.dataset.ann, true); }
    };
    // 앤 상세 창은 실시간으로 다시 그려지므로, 누르는 순간 바로 반응한다
    this.right.onpointerdown = e => {
      if (e.button !== 0) return;
      const t = e.target;
      const it = t.closest('[data-itab]');
      if (it) { e.preventDefault(); this.innerTab = it.dataset.itab; this.renderAnn(); return; }
      const ann = t.closest('[data-ann]');
      if (ann) { e.preventDefault(); this.select(+ann.dataset.ann, true); return; }
      const town = t.closest('[data-town]');
      if (town) { e.preventDefault(); this.flyTown(+town.dataset.town); return; }
      const act = t.closest('[data-act]')?.dataset.act;
      if (act === 'close-ann') { e.preventDefault(); this.select(null); }
      if (act === 'follow') { e.preventDefault(); this.toggleFollow(); }
    };
    this.el.onclick = e => {
      if (e.target.closest('#left-body [data-ann]') || e.target.closest('#right')) return;
      const t = e.target;
      const tab = t.closest('#left-tabs button');
      if (tab) { this.tab = tab.dataset.tab; document.querySelectorAll('#left-tabs button').forEach(b => b.classList.toggle('on', b === tab)); this.renderLeft(true); return; }
      const ann = t.closest('[data-ann]');
      if (ann) { this.select(+ann.dataset.ann, true); return; }
      const town = t.closest('[data-town]');
      if (town) { this.flyTown(+town.dataset.town); return; }
      const it = t.closest('[data-itab]');
      if (it) { this.innerTab = it.dataset.itab; this.renderAnn(); return; }
      const nf = t.closest('[data-nf]');
      if (nf) { this.newsFilter = nf.dataset.nf; this.renderLeft(true); return; }
      const act = t.closest('[data-act]')?.dataset.act;
      if (act === 'close-ann') this.select(null);
      if (act === 'follow') this.toggleFollow();
      if (act === 'deploy-more') this.onDeployMore?.();
      if (act === 'home') this.goHome();
      if (act === 'space') this.w3d.rig.flyTo({ alt: 1.4e8 });
      if (act === 'toggle-left') document.getElementById('left').classList.toggle('closed');
      if (t.closest('.brand')) this.onExit?.();
    };
    this.left.oninput = e => { if (e.target.id === 'ann-search') { this.filter = e.target.value.trim(); this.renderLeft(true, true); } };
  }

  goHome() {
    const c = this.me && this.state.countries.get(this.me.country);
    const s = c && (this.state.settlements.get(c.capital) || [...this.state.settlements.values()].find(x => x.country === c.code));
    if (s) this.flyTown(s.id);
  }

  flyTown(sid) {
    const s = this.state.settlements.get(sid);
    if (!s) return;
    this.w3d.rig.follow = null;
    this.w3d.rig.flyTo({ lat: s.lat, lon: s.lon, alt: 1.6 });
  }

  select(id, fly = false) {
    this.selected = id;
    this.w3d.cities.selected = id;
    this.live?.watch(id);
    if (!id) { this.right.classList.add('hidden'); this.w3d.rig.follow = null; this.detail = null; this.renderLeft(true); return; }
    this.detail = null;
    this.right.classList.remove('hidden');
    this.right.innerHTML = '<div id="ann-panel"><p class="empty">앤의 마음을 읽는 중…</p></div>';
    if (fly) {
      const a = this.state.anns.get(id);
      if (a?.al) {
        this.following = true;
        this.w3d.rig.follow = () => this.w3d.cities.annLL(this.state, id);
        this.w3d.rig.flyTo({ alt: Math.min(this.w3d.rig.tgt.alt, 0.09) });
      }
    }
    this.renderLeft(true);
  }

  toggleFollow() {
    const id = this.selected;
    if (!id) return;
    if (this.w3d.rig.follow) { this.w3d.rig.follow = null; }
    else { this.w3d.rig.follow = () => this.w3d.cities.annLL(this.state, id); if (this.w3d.rig.alt > 2) this.w3d.rig.flyTo({ alt: 0.09 }); }
    this.renderAnn();
  }

  onAnnDetail(d) {
    if (!d || d.id !== this.selected) return;
    this.detail = d;
    this.renderAnn();
  }

  onTick() {
    this.renderLeft(false);
    // 새 소식: 내 앤 관련은 토스트
    const evs = this.state.events.filter(e => e.id > this.lastEventId);
    if (evs.length) {
      this.lastEventId = evs[evs.length - 1].id;
      const tk = document.getElementById('ticker');
      const top = evs.sort((a, b) => b.imp - a.imp)[0];
      tk.textContent = top.text;
      if (this.me) {
        const mine = evs.find(e => e.anns?.some(id => this.state.anns.get(id)?.o === this.me.id) && e.imp >= 1);
        if (mine) this.toast(mine.text);
      }
    }
  }

  toast(text) {
    const t = document.getElementById('toast');
    t.textContent = text;
    t.classList.remove('hidden');
    clearTimeout(this.toastT);
    this.toastT = setTimeout(() => t.classList.add('hidden'), 4500);
  }

  renderTop() {
    const st = this.state;
    if (!st.world) return;
    const now = st.now();
    document.getElementById('hud-server').textContent = st.world.name;
    document.getElementById('hud-date').textContent = fmtDateTime(now);
    let sub = `세계시 · ×${st.scale}`;
    const focus = this.selected && st.anns.get(this.selected);
    const s = focus ? st.settlements.get(focus.s) : (this.me && st.settlements.get(st.countries.get(this.me.country)?.capital));
    if (s) sub = `${s.name} 현지 ${String(Math.floor(localHour(now, s.lon))).padStart(2, '0')}:${String(Math.floor((localHour(now, s.lon) % 1) * 60)).padStart(2, '0')} · ${sub}`;
    let pop = 0; for (const a of st.anns.values()) if (a.al) pop++;
    document.getElementById('hud-sub').textContent = `${sub} · 세계 인구 ${pop}명`;
    const meEl = document.getElementById('hud-me');
    if (this.me) {
      const p = st.players.get(this.me.id);
      const c = st.countries.get(this.me.country);
      meEl.innerHTML = `<span class="dot" style="background:${p?.color || '#888'}"></span>${esc(this.me.name)} · ${esc(c?.name || '')}`;
    } else meEl.innerHTML = '👁️ 관전 중';
  }

  annRow(a) {
    const st = this.state;
    const ps = st.pos.get(a.id);
    const age = st.ageOf(a);
    const emoji = a.al ? (this.meta.acts[ps?.act]?.emoji || (ps?.travel ? '🧭' : '')) : '🕯️';
    const color = st.playerColor(a.o);
    return `<div class="ann-row ${a.id === this.selected ? 'on' : ''} ${a.al ? '' : 'dead'}" data-ann="${a.id}">
      ${avatarHTML({ ...a, age }, color)}
      <div style="min-width:0"><div class="nm">${esc(a.n)} <small>${a.g === 'M' ? '♂' : '♀'} ${age}세</small>${a.ld ? `<span class="badge">${esc(a.ld)}</span>` : ''}</div>
      <div class="job">${a.al ? esc(a.j || (age < 19 ? (age < 3 ? '아기' : '학생') : '무직')) : '세상을 떠남'}${a.gen > 1 ? ` · ${a.gen}세대` : ''}</div></div>
      <div class="act" title="${esc(this.meta.acts[ps?.act]?.label || '')}">${emoji}</div>
    </div>`;
  }

  renderLeft(force, keepFocus) {
    const now = performance.now();
    if (!force && now - (this.lastLeft || 0) < 1000) return;
    this.lastLeft = now;
    const st = this.state;
    const L = this.left;
    const sc = L.scrollTop;
    let html = '';
    if (this.tab === 'mine') {
      if (!this.me) html = '<p class="empty">관전 중입니다.<br>모든 앤 탭에서 다른 플레이어의 앤들을 지켜보세요.</p>';
      else {
        const mine = [...st.anns.values()].filter(a => a.o === this.me.id).sort((a, b) => (b.al - a.al) || (a.b - b.b));
        html = mine.length ? mine.map(a => this.annRow(a)).join('') : '<p class="empty">아직 투입한 앤이 없습니다.</p>';
        const born = mine.filter(a => a.gen > 1).length;
        if (mine.length) html = `<p class="sub small" style="margin:0 4px 8px">살아 있는 앤 ${mine.filter(a => a.al).length}명 · 태어난 후손 ${born}명</p>` + html;
      }
    } else if (this.tab === 'all') {
      const f = this.filter.toLowerCase();
      const list = [...st.anns.values()].filter(a => a.al && (!f || a.n.toLowerCase().includes(f) || (a.j || '').toLowerCase().includes(f) || (st.players.get(a.o)?.name || '').toLowerCase().includes(f)))
        .sort((a, b) => (b.ld ? 1 : 0) - (a.ld ? 1 : 0) || a.b - b.b).slice(0, 250);
      const listHtml = list.map(a => this.annRow(a)).join('') || '<p class="empty">아무도 없습니다.</p>';
      const box = L.querySelector('#ann-list');
      if (box && (keepFocus || document.activeElement?.id === 'ann-search')) { box.innerHTML = listHtml; return; }
      const players = [...st.players.values()].map(p => `<span class="chip"><span class="dot" style="background:${p.color}"></span>${esc(p.name)} · ${esc(st.countries.get(p.country)?.name || '')} · ${p.alive ?? p.anns}명</span>`).join(' ');
      html = `<input id="ann-search" class="search" placeholder="이름 · 직업 · 플레이어로 찾기" value="${esc(this.filter)}">
        <div class="tags" style="margin:0 0 10px">${players}</div><div id="ann-list">${listHtml}</div>`;
    } else if (this.tab === 'nations') {
      const cs = [...st.countries.values()].sort((a, b) => b.pop - a.pop);
      html = cs.map(c => {
        const prog = c.nextTech ? (c.tech - c.curTech) / (c.nextTech - c.curTech) * 100 : 100;
        const towns = [...st.settlements.values()].filter(s => s.country === c.code).sort((a, b) => b.pop - a.pop);
        return `<div class="card">
          <h3>${esc(c.name)} <span class="chip">${esc(c.eraName)}</span></h3>
          <div class="row"><span>기술 → 다음 시대</span><b>${Math.round(prog)}%</b></div>${bar(prog, 'linear-gradient(90deg,#58b4ff,#7ef0c8)')}
          <div class="row"><span>지도자</span><b>${c.leader ? `<span class="linklike" data-ann="${c.leader.id}">${esc(c.leader.title)} ${esc(c.leader.name)}</span>` : '없음'}</b></div>
          <div class="row"><span>인구</span><b>${c.pop}명</b></div>
          <div class="row"><span>국고 · 세율</span><b>${c.treasury.toLocaleString()} · ${c.tax}%</b></div>
          <div class="row"><span>정착지</span><b>${towns.map(s => `<span class="linklike" data-town="${s.id}">${esc(s.name)}</span>(${esc(s.levelName)})`).join(', ')}</b></div>
          ${c.discoveries.length ? `<div class="tags">${c.discoveries.map(d => `<span>💡${esc(d)}</span>`).join('')}</div>` : ''}
          ${c.history.length ? `<p class="sub small">${c.history.slice(-4).reverse().map(h => `${fmtDate(h.t)} ${esc(h.text)}`).join('<br>')}</p>` : ''}
        </div>`;
      }).join('') || '<p class="empty">아직 문명이 시작된 나라가 없습니다.</p>';
    } else if (this.tab === 'biz') {
      const cos = [...st.companies.values()].sort((a, b) => b.emp - a.emp);
      html = cos.map(co => {
        const ceo = co.ceo && st.anns.get(co.ceo);
        const s = st.settlements.get(co.sid);
        return `<div class="card"><h3>${co.pub ? '🏛️' : '🏢'} ${esc(co.name)} <span class="chip">${esc(co.indName)}</span></h3>
          <div class="row"><span>${co.pub ? '공공기관' : '대표'}</span><b>${ceo ? `<span class="linklike" data-ann="${ceo.id}">${esc(ceo.n)}</span>` : co.pub ? (st.countries.get(co.country)?.name || '') : '-'}</b></div>
          <div class="row"><span>직원 · 자금</span><b>${co.emp}명 · ${co.pub ? '국고' : co.money.toLocaleString()}</b></div>
          <div class="row"><span>위치 · 설립</span><b>${s ? `<span class="linklike" data-town="${s.id}">${esc(s.name)}</span>` : ''} · ${fmtDate(co.founded)}</b></div></div>`;
      }).join('') || '<p class="empty">아직 회사가 없습니다. 농경 시대가 되면 야망 있는 앤들이 회사를 세웁니다.</p>';
    } else if (this.tab === 'news') {
      const nf = this.newsFilter;
      let items = [];
      if (nf !== 'chat') for (const e of st.events) {
        const mine = this.me && e.anns?.some(id => st.anns.get(id)?.o === this.me.id);
        if (nf === 'mine' && !mine) continue;
        items.push({ t: e.t, html: `<div class="feed-item imp${e.imp} ${mine ? 'mine' : ''}"><time>${fmtDateTime(e.t)}</time>${esc(e.text)}</div>` });
      }
      if (nf === 'chat' || nf === 'all') for (const c of st.chat) {
        items.push({ t: c.t, html: `<div class="feed-item chat"><time>${fmtDateTime(c.t)} · 💬</time>${esc(c.line)}</div>` });
      }
      items.sort((a, b) => b.t - a.t);
      html = `<div class="inner-tabs">${[['all', '전체'], ['mine', '내 앤'], ['chat', '대화']].map(([k, n]) => `<button data-nf="${k}" class="${nf === k ? 'on' : ''}">${n}</button>`).join('')}</div>` +
        (items.slice(0, 150).map(i => i.html).join('') || '<p class="empty">아직 소식이 없습니다.</p>');
    }
    if (html === this.lastLeftHtml && !keepFocus) return;
    this.lastLeftHtml = html;
    L.innerHTML = html;
    L.scrollTop = sc;
    if (keepFocus) { const s = L.querySelector('#ann-search'); if (s) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); } }
  }

  renderAnn() {
    const d = this.detail;
    if (!d) return;
    const st = this.state;
    const panelBody = this.right.querySelector('.ap-body');
    const scroll = panelBody ? panelBody.scrollTop : 0;
    const color = d.owner?.color || st.playerColor(d.o);
    const now = st.now();
    const act = d.act;
    const nd = d.nd || {};
    const follow = !!this.w3d.rig.follow;
    const tab = this.innerTab;
    let body = '';
    if (tab === 'mind') {
      body += `<div class="sec">속마음</div>` + (d.th.length ? d.th.slice(0, 12).map(t => `<div class="thought">${esc(t.text)}<time>${ago(now, t.t)}</time></div>`).join('') : '<p class="sub">아직 아무 생각도 들려오지 않는다.</p>');
      if (d.why?.length && d.al) {
        body += `<div class="sec">마지막 고민 — 왜 이 행동을 골랐을까</div>` + d.why.map((w, i) => `<div class="why ${i === 0 ? 'top' : ''}"><span>${this.meta.acts[w.a]?.emoji || '•'}</span><div><b>${esc(this.meta.acts[w.a]?.label || w.a)}${w.k && w.k !== 'chat' ? ` (${{ date: '데이트', confess: '고백', propose: '청혼', comfort: '위로', family: '가족' }[w.k] || w.k})` : ''}</b><div class="rs">${esc(w.r.join(' · ') || '그냥')}</div></div><span class="sc">${w.s}</span></div>`).join('') + `<p class="sub small">점수가 높을수록 끌렸던 선택. 성격에 따라 매번 조금씩 흔들린다 — 1위가 늘 이기지는 않는다.</p>`;
      }
    } else if (tab === 'self') {
      body += `<div class="sec">성격</div>` + Object.entries(d.tr).map(([k, v]) => `<div class="stat"><span>${d.traitNames[k]}</span>${bar(v, '#9775fa')}<b>${v}</b></div>`).join('');
      body += `<div class="sec">능력</div>` + Object.entries(d.ab).map(([k, v]) => `<div class="stat"><span>${d.abilityNames[k]}</span>${bar(v, '#ff922b')}<b>${v}</b></div>`).join('');
      body += `<div class="stat"><span>야망</span>${bar(d.amb, '#ffd43b')}<b>${d.amb}</b></div>`;
      body += `<div class="sec">기술 (경험으로 자란다)</div>` + Object.entries(d.sk).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<div class="stat"><span>${d.skillNames[k]}</span>${bar(v, '#69db7c')}<b>${Math.round(v)}</b></div>`).join('');
      body += `<div class="sec">상태</div>
        <div class="stat"><span>건강</span>${bar(d.health, '#69db7c')}<b>${d.health}</b></div>
        <div class="stat"><span>자신감</span>${bar(d.conf, '#58b4ff')}<b>${d.conf}</b></div>
        <div class="stat"><span>교육</span>${bar(d.edu, '#74c0fc')}<b>${d.edu}</b></div>
        <div class="stat"><span>평판</span>${bar(50 + d.rep / 2, '#ffa8a8')}<b>${d.rep}</b></div>
        <div class="stat"><span>명성</span>${bar(d.fame, '#ffd43b')}<b>${d.fame}</b></div>
        <div class="card" style="margin-top:10px"><div class="row"><span>재산</span><b>${d.money.toLocaleString()}</b></div>
        <div class="row"><span>집</span><b>${d.home ? esc(d.home.name) + (d.home.done ? '' : ' (짓는 중)') : '없음'}</b></div>
        ${d.job ? `<div class="row"><span>일</span><b>${esc(d.job.title)}</b></div><div class="row"><span>만족도</span><b>${d.job.sat}</b></div>` : ''}
        ${d.techMade ? `<div class="row"><span>기술 기여</span><b>${d.techMade.toLocaleString()}</b></div>` : ''}
        ${d.discoveries.length ? `<div class="tags">${d.discoveries.map(x => `<span>💡${esc(x)}</span>`).join('')}</div>` : ''}</div>
        ${d.career.length ? `<div class="sec">경력</div>${d.career.slice().reverse().map(c => `<div class="life"><span>💼</span><div>${esc(c.text)}<time>${fmtDate(c.t)}</time></div></div>`).join('')}` : ''}`;
    } else if (tab === 'rels') {
      const fam = d.family;
      const fl = [];
      if (fam.spouse) fl.push(['배우자', fam.spouse]);
      else if (fam.partner) fl.push(['연인', fam.partner]);
      for (const p of fam.parents) fl.push(['부모', p]);
      for (const k of fam.kids) fl.push(['자녀', k]);
      for (const s of fam.siblings) fl.push(['형제자매', s]);
      if (fl.length) body += `<div class="sec">가족</div><div class="tags">${fl.map(([k, x]) => `<span class="linklike" data-ann="${x.id}" style="cursor:pointer">${k} ${esc(x.name)}${x.alive ? '' : ' 🕯️'}</span>`).join('')}</div>`;
      if (d.preg) body += `<p class="sub">🤰 ${fmtDate(d.preg.due)}쯤 아기가 태어날 예정</p>`;
      body += `<div class="sec">마음속 사람들</div>` + (d.rels.map(r => `<div class="rel" data-ann="${r.id}">
        <div><b>${esc(r.name)}</b>${r.alive ? '' : ' 🕯️'} <span class="k ${relCls(r.k)}">${esc(r.kind)}</span></div><div class="sub small" style="margin:0">${esc(r.op)}</div>
        <div class="bars"><div>호감 ${r.a}${bar(50 + r.a / 2, r.a >= 0 ? '#69db7c' : '#ff6b6b')}</div><div>친밀 ${r.f}${bar(r.f, '#58b4ff')}</div><div>${r.r > 0 ? `설렘 ${r.r}${bar(r.r, '#ff7aa8')}` : `신뢰 ${r.t}${bar(r.t, '#ffd43b')}`}</div></div>
      </div>`).join('') || '<p class="sub">아직 아는 사람이 없다.</p>');
    } else if (tab === 'mem') {
      body += `<div class="sec">기억 (중요한 것일수록 오래 남는다)</div>` + d.mem.map(m => `<div class="mem ${m.v > 0.2 ? 'pos' : m.v < -0.2 ? 'neg' : ''} ${m.imp >= 7 ? 'big' : ''}">${m.imp >= 8 ? '⭐ ' : ''}${esc(m.text)}<time>${fmtDateTime(m.t)}</time></div>`).join('');
    } else if (tab === 'life') {
      body += `<div class="sec">인생</div>` + d.life.slice().reverse().map(l => `<div class="life"><span>${l.icon}</span><div>${esc(l.text)}<time>${fmtDate(l.t)}</time></div></div>`).join('');
    } else if (tab === 'dream') {
      body += `<div class="sec">꿈</div>` + d.goals.map(g => `<div class="goal ${g.done ? 'done' : ''}"><span>${g.done ? (g.given ? '🍂' : '✅') : '✨'}</span><div>${esc(g.text)}${g.done ? `<div class="sub small" style="margin:0">${g.given ? '내려놓음' : `이룸 · ${g.at ? fmtDate(g.at) : ''}`}</div>` : ''}</div></div>`).join('');
      const vals = Object.entries(d.val).sort((a, b) => b[1] - a[1]);
      const VN = { family: '가족', wealth: '부', knowledge: '지식', power: '권력', art: '예술', adventure: '모험', sport: '운동', community: '공동체', mastery: '장인정신' };
      body += `<div class="sec">가치관</div>` + vals.map(([k, v]) => `<div class="stat"><span>${VN[k] || k}</span>${bar(v, '#7ef0c8')}<b>${Math.round(v)}</b></div>`).join('');
      if (d.wantsMigrate) body += `<p class="sub">🧭 새로운 땅으로 떠날 생각을 하고 있다 — ${esc(d.wantsMigrate)}</p>`;
    }
    const nowBox = d.al ? `<div class="now">
        <div class="what"><span class="em">${act?.emoji || '🙂'}</span>${esc(act?.label || '쉬는 중')}${act?.target ? ` <span class="sub" style="margin:0">— <span class="linklike" data-ann="${act.target.id}">${esc(act.target.name)}</span></span>` : ''}${act?.ph === 'move' ? ' <span class="chip">이동 중</span>' : ''}</div>
        ${act?.line ? `<div class="line">💬 ${esc(act.line)}</div>` : ''}
        <div class="emo">감정: <b>${esc(d.emo?.k || '평온')}</b>${d.emo?.why ? ` <span class="sub" style="margin:0">(${esc(d.emo.why)})</span>` : ''} · 기분 ${d.mood > 0 ? '+' : ''}${d.mood} · 스트레스 ${d.stress}${d.sick > 0 ? ' · 🤒 아픔' : ''}</div>
        <div class="gauges">
          <div class="gauge">배부름${bar(nd.food, '#ffa94d')}</div><div class="gauge">기력${bar(nd.energy, '#74c0fc')}</div>
          <div class="gauge">교류${bar(nd.social, '#f783ac')}</div><div class="gauge">즐거움${bar(nd.fun, '#a9e34b')}</div>
        </div></div>` : `<div class="now"><div class="what"><span class="em">🕯️</span>${fmtDate(d.d)}, ${d.age}세로 세상을 떠났다 (${esc(d.cause || '')})</div></div>`;
    this.right.innerHTML = `<div id="ann-panel">
      <div class="ap-head">
        ${avatarHTML({ ...d, age: d.age }, color)}
        <div><h2>${esc(d.n)} <small style="font-size:13px;color:var(--muted);font-weight:400">${d.g === 'M' ? '♂' : '♀'} ${d.age}세 · ${esc(d.stage)}</small>${d.ld ? `<span class="badge">${esc(d.ld)}</span>` : ''}</h2>
          <div class="meta">${d.job ? esc(d.job.title) : d.retired ? '은퇴' : d.age < 19 ? '' : '무직'}${d.settlement ? ` · ${esc(d.country?.name)} <span class="linklike" data-town="${d.settlement.id}">${esc(d.settlement.name)}</span>` : ''}<br>
          ${d.owner ? `<span class="dot" style="background:${d.owner.color}"></span> ${esc(d.owner.name)}의 앤` : '야생의 앤'}${d.gen > 1 ? ` · ${d.gen}세대` : ''}</div></div>
        <div class="acts">${d.al ? `<button class="btn small ${follow ? 'primary' : ''}" data-act="follow">${follow ? '📍 따라가는 중' : '📍 따라가기'}</button>` : ''}<button class="btn small ghost" data-act="close-ann">닫기 ✕</button></div>
      </div>
      <div class="ap-body">${nowBox}
        <div class="inner-tabs">${[['mind', '💭 생각'], ['self', '🧬 성격·능력'], ['rels', '💞 관계'], ['mem', '📔 기억'], ['life', '📜 인생'], ['dream', '✨ 꿈']].map(([k, n]) => `<button data-itab="${k}" class="${tab === k ? 'on' : ''}">${n}</button>`).join('')}</div>
        ${body}
      </div></div>`;
    const nb = this.right.querySelector('.ap-body');
    if (nb) nb.scrollTop = scroll;
  }

  buildingTip(bid, x, y) {
    const b = this.state.buildings.get(bid);
    const tip = document.getElementById('bld-tip');
    if (!b) return tip.classList.add('hidden');
    const def = this.meta.buildings[b.type];
    const owner = b.owner && this.state.anns.get(b.owner);
    const co = b.co && this.state.companies.get(b.co);
    tip.innerHTML = `<b>${esc(b.name)}</b> <span class="sub small">${esc(def?.name || b.type)}</span><br>
      ${b.done ? '' : `🏗️ 공사 중 ${Math.round(b.p * 100)}%<br>`}${co ? `🏢 ${esc(co.name)}<br>` : ''}${owner ? `주인: ${esc(owner.n)}<br>` : ''}${b.res ? `사는 사람 ${b.res}명` : ''}`;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
    tip.classList.remove('hidden');
    clearTimeout(this.tipT);
    this.tipT = setTimeout(() => tip.classList.add('hidden'), 3500);
  }
}
