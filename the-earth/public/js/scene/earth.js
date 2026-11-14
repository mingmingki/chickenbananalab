// 지구: 땅과 바다를 우리가 직접 그린다 (사진을 붙이지 않는다).
//   · 해안선은 나라 다각형에 프랙탈 무늬를 더해, 확대할수록 더 세밀하고 날카롭다
//   · 숲·초원·사막·툰드라·바위산·만년설·계절 눈은 지형 자료(scripts/make-terrain.py)에 따라 칠하고,
//     가까이 갈수록 숲 덩어리·모래 물결·산 주름 같은 잔무늬가 끝없이 생긴다
//   · 바다는 깊이에 따라 색이 다르고, 가까이 가면 해안의 얕은 물빛과 물거품, 잔물결 반짝임이 보인다
//   · 낮/밤, 실제 구름, 앤들이 세운 마을의 불빛, 대기
import * as THREE from 'three';
import { R, latLonSphere, drawCountry } from './geo.js';

const LOGV = '#include <common>\n#include <logdepthbuf_pars_vertex>\n';
const LOGF = '#include <logdepthbuf_pars_fragment>\n';

const PHONE_MEM = () => Math.min(screen.width, screen.height) <= 540;

/** 나라 ID 지도(선택용), 땅 모양(R)·고른 나라(G)·내 나라(B) 그림 */
export function buildEarthTextures(countries) {
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

  const idTex = new THREE.CanvasTexture(idc);
  idTex.magFilter = idTex.minFilter = THREE.NearestFilter;
  idTex.generateMipmaps = false;
  return { idTex, idData, IW, IH, ovTex, paintOverlay };
}

const TL = new THREE.TextureLoader();
const loadTex = (f, base = '') => new Promise((res, rej) => TL.load(`${base}/textures/earth/${f}`, t => {
  t.wrapS = THREE.RepeatWrapping; t.anisotropy = 8;
  res(t);
}, undefined, rej));
/** 지형 자료 (높이·물·깊이 / 식생·건조함·얼음 / 계절 눈·붉은 정도) + 구름. 실패하면 null */
export function loadEarthData(base = '') {
  return Promise.all(['aux-2k.png', 'terrain-2k.png', 'climate-1k.png', 'clouds-2k.jpg'].map(f => loadTex(f, base)))
    .then(([aux, terrain, climate, clouds]) => ({ aux, terrain, climate, clouds }))
    .catch(() => null);
}

const BLACK = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1); BLACK.needsUpdate = true;
// 자료가 오기 전: 높이 0 · 물 0 · 얕은 바다 / 초원 정도
const AUX0 = new THREE.DataTexture(new Uint8Array([0, 0, 40, 255]), 1, 1); AUX0.needsUpdate = true;
const TERR0 = new THREE.DataTexture(new Uint8Array([110, 50, 0, 255]), 1, 1); TERR0.needsUpdate = true;
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
    auxMap: { value: AUX0 }, terrMap: { value: TERR0 }, climMap: { value: BLACK }, cloudMap: { value: BLACK },
    idMap: { value: textures.idTex }, lightsMap: { value: lightsTex }, ovMap: { value: textures.ovTex },
    sunDir: { value: new THREE.Vector3(1, 0, 0) }, hoverId: { value: -1 }, myId: { value: -1 }, lightsFade: { value: 1 },
    season: { value: 0.5 },        // 0 = 북반구 한여름(7월), 1 = 북반구 한겨울(1월)
    imagery: { value: 0 },         // 지형 자료가 들어왔나 (0 → 1 로 서서히)
    relief: { value: 1 },          // 지형 음영 세기
    borderAlpha: { value: 0 },     // (국경선은 이제 선으로 그린다: vectors.js)
    cloudShift: { value: 0 },      // 구름이 흘러간 정도 (구름 그림자와 같이)
    cloudAmt: { value: 0.85 },
    time: { value: 0 },
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
      uniform sampler2D auxMap; uniform sampler2D terrMap; uniform sampler2D climMap; uniform sampler2D cloudMap;
      uniform sampler2D idMap; uniform sampler2D lightsMap; uniform sampler2D ovMap;
      uniform vec3 sunDir; uniform float hoverId; uniform float myId; uniform float lightsFade;
      uniform float season; uniform float imagery; uniform float relief; uniform float cloudShift; uniform float cloudAmt; uniform float time;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      ${NOISE_GLSL}
      // 프랙탈 잡음 세 가지. 단계마다 무늬를 돌려(M3) 격자 결이 안 보이게 하고,
      // 화면 한 점(px km)보다 잘아지는 단계는 서서히 지워서 확대·축소할 때 이음매가 생기지 않게 한다.
      const mat3 M3 = mat3(0.00, 0.80, 0.60, -0.80, 0.36, -0.48, -0.60, -0.48, 0.64);
      float lodW(float s, float px) { return clamp(s / px * 0.5 - 1.0, 0.0, 1.0); }   // 2px 에서 0, 4px 에서 1
      // 해안선·높이 흔들기: s0 크기부터 잘게
      float fbmTo(vec3 p, float s0, float px) {
        vec3 q = p / s0; float s = s0, a = 0.5, v = 0.0;
        for (int i = 0; i < 10; i++) {
          float w = lodW(s, px);
          if (w <= 0.0) break;
          v += a * w * (noise3(q) - 0.5);
          q = M3 * q * 2.0 + 1.7; s *= 0.5; a *= 0.62;
        }
        return v;
      }
      // 언덕 높이: 단계마다 크기와 높이를 함께 절반으로 — 어느 배율에서 봐도 비탈이 비슷하다
      float hillFbm(vec3 p, float s0, float px) {
        vec3 q = p / s0; float s = s0, a = 0.5, v = 0.0;
        for (int i = 0; i < 10; i++) {
          float w = lodW(s, px * 1.5);
          if (w <= 0.0) break;
          v += a * w * (noise3(q) - 0.5);
          q = M3 * q * 2.0 + 3.1; s *= 0.5; a *= 0.5;
        }
        return v;
      }
      // 산줄기: 잡음이 0.5 를 지나는 곳마다 뾰족한 능선이 선다 (굵은 능선이 잔 능선을 이끈다)
      float ridgeFbm(vec3 p, float s0, float px) {
        vec3 q = p / s0; float s = s0, a = 0.5, v = 0.0, g = 1.0;
        for (int i = 0; i < 10; i++) {
          float w = lodW(s, px * 1.5);
          if (w <= 0.0) break;
          float nz = noise3(q);
          if (i < 4) {
            float d = nz * 2.0 - 1.0;
            float r = 1.0 - sqrt(d * d + 0.02);     // 굵은 능선 (네 단계) — 꼭대기는 살짝 둥글게
            v += a * w * (r - 0.62) * g * 1.3;
            g = clamp(0.55 + r * 0.6, 0.0, 1.0);
          } else {
            v += a * w * (nz - 0.5) * 0.9;          // 잔주름은 둥글게 (곰보 자국이 생기지 않게)
          }
          q = M3 * q * 2.0 + 5.3; s *= 0.5; a *= 0.5;
        }
        return v;
      }
      // 화면 한 점보다 잘아지는 무늬는 평균값(0.5)으로 녹인다 — 멀리서 지글거리지 않게
      float nl(vec3 p, float s, float px) { return mix(0.5, noise3(p / s), smoothstep(px * 1.5, px * 5.0, s)); }
      void main() {
        #include <logdepthbuf_fragment>
        vec3 n = normalize(vN);
        float ndl = dot(n, sunDir);
        float day = smoothstep(-0.15, 0.2, ndl);
        float camD = length(cameraPosition - vW);
        float px = max(length(fwidth(vW)), 1e-4);          // 이 점 하나가 덮는 땅의 크기 (km)
        vec4 aux = texture2D(auxMap, vUv);                  // 높이 · 물 · 깊이(호수=1)
        vec3 ter = texture2D(terrMap, vUv).rgb;             // 식생 · 건조함 · 얼음
        vec3 cli = texture2D(climMap, vUv).rgb;             // 계절 눈 · 붉은 정도
        vec3 ov = texture2D(ovMap, vUv).rgb;                // 땅 모양 · 고른 나라 · 내 나라
        float lat = (vUv.y - 0.5) * 180.0;

        // ── 해안선: 나라 다각형의 부드러운 경계 + 프랙탈 ──
        float lf = aux.b - 0.86 + fbmTo(vW + 17.0, 12.0, px) * 0.35;
        float lake = smoothstep(-max(fwidth(lf), 1e-3), max(fwidth(lf), 1e-3), lf) * imagery;
        float base = clamp(ov.r - lake, 0.0, 1.0);
        float f = base - 0.5 + fbmTo(vW, 48.0, px) * 0.8;
        float aa = max(fwidth(f), 1e-4);
        float land = smoothstep(-aa, aa, f);

        // ── 땅 ──
        float h = aux.r;
        float veg = ter.r, dry = ter.g, ice = ter.b, snow = cli.r, red = cli.g;
        float m1 = noise3(vW / 220.0), m2 = noise3(vW / 55.0);
        vec3 forest = vec3(0.085, 0.215, 0.085), grass = vec3(0.30, 0.47, 0.18), dryG = vec3(0.62, 0.57, 0.33);
        vec3 sand = vec3(0.86, 0.76, 0.54), ochre = vec3(0.72, 0.43, 0.23), rock = vec3(0.47, 0.43, 0.39);
        vec3 tundra = vec3(0.50, 0.52, 0.41), iceC = vec3(0.92, 0.95, 0.99);
        vec3 desert = mix(sand, ochre, smoothstep(0.15, 0.85, red));
        vec3 dryCol = mix(dryG, desert, smoothstep(0.3, 0.8, dry + (m2 - 0.5) * 0.2));
        vec3 vegCol = mix(grass, forest, smoothstep(0.42, 0.85, veg + (m2 - 0.5) * 0.22));
        vec3 col = mix(dryCol, vegCol, smoothstep(0.16, 0.5, veg + (m1 - 0.5) * 0.14));
        col = mix(col, tundra, smoothstep(56.0, 70.0, abs(lat)) * (1.0 - smoothstep(0.55, 0.9, veg)) * 0.75);
        float hd = h + fbmTo(vW, 30.0, px) * 0.16 * smoothstep(0.04, 0.25, h);
        col = mix(col, rock, smoothstep(0.18, 0.45, hd) * 0.8);
        col = mix(col, vec3(0.60, 0.53, 0.41), smoothstep(0.5, 0.62, h) * (1.0 - veg) * 0.6);   // 높은 고원은 누런 흙빛
        // 만년설: 적도 쪽은 아주 높은 봉우리만, 극지방으로 갈수록 낮은 산까지
        float snowLine = mix(0.9, 0.22, smoothstep(25.0, 60.0, abs(lat)));
        col = mix(col, iceC, clamp(max(ice, smoothstep(snowLine, snowLine + 0.08, hd)), 0.0, 1.0));
        // 계절 눈: 북반구는 1월, 남반구는 7월에 하얗다
        float winter = lat > 0.0 ? season : 1.0 - season;
        col = mix(col, vec3(0.88, 0.91, 0.95), snow * smoothstep(0.15, 0.85, winter) * 0.85);
        // 가까이 갈수록 생기는 잔무늬: 숲과 들판이 또렷한 경계로 나뉘고, 사막엔 모래 물결
        float near = smoothstep(320.0, 15.0, camD);
        float d1 = nl(vW, 7.0, px);
        col *= 1.0 + (m2 - 0.5) * 0.16 + (d1 - 0.5) * 0.14;
        near *= 1.0 - smoothstep(0.12, 0.32, hd);      // 산비탈에는 들판 무늬를 깔지 않는다
        if (near > 0.0) {
          float pn = nl(vW, 3.2, px) * 0.65 + nl(vW, 0.9, px) * 0.35;
          float pw = max(fwidth(pn) * 2.0, 0.07);
          float woods = smoothstep(0.52 - pw, 0.52 + pw, pn + (veg - 0.55) * 0.5);       // 숲 덩어리
          float field = smoothstep(0.6 - pw, 0.6 + pw, nl(vW + 3.1, 1.4, px) * 0.7 + nl(vW, 0.45, px) * 0.3 + (0.4 - abs(veg - 0.45)) * 0.4);  // 밝은 풀밭
          vec3 cw = mix(col, forest * 0.95, woods * smoothstep(0.25, 0.6, veg) * 0.5);
          cw = mix(cw, mix(col, vec3(0.42, 0.55, 0.26), 0.45), field * (1.0 - woods) * smoothstep(0.15, 0.4, veg) * (1.0 - smoothstep(0.8, 0.95, veg)) * 0.6);
          float d3 = nl(vW, 0.35, px) * 0.6 + nl(vW, 0.12, px) * 0.4;
          cw *= 1.0 + (d3 - 0.5) * 0.26 * smoothstep(0.3, 0.8, veg);
          float dune = sin(dot(vW, vec3(0.83, 0.31, 0.47)) * 1.7 + d1 * 7.0) * 0.5 + 0.5;
          cw *= 1.0 + (dune - 0.5) * 0.2 * smoothstep(0.45, 0.9, dry) * (1.0 - veg);
          col = mix(col, cw, near);
        }
        // 바닷가 모래 (가까이서만)
        float beach = (1.0 - smoothstep(0.0, max(aa * 3.0, 0.02), f)) * smoothstep(1.5, 0.15, px) * (1.0 - ice);
        col = mix(col, vec3(0.86, 0.80, 0.63), beach * 0.75);

        // ── 바다·호수 ──
        float depth = clamp(aux.b / 0.6, 0.0, 1.0);
        float coastW = smoothstep(-0.3, 0.0, f);
        vec3 wcol = mix(vec3(0.035, 0.16, 0.32), vec3(0.012, 0.06, 0.16), smoothstep(0.3, 0.9, depth));
        wcol = mix(wcol, vec3(0.06, 0.30, 0.45), smoothstep(0.35, 0.05, depth) * 0.85);
        wcol = mix(wcol, vec3(0.16, 0.52, 0.58), coastW * 0.6);
        wcol = mix(wcol, vec3(0.05, 0.24, 0.36), lake);
        wcol *= 1.0 + (m2 - 0.5) * 0.12;
        wcol = mix(wcol, iceC * 0.94, ice * (1.0 - lake) * 0.85);   // 극지방의 바다 얼음
        float foam = smoothstep(-max(aa * 4.0, 0.03), 0.0, f) * smoothstep(1.2, 0.12, px);
        wcol = mix(wcol, vec3(0.86, 0.93, 0.96), foam * 0.55);

        vec3 c0 = mix(wcol, col, land);
        // 나라 강조: 살짝 밝게
        vec4 idc = texture2D(idMap, vUv);
        float id = floor(idc.r * 255.0 + 0.5) + floor(idc.g * 255.0 + 0.5) * 256.0;
        if (hoverId > 0.0 && abs(id - hoverId) < 0.5) c0 *= 1.08;
        c0 *= 1.0 + ov.g * 0.1;
        // 구름 그림자
        float cl = texture2D(cloudMap, vUv + vec2(cloudShift, 0.0)).r;
        c0 *= 1.0 - smoothstep(0.25, 0.85, cl) * 0.26 * cloudAmt * imagery;

        // ── 빛: 높이 지도로 산맥이 도드라지고, 가까이선 산 주름이 햇빛에 그늘진다 ──
        vec3 east = cross(vec3(0.0, 1.0, 0.0), n);
        east = length(east) < 1e-4 ? vec3(1.0, 0.0, 0.0) : normalize(east);
        vec3 north = cross(n, east);
        vec2 tx = vec2(1.0 / 2048.0, 1.0 / 1024.0);
        float hx = texture2D(auxMap, vUv + vec2(tx.x, 0.0)).r - texture2D(auxMap, vUv - vec2(tx.x, 0.0)).r;
        float hy = texture2D(auxMap, vUv + vec2(0.0, tx.y)).r - texture2D(auxMap, vUv - vec2(0.0, tx.y)).r;
        vec3 nb = normalize(n - (east * hx + north * hy) * relief * 5.0 * imagery * land);
        float ndlb = dot(nb, sunDir);
        // 언덕·산 주름: 프랙탈 높이를 화면 기울기로 법선에 얹는다 (가볍고, 확대할수록 세밀하다)
        // (해안에서 높이가 뚝 끊기면 검은 테두리가 생기므로 land 는 곱하지 않는다 — 물에서는 어차피 안 쓴다)
        float mtnK = smoothstep(0.08, 0.4, h);
        float hillAmp = (0.5 + mtnK * 1.0) * smoothstep(4000.0, 60.0, camD) * (1.0 - ice * 0.6) * step(0.001, land + smoothstep(-0.3, 0.0, f));
        if (hillAmp > 0.0) {
          float hill = mix(hillFbm(vW, 40.0, px), ridgeFbm(vW, 60.0, px), mtnK) * hillAmp * 9.6;
          vec3 dp1 = dFdx(vW), dp2 = dFdy(vW);
          float dh1 = dFdx(hill), dh2 = dFdy(hill);
          vec3 r1 = cross(dp2, nb), r2 = cross(nb, dp1);
          float det = dot(dp1, r1);
          if (abs(det) > 1e-12) nb = normalize(abs(det) * nb - sign(det) * (dh1 * r1 + dh2 * r2));
          ndlb = dot(nb, sunDir);
        }
        float lightK = max(mix(ndl, ndlb, land), 0.0);
        vec3 V = normalize(cameraPosition - vW);
        vec3 lit = c0 * (0.04 + land * 0.07 + lightK * 1.0);   // 땅은 하늘빛이 그늘을 조금 밝힌다
        // 바다: 햇빛 반짝임 (가까이선 잔물결이 반짝인다) + 비스듬히 보면 하늘빛
        float rip = smoothstep(800.0, 30.0, camD);
        vec3 wn = normalize(n + (east * (nl(vW + vec3(0.0, 0.0, time * 0.36), 0.9, px) - 0.5) + north * (nl(vW + vec3(5.0, time * 0.36, 0.0), 0.9, px) - 0.5)) * 0.18 * rip);
        vec3 Hh = normalize(sunDir + V);
        float spec = pow(max(dot(wn, Hh), 0.0), mix(110.0, 260.0, rip)) * (1.0 - land) * (1.0 - ice) * day * 0.4;
        float fres = pow(1.0 - max(dot(n, V), 0.0), 3.0) * (1.0 - land);
        lit += vec3(0.25, 0.45, 0.7) * fres * 0.25 * day;
        vec3 lights = texture2D(lightsMap, vUv).rgb;
        vec3 night = c0 * vec3(0.02, 0.025, 0.04) + lights * 1.8 * land * lightsFade;
        vec3 c = mix(night, lit, day) + spec * vec3(1.0, 0.93, 0.78);
        float rim = pow(1.0 - max(dot(n, V), 0.0), 4.0);
        c += vec3(0.25, 0.5, 1.0) * rim * 0.55 * smoothstep(-0.35, 0.35, ndl);
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
    /** 지형 자료를 받으면 끼운다 */
    setData(d) {
      uniforms.auxMap.value = d.aux; uniforms.terrMap.value = d.terrain; uniforms.climMap.value = d.climate; uniforms.cloudMap.value = d.clouds;
      this.imageryOn = true;
    },
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
