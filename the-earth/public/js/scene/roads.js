// 도로: 구간마다 등급이 다르다 (흙길·자갈길·돌길·포장도로·대로).
// 지면 캔버스에 노면·인도·차선·횡단보도를 그리고, 가로등·가로수·신호등·공사 고깔은 3D로 세운다.
import * as THREE from 'three';

const CELL = 20, BLOCK = 5, BW = BLOCK * CELL, OFF = CELL / 2;
export const ROAD_STYLE = [
  { w: 5, col: '#a68d68' },
  { w: 6.5, col: '#9b958a', speck: true },
  { w: 7.5, col: '#8e8a83', cobble: true },
  { w: 9, col: '#43464c', side: 2.2, sideCol: '#bdb8ae', center: '#e9c547' },
  { w: 13, col: '#36393f', side: 3.2, sideCol: '#c9c3b8', center: '#f2c94c', lanes: true, cross: true },
];
const rnd = seed => () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
const keyHash = k => { let h = 7; for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) % 2147483647; return h || 1; };

export function segEnds(key) {
  const [o, bi, bj] = key.split(',');
  const x0 = +bi * BW + OFF, z0 = +bj * BW + OFF;
  return o === 'h' ? { h: true, x0, z0, x1: x0 + BW, z1: z0 } : { h: false, x0, z0, x1: x0, z1: z0 + BW };
}

/** 진행 중인 도로 공사: 지금 공사 중인 구간과 진척 */
export function workState(w) {
  const n = w.segs?.length || 0;
  if (!n) return null;
  const t = Math.min(n, (w.p || 0) * n);
  const i = Math.min(n - 1, Math.floor(t));
  return { i, frac: t - i, n };
}

// 신호등 주기 (서버 traffic.js 와 같음): 남북 초록 40초 · 노랑 5 · 동서 초록 40 · 노랑 5
export const SIGNAL_CYCLE = 90;
export function signalPhase(off, axis, clockMin) {
  const t = (((clockMin * 60 + off) % SIGNAL_CYCLE) + SIGNAL_CYCLE) % SIGNAL_CYCLE;
  if (axis === 'v') return t < 40 ? 'g' : t < 45 ? 'y' : 'r';
  return t < 45 ? 'r' : t < 85 ? 'g' : 'y';
}
export const nodeXY = k => { const [i, j] = k.split(',').map(Number); return [i * BW + OFF, j * BW + OFF]; };
/** 교차로에 닿는 길들 → { h: 동서 길 폭, v: 남북 길 폭, dirs: [[dx,dz,폭]...] } */
function nodeRoads(roads, node) {
  const [i, j] = node.split(',').map(Number);
  const out = { h: 0, v: 0, dirs: [] };
  const try_ = (k, dx, dz, axis) => { const g = roads[k]; if (g == null) return; const w = ROAD_STYLE[g].w; out[axis] = Math.max(out[axis], w); out.dirs.push([dx, dz, w]); };
  try_(`h,${i},${j}`, 1, 0, 'h'); try_(`h,${i - 1},${j}`, -1, 0, 'h'); try_(`v,${i},${j}`, 0, 1, 'v'); try_(`v,${i},${j - 1}`, 0, -1, 'v');
  return out;
}

/** 지면 캔버스에 도로를 그린다. crossNodes: 횡단보도를 그릴 교차로 (신호등) */
export function paintRoads(g, px, sc, roads, works, snow, crossNodes = []) {
  const keys = Object.keys(roads || {});
  const rect = (e, w, from = 0, to = 1, extend = true) => {
    const ext = extend ? w / 2 : 0;
    if (e.h) { const a = e.x0 + (e.x1 - e.x0) * from - (from === 0 ? ext : 0), b = e.x0 + (e.x1 - e.x0) * to + (to === 1 ? ext : 0); return [px(a), px(e.z0 - w / 2), (b - a) * sc, w * sc]; }
    const a = e.z0 + (e.z1 - e.z0) * from - (from === 0 ? ext : 0), b = e.z0 + (e.z1 - e.z0) * to + (to === 1 ? ext : 0);
    return [px(e.x0 - w / 2), px(a), w * sc, (b - a) * sc];
  };
  const surface = (k, grade, from = 0, to = 1) => {
    const st = ROAD_STYLE[grade], e = segEnds(k);
    const [x, y, w, h] = rect(e, st.w, from, to);
    g.fillStyle = st.col; g.fillRect(x, y, w, h);
    const r = rnd(keyHash(k) + grade);
    if (grade === 0) { g.fillStyle = 'rgba(80,60,35,0.25)'; for (let i = 0; i < 40; i++) g.fillRect(x + r() * w, y + r() * h, 2, 2); }
    if (st.speck) for (let i = 0; i < 160; i++) { g.fillStyle = r() < 0.5 ? 'rgba(255,255,255,0.28)' : 'rgba(40,40,40,0.25)'; g.fillRect(x + r() * w, y + r() * h, 1.5, 1.5); }
    if (st.cobble) {
      g.strokeStyle = 'rgba(60,55,50,0.45)'; g.lineWidth = 1;
      const step = Math.max(2, 1.1 * sc);
      for (let yy = y; yy < y + h; yy += step) { g.beginPath(); g.moveTo(x, yy); g.lineTo(x + w, yy); g.stroke(); }
      for (let xx = x; xx < x + w; xx += step) { g.beginPath(); g.moveTo(xx, y); g.lineTo(xx, y + h); g.stroke(); }
    }
  };
  // 인도 (포장도로·대로)
  for (const k of keys) {
    const gr = roads[k]; const st = ROAD_STYLE[gr];
    if (!st.side) continue;
    const e = segEnds(k);
    const [x, y, w, h] = rect(e, st.w + st.side * 2);
    g.fillStyle = st.sideCol; g.fillRect(x, y, w, h);
    g.strokeStyle = 'rgba(0,0,0,0.12)'; g.lineWidth = 1;
    const step = 1.5 * sc;
    if (gr >= 4) for (let t = 0; t < (e.h ? w : h); t += step) { g.beginPath(); if (e.h) { g.moveTo(x + t, y); g.lineTo(x + t, y + h); } else { g.moveTo(x, y + t); g.lineTo(x + w, y + t); } g.stroke(); }
  }
  // 노면: 낮은 등급부터 (교차로는 좋은 길이 덮는다)
  for (const k of keys.sort((a, b) => roads[a] - roads[b])) surface(k, roads[k]);
  // 차선
  for (const k of keys) {
    const gr = roads[k], st = ROAD_STYLE[gr];
    if (!st.center) continue;
    const e = segEnds(k);
    const line = (off, color, width, dash) => {
      g.strokeStyle = color; g.lineWidth = Math.max(1, width * sc); g.setLineDash(dash ? [3 * sc, 3 * sc] : []);
      g.beginPath();
      if (e.h) { g.moveTo(px(e.x0 + st.w / 2), px(e.z0 + off)); g.lineTo(px(e.x1 - st.w / 2), px(e.z0 + off)); }
      else { g.moveTo(px(e.x0 + off), px(e.z0 + st.w / 2)); g.lineTo(px(e.x0 + off), px(e.z1 - st.w / 2)); }
      g.stroke();
    };
    if (gr >= 4) { line(-0.25, st.center, 0.18); line(0.25, st.center, 0.18); line(-st.w / 4, 'rgba(255,255,255,0.8)', 0.15, true); line(st.w / 4, 'rgba(255,255,255,0.8)', 0.15, true); }
    else line(0, st.center, 0.2);
    g.setLineDash([]);
    // 횡단보도 (대로의 양 끝)
    if (st.cross) {
      g.fillStyle = 'rgba(245,245,245,0.85)';
      for (const end of [0, 1]) {
        const base = end ? (e.h ? e.x1 - st.w / 2 - 4 : e.z1 - st.w / 2 - 4) : (e.h ? e.x0 + st.w / 2 + 1 : e.z0 + st.w / 2 + 1);
        for (let s = -st.w / 2 + 0.6; s < st.w / 2 - 0.4; s += 1.2) {
          if (e.h) g.fillRect(px(base), px(e.z0 + s), 3 * sc, 0.6 * sc);
          else g.fillRect(px(e.x0 + s), px(base), 0.6 * sc, 3 * sc);
        }
      }
    }
  }
  // 신호등 교차로: 네 방향 횡단보도와 정지선
  for (const node of crossNodes) {
    const [x0, z0] = nodeXY(node), nr = nodeRoads(roads, node);
    for (const [dx, dz, w] of nr.dirs) {
      const half = (dx ? nr.v : nr.h) / 2 || 3;
      const a = half + 1.2, len = 3.2; // 교차로에서 조금 떨어져서 폭 3.2m
      g.fillStyle = 'rgba(245,245,245,0.9)';
      for (let s2 = -w / 2 + 0.5; s2 < w / 2 - 0.3; s2 += 1.1) {
        if (dx) g.fillRect(px(x0 + dx * a + (dx < 0 ? -len : 0)), px(z0 + s2), len * sc, 0.55 * sc);
        else g.fillRect(px(x0 + s2), px(z0 + dz * a + (dz < 0 ? -len : 0)), 0.55 * sc, len * sc);
      }
      // 정지선 (횡단보도 뒤, 들어오는 차선 쪽)
      g.fillRect(dx ? px(x0 + dx * (a + len + 0.8)) : px(z0 > 0 ? x0 : x0), dx ? px(z0) : px(z0 + dz * (a + len + 0.8)), dx ? 0.4 * sc : (w / 2) * sc, dx ? (w / 2) * sc : 0.4 * sc);
    }
  }
  // 공사 구간: 새 노면이 앞에서부터 깔리고, 주황·흰 줄무늬로 막아 둔다. 다음 구간은 측량 표시
  for (const w of works) {
    const ws = workState(w);
    if (!ws) continue;
    const k = w.segs[ws.i], e = segEnds(k), st = ROAD_STYLE[w.grade];
    if (!st) continue;
    if (ws.frac > 0.02) surface(k, w.grade, 0, ws.frac);
    const [x, y, ww, hh] = rect(e, st.w + 1, 0, 1, false);
    g.save();
    g.lineWidth = Math.max(2, 0.6 * sc); g.setLineDash([2 * sc, 2 * sc]);
    g.strokeStyle = '#ff8c1a'; g.strokeRect(x, y, ww, hh);
    g.lineDashOffset = 2 * sc; g.strokeStyle = '#ffffff'; g.strokeRect(x, y, ww, hh);
    g.restore();
    for (let j = ws.i + 1; j < w.segs.length; j++) {
      const [a, b2, c2, d2] = rect(segEnds(w.segs[j]), st.w, 0, 1, false);
      g.save(); g.setLineDash([1.5 * sc, 3 * sc]); g.strokeStyle = 'rgba(255,140,26,0.7)'; g.lineWidth = Math.max(1, 0.3 * sc); g.strokeRect(a, b2, c2, d2); g.restore();
    }
  }
  // 눈: 흙길은 하얗게 덮이고, 포장도로는 제설이 되어 덜 쌓인다
  if (snow > 0.05) for (const k of keys) {
    const gr = roads[k], st = ROAD_STYLE[gr];
    const [x, y, w, h] = rect(segEnds(k), st.w + (st.side || 0) * 2);
    g.fillStyle = `rgba(244,247,250,${Math.min(0.85, snow * (gr >= 3 ? 0.35 : 0.85))})`;
    g.fillRect(x, y, w, h);
  }
}

// ── 3D 소품 ──
const PG = {
  pole: new THREE.CylinderGeometry(0.12, 0.16, 1, 6).translate(0, 0.5, 0),
  head: new THREE.BoxGeometry(1.4, 0.3, 0.5),
  crown: new THREE.SphereGeometry(1, 8, 6),
  trunk: new THREE.CylinderGeometry(0.15, 0.22, 1, 5).translate(0, 0.5, 0),
  signal: new THREE.BoxGeometry(0.5, 1.4, 0.5),
  cone: new THREE.ConeGeometry(0.3, 0.8, 8).translate(0, 0.4, 0),
  lantern: new THREE.BoxGeometry(0.6, 0.7, 0.6),
};
const PM = {
  pole: new THREE.MeshStandardMaterial({ color: 0x5b636b, roughness: 0.6, metalness: 0.4 }),
  wood: new THREE.MeshStandardMaterial({ color: 0x4a3626, roughness: 1 }),
  head: new THREE.MeshStandardMaterial({ color: 0xfff3cf, emissive: 0xffe2a0, emissiveIntensity: 0 }),
  lantern: new THREE.MeshStandardMaterial({ color: 0xffd9a0, emissive: 0xffb860, emissiveIntensity: 0 }),
  crown: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.9 }),
  trunk: new THREE.MeshStandardMaterial({ color: 0x5b4330, roughness: 1 }),
  signal: new THREE.MeshStandardMaterial({ color: 0x2b2f33, emissive: 0x33ff66, emissiveIntensity: 0.6 }),
  cone: new THREE.MeshStandardMaterial({ color: 0xff7a1a, roughness: 0.6 }),
};
export const roadLampMats = [PM.head, PM.lantern];

const SG = {
  pole: new THREE.CylinderGeometry(0.11, 0.14, 1, 8).translate(0, 0.5, 0),
  arm: new THREE.BoxGeometry(1, 0.12, 0.12).translate(0.5, 0, 0),
  box: new THREE.BoxGeometry(1.35, 0.42, 0.36),
  lamp: new THREE.CircleGeometry(0.14, 12),
  trench: new THREE.BoxGeometry(1, 0.06, 0.7),
  found: new THREE.BoxGeometry(1.1, 0.5, 1.1).translate(0, 0.25, 0),
  slab: new THREE.BoxGeometry(3.2, 0.16, 1.6).translate(0, 0.08, 0),
  roof: new THREE.BoxGeometry(3.2, 0.1, 1.5),
  glass: new THREE.BoxGeometry(3.0, 1.9, 0.04),
  bench: new THREE.BoxGeometry(2.0, 0.08, 0.42),
  sign: new THREE.BoxGeometry(0.62, 0.62, 0.05),
};
const SM = {
  metal: new THREE.MeshStandardMaterial({ color: 0x8a9096, roughness: 0.5, metalness: 0.5 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x22262a, roughness: 0.7 }),
  trench: new THREE.MeshStandardMaterial({ color: 0x4a3b2c, roughness: 1 }),
  conc: new THREE.MeshStandardMaterial({ color: 0xb8b4ab, roughness: 0.95 }),
  slab: new THREE.MeshStandardMaterial({ color: 0xc9c4ba, roughness: 0.9 }),
  roof: new THREE.MeshStandardMaterial({ color: 0x2f5d8a, roughness: 0.6 }),
  glass: new THREE.MeshStandardMaterial({ color: 0xa9cbe0, transparent: true, opacity: 0.35, roughness: 0.1 }),
  bench: new THREE.MeshStandardMaterial({ color: 0x7b5a3a, roughness: 0.9 }),
  sign: new THREE.MeshStandardMaterial({ color: 0x1f6fd1, emissive: 0x1f6fd1, emissiveIntensity: 0.25 }),
};
const LAMP_COL = { r: 0xff3b30, y: 0xffc107, g: 0x2ee66b };
/** 신호등 한 곳. stage: 공사 단계 (99 = 완공). lamps 에 깜빡일 재질을 모은다 */
function signalProps(grp, roads, node, hAt, { off, lamps, stage, blink = false }) {
  const [x0, z0] = nodeXY(node), nr = nodeRoads(roads, node);
  const hw = Math.max(nr.h, 6) / 2, hv = Math.max(nr.v, 6) / 2;
  const y0 = hAt(x0, z0);
  // 모서리 네 곳: [x부호, z부호, 담당 방향(남북 v / 동서 h)]
  const corners = [[1, 1, 'v'], [-1, -1, 'v'], [-1, 1, 'h'], [1, -1, 'h']];
  const mats = {};
  for (const axis of ['v', 'h']) {
    mats[axis] = {};
    for (const c of ['r', 'y', 'g']) mats[axis][c] = new THREE.MeshStandardMaterial({ color: 0x111111, emissive: LAMP_COL[c], emissiveIntensity: 0.04 });
    if (stage >= 4) lamps.push({ off, axis, mats: mats[axis], blink, done: stage >= 99 });
  }
  for (const [sx, sz, axis] of corners) {
    const cx = x0 + sx * (hv + 1.4), cz = z0 + sz * (hw + 1.4);
    if (stage >= 1 && stage < 99) { const tr = new THREE.Mesh(SG.trench, SM.trench); tr.position.set(x0 + sx * (hv + 1.4) / 2, hAt(cx, cz) + 0.05, cz); tr.scale.x = hv + 1.4; grp.add(tr); }
    if (stage >= 2) { const f = new THREE.Mesh(SG.found, SM.conc); f.position.set(cx, hAt(cx, cz) - 0.2, cz); grp.add(f); }
    if (stage < 3) continue;
    const pole = new THREE.Mesh(SG.pole, SM.metal); pole.position.set(cx, hAt(cx, cz), cz); pole.scale.y = 6.2; grp.add(pole);
    // 가로대: 담당 방향의 들어오는 차선 위로 뻗는다
    const arm = new THREE.Mesh(SG.arm, SM.metal);
    const reach = axis === 'v' ? hv + 0.6 : hw + 0.6;
    arm.position.set(cx, hAt(cx, cz) + 5.9, cz);
    arm.scale.x = reach;
    arm.rotation.y = axis === 'v' ? (sx > 0 ? Math.PI : 0) : (sz > 0 ? Math.PI / 2 : -Math.PI / 2);
    grp.add(arm);
    if (stage < 4) continue;
    const hx = axis === 'v' ? cx - sx * reach * 0.7 : cx, hz = axis === 'h' ? cz - sz * reach * 0.7 : cz;
    const head = new THREE.Group();
    head.position.set(hx, hAt(cx, cz) + 5.6, hz);
    // 남북 담당은 남북을 바라보고, 동서 담당은 동서를 바라본다 (들어오는 차를 향해)
    head.rotation.y = axis === 'v' ? (sz > 0 ? Math.PI : 0) : (sx > 0 ? -Math.PI / 2 : Math.PI / 2);
    if (axis === 'v') head.rotation.y = sz > 0 ? 0 : Math.PI;
    head.add(new THREE.Mesh(SG.box, SM.dark));
    ['r', 'y', 'g'].forEach((c, i) => { const l = new THREE.Mesh(SG.lamp, mats[axis][c]); l.position.set(-0.42 + i * 0.42, 0, 0.185); head.add(l); const l2 = l.clone(); l2.position.z = -0.185; l2.rotation.y = Math.PI; head.add(l2); });
    grp.add(head);
  }
}
/** 버스 정류장: 승강장 블록, 지붕·유리벽·의자, 파란 표지판 */
function stopProps(grp, x, z, hAt, stage, name) {
  const y = hAt(x, z);
  if (stage >= 1) { const sl = new THREE.Mesh(SG.slab, SM.slab); sl.position.set(x, y, z); grp.add(sl); }
  if (stage >= 2) {
    const sp = new THREE.Mesh(SG.pole, SM.metal); sp.position.set(x + 1.9, y, z); sp.scale.y = 2.8; grp.add(sp);
    const sg = new THREE.Mesh(SG.sign, SM.sign); sg.position.set(x + 1.9, y + 2.6, z); grp.add(sg);
    for (const dx of [-1.4, 1.4]) { const p = new THREE.Mesh(SG.pole, SM.metal); p.position.set(x + dx, y + 0.16, z - 0.6); p.scale.y = 2.4; grp.add(p); }
    const rf = new THREE.Mesh(SG.roof, SM.roof); rf.position.set(x, y + 2.6, z - 0.1); grp.add(rf);
    const gl = new THREE.Mesh(SG.glass, SM.glass); gl.position.set(x, y + 1.15, z - 0.7); grp.add(gl);
    const bn = new THREE.Mesh(SG.bench, SM.bench); bn.position.set(x, y + 0.6, z - 0.4); grp.add(bn);
  }
}

export function buildRoadProps(roads, works, hAt, extra = {}) {
  const grp = new THREE.Group();
  const lights = [], lanterns = [], trees = [], signals = [], cones = [];
  const along = (e, step, fn) => { const L = BW; for (let t = step / 2; t < L; t += step) { const x = e.h ? e.x0 + t : e.x0, z = e.h ? e.z0 : e.z0 + t; fn(x, z); } };
  const inters = new Map();
  for (const [k, gr] of Object.entries(roads || {})) {
    const e = segEnds(k), st = ROAD_STYLE[gr];
    const off = st.w / 2 + (st.side || 1) * 0.6;
    if (gr >= 3) along(e, gr >= 4 ? 25 : 33, (x, z) => { for (const s of gr >= 4 ? [-1, 1] : [1]) lights.push(e.h ? [x, z + s * off, s, 0] : [x + s * off, z, 0, s]); });
    if (gr === 2) along(e, 40, (x, z) => lanterns.push(e.h ? [x, z + off] : [x + off, z]));
    if (gr >= 4) along(e, 13, (x, z) => { for (const s of [-1, 1]) trees.push(e.h ? [x + 6, z + s * (off + 0.8)] : [x + s * (off + 0.8), z + 6]); });
  }
  // (신호등은 저절로 생기지 않는다 — 관청 교통과가 세운 것만 아래에서 그린다)
  for (const w of works) {
    const ws = workState(w);
    if (!ws) continue;
    const e = segEnds(w.segs[ws.i]), st = ROAD_STYLE[w.grade];
    along(e, 4, (x, z) => { for (const s of [-1, 1]) cones.push(e.h ? [x, z + s * (st.w / 2 + 0.5)] : [x + s * (st.w / 2 + 0.5), z]); });
  }
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), v = new THREE.Vector3(), sc = new THREE.Vector3();
  const inst = (geo, mat, list, fn) => {
    if (!list.length) return null;
    const im = new THREE.InstancedMesh(geo, mat, list.length);
    list.forEach((it, i) => { fn(it, v, sc); m4.compose(v, q, sc); im.setMatrixAt(i, m4); });
    im.frustumCulled = false;
    grp.add(im);
    return im;
  };
  inst(PG.pole, PM.pole, lights, ([x, z], p, s) => { p.set(x, hAt(x, z), z); s.set(1, 8, 1); });
  inst(PG.head, PM.head, lights, ([x, z, sx, sz], p, s) => { p.set(x - sx * 0.6, hAt(x, z) + 8, z - sz * 0.6); s.set(sz ? 0.4 : 1, 1, sz ? 1.4 / 0.5 : 1); });
  inst(PG.pole, PM.wood, lanterns, ([x, z], p, s) => { p.set(x, hAt(x, z), z); s.set(1, 3.2, 1); });
  inst(PG.lantern, PM.lantern, lanterns, ([x, z], p, s) => { p.set(x, hAt(x, z) + 3.4, z); s.set(1, 1, 1); });
  inst(PG.trunk, PM.trunk, trees, ([x, z], p, s) => { p.set(x, hAt(x, z), z); s.set(1, 2.4, 1); });
  const crowns = inst(PG.crown, PM.crown, trees, ([x, z], p, s) => { p.set(x, hAt(x, z) + 3.6, z); s.set(1.9, 2.2, 1.9); });
  const lamps = [];
  // 다 지은 신호등: 네 모서리 기둥 + 길 위로 뻗은 가로대 + 가로형 3구 신호등 (빨강·노랑·초록)
  for (const [node, sg] of Object.entries(extra.signals || {})) signalProps(grp, roads, node, hAt, { off: sg.off, lamps, stage: 99 });
  // 공사 중인 신호등·정류장: 단계에 따라
  for (const w of works) {
    if (!w.project) continue;
    const [x0, z0] = w.node ? nodeXY(w.node) : [w.x, w.z];
    if (w.type === 'signal') {
      const nr = nodeRoads(roads, w.node), r = Math.max(nr.h, nr.v, 6) / 2 + 2.2;
      for (let k = 0; k < 14; k++) { const an = k / 14 * Math.PI * 2; cones.push([x0 + Math.cos(an) * r, z0 + Math.sin(an) * r]); }
      signalProps(grp, roads, w.node, hAt, { off: 0, lamps, stage: w.stage, blink: w.stage >= 7 });
    } else if (w.type === 'busstop') {
      for (const [dx, dz] of [[-2, -1.2], [2, -1.2], [-2, 1.2], [2, 1.2]]) cones.push([w.x + dx, w.z + dz]);
      stopProps(grp, w.x, w.z, hAt, w.stage);
    }
  }
  for (const st of extra.stops || []) if (st.built) stopProps(grp, st.pos[0], st.pos[1], hAt, 99, st.name);
  inst(PG.cone, PM.cone, cones, ([x, z], p, s) => { p.set(x, hAt(x, z), z); s.set(1, 1, 1); });
  return { grp, crowns, nTrees: trees.length, lamps };
}
