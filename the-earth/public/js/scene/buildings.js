// 건물 모양.
// 공사 중: 펜스·터파기 → 기초 → 골조(철근콘크리트 기둥·슬래브 또는 나무 기둥) + 비계·안전망 + 타워크레인 → 외벽 → 지붕 → 완공.
// 완공: 시대·용도별 외벽(흙벽, 초가, 한옥, 벽돌, 콘크리트, 유리, 공장 철판), 문, 창(밤에는 불빛), 간판·차양, 발코니, 옥상 설비, 굴뚝.
import * as THREE from 'three';

const hash = n => { let h = (n * 2654435761) >>> 0; h ^= h >>> 15; return (h % 1000) / 1000; };

const GEO = {
  box: new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0),
  cone: new THREE.ConeGeometry(0.74, 1, 12).translate(0, 0.5, 0),
  pyr: new THREE.ConeGeometry(0.72, 1, 4).rotateY(Math.PI / 4).translate(0, 0.5, 0),
  dome: new THREE.SphereGeometry(0.5, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
  cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 14).translate(0, 0.5, 0),
  flame: new THREE.ConeGeometry(0.5, 1, 7).translate(0, 0.5, 0),
  torus: new THREE.TorusGeometry(1, 0.22, 10, 40).rotateX(Math.PI / 2),
  prism: (() => {
    const s = new THREE.Shape(); s.moveTo(-0.5, 0); s.lineTo(0.5, 0); s.lineTo(0, 1); s.closePath();
    return new THREE.ExtrudeGeometry(s, { depth: 1, bevelEnabled: false }).translate(0, 0, -0.5);
  })(),
  // 처마가 살짝 들린 한옥 지붕 (단면)
  hanokRoof: (() => {
    const s = new THREE.Shape();
    s.moveTo(-0.62, 0.02); s.quadraticCurveTo(-0.45, 0.08, -0.3, 0.42); s.lineTo(-0.06, 0.92); s.lineTo(0.06, 0.92); s.lineTo(0.3, 0.42); s.quadraticCurveTo(0.45, 0.08, 0.62, 0.02); s.lineTo(0.5, 0.1); s.lineTo(-0.5, 0.1); s.closePath();
    return new THREE.ExtrudeGeometry(s, { depth: 1, bevelEnabled: false, curveSegments: 6 }).translate(0, 0, -0.5);
  })(),
};
export const BGEO = GEO;

// ── 외벽 텍스처: 4×4 칸(창 16개)을 한 장에. 낮 색(map)과 밤 불빛(emissive) ──
const STYLE = {
  mud: { tw: 4, th: 3 }, timber: { tw: 5, th: 3.2 }, hanok: { tw: 3.2, th: 3.4 }, brick: { tw: 4, th: 3.4 },
  concrete: { tw: 4, th: 3.2 }, glass: { tw: 3, th: 3.6 }, industrial: { tw: 6, th: 5 }, civic: { tw: 4.5, th: 3.8 },
};
const texCache = new Map();
function wallTexture(style, color) {
  const key = style + color;
  if (texCache.has(key)) return texCache.get(key);
  const S = 512, C = S / 4;
  const a = document.createElement('canvas'); a.width = a.height = S;
  const e = document.createElement('canvas'); e.width = e.height = S;
  const g = a.getContext('2d'), ge = e.getContext('2d');
  const base = new THREE.Color(color);
  const css = (c, k = 1) => `#${c.clone().multiplyScalar(k).getHexString()}`;
  ge.fillStyle = '#000'; ge.fillRect(0, 0, S, S);
  g.fillStyle = css(base); g.fillRect(0, 0, S, S);
  const lit = (x, y, w, h, p = 0.62) => { if (Math.random() < p) { ge.fillStyle = Math.random() < 0.5 ? '#ffd58a' : '#ffe8c0'; ge.fillRect(x, y, w, h); } };
  const noise = (k, n = 900) => { for (let i = 0; i < n; i++) { g.fillStyle = `rgba(${k ? '255,255,255' : '0,0,0'},${Math.random() * 0.06})`; g.fillRect(Math.random() * S, Math.random() * S, 2 + Math.random() * 4, 2 + Math.random() * 4); } };
  for (let ty = 0; ty < 4; ty++) for (let tx = 0; tx < 4; tx++) {
    const x = tx * C, y = ty * C;
    if (style === 'mud' || style === 'timber') {
      if (style === 'timber') { g.fillStyle = '#6b4a2e'; g.fillRect(x, y, 9, C); g.fillRect(x, y, C, 8); g.fillRect(x, y + C - 10, C, 10); }
      if ((tx + ty) % 2 === 0) {
        const wx = x + C * 0.36, wy = y + C * 0.3, ww = C * 0.28, wh = C * 0.3;
        g.fillStyle = '#3b2a1c'; g.fillRect(wx - 3, wy - 3, ww + 6, wh + 6);
        g.fillStyle = '#e9dcbc'; g.fillRect(wx, wy, ww, wh);
        g.strokeStyle = '#5a4128'; g.lineWidth = 2; for (let k = 1; k < 3; k++) { g.beginPath(); g.moveTo(wx + ww * k / 3, wy); g.lineTo(wx + ww * k / 3, wy + wh); g.stroke(); g.beginPath(); g.moveTo(wx, wy + wh * k / 3); g.lineTo(wx + ww, wy + wh * k / 3); g.stroke(); }
        lit(wx, wy, ww, wh, 0.5);
      }
    } else if (style === 'hanok') {
      g.fillStyle = '#4a3322'; g.fillRect(x, y, 10, C); g.fillRect(x, y, C, 10); g.fillRect(x, y + C - 14, C, 14);
      const wx = x + 22, wy = y + 24, ww = C - 36, wh = C - 52;
      g.fillStyle = '#efe4c8'; g.fillRect(wx, wy, ww, wh);
      g.strokeStyle = '#6a4a30'; g.lineWidth = 2;
      for (let k = 1; k < 5; k++) { g.beginPath(); g.moveTo(wx + ww * k / 5, wy); g.lineTo(wx + ww * k / 5, wy + wh); g.stroke(); }
      for (let k = 1; k < 4; k++) { g.beginPath(); g.moveTo(wx, wy + wh * k / 4); g.lineTo(wx + ww, wy + wh * k / 4); g.stroke(); }
      g.strokeRect(wx, wy, ww, wh);
      lit(wx, wy, ww, wh, 0.55);
    } else if (style === 'brick') {
      const bh = 8, bw = 22;
      for (let r = 0; r < C / bh; r++) for (let k = -1; k < C / bw + 1; k++) {
        const v = 0.86 + Math.random() * 0.22;
        g.fillStyle = css(base, v); g.fillRect(x + k * bw + (r % 2 ? bw / 2 : 0), y + r * bh, bw - 2, bh - 2);
      }
      const wx = x + C * 0.3, wy = y + C * 0.22, ww = C * 0.4, wh = C * 0.5;
      g.fillStyle = '#f2ede4'; g.fillRect(wx - 5, wy - 5, ww + 10, wh + 10);
      g.fillStyle = '#4f6478'; g.fillRect(wx, wy, ww, wh);
      g.fillStyle = 'rgba(255,255,255,0.25)'; g.fillRect(wx + 3, wy + 3, ww * 0.3, wh - 6);
      g.fillStyle = '#f2ede4'; g.fillRect(wx + ww / 2 - 2, wy, 4, wh); g.fillRect(wx, wy + wh * 0.45, ww, 4);
      g.fillStyle = css(base, 0.7); g.fillRect(wx - 7, wy + wh + 5, ww + 14, 6);
      lit(wx, wy, ww, wh);
    } else if (style === 'concrete' || style === 'civic') {
      noise(1, 60); noise(0, 60);
      const wx = x + C * 0.12, wy = y + C * 0.22, ww = C * 0.76, wh = C * 0.48;
      g.fillStyle = '#3e5266'; g.fillRect(wx, wy, ww, wh);
      g.fillStyle = 'rgba(190,220,255,0.28)'; g.fillRect(wx + 4, wy + 4, ww * 0.35, wh - 8);
      g.fillStyle = css(base, 0.92); g.fillRect(wx + ww / 3 - 2, wy, 4, wh); g.fillRect(wx + ww * 2 / 3 - 2, wy, 4, wh);
      g.fillStyle = css(base, 0.78); g.fillRect(x, y + C - 6, C, 6); // 층 띠
      lit(wx, wy, ww, wh, style === 'civic' ? 0.45 : 0.65);
    } else if (style === 'glass') {
      const grd = g.createLinearGradient(x, y, x + C, y + C);
      grd.addColorStop(0, '#5f8fb3'); grd.addColorStop(1, '#2f5675');
      g.fillStyle = grd; g.fillRect(x, y, C, C);
      g.fillStyle = 'rgba(255,255,255,0.18)'; g.fillRect(x + 6, y + 4, C * 0.25, C - 30);
      g.fillStyle = '#c7d3dc'; g.fillRect(x, y, 3, C); g.fillRect(x, y + C - 22, C, 22);
      lit(x + 3, y, C - 3, C - 22, 0.7);
    } else if (style === 'industrial') {
      for (let k = 0; k < C; k += 8) { g.fillStyle = css(base, k % 16 ? 0.93 : 1.05); g.fillRect(x + k, y, 4, C); }
      if (ty === 0) { g.fillStyle = '#41535f'; g.fillRect(x + 6, y + 14, C - 12, 22); lit(x + 6, y + 14, C - 12, 22, 0.8); }
      g.fillStyle = 'rgba(0,0,0,0.12)'; g.fillRect(x, y + C - 4, C, 4);
    }
  }
  const ta = new THREE.CanvasTexture(a), te = new THREE.CanvasTexture(e);
  for (const t of [ta, te]) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; }
  const out = { map: ta, emissive: te, ...STYLE[style] };
  texCache.set(key, out);
  return out;
}

/** 벽 크기에 맞춘 UV: 텍스처 한 장 = 4×4칸 */
function wallBox(w, h, d, tw, th) {
  const g = new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0);
  const uv = g.attributes.uv;
  const spans = [[d, h], [d, h], null, null, [w, h], [w, h]];
  for (let f = 0; f < 6; f++) for (let k = 0; k < 4; k++) {
    const i = f * 4 + k;
    if (!spans[f]) { uv.setXY(i, 0.01, 0.97); continue; }
    uv.setXY(i, uv.getX(i) * spans[f][0] / (tw * 4), uv.getY(i) * spans[f][1] / (th * 4));
  }
  return g;
}

export function styleOf(type, def) {
  if (def.glass) return 'glass';
  if (type === 'hut') return 'mud';
  if (def.roof === 'saw' || ['carplant', 'chemplant', 'powerplant', 'mine'].includes(type)) return 'industrial';
  if ((def.era ?? 0) <= 1) return 'timber';
  if (def.era === 2) return type === 'dock' ? 'timber' : 'hanok';
  if (def.era === 3) return 'brick';
  if (def.kind === 'civic') return 'civic';
  return 'concrete';
}
const SHOPS = new Set(['restaurant', 'market', 'tailorshop', 'bakery', 'salon', 'cafe', 'supermarket', 'bank', 'clinic', 'studio', 'forge', 'postoffice', 'hotel']);

/** 건물 하나 → THREE.Group */
export function makeBuilding(cx, b, def, opts) {
  const { era = 0, snow = 0, hAt, cold = false } = opts;
  const grp = new THREE.Group();
  grp.position.set(b.x, hAt(b.x, b.z) + 0.3, b.z);
  grp.userData.bid = b.id;
  const v = hash(b.id);
  const col = new THREE.Color(def.color).offsetHSL((v - 0.5) * 0.04, 0, (v - 0.5) * 0.1);
  const hex = '#' + col.getHexString();
  const add = (geo, material, sx, sy, sz, x = 0, y = 0, z = 0, ry = 0) => {
    const m = new THREE.Mesh(geo, material);
    m.scale.set(sx, sy, sz); m.position.set(x, y, z); m.rotation.y = ry;
    m.userData.bid = b.id;
    grp.add(m);
    return m;
  };
  const M = (c, o) => cx.mat(c, o);
  const snowy = (c, k = 0.85) => (snow > 0.05 ? '#' + new THREE.Color(c).lerp(new THREE.Color('#f4f7fa'), Math.min(1, snow) * k).getHexString() : c);

  // 기초 (경사지에서 떠 보이지 않게)
  if (b.type !== 'farm' && b.type !== 'campfire') add(GEO.box, M('#7d746a'), b.w + 0.8, 4, b.d + 0.8, 0, -3.9, 0);

  if (def.roof === 'field') { // 밭: 지면에 그리고, 헛간 하나
    if (b.done) {
      const bx = b.w / 2 - 5, bz = -b.d / 2 + 5;
      add(GEO.box, M('#8b4a32'), 6, 3.2, 5, bx, 0, bz);
      add(GEO.prism, M(snowy('#5a3324')), 6.6, 2, 5.4, bx, 3.2, bz, Math.PI / 2);
    }
    return grp;
  }
  if (def.roof === 'fire') {
    add(GEO.cyl, M('#6b6158'), 3.4, 0.35, 3.4);
    add(GEO.box, M('#5a3d22'), 2.6, 0.35, 0.35, 0, 0.35, 0, 0.6);
    add(GEO.box, M('#5a3d22'), 2.6, 0.35, 0.35, 0, 0.35, 0, -0.6);
    const f = add(GEO.flame, new THREE.MeshBasicMaterial({ color: 0xff8a2a, transparent: true, opacity: 0.9 }), 1.2, 1.9, 1.2, 0, 0.5, 0);
    cx.flames.push(f);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: cx.glowTex, color: 0xffa040, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    glow.scale.set(14, 14, 1); glow.position.y = 1.5; grp.add(glow);
    return grp;
  }

  if (!b.done) construction(cx, b, def, grp, add, { era, hex, snowy });
  else finished(cx, b, def, grp, add, { era, hex, snowy, v, cold });
  if (b.fire) fireFx(cx, b, grp);
  return grp;
}

// ── 공사 중 ──
function construction(cx, b, def, grp, add, { era, hex, snowy }) {
  const M = (c, o) => cx.mat(c, o);
  const p = Math.max(0, Math.min(1, b.p || 0));
  const W = b.w, D = b.d, H = Math.max(2.4, b.h);
  const modern = era >= 4 || (def.era ?? 0) >= 4;
  const tall = H >= 14;
  const burnt = b.burnt && p < 0.35;
  // 펜스
  const fenceCol = modern ? '#e8eef2' : '#8a6a45', fh = modern ? 2.4 : 1.4;
  const fx = W / 2 + 3.2, fz = D / 2 + 3.2;
  add(GEO.box, M(fenceCol), W + 6.4, fh, 0.15, 0, 0, fz);
  add(GEO.box, M(fenceCol), W + 6.4, fh, 0.15, 0, 0, -fz);
  add(GEO.box, M(fenceCol), 0.15, fh, D + 6.4, fx, 0, 0);
  add(GEO.box, M(fenceCol), 0.15, fh, D + 6.4, -fx, 0, 0);
  if (modern) add(GEO.box, M('#2b6cb0'), W * 0.5, 0.7, 0.18, 0, 1.2, fz + 0.05); // 공사 안내판
  // 자재 더미
  const pile = (x, z, c, n = 3) => { for (let k = 0; k < n; k++) add(GEO.box, M(c), 2.2 - k * 0.4, 0.45, 1.4 - k * 0.2, x, k * 0.45, z); };
  if (p < 0.92) {
    pile(fx - 2.2, fz - 1.8, modern ? '#9aa1a8' : '#a45a3c');
    pile(-fx + 2.2, fz - 1.8, '#9b7a52', 2);
  }
  if (p < 0.08) {
    // 터파기: 흙이 드러난 바닥과 흙더미, (산업 시대부터) 굴착기
    add(GEO.box, M('#6d5136'), W, 0.25, D, 0, -0.1, 0);
    add(GEO.cone, M('#7a5a3b'), 4, 2.2, 4, -W / 2 + 2, 0, -D / 2 + 2);
    if (modern) excavator(add, M, W * 0.2, D * 0.1);
    return;
  }
  // 기초 슬래브
  add(GEO.box, M(modern ? '#a9aaa5' : '#8d8478'), W, 0.6, D, 0, 0, 0);
  if (p < 0.2) { if (modern && p < 0.14) excavator(add, M, W * 0.25, -D * 0.2); return; }
  const k = Math.min(1, (p - 0.2) / 0.62); // 골조 높이 비율
  const hNow = Math.max(1.2, H * k);
  const frameCol = burnt ? '#2b2622' : modern ? '#b9b8b2' : '#8a6239';
  // 기둥
  const nx = Math.max(2, Math.round(W / 6) + 1), nz = Math.max(2, Math.round(D / 6) + 1);
  const cs = modern ? 0.7 : 0.45;
  for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) {
    if (i > 0 && i < nx - 1 && j > 0 && j < nz - 1 && (i + j) % 2) continue;
    add(GEO.box, M(frameCol), cs, hNow, cs, -W / 2 + cs / 2 + i * (W - cs) / (nx - 1), 0.6, -D / 2 + cs / 2 + j * (D - cs) / (nz - 1));
  }
  // 층 슬래브(현대) 또는 들보(전통)
  const fl = modern ? 3.4 : Math.max(2.4, H * 0.8);
  for (let y = fl; y <= hNow + 0.01; y += fl) add(GEO.box, M(frameCol), W, modern ? 0.35 : 0.3, modern ? D : 0.35, 0, y + 0.6, 0);
  if (!modern) for (let y = fl; y <= hNow + 0.01; y += fl) add(GEO.box, M(frameCol), 0.35, 0.3, D, 0, y + 0.6, 0);
  // 외벽이 아래부터 차오른다 (골조가 올라간 뒤)
  const wallK = Math.max(0, (p - 0.45) / 0.45);
  if (wallK > 0) {
    const st = styleOf(b.type, def);
    const tex = wallTexture(st, hex);
    const wh = Math.min(hNow, H * wallK);
    const m = new THREE.Mesh(wallBox(W * 0.98, wh, D * 0.98, tex.tw, tex.th), cx.wallMat(st, hex, tex));
    m.position.y = 0.6; m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
  }
  // 지붕은 마지막에
  if (p > 0.86) roof(cx, b, def, add, H + 0.6, hex, snowy, (p - 0.86) / 0.14);
  // 비계(파이프)와 안전망
  const sh = Math.min(H, hNow + 1.6) + 0.6;
  const sc = new THREE.LineSegments(scaffoldGeo(W + 2.2, sh, D + 2.2, modern ? 2 : 2.4), cx.lineMat(modern ? 0xd9a441 : 0x9c7a4c));
  sc.userData.bid = b.id; sc.userData.ownGeo = true; grp.add(sc);
  if (modern && sh > 3) {
    const net = M(p > 0.86 ? '#4a7fb8' : '#3f8f5a', { opacity: 0.35 });
    add(GEO.box, net, W + 2.3, sh * 0.85, 0.05, 0, 0.6, D / 2 + 1.15);
    add(GEO.box, net, W + 2.3, sh * 0.85, 0.05, 0, 0.6, -D / 2 - 1.15);
    add(GEO.box, net, 0.05, sh * 0.85, D + 2.3, W / 2 + 1.15, 0.6, 0);
    add(GEO.box, net, 0.05, sh * 0.85, D + 2.3, -W / 2 - 1.15, 0.6, 0);
  }
  // 타워크레인 (높은 건물)
  if (tall && modern && p < 0.97) crane(cx, b, grp, M, W, D, H, (b.crew || 0) > 0 && !b.stalled);
  else if (!modern && H >= 6 && p < 0.9) { // 옛날엔 나무 기중기
    add(GEO.box, M('#7a5532'), 0.4, H + 3, 0.4, W / 2 + 2.5, 0, D / 2 + 2.5);
    add(GEO.box, M('#7a5532'), 5, 0.35, 0.35, W / 2 + 0.5, H + 2.6, D / 2 + 2.5, 0.5);
  }
  if (b.stalled) add(GEO.box, M('#d64545'), 3, 1.1, 0.12, -W * 0.25, 1.2, fz + 0.1); // 공사 중단 표지
}

function excavator(add, M, x, z) {
  add(GEO.box, M('#333'), 3.4, 0.7, 2.4, x, 0, z);
  add(GEO.box, M('#f2b705'), 2.8, 1.4, 2.2, x, 0.7, z);
  add(GEO.box, M('#f2b705'), 1.3, 1.2, 1.3, x - 0.5, 2.1, z);
  add(GEO.box, M('#f2b705'), 0.35, 0.35, 3.6, x + 1.4, 1.9, z + 1.6, 0).rotation.x = -0.5;
}

function scaffoldGeo(w, h, d, step) {
  const pts = [];
  const L = (x1, y1, z1, x2, y2, z2) => pts.push(x1, y1, z1, x2, y2, z2);
  const hx = w / 2, hz = d / 2;
  for (let y = 0.6; y <= h + 0.01; y += step) { L(-hx, y, -hz, hx, y, -hz); L(-hx, y, hz, hx, y, hz); L(-hx, y, -hz, -hx, y, hz); L(hx, y, -hz, hx, y, hz); }
  for (let x = -hx; x <= hx + 0.01; x += step * 1.5) { L(x, 0.6, -hz, x, h, -hz); L(x, 0.6, hz, x, h, hz); }
  for (let z = -hz; z <= hz + 0.01; z += step * 1.5) { L(-hx, 0.6, z, -hx, h, z); L(hx, 0.6, z, hx, h, z); }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  return g;
}

function crane(cx, b, grp, M, W, D, H, active) {
  const mh = H + 12, x = W / 2 + 4, z = -D / 2 - 4;
  const yel = M('#f2b705');
  const holder = new THREE.Group();
  const base = new THREE.Mesh(GEO.box, M('#555')); base.scale.set(3, 0.8, 3); base.position.set(x, 0, z); holder.add(base);
  // 격자 마스트
  const mast = new THREE.LineSegments(scaffoldGeo(1.6, mh, 1.6, 1.6), cx.lineMat(0xf2b705));
  mast.position.set(x, 0, z); mast.userData.ownGeo = true; holder.add(mast);
  // 회전하는 지브
  const jib = new THREE.Group();
  jib.position.set(x, mh + 0.6, z);
  const len = Math.max(W, D) * 1.15 + 8;
  const part = (sx, sy, sz, px, py, pz, m = yel) => { const o = new THREE.Mesh(GEO.box, m); o.scale.set(sx, sy, sz); o.position.set(px, py, pz); jib.add(o); return o; };
  part(len, 0.9, 1.1, -len / 2 + 2, 0, 0);
  part(9, 0.9, 1.1, 5.5, 0, 0);
  part(2.6, 1.8, 2.2, 1.2, -1.8, 0, M('#e8e8e8')); // 운전실
  part(2.8, 2.2, 2.2, 8.5, -2.2, 0, M('#888')); // 균형추
  part(0.5, 3.2, 0.5, 0, 0.9, 0);
  const hookY = Math.max(4, mh * 0.45);
  part(0.08, hookY, 0.08, -len * 0.55, -hookY, 0, M('#222'));
  part(1.6, 0.8, 1.6, -len * 0.55, -hookY - 0.8, 0, M('#9aa1a8')); // 매단 자재
  jib.rotation.y = hash(b.id + 7) * Math.PI * 2;
  jib.userData.r0 = jib.rotation.y; jib.userData.ph = hash(b.id) * 10;
  holder.add(jib);
  holder.traverse(o => { o.userData.bid = b.id; });
  grp.add(holder);
  if (active) cx.cranes.push(jib);
}

// ── 지붕 ──
function roof(cx, b, def, add, h, hex, snowy, grow = 1) {
  const M = (c, o) => cx.mat(c, o);
  const r = def.roof, W = b.w, D = b.d;
  const rcBase = def.roofColor || '#' + new THREE.Color(hex).multiplyScalar(0.7).getHexString();
  const rc = M(snowy(rcBase));
  const long = W >= D;
  const g = Math.max(0.05, grow);
  if (r === 'cone') add(GEO.cone, rc, W * 1.08, Math.max(W, D) * 0.55 * g, D * 1.08, 0, h);
  else if (r === 'gable') {
    const era = def.era ?? 0;
    const geo = era === 2 ? GEO.hanokRoof : GEO.prism;
    const ry = long ? Math.PI / 2 : 0;
    add(geo, rc, (long ? D : W) * (era === 2 ? 1.05 : 1.14), Math.min(W, D) * (era === 2 ? 0.55 : 0.42) * g, (long ? W : D) * 1.08, 0, h - (era === 2 ? 0.2 : 0), 0, ry);
    if (era === 2 && g >= 1) add(GEO.box, M('#3b4148'), long ? W * 1.1 : 0.5, 0.5, long ? 0.5 : D * 1.1, 0, h + Math.min(W, D) * 0.5, 0); // 용마루
  } else if (r === 'pyramid') add(GEO.pyr, rc, W, b.h * 0.6 * g, D, 0, h);
  else if (r === 'dome') add(GEO.dome, def.roofColor ? rc : M(snowy('#cfd8e0')), Math.min(W, D) * 0.8, Math.min(W, D) * 0.8 * g, Math.min(W, D) * 0.8, 0, h);
  else if (r === 'saw') { for (let k = 0; k < 3; k++) add(GEO.prism, M(snowy('#6f757b')), W / 3, 3.5 * g, D, -W / 3 + k * W / 3, h, 0); }
  else if (r === 'flat' || r === 'none') add(GEO.box, M(snowy('#5c6168', 0.95)), W + 0.4, 0.5, D + 0.4, 0, h);
}

// ── 완공 ──
function finished(cx, b, def, grp, add, { era, hex, snowy, v, cold }) {
  const M = (c, o) => cx.mat(c, o);
  const W = b.w, D = b.d, H = b.h;
  const r = def.roof;
  if (r === 'bowl') { // 경기장
    add(GEO.torus, M(hex), W * 0.42, H * 0.9, D * 0.42, 0, H * 0.35);
    add(GEO.cyl, M('#4f9a3c'), W * 0.62, 0.3, D * 0.62);
    for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) { add(GEO.box, M('#9aa3ad'), 0.6, H * 1.6, 0.6, x * W * 0.45, 0, z * D * 0.45); cx.lamp(add(GEO.box, M('#fff7d6', { emissive: '#fff2c0' }), 3, 1, 1, x * W * 0.45, H * 1.6, z * D * 0.45)); }
    return;
  }
  if (r === 'rocket') {
    add(GEO.box, M('#cfd5dc'), W * 0.5, 4, D * 0.5, -W * 0.15, 0, D * 0.15);
    add(GEO.cyl, M('#f4f6f8'), 5, 46, 5, W * 0.3, 0, -D * 0.3);
    add(GEO.cone, M('#d33b3b'), 4, 8, 4, W * 0.3, 46, -D * 0.3);
    add(GEO.box, M('#9aa3ad'), 2.4, 52, 2.4, W * 0.3 + 5, 0, -D * 0.3);
    return;
  }
  if (r === 'cone' && b.type === 'hut') { // 움집: 원뿔 지붕이 곧 집
    add(GEO.cyl, M('#6e5236'), W * 0.9, 0.9, D * 0.9);
    add(GEO.cone, M(snowy('#8d6b48')), W * 1.2, H * 1.25, D * 1.2, 0, 0.6);
    add(GEO.box, M('#2a1e14'), 1.2, 1.5, 0.3, 0, 0.3, D * 0.48);
    return;
  }
  const st = styleOf(b.type, def);
  const tex = wallTexture(st, b.burnt ? '#3b3532' : hex);
  const body = new THREE.Mesh(wallBox(W, H, D, tex.tw, tex.th), cx.wallMat(st, hex, tex));
  body.userData.bid = b.id; body.userData.ownGeo = true; grp.add(body);
  // 문
  const civic = def.kind === 'civic';
  const doorW = civic || W > 20 ? 3.2 : 1.3, doorH = civic || W > 20 ? 3 : 2.2;
  const doorCol = st === 'hanok' || st === 'timber' ? '#4a3322' : st === 'brick' ? '#5b3a2e' : '#2e3b48';
  add(GEO.box, M(doorCol), doorW, doorH, 0.25, (v - 0.5) * W * 0.3, 0, D / 2 + 0.05);
  if (st === 'concrete' || st === 'civic' || st === 'glass') add(GEO.box, M('#6b7681'), doorW + 1.6, 0.3, 1.6, (v - 0.5) * W * 0.3, doorH + 0.2, D / 2 + 0.8); // 현관 캐노피
  // 가게: 차양과 간판
  if (SHOPS.has(b.type)) {
    const ac = def.accent || ['#c0392b', '#2a9d8f', '#e9c46a', '#264653', '#8e44ad'][Math.floor(v * 5)];
    const awn = add(GEO.box, M(ac), Math.min(W * 0.8, 10), 0.18, 1.8, 0, Math.min(3.1, H * 0.6), D / 2 + 0.9);
    awn.rotation.x = 0.28;
    add(GEO.box, M('#f6f1e7'), Math.min(W * 0.6, 7), 1.1, 0.2, 0, Math.min(H - 0.6, 4.2), D / 2 + 0.12);
    add(GEO.box, M(ac), Math.min(W * 0.6, 7) - 0.6, 0.6, 0.22, 0, Math.min(H - 0.6, 4.2) + 0.25, D / 2 + 0.14);
  } else if (def.accent) add(GEO.box, M(def.accent), Math.min(5, W * 0.4), 1.4, 0.3, 0, H * 0.75, D / 2 + 0.2);
  // 아파트·연립: 층마다 발코니
  if (def.kind === 'home' && era >= 4 && H >= 10 && st !== 'glass') {
    const floors = Math.floor(H / 3.2);
    const balc = M('#d7dadf');
    for (let f = 1; f < floors; f++) {
      add(GEO.box, balc, W * 0.92, 0.25, 1.2, 0, f * 3.2, D / 2 + 0.6);
      add(GEO.box, M('#9fb3c4', { opacity: 0.55 }), W * 0.92, 1, 0.08, 0, f * 3.2 + 0.25, D / 2 + 1.18);
    }
  }
  // 굴뚝
  if (def.chimney) {
    for (let k = 0; k < (W > 40 ? 2 : 1); k++) {
      const cxp = W * (0.3 - k * 0.5), czp = -D * 0.25;
      add(GEO.cyl, M('#7a6f69'), 2.6, H + 16, 2.6, cxp, 0, czp);
      add(GEO.cyl, M('#c94a3a'), 2.7, 1.2, 2.7, cxp, H + 13.5, czp);
      cx.smokeAt(grp, cxp, H + 17, czp, 1.6);
    }
  } else if (def.kind === 'home' && (def.era ?? 0) <= 3 && (def.era ?? 0) >= 1) {
    add(GEO.box, M('#6f5a4a'), 0.9, H * 0.5 + 2, 0.9, W * 0.3, H * 0.8, -D * 0.2); // 작은 굴뚝 (아궁이)
    if (cold) cx.smokeAt(grp, W * 0.3, H * 1.3 + 2.4, -D * 0.2, 0.5);
  }
  roof(cx, b, def, add, H, hex, snowy, 1);
  // 옥상 설비 (산업 시대 이후 평지붕)
  if ((r === 'flat') && era >= 4 && H >= 8) {
    add(GEO.box, M('#9aa1a8'), 2.4, 1.4, 1.6, W * 0.25, H + 0.5, -D * 0.2);
    add(GEO.cyl, M('#b8bec4'), 2.2, 2.2, 2.2, -W * 0.25, H + 0.5, D * 0.15);
    add(GEO.box, M('#5c6168'), W + 0.6, 0.9, 0.3, 0, H, D / 2 + 0.15); // 난간
    add(GEO.box, M('#5c6168'), W + 0.6, 0.9, 0.3, 0, H, -D / 2 - 0.15);
    if (H > 30) { add(GEO.box, M('#e8e8e8'), 0.25, 8, 0.25, 0, H + 0.5, 0); cx.lamp(add(GEO.box, M('#ff4040', { emissive: '#ff2020' }), 0.6, 0.6, 0.6, 0, H + 8.5, 0)); }
  }
  // 소방서·경찰서: 차고 문과 차
  if (b.type === 'firestation') { for (let k = 0; k < 2; k++) { add(GEO.box, M('#c62828'), 3.8, 3.4, 0.3, -W * 0.25 + k * W * 0.5, 0, D / 2 + 0.06); add(GEO.box, M('#d32f2f'), 2.4, 2.2, 6, -W * 0.25 + k * W * 0.5, 0, D / 2 + 4); } }
  if (b.type === 'police') { add(GEO.box, M('#f5f5f5'), 2, 1.5, 4.4, W * 0.3, 0, D / 2 + 3.5); add(GEO.box, M('#1d4ed8'), 2.05, 0.4, 4.45, W * 0.3, 0.8, D / 2 + 3.5); add(GEO.box, M('#e53935', { emissive: '#e53935' }), 0.8, 0.3, 0.4, W * 0.3, 1.5, D / 2 + 3.5); }
  if (b.type === 'hospital') { add(GEO.box, M('#e53935'), 1.2, 4, 0.3, 0, H * 0.82, D / 2 + 0.2); add(GEO.box, M('#e53935'), 4, 1.2, 0.3, 0, H * 0.82 + 1.4, D / 2 + 0.2); }
  if (b.type === 'school' || b.type === 'middle' || b.type === 'high') { add(GEO.box, M('#b5925a'), W * 1.2, 0.05, D * 1.4, 0, -0.2, D * 1.3); add(GEO.box, M('#e8e8e8'), 0.2, 6, 0.2, W * 0.45, 0, D * 0.9); } // 운동장과 국기 게양대
  if (b.type === 'temple') add(GEO.box, M('#b23a2a'), 1, H * 0.5, 1, W * 0.55, 0, D * 0.55);
}

function fireFx(cx, b, grp) {
  const W = b.w, D = b.d, H = Math.max(3, b.h * (b.done ? 1 : Math.max(0.3, b.p || 0)));
  const top = H + Math.min(W, D) * 0.35; // 지붕을 뚫고 솟는 불길
  const n = Math.max(5, Math.min(12, Math.round((W + D) / 4)));
  for (let k = 0; k < n; k++) {
    const x = (hash(b.id * 31 + k) - 0.5) * W * 0.9, z = (hash(b.id * 17 + k) - 0.5) * D * 0.9;
    const f = new THREE.Mesh(GEO.flame, new THREE.MeshBasicMaterial({ color: k % 3 === 0 ? 0xffd25a : k % 2 ? 0xff6a10 : 0xff9a2e, transparent: true, opacity: 0.85, depthWrite: false }));
    const s = 2.4 + hash(k + b.id) * 3.2;
    f.scale.set(s, s * 2.6, s); f.position.set(x, H * 0.45 + hash(k * 7 + b.id) * (top - H * 0.45), z); f.userData.bid = b.id;
    grp.add(f); cx.flames.push(f);
  }
  for (let k = 0; k < 3; k++) {
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: cx.glowTex, color: 0xff6a1a, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.9 }));
    const g = Math.max(W, D) * (1.4 + k * 0.6);
    glow.scale.set(g, g, 1); glow.position.set(0, top * (0.6 + k * 0.25), 0); grp.add(glow);
  }
  cx.smokeAt(grp, 0, top + 2, 0, 2.6, true);
}
