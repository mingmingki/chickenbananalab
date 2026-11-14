// 지구: NASA 위성 사진(블루 마블, 퍼블릭 도메인)으로 그린 실제 지구 + 계절(겨울엔 북반구가 하얗게) + 지형 음영 + 실제 구름
// + 나라 경계선 + 낮/밤 셰이더 + 앤들이 세운 마을의 불빛 + 대기.
// 사진을 못 불러오면 예전처럼 국경 다각형으로 그린 지도로 대신한다.
import * as THREE from 'three';
import { R, latLonSphere, drawCountry, traceRings } from './geo.js';

const LOGV = '#include <common>\n#include <logdepthbuf_pars_vertex>\n';
const LOGF = '#include <logdepthbuf_pars_fragment>\n';

// 사막·빙하·열대우림 (경도1, 경도2, 위도1, 위도2, 색, 세기)
const REGIONS = [
  [-17, 35, 14, 31, '#d9b77a', 0.95], [34, 60, 13, 32, '#d6b073', 0.9], [48, 63, 26, 37, '#c9a777', 0.6], [68, 76, 23, 29, '#ccaa72', 0.6],
  [75, 92, 36, 42, '#d2b67f', 0.8], [92, 115, 38, 46, '#c8b088', 0.7], [116, 142, -32, -19, '#cf8f55', 0.85], [-118, -104, 29, 38, '#c99b68', 0.6],
  [12, 27, -29, -19, '#c9a46a', 0.6], [-72, -68, -28, -18, '#c5a27a', 0.5], [55, 70, 38, 46, '#c7ae80', 0.55],
  [-75, -48, -12, 4, '#2f6b2a', 0.55], [10, 30, -6, 5, '#2f6b2a', 0.5], [95, 150, -8, 8, '#2e6a33', 0.4],
  [-56, -18, 59, 84, '#eef3f6', 1.0], [-180, 180, -91, -62, '#eef2f5', 1.0], [-95, -60, 72, 84, '#e6edf0', 0.6],
  // 산맥과 고원
  [80, 101, 29, 38, '#9a8566', 0.75], [74, 96, 26.5, 32.5, '#e9e7e3', 0.65], [-77, -66, -48, 6, '#8d7b62', 0.55], [-123, -104, 36, 60, '#7d7360', 0.45],
  [5.5, 15.5, 44.8, 47.8, '#d9d9d6', 0.55], [39, 48.5, 41, 44, '#cfcfcb', 0.4], [35.5, 42, 6, 14, '#7e6b4f', 0.4], [56, 61, 50, 67, '#6e6a55', 0.3],
  [5, 19, 59, 70, '#8a8c84', 0.35], [127.8, 129.8, 35.8, 39.5, '#5d6a48', 0.35], [136, 139.5, 35, 37.5, '#6f7562', 0.35], [-125, -117, 40, 50, '#6c7258', 0.3],
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

const PHONE_MEM = () => Math.min(screen.width, screen.height) <= 540;

/** 나라 ID 지도(선택용), 나라 강조 오버레이, 국경선 */
export function buildEarthTextures(countries, maxTex, borders = null) {
  const phone = PHONE_MEM();
  // 1) 국가 ID 지도 (선택/강조용)
  const IW = phone ? 2048 : 4096, IH = IW / 2;
  const idc = document.createElement('canvas'); idc.width = IW; idc.height = IH;
  const ig = idc.getContext('2d', { willReadFrequently: true });
  ig.fillStyle = '#000'; ig.fillRect(0, 0, IW, IH);
  countries.forEach((c, i) => {
    const id = i + 1;
    ig.fillStyle = `rgb(${id & 255},${id >> 8},0)`;
    ig.beginPath(); drawCountry(ig, c, IW, IH); ig.fill('evenodd');
  });
  const idData = ig.getImageData(0, 0, IW, IH).data;

  // 2) 부드러운 마스크: R=육지, G=선택한 나라, B=내 나라 (선형 보간으로 해안선이 매끈하게)
  const ov = document.createElement('canvas'); ov.width = phone ? 2048 : 4096; ov.height = ov.width / 2;
  const maskS = document.createElement('canvas'); maskS.width = ov.width; maskS.height = ov.height;
  const mg = maskS.getContext('2d');
  mg.fillStyle = '#fff';
  for (const c of countries) { mg.beginPath(); drawCountry(mg, c, ov.width, ov.height); mg.fill('evenodd'); }
  const ovTex = new THREE.CanvasTexture(ov);
  ovTex.generateMipmaps = true; ovTex.minFilter = THREE.LinearMipmapLinearFilter; ovTex.magFilter = THREE.LinearFilter;
  const paintOverlay = (selIdx = -1, myIdx = -1) => {
    const g = ov.getContext('2d');
    g.globalCompositeOperation = 'source-over';
    g.fillStyle = '#000'; g.fillRect(0, 0, ov.width, ov.height);
    g.drawImage(maskS, 0, 0);
    g.globalCompositeOperation = 'multiply'; g.fillStyle = '#ff0000'; g.fillRect(0, 0, ov.width, ov.height);
    g.globalCompositeOperation = 'lighter';
    if (selIdx >= 0) { g.fillStyle = '#00ff00'; g.beginPath(); drawCountry(g, countries[selIdx], ov.width, ov.height); g.fill('evenodd'); }
    if (myIdx >= 0) { g.fillStyle = '#0000ff'; g.beginPath(); drawCountry(g, countries[myIdx], ov.width, ov.height); g.fill('evenodd'); }
    g.globalCompositeOperation = 'source-over';
    ovTex.needsUpdate = true;
  };
  paintOverlay();

  // 3) 국경선: 나라와 나라 사이 육지 국경만 (해안선은 사진에 보인다). 서버가 안 주면 나라 외곽선
  const bc = document.createElement('canvas'); bc.width = ov.width; bc.height = ov.height;
  const bg = bc.getContext('2d');
  bg.strokeStyle = '#fff'; bg.lineWidth = Math.max(1, ov.width / 4096 * 1.6); bg.lineJoin = 'round'; bg.lineCap = 'round';
  if (borders?.length) {
    bg.beginPath();
    for (const off of [-bc.width, 0, bc.width]) traceRings(bg, borders, bc.width, bc.height, off, { open: true });
    bg.stroke();
  } else for (const c of countries) { bg.beginPath(); drawCountry(bg, c, bc.width, bc.height); bg.stroke(); }
  const borderTex = new THREE.CanvasTexture(bc);
  borderTex.anisotropy = 4;

  const idTex = new THREE.CanvasTexture(idc);
  idTex.magFilter = idTex.minFilter = THREE.NearestFilter;
  idTex.generateMipmaps = false;
  // 사진을 못 불러왔을 때 쓸 예전 지도 (필요할 때만 만든다)
  const fallbackDay = () => proceduralDay(countries, phone ? 2048 : 4096);
  return { idTex, idData, IW, IH, ovTex, paintOverlay, borderTex, fallbackDay };
}

/** 예전 방식의 지도: 국경 다각형 + 위도별 색 + 사막·산맥 얼룩 */
function proceduralDay(countries, W) {
  const H = W / 2, sx = W / 4096;
  const mask = document.createElement('canvas'); mask.width = W; mask.height = H;
  const mg = mask.getContext('2d');
  mg.fillStyle = '#fff';
  for (const c of countries) { mg.beginPath(); drawCountry(mg, c, W, H); mg.fill('evenodd'); }
  mg.fillRect(0, H * (1 - 12 / 180), W, H);
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
  bg.globalCompositeOperation = 'soft-light';
  bg.drawImage(noiseCanvas(512, 256, 9, 3), 0, 0, W, H);
  bg.globalCompositeOperation = 'destination-in';
  bg.drawImage(mask, 0, 0);
  const day = document.createElement('canvas'); day.width = W; day.height = H;
  const dg = day.getContext('2d');
  const og = dg.createLinearGradient(0, 0, 0, H);
  og.addColorStop(0, '#0a2540'); og.addColorStop(0.5, '#0e3d6e'); og.addColorStop(1, '#0a2540');
  dg.fillStyle = og; dg.fillRect(0, 0, W, H);
  const shelf = document.createElement('canvas'); shelf.width = W; shelf.height = H;
  const sg = shelf.getContext('2d');
  sg.filter = `blur(${Math.round(10 * sx)}px)`; sg.drawImage(mask, 0, 0); sg.filter = 'none';
  sg.globalCompositeOperation = 'source-in'; sg.fillStyle = '#2a7fa8'; sg.fillRect(0, 0, W, H);
  dg.globalAlpha = 0.55; dg.drawImage(shelf, 0, 0); dg.globalAlpha = 1;
  dg.drawImage(bio, 0, 0);
  bio.width = bio.height = 0; shelf.width = shelf.height = 0; mask.width = mask.height = 0;
  const t = new THREE.CanvasTexture(day);
  t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  return t;
}

const TL = new THREE.TextureLoader();
const loadTex = (f, srgb, base = '') => new Promise((res, rej) => TL.load(`${base}/textures/earth/${f}`, t => {
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = THREE.RepeatWrapping; t.anisotropy = 8;
  res(t);
}, undefined, rej));
/** 휴대폰은 메모리가 작아 지금 계절의 4K 사진 한 장만 (계절이 바뀌면 바꿔 끼운다) */
export const singleSeason = maxTex => PHONE_MEM() || maxTex < 4096;
/** NASA 위성 사진 불러오기: 컴퓨터는 겨울·여름 4K 두 장을 섞고, 휴대폰은 지금 계절 한 장. 실패하면 null */
export function loadEarthImagery(maxTex, season = 0.5, base = '') {
  const one = singleSeason(maxTex);
  const days = one ? [loadTex(`day-${season > 0.5 ? 'winter' : 'summer'}-${maxTex >= 4096 ? 4 : 2}k.jpg`, true, base)]
    : [loadTex('day-winter-4k.jpg', true, base), loadTex('day-summer-4k.jpg', true, base)];
  return Promise.all([...days, loadTex('aux-2k.png', false, base), loadTex('clouds-2k.jpg', false, base)])
    .then(r => one ? { winter: r[0], summer: r[0], aux: r[1], clouds: r[2], one: season > 0.5 ? 'winter' : 'summer', big: maxTex >= 4096 } : { winter: r[0], summer: r[1], aux: r[2], clouds: r[3] })
    .catch(() => null);
}

const BLACK = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1); BLACK.needsUpdate = true;
const SEA = new THREE.DataTexture(new Uint8Array([12, 50, 95, 255]), 1, 1); SEA.needsUpdate = true;
const NOISE_GLSL = `
  float hash3(vec3 p){ p = fract(p*0.3183099+.1); p *= 17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
  float noise3(vec3 x){ vec3 i=floor(x); vec3 f=fract(x); f=f*f*(3.0-2.0*f);
    return mix(mix(mix(hash3(i),hash3(i+vec3(1,0,0)),f.x),mix(hash3(i+vec3(0,1,0)),hash3(i+vec3(1,1,0)),f.x),f.y),
               mix(mix(hash3(i+vec3(0,0,1)),hash3(i+vec3(1,0,1)),f.x),mix(hash3(i+vec3(0,1,1)),hash3(i+vec3(1,1,1)),f.x),f.y),f.z); }`;

export function makeEarth(textures) {
  const lightsCanvas = document.createElement('canvas');
  lightsCanvas.width = 2048; lightsCanvas.height = 1024;
  const lightsTex = new THREE.CanvasTexture(lightsCanvas);
  lightsTex.colorSpace = THREE.SRGBColorSpace;

  const uniforms = {
    winterMap: { value: SEA }, summerMap: { value: SEA }, auxMap: { value: BLACK }, cloudMap: { value: BLACK },
    idMap: { value: textures.idTex }, lightsMap: { value: lightsTex }, ovMap: { value: textures.ovTex }, borderMap: { value: textures.borderTex },
    sunDir: { value: new THREE.Vector3(1, 0, 0) }, hoverId: { value: -1 }, myId: { value: -1 }, lightsFade: { value: 1 },
    season: { value: 0.5 },        // 0 = 북반구 한여름(7월), 1 = 북반구 한겨울(1월)
    imagery: { value: 0 },         // 위성 사진이 들어왔나 (0 → 1 로 서서히)
    relief: { value: 1 },          // 지형 음영 세기
    borderAlpha: { value: 0.35 },  // 국경선
    cloudShift: { value: 0 },      // 구름이 흘러간 정도 (구름 그림자와 같이)
    cloudAmt: { value: 0.85 },
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
      uniform sampler2D winterMap; uniform sampler2D summerMap; uniform sampler2D auxMap; uniform sampler2D cloudMap;
      uniform sampler2D idMap; uniform sampler2D lightsMap; uniform sampler2D ovMap; uniform sampler2D borderMap;
      uniform vec3 sunDir; uniform float hoverId; uniform float myId; uniform float lightsFade;
      uniform float season; uniform float imagery; uniform float relief; uniform float borderAlpha; uniform float cloudShift; uniform float cloudAmt;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      ${NOISE_GLSL}
      void main() {
        #include <logdepthbuf_fragment>
        vec3 n = normalize(vN);
        float ndl = dot(n, sunDir);
        float day = smoothstep(-0.15, 0.2, ndl);
        // 계절: 겨울엔 북반구 눈 덮인 사진, 여름엔 눈 녹은 사진 (남반구는 반대 계절이 이미 들어 있다)
        vec3 col = mix(texture2D(summerMap, vUv).rgb, texture2D(winterMap, vUv).rgb, season);
        vec4 aux = texture2D(auxMap, vUv);
        float water = aux.g * imagery;
        vec3 ov = texture2D(ovMap, vUv).rgb;
        float land = mix(ov.r, 1.0 - water, imagery);
        // 지형 음영: 높이 지도의 기울기로 법선을 기울인다 (히말라야·안데스·로키가 햇빛에 도드라진다)
        vec3 east = cross(vec3(0.0, 1.0, 0.0), n);
        east = length(east) < 1e-4 ? vec3(1.0, 0.0, 0.0) : normalize(east);
        vec3 north = cross(n, east);
        vec2 tx = vec2(1.0 / 2048.0, 1.0 / 1024.0);
        float hx = texture2D(auxMap, vUv + vec2(tx.x, 0.0)).r - texture2D(auxMap, vUv - vec2(tx.x, 0.0)).r;
        float hy = texture2D(auxMap, vUv + vec2(0.0, tx.y)).r - texture2D(auxMap, vUv - vec2(0.0, tx.y)).r;
        vec3 nb = normalize(n - (east * hx + north * hy) * relief * 7.0 * imagery * (1.0 - water));
        float ndlb = dot(nb, sunDir);
        // 가까이 내려가면 사진이 흐려 보이지 않게 잔무늬를 섞는다
        float camD = length(cameraPosition - vW);
        // (사진 한 칸이 약 10km라 1,000km 아래에서는 흐려진다 → 몇 km 크기의 들판·숲 얼룩과 물결을 섞는다)
        float detail = smoothstep(1200.0, 80.0, camD) * imagery;
        float nz = noise3(vW * 0.12) * 0.45 + noise3(vW * 0.5) * 0.35 + noise3(vW * 2.2) * 0.2;
        col *= 1.0 + (nz - 0.5) * mix(0.42, 0.12, water) * detail;
        vec4 idc = texture2D(idMap, vUv);
        float id = floor(idc.r * 255.0 + 0.5) + floor(idc.g * 255.0 + 0.5) * 256.0;
        // 나라 강조는 살짝 밝히기만 (테두리는 선으로 또렷하게 그린다 — 색을 칠하면 땅 사진이 가려진다)
        if (hoverId > 0.0 && abs(id - hoverId) < 0.5) col *= 1.08;
        col *= 1.0 + ov.g * 0.1;
        // 국경선
        col = mix(col, vec3(1.0), texture2D(borderMap, vUv).r * borderAlpha);
        // 구름 그림자 (구름 층과 같은 만큼 흘러간 자리)
        float cl = texture2D(cloudMap, vUv + vec2(cloudShift, 0.0)).r;
        col *= 1.0 - smoothstep(0.25, 0.85, cl) * 0.28 * cloudAmt * imagery;
        vec3 V = normalize(cameraPosition - vW);
        vec3 lit = col * (0.035 + max(mix(ndl, ndlb, land), 0.0) * 1.05);
        // 바다: 햇빛 반짝임 + 비스듬히 보면 하늘빛
        vec3 H = normalize(sunDir + V);
        float spec = pow(max(dot(n, H), 0.0), 80.0) * (1.0 - land) * day * 0.8;
        float fres = pow(1.0 - max(dot(n, V), 0.0), 3.0) * (1.0 - land);
        lit += vec3(0.25, 0.45, 0.7) * fres * 0.25 * day;
        vec3 lights = texture2D(lightsMap, vUv).rgb;
        vec3 night = col * vec3(0.02, 0.025, 0.04) + lights * 1.8 * land * lightsFade;
        vec3 c = mix(night, lit, day) + spec * vec3(1.0, 0.92, 0.75);
        float rim = pow(1.0 - max(dot(n, V), 0.0), 4.0);
        c += vec3(0.25, 0.5, 1.0) * rim * 0.55 * smoothstep(-0.35, 0.35, ndl);
        // 노을빛 경계
        c += vec3(0.55, 0.18, 0.05) * smoothstep(0.12, 0.0, abs(ndl)) * 0.05;
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(latLonSphere(R, 512, 256), mat);
  mesh.name = 'earth';

  // 구름: 실제 구름 사진이 천천히 흘러가고, 잔무늬가 조금씩 바뀐다 (사진이 없으면 절차적 구름)
  const cloudU = { sunDir: uniforms.sunDir, time: { value: 0 }, opacity: { value: 0.85 }, cloudMap: uniforms.cloudMap, imagery: uniforms.imagery, cloudShift: uniforms.cloudShift };
  const clouds = new THREE.Mesh(latLonSphere(R * 1.0045, 256, 128), new THREE.ShaderMaterial({
    uniforms: cloudU, transparent: true, depthWrite: false,
    vertexShader: LOGV + `varying vec3 vP; varying vec3 vN; varying vec2 vUv;
      void main(){ vP = normalize(position); vUv = uv; vN = normalize(mat3(modelMatrix)*normal); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0);
      #include <logdepthbuf_vertex>
      }`,
    fragmentShader: LOGF + `uniform vec3 sunDir; uniform float time; uniform float opacity; uniform sampler2D cloudMap; uniform float imagery; uniform float cloudShift;
      varying vec3 vP; varying vec3 vN; varying vec2 vUv;
      ${NOISE_GLSL}
      float fbm(vec3 p){ float v=0.0; float a=0.5; for(int i=0;i<5;i++){ v+=a*noise3(p); p=p*2.03+vec3(1.7,9.2,3.1); a*=0.5; } return v; }
      void main(){
        #include <logdepthbuf_fragment>
        vec3 q = vP*3.2 + vec3(time*0.012, 0.0, time*0.007);
        float lat = vP.y;
        float th = 0.6 - 0.07*cos(lat*7.0) + 0.05*abs(lat);
        float proc = smoothstep(th, th + 0.16, fbm(q + fbm(q*1.7)*0.9)) * 0.92;
        // 사진 구름 + 시간에 따라 변하는 잔무늬 (구름이 살아 움직이게)
        float img = texture2D(cloudMap, vUv + vec2(cloudShift, 0.0)).r;
        float wob = noise3(vP * 18.0 + vec3(time * 0.02)) - 0.5;
        float real = smoothstep(0.12, 0.85, img + wob * 0.18);
        float a = mix(proc, real, imagery);
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
  return {
    group, mesh, clouds, atmo, uniforms, cloudU, lightsCanvas, lightsTex,
    /** 위성 사진을 받으면 끼운다 */
    setImagery(img) {
      uniforms.winterMap.value = img.winter; uniforms.summerMap.value = img.summer;
      uniforms.auxMap.value = img.aux; uniforms.cloudMap.value = img.clouds;
      this.imageryOn = true;
      this.one = img.one || null; this.oneBig = img.big;
    },
    /** 휴대폰: 계절이 반대로 넘어가면 그 계절 사진으로 바꿔 끼운다 */
    followSeason(season) {
      if (!this.one || this.swapping) return;
      const want = season > 0.55 ? 'winter' : season < 0.45 ? 'summer' : this.one;
      if (want === this.one) return;
      this.swapping = true;
      loadTex(`day-${want}-${this.oneBig ? 4 : 2}k.jpg`, true).then(t => {
        const old = uniforms.winterMap.value;
        uniforms.winterMap.value = t; uniforms.summerMap.value = t;
        if (old && old !== t && old.dispose) old.dispose();
        this.one = want;
      }).catch(() => {}).finally(() => { this.swapping = false; });
    },
    useFallback(dayTex) { uniforms.winterMap.value = dayTex; uniforms.summerMap.value = dayTex; uniforms.imagery.value = 0; },
  };
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
