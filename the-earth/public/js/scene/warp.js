// 마을을 부드럽게 휘기: 서버의 길·집 자리는 20m 격자이지만, 화면에 그릴 때는 마을 전체(땅·강·숲·길·집·사람·차)를
// 같은 물결로 함께 구부린다. 반듯한 길이 굽이진 길이 되고, 집들은 굽은 길을 따라 돌아앉는다.
// 모두 같이 휘므로 집이 길이나 물에 빠지지 않는다. 마을 가장자리로 가면 휨이 0이 되어 지구 쪽 땅과 그대로 이어진다.
// 높이(y)는 휘기 전 자리에서 잰 그대로 둔다 — 땅도 함께 옮겨 가므로 그 자리의 높이가 맞다.
import * as THREE from 'three';
import { TERRAIN_R } from '../shared/terrain.js';

const frac = v => v - Math.floor(v);
const rnd = (seed, k) => frac(Math.sin(seed * 12.9898 + k * 78.233) * 43758.5453);

/**
 * 반듯하게 펴진 곳: 관청이 계획해서 닦은 길(자갈길 이상) 양옆 블록. 사람이 다녀 저절로 난 흙길 동네는 구불구불한 그대로.
 * roads: { 'h,i,j' | 'v,i,j': 등급 } (서버 infra.js 와 같은 100m 블록 격자, 길은 블록 경계 x = i·100 + 10)
 * 반환: { key, S(x, z) → 0(구불구불) ~ 1(반듯) }
 */
export function straightField(roads = {}) {
  const BW = 100, OFF = 10;
  const blocks = new Set();
  for (const [e, g] of Object.entries(roads)) {
    if (!(g >= 1)) continue;
    const [k, a, b] = e.split(','), i = +a, j = +b;
    if (k === 'h') { blocks.add(i + ',' + (j - 1)); blocks.add(i + ',' + j); }
    else { blocks.add((i - 1) + ',' + j); blocks.add(i + ',' + j); }
  }
  const key = [...blocks].sort().join(';');
  if (!blocks.size) return { key, S: null };
  const V = (bi, bj) => (blocks.has(bi + ',' + bj) ? 1 : 0);
  // 블록 가운데 값을 겹선형으로 잇는다 (블록 가운데: i·100 + 60)
  const S = (x, z) => {
    const fx = (x - OFF - BW / 2) / BW, fz = (z - OFF - BW / 2) / BW;
    const i = Math.floor(fx), j = Math.floor(fz), u = fx - i, v = fz - j;
    return (V(i, j) * (1 - u) + V(i + 1, j) * u) * (1 - v) + (V(i, j + 1) * (1 - u) + V(i + 1, j + 1) * u) * v;
  };
  return { key, S };
}

/** 정착지마다 다른 물결. S: straightField 의 펴진 정도 (없으면 마을 전체가 구불구불) */
export function makeWarp(seed, S = null) {
  // 큰 굽이 두 개 (파장 240~320m, 진폭 13~17m) + 작은 굽이 둘 (110~150m, 4~6m). 기울기 합이 1보다 한참 작아 접히지 않는다
  const waves = [];
  const add = (k, lam0, lam1, a0, a1) => {
    const lam = lam0 + rnd(seed, k) * (lam1 - lam0), amp = a0 + rnd(seed, k + 1) * (a1 - a0);
    const dir = rnd(seed, k + 2) * Math.PI * 2, ph = rnd(seed, k + 3) * Math.PI * 2, axis = rnd(seed, k + 4) * Math.PI * 2;
    waves.push({ kx: Math.cos(dir) * 2 * Math.PI / lam, kz: Math.sin(dir) * 2 * Math.PI / lam, ph, ax: Math.cos(axis) * amp, az: Math.sin(axis) * amp });
  };
  add(1, 240, 320, 13, 17); add(11, 240, 320, 13, 17); add(21, 110, 150, 4, 6); add(31, 110, 150, 4, 6);
  const R0 = TERRAIN_R * 0.6, R1 = TERRAIN_R * 0.85;
  const fade = (x, z) => { const r = Math.hypot(x, z); if (r <= R0) return 1; if (r >= R1) return 0; const t = (r - R0) / (R1 - R0); return 1 - t * t * (3 - 2 * t); };
  const off = (x, z) => {
    let dx = 0, dz = 0;
    for (const w of waves) { const s = Math.sin(w.kx * x + w.kz * z + w.ph); dx += w.ax * s; dz += w.az * s; }
    let f = fade(x, z);
    if (S && f > 0) { const t = Math.min(1, Math.max(0, (S(x, z) - 0.3) / 0.6)); f *= 1 - t * t * (3 - 2 * t); }   // 닦은 길 동네는 반듯하게
    return [dx * f, dz * f];
  };
  const on = !(typeof window !== 'undefined' && window.__noWarp);   // 비교용으로 끌 수 있다
  const P = on ? (x, z) => { const [dx, dz] = off(x, z); return [x + dx, z + dz]; } : (x, z) => [x, z];
  /** 그 자리에서 땅이 돌아간 각 (three.js rotation.y 와 같은 방향) */
  const rot = (x, z) => {
    const [ax, az] = P(x + 1, z), [bx, bz] = P(x - 1, z);
    return Math.atan2(-(az - bz), ax - bx);
  };
  return {
    P, rot,
    /** 기하의 꼭짓점 xz 를 휜다 (한 번만) */
    geo(g) {
      if (!g || g.userData.warped) return g;
      const pa = g.attributes.position;
      for (let i = 0; i < pa.count; i++) { const [x, z] = P(pa.getX(i), pa.getZ(i)); pa.setX(i, x); pa.setZ(i, z); }
      pa.needsUpdate = true;
      g.userData.warped = true;
      if (g.attributes.normal) g.computeVertexNormals();
      g.computeBoundingSphere(); g.computeBoundingBox();
      return g;
    },
    /** 자리에 놓인 물체: 위치를 옮기고 그 자리의 기울기만큼 돌린다 */
    obj(o) {
      if (o.userData.warped) return o;
      const [x, z] = P(o.position.x, o.position.z);
      o.rotation.y += rot(o.position.x, o.position.z);
      o.position.x = x; o.position.z = z;
      o.userData.warped = true;
      return o;
    },
    /** 여러 개를 한 번에 그리는 물체: 하나하나의 자리 */
    inst(im) {
      if (im.userData.warped) return im;
      const m = new THREE.Matrix4(), p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3(), r = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
      for (let i = 0; i < im.count; i++) {
        im.getMatrixAt(i, m); m.decompose(p, q, s);
        r.setFromAxisAngle(up, rot(p.x, p.z)); q.premultiply(r);
        const [x, z] = P(p.x, p.z); p.x = x; p.z = z;
        m.compose(p, q, s); im.setMatrixAt(i, m);
      }
      im.instanceMatrix.needsUpdate = true;
      im.computeBoundingSphere?.();
      im.userData.warped = true;
      return im;
    },
    /** 무리(group) 안의 것들: 여러 개짜리는 하나하나, 땅 좌표로 지은 기하는 꼭짓점을, 자리에 놓인 물체는 통째로 */
    tree(grp) {
      for (const o of [...grp.children]) {
        if (o.isInstancedMesh) this.inst(o);
        else if ((o.isMesh || o.isLineSegments || o.isLine || o.isPoints) && o.position.lengthSq() < 1e-6 && o.geometry) this.geo(o.geometry);
        else this.obj(o);
      }
      return grp;
    },
  };
}
