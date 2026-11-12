// 국가 지리 데이터: world-atlas(Natural Earth 1:50m) → 국가별 다각형, 한국어 이름, 문화권, 육지 샘플링
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { feature } from 'topojson-client';
import { geoCentroid, geoContains, geoArea, geoBounds } from 'd3-geo';
import countries from 'i18n-iso-countries';

const require = createRequire(import.meta.url);
countries.registerLocale(require('i18n-iso-countries/langs/ko.json'));

const CULTURE_BY_A2 = {};
const assign = (culture, codes) => codes.split(' ').forEach(c => (CULTURE_BY_A2[c] = culture));
assign('ko', 'KR KP');
assign('ja', 'JP');
assign('zh', 'CN TW HK MO SG');
assign('vi', 'VN LA KH TH MM MY ID PH BN TL');
assign('in', 'IN PK BD LK NP BT MV AF');
assign('ar', 'SA AE QA KW BH OM YE IQ SY JO LB PS IL EG LY TN DZ MA EH SD MR IR TR');
assign('ru', 'RU UA BY KZ UZ TM KG TJ MN AZ AM GE MD');
assign('de', 'DE AT CH LI NL LU DK NO SE FI IS PL CZ SK HU SI EE LV LT');
assign('fr', 'FR BE MC HT');
assign('it', 'IT SM VA MT HR RS BA ME AL MK GR BG RO CY XK');
assign('es', 'ES MX GT HN SV NI CR PA CU DO PR CO VE EC PE BO CL AR UY PY AD');
assign('pt', 'PT BR AO MZ CV GW ST');
assign('af', 'NG GH CI SN ML BF NE TD CM CF CG CD GA GQ KE TZ UG RW BI ET ER DJ SO SS ZA ZW ZM MW BW NA LS SZ MG GM GN SL LR TG BJ KM MU SC');
// 나머지(영미권·오세아니아 등)는 en

let cache = null;

function round2(v) { return Math.round(v * 100) / 100; }

export function loadGeo() {
  if (cache) return cache;
  const topoPath = require.resolve('world-atlas/countries-50m.json');
  const topo = JSON.parse(readFileSync(topoPath, 'utf8'));
  const fc = feature(topo, topo.objects.countries);
  const list = [];
  const byCode = new Map();
  for (const f of fc.features) {
    const nameEn = f.properties?.name || 'Unknown';
    let a2 = f.id != null ? countries.numericToAlpha2(String(f.id).padStart(3, '0')) : undefined;
    if (!a2 && nameEn === 'Kosovo') a2 = 'XK';
    if (!a2 && nameEn === 'N. Cyprus') a2 = 'XC';
    if (!a2 && nameEn === 'Somaliland') a2 = 'XS';
    if (!a2) a2 = 'X' + list.length;
    if (byCode.has(a2)) continue;
    let name = countries.getName(a2, 'ko') || nameEn;
    if (a2 === 'XK') name = '코소보';
    if (a2 === 'XC') name = '북키프로스';
    if (a2 === 'XS') name = '소말릴란드';
    if (a2 === 'KR') name = '대한민국';
    if (a2 === 'KP') name = '북한';
    if (a2 === 'US') name = '미국';
    if (a2 === 'GB') name = '영국';
    if (a2 === 'RU') name = '러시아';
    if (a2 === 'X226') name = '인도양 지역';
    if (a2 === 'X237') name = '시아첸 빙하';
    // 다각형들
    const geom = f.geometry;
    if (!geom) continue;
    const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
    // 가장 큰 다각형 (도시를 배치할 본토)
    let best = null, bestArea = -1;
    const polyFeatures = polys.map(p => ({ type: 'Feature', geometry: { type: 'Polygon', coordinates: p } }));
    for (const pf of polyFeatures) {
      const a = geoArea(pf);
      if (a > bestArea) { bestArea = a; best = pf; }
    }
    let centroid = geoCentroid(best);
    if (!geoContains(best, centroid)) {
      // 섬나라 등: 다각형 내부의 점을 찾는다
      const b = geoBounds(best);
      let x0 = b[0][0], x1 = b[1][0];
      if (x1 < x0) x1 += 360;
      for (let i = 0; i < 400; i++) {
        const p = [x0 + Math.random() * (x1 - x0), b[0][1] + Math.random() * (b[1][1] - b[0][1])];
        if (p[0] > 180) p[0] -= 360;
        if (geoContains(best, p)) { centroid = p; break; }
      }
    }
    const rings = [];
    for (const p of polys) for (const ring of p) {
      const flat = [];
      for (const [lon, lat] of ring) flat.push(round2(lon), round2(lat));
      rings.push(flat);
    }
    const c = {
      code: a2,
      name,
      nameEn,
      culture: CULTURE_BY_A2[a2] || 'en',
      centroid: [round2(centroid[0]), round2(centroid[1])],
      area: geoArea(f),
      mainland: best,
      feature: f,
      rings,
    };
    list.push(c);
    byCode.set(a2, c);
  }
  list.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
  const publicList = list.map(c => ({
    code: c.code, name: c.name, nameEn: c.nameEn, culture: c.culture, centroid: c.centroid, rings: c.rings,
  }));
  const publicJson = JSON.stringify({ countries: publicList });
  cache = { list, byCode, publicJson };
  return cache;
}

export function getCountry(code) {
  return loadGeo().byCode.get(code);
}

/** 국가 본토 안의 무작위 육지 지점 (near가 주어지면 그 근처 maxDeg 이내) */
export function randomLandPoint(code, near = null, minDeg = 0, maxDeg = 6) {
  const c = getCountry(code);
  if (!c) return [0, 0];
  const shape = c.mainland;
  const b = geoBounds(shape);
  for (let i = 0; i < 600; i++) {
    let p;
    if (near) {
      const ang = Math.random() * Math.PI * 2;
      const d = minDeg + Math.random() * (maxDeg - minDeg);
      p = [near[0] + Math.cos(ang) * d / Math.max(0.2, Math.cos(near[1] * Math.PI / 180)), near[1] + Math.sin(ang) * d];
    } else {
      let x0 = b[0][0], x1 = b[1][0];
      if (x1 < x0) x1 += 360; // 날짜변경선을 넘는 나라(러시아, 피지 등)
      p = [x0 + Math.random() * (x1 - x0), b[0][1] + Math.random() * (b[1][1] - b[0][1])];
    }
    if (p[0] > 180) p[0] -= 360;
    if (p[0] < -180) p[0] += 360;
    if (p[1] > 85 || p[1] < -85) continue;
    if (geoContains(shape, p)) return [round2(p[0]), round2(p[1])];
  }
  return near ? [...near] : [...c.centroid];
}

// ── 육지/바다 래스터 (0.05° 격자) : 해안 근처 정착지에 바다를 놓기 위해 ──
const RES = 0.05, RW = Math.round(360 / RES), RH = Math.round(180 / RES);
let land = null;
function buildLandRaster() {
  const { list } = loadGeo();
  const rows = Array.from({ length: RH }, () => []);
  for (const c of list) for (const flat of c.rings) {
    for (let k = 0; k < flat.length; k += 2) {
      const k2 = (k + 2) % flat.length;
      const x1 = flat[k], y1 = flat[k + 1], x2 = flat[k2], y2 = flat[k2 + 1];
      if (Math.abs(x2 - x1) > 180) continue; // 날짜변경선에서 잘린 변
      const r1 = (90 - y1) / RES - 0.5, r2 = (90 - y2) / RES - 0.5;
      const lo = Math.ceil(Math.min(r1, r2)), hi = Math.floor(Math.max(r1, r2));
      if (r1 === r2) continue;
      for (let r = Math.max(0, lo); r <= Math.min(RH - 1, hi); r++) {
        if ((r === hi && r === Math.max(r1, r2))) continue; // 꼭짓점 중복 방지
        const t = (r - r1) / (r2 - r1);
        rows[r].push((x1 + (x2 - x1) * t + 180) / RES);
      }
    }
  }
  land = new Uint8Array(RW * RH);
  for (let r = 0; r < RH; r++) {
    const xs = rows[r].sort((a, b) => a - b);
    for (let i = 0; i + 1 < xs.length; i += 2) {
      const a = Math.max(0, Math.ceil(xs[i] - 0.5)), b = Math.min(RW - 1, Math.floor(xs[i + 1] - 0.5));
      land.fill(1, r * RW + a, r * RW + b + 1);
    }
  }
  // 남극 끝
  land.fill(1, Math.round((90 + 78) / RES) * RW);
}
export function isLand(lon, lat) {
  if (!land) buildLandRaster();
  let x = Math.floor((lon + 180) / RES), y = Math.floor((90 - lat) / RES);
  x = ((x % RW) + RW) % RW; y = Math.min(RH - 1, Math.max(0, y));
  return land[y * RW + x] === 1;
}
/** 주변 바다 방향: 16방향 × 여러 거리에서 바다를 찾는다. 없으면 null */
export function seaNear(lon, lat, maxKm = 30) {
  const kmLon = 111.32 * Math.cos(lat * Math.PI / 180), kmLat = 110.57;
  let sx = 0, sz = 0, nearest = Infinity, hits = 0;
  for (let i = 0; i < 16; i++) {
    const ang = i / 16 * Math.PI * 2; // 0 = 동쪽, π/2 = 남쪽 (로컬 x=동, z=남)
    for (const km of [3, 6, 10, 16, 23, maxKm]) {
      const lo = lon + Math.cos(ang) * km / kmLon, la = lat - Math.sin(ang) * km / kmLat;
      if (!isLand(lo, la)) { sx += Math.cos(ang) / km; sz += Math.sin(ang) / km; hits++; nearest = Math.min(nearest, km); break; }
    }
  }
  if (!hits) return null;
  return { angle: Math.atan2(sz, sx), km: nearest, open: hits / 16 };
}
