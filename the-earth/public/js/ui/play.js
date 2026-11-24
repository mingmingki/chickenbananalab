// 내 앤에게 속삭이기 · 이번 생의 도전 · 명예의 전당 (화면 쪽).
//  · '내 앤' 탭에 속삭임 카드(남은 횟수, 마음에 둔 말, 지난 결과)와 도전 카드(진행 막대, 별)를 그린다.
//  · 속삭이기를 누르면 지금 건넬 수 있는 말 목록 — 앤이 얼마나 내켜 하는지(내켜 함 · 그럭저럭 · 내키지 않음)가 함께 보인다.
//  · 명예의 전당: 플레이어끼리의 순위와 세계에서 가장 널리 퍼진 노래·이야기, 이름난 사람.
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const FEEL = { yes: ['내켜 함', 'yes'], meh: ['그럭저럭', 'meh'], no: ['내키지 않음', 'no'] };
const KIND = { song: '노래', story: '이야기', game: '놀이', dish: '음식', tool: '도구', saying: '속담', dance: '춤' };

/** 남은 시간 (게임 분 → 현실 분) */
function realLeft(gameMin, scale) {
  const sec = Math.max(0, gameMin) * 60 / Math.max(1, scale || 24);
  if (sec < 60) return '곧';
  const m = Math.round(sec / 60);
  return m < 60 ? `${m}분 뒤` : `${Math.floor(m / 60)}시간 ${m % 60 ? (m % 60) + '분 ' : ''}뒤`;
}

function dots(wh) {
  let h = '';
  for (let i = 0; i < wh.max; i++) h += `<i class="whd ${i < wh.n ? 'on' : ''}"></i>`;
  for (let i = 0; i < wh.bonus; i++) h += '<i class="whd on bonus" title="도전으로 받은 덤"></i>';
  return h;
}

/** '내 앤' 탭의 속삭임 + 도전 카드 */
export function playCardsHTML(play, { now, scale }) {
  if (!play) return '';
  const { wh, ch } = play;
  let h = '';
  if (wh) {
    const can = wh.n + wh.bonus > 0;
    const act = wh.active;
    h += `<div class="wh-card">
      <div class="wh-top"><b>💬 속삭임</b><span class="whdots" aria-label="남은 속삭임 ${wh.n + wh.bonus}개">${dots(wh)}</span>
        ${wh.next != null && wh.n < wh.max ? `<span class="sub small wh-next">다음 ${realLeft(wh.next - now, scale)}</span>` : ''}</div>
      ${act ? `<div class="wh-active">${act.icon} <span><b>마음에 둔 말</b> · ${esc(act.t)}<br><small class="sub">${act.ph === 'doing' ? '지금 하고 있어요' : '틈이 나면 해 볼 거예요'}</small></span></div>`
        : wh.last ? `<div class="wh-last v${wh.last.v > 0 ? 'p' : wh.last.v < 0 ? 'n' : '0'}"><small class="sub">지난 속삭임 · ${esc(wh.last.t2)}</small><br>${esc(wh.last.text)}</div>`
          : `<p class="sub small" style="margin:2px 0 0">앤에게 먼저 한마디 건네 보세요. 듣고 따를지는 앤의 마음이에요.</p>`}
      <button class="btn ${can && !act && !wh.why ? 'primary' : 'ghost'} wh-btn" data-act="whisper" ${act ? 'disabled' : ''}>${act ? '앤이 마음에 두고 있어요' : wh.why ? esc(wh.why) : can ? '속삭이기' : `속삭임을 다 썼어요 · ${wh.next != null ? realLeft(wh.next - now, scale) + ' 하나 더' : ''}`}</button>
    </div>`;
  }
  if (ch && ch.list?.length) {
    h += `<div class="ch-card"><div class="wh-top"><b>🎯 이번 생의 도전</b><span class="chip star">⭐ ${ch.stars}</span></div>
      ${ch.list.map((c, i) => {
        const pct = Math.round(Math.min(1, c.cur / Math.max(1, c.goal)) * 100);
        return `<div class="ch-row"><span class="ch-ic">${c.icon}</span><div class="ch-body"><div class="ch-l">${esc(c.label)} <small class="sub">별 ${c.stars}</small></div>
          <div class="bar"><i style="width:${pct}%"></i></div><small class="sub">${c.big ? `${pct}%` : `${c.cur} / ${c.goal}`} · ${esc(c.hint)}</small></div>
          ${ch.canReroll ? `<button class="btn small ghost ch-rr" data-act="ch-reroll" data-i="${i}" title="다른 도전으로 (하루 한 번)" aria-label="다른 도전으로">↻</button>` : ''}</div>`;
      }).join('')}
      <p class="sub small" style="margin:6px 0 0">이루면 별과 덤 속삭임을 받아요${ch.done ? ` · 지금까지 ${ch.done}번 이룸` : ''}</p></div>`;
  }
  return h;
}

/** 속삭이기 창 → 고른 말 { k, b } 또는 null */
export function whisperSheet(sheet, play) {
  const wh = play?.wh;
  if (!wh) return Promise.resolve(null);
  const opts = wh.opts || [];
  const people = opts.filter(o => o.b != null), solo = opts.filter(o => o.b == null);
  const row = (o, i) => `<button class="wh-opt f-${o.f}" data-m="o:${i}"><span class="ic">${o.icon}</span><span class="tx"><b>${esc(o.t)}</b><small>${esc(o.d || '')}</small></span><span class="feel ${FEEL[o.f][1]}">${FEEL[o.f][0]}</span></button>`;
  const idx = o => opts.indexOf(o);
  return new Promise(resolve => {
    let done = false;
    const c = sheet(`<h3>💬 ${esc(wh.ann?.name || '앤')}에게 속삭이기</h3>
      <p class="sub small">남은 속삭임 <b>${wh.n + wh.bonus}</b>개 · 앤이 마음속 목소리를 믿는 정도 <b>${wh.ann?.heed ?? '-'}</b>. 따르면 틈날 때 해 보고, 어떻게 됐는지 알려 줘요.</p>
      ${people.length ? `<div class="sec">사람</div><div class="wh-list">${people.map(o => row(o, idx(o))).join('')}</div>` : ''}
      ${solo.length ? `<div class="sec">하는 일</div><div class="wh-list">${solo.map(o => row(o, idx(o))).join('')}</div>` : ''}
      <div class="acts"><button class="btn ghost" data-m="close">닫기</button></div>`, (a, ctx) => {
      if (!a.startsWith('o:')) return;
      const o = opts[+a.slice(2)];
      done = true; ctx.close(); resolve(o ? { k: o.k, b: o.b } : null);
    }, { wide: false });
    c.onClose = () => { if (!done) resolve(null); };
  });
}

/** 속삭인 뒤: 앤의 대답 */
export function whisperReply(sheet, r, name) {
  const follow = r.follow;
  sheet(`<div class="wh-reply ${follow ? 'ok' : 'no'}"><div class="big">${follow ? '🙂' : '🙅'}</div>
    <p class="sub small" style="margin:0">"${esc(r.text)}"</p>
    <h3 style="margin:8px 0">${esc(name || '앤')}: “${esc(r.line)}”</h3>
    <p class="sub small">${follow ? '틈이 나면 해 보고, 어떻게 됐는지 알림으로 알려 줄게요.' : '이번엔 자기 뜻대로 하기로 했어요. 내켜 하는 말일수록 잘 따라요.'}</p></div>
    <div class="acts"><button class="btn primary" data-m="close">알겠어요</button></div>`);
}

/** 명예의 전당 */
export function fameSheet(sheet, d, { onAnn } = {}) {
  let tab = 0;
  const render = () => {
    const b = d.boards[tab];
    const rows = b.rows.length ? b.rows.map(r => `<div class="fame-row ${r.me ? 'me' : ''}"><span class="rk ${r.rank <= 3 ? 'top' : ''}">${r.rank}</span><span class="dot" style="background:${esc(r.color)}"></span>
        <span class="nm"><b>${esc(r.name)}</b>${r.ann ? ` <span class="linklike" data-m="ann:${r.ann.id}">${esc(r.ann.name)}</span>` : ''}${b.k === 'clan' && r.gen > 1 ? ` <small class="sub">${r.gen}대</small>` : ''}</span><b class="v">${r.v}${b.unit && b.unit.length <= 2 ? b.unit : ''}</b></div>`).join('')
      : '<p class="empty">아직 아무도 없어요.</p>';
    return `<h3>🏆 명예의 전당</h3>
      <div class="inner-tabs">${d.boards.map((x, i) => `<button data-m="tab:${i}" class="${i === tab ? 'on' : ''}">${esc(x.name.replace(/ \(.*\)/, ''))}</button>`).join('')}</div>
      <p class="sub small" style="margin:4px 0 8px">${esc(b.name)}${b.k === 'spread' ? ' — 내 앤과 후손이 지은 것을 아는 사람 수' : ''} · 플레이어 ${d.players}명</p>
      <div class="fame-list">${rows}</div>
      ${d.works?.length ? `<div class="sec">🎵 세상에서 가장 널리 퍼진 것</div>${d.works.map((w, i) => `<div class="fame-row ${w.mine ? 'me' : ''}"><span class="rk">${i + 1}</span><span class="nm"><b>'${esc(w.name)}'</b> <small class="sub">${KIND[w.kind] || ''} · <span class="linklike" data-m="ann:${w.by}">${esc(w.byName)}</span>${w.alive ? '' : ' 🕯️'}</small></span><b class="v">${w.n}명</b></div>`).join('')}` : ''}
      ${d.people?.length ? `<div class="sec">⭐ 지금 이름난 사람</div>${d.people.map((p, i) => `<div class="fame-row ${p.mine ? 'me' : ''}"><span class="rk">${i + 1}</span><span class="nm"><span class="linklike" data-m="ann:${p.id}"><b>${esc(p.name)}</b></span>${p.title ? ` <small class="chip">${esc(p.title)}</small>` : ''}${p.why ? ` <small class="sub">${esc(p.why)}</small>` : ''}</span><b class="v">${p.fame}</b></div>`).join('')}` : ''}
      <div class="acts"><button class="btn ghost" data-m="close">닫기</button></div>`;
  };
  const c = sheet(render(), (a, ctx) => {
    if (a.startsWith('tab:')) { tab = +a.slice(4); ctx.box.innerHTML = render(); return; }
    if (a.startsWith('ann:')) { ctx.close(); onAnn?.(+a.slice(4)); }
  });
  return c;
}

