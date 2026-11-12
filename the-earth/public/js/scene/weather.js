// 날씨 연출: 가까이 내려가면 비·눈이 내리고, 흐린 날엔 하늘이 잿빛으로 낮아지고, 안개가 끼고, 폭우엔 번개가 친다.
// 계절: 나무 색(봄꽃·신록·단풍·앙상한 겨울나무), 쌓인 눈.
import * as THREE from 'three';
import { PHONE } from '../device.js';

export const WX = {
  맑음: { cloud: 0 }, 구름: { cloud: 0.55, fog: 0.05 }, 안개: { cloud: 0.5, fog: 1.1 },
  비: { cloud: 0.8, rain: 0.55, fog: 0.22 }, 소나기: { cloud: 0.75, rain: 0.7, fog: 0.18 }, 폭우: { cloud: 1, rain: 1, fog: 0.5, storm: true },
  태풍: { cloud: 1, rain: 1, fog: 0.6, storm: true, wind: 0.9 }, 눈: { cloud: 0.75, snow: 0.6, fog: 0.25 }, 폭설: { cloud: 0.95, snow: 1, fog: 0.6, wind: 0.3 },
  무더위: { cloud: 0, haze: 0.3 }, 한파: { cloud: 0.2 },
};

function dotTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 32;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(0.5, 'rgba(255,255,255,0.8)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(c);
}

export class Weather {
  constructor(scene) {
    this.scene = scene;
    this.NR = PHONE ? 2500 : 6000; this.NS = PHONE ? 2200 : 5000;
    const rg = new THREE.BufferGeometry();
    rg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(this.NR * 6), 3));
    this.rain = new THREE.LineSegments(rg, new THREE.LineBasicMaterial({ color: 0xb4c8dc, transparent: true, opacity: 0.5, depthWrite: false }));
    this.rain.frustumCulled = false; this.rain.visible = false;
    const sg = new THREE.BufferGeometry();
    sg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(this.NS * 3), 3));
    this.snow = new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xffffff, size: 3.2, sizeAttenuation: false, map: dotTexture(), transparent: true, opacity: 0.95, depthWrite: false }));
    this.snow.frustumCulled = false; this.snow.visible = false;
    this.seed = new Float32Array(Math.max(this.NR, this.NS) * 4).map(() => Math.random());
    // 흐린 하늘: 카메라를 감싸는 잿빛 돔 (멀리 있는 것을 가린다)
    this.dome = new THREE.Mesh(new THREE.SphereGeometry(16, 32, 16), new THREE.MeshBasicMaterial({ color: 0x8c949c, side: THREE.BackSide, transparent: true, opacity: 0, depthWrite: false, fog: false }));
    this.dome.renderOrder = 5; this.dome.frustumCulled = false;
    scene.add(this.dome);
    this.fog = new THREE.FogExp2(0x9aa3ad, 0);
    this.flash = 0; this.nextFlash = 3;
    this.parent = null;
    this.inv = new THREE.Matrix4(); this.v = new THREE.Vector3();
    this.box = null;
  }

  /** 매 프레임. town: 가장 가까운 정착지(cities 의 t), alt: 카메라 고도(km). 반환: 빛을 얼마나 낮출지 */
  update(dt, camera, town, alt, night) {
    const s = town?.s;
    const w = (s && WX[s.weather?.k]) || WX.맑음;
    // 지면 가까이에서만 (고도 0~6km에서 서서히)
    const near = town ? THREE.MathUtils.clamp((6 - alt) / 4, 0, 1) : 0;
    const cloud = (w.cloud || 0) * THREE.MathUtils.clamp((40 - alt) / 30, 0, 1);
    // 돔과 안개
    this.dome.position.copy(camera.position);
    const dayCol = new THREE.Color(w.storm ? 0x5d646c : 0x8f979f), nightCol = new THREE.Color(0x151a21);
    this.dome.material.color.copy(dayCol).lerp(nightCol, night);
    this.dome.material.opacity = cloud * near * (w.fog ? 0.55 + Math.min(0.4, w.fog * 0.4) : 0.45);
    this.dome.visible = this.dome.material.opacity > 0.01;
    const fogD = (w.fog || 0) * near * 0.9;
    if (fogD > 0.01) { this.fog.density = fogD; this.fog.color.copy(this.dome.material.color); this.scene.fog = this.fog; }
    else this.scene.fog = null;
    // 번개
    if (w.storm && near > 0.3) {
      this.nextFlash -= dt;
      if (this.nextFlash <= 0) { this.flash = 1; this.nextFlash = 3 + Math.random() * 9; }
    }
    this.flash = Math.max(0, this.flash - dt * 6);
    // 비·눈 입자: 정착지 좌표계(미터)에서 카메라 둘레 상자 안에 뿌린다
    const kind = w.rain ? 'rain' : w.snow ? 'snow' : null;
    const amt = (w.rain || w.snow || 0) * near;
    for (const o of [this.rain, this.snow]) { if (o.parent !== (town?.g || null)) { o.parent?.remove(o); town?.g?.add(o); } }
    this.rain.visible = kind === 'rain' && amt > 0.02;
    this.snow.visible = kind === 'snow' && amt > 0.02;
    if (!town || !(this.rain.visible || this.snow.visible)) return { dim: cloud * near * 0.5, flash: this.flash };
    this.inv.copy(town.g.matrixWorld).invert();
    const cam = this.v.copy(camera.position).applyMatrix4(this.inv);
    const ground = town.hAt(cam.x, cam.z);
    const hgt = Math.max(5, cam.y - ground);
    const B = THREE.MathUtils.clamp(hgt * 0.9, 35, 900);
    const wind = (w.wind || 0.12);
    if (kind === 'rain') {
      const n = Math.floor(this.NR * Math.min(1, amt)), pos = this.rain.geometry.attributes.position.array;
      const sp = Math.max(12, B * 0.9), len = sp * 0.045;
      if (!this.box || this.box.k !== 'rain' || Math.abs(this.box.B - B) > B * 0.3) this.reset('rain', cam, B, ground);
      const bx = this.box;
      bx.cx = cam.x; bx.cz = cam.z; bx.top = ground + B * 2; bx.bot = ground - 2;
      for (let i = 0; i < n; i++) {
        const o = i * 6;
        let x = pos[o], y = pos[o + 1] - sp * dt, z = pos[o + 2];
        x += sp * wind * 0.35 * dt;
        if (y < bx.bot || Math.abs(x - bx.cx) > B || Math.abs(z - bx.cz) > B) { x = bx.cx + (Math.random() * 2 - 1) * B; z = bx.cz + (Math.random() * 2 - 1) * B; y = bx.bot + Math.random() * (bx.top - bx.bot); }
        pos[o] = x; pos[o + 1] = y; pos[o + 2] = z;
        pos[o + 3] = x - sp * wind * 0.35 * 0.045; pos[o + 4] = y + len; pos[o + 5] = z;
      }
      this.rain.geometry.setDrawRange(0, n * 2);
      this.rain.geometry.attributes.position.needsUpdate = true;
      this.rain.material.color.setHex(night > 0.5 ? 0x6d7f93 : 0xb4c8dc);
    } else {
      const n = Math.floor(this.NS * Math.min(1, amt)), pos = this.snow.geometry.attributes.position.array, sd = this.seed;
      const sp = Math.max(1.6, B * 0.07);
      if (!this.box || this.box.k !== 'snow' || Math.abs(this.box.B - B) > B * 0.3) this.reset('snow', cam, B, ground);
      const bx = this.box;
      bx.cx = cam.x; bx.cz = cam.z; bx.top = ground + B * 2; bx.bot = ground - 2;
      const t = performance.now() / 1000;
      for (let i = 0; i < n; i++) {
        const o = i * 3;
        let x = pos[o] + (Math.sin(t * (0.6 + sd[i]) + i) * 0.6 + wind * 2) * sp * 0.25 * dt, y = pos[o + 1] - sp * (0.7 + sd[i] * 0.6) * dt, z = pos[o + 2] + Math.cos(t * 0.7 + i * 1.3) * sp * 0.12 * dt;
        if (y < bx.bot || Math.abs(x - bx.cx) > B || Math.abs(z - bx.cz) > B) { x = bx.cx + (Math.random() * 2 - 1) * B; z = bx.cz + (Math.random() * 2 - 1) * B; y = bx.bot + Math.random() * (bx.top - bx.bot); }
        pos[o] = x; pos[o + 1] = y; pos[o + 2] = z;
      }
      this.snow.geometry.setDrawRange(0, n);
      this.snow.geometry.attributes.position.needsUpdate = true;
      this.snow.material.size = THREE.MathUtils.clamp(900 / B, 1.6, 4.5);
    }
    return { dim: cloud * near * 0.5, flash: this.flash };
  }

  reset(k, cam, B, ground) {
    this.box = { k, B, cx: cam.x, cz: cam.z };
    const arr = (k === 'rain' ? this.rain : this.snow).geometry.attributes.position.array;
    const stride = k === 'rain' ? 6 : 3, n = arr.length / stride;
    for (let i = 0; i < n; i++) {
      const o = i * stride;
      arr[o] = cam.x + (Math.random() * 2 - 1) * B; arr[o + 1] = ground + Math.random() * B * 2; arr[o + 2] = cam.z + (Math.random() * 2 - 1) * B;
      if (stride === 6) { arr[o + 3] = arr[o]; arr[o + 4] = arr[o + 1] + 1; arr[o + 5] = arr[o + 2]; }
    }
  }
}

// ── 계절 ──
/** 나무 한 그루의 색. kind 1 = 상록 침엽수, 0 = 활엽수 섞인 숲. r = 0~1 개체 난수 */
export function treeColor(out, season, month, snow, kind, r, biome) {
  if (biome === 'tropical') return out.setHSL(0.3 + r * 0.05, 0.5, 0.22 + r * 0.05);
  const deciduous = kind === 0 && r < 0.6;
  if (!deciduous) {
    out.setHSL(0.33 + r * 0.04, 0.42, kind ? 0.17 : 0.2 + r * 0.04);
  } else if (season === '봄') {
    if (month <= 4 && r < 0.18) out.setHSL(0.93 + r * 0.2, 0.55, 0.82); // 벚꽃
    else out.setHSL(0.26 + r * 0.04, 0.5, 0.36 + r * 0.06); // 연두
  } else if (season === '여름') out.setHSL(0.3 + r * 0.04, 0.5, 0.22 + r * 0.05);
  else if (season === '가을') {
    const late = month === 10 || month === 11 || month === 4 || month === 5 ? 1 : 0.5;
    const hue = r < 0.3 ? 0.02 : r < 0.6 ? 0.07 : 0.12; // 빨강·주황·노랑
    out.setHSL(late > 0.6 ? hue : 0.18 + r * 0.05, 0.7, 0.4);
  } else out.setHSL(0.07, 0.15, 0.3 + r * 0.06); // 겨울: 앙상한 가지
  if (snow > 0.05) out.lerp(new THREE.Color(0xf2f5f8), Math.min(0.75, snow * (deciduous ? 0.5 : 0.75)));
  return out;
}
export function seasonOfMonth(month, lat) {
  const m = lat < 0 ? ((month + 5) % 12) + 1 : month;
  return m >= 3 && m <= 5 ? '봄' : m >= 6 && m <= 8 ? '여름' : m >= 9 && m <= 11 ? '가을' : '겨울';
}
