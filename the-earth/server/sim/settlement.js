// 정착지(마을→도시)와 건물 배치. 20m 격자, 5칸마다 도로.
import { BUILDINGS, SETTLEMENT_LEVELS, ERAS } from './data.js';
import { rand, clamp } from './util.js';
import { settlementName } from './names.js';
import { Terrain, makeSite } from '../../public/js/shared/terrain.js';
import { seaNear } from '../geo.js';

// 큰 산맥 (지형을 더 험하게)
const RANGES = [[72, 98, 26, 37], [-80, -64, -50, 8], [-125, -104, 35, 62], [5, 16, 44, 48], [38, 50, 40, 44], [44, 60, 26, 38], [-9, 10, 29, 36], [126.5, 130, 35.5, 39], [136, 140, 35, 37]];
const DESERTS = [[-17, 35, 15, 30], [34, 60, 15, 31], [116, 142, -31, -20], [90, 115, 38, 46], [75, 92, 36, 42], [-118, -104, 30, 37]];
const inBox = (lon, lat, b) => lon >= b[0] && lon <= b[1] && lat >= b[2] && lat <= b[3];
export function biomeAt(lon, lat) {
  if (Math.abs(lat) > 60) return 'snow';
  if (DESERTS.some(b => inBox(lon, lat, b))) return 'desert';
  if (Math.abs(lat) < 16) return 'tropical';
  return 'temperate';
}

const terrains = new Map();
/** 정착지의 지형 (저장되지 않는 계산 결과는 캐시) */
export function terrainOf(s) {
  let t = terrains.get(s.id);
  if (!t) { t = new Terrain(s.site); terrains.set(s.id, t); }
  return t;
}

export const CELL = 20;
const BLOCK = 5;
const mod = (n, m) => ((n % m) + m) % m;
export const isRoad = (i, j) => mod(i, BLOCK) === 0 || mod(j, BLOCK) === 0;

// 중심에서 가까운 순서의 칸 목록 (한 번만 계산)
const SPIRAL = [];
{
  const R = 110;
  for (let i = -R; i <= R; i++) for (let j = -R; j <= R; j++) {
    const d = Math.hypot(i + 0.5, j + 0.5);
    if (d <= R) SPIRAL.push([i, j, d]);
  }
  SPIRAL.sort((a, b) => a[2] - b[2]);
}

export function makeSettlement(world, country, lon, lat, founder) {
  const taken = new Set([...world.settlements.values()].map(s => s.name));
  const s = {
    id: world.newId(), name: settlementName(country.culture, taken), country: country.code,
    lon, lat, founded: Math.round(world.clock), founder: founder?.id ?? null,
    level: 0, occ: {}, radius: 40, food: 40, pop: 0, buildings: [],
  };
  // 처음엔 자연뿐: 산, 언덕, 강, 바다, 숲
  s.site = makeSite(s.id * 2654435761 + Math.floor(rand() * 1e6), {
    sea: seaNear(lon, lat), biome: biomeAt(lon, lat), mountainous: RANGES.some(b => inBox(lon, lat, b)),
  });
  world.settlements.set(s.id, s);
  world.dirtySettlements.add(s.id);
  return s;
}

function fits(s, i, j, w, d) {
  const t = s.site ? terrainOf(s) : null;
  for (let a = 0; a < w; a++) for (let b = 0; b < d; b++) {
    const ci = i + a, cj = j + b;
    if (isRoad(ci, cj)) return false;
    if (s.occ[ci + ',' + cj]) return false;
    if (t && !t.cellInfo(ci, cj).ok) return false; // 물, 가파른 비탈, 높은 산에는 못 짓는다
  }
  return true;
}

/** 빈 땅 찾기. minR/maxR 는 칸 단위 거리 */
export function findLot(s, w, d, minR = 0, maxR = 200, jitter = 0) {
  let skipped = 0;
  for (const [i, j, dist] of SPIRAL) {
    if (dist < minR) continue;
    if (dist > maxR) break;
    if (jitter && rand() < jitter && skipped < 30) { skipped++; continue; }
    if (fits(s, i, j, w, d)) return [i, j];
  }
  return null;
}

/** 가장 먼 비농지 건물까지의 거리(칸) */
function townRadiusCells(world, s) {
  let r = 2;
  for (const bid of s.buildings) {
    const b = world.buildings.get(bid);
    if (!b || b.type === 'farm') continue;
    r = Math.max(r, Math.hypot(b.x, b.z) / CELL + 1);
  }
  return r;
}

export function placeBuilding(world, s, type, opts = {}) {
  const def = BUILDINGS[type];
  if (!def) return null;
  let [w, d] = def.cells;
  if (rand() < 0.5 && w !== d) [w, d] = [d, w];
  let lot;
  if (def.kind === 'farm') {
    const r = townRadiusCells(world, s);
    lot = findLot(s, w, d, r + 2, r + 30, 0.3);
  } else if (def.kind === 'civic' || type === 'market') {
    lot = findLot(s, w, d, 0, 200, 0);
  } else {
    lot = findLot(s, w, d, 1, 200, 0.15);
  }
  if (!lot) return null;
  const [i, j] = lot;
  const era = opts.era ?? 0;
  const swapped = w !== def.cells[0] || (w === d && rand() < 0.5);
  const hScale = def.kind === 'home' || def.kind === 'work' ? 0.85 + rand() * 0.35 : 1;
  const b = {
    id: world.newId(), type, sid: s.id, name: opts.name || def.name,
    x: (i + w / 2) * CELL, z: (j + d / 2) * CELL,
    w: swapped ? def.size[1] : def.size[0], d: swapped ? def.size[0] : def.size[1],
    h: Math.round(def.size[2] * hScale * 10) / 10,
    rot: 0, cells: [i, j, w, d],
    work: def.work, progress: opts.done ? def.work : 0, done: !!opts.done,
    owner: opts.owner ?? null, co: opts.co ?? null, era, res: [], budget: opts.budget || 0,
    built: opts.done ? Math.round(world.clock) : null, started: Math.round(world.clock),
  };
  for (let a = 0; a < w; a++) for (let c = 0; c < d; c++) s.occ[(i + a) + ',' + (j + c)] = b.id;
  world.buildings.set(b.id, b);
  s.buildings.push(b.id);
  s.radius = Math.max(s.radius, Math.hypot(b.x, b.z) + Math.max(b.w, b.d));
  world.dirtyBuildings.add(b.id);
  return b;
}

export function removeBuilding(world, b) {
  const s = world.settlements.get(b.sid);
  if (s) {
    const [i, j, w, d] = b.cells;
    for (let a = 0; a < w; a++) for (let c = 0; c < d; c++) delete s.occ[(i + a) + ',' + (j + c)];
    s.buildings = s.buildings.filter(id => id !== b.id);
  }
  for (const rid of b.res) { const a = world.anns.get(rid); if (a && a.home === b.id) a.home = null; }
  world.buildings.delete(b.id);
  world.removedBuildings.add(b.id);
}

/** 건설 진척. 완공되면 true */
export function addWork(world, b, hours) {
  if (b.done) return false;
  b.progress = Math.min(b.work, b.progress + hours);
  world.dirtyBuildings.add(b.id);
  if (b.progress >= b.work) {
    b.done = true;
    b.built = Math.round(world.clock);
    return true;
  }
  return false;
}

export function updateSettlementLevel(world, s) {
  const country = world.countries[s.country];
  const done = s.buildings.filter(id => world.buildings.get(id)?.done).length;
  let level = 0;
  SETTLEMENT_LEVELS.forEach((L, i) => {
    if (done >= L.minB * (s.pop < 6 ? 2 : 1) && (L.minEra ?? 0) <= country.era) level = i;
  });
  if (level !== s.level) {
    const up = level > s.level;
    s.level = level;
    world.dirtySettlements.add(s.id);
    if (up) world.news('city', `🏙️ ${s.name}이(가) ${SETTLEMENT_LEVELS[level].name}(으)로 성장했다`, { country: s.country, sid: s.id }, level >= 3 ? 3 : 2);
  }
}

export function plazaPoint(s, spread = 14) {
  const ang = rand() * Math.PI * 2, r = 4 + rand() * spread;
  return [Math.cos(ang) * r, Math.sin(ang) * r];
}

/** 마을 밖 자연 속 (물이 아닌 땅) */
export function wildPoint(s, kind = null) {
  const t = s.site ? terrainOf(s) : null;
  let best = null;
  for (let k = 0; k < 24; k++) {
    const ang = rand() * Math.PI * 2, r = s.radius + 40 + rand() * (kind === 'mountain' ? 1400 : 320);
    const x = Math.cos(ang) * r, z = Math.sin(ang) * r;
    if (!t) return [x, z];
    const p = t.sample(x, z);
    if (p.water || p.h < 0.8) continue;
    if (kind === 'forest' && t.forest(x, z, p.h) < 0.3) { best = best || [x, z]; continue; }
    if (kind === 'mountain' && p.h < 90) { if (!best || p.h > best[2]) best = [x, z, p.h]; continue; }
    if (p.h > 160 && kind !== 'mountain') continue;
    return [x, z];
  }
  return best ? [best[0], best[1]] : [s.radius + 60, 0];
}

/** 물가 (강·바다·호수) — 없으면 null */
export function shorePoint(s) {
  const t = s.site ? terrainOf(s) : null;
  if (!t || !(s.site.river || s.site.coast || s.site.lake)) return null;
  for (let k = 0; k < 16; k++) {
    const ang = rand() * Math.PI * 2;
    let prev = null;
    for (let r = 20; r < 2600; r += 25) {
      const x = Math.cos(ang) * r, z = Math.sin(ang) * r;
      const p = t.sample(x, z);
      if (p.water) { if (prev) return prev; break; }
      prev = [x, z];
    }
  }
  return null;
}

export function siteFeatures(s) {
  if (!s.site) return {};
  const st = s.site;
  return { sea: !!st.coast, river: !!st.river, lake: !!st.lake, mountain: st.mtn > 420, biome: st.biome };
}

export function doorPoint(b) {
  const side = rand() < 0.5 ? 1 : -1;
  return [b.x + (rand() - 0.5) * b.w * 0.6, b.z + side * (b.d / 2 + 2 + rand() * 2)];
}

export function eraOf(world, s) {
  return world.countries[s.country]?.era ?? 0;
}

export function prodMul(world, s) {
  return ERAS[clamp(eraOf(world, s), 0, 7)].prod;
}

/** 예전 저장 파일처럼 지형 정보가 없는 정착지에 지형을 만든다 */
export function ensureSite(s) {
  if (s.site) return;
  s.site = makeSite(s.id * 2654435761 + 12345, { sea: seaNear(s.lon, s.lat), biome: biomeAt(s.lon, s.lat), mountainous: RANGES.some(b => inBox(s.lon, s.lat, b)) });
}
