// 자동차와 버스: 달리는 차(앤이 운전 중), 집 앞에 세워 둔 차, 노선을 도는 버스.
import * as THREE from 'three';

/** 상자 여러 개를 한 덩어리로 (부분마다 색: 흰색 부분만 차마다 다른 색이 입혀진다) */
function boxes(parts) {
  const pos = [], nor = [], col = [];
  const c = new THREE.Color();
  for (const [w, h, d, x, y, z, hex] of parts) {
    const g = new THREE.BoxGeometry(w, h, d).translate(x, y, z).toNonIndexed();
    pos.push(...g.attributes.position.array); nor.push(...g.attributes.normal.array);
    c.set(hex);
    for (let i = 0; i < g.attributes.position.count; i++) col.push(c.r, c.g, c.b);
    g.dispose();
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  out.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  out.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  return out;
}
const W = '#ffffff', GLASS = '#1d2a35', TIRE = '#151515', LIGHT = '#fff3c4', TAIL = '#b3261e';
// 승용차 (길이 4.4m): 차체, 유리 지붕, 바퀴, 전조등·후미등
const CAR = boxes([
  [1.8, 0.7, 4.4, 0, 0.62, 0, W],
  [1.62, 0.62, 2.3, 0, 1.28, -0.25, GLASS],
  [1.5, 0.08, 2.0, 0, 1.62, -0.25, W],
  ...[[-0.82, 1.4], [0.82, 1.4], [-0.82, -1.4], [0.82, -1.4]].map(([x, z]) => [0.26, 0.62, 0.62, x, 0.31, z, TIRE]),
  [1.5, 0.16, 0.05, 0, 0.72, 2.21, LIGHT],
  [1.5, 0.14, 0.05, 0, 0.78, -2.21, TAIL],
]);
// 시내버스 (길이 11m): 차체, 띠 창문, 앞유리, 지붕 냉방기, 바퀴
const BUS = boxes([
  [2.5, 2.5, 11, 0, 1.65, 0, W],
  [2.54, 0.95, 9.4, 0, 2.15, -0.4, GLASS],
  [2.3, 1.3, 0.06, 0, 2.0, 5.5, GLASS],
  [1.7, 0.3, 3.2, 0, 3.05, -1, '#d9dde0'],
  [2.2, 0.22, 0.06, 0, 0.75, 5.52, LIGHT],
  ...[[-1.1, 3.6], [1.1, 3.6], [-1.1, -3.4], [1.1, -3.4]].map(([x, z]) => [0.32, 0.9, 0.9, x, 0.45, z, TIRE]),
]);
// 한국 자동차 색 비율 (흰색·회색·검정이 대부분)
const CAR_COLS = ['#f2f2f0', '#f2f2f0', '#f2f2f0', '#c9cdd1', '#c9cdd1', '#7d8389', '#2a2d31', '#2a2d31', '#1f3a5f', '#8f1d21', '#d8cfbf', '#3d5a40'];
const BUS_COLS = ['#2f7dd1', '#1f9d55', '#e3a521', '#d64545'];
const hash = n => { n = (n ^ 61) ^ (n >>> 16); n = n + (n << 3); n ^= n >>> 4; n = Math.imul(n, 0x27d4eb2d); return ((n ^ (n >>> 15)) >>> 0) / 4294967296; };

export class TownVehicles {
  constructor(group) {
    this.g = group;
    this.cap = 0;
    this.mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.45, metalness: 0.25 });
    this.busMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.5, metalness: 0.2 });
    this.busCap = 0;
  }
  ensure(nCars, nBuses) {
    if (nCars > this.cap) {
      if (this.cars) { this.g.remove(this.cars); this.cars.dispose(); }
      this.cap = Math.max(16, 2 ** Math.ceil(Math.log2(nCars + 1)));
      this.cars = new THREE.InstancedMesh(CAR, this.mat, this.cap);
      this.cars.frustumCulled = false; this.cars.count = 0;
      this.g.add(this.cars);
    }
    if (nBuses > this.busCap) {
      if (this.buses) { this.g.remove(this.buses); this.buses.dispose(); }
      this.busCap = Math.max(4, 2 ** Math.ceil(Math.log2(nBuses + 1)));
      this.buses = new THREE.InstancedMesh(BUS, this.busMat, this.busCap);
      this.buses.frustumCulled = false; this.buses.count = 0;
      this.g.add(this.buses);
    }
  }
  /**
   * driving: [{id, x, z, h}], parked: [{id, x, z, h}], buses: [{id, x, z, h, co}]
   * hAt: 땅 높이, show: 가까울 때만
   */
  update(driving, parked, buses, hAt, show) {
    const nC = show ? driving.length + parked.length : 0, nB = show ? buses.length : 0;
    this.ensure(nC, nB);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3(), s = new THREE.Vector3(1, 1, 1), up = new THREE.Vector3(0, 1, 0), col = new THREE.Color();
    let i = 0;
    if (show) for (const list of [driving, parked]) for (const v of list) {
      q.setFromAxisAngle(up, v.h || 0);
      // 우측통행: 달리는 차는 길 가운데에서 오른쪽 차선으로
      const lane = list === driving ? 2.2 : 0;
      p.set(v.x - Math.cos(v.h || 0) * lane, hAt(v.x, v.z) + 0.02, v.z + Math.sin(v.h || 0) * lane);
      m.compose(p, q, s);
      this.cars.setMatrixAt(i, m);
      this.cars.setColorAt(i, col.set(CAR_COLS[Math.floor(hash(v.id) * CAR_COLS.length)]));
      i++;
    }
    if (this.cars) { this.cars.count = i; this.cars.instanceMatrix.needsUpdate = true; if (this.cars.instanceColor) this.cars.instanceColor.needsUpdate = true; }
    let j = 0;
    if (show) for (const b of buses) {
      q.setFromAxisAngle(up, b.h || 0);
      p.set(b.x - Math.cos(b.h || 0) * 2.4, hAt(b.x, b.z) + 0.02, b.z + Math.sin(b.h || 0) * 2.4);
      m.compose(p, q, s);
      this.buses.setMatrixAt(j, m);
      this.buses.setColorAt(j, col.set(BUS_COLS[Math.floor(hash(b.co || 1) * BUS_COLS.length)]));
      j++;
    }
    if (this.buses) { this.buses.count = j; this.buses.instanceMatrix.needsUpdate = true; if (this.buses.instanceColor) this.buses.instanceColor.needsUpdate = true; }
  }
}
