// 교통: 길 그물(도로 등급별 속도), 자동차, 버스(노선·정류장·기사), 신호등.
// 신호등·정류장은 바로 생기지 않는다. 관청 교통과(정류장은 버스 회사)가 계획하고, 단계마다 정해진 인시(사람×시간)를
// 일꾼들이 채워야 하고, 콘크리트 양생처럼 사람이 아무리 많아도 기다려야 하는 날도 있다.
import { chance, pick, clamp, rand, ageOf, localHour, DAY, ga } from './util.js';
import { assignContractor } from './economy.js';
import { refMonth, wageOfEra } from './budget.js';
import { lifeEvent, think } from './ann.js';
import { logToday } from './mind.js';

const BW = 100, OFF = 10; // settlement.js 의 격자: 블록 100m, 길은 블록 모서리
export const CAR_SPEED = [200, 260, 320, 480, 600]; // m/분 (흙길 12km/h … 대로 36km/h)
const BUS_K = 0.75;          // 버스는 승용차보다 느리다
const WALK = 64;             // m/분 (걷기, 대략)
const PARK_SPEED = 90;       // 골목·주차장
export const SIGNAL_CYCLE = 90; // 초: 남북 초록 40 · 노랑 5 · 동서 초록 40 · 노랑 5
const BUS_CAP = 40;
const BUS_WAIT_MAX = 35;     // 분: 이만큼 기다려도 안 오면 걸어간다

// ── 공정 (단계별 인시) ──
// mh: 그 단계에 드는 표준 인시, crew: 한꺼번에 붙을 수 있는 사람 수, cure: 일 없이 기다리는 날 (양생·시험 운영)
export const STAGES = {
  signal: [
    { name: '측량·설계', mh: 16, trade: '토목' },
    { name: '굴착·관로 매설', mh: 96, trade: '토목' },
    { name: '기초 콘크리트 타설', mh: 64, trade: '토목', cure: 7, cureName: '콘크리트 양생' },
    { name: '신호주·가로대 세우기', mh: 48, trade: '크레인' },
    { name: '신호등 달기·배선', mh: 72, trade: '전기' },
    { name: '제어기 설치·신호 시간 설정', mh: 40, trade: '전기' },
    { name: '정지선·횡단보도 도색', mh: 24, trade: '도장' },
    { name: '시험 운영 (점멸)', mh: 8, trade: '전기', cure: 2, cureName: '점멸 시험 운영' },
  ],
  busstop: [
    { name: '표지판 기초', mh: 8, trade: '토목', cure: 2, cureName: '기초 굳히기' },
    { name: '승강장 블록 깔기', mh: 16, trade: '토목' },
    { name: '정류장 표지판·의자 설치', mh: 8, trade: '설치' },
  ],
};
export const PROJECT_CREW = { signal: 5, busstop: 3 };
const MAT_K = { signal: 2.4, busstop: 1.2 }; // 인건비 대비 자재비 (신호등은 신호기·케이블·제어기가 비싸다)
export const stagesTotal = type => STAGES[type].reduce((t, s) => t + s.mh, 0);

/** 지금 몇 번째 단계인가 (progress = 지금까지 채운 표준 인시) */
export function stageIndex(b) {
  let cum = 0;
  for (let i = 0; i < b.stages.length; i++) { cum += b.stages[i].mh; if (b.progress < cum - 1e-6) return i; }
  return b.stages.length - 1;
}
/** 단계 공사에 일을 더한다. 양생 중이면 일이 안 된다. 쓰고 남은 시간은 돌려준다 */
export function stagedWork(world, b, hours) {
  if (b.waitUntil && world.clock < b.waitUntil) return 0;
  if (b.waitUntil) { b.waitUntil = 0; b.waitName = null; }
  const i = stageIndex(b);
  let end = 0; for (let k = 0; k <= i; k++) end += b.stages[k].mh;
  const add = Math.min(hours, end - b.progress);
  b.progress += add;
  if (b.progress >= end - 1e-6) {
    b.progress = end;
    const st = b.stages[i];
    if (st.cure && i < b.stages.length - 1) { b.waitUntil = world.clock + st.cure * DAY; b.waitName = st.cureName; }
    else if (st.cure && i === b.stages.length - 1 && !b.cured) { b.waitUntil = world.clock + st.cure * DAY; b.waitName = st.cureName; b.cured = true; b.progress = end - 1e-3; }
  }
  b.stage = stageIndex(b);
  return add;
}
export const isWaiting = (world, b) => !!(b?.waitUntil && world.clock < b.waitUntil);

// ── 길 그물 ──
const nodeXY = k => { const i = k.indexOf(','); return [+k.slice(0, i) * BW + OFF, +k.slice(i + 1) * BW + OFF]; };
const segNodes = key => { const [o, bi, bj] = key.split(','); const i = +bi, j = +bj; return o === 'h' ? [`${i},${j}`, `${i + 1},${j}`] : [`${i},${j}`, `${i},${j + 1}`]; };
export { nodeXY };

export function roadGraph(world, s) {
  const roads = s.roads || {};
  let n = 0, sig = 0; for (const k in roads) { n++; sig += roads[k] + 1; }
  const key = n + '/' + sig;
  world._graphs = world._graphs || new Map();
  const c = world._graphs.get(s.id);
  if (c && c.key === key) return c;
  const adj = new Map();
  for (const [seg, g] of Object.entries(roads)) {
    const [u, v] = segNodes(seg);
    for (const [x, y] of [[u, v], [v, u]]) { if (!adj.has(x)) adj.set(x, []); adj.get(x).push({ to: y, g, axis: seg[0] }); }
  }
  const out = { key, adj, nodes: [...adj.keys()], routes: new Map() };
  world._graphs.set(s.id, out);
  return out;
}
export function nearestNode(gr, x, z) {
  let best = null, bd = Infinity;
  for (const k of gr.nodes) { const [nx, nz] = nodeXY(k); const d = Math.abs(nx - x) + Math.abs(nz - z); if (d < bd) { bd = d; best = k; } }
  return best;
}
/** 가장 빨리 가는 길 → [{ node, axis(들어올 때 방향 h/v), g(들어올 때 길 등급) }] */
export function route(gr, from, to) {
  const ck = from + '>' + to;
  if (gr.routes.has(ck)) return gr.routes.get(ck);
  let res = null;
  if (from === to) res = [{ node: from, axis: null, g: 0 }];
  else {
    const dist = new Map([[from, 0]]), prev = new Map(), done = new Set(), pq = [[0, from]];
    while (pq.length) {
      let mi = 0; for (let i = 1; i < pq.length; i++) if (pq[i][0] < pq[mi][0]) mi = i;
      const [d, u] = pq.splice(mi, 1)[0];
      if (done.has(u)) continue;
      done.add(u);
      if (u === to) break;
      for (const e of gr.adj.get(u) || []) {
        const nd = d + BW / CAR_SPEED[e.g];
        if (nd < (dist.get(e.to) ?? Infinity)) { dist.set(e.to, nd); prev.set(e.to, { u, e }); pq.push([nd, e.to]); }
      }
    }
    if (prev.has(to)) {
      res = [];
      for (let cur = to; cur !== from;) { const p = prev.get(cur); res.push({ node: cur, axis: p.e.axis, g: p.e.g }); cur = p.u; }
      res.push({ node: from, axis: null, g: 0 });
      res.reverse();
    }
  }
  if (gr.routes.size > 4000) gr.routes.clear();
  gr.routes.set(ck, res);
  return res;
}

// ── 신호등 ──
export function signalState(s, node, axis, clock) {
  const sg = s.signals?.[node];
  if (!sg) return 'g';
  const t = (((clock * 60 + sg.off) % SIGNAL_CYCLE) + SIGNAL_CYCLE) % SIGNAL_CYCLE;
  const ns = t < 40 ? 'g' : t < 45 ? 'y' : 'r';
  const ew = t < 45 ? 'r' : t < 85 ? 'g' : 'y';
  return axis === 'v' ? ns : ew;
}

// ── 길 떠나기: 걸을까, 차를 몰까, 버스를 탈까 ──
const busRunning = (world, s) => !!(s.bus?.open && s.bus.buses?.some(b => b.on));
export function planTrip(world, a, act) {
  if (act.type === 'social' || act.patrol || a.travel) return null;
  const s = world.settlements.get(a.sid), c = world.countries[a.country];
  if (!s || !c) return null;
  const d = Math.hypot(act.tx - a.x, act.tz - a.z);
  if (d < 160) return null;
  const age = ageOf(a, world.clock);
  const commute = act.type === 'work' || act.type === 'build' || act.type === 'sleep' || act.type === 'shop';
  if (a.car && c.era >= 5 && age >= 18 && age < 85 && !(a.sick > 60) && (commute || d >= 230)) {
    const t = carTrip(world, s, a, act, d);
    if (t) return t;
  }
  if (d > 280 && age >= 7 && busRunning(world, s)) return busTrip(world, s, a, act, d, age);
  return null;
}

function carTrip(world, s, a, act, direct) {
  const gr = roadGraph(world, s);
  if (gr.nodes.length < 3) return null;
  const A = nearestNode(gr, a.x, a.z), B = nearestNode(gr, act.tx, act.tz);
  if (A === B) return null;
  const path = route(gr, A, B);
  if (!path) return null;
  const wps = path.map(p => { const [x, z] = nodeXY(p.node); return { x, z, node: p.node, axis: p.axis, g: p.g }; });
  wps[0].axis = null;
  wps.push({ x: act.tx, z: act.tz, node: null, axis: null, g: -1 });
  if (wps.length * BW > direct * 3.5 + 400) return null; // 너무 돌아가면 걷는다
  if (chance(0.35)) logToday(world, a, '🚗', act.type === 'work' || act.type === 'build' ? '차를 몰고 출근' : '차를 몰고 나갔다');
  return { mode: 'car', wps, i: 0 };
}

/** 차가 한 걸음 (dt분). 도착하면 true */
function carStep(world, s, a, trip, dt) {
  let left = dt;
  while (left > 1e-6 && trip.i < trip.wps.length) {
    const w = trip.wps[trip.i];
    const sp = w.g >= 0 && trip.i > 0 ? CAR_SPEED[w.g] : PARK_SPEED;
    const dd = Math.hypot(w.x - a.x, w.z - a.z);
    if (w.node && w.axis && s.signals?.[w.node] && dd <= sp * left + 0.01 && signalState(s, w.node, w.axis, world.clock) !== 'g') {
      const go = Math.max(0, dd - 8); // 정지선
      if (go > 0.01) { const k = Math.min(go, sp * left) / dd; a.x += (w.x - a.x) * k; a.z += (w.z - a.z) * k; }
      trip.stop = true;
      return false;
    }
    trip.stop = false;
    if (dd <= sp * left) {
      a.x = w.x; a.z = w.z; left -= dd / sp; trip.i++;
      if (w.node) { s.flow = s.flow || {}; s.flow[w.node] = (s.flow[w.node] || 0) + 1; via(a, w.x, w.z); }
    } else { const k = sp * left / dd; a.x += (w.x - a.x) * k; a.z += (w.z - a.z) * k; left = 0; }
  }
  return trip.i >= trip.wps.length;
}
/** 이번 틱에 지나간 모퉁이 (화면이 길을 따라 그리도록) */
function via(o, x, z) { (o._via = o._via || []).push(Math.round(x * 10) / 10, Math.round(z * 10) / 10); if (o._via.length > 40) o._via.splice(0, o._via.length - 40); }

// ── 버스 ──
function stopXY(s, k) { return s.bus.stops[k].pos; }
function busTrip(world, s, a, act, direct, age = 30) {
  const st = s.bus.stops;
  let bi = -1, bd = Infinity, ai = -1, ad = Infinity;
  st.forEach((p, i) => {
    const d1 = Math.hypot(p.pos[0] - a.x, p.pos[1] - a.z), d2 = Math.hypot(p.pos[0] - act.tx, p.pos[1] - act.tz);
    if (d1 < bd) { bd = d1; bi = i; }
    if (d2 < ad) { ad = d2; ai = i; }
  });
  if (bi < 0 || ai < 0 || bi === ai) return null;
  const ride = (s.bus.stops[ai].d - s.bus.stops[bi].d + s.bus.len) % s.bus.len;
  const on = s.bus.buses.filter(b => b.on).length || 1;
  const est = bd / WALK + (s.bus.len / (CAR_SPEED[3] * BUS_K) / on) / 2 + ride / (CAR_SPEED[3] * BUS_K) + ad / WALK;
  // 걷는 것보다 빨라야 타지만, 비·눈·추위·더위, 나이, 피곤하면 좀 느려도 탄다
  const wx = s.weather || {};
  const bad = /비|소나기|폭우|태풍|눈|폭설/.test(wx.k || '') || (wx.temp ?? 15) < 0 || (wx.temp ?? 15) > 30;
  const k = 0.9 + (bad ? 0.45 : 0) + (age >= 65 ? 0.35 : 0) + (a.nd.energy < 40 ? 0.2 : 0) + (a.ab.ath < 35 ? 0.1 : 0);
  if (est > direct / WALK * k) return null;
  return { mode: 'bus', leg: 'walk1', board: bi, alight: ai };
}
function walkTo(a, x, z, dt) {
  const dd = Math.hypot(x - a.x, z - a.z), step = WALK * dt;
  if (dd <= step) { a.x = x; a.z = z; return true; }
  a.x += (x - a.x) / dd * step; a.z += (z - a.z) / dd * step;
  return false;
}
function busRiderStep(world, s, a, act, trip, dt) {
  if (!s.bus?.open) { act.trip = null; return false; }
  if (trip.leg === 'walk1') {
    const [x, z] = stopXY(s, trip.board);
    if (walkTo(a, x, z, dt)) { trip.leg = 'wait'; trip.since = world.clock; (s.bus.stops[trip.board].q = s.bus.stops[trip.board].q || []).push(a.id); }
    return false;
  }
  if (trip.leg === 'wait') {
    if (world.clock - trip.since > BUS_WAIT_MAX) {
      const q = s.bus.stops[trip.board].q || []; const i = q.indexOf(a.id); if (i >= 0) q.splice(i, 1);
      act.trip = null; // 걸어간다
      if (chance(0.4)) logToday(world, a, '⌛', `${s.bus.stops[trip.board].name} 정류장에서 기다리다 걸어갔다`);
      return false;
    }
    return false;
  }
  if (trip.leg === 'ride') return false; // 버스가 옮겨 준다
  if (trip.leg === 'walk2') return walkTo(a, act.tx, act.tz, dt);
  return false;
}

/** 탈것을 쓰는 이동 한 걸음. mind.js 의 이동 단계에서 부른다. 도착하면 true */
export function tripStep(world, a, act, dt) {
  const trip = act.trip, s = world.settlements.get(a.sid);
  if (!trip || !s) return null;
  if (trip.mode === 'car') return carStep(world, s, a, trip, dt);
  return busRiderStep(world, s, a, act, trip, dt);
}
/** 화면에 보낼 탈것 모드: 0 걷기, 1 자동차, 2 버스 승객(버스 안), 3 버스 기사 */
export function vehicleMode(a) {
  if (a.drive && a.act?.type === 'work') return 3;
  const t = a.act?.ph === 'move' && a.act.trip;
  if (!t) return 0;
  if (t.mode === 'car') return 1;
  return t.leg === 'ride' ? 2 : 0;
}

// ── 버스 노선 ──
function makeLoop(world, s) {
  const gr = roadGraph(world, s);
  if (gr.nodes.length < 8) return null;
  // 따로 떨어진 길(외딴 집 둘레)은 빼고, 가장 큰 길 그물에서만
  const seen = new Set();
  let main = [];
  for (const k of gr.nodes) {
    if (seen.has(k)) continue;
    const comp = [], q = [k]; seen.add(k);
    while (q.length) { const u = q.pop(); comp.push(u); for (const e of gr.adj.get(u) || []) if (!seen.has(e.to)) { seen.add(e.to); q.push(e.to); } }
    if (comp.length > main.length) main = comp;
  }
  if (main.length < 8) return null;
  const sub = { nodes: main };
  // 그 길 그물의 바깥쪽 70% 를 도는 고리: 네 모서리에 가까운 교차로를 잇는다
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const k of main) { const [x, z] = nodeXY(k); x0 = Math.min(x0, x); x1 = Math.max(x1, x); z0 = Math.min(z0, z); z1 = Math.max(z1, z); }
  const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2, hx = Math.max(BW, (x1 - x0) * 0.35), hz = Math.max(BW, (z1 - z0) * 0.35);
  const corners = [[cx - hx, cz - hz], [cx + hx, cz - hz], [cx + hx, cz + hz], [cx - hx, cz + hz]].map(([x, z]) => nearestNode(sub, x, z));
  if (new Set(corners).size < 3) return null;
  const loop = [];
  for (let i = 0; i < 4; i++) {
    const r = route(gr, corners[i], corners[(i + 1) % 4]);
    if (!r) return null;
    for (const p of (loop.length ? r.slice(1) : r)) loop.push(p);
  }
  if (loop.length > 1 && loop[loop.length - 1].node === loop[0].node) loop.pop();
  if (loop.length < 4) return null;
  // 거리 누적
  const pts = loop.map(p => ({ ...p, xy: nodeXY(p.node) }));
  let len = 0;
  pts.forEach((p, i) => { p.d = len; const q = pts[(i + 1) % pts.length]; len += Math.hypot(q.xy[0] - p.xy[0], q.xy[1] - p.xy[1]); });
  return { pts, len };
}
const STOP_NAMES = ['시장 앞', '관청 앞', '학교 앞', '중앙', '사거리', '마을 어귀', '공원 앞', '병원 앞', '새마을', '큰길', '동네 입구', '우체국 앞'];
/** 버스 회사가 생기면: 노선을 긋고 정류장 공사를 맡긴다. 정류장이 다 지어지면 개통, 버스를 산다 */
export function busDaily(world, s, c) {
  // 민간 버스 회사, 없으면 관청의 공영버스
  const co = [...world.companies.values()].find(x => !x.dead && x.ind === 'bus' && x.sid === s.id) || [...world.companies.values()].find(x => !x.dead && x.pub && x.ind === 'transit' && x.sid === s.id);
  if (!co) { if (s.bus?.open) { world.news('build', `🚌 ${s.name} 버스 운행이 멈췄다 — 버스 회사가 문을 닫았다`, { sid: s.id, country: s.country }, 1); } if (s.bus) s.bus = null; return; }
  if (!s.bus || s.bus.co !== co.id) {
    const lp = makeLoop(world, s);
    if (!lp) return;
    const stops = [];
    const every = Math.max(1, Math.round(lp.pts.length / Math.min(10, Math.max(3, Math.round(lp.len / 260)))));
    for (let i = 0; i < lp.pts.length; i += every) {
      const p = lp.pts[i], q = lp.pts[(i + 1) % lp.pts.length];
      // 정류장은 교차로에서 다음 교차로 쪽으로 25m, 길 가장자리
      const dx = Math.sign(q.xy[0] - p.xy[0]), dz = Math.sign(q.xy[1] - p.xy[1]);
      const pos = [p.xy[0] + dx * 25 + dz * 7, p.xy[1] + dz * 25 - dx * 7];
      stops.push({ i, d: p.d + 25, node: p.node, pos, name: STOP_NAMES[stops.length % STOP_NAMES.length], built: false, q: [] });
    }
    for (const st of stops) st.d %= lp.len;
    s.bus = { co: co.id, pts: lp.pts.map(p => ({ node: p.node, xy: p.xy, d: p.d, axis: p.axis, g: p.g })), len: lp.len, stops, open: false, buses: [], planned: Math.round(world.clock) };
    // 공영버스는 나라 곳간에서, 민간 버스 회사는 회사 돈으로
    for (const st of stops) st.project = makeProject(world, s, 'busstop', st.pos[0], st.pos[1], { node: st.node, name: `${st.name} 버스 정류장 설치`, payer: co.pub ? null : co });
    world.news('build', `🚏 ${co.name}이(가) ${s.name} 버스 노선(${(lp.len / 1000).toFixed(1)}km)을 정하고 정류장 ${stops.length}곳 공사를 시작했다`, { sid: s.id, country: s.country }, 1);
    return;
  }
  const B = s.bus;
  for (const st of B.stops) if (!st.built && st.project && !world.buildings.get(st.project)) st.built = true; // 공사가 끝나 지워졌으면
  if (!B.open && B.stops.every(st => st.built)) {
    B.open = true; B.opened = Math.round(world.clock);
    world.news('build', `🚌 ${s.name} 버스 노선 개통 — ${co.name}, 정류장 ${B.stops.length}곳`, { sid: s.id, country: s.country }, 2);
    world.first?.(c, 'bus', `첫 버스 노선 — ${s.name}`, '🚌');
  }
  if (!B.open) return;
  // 버스 대수: 기사 수만큼 (노선 길이에 맞게), 돈이 되는 만큼 산다
  const drivers = co.emp.map(id => world.anns.get(id)).filter(e => e?.alive && e.job?.id === 'bus_driver');
  const want = clamp(drivers.length, 0, Math.ceil(B.len / 900) + 1);
  const price = refMonth(c.era) * 20;
  const purse = co.pub ? { get: () => c.treasury * 0.3, pay: v => { c.treasury -= v; } } : { get: () => co.money, pay: v => { co.money -= v; } };
  while (B.buses.length < want && purse.get() > price * 0.5) {
    purse.pay(price);
    const n = B.buses.length;
    B.buses.push({ id: world.newId(), d: (B.len / Math.max(1, want)) * n, on: false, riders: [], driver: null, dwell: 0, no: n + 1 });
    world.news('build', `🚌 ${co.name}이(가) ${n + 1}호 버스를 들여왔다`, { sid: s.id, country: s.country }, 0);
  }
  if (B.buses.length > want + 1) B.buses.length = want + 1;
}

/** 버스 운행 (매 틱). 기사가 출근해 있어야 달린다 */
export function busTick(world, s, dt) {
  const B = s.bus;
  if (!B?.open || !B.buses.length) return;
  const co = world.companies.get(B.co);
  const hr = localHour(world.clock, s.lon);
  const service = hr >= 5.5 && hr < 24;
  const onDuty = co ? co.emp.map(id => world.anns.get(id)).filter(e => e?.alive && e.job?.id === 'bus_driver' && e.act?.type === 'work' && e.act.ph === 'do') : [];
  const taken = new Set();
  for (const bus of B.buses) {
    if (bus.driver) { const d = world.anns.get(bus.driver); if (!d || !onDuty.includes(d)) { if (d) d.drive = null; bus.driver = null; } else taken.add(d.id); }
  }
  for (const bus of B.buses) {
    if (!bus.driver && service) { const d = onDuty.find(e => !taken.has(e.id)); if (d) { bus.driver = d.id; taken.add(d.id); d.drive = bus.id; if (chance(0.3)) logToday(world, d, '🚌', `${bus.no}호 버스 운전`); } }
    // 기사가 없으면: 손님이 있으면 다음 정류장까지만 가서 모두 내려 주고 선다
    bus.on = !!bus.driver || bus.riders.length > 0;
    if (!bus.on) continue;
    const lastRun = !bus.driver;
    let left = dt;
    while (left > 1e-6) {
      if (bus.dwell > 0) { const w = Math.min(bus.dwell, left); bus.dwell -= w; left -= w; continue; }
      // 다음 목표: 다음 정류장 또는 다음 교차로
      const d0 = bus.d;
      let nextD = B.len + 1, nextStop = null, nextNode = null;
      for (const st of B.stops) { const dd = (st.d - d0 + B.len) % B.len; if (dd > 0.01 && dd < nextD) { nextD = dd; nextStop = st; } }
      for (const p of B.pts) { const dd = (p.d - d0 + B.len) % B.len; if (dd > 0.01 && dd < nextD) { nextD = dd; nextStop = null; nextNode = p; } }
      const seg = segAt(B, d0);
      const sp = CAR_SPEED[Math.max(0, seg.g ?? 3)] * BUS_K;
      if (nextNode && s.signals?.[nextNode.node] && nextD <= sp * left + 0.01 && signalState(s, nextNode.node, nextNode.axis, world.clock) !== 'g') {
        bus.d = (d0 + Math.max(0, nextD - 9)) % B.len; left = 0; break; // 빨간불
      }
      if (nextD <= sp * left) {
        bus.d = (d0 + nextD) % B.len; left -= nextD / sp;
        if (nextNode) { via(bus, nextNode.xy[0], nextNode.xy[1]); s.flow = s.flow || {}; s.flow[nextNode.node] = (s.flow[nextNode.node] || 0) + 1; }
        if (nextStop) {
          if (lastRun) { emptyBus(world, s, bus, nextStop); bus.on = false; left = 0; break; }
          atStop(world, s, bus, nextStop);
        }
      } else { bus.d = (d0 + sp * left) % B.len; left = 0; }
    }
    const [x, z, h] = posAt(B, bus.d);
    bus.x = x; bus.z = z; bus.h = h;
    for (const id of bus.riders) { const r = world.anns.get(id); if (r) { r.x = x; r.z = z; } }
    if (bus.driver) { const d = world.anns.get(bus.driver); if (d) { d.x = x; d.z = z; } }
  }
}
function segAt(B, d) {
  for (let i = B.pts.length - 1; i >= 0; i--) if (B.pts[i].d <= d) { const nx = B.pts[(i + 1) % B.pts.length]; return { g: nx.g ?? 3 }; }
  return { g: 3 };
}
function posAt(B, d) {
  let i = 0;
  for (let k = 0; k < B.pts.length; k++) if (B.pts[k].d <= d) i = k;
  const p = B.pts[i], q = B.pts[(i + 1) % B.pts.length];
  const L = Math.hypot(q.xy[0] - p.xy[0], q.xy[1] - p.xy[1]) || 1, t = clamp((d - p.d) / L, 0, 1);
  return [p.xy[0] + (q.xy[0] - p.xy[0]) * t, p.xy[1] + (q.xy[1] - p.xy[1]) * t, Math.atan2(q.xy[0] - p.xy[0], q.xy[1] - p.xy[1])];
}
/** 운행 끝: 손님을 모두 내려 준다 */
function emptyBus(world, s, bus, st) {
  for (const id of bus.riders) {
    const r = world.anns.get(id), t = r?.act?.trip;
    if (r && t?.mode === 'bus') { t.leg = 'walk2'; r.x = st.pos[0]; r.z = st.pos[1]; if (chance(0.5)) logToday(world, r, '🚏', `버스가 운행을 마쳐 ${st.name}에서 내렸다`); }
  }
  bus.riders = [];
}
function atStop(world, s, bus, st) {
  let changed = false;
  // 내릴 사람
  bus.riders = bus.riders.filter(id => {
    const r = world.anns.get(id);
    const t = r?.act?.trip;
    if (!r || !r.alive || !t || t.mode !== 'bus') return false;
    if (s.bus.stops[t.alight] === st) { t.leg = 'walk2'; r.x = st.pos[0]; r.z = st.pos[1]; changed = true; return false; }
    return true;
  });
  // 탈 사람
  const q = st.q || [];
  while (q.length && bus.riders.length < BUS_CAP) {
    const id = q.shift(), r = world.anns.get(id), t = r?.act?.trip;
    if (!r || !r.alive || !t || t.leg !== 'wait') continue;
    t.leg = 'ride'; bus.riders.push(id); changed = true;
    if (chance(0.3)) logToday(world, r, '🚌', `${st.name} 정류장에서 버스를 탔다`);
  }
  bus.dwell = changed ? 0.5 : 0.15;
}

// ── 공사 계획 ──
/** 단계 공사 하나 (신호등·정류장). payer: 돈 낼 회사, 없으면 나라 곳간 */
export function makeProject(world, s, type, x, z, { node, name, payer = null } = {}) {
  const c = world.countries[s.country];
  const stages = STAGES[type].map(st => ({ ...st }));
  const work = stagesTotal(type);
  const wage = wageOfEra(c.era);
  const cost = Math.round(work * wage / 8 * (1 + MAT_K[type]) * 0.14);
  if (payer) payer.money -= cost; else c.treasury -= cost;
  const b = {
    id: world.newId(), type, sid: s.id, name, infra: true, project: true, node,
    x, z, w: 6, d: 6, h: 0, rot: 0, cells: [0, 0, 0, 0], done: false, progress: 0, work, budget: cost,
    started: Math.round(world.clock), era: c.era, owner: null, co: null, res: [], stages, stage: 0, mh: 0, maxCrew: PROJECT_CREW[type],
  };
  world.buildings.set(b.id, b);
  (s.works = s.works || []).push(b.id);
  world.dirtyBuildings.add(b.id);
  assignContractor(world, b);
  return b.id;
}

/** 교통과: 차가 많이 지나는 교차로에 신호등을 계획한다 (하루에 한 번) */
export function trafficDaily(world, s, hall) {
  const c = world.countries[s.country];
  // 교차로별 통행량 (지수 평균)
  s.flowAvg = s.flowAvg || {};
  for (const k of new Set([...Object.keys(s.flowAvg), ...Object.keys(s.flow || {})])) {
    const v = (s.flowAvg[k] || 0) * 0.85 + (s.flow?.[k] || 0) * 0.15;
    if (v < 0.5) delete s.flowAvg[k]; else s.flowAvg[k] = Math.round(v * 10) / 10;
  }
  s.flow = {};
  busDaily(world, s, c);
  if (c.era < 5 || !hall?.depts?.includes('traffic') || c.pending) return;
  s.signals = s.signals || {};
  const active = (s.works || []).map(id => world.buildings.get(id)).filter(b => b && b.type === 'signal' && !b.done);
  if (active.length >= 1 + Math.floor(s.pop / 400)) return;
  const gr = roadGraph(world, s);
  const cand = Object.entries(s.flowAvg)
    .filter(([k, v]) => v >= 30 && !s.signals[k] && (gr.adj.get(k)?.length || 0) >= 3 && !active.some(b => b.node === k))
    .sort((a, b) => b[1] - a[1]);
  if (!cand.length) return;
  const [node, flow] = cand[0];
  const wage = wageOfEra(c.era);
  const est = stagesTotal('signal') * wage / 8 * (1 + MAT_K.signal) * 0.14;
  if (c.treasury * 0.5 < est) return;
  const [x, z] = nodeXY(node);
  const bid = makeProject(world, s, 'signal', x, z, { node, name: `${crossName(node)} 신호등 설치` });
  world.news('build', `🚦 ${hall.name} 교통과: 하루 차량 ${Math.round(flow)}대가 지나는 ${s.name} ${crossName(node)}에 신호등을 세우기로 했다 — 표준 ${stagesTotal('signal')}인시`, { sid: s.id, country: s.country }, 1);
  const off = hall.emp.map(id => world.anns.get(id)).find(e => e && e.job?.dept === 'traffic');
  if (off) {
    lifeEvent(world, off, `${crossName(node)} 신호등 설치 계획`, '🚦');
    if (chance(0.5)) think(world, off, pick(['저 사거리는 출퇴근 때마다 아슬아슬했지.', '신호 주기는 90초로 하자.', '사고 나기 전에 세워야 한다.']));
  }
  return bid;
}
export function crossName(node) {
  const [i, j] = node.split(',').map(Number);
  const STEM = ['새', '큰', '솔', '달', '해', '별', '꽃', '샘'];
  const ew = j === 0 ? '중앙로' : `${STEM[Math.abs(j) % 8]}${j > 0 ? '남' : '북'}로`;
  const ns = i === 0 ? '관청로' : `${STEM[(Math.abs(i) + 3) % 8]}${i > 0 ? '동' : '서'}길`;
  return `${ew}·${ns} 사거리`;
}

/** 공사가 끝났을 때 */
export function projectDone(world, b) {
  const s = world.settlements.get(b.sid);
  if (!s) return;
  const days = Math.max(1, Math.round((world.clock - (b.started || world.clock)) / DAY));
  if (b.type === 'signal') {
    s.signals = s.signals || {};
    s.signals[b.node] = { t: Math.round(world.clock), off: Math.floor(rand() * SIGNAL_CYCLE) };
    world.news('build', `🚦 ${s.name} ${crossName(b.node)} 신호등이 켜졌다 — ${days}일, 실제 ${Math.round(b.mh || 0)}인시`, { sid: s.id, country: s.country }, 1);
    world.first?.(world.countries[s.country], 'signal', `첫 신호등 — ${s.name} ${crossName(b.node)}`, '🚦');
  }
  if (b.type === 'busstop' && s.bus) {
    const st = s.bus.stops.find(x => x.project === b.id);
    if (st) st.built = true;
  }
  world.dirtySettlements.add(s.id);
}
