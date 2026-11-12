// 지형: 산·언덕·강·바다·숲. 서버(건물 배치)와 화면(렌더링)이 똑같은 결과를 내도록 결정적이다.
// 좌표는 정착지 중심 기준 미터 (x = 동쪽, z = 남쪽), 높이 0 = 해수면.

export const TERRAIN_R = 4200;

function h2(ix, iy, s) {
  let h = Math.imul(ix | 0, 374761393) ^ Math.imul(iy | 0, 668265263) ^ Math.imul(s | 0, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
function vn(x, y, s) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  const a = h2(ix, iy, s), b = h2(ix + 1, iy, s), c = h2(ix, iy + 1, s), d = h2(ix + 1, iy + 1, s);
  return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
}
function fbm(x, y, s, oct = 5) {
  let v = 0, a = 0.5, f = 1, n = 0;
  for (let i = 0; i < oct; i++) { v += a * vn(x * f, y * f, s + i * 17); n += a; a *= 0.5; f *= 2.03; }
  return v / n;
}
function ridge(x, y, s, oct = 5) {
  let v = 0, a = 0.5, f = 1, n = 0;
  for (let i = 0; i < oct; i++) { let r = 1 - Math.abs(vn(x * f, y * f, s + i * 31) * 2 - 1); r *= r; v += a * r; n += a; a *= 0.5; f *= 2.1; }
  return v / n;
}
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/** 정착지가 생길 때 한 번 정하는 땅의 성격 */
export function makeSite(seed, opts = {}) {
  let s = seed >>> 0;
  const r = () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  const site = { seed: Math.floor(r() * 1e6), mtn: 120 + r() * 520, mdir: r() * Math.PI * 2, biome: opts.biome || 'temperate' };
  if (opts.mountainous) site.mtn += 350;
  if (opts.sea) site.coast = { a: opts.sea.angle, d: Math.round(650 + Math.min(1, opts.sea.km / 30) * 1700 + r() * 300) };
  if (opts.river ?? r() < 0.72) site.river = { a: r() * Math.PI, off: Math.round((r() < 0.5 ? 1 : -1) * (170 + r() * 300)), w: Math.round(14 + r() * 22), m: Math.round(90 + r() * 200), p: r() * 6.28, p2: r() * 6.28 };
  if (!site.river && !site.coast && r() < 0.6) site.lake = { x: Math.round((r() - 0.5) * 1600), z: Math.round((r() - 0.5) * 1600), rad: Math.round(160 + r() * 260) };
  if (site.lake && Math.hypot(site.lake.x, site.lake.z) < site.lake.rad + 260) site.lake.x += Math.sign(site.lake.x || 1) * (site.lake.rad + 300);
  return site;
}

export class Terrain {
  constructor(site) {
    this.s = site || { seed: 1, mtn: 200, mdir: 0 };
    const rv = this.s.river;
    if (rv) { this.rc = Math.cos(rv.a); this.rs = Math.sin(rv.a); }
    this.cache = new Map();
  }

  /** 높이(m)와 물의 종류 */
  sample(x, z) {
    const s = this.s, r = Math.hypot(x, z);
    let h = 3 + fbm(x / 1100, z / 1100, s.seed) * 42 * smooth(150, 950, r) + (fbm(x / 240, z / 240, s.seed + 5, 3) - 0.5) * 6 * smooth(60, 380, r);
    // 산맥: 중심에서 멀어질수록, 한쪽 방향으로 더 높게
    const dirBias = (x * Math.cos(s.mdir) + z * Math.sin(s.mdir)) / TERRAIN_R;
    const mt = Math.pow(ridge(x / 2600, z / 2600, s.seed + 9, 5), 2.1);
    h += mt * s.mtn * smooth(850, 2500, r * (1 + dirBias * 0.45));
    h *= 1 - smooth(TERRAIN_R * 0.8, TERRAIN_R, r);
    let water = null;
    // 바다
    if (s.coast) {
      const proj = x * Math.cos(s.coast.a) + z * Math.sin(s.coast.a) + (fbm(x / 650, z / 650, s.seed + 21) - 0.5) * 760;
      const over = proj - s.coast.d;
      if (over > -260) {
        const t = smooth(-260, 40, over);
        const sea = -1.5 - Math.max(0, over) * 0.06;
        h = h * (1 - t) + sea * t;
        if (h < 0) water = 'sea';
      }
    }
    // 강: 굽이치는 물길과 그 주변의 넓은 골짜기
    if (s.river) {
      const rv = s.river;
      const u = x * this.rc + z * this.rs, v = -x * this.rs + z * this.rc;
      const c = rv.off + rv.m * Math.sin(u / 950 + rv.p) + rv.m * 0.35 * Math.sin(u / 330 + rv.p2);
      const d = Math.abs(v - c);
      h = h * (0.22 + 0.78 * smooth(rv.w, rv.w + 650, d));
      if (d < rv.w + 14) {
        const t = smooth(rv.w * 0.55, rv.w + 14, d);
        h = -2.4 * (1 - t) + h * t;
        if (h < 0 && !water) water = 'river';
      }
    }
    if (s.lake) {
      const d = Math.hypot(x - s.lake.x, z - s.lake.z) + (fbm(x / 300, z / 300, s.seed + 33) - 0.5) * 120;
      if (d < s.lake.rad + 40) { const t = smooth(s.lake.rad - 30, s.lake.rad + 40, d); h = -2 * (1 - t) + h * t; if (h < 0 && !water) water = 'lake'; }
    }
    return { h, water };
  }
  height(x, z) { return this.sample(x, z).h; }

  /** 숲의 밀도 0..1 */
  forest(x, z, h = this.height(x, z)) {
    if (h < 1.2 || h > 520) return 0;
    const b = this.s.biome;
    const base = b === 'desert' ? -0.35 : b === 'snow' ? -0.15 : b === 'tropical' ? 0.12 : 0;
    const f = fbm(x / 520, z / 520, this.s.seed + 77, 4) + base + (h > 60 ? 0.08 : 0);
    return Math.max(0, Math.min(1, (f - 0.5) * 3.2));
  }

  /** 20m 칸에 건물을 지을 수 있는가 (물이 아니고, 너무 가파르거나 높지 않다) */
  cellInfo(i, j, cell = 20) {
    const key = i + ',' + j;
    let c = this.cache.get(key);
    if (c) return c;
    const x0 = i * cell, z0 = j * cell;
    let min = Infinity, max = -Infinity, wet = false;
    for (const [dx, dz] of [[0, 0], [cell, 0], [0, cell], [cell, cell], [cell / 2, cell / 2]]) {
      const p = this.sample(x0 + dx, z0 + dz);
      min = Math.min(min, p.h); max = Math.max(max, p.h);
      if (p.water || p.h < 0.6) wet = true;
    }
    c = { ok: !wet && max - min < 4.5 && max < 160, h: (min + max) / 2, wet };
    if (this.cache.size > 60000) this.cache.clear();
    this.cache.set(key, c);
    return c;
  }

  features() {
    const s = this.s;
    let mountainous = s.mtn > 420;
    return { sea: !!s.coast, river: !!s.river, lake: !!s.lake, mountain: mountainous, hills: !mountainous };
  }
}
