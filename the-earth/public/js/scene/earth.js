// 지구: 실제 국경으로 그린 지형 텍스처 + 낮/밤 셰이더 + 도시 불빛 + 구름 + 대기
import * as THREE from 'three';
import { R, latLonSphere, drawCountry } from './geo.js';

const LOGV = '#include <common>\n#include <logdepthbuf_pars_vertex>\n';
const LOGF = '#include <logdepthbuf_pars_fragment>\n';

// 사막·빙하·열대우림 (경도1, 경도2, 위도1, 위도2, 색, 세기)
const REGIONS = [
  [-17, 35, 14, 31, '#d9b77a', 0.95], [34, 60, 13, 32, '#d6b073', 0.9], [48, 63, 26, 37, '#c9a777', 0.6], [68, 76, 23, 29, '#ccaa72', 0.6],
  [75, 92, 36, 42, '#d2b67f', 0.8], [92, 115, 38, 46, '#c8b088', 0.7], [116, 142, -32, -19, '#cf8f55', 0.85], [-118, -104, 29, 38, '#c99b68', 0.6],
  [12, 27, -29, -19, '#c9a46a', 0.6], [-72, -68, -28, -18, '#c5a27a', 0.5], [55, 70, 38, 46, '#c7ae80', 0.55],
  [-75, -48, -12, 4, '#2f6b2a', 0.55], [10, 30, -6, 5, '#2f6b2a', 0.5], [95, 150, -8, 8, '#2e6a33', 0.4],
  [-56, -18, 59, 84, '#eef3f6', 1.0], [-180, 180, -91, -62, '#eef2f5', 1.0], [-95, -60, 72, 84, '#e6edf0', 0.6],
];

function latBiome(lat) {
  const a = Math.abs(lat);
  if (a > 72) return '#e4ebef';
  if (a > 62) return '#8e9a7c';
  if (a > 50) return '#3f6a45';
  if (a > 36) return '#5c8a47';
  if (a > 24) return '#8fa05a';
  if (a > 12) return '#6f9446';
  return '#3d7a3a';
}

function noiseCanvas(w, h, scale, seed = 1) {
  // 값 노이즈 (경도 방향으로 이어지게)
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const g = c.getContext('2d');
  const img = g.createImageData(w, h);
  const gw = Math.ceil(w / scale) + 1, gh = Math.ceil(h / scale) + 1;
  let s = seed * 9301 + 49297;
  const grid = new Float32Array(gw * gh).map(() => { s = (s * 9301 + 49297) % 233280; return s / 233280; });
  const gx = Math.ceil(w / scale);
  const at = (i, j) => grid[(j % gh) * gw + (i % gx)];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const fx = x / scale, fy = y / scale, i = Math.floor(fx), j = Math.floor(fy);
    const tx = fx - i, ty = fy - j, sx = tx * tx * (3 - 2 * tx), sy = ty * ty * (3 - 2 * ty);
    const v = (at(i, j) * (1 - sx) + at(i + 1, j) * sx) * (1 - sy) + (at(i, j + 1) * (1 - sx) + at(i + 1, j + 1) * sx) * sy;
    const o = (y * w + x) * 4;
    const b = v * 255;
    img.data[o] = img.data[o + 1] = img.data[o + 2] = b; img.data[o + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return c;
}

export function buildEarthTextures(countries, maxTex) {
  const W = maxTex >= 8192 && Math.min(screen.width, screen.height) > 700 ? 8192 : 4096, H = W / 2;
  const sx = W / 4096;
  // 1) 육지 마스크
  const mask = document.createElement('canvas'); mask.width = W; mask.height = H;
  const mg = mask.getContext('2d');
  mg.fillStyle = '#fff';
  for (const c of countries) { mg.beginPath(); drawCountry(mg, c, W, H); mg.fill('evenodd'); }
  mg.fillRect(0, H * (1 - 12 / 180), W, H); // 남극 끝

  // 2) 생물군계 색
  const bio = document.createElement('canvas'); bio.width = W; bio.height = H;
  const bg = bio.getContext('2d');
  const grad = bg.createLinearGradient(0, 0, 0, H);
  for (let lat = 90; lat >= -90; lat -= 3) grad.addColorStop((90 - lat) / 180, latBiome(lat));
  bg.fillStyle = grad; bg.fillRect(0, 0, W, H);
  for (const [lo1, lo2, la1, la2, col, a] of REGIONS) {
    const cx = ((lo1 + lo2) / 2 + 180) / 360 * W, cy = (90 - (la1 + la2) / 2) / 180 * H;
    const rx = (lo2 - lo1) / 360 * W / 2, ry = (la2 - la1) / 180 * H / 2;
    bg.save(); bg.translate(cx, cy); bg.scale(rx, ry);
    const rg = bg.createRadialGradient(0, 0, 0, 0, 0, 1.25);
    rg.addColorStop(0, col); rg.addColorStop(0.65, col); rg.addColorStop(1, col + '00');
    bg.globalAlpha = a; bg.fillStyle = rg; bg.beginPath(); bg.arc(0, 0, 1.25, 0, Math.PI * 2); bg.fill();
    bg.restore();
  }
  bg.globalAlpha = 1;
  // 얼룩덜룩한 질감
  bg.globalCompositeOperation = 'soft-light';
  bg.drawImage(noiseCanvas(512, 256, 9, 3), 0, 0, W, H);
  bg.globalAlpha = 0.6;
  bg.drawImage(noiseCanvas(1024, 512, 4, 7), 0, 0, W, H);
  bg.globalAlpha = 1;
  bg.globalCompositeOperation = 'destination-in';
  bg.drawImage(mask, 0, 0);
  bg.globalCompositeOperation = 'source-over';

  // 3) 바다 + 얕은 대륙붕
  const day = document.createElement('canvas'); day.width = W; day.height = H;
  const dg = day.getContext('2d');
  const og = dg.createLinearGradient(0, 0, 0, H);
  og.addColorStop(0, '#0a2540'); og.addColorStop(0.3, '#0c3560'); og.addColorStop(0.5, '#0e3d6e'); og.addColorStop(0.7, '#0c3560'); og.addColorStop(1, '#0a2540');
  dg.fillStyle = og; dg.fillRect(0, 0, W, H);
  dg.globalAlpha = 0.25; dg.globalCompositeOperation = 'overlay';
  dg.drawImage(noiseCanvas(512, 256, 16, 11), 0, 0, W, H);
  dg.globalCompositeOperation = 'source-over'; dg.globalAlpha = 1;
  const shelf = document.createElement('canvas'); shelf.width = W; shelf.height = H;
  const sg = shelf.getContext('2d');
  sg.filter = `blur(${Math.round(10 * sx)}px)`; sg.drawImage(mask, 0, 0); sg.filter = 'none';
  sg.globalCompositeOperation = 'source-in'; sg.fillStyle = '#2a7fa8'; sg.fillRect(0, 0, W, H);
  dg.globalAlpha = 0.55; dg.drawImage(shelf, 0, 0); dg.globalAlpha = 1;
  dg.drawImage(bio, 0, 0);
  // 국경선
  dg.strokeStyle = 'rgba(255,255,255,0.28)'; dg.lineWidth = 1.1 * sx; dg.lineJoin = 'round';
  for (const c of countries) { dg.beginPath(); drawCountry(dg, c, W, H); dg.stroke(); }

  // 4) 국가 ID 지도 (선택/강조용)
  const IW = 4096, IH = 2048;
  const idc = document.createElement('canvas'); idc.width = IW; idc.height = IH;
  const ig = idc.getContext('2d', { willReadFrequently: true });
  ig.fillStyle = '#000'; ig.fillRect(0, 0, IW, IH);
  countries.forEach((c, i) => {
    const id = i + 1;
    ig.fillStyle = `rgb(${id & 255},${id >> 8},0)`;
    ig.beginPath(); drawCountry(ig, c, IW, IH); ig.fill('evenodd');
  });
  const idData = ig.getImageData(0, 0, IW, IH).data;

  const dayTex = new THREE.CanvasTexture(day);
  dayTex.colorSpace = THREE.SRGBColorSpace;
  dayTex.anisotropy = 8;
  const idTex = new THREE.CanvasTexture(idc);
  idTex.magFilter = idTex.minFilter = THREE.NearestFilter;
  idTex.generateMipmaps = false;
  return { dayTex, idTex, idData, IW, IH };
}

export function makeEarth(textures) {
  const lightsCanvas = document.createElement('canvas');
  lightsCanvas.width = 2048; lightsCanvas.height = 1024;
  const lightsTex = new THREE.CanvasTexture(lightsCanvas);
  lightsTex.colorSpace = THREE.SRGBColorSpace;

  const uniforms = {
    dayMap: { value: textures.dayTex }, idMap: { value: textures.idTex }, lightsMap: { value: lightsTex },
    sunDir: { value: new THREE.Vector3(1, 0, 0) }, hoverId: { value: -1 }, myId: { value: -1 }, activeBoost: { value: 0 },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: LOGV + `
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      void main() {
        vUv = uv; vN = normalize(mat3(modelMatrix) * normal);
        vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
        gl_Position = projectionMatrix * viewMatrix * w;
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: LOGF + `
      uniform sampler2D dayMap; uniform sampler2D idMap; uniform sampler2D lightsMap;
      uniform vec3 sunDir; uniform float hoverId; uniform float myId;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      void main() {
        #include <logdepthbuf_fragment>
        vec3 n = normalize(vN);
        float ndl = dot(n, sunDir);
        float day = smoothstep(-0.15, 0.2, ndl);
        vec3 col = texture2D(dayMap, vUv).rgb;
        vec4 idc = texture2D(idMap, vUv);
        float id = floor(idc.r * 255.0 + 0.5) + floor(idc.g * 255.0 + 0.5) * 256.0;
        float land = step(0.5, id);
        if (hoverId > 0.0 && abs(id - hoverId) < 0.5) col = mix(col, vec3(1.0, 0.82, 0.35), 0.45);
        if (myId > 0.0 && abs(id - myId) < 0.5) col = mix(col, vec3(0.35, 0.85, 1.0), 0.18);
        vec3 V = normalize(cameraPosition - vW);
        vec3 lit = col * (0.035 + max(ndl, 0.0) * 1.05);
        vec3 H = normalize(sunDir + V);
        float spec = pow(max(dot(n, H), 0.0), 70.0) * (1.0 - land) * day * 0.75;
        vec3 lights = texture2D(lightsMap, vUv).rgb;
        vec3 night = col * 0.025 + lights * 1.8 * land;
        vec3 c = mix(night, lit, day) + spec * vec3(1.0, 0.92, 0.75);
        float rim = pow(1.0 - max(dot(n, V), 0.0), 4.0);
        c += vec3(0.25, 0.5, 1.0) * rim * 0.55 * smoothstep(-0.35, 0.35, ndl);
        // 노을빛 경계
        c += vec3(0.6, 0.25, 0.05) * smoothstep(0.2, 0.0, abs(ndl)) * 0.12 * (1.0 - land * 0.5);
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(latLonSphere(R, 512, 256), mat);
  mesh.name = 'earth';

  // 구름 (절차적 3D 노이즈)
  const cloudU = { sunDir: uniforms.sunDir, time: { value: 0 }, opacity: { value: 0.85 } };
  const clouds = new THREE.Mesh(latLonSphere(R * 1.0045, 256, 128), new THREE.ShaderMaterial({
    uniforms: cloudU, transparent: true, depthWrite: false,
    vertexShader: LOGV + `varying vec3 vP; varying vec3 vN;
      void main(){ vP = normalize(position); vN = normalize(mat3(modelMatrix)*normal); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0);
      #include <logdepthbuf_vertex>
      }`,
    fragmentShader: LOGF + `uniform vec3 sunDir; uniform float time; uniform float opacity; varying vec3 vP; varying vec3 vN;
      float hash(vec3 p){ p = fract(p*0.3183099+.1); p *= 17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
      float noise(vec3 x){ vec3 i=floor(x); vec3 f=fract(x); f=f*f*(3.0-2.0*f);
        return mix(mix(mix(hash(i+vec3(0,0,0)),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
                   mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z); }
      float fbm(vec3 p){ float v=0.0; float a=0.5; for(int i=0;i<6;i++){ v+=a*noise(p); p=p*2.03+vec3(1.7,9.2,3.1); a*=0.5; } return v; }
      void main(){
        #include <logdepthbuf_fragment>
        float lat = vP.y;
        vec3 q = vP*3.2 + vec3(time*0.012, 0.0, time*0.007);
        float band = 0.55 + 0.25*cos(lat*9.0);
        float d = fbm(q + fbm(q*1.7)*0.9);
        float a = smoothstep(band*0.62, band*0.62+0.22, d);
        float ndl = dot(normalize(vN), sunDir);
        float lightAmt = smoothstep(-0.2, 0.3, ndl);
        vec3 c = mix(vec3(0.02,0.03,0.05), vec3(1.0), 0.06 + lightAmt*0.94);
        gl_FragColor = vec4(c, a*opacity*(0.25+0.75*lightAmt));
        #include <colorspace_fragment>
      }`,
  }));
  clouds.name = 'clouds';

  // 대기 (바깥쪽 빛무리)
  const atmo = new THREE.Mesh(new THREE.SphereGeometry(R * 1.03, 96, 64), new THREE.ShaderMaterial({
    uniforms: { sunDir: uniforms.sunDir },
    side: THREE.BackSide, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: LOGV + `varying vec3 vN; varying vec3 vW; void main(){ vN = normalize(mat3(modelMatrix)*normal); vec4 w = modelMatrix*vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix*viewMatrix*w;
      #include <logdepthbuf_vertex>
      }`,
    fragmentShader: LOGF + `uniform vec3 sunDir; varying vec3 vN; varying vec3 vW;
      void main(){
        #include <logdepthbuf_fragment>
        vec3 V = normalize(cameraPosition - vW);
        // 뒷면 법선과 시선의 각: 지구 가장자리에서 ~0.24, 대기 바깥 끝에서 0
        float k = clamp(dot(normalize(vN), -V) / 0.24, 0.0, 1.0);
        float glow = pow(k, 1.8);
        float sun = smoothstep(-0.45, 0.4, dot(normalize(vN), sunDir));
        vec3 c = mix(vec3(0.1,0.3,1.0), vec3(0.45,0.75,1.0), sun) * glow * (0.08 + sun * 0.9);
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  }));
  atmo.name = 'atmosphere';

  const group = new THREE.Group();
  group.add(mesh, clouds, atmo);
  return { group, mesh, clouds, atmo, uniforms, cloudU, lightsCanvas, lightsTex };
}

/** 정착지 불빛 (밤에 보이는 도시의 빛) */
export function paintLights(earth, settlements) {
  const c = earth.lightsCanvas, g = c.getContext('2d');
  g.clearRect(0, 0, c.width, c.height);
  g.globalCompositeOperation = 'lighter';
  for (const s of settlements) {
    if (s.abandoned) continue;
    const x = (s.lon + 180) / 360 * c.width, y = (90 - s.lat) / 180 * c.height;
    const r = 1.2 + s.level * 1.4 + Math.sqrt(s.pop) * 0.35;
    const a = Math.min(1, 0.25 + s.level * 0.15 + s.pop / 200);
    const rg = g.createRadialGradient(x, y, 0, x, y, r * 3);
    rg.addColorStop(0, `rgba(255,214,140,${a})`); rg.addColorStop(0.35, `rgba(255,170,80,${a * 0.45})`); rg.addColorStop(1, 'rgba(255,140,40,0)');
    g.fillStyle = rg; g.beginPath(); g.arc(x, y, r * 3, 0, Math.PI * 2); g.fill();
  }
  earth.lightsTex.needsUpdate = true;
}

/** 위경도 → 국가 인덱스 (ID 지도 픽셀 조회) */
export function countryIndexAt(textures, lat, lon) {
  const x = Math.floor((lon + 180) / 360 * textures.IW) % textures.IW, y = Math.min(textures.IH - 1, Math.floor((90 - lat) / 180 * textures.IH));
  const o = (y * textures.IW + x) * 4;
  return textures.idData[o] + (textures.idData[o + 1] << 8) - 1;
}
