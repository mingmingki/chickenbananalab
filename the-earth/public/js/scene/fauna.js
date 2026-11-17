// 동물: 기르는 짐승(소·물소·돼지·양·염소·말·낙타·라마·닭·오리·개·고양이), 들짐승(사슴·멧돼지·토끼·여우·늑대·곰·다람쥐·
// 순록·북극여우·코끼리·얼룩말·영양·원숭이), 새(참새·까치·까마귀·비둘기·갈매기·제비·기러기·매·백로·앵무새),
// 곤충(나비·벌·잠자리·반딧불이). 시대·지역·기후·계절·낮밤·날씨에 따라 나타나고, 마을과 같이 휜다(warp.js).
// 모형은 상자 몇 개를 합친 저다각형, 걸음·날갯짓·풀 뜯기·꼬리는 셰이더에서 움직인다 (한 종류 = 한 번 그리기).
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { PHONE } from '../device.js';

const frac = v => v - Math.floor(v);
const R = (seed, k) => frac(Math.sin(seed * 12.9898 + k * 78.233) * 43758.5453);
const pick = (arr, r) => arr[Math.min(arr.length - 1, Math.floor(r * arr.length))];

// ── 모형 짓기: 부위(part) — 0 몸 · 1~4 다리(1·2 같은 박자, 3·4 반대 박자) · 5 머리 · 6·7 날개 · 8 꼬리. tint 1 = 털빛(개체마다) ──
class Shape {
  constructor() { this.parts = []; }
  add(g, col, part = 0, tint = 1) {
    g.deleteAttribute('uv'); if (g.index) g = g.toNonIndexed();
    const n = g.attributes.position.count, c = new THREE.Color(col), cc = new Float32Array(n * 3), pp = new Float32Array(n), tt = new Float32Array(n);
    for (let i = 0; i < n; i++) { cc[i * 3] = c.r; cc[i * 3 + 1] = c.g; cc[i * 3 + 2] = c.b; pp[i] = part; tt[i] = tint; }
    g.setAttribute('color', new THREE.BufferAttribute(cc, 3)); g.setAttribute('part', new THREE.BufferAttribute(pp, 1)); g.setAttribute('tint', new THREE.BufferAttribute(tt, 1));
    this.parts.push(g); return this;
  }
  box(w, h, d, x, y, z, col, part = 0, tint = 1, rx = 0, ry = 0, rz = 0) {
    const g = new THREE.BoxGeometry(w, h, d); if (rx) g.rotateX(rx); if (rz) g.rotateZ(rz); if (ry) g.rotateY(ry); g.translate(x, y, z);
    return this.add(g, col, part, tint);
  }
  ball(r, x, y, z, col, part = 0, tint = 1, sx = 1, sy = 1, sz = 1) { return this.add(new THREE.IcosahedronGeometry(r, 1).scale(sx, sy, sz).translate(x, y, z), col, part, tint); }
  geo() { const g = mergeGeometries(this.parts); g.computeVertexNormals(); return g; }
}

/** 네발짐승. 앞은 +z. o: 몸 길이 L·높이 H·너비 W·다리 lh·목 nl·목 각도 na·머리 [l,h,w]·뿔/엄니/혹 등 */
function quad(o) {
  const s = new Shape(), { L, H, W, lh } = o, top = lh + H;
  const legW = o.legW ?? W * 0.22, legC = o.legC ?? '#e2e2e2';
  for (const [i, sx, sz] of [[1, -1, 1], [2, 1, -1], [3, 1, 1], [4, -1, -1]]) {
    s.box(legW, lh, legW, sx * W * 0.3, lh / 2, sz * L * 0.36, legC, i, o.legTint ?? 1);
    s.box(legW * 1.05, lh * 0.12, legW * 1.1, sx * W * 0.3, lh * 0.06, sz * L * 0.36, o.hoof || '#3a3530', i, 0);
  }
  if (o.stripes) { const n = 9; for (let k = 0; k < n; k++) s.box(W, H, L / n, 0, lh + H / 2, -L / 2 + L * (k + 0.5) / n, k % 2 ? '#1c1c1c' : '#f2f2ee', 0, 0); }
  else s.box(W, H, L, 0, lh + H / 2, 0, '#ffffff', 0, 1);
  if (o.belly) s.box(W * 0.9, H * 0.3, L * 0.8, 0, lh + H * 0.12, 0, o.belly, 0, 0);
  if (o.wool) for (let k = 0; k < 5; k++) s.ball(W * 0.42, (k % 2 - 0.5) * W * 0.3, lh + H * 0.62, -L * 0.36 + k * L * 0.18, '#ffffff', 0, 1);
  if (o.hump) s.ball(W * 0.45, 0, top + W * 0.12, -L * 0.05, '#ffffff', 0, 1, 1, 0.9, 1.3);
  // 목과 머리
  const na = o.na ?? 0.6, nl = o.nl ?? H * 0.6;
  const nx = top - H * 0.25 + Math.cos(na) * nl / 2, nz = L / 2 + Math.sin(na) * nl / 2 * 0.9;
  if (nl > 0.05) s.box(W * 0.42, nl, W * 0.42, 0, nx, nz - W * 0.05, '#ffffff', 5, 1, na);
  const [hl, hh, hw] = o.head;
  const hy = top - H * 0.25 + Math.cos(na) * nl, hz = L / 2 + Math.sin(na) * nl * 0.9 + hl * 0.35;
  s.box(hw, hh, hl, 0, hy, hz, '#ffffff', 5, 1);
  if (o.snout) s.box(hw * 0.7, hh * 0.55, hl * 0.35, 0, hy - hh * 0.15, hz + hl * 0.6, o.snout, 5, 0);
  for (const sx of [-1, 1]) s.box(hw * 0.12, hh * 0.14, hw * 0.12, sx * hw * 0.5, hy + hh * 0.18, hz + hl * 0.25, '#151515', 5, 0);   // 눈
  if (o.ears) for (const sx of [-1, 1]) s.box(hw * 0.22, hh * (o.ears === 'tall' ? 0.9 : 0.4), hw * 0.12, sx * hw * 0.38, hy + hh * (o.ears === 'tall' ? 0.85 : 0.6), hz - hl * 0.2, o.earC || '#ffffff', 5, o.earC ? 0 : 1, 0, 0, sx * 0.3);
  if (o.horns === 'short') for (const sx of [-1, 1]) s.box(0.06, hh * 0.5, 0.06, sx * hw * 0.35, hy + hh * 0.7, hz - hl * 0.2, '#e8dfc8', 5, 0, 0, 0, sx * -0.6);
  if (o.horns === 'curl') for (const sx of [-1, 1]) s.ball(hh * 0.28, sx * hw * 0.5, hy + hh * 0.35, hz - hl * 0.25, '#cfc3a8', 5, 0, 0.6, 1, 1);
  if (o.horns === 'wide') for (const sx of [-1, 1]) s.box(hw * 0.8, 0.08, 0.1, sx * hw * 0.7, hy + hh * 0.55, hz - hl * 0.2, '#3a3a3a', 5, 0, 0, 0, sx * 0.25);
  if (o.horns === 'antler') for (const sx of [-1, 1]) { s.box(0.05, hh * 1.3, 0.05, sx * hw * 0.32, hy + hh * 1.0, hz - hl * 0.25, '#b49a72', 5, 0, 0, 0, sx * -0.4); s.box(0.04, hh * 0.6, 0.04, sx * hw * 0.55, hy + hh * 1.25, hz - hl * 0.1, '#b49a72', 5, 0, 0.6, 0, sx * -0.3); }
  if (o.tusk) for (const sx of [-1, 1]) s.box(0.05, 0.05, hl * 0.5, sx * hw * 0.3, hy - hh * 0.35, hz + hl * 0.55, '#f2ecdc', 5, 0, -0.4);
  if (o.trunk) s.box(hw * 0.25, hh * 1.4, hw * 0.25, 0, hy - hh * 0.8, hz + hl * 0.55, '#ffffff', 5, 1, 0.25);
  if (o.mane) s.box(W * 0.12, nl * 0.9, W * 0.2, 0, nx + 0.05, nz - W * 0.15, o.mane, 5, 0, na);
  if (o.beard) s.box(hw * 0.25, hh * 0.4, hw * 0.2, 0, hy - hh * 0.55, hz + hl * 0.3, '#e8e2d6', 5, 0);
  // 꼬리
  const tl = o.tail ?? H * 0.6;
  if (tl > 0) s.box(o.tailW ?? 0.07, tl, o.tailW ?? 0.07, 0, top - H * 0.15 - (o.tailUp ? -tl * 0.4 : tl * 0.45), -L / 2 - 0.04, o.tailC || '#ffffff', 8, o.tailC ? 0 : 1, o.tailUp ? -0.5 : 0.25);
  return { geo: s.geo(), legTop: lh, headDrop: Math.max(0.1, hy - H * 0.3) * 0.55, size: Math.max(L, top) };
}

/** 새: 몸·머리·부리·꼬리·날개(6·7), 땅에 사는 새는 다리 */
function bird(o) {
  const s = new Shape(), L = o.L;
  const y0 = o.legs ? o.legs : 0;
  s.ball(L * 0.32, 0, y0 + L * 0.3, 0, '#ffffff', 0, 1, 0.8, 0.75, 1.35);
  s.ball(L * 0.17, 0, y0 + L * 0.55, L * 0.38, o.headC || '#ffffff', 5, o.headC ? 0 : 1);
  s.box(L * 0.05, L * 0.05, L * 0.16, 0, y0 + L * 0.52, L * 0.55, o.beak || '#e0a020', 5, 0);
  if (o.comb) s.box(L * 0.04, L * 0.12, L * 0.16, 0, y0 + L * 0.72, L * 0.38, '#d33', 5, 0);
  s.box(L * 0.2, L * 0.04, L * (o.tailL ?? 0.35), 0, y0 + L * 0.34, -L * 0.48, o.tailC || '#ffffff', 8, o.tailC ? 0 : 1, -0.15);
  const span = o.span ?? L * 0.9;
  for (const [part, sx] of [[6, -1], [7, 1]]) s.box(span, L * 0.03, L * 0.42, sx * (span / 2 + L * 0.12), y0 + L * 0.42, 0, o.wingC || '#ffffff', part, o.wingC ? 0 : 1);
  if (o.legs) for (const sx of [-1, 1]) s.box(0.03, o.legs, 0.03, sx * L * 0.1, o.legs / 2, 0, o.legC || '#d89a3a', 1, 0);
  return { geo: s.geo(), legTop: y0, headDrop: L * 0.25, size: L };
}

/** 곤충: 나비·벌·잠자리 */
function bug(kind) {
  const s = new Shape();
  if (kind === 'butterfly') {
    s.box(0.012, 0.012, 0.06, 0, 0, 0, '#222', 0, 0);
    for (const [p, sx] of [[6, -1], [7, 1]]) { s.box(0.05, 0.004, 0.04, sx * 0.03, 0, 0.012, '#ffffff', p, 1); s.box(0.035, 0.004, 0.03, sx * 0.024, 0, -0.022, '#ffffff', p, 1); }
  } else if (kind === 'bee') {
    s.ball(0.012, 0, 0, 0, '#ffffff', 0, 1, 1, 1, 1.5); s.box(0.026, 0.006, 0.006, 0, 0.002, -0.004, '#111', 0, 0);
    for (const [p, sx] of [[6, -1], [7, 1]]) s.box(0.02, 0.002, 0.012, sx * 0.012, 0.01, 0, '#eef4ff', p, 0);
  } else {
    s.box(0.01, 0.01, 0.08, 0, 0, -0.01, '#ffffff', 0, 1);
    for (const [p, sx] of [[6, -1], [7, 1]]) { s.box(0.06, 0.002, 0.012, sx * 0.032, 0.004, 0.018, '#e8eef4', p, 0); s.box(0.055, 0.002, 0.011, sx * 0.03, 0.004, 0.002, '#e8eef4', p, 0); }
  }
  return { geo: s.geo(), legTop: 0, headDrop: 0, size: 0.1 };
}

// ── 종류표 ── c: 털빛 후보, sp: 걸음 속도(m/s), f: 다리 박자, flap: 날갯짓, herd: 무리 크기, act: 'day'|'night'|'any'
export const SPECIES = {
  cow: { name: '소', make: () => quad({ L: 1.9, H: 0.85, W: 0.72, lh: 0.75, nl: 0.35, na: 0.9, head: [0.5, 0.4, 0.36], snout: '#d9b8a8', ears: 'small', horns: 'short', tail: 0.7 }), c: ['#7a4a2a', '#a8703f', '#2e2622', '#e8e2d6', '#c9a26a'], sp: 0.6, f: 3 },
  buffalo: { name: '물소', make: () => quad({ L: 2.0, H: 0.9, W: 0.8, lh: 0.7, nl: 0.25, na: 1.1, head: [0.5, 0.42, 0.4], horns: 'wide', tail: 0.6 }), c: ['#3a3a3a', '#4a4440', '#2a2a2a'], sp: 0.5, f: 2.6 },
  pig: { name: '돼지', make: () => quad({ L: 1.15, H: 0.55, W: 0.5, lh: 0.3, nl: 0, head: [0.35, 0.36, 0.32], snout: '#e8a8a8', ears: 'small', tail: 0.18, tailUp: true }), c: ['#f0c0b4', '#e8b0a4', '#2a2422', '#d8b0a0'], sp: 0.5, f: 5 },
  sheep: { name: '양', make: () => quad({ L: 1.0, H: 0.6, W: 0.62, lh: 0.42, nl: 0.15, na: 0.7, head: [0.3, 0.28, 0.22], legC: '#3a3330', legTint: 0, ears: 'small', earC: '#3a3330', wool: true, tail: 0.2 }), c: ['#f2efe6', '#e8e2d2', '#ddd5c4', '#3a3330'], sp: 0.55, f: 4.5 },
  goat: { name: '염소', make: () => quad({ L: 0.95, H: 0.48, W: 0.38, lh: 0.55, nl: 0.3, na: 0.5, head: [0.3, 0.24, 0.2], ears: 'small', horns: 'curl', beard: true, tail: 0.15, tailUp: true }), c: ['#f2efe6', '#2a2522', '#8a5a3a', '#c9b49a'], sp: 0.7, f: 5 },
  horse: { name: '말', make: () => quad({ L: 1.9, H: 0.72, W: 0.55, lh: 1.05, nl: 0.85, na: 0.45, head: [0.6, 0.3, 0.26], ears: 'small', mane: '#2a1e18', tail: 0.8, tailW: 0.12, tailC: '#2a1e18' }), c: ['#6b3f22', '#2a1e18', '#a0602f', '#e8e2d6', '#8a7a6a'], sp: 1.1, f: 3.6 },
  camel: { name: '낙타', make: () => quad({ L: 2.0, H: 0.75, W: 0.6, lh: 1.4, nl: 1.0, na: 0.55, head: [0.55, 0.28, 0.24], ears: 'small', hump: true, tail: 0.5 }), c: ['#c9a06a', '#b8905a', '#d8b47e'], sp: 0.8, f: 2.4 },
  llama: { name: '라마', make: () => quad({ L: 1.2, H: 0.6, W: 0.45, lh: 0.85, nl: 0.9, na: 0.15, head: [0.32, 0.22, 0.18], ears: 'tall', wool: true, tail: 0.2 }), c: ['#f2efe6', '#8a5a3a', '#3a3330', '#c9b49a'], sp: 0.7, f: 3.5 },
  dog: { name: '개', make: () => quad({ L: 0.8, H: 0.32, W: 0.26, lh: 0.38, nl: 0.18, na: 0.4, head: [0.28, 0.2, 0.18], snout: '#2a2422', ears: 'tall', tail: 0.3, tailUp: true }), c: ['#d8b07a', '#f2ece0', '#2a2422', '#8a5a3a', '#c9a06a'], sp: 1.3, f: 6 },
  cat: { vis: 1.15, name: '고양이', make: () => quad({ L: 0.45, H: 0.18, W: 0.15, lh: 0.2, nl: 0.06, na: 0.3, head: [0.14, 0.13, 0.13], ears: 'tall', tail: 0.32, tailUp: true }), c: ['#e09a4a', '#2a2422', '#f2ece0', '#8a8278', '#c9a06a'], sp: 0.7, f: 6 },
  deer: { name: '사슴', make: () => quad({ L: 1.35, H: 0.55, W: 0.42, lh: 0.95, nl: 0.55, na: 0.35, head: [0.38, 0.22, 0.18], ears: 'tall', horns: 'antler', tail: 0.15, tailC: '#f2efe6' }), c: ['#9a6a3e', '#8a5a32', '#a87a4a'], sp: 1.2, f: 3.8, herd: [2, 6] },
  boar: { name: '멧돼지', make: () => quad({ L: 1.3, H: 0.62, W: 0.5, lh: 0.42, nl: 0, head: [0.45, 0.4, 0.34], snout: '#3a3330', ears: 'small', tusk: true, tail: 0.2 }), c: ['#4a3a2e', '#3a2e24', '#5a4636'], sp: 0.8, f: 5, herd: [2, 5], act: 'night' },
  rabbit: { vis: 1.3, name: '토끼', make: () => quad({ L: 0.36, H: 0.2, W: 0.18, lh: 0.1, nl: 0.04, na: 0.2, head: [0.13, 0.13, 0.12], ears: 'tall', tail: 0.06, tailC: '#f2efe6' }), c: ['#9a8a78', '#c9b49a', '#f2efe6', '#7a6a5a'], sp: 1.4, f: 7, hop: true },
  fox: { name: '여우', make: () => quad({ L: 0.75, H: 0.26, W: 0.22, lh: 0.32, nl: 0.15, na: 0.4, head: [0.26, 0.17, 0.16], snout: '#2a2422', ears: 'tall', tail: 0.45, tailW: 0.12 }), c: ['#d0702a', '#c86424', '#b8601e'], sp: 1.3, f: 6, act: 'night' },
  arcticfox: { name: '북극여우', make: () => quad({ L: 0.7, H: 0.26, W: 0.22, lh: 0.28, nl: 0.12, na: 0.4, head: [0.24, 0.17, 0.16], ears: 'small', tail: 0.4, tailW: 0.12 }), c: ['#f4f6f8', '#e8ecef'], sp: 1.2, f: 6 },
  wolf: { name: '늑대', make: () => quad({ L: 1.15, H: 0.36, W: 0.3, lh: 0.55, nl: 0.25, na: 0.5, head: [0.34, 0.22, 0.2], snout: '#3a3a3a', ears: 'tall', tail: 0.45, tailW: 0.1 }), c: ['#8a8680', '#6a6660', '#a8a49c'], sp: 1.4, f: 4.5, herd: [3, 5] },
  bear: { name: '곰', make: () => quad({ L: 1.7, H: 0.9, W: 0.85, lh: 0.55, nl: 0.15, na: 0.6, head: [0.5, 0.45, 0.45], snout: '#5a4636', ears: 'small', tail: 0.08 }), c: ['#2a2220', '#5a3e28', '#3a2a20'], sp: 0.8, f: 3 },
  polarbear: { name: '북극곰', make: () => quad({ L: 1.9, H: 0.95, W: 0.9, lh: 0.6, nl: 0.35, na: 0.8, head: [0.5, 0.4, 0.4], ears: 'small', tail: 0.08 }), c: ['#f4f2ea', '#ece8dc'], sp: 0.8, f: 3 },
  squirrel: { vis: 1.6, name: '다람쥐', make: () => quad({ L: 0.2, H: 0.1, W: 0.08, lh: 0.06, nl: 0.03, na: 0.3, head: [0.07, 0.07, 0.07], ears: 'small', tail: 0.18, tailUp: true, tailW: 0.06 }), c: ['#a0602f', '#8a5a3a', '#7a6a5a'], sp: 1.2, f: 9, hop: true },
  reindeer: { name: '순록', make: () => quad({ L: 1.5, H: 0.62, W: 0.5, lh: 0.9, nl: 0.45, na: 0.4, head: [0.4, 0.25, 0.22], ears: 'small', horns: 'antler', tail: 0.12 }), c: ['#8a7a6a', '#a8988a', '#6a5a4a', '#e8e2d6'], sp: 1.0, f: 3.6, herd: [3, 8] },
  elephant: { name: '코끼리', make: () => quad({ L: 3.0, H: 1.6, W: 1.4, lh: 1.3, nl: 0, head: [0.9, 1.1, 1.0], ears: 'small', tusk: true, trunk: true, tail: 0.7, legW: 0.42 }), c: ['#8a8680', '#7a766e', '#9a958c'], sp: 0.8, f: 1.8, herd: [3, 7] },
  zebra: { name: '얼룩말', make: () => quad({ L: 1.7, H: 0.65, W: 0.5, lh: 0.95, nl: 0.7, na: 0.45, head: [0.5, 0.26, 0.22], ears: 'small', mane: '#1c1c1c', stripes: true, legC: '#f2f2ee', legTint: 0, tail: 0.5 }), c: ['#ffffff'], sp: 1.2, f: 3.6, herd: [4, 9] },
  antelope: { name: '영양', make: () => quad({ L: 1.1, H: 0.45, W: 0.32, lh: 0.8, nl: 0.4, na: 0.3, head: [0.3, 0.18, 0.15], ears: 'small', horns: 'short', belly: '#f2efe6', tail: 0.15 }), c: ['#c08a4a', '#a8783e', '#d09a5a'], sp: 1.6, f: 4.2, herd: [4, 10] },
  monkey: { name: '원숭이', make: () => quad({ L: 0.45, H: 0.3, W: 0.22, lh: 0.28, nl: 0.06, na: 0.2, head: [0.18, 0.17, 0.17], snout: '#d8b8a0', ears: 'small', tail: 0.5, tailUp: true, tailW: 0.05 }), c: ['#6a4a2a', '#8a6a4a', '#4a3a2a'], sp: 1.1, f: 6, herd: [3, 8] },
  // 땅에 사는 새
  chicken: { vis: 1.2, name: '닭', make: () => bird({ L: 0.42, legs: 0.16, comb: true, tailL: 0.28, span: 0.2 }), c: ['#f2ece0', '#a0602f', '#3a2a20', '#c9a06a'], sp: 0.35, f: 6, ground: true, peck: true },
  duck: { vis: 1.1, name: '오리', make: () => bird({ L: 0.45, legs: 0.08, beak: '#e8a020', span: 0.24, tailL: 0.2 }), c: ['#f4f2ea', '#8a6a4a', '#5a7a5a'], sp: 0.3, f: 5, ground: true, peck: true },
  egret: { name: '백로', make: () => bird({ L: 0.6, legs: 0.5, beak: '#e8c040', legC: '#2a2a2a', span: 0.5, tailL: 0.25 }), c: ['#f6f6f2'], sp: 0.15, f: 2, ground: true, peck: true },
  pigeon: { vis: 1.3, name: '비둘기', make: () => bird({ L: 0.3, legs: 0.05, beak: '#8a8680', span: 0.28 }), c: ['#8a8c94', '#a8aab2', '#6a6c74'], sp: 0.4, f: 6, ground: true, peck: true },
  // 나는 새
  sparrow: { vis: 1.8, name: '참새', make: () => bird({ L: 0.15, beak: '#3a3330', span: 0.12, headC: '#7a4a2a' }), c: ['#9a7a58', '#8a6a48'], flap: 22, fly: true },
  magpie: { name: '까치', make: () => bird({ L: 0.45, beak: '#1a1a1a', headC: '#151515', tailL: 0.55, tailC: '#152030', span: 0.38 }), c: ['#f2f2f2'], flap: 9, fly: true },
  crow: { name: '까마귀', make: () => bird({ L: 0.45, beak: '#1a1a1a', span: 0.45 }), c: ['#16161a', '#1e1e24'], flap: 7, fly: true },
  gull: { name: '갈매기', make: () => bird({ L: 0.5, beak: '#e8c040', span: 0.7, wingC: '#a8b0b8' }), c: ['#f4f6f8'], flap: 5, fly: true, glide: true },
  swallow: { vis: 1.6, name: '제비', make: () => bird({ L: 0.18, beak: '#1a1a1a', span: 0.2, tailL: 0.3, headC: '#b8402a' }), c: ['#1a2440'], flap: 14, fly: true },
  goose: { name: '기러기', make: () => bird({ L: 0.75, beak: '#3a3330', span: 0.8, headC: '#4a4440' }), c: ['#8a8278', '#9a9288'], flap: 4.5, fly: true },
  hawk: { name: '매', make: () => bird({ L: 0.55, beak: '#e8c040', span: 0.9 }), c: ['#6a4a2e', '#7a5a3a'], flap: 3, fly: true, glide: true },
  parrot: { name: '앵무새', make: () => bird({ L: 0.35, beak: '#2a2a2a', span: 0.3, tailL: 0.45 }), c: ['#2aa84a', '#e83a2a', '#2a7ae8', '#f2c02a'], flap: 12, fly: true },
  // 곤충
  butterfly: { vis: 3, name: '나비', make: () => bug('butterfly'), c: ['#ffffff', '#f2d84a', '#f29a2a', '#6aa8f2', '#f2f2e0'], flap: 14, bug: true },
  bee: { vis: 3, name: '벌', make: () => bug('bee'), c: ['#f2c02a'], flap: 60, bug: true },
  dragonfly: { vis: 2.5, name: '잠자리', make: () => bug('dragonfly'), c: ['#d8402a', '#3a7ad8', '#6a9a3a'], flap: 30, bug: true },
};

const VERT_HEAD = `attribute float part;\nattribute float tint;\nattribute vec3 aAnim;\nuniform float uTime;\nuniform float uLegTop;\nuniform float uFreq;\nuniform float uFlap;\nuniform float uHeadDrop;\n`;
function animMaterial(sp, info) {
  const m = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85, flatShading: true });
  const u = { uTime: { value: 0 }, uLegTop: { value: info.legTop }, uFreq: { value: sp.f || 4 }, uFlap: { value: sp.flap || 0 }, uHeadDrop: { value: info.headDrop } };
  m.userData.u = u;
  m.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = VERT_HEAD + sh.vertexShader
      .replace('#include <color_vertex>', `vColor = vec3(1.0); vColor *= color;\n#ifdef USE_INSTANCING_COLOR\n vColor.xyz *= mix(vec3(1.0), instanceColor.xyz, tint);\n#endif`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        float ph = aAnim.x, mv = aAnim.y, gz = aAnim.z;
        float sw = sin(uTime * uFreq + ph);
        if (part > 0.5 && part < 4.5) {                      // 다리: 위를 축으로 앞뒤로
          float sd = (part < 2.5) ? 1.0 : -1.0;
          transformed.z += sw * sd * mv * (uLegTop - transformed.y) * 0.55;
        } else if (part > 4.5 && part < 5.5) {               // 머리: 풀 뜯기·모이 쪼기
          transformed.y -= gz * uHeadDrop * (0.75 + 0.25 * sin(uTime * 5.0 + ph));
          transformed.z += gz * uHeadDrop * 0.25;
        } else if (part > 5.5 && part < 7.5) {               // 날개
          transformed.y += sin(uTime * uFlap + ph) * abs(transformed.x) * 1.1 * mv;
        } else if (part > 7.5) {                             // 꼬리
          transformed.x += sin(uTime * 4.0 + ph) * 0.08 * (0.3 + mv);
        }`);
  };
  m.customProgramCacheKey = () => 'fauna-anim';
  return m;
}

/** 마을 하나의 동물들 */
export class Fauna {
  constructor(g, t) { this.g = g; this.t = t; this.meshes = new Map(); this.list = []; this.key = ''; this.firefly = null; }

  mesh(key, n) {
    let e = this.meshes.get(key);
    if (e && e.cap >= n) return e;
    if (e) { this.g.remove(e.im); e.im.geometry.dispose(); e.im.material.dispose(); e.im.dispose(); }
    const sp = SPECIES[key], info = sp.make();
    const cap = Math.max(8, 2 ** Math.ceil(Math.log2(n + 1)));
    info.geo.setAttribute('aAnim', new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3));
    const im = new THREE.InstancedMesh(info.geo, animMaterial(sp, info), cap);
    im.frustumCulled = false; im.count = 0;
    this.g.add(im);
    e = { im, cap, info, anim: info.geo.attributes.aAnim };
    this.meshes.set(key, e);
    return e;
  }

  /**
   * 누가 어디에 사는지 정한다 (마을이 바뀌면 다시). env: { blds, era, biome, lat, lon, T(지형), devR, season, coast, river }
   */
  spawn(env) {
    const { blds, era, biome, lat, lon, T, season } = env;
    const key = `${era}|${season}|${blds.length}|${Math.round(env.devR / 40)}`;
    if (key === this.key) return;
    this.key = key;
    const seed = this.t.s.id * 7.13;
    const L = [];
    let k = 0;
    const r = () => R(seed, k++);
    const ok = (x, z) => { const p = T.sample(x, z); return !p.water && p.h > 1 && p.h < 700; };
    const add = (sp, x, z, o = {}) => {
      const s = SPECIES[sp], h = o.herd;
      if (h && h.pa == null) { h.pa = r() * 6.28; h.pb = r() * 6.28; h.f1 = 0.02 + r() * 0.03; h.speed = (s.sp || 0.5) * (0.8 + r() * 0.4); }
      L.push({ sp, x, z, rad: o.rad ?? 6, speed: h ? h.speed : (s.sp || 0.5) * (0.8 + r() * 0.4), ph: r() * 6.28, pa: h ? h.pa : r() * 6.28, pb: h ? h.pb : r() * 6.28, f1: h ? h.f1 : 0.02 + r() * 0.03, col: pick(s.c, r()), size: o.size ?? (0.85 + r() * 0.3) * (s.vis || 1), herd: h ?? null, off: o.off ?? [0, 0], act: s.act || 'day', fly: !!s.fly, bug: !!s.bug, ground: !!s.ground, peck: !!s.peck, hop: !!s.hop, alt: o.alt ?? 0 });
    };
    const cap = PHONE ? 0.55 : 1;
    // 지역
    const eastAsia = lon > 100 && lon < 146 && lat > 18 && lat < 52, korea = lon > 124 && lon < 131 && lat > 33 && lat < 43;
    const africa = lon > -20 && lon < 52 && lat > -36 && lat < 37, southAsia = lon > 65 && lon < 100 && lat > 5 && lat < 35;
    const andes = lon > -82 && lon < -62 && lat > -40 && lat < 5, rice = eastAsia || southAsia || (lon > 95 && lon < 125 && lat > -10 && lat < 25);
    const desert = biome === 'desert', snow = biome === 'snow', tropical = biome === 'tropical';

    // ── 기르는 짐승: 목장(밭의 일부)과 집 마당 ──
    if (era >= 1) {
      const herdKinds = desert ? ['camel', 'goat', 'sheep'] : snow ? ['reindeer', 'sheep'] : andes ? ['llama', 'sheep', 'cow'] : rice && tropical ? ['buffalo', 'pig', 'goat'] : rice ? ['cow', 'pig', 'goat', 'cow'] : ['cow', 'sheep', 'pig', 'goat', 'cow'];
      if (era >= 2 && !desert) herdKinds.push('horse');
      let n = 0;
      for (const b of blds) {
        if (b.type !== 'farm' || !b.done) continue;
        const hv = R(b.id, 3);
        if (hv < 0.45) {   // 목장
          const sp = pick(herdKinds, R(b.id, 5));
          const cnt = Math.round((3 + R(b.id, 7) * 9) * cap);
          for (let i = 0; i < cnt && n < 260 * cap; i++, n++) add(sp, b.x + (r() - 0.5) * b.w * 0.6, b.z + (r() - 0.5) * b.d * 0.6, { rad: Math.min(b.w, b.d) * 0.3 });
        } else if (rice && hv < 0.75) {   // 논: 오리·백로
          for (let i = 0; i < 2 + Math.floor(R(b.id, 9) * 4); i++) add(R(b.id, 11 + i) < 0.5 ? 'duck' : 'egret', b.x + (r() - 0.5) * b.w * 0.7, b.z + (r() - 0.5) * b.d * 0.7, { rad: 6 });
        }
      }
    }
    // 집 마당: 닭·개·고양이 (옛 마을), 근대 도시는 개·고양이만 조금
    let yard = 0;
    for (const b of blds) {
      if (!b.done || b.infra) continue;
      const kind = env.kind(b.type);
      if (kind !== 'home') continue;
      const h = R(b.id, 21), side = R(b.id, 23) < 0.5 ? -1 : 1;
      const yx = b.x + side * (b.w / 2 + 3), yz = b.z + b.d / 2 + 3;
      if (era >= 1 && era <= 4 && h < 0.55 && yard < 220 * cap) { const c = 2 + Math.floor(R(b.id, 25) * 5); for (let i = 0; i < c; i++, yard++) add('chicken', yx + (r() - 0.5) * 4, yz + (r() - 0.5) * 4, { rad: 3.5 }); }
      if (R(b.id, 27) < (era === 0 ? 0.15 : 0.35)) add('dog', yx, yz + 1, { rad: era >= 5 ? 14 : 8 });
      if (era >= 2 && R(b.id, 29) < 0.25) add('cat', b.x - side * (b.w / 2 + 1.5), b.z, { rad: 6 });
    }
    // ── 들짐승: 마을 밖 숲과 들 ──
    const wildK = Math.max(0.2, 1 - era * 0.12) * cap;
    const wild = desert ? [['camel', 3], ['fox', 3]] : snow ? [['reindeer', 4], ['arcticfox', 3], ['wolf', 1], ...(env.coast ? [['polarbear', 1]] : [])]
      : tropical && africa ? [['elephant', 2], ['antelope', 3], ['zebra', 2], ['monkey', 3]]
      : tropical ? [['monkey', 4], ['boar', 2], ...(southAsia ? [['elephant', 2]] : []), ['deer', 2]]
      : africa ? [['antelope', 3], ['zebra', 2], ['fox', 2]]
      : [['deer', 3], ['boar', 3], ['rabbit', 5], ['fox', 2], ['squirrel', 4], ...(era <= 2 ? [['wolf', 1]] : []), ['bear', korea ? 0.5 : 1]];
    const R0 = env.devR + 70, R1 = Math.min(2600, env.devR + 900);
    for (const [sp, w] of wild) {
      const groups = Math.round(w * 2.2 * wildK + r() * 0.8);
      const [h0, h1] = SPECIES[sp].herd || [1, 2];
      for (let g = 0; g < groups; g++) {
        let x = 0, z = 0, found = false;
        for (let tries = 0; tries < 12 && !found; tries++) {
          const a = r() * 6.28, d = R0 + r() * (R1 - R0);
          x = Math.cos(a) * d; z = Math.sin(a) * d;
          found = ok(x, z) && (sp === 'squirrel' ? T.forest(x, z) > 0.3 : true);
        }
        if (!found) continue;
        const n = h0 + Math.floor(r() * (h1 - h0 + 1)), herd = { x, z };
        for (let i = 0; i < n; i++) add(sp, x, z, { rad: sp === 'squirrel' || sp === 'rabbit' ? 15 : 60, herd, off: [(r() - 0.5) * 8, (r() - 0.5) * 8] });
      }
    }
    // ── 새: 무리지어 날거나, 땅에서 모이를 쫀다 ──
    const flyers = [];
    if (env.coast) flyers.push(['gull', 2]);
    if (tropical) flyers.push(['parrot', 2]);
    if (eastAsia) flyers.push(['magpie', 2]);
    flyers.push(['crow', 1], ['sparrow', 2], ['hawk', 0.5]);
    if (season === '여름' && !snow && !desert) flyers.push(['swallow', 2]);
    for (const [sp, w] of flyers) {
      const flocks = Math.round(w * (1.5 + r()) * cap);
      for (let f = 0; f < flocks; f++) {
        const a = r() * 6.28, d = r() * (env.devR + 400), cx = Math.cos(a) * d, cz = Math.sin(a) * d;
        const n = sp === 'hawk' ? 1 : sp === 'magpie' ? 2 : 4 + Math.floor(r() * 7);
        const herd = { x: cx, z: cz };
        for (let i = 0; i < n; i++) add(sp, cx, cz, { rad: sp === 'hawk' ? 160 : 40 + r() * 60, herd, off: [(r() - 0.5) * 10, (r() - 0.5) * 10], alt: sp === 'hawk' ? 90 : sp === 'gull' ? 35 : sp === 'swallow' ? 6 : 14 + r() * 14 });
      }
    }
    if (era >= 4) for (let i = 0; i < 18 * cap; i++) add('pigeon', (r() - 0.5) * 40, (r() - 0.5) * 40, { rad: 10 });
    // 가을·겨울 하늘을 지나가는 기러기 떼 (V자)
    if ((season === '가을' || season === '겨울') && !tropical) {
      const herd = { x: 0, z: 0, migrate: true };
      for (let i = 0; i < 13; i++) { const row = Math.ceil(i / 2), sd = i % 2 ? 1 : -1; add('goose', 0, 0, { herd, off: [sd * row * 3.2, -row * 3.2], alt: 120 }); }
    }
    // ── 곤충 (가까이서만 보인다): 꽃과 마당·밭 둘레, 물가 ──
    const homes = blds.filter(b => b.done && env.kind(b.type) === 'home');
    const fields = blds.filter(b => b.done && b.type === 'farm');
    const spots = [...homes.map(b => [b.x + (R(b.id, 31) - 0.5) * b.w, b.z + b.d / 2 + 2]), ...fields.map(b => [b.x, b.z])];
    if ((season === '봄' || season === '여름') && !snow && spots.length) {
      for (let i = 0; i < 46 * cap; i++) { const [x, z] = spots[i % spots.length]; add('butterfly', x + (r() - 0.5) * 8, z + (r() - 0.5) * 8, { rad: 4, alt: 0.8 + r() * 1.2 }); }
      for (let i = 0; i < 28 * cap; i++) { const [x, z] = spots[(i * 7) % spots.length]; add('bee', x + (r() - 0.5) * 5, z + (r() - 0.5) * 5, { rad: 2, alt: 0.6 + r() * 0.6 }); }
    }
    if ((season === '여름' || season === '가을') && !snow) for (let i = 0; i < 26 * cap; i++) {
      const [x, z] = spots.length ? spots[(i * 5) % spots.length] : [0, 0];
      const sp = 'dragonfly'; add(sp, x + (r() - 0.5) * 20, z + (r() - 0.5) * 20, { rad: 7, alt: 1.2 + r() * 1.8 });
      L[L.length - 1].col = season === '가을' ? '#d8402a' : pick(SPECIES.dragonfly.c, r());
    }
    // 반딧불이: 여름밤, 풀숲과 물가 (큰 도시엔 거의 없다)
    this.fireflies = [];
    if (season === '여름' && !snow && !desert && era <= 5) for (let i = 0; i < 90 * cap; i++) {
      const a = r() * 6.28, d = env.devR * 0.6 + 20 + r() * 220, x = Math.cos(a) * d, z = Math.sin(a) * d;
      if (ok(x, z)) this.fireflies.push({ x, z, ph: r() * 6.28, sp: 0.5 + r(), alt: 0.4 + r() * 1.4 });
    }
    this.list = L;
    // 종류별 그릇
    const count = new Map();
    for (const a of L) count.set(a.sp, (count.get(a.sp) || 0) + 1);
    for (const [sp, n] of count) this.mesh(sp, n);
    for (const [sp, e] of this.meshes) if (!count.has(sp)) e.im.count = 0;
    if (this.fireflies.length && !this.firefly) {
      const geo = new THREE.IcosahedronGeometry(0.06, 0);
      this.firefly = new THREE.InstancedMesh(geo, new THREE.MeshBasicMaterial({ color: 0xd8ff6a, transparent: true, opacity: 0.95, depthWrite: false }), 128);
      this.firefly.frustumCulled = false; this.firefly.count = 0;
      this.g.add(this.firefly);
    }
  }

  /** 매 프레임: 자리·방향·걸음 (time: 실제 초, env: { night, rain, P, rot, hAt, near, close }) */
  update(time, env) {
    const { night, rain, P, rot, hAt, near, close } = env;
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3(), s = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0), col = new THREE.Color();
    const bank = new THREE.Quaternion(), zAx = new THREE.Vector3(0, 0, 1);
    const idx = new Map();
    for (const [sp, e] of this.meshes) { idx.set(sp, 0); e.im.visible = near; }
    if (!near) { if (this.firefly) this.firefly.visible = false; return; }
    for (const a of this.list) {
      const e = this.meshes.get(a.sp);
      if (!e) continue;
      // 쉬는 때: 낮짐승은 밤에, 밤짐승은 낮에 (새는 밤엔 둥지로, 곤충은 비 오거나 밤이면 없다)
      const awake = a.act === 'night' ? 0.3 + night * 0.7 : 1 - night * 0.85;
      if ((a.fly || a.bug) && (night > 0.55 || rain > 0.3)) continue;
      if (a.bug && !close) continue;
      const i = idx.get(a.sp);
      if (i >= e.cap) continue;
      idx.set(a.sp, i + 1);
      // 길: 무리 가운데를 축으로 천천히 도는 두 겹 물결, '멈칫' 시간(풀 뜯기·쉬기)이 섞인다
      const T0 = time * (a.fly ? 0.6 : 1) * (a.bug ? 2.2 : 1);
      const gate = 0.5 + 0.5 * Math.sin(T0 * a.f1 * 2.3 + a.pb);           // 0 이면 멈춰 서서 풀을 뜯는다
      const go = a.fly || a.bug ? 1 : Math.max(0, gate * 1.3 - 0.3) * awake;
      const u = T0 * a.speed / Math.max(4, a.rad) * 0.9 - (a.fly || a.bug ? 0 : Math.sin(T0 * a.f1 * 2.3 + a.pb) * 0.8);
      let cx = a.herd ? a.herd.x : a.x, cz = a.herd ? a.herd.z : a.z;
      let x, z, dx, dz;
      if (a.herd?.migrate) {
        // 하늘을 가로질러 지나간다 (북동→남서), 4분마다 한 번
        const per = 240, k = ((time / per) % 1), L = 2400, dir = 3.9;
        const ox = Math.cos(dir), oz = Math.sin(dir), px = -oz, pz = ox;
        const along = (k - 0.5) * L;
        x = ox * along + px * a.off[0] + ox * a.off[1]; z = oz * along + pz * a.off[0] + oz * a.off[1];
        dx = ox; dz = oz;
      } else {
        const w1 = u + a.pa, w2 = u * 2.3 + a.pb;
        x = cx + a.off[0] + a.rad * (0.7 * Math.sin(w1) + 0.3 * Math.sin(w2));
        z = cz + a.off[1] + a.rad * (0.7 * Math.cos(w1 * 0.9) + 0.3 * Math.cos(w2 * 1.1));
        dx = 0.7 * Math.cos(w1) + 0.69 * Math.cos(w2); dz = -0.63 * Math.sin(w1 * 0.9) - 0.76 * Math.sin(w2 * 1.1);
      }
      const head = Math.atan2(dx, dz) + rot(x, z);
      let y = hAt(x, z) + 0.45;
      if (a.fly) y += a.alt + Math.sin(T0 * 0.7 + a.ph) * 2;
      else if (a.bug) y += a.alt + Math.sin(T0 * 3.1 + a.ph) * 0.25;
      else if (a.hop) y += Math.abs(Math.sin(time * 6 + a.ph)) * 0.22 * go;
      const [wx, wz] = P(x, z);
      p.set(wx, y, wz);
      q.setFromAxisAngle(up, head);
      if (a.fly) q.multiply(bank.setFromAxisAngle(zAx, Math.sin(T0 * 0.5 + a.ph) * 0.25));
      s.setScalar(a.size);
      m.compose(p, q, s);
      e.im.setMatrixAt(i, m);
      e.im.setColorAt(i, col.set(a.col));
      const glide = SPECIES[a.sp].glide && Math.sin(T0 * 0.3 + a.ph) > 0.2 ? 0.15 : 1;
      e.anim.setXYZ(i, a.ph, a.fly || a.bug ? glide : go, a.peck || a.ground ? (1 - go) * awake : (1 - go) * awake * 0.9);
    }
    for (const [sp, e] of this.meshes) {
      e.im.count = idx.get(sp) || 0;
      e.im.instanceMatrix.needsUpdate = true; if (e.im.instanceColor) e.im.instanceColor.needsUpdate = true; e.anim.needsUpdate = true;
      e.im.material.userData.u.uTime.value = time;
    }
    // 반딧불이: 밤에만, 깜빡깜빡
    if (this.firefly) {
      const on = night > 0.6 && rain < 0.3 && close;
      this.firefly.visible = on;
      if (on) {
        let n = 0;
        for (const f of this.fireflies) {
          if (n >= 128) break;
          const blink = Math.max(0, Math.sin(time * 2.2 * f.sp + f.ph));
          const x = f.x + Math.sin(time * 0.3 * f.sp + f.ph) * 3, z = f.z + Math.cos(time * 0.25 * f.sp + f.ph * 2) * 3;
          const [wx, wz] = P(x, z);
          p.set(wx, hAt(x, z) + 0.45 + f.alt + Math.sin(time * 0.8 + f.ph) * 0.3, wz);
          s.setScalar(0.3 + blink * 1.1);
          m.compose(p, q.identity(), s);
          this.firefly.setMatrixAt(n++, m);
        }
        this.firefly.count = n; this.firefly.instanceMatrix.needsUpdate = true;
      }
    }
  }

  dispose() {
    for (const e of this.meshes.values()) { this.g.remove(e.im); e.im.geometry.dispose(); e.im.material.dispose(); e.im.dispose(); }
    this.meshes.clear();
    if (this.firefly) { this.g.remove(this.firefly); this.firefly.geometry.dispose(); this.firefly.material.dispose(); this.firefly.dispose(); this.firefly = null; }
  }
}

export const FAUNA_NAMES = Object.fromEntries(Object.entries(SPECIES).map(([k, v]) => [k, v.name]));
