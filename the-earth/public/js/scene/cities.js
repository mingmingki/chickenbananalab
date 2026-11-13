// 정착지 렌더링: 지면(도로·광장·밭), 건물(시대별 양식, 공사 중 비계, 밤의 창문 불빛), 앤들(사람 모형·점)
import * as THREE from 'three';
import { R, frameAt, llToVec, localToLL } from './geo.js';
import { glowTexture } from './space.js';
import { Terrain, TERRAIN_R } from '../shared/terrain.js';
import { makeBuilding, BGEO } from './buildings.js';
import { paintRoads, buildRoadProps, roadLampMats, signalPhase } from './roads.js';
import { TownVehicles } from './vehicles.js';
import { treeColor, seasonOfMonth } from './weather.js';
import { dateOf } from '../state.js';
import { PHONE } from '../device.js';

const CELL = 20, BLOCK = 5;
const mod = (n, m) => ((n % m) + m) % m;
const hash = n => { let h = (n * 2654435761) >>> 0; h ^= h >>> 15; return (h % 1000) / 1000; };

function prismGeometry() {
  const s = new THREE.Shape();
  s.moveTo(-0.5, 0); s.lineTo(0.5, 0); s.lineTo(0, 1); s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth: 1, bevelEnabled: false });
  g.translate(0, 0, -0.5);
  return g;
}
const G = {
  box: new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0),
  cone: new THREE.ConeGeometry(0.74, 1, 10).translate(0, 0.5, 0),
  pyr: new THREE.ConeGeometry(0.72, 1, 4).rotateY(Math.PI / 4).translate(0, 0.5, 0),
  dome: new THREE.SphereGeometry(0.5, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
  prism: prismGeometry(),
  cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 16).translate(0, 0.5, 0),
  flame: new THREE.ConeGeometry(0.5, 1, 7).translate(0, 0.5, 0),
  torus: new THREE.TorusGeometry(1, 0.22, 10, 40).rotateX(Math.PI / 2),
  body: new THREE.CapsuleGeometry(0.21, 0.72, 4, 10).translate(0, 0.59, 0),
  head: new THREE.SphereGeometry(0.165, 14, 10).translate(0, 1.36, 0),
  hair: new THREE.SphereGeometry(0.178, 14, 8, 0, Math.PI * 2, 0, Math.PI * 0.55).translate(0, 1.38, 0),
  hat: new THREE.CylinderGeometry(0.15, 0.2, 0.13, 12).translate(0, 1.52, 0),
};
const HAT = { police: '#1f2a44', soldier: '#1c1c1c', firefighter: '#c62828' };
const HARDHAT = new Set(['builder', 'laborer', 'lumberjack', 'steelworker', 'welder', 'miner', 'worker', 'assembler', 'operator', 'machinist', 'engineer']);

// 창문 타일 (낮: 유리 / 밤: 켜진 창)
function windowTextures() {
  const S = 128;
  const a = document.createElement('canvas'); a.width = a.height = S;
  const e = document.createElement('canvas'); e.width = e.height = S;
  const ga = a.getContext('2d'), ge = e.getContext('2d');
  ga.fillStyle = '#fff'; ga.fillRect(0, 0, S, S);
  ge.fillStyle = '#000'; ge.fillRect(0, 0, S, S);
  for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) {
    const x = i * 64 + 16, y = j * 64 + 14, w = 32, h = 34;
    ga.fillStyle = '#5d7389'; ga.fillRect(x, y, w, h);
    ga.fillStyle = 'rgba(255,255,255,0.35)'; ga.fillRect(x + 3, y + 3, 8, h - 6);
    if (Math.random() < 0.72) { ge.fillStyle = Math.random() < 0.5 ? '#ffd98a' : '#ffe9bd'; ge.fillRect(x, y, w, h); }
  }
  ga.fillStyle = '#ddd'; ga.fillRect(0, 0, 6, 6);
  const ta = new THREE.CanvasTexture(a), te = new THREE.CanvasTexture(e);
  for (const t of [ta, te]) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; }
  return { map: ta, emissive: te };
}

/** 벽 크기에 맞춘 UV (창문이 늘어나지 않게) */
function boxWithUV(w, h, d) {
  const g = new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0);
  const uv = g.attributes.uv;
  const spans = [[d, h], [d, h], null, null, [w, h], [w, h]];
  for (let f = 0; f < 6; f++) for (let k = 0; k < 4; k++) {
    const i = f * 4 + k;
    if (!spans[f]) { uv.setXY(i, 0.01, 0.01); continue; }
    uv.setXY(i, uv.getX(i) * spans[f][0] / 12, uv.getY(i) * spans[f][1] / 7);
  }
  return g;
}

function biomeGround(lat) {
  const a = Math.abs(lat);
  if (a > 64) return '#a7ae98';
  if (a > 50) return '#5e8250';
  if (a > 36) return '#6f954f';
  if (a > 22) return '#9a9a5e';
  return '#5e9146';
}

export class Cities {
  constructor(parent, meta, earthMesh) {
    this.meta = meta;
    this.earthMesh = earthMesh;
    this.ray = new THREE.Raycaster();
    this.root = new THREE.Group(); this.root.name = 'cities';
    parent.add(this.root);
    this.towns = new Map();
    this.mats = new Map();
    this.win = windowTextures();
    this.winMats = [];
    this.markerTex = glowTexture('rgba(255,236,190,1)', 'rgba(255,180,90,0.45)', 64);
    this.flameMats = [];
    this.smoke = [];
    this.cranes = [];
    this.lineMats = new Map();
    this.wallMats = new Map();
    this.smokeTex = glowTexture('rgba(200,200,200,0.9)', 'rgba(150,150,150,0.25)', 64);
    const self = this;
    this.bctx = {
      mat: (c, o) => self.mat(c, o),
      wallMat: (st, hex, tex) => self.wallMat(st, hex, tex),
      lineMat: c => self.lineMat(c),
      flames: this.flameMats, cranes: this.cranes,
      smokeAt: (grp, x, y, z, k, fire) => self.smokeAt(grp, x, y, z, k, fire),
      lamp: m => m, glowTex: this.markerTex,
    };
    this.selected = null;
    this.night = 0;
    this.selMarker = this.makeSelMarker();
  }

  mat(color, opts = {}) {
    const key = color + JSON.stringify(opts);
    let m = this.mats.get(key);
    if (!m) {
      if (opts.windows) {
        m = new THREE.MeshStandardMaterial({ color, map: this.win.map, emissiveMap: this.win.emissive, emissive: 0xffffff, emissiveIntensity: 0, roughness: opts.glass ? 0.35 : 0.85, metalness: opts.glass ? 0.3 : 0 });
        this.winMats.push(m);
      } else if (opts.basic) m = new THREE.MeshBasicMaterial({ color, transparent: !!opts.opacity, opacity: opts.opacity ?? 1, depthWrite: !opts.opacity });
      else m = new THREE.MeshStandardMaterial({ color, roughness: 0.9, metalness: 0, transparent: !!opts.opacity, opacity: opts.opacity ?? 1, emissive: opts.emissive || 0x000000, emissiveIntensity: opts.emissive ? 1 : 0 });
      this.mats.set(key, m);
    }
    return m;
  }

  wallMat(st, hex, tex) {
    const key = st + hex;
    let m = this.wallMats.get(key);
    if (!m) {
      m = new THREE.MeshStandardMaterial({ map: tex.map, emissiveMap: tex.emissive, emissive: 0xffffff, emissiveIntensity: 0, roughness: st === 'glass' ? 0.25 : 0.88, metalness: st === 'glass' ? 0.35 : 0 });
      this.wallMats.set(key, m); this.winMats.push(m);
    }
    return m;
  }
  lineMat(c) {
    let m = this.lineMats.get(c);
    if (!m) { m = new THREE.LineBasicMaterial({ color: c }); this.lineMats.set(c, m); }
    return m;
  }
  /** 굴뚝·불난 건물의 연기: 피어오르며 흩어지는 스프라이트 몇 개 */
  smokeAt(grp, x, y, z, k = 1, fire = false) {
    const n = fire ? 7 : 4;
    for (let i = 0; i < n; i++) {
      const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.smokeTex, color: fire ? 0x3a3532 : 0xd8d8d8, transparent: true, depthWrite: false, opacity: 0 }));
      spr.position.set(x, y, z);
      grp.add(spr);
      this.smoke.push({ spr, x, y, z, k, ph: i / n, sp: fire ? 0.35 : 0.12 + Math.random() * 0.05 });
    }
  }

  makeSelMarker() {
    const g = new THREE.Group();
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.7, 0.95, 32).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x58b4ff, transparent: true, opacity: 0.9, depthWrite: false, side: THREE.DoubleSide }));
    ring.position.y = 0.05;
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.25, 60, 8, 1, true).translate(0, 30, 0), new THREE.MeshBasicMaterial({ color: 0x7ec8ff, transparent: true, opacity: 0.22, depthWrite: false, blending: THREE.AdditiveBlending }));
    g.add(ring, beam);
    g.visible = false;
    return g;
  }

  /** 정착지 목록을 반영 */
  syncTowns(state) {
    for (const s of state.settlements.values()) {
      let t = this.towns.get(s.id);
      if (!t) { t = this.makeTown(s); this.towns.set(s.id, t); state.dirty.buildings.add(s.id); }
      t.s = s;
      // 신호등이 켜지거나 정류장이 생기면 길 소품을 다시
      const tk = Object.keys(s.signals || {}).join() + '|' + (s.bus?.stops || []).map(x => (x.built ? 1 : 0)).join('');
      if (tk !== t.trafficKey) { t.trafficKey = tk; state.dirty.buildings.add(s.id); }
      t.marker.scale.setScalar(0.012 + s.level * 0.005 + Math.min(0.012, s.pop * 0.0004));
      t.marker.material.opacity = s.abandoned ? 0.25 : 1;
    }
  }

  makeTown(s) {
    const { up, east, north } = frameAt(s.lat, s.lon);
    const south = north.clone().negate();
    const g = new THREE.Group();
    g.matrixAutoUpdate = false;
    // 지구 다각형 면은 실제 구보다 최대 ~200m 낮다 → 렌더링된 지표 위에 정확히 앉힌다
    this.ray.set(up.clone().multiplyScalar(R + 5), up.clone().negate());
    const hit = this.ray.intersectObject(this.earthMesh, false)[0];
    const base = hit ? hit.point.length() + 0.0008 : R;
    const m = new THREE.Matrix4().makeBasis(east, up, south);
    m.multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001));
    m.setPosition(up.clone().multiplyScalar(base));
    g.matrix.copy(m);
    g.matrixWorldNeedsUpdate = true;
    this.root.add(g);
    const marker = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.markerTex, transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending, sizeAttenuation: false }));
    marker.position.copy(up).multiplyScalar(R + 2);
    this.root.add(marker);
    const t = { s, g, marker, base, center: up.clone().multiplyScalar(base), up, buildings: new THREE.Group(), bmap: new Map(), ground: null, anns: null, dots: null, ids: [], groundKey: '' };
    t.terrain = new Terrain(s.site);
    const hc = new Map();
    t.hAt = (x, z) => {
      const k = Math.round(x / 2) * 100003 + Math.round(z / 2);
      let h = hc.get(k);
      if (h === undefined) { h = Math.max(0, t.terrain.height(x, z)); if (hc.size > 50000) hc.clear(); hc.set(k, h); }
      return h;
    };
    g.add(t.buildings);
    return t;
  }

  /** 자연: 산과 언덕(지형), 물(바다·강·호수), 숲. 가까워졌을 때 한 번 만든다 */
  buildNature(t) {
    const T = t.terrain, N = 168, TR = TERRAIN_R, step = 2 * TR / N;
    const pos = new Float32Array((N + 1) * (N + 1) * 3), col = new Float32Array((N + 1) * (N + 1) * 3), H = new Float32Array((N + 1) * (N + 1));
    for (let j = 0; j <= N; j++) for (let i = 0; i <= N; i++) {
      const x = -TR + i * step, z = -TR + j * step, k = j * (N + 1) + i;
      const rr = Math.hypot(x, z);
      // 가장자리는 지구 표면 아래로 살짝 가라앉혀 절벽 없이 이어지게
      const h = T.height(x, z) - 60 * Math.min(1, Math.max(0, (rr - TR * 0.955) / (TR * 0.045)));
      H[k] = h; pos[k * 3] = x; pos[k * 3 + 1] = h; pos[k * 3 + 2] = z;
    }
    const grass = new THREE.Color(biomeGround(t.s.lat)), forest = new THREE.Color('#2f5a2e'), sand = new THREE.Color('#d9c89a'), rock = new THREE.Color('#7b7166'),
      snow = new THREE.Color('#eef1f4'), wet = new THREE.Color('#6d6a52'), cc = new THREE.Color(), muted = new THREE.Color(biomeGround(t.s.lat)).lerp(new THREE.Color('#8a9a78'), 0.5);
    const snowLine = t.s.site?.biome === 'snow' ? 180 : t.s.site?.biome === 'tropical' ? 900 : 520;
    const snowMask = new Float32Array((N + 1) * (N + 1));
    for (let j = 0; j <= N; j++) for (let i = 0; i <= N; i++) {
      const k = j * (N + 1) + i, h = H[k], x = pos[k * 3], z = pos[k * 3 + 2];
      const hx = H[j * (N + 1) + Math.min(N, i + 1)] - H[j * (N + 1) + Math.max(0, i - 1)], hz = H[Math.min(N, j + 1) * (N + 1) + i] - H[Math.max(0, j - 1) * (N + 1) + i];
      const slope = Math.hypot(hx, hz) / (2 * step);
      const fr = T.forest(x, z, h);
      if (h < 0.4) cc.copy(wet).lerp(sand, 0.4);
      else if (h < 2.2 && t.s.site?.coast) cc.copy(sand);
      else { cc.copy(grass).lerp(forest, Math.min(1, fr * 1.3)); if (t.s.site?.biome === 'desert') cc.lerp(sand, 0.55); }
      cc.lerp(rock, Math.min(1, Math.max(0, (slope - 0.35) * 2.2) + Math.max(0, (h - 260) / 300)));
      if (h > snowLine) cc.lerp(snow, Math.min(1, (h - snowLine) / 120));
      const edge = Math.min(1, Math.max(0, (Math.hypot(x, z) - TR * 0.8) / (TR * 0.17)));
      cc.lerp(muted, edge * 0.7);
      const n = 0.92 + ((i * 928371 + j * 1237) % 97) / 97 * 0.12;
      col[k * 3] = cc.r * n; col[k * 3 + 1] = cc.g * n; col[k * 3 + 2] = cc.b * n;
      snowMask[k] = h < 0.4 ? 0 : Math.max(0, 1 - Math.max(0, slope - 0.25) * 2.5) * (1 - edge * 0.5);
    }
    const idx = [];
    for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
      const cx = -TR + (i + 0.5) * step, cz = -TR + (j + 0.5) * step;
      if (cx * cx + cz * cz > TR * TR) continue;
      const a = j * (N + 1) + i, b = a + N + 1;
      idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const land = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, metalness: 0 }));
    land.userData.terrain = true;
    t.baseCol = col.slice(); t.snowMask = snowMask; t.landGeo = geo; t.snowApplied = 0;
    const water = new THREE.Mesh(new THREE.CircleGeometry(TR * 0.955, 96).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial({ color: 0x2b6a96, transparent: true, opacity: 0.86, roughness: 0.12, metalness: 0.15 }));
    water.position.y = -0.3;
    t.g.add(land, water);
    // 강: 지형 격자보다 가는 물길도 끊기지 않도록 지형 위에 덮는 띠
    const rv = t.s.site?.river;
    if (rv) {
      const hGrid = (x, z) => {
        const fx = (x + TR) / step, fz = (z + TR) / step, i = Math.max(0, Math.min(N - 1, Math.floor(fx))), j = Math.max(0, Math.min(N - 1, Math.floor(fz)));
        const u = fx - i, v = fz - j, k = j * (N + 1) + i;
        return (H[k] * (1 - u) + H[k + 1] * u) * (1 - v) + (H[k + N + 1] * (1 - u) + H[k + N + 2] * u) * v;
      };
      const rc = Math.cos(rv.a), rs = Math.sin(rv.a);
      const center = u => rv.off + rv.m * Math.sin(u / 950 + rv.p) + rv.m * 0.35 * Math.sin(u / 330 + rv.p2);
      const rp = [], ri = [];
      let prevOk = false;
      for (let u = -TR; u <= TR; u += 16) {
        const v = center(u), dv = (center(u + 1) - center(u - 1)) / 2;
        const x = u * rc - v * rs, z = u * rs + v * rc;
        const ok = x * x + z * z < (TR * 0.95) ** 2;
        const tx = rc - dv * rs, tz = rs + dv * rc, tl = Math.hypot(tx, tz);
        const nx = -tz / tl, nz = tx / tl, hw = rv.w * 0.85;
        const base = rp.length / 3;
        for (const sgn of [-1, 1]) { const px = x + nx * hw * sgn, pz = z + nz * hw * sgn; rp.push(px, Math.max(hGrid(px, pz), hGrid(x, z)) + 0.5, pz); }
        if (ok && prevOk) ri.push(base - 2, base - 1, base, base - 1, base + 1, base);
        prevOk = ok;
      }
      const rg = new THREE.BufferGeometry();
      rg.setAttribute('position', new THREE.Float32BufferAttribute(rp, 3)); rg.setIndex(ri); rg.computeVertexNormals();
      const river = new THREE.Mesh(rg, new THREE.MeshStandardMaterial({ color: 0x2f74a6, roughness: 0.15, metalness: 0.1, transparent: true, opacity: 0.92, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -3, depthWrite: false }));
      river.renderOrder = 2;
      t.g.add(river);
    }
    // 나무 후보 (숲은 촘촘히, 들판엔 드문드문)
    const trees = [];
    const SP = 30;
    for (let x = -TR * 0.9; x < TR * 0.9; x += SP) for (let z = -TR * 0.9; z < TR * 0.9; z += SP) {
      const hh = (Math.sin(x * 12.9898 + z * 78.233) * 43758.5453) % 1, r1 = Math.abs(hh), r2 = Math.abs((hh * 7.31) % 1);
      const px = x + (r1 - 0.5) * SP, pz = z + (r2 - 0.5) * SP;
      if (px * px + pz * pz > (TR * 0.88) ** 2) continue;
      const p = T.sample(px, pz);
      if (p.water || p.h < 1.2) continue;
      const f = T.forest(px, pz, p.h);
      if (r1 > f * 0.95 + 0.03) continue;
      trees.push([px, p.h, pz, 0.7 + r2 * 0.7, f > 0.5 && p.h > 120 ? 1 : 0]);
      if (trees.length > (PHONE ? 6000 : 14000)) break;
    }
    t.treeList = trees;
    const crown = new THREE.ConeGeometry(3.2, 9, 7).translate(0, 8, 0), trunk = new THREE.CylinderGeometry(0.45, 0.6, 4, 5).translate(0, 2, 0);
    t.treeCrown = new THREE.InstancedMesh(crown, new THREE.MeshStandardMaterial({ color: 0x2f6b34, roughness: 0.9 }), trees.length);
    t.treeTrunk = new THREE.InstancedMesh(trunk, new THREE.MeshStandardMaterial({ color: 0x5b4330, roughness: 1 }), trees.length);
    t.treeCrown.frustumCulled = t.treeTrunk.frustumCulled = false;
    t.g.add(t.treeCrown, t.treeTrunk);
    t.nature = { land, water };
    this.refreshTrees(t);
  }

  /** 건물과 도로가 들어선 자리의 나무는 베어진다 */
  refreshTrees(t, blds = null) {
    if (!t.treeList) return;
    const sea = this.seasonOf(t);
    blds = blds || t.lastBlds || [];
    const cleared = [];
    for (const b of blds) cleared.push([b.x, b.z, Math.max(b.w, b.d) / 2 + 9]);
    let devR = 0;
    for (const b of blds) if (b.type !== 'farm') devR = Math.max(devR, Math.hypot(b.x, b.z));
    const core = devR > 0 ? devR + 40 : 45;
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), p = new THREE.Vector3(), col = new THREE.Color();
    let n = 0;
    for (const [x, h, z, s, kind] of t.treeList) {
      if (x * x + z * z < core * core) continue;
      let hit = false;
      for (const [cx, cz, r] of cleared) if ((x - cx) ** 2 + (z - cz) ** 2 < r * r) { hit = true; break; }
      if (hit) continue;
      const r = ((s * 7919.37) % 1 + 1) % 1;
      const bare = sea.season === '겨울' && kind === 0 && r < 0.6 && t.s.site?.biome !== 'tropical';
      sc.set(s * (bare ? 0.7 : 1), s * (kind ? 1.25 : 1), s * (bare ? 0.7 : 1)); p.set(x, h - 0.3, z);
      m.compose(p, q, sc);
      t.treeCrown.setMatrixAt(n, m); t.treeTrunk.setMatrixAt(n, m);
      t.treeCrown.setColorAt(n, treeColor(col, sea.season, sea.month, sea.snow, kind, r, t.s.site?.biome));
      n++;
    }
    t.treeCrown.count = t.treeTrunk.count = n;
    t.treeCrown.instanceMatrix.needsUpdate = t.treeTrunk.instanceMatrix.needsUpdate = true;
    if (t.treeCrown.instanceColor) t.treeCrown.instanceColor.needsUpdate = true;
  }

  /** 이 정착지의 지금 계절·달·쌓인 눈 */
  seasonOf(t) {
    const d = dateOf(this.now || 0);
    return { season: t.s.weather?.season || seasonOfMonth(d.month, t.s.lat), month: t.s.lat < 0 ? ((d.month + 5) % 12) + 1 : d.month, snow: t.s.snow || 0, temp: t.s.weather?.temp ?? 12 };
  }
  /** 지형에 눈을 덮거나 걷어낸다 */
  applySnow(t) {
    if (!t.landGeo) return;
    const snow = t.s.snow || 0;
    if (Math.abs(snow - t.snowApplied) < 0.04) return;
    t.snowApplied = snow;
    const col = t.landGeo.attributes.color.array, base = t.baseCol, mask = t.snowMask;
    for (let k = 0; k < mask.length; k++) {
      const a = Math.min(0.92, snow * 1.1) * mask[k];
      col[k * 3] = base[k * 3] * (1 - a) + 0.95 * a; col[k * 3 + 1] = base[k * 3 + 1] * (1 - a) + 0.965 * a; col[k * 3 + 2] = base[k * 3 + 2] * (1 - a) + 0.98 * a;
    }
    t.landGeo.attributes.color.needsUpdate = true;
  }

  /** 정착지 하나의 건물/지면 다시 만들기 */
  rebuild(state, sid) {
    const t = this.towns.get(sid);
    if (!t) return;
    const s = t.s;
    const era = state.countries.get(s.country)?.era ?? 0;
    const blds = [...state.buildings.values()].filter(b => b.sid === sid);
    // 지면
    let maxR = 60;
    for (const b of blds) maxR = Math.max(maxR, Math.hypot(b.x, b.z) + Math.max(b.w, b.d));
    const rg = Math.ceil((maxR + 90) / 100) * 100;
    if (!t.ground || t.groundR !== rg) {
      if (t.ground) { t.g.remove(t.ground); t.ground.geometry.dispose(); t.ground.material.map.dispose(); }
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = rg > 300 && !PHONE ? 2048 : 1024;
      const tex = new THREE.CanvasTexture(canvas); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
      // 도로·광장·밭을 지형 위에 덮는 얇은 막 (지형의 높낮이를 따른다)
      const M = 96, geo = new THREE.PlaneGeometry(2 * rg, 2 * rg, M, M).rotateX(-Math.PI / 2);
      const pa = geo.attributes.position;
      for (let i = 0; i < pa.count; i++) pa.setY(i, t.hAt(pa.getX(i), pa.getZ(i)) + 0.45);
      geo.computeVertexNormals();
      const ground = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: tex, roughness: 1, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 }));
      ground.renderOrder = 1;
      t.g.add(ground);
      t.ground = ground; t.groundR = rg; t.canvas = canvas; t.groundTex = tex;
    }
    const works = blds.filter(b => b.infra && !b.done);
    const real = blds.filter(b => !b.infra);
    t.lastBlds = real;
    this.refreshTrees(t, real);
    this.paintGround(t, real, era, works);
    // 도로 소품 (가로등·가로수·신호등·공사 고깔)
    const roadKey = JSON.stringify(s.roads || {}) + works.map(w => w.id + ':' + Math.round((w.p || 0) * 20) + ':' + (w.stage ?? '')).join(',') + '|' + (t.trafficKey || '');
    if (roadKey !== t.roadKey) {
      t.roadKey = roadKey;
      if (t.roadProps) { t.g.remove(t.roadProps.grp); t.roadProps.grp.traverse(o => o.dispose?.()); }
      t.roadProps = buildRoadProps(this.roadsOf(s, real), works, t.hAt, { signals: s.signals, stops: s.bus?.stops });
      t.g.add(t.roadProps.grp);
      this.colorRoadTrees(t);
    }
    // 건물
    const sea = this.seasonOf(t);
    const snowB = Math.round(Math.min(1, sea.snow) * 4) / 4, cold = sea.season === '겨울' || sea.temp < 6;
    const keep = new Set();
    for (const b of real) {
      keep.add(b.id);
      const key = `${b.type}|${b.done}|${Math.round((b.p || 0) * 40)}|${b.h}|${b.fire ? 1 : 0}${b.burnt ? 1 : 0}${b.stalled ? 1 : 0}${(b.crew || 0) > 0 ? 1 : 0}|${snowB}|${cold ? 1 : 0}|${era}`;
      const old = t.bmap.get(b.id);
      if (old && old.userData.key === key) continue;
      if (old) { t.buildings.remove(old); disposeGroup(old); }
      const def = this.meta.buildings[b.type] || { roof: 'flat', color: '#999', kind: 'work' };
      const m = makeBuilding(this.bctx, b, def, { era, snow: snowB, hAt: t.hAt, cold });
      m.userData.key = key;
      t.buildings.add(m);
      t.bmap.set(b.id, m);
    }
    t.snowB = snowB; t.cold = cold;
    for (const [id, m] of t.bmap) if (!keep.has(id)) { t.buildings.remove(m); disposeGroup(m); t.bmap.delete(id); }
  }

  /** 도로 등급표 (서버가 아직 안 보냈으면 개발된 블록 둘레를 흙길로) */
  roadsOf(s, blds) {
    if (s.roads && Object.keys(s.roads).length) return s.roads;
    const roads = {};
    const blocks = new Set(['0,0', '-1,-1', '-1,0', '0,-1']);
    for (const b of blds) if (b.type !== 'farm') blocks.add(Math.floor(Math.floor(b.x / CELL) / BLOCK) + ',' + Math.floor(Math.floor(b.z / CELL) / BLOCK));
    for (const k of blocks) { const [bi, bj] = k.split(',').map(Number); for (const e of [`h,${bi},${bj}`, `h,${bi},${bj + 1}`, `v,${bi},${bj}`, `v,${bi + 1},${bj}`]) roads[e] = 0; }
    return roads;
  }

  paintGround(t, blds, era, works = []) {
    const c = t.canvas, g = c.getContext('2d'), S = c.width, rg = t.groundR;
    const px = v => (v + rg) / (2 * rg) * S, sc = S / (2 * rg);
    g.clearRect(0, 0, S, S);
    const off = CELL / 2;
    const snow = t.s.snow || 0, sea = this.seasonOf(t);
    // 광장
    g.fillStyle = era >= 3 ? '#b9b2a4' : '#b09a74';
    g.beginPath(); g.arc(px(off), px(off), 18 * sc, 0, 6.28); g.fill();
    // 건물 바닥과 밭 (밭은 계절 따라: 봄 갈아엎은 흙, 여름 초록, 가을 누런 벼, 겨울 빈 밭)
    const fieldCol = { 봄: ['#8a7350', '#7fa83a'], 여름: ['#5f9a35', '#79a83a'], 가을: ['#d2b24a', '#c9a23f'], 겨울: ['#8b7a5e', '#7d6c52'] }[sea.season] || ['#c9b24f', '#7fa83a'];
    for (const b of blds) {
      if (b.type === 'farm') {
        const x = px(b.x - b.w / 2), z = px(b.z - b.d / 2), w = b.w * sc, d = b.d * sc;
        g.fillStyle = b.done ? fieldCol[hash(b.id) < 0.5 ? 0 : 1] : '#8a7350';
        g.fillRect(x, z, w, d);
        g.strokeStyle = 'rgba(70,50,20,0.35)'; g.lineWidth = Math.max(1, 0.8 * sc);
        for (let k = 1.5; k < b.d; k += 2.5) { g.beginPath(); g.moveTo(x, z + k * sc); g.lineTo(x + w, z + k * sc); g.stroke(); }
        if (snow > 0.05) { g.fillStyle = `rgba(244,247,250,${Math.min(0.85, snow)})`; g.fillRect(x, z, w, d); }
      } else {
        g.fillStyle = era >= 4 ? 'rgba(150,150,150,0.8)' : 'rgba(120,100,70,0.55)';
        const m = 3;
        g.fillRect(px(b.x - b.w / 2 - m), px(b.z - b.d / 2 - m), (b.w + 2 * m) * sc, (b.d + 2 * m) * sc);
      }
    }
    // 횡단보도: 켜진 신호등, 그리고 도색 단계까지 온 신호등 공사
    const cross = [...Object.keys(t.s.signals || {}), ...works.filter(w => w.type === 'signal' && w.stage >= 6).map(w => w.node)];
    paintRoads(g, px, sc, this.roadsOf(t.s, blds), works, snow, cross);
    const fade = g.createRadialGradient(S / 2, S / 2, S * 0.3, S / 2, S / 2, S / 2);
    fade.addColorStop(0, 'rgba(0,0,0,1)'); fade.addColorStop(0.75, 'rgba(0,0,0,0.75)'); fade.addColorStop(1, 'rgba(0,0,0,0)');
    g.globalCompositeOperation = 'destination-in'; g.fillStyle = fade; g.fillRect(0, 0, S, S);
    g.globalCompositeOperation = 'source-over';
    t.groundTex.needsUpdate = true;
  }

  /** 가로수 색도 계절을 따른다 */
  colorRoadTrees(t) {
    const cr = t.roadProps?.crowns;
    if (!cr) return;
    const sea = this.seasonOf(t), col = new THREE.Color();
    for (let i = 0; i < cr.count; i++) treeColor(col, sea.season, sea.month, sea.snow, 0, ((i * 0.618) % 0.6), t.s.site?.biome), cr.setColorAt(i, col);
    if (cr.instanceColor) cr.instanceColor.needsUpdate = true;
  }

  ensureAnnMeshes(t, n) {
    if (t.anns && t.cap >= n) return;
    const cap = Math.max(32, 2 ** Math.ceil(Math.log2(n + 1)));
    if (t.anns) for (const m of Object.values(t.anns)) { t.g.remove(m); m.dispose(); }
    const mk = (geo, mat) => { const m = new THREE.InstancedMesh(geo, mat, cap); m.count = 0; m.frustumCulled = false; t.g.add(m); return m; };
    t.anns = {
      body: mk(G.body, new THREE.MeshStandardMaterial({ roughness: 0.8 })),
      head: mk(G.head, new THREE.MeshStandardMaterial({ roughness: 0.7 })),
      hair: mk(G.hair, new THREE.MeshStandardMaterial({ roughness: 0.9 })),
      hat: mk(G.hat, new THREE.MeshStandardMaterial({ roughness: 0.6 })),
    };
    t.cap = cap;
    if (t.dots) { t.g.remove(t.dots); t.dots.geometry.dispose(); }
    const dg = new THREE.BufferGeometry();
    dg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * 3), 3));
    dg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(cap * 3), 3));
    t.dots = new THREE.Points(dg, new THREE.PointsMaterial({ size: 5, sizeAttenuation: false, vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false }));
    t.dots.frustumCulled = false;
    t.g.add(t.dots);
  }

  /** 매 프레임: 거리별 표시, 앤 위치 갱신. 라벨 요청 목록을 돌려준다 */
  update(state, camera, rig, time) {
    const labels = [];
    const camPos = camera.position;
    const alt = rig.alt;
    this.now = state.now();
    // 떨어져 나간(다시 지어진) 건물의 효과는 정리
    if (!this.pruneT || time - this.pruneT > 2) {
      this.pruneT = time;
      const live = o => { while (o) { if (o === this.root) return true; o = o.parent; } return false; };
      for (const k of ['flameMats', 'cranes']) { const keep = this[k].filter(live); this[k].length = 0; this[k].push(...keep); }
      this.smoke = this.smoke.filter(sm => live(sm.spr));
    }
    for (const fm of this.flameMats) { const b = fm.userData.base || (fm.userData.base = fm.scale.y); fm.scale.y = b * (0.85 + Math.sin(time * 9 + fm.id) * 0.2); }
    for (const sm of this.smoke) {
      const k = (time * sm.sp + sm.ph) % 1;
      sm.spr.position.set(sm.x + Math.sin(k * 3 + sm.ph * 9) * sm.k * 1.5 + k * sm.k * 4, sm.y + k * sm.k * 14, sm.z);
      const sz = sm.k * (2 + k * 6);
      sm.spr.scale.set(sz, sz, 1);
      sm.spr.material.opacity = Math.sin(k * Math.PI) * (sm.k > 2 ? 0.75 : 0.45);
    }
    for (const j of this.cranes) j.rotation.y = j.userData.r0 + Math.sin(time * 0.12 + j.userData.ph) * 1.1;
    for (const m of this.winMats) m.emissiveIntensity = this.night * 1.4;
    for (const m of roadLampMats) m.emissiveIntensity = this.night * 2.2;
    this.nearest = null;
    const tmpM = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), p = new THREE.Vector3(), col = new THREE.Color(), up = new THREE.Vector3(0, 1, 0);
    this.selMarker.visible = false;
    this.builtThisFrame = false;
    // 최근 대화 (말풍선)
    const nowMin = state.now();
    const talkLine = new Map();
    for (let i = state.chat.length - 1; i >= 0 && i > state.chat.length - 80; i--) {
      const c = state.chat[i];
      if (nowMin - c.t > 90) break;
      if (!talkLine.has(c.a) && !talkLine.has(c.b)) talkLine.set(Math.min(c.a, c.b), c.line);
    }
    let bubbles = 0;
    for (const t of this.towns.values()) {
      const dist = camPos.distanceTo(t.center);
      const facing = t.up.dot(p.copy(camPos).sub(t.center)) > -50;
      t.g.visible = dist < 900 && facing;
      if (t.g.visible && !t.nature && dist < 600 && !this.builtThisFrame) { this.buildNature(t); this.builtThisFrame = true; }
      if (t.g.visible && (!this.nearest || dist < this.nearest.dist)) this.nearest = { t, dist };
      // 계절·눈이 바뀌면 나무 색과 눈 덮인 땅·지붕을 다시
      if (t.g.visible && t.nature) {
        const sea = this.seasonOf(t);
        const sk = `${sea.season}|${sea.month}|${Math.round(sea.snow * 10)}`;
        if (sk !== t.seasonKey) {
          t.seasonKey = sk;
          this.refreshTrees(t); this.colorRoadTrees(t); this.applySnow(t);
          const snowB = Math.round(Math.min(1, sea.snow) * 4) / 4, cold = sea.season === '겨울' || sea.temp < 6;
          if (t.snowB !== undefined && (snowB !== t.snowB || cold !== t.cold)) state.dirty.buildings.add(t.s.id);
        }
      }
      t.marker.visible = alt > 40 && facing;
      if (facing && alt < 9000 && dist < 9000) labels.push({ key: 'town' + t.s.id, pos: t.center.clone().addScaledVector(t.up, alt < 30 ? 0.12 : 0), cls: 'town lv' + t.s.level, sid: t.s.id, html: `<b>${t.s.name}</b><small>${t.s.levelName} · ${t.s.pop}명</small>`, pri: 1 + t.s.level * 0.01 + Math.min(0.009, t.s.pop / 1e5) });
      if (!t.g.visible) continue;
      // 앤들
      const list = [];
      for (const [id, ps] of state.pos) if (ps.sid === t.s.id && !ps.travel && ps.act !== 'dead') list.push([id, ps]);
      this.ensureAnnMeshes(t, list.length);
      const showFig = dist < 5;
      const showDots = dist < 200 && dist > 0.6;
      let n = 0, nh = 0;
      const driving = [], parked = [], parkSlot = new Map();
      const dpos = t.dots.geometry.attributes.position.array, dcol = t.dots.geometry.attributes.color.array;
      let dn = 0;
      t.ids.length = 0;
      for (const [id, ps] of list) {
        const a = state.anns.get(id);
        if (!a) continue;
        const ip = state.interp(ps);
        // 차를 모는 앤은 차로, 버스 안의 앤(승객·기사)은 버스로 보인다
        if (ps.mode === 1) driving.push({ id, x: ip.x, z: ip.z, h: ip.h ?? ps.heading ?? 0 });
        else if (a.cr && a.h && dist < 6) {
          const hb = state.buildings.get(a.h);
          if (hb) {
            const k = parkSlot.get(a.h) || 0; parkSlot.set(a.h, k + 1);
            // 집 옆에 나란히 (아파트처럼 많으면 지하 주차장 — 몇 대만 밖에)
            if (k < Math.max(1, Math.floor(hb.d / 5))) parked.push({ id, x: hb.x + hb.w / 2 + 2.4, z: hb.z - hb.d / 2 + 2.6 + k * 5, h: 0 });
          }
        }
        const hidden = ps.act === 'sleep' || ps.mode >= 1;
        const selected = this.selected === id;
        if (showDots && (!hidden || ps.mode === 1)) {
          col.set(state.playerColor(a.o));
          if (selected) col.set('#ffffff');
          dpos[dn * 3] = ip.x; dpos[dn * 3 + 1] = t.hAt(ip.x, ip.z) + 3; dpos[dn * 3 + 2] = ip.z;
          dcol[dn * 3] = col.r; dcol[dn * 3 + 1] = col.g; dcol[dn * 3 + 2] = col.b;
          dn++;
        }
        if (selected) {
          this.selMarker.visible = true;
          if (this.selMarker.parent !== t.g) t.g.add(this.selMarker);
          this.selMarker.position.set(ip.x, t.hAt(ip.x, ip.z) + 0.2, ip.z);
          const s = Math.max(1, Math.min(40, dist * 30));
          this.selMarker.children[0].scale.setScalar(Math.max(1, s * 0.15));
        }
        if (!showFig || hidden) continue;
        const age = state.ageOf(a);
        const ageS = age < 3 ? 0.42 : age < 13 ? 0.55 + age * 0.03 : age < 18 ? 0.92 : 1;
        const hs = (a.lk?.height || 168) / 170 * ageS;
        const walking = ps.moving;
        const bob = walking ? Math.abs(Math.sin(time * 9 + id)) * 0.06 : 0;
        q.setFromAxisAngle(up, ps.heading || 0);
        sc.set(hs, hs, hs);
        p.set(ip.x, t.hAt(ip.x, ip.z) + 0.1 + bob, ip.z);
        tmpM.compose(p, q, sc);
        t.anns.body.setMatrixAt(n, tmpM); t.anns.head.setMatrixAt(n, tmpM); t.anns.hair.setMatrixAt(n, tmpM);
        t.anns.body.setColorAt(n, col.set(state.playerColor(a.o)));
        t.anns.head.setColorAt(n, col.set(a.lk?.skin || '#e0b896'));
        t.anns.hair.setColorAt(n, col.set(a.lk?.hair || '#222'));
        // 근무 중인 경찰·포졸(모자), 소방관(빨간 헬멧), 현장 사람들(안전모)
        const onDuty = ps.act === 'work' || ps.act === 'build';
        const hatCol = onDuty && (HAT[a.jk] || (HARDHAT.has(a.jk) || ps.act === 'build' ? '#f2c94c' : null));
        if (hatCol) { t.anns.hat.setMatrixAt(nh, tmpM); t.anns.hat.setColorAt(nh, col.set(hatCol)); nh++; }
        t.ids[n] = id;
        n++;
        // 이름표
        if (dist < 1.6) {
          const wp = p.clone().setY(3.3).applyMatrix4(t.g.matrix);
          const emoji = this.meta.acts[ps.act]?.emoji || '';
          const named = selected || dist < 0.45;
          labels.push({ key: 'ann' + id, pos: wp, cls: 'ann' + (selected ? ' sel' : '') + (named ? '' : ' mini'), html: named ? `<span class="e">${emoji}</span>${a.n}` : `<span class="e">${emoji}</span>`, pri: selected ? 3 : 2, annId: id, d: camPos.distanceTo(wp) });
          const line = (ps.act === 'talk' || ps.act === 'date' || ps.act === 'family' || ps.act === 'play') && talkLine.get(Math.min(id, ps.target || id));
          if (line && dist < 0.9 && bubbles < 6 && id < (ps.target || Infinity)) {
            bubbles++;
            const bp = p.clone().setY(4.6).applyMatrix4(t.g.matrix);
            const short = line.length > 70 ? line.slice(0, 68) + '…' : line;
            labels.push({ key: 'bub' + id, pos: bp, cls: 'bubble', html: short.replace(/[<>&]/g, ''), pri: 4, d: camPos.distanceTo(bp) });
          }
        }
      }
      for (const m of Object.values(t.anns)) { m.count = m === t.anns.hat ? nh : n; m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; }
      // 차와 버스
      t.veh = t.veh || new TownVehicles(t.g);
      const buses = [];
      for (const [bid, b] of state.buses) if (b.sid === t.s.id && b.on) { const bp = state.interp(b); buses.push({ id: bid, x: bp.x, z: bp.z, h: bp.h ?? b.h, co: t.s.bus?.co }); if (dist < 1.2 && b.no) { const wp = new THREE.Vector3(bp.x, t.hAt(bp.x, bp.z) + 4.5, bp.z).applyMatrix4(t.g.matrix); labels.push({ key: 'bus' + bid, pos: wp, cls: 'ann mini', html: `🚌 ${b.no}호 · ${b.riders}명`, pri: 2, d: camPos.distanceTo(wp) }); } }
      t.veh.update(driving, parked, buses, t.hAt, dist < 6);
      // 신호등 불빛 (빨강 → 초록 → 노랑, 공사 마지막 시험 운영은 노란불 깜빡)
      if (t.roadProps?.lamps?.length && dist < 6) {
        const blinkOn = Math.floor(time * 2) % 2 === 0;
        for (const L of t.roadProps.lamps) {
          const ph = L.done ? signalPhase(L.off, L.axis, nowMin) : L.blink && blinkOn ? 'y' : null;
          for (const c of ['r', 'y', 'g']) L.mats[c].emissiveIntensity = ph === c ? 2.4 : 0.04;
        }
      }
      t.dots.geometry.setDrawRange(0, dn);
      t.dots.geometry.attributes.position.needsUpdate = true;
      t.dots.geometry.attributes.color.needsUpdate = true;
      t.dots.visible = showDots;
    }
    return labels;
  }

  /** 앤의 지구 좌표 (카메라 추적용) */
  /** 버스의 지구 좌표 (카메라로 따라가기) */
  busLL(state, id) {
    const b = state.buses.get(id);
    const s = b && state.settlements.get(b.sid);
    if (!s) return null;
    const ip = state.interp(b), ll = localToLL(s, ip.x, ip.z), t = this.towns.get(b.sid);
    return { lat: ll.lat, lon: ll.lon, h: (t ? t.base - R + t.hAt(ip.x, ip.z) / 1000 : 0) + 0.0015 };
  }

  annLL(state, id) {
    const ps = state.pos.get(id);
    if (!ps) return null;
    const s = state.settlements.get(ps.sid);
    if (!s) return null;
    const ip = state.interp(ps);
    const ll = localToLL(s, ip.x, ip.z);
    const t = this.towns.get(ps.sid);
    return { lat: ll.lat, lon: ll.lon, h: (t ? t.base - R + t.hAt(ip.x, ip.z) / 1000 : 0) + 0.0012 };
  }

  pick(raycaster) {
    // 앤 우선, 그 다음 건물
    let best = null;
    for (const t of this.towns.values()) {
      if (!t.g.visible || !t.anns) continue;
      const hits = raycaster.intersectObjects([t.anns.body, t.anns.head], false);
      if (hits.length && hits[0].instanceId != null) {
        const h = hits[0];
        if (!best || h.distance < best.distance) best = { distance: h.distance, annId: t.ids[h.instanceId] };
      }
    }
    if (best) return best;
    for (const t of this.towns.values()) {
      if (!t.g.visible) continue;
      const hits = raycaster.intersectObject(t.buildings, true);
      if (hits.length) {
        const h = hits[0];
        let o = h.object; while (o && o.userData.bid == null) o = o.parent;
        if (o && (!best || h.distance < best.distance)) best = { distance: h.distance, bid: o.userData.bid };
      }
    }
    return best;
  }

  /** 화면의 한 점에서 가장 가까운 앤 (점으로 보일 때 클릭용) */
  nearestAnnOnScreen(state, camera, x, y, w, h, maxPx = 14) {
    let best = null, bd = maxPx;
    const v = new THREE.Vector3();
    for (const t of this.towns.values()) {
      if (!t.g.visible || !t.dots?.visible && !(t.anns?.body.count)) continue;
      for (const [id, ps] of state.pos) {
        if (ps.sid !== t.s.id || ps.travel || ps.act === 'sleep') continue;
        const ip = state.interp(ps);
        v.set(ip.x, t.hAt(ip.x, ip.z) + 1, ip.z).applyMatrix4(t.g.matrix).project(camera);
        if (v.z > 1) continue;
        const sx = (v.x + 1) / 2 * w, sy = (1 - v.y) / 2 * h;
        const d = Math.hypot(sx - x, sy - y);
        if (d < bd) { bd = d; best = id; }
      }
    }
    return best;
  }
}

const SHARED = new Set([...Object.values(G), ...Object.values(BGEO)]);
function disposeGroup(g) {
  g.traverse(o => {
    if (o.geometry && (o.userData.ownGeo || !SHARED.has(o.geometry))) o.geometry.dispose();
    if (o.isSprite) o.material.dispose();
    else if (o.material && o.material.transparent && o.material.color && !o.material.map && o.material.opacity === 0.85) o.material.dispose(); // 불꽃
  });
}
