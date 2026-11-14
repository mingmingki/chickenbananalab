// 국경선과 고른 나라·내 나라 테두리, 그리고 강을 그림(텍스처)이 아니라 '선'으로 그린다.
// 그림은 확대하면 계단처럼 깨지지만, 선은 아무리 가까이 가도 또렷하다.
// 강은 실제 너비(km)로 그려 가까이 갈수록 넓어지고, 멀리선 가는 실선으로 남는다. 밤에는 어두워진다.
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { R, llToVec } from './geo.js';

const LIFT = 1.0005;     // 땅 위로 살짝 (약 3km) 띄워 지면과 겹쳐 깜빡이지 않게
const STEP = 0.6;        // 긴 변은 0.6도마다 나눠 지구 곡면을 따라가게

/** [경도, 위도, 경도, 위도, …] 고리들 → 선분 좌표 (x1,y1,z1,x2,y2,z2, …) */
function segments(rings, close, radius = R * LIFT) {
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
        llToVec(pt, pl, radius, a); llToVec(la, l, radius, b);
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

/** hydro.json 의 차이 부호화 좌표 → [경도, 위도, …] */
export function decodeLine(arr, start, q) {
  const out = [];
  let x = 0, y = 0;
  for (let i = start; i + 1 < arr.length; i += 2) {
    x += arr[i]; y += arr[i + 1];
    out.push(x / q, y / q);
  }
  return out;
}

// 강의 크기 등급 (Natural Earth scalerank) → 실제 너비(km), 보이기 시작하는 높이(km), 멀리서도 지키는 최소 굵기(px)
const RIVER_CLASS = [
  { max: 2, km: 1.1, alt: 9000, minPx: 1.6 },   // 아마존·나일·양쯔강 같은 큰 강
  { max: 4, km: 0.6, alt: 4500, minPx: 1.25 },
  { max: 6, km: 0.3, alt: 1800, minPx: 1.0 },
  { max: 99, km: 0.16, alt: 800, minPx: 0.9 },
];
const RIVER_LIFT = 1.00004;   // 땅 위 약 250m (지구 공의 다각형 면이 처지는 만큼보다 조금 높게)

/** 밤에는 어두워지게: 선 셰이더에 해 방향을 넣는다 */
function nightDim(mat, sunDir) {
  mat.onBeforeCompile = sh => {
    sh.uniforms.sunDirW = sunDir;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nuniform vec3 sunDirW; varying float vDayK;')
      .replace('#include <logdepthbuf_vertex>', `#include <logdepthbuf_vertex>
        vec3 dayP = (modelMatrix * vec4(position.y < 0.5 ? instanceStart : instanceEnd, 1.0)).xyz - (modelMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
        vDayK = smoothstep(-0.12, 0.22, dot(normalize(dayP), sunDirW));`);
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vDayK;')
      .replace('gl_FragColor = vec4( diffuseColor.rgb, alpha );', 'gl_FragColor = vec4( diffuseColor.rgb * mix(0.06, 1.0, vDayK), alpha );');
  };
  mat.customProgramCacheKey = () => 'river-night';
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
    for (const m of [this.borders, this.sel, this.mine, ...(this.rivers || [])]) if (m) m.material.resolution.copy(this.size);
  }
  /** 강 자료(hydro.json)를 받으면 크기 등급별로 선을 만든다. sunDir 는 지구 셰이더와 같은 해 방향 */
  setRivers(hydro, sunDir) {
    if (!hydro?.rivers || this.rivers) return;
    const groups = RIVER_CLASS.map(() => []);
    for (const r of hydro.rivers) {
      const ci = RIVER_CLASS.findIndex(c => r[0] <= c.max);
      groups[ci].push(decodeLine(r, 1, hydro.q));
    }
    this.rivers = groups.map((lines, i) => {
      const geo = new LineSegmentsGeometry();
      geo.setPositions(segments(lines, false, R * RIVER_LIFT));
      const mat = new LineMaterial({ color: 0x3f7fa8, linewidth: RIVER_CLASS[i].km, transparent: true, opacity: 0, depthWrite: false, worldUnits: true });
      nightDim(mat, sunDir);
      mat.resolution.copy(this.size);
      const m = new LineSegments2(geo, mat);
      m.renderOrder = 2; m.frustumCulled = false; m.visible = false;
      this.parent.add(m);
      return m;
    });
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
  /** 매 프레임: 높이와 화면에 따라 선의 진하기 (마을 가까이 내려가면 사라진다). kmPerPx = 화면 가운데 한 점이 덮는 땅 (km) */
  update(alt, mode, kmPerPx = alt / 800) {
    const k = THREE.MathUtils.clamp;
    if (this.rivers) this.rivers.forEach((m, i) => {
      const c = RIVER_CLASS[i];
      // 높이 올라가면 작은 강부터 사라지고, 마을 높이(수십 km 아래)에선 마을의 강에 자리를 내준다
      const o = k((c.alt - alt) / (c.alt * 0.35), 0, 1) * k((alt - 12) / 30, 0, 1) * 0.92;
      m.visible = o > 0.01;
      if (!m.visible) return;
      m.material.opacity = o;
      m.material.linewidth = Math.max(c.km, c.minPx * kmPerPx);
    });
    const picking = mode === 'pick-country';
    this.borders.material.opacity = picking ? k((alt - 40) / 400, 0, 1) * 0.7 : k((alt - 250) / 1500, 0, 1) * 0.4;
    this.borders.visible = this.borders.material.opacity > 0.01;
    if (this.sel) { this.sel.material.opacity = k((alt - 20) / 200, 0, 1) * 0.95; this.sel.visible = this.sel.material.opacity > 0.01; }
    if (this.mine) { this.mine.material.opacity = k((alt - 30) / 300, 0, 1) * (picking ? 0.5 : 0.75); this.mine.visible = this.mine.material.opacity > 0.01; }
  }
}
