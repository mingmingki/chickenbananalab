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
    // 말풍선을 누르면 구름처럼 흩어져 사라진다 (같은 말은 다시 뜨지 않는다)
    this.dismissed = new Map();
    root.addEventListener('click', e => {
      const b = e.target.closest('.lbl.bubble');
      if (b && !b._puffing) this.puff(b);
    });
  }
  puff(b) {
    b._puffing = true;
    this.dismissed.set(b._key, b._html);
    if (this.dismissed.size > 200) this.dismissed.delete(this.dismissed.keys().next().value);
    for (let i = 0; i < 7; i++) {
      const d = document.createElement('i');
      d.className = 'puffdot';
      const a = (i / 7) * Math.PI * 2 + Math.random() * 0.6;
      d.style.setProperty('--dx', `${Math.cos(a) * (40 + Math.random() * 30)}px`);
      d.style.setProperty('--dy', `${Math.sin(a) * (22 + Math.random() * 18) - 10}px`);
      d.style.setProperty('--s', `${0.7 + Math.random() * 0.8}`);
      d.style.left = `${20 + Math.random() * 60}%`; d.style.top = `${30 + Math.random() * 40}%`;
      b.appendChild(d);
    }
    b.classList.add('puff');
    setTimeout(() => { b.style.display = 'none'; b.classList.remove('puff'); b.querySelectorAll('.puffdot').forEach(x => x.remove()); b._puffing = false; }, 720);
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
    const boxes = [], bubs = [];
    for (const r of reqs) {
      const bubble = r.cls.includes('bubble');
      if (bubble && this.dismissed.get(r.key) === r.html) continue;
      if (r.cls.startsWith('ann') && ++nAnn > MAX_ANN) continue;
      if (r.cls.startsWith('town') && ++nTown > MAX_TOWN) continue;
      if (this.occluded(cam, r.pos)) continue;
      const p = this.v.copy(r.pos).project(cam);
      if (p.z > 1 || p.x < -1.1 || p.x > 1.1 || p.y < -1.1 || p.y > 1.1) continue;
      // 지명은 마을·앤 이름표와 겹치면 양보한다
      const sx = (p.x + 1) / 2 * w, sy = (1 - p.y) / 2 * h;
      // 마을 이름끼리 겹치면 큰 마을이 먼저 (멀리서 볼 때 글자가 뭉치지 않게)
      if (r.cls.startsWith('geo') || r.cls.startsWith('town')) { if (boxes.some(([bx, by]) => Math.abs(bx - sx) < (r.cls.startsWith('town') ? 64 : 76) && Math.abs(by - sy) < 40)) continue; }
      // 말풍선끼리 겹치면 덜 중요한 쪽은 다음에
      if (bubble) { if (bubs.some(([bx, by]) => Math.abs(bx - sx) < 180 && Math.abs(by - sy) < 52)) continue; bubs.push([sx, sy]); }
      boxes.push([sx, sy]);
      let el = this.pool.get(r.key);
      if (el?._puffing) { used.add(r.key); continue; }
      if (!el) {
        el = document.createElement('div');
        if (r.sid != null) el.addEventListener('click', () => this.onTownClick?.(r.sid));
        this.root.appendChild(el);
        this.pool.set(r.key, el);
      }
      el._annId = r.annId ?? null;
      if (el._html !== r.html) { el.innerHTML = bubble ? `<span class="bt">${r.html}</span>` : r.html; el._html = r.html; el._key = r.key; if (bubble) { el.classList.remove('pop'); void el.offsetWidth; } }
      const cls = 'lbl ' + r.cls + (bubble ? ' pop' : '');
      if (el.className !== cls) el.className = cls;
      el.style.transform = `translate(${((p.x + 1) / 2 * w).toFixed(1)}px, ${((1 - p.y) / 2 * h).toFixed(1)}px) translate(-50%, -100%)`;
      el.style.display = '';
      used.add(r.key);
    }
    for (const [k, el] of this.pool) if (!used.has(k) && !el._puffing) {
      if (this.pool.size > 300) { el.remove(); this.pool.delete(k); } else el.style.display = 'none';
    }
  }
  /** 화면의 이 점에 앤 이름표가 있으면 그 앤 (이름표는 화면을 끄는 손짓을 막지 않도록 터치를 받지 않으므로 직접 잰다) */
  hitAnn(x, y, pad = 8) {
    let best = null, bd = Infinity;
    for (const el of this.pool.values()) {
      if (el._annId == null || el.style.display === 'none' || el._puffing) continue;
      const r = el.getBoundingClientRect();
      if (x < r.left - pad || x > r.right + pad || y < r.top - pad || y > r.bottom + pad) continue;
      const d = Math.hypot(x - (r.left + r.right) / 2, y - (r.top + r.bottom) / 2);
      if (d < bd) { bd = d; best = el._annId; }
    }
    return best;
  }
  clear() { for (const el of this.pool.values()) el.remove(); this.pool.clear(); }
}
