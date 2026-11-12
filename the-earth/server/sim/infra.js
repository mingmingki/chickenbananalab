// 도로와 관청(시청)의 부서.
// 도로는 등급이 있다: 흙길 → 자갈길 → 돌길 → 포장도로 → 대로.
// 사람이 다니면 흙길이 생기고, 그 다음부터는 관청에 도시환경과(대로는 교통과)가 생겨 공사를 계획해야 좋아진다.
// 공사는 건설회사(없으면 일용직·이웃)가 구간마다 몇 달씩 걸려 해낸다.
import { chance, pick, ga, ro } from './util.js';
import { lifeEvent, remember, think } from './ann.js';
import { assignContractor } from './economy.js';
import { ROAD_GRADES, DEPTS, maxRoadGrade, deptName } from './data.js';
export { ROAD_GRADES, DEPTS, maxRoadGrade, deptName };

const CELL = 20, BLOCK = 5, BW = BLOCK * CELL, OFF = CELL / 2; // settlement.js 와 같은 격자

// 도로 구간: 'h,bi,bj' = (bi,bj) 모서리에서 동쪽으로 한 블록, 'v,bi,bj' = 남쪽으로 한 블록
export function segCenter(key) {
  const [o, bi, bj] = key.split(',');
  const x0 = +bi * BW + OFF, z0 = +bj * BW + OFF;
  return o === 'h' ? [x0 + BW / 2, z0] : [x0, z0 + BW / 2];
}
const blockEdges = (bi, bj) => [`h,${bi},${bj}`, `h,${bi},${bj + 1}`, `v,${bi},${bj}`, `v,${bi + 1},${bj}`];

/** 길 이름: 중심을 지나는 길이 중앙로·관청로, 나머지는 방향과 순서로 */
const STEMS = ['새', '큰', '솔', '달', '해', '별', '꽃', '샘'];
export function roadName(key) {
  const [o, bi, bj] = key.split(',').map((v, i) => (i ? +v : v));
  if (o === 'h') return bj === 0 ? '중앙로' : `${STEMS[Math.abs(bj) % STEMS.length]}${bj > 0 ? '남' : '북'}로`;
  return bi === 0 ? '관청로' : `${STEMS[(Math.abs(bi) + 3) % STEMS.length]}${bi > 0 ? '동' : '서'}길`;
}

/** 개발된 블록 둘레의 도로 구간에 흙길을 낸다 (사람이 다니면 길이 된다) */
export function syncRoads(world, s) {
  s.roads = s.roads || {};
  const blocks = new Set(['0,0', '-1,-1', '-1,0', '0,-1']);
  for (const id of s.buildings) {
    const b = world.buildings.get(id);
    if (!b || b.type === 'farm' || b.infra) continue;
    blocks.add(Math.floor(Math.floor(b.x / CELL) / BLOCK) + ',' + Math.floor(Math.floor(b.z / CELL) / BLOCK));
  }
  let added = 0;
  for (const k of blocks) {
    const [bi, bj] = k.split(',').map(Number);
    for (const e of blockEdges(bi, bj)) if (s.roads[e] == null) { s.roads[e] = 0; added++; }
  }
  if (added) world.dirtySettlements.add(s.id);
}

/** 관청 부서 갱신. 새 부서가 생기면 공무원 한 명이 발령받는다 */
export function hallDepts(world, s, hall) {
  const c = world.countries[s.country];
  hall.depts = hall.depts || [];
  const staff = hall.emp.length;
  for (const d of DEPTS) {
    if (hall.depts.includes(d.key) || c.era < d.era || staff < d.staff) continue;
    hall.depts.push(d.key);
    world.news('gov', `🏛️ ${hall.name}에 ${ga(d.name)} 생겼다 — ${d.does}`, { sid: s.id, country: s.country }, 1);
    const off = hall.emp.map(id => world.anns.get(id)).find(e => e && e.job?.id === 'official' && !e.job.dept);
    if (off) {
      off.job.dept = d.key;
      lifeEvent(world, off, `${hall.name} ${d.name} 발령`, '🗂️');
      remember(world, off, `새로 생긴 ${ro(d.name)} 발령이 났다`, 5, 0.5, [], 'work');
    }
  }
  // 부서 없는 공무원은 부서를 정해 준다
  for (const id of hall.emp) {
    const e = world.anns.get(id);
    if (e && e.job?.id === 'official' && !e.job.dept && hall.depts.length) e.job.dept = pick(hall.depts);
  }
}


/** 도시환경과·교통과: 도로 공사를 계획한다 */
export function planRoadWorks(world, s, hall, wage) {
  const c = world.countries[s.country];
  if (!hall.depts?.includes('urban') || c.pending) return;
  s.works = (s.works || []).filter(id => { const b = world.buildings.get(id); return b && !b.done; });
  if (s.works.length >= 1 + Math.floor(s.pop / 250)) return;
  const top = Math.min(maxRoadGrade(c.era), hall.depts.includes('traffic') ? 4 : 3);
  // 중심에서 가까운 구간부터, 낮은 등급부터
  const cands = Object.entries(s.roads || {}).filter(([k, g]) => g < top && !inWork(world, s, k))
    .map(([k, g]) => { const [x, z] = segCenter(k); return { k, g, d: Math.hypot(x, z) }; })
    .sort((a, b) => a.g - b.g || a.d - b.d);
  if (!cands.length) return;
  const g0 = cands[0].g, target = top;
  // 같은 길(같은 줄) 위주로 몇 구간을 묶는다
  const first = cands[0].k, name = roadName(first);
  let segs = cands.filter(x => x.g === g0 && roadName(x.k) === name).slice(0, 5).map(x => x.k);
  if (segs.length < 3) segs = [...new Set([...segs, ...cands.filter(x => x.g === g0).slice(0, 4).map(x => x.k)])].slice(0, 5);
  let perSeg = 0;
  for (let g = g0 + 1; g <= target; g++) perSeg += ROAD_GRADES[g].work * (g === target ? 1 : 0.35);
  perSeg = Math.round(perSeg);
  // 예산이 빠듯하면 구간을 줄여서라도 시작한다 (나라 곳간의 60%까지만 쓴다)
  const segCost = perSeg * wage / 8 * 0.12;
  const n = Math.min(segs.length, Math.floor(Math.max(0, c.treasury) * 0.6 / segCost));
  if (n < 1) return;
  segs = segs.slice(0, n);
  const work = perSeg * segs.length;
  const cost = Math.round(segCost * segs.length);
  c.treasury -= cost;
  const [x, z] = segCenter(segs[0]);
  const verb = ROAD_GRADES[target].verb;
  const b = {
    id: world.newId(), type: 'roadwork', sid: s.id, name: `${name} ${verb}`, infra: true,
    x, z, w: 10, d: 10, h: 0, rot: 0, cells: [0, 0, 0, 0], done: false, progress: 0, work, budget: cost,
    started: Math.round(world.clock), era: c.era, owner: null, co: null, res: [], segs, grade: target, perSeg, segDone: 0,
  };
  world.buildings.set(b.id, b);
  s.works.push(b.id);
  world.dirtyBuildings.add(b.id);
  assignContractor(world, b);
  world.news('build', `🚧 ${hall.name} ${deptName(target >= 4 ? 'traffic' : 'urban')}: '${s.name} ${name}' ${ROAD_GRADES[g0].name} ${segs.length}구간을 ${ro(ROAD_GRADES[target].name)} — ${verb} 공사 시작`, { sid: s.id, country: s.country }, 1);
  const planner = hall.emp.map(id => world.anns.get(id)).find(e => e && e.job?.dept === (target >= 4 ? 'traffic' : 'urban'));
  if (planner) {
    lifeEvent(world, planner, `'${name} ${verb}' 공사 계획을 맡았다`, '📐');
    if (chance(0.5)) think(world, planner, pick(['길이 좋아지면 마을이 달라질 거야.', '예산 안에서 끝내야 한다.', '비 오는 날 진흙탕 길은 이제 그만.']));
  }
  return b;
}
function inWork(world, s, key) {
  for (const id of s.works || []) { const b = world.buildings.get(id); if (b && !b.done && b.segs.includes(key)) return true; }
  return false;
}

/** 공사 진척에 따라 구간이 하나씩 새 길로 바뀐다. 일하는 곳(좌표)도 다음 구간으로 옮겨 간다 */
export function roadProgress(world, b) {
  const s = world.settlements.get(b.sid);
  if (!s) return;
  const n = Math.min(b.segs.length, Math.floor(b.progress / b.perSeg + 1e-6));
  if (n > (b.segDone || 0)) {
    for (let i = b.segDone || 0; i < n; i++) s.roads[b.segs[i]] = Math.max(s.roads[b.segs[i]] || 0, b.grade);
    b.segDone = n;
    world.dirtySettlements.add(s.id);
    if (n < b.segs.length) { [b.x, b.z] = segCenter(b.segs[n]); }
  }
}
