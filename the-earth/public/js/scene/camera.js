// 카메라: 지표의 한 점(초점)을 고도·방위·기울기로 바라본다. 우주에서 골목길까지 끊김 없이 확대된다.
import * as THREE from 'three';
import { R, R2D, D2R, llToVec, frameAt } from './geo.js';

const clamp = THREE.MathUtils.clamp;
const wrapLon = l => ((l + 540) % 360) - 180;
export const MIN_ALT = 0.012, MAX_ALT = 2.6e8;

export class CameraRig {
  constructor(camera, dom) {
    this.cam = camera;
    this.dom = dom;
    this.cur = { lat: 30, lon: 127, alt: 22000, heading: 0, tilt: 90, h: 0 };
    this.tgt = { ...this.cur };
    this.follow = null;     // () => {lat, lon, h}
    this.enabled = true;
    this.userTilt = false;
    this.onUserMove = null;
    this.vel = { lat: 0, lon: 0 };
    this.pointers = new Map();
    this._bind();
  }

  minTilt(alt) {
    // 높이 올라갈수록 수직(90°)으로만 볼 수 있다
    const t = clamp((Math.log10(alt) - Math.log10(0.4)) / (Math.log10(3000) - Math.log10(0.4)), 0, 1);
    return 10 + t * 80;
  }

  flyTo(o) {
    if (o.lat != null) this.tgt.lat = o.lat;
    if (o.lon != null) {
      // 가까운 쪽으로 돈다
      let d = wrapLon(o.lon - this.cur.lon);
      this.tgt.lon = this.cur.lon + d;
    }
    if (o.alt != null) this.tgt.alt = clamp(o.alt, MIN_ALT, MAX_ALT);
    if (o.heading != null) this.tgt.heading = o.heading;
    if (o.tilt != null) { this.tgt.tilt = o.tilt; this.userTilt = true; }
    else this.userTilt = false;
    if (o.h != null) this.tgt.h = o.h;
  }

  jump(o) { this.flyTo(o); Object.assign(this.cur, this.tgt); }

  _bind() {
    const el = this.dom;
    el.addEventListener('contextmenu', e => e.preventDefault());
    el.addEventListener('pointerdown', e => {
      if (!this.enabled) return;
      el.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, btn: e.button, shift: e.shiftKey || e.ctrlKey });
      this.vel.lat = this.vel.lon = 0;
      this.dragged = false;
    });
    el.addEventListener('pointermove', e => {
      const p = this.pointers.get(e.pointerId);
      if (!p || !this.enabled) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      if (Math.hypot(e.clientX - p.sx, e.clientY - p.sy) > 4) this.dragged = true;
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const before = Math.hypot(a.x - b.x, a.y - b.y);
        p.x = e.clientX; p.y = e.clientY;
        const after = Math.hypot(a.x - b.x, a.y - b.y);
        if (before > 0 && after > 0) this.zoomBy(before / after);
        // 두 손가락 세로 이동 = 기울이기
        this.tgt.tilt = clamp(this.tgt.tilt + dy * 0.15, this.minTilt(this.tgt.alt), 90); this.userTilt = true;
        return;
      }
      p.x = e.clientX; p.y = e.clientY;
      if (p.btn === 2 || p.shift) {
        this.tgt.heading -= dx * 0.3 * D2R;
        this.tgt.tilt = clamp(this.tgt.tilt + dy * 0.25, this.minTilt(this.tgt.alt), 90);
        this.userTilt = true;
      } else {
        this.pan(dx, dy);
      }
      if (this.dragged) this.onUserMove?.();
    });
    const up = e => { this.pointers.delete(e.pointerId); };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', e => {
      if (!this.enabled) return;
      e.preventDefault();
      this.zoomBy(Math.exp(e.deltaY * 0.0012 * (e.deltaMode === 1 ? 30 : 1)));
    }, { passive: false });
  }

  zoomBy(f) {
    this.tgt.alt = clamp(this.tgt.alt * f, MIN_ALT, MAX_ALT);
    const mt = this.minTilt(this.tgt.alt);
    if (this.tgt.tilt < mt) this.tgt.tilt = mt;
  }

  pan(dx, dy) {
    const h = this.dom.clientHeight || 800;
    const dist = Math.min(this.cur.alt, R * 3);
    const kmPerPx = 2 * dist * Math.tan(this.cam.fov * D2R / 2) / h;
    let degPerPx = Math.min(kmPerPx / R * R2D, 0.35);
    const c = Math.cos(this.cur.heading), s = Math.sin(this.cur.heading);
    // 화면 좌표 → 동/북 이동 (방위 회전 반영)
    const north = dy * c + dx * s, east = -dx * c + dy * s;
    const dLat = north * degPerPx, dLon = east * degPerPx / Math.max(0.15, Math.cos(this.cur.lat * D2R));
    this.tgt.lat = clamp(this.tgt.lat + dLat, -85, 85);
    this.tgt.lon += dLon;
    this.vel.lat = dLat; this.vel.lon = dLon;
    if (this.follow) { this.follow = null; this.onUserMove?.('unfollow'); }
  }

  update(dt) {
    const k = 1 - Math.exp(-dt * 6);
    if (this.follow) {
      const f = this.follow();
      if (f) { this.tgt.lat = f.lat; let d = wrapLon(f.lon - this.cur.lon); this.tgt.lon = this.cur.lon + d; this.tgt.h = f.h || 0; }
    } else if (!this.pointers.size && (Math.abs(this.vel.lat) + Math.abs(this.vel.lon) > 1e-6)) {
      // 관성
      this.tgt.lat = clamp(this.tgt.lat + this.vel.lat * 0.9, -85, 85);
      this.tgt.lon += this.vel.lon * 0.9;
      this.vel.lat *= 0.88; this.vel.lon *= 0.88;
    }
    if (!this.userTilt) {
      // 가까이 갈수록 자연스럽게 비스듬히
      const t = clamp((Math.log10(this.tgt.alt) - Math.log10(0.15)) / (Math.log10(60) - Math.log10(0.15)), 0, 1);
      this.tgt.tilt = 38 + t * 52;
    }
    this.tgt.tilt = clamp(this.tgt.tilt, this.minTilt(this.tgt.alt), 90);
    const c = this.cur, g = this.tgt;
    c.lat += (g.lat - c.lat) * k;
    c.lon += (g.lon - c.lon) * k;
    c.alt = Math.exp(Math.log(c.alt) + (Math.log(g.alt) - Math.log(c.alt)) * k);
    c.heading += (g.heading - c.heading) * k;
    c.tilt += (g.tilt - c.tilt) * k;
    c.h += (g.h - c.h) * k;
    if (Math.abs(c.lon) > 720) { const w = wrapLon(c.lon) - c.lon; c.lon += w; g.lon += w; }
    this.apply();
  }

  apply() {
    const c = this.cur;
    const { up, east, north } = frameAt(c.lat, wrapLon(c.lon));
    const P = llToVec(c.lat, wrapLon(c.lon), R + c.h);
    const tilt = c.tilt * D2R;
    const f = north.clone().multiplyScalar(Math.cos(c.heading)).addScaledVector(east, Math.sin(c.heading));
    const dir = f.clone().multiplyScalar(-Math.cos(tilt)).addScaledVector(up, Math.sin(tilt));
    this.cam.position.copy(P).addScaledVector(dir, c.alt);
    this.cam.up.copy(f).multiplyScalar(Math.sin(tilt)).addScaledVector(up, Math.cos(tilt)).normalize();
    this.cam.lookAt(P);
    const near = clamp(c.alt * 0.002, 0.0003, 2000);
    if (Math.abs(this.cam.near - near) / near > 0.1) { this.cam.near = near; this.cam.far = 3e10; this.cam.updateProjectionMatrix(); }
    this.focus = P;
  }

  get alt() { return this.cur.alt; }
}
