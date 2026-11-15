// 도로를 진짜 입체로: 아스팔트 노면(차선·정지선·횡단보도·맨홀), 턱이 있는 보도(보도블록·연석),
// 흙길·자갈길·돌길. 지면 캔버스(멀리서 보는 그림) 위에 얹혀, 가까이 가도 선이 또렷하다.
import * as THREE from 'three';
import { ROAD_STYLE, segEnds, workState, nodeXY } from './roads.js';

const BW = 100;
// 높이: 지면 막(+0.45) 바로 위에 노면, 보도는 15cm 높다
const L0 = 0.5, CURB = 0.15;

// ── 결이 있는 타일 그림 (한 번만 만든다) ──
let TEX = null;
function lcg(seed) { return () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }; }
function canvasTex(S, draw) {
  const c = document.createElement('canvas'); c.width = c.height = S;
  draw(c.getContext('2d'), S);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  return t;
}
function grain(g, S, [r, gg, b], amp, seed, alphaFn = null) {
  const im = g.getImageData(0, 0, S, S), d = im.data, rn = lcg(seed);
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const i = (y * S + x) * 4, n = (rn() - 0.5) * amp;
    d[i] = Math.max(0, Math.min(255, d[i] + n)); d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + n)); d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + n));
    if (alphaFn) d[i + 3] = alphaFn(x, y, rn);
  }
  g.putImageData(im, 0, 0);
  void r; void gg; void b;
}
function textures() {
  if (TEX) return TEX;
  TEX = {
    // 아스팔트 (5m): 골재 알갱이, 땜질 자국, 가는 균열
    asphalt: canvasTex(256, (g, S) => {
      g.fillStyle = '#3e4146'; g.fillRect(0, 0, S, S);
      const r = lcg(11);
      // 얼룩은 아주 옅게 (반복 무늬가 눈에 띄지 않게)
      for (let i = 0; i < 9; i++) { g.fillStyle = `rgba(${r() < 0.5 ? '25,27,30' : '85,87,90'},${0.025 + r() * 0.03})`; g.beginPath(); g.ellipse(r() * S, r() * S, 12 + r() * 40, 8 + r() * 30, r() * 3, 0, Math.PI * 2); g.fill(); }
      grain(g, S, [0, 0, 0], 22, 7);
      for (let i = 0; i < 900; i++) { g.fillStyle = r() < 0.6 ? 'rgba(170,170,165,0.55)' : 'rgba(15,15,15,0.5)'; g.fillRect(r() * S, r() * S, 1, 1); }
      g.strokeStyle = 'rgba(18,18,20,0.55)'; g.lineWidth = 1;
      for (let k = 0; k < 3; k++) { let x = r() * S, y = r() * S; g.beginPath(); g.moveTo(x, y); for (let s = 0; s < 14; s++) { x += (r() - 0.5) * 14; y += (r() - 0.3) * 10; g.lineTo(x, y); } g.stroke(); }
    }),
    // 보도블록 (4m, 50cm 블록): 블록마다 색이 조금씩 다르고 줄눈이 있다
    paver: canvasTex(256, (g, S) => {
      const r = lcg(23), n = 8, t = S / n;
      g.fillStyle = '#8f8a82'; g.fillRect(0, 0, S, S);
      for (let j = 0; j < n; j++) for (let i = 0; i < n * 2; i++) {
        const off = (j % 2) * t / 2, x = i * t - off, k = 0.95 + r() * 0.08;
        g.fillStyle = `rgb(${Math.round(196 * k)},${Math.round(190 * k)},${Math.round(181 * k)})`;
        g.fillRect(x + 1.5, j * t + 1.5, t - 3, t - 3);
      }
      grain(g, S, [0, 0, 0], 12, 29);
    }),
    // 돌길 (3m): 둥근 돌
    cobble: canvasTex(256, (g, S) => {
      g.fillStyle = '#4c463e'; g.fillRect(0, 0, S, S);
      const r = lcg(31), n = 11, t = S / n;
      for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) {
        const cx = (i + (j % 2) * 0.5) * t + (r() - 0.5) * 3, cy = j * t + (r() - 0.5) * 3, v = 118 + r() * 50;
        g.fillStyle = `rgb(${v},${v - 6},${v - 14})`;
        g.beginPath(); g.ellipse(cx, cy, t * 0.44, t * 0.4, r() * 3, 0, Math.PI * 2); g.fill();
        g.fillStyle = 'rgba(255,255,255,0.12)'; g.beginPath(); g.ellipse(cx - 2, cy - 3, t * 0.22, t * 0.14, 0, 0, Math.PI * 2); g.fill();
        // 가장자리를 이어 붙여도 이음매가 안 보이게 반대편에도
        if (i === 0 || j === 0) { g.fillStyle = `rgb(${v},${v - 6},${v - 14})`; g.beginPath(); g.ellipse(cx + (i === 0 ? S : 0), cy + (j === 0 ? S : 0), t * 0.44, t * 0.4, 0, 0, Math.PI * 2); g.fill(); }
      }
      grain(g, S, [0, 0, 0], 14, 37);
    }),
    // 자갈길 (4m): 가장자리는 풀밭으로 흐려진다 (v = 길 폭 방향 0..1)
    gravel: canvasTex(256, (g, S) => {
      g.fillStyle = '#968f82'; g.fillRect(0, 0, S, S);
      const r = lcg(41);
      for (let i = 0; i < 2600; i++) { const v = 90 + r() * 120; g.fillStyle = `rgba(${v},${v - 4},${v - 10},0.8)`; g.fillRect(r() * S, r() * S, 1 + r() * 2, 1 + r() * 2); }
      grain(g, S, [0, 0, 0], 16, 43, (x, y, rn) => { const e = Math.min(y, S - 1 - y) / S; return Math.round(255 * Math.min(1, Math.max(0, (e - 0.015 - rn() * 0.03) / 0.06))); });
    }),
    // 흙길 (6m): 바퀴 자국 두 줄, 가장자리는 흐릿하게
    dirt: canvasTex(256, (g, S) => {
      g.fillStyle = '#a58b66'; g.fillRect(0, 0, S, S);
      for (const v of [0.32, 0.68]) { g.fillStyle = 'rgba(95,72,45,0.28)'; g.fillRect(0, v * S - 9, S, 18); g.fillStyle = 'rgba(95,72,45,0.18)'; g.fillRect(0, v * S - 14, S, 28); }
      const r = lcg(53);
      for (let i = 0; i < 700; i++) { g.fillStyle = r() < 0.5 ? 'rgba(70,52,30,0.35)' : 'rgba(210,190,150,0.3)'; g.fillRect(r() * S, r() * S, 2, 2); }
      for (let i = 0; i < 40; i++) { g.fillStyle = 'rgba(92,120,60,0.55)'; g.fillRect(r() * S, S * 0.47 + (r() - 0.5) * 18, 2, 3); }
      grain(g, S, [0, 0, 0], 18, 59, (x, y, rn) => { const e = Math.min(y, S - 1 - y) / S; return Math.round(255 * Math.min(1, Math.max(0, (e - 0.02 - rn() * 0.05) / 0.09))); });
    }),
    // 연석·콘크리트
    curb: canvasTex(64, (g, S) => { g.fillStyle = '#bdb8ae'; g.fillRect(0, 0, S, S); grain(g, S, [0, 0, 0], 14, 61); }),
  };
  return TEX;
}

/** 눈: 노면 색을 흰빛으로 섞는다 (u.value 0..1) */
function snowy(mat, u, k) {
  mat.onBeforeCompile = sh => {
    sh.uniforms.uSnow = u;
    sh.fragmentShader = 'uniform float uSnow;\n' + sh.fragmentShader.replace('#include <map_fragment>', `#include <map_fragment>\n  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.92, 0.94, 0.96), clamp(uSnow * ${k.toFixed(2)}, 0.0, 0.9));`);
  };
  mat.customProgramCacheKey = () => 'roadsnow' + k;
  return mat;
}

// ── 기하 ──
class GB {
  constructor(color = false) { this.p = []; this.uv = []; this.c = color ? [] : null; this.i = []; }
  get n() { return this.p.length / 3; }
  geo() {
    if (!this.i.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.p, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(this.uv, 2));
    if (this.c) g.setAttribute('color', new THREE.Float32BufferAttribute(this.c, 3));
    g.setIndex(this.i);
    g.computeVertexNormals();
    return g;
  }
}
/** 구간을 따라 가는 띠 (a: 길 방향 좌표, c: 길 중심에서 옆으로). uv: 'world' = 땅에 고정, 숫자 = 길이 방향 반복 길이(폭 방향 0..1) */
function strip(B, e, a0, a1, c0, c1, lift, hAt, uv, col = null, step = 4) {
  if (a1 - a0 < 0.01) return;
  const n = Math.max(1, Math.ceil((a1 - a0) / step)), m = c1 - c0 > 7 ? 2 : 1, base = B.n;
  for (let i = 0; i <= n; i++) {
    const a = a0 + (a1 - a0) * i / n;
    for (let j = 0; j <= m; j++) {
      const c = c0 + (c1 - c0) * j / m;
      const x = e.h ? a : e.x0 + c, z = e.h ? e.z0 + c : a;
      B.p.push(x, hAt(x, z) + lift, z);
      if (typeof uv === 'number') B.uv.push(a / uv, j / m);
      else B.uv.push(x / uv.t, z / uv.t);
      if (B.c) B.c.push(col[0], col[1], col[2]);
    }
  }
  const v = (i, j) => base + i * (m + 1) + j;
  for (let i = 0; i < n; i++) for (let j = 0; j < m; j++) {
    if (e.h) B.i.push(v(i, j), v(i, j + 1), v(i + 1, j), v(i + 1, j), v(i, j + 1), v(i + 1, j + 1));
    else B.i.push(v(i, j), v(i + 1, j), v(i, j + 1), v(i + 1, j), v(i + 1, j + 1), v(i, j + 1));
  }
}
/** 세로 벽 (연석 면): 길 쪽(inward) 또는 바깥쪽을 바라본다 */
function wall(B, e, a0, a1, c, lo, hi, hAt, inward) {
  if (a1 - a0 < 0.01) return;
  const n = Math.max(1, Math.ceil((a1 - a0) / 4)), base = B.n;
  for (let i = 0; i <= n; i++) {
    const a = a0 + (a1 - a0) * i / n, x = e.h ? a : e.x0 + c, z = e.h ? e.z0 + c : a, y = hAt(x, z);
    B.p.push(x, y + lo, z, x, y + hi, z);
    B.uv.push(a / 2, 0, a / 2, 0.25);
  }
  // 길 쪽을 보게: 가로길은 c>0 이면 뒤집고, 세로길은 c<0 이면 뒤집는다 (바깥쪽이면 반대로)
  let flip = e.h ? c > 0 : c < 0;
  if (!inward) flip = !flip;
  for (let i = 0; i < n; i++) {
    const l0 = base + i * 2, h0 = l0 + 1, l1 = l0 + 2, h1 = l0 + 3;
    if (!flip) B.i.push(l0, l1, h0, l1, h1, h0); else B.i.push(l0, h0, l1, l1, h0, h1);
  }
}

const WHITE = [0.93, 0.93, 0.9], YELLOW = [0.95, 0.75, 0.18], IRON = [0.2, 0.2, 0.21];
const hashK = k => { let h = 7; for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) % 2147483647; return h || 1; };

/**
 * 도로 입체 메시. roads: { 'h,i,j': 등급 }, works: 진행 중인 도로 공사, signals: 신호등 교차로.
 * 반환: { grp, snow } — snow.value 를 바꾸면 노면에 눈이 덮인다.
 */
export function buildRoadMeshes(roads, works, hAt, { signals = {} } = {}) {
  const T = textures();
  const keys = Object.keys(roads || {});
  const grp = new THREE.Group();
  const snow = { value: 0 };
  if (!keys.length) return { grp, snow };
  // 지금 공사 중인 구간은 메시를 비워 둔다 (지면 그림의 공사 표시가 보이게)
  const busy = new Set();
  for (const w of works || []) { const ws = workState(w); if (ws) busy.add(w.segs[ws.i]); }
  const B = { asphalt: new GB(), paver: new GB(), cobble: new GB(), gravel: new GB(), dirt: new GB(), curb: new GB(), mark: new GB(true) };
  const W = k => (roads[k] != null ? ROAD_STYLE[roads[k]] : null);
  // 교차로에서 옆으로 갈라지는 길 (h 길의 +z 쪽이면 v 길) 의 폭
  const crossAt = (h, i, j, side) => h ? W(side > 0 ? `v,${i},${j}` : `v,${i},${j - 1}`) : W(side > 0 ? `h,${i},${j}` : `h,${i - 1},${j}`);
  const nodeW = (h, i, j) => { const a = crossAt(h, i, j, 1), b = crossAt(h, i, j, -1); return Math.max(a?.w || 0, b?.w || 0); };
  const sig = new Set(Object.keys(signals || {}));
  for (const k of keys) {
    if (busy.has(k)) continue;
    const gr = roads[k], st = ROAD_STYLE[gr], e = segEnds(k);
    const [, si, sj] = k.split(','), i0 = +si, j0 = +sj;
    const [i1, j1] = e.h ? [i0 + 1, j0] : [i0, j0 + 1];
    const A0 = e.h ? e.x0 : e.z0, A1 = A0 + BW;
    const cw0 = nodeW(e.h, i0, j0), cw1 = nodeW(e.h, i1, j1);
    const lift = L0 + gr * 0.006 + (e.h ? 0.002 : 0);
    // 노면: 교차로 한가운데까지 덮도록 양 끝을 늘인다
    const ext0 = Math.max(cw0, st.w) / 2, ext1 = Math.max(cw1, st.w) / 2;
    const mat = gr >= 3 ? 'asphalt' : gr === 2 ? 'cobble' : gr === 1 ? 'gravel' : 'dirt';
    const uv = gr >= 3 ? { t: 7 } : gr === 2 ? { t: 3 } : gr === 1 ? 4 : 6;
    strip(B[mat], e, A0 - ext0, A1 + ext1, -st.w / 2, st.w / 2, lift, hAt, uv);
    if (!st.side) continue;
    // 보도: 양쪽, 옆길이 갈라지는 교차로에서는 끊긴다
    for (const s of [-1, 1]) {
      const c0 = s > 0 ? st.w / 2 : -st.w / 2 - st.side, c1 = s > 0 ? st.w / 2 + st.side : -st.w / 2;
      const x0 = crossAt(e.h, i0, j0, s), x1 = crossAt(e.h, i1, j1, s);
      const a0 = A0 + (x0 ? x0.w / 2 : 0), a1 = A1 - (x1 ? x1.w / 2 : 0);
      const sl = L0 + CURB + (e.h ? 0 : 0.003);
      strip(B.paver, e, a0, a1, c0, c1, sl, hAt, { t: 4 });
      wall(B.curb, e, a0, a1, s * st.w / 2, lift, sl, hAt, true);
      wall(B.curb, e, a0, a1, s * (st.w / 2 + st.side), 0.3, sl, hAt, false);
    }
    // ── 노면 표시 ──
    const ml = lift + 0.012;
    const paved = (h, i, j) => Math.max(roads[h ? `v,${i},${j}` : `h,${i},${j}`] ?? -1, roads[h ? `v,${i},${j - 1}` : `h,${i - 1},${j}`] ?? -1) >= 3;
    const cross0 = gr >= 3 && cw0 > 0 && paved(e.h, i0, j0) || sig.has(`${i0},${j0}`), cross1 = gr >= 3 && cw1 > 0 && paved(e.h, i1, j1) || sig.has(`${i1},${j1}`);
    const clear0 = cw0 / 2 + (cross0 ? 5.2 : cw0 ? 0.8 : 0), clear1 = cw1 / 2 + (cross1 ? 5.2 : cw1 ? 0.8 : 0);
    const la = A0 + clear0, lb = A1 - clear1;
    const line = (c, wd, col, dash = 0, gap = 0) => {
      if (!dash) { strip(B.mark, e, la, lb, c - wd / 2, c + wd / 2, ml, hAt, 1, col, 6); return; }
      for (let a = la; a < lb; a += dash + gap) strip(B.mark, e, a, Math.min(lb, a + dash), c - wd / 2, c + wd / 2, ml, hAt, 1, col, 6);
    };
    if (gr >= 4) {
      line(-0.17, 0.15, YELLOW); line(0.17, 0.15, YELLOW);
      line(-st.w / 4, 0.14, WHITE, 3, 5); line(st.w / 4, 0.14, WHITE, 3, 5);
      line(-st.w / 2 + 0.35, 0.15, WHITE); line(st.w / 2 - 0.35, 0.15, WHITE);
    } else if (gr === 3) line(0, 0.15, YELLOW);
    // 횡단보도와 정지선
    for (const [end, on, cw] of [[0, cross0, cw0], [1, cross1, cw1]]) {
      if (!on || gr < 3) continue;
      const from = end ? A1 - cw / 2 - 1.2 - 3.2 : A0 + cw / 2 + 1.2;
      for (let c = -st.w / 2 + 0.55; c < st.w / 2 - 0.4; c += 1.0) strip(B.mark, e, from, from + 3.2, c, c + 0.5, ml, hAt, 1, WHITE, 6);
      // 정지선: 교차로로 들어오는 차선 쪽 절반 (오른쪽 통행)
      const sl = end ? from - 1.0 : from + 3.2 + 0.6;
      const into = e.h ? (end ? 1 : -1) : (end ? -1 : 1);
      strip(B.mark, e, sl, sl + 0.45, into > 0 ? 0.3 : -st.w / 2 + 0.2, into > 0 ? st.w / 2 - 0.2 : -0.3, ml, hAt, 1, WHITE, 6);
    }
    // 맨홀 뚜껑
    if (gr >= 3) {
      const r = lcg(hashK(k));
      for (let a = la + 8 + r() * 20; a < lb - 3; a += 30 + r() * 25) { const c = (r() < 0.5 ? -1 : 1) * st.w / 4 * (0.7 + r() * 0.5); strip(B.mark, e, a, a + 0.7, c - 0.35, c + 0.35, ml - 0.004, hAt, 1, IRON, 6); }
    }
  }
  const mats = {
    asphalt: snowy(new THREE.MeshStandardMaterial({ map: T.asphalt, roughness: 0.92 }), snow, 0.35),
    paver: snowy(new THREE.MeshStandardMaterial({ map: T.paver, roughness: 0.9 }), snow, 0.6),
    cobble: snowy(new THREE.MeshStandardMaterial({ map: T.cobble, roughness: 0.95 }), snow, 0.8),
    gravel: snowy(new THREE.MeshStandardMaterial({ map: T.gravel, roughness: 1, transparent: true }), snow, 0.85),
    dirt: snowy(new THREE.MeshStandardMaterial({ map: T.dirt, roughness: 1, transparent: true }), snow, 0.9),
    curb: snowy(new THREE.MeshStandardMaterial({ map: T.curb, roughness: 0.85 }), snow, 0.4),
    mark: snowy(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }), snow, 0.5),
  };
  for (const [name, b] of Object.entries(B)) {
    const g = b.geo();
    if (!g) continue;
    const m = new THREE.Mesh(g, mats[name]);
    m.renderOrder = name === 'mark' ? 3 : 2;
    m.userData.road = true;
    grp.add(m);
  }
  return { grp, snow };
}
