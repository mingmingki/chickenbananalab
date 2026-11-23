// 사람 모형: 다리·팔·손·몸통·목·머리(눈·코)·머리 모양·수염·모자·가방·우산·지팡이.
// 걸으면 팔다리를 흔들고, 뛰면 크게. 나이에 따라 몸의 비율이 다르다:
//   아기는 앉아 있거나 기어 다니고, 아이는 머리가 크고 다리가 짧고 깡충깡충 걷고,
//   어른은 체격이 저마다 다르고(마른 사람·통통한 사람·어깨 넓은 사람), 어르신은 머리가 세고 등이 굽고 지팡이를 짚는다.
// 옷은 시대(원시 가죽옷 → 한복 → 양복·교복 → 오늘날 옷 → 우주 시대)·계절(여름 반팔·반바지, 겨울 외투·털모자)·
// 성별·나이·직업(경찰·소방관·군인·의사 가운·요리사 모자·현장 안전모·회사원 정장)에 따라 입는다. 비 오면 우산.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const HIP = 0.88, SHOULDER = 1.4, HEAD_Y = 1.585;
const paint = (g, c = [1, 1, 1]) => {
  const n = g.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c[0]; a[i * 3 + 1] = c[1]; a[i * 3 + 2] = c[2]; }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return g;
};
const merge = gs => mergeGeometries(gs.map(g => (g.attributes.color ? g : paint(g))));
const DARK = [0.22, 0.22, 0.22], EYE = [0.08, 0.07, 0.07];
const WHITE = new THREE.Color(1, 1, 1);

let GEO = null;
function geos() {
  if (GEO) return GEO;
  const sph = (r, ws, hs, t0 = 0, tl = Math.PI) => new THREE.SphereGeometry(r, ws, hs, 0, Math.PI * 2, t0, tl);
  const hairCap = () => sph(0.117, 16, 10, 0, Math.PI * 0.52).scale(0.95, 1.08, 1.04).translate(0, HEAD_Y + 0.012, -0.008);
  GEO = {
    leg: merge([new THREE.CylinderGeometry(0.068, 0.05, 0.84, 8).translate(0, -0.42, 0), paint(new THREE.BoxGeometry(0.095, 0.075, 0.22).translate(0, -0.85, 0.035), DARK)]),
    thigh: new THREE.CylinderGeometry(0.082, 0.077, 0.3, 8).translate(0, -0.13, 0),
    torso: merge([new THREE.CylinderGeometry(0.175, 0.15, 0.56, 12).scale(1, 1, 0.62).translate(0, HIP + 0.26, 0), sph(0.175, 12, 6, 0, Math.PI * 0.5).scale(1, 0.32, 0.62).translate(0, HIP + 0.54, 0)]),
    coat: merge([new THREE.CylinderGeometry(0.183, 0.215, 0.86, 12).scale(1, 1, 0.68).translate(0, SHOULDER - 0.43 + 0.01, 0), sph(0.183, 12, 6, 0, Math.PI * 0.5).scale(1, 0.3, 0.68).translate(0, SHOULDER + 0.01, 0)]),
    robe: merge([new THREE.CylinderGeometry(0.183, 0.28, 1.12, 14).scale(1, 1, 0.72).translate(0, SHOULDER - 0.56 + 0.01, 0), sph(0.183, 12, 6, 0, Math.PI * 0.5).scale(1, 0.3, 0.72).translate(0, SHOULDER + 0.01, 0)]),
    skirtS: new THREE.CylinderGeometry(0.165, 0.25, 0.42, 14, 1, true).scale(1, 1, 0.78).translate(0, 0.71, 0),
    skirtL: new THREE.CylinderGeometry(0.165, 0.34, 1.24, 16, 1, true).scale(1, 1, 0.82).translate(0, 0.68, 0),
    arm: new THREE.CylinderGeometry(0.048, 0.038, 0.6, 8).translate(0, -0.3, 0),
    sleeve: new THREE.CylinderGeometry(0.062, 0.057, 0.2, 8).translate(0, -0.08, 0),
    hand: sph(0.046, 8, 6).scale(0.85, 1.15, 0.7).translate(0, -0.63, 0),
    head: merge([
      sph(0.108, 16, 12).scale(0.92, 1.1, 1).translate(0, HEAD_Y, 0),
      new THREE.CylinderGeometry(0.048, 0.054, 0.13, 8).translate(0, 1.45, 0),
      paint(new THREE.BoxGeometry(0.022, 0.035, 0.03).translate(0, HEAD_Y - 0.012, 0.104), [0.92, 0.85, 0.82]),
      paint(sph(0.013, 6, 4).translate(-0.036, HEAD_Y + 0.018, 0.093), EYE),
      paint(sph(0.013, 6, 4).translate(0.036, HEAD_Y + 0.018, 0.093), EYE),
    ]),
    hair: {
      short: hairCap(),
      long: merge([hairCap(), new THREE.BoxGeometry(0.2, 0.32, 0.05).translate(0, HEAD_Y - 0.13, -0.088)]),
      bob: sph(0.121, 16, 10, 0, Math.PI * 0.68).scale(0.96, 1.06, 1.04).translate(0, HEAD_Y + 0.004, -0.012),
      bun: merge([hairCap(), sph(0.055, 10, 8).translate(0, HEAD_Y + 0.07, -0.105)]),
      pony: merge([hairCap(), new THREE.CylinderGeometry(0.032, 0.014, 0.26, 6).rotateX(0.35).translate(0, HEAD_Y - 0.07, -0.15)]),
    },
    hat: {
      cap: merge([new THREE.CylinderGeometry(0.112, 0.12, 0.075, 12).translate(0, HEAD_Y + 0.08, 0), new THREE.BoxGeometry(0.17, 0.014, 0.1).translate(0, HEAD_Y + 0.048, 0.1)]),
      hard: merge([sph(0.13, 14, 8, 0, Math.PI * 0.5).translate(0, HEAD_Y + 0.025, 0), new THREE.CylinderGeometry(0.152, 0.152, 0.014, 14).translate(0, HEAD_Y + 0.025, 0.012)]),
      gat: merge([new THREE.CylinderGeometry(0.27, 0.27, 0.008, 22).translate(0, HEAD_Y + 0.085, 0), new THREE.CylinderGeometry(0.072, 0.085, 0.15, 12).translate(0, HEAD_Y + 0.16, 0)]),
      beanie: sph(0.124, 14, 8, 0, Math.PI * 0.56).scale(1, 1.18, 1).translate(0, HEAD_Y + 0.012, 0),
      straw: new THREE.CylinderGeometry(0.025, 0.25, 0.11, 18).translate(0, HEAD_Y + 0.115, 0),
      toque: new THREE.CylinderGeometry(0.115, 0.1, 0.21, 12).translate(0, HEAD_Y + 0.18, 0),
    },
    pack: new THREE.BoxGeometry(0.27, 0.33, 0.12).translate(0, 1.13, -0.15),
    // 수염: 짧은 턱수염 · 콧수염 · 길게 늘어뜨린 수염 (머리카락 색)
    beard: {
      short: sph(0.078, 10, 8, Math.PI * 0.45, Math.PI * 0.55).scale(1.05, 0.95, 0.9).translate(0, HEAD_Y - 0.01, 0.012),
      mous: new THREE.BoxGeometry(0.07, 0.014, 0.02).translate(0, HEAD_Y - 0.035, 0.1),
      long: merge([new THREE.ConeGeometry(0.05, 0.2, 8).rotateX(Math.PI).translate(0, HEAD_Y - 0.15, 0.075), new THREE.BoxGeometry(0.07, 0.014, 0.02).translate(0, HEAD_Y - 0.035, 0.1)]),
    },
    // 지팡이: 손에서 땅까지
    cane: merge([new THREE.CylinderGeometry(0.014, 0.012, 0.86, 5).translate(0, 0.43, 0), new THREE.CylinderGeometry(0.014, 0.014, 0.12, 5).rotateZ(Math.PI / 2).translate(-0.05, 0.86, 0)]),
    // 우산: 손(0.88m)에서 머리 위 갓(2.08m)까지
    umbrella: merge([new THREE.ConeGeometry(0.6, 0.22, 10, 1, true).translate(0, 2.08, 0), paint(new THREE.CylinderGeometry(0.011, 0.011, 1.2, 4).translate(0, 1.48, 0), DARK)]),
  };
  // 재질이 꼭짓점 색을 쓰므로, 색이 없는 모양엔 흰색을 칠해 둔다 (안 그러면 까맣게 나온다)
  const fill = o => { for (const v of Object.values(o)) if (v.isBufferGeometry) { if (!v.attributes.color) paint(v); } else fill(v); };
  fill(GEO);
  return GEO;
}

// ── 옷 고르기 ──
const hash = (n, k = 1) => { let h = ((n * 2654435761) ^ (k * 40503)) >>> 0; h ^= h >>> 15; h = Math.imul(h, 2246822519) >>> 0; h = (h ^ (h >>> 13)) >>> 0; return (h % 10000) / 10000; };
const P = {
  hide: ['#8b6a45', '#7a5c3a', '#a07c52', '#6e5236', '#94785a'],
  hanM: ['#efe9dc', '#e4dccb', '#d8cfbd', '#f2eee4', '#c9bea8', '#4a5677', '#6b5a45'],
  hanF: ['#f4efe4', '#f2d7d9', '#e9d27a', '#c7dbe9', '#f0e4c8', '#e8c3c8'],
  chima: ['#b83b3b', '#2c3e70', '#d98ca6', '#7a5c8f', '#e8e2d4', '#1f5e5b', '#c25b7a'],
  ind: ['#f2f2ee', '#8a8f96', '#2e3a59', '#5b4a3a', '#d8d2c2', '#6b7b5e', '#3b3b3f'],
  indF: ['#f4e9e1', '#c9d8e6', '#e8c9c9', '#f0e6c8', '#9fb7a5', '#7d8fb3'],
  modern: ['#ffffff', '#1d1d1f', '#2b4c7e', '#c0392b', '#e6b800', '#7f8c8d', '#16a085', '#e67e22', '#8e44ad', '#f5f0e6', '#5d8aa8', '#d35d6e'],
  today: ['#f5f5f2', '#222326', '#bfc6cc', '#e8d9c4', '#9aa59b', '#4a6fa5', '#d97b66', '#2f3e46', '#e0b0b0', '#ffffff', '#6d5d6e', '#c9a66b'],
  space: ['#e8eef3', '#c9d6e0', '#1b2a38', '#59d0ff', '#ff6fb5', '#ffffff', '#9aa8ff'],
  jeans: ['#3b5b8c', '#2c3e50', '#46658f', '#1d1d1f', '#7f8c8d', '#c8b99a', '#4a4a4f'],
  winter: ['#1d1d1f', '#3a3a3c', '#4b5563', '#6b4f3a', '#2e3a59', '#7a2e2e', '#c8bfb0', '#e9e4da', '#455a3e'],
  suit: ['#1f2533', '#2b2f36', '#3b4250', '#45403a'],
  pack: ['#2e3a59', '#c0392b', '#1d1d1f', '#e6b800', '#16a085', '#d35d6e'],
  umbrella: ['#1d1d1f', '#2b4c7e', '#c0392b', '#f1c40f', '#7f8c8d', '#e8e8e8', '#16a085', '#d35d6e'],
  // 아이 옷은 밝고, 어르신 옷은 차분하다
  kid: ['#ff6b6b', '#ffd43b', '#69db7c', '#4dabf7', '#f783ac', '#ff922b', '#9775fa', '#3bc9db', '#a9e34b'],
  kidHan: ['#e84a5f', '#f9c74f', '#90be6d', '#43aa8b', '#f8961e', '#577590', '#f15bb5'],
  elder: ['#6b6256', '#8a7f72', '#4f5a5e', '#7a6a5a', '#5c5045', '#8e8a80', '#3e4a52'],
  cane: ['#6e5236', '#5b4330', '#8a6a48', '#3b3b3f'],
};
const pk = (arr, id, k) => arr[Math.floor(hash(id, k) * arr.length) % arr.length];
const OFFICE = /clerk|office|banker|lawyer|paralegal|cpa|tax_acct|marketer|salesrep|hr|broker|analyst|insurer|realtor|appraiser|planner|programmer|data_sci|ai_eng|designer|architect|official|journalist|pd|editor|copywriter|announcer|store_mgr|ent_mgr|tv_writer/;
const MEDIC = /doctor|nurse|pharmacist|dentist|hygienist|kmd|vet|radiologist|labtech|physio|scientist|chemist|researcher|pharma_res/;
const SITE = /builder|laborer|lumberjack|steelworker|welder|miner|worker|assembler|operator|machinist|engineer|rebar|plasterer|electrician|plumber|crane_op|excavator|site_mgr|civil_eng|forklift|fab_op|semi_eng|painter/;
const COOK = /cook|chef|baker/;
const FARM = /farmer|peasant|rancher|gatherer/;

/**
 * 한 사람의 옷차림. a: 앤 메타 (g 성별, lk 생김새, jk 직업, sc 학교), age, era, season, duty(근무 중), school(수업 중), rain
 */
export function dress(id, a, age, era, season, duty, school, rain) {
  const F = a.g === 'F', col = c => new THREE.Color(c);
  const skin = col(a.lk?.skin || '#e0b896');
  const hc = col(a.lk?.hair || '#222');
  if (age > 50) hc.lerp(new THREE.Color('#c9c9c9'), Math.min(0.9, (age - 50) / 28));
  // 몸의 비율: 아이는 다리·팔이 짧고, 어른은 체격이 저마다 (마른·보통·통통·어깨 넓은), 어르신은 조금 작아진다
  const legK = age < 1.5 ? 0.55 : age < 4 ? 0.58 + age * 0.02 : age < 13 ? 0.64 + (age - 4) * 0.027 : age < 17 ? 0.88 + (age - 13) * 0.03 : 1;
  const bodyR = hash(id, 9);
  const build = age < 8 ? 1.04 + bodyR * 0.1 : age < 13 ? 0.96 + bodyR * 0.1 : age < 18 ? 0.86 + bodyR * 0.12 : bodyR < 0.2 ? 0.86 + bodyR * 0.2 : bodyR < 0.72 ? 0.95 + (bodyR - 0.2) * 0.2 : 1.08 + (bodyR - 0.72) * (age > 32 ? 1.1 : 0.55);
  const wide = (F ? 0.9 : 1) * (!F && age >= 18 && hash(id, 26) < 0.3 ? 1.08 : 1);
  const s = { skin, hairCol: hc, hairStyle: 'short', top: null, bottom: null, torso: 'torso', bot: 'pants', legBare: false, armBare: false, hat: null, hatCol: null, pack: false, packCol: null, umbrella: null, wide, build, legK, armK: 0.78 + legK * 0.22, beard: null, cane: null };
  const summer = season === '여름', winter = season === '겨울';
  // 머리 모양
  if (F) s.hairStyle = age < 13 ? (hash(id, 2) < 0.5 ? 'pony' : 'bob') : age > 55 ? 'bob' : pk(['long', 'long', 'bob', 'pony', 'short'], id, 2);
  else s.hairStyle = age > 58 && hash(id, 3) < 0.35 ? null : 'short';
  // 시대별 기본 옷
  if (era <= 0) {
    s.top = col(pk(P.hide, id, 4)); s.bottom = s.top.clone(); s.torso = 'coat'; s.bot = 'skirtS'; s.legBare = true; s.armBare = true;
    s.hairStyle = F || hash(id, 5) < 0.5 ? 'long' : 'short';
  } else if (era <= 3) {
    if (F) { s.top = col(pk(P.hanF, id, 4)); s.bottom = col(pk(P.chima, id, 6)); s.bot = 'skirtL'; s.hairStyle = age >= 18 ? 'bun' : age >= 8 ? 'pony' : 'short'; }
    else {
      s.top = col(pk(P.hanM, id, 4)); s.bottom = col(pk(P.hanM, id, 6)).lerp(new THREE.Color('#ffffff'), 0.2);
      if (age >= 18) { s.hairStyle = 'bun'; if (era >= 2 && !duty && hash(id, 7) < 0.6) { s.hat = 'gat'; s.hatCol = col('#1a1a1a'); } }
      if (age >= 40 && hash(id, 8) < 0.5) s.torso = 'robe';
    }
    if (summer && duty && FARM.test(a.jk || '')) { s.hat = 'straw'; s.hatCol = col('#d8c08a'); s.armBare = !F; }
  } else {
    const pal = era === 4 ? (F ? P.indF : P.ind) : era === 5 ? P.modern : era === 6 ? P.today : P.space;
    s.top = col(pk(pal, id, 4));
    s.bottom = col(era >= 5 ? pk(P.jeans, id, 6) : pk(P.ind, id, 6));
    if (F && hash(id, 10) < (era === 4 ? 0.6 : 0.35)) { s.bot = 'skirtS'; s.legBare = summer || hash(id, 11) < 0.3; s.bottom = col(pk(pal, id, 12)); }
    if (summer) {
      s.armBare = hash(id, 13) < 0.8;
      if (s.bot === 'pants' && (age < 14 || hash(id, 14) < (F ? 0.3 : 0.4))) { s.bot = 'shorts'; s.legBare = true; }
    }
    if (winter) { s.torso = 'coat'; s.top = col(pk(P.winter, id, 15)); s.armBare = false; s.legBare = false; if (age < 40 && hash(id, 16) < 0.35) { s.hat = 'beanie'; s.hatCol = col(pk(P.modern, id, 17)); } }
    else if (season === '가을' && hash(id, 18) < 0.4) { s.torso = 'coat'; s.top = col(pk(P.winter, id, 19)).lerp(new THREE.Color('#c8b49a'), 0.35); }
    // 학생: 교복과 책가방
    if (school && age >= 7 && age < 19) {
      if (age >= 12) { s.top = col(era === 4 ? '#1b1b22' : '#26324f'); s.bottom = col(F ? '#3d4a63' : '#3a3f4a'); s.bot = F ? 'skirtS' : 'pants'; s.legBare = false; s.torso = winter ? 'coat' : 'torso'; s.armBare = summer; }
      s.pack = true; s.packCol = col(pk(P.pack, id, 20));
    }
    // 근무복
    if (duty && a.jk) {
      const j = a.jk;
      if (j === 'police') { s.top = col('#1f2a44'); s.bottom = col('#1f2a44'); s.hat = 'cap'; s.hatCol = col('#1f2a44'); s.torso = 'torso'; s.armBare = summer; }
      else if (j === 'firefighter') { s.top = col('#3a3f45'); s.bottom = col('#3a3f45'); s.hat = 'hard'; s.hatCol = col('#c62828'); s.torso = 'coat'; s.armBare = false; }
      else if (j === 'soldier') { s.top = col('#4b5d3a'); s.bottom = col('#4b5d3a'); s.hat = 'cap'; s.hatCol = col('#3e4d30'); s.torso = 'torso'; }
      else if (MEDIC.test(j)) { s.torso = 'coat'; s.top = col('#f7f7f5'); s.armBare = false; }
      else if (COOK.test(j)) { s.top = col('#f7f7f5'); s.hat = 'toque'; s.hatCol = col('#ffffff'); s.torso = 'torso'; s.armBare = false; }
      else if (SITE.test(j)) { s.top = col(hash(id, 21) < 0.5 ? '#f39c12' : '#c6e03a'); s.bottom = col('#3a3f4a'); s.hat = 'hard'; s.hatCol = col(hash(id, 22) < 0.7 ? '#f2c94c' : '#ffffff'); s.torso = 'torso'; s.bot = 'pants'; s.legBare = false; }
      else if (OFFICE.test(j) && era >= 4 && !summer) { s.torso = 'coat'; s.top = col(pk(P.suit, id, 23)); s.bottom = s.top.clone(); s.armBare = false; if (s.bot === 'shorts') { s.bot = 'pants'; s.legBare = false; } }
      else if (FARM.test(j) && summer) { s.hat = 'straw'; s.hatCol = col('#d8c08a'); }
      else if (j === 'chef' || j === 'barista' || j === 'server') { s.top = col(j === 'barista' ? '#3b2e26' : '#f7f7f5'); }
    }
  }
  // 아이: 밝은 옷 (원시 시대 아이는 여름엔 거의 벗고 다닌다), 어르신: 차분한 옷
  if (age < 13 && !school) {
    if (era <= 0) { if (summer || age < 6) { s.top = skin.clone(); s.armBare = true; } s.legBare = true; }
    else if (era <= 3) { if (!duty) s.top = col(pk(P.kidHan, id, 27)); }
    else if (!s.umbrella && s.torso !== 'coat') s.top = col(pk(P.kid, id, 27));
  }
  if (age >= 62 && era >= 1 && !duty && s.torso !== 'coat') s.top = col(pk(P.elder, id, 28)).lerp(s.top, 0.35);
  // 수염: 옛날일수록, 나이 들수록 많이 기른다
  if (!F && age >= 20) {
    const r = hash(id, 29), p = era <= 0 ? 0.6 : era <= 3 ? (age >= 30 ? 0.65 : 0.25) : era === 4 ? 0.3 : 0.12;
    if (r < p) s.beard = era >= 1 && era <= 3 && age >= 50 ? 'long' : r < p * 0.35 ? 'mous' : 'short';
  }
  // 지팡이: 일흔이 넘은 어르신 중 다수
  if (age >= 70 && hash(id, 31) < 0.65) s.cane = col(pk(P.cane, id, 32));
  if (rain && era >= 4) s.umbrella = col(pk(P.umbrella, id, 24));
  else if (rain && era >= 2 && hash(id, 25) < 0.5) { s.hat = s.hat || 'straw'; s.hatCol = s.hatCol || col('#c9b07a'); }
  if (!s.top) s.top = col('#cccccc');
  if (!s.bottom) s.bottom = col('#555555');
  return s;
}

// ── 그리기 ──
const PARTS = ['leg', 'thigh', 'torso', 'coat', 'robe', 'skirtS', 'skirtL', 'arm', 'sleeve', 'hand', 'head', 'pack', 'umbrella', 'cane'];
const PAIRS = new Set(['leg', 'thigh', 'arm', 'sleeve', 'hand']);
export class People {
  constructor(parent) {
    this.parent = parent; this.cap = 0; this.m = {}; this.cache = new Map();
    this.M = { base: new THREE.Matrix4(), up: new THREE.Matrix4(), tmp: new THREE.Matrix4(), r: new THREE.Matrix4(), out: new THREE.Matrix4(), head: new THREE.Matrix4() };
  }
  ensure(n) {
    if (this.cap >= n && this.cap) return;
    const cap = Math.max(32, 2 ** Math.ceil(Math.log2(n + 1)));
    for (const m of Object.values(this.m)) { this.parent.remove(m); m.material.dispose(); m.dispose(); }
    const G = geos();
    const mat = (r, side = THREE.FrontSide) => new THREE.MeshStandardMaterial({ vertexColors: true, roughness: r, side });
    const mk = (key, geo, m, k = 1) => { const im = new THREE.InstancedMesh(geo, m, cap * k); im.count = 0; im.frustumCulled = false; im.visible = false; this.parent.add(im); this.m[key] = im; };
    const cloth = mat(0.85), skinM = mat(0.6), hairM = mat(0.75), skirtM = mat(0.85, THREE.DoubleSide), hatM = mat(0.55), umbM = mat(0.5, THREE.DoubleSide);
    for (const p of PARTS) mk(p, G[p], p === 'head' || p === 'hand' ? skinM : p.startsWith('skirt') ? skirtM : p === 'umbrella' ? umbM : cloth, PAIRS.has(p) ? 2 : 1);
    for (const [k, g] of Object.entries(G.hair)) mk('hair_' + k, g, hairM);
    for (const [k, g] of Object.entries(G.hat)) mk('hat_' + k, g, hatM);
    for (const [k, g] of Object.entries(G.beard)) mk('beard_' + k, g, hairM);
    // 발밑 그림자 (땅에 붙어 보이게)
    mk('blob', new THREE.CircleGeometry(0.34, 14).rotateX(-Math.PI / 2).translate(0, 0.03, 0), new THREE.MeshBasicMaterial({ color: 0x0c1018, transparent: true, opacity: 0.32, depthWrite: false }));
    // 누르기 쉬운 보이지 않는 몸통 (고르기 전용)
    mk('pick', new THREE.CapsuleGeometry(0.3, 1.1, 2, 6).translate(0, 0.9, 0), new THREE.MeshBasicMaterial({ visible: false }));
    this.cap = cap;
  }
  begin() { for (const m of Object.values(this.m)) m.count = 0; }
  put(key, mat, color) {
    const im = this.m[key], i = im.count++;
    im.setMatrixAt(i, mat); im.setColorAt(i, color);
  }
  /** 옷차림 (바뀔 때만 다시 고른다) */
  spec(id, key, make) {
    const c = this.cache.get(id);
    if (c && c.key === key) return c.s;
    const s = make();
    this.cache.set(id, { key, s });
    if (this.cache.size > 4000) this.cache.clear();
    return s;
  }
  /**
   * base: 발 위치·방향·키 (Matrix4), s: 옷차림, pose: { walk 0..1, run, t(초), id, age, talk, work, jump }
   */
  add(base, s, pose) {
    const { up, tmp, r, out, head } = this.M;
    this.put('pick', base, s.skin);
    this.put('blob', base, WHITE);
    const age = pose.age;
    // 아기: 멈춰 있으면 앉고, 움직이면 긴다
    if (age < 1.3) return this.baby(base, s, pose);
    const legK = s.legK || 1, armK = s.armK || 1, hipY = HIP * legK;
    const kid = age < 12, old = age > 65;
    const ph = pose.t * (pose.run ? 13 : old ? 6 : kid ? 11 : 8.5) + pose.id * 1.7;
    const amp = pose.walk ? (pose.run ? 0.85 : old ? 0.26 : kid ? 0.62 : 0.48) : 0;
    const swing = Math.sin(ph) * amp;
    // 위로 몸 (굽은 등 · 걷는 리듬 · 아이는 통통 튄다)
    const stoop = age > 74 ? 0.22 : age > 66 ? 0.12 : age > 58 ? 0.05 : 0;
    const bob = pose.walk && kid ? Math.abs(Math.sin(ph)) * 0.035 : 0;
    up.makeTranslation(0, hipY + bob, 0).multiply(r.makeRotationX(stoop + (pose.run ? 0.12 : 0))).multiply(tmp.makeTranslation(0, -HIP, 0));
    // 다리 (아이는 짧다)
    for (const sx of [-1, 1]) {
      const a = sx * swing;
      out.copy(base).multiply(tmp.makeTranslation(sx * 0.088 * s.build, hipY + bob, 0)).multiply(r.makeRotationX(a)).multiply(tmp.makeScale(s.build, legK, s.build));
      this.put('leg', out, s.legBare ? s.skin : s.bottom);
      if (s.bot === 'shorts') this.put('thigh', out, s.bottom);
    }
    // 몸통
    out.copy(base).multiply(up).multiply(tmp.makeScale(s.wide * s.build, 1, s.build));
    this.put(s.torso, out, s.top);
    if (s.bot === 'skirtS' || s.bot === 'skirtL') { out.copy(base).multiply(tmp.makeTranslation(0, hipY - HIP, 0)).multiply(tmp.makeScale(s.build, 1, s.build)); this.put(s.bot, out, s.bottom); }
    if (s.pack) { out.copy(base).multiply(up); this.put('pack', out, s.packCol); }
    // 팔: 걸을 땐 다리와 엇갈려, 말할 땐 손짓, 일할 땐 망치질, 우산을 든 손은 위로, 지팡이 짚은 손은 앞으로
    for (const sx of [-1, 1]) {
      let a = -sx * swing * 0.8;
      if (pose.run) a -= 0.35;
      if (pose.talk && sx > 0) a = -0.45 + Math.sin(pose.t * 3.2 + pose.id) * 0.3;
      if (pose.work && sx > 0) a = -1.1 + Math.sin(pose.t * 7 + pose.id) * 0.55;
      if (s.umbrella && sx < 0) a = -0.65;
      if (s.cane && sx > 0) a = -0.35 + (pose.walk ? Math.sin(ph) * 0.12 : 0);
      if (kid && pose.play && sx > 0) a = -2.6 + Math.sin(pose.t * 9 + pose.id) * 0.4;   // 노는 아이는 손을 번쩍
      out.copy(base).multiply(up).multiply(tmp.makeTranslation(sx * 0.205 * s.wide * s.build, SHOULDER - 0.02, 0)).multiply(r.makeRotationZ(sx * 0.07)).multiply(tmp.makeRotationX(a)).multiply(r.makeScale(1, armK, 1));
      this.put('arm', out, s.armBare ? s.skin : s.top);
      if (s.armBare) this.put('sleeve', out, s.top);
      this.put('hand', out, s.skin);
    }
    if (s.cane) { out.copy(base).multiply(tmp.makeTranslation(0.25 * s.wide * s.build, 0, 0.3 + (pose.walk ? Math.sin(ph) * 0.06 : 0))).multiply(r.makeRotationX(-0.12)).multiply(tmp.makeScale(1, legK * 0.98, 1)); this.put('cane', out, s.cane); }
    // 머리 (아이는 머리가 크다)
    const hk = age < 3 ? 1.75 : age < 13 ? 1.72 - age * 0.048 : age < 16 ? 1.14 - (age - 13) * 0.03 : 1;
    head.copy(base).multiply(up).multiply(tmp.makeTranslation(0, HEAD_Y - 0.09, 0)).multiply(r.makeScale(hk, hk, hk)).multiply(tmp.makeTranslation(0, -(HEAD_Y - 0.09), 0));
    this.drawHead(head, s);
    if (s.umbrella) { out.copy(base).multiply(tmp.makeTranslation(-0.2 * s.wide * s.build, 0, 0.38)); this.put('umbrella', out, s.umbrella); }
  }
  drawHead(head, s) {
    this.put('head', head, s.skin);
    if (s.hairStyle && !(s.hat === 'hard' || s.hat === 'beanie' || s.hat === 'toque') || s.hairStyle === 'long') this.put('hair_' + (s.hairStyle || 'short'), head, s.hairCol);
    if (s.beard) this.put('beard_' + s.beard, head, s.hairCol);
    if (s.hat) this.put('hat_' + s.hat, head, s.hatCol);
  }
  /** 아기: 엉덩이를 땅에 붙이고 앉아 있거나, 손발로 긴다 */
  baby(base, s, pose) {
    const { up, tmp, r, out, head } = this.M;
    const crawl = pose.walk;
    const ph = pose.t * 7 + pose.id;
    const hipY = crawl ? 0.36 : 0.12;
    // 몸: 앉으면 곧게, 기면 앞으로 엎드려
    up.makeTranslation(0, hipY, 0).multiply(r.makeRotationX(crawl ? 1.25 : 0.05)).multiply(tmp.makeTranslation(0, -HIP, 0));
    out.copy(base).multiply(up).multiply(tmp.makeScale(s.build * 1.05, 0.92, s.build * 1.05));
    this.put('torso', out, s.top);
    for (const sx of [-1, 1]) {
      // 다리: 앉으면 앞으로 뻗고, 기면 무릎을 꿇은 듯 뒤로
      out.copy(base).multiply(tmp.makeTranslation(sx * 0.09, hipY, crawl ? -0.04 : 0.02)).multiply(r.makeRotationX(crawl ? 1.35 + Math.sin(ph + (sx > 0 ? Math.PI : 0)) * 0.25 : -1.45)).multiply(tmp.makeScale(1.1, 0.5, 1.1));
      this.put('leg', out, s.legBare ? s.skin : s.bottom);
      // 팔: 앉으면 앞으로 살짝, 기면 땅을 짚는다
      out.copy(base).multiply(up).multiply(tmp.makeTranslation(sx * 0.2, SHOULDER - 0.02, 0)).multiply(r.makeRotationX(crawl ? -1.2 + Math.sin(ph + (sx > 0 ? 0 : Math.PI)) * 0.3 : -0.55 + Math.sin(pose.t * 2 + pose.id + sx) * 0.15)).multiply(tmp.makeScale(1.1, 0.62, 1.1));
      this.put('arm', out, s.armBare ? s.skin : s.top);
      this.put('hand', out, s.skin);
    }
    head.copy(base).multiply(up).multiply(tmp.makeTranslation(0, HEAD_Y - 0.09, 0)).multiply(r.makeScale(1.55, 1.55, 1.55)).multiply(tmp.makeTranslation(0, -(HEAD_Y - 0.09), 0));
    this.drawHead(head, s);
  }
  end() {
    for (const m of Object.values(this.m)) { m.visible = m.count > 0; if (m.count) { m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; } }
  }
  dispose() { for (const m of Object.values(this.m)) { this.parent.remove(m); m.dispose(); } this.m = {}; this.cap = 0; }
}
