// 3D 위에 띄우는 HTML 라벨 (정착지 이름, 앤 이름표, 말풍선, 행성 이름)
import * as THREE from 'three';
import { R } from './geo.js';
import { PHONE } from '../device.js';
const MAX_ANN = PHONE ? 22 : 60, MAX_TOWN = PHONE ? 30 : 80;

export class Labels {
  constructor(root, onTownClick) {
    this.root = root;
    this.pool = new Map();
    this.onTownClick = onTownClick;
    this.v = new THREE.Vector3();
  }
  occluded(cam, pos) {
    // 카메라→점 선분이 지구에 가리는가
    const o = cam.position, d = this.v.copy(pos).sub(o);
    const len = d.length(); d.divideScalar(len);
    const b = o.dot(d), c = o.lengthSq() - (R * 0.998) ** 2;
    const disc = b * b - c;
    if (disc < 0) return false;
    const t = -b - Math.sqrt(disc);
    return t > 0 && t < len - 0.01;
  }
  update(reqs, cam, w, h) {
    const used = new Set();
    reqs.sort((a, b) => (b.pri || 0) - (a.pri || 0) || (a.d || 0) - (b.d || 0));
    let nAnn = 0, nTown = 0;
    for (const r of reqs) {
      if (r.cls.startsWith('ann') && ++nAnn > MAX_ANN) continue;
      if (r.cls.startsWith('town') && ++nTown > MAX_TOWN) continue;
      if (this.occluded(cam, r.pos)) continue;
      const p = this.v.copy(r.pos).project(cam);
      if (p.z > 1 || p.x < -1.1 || p.x > 1.1 || p.y < -1.1 || p.y > 1.1) continue;
      let el = this.pool.get(r.key);
      if (!el) {
        el = document.createElement('div');
        if (r.sid != null) el.addEventListener('click', () => this.onTownClick?.(r.sid));
        this.root.appendChild(el);
        this.pool.set(r.key, el);
      }
      if (el._html !== r.html) { el.innerHTML = r.html; el._html = r.html; }
      const cls = 'lbl ' + r.cls;
      if (el.className !== cls) el.className = cls;
      el.style.transform = `translate(${((p.x + 1) / 2 * w).toFixed(1)}px, ${((1 - p.y) / 2 * h).toFixed(1)}px) translate(-50%, -100%)`;
      el.style.display = '';
      used.add(r.key);
    }
    for (const [k, el] of this.pool) if (!used.has(k)) {
      if (this.pool.size > 300) { el.remove(); this.pool.delete(k); } else el.style.display = 'none';
    }
  }
  clear() { for (const el of this.pool.values()) el.remove(); this.pool.clear(); }
}
