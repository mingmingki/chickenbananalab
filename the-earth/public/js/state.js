// 클라이언트 쪽 세계 상태. 서버의 init/tick 을 받아 갱신하고, 앤 위치는 부드럽게 보간한다.

export const MIN_DAY = 1440, MIN_YEAR = 525600;
const MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
export function dateOf(min) {
  const dayN = Math.floor(min / MIN_DAY);
  const year = Math.floor(dayN / 365) + 1;
  let doy = dayN % 365, m = 0;
  while (doy >= MONTH_DAYS[m]) { doy -= MONTH_DAYS[m]; m++; }
  const mod = ((min % MIN_DAY) + MIN_DAY) % MIN_DAY;
  return { year, month: m + 1, day: doy + 1, hour: Math.floor(mod / 60), minute: Math.floor(mod % 60), doy: dayN % 365 };
}
export const fmtDate = min => { const d = dateOf(min); return `${d.year}년 ${d.month}월 ${d.day}일`; };
export const fmtTime = min => { const d = dateOf(min); return `${String(d.hour).padStart(2, '0')}:${String(d.minute).padStart(2, '0')}`; };
export const fmtDateTime = min => `${fmtDate(min)} ${fmtTime(min)}`;
export const localHour = (min, lon) => ((((min / 60) + lon / 15) % 24) + 24) % 24;

export class GameState {
  constructor() {
    this.reset();
  }
  reset() {
    this.world = null;
    this.clock = 0;
    this.clockAt = performance.now();
    this.scale = 24;
    this.countries = new Map();
    this.settlements = new Map();
    this.buildings = new Map();
    this.companies = new Map();
    this.players = new Map();
    this.anns = new Map(); // 메타
    this.pos = new Map();  // id -> {sid, x, z, px, pz, t0, act, target, moving}
    this.events = [];
    this.chat = [];
    this.listeners = {};
    this.dirty = { buildings: new Set(), settlements: true, countries: true, anns: true };
  }
  on(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  emit(ev, data) { for (const fn of this.listeners[ev] || []) fn(data); }

  /** 현재 게임 시각 (틱 사이도 보간) */
  now() {
    return this.clock + (performance.now() - this.clockAt) / 1000 * this.scale / 60;
  }

  applyInit(m) {
    this.world = m.world;
    this.clock = m.world.clock;
    this.clockAt = performance.now();
    this.scale = m.world.paused ? 0 : m.world.timeScale || 24;
    this.countries.clear(); this.settlements.clear(); this.buildings.clear(); this.companies.clear(); this.players.clear(); this.anns.clear(); this.pos.clear();
    for (const c of m.countries) this.countries.set(c.code, c);
    for (const s of m.settlements) this.settlements.set(s.id, s);
    for (const b of m.buildings) this.buildings.set(b.id, b);
    for (const c of m.companies) this.companies.set(c.id, c);
    for (const p of m.players) this.players.set(p.id, p);
    for (const a of m.anns) this.anns.set(a.id, a);
    this.applyPos(m.pos, true);
    this.events = m.events || [];
    this.chat = m.chat || [];
    this.dirty.all = true;
    this.emit('init');
  }

  applyPos(list, snap = false, full = false) {
    const t = performance.now();
    const seen = new Set();
    for (const [id, sid, x, z, act, target, travel, moving] of list) {
      seen.add(id);
      let p = this.pos.get(id);
      if (!p || snap || p.sid !== sid) {
        p = { sid, x, z, px: x, pz: z, t0: t, act, target, travel, moving, heading: 0 };
        this.pos.set(id, p);
      } else {
        const cur = this.interp(p, t);
        p.px = cur.x; p.pz = cur.z;
        if (Math.hypot(x - p.x, z - p.z) > 0.05) p.heading = Math.atan2(x - cur.x, z - cur.z);
        p.x = x; p.z = z; p.t0 = t;
      }
      if (p.act !== act) { p.act = act; p.actAt = t; }
      p.target = target; p.travel = travel; p.moving = moving; p.sid = sid;
    }
    // 바뀐 앤만 오므로, 빠진 앤 정리는 전체가 올 때만
    if (full) for (const id of this.pos.keys()) if (!seen.has(id)) { const a = this.anns.get(id); if (!a || !a.al) this.pos.delete(id); }
    for (const [id, , , , act] of list) if (act === 'dead' && !this.anns.get(id)?.al) setTimeout(() => { if (!this.anns.get(id)?.al) this.pos.delete(id); }, 1500);
  }

  /** 1초 간격 위치 사이를 보간 */
  interp(p, t = performance.now()) {
    const k = Math.min(1, (t - p.t0) / 1000);
    return { x: p.px + (p.x - p.px) * k, z: p.pz + (p.z - p.pz) * k };
  }

  applyTick(m) {
    this.clock = m.clock;
    this.clockAt = performance.now();
    if (m.scale != null) this.scale = m.scale; // 0 = 운영자가 일시정지한 서버
    if (m.anns) { for (const a of m.anns) this.anns.set(a.id, a); this.dirty.anns = true; }
    this.applyPos(m.pos, false, !!m.full);
    if (m.buildings) for (const b of m.buildings) { this.buildings.set(b.id, b); this.dirty.buildings.add(b.sid); }
    if (m.removed) for (const id of m.removed) { const b = this.buildings.get(id); if (b) { this.dirty.buildings.add(b.sid); this.buildings.delete(id); } }
    if (m.settlements) { for (const s of m.settlements) this.settlements.set(s.id, s); this.dirty.settlements = true; }
    if (m.countries) { for (const c of m.countries) this.countries.set(c.code, c); this.dirty.countries = true; }
    if (m.companies) for (const c of m.companies) { if (c.dead) this.companies.delete(c.id); else this.companies.set(c.id, c); }
    if (m.events) { this.events.push(...m.events); if (this.events.length > 400) this.events.splice(0, this.events.length - 400); }
    if (m.chat) { this.chat.push(...m.chat); if (this.chat.length > 200) this.chat.splice(0, this.chat.length - 200); }
    this.emit('tick', m);
  }

  annName(id) { return this.anns.get(id)?.n || '?'; }
  ageOf(a) { return Math.floor(((a.al ? this.now() : a.d) - a.b) / MIN_YEAR); }
  playerColor(id) { return this.players.get(id)?.color || '#9aa6bf'; }
}

export const state = new GameState();
