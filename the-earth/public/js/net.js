// 서버와의 통신: REST + 웹소켓 (자동 재연결)

export async function api(path, body) {
  const res = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

export class Live {
  constructor(serverId, handlers) {
    this.serverId = serverId;
    this.h = handlers;
    this.closed = false;
    this.watchId = null;
    this.retry = 0;
    this.connect();
  }
  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = this.ws = new WebSocket(`${proto}://${location.host}/ws?server=${encodeURIComponent(this.serverId)}`);
    ws.onopen = () => { this.retry = 0; if (this.watchId) this.watch(this.watchId); this.h.open?.(); };
    ws.onmessage = ev => {
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
  watch(id) {
    this.watchId = id;
    if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ t: 'watch', id }));
  }
  close() { this.closed = true; this.ws.close(); }
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
