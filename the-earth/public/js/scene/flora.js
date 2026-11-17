// 나무 종류: 바늘잎나무(소나무·전나무), 넓은잎나무(참나무·느티나무), 자작나무, 미루나무, 야자수, 덤불.
// 기후(온대·열대·사막·눈)와 높이에 따라 섞어 심는다. 넓은잎나무는 계절 따라 연두·초록·단풍·앙상한 가지, 바늘잎은 사철 푸르다.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { treeColor } from './weather.js';

const clean = g => { g.deleteAttribute('uv'); return g.index ? g.toNonIndexed() : g; };
const merge = gs => { const g = mergeGeometries(gs.map(clean)); g.computeVertexNormals(); return g; };
const ico = (r, x, y, z, sx = 1, sy = 1, sz = 1, d = 1) => new THREE.IcosahedronGeometry(r, d).scale(sx, sy, sz).translate(x, y, z);

/** 종마다 [수관, 줄기] 기하 (밑동이 0) */
function speciesGeo() {
  return {
    pine: [merge([new THREE.ConeGeometry(3.4, 4.2, 8).translate(0, 4.6, 0), new THREE.ConeGeometry(2.7, 3.6, 8).translate(0, 6.9, 0), new THREE.ConeGeometry(1.9, 3.2, 8).translate(0, 9, 0)]),
      new THREE.CylinderGeometry(0.32, 0.5, 4, 6).translate(0, 2, 0)],
    broad: [merge([ico(3.1, 0, 7.2, 0), ico(2.4, 1.7, 6.1, 0.8), ico(2.3, -1.5, 6.4, -0.9), ico(2.0, 0.3, 5.6, -1.6)]),
      new THREE.CylinderGeometry(0.38, 0.62, 5.6, 6).translate(0, 2.8, 0)],
    birch: [merge([ico(1.7, 0, 8.4, 0, 1, 2.1, 1), ico(1.2, 0.6, 6.6, 0.3, 1, 1.4, 1)]),
      new THREE.CylinderGeometry(0.2, 0.28, 8, 6).translate(0, 4, 0)],
    poplar: [ico(1.5, 0, 7.6, 0, 1, 3.2, 1),
      new THREE.CylinderGeometry(0.22, 0.34, 4, 6).translate(0, 2, 0)],
    palm: [merge(Array.from({ length: 7 }, (_, k) => {
      const a = k / 7 * Math.PI * 2;
      return new THREE.BoxGeometry(0.9, 0.12, 4.2).translate(0, 0, 2.1).rotateX(0.55).rotateY(a).translate(0, 9.2, 0);
    }).concat([ico(0.6, 0, 9.1, 0)])),
      new THREE.CylinderGeometry(0.26, 0.38, 9.3, 6).translate(0, 4.65, 0)],
    bush: [merge([ico(1.6, 0, 1.1, 0, 1.2, 0.8, 1.1), ico(1.1, 0.9, 0.9, 0.4)]), null],
  };
}
const TRUNK_COL = { pine: 0x5b4330, broad: 0x4f3b2a, birch: 0xe6e1d6, poplar: 0x6a5a48, palm: 0x8a7350 };
const EVERGREEN = new Set(['pine', 'palm']);

/** 이 자리에 설 나무 종류 */
export function pickSpecies(biome, kind, r, lat) {
  if (biome === 'tropical') return r < 0.38 ? 'palm' : r < 0.9 ? 'broad' : 'bush';
  if (biome === 'desert') return r < 0.25 ? 'palm' : r < 0.8 ? 'bush' : 'broad';
  if (biome === 'snow') return r < 0.75 ? 'pine' : 'birch';
  if (kind === 1) return r < 0.85 ? 'pine' : 'birch';      // 높은 산
  const north = Math.abs(lat) > 48;
  if (r < (north ? 0.42 : 0.3)) return 'pine';
  if (r < 0.74) return 'broad';
  if (r < (north ? 0.9 : 0.83)) return 'birch';
  if (r < 0.93) return 'poplar';
  return 'bush';
}

export class TreeSet {
  constructor(g, n) {
    this.g = g;
    this.geo = speciesGeo();
    this.crowns = {}; this.trunks = {};
    const cm = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.9, flatShading: true });
    for (const [k, [c, tr]] of Object.entries(this.geo)) {
      this.crowns[k] = new THREE.InstancedMesh(c, cm, n);
      this.crowns[k].frustumCulled = false; this.crowns[k].count = 0;
      g.add(this.crowns[k]);
      if (tr) {
        this.trunks[k] = new THREE.InstancedMesh(tr, new THREE.MeshStandardMaterial({ color: TRUNK_COL[k], roughness: 1 }), n);
        this.trunks[k].frustumCulled = false; this.trunks[k].count = 0;
        g.add(this.trunks[k]);
      }
    }
  }
  /** list: [[x, y, z, 크기, kind, 종]] (휘기 전 자리) · P: 휘기 · sea: 계절 */
  fill(list, P, sea, biome) {
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), p = new THREE.Vector3(), col = new THREE.Color(), up = new THREE.Vector3(0, 1, 0);
    const cnt = {};
    for (const k in this.crowns) cnt[k] = 0;
    const shade = [];
    for (const [x, h, z, s, kind, sp] of list) {
      const r = ((s * 7919.37) % 1 + 1) % 1;
      const ever = EVERGREEN.has(sp) || (sp === 'bush' && r > 0.5);
      const bare = sea.season === '겨울' && !ever && biome !== 'tropical';
      const k = ever ? 1 : 0, rr = ever ? r : r * 0.59;
      const hs = sp === 'bush' ? s * 0.9 : s * (kind ? 1.18 : 1);
      sc.set(s * (bare && sp !== 'bush' ? 0.72 : 1), hs, s * (bare && sp !== 'bush' ? 0.72 : 1));
      const [wx, wz] = P(x, z);
      p.set(wx, h - 0.3, wz);
      q.setFromAxisAngle(up, r * 6.28);
      m.compose(p, q, sc);
      const i = cnt[sp]++;
      this.crowns[sp].setMatrixAt(i, m);
      this.crowns[sp].setColorAt(i, treeColor(col, sea.season, sea.month, sea.snow, k, rr, biome === 'tropical' && sp !== 'palm' ? 'tropical' : biome === 'tropical' ? 'tropical' : biome));
      if (this.trunks[sp]) this.trunks[sp].setMatrixAt(i, m);
      shade.push([x, z, sc.x * (sp === 'bush' ? 0.5 : sp === 'birch' || sp === 'poplar' ? 0.6 : 1), hs * (sp === 'bush' ? 0.25 : 1)]);
    }
    for (const k in this.crowns) {
      const c = this.crowns[k]; c.count = cnt[k]; c.instanceMatrix.needsUpdate = true; if (c.instanceColor) c.instanceColor.needsUpdate = true;
      if (this.trunks[k]) { this.trunks[k].count = cnt[k]; this.trunks[k].instanceMatrix.needsUpdate = true; }
    }
    return shade;
  }
  set visible(v) { for (const k in this.crowns) { this.crowns[k].visible = v; if (this.trunks[k]) this.trunks[k].visible = v; } }
  dispose() {
    for (const k in this.crowns) {
      for (const o of [this.crowns[k], this.trunks[k]]) if (o) { this.g.remove(o); o.geometry.dispose(); o.material.dispose(); o.dispose(); }
    }
  }
}
