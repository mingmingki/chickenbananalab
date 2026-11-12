// 우주: 별하늘(360°), 은하수, 태양, 달, 행성. 게임 시계에 따라 태양의 방향(낮/밤, 계절)이 정해진다.
import * as THREE from 'three';
import { R, D2R } from './geo.js';

const LOGV = '#include <common>\n#include <logdepthbuf_pars_vertex>\n';
const LOGF = '#include <logdepthbuf_pars_fragment>\n';
const OBLIQ = 23.44 * D2R;
export const D_SUN = 2.0e6; // 태양까지의 거리 (실제보다 압축)
const SKY_R = 8e9;

export function glowTexture(inner = 'rgba(255,255,255,1)', mid = 'rgba(255,220,160,0.35)', size = 256) {
  const c = document.createElement('canvas'); c.width = c.height = size;
  const g = c.getContext('2d');
  const rg = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  rg.addColorStop(0, inner); rg.addColorStop(0.18, mid); rg.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = rg; g.fillRect(0, 0, size, size);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function eq(raDeg, decDeg, r = 1) {
  const a = raDeg * D2R, d = decDeg * D2R;
  return new THREE.Vector3(r * Math.cos(d) * Math.sin(a), r * Math.sin(d), r * Math.cos(d) * Math.cos(a));
}

function makeStars() {
  const N = 16000, MW = 26000;
  const pos = new Float32Array((N + MW) * 3), col = new Float32Array((N + MW) * 3), size = new Float32Array(N + MW), ph = new Float32Array(N + MW);
  const palette = [[0.62, 0.72, 1], [0.8, 0.86, 1], [1, 1, 1], [1, 0.95, 0.85], [1, 0.85, 0.65], [1, 0.72, 0.5]];
  const ngp = eq(192.86, 27.13).normalize();
  const t1 = new THREE.Vector3(0, 1, 0).cross(ngp).normalize(), t2 = ngp.clone().cross(t1).normalize();
  const v = new THREE.Vector3();
  for (let i = 0; i < N + MW; i++) {
    if (i < N) {
      const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
      v.set(Math.sqrt(1 - u * u) * Math.cos(th), u, Math.sqrt(1 - u * u) * Math.sin(th));
    } else {
      // 은하수: 은하 평면 근처에 몰린 희미한 별들 (중심 방향이 더 밝고 두껍다)
      const th = Math.random() * Math.PI * 2;
      const center = 0.5 + 0.5 * Math.cos(th - 4.65);
      const spread = (0.05 + 0.1 * center) * (Math.random() + Math.random() + Math.random() - 1.5);
      v.copy(t1).multiplyScalar(Math.cos(th)).addScaledVector(t2, Math.sin(th)).addScaledVector(ngp, spread).normalize();
    }
    pos.set([v.x * SKY_R, v.y * SKY_R, v.z * SKY_R], i * 3);
    const p = palette[Math.floor(Math.pow(Math.random(), 0.8) * palette.length)];
    col.set(p, i * 3);
    const m = Math.pow(Math.random(), i < N ? 6 : 9);
    size[i] = i < N ? 1.0 + m * 4.2 : 0.8 + m * 2.4;
    ph[i] = Math.random() * 100;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.setAttribute('size', new THREE.BufferAttribute(size, 1));
  g.setAttribute('phase', new THREE.BufferAttribute(ph, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { time: { value: 0 }, pixelRatio: { value: Math.min(2, devicePixelRatio) } },
    vertexShader: LOGV + `attribute float size; attribute float phase; attribute vec3 color; uniform float time; uniform float pixelRatio; varying vec3 vC; varying float vA;
      void main(){ vC = color; vA = 0.55 + 0.45 * sin(time * (0.6 + fract(phase) * 1.6) + phase);
        vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * mv; gl_PointSize = size * pixelRatio * (0.85 + 0.3 * vA);
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: LOGF + `varying vec3 vC; varying float vA;
      void main(){
        #include <logdepthbuf_fragment>
        vec2 d = gl_PointCoord - 0.5; float r = length(d); if (r > 0.5) discard;
        float a = smoothstep(0.5, 0.0, r); gl_FragColor = vec4(vC, a * (0.55 + 0.45 * vA));
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(g, mat);
  pts.frustumCulled = false;
  return pts;
}

function skySprite(tex, dir, scale, opacity = 1, rot = 0) {
  const m = new THREE.SpriteMaterial({ map: tex, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending, rotation: rot });
  const s = new THREE.Sprite(m);
  s.position.copy(dir).multiplyScalar(SKY_R * 0.98);
  s.scale.set(SKY_R * scale, SKY_R * scale * 0.55, 1);
  return s;
}

function galaxyTexture(c1, c2) {
  const S = 256, cv = document.createElement('canvas'); cv.width = cv.height = S;
  const g = cv.getContext('2d');
  const rg = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  rg.addColorStop(0, c1); rg.addColorStop(0.15, c2); rg.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = rg; g.fillRect(0, 0, S, S);
  for (let i = 0; i < 500; i++) { const a = Math.random() * 6.28, r = Math.pow(Math.random(), 1.5) * S * 0.45; g.fillStyle = `rgba(255,255,255,${Math.random() * 0.4})`; g.fillRect(S / 2 + Math.cos(a) * r, S / 2 + Math.sin(a) * r * 0.5, 1, 1); }
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function planetTexture(kind) {
  const W = 512, H = 256, c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d');
  const bands = {
    mercury: ['#8c8580', '#9e968f', '#7a736e'], venus: ['#e8d5a3', '#dcc28a', '#efe0b8'], mars: ['#b5532f', '#c4673f', '#9d4628', '#d27a4c'],
    jupiter: ['#d9b48a', '#c48d5d', '#efe0c8', '#a8724a', '#e4c9a3', '#b98a63'], saturn: ['#e3cf9d', '#d1b57d', '#efe2bd', '#c2a46e'],
    uranus: ['#9fe3e8', '#8fd6de'], neptune: ['#3f6fd8', '#4c80e6', '#3560c4'], moon: ['#8e8e8e', '#9a9a9a', '#7f7f7f'],
  }[kind];
  for (let y = 0; y < H; y++) {
    const k = Math.floor((Math.sin(y * 0.11 + Math.sin(y * 0.031) * 3) * 0.5 + 0.5) * bands.length) % bands.length;
    g.fillStyle = bands[k]; g.fillRect(0, y, W, 1);
  }
  if (kind === 'mars' || kind === 'mercury' || kind === 'moon') {
    for (let i = 0; i < (kind === 'moon' ? 260 : 120); i++) {
      const x = Math.random() * W, y = Math.random() * H, r = Math.pow(Math.random(), 2) * 14 + 1.5;
      g.fillStyle = `rgba(0,0,0,${0.08 + Math.random() * 0.15})`; g.beginPath(); g.arc(x, y, r, 0, 6.28); g.fill();
      g.strokeStyle = `rgba(255,255,255,${0.06 + Math.random() * 0.08})`; g.beginPath(); g.arc(x - 0.6, y - 0.6, r, 0, 6.28); g.stroke();
    }
    if (kind === 'moon') for (let i = 0; i < 9; i++) { g.fillStyle = 'rgba(40,40,45,0.28)'; g.beginPath(); g.ellipse(Math.random() * W, H * 0.25 + Math.random() * H * 0.5, 20 + Math.random() * 40, 14 + Math.random() * 24, Math.random() * 3, 0, 6.28); g.fill(); }
  }
  if (kind === 'mars') { g.fillStyle = '#f2f2f2'; g.fillRect(0, 0, W, 10); g.fillRect(0, H - 8, W, 8); }
  if (kind === 'jupiter') { g.fillStyle = '#b5583a'; g.beginPath(); g.ellipse(W * 0.6, H * 0.64, 26, 13, 0, 0, 6.28); g.fill(); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

const PLANETS = [
  { key: 'mercury', name: '수성', a: 0.387, P: 88, r: 7000, L0: 252 },
  { key: 'venus', name: '금성', a: 0.723, P: 224.7, r: 13000, L0: 181 },
  { key: 'mars', name: '화성', a: 1.524, P: 687, r: 9000, L0: 355 },
  { key: 'jupiter', name: '목성', a: 5.2, P: 4333, r: 52000, L0: 34 },
  { key: 'saturn', name: '토성', a: 9.54, P: 10759, r: 44000, L0: 50, ring: true },
  { key: 'uranus', name: '천왕성', a: 19.2, P: 30687, r: 24000, L0: 314 },
  { key: 'neptune', name: '해왕성', a: 30.1, P: 60190, r: 23000, L0: 304 },
];

export function makeSpace() {
  const sky = new THREE.Group(); sky.name = 'sky';
  sky.add(makeStars());
  const mw = galaxyTexture('rgba(255,240,220,0.9)', 'rgba(170,150,230,0.25)');
  sky.add(skySprite(mw, eq(10.7, 41.3), 0.06, 0.8, 0.6));      // 안드로메다
  sky.add(skySprite(mw, eq(80.9, -69.8), 0.09, 0.55, 0.3));    // 대마젤란
  sky.add(skySprite(mw, eq(13.2, -72.8), 0.05, 0.45, 1.2));    // 소마젤란
  const neb = [['rgba(255,120,150,0.8)', 'rgba(255,80,120,0.12)', 266, -29], ['rgba(120,200,255,0.7)', 'rgba(80,140,255,0.1)', 84, -5], ['rgba(255,170,90,0.6)', 'rgba(255,110,60,0.1)', 300, 40], ['rgba(170,120,255,0.6)', 'rgba(120,90,255,0.1)', 200, -60]];
  for (const [c1, c2, ra, dec] of neb) sky.add(skySprite(galaxyTexture(c1, c2), eq(ra, dec), 0.22, 0.35, Math.random() * 3));

  // 태양계 (적도 좌표계 안에 황도면을 기울여 둔다)
  const equatorial = new THREE.Group();
  const ecliptic = new THREE.Group();
  ecliptic.rotation.z = OBLIQ;
  equatorial.add(ecliptic);

  const sunGroup = new THREE.Group();
  const sunMesh = new THREE.Mesh(new THREE.SphereGeometry(30000, 48, 32), new THREE.MeshBasicMaterial({ color: 0xfff1d0 }));
  const sunGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,250,235,1)', 'rgba(255,200,120,0.4)'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  sunGlow.scale.set(5e5, 5e5, 1);
  const sunFar = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,250,235,1)', 'rgba(255,190,110,0.5)'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: false }));
  sunFar.scale.set(0.09, 0.09, 1);
  const light = new THREE.PointLight(0xffffff, 3.2, 0, 0);
  sunGroup.add(sunMesh, sunGlow, sunFar, light);
  ecliptic.add(sunGroup);

  const planets = [];
  const orbitMat = new THREE.LineBasicMaterial({ color: 0x6f8cc8, transparent: true, opacity: 0.18 });
  const dotTex = glowTexture('rgba(255,255,255,1)', 'rgba(255,255,255,0.3)', 64);
  for (const p of PLANETS) {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(p.r, 48, 32), new THREE.MeshStandardMaterial({ map: planetTexture(p.key), roughness: 0.9, metalness: 0 }));
    if (p.ring) {
      const rg = new THREE.RingGeometry(p.r * 1.3, p.r * 2.3, 96);
      const cv = document.createElement('canvas'); cv.width = 256; cv.height = 4;
      const cg = cv.getContext('2d');
      for (let x = 0; x < 256; x++) { cg.fillStyle = `rgba(230,210,170,${0.25 + 0.6 * Math.abs(Math.sin(x * 0.19)) * (x > 150 && x < 165 ? 0.1 : 1)})`; cg.fillRect(x, 0, 1, 4); }
      const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
      const pos = rg.attributes.position, uv = rg.attributes.uv, v = new THREE.Vector3();
      for (let i = 0; i < pos.count; i++) { v.fromBufferAttribute(pos, i); uv.setXY(i, (v.length() - p.r * 1.3) / (p.r), 0.5); }
      const ring = new THREE.Mesh(rg, new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: true, depthWrite: false }));
      ring.rotation.x = -Math.PI / 2 + 0.47;
      mesh.add(ring);
    }
    const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: dotTex, color: 0xfff6e0, transparent: true, depthWrite: false, sizeAttenuation: false, blending: THREE.AdditiveBlending }));
    dot.scale.set(0.012, 0.012, 1);
    mesh.add(dot);
    ecliptic.add(mesh);
    const pts = [];
    for (let i = 0; i <= 256; i++) { const t = i / 256 * Math.PI * 2; pts.push(new THREE.Vector3(Math.sin(t) * p.a * D_SUN, 0, Math.cos(t) * p.a * D_SUN)); }
    const orbit = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), orbitMat);
    ecliptic.add(orbit);
    planets.push({ ...p, mesh, orbit });
  }
  // 지구 궤도
  { const pts = []; for (let i = 0; i <= 256; i++) { const t = i / 256 * Math.PI * 2; pts.push(new THREE.Vector3(Math.sin(t) * D_SUN, 0, Math.cos(t) * D_SUN)); }
    const eo = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x58b4ff, transparent: true, opacity: 0.25 }));
    ecliptic.add(eo); planets.earthOrbit = eo; }

  // 달 (실제 거리와 크기)
  const moon = new THREE.Mesh(new THREE.SphereGeometry(1737, 64, 48), new THREE.MeshStandardMaterial({ map: planetTexture('moon'), roughness: 1, metalness: 0 }));
  ecliptic.add(moon);

  // 하늘에서 오는 채움빛 (그늘진 벽이 새까맣지 않게)
  const ambient = new THREE.HemisphereLight(0xcfe0ff, 0x6b5a45, 0.9);

  const space = { sky, equatorial, ecliptic, sunGroup, sunMesh, sunGlow, sunFar, light, planets, moon, ambient, sunDir: new THREE.Vector3(1, 0, 0), sunWorld: new THREE.Vector3() };

  /** 게임 시각(분)에 맞춰 천체 배치 */
  space.update = (minutes, camera, realTime) => {
    const d = minutes / 1440;
    const lam = (280.46 + 0.9856474 * d) * D2R; // 태양의 황경
    // 태양의 적경
    const ra = Math.atan2(Math.cos(OBLIQ) * Math.sin(lam), Math.cos(lam));
    const utcH = ((minutes / 60) % 24 + 24) % 24;
    const subLon = -(utcH - 12) * 15 * D2R; // 태양이 머리 위에 있는 경도
    const theta = ra - subLon;
    equatorial.rotation.y = -theta;
    sky.rotation.y = -theta;
    sky.position.copy(camera.position);
    sunGroup.position.set(Math.sin(lam) * D_SUN, 0, Math.cos(lam) * D_SUN);
    const sunPos = sunGroup.position;
    for (const p of planets) {
      const L = (p.L0 + 360 * d / p.P) * D2R;
      p.mesh.position.set(sunPos.x + Math.sin(L) * p.a * D_SUN, 0, sunPos.z + Math.cos(L) * p.a * D_SUN);
      p.orbit.position.copy(sunPos);
      p.mesh.rotation.y = realTime * 0.05;
    }
    planets.earthOrbit.position.copy(sunPos);
    const ml = (218.3 + 13.176396 * d) * D2R, mb = 5.1 * D2R * Math.sin((93.3 + 13.229350 * d) * D2R);
    moon.position.set(Math.cos(mb) * Math.sin(ml) * 384400, Math.sin(mb) * 384400, Math.cos(mb) * Math.cos(ml) * 384400);
    moon.rotation.y = ml + Math.PI;
    equatorial.updateMatrixWorld(true);
    sunGroup.getWorldPosition(space.sunWorld);
    space.sunDir.copy(space.sunWorld).normalize();
    sky.children[0].material.uniforms.time.value = realTime;
  };
  return space;
}
