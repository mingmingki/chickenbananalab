// 국경선과 고른 나라·내 나라 테두리를 그림(텍스처)이 아니라 '선'으로 그린다.
// 그림은 확대하면 계단처럼 깨지지만, 선은 아무리 가까이 가도 또렷하다.
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { R, llToVec } from './geo.js';

const LIFT = 1.0005;     // 땅 위로 살짝 (약 3km) 띄워 지면과 겹쳐 깜빡이지 않게
const STEP = 0.6;        // 긴 변은 0.6도마다 나눠 지구 곡면을 따라가게

/** [경도, 위도, 경도, 위도, …] 고리들 → 선분 좌표 (x1,y1,z1,x2,y2,z2, …) */
function segments(rings, close) {
  const out = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  for (const flat of rings) {
    const n = flat.length / 2;
    if (n < 2) continue;
    const same = flat[0] === flat[flat.length - 2] && flat[1] === flat[flat.length - 1];
    const m = close && !same ? n : n - 1;
    for (let k = 0; k < m; k++) {
      const j = (k + 1) % n;
      const lon1 = flat[2 * k], lat1 = flat[2 * k + 1];
      let lon2 = flat[2 * j];
      const lat2 = flat[2 * j + 1];
      if (lon2 - lon1 > 180) lon2 -= 360; else if (lon1 - lon2 > 180) lon2 += 360;
      const steps = Math.max(1, Math.ceil(Math.max(Math.abs(lon2 - lon1), Math.abs(lat2 - lat1)) / STEP));
      let pl = lon1, pt = lat1;
      for (let s = 1; s <= steps; s++) {
        const t = s / steps, l = lon1 + (lon2 - lon1) * t, la = lat1 + (lat2 - lat1) * t;
        llToVec(pt, pl, R * LIFT, a); llToVec(la, l, R * LIFT, b);
        out.push(a.x, a.y, a.z, b.x, b.y, b.z);
        pl = l; pt = la;
      }
    }
  }
  return out;
}

function lineMesh(pos, color, width) {
  const geo = new LineSegmentsGeometry();
  geo.setPositions(pos);
  const mat = new LineMaterial({ color, linewidth: width, transparent: true, opacity: 0, depthWrite: false, worldUnits: false });
  const mesh = new LineSegments2(geo, mat);
  mesh.renderOrder = 3;
  mesh.frustumCulled = false;
  return mesh;
}

export class EarthVectors {
  constructor(parent, countries, borders, renderer) {
    this.parent = parent;
    this.countries = countries;
    this.renderer = renderer;
    this.size = new THREE.Vector2();
    // 나라와 나라 사이 육지 국경 (해안선은 사진에 보인다). 서버가 국경을 안 주면 나라 외곽선
    const pos = borders?.length ? segments(borders, false) : segments(countries.flatMap(c => c.rings), true);
    this.borders = lineMesh(pos, 0xffffff, 1.1);
    parent.add(this.borders);
    this.sel = null; this.mine = null;
    this.selIdx = -1; this.mineIdx = -1;
    this.resize();
  }
  resize() {
    this.renderer.getDrawingBufferSize(this.size);
    for (const m of [this.borders, this.sel, this.mine]) if (m) m.material.resolution.copy(this.size);
  }
  outline(idx, color, width) {
    const c = this.countries[idx];
    if (!c) return null;
    const m = lineMesh(segments(c.rings, true), color, width);
    m.material.resolution.copy(this.size);
    this.parent.add(m);
    return m;
  }
  drop(m) { if (m) { this.parent.remove(m); m.geometry.dispose(); m.material.dispose(); } }
  setSelected(idx) {
    if (idx === this.selIdx) return;
    this.selIdx = idx ?? -1;
    this.drop(this.sel);
    this.sel = this.selIdx >= 0 ? this.outline(this.selIdx, 0xffd36b, 2.6) : null;
  }
  setMine(idx) {
    if (idx === this.mineIdx) return;
    this.mineIdx = idx ?? -1;
    this.drop(this.mine);
    this.mine = this.mineIdx >= 0 ? this.outline(this.mineIdx, 0x63d2ff, 1.8) : null;
  }
  /** 매 프레임: 높이와 화면에 따라 선의 진하기 (마을 가까이 내려가면 사라진다) */
  update(alt, mode) {
    const k = THREE.MathUtils.clamp;
    const picking = mode === 'pick-country';
    this.borders.material.opacity = picking ? k((alt - 40) / 400, 0, 1) * 0.7 : k((alt - 250) / 1500, 0, 1) * 0.4;
    this.borders.visible = this.borders.material.opacity > 0.01;
    if (this.sel) { this.sel.material.opacity = k((alt - 20) / 200, 0, 1) * 0.95; this.sel.visible = this.sel.material.opacity > 0.01; }
    if (this.mine) { this.mine.material.opacity = k((alt - 30) / 300, 0, 1) * (picking ? 0.5 : 0.75); this.mine.visible = this.mine.material.opacity > 0.01; }
  }
}
