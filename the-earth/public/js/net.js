// 서버와의 통신: REST + 웹소켓 (자동 재연결), 로그인 토큰

// 앱(Capacitor)으로 감싸면 화면 파일은 휴대폰 안에 있고 서버는 따로 있다.
// <meta name="earth-server" content="https://..."> (scripts/build-app.mjs 가 넣어 줌) 로 서버 주소를 알려 준다. 웹에서는 비워 둔다.
export const SERVER = (document.querySelector('meta[name=earth-server]')?.content || '').replace(/\/$/, '');
export const serverUrl = path => SERVER + path;
/** 휴대폰 앱 안에서 실행 중인가 (Capacitor) */
export const NATIVE = !!window.Capacitor?.isNativePlatform?.();

const AUTH = 'theEarth.auth.v1';
export const auth = {
  get() { try { return JSON.parse(localStorage.getItem(AUTH) || 'null'); } catch { return null; } },
  set(v) { try { v ? localStorage.setItem(AUTH, JSON.stringify(v)) : localStorage.removeItem(AUTH); } catch { /* */ } },
};

export async function api(path, body, method) {
  const headers = {};
  const a = auth.get();
  if (a?.token) headers.Authorization = 'Bearer ' + a.token;
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(SERVER + path, { method: method || (body ? 'POST' : 'GET'), headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && a?.token && !path.startsWith('/api/auth/login')) auth.set(null); // 만료된 로그인
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

export class Live {
  constructor(serverId, handlers, playerToken = null) {
    this.serverId = serverId;
    this.playerToken = playerToken;
    this.h = handlers;
    this.closed = false;
    this.watchId = null;
    this.retry = 0;
    this.lastMsg = Date.now();
    this.connect();
    // 휴대폰이 잠들었다 깨면 연결이 죽어 있기 쉽다 → 바로 다시 잇는다
    this.onVis = () => {
      if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ t: 'vis', v: !document.hidden }));
      if (document.hidden || this.closed) return;
      if (this.ws.readyState !== 1 || Date.now() - this.lastMsg > 4000) { this.retry = 0; try { this.ws.close(); } catch { /* */ } }
    };
    document.addEventListener('visibilitychange', this.onVis);
  }
  connect() {
    const base = SERVER ? SERVER.replace(/^http/, 'ws') : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
    const ws = this.ws = new WebSocket(`${base}/ws?server=${encodeURIComponent(this.serverId)}`);
    ws.onopen = () => {
      this.retry = 0;
      if (this.playerToken) ws.send(JSON.stringify({ t: 'hello', token: this.playerToken }));
      ws.send(JSON.stringify({ t: 'vis', v: !document.hidden }));
      if (this.watchId) this.watch(this.watchId);
      this.h.open?.();
    };
    ws.onmessage = ev => {
      this.lastMsg = Date.now();
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      this.h[m.t]?.(m);
    };
    ws.onclose = () => {
      if (this.closed) return;
      this.h.down?.();
      setTimeout(() => this.connect(), Math.min(8000, 800 * 2 ** this.retry++));
    };
  }
  hello(token) { this.playerToken = token; if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ t: 'hello', token })); }
  watch(id) {
    this.watchId = id;
    if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ t: 'watch', id }));
  }
  close() { this.closed = true; document.removeEventListener('visibilitychange', this.onVis); this.ws.close(); }
}

// 저장된 게임 (이어하기)
const KEY = 'theEarth.sessions.v1';
export function loadSessions() {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; }
}
export function saveSession(s) {
  try {
    const list = loadSessions().filter(x => !(x.serverId === s.serverId && x.token === s.token));
    list.unshift({ ...s, at: Date.now() });
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, 12)));
  } catch { /* 저장 불가 환경 */ }
}
export function forgetSession(serverId, token) {
  try { localStorage.setItem(KEY, JSON.stringify(loadSessions().filter(x => !(x.serverId === serverId && x.token === token)))); } catch { /* */ }
}
