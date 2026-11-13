// 구글 로그인 (OpenID Connect, 서버 쪽 리디렉트 방식).
// 구글에서는 계정 고유번호(sub)만 받는다 — 이메일·이름은 받지 않는다 (scope: openid).
//
// 흐름: 앱이 begin 으로 요청 번호(id, 앱만 앎)를 받고 → 구글 로그인 창 → callback 에서 결과를 id 에 걸어 둔다 → 앱이 poll 로 가져간다.
//  - 같은 브라우저로 돌아오면(begin 때 심은 쿠키가 맞으면) 바로 끝난다.
//  - 아이폰 '홈 화면 앱'처럼 다른 브라우저(Safari)로 돌아가 버리면, 그 화면에 6자리 확인 코드를 보여 주고 앱에서 입력받는다.
//    (남이 보낸 로그인 링크를 눌러 자기 구글 계정이 넘어가는 피싱도 이 코드가 막는다)
import { randomBytes, randomInt, timingSafeEqual } from 'node:crypto';

const CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '';
const CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || '';
const AUTH_URL = process.env.GOOGLE_AUTH_URL || 'https://accounts.google.com/o/oauth2/v2/auth';
const TOKEN_URL = process.env.GOOGLE_TOKEN_URL || 'https://oauth2.googleapis.com/token';
const ISSUERS = ['https://accounts.google.com', 'accounts.google.com'];
const TTL = 10 * 60_000;

export const googleEnabled = () => !!(CLIENT_ID && CLIENT_SECRET);

const pending = new Map(); // id → { state, bind, link, created, status, result, code, tries }
const byState = new Map(); // state → id
setInterval(() => {
  const now = Date.now();
  for (const [id, p] of pending) if (now - p.created > TTL) { pending.delete(id); byState.delete(p.state); }
}, 60_000).unref();

const rnd = () => randomBytes(32).toString('base64url');
const same = (a, b) => { const x = Buffer.from(String(a || '')), y = Buffer.from(String(b || '')); return x.length === y.length && x.length > 0 && timingSafeEqual(x, y); };

/** 로그인 시작: { id(앱이 보관), state(주소에 실림), bind(쿠키) } */
export function begin({ link = null } = {}) {
  if (pending.size > 5000) throw new Error('잠시 후 다시 시도해 주세요');
  const id = rnd(), state = rnd(), bind = rnd();
  pending.set(id, { state, bind, link, created: Date.now(), status: 'wait', tries: 0 });
  byState.set(state, id);
  return { id, state, bind };
}

export function authUrl(state, redirectUri) {
  const q = new URLSearchParams({ client_id: CLIENT_ID, redirect_uri: redirectUri, response_type: 'code', scope: 'openid', state, prompt: 'select_account' });
  return `${AUTH_URL}?${q}`;
}

/** 구글이 돌려준 code 를 토큰으로 바꾸고, ID 토큰에서 계정 고유번호(sub)를 꺼낸다 */
export async function exchange(code, redirectUri) {
  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ code, client_id: CLIENT_ID, client_secret: CLIENT_SECRET, redirect_uri: redirectUri, grant_type: 'authorization_code' }),
    signal: AbortSignal.timeout(10_000),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || !j.id_token) throw new Error('구글 토큰 교환 실패: ' + (j.error || res.status));
  // ID 토큰을 구글 토큰 주소에서 TLS로 직접 받았으므로 서명 대신 발급자·대상·만료를 확인한다 (OpenID Connect Core 3.1.3.7)
  const payload = JSON.parse(Buffer.from(String(j.id_token).split('.')[1] || '', 'base64url').toString('utf8'));
  if (!ISSUERS.includes(payload.iss)) throw new Error('발급자가 구글이 아닙니다');
  if (payload.aud !== CLIENT_ID && !(Array.isArray(payload.aud) && payload.aud.includes(CLIENT_ID))) throw new Error('다른 앱의 토큰입니다');
  if (!payload.exp || payload.exp * 1000 < Date.now() - 60_000) throw new Error('만료된 토큰입니다');
  if (!payload.sub) throw new Error('계정 번호가 없습니다');
  return String(payload.sub);
}

/** callback: state 로 요청을 찾고, 같은 브라우저인지(쿠키) 본다 */
export function byStateLookup(state) {
  const id = byState.get(String(state || ''));
  const p = id && pending.get(id);
  return p ? { id, p } : null;
}
/** 결과를 걸어 둔다. 같은 브라우저가 아니면 확인 코드를 만든다 */
export function settle(id, bound, result) {
  const p = pending.get(id);
  if (!p) return null;
  byState.delete(p.state);
  p.result = result;
  if (bound) p.status = 'ready';
  else { p.status = 'code'; p.code = String(randomInt(0, 1e6)).padStart(6, '0'); }
  return p;
}
export function fail(id, message) {
  const p = pending.get(id);
  if (p) { p.status = 'error'; p.error = message; byState.delete(p.state); }
}
/** 앱이 결과를 가져간다 (한 번만). 코드가 필요하면 code 를 맞혀야 한다 */
export function poll(id, code) {
  const p = pending.get(String(id || ''));
  if (!p) return { status: 'gone' };
  if (p.status === 'wait') return { status: 'wait' };
  if (p.status === 'error') { pending.delete(id); return { status: 'error', error: p.error }; }
  if (p.status === 'code') {
    if (!code) return { status: 'code' };
    if (!same(code, p.code)) {
      if (++p.tries >= 5) { pending.delete(id); return { status: 'error', error: '확인 코드를 여러 번 틀렸습니다. 처음부터 다시 해 주세요.' }; }
      return { status: 'code', wrong: true };
    }
  }
  pending.delete(id);
  return { status: 'done', result: p.result, link: p.link };
}
export const bindMatches = (p, cookieVal) => same(p.bind, cookieVal);

// 처음 구글로 들어온 사람: 아이디를 정할 때까지 잠깐 보관
const tickets = new Map(); // ticket → { sub, created }
export function signupTicket(sub) { const t = rnd(); tickets.set(t, { sub, created: Date.now() }); return t; }
export function takeTicket(t, peek = false) {
  const v = tickets.get(String(t || ''));
  if (!v || Date.now() - v.created > TTL * 3) { tickets.delete(t); return null; }
  if (!peek) tickets.delete(t);
  return v.sub;
}
