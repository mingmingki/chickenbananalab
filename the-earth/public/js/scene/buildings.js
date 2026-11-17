// 건물 모양.
// 공사 중: 펜스·터파기 → 기초 → 골조(철근콘크리트 기둥·슬래브 또는 나무 기둥) + 비계·안전망 + 타워크레인 → 외벽 → 지붕 → 완공.
// 완공: 시대·용도별 외벽(흙벽, 초가, 한옥, 벽돌, 콘크리트, 유리, 공장 철판), 문, 창(밤에는 불빛), 간판·차양, 발코니, 옥상 설비, 굴뚝.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { buildHome, HOME_TYPES } from './homes.js';

const hash = n => { let h = (n * 2654435761) >>> 0; h = (h ^ (h >>> 15)) >>> 0; return (h % 1000) / 1000; };

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
export function wallTexture(style, color) {
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
      g.fillStyle = '#d9dcdf'; g.fillRect(wx - 3, wy - 3, ww + 6, wh + 6);            // 창틀
      const sky = g.createLinearGradient(wx, wy, wx + ww * 0.6, wy + wh);
      sky.addColorStop(0, '#7f9fbd'); sky.addColorStop(0.55, '#3f5670'); sky.addColorStop(1, '#2c3d4f');
      g.fillStyle = sky; g.fillRect(wx, wy, ww, wh);
      // 커튼·블라인드 (창마다 다르게)
      const cur = Math.random();
      if (cur < 0.3) { g.fillStyle = ['#e9dfcb', '#f2efe8', '#d8c7a8', '#c9d6e3'][Math.floor(Math.random() * 4)]; g.fillRect(wx, wy, ww * (0.2 + Math.random() * 0.25), wh); g.fillRect(wx + ww * (0.75 + Math.random() * 0.1), wy, ww * 0.2, wh); }
      else if (cur < 0.5) { g.fillStyle = 'rgba(235,235,230,0.85)'; const bh = wh * (0.3 + Math.random() * 0.5); g.fillRect(wx, wy, ww, bh); g.strokeStyle = 'rgba(170,170,165,0.6)'; g.lineWidth = 1; for (let k = 3; k < bh; k += 4) { g.beginPath(); g.moveTo(wx, wy + k); g.lineTo(wx + ww, wy + k); g.stroke(); } }
      g.fillStyle = 'rgba(220,235,255,0.22)'; g.beginPath(); g.moveTo(wx + ww * 0.08, wy + wh); g.lineTo(wx + ww * 0.32, wy); g.lineTo(wx + ww * 0.46, wy); g.lineTo(wx + ww * 0.22, wy + wh); g.fill(); // 비친 빛
      g.fillStyle = '#cfd3d6'; g.fillRect(wx + ww / 2 - 2, wy, 4, wh);                // 가운데 문살
      g.fillStyle = css(base, 0.62); g.fillRect(wx - 4, wy + wh + 3, ww + 8, 5);       // 창턱 그늘
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
export function wallBox(w, h, d, tw, th) {
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

/** 둥근 벽: 텍스처 한 장 = 4×4칸이 둘레를 따라 감긴다 */
export function wallCyl(r, h, tw, th) {
  const g = new THREE.CylinderGeometry(r, r, h, 28, 1, true).translate(0, h / 2, 0);
  const uv = g.attributes.uv;
  const circ = 2 * Math.PI * r;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * circ / (tw * 4), uv.getY(i) * h / (th * 4));
  return g;
}

/**
 * 마당 울타리 모양: 집 둘레를 감싸는 들쭉날쭉한 다각형 (건물 좌표 기준, 앞쪽(+z)에 대문 자리).
 * 땅에 칠하는 마당(cities.js)과 3D 담장이 같은 모양을 쓴다.
 */
export function yardPoly(b) {
  const n = 7 + Math.floor(hash(b.id * 3) * 3);
  const maxR = Math.max(b.w, b.d) / 2 + 6;
  const lim = 9.3;   // 한 칸(20m) 안에 머물게
  const pts = [];
  for (let k = 0; k < n; k++) {
    const a = (k / n) * Math.PI * 2 + (hash(b.id * 11 + k) - 0.5) * 0.5;
    const r = Math.min(lim, maxR * (0.86 + hash(b.id * 7 + k) * 0.16));
    // 직사각형 집을 감싸도록: 방향마다 필요한 최소 반지름
    const need = Math.min(Math.abs((b.w / 2 + 2) / (Math.cos(a) || 1e-6)), Math.abs((b.d / 2 + 2) / (Math.sin(a) || 1e-6)));
    const rr = Math.min(lim, Math.max(r, need));
    pts.push([Math.cos(a) * rr, Math.sin(a) * rr]);
  }
  return pts;
}
/** 건물이 틀어 앉은 각도: 움집·초가·기와·벽돌집은 ±10° 안에서 제멋대로, 나머지는 반듯하게 */
const TRAD = new Set(['hut', 'cottage', 'house', 'brickhouse', 'granary', 'seodang', 'forge', 'workshop']);
export const bRot = b => b.rot ?? (TRAD.has(b.type) ? (hash(b.id * 19) - 0.5) * 0.36 : 0);
/** 시대·종류별 마당이 있나: 'wall' 돌담 · 'fence' 싸리 울타리 · 'hedge' 생울타리 · null */
export function yardKind(b) {
  if (b.type === 'cottage') return 'fence';
  if (b.type === 'house') return 'wall';
  if (b.type === 'brickhouse') return hash(b.id * 5) < 0.5 ? 'hedge' : 'wall';
  return null;
}

/** 건물 몸체 모양 (같은 종류라도 집마다 조금씩): box · L(ㄱ자) · U(ㄷ자) · court(ㅁ자) · T · stepped(계단식 탑) · twin(쌍둥이 동) · round(원통) */
export function shapeOf(b, def) {
  const t = b.type, v = hash(b.id * 13);
  if (t === 'cottage') return v < 0.3 ? 'L' : 'box';
  if (t === 'house') return v < 0.4 ? 'L' : v < 0.65 ? 'U' : 'box';            // 기와집: ㄱ자·ㄷ자 한옥
  if (t === 'brickhouse') return v < 0.35 ? 'L' : 'box';
  if (t === 'hall' || t === 'academy') return 'court';                          // 관청·향교: ㅁ자
  if (t === 'school' || t === 'middle' || t === 'high') return v < 0.5 ? 'L' : 'U';
  if (t === 'university') return 'court';
  if (t === 'hospital') return 'T';
  if (t === 'hotel') return v < 0.5 ? 'T' : 'L';
  if (t === 'skytower') return v < 0.5 ? 'stepped' : 'round';
  if (t === 'office') return v < 0.4 ? 'stepped' : v < 0.6 ? 'round' : 'box';
  if (t === 'apartment') return 'twin';
  if (t === 'townhouse' || t === 'bizbuilding' || t === 'shop') return v < 0.3 ? 'L' : 'box';
  if (t === 'concert' || t === 'granary') return 'round';
  return 'box';
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

// 분수·우물 물 (모든 건물이 함께 쓴다)
const FMAT = {
  water: new THREE.MeshStandardMaterial({ color: 0x5f9cc4, roughness: 0.08, metalness: 0.1, transparent: true, opacity: 0.86 }),
  spray: new THREE.MeshBasicMaterial({ color: 0xe6f4ff, transparent: true, opacity: 0.55, depthWrite: false }),
  well: new THREE.MeshStandardMaterial({ color: 0x1d2b33, roughness: 0.2 }),
};

// 옛 살림집은 집마다 벽·지붕 빛깔이 다르다 (흙벽·회벽·황토, 새 짚·묵은 짚, 검은 기와·푸른 기와, 붉은 벽돌·검붉은 벽돌…)
const HOME_PAL = {
  hut: { wall: ['#8d6b48', '#7a5c3a', '#9a7a52', '#86684a'], roof: ['#8d6b48', '#a07c52', '#6e5236', '#94785a'] },
  cottage: { wall: ['#cdb38a', '#d9c4a0', '#c49a6c', '#e3d6bd', '#b98d62', '#d6b48c', '#c8a57a', '#e9dfc9'], roof: ['#b9974f', '#c9a54e', '#a8874a', '#97804f', '#d4b25c', '#9c8456', '#b59a5e'] },
  house: { wall: ['#e8e0cf', '#efe8d8', '#ddd2bd', '#e9dcc4', '#f2ece0'], roof: ['#4a5560', '#3b4148', '#55606b', '#5a4a3a', '#3f4a52', '#46505a'] },
  brickhouse: { wall: ['#9c5a45', '#a8664c', '#8a4f3c', '#b87355', '#7d4a3a', '#a0705a', '#93603f'], roof: ['#5b3a2e', '#4a5058', '#6b3b2a', '#3f4a3f', '#7a4a32', '#545454'] },
};

/** 건물 하나 → THREE.Group */
export function makeBuilding(cx, b, def, opts) {
  const { era = 0, snow = 0, hAt, cold = false } = opts;
  const grp = new THREE.Group();
  grp.position.set(b.x, hAt(b.x, b.z) + 0.3, b.z);
  grp.rotation.y = bRot(b);   // 옛 마을 집은 반듯하지 않게 조금씩 틀어 앉는다
  grp.userData.bid = b.id;
  const v = hash(b.id);
  const pal = HOME_PAL[b.type];
  if (pal) {
    // 지붕 모양도 집마다: 초가는 둥근 지붕·우진각·맞배, 벽돌집은 맞배·우진각
    const roofForm = (b.type === 'cottage' || b.type === 'brickhouse') && hash(b.id * 7) < 0.45 ? 'hip' : null;
    def = { ...def, color: pal.wall[Math.floor(hash(b.id * 3) * pal.wall.length)], roofColor: pal.roof[Math.floor(hash(b.id * 11) * pal.roof.length)], form: roofForm };
    // 키와 덩치도 조금씩 다르게 (마당 안에서)
    if (b.done) { const k = 0.9 + hash(b.id * 17) * 0.12; grp.scale.set(k, 0.88 + hash(b.id * 23) * 0.28, k); }
  }
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
  if (def.roof === 'fire' && era >= 4) {
    // 광장 분수: 둥근 수반, 물, 가운데 기둥과 물줄기
    add(GEO.cyl, M('#c9c4ba'), 7.2, 0.55, 7.2);
    add(GEO.cyl, FMAT.water, 6.4, 0.58, 6.4, 0, 0.02, 0);
    add(GEO.cyl, M('#b9b3a8'), 0.7, 1.6, 0.7, 0, 0.5, 0);
    add(GEO.cyl, M('#c9c4ba'), 2.4, 0.18, 2.4, 0, 1.6, 0);
    const spray = FMAT.spray;
    add(GEO.cone, spray, 0.5, 1.4, 0.5, 0, 1.78, 0);
    for (let k = 0; k < 6; k++) { const a = k / 6 * Math.PI * 2; add(GEO.cone, spray, 0.18, 0.7, 0.18, Math.cos(a) * 2.6, 0.55, Math.sin(a) * 2.6); }
    for (let k = 0; k < 4; k++) { const a = k / 4 * Math.PI * 2 + 0.4; add(GEO.box, M('#8a6a48'), 2.2, 0.08, 0.5, Math.cos(a) * 5.4, 0.45, Math.sin(a) * 5.4, -a + Math.PI / 2); add(GEO.box, M('#555'), 2.0, 0.42, 0.4, Math.cos(a) * 5.4, 0, Math.sin(a) * 5.4, -a + Math.PI / 2); }
    return grp;
  }
  if (def.roof === 'fire' && era >= 2) {
    // 마을 우물: 돌로 쌓은 둘레, 두레박 틀과 작은 지붕
    add(GEO.cyl, M('#8d857a'), 2.2, 0.85, 2.2);
    add(GEO.cyl, FMAT.well, 1.7, 0.86, 1.7, 0, 0.01, 0);
    for (const sx of [-1, 1]) add(GEO.box, M('#5a3d22'), 0.16, 2.3, 0.16, sx * 1.15, 0, 0);
    add(GEO.box, M('#5a3d22'), 2.6, 0.14, 0.14, 0, 2.2, 0);
    add(GEO.prism, M(snowy('#4a3a2c')), 2.8, 0.7, 1.4, 0, 2.3, 0);
    add(GEO.cyl, M('#6b4a2c'), 0.3, 0.3, 0.3, 0.5, 1.4, 0);
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

  if (b.type === 'demolish') { demolitionSite(cx, b, add, snowy); return grp; }
  // 허무는 중: 지을 때를 거꾸로 — 지붕·벽이 걷히고 비계와 뼈대만 남았다가 바닥까지
  if (b.done && b.demo != null) construction(cx, { ...b, done: false, p: Math.max(0.03, 0.9 * (1 - b.demo)), stalled: false }, def, grp, add, { era, hex, snowy });
  else if (!b.done) construction(cx, b, def, grp, add, { era, hex, snowy });
  else finished(cx, b, def, grp, add, { era, hex, snowy, v, cold });
  if (b.fire) fireFx(cx, b, grp);
  return grp;
}

// ── 철거 현장: 폐자재 더미(허문 만큼 쌓인다), 덤프트럭, 굴착기, 가림막 ──
function demolitionSite(cx, b, add, snowy) {
  const M = (c, o) => cx.mat(c, o);
  const p = Math.max(0.08, Math.min(1, b.p || 0));
  add(GEO.cone, M(snowy('#8a8178', 0.5)), 4.2 * p + 1.2, 2.4 * p + 0.5, 3.6 * p + 1.2, -2.2, 0, 0);      // 폐자재 더미
  add(GEO.box, M('#6b5a48'), 1.6 * p + 0.4, 0.5, 1.2 * p + 0.3, -1.2, 0, 1.0, 0.4);                      // 부서진 나무
  // 덤프트럭: 적재함·운전석·바퀴
  add(GEO.box, M('#e0a020'), 2.2, 1.2, 3.6, 2.4, 0.9, -0.4);
  add(GEO.box, M('#c98a12'), 2.2, 1.5, 1.4, 2.4, 0.9, 2.1);
  add(GEO.box, M('#9fb3c4', { opacity: 0.7 }), 2.0, 0.6, 0.06, 2.4, 1.8, 2.82);
  for (const [x, z] of [[1.3, -1.4], [3.5, -1.4], [1.3, 1.9], [3.5, 1.9]]) add(GEO.cyl, M('#222'), 0.8, 0.5, 0.8, x, 0, z);
  if ((b.era ?? 0) >= 4) excavator(add, M, -0.5, -2.4);
  // 가림막
  for (let k = 0; k < 4; k++) add(GEO.box, M(k % 2 ? '#f2f2ef' : '#c9a227'), 1.9, 1.6, 0.08, -3.4 + k * 2, 0, 3.4);
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
function roof(cx, b, def, add, h, hex, snowy, grow = 1, ox = 0, oz = 0, rw = b.w, rd = b.d) {
  const M = (c, o) => cx.mat(c, o);
  const r = def.roof, W = rw, D = rd;
  const add0 = add;
  add = (geo, m, sx, sy, sz, x = 0, y = 0, z = 0, ry = 0) => add0(geo, m, sx, sy, sz, x + ox, y, z + oz, ry);
  const rcBase = def.roofColor || '#' + new THREE.Color(hex).multiplyScalar(0.7).getHexString();
  const rc = M(snowy(rcBase));
  const long = W >= D;
  const g = Math.max(0.05, grow);
  if (r === 'cone') add(GEO.cone, rc, W * 1.08, Math.max(W, D) * 0.55 * g, D * 1.08, 0, h);
  else if (r === 'gable' && def.form === 'round') {
    // 둥근 초가지붕: 처마가 낮게 둥글게 덮이고, 용마름이 얹힌다
    const rh = Math.min(W, D) * 0.48 * g;
    add(GEO.dome, rc, W * 1.24, rh * 1.15, D * 1.26, 0, h - 0.3);
    if (g >= 1) add(GEO.box, M(snowy('#8a6f3e')), long ? W * 0.5 : 0.45, 0.3, long ? 0.45 : D * 0.5, 0, h - 0.3 + rh * 0.55, 0);
  } else if (r === 'gable' && def.form === 'hip' && W > 0) {
    add(GEO.pyr, rc, W * 1.16, Math.min(W, D) * 0.42 * g, D * 1.16, 0, h - 0.1);
  } else if (r === 'gable') {
    const era = def.era ?? 0;
    const geo = era === 2 ? GEO.hanokRoof : GEO.prism;
    const ry = long ? Math.PI / 2 : 0;
    add(geo, rc, (long ? D : W) * (era === 2 ? 1.05 : 1.14), Math.min(W, D) * (era === 2 ? 0.55 : 0.42) * g, (long ? W : D) * 1.08, 0, h - (era === 2 ? 0.2 : 0), 0, ry);
    if (era === 2 && g >= 1) add(GEO.box, M('#3b4148'), long ? W * 1.1 : 0.5, 0.5, long ? 0.5 : D * 1.1, 0, h + Math.min(W, D) * 0.5, 0); // 용마루
  } else if (r === 'pyramid') add(GEO.pyr, rc, W, b.h * 0.6 * g, D, 0, h);
  else if (r === 'dome') add(GEO.dome, def.roofColor ? rc : M(snowy('#cfd8e0')), Math.min(W, D) * 0.8, Math.min(W, D) * 0.8 * g, Math.min(W, D) * 0.8, 0, h);
  else if (r === 'saw') { for (let k = 0; k < 3; k++) add(GEO.prism, M(snowy('#6f757b')), W / 3, 3.5 * g, D, -W / 3 + k * W / 3, h, 0); }
  else if (r === 'flat' || r === 'none') {
    const era = def.era ?? 0, k = hash(b.id * 41);
    const top = era >= 4 && era <= 6 && !def.glass && (def.kind === 'home' || k < 0.4) && k < 0.7 ? (k < 0.35 ? '#5d8a6c' : '#6f9a7a') : k < 0.5 ? '#6a6f75' : '#8d9298';
    add(GEO.box, M(snowy(top, 0.95)), W + 0.4, 0.5, D + 0.4, 0, h);
  }
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
  if (HOME_TYPES.has(b.type) && !b.burnt) {   // 살림집: 집마다 다른 설계도 (homes.js)
    buildHome(cx, b, def, grp, { snowy, cold, hex });
    yardWalls(cx, b, grp, snowy, W);
    return;
  }
  const st = styleOf(b.type, def);
  const tex = wallTexture(st, b.burnt ? '#3b3532' : hex);
  const wmat = cx.wallMat(st, hex, tex);
  const shape = shapeOf(b, def);
  // 몸체 한 덩어리 (x, z: 가운데, y0: 바닥 높이) — 지붕은 덩어리마다
  const masses = [];
  const mass = (x, z, w, d, h, y0 = 0, withRoof = true) => {
    masses.push({ x, z, w, d, h, y0 });
    const m = new THREE.Mesh(wallBox(w, h, d, tex.tw, tex.th), wmat);
    m.position.set(x, y0, z); m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
    if (withRoof) roof(cx, b, def, (g, mm, sx, sy, sz, px, py, pz, ry) => add(g, mm, sx, sy, sz, px, py + y0, pz, ry), h, hex, snowy, 1, x, z, w, d);
  };
  // 앞쪽(대문·현관·간판이 붙는 면): x 가운데, z 앞면, 너비
  let front = { x: (v - 0.5) * W * 0.3, z: D / 2, w: W };
  let boxRoof = true, plainTop = true;
  if (shape === 'L') {          // ㄱ자: 뒤채(가로) + 옆채(앞으로 꺾임) — 앞마당이 생긴다
    mass(0, -D * 0.25, W, D * 0.5, H);
    mass(-W * 0.3, D * 0.25, W * 0.4, D * 0.5, H * 0.92);
    front = { x: -W * 0.3, z: D / 2, w: W * 0.4 }; boxRoof = false; plainTop = false;
  } else if (shape === 'U') {   // ㄷ자: 안채 + 양쪽 날개, 가운데 마당
    mass(0, -D * 0.28, W, D * 0.44, H);
    mass(-W * 0.36, D * 0.22, W * 0.28, D * 0.56, H * 0.9);
    mass(W * 0.36, D * 0.22, W * 0.28, D * 0.56, H * 0.9);
    front = { x: W * 0.36, z: D / 2, w: W * 0.28 }; boxRoof = false; plainTop = false;
  } else if (shape === 'court') { // ㅁ자: 네 채가 안마당을 둘러싸고, 앞채 가운데가 대문
    mass(0, -D * 0.38, W, D * 0.24, H);
    mass(-W * 0.39, 0, W * 0.22, D * 0.52, H * 0.9);
    mass(W * 0.39, 0, W * 0.22, D * 0.52, H * 0.9);
    mass(-W * 0.3, D * 0.38, W * 0.4, D * 0.24, H * 0.85);
    mass(W * 0.3, D * 0.38, W * 0.4, D * 0.24, H * 0.85);
    add(GEO.box, M(def.roofColor || '#5a4a3a'), W * 0.22, H * 0.7, D * 0.24, 0, 0, D * 0.38);   // 솟을대문
    front = { x: 0, z: D / 2, w: W * 0.2 }; boxRoof = false; plainTop = false;
  } else if (shape === 'T') {   // T자: 앞 건물 + 뒤로 뻗은 병동·객실동
    mass(0, D * 0.25, W, D * 0.5, H);
    mass(0, -D * 0.25, W * 0.4, D * 0.5, H * 1.25);
    front = { x: 0, z: D / 2, w: W }; boxRoof = false; plainTop = false;
  } else if (shape === 'stepped') { // 계단식 탑: 아래는 넓고 위로 갈수록 좁아진다
    const h1 = H * 0.18, h2 = H * 0.5, h3 = H - h1 - h2;
    mass(0, 0, W, D, h1, 0, false);
    mass(0, 0, W * 0.78, D * 0.78, h2, h1, false);
    mass(0, 0, W * 0.56, D * 0.56, h3, h1 + h2, true);
    add(GEO.box, M('#5c6168'), W + 0.4, 0.4, D + 0.4, 0, h1, 0);
    add(GEO.box, M('#5c6168'), W * 0.78 + 0.4, 0.4, D * 0.78 + 0.4, 0, h1 + h2, 0);
    boxRoof = false; plainTop = false;
    add(GEO.box, M('#e8e8e8'), 0.25, 9, 0.25, 0, H + 0.5, 0); cx.lamp(add(GEO.box, M('#ff4040', { emissive: '#ff2020' }), 0.6, 0.6, 0.6, 0, H + 9.5, 0));
  } else if (shape === 'round') { // 원통형 (둥근 탑·공연장·곡물 창고)
    const r = Math.min(W, D) / 2;
    const m = new THREE.Mesh(wallCyl(r, H, tex.tw, tex.th), wmat);
    m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
    add(GEO.cyl, M(snowy('#5c6168', 0.95)), r * 2 + 0.6, 0.6, r * 2 + 0.6, 0, H);
    if (def.roof === 'cone' || def.roof === 'dome' || b.type === 'concert') roof(cx, b, def, add, H, hex, snowy, 1, 0, 0, r * 2, r * 2);
    front = { x: 0, z: r, w: r }; boxRoof = false; plainTop = false;
  } else if (shape === 'twin') { // 쌍둥이 동: 두 동이 나란히, 높이가 조금 다르다
    const h2 = H * (0.82 + hash(b.id * 3) * 0.16);
    mass(-W * 0.26, 0, W * 0.42, D * 0.8, H);
    mass(W * 0.26, 0, W * 0.42, D * 0.8, h2);
    const balc = M('#d7dadf'), glassM = M('#9fb3c4', { opacity: 0.55 });
    for (const [x, hh] of [[-W * 0.26, H], [W * 0.26, h2]]) for (let f = 1; f < Math.floor(hh / 3.2); f++) {
      add(GEO.box, balc, W * 0.4, 0.25, 1.2, x, f * 3.2, D * 0.4 + 0.6);
      add(GEO.box, glassM, W * 0.4, 1, 0.08, x, f * 3.2 + 0.25, D * 0.4 + 1.18);
    }
    front = { x: -W * 0.26, z: D * 0.4, w: W * 0.42 }; boxRoof = false; plainTop = false;
  } else {
    mass(0, 0, W, D, H, 0, false);
  }
  // 문
  const civic = def.kind === 'civic';
  const doorW = civic || W > 20 ? 3.2 : 1.3, doorH = civic || W > 20 ? 3 : 2.2;
  const doorCol = st === 'hanok' || st === 'timber' ? '#4a3322' : st === 'brick' ? '#5b3a2e' : '#2e3b48';
  add(GEO.box, M(doorCol), doorW, doorH, 0.25, front.x, 0, front.z + 0.05);
  if (st === 'concrete' || st === 'civic' || st === 'glass') add(GEO.box, M('#6b7681'), doorW + 1.6, 0.3, 1.6, front.x, doorH + 0.2, front.z + 0.8); // 현관 캐노피
  // 가게: 차양과 간판
  if (SHOPS.has(b.type)) {
    const ac = def.accent || ['#c0392b', '#2a9d8f', '#e9c46a', '#264653', '#8e44ad'][Math.floor(v * 5)];
    const fw = front.w;
    const awn = add(GEO.box, M(ac), Math.min(fw * 0.8, 10), 0.18, 1.8, front.x, Math.min(3.1, H * 0.6), front.z + 0.9);
    awn.rotation.x = 0.28;
    add(GEO.box, M('#f6f1e7'), Math.min(fw * 0.6, 7), 1.1, 0.2, front.x, Math.min(H - 0.6, 4.2), front.z + 0.12);
    add(GEO.box, M(ac), Math.min(fw * 0.6, 7) - 0.6, 0.6, 0.22, front.x, Math.min(H - 0.6, 4.2) + 0.25, front.z + 0.14);
  } else if (def.accent) add(GEO.box, M(def.accent), Math.min(5, front.w * 0.4), 1.4, 0.3, front.x, H * 0.75, front.z + 0.2);
  yardWalls(cx, b, grp, snowy, W);
  // 아파트·연립: 층마다 발코니
  if (def.kind === 'home' && era >= 4 && H >= 10 && st !== 'glass' && shape === 'box') {
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
  if (boxRoof) roof(cx, b, def, add, H, hex, snowy, 1);
  // 입면·옥상의 입체 장식 (한 덩어리로 합쳐 그린다)
  facade(cx, b, def, grp, { era, st, hex, v, masses, front, shape, r, plainTop });
  if ((r === 'flat') && era >= 4 && H > 30 && plainTop) { add(GEO.box, M('#e8e8e8'), 0.25, 8, 0.25, 0, H + 0.5, 0); cx.lamp(add(GEO.box, M('#ff4040', { emissive: '#ff2020' }), 0.6, 0.6, 0.6, 0, H + 8.5, 0)); }
  // 소방서·경찰서: 차고 문과 차
  if (b.type === 'firestation') { for (let k = 0; k < 2; k++) { add(GEO.box, M('#c62828'), 3.8, 3.4, 0.3, -W * 0.25 + k * W * 0.5, 0, D / 2 + 0.06); add(GEO.box, M('#d32f2f'), 2.4, 2.2, 6, -W * 0.25 + k * W * 0.5, 0, D / 2 + 4); } }
  if (b.type === 'police') { add(GEO.box, M('#f5f5f5'), 2, 1.5, 4.4, W * 0.3, 0, D / 2 + 3.5); add(GEO.box, M('#1d4ed8'), 2.05, 0.4, 4.45, W * 0.3, 0.8, D / 2 + 3.5); add(GEO.box, M('#e53935', { emissive: '#e53935' }), 0.8, 0.3, 0.4, W * 0.3, 1.5, D / 2 + 3.5); }
  if (b.type === 'hospital') { add(GEO.box, M('#e53935'), 1.2, 4, 0.3, 0, H * 0.82, D / 2 + 0.2); add(GEO.box, M('#e53935'), 4, 1.2, 0.3, 0, H * 0.82 + 1.4, D / 2 + 0.2); }
  if (b.type === 'school' || b.type === 'middle' || b.type === 'high') { add(GEO.box, M('#b5925a'), W * 1.2, 0.05, D * 1.4, 0, -0.2, D * 1.3); add(GEO.box, M('#e8e8e8'), 0.2, 6, 0.2, W * 0.45, 0, D * 0.9); } // 운동장과 국기 게양대
  if (b.type === 'temple') add(GEO.box, M('#b23a2a'), 1, H * 0.5, 1, W * 0.55, 0, D * 0.55);
  // 방송국: 옥상의 격자 송신탑(빨간 항공등) + 위성 접시 + 큰 로고판
  if (b.type === 'broadcast') {
    const tx = W * 0.32, tz = -D * 0.15, th = H + 34;
    const tower = new THREE.LineSegments(scaffoldGeo(2.4, th - H, 2.4, 2.2), cx.lineMat(0xd94b3b));
    tower.position.set(tx, H, tz); tower.userData.bid = b.id; tower.userData.ownGeo = true; grp.add(tower);
    for (const y of [H + (th - H) * 0.5, th]) cx.lamp(add(GEO.box, M('#ff3030', { emissive: '#ff2020' }), 0.7, 0.7, 0.7, tx, y, tz));
    if (era >= 5) for (let k = 0; k < 3; k++) {
      const dish = add(GEO.dome, M('#f2f4f6'), 3.2, 3.2, 3.2, -W * 0.3 + k * 4.2, H + 1.6, D * 0.18);
      dish.rotation.x = -1.0; dish.rotation.z = (k - 1) * 0.3;
      add(GEO.box, M('#9aa1a8'), 0.3, 1.6, 0.3, -W * 0.3 + k * 4.2, H, D * 0.18);
    }
    add(GEO.box, M(def.accent || '#1565c0'), Math.min(10, W * 0.35), 2.4, 0.3, -W * 0.15, H * 0.72, D / 2 + 0.2);
    add(GEO.box, M('#ffffff', { emissive: '#d6e4ff' }), Math.min(10, W * 0.35) * 0.7, 0.8, 0.32, -W * 0.15, H * 0.72 + 0.8, D / 2 + 0.22);
  }
  // 극장: 불 켜진 큰 간판(마키)과 포스터
  if (b.type === 'cinema') {
    cx.lamp(add(GEO.box, M(def.accent || '#f5c518', { emissive: '#ffd84a' }), W * 0.7, 2.6, 1.2, 0, H * 0.62, D / 2 + 0.6));
    add(GEO.box, M('#1b1b1b'), W * 0.6, 1.4, 0.15, 0, H * 0.62 + 0.6, D / 2 + 1.25);
    for (let k = 0; k < 3; k++) add(GEO.box, M(['#c0392b', '#2c3e50', '#8e44ad'][k], { emissive: ['#401010', '#101820', '#2a1030'][k] }), 1.8, 2.6, 0.12, -W * 0.3 + k * W * 0.3, 1.2, D / 2 + 0.12);
  }
}

// 마당 담장 (옛 집): 돌담·싸리 울타리·생울타리 — 앞쪽은 대문 자리로 비운다
function yardWalls(cx, b, grp, snowy, W) {
  const yk = yardKind(b);
  if (yk) {
    const pts = yardPoly(b);
    const parts = [], C = c => new THREE.Color(snowy(c, 0.6));
    const bx = (w, hgt, d, x, y, z, ry, c) => { const g = new THREE.BoxGeometry(w, hgt, d); g.rotateY(ry); g.translate(x, y + hgt / 2, z); parts.push(paintG(g, c)); };
    const wallC = yk === 'wall' ? C('#8f877c') : yk === 'hedge' ? C('#3f6b35') : C('#9a7b52'), capC = C('#4a5560'), postC = C('#7a5c3a');
    const hh = yk === 'wall' ? 1.3 : yk === 'hedge' ? 1.1 : 1.0, th = yk === 'hedge' ? 0.9 : 0.55;
    for (let k = 0; k < pts.length; k++) {
      const [x1, z1] = pts[k], [x2, z2] = pts[(k + 1) % pts.length];
      const mx = (x1 + x2) / 2, mz = (z1 + z2) / 2;
      if (mz > 0 && Math.abs(mx) < Math.max(4, W * 0.35)) continue;   // 대문 자리
      const len = Math.hypot(x2 - x1, z2 - z1), ang = -Math.atan2(z2 - z1, x2 - x1);
      if (yk === 'fence') {
        // 싸리 울타리: 1.2m마다 말뚝, 가로대 두 줄, 사이사이 싸릿대
        const n = Math.max(1, Math.round(len / 1.2));
        for (let i = 0; i <= n; i++) { const t = i / n; bx(0.11, 1.15, 0.11, x1 + (x2 - x1) * t, 0, z1 + (z2 - z1) * t, ang, postC); }
        for (const y of [0.32, 0.78]) bx(len, 0.06, 0.06, mx, y, mz, ang, wallC);
        for (let i = 0; i < n * 3; i++) { const t = (i + 0.5) / (n * 3); bx(0.035, 0.85 + ((i * 37) % 7) * 0.03, 0.035, x1 + (x2 - x1) * t, 0.05, z1 + (z2 - z1) * t, ang, wallC); }
      } else {
        bx(len, hh, th, mx, 0, mz, ang, wallC);
        if (yk === 'wall') bx(len, 0.25, th + 0.35, mx, hh, mz, ang, capC);
      }
    }
    if (parts.length) {
      const g = mergeGeometries(parts); for (const p of parts) p.dispose();
      const m = new THREE.Mesh(g, cx.detailMat()); m.userData.bid = b.id; m.userData.ownGeo = true; grp.add(m);
    }
  }
}

// ── 입면 디테일: 층 띠·창턱, 모서리 기둥, 기단, 처마 돌림띠와 난간, 유리 커튼월 멀리언, 1층 상가 유리와 간판 띠,
//    실외기, 현관 계단, 옥상(계단실·물탱크·실외기·안테나·태양광·옥상 정원). 모두 상자 몇 개 → 한 메시 ──
const paintG = (g, c) => {
  const n = g.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return g;
};
const COMMERCIAL = new Set(['bizbuilding', 'shop', 'office', 'townhouse', 'bank', 'hotel', 'supermarket', 'cafe', 'restaurant', 'clinic', 'salon', 'bakery', 'market', 'tailorshop', 'studio', 'postoffice']);
function facade(cx, b, def, grp, { era, st, hex, v, masses, front, shape, r, plainTop }) {
  if (era < 3 || !masses.length) return;
  if (!['brick', 'concrete', 'civic', 'glass'].includes(st)) return;
  const parts = [], C = c => new THREE.Color(c);
  const base = C(hex);
  const ledgeC = st === 'brick' ? C('#d8d0c0') : st === 'glass' ? C('#c9d0d6') : base.clone().lerp(C('#ffffff'), 0.28);
  const darkC = st === 'brick' ? base.clone().multiplyScalar(0.62) : C('#6b6f73');
  const box = (w, h, d, x, y, z, col, ry = 0) => { const g = new THREE.BoxGeometry(w, h, d); if (ry) g.rotateY(ry); g.translate(x, y + h / 2, z); parts.push(paintG(g, col)); };
  const cyl = (rad, h, x, y, z, col, seg = 10) => { const g = new THREE.CylinderGeometry(rad, rad, h, seg); g.translate(x, y + h / 2, z); parts.push(paintG(g, col)); };
  const tex = STYLE[st], fh = tex.th, cw = tex.tw;
  const home = def.kind === 'home', rnd = (k) => hash(b.id * 31 + k * 7);
  const lite = cx.lite;
  let kAC = 0;
  for (const m of masses) {
    const { x, z, w, d, h, y0 } = m;
    const floors = Math.max(1, Math.round(h / fh));
    const faces = [[0, 1, w], [0, -1, w], [1, 0, d], [-1, 0, d]]; // [nx, nz, 길이]
    // 기단 (바닥 둘레 어두운 띠)
    if (y0 === 0) box(w + 0.3, 0.6, d + 0.3, x, 0, z, darkC);
    // 처마 돌림띠 (꼭대기)
    box(w + 0.5, 0.45, d + 0.5, x, y0 + h - 0.45, z, ledgeC);
    if (st === 'glass') {
      // 커튼월: 세로 멀리언(칸마다), 층마다 가로 띠
      for (const [nx, nz, len] of faces) {
        const n = Math.max(1, Math.floor(len / cw));
        for (let i = 0; i <= n; i++) {
          const t = -len / 2 + len * i / n;
          const px = nx ? x + nx * (w / 2 + 0.12) : x + t, pz = nz ? z + nz * (d / 2 + 0.12) : z + t;
          box(nx ? 0.24 : 0.14, h - 0.4, nx ? 0.14 : 0.24, px, y0 + 0.2, pz, ledgeC);
        }
        if (!lite || floors < 12) for (let f = 1; f < floors; f++) box(nx ? 0.12 : len + 0.1, 0.22, nx ? len + 0.1 : 0.12, x + nx * (w / 2 + 0.06), y0 + f * fh - 0.11, z + nz * (d / 2 + 0.06), ledgeC);
      }
    } else {
      // 층마다 창턱 띠 (창 아랫선) — 휴대폰에선 높은 건물은 몇 층 걸러
      const step = lite && floors > 8 ? 2 : 1;
      for (let f = 0; f < floors; f += step) {
        const y = y0 + f * fh + fh * 0.28 - 0.06;
        if (y0 === 0 && f === 0 && COMMERCIAL.has(b.type)) continue;
        for (const [nx, nz, len] of faces) box(nx ? 0.18 : len + 0.04, 0.12, nx ? len + 0.04 : 0.18, x + nx * (w / 2 + 0.09), y, z + nz * (d / 2 + 0.09), ledgeC);
      }
      // 모서리 기둥 (벽돌·관공서)
      if (st === 'brick' || st === 'civic') for (const sx of [-1, 1]) for (const sz of [-1, 1]) box(0.55, h, 0.55, x + sx * (w / 2 - 0.1), y0, z + sz * (d / 2 - 0.1), ledgeC);
      // 실외기 (근대 이후 살림집): 창 아래 몇 군데
      if (home && era >= 5 && st === 'concrete') for (const [nx, nz, len] of faces) {
        if (nz < 0 && nx === 0 && rnd(1) < 0.5) continue;
        const n = Math.max(1, Math.floor(len / cw));
        for (let f = 0; f < floors; f++) for (let i = 0; i < n; i++) {
          if (hash(b.id * 97 + f * 13 + i * 7 + nx * 3 + nz * 5) > 0.22 || kAC > 60) continue;
          kAC++;
          const t = -len / 2 + (i + 0.5) * len / n + cw * 0.28;
          const px = nx ? x + nx * (w / 2 + 0.22) : x + t, pz = nz ? z + nz * (d / 2 + 0.22) : z + t;
          box(nx ? 0.42 : 0.82, 0.58, nx ? 0.82 : 0.42, px, y0 + f * fh + 0.25, pz, C('#e7e8e6'));
          box(nx ? 0.05 : 0.5, 0.42, nx ? 0.5 : 0.05, px + nx * 0.22, y0 + f * fh + 0.33, pz + nz * 0.22, C('#5b6066'));
        }
      }
    }
    // 옥상 (평지붕 맨 위 덩어리)
    if (r === 'flat' && era >= 3 && (plainTop || m === masses[masses.length - 1] || shape === 'twin')) {
      const top = y0 + h;
      // 난간 (네 면)
      box(w + 0.5, 0.95, 0.22, x, top, z + d / 2 + 0.14, ledgeC); box(w + 0.5, 0.95, 0.22, x, top, z - d / 2 - 0.14, ledgeC);
      box(0.22, 0.95, d + 0.5, x + w / 2 + 0.14, top, z, ledgeC); box(0.22, 0.95, d + 0.5, x - w / 2 - 0.14, top, z, ledgeC);
      if (era >= 4 && w > 8 && d > 8) {
        // 계단실
        const sx = x + (rnd(2) - 0.5) * w * 0.4, sz = z + (rnd(3) - 0.5) * d * 0.4;
        box(3.2, 2.8, 3.6, sx, top, sz, base.clone().lerp(C('#ffffff'), 0.1)); box(3.5, 0.25, 3.9, sx, top + 2.8, sz, ledgeC); box(1.0, 2.0, 0.08, sx, top, sz + 1.82, C('#4a5560'));
        // 물탱크 (산업·근대)
        if (era <= 5 && rnd(4) < 0.75) { const tx = x - w * 0.25, tz = z + d * 0.22; for (const [lx, lz] of [[-0.7, -0.7], [0.7, -0.7], [-0.7, 0.7], [0.7, 0.7]]) box(0.14, 1.2, 0.14, tx + lx, top, tz + lz, C('#6b7075')); cyl(1.15, 2.0, tx, top + 1.2, tz, era === 4 ? C('#5e88b5') : C('#d8dcdf'), 14); }
        // 실외기
        if (era >= 5) for (let k = 0; k < (w > 20 ? 3 : 1); k++) { const ax = x + w * 0.28 - k * 2.4, az = z - d * 0.25; box(1.8, 1.1, 1.3, ax, top, az, C('#cfd3d6')); cyl(0.42, 0.06, ax, top + 1.1, az, C('#3a3f44'), 12); }
        // 안테나
        if (rnd(5) < 0.6) { box(0.08, 3.2, 0.08, x + w * 0.35, top, z + d * 0.3, C('#9aa1a8')); box(1.4, 0.06, 0.06, x + w * 0.35, top + 2.6, z + d * 0.3, C('#9aa1a8')); }
        // 정보화 시대: 태양광 패널 · 옥상 정원
        if (era >= 6 && rnd(6) < 0.45) for (let i = 0; i < 3; i++) { const g = new THREE.BoxGeometry(w * 0.35, 0.08, 1.6); g.rotateX(-0.45); g.translate(x - w * 0.12, top + 0.75, z - d * 0.3 + i * 2.2); parts.push(paintG(g, C('#22324f'))); }
        else if (era >= 6 && rnd(7) < 0.35) { box(w * 0.55, 0.35, d * 0.45, x - w * 0.15, top, z + d * 0.15, C('#5f8f4a')); for (let i = 0; i < 4; i++) cyl(0.5, 1.2, x - w * 0.35 + i * w * 0.13, top + 0.35, z + d * 0.15 + (i % 2 - 0.5) * d * 0.2, C('#3f7a3a'), 8); }
      }
    }
  }
  // 1층: 상가 유리창과 간판 띠 (근대 이후 상업 건물)
  if (COMMERCIAL.has(b.type) && era >= 4 && st !== 'glass') {
    const fw = Math.min(front.w * 0.92, 24), gz = front.z + 0.08;
    box(fw, 2.7, 0.14, front.x, 0.45, gz, C('#2f3d48'));
    for (let t = -fw / 2; t <= fw / 2 + 0.01; t += 2.2) box(0.1, 2.7, 0.2, front.x + t, 0.45, gz + 0.02, C('#c9ccd0'));
    box(fw + 0.2, 0.9, 0.3, front.x, 3.2, gz + 0.06, C(def.accent || ['#c0392b', '#1f6f8b', '#e9b949', '#2a9d8f', '#6c5b7b', '#264653'][Math.floor(v * 6)]));
    box(fw * 0.5, 0.42, 0.06, front.x, 3.44, gz + 0.24, C('#f7f3ea'));
  }
  // 현관 계단
  if (masses[0]) box(Math.min(front.w * 0.5, def.kind === 'civic' ? 6 : 3), 0.18, 1.0, front.x, 0, front.z + 0.6, C('#b9b4ab'));
  if (!parts.length) return;
  const geo = mergeGeometries(parts);
  for (const g of parts) g.dispose();
  const mesh = new THREE.Mesh(geo, cx.detailMat());
  mesh.userData.bid = b.id; mesh.userData.ownGeo = true;
  grp.add(mesh);
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
