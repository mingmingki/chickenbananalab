// 좌표: 지구 중심 고정 좌표계 (단위 km). +Y = 북극, 경도 0° = +Z, 동경 90° = +X
import * as THREE from 'three';

export const R = 6371;
export const D2R = Math.PI / 180, R2D = 180 / Math.PI;

export function llToVec(lat, lon, r = R, out = new THREE.Vector3()) {
  const la = lat * D2R, lo = lon * D2R;
  return out.set(r * Math.cos(la) * Math.sin(lo), r * Math.sin(la), r * Math.cos(la) * Math.cos(lo));
}
export function vecToLL(v) {
  const r = v.length();
  return { lat: Math.asin(THREE.MathUtils.clamp(v.y / r, -1, 1)) * R2D, lon: Math.atan2(v.x, v.z) * R2D };
}
/** 지표의 국소 좌표계: up(법선), east, north */
export function frameAt(lat, lon) {
  const up = llToVec(lat, lon, 1);
  let east = new THREE.Vector3(0, 1, 0).cross(up);
  if (east.lengthSq() < 1e-8) east.set(1, 0, 0);
  east.normalize();
  const north = up.clone().cross(east).normalize();
  return { up, east, north };
}
/** 정착지 로컬 좌표(m, x=동쪽, z=남쪽) → 위경도 */
export function localToLL(s, x, z) {
  const lat = s.lat - (z / 1000) / R * R2D;
  const lon = s.lon + (x / 1000) / (R * Math.cos(s.lat * D2R)) * R2D;
  return { lat, lon };
}

/** 위경도 격자 구 (UV가 등장방형 지도와 정확히 맞도록) */
export function latLonSphere(r, segW, segH) {
  const pos = [], uv = [], idx = [], nrm = [];
  for (let j = 0; j <= segH; j++) {
    const lat = 90 - j / segH * 180;
    for (let i = 0; i <= segW; i++) {
      const lon = -180 + i / segW * 360;
      const v = llToVec(lat, lon, 1);
      pos.push(v.x * r, v.y * r, v.z * r);
      nrm.push(v.x, v.y, v.z);
      uv.push(i / segW, 1 - j / segH);
    }
  }
  for (let j = 0; j < segH; j++) for (let i = 0; i < segW; i++) {
    const a = j * (segW + 1) + i, b = a + segW + 1;
    idx.push(a, b, a + 1, b, b + 1, a + 1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  return g;
}

/** 등장방형 캔버스에 국가 다각형 그리기 (날짜변경선 처리) */
export function traceRings(ctx, rings, W, H, offsetX = 0) {
  for (const flat of rings) {
    let prevLon = null, shift = 0;
    for (let k = 0; k < flat.length; k += 2) {
      let lon = flat[k] + shift;
      const lat = flat[k + 1];
      if (prevLon !== null) {
        if (lon - prevLon > 180) { shift -= 360; lon -= 360; }
        else if (prevLon - lon > 180) { shift += 360; lon += 360; }
      }
      prevLon = lon;
      const x = (lon + 180) / 360 * W + offsetX, y = (90 - lat) / 180 * H;
      if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}
export function drawCountry(ctx, c, W, H) {
  for (const off of [-W, 0, W]) traceRings(ctx, c.rings, W, H, off);
}
