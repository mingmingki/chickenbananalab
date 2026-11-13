// The Earth — 화면 흐름: 인트로 → 서버 → 나라 → 앤 만들기 → 관찰
import { state, fmtDate } from './state.js';
import { api, auth, Live, loadSessions, saveSession, forgetSession } from './net.js';
import { World3D } from './scene/scene.js';
import { Creator } from './ui/creator.js';
import { Hud } from './ui/hud.js';
import { sound } from './audio.js';
import { account, accountModal, settingsModal, inboxModal, recapModal, reportModal, tutorial, tutorialDone, sheet } from './ui/extras.js';
// 휴대폰 세로 화면: 아래쪽 시트에 가리지 않게 지구를 위로 올린다
const phoneLift = v => (innerWidth <= 860 && innerHeight > innerWidth ? v : 0);

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const flag = code => /^[A-Z]{2}$/.test(code) ? String.fromCodePoint(...[...code].map(c => 0x1f1e6 + c.charCodeAt(0) - 65)) : '🏳️';

let meta, geo, w3d, live = null, hud = null;
const ctx = { server: null, session: null, country: null };

function show(id) {
  for (const s of ['intro', 'servers', 'country', 'creator']) $('#' + s).classList.toggle('hidden', s !== id);
}

function modal(html, onAct) {
  const m = $('#modal');
  m.querySelector('.modal-box').innerHTML = html;
  m.classList.remove('hidden');
  return new Promise(resolve => {
    m.onclick = e => {
      const a = e.target.closest('[data-m]')?.dataset.m;
      if (!a && e.target !== m) return;
      const val = onAct ? onAct(a || 'cancel', m) : a;
      if (val === false) return;
      m.classList.add('hidden');
      resolve(a || 'cancel');
    };
  });
}

function toast(text) {
  const t = $('#toast');
  t.textContent = text;
  t.classList.remove('hidden');
  $('#hud').classList.remove('hidden');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.classList.add('hidden'); if (!hud) $('#hud').classList.add('hidden'); }, 3000);
}

async function boot() {
  $('#loading').classList.remove('hidden');
  // 앱처럼: 서비스 워커(빠른 재방문, 휴대폰 알림)
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  [meta, geo] = await Promise.all([api('/api/meta'), api('/api/geo')]);
  w3d = new World3D($('#scene'), meta, geo.countries);
  w3d.setState(state);
  $('#loading').classList.add('hidden');
  refreshAccount();
  // 소리: 장면이 알려 주는 상태(고도·날씨·밤·시대)를 넘겨준다
  setInterval(() => {
    const se = w3d.sense;
    if (!se) return;
    se.era = se.s ? state.countries.get(se.s.country)?.era ?? 0 : 0;
    sound.feed(se);
  }, 200);
  // 버튼 누르는 소리
  addEventListener('click', e => { if (e.target.closest('.btn, .tabs button, .seg button')) sound.sfx('click'); }, true);
  intro();
}

// ── 계정 ──
async function refreshAccount() {
  if (auth.get()?.token) {
    try { const r = await api('/api/auth/me'); auth.set({ ...auth.get(), account: r.account }); ctx.games = r.games; if (r.account.prefs?.sound) sound.set(r.account.prefs.sound); }
    catch { ctx.games = []; }
  } else ctx.games = [];
  const acc = account();
  $('#intro-account').textContent = acc ? `👤 ${acc.username}` : '로그인';
}
const onAccount = () => { refreshAccount(); };
const openSettings = () => settingsModal({ toast: msg => (hud ? hud.toast(msg) : toast(msg)), onAccount, onTutorial: hud ? () => tutorial({ hasMine: !!ctx.session }) : null });
/** 이어할 수 있는 게임: 이 기기에 저장된 것 + 계정에 묶인 것 */
function allSessions() {
  const list = loadSessions();
  for (const g of ctx.games || []) if (!list.some(s => s.serverId === g.serverId && s.token === g.token)) list.push({ ...g, seenAcc: g.seen });
  return list;
}
// '그동안 있었던 일'을 위해 마지막으로 본 게임 시각을 기억한다
const seenKey = s => `theEarth.seen.${s.serverId}.${s.token}`;
const getSeen = s => { let v = 0; try { v = +localStorage.getItem(seenKey(s)) || 0; } catch { /* */ } return Math.max(v, s.seenAcc || 0, (ctx.games || []).find(g => g.token === s.token)?.seen || 0); };
function markSeen() {
  const s = ctx.session;
  if (!s || !state.world) return;
  const clock = Math.floor(state.now());
  try { localStorage.setItem(seenKey(s), String(clock)); } catch { /* */ }
  if (account()) api(`/api/servers/${s.serverId}/seen`, { token: s.token, clock }).catch(() => null);
}
setInterval(() => { if (ctx.mode === 'observe') markSeen(); }, 60_000);
document.addEventListener('visibilitychange', () => { if (document.hidden && ctx.mode === 'observe') markSeen(); });

// ── 인트로 ──
function intro() {
  show('intro');
  const title = $('.intro-title');
  title.classList.remove('show');
  $('.skip-hint').style.opacity = '1';
  let done = false;
  const landed = () => {
    if (done) return; done = true;
    title.classList.add('show');
    $('.skip-hint').style.opacity = '0';
    w3d.menuSpin(true);
  };
  w3d.playIntro(landed);
  $('#intro').onclick = e => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'account') { account() ? openSettings() : accountModal({ toast, onChange: onAccount }); return; }
    if (act === 'settings') { openSettings(); return; }
    if (e.target.closest('.intro-foot')) return;
    if (!done) { w3d.introSkip?.(); return; }
    if (act === 'start') showServers();
    if (act === 'continue') continueGame();
  };
}

async function continueGame() {
  if (account()) await refreshAccount();
  const sessions = allSessions();
  if (!sessions.length) return toast(account() ? '아직 게임이 없습니다. 시작하기를 눌러 주세요.' : '저장된 게임이 없습니다. 시작하기를 누르거나, 다른 기기에서 하던 게임은 로그인해서 이어하세요.');
  let pick = sessions[0];
  if (sessions.length > 1) {
    const servers = await api('/api/servers').catch(() => []);
    const name = id => servers.find(s => s.id === id)?.name || id;
    let chosen = null;
    await modal(`<h3>이어하기</h3><p class="sub">어느 게임을 이어할까요?</p>
      <div style="display:grid;gap:8px;margin-top:12px">${sessions.map((s, i) => `<button class="btn" data-m="s${i}">${esc(s.playerName)} — ${esc(name(s.serverId))} · ${esc(s.countryName || s.country)}</button>`).join('')}</div>
      <div class="acts"><button class="btn ghost" data-m="cancel">취소</button></div>`, a => { if (a.startsWith('s')) chosen = sessions[+a.slice(1)]; });
    if (!chosen) return;
    pick = chosen;
  }
  try {
    const me = await api(`/api/servers/${pick.serverId}/me?token=${encodeURIComponent(pick.token)}`);
    const servers = await api('/api/servers');
    ctx.server = servers.find(s => s.id === pick.serverId);
    ctx.session = { ...pick, player: me.player, room: me.room };
    if (!me.anns.length) return openCreator();
    observe(false, null, getSeen(pick));
  } catch (e) {
    forgetSession(pick.serverId, pick.token);
    toast('저장된 게임을 찾을 수 없습니다: ' + e.message);
  }
}

// ── 서버 ──
async function showServers() {
  show('servers');
  const list = $('#server-list');
  list.innerHTML = '<p class="empty">서버 목록을 불러오는 중…</p>';
  const servers = await api('/api/servers');
  const sessions = allSessions();
  $('#btn-new-server').classList.toggle('hidden', !(meta.openServers || account()?.admin));
  list.innerHTML = servers.map(s => {
    const mine = sessions.find(x => x.serverId === s.id);
    return `<div class="server-card">
      <h3>${esc(s.name)}</h3>
      <div class="meta"><span class="date">📅 ${esc(s.date)}</span><span class="chip">👥 플레이어 ${s.players}</span><span class="chip">🧑 인구 ${s.population}</span><span class="chip">🌍 나라 ${s.countries}</span><span class="chip">⏱ ×${s.timeScale}</span></div>
      <div class="acts">
        ${mine ? `<button class="btn primary" data-continue="${s.id}">이어하기</button>` : ''}
        <button class="btn ${mine ? '' : 'primary'}" data-join="${s.id}">새로 시작</button>
        <button class="btn ghost small" data-watch="${s.id}">👁️ 관전</button>
      </div>
      <p class="sub small" style="grid-column:1">${new Date(s.created).toLocaleDateString('ko-KR')} 개설 — 1년 1월 1일부터 시작된 세계${s.paused ? ' · ⏸ 점검 중' : ''}</p>
    </div>`;
  }).join('') || '<p class="empty">서버가 없습니다.</p>';
  $('#servers').onclick = async e => {
    const t = e.target;
    if (t.closest('[data-act=back]')) { show('intro'); return; }
    if (t.closest('[data-act=new-server]')) return newServer();
    const j = t.closest('[data-join]'), w = t.closest('[data-watch]'), c = t.closest('[data-continue]');
    if (j) { ctx.server = servers.find(s => s.id === j.dataset.join); pickCountry(); }
    if (w) { ctx.server = servers.find(s => s.id === w.dataset.watch); ctx.session = null; observe(); }
    if (c) {
      const sess = sessions.find(x => x.serverId === c.dataset.continue);
      try {
        const me = await api(`/api/servers/${sess.serverId}/me?token=${encodeURIComponent(sess.token)}`);
        ctx.server = servers.find(s => s.id === sess.serverId);
        ctx.session = { ...sess, player: me.player, room: me.room };
        me.anns.length ? observe(false, null, getSeen(sess)) : openCreator();
      } catch (err) { forgetSession(sess.serverId, sess.token); toast(err.message); showServers(); }
    }
  };
}

async function newServer() {
  let name = '';
  const res = await modal(`<h3>새 서버 열기</h3>
    <p class="sub">새 서버는 지금 이 순간 <b>1년 1월 1일 0시</b>부터 시작합니다. 아무도 없는 지구에서 첫 문명이 시작됩니다.</p>
    <label class="field" style="margin-top:12px"><span>서버 이름</span><input id="srv-name" maxlength="30" placeholder="예: 제2서버 · 새벽"></label>
    <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn primary" data-m="ok">서버 열기</button></div>`,
  (a, m) => { if (a === 'ok') name = m.querySelector('#srv-name').value; });
  if (res !== 'ok') return;
  try { await api('/api/servers', { name }); toast('새 서버가 열렸습니다 — 1년 1월 1일'); } catch (e) { toast(e.message); }
  showServers();
}

// ── 나라 선택 ──
function connect(serverId) {
  if (live && live.serverId === serverId) return live;
  live?.close();
  state.reset();
  live = new Live(serverId, {
    notes: m => { hud?.onNotes(m.notes); const top = m.notes.slice().sort((a, b) => b.lvl - a.lvl)[0]; if (top) sound.forIcon(top.icon); },
    init: m => state.applyInit(m),
    tick: m => {
      state.applyTick(m);
      hud?.onTick();
      if (ctx.mode === 'country') refreshCountryInfo();
      for (const e of m.events || []) if (e.kind === 'space' && e.sid) { w3d.launchRocket(e.sid); sound.sfx('rocket'); }
    },
    ann: m => hud?.onAnnDetail(m.ann),
  }, ctx.session?.serverId === serverId ? ctx.session.token : null);
  return live;
}

let selectedIdx = -1;
function pickCountry() {
  show('country');
  ctx.mode = 'country';
  connect(ctx.server.id);
  w3d.menuSpin(false);
  w3d.setLift(phoneLift(0.25));
  w3d.mode = 'pick-country';
  w3d.rig.enabled = true;
  w3d.rig.flyTo({ alt: 15000, tilt: 90 });
  selectedIdx = -1;
  w3d.setSelectedCountry(-1);
  $('#country-card').classList.add('hidden');
  $('#country-confirm').disabled = true;
  const nameIn = $('#player-name');
  nameIn.value = nameIn.value || localStorage.getItem('theEarth.name') || '';
  const search = $('#country-search');
  search.value = '';
  const renderList = () => {
    const q = search.value.trim();
    const items = geo.countries.map((c, i) => ({ c, i })).filter(({ c }) => !q || c.name.includes(q) || c.nameEn.toLowerCase().includes(q.toLowerCase()));
    const active = new Set([...state.countries.keys()]);
    items.sort((a, b) => (active.has(b.c.code) - active.has(a.c.code)) || a.c.name.localeCompare(b.c.name, 'ko'));
    $('#country-results').innerHTML = items.slice(0, 120).map(({ c, i }) => {
      const st = state.countries.get(c.code);
      return `<button data-ci="${i}" class="${i === selectedIdx ? 'on' : ''}"><span>${flag(c.code)} ${esc(c.name)}</span><small>${st ? `${esc(st.eraName)} · ${st.pop}명` : ''}</small></button>`;
    }).join('');
  };
  renderList();
  search.oninput = renderList;
  state.on('init', () => { if (ctx.mode === 'country') renderList(); });
  $('#country-results').onclick = e => { const b = e.target.closest('[data-ci]'); if (b) selectCountry(+b.dataset.ci, true); };
  w3d.onCountryPick = idx => selectCountry(idx, false);
  w3d.onCountryHover = (idx, x, y) => {
    const tip = $('#country-hover');
    if (idx < 0) { tip.classList.add('hidden'); return; }
    const c = geo.countries[idx];
    const st = state.countries.get(c.code);
    tip.innerHTML = `${flag(c.code)} <b>${esc(c.name)}</b>${st ? ` · ${esc(st.eraName)} · ${st.pop}명` : ''}`;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
    tip.classList.remove('hidden');
  };
  $('#country').onclick = async e => {
    if (e.target.closest('[data-act=back]')) { ctx.mode = null; w3d.mode = 'menu'; w3d.menuSpin(true); $('#country-hover').classList.add('hidden'); showServers(); }
    if (e.target.closest('#country-confirm')) joinCountry();
  };
}

function selectCountry(idx, fly) {
  selectedIdx = idx;
  const c = geo.countries[idx];
  w3d.setSelectedCountry(idx);
  if (fly || true) w3d.flyToCountry(c, Math.max(2500, Math.min(9000, Math.sqrt(c.rings.reduce((s, r) => s + r.length, 0)) * 120)));
  $('#country-confirm').disabled = false;
  refreshCountryInfo();
  document.querySelectorAll('#country-results button').forEach(b => b.classList.toggle('on', +b.dataset.ci === idx));
}

function refreshCountryInfo() {
  if (selectedIdx < 0) return;
  const c = geo.countries[selectedIdx];
  const st = state.countries.get(c.code);
  const players = [...state.players.values()].filter(p => p.country === c.code);
  const card = $('#country-card');
  card.classList.remove('hidden');
  card.innerHTML = `<h3>${flag(c.code)} ${esc(c.name)}</h3>
    <p class="sub" style="margin:0">${st ? `${esc(st.eraName)} · 인구 ${st.pop}명 · 플레이어 ${players.length}명${st.leader ? `<br>${esc(st.leader.title)}: ${esc(st.leader.name)}` : ''}` : '아직 아무도 살지 않는 땅. 당신의 앤들이 첫 문명을 시작합니다.'}</p>`;
}

async function joinCountry() {
  const name = $('#player-name').value.trim();
  if (!name) { $('#player-name').focus(); toast('플레이어 이름을 입력해 주세요'); return; }
  const c = geo.countries[selectedIdx];
  try { localStorage.setItem('theEarth.name', name); } catch { /* */ }
  try {
    const r = await api(`/api/servers/${ctx.server.id}/join`, { name, country: c.code });
    ctx.session = { serverId: ctx.server.id, token: r.token, playerName: name, country: c.code, countryName: c.name, player: r.player, room: meta.maxAnns };
    live?.hello(r.token);
    saveSession({ serverId: ctx.server.id, token: r.token, playerName: name, country: c.code, countryName: c.name });
    $('#country-hover').classList.add('hidden');
    openCreator();
  } catch (e) { toast(e.message); }
}

// ── 앤 만들기 ──
let creator = null;
async function openCreator() {
  ctx.mode = 'creator';
  show('creator');
  hud?.destroy(); hud = null;
  const country = geo.countries.find(c => c.code === ctx.session.country);
  w3d.mode = 'pick-country';
  w3d.setSelectedCountry(geo.countries.indexOf(country));
  w3d.flyToCountry(country, 4500);
  const info = await api(`/api/servers/${ctx.session.serverId}/country/${country.code}`).catch(() => ({ mode: 'pioneer', families: 0 }));
  creator = new Creator($('#creator'), { culture: country.culture, countryName: country.name, room: ctx.session.room ?? meta.maxAnns, max: meta.maxAnns, mode: info.mode, families: info.families });
  await creator.open();
  $('#creator').onclick = async e => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'back') { creator = null; hud ? null : pickCountry(); }
    if (act === 'add-ann' && creator.anns.length < creator.opts.room) { creator.anns.push(await creator.randomAnn()); creator.cur = creator.anns.length - 1; creator.render(); }
    if (act === 'random-all') { for (let i = 0; i < creator.anns.length; i++) creator.anns[i] = await creator.randomAnn(creator.anns[i].gender); creator.render(); }
    if (act === 'deploy') deploy();
  };
}

async function deploy() {
  if (!creator.anns.length) return toast('앤을 한 명 이상 만들어 주세요');
  const ok = await modal(`<h3>🌱 앤 ${creator.anns.length}명을 투입할까요?</h3>
    <p class="sub">투입한 순간부터 앤들은 스스로 생각하고 선택합니다. <b>더 이상 조종할 수 없습니다.</b><br>누구를 만나고, 어떤 일을 하고, 누구와 사랑에 빠질지 — 지켜보기만 하세요.</p>
    <div class="acts"><button class="btn ghost" data-m="cancel">조금 더 고민할래요</button><button class="btn primary" data-m="ok">투입하기</button></div>`);
  if (ok !== 'ok') return;
  try {
    const specs = creator.anns.map(a => ({ name: a.name, gender: a.gender, age: a.age, traits: a.traits, abilities: a.abilities, ambition: a.ambition }));
    const r = await api(`/api/servers/${ctx.session.serverId}/deploy`, { token: ctx.session.token, anns: specs });
    sound.sfx('deploy');
    creator = null;
    observe(true, r.anns?.[0]?.id);
  } catch (e) { toast(e.message); }
}

// ── 관찰 ──
function observe(justDeployed = false, firstAnn = null, since = null) {
  ctx.mode = 'observe';
  show(null);
  connect(ctx.server.id);
  if (ctx.session) live.hello(ctx.session.token);
  w3d.menuSpin(false);
  w3d.mode = 'observe';
  w3d.rig.enabled = true;
  w3d.setSelectedCountry(-1);
  hud?.destroy();
  const me = ctx.session ? { id: ctx.session.player?.id, name: ctx.session.playerName, country: ctx.session.country } : null;
  w3d.setMyCountry(me?.country);
  hud = new Hud({
    state, w3d, live, me, meta,
    onDeployMore: async () => {
      const r = await api(`/api/servers/${ctx.session.serverId}/me?token=${encodeURIComponent(ctx.session.token)}`).catch(() => null);
      if (!r || r.room <= 0) return hud.toast(`더 투입할 수 없습니다 (최대 ${meta.maxAnns}명)`);
      ctx.session.room = r.room;
      openCreator();
    },
    onExit: async () => {
      const r = await modal(`<h3>처음 화면으로</h3><p class="sub">앤들은 당신이 없어도 계속 살아갑니다. 이어하기로 언제든 돌아올 수 있어요.</p><div class="acts"><button class="btn ghost" data-m="cancel">계속 보기</button><button class="btn primary" data-m="ok">처음으로</button></div>`);
      if (r !== 'ok') return;
      markSeen();
      hud.destroy(); hud = null; live?.close(); live = null; state.reset(); w3d.labels.clear(); ctx.session = null;
      w3d.mode = 'menu'; w3d.rig.flyTo({ alt: 16000, tilt: 90 }); w3d.menuSpin(true);
      show('intro'); $('.intro-title').classList.add('show');
    },
  });
  Object.assign(hud, {
    onSettings: openSettings,
    onInbox: async () => {
      const r = await api(`/api/servers/${ctx.session.serverId}/inbox?token=${encodeURIComponent(ctx.session.token)}`).catch(e => { hud.toast(e.message); return null; });
      if (r) inboxModal({ notes: r.notes, ach: r.ach, meta, onAnn: id => hud.select(id, true) });
    },
    onReport: d => reportModal({ server: ctx.server.id, ann: d, toast: msg => hud.toast(msg) }),
  });
  w3d.onAnnPick = id => hud.select(id, false);
  w3d.onBuildingPick = (bid, x, y) => hud.buildingTip(bid, x, y);
  w3d.onTownClick = sid => hud.flyTown(sid);
  w3d.onEmptyClick = () => {};
  w3d.rig.onUserMove = () => { if (hud?.detail) hud.renderAnn(); };
  const go = () => {
    if (me) {
      const c = state.countries.get(me.country);
      const s = c && (state.settlements.get(c.capital) || [...state.settlements.values()].find(x => x.country === me.country));
      if (s) w3d.rig.flyTo({ lat: s.lat, lon: s.lon, alt: justDeployed ? 0.9 : 2.2 });
      if (justDeployed) {
        const a = firstAnn && state.anns.get(firstAnn);
        hud.toast(a && a.gen > 1 ? '앤이 한 가정에서 태어났습니다. 연대기에서 그 삶을 지켜보세요.' : '앤들이 자연 속에 내려섰습니다. 이제 지켜보세요.');
        if (firstAnn) setTimeout(() => hud.select(firstAnn, true), 1200);
        if (!tutorialDone()) setTimeout(() => { if (ctx.mode === 'observe') tutorial({ hasMine: true }); }, 2600);
      } else if (since != null) {
        // 다시 들어왔을 때: 그동안 있었던 일 (게임 속 하루 이상 지났을 때)
        api(`/api/servers/${ctx.session.serverId}/recap?token=${encodeURIComponent(ctx.session.token)}&since=${since}`).then(r => {
          if (ctx.mode !== 'observe') return;
          if (r.days >= 1 && since > 0) recapModal(r, { scale: state.scale, onAnn: id => hud.select(id, true) });
          else if (!tutorialDone()) setTimeout(() => tutorial({ hasMine: true }), 1500);
        }).catch(() => null);
      }
      if (ctx.session) {
        api(`/api/servers/${ctx.session.serverId}/inbox?token=${encodeURIComponent(ctx.session.token)}`).then(r => {
          const seen = since ?? getSeen(ctx.session);
          hud?.setUnread(r.notes.filter(n => n.t > seen).length);
        }).catch(() => null);
      }
    } else {
      const s = [...state.settlements.values()].sort((a, b) => b.pop - a.pop)[0];
      if (s) w3d.rig.flyTo({ lat: s.lat, lon: s.lon, alt: 3 });
    }
  };
  if (state.world) go(); else state.on('init', function once() { if (ctx.mode === 'observe') go(); });
}

boot().catch(e => { console.error(e); $('#loading').innerHTML = `<p>불러오기 실패: ${esc(e.message)}</p>`; });
window.__earth = { state, get w3d() { return w3d; }, ctx };
