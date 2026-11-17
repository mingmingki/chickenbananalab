// 살림집 설계: 집마다 번호로 '설계도'를 뽑아 평면·층·지붕·재료·색·곁채·마루·굴뚝·장독대를 조합한다.
// 지붕은 평면을 그대로 따른다 (네모난 집에는 네모난 지붕: 맞배·우진각·팔작·모임·외쪽·꺾임·평지붕, 둥근 집에만 원뿔 지붕).
// 조합 수: 초가 약 2만, 기와집 약 1만, 벽돌집 수십만, 연립 수만 가지.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { wallTexture, wallBox, wallCyl } from './buildings.js';

const hash = n => { let h = (n * 2654435761) >>> 0; h = (h ^ (h >>> 15)) >>> 0; return (h % 1000) / 1000; };
const pickOf = (arr, r) => arr[Math.min(arr.length - 1, Math.floor(r * arr.length))];

// ── 재료 빛깔 ──
const PAL = {
  mud: ['#cdb38a', '#d9c4a0', '#c49a6c', '#e3d6bd', '#b98d62', '#d6b48c', '#e9dfc9', '#efe7d6', '#c8a57a'],
  thatch: ['#c9a54e', '#d4b25c', '#b9974f', '#a8874a', '#9c8456', '#8a7a5e', '#b59a5e', '#7d6b4c'],
  hanokWall: ['#efe8d8', '#e8e0cf', '#f2ece0', '#e9dcc4', '#ddd2bd'],
  giwa: ['#3f474f', '#4a5560', '#2f3438', '#55606b', '#5a5048', '#46505a'],
  brick: ['#9c5a45', '#a8664c', '#8a4f3c', '#b87355', '#7d4a3a', '#a0705a', '#93603f', '#c9a86a', '#b5926a'],
  stucco: ['#e8d8b8', '#d9e3d0', '#e7cfc5', '#efe6d2', '#d6dde6', '#f1e3c4', '#e2d4c0', '#cfd8c4'],
  roof3: ['#4a5058', '#a9553a', '#b8673f', '#b33a2e', '#3f6fa8', '#4f8a5b', '#5b4636', '#2e3136', '#7a3b2e', '#2f6f78'],
  town: ['#c9b9a6', '#e3dccf', '#d8cbb5', '#cfd4d8', '#e6d3c0', '#d9c7b8', '#c8d0c0', '#eadfca', '#d4c0a8'],
  roof4: ['#3f6fa8', '#b33a2e', '#4f8a5b', '#5a5f66', '#2f6f78', '#8a4a3a'],
  timber: ['#5a3d22', '#6b4a2c', '#4a3322', '#7a5434', '#3f2d1e'],
  door: ['#5a3d22', '#7a3b2e', '#2f4a5a', '#3d5a3a', '#6b4a2c', '#8a2f2f', '#2e3b48', '#c9b89a'],
};

/** 집 한 채의 설계도 (b.id 로 늘 같은 집) */
export function homeDesign(b) {
  const R = k => hash(b.id * 7919 + k * 104729);
  const t = b.type;
  if (t === 'hut') return { kind: 'hut', round: R(1) < 0.6, wall: pickOf(PAL.mud, R(2)), roof: pickOf(PAL.thatch, R(3)) };
  if (t === 'cottage') {
    return {
      kind: 'cottage', style: R(1) < 0.7 ? 'mud' : 'timber',
      plan: pickOf(['I', 'I', 'L', 'L', 'annex', 'two'], R(2)),
      roof: pickOf(['thatchHip', 'thatchHip', 'thatchGable', 'thatchHalf'], R(3)),
      wall: pickOf(PAL.mud, R(4)), roofCol: pickOf(PAL.thatch, R(5)), timber: pickOf(PAL.timber, R(6)),
      maru: R(7) < 0.65, chimney: pickOf(['side', 'back', 'none'], R(8)), jars: R(9) < 0.55, wood: R(10) < 0.4,
      pitch: 0.42 + R(11) * 0.16, mirror: R(12) < 0.5,
    };
  }
  if (t === 'house') {
    return {
      kind: 'hanok', style: 'hanok',
      plan: pickOf(['I', 'L', 'L', 'U', 'annex'], R(2)),
      roof: pickOf(['hanokGable', 'hanokHip', 'hanokHalf', 'hanokHalf'], R(3)),
      wall: pickOf(PAL.hanokWall, R(4)), roofCol: pickOf(PAL.giwa, R(5)), timber: pickOf(PAL.timber, R(6)),
      maru: R(7) < 0.8, chimney: pickOf(['side', 'back'], R(8)), jars: R(9) < 0.7, base: 0.5 + R(10) * 0.5,
      pitch: 0.5 + R(11) * 0.12, mirror: R(12) < 0.5,
    };
  }
  if (t === 'brickhouse') {
    const storeys = pickOf([1, 1.5, 2, 2], R(1));
    return {
      kind: 'brick', style: R(13) < 0.62 ? 'brick' : 'concrete', storeys,
      plan: pickOf(['I', 'L', 'T', 'offset', 'annex', 'I'], R(2)),
      roof: pickOf(['gable', 'hip', 'cross', 'gambrel', 'saltbox', 'halfhip', 'pyramid', 'shedPair'], R(3)),
      wall: R(13) < 0.62 ? pickOf(PAL.brick, R(4)) : pickOf(PAL.stucco, R(4)), roofCol: pickOf(PAL.roof3, R(5)), trim: pickOf(['#f2ede4', '#e8e0d0', '#3b3f44', '#ffffff', '#d9cbb0'], R(6)),
      porch: pickOf(['none', 'front', 'corner', 'full'], R(7)), chimney: pickOf(['end', 'mid', 'none', 'end'], R(8)), dormers: storeys === 1.5,
      bay: R(9) < 0.35, door: pickOf(PAL.door, R(10)), pitch: 0.35 + R(11) * 0.35, mirror: R(12) < 0.5, fence: R(14) < 0.5,
    };
  }
  if (t === 'townhouse') {
    return {
      kind: 'town', style: 'concrete', storeys: 3 + Math.floor(R(1) * 2),
      plan: pickOf(['I', 'L', 'offset', 'I', 'step'], R(2)),
      roof: pickOf(['flat', 'flat', 'hip', 'mansard', 'gable'], R(3)),
      wall: pickOf(PAL.town, R(4)), roofCol: pickOf(PAL.roof4, R(5)), trim: pickOf(['#f2f2ef', '#d7dadf', '#5a5f66', '#c9b9a6'], R(6)),
      balcony: pickOf(['strip', 'boxes', 'none', 'strip'], R(7)), stair: R(8) < 0.6, door: pickOf(PAL.door, R(10)), pitch: 0.22 + R(11) * 0.12, mirror: R(12) < 0.5,
    };
  }
  return null;
}

// ── 지붕 기하 ──
// 바닥 y=0 이 처마 높이, 가운데 (0,0). x 가 용마루 방향이 되게 (w ≥ d). prof: 처마에서 용마루까지의 높이 곡선.
const PROF = {
  straight: t => t,
  thatch: t => 1 - Math.pow(1 - t, 1.8),                     // 처마는 가파르고 위는 둥글게 (초가)
  hanok: t => Math.pow(t, 1.45),                              // 처마는 완만하고 위로 갈수록 가파르게 (기와 곡선)
  gambrel: t => (t < 0.42 ? t / 0.42 * 0.68 : 0.68 + (t - 0.42) / 0.58 * 0.32), // 꺾인 지붕
  mansard: t => (t < 0.3 ? t / 0.3 * 0.8 : 0.8 + (t - 0.3) / 0.7 * 0.2),
};
class Mesher {
  constructor() { this.p = []; this.c = []; }
  tri(a, b, c, col) { this.p.push(...a, ...b, ...c); for (let i = 0; i < 3; i++) this.c.push(col.r, col.g, col.b); }
  quad(a, b, c, d, col) { this.tri(a, b, c, col); this.tri(a, c, d, col); }
  geo() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.p, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(this.c, 3));
    g.computeVertexNormals();
    return g;
  }
}
/**
 * 지붕 한 덩어리를 그린다.
 * o: { w, d, h, kind: 'gable'|'hip'|'half'|'pyramid'|'shed'|'saltbox', prof, ov, col, endCol, fasciaH, ridge: {col, w}, upturn, steps, x, y, z, rot }
 */
function roof(M, o) {
  let W = o.w, D = o.d, rot = o.rot || 0;
  if (D > W && o.kind !== 'shed') { [W, D] = [D, W]; rot += Math.PI / 2; }
  const ov = o.ov ?? 0.5, Wp = W + ov * 2, Dp = D + ov * 2, h = o.h;
  const prof = PROF[o.prof || 'straight'], N = o.steps || 6;
  const cs = Math.cos(rot), sn = Math.sin(rot);
  const P = (x, y, z) => [o.x + x * cs + z * sn, o.y + y, o.z - x * sn + z * cs];
  const col = new THREE.Color(o.col), endCol = new THREE.Color(o.endCol || o.col);
  const y0 = -ov * h / (Dp / 2) * 0.55;    // 처마는 벽보다 조금 아래로
  const band = k => col.clone().multiplyScalar(k % 2 ? 0.93 : 1.0);   // 기와·이엉 줄무늬
  if (o.kind === 'shed') {                  // 외쪽 지붕: 앞이 낮고 뒤가 높다 (z+ 가 앞)
    const a = P(-Wp / 2, y0, Dp / 2), b = P(Wp / 2, y0, Dp / 2), c = P(Wp / 2, h, -Dp / 2), d = P(-Wp / 2, h, -Dp / 2);
    M.quad(a, b, c, d, col);
    M.tri(P(W / 2, 0, D / 2), P(W / 2, h, -D / 2), P(W / 2, 0, -D / 2), endCol);
    M.tri(P(-W / 2, 0, D / 2), P(-W / 2, 0, -D / 2), P(-W / 2, h, -D / 2), endCol);
    M.quad(P(-W / 2, 0, -D / 2), P(W / 2, 0, -D / 2), P(W / 2, h, -D / 2), P(-W / 2, h, -D / 2), endCol);
    const fh = o.fasciaH ?? 0.18;
    M.quad(P(-Wp / 2, y0 - fh, Dp / 2), P(Wp / 2, y0 - fh, Dp / 2), b, a, col.clone().multiplyScalar(0.7));
    return;
  }
  const xinset = t => {
    if (o.kind === 'gable' || o.kind === 'saltbox') return 0;
    if (o.kind === 'half') return Math.min(t, 0.5) * Dp / 2;
    return Math.min(t * Dp / 2, Wp / 2 - 0.05);       // hip · pyramid
  };
  // 맞배 비대칭(솔트박스): 용마루를 뒤로 민다
  const zr = o.kind === 'saltbox' ? -Dp * 0.18 : 0;
  const ring = t => {
    const xi = xinset(t), y = y0 + (h - y0) * prof(t);
    const zf = Dp / 2 - t * (Dp / 2 - zr), zb = -Dp / 2 + t * (Dp / 2 + zr);   // 앞·뒤 처마선이 용마루로 모인다
    return { xl: -Wp / 2 + xi, xr: Wp / 2 - xi, zf, zb, y };
  };
  const R = [];
  for (let k = 0; k <= N; k++) R.push(ring(k / N));
  const up = o.upturn || 0, SEG = up ? 6 : 1;
  // 처마 귀가 들린다: 처마 줄(0)과 그 위 줄(1)에서, 가운데서 멀수록 높이 (세제곱)
  const liftK = k => (k === 0 ? up : k === 1 ? up * 0.35 : 0);
  const liftAt = (k, u) => liftK(k) * Math.pow(Math.abs(2 * u - 1), 3);       // u: 처마선 위 0..1
  const lerp = (a, b, u) => a + (b - a) * u;
  for (let k = 0; k < N; k++) {
    const a = R[k], b = R[k + 1];
    const c0 = band(k);
    for (let j = 0; j < SEG; j++) {
      const u0 = j / SEG, u1 = (j + 1) / SEG;
      // 앞면 (z+) · 뒷면 (z-)
      for (const [za, zb, s] of [[a.zf, b.zf, 1], [a.zb, b.zb, -1]]) {
        const p1 = P(lerp(a.xl, a.xr, u0), a.y + liftAt(k, u0), za), p2 = P(lerp(a.xl, a.xr, u1), a.y + liftAt(k, u1), za);
        const p3 = P(lerp(b.xl, b.xr, u1), b.y + liftAt(k + 1, u1), zb), p4 = P(lerp(b.xl, b.xr, u0), b.y + liftAt(k + 1, u0), zb);
        if (s > 0) M.quad(p1, p2, p3, p4, c0); else M.quad(p2, p1, p4, p3, c0);
      }
    }
    // 옆면: 안쪽으로 들어오면 지붕(귀가 들린다), 그대로면 벽면 위 박공벽
    const slopeX = Math.abs(b.xr - a.xr) > 1e-4;
    if (slopeX) {
      for (let j = 0; j < SEG; j++) {
        const u0 = j / SEG, u1 = (j + 1) / SEG;
        for (const [xa, xb, s] of [[a.xr, b.xr, 1], [a.xl, b.xl, -1]]) {
          const p1 = P(xa, a.y + liftAt(k, u0), lerp(a.zf, a.zb, u0)), p2 = P(xa, a.y + liftAt(k, u1), lerp(a.zf, a.zb, u1));
          const p3 = P(xb, b.y + liftAt(k + 1, u1), lerp(b.zf, b.zb, u1)), p4 = P(xb, b.y + liftAt(k + 1, u0), lerp(b.zf, b.zb, u0));
          if (s > 0) M.quad(p1, p2, p3, p4, c0); else M.quad(p2, p1, p4, p3, c0);
        }
      }
    } else {
      // 박공벽: 벽면(처마만큼 안쪽) 위 삼각형 조각
      const zfa = Math.min(a.zf, D / 2), zba = Math.max(a.zb, -D / 2), zfb = Math.min(b.zf, D / 2), zbb = Math.max(b.zb, -D / 2);
      const ya = Math.max(a.y, 0), yb = b.y;
      if (zfa > zba) for (const xx of [W / 2, -W / 2]) M.quad(P(xx, ya, zfa), P(xx, ya, zba), P(xx, yb, Math.max(zbb, Math.min(zfb, zba))), P(xx, yb, Math.min(zfb, Math.max(zbb, zfa))), endCol);
    }
  }
  // 처마 두께 (이엉은 두툼하게) — 처마선을 따라, 들린 귀도 함께
  const fh = o.fasciaH ?? 0.18, r0 = R[0], fc = col.clone().multiplyScalar(0.72);
  const edge = (x1, z1, x2, z2) => {
    for (let j = 0; j < SEG; j++) {
      const u0 = j / SEG, u1 = (j + 1) / SEG;
      const xa = lerp(x1, x2, u0), za = lerp(z1, z2, u0), xb = lerp(x1, x2, u1), zb = lerp(z1, z2, u1);
      const ya = r0.y + liftAt(0, u0), yb = r0.y + liftAt(0, u1);
      M.quad(P(xa, ya - fh, za), P(xb, yb - fh, zb), P(xb, yb, zb), P(xa, ya, za), fc);
    }
  };
  edge(r0.xl, r0.zf, r0.xr, r0.zf); edge(r0.xr, r0.zb, r0.xl, r0.zb);
  if (o.kind !== 'gable' && o.kind !== 'saltbox') { edge(r0.xr, r0.zf, r0.xr, r0.zb); edge(r0.xl, r0.zb, r0.xl, r0.zf); }
  // 용마루
  const top = R[N];
  if (o.ridge && top.xr - top.xl > 0.3) {
    const rc = new THREE.Color(o.ridge.col), rw = o.ridge.w, rh = o.ridge.h ?? rw * 0.7;
    const y = top.y, z = top.zf;
    const L = top.xl - (o.kind === 'gable' ? 0 : 0.1), Rr = top.xr + (o.kind === 'gable' ? 0 : 0.1);
    const v = (x, yy, zz) => P(x, yy, zz);
    M.quad(v(L, y - 0.05, z + rw / 2), v(Rr, y - 0.05, z + rw / 2), v(Rr, y + rh, z + rw / 2), v(L, y + rh, z + rw / 2), rc);
    M.quad(v(Rr, y - 0.05, z - rw / 2), v(L, y - 0.05, z - rw / 2), v(L, y + rh, z - rw / 2), v(Rr, y + rh, z - rw / 2), rc);
    M.quad(v(L, y + rh, z + rw / 2), v(Rr, y + rh, z + rw / 2), v(Rr, y + rh, z - rw / 2), v(L, y + rh, z - rw / 2), rc);
    if (o.ridge.horns) for (const sx of [L, Rr]) {   // 기와 용마루 끝: 살짝 솟은 망와
      const d = sx === L ? -1 : 1;
      M.quad(v(sx, y + rh, z + rw / 2), v(sx + d * 0.5, y + rh + 0.45, z + rw / 2), v(sx + d * 0.5, y + rh + 0.45, z - rw / 2), v(sx, y + rh, z - rw / 2), rc);
    }
  }
  return { ridgeY: o.y + top.y, x0: top.xl, x1: top.xr };
}

/** 평지붕: 슬래브 + 난간 */
function flatRoof(parts, x, y, z, w, d, col, parapet = 0.9) {
  box(parts, w + 0.4, 0.35, d + 0.4, x, y, z, col);
  if (parapet) {
    for (const s of [-1, 1]) { box(parts, w + 0.4, parapet, 0.25, x, y + 0.35, z + s * (d / 2 + 0.08), col); box(parts, 0.25, parapet, d + 0.4, x + s * (w / 2 + 0.08), y + 0.35, z, col); }
  }
}

// ── 작은 부재 (한 메시로 합친다) ──
function paint(g, c) {
  g.deleteAttribute('uv');
  const n = g.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return g.index ? g.toNonIndexed() : g;
}
function box(parts, w, h, d, x, y, z, col, ry = 0) {
  const g = new THREE.BoxGeometry(w, h, d); if (ry) g.rotateY(ry); g.translate(x, y + h / 2, z);
  parts.push(paint(g, new THREE.Color(col)));
}
function cyl(parts, r, h, x, y, z, col, seg = 8, r2 = r) {
  const g = new THREE.CylinderGeometry(r2, r, h, seg); g.translate(x, y + h / 2, z);
  parts.push(paint(g, new THREE.Color(col)));
}

/** 평면: 몸채들 [{x, z, w, d, h, main}] — 앞은 +z */
function planMasses(ds, W, D, H) {
  const m = ds.mirror ? -1 : 1;
  switch (ds.plan) {
    case 'L': return [{ x: 0, z: -D * 0.2, w: W, d: D * 0.6, h: H, main: true }, { x: m * -W * 0.3, z: D * 0.25, w: W * 0.4, d: D * 0.5, h: H * 0.94 }];
    case 'T': return [{ x: 0, z: D * 0.18, w: W, d: D * 0.6, h: H, main: true }, { x: 0, z: -D * 0.3, w: W * 0.42, d: D * 0.4, h: H * 0.9 }];
    case 'U': return [{ x: 0, z: -D * 0.26, w: W, d: D * 0.48, h: H, main: true }, { x: -W * 0.36, z: D * 0.24, w: W * 0.28, d: D * 0.52, h: H * 0.9 }, { x: W * 0.36, z: D * 0.24, w: W * 0.28, d: D * 0.52, h: H * 0.9 }];
    case 'offset': return [{ x: m * -W * 0.2, z: -D * 0.08, w: W * 0.6, d: D * 0.84, h: H, main: true }, { x: m * W * 0.3, z: D * 0.08, w: W * 0.4, d: D * 0.7, h: H * 0.78 }];
    case 'annex': return [{ x: m * -W * 0.12, z: 0, w: W * 0.76, d: D * 0.9, h: H, main: true }, { x: m * W * 0.38, z: D * 0.05, w: W * 0.24, d: D * 0.6, h: H * 0.72, lean: true }];
    case 'two': return [{ x: 0, z: -D * 0.18, w: W, d: D * 0.55, h: H, main: true }, { x: m * W * 0.28, z: D * 0.36, w: W * 0.42, d: D * 0.28, h: H * 0.8, apart: true }];
    case 'step': return [{ x: m * -W * 0.18, z: 0, w: W * 0.64, d: D, h: H, main: true }, { x: m * W * 0.32, z: D * 0.08, w: W * 0.36, d: D * 0.84, h: H * 0.72 }];
    default: return [{ x: 0, z: 0, w: W, d: D * 0.92, h: H, main: true }];
  }
}

/**
 * 살림집 한 채를 grp 에 그린다. 반환: { front: {x, z, w}, masses } (마당·간판 위치용)
 */
export function buildHome(cx, b, def, grp, { snowy, cold, hex }) {
  const ds = homeDesign(b);
  if (!ds) return null;
  const W = b.w, D = b.d, H = b.h;
  const parts = [], roofM = new Mesher();
  let main = { x: 0, z: 0, w: W, d: D }, fx = 0;
  const S = c => snowy(c);                     // 지붕은 눈이 쌓인다
  const wallOf = (style, color) => {
    const tex = wallTexture(style, color);
    return { tex, mat: cx.wallMat(style, color, tex) };
  };
  const addWall = (wm, w, h, d, x, y, z, ry = 0) => {
    const m = new THREE.Mesh(wallBox(w, h, d, wm.tex.tw, wm.tex.th), wm.mat);
    m.position.set(x, y, z); m.rotation.y = ry; m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
  };

  // ── 움집: 둥근 움집(원뿔 지붕) 또는 네모 움집(땅까지 닿는 맞배) ──
  if (ds.kind === 'hut') {
    if (ds.round) {
      const r = Math.min(W, D) * 0.46;
      const wm = wallOf('mud', ds.wall);
      const m = new THREE.Mesh(wallCyl(r, 0.9, wm.tex.tw, wm.tex.th), wm.mat); m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
      const cone = new THREE.ConeGeometry(r * 1.32, H * 1.25, 14); cone.translate(0, 0.6 + H * 0.625, 0);
      parts.push(paint(cone, new THREE.Color(S(ds.roof))));
      cyl(parts, 0.25, 0.8, 0, 0.6 + H * 1.2, 0, '#6e5236', 6);       // 꼭대기 묶음
      box(parts, 1.2, 1.5, 0.3, 0, 0.3, r * 1.02, '#2a1e14');
    } else {
      roof(roofM, { w: W * 0.95, d: D * 0.9, h: H * 1.15, kind: 'gable', prof: 'straight', ov: 0.3, col: S(ds.roof), endCol: '#6e5236', fasciaH: 0.3, x: 0, y: 0.3, z: 0, steps: 4, ridge: { col: S('#7d6b4c'), w: 0.5 } });
      box(parts, 1.1, 1.5, 0.3, 0, 0.3, D * 0.42, '#2a1e14');
    }
    return finish();
  }

  const wallH = ds.kind === 'town' ? H : ds.storeys ? Math.max(3.4, H * (ds.storeys / 2)) : ds.kind === 'cottage' ? Math.min(2.7, H * 0.75) : ds.kind === 'hanok' ? Math.min(3.3, H * 0.78) : H;
  const masses = planMasses(ds, W, D, wallH);
  main = masses[0];
  const style = ds.style;
  const wm = wallOf(style === 'brick' || style === 'concrete' || style === 'mud' || style === 'timber' || style === 'hanok' ? style : 'concrete', ds.wall);
  // 기단: 기와집은 돌 기단, 초가는 낮은 흙 기단
  const baseH = ds.kind === 'hanok' ? ds.base : ds.kind === 'cottage' ? 0.3 : 0.25;
  const baseC = ds.kind === 'hanok' ? '#9a948a' : ds.kind === 'cottage' ? '#9a8466' : '#8f8a84';
  for (const m of masses) box(parts, m.w + (ds.kind === 'hanok' ? 1.2 : 0.4), baseH, m.d + (ds.kind === 'hanok' ? 1.2 : 0.4), m.x, 0, m.z, baseC);

  const roofCol = S(ds.roofCol);
  for (const m of masses) {
    const y = baseH;
    addWall(wm, m.w, m.h, m.d, m.x, y, m.z);
    // 나무 기둥 (초가·한옥): 네 귀퉁이와 앞면
    if (ds.kind === 'cottage' || ds.kind === 'hanok') {
      const tc = ds.timber, n = Math.max(2, Math.round(m.w / 2.6));
      // 앞 기둥: 앞에 다른 채가 붙어 가리는 곳에는 세우지 않는다 (지붕을 뚫지 않게)
      const hidden = x => masses.some(o => o !== m && Math.abs(x - o.x) < o.w / 2 + 0.3 && o.z > m.z && o.z - o.d / 2 < m.z + m.d / 2 + 0.6);
      for (let i = 0; i <= n; i++) { const x = m.x - m.w / 2 + m.w * i / n; if (!hidden(x)) box(parts, 0.26, m.h, 0.26, x, y, m.z + m.d / 2 + 0.02, tc); }
      for (const sx of [-1, 1]) box(parts, 0.26, m.h, 0.26, m.x + sx * m.w / 2, y, m.z - m.d / 2, tc);
      box(parts, m.w + 0.1, 0.22, 0.28, m.x, y + m.h - 0.22, m.z + m.d / 2 + 0.02, tc);   // 도리
    }
    const top = y + m.h;
    const pitch = ds.pitch || 0.45;
    if (ds.kind === 'cottage') {
      const kind = ds.roof === 'thatchGable' ? 'gable' : ds.roof === 'thatchHalf' ? 'half' : 'hip';
      roof(roofM, { w: m.w, d: m.d, h: Math.min(m.w, m.d) * pitch, kind: m.lean ? 'shed' : kind, prof: 'thatch', ov: 0.75, col: roofCol, endCol: ds.wall, fasciaH: 0.42, x: m.x, y: top, z: m.z, steps: 7, ridge: m.lean ? null : { col: S('#8a6f3e'), w: 0.55, h: 0.32 } });
    } else if (ds.kind === 'hanok') {
      const kind = ds.roof === 'hanokGable' ? 'gable' : ds.roof === 'hanokHip' ? 'hip' : 'half';
      roof(roofM, { w: m.w, d: m.d, h: Math.min(m.w, m.d) * pitch, kind: m.lean ? 'shed' : kind, prof: 'hanok', ov: 1.1, col: roofCol, endCol: ds.wall, fasciaH: 0.22, upturn: 0.55, x: m.x, y: top, z: m.z, steps: 9, ridge: m.lean ? null : { col: S('#2b3036'), w: 0.6, h: 0.55, horns: true } });
    } else if (ds.kind === 'brick') {
      const k = ds.roof;
      const rh = Math.min(m.w, m.d) * pitch;
      const kind = k === 'hip' ? 'hip' : k === 'pyramid' ? 'hip' : k === 'halfhip' ? 'half' : k === 'saltbox' ? 'saltbox' : 'gable';
      const prof = k === 'gambrel' ? 'gambrel' : 'straight';
      if (k === 'shedPair' && !m.main) roof(roofM, { w: m.w, d: m.d, h: rh * 0.6, kind: 'shed', ov: 0.4, col: roofCol, endCol: ds.wall, x: m.x, y: top, z: m.z });
      else if (m.lean) roof(roofM, { w: m.w, d: m.d, h: rh * 0.5, kind: 'shed', ov: 0.4, col: roofCol, endCol: ds.wall, x: m.x, y: top, z: m.z });
      else {
        // 십자 맞배(cross): 곁채는 용마루를 다른 방향으로
        const rot = k === 'cross' && !m.main ? Math.PI / 2 : 0;
        const w2 = rot ? m.d : m.w, d2 = rot ? m.w : m.d;
        roof(roofM, { w: w2, d: d2, h: k === 'pyramid' ? Math.max(m.w, m.d) * pitch * 0.7 : rh, kind: k === 'pyramid' ? 'hip' : kind, prof, ov: 0.5, col: roofCol, endCol: ds.wall, fasciaH: 0.2, x: m.x, y: top, z: m.z, rot, steps: prof === 'straight' ? 2 : 6, ridge: { col: new THREE.Color(roofCol).multiplyScalar(0.7), w: 0.3, h: 0.22 } });
      }
      // 2층 벽돌집: 층 띠
      if (ds.storeys >= 2) box(parts, m.w + 0.2, 0.18, m.d + 0.2, m.x, y + m.h * 0.5, m.z, ds.trim);
    } else if (ds.kind === 'town') {
      const k = ds.roof;
      if (k === 'flat') flatRoof(parts, m.x, top, m.z, m.w, m.d, ds.trim);
      else if (k === 'mansard') roof(roofM, { w: m.w, d: m.d, h: Math.min(m.w, m.d) * 0.4, kind: 'hip', prof: 'mansard', ov: 0.3, col: roofCol, x: m.x, y: top, z: m.z, steps: 5, ridge: null });
      else roof(roofM, { w: m.w, d: m.d, h: Math.min(m.w, m.d) * ds.pitch, kind: k === 'hip' ? 'hip' : 'gable', ov: 0.5, col: roofCol, endCol: ds.wall, x: m.x, y: top, z: m.z, steps: 2, ridge: { col: new THREE.Color(roofCol).multiplyScalar(0.7), w: 0.3 } });
      // 층마다 발코니
      const floors = Math.max(2, Math.round(m.h / 3.1));
      for (let f = 1; f < floors; f++) {
        const yy = y + f * m.h / floors;
        box(parts, m.w + 0.1, 0.16, m.d + 0.1, m.x, yy - 0.08, m.z, ds.trim);   // 층 띠
        if (ds.balcony === 'strip' && m.main) { box(parts, m.w * 0.86, 0.22, 1.2, m.x, yy, m.z + m.d / 2 + 0.6, ds.trim); box(parts, m.w * 0.86, 0.9, 0.08, m.x, yy + 0.22, m.z + m.d / 2 + 1.16, '#9fb3c4'); }
        if (ds.balcony === 'boxes' && m.main) for (const sx of [-0.25, 0.25]) { box(parts, m.w * 0.3, 0.22, 1.1, m.x + sx * m.w, yy, m.z + m.d / 2 + 0.55, ds.trim); box(parts, m.w * 0.3, 0.95, 0.1, m.x + sx * m.w, yy + 0.22, m.z + m.d / 2 + 1.06, ds.trim); }
      }
    }
  }

  // ── 문·마루·현관 ──
  const fz = main.z + main.d / 2;
  fx = main.x + (ds.mirror ? 1 : -1) * main.w * 0.18;
  if (ds.kind === 'cottage' || ds.kind === 'hanok') {
    const nd = ds.kind === 'hanok' ? 3 : 2;
    for (let i = 0; i < nd; i++) box(parts, 1.0, 1.9, 0.12, main.x - main.w * 0.3 + i * main.w * 0.3, baseH + 0.25, fz + 0.04, i === 1 ? '#efe4c8' : ds.timber);   // 문 (가운데는 창호지)
    if (ds.maru) {   // 툇마루: 앞쪽 나무 마루와 받침
      box(parts, main.w * 0.86, 0.16, 1.1, main.x, baseH + 0.45, fz + 0.6, '#a07c52');
      for (const sx of [-0.4, 0, 0.4]) box(parts, 0.14, 0.45, 0.14, main.x + sx * main.w, baseH, fz + 1.05, '#6b4a2c');
    }
    box(parts, 1.2, 0.22, 0.6, fx, 0, fz + 1.5, '#8f877c');   // 디딤돌
  } else {
    box(parts, 1.2, 2.2, 0.14, fx, baseH, fz + 0.05, ds.door);
    box(parts, 1.6, 0.12, 0.2, fx, baseH + 2.25, fz + 0.1, ds.trim);
    if (ds.kind === 'brick') {
      if (ds.porch !== 'none') {   // 현관 지붕과 기둥
        const pw = ds.porch === 'full' ? main.w : ds.porch === 'corner' ? main.w * 0.45 : 2.8;
        const px = ds.porch === 'full' ? main.x : fx;
        box(parts, pw, 0.22, 2.2, px, 0, fz + 1.1, '#b9b4ab');
        const n = Math.max(2, Math.round(pw / 2.4));
        for (let i = 0; i <= n; i++) cyl(parts, 0.12, 2.6, px - pw / 2 + 0.2 + (pw - 0.4) * i / n, 0.22, fz + 2.05, ds.trim, 8);
        roof(roofM, { w: pw + 0.4, d: 2.3, h: 0.7, kind: 'shed', ov: 0.2, col: roofCol, endCol: ds.trim, x: px, y: 2.8, z: fz + 1.15 });
      } else box(parts, 1.8, 0.2, 1.0, fx, 0, fz + 0.5, '#b9b4ab');
      if (ds.bay && main.w > 6) {   // 내닫이창
        const bx = main.x - (ds.mirror ? 1 : -1) * main.w * 0.22;
        box(parts, 2.4, 2.0, 0.8, bx, baseH + 0.7, fz + 0.4, ds.trim);
        box(parts, 2.0, 1.4, 0.82, bx, baseH + 1.0, fz + 0.42, '#4f6478');
        box(parts, 2.6, 0.2, 1.0, bx, baseH + 2.7, fz + 0.45, roofCol);
      }
      if (ds.dormers) {   // 다락 지붕창
        const rh = Math.min(main.w, main.d) * (ds.pitch || 0.45);
        for (const sx of main.w > 8 ? [-0.25, 0.25] : [0]) {
          const dx = main.x + sx * main.w, dy = baseH + main.h + rh * 0.18, dz = main.z + main.d * 0.18;
          box(parts, 1.6, 1.3, 1.4, dx, dy, dz, ds.wall);
          box(parts, 1.0, 0.8, 0.06, dx, dy + 0.3, dz + 0.71, '#4f6478');
          roof(roofM, { w: 1.4, d: 1.6, h: 0.7, kind: 'gable', ov: 0.15, col: roofCol, endCol: ds.wall, x: dx, y: dy + 1.3, z: dz, rot: Math.PI / 2, steps: 1 });
        }
      }
    } else {
      box(parts, 2.4, 0.2, 1.4, fx, 0, fz + 0.7, '#b9b4ab');
      if (ds.stair) { box(parts, 2.6, main.h * 0.96, 2.4, main.x + main.w / 2 + 1.1, 0, main.z - main.d * 0.15, ds.trim); }   // 바깥 계단실
    }
  }

  // ── 굴뚝: 초가·한옥은 집 옆에 낮게 따로 선 굴뚝, 벽돌집은 지붕을 뚫고 ──
  const ch = ds.chimney;
  if (ch && ch !== 'none') {
    if (ds.kind === 'cottage' || ds.kind === 'hanok') {
      const x = ch === 'side' ? main.x + (ds.mirror ? -1 : 1) * (main.w / 2 + 1.3) : main.x + main.w * 0.3, z = ch === 'side' ? main.z - main.d * 0.2 : main.z - main.d / 2 - 1.4;
      box(parts, 0.9, 1.9, 0.9, x, 0, z, ds.kind === 'hanok' ? '#8a7f74' : '#8a6f50');
      box(parts, 1.1, 0.25, 1.1, x, 1.9, z, '#5a4a3a');
      if (cold) cx.smokeAt(grp, x, 2.4, z, 0.5);
    } else if (ds.kind === 'brick') {
      const x = ch === 'end' ? main.x + (ds.mirror ? -1 : 1) * (main.w / 2 - 0.6) : main.x + main.w * 0.1, z = main.z - main.d * 0.1;
      const rh = Math.min(main.w, main.d) * (ds.pitch || 0.45);
      box(parts, 0.9, baseH + main.h + rh + 0.8, 0.9, x, 0, z, ds.style === 'brick' ? new THREE.Color(ds.wall).multiplyScalar(0.8) : '#8a4f3c');
      box(parts, 1.1, 0.2, 1.1, x, baseH + main.h + rh + 0.8, z, '#5a5550');
      if (cold) cx.smokeAt(grp, x, baseH + main.h + rh + 1.4, z, 0.5);
    }
  }
  // 장독대 · 장작더미
  if (ds.jars) {
    const jx = main.x + (ds.mirror ? 1 : -1) * (main.w / 2 + 1.6), jz = main.z - main.d * 0.35;
    box(parts, 2.4, 0.25, 1.8, jx, 0, jz, '#8f877c');
    for (let i = 0; i < 5; i++) cyl(parts, 0.28 + (i % 2) * 0.08, 0.6 + (i % 3) * 0.12, jx - 0.8 + (i % 3) * 0.75, 0.25, jz - 0.4 + Math.floor(i / 3) * 0.8, '#5a3a24', 8, 0.2);
  }
  if (ds.wood) { const wx = main.x - (ds.mirror ? 1 : -1) * (main.w / 2 + 0.7), wz = main.z + main.d * 0.1; for (let i = 0; i < 3; i++) box(parts, 0.6, 0.35, 2.2, wx, i * 0.35, wz, i % 2 ? '#8a6a48' : '#7a5a3a'); }
  // 곁채(따로 선 헛간)는 몸채 목록 안에 있다

  return finish();

  function finish() {
    if (roofM.p.length) {
      const g = roofM.geo();
      const mat = cx.roofMat();
      const m = new THREE.Mesh(g, mat); m.userData.bid = b.id; m.userData.ownGeo = true; m.castShadow = true; grp.add(m);
    }
    if (parts.length) {
      const g = mergeGeometries(parts); for (const p of parts) p.dispose();
      const m = new THREE.Mesh(g, cx.detailMat()); m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
    }
    return { front: { x: fx, z: main.z + main.d / 2, w: main.w * 0.5 } };
  }
}

export const HOME_TYPES = new Set(['hut', 'cottage', 'house', 'brickhouse', 'townhouse']);
