// 계정(로그인·가입), 설정(소리·알림·계정), 알림함과 업적, '그동안 있었던 일', 신고, 처음 하는 사람을 위한 안내.
import { api, auth, loadSessions } from '../net.js';
import { sound } from '../audio.js';
import { fmtDate } from '../state.js';
import { TOUCH } from '../device.js';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** 모달 하나 띄우기: data-m 버튼을 누르면 onAct(a, ctx). 배경을 누르면 닫힌다 */
let sheetSeq = 0;
export function sheet(html, onAct = () => {}, { wide = false, sticky = false } = {}) {
  const m = $('#modal'), box = m.querySelector('.modal-box');
  const mine = ++sheetSeq; // 창은 하나뿐 — 나중에 열린 창을 예전 창이 닫지 않게
  box.innerHTML = html;
  box.classList.toggle('wide', wide);
  m.classList.remove('hidden');
  box.scrollTop = 0;
  let closed = false;
  const ctx = {
    box,
    close() {
      if (closed) return;
      closed = true;
      if (sheetSeq === mine) { m.classList.add('hidden'); m.onclick = null; box.oninput = null; box.onchange = null; box.classList.remove('wide'); }
      ctx.onClose?.();
    },
    err(msg) { const e = box.querySelector('.form-err'); if (e) { e.textContent = msg || ''; e.classList.toggle('hidden', !msg); } },
  };
  m.onclick = e => {
    if (e.target === m) { if (!sticky) ctx.close(); return; }
    const el = e.target.closest('[data-m]');
    if (!el) return;
    const a = el.dataset.m;
    if (a === 'close' || a === 'cancel') { ctx.close(); return; }
    onAct(a, ctx, el);
  };
  return ctx;
}

// ── 계정 ──
export const account = () => auth.get()?.account || null;

/** 로그인 성공 뒤 공통: 토큰 저장, 이 기기의 게임을 계정에 붙이기, 소리 설정 가져오기 */
async function finishLogin(r, { toast, onChange, isNew = false } = {}) {
  auth.set({ token: r.token, account: r.account });
  const local = loadSessions();
  if (local.length) await api('/api/auth/claim', { games: local.map(s => ({ serverId: s.serverId, token: s.token })) }).catch(() => null);
  if (r.account.prefs?.sound) sound.set(r.account.prefs.sound);
  toast?.(isNew ? `가입을 환영합니다, ${r.account.username}님` : `${r.account.username}님, 다시 오셨네요`);
  onChange?.(r.account);
  return r.account;
}

const G_LOGO = '<svg class="g-logo" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.2C12.5 13.6 17.8 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.8c4.3-4 6.9-9.9 6.9-17.2z"/><path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.2C1 16.6 0 20.2 0 24s1 7.4 2.7 10.8l7.9-6.2z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.8c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.2C6.6 42.6 14.6 48 24 48z"/></svg>';

export function accountModal({ toast, onChange, mode = 'login', google = false } = {}) {
  const render = () => `<h3>${mode === 'login' ? '로그인' : '가입하기'}</h3>
    ${google ? `<button class="btn google-btn" data-m="google">${G_LOGO}<span>Google로 계속하기</span></button><div class="or"><span>또는 아이디로</span></div>` : ''}
    <div class="seg"><button class="${mode === 'login' ? 'on' : ''}" data-m="mode-login">로그인</button><button class="${mode === 'signup' ? 'on' : ''}" data-m="mode-signup">가입하기</button></div>
    <p class="sub small">로그인하면 <b>어느 기기에서든 이어할 수 있고</b>, 앱을 꺼도 <b>휴대폰 알림</b>을 받을 수 있어요. 로그인하지 않아도 플레이할 수 있습니다 (이 기기에만 저장).</p>
    <label class="field"><span>아이디</span><input id="acc-user" maxlength="16" autocomplete="username" autocapitalize="off" placeholder="한글·영문·숫자 2~16자"></label>
    <label class="field"><span>비밀번호</span><input id="acc-pass" type="password" maxlength="72" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" placeholder="8자 이상"></label>
    ${mode === 'signup' ? `<label class="field"><span>비밀번호 확인</span><input id="acc-pass2" type="password" maxlength="72" autocomplete="new-password"></label>
    <label class="check"><input id="acc-agree" type="checkbox"> 만 14세 이상이며 <a href="/terms" target="_blank">이용약관</a>과 <a href="/privacy" target="_blank">개인정보 처리방침</a>에 동의합니다</label>` : ''}
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn ghost" data-m="cancel">닫기</button><button class="btn primary" data-m="go">${mode === 'login' ? '로그인' : '가입하기'}</button></div>`;
  return new Promise(resolve => {
    let busy = false;
    const ctx = sheet(render(), async (a, c) => {
      if (a.startsWith('mode-')) { mode = a.slice(5); c.box.innerHTML = render(); return; }
      if (a === 'google') { c.close(); googleLogin({ toast, onChange }).catch(e => toast?.(e.message)); return; }
      if (a !== 'go' || busy) return;
      const username = c.box.querySelector('#acc-user').value.trim(), password = c.box.querySelector('#acc-pass').value;
      if (mode === 'signup') {
        if (password !== c.box.querySelector('#acc-pass2').value) return c.err('비밀번호 확인이 다릅니다');
        if (!c.box.querySelector('#acc-agree').checked) return c.err('약관과 개인정보 처리방침에 동의해 주세요');
      }
      busy = true;
      try {
        const r = await api(`/api/auth/${mode}`, { username, password, agree: true });
        c.close();
        resolve(await finishLogin(r, { toast, onChange, isNew: mode === 'signup' }));
      } catch (e) { c.err(e.message); } finally { busy = false; }
    });
    ctx.onClose = () => resolve(account());
    setTimeout(() => ctx.box.querySelector('#acc-user')?.focus(), 50);
  });
}

// ── 구글 로그인 ──
// 같은 브라우저로 돌아오면 자동, 아이폰 홈 화면 앱처럼 Safari로 빠지면 6자리 확인 코드를 입력받는다.
const G_KEY = 'theEarth.glogin';
const isStandalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const gPending = () => { try { const v = JSON.parse(localStorage.getItem(G_KEY) || 'null'); return v && Date.now() - v.at < 15 * 60_000 ? v : null; } catch { return null; } };
const gClear = () => { try { localStorage.removeItem(G_KEY); } catch { /* */ } };

export async function googleLogin({ toast, onChange, link = false } = {}) {
  const r = await api('/api/auth/google/begin', { link });
  const popup = isStandalone();
  try { localStorage.setItem(G_KEY, JSON.stringify({ id: r.id, at: Date.now(), link, popup })); localStorage.removeItem(G_KEY + '.code'); } catch { /* */ }
  if (popup) {
    // 홈 화면 앱: 바깥 브라우저에서 로그인하고, 이 앱은 그대로 기다린다
    window.open(r.url, '_blank');
    return googleWait({ toast, onChange });
  }
  location.href = r.url;
}

/** 돌아왔을 때 (주소 끝이 #glogin) 또는 홈 화면 앱에서 기다릴 때: 결과를 가져온다 */
async function googlePoll(code = null) {
  const p = gPending();
  if (!p) return { status: 'gone', error: '로그인 요청을 찾을 수 없습니다. 다시 시도해 주세요.' };
  return api('/api/auth/google/poll', { id: p.id, code });
}
const finished = r => r.status === 'done' || r.status === 'error' || r.status === 'gone';
async function googleHandle(r, { toast, onChange }) {
  if (r.status === 'done') {
    gClear();
    if (r.linked) { toast?.('구글 계정을 연결했습니다. 이제 구글로도 로그인할 수 있어요.'); onChange?.(account()); return 'done'; }
    if (r.signup) { googleSignupModal(r.signup, { toast, onChange }); return 'done'; }
    await finishLogin(r, { toast, onChange });
    return 'done';
  }
  if (r.status === 'error' || r.status === 'gone') { gClear(); toast?.(r.error || '로그인 시간이 지났습니다. 다시 시도해 주세요.'); return 'error'; }
  return r.status; // wait | code
}

/** 기다리는 창: 자동으로 확인하고, 다른 브라우저에 확인 코드가 나왔으면 입력받는다 */
function googleWait({ toast, onChange, needCode = false, wrong = false } = {}) {
  return new Promise(resolve => {
    let timer = null, done = false;
    const ctx = sheet(`<h3>${G_LOGO} 구글 로그인</h3>
      <p class="sub">${needCode ? '구글 로그인 창에 나온 <b>6자리 확인 코드</b>를 입력해 주세요.' : '열린 창에서 구글 로그인을 마치고 이 화면으로 돌아오세요. 자동으로 이어집니다.<br>확인 코드가 나왔다면 아래에 입력하세요.'}</p>
      <label class="field"><span>확인 코드</span><input id="g-code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="123 456"></label>
      <p class="form-err ${wrong ? '' : 'hidden'}">${wrong ? '코드가 맞지 않습니다' : ''}</p>
      <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn primary" data-m="go">확인</button></div>`, async (a, c) => {
      if (a !== 'go') return;
      const code = c.box.querySelector('#g-code').value.replace(/\D/g, '');
      if (code.length !== 6) return c.err('6자리 숫자를 입력해 주세요');
      if (inFlight || done) return;
      inFlight = true;
      try {
        const r = await googlePoll(code);
        if (done) return;
        if (!finished(r)) return c.err(r.status === 'wait' ? '아직 구글 로그인이 끝나지 않았어요' : '코드가 맞지 않습니다');
        done = true; c.close();
        await googleHandle(r, { toast, onChange });
      } catch (e) { c.err(e.message); } finally { inFlight = false; }
    });
    let inFlight = false;
    const tick = async () => {
      if (done || inFlight || document.hidden) return;
      inFlight = true;
      try { await tickOnce(); } finally { inFlight = false; }
    };
    const tickOnce = async () => {
      // 로그인 창이 저장소를 함께 쓰면 확인 코드를 남겨 둔다 → 대신 입력
      let handed = null;
      try { handed = localStorage.getItem(G_KEY + '.code'); if (handed) localStorage.removeItem(G_KEY + '.code'); } catch { /* */ }
      try {
        const r = await googlePoll(handed);
        if (done || !finished(r)) return;
        done = true; ctx.close();
        await googleHandle(r, { toast, onChange });
      } catch { /* 잠깐 끊김 */ }
    };
    timer = setInterval(tick, 2500);
    addEventListener('storage', tick);
    const vis = () => { if (!document.hidden) tick(); };
    document.addEventListener('visibilitychange', vis);
    ctx.onClose = () => { done = true; clearInterval(timer); removeEventListener('storage', tick); document.removeEventListener('visibilitychange', vis); resolve(account()); };
  });
}
/** 주소 끝 #glogin(=코드) 처리. main.js 가 시작할 때 부른다 */
export async function googleReturn({ toast, onChange }) {
  const m = location.hash.match(/^#glogin(?:=(\d{6}))?$/);
  if (!m) return;
  history.replaceState(null, '', location.pathname + location.search);
  try {
    const r = await googlePoll(m[1] || null);
    if (finished(r)) await googleHandle(r, { toast, onChange });
    else googleWait({ toast, onChange, needCode: r.status === 'code' });
  } catch (e) { toast?.(e.message); }
}

/** 처음 구글로 온 사람: 아이디 정하기 + 약관 동의 */
function googleSignupModal(ticket, { toast, onChange }) {
  sheet(`<h3>${G_LOGO} 처음 오셨네요!</h3>
    <p class="sub">게임에서 쓸 아이디를 정해 주세요. 구글에서는 계정 번호만 받고, 이메일·이름은 받지 않습니다.</p>
    <label class="field"><span>아이디</span><input id="gs-user" maxlength="16" autocapitalize="off" placeholder="한글·영문·숫자 2~16자"></label>
    <label class="check"><input id="gs-agree" type="checkbox"> 만 14세 이상이며 <a href="/terms" target="_blank">이용약관</a>과 <a href="/privacy" target="_blank">개인정보 처리방침</a>에 동의합니다</label>
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn primary" data-m="go">가입하기</button></div>`, async (a, c) => {
    if (a !== 'go') return;
    if (!c.box.querySelector('#gs-agree').checked) return c.err('약관과 개인정보 처리방침에 동의해 주세요');
    try {
      const r = await api('/api/auth/google/signup', { ticket, username: c.box.querySelector('#gs-user').value.trim(), agree: true });
      c.close();
      await finishLogin(r, { toast, onChange, isNew: true });
    } catch (e) { c.err(e.message); }
  }, { sticky: true });
  setTimeout(() => document.querySelector('#gs-user')?.focus(), 50);
}

export async function logout() {
  await api('/api/auth/logout', {}).catch(() => null);
  auth.set(null);
}

// ── 휴대폰 알림 (웹 푸시) ──
const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const b64ToBytes = s => { const p = '='.repeat((4 - s.length % 4) % 4), b = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(b, c => c.charCodeAt(0)); };
export async function pushState() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    return { ok: false, why: isIOS() && !standalone() ? 'ios-home' : 'unsupported' };
  }
  if (Notification.permission === 'denied') return { ok: false, why: 'denied' };
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  return { ok: true, on: !!sub, sub };
}
export async function enablePush() {
  const { key } = await api('/api/push/key');
  if (!key) throw new Error('서버에 알림 기능이 꺼져 있습니다');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('알림 권한을 허용해 주세요 (설정 → 알림)');
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await api('/api/push/subscribe', { sub: sub.toJSON() });
}
export async function disablePush() {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) { await api('/api/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => null); await sub.unsubscribe(); }
}

// ── 설정 ──
export async function settingsModal({ toast, onAccount, onTutorial, google = false } = {}) {
  const ps = await pushState().catch(() => ({ ok: false, why: 'unsupported' }));
  const acc = account();
  const p = sound.prefs;
  const slider = (k, label) => `<label class="slider"><span>${label}</span><input type="range" min="0" max="100" value="${Math.round(p[k] * 100)}" data-snd="${k}"></label>`;
  const pushRow = !acc ? '<p class="sub small">휴대폰 알림은 로그인한 뒤 켤 수 있어요.</p>'
    : !ps.ok ? `<p class="sub small">${ps.why === 'ios-home' ? '아이폰은 Safari의 <b>공유 → 홈 화면에 추가</b>로 설치한 앱에서 알림을 켤 수 있어요 (iOS 16.4 이상).' : ps.why === 'denied' ? '알림이 차단되어 있습니다. 브라우저(또는 휴대폰) 설정에서 이 사이트의 알림을 허용해 주세요.' : '이 브라우저는 알림을 지원하지 않습니다.'}</p>`
    : `<div class="row-acts">${ps.on ? '<button class="btn small" data-m="push-off">알림 끄기</button><button class="btn small ghost" data-m="push-test">시험 알림</button>' : '<button class="btn small primary" data-m="push-on">📱 휴대폰 알림 켜기</button>'}</div>
       <p class="sub small">내 앤의 탄생·결혼·죽음, 업적 같은 큰 소식만 모아서 보내요.</p>`;
  const html = `<h3>⚙️ 설정</h3>
    <section class="set-sec"><h4>🔊 소리</h4>
      <label class="check"><input type="checkbox" data-snd="on" ${p.on ? 'checked' : ''}> 소리 켜기</label>
      ${slider('master', '전체')}${slider('music', '음악')}${slider('amb', '환경음 (바람·비·새·도시)')}${slider('sfx', '효과음')}
    </section>
    <section class="set-sec"><h4>🔔 알림</h4>${pushRow}</section>
    <section class="set-sec"><h4>👤 계정</h4>
      ${acc ? `<p class="sub small"><b>${esc(acc.username)}</b>으로 로그인됨${acc.admin ? ' · 관리자' : ''}</p>
        <p class="sub small">${acc.google ? `${G_LOGO} 구글 계정 연결됨` : '구글 계정 연결 안 됨'}${acc.hasPassword === false ? ' · 비밀번호 없음 (구글로만 로그인)' : ''}</p>
        <div class="row-acts"><button class="btn small" data-m="logout">로그아웃</button><button class="btn small ghost" data-m="password">${acc.hasPassword === false ? '비밀번호 만들기' : '비밀번호 바꾸기'}</button>${google && !acc.google ? '<button class="btn small ghost" data-m="glink">구글 계정 연결</button>' : ''}${acc.google && acc.hasPassword !== false ? '<button class="btn small ghost" data-m="gunlink">구글 연결 끊기</button>' : ''}${acc.admin ? '<a class="btn small ghost" href="/admin" target="_blank">관리자 페이지</a>' : ''}<button class="btn small ghost danger" data-m="delete">회원 탈퇴</button></div>`
      : '<p class="sub small">로그인하면 어느 기기에서든 이어할 수 있어요.</p><div class="row-acts"><button class="btn small primary" data-m="login">로그인 / 가입</button></div>'}
    </section>
    <section class="set-sec"><h4>❓ 도움말</h4>
      <div class="row-acts">${onTutorial ? '<button class="btn small" data-m="tutorial">처음 안내 다시 보기</button>' : ''}<a class="btn small ghost" href="/terms" target="_blank">이용약관</a><a class="btn small ghost" href="/privacy" target="_blank">개인정보 처리방침</a></div>
    </section>
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn primary" data-m="close">닫기</button></div>`;
  const reopen = () => settingsModal({ toast, onAccount, onTutorial, google });
  const ctx = sheet(html, async (a, c) => {
    try {
      if (a === 'login') { c.close(); await accountModal({ toast, onChange: onAccount, google }); reopen(); }
      if (a === 'glink') { c.close(); await googleLogin({ toast, onChange: onAccount, link: true }); }
      if (a === 'gunlink') { await api('/api/auth/google/unlink', {}); toast?.('구글 연결을 끊었습니다'); onAccount?.(account()); c.close(); }
      if (a === 'logout') { await logout(); toast?.('로그아웃했습니다'); onAccount?.(null); c.close(); }
      if (a === 'push-on') { await enablePush(); toast?.('휴대폰 알림을 켰습니다'); c.close(); reopen(); }
      if (a === 'push-off') { await disablePush(); toast?.('휴대폰 알림을 껐습니다'); c.close(); reopen(); }
      if (a === 'push-test') { const r = await api('/api/push/test', {}); toast?.(r.sent ? '시험 알림을 보냈습니다' : '보낼 기기가 없습니다'); }
      if (a === 'tutorial') { c.close(); onTutorial?.(); }
      if (a === 'password') { c.close(); passwordModal({ toast, hasPassword: acc.hasPassword !== false, onAccount }); }
      if (a === 'delete') { c.close(); deleteModal({ toast, onAccount, hasPassword: acc.hasPassword !== false, username: acc.username }); }
    } catch (e) { c.err(e.message); }
  });
  let saveT = null;
  ctx.box.oninput = e => {
    const k = e.target.dataset.snd;
    if (!k) return;
    sound.set({ [k]: k === 'on' ? e.target.checked : e.target.value / 100 });
    if (k === 'sfx') sound.sfx('note');
    // 계정에 저장 (다른 기기에서도 같은 설정)
    clearTimeout(saveT);
    if (account()) saveT = setTimeout(() => api('/api/auth/prefs', { prefs: { sound: sound.prefs } }, 'PUT').catch(() => null), 800);
  };
  ctx.box.onchange = ctx.box.oninput;
}

function passwordModal({ toast, hasPassword = true, onAccount }) {
  sheet(`<h3>${hasPassword ? '비밀번호 바꾸기' : '비밀번호 만들기'}</h3>
    ${hasPassword ? '<label class="field"><span>지금 비밀번호</span><input id="pw-old" type="password" autocomplete="current-password"></label>' : '<p class="sub small">비밀번호를 만들면 구글 없이 아이디로도 로그인할 수 있어요.</p>'}
    <label class="field"><span>새 비밀번호 (8자 이상)</span><input id="pw-new" type="password" autocomplete="new-password"></label>
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn primary" data-m="go">바꾸기</button></div>`, async (a, c) => {
    if (a !== 'go') return;
    try {
      await api('/api/auth/password', { old: c.box.querySelector('#pw-old')?.value || '', password: c.box.querySelector('#pw-new').value });
      toast?.(hasPassword ? '비밀번호를 바꿨습니다' : '비밀번호를 만들었습니다');
      onAccount?.(account());
      c.close();
    } catch (e) { c.err(e.message); }
  });
}
function deleteModal({ toast, onAccount, hasPassword = true, username = '' }) {
  sheet(`<h3>회원 탈퇴</h3>
    <p class="sub">계정과 로그인 기록, 알림 구독이 바로 지워집니다. 게임 속 앤들은 세상에 남아 계속 살아가고, 플레이어 이름은 '탈퇴한 플레이어'로 바뀝니다. <b>되돌릴 수 없습니다.</b></p>
    ${hasPassword ? '<label class="field"><span>비밀번호 확인</span><input id="del-pw" type="password" autocomplete="current-password"></label>' : `<label class="field"><span>확인용으로 아이디 <b>${esc(username)}</b>를 입력하세요</span><input id="del-confirm" autocapitalize="off"></label>`}
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn danger" data-m="go">탈퇴하기</button></div>`, async (a, c) => {
    if (a !== 'go') return;
    try { await api('/api/auth/delete', { password: c.box.querySelector('#del-pw')?.value || '', confirm: c.box.querySelector('#del-confirm')?.value.trim() || '' }); auth.set(null); toast?.('탈퇴했습니다. 그동안 고마웠어요.'); onAccount?.(null); c.close(); } catch (e) { c.err(e.message); }
  });
}

// ── 알림함 · 업적 ──
export function inboxModal({ notes, ach, meta, onAnn, tab = 'notes' }) {
  const render = () => {
    const body = tab === 'notes'
      ? (notes.length ? `<div class="inbox">${notes.slice().reverse().map(n => `<div class="note lvl${n.lvl}" ${n.ann ? `data-m="ann:${n.ann}"` : ''}><span class="ni">${n.icon}</span><div><b>${esc(n.name || '')}</b> ${esc(n.text)}<small>${fmtDate(n.t)}</small></div></div>`).join('')}</div>`
        : '<p class="empty">아직 알림이 없습니다. 내 앤에게 큰 일이 생기면 여기에 쌓여요.</p>')
      : `<p class="sub small">${Object.keys(ach).length} / ${Object.keys(meta.achievements).length} 달성</p><div class="ach-grid">${Object.entries(meta.achievements).map(([k, d]) => `<div class="ach ${ach[k] != null ? 'got' : ''}"><span>${d.icon}</span><b>${esc(d.name)}</b><small>${esc(d.desc)}${ach[k] != null ? `<br>${fmtDate(ach[k])}` : ''}</small></div>`).join('')}</div>`;
    return `<h3>🔔 알림함</h3>
      <div class="seg"><button class="${tab === 'notes' ? 'on' : ''}" data-m="tab-notes">알림</button><button class="${tab === 'ach' ? 'on' : ''}" data-m="tab-ach">🏆 업적</button></div>
      ${body}<div class="acts"><button class="btn primary" data-m="close">닫기</button></div>`;
  };
  sheet(render(), (a, c) => {
    if (a.startsWith('tab-')) { tab = a.slice(4); c.box.innerHTML = render(); return; }
    if (a.startsWith('ann:')) { c.close(); onAnn?.(+a.slice(4)); }
  }, { wide: true });
}

// ── 그동안 있었던 일 ──
export function recapModal(r, { scale, onAnn }) {
  const realH = r.days * 1440 / (scale || 24) / 60;
  const when = realH >= 48 ? `${Math.round(realH / 24)}일` : realH >= 1 ? `${Math.round(realH)}시간` : `${Math.max(1, Math.round(realH * 60))}분`;
  const notes = r.notes.slice().sort((a, b) => b.lvl - a.lvl || b.t - a.t).slice(0, 14).sort((a, b) => a.t - b.t);
  sheet(`<h3>🕰️ 그동안 있었던 일</h3>
    <p class="sub">당신이 없는 동안 게임 속 <b>${r.days.toLocaleString()}일</b>이 흘렀습니다 (현실로 약 ${when}).</p>
    <div class="recap-stats"><div><b>${r.alive}</b><small>살아 있는 내 앤</small></div><div><b>${r.born}</b><small>새로 태어난 후손</small></div><div><b>${r.died}</b><small>세상을 떠난 앤</small></div><div><b>${esc(r.eraName)}</b><small>나라 · 인구 ${r.pop}</small></div></div>
    ${notes.length ? `<h4>내 앤들의 소식</h4><div class="inbox">${notes.map(n => `<div class="note lvl${n.lvl}" ${n.ann ? `data-m="ann:${n.ann}"` : ''}><span class="ni">${n.icon}</span><div><b>${esc(n.name || '')}</b> ${esc(n.text)}<small>${fmtDate(n.t)}</small></div></div>`).join('')}</div>` : '<p class="empty">조용한 나날이었습니다.</p>'}
    ${r.history.length ? `<h4>나라의 역사</h4><div class="inbox">${r.history.map(h => `<div class="note"><span class="ni">${h.icon || '📜'}</span><div>${esc(h.text)}<small>${fmtDate(h.t)}</small></div></div>`).join('')}</div>` : ''}
    <div class="acts"><button class="btn primary" data-m="close">계속 지켜보기</button></div>`, (a, c) => {
    if (a.startsWith('ann:')) { c.close(); onAnn?.(+a.slice(4)); }
  }, { wide: true });
}

// ── 신고 ──
export function reportModal({ server, ann, toast }) {
  sheet(`<h3>🚩 신고하기</h3>
    <p class="sub"><b>${esc(ann.n)}</b>${ann.owner ? ` (플레이어 ${esc(ann.owner.name)})` : ''} — 무엇이 문제인가요? 운영자가 확인합니다.</p>
    <div class="radio-list">
      <label><input type="radio" name="rr" value="부적절한 앤 이름" checked> 앤 이름이 부적절해요</label>
      ${ann.owner ? '<label><input type="radio" name="rr" value="부적절한 플레이어 이름"> 플레이어 이름이 부적절해요</label>' : ''}
      <label><input type="radio" name="rr" value="기타"> 기타</label>
    </div>
    <label class="field"><span>자세히 (선택)</span><input id="rr-more" maxlength="200"></label>
    <p class="form-err hidden"></p>
    <div class="acts"><button class="btn ghost" data-m="cancel">취소</button><button class="btn primary" data-m="go">신고</button></div>`, async (a, c) => {
    if (a !== 'go') return;
    const why = c.box.querySelector('input[name=rr]:checked')?.value || '기타';
    const more = c.box.querySelector('#rr-more').value.trim();
    const kind = why.includes('플레이어') ? 'player' : 'ann';
    try {
      await api('/api/report', { server, kind, id: kind === 'player' ? ann.owner.id : ann.id, reason: more ? `${why}: ${more}` : why });
      toast?.('신고가 접수되었습니다. 고맙습니다.');
      c.close();
    } catch (e) { c.err(e.message); }
  });
}

// ── 처음 안내 ──
const TUT_KEY = 'theEarth.tutorial.v1';
export const tutorialDone = () => { try { return !!localStorage.getItem(TUT_KEY); } catch { return true; } };
export function tutorial({ hasMine = true } = {}) {
  const steps = [
    { text: '<b>환영합니다!</b><br>투입한 앤들은 이제 <b>스스로 생각하고 살아갑니다.</b> 당신은 조종할 수 없고, 지켜볼 수만 있어요.' },
    { sel: '#left-tabs', text: '여기서 <b>내 앤</b>, 모든 앤, 나라, 회사, 직업, 소식을 볼 수 있어요.' },
    { sel: '#left-body', text: hasMine ? '앤을 누르면 그 앤의 <b>연대기, 속마음, 관계, 돈</b>을 들여다볼 수 있어요.' : '앤을 누르면 그 앤의 삶을 들여다볼 수 있어요.' },
    { text: TOUCH ? '<b>한 손가락</b>으로 지구를 돌리고, <b>두 손가락</b>으로 확대·회전해요. 마을을 <b>두 번 톡</b> 하면 가까이 내려가요.' : '<b>드래그</b>로 지구를 돌리고, <b>휠</b>로 확대해요. 오른쪽 버튼 드래그로 기울이고, 마을을 <b>두 번 누르면</b> 가까이 내려가요.' },
    { sel: '#hud-date', text: '<b>현실 1시간 = 게임 하루.</b> 앱을 닫아도 세계는 계속 흘러가요. 다시 오면 그동안 있었던 일을 알려 드려요.' },
    { sel: '#hud [data-act=inbox]', text: '내 앤의 탄생·결혼·죽음 같은 큰 소식과 <b>🏆 업적</b>이 여기에 쌓여요.' },
    { sel: '#hud [data-act=settings]', text: '<b>소리</b>, <b>휴대폰 알림</b>, 계정은 ⚙️ 설정에서 바꿀 수 있어요. 즐거운 관찰 되세요!' },
  ];
  let i = 0;
  const ov = document.createElement('div');
  ov.id = 'coach';
  ov.innerHTML = '<div class="coach-hole"></div><div class="coach-bubble"><p></p><div class="acts"><button class="btn ghost small" data-c="skip">건너뛰기</button><button class="btn primary small" data-c="next">다음</button></div></div>';
  document.body.appendChild(ov);
  const hole = ov.querySelector('.coach-hole'), bub = ov.querySelector('.coach-bubble');
  const show = () => {
    const st = steps[i];
    bub.querySelector('p').innerHTML = st.text;
    bub.querySelector('[data-c=next]').textContent = i === steps.length - 1 ? '시작하기' : `다음 (${i + 1}/${steps.length})`;
    // 휴대폰: 아래 시트가 접혀 있으면 펴서 보여 준다
    const sheetEl = document.getElementById('left');
    if (st.sel?.startsWith('#left') && sheetEl?.classList.contains('closed')) { sheetEl.classList.remove('closed'); setTimeout(show, 320); return; }
    const el = st.sel && document.querySelector(st.sel);
    const r = el && el.offsetParent !== null ? el.getBoundingClientRect() : null;
    ov.classList.toggle('dim', !(r && r.width));
    const bw = Math.min(r && r.width ? 340 : 380, innerWidth - 24);
    bub.style.cssText = `left:0;top:0;width:${bw}px;visibility:hidden`;
    const bh = bub.offsetHeight, M = 12;
    let x, y;
    if (r && r.width) {
      hole.style.cssText = `display:block;left:${r.left - 6}px;top:${r.top - 6}px;width:${r.width + 12}px;height:${r.height + 12}px`;
      const cx = Math.max(M, Math.min(innerWidth - bw - M, r.left + r.width / 2 - bw / 2));
      // 아래 → 위 → 오른쪽 → 왼쪽 → 대상 위에 겹쳐서, 화면 안에 들어가는 첫 자리
      if (r.bottom + 14 + bh < innerHeight - M) { x = cx; y = r.bottom + 14; }
      else if (r.top - 14 - bh > M) { x = cx; y = r.top - 14 - bh; }
      else if (r.right + 14 + bw < innerWidth - M) { x = r.right + 14; y = Math.max(M, Math.min(innerHeight - bh - M, r.top + 20)); }
      else if (r.left - 14 - bw > M) { x = r.left - 14 - bw; y = Math.max(M, Math.min(innerHeight - bh - M, r.top + 20)); }
      else { x = cx; y = Math.max(M, Math.min(innerHeight - bh - M, r.top + r.height / 2 - bh / 2)); }
    } else {
      hole.style.cssText = 'display:none';
      x = (innerWidth - bw) / 2; y = Math.max(M, innerHeight * 0.38 - bh / 2);
    }
    bub.style.cssText = `left:${x}px;top:${y}px;width:${bw}px`;
  };
  const end = () => { ov.remove(); try { localStorage.setItem(TUT_KEY, '1'); } catch { /* */ } };
  ov.onclick = e => {
    const c = e.target.closest('[data-c]')?.dataset.c;
    if (c === 'skip') return end();
    if (c === 'next') { sound.sfx('click'); if (++i >= steps.length) return end(); show(); }
  };
  addEventListener('resize', show, { once: true });
  show();
}
