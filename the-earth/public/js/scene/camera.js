// 카메라: 지표의 한 점(초점)을 고도·방위·기울기로 바라본다. 지구 전체에서 골목길까지 끊김 없이 확대된다.
// 손가락(마우스)으로 잡은 땅은 손가락을 그대로 따라온다 — 지구본을 손으로 돌리듯.
import * as THREE from 'three';
import { R, R2D, D2R, llToVec, frameAt } from './geo.js';

const clamp = THREE.MathUtils.clamp;
const wrapLon = l => ((l + 540) % 360) - 180;
// 가장 높이 올라가면 휴대폰 세로 화면에도 지구 전체가 들어온다 (우주로는 나가지 않는다)
export const MIN_ALT = 0.012, MAX_ALT = 32000;

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
    this.pickLL = null;     // (화면 x, y) → {lat, lon} : 커서가 가리키는 땅 (줌을 그쪽으로)
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
      if (this.pointers.size === 1) {
        this.dragged = false;
        // 손가락이 잡은 땅 (지구 밖을 잡으면 없음)
        this.grab = !(e.button === 2 || e.shiftKey || e.ctrlKey) && this.pickLL ? this.pickLL(e.clientX, e.clientY) : null;
      } else this.grab = null;
      this.g2 = this.pointers.size === 2 ? this.twoFinger() : null;
      if (this.g2) {
        // 두 손가락 사이의 땅을 잡는다. 처음 조금 움직여 보고 '벌리기·옮기기'인지 '함께 위아래(기울이기)'인지 정한다
        this.grab = this.pickLL?.(this.g2.cx, this.g2.cy) || null;
        this.g2mode = null; this.g2acc = { d: 0, x: 0, y: 0 };
      }
    });
    el.addEventListener('pointermove', e => {
      const p = this.pointers.get(e.pointerId);
      if (!p || !this.enabled) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      if (Math.hypot(e.clientX - p.sx, e.clientY - p.sy) > 4) this.dragged = true;
      if (this.pointers.size >= 2) {
        // 두 손가락: 벌리고 오므리기 = 확대·축소, 비틀기 = 회전, 함께 위아래로 = 기울이기
        p.x = e.clientX; p.y = e.clientY;
        const g = this.twoFinger(), prev = this.g2 || g;
        const dcy = g.cy - prev.cy;
        if (!this.g2mode) {
          const A = this.g2acc;
          A.d += Math.abs(g.d - prev.d); A.x += g.cx - prev.cx; A.y += dcy;
          if (A.d + Math.abs(A.x) + Math.abs(A.y) > 14) this.g2mode = Math.abs(A.y) > 2.5 * A.d && Math.abs(A.y) > 2 * Math.abs(A.x) ? 'tilt' : 'pinch';
        }
        if (this.g2mode === 'tilt') {
          this.tgt.tilt = clamp(this.tgt.tilt + dcy * 0.25, this.minTilt(this.tgt.alt), 90); this.userTilt = true;
        } else {
          // 벌리고 오므리기: 손가락 사이의 땅이 손가락을 그대로 따라온다 (확대·이동·비틀기가 한 번에)
          let da = g.ang - prev.ang;
          if (da > Math.PI) da -= Math.PI * 2; if (da < -Math.PI) da += Math.PI * 2;
          if (Math.abs(da) < 0.5 && this.g2mode) this.tgt.heading -= da;
          if (prev.d > 0 && g.d > 0) {
            if (this.grab && this.cur.alt >= 3) {
              this.zoomBy(prev.d / g.d);
              this.cur.alt = this.tgt.alt; this.cur.tilt = this.tgt.tilt; this.cur.heading = this.tgt.heading;
              this.grabTo(g.cx, g.cy);
            } else this.zoomBy(prev.d / g.d, this.pickLL?.(g.cx, g.cy));
          }
        }
        this.g2 = g;
        this.dragged = true;
        this.onUserMove?.();
        return;
      }
      p.x = e.clientX; p.y = e.clientY;
      if (p.btn === 2 || p.shift) {
        this.tgt.heading -= dx * 0.3 * D2R;
        this.tgt.tilt = clamp(this.tgt.tilt + dy * 0.25, this.minTilt(this.tgt.alt), 90);
        this.userTilt = true;
      } else if (!this.grabTo(e.clientX, e.clientY)) {
        this.pan(dx, dy);
      }
      if (this.dragged) this.onUserMove?.();
    });
    const up = e => {
      const p = this.pointers.get(e.pointerId);
      this.pointers.delete(e.pointerId);
      this.grab = null;
      // 두 손가락 중 하나만 떼면, 남은 손가락이 그 자리의 땅을 이어서 잡는다
      if (this.pointers.size === 1 && p) {
        const [q] = this.pointers.values();
        this.grab = this.pickLL?.(q.x, q.y) || null;
        this.lastMoveAt = performance.now();
      }
      // 손을 뗄 때 방금까지 움직이던 만큼 미끄러진다 (잠깐 멈췄다 떼면 그 자리에 선다)
      if (p && performance.now() - (this.lastMoveAt || 0) > 90) this.vel.lat = this.vel.lon = 0;
      if (this.pointers.size < 2) this.g2 = null;
      // 손가락으로 두 번 톡톡 = 그곳으로 확대
      if (p && e.pointerType !== 'mouse' && !this.dragged && !this.pointers.size) {
        const now = performance.now(), lt = this.lastTap;
        if (lt && now - lt.t < 320 && Math.hypot(lt.x - e.clientX, lt.y - e.clientY) < 30) { this.lastTap = null; this.onDoubleTap?.(e); }
        else this.lastTap = { t: now, x: e.clientX, y: e.clientY };
      }
    };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', e => {
      if (!this.enabled) return;
      e.preventDefault();
      // 트랙패드의 잘게 쪼개진 스크롤도, 마우스 휠의 큰 칸도 같은 느낌으로 (한 번에 최대 2배)
      const dy = clamp(e.deltaY * (e.deltaMode === 1 ? 30 : e.deltaMode === 2 ? 600 : 1), -580, 580);
      this.zoomBy(Math.exp(dy * 0.0012), this.pickLL?.(e.clientX, e.clientY));
    }, { passive: false });
    // 키보드: 방향키 = 이동, +/- = 확대·축소, Q/E = 돌리기, PageUp/Down = 기울이기 (글을 쓰는 중이면 무시)
    addEventListener('keydown', e => {
      if (!this.enabled || e.target.closest?.('input, textarea, select, [contenteditable]')) return;
      const k = e.key;
      const step = 60;
      if (k === 'ArrowLeft') this.pan(step, 0); else if (k === 'ArrowRight') this.pan(-step, 0);
      else if (k === 'ArrowUp') this.pan(0, step); else if (k === 'ArrowDown') this.pan(0, -step);
      else if (k === '+' || k === '=') this.zoomBy(0.6); else if (k === '-' || k === '_') this.zoomBy(1.65);
      else if (k === 'q' || k === 'Q') this.tgt.heading += 0.2; else if (k === 'e' || k === 'E') this.tgt.heading -= 0.2;
      else if (k === 'PageUp') { this.tgt.tilt = clamp(this.tgt.tilt - 8, this.minTilt(this.tgt.alt), 90); this.userTilt = true; }
      else if (k === 'PageDown') { this.tgt.tilt = clamp(this.tgt.tilt + 8, this.minTilt(this.tgt.alt), 90); this.userTilt = true; }
      else return;
      this.vel.lat = this.vel.lon = 0;
      e.preventDefault();
    });
  }

  twoFinger() {
    const [a, b] = [...this.pointers.values()];
    return { d: Math.hypot(a.x - b.x, a.y - b.y), ang: Math.atan2(b.y - a.y, b.x - a.x), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
  }

  /** 확대·축소. at(커서가 가리키는 땅)이 있으면 그 점이 화면에서 제자리에 있도록 중심을 옮긴다 (구글 어스처럼) */
  zoomBy(f, at = null) {
    const before = this.tgt.alt;
    this.tgt.alt = clamp(this.tgt.alt * f, MIN_ALT, MAX_ALT);
    const ff = this.tgt.alt / before;
    if (at && !this.follow && Math.abs(ff - 1) > 1e-4 && this.tgt.alt < 30000) {
      // 중심' = 커서 + (중심 − 커서) × 배율 (한 번에 너무 멀리 튀지 않게 제한)
      const dLat = at.lat - this.tgt.lat, dLon = wrapLon(at.lon - this.tgt.lon);
      const k = clamp(1 - ff, -1, 0.9);
      this.tgt.lat = clamp(this.tgt.lat + clamp(dLat * k, -25, 25), -85, 85);
      this.tgt.lon += clamp(dLon * k, -40, 40);
      this.vel.lat = this.vel.lon = 0;
    }
    const mt = this.minTilt(this.tgt.alt);
    if (this.tgt.tilt < mt) this.tgt.tilt = mt;
  }

  /** 잡은 땅이 손가락(x, y) 아래로 오도록 지구를 돌린다. 땅을 못 잡았으면 false */
  grabTo(x, y) {
    if (!this.grab || !this.pickLL || this.cur.alt < 3) return false;
    // 끄는 동안은 카메라가 바로 따라온다 (늦게 따라오면 손가락과 어긋난다)
    // 위경도로 한 번 돌리면 조금 어긋나므로 두세 번 다듬어 손가락 바로 밑에 맞춘다
    let dLat = 0, dLon = 0;
    for (let it = 0; it < 3; it++) {
      this.cur.lat = this.tgt.lat; this.cur.lon = this.tgt.lon;
      this.apply(); this.cam.updateMatrixWorld();
      const now = this.pickLL(x, y);
      if (!now) { if (it === 0) return false; break; }
      const eLat = clamp(this.grab.lat - now.lat, -20, 20), eLon = clamp(wrapLon(this.grab.lon - now.lon), -30, 30);
      this.tgt.lat = clamp(this.tgt.lat + eLat, -85, 85);
      this.tgt.lon += eLon;
      dLat += eLat; dLon += eLon;
      if (Math.abs(eLat) + Math.abs(eLon) < 1e-4 * Math.max(1, this.cur.alt / 100)) break;
    }
    this.cur.lat = this.tgt.lat; this.cur.lon = this.tgt.lon;
    this.apply(); this.cam.updateMatrixWorld();
    // 관성: 이번 움직임을 초당 60장 기준 한 장면 몫으로
    const t = performance.now(), dt = Math.max(8, Math.min(60, t - (this.lastMoveAt || t - 16)));
    this.lastMoveAt = t;
    this.vel.lat = dLat * 16.7 / dt; this.vel.lon = dLon * 16.7 / dt;
    if (this.follow) { this.follow = null; this.onUserMove?.('unfollow'); }
    return true;
  }

  pan(dx, dy) {
    this.lastMoveAt = performance.now();
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
      // 관성: 초당 장면 수(30·60fps)와 상관없이 같은 만큼 미끄러진다
      const fr = Math.min(6, dt * 60);
      this.tgt.lat = clamp(this.tgt.lat + this.vel.lat * 0.9 * fr, -85, 85);
      this.tgt.lon += this.vel.lon * 0.9 * fr;
      const decay = Math.pow(0.88, fr);
      this.vel.lat *= decay; this.vel.lon *= decay;
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

  /** 지금 움직이는 중인가 (손가락이 닿아 있거나, 미끄러지거나, 목표로 날아가는 중) */
  busy() {
    if (this.pointers.size || Math.abs(this.vel.lat) + Math.abs(this.vel.lon) > 1e-4) return true;
    const c = this.cur, g = this.tgt;
    return Math.abs(g.lat - c.lat) + Math.abs(wrapLon(g.lon - c.lon)) > 1e-3 || Math.abs(Math.log(g.alt / c.alt)) > 2e-3 || Math.abs(g.tilt - c.tilt) > 0.05 || Math.abs(g.heading - c.heading) > 1e-3;
  }
}
