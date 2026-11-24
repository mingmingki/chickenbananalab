// 부족과 나라
//  · 땅(지금의 나라 자리)마다 부족 서너 개가 흩어져 살며 시작한다. 처음부터 나라 이름이 있지 않다.
//  · 부족마다 족장이 있고, 족장이 똑똑하고 호기심이 많을수록 새 기술(불·돌도끼·나무 다루기·흙벽…)을 일찍 알아낸다.
//    아는 것은 부족마다 다르다 — 이웃 부족과 오가며 배우기도 하고, 싸워서 빼앗기도 한다.
//  · 부족끼리 선물을 주고받고, 혼인으로 맺어지고, 사냥터를 두고 다투고, 습격하고, 굴복시키고, 하나로 합친다.
//  · 한 부족이 땅을 모두 묶으면 그 족장이 나라를 세우고 이름을 짓는다. 고대 문명은 나라가 서야 열린다.
//  · 나라끼리도 사신을 주고받고, 국경을 두고 다투고, 전쟁을 한다.
import { clamp, rand, pick, chance, gauss, ageOf, DAY, YEAR, ga, neun, reul, wa, weighted } from './util.js';
import { ERAS } from './data.js';
import { remember, lifeEvent, think, feel } from './ann.js';
import { rel, marry } from './social.js';
import { makeSettlement } from './settlement.js';
import { getCountry, randomLandPoint, seaNear } from '../geo.js';
import { leaveJob, setJob } from './economy.js';
import { consult } from './dilemma.js';
import { INDUSTRIES, BUILDINGS } from './data.js';

// ── 땅 이름: 나라가 서기 전에는 땅을 지형으로 부른다 ──
const LAND = {
  KR: '한반도 남부', KP: '한반도 북부', JP: '일본 열도', CN: '황하와 장강의 땅', TW: '타이완 섬', MN: '몽골 고원', RU: '동유럽 평원과 시베리아',
  KZ: '카자흐 초원', UZ: '오아시스의 땅', TM: '카라쿰 사막', KG: '톈산 산맥', TJ: '파미르 고원', AF: '힌두쿠시', PK: '인더스 강 유역',
  IN: '인도 아대륙', BD: '갠지스 삼각주', NP: '히말라야 기슭', BT: '히말라야 동쪽', LK: '실론 섬', MM: '이라와디 강 유역', TH: '짜오프라야 강 유역',
  LA: '메콩 강 상류', KH: '메콩 강 하류', VN: '홍강과 메콩의 땅', MY: '말레이 반도', SG: '말레이 끝 섬', ID: '누산타라 섬들', PH: '필리핀 군도',
  PG: '뉴기니 섬', AU: '오스트레일리아 대륙', NZ: '아오테아로아',
  IR: '이란 고원', IQ: '메소포타미아', SY: '레반트 북부', LB: '레반트 해안', IL: '가나안', PS: '가나안 고원', JO: '요르단 강 동쪽',
  SA: '아라비아 반도', YE: '아라비아 남부', OM: '아라비아 동쪽 끝', AE: '아라비아 만 해안', QA: '아라비아 만 반도', KW: '메소포타미아 하구',
  TR: '아나톨리아', GE: '캅카스 남쪽 기슭', AM: '아르메니아 고원', AZ: '캅카스 동쪽', CY: '키프로스 섬',
  EG: '나일 강 유역', SD: '누비아', SS: '나일 강 상류', LY: '리비아 사막', TN: '아프리카 북쪽 해안', DZ: '마그레브', MA: '마그레브 서쪽', MR: '사하라 서쪽',
  ML: '사헬', NE: '사하라 남쪽', TD: '차드 호수 땅', NG: '니제르 강 유역', GH: '황금 해안', CI: '상아 해안', SN: '세네갈 강 유역', GN: '푸타잘론 고원',
  ET: '에티오피아 고원', ER: '홍해 서쪽 해안', SO: '아프리카의 뿔', KE: '동아프리카 고원', TZ: '킬리만자로 기슭', UG: '빅토리아 호수 땅', RW: '천 개의 언덕',
  CD: '콩고 분지', CG: '콩고 강 서쪽', GA: '적도의 숲', CM: '카메룬 산지', AO: '앙골라 고원', ZM: '잠베지 강 유역', ZW: '짐바브웨 고원', MZ: '잠베지 강 하구',
  MW: '말라위 호수 땅', BW: '칼라하리', NA: '나미브 사막', ZA: '남아프리카 고원', MG: '마다가스카르 섬',
  GR: '에게 해 땅', IT: '이탈리아 반도', ES: '이베리아 반도', PT: '이베리아 서쪽 해안', FR: '갈리아', BE: '갈리아 북쪽', NL: '라인 강 하구', LU: '아르덴 숲',
  GB: '브리튼 섬', IE: '에린 섬', IS: '얼음의 섬', DE: '게르마니아', AT: '알프스 동쪽', CH: '알프스', PL: '비스와 강 유역', CZ: '보헤미아', SK: '카르파티아 서쪽',
  HU: '판노니아 평원', RO: '다키아', BG: '트라키아', RS: '발칸 반도 북부', HR: '아드리아 해 동쪽', BA: '디나르 산맥', ME: '검은 산', AL: '일리리아', MK: '마케도니아',
  SI: '알프스 남쪽', UA: '드네프르 강 유역', BY: '프리퍄트 습지', MD: '드네스트르 강 유역', LT: '발트 해 남쪽', LV: '발트 해 동쪽', EE: '발트 해 북쪽',
  FI: '천 개의 호수 땅', SE: '스칸디나비아', NO: '피오르의 땅', DK: '유틀란트 반도',
  US: '북아메리카 대평원', CA: '북쪽 숲과 호수', MX: '메소아메리카', GT: '마야의 숲', BZ: '마야 해안', HN: '중앙아메리카 산지', SV: '화산의 해안', NI: '호수의 지협',
  CR: '중앙아메리카 남쪽', PA: '두 바다 사이의 지협', CU: '카리브의 큰 섬', HT: '이스파니올라 서쪽', DO: '이스파니올라 동쪽', JM: '카리브의 샘 섬',
  CO: '안데스 북쪽', VE: '오리노코 강 유역', EC: '적도의 안데스', PE: '안데스 산지', BO: '안데스 고원', CL: '안데스 서쪽 해안', AR: '팜파스', UY: '라플라타 강 동쪽',
  PY: '파라나 강 유역', BR: '아마존', GY: '기아나 숲', SR: '기아나 해안',
};
export function landName(code) {
  const g = getCountry(code);
  return LAND[code] || `${g?.name || code} 땅`;
}

// ── 나라 이름: 나라를 세우는 족장이 짓는다 ──
const KO_NATION = '사로 가라 다벌 아라 한밝 새벌 버들 미리 누리 가람 비사 소벌 아사 고마 솔내 별뫼 달벌 감내 너른 한울 새밝 늘봄 마루 아침 해솟 밝달'.split(' ');
const NATION_SUF = { ko: ['국', '국', '나라'], ja: ['국'], zh: ['국'], default: [' 왕국', ' 연맹', '국'] };
export function nationName(world, c, t, L) {
  const taken = new Set(Object.values(world.countries).map(x => x.nation?.name).filter(Boolean));
  const proud = L && (L.amb > 70 || L.tr.E > 72);
  for (let i = 0; i < 40; i++) {
    let n;
    if (c.culture === 'ko') {
      const cut = t.name.replace(/(마을|골|내|터|뜰|나루|들|고을)$/, '');
      const base = chance(0.45) ? (cut.length >= 2 ? cut : t.name) : pick(KO_NATION);
      n = (proud && chance(0.5) ? pick(['큰', '대', '한']) : '') + base + pick(NATION_SUF.ko);
    } else {
      const suf = NATION_SUF[c.culture] || NATION_SUF.default;
      n = (proud && chance(0.4) ? '대 ' : '') + t.name + pick(suf);
    }
    if (!taken.has(n)) return n.replace(/\s+/g, ' ').trim();
  }
  return t.name + ' 왕국';
}

// ── 이른 시대의 기술: 무엇을 알아야 무엇을 알 수 있나 ──
export const EARLY_TECH = {
  '불의 사용': { req: [], w: 3, how: '마른 나뭇가지를 비벼 불을 피우는 법', icon: '🔥' },
  '석기 도구': { req: [], w: 3, how: '돌을 깨뜨려 날카로운 돌도끼를 만드는 법', icon: '🪓' },
  '언어': { req: [], w: 1.5, how: '서로의 뜻을 말로 주고받는 법', icon: '🗣️' },
  '나무 다루기': { req: ['석기 도구'], w: 2.5, how: '돌도끼로 나무를 베어 기둥과 서까래를 다듬는 법', icon: '🪵' },
  '사냥 기술': { req: ['석기 도구'], w: 2, how: '무리를 지어 짐승을 몰아 잡는 법', icon: '🏹' },
  '동굴 벽화': { req: ['불의 사용'], w: 0.8, how: '바위에 숯과 흙으로 그림을 남기는 법', icon: '🎨' },
  '매듭과 밧줄': { req: ['나무 다루기'], w: 1.2, how: '풀줄기를 꼬아 밧줄을 만드는 법', icon: '🪢' },
  '농업': { req: ['석기 도구', '불의 사용', '나무 다루기'], w: 1.3, how: '씨앗을 심어 곡식을 거두는 법 — 이제 논밭을 일군다', icon: '🌾' },
  '목축': { req: ['농업', '매듭과 밧줄'], w: 1, how: '짐승을 길들여 우리에서 기르는 법', icon: '🐄' },
  '토기': { req: ['불의 사용', '농업'], w: 1.4, how: '진흙을 빚어 불에 구운 그릇을 만드는 법 — 곡식을 오래 둘 수 있다', icon: '🏺' },
  '흙벽 쌓기': { req: ['나무 다루기', '농업'], w: 1.8, how: '진흙에 짚을 섞어 벽을 쌓는 법 — 이제 초가집을 지을 수 있다', icon: '🛖' },
  '직조': { req: ['매듭과 밧줄'], w: 1, how: '실을 엮어 옷감을 짜는 법', icon: '🧵' },
  '관개 수로': { req: ['농업'], w: 0.9, how: '물길을 내어 논밭에 물을 대는 법', icon: '💧' },
  '바퀴': { req: ['나무 다루기', '토기'], w: 0.7, how: '둥근 나무로 무거운 짐을 굴리는 법', icon: '🛞' },
};

// ── 부족 조회 ──
const COLORS = ['#e8590c', '#1c7ed6', '#2b8a3e', '#ae3ec9', '#f08c00', '#0b7285', '#c2255c', '#5f3dc4', '#66a80f', '#d6336c'];
export const tribeLabel = t => `${t.name} 부족`;
export const liveTribes = c => Object.values(c?.tribes || {}).filter(t => !t.gone);
export function tribeOfS(world, s) { const c = s && world.countries[s.country]; return (c?.tribes && s.tribe && c.tribes[s.tribe]) || null; }
export function tribeOfA(world, a) { return tribeOfS(world, world.settlements.get(a.sid)); }
export function tribePeople(world, t) { const out = []; for (const sid of t.sids) out.push(...world.residents(sid)); return out; }
/** 나라의 우두머리 이름: 나라가 서기 전 부족은 족장, 막 선 나라는 군장, 고대부터는 왕… */
export function leaderTitle(c) { return c?.nation ? (c.era <= 1 ? '군장' : ERAS[c.era].leader) : '족장'; }
/** 이 앤의 자리 이름 (부족의 족장 또는 나라의 우두머리) — 없으면 null */
export function titleOf(world, a) {
  const c = world.countries[a.country];
  if (!c) return null;
  if (c.leader === a.id) return leaderTitle(c);
  if (!c.nation) { const t = tribeOfA(world, a); if (t && t.chief === a.id) return '족장'; }
  return null;
}
/** 그 정착지(또는 그 앤의 정착지)가 이 기술을 아는가. 나라가 서면 나라 전체가 함께 안다 */
export function knows(world, sOrA, d) {
  const s = sOrA?.buildings ? sOrA : world.settlements.get(sOrA?.sid);
  const c = s && world.countries[s.country];
  if (!c) return false;
  if (!c.tribes || c.nation) return c.discoveries.includes(d);
  const t = tribeOfS(world, s);
  return t ? (t.known || []).includes(d) : c.discoveries.includes(d);
}
const kmBetween = (p, q) => Math.hypot((p[0] - q[0]) * 111 * Math.cos(((p[1] + q[1]) / 2) * Math.PI / 180), (p[1] - q[1]) * 111);
function homeOf(world, t) { return world.settlements.get(t.home) || world.settlements.get(t.sids[0]); }
function tribeKm(world, a, b) { const p = homeOf(world, a), q = homeOf(world, b); return p && q ? kmBetween([p.lon, p.lat], [q.lon, q.lat]) : 100; }
const chiefOf = (world, t) => { const L = t.chief != null && world.anns.get(t.chief); return L && L.alive ? L : null; };
const adult = (world, a) => ageOf(a, world.clock) >= 19;

export function makeTribe(world, c, s) {
  c.tribes = c.tribes || {};
  const id = 't' + s.id;
  const used = new Set(Object.values(c.tribes).map(t => t.color));
  const t = c.tribes[id] = { id, name: s.name, home: s.id, sids: [s.id], chief: null, since: null, color: COLORS.find(x => !used.has(x)) || pick(COLORS), founded: Math.round(world.clock), rel: {}, known: [], wins: 0, losses: 0 };
  s.tribe = id;
  for (const o of liveTribes(c)) if (o !== t) {
    const r0 = clamp(8 - tribeKm(world, o, t) * 0.12 + gauss() * 14, -45, 45);
    t.rel[o.id] = r0; o.rel[t.id] = r0;
  }
  world.dirtyCountries.add(c.code); world.dirtySettlements.add(s.id);
  return t;
}

/** 부족 하나의 처음 사람들: 부부 여덟아홉 쌍과 아이들, 혼자 사는 젊은이, 어르신 — 마흔 명 안팎 */
export const tribePlan = (big = false) => ({ couples: 8 + Math.floor(rand() * 3) + (big ? 1 : 0), singles: 2 + Math.floor(rand() * 3), elders: 2 + Math.floor(rand() * 2) });
/** 플레이어가 아직 없는 먼 땅의 부족: 조금 작게 (서른 명 안팎) — 서버 메모리를 아끼려고 */
export const farPlan = () => ({ couples: 6 + Math.floor(rand() * 3), singles: 1 + Math.floor(rand() * 3), elders: 1 + Math.floor(rand() * 2) });

// 지구 곳곳: 세계가 처음 열릴 때 부족들이 사는 땅 — 먼저 한반도 둘레와 대륙마다 한두 곳, 인구 목표가 크면 더 많은 땅에
export const WORLD_LANDS = ['KP', 'JP', 'CN', 'MN', 'VN', 'IN', 'IR', 'TR', 'EG', 'NG', 'ZA', 'IT', 'GB', 'RU', 'US', 'MX', 'PE', 'BR', 'AU'];
export const MORE_LANDS = ['TH', 'ID', 'PH', 'IQ', 'SA', 'PK', 'BD', 'MM', 'KZ', 'UZ', 'ET', 'KE', 'MA', 'DZ', 'SD', 'CD', 'AO', 'TZ', 'GH', 'MG',
  'ES', 'FR', 'DE', 'PL', 'UA', 'SE', 'GR', 'TW', 'NP', 'AF', 'CA', 'CO', 'AR', 'CL', 'VE', 'BO', 'CU', 'NZ', 'PG', 'NO', 'FI', 'RO', 'HU', 'IE'];
/**
 * 세계를 채운다: 처음 열릴 때, 그리고 서버 메모리가 늘어 인구 목표가 커졌을 때 (예전 저장본은 불러올 때).
 * 아직 사람이 없는 땅부터 부족 서너 개씩, 그래도 모자라면 플레이어가 없는 땅에 부족을 하나씩 더한다.
 */
export function seedWorld(world, seedVillage) {
  const target = world.popTarget || 1200;
  if ((world.seededFor || 0) >= target) return 0;
  const alive = () => { let n = 0; for (const a of world.anns.values()) if (a.alive) n++; return n; };
  world.worldSeeded = world.worldSeeded || Math.round(world.clock) || 1;
  const made = [];
  const players = new Set(Object.values(world.players).map(p => p.country));
  for (const code of [...WORLD_LANDS, ...MORE_LANDS]) {
    if (alive() >= target) break;
    if (!getCountry(code)) continue;
    if ([...world.settlements.values()].some(s => s.country === code)) continue;
    const c = world.ensureCountry(code);
    if (c.nation) continue;
    seedLand(world, c, seedVillage, { tribes: chance(0.5) ? 3 : 4, quiet: true, plan: farPlan });
    made.push(c.name);
  }
  // 땅마다 부족을 하나씩 더 (부족 다섯 개까지, 나라가 서지 않은 땅만)
  for (let round = 0; round < 3 && alive() < target; round++) {
    for (const c of Object.values(world.countries)) {
      if (alive() >= target) break;
      if (c.nation || !c.tribes || players.has(c.code) || liveTribes(c).length >= 5 || !liveTribes(c).length) continue;
      addTribe(world, c, seedVillage);
    }
  }
  world.seededFor = target;
  world.rebuildResidents();
  if (made.length) world.news('city', `🌍 지구 곳곳 ${made.length}개 땅에 부족들이 흩어져 산다 — ${made.slice(0, 6).join(', ')} …`, {}, 3);
  return made.length;
}
/** 이미 부족이 사는 땅에 이웃 부족 하나를 더 */
export function addTribe(world, c, seedVillage, plan = farPlan) {
  const sets = [...world.settlements.values()].filter(s => s.country === c.code && !s.abandoned);
  const base = world.settlements.get(c.capital) || sets[0];
  if (!base) return null;
  let best = null;
  for (let k = 0; k < 16; k++) {
    const p = randomLandPoint(c.code, [base.lon, base.lat], 0.22, 1.3);
    const dmin = Math.min(...sets.map(s => kmBetween(p, [s.lon, s.lat])));
    if (dmin < 14) continue;
    const sc = (seaNear(p[0], p[1]) ? 1.2 : 0) + Math.min(dmin, 50) / 25 - Math.max(0, dmin - 100) / 40 + rand();
    if (!best || sc > best.sc) best = { p, sc };
  }
  if (!best) return null;
  const s = makeSettlement(world, c, best.p[0], best.p[1], null);
  const t = makeTribe(world, c, s);
  // 이웃 부족들이 아는 것 가운데 쉬운 것 몇 가지
  const known = new Set(liveTribes(c).flatMap(x => x.known || []));
  t.known = Object.keys(EARLY_TECH).filter(d => known.has(d) && EARLY_TECH[d].req.length <= 1 && chance(0.6));
  seedVillage(world, c, s, plan(), true);
  world.rebuildResidents();
  chiefDaily(world, c, t, true);
  return t;
}

/** 땅에 처음 사람이 들어설 때: 부족 서너 개를 흩어 놓는다. 첫 부족의 정착지를 돌려준다 */
export function seedLand(world, c, seedVillage, opts = {}) {
  const g = getCountry(c.code);
  // 사람들은 물가에 모여 산다
  const spot = (near, lo, hi) => {
    let best = null;
    for (let i = 0; i < 8; i++) {
      const p = randomLandPoint(c.code, near, lo, hi);
      const sea = seaNear(p[0], p[1]);
      const sc = (sea ? 2 + (sea.km < 15 ? 1 : 0) : 0) + rand() * 2;
      if (!best || sc > best.sc) best = { p, sc };
    }
    return best.p;
  };
  const spots = [spot(g.centroid, 0, 1.4)];
  const n = opts.tribes || (chance(0.5) ? 4 : 5);
  for (let i = 1; i < n; i++) {
    let best = null;
    for (let k = 0; k < 16; k++) {
      const p = randomLandPoint(c.code, spots[0], 0.22, 1.1);
      const dmin = Math.min(...spots.map(q => kmBetween(p, q)));
      if (dmin < 14) continue;
      const sea = seaNear(p[0], p[1]);
      const sc = (sea ? 1.2 : 0) + Math.min(dmin, 50) / 25 - Math.max(0, dmin - 90) / 40 + rand();
      if (!best || sc > best.sc) best = { p, sc };
    }
    if (best) spots.push(best.p);
  }
  const made = [];
  for (let i = 0; i < spots.length; i++) {
    const [lon, lat] = spots[i];
    const s = makeSettlement(world, c, lon, lat, null);
    makeTribe(world, c, s);
    seedVillage(world, c, s, opts.plan ? opts.plan() : tribePlan(i === 0), opts.quiet);
    made.push(s);
  }
  c.capital = made[0].id;
  c.tribesAtStart = made.length;
  if (!opts.quiet) world.news('city', `🏕️ ${c.name}에 부족 ${made.length}개가 흩어져 산다 — ${made.map(s => s.name).join(', ')}`, { country: c.code, sid: made[0].id }, 2);
  world.rebuildResidents();
  for (const t of liveTribes(c)) chiefDaily(world, c, t, true);
  return made[0];
}

// ── 족장 ──
export function setChief(world, c, t, a, how) {
  const prev = chiefOf(world, t);
  t.chief = a.id; t.since = Math.round(world.clock);
  remember(world, a, `${tribeLabel(t)}의 족장이 되었다`, 10, 1, [], 'power');
  lifeEvent(world, a, `${tribeLabel(t)} 족장 (${how})`, '👑');
  feel(a, 25, '감격', '족장');
  think(world, a, a.tr.A > 60 ? '우리 부족 모두가 배곯지 않게 하겠다.' : a.amb > 70 ? '우리 부족을 이 땅에서 가장 강하게 만들겠다.' : '무거운 자리다. 잘 해낼 수 있을까.');
  const g = a.goals.find(g => g.k === 'power' && !g.done);
  if (g) world.achieveGoal(a, g);
  world.news('power', `👑 ${ga(a.name)} ${tribeLabel(t)}의 족장이 되었다 (${how})`, { anns: [a.id], country: c.code, sid: a.sid }, a.owner ? 3 : 2);
  world.dirtyAnnMeta.add(a.id);
  if (prev) world.dirtyAnnMeta.add(prev.id);
  world.dirtyCountries.add(c.code);
}
export function chiefOfTribe(world, t) { return chiefOf(world, t); }
function chiefDaily(world, c, t, quiet = false) {
  const people = tribePeople(world, t);
  t.pop = people.length;
  const L = t.chief != null ? world.anns.get(t.chief) : null;
  const ok = L && L.alive && t.sids.includes(L.sid) && !L.travel?.kind;
  if (!ok && !(L && L.alive && L.travel)) {
    if (L && !L.alive && !t.mourned) { t.mourned = L.id; c.history.push({ t: Math.round(world.clock), text: `${tribeLabel(t)} 족장 ${L.name} 세상을 떠남`, icon: '🕯️' }); }
    const top = people.filter(a => adult(world, a)).sort((x, y) => world.influence(y) - world.influence(x))[0];
    if (top) {
      if (quiet) { t.chief = top.id; t.since = Math.round(world.clock); world.dirtyAnnMeta.add(top.id); lifeEvent(world, top, `${tribeLabel(t)} 족장`, '👑'); }
      else setChief(world, c, t, top, L ? '추대' : '추대');
    }
    return;
  }
  // 플레이어의 앤이 따르는 이가 많으면: 족장 자리에 나서 볼지 묻는다
  if (!quiet && L && world.clock - (t.since || 0) > DAY * 20) for (const a of people) {
    if (!a.owner || a.id === L.id || ageOf(a, world.clock) < 20 || !chance(0.015)) continue;
    if (world.influence(a) >= world.influence(L) - 25) consult(world, a, 'chief_run', { c: c.code, tribe: t.id });
  }
  // 더 힘 있고 따르는 이가 많은 자가 나타나면 족장이 바뀐다
  if (L && world.clock - (t.since || 0) > DAY * 60 && chance(0.05)) {
    const top = people.filter(a => adult(world, a)).sort((x, y) => world.influence(y) - world.influence(x))[0];
    if (top && top.id !== L.id && world.influence(top) > world.influence(L) + 12) {
      remember(world, L, `${ga(top.name)} 새 족장이 되었다`, 7, -0.6, [top.id], 'power');
      setChief(world, c, t, top, '세력 교체');
    }
  }
}

// ── 발견: 족장이 똑똑하고 호기심 많을수록, 부족에 생각 깊은 사람이 많을수록 빨리 ──
export function nextTech(known, era) {
  const left = Object.keys(EARLY_TECH).filter(d => !known.includes(d) && EARLY_TECH[d].req.every(r => known.includes(r)));
  if (!left.length) return null;
  // 농경 시대 전엔 원시 시대 기술이 먼저 (농업은 원시 시대 기술을 거의 다 알고 난 뒤에)
  return weighted(left, d => EARLY_TECH[d].w * (d === '농업' && era === 0 ? (known.length >= 5 ? 2 : 0.6) : 1));
}
export function chiefWits(L) { return L ? L.ab.int * 0.6 + L.tr.O * 0.4 : 35; }
function discoveryDaily(world, c) {
  if (c.era >= 2) return;
  const units = c.nation || !c.tribes
    ? [{ t: null, L: c.leader != null ? world.anns.get(c.leader) : null, sids: [...world.settlements.values()].filter(s => s.country === c.code).map(s => s.id), known: c.discoveries }]
    : liveTribes(c).map(t => ({ t, L: chiefOf(world, t), sids: t.sids, known: t.known || (t.known = []) }));
  for (const u of units) {
    const people = u.sids.flatMap(sid => world.residents(sid)).filter(a => ageOf(a, world.clock) >= 14);
    if (people.length < 3) continue;
    const smart = chiefWits(u.L);
    const thinkers = people.slice().sort((x, y) => (y.ab.int + y.tr.O * 0.5) - (x.ab.int + x.tr.O * 0.5)).slice(0, 3);
    const brains = thinkers.reduce((s, a) => s + a.ab.int, 0) / thinkers.length;
    const p = 0.05 * (0.2 + smart / 55) * (0.5 + brains / 110) * Math.sqrt(people.length / 14);
    if (!chance(p)) continue;
    const d = nextTech(u.known, c.era);
    if (!d) continue;
    const finder = u.L && smart >= 66 && chance(0.45) ? u.L : pick(thinkers);
    world.tryDiscovery(finder, d, { chief: u.L && u.L !== finder ? u.L : null, tribe: u.t });
  }
}
/** 발견을 부족(또는 나라)의 앎에 더한다. 처음 아는 것이면 true */
export function learn(world, c, t, d) {
  if (t && !c.nation) { t.known = t.known || []; if (t.known.includes(d)) return false; t.known.push(d); }
  if (!c.discoveries.includes(d)) c.discoveries.push(d);
  world.dirtyCountries.add(c.code);
  return true;
}

// ── 부족 사이 ──
function hungry(world, t) {
  let food = 0, pop = 0;
  for (const sid of t.sids) { const s = world.settlements.get(sid); if (s) { food += s.food; pop += s.pop; } }
  return pop > 0 && food < pop * 3;
}
/** 싸울 사람들: 나서겠다는 사람(want)이 먼저, 숨기로 한 사람(skip)은 빼고, 힘센 순으로 */
function fighters(world, t, share = 0.35, want = null, skip = null) {
  const all = tribePeople(world, t).filter(a => { const g = ageOf(a, world.clock); return g >= 16 && g <= 50 && !a.travel && (a.sick || 0) < 0.5 && !a.preg && !skip?.has(a.id); });
  all.sort((x, y) => (want?.has(y.id) ? 1000 : 0) + (y.ab.ath + (y.gender === 'M' ? 15 : 0)) - (want?.has(x.id) ? 1000 : 0) - (x.ab.ath + (x.gender === 'M' ? 15 : 0)));
  const n = Math.max(2, Math.ceil(all.length * share), want ? all.filter(a => want.has(a.id)).length : 0);
  return all.slice(0, n);
}
function power(world, fs, L, home, known) {
  const k = 1 + (known.includes('석기 도구') ? 0.1 : 0) + (known.includes('사냥 기술') ? 0.15 : 0) + (known.includes('매듭과 밧줄') ? 0.05 : 0) + (known.includes('바퀴') ? 0.05 : 0);
  return fs.reduce((s, a) => s + 0.6 + a.ab.ath / 120 + a.sk.athletics / 250, 0) * (1 + (L?.sk.leadership || 0) / 250) * (home ? 1.25 : 1) * k;
}
function hurt(world, a, deathOk, cause) {
  if (deathOk && !a.owner && chance(0.5)) { world.die(a, cause); return 'dead'; }
  a.sick = Math.max(a.sick || 0, 0.6 + rand() * 0.8);
  a.health = clamp(a.health - 15 - rand() * 15);
  remember(world, a, `${cause}에서 다쳤다`, 6, -0.6, [], 'event');
  feel(a, -15, '아픔', cause);
  return 'hurt';
}
/** 싸움 한 판: 공격하는 쪽이 이기면 true. kind: 습격 · 전투 */
function clash(world, c, att, def, kind, o = {}) {
  const fa = fighters(world, att, 0.35, o.join), fd = fighters(world, def, 0.35, o.guard, o.hide);
  if (!fa.length || !fd.length) return null;
  const LA = chiefOf(world, att), LD = chiefOf(world, def);
  const pa = power(world, fa, LA, false, att.known || []), pd = power(world, fd, LD, true, def.known || []) * (o.warned ? 1.25 : 1);
  const win = chance(clamp(pa / (pa + pd) + gauss() * 0.08, 0.08, 0.92));
  const [lose, wonF, lostF] = win ? [def, fa, fd] : [att, fd, fa];
  let dead = 0, hurtN = 0;
  const nLost = 1 + Math.floor(rand() * Math.min(4, lostF.length * 0.4)), nWon = chance(0.6) ? 1 : 0;
  for (const a of lostF.sort(() => rand() - 0.5).slice(0, nLost)) (hurt(world, a, chance(0.55), `${wa(tribeLabel(win ? att : def))}의 싸움`) === 'dead' ? dead++ : hurtN++);
  for (const a of wonF.sort(() => rand() - 0.5).slice(0, nWon)) (hurt(world, a, chance(0.3), `${wa(tribeLabel(win ? def : att))}의 싸움`) === 'dead' ? dead++ : hurtN++);
  // 습격에 나선 사람들은 반나절쯤 마을을 비운다
  const until = Math.round(world.clock + 240 + rand() * 300);
  for (const a of fa) if (a.alive && !a.travel) { a.act = null; a.travel = { until, from: a.sid, to: a.sid, kind: 'raid', where: tribeLabel(def) }; world.dirtyAnnMeta.add(a.id); }
  for (const a of fa) if (a.alive && o.join?.has(a.id)) { a.fame = clamp((a.fame || 0) + (win ? 4 : 1), 0, 100); lifeEvent(world, a, `${tribeLabel(def)} ${kind}에 앞장섰다 — ${win ? '이겼다' : '졌다'}`, '⚔️'); }
  for (const a of fa) if (a.alive) remember(world, a, win ? `${tribeLabel(def)}을(를) ${kind === '습격' ? '습격해 이기고 돌아왔다' : '싸워 이겼다'}` : `${tribeLabel(def)}에 ${kind === '습격' ? '쳐들어갔다가 쫓겨났다' : '싸움에서 졌다'}`, 7, win ? 0.4 : -0.6, LA ? [LA.id] : [], 'war');
  for (const a of fd) if (a.alive) remember(world, a, win ? `${tribeLabel(att)}이(가) 쳐들어와 마을을 지키지 못했다` : `쳐들어온 ${tribeLabel(att)}을(를) 몰아냈다`, 7, win ? -0.7 : 0.6, LD ? [LD.id] : [], 'war');
  (win ? att : def).wins = ((win ? att : def).wins || 0) + 1; lose.losses = (lose.losses || 0) + 1;
  return { win, dead, hurt: hurtN, fa: fa.length, fd: fd.length };
}
function moveFood(world, from, to, share) {
  const s1 = homeOf(world, from), s2 = homeOf(world, to);
  if (!s1 || !s2) return 0;
  const amt = Math.round(s1.food * share);
  s1.food -= amt; s2.food += amt;
  return amt;
}
/** 한 부족이 다른 부족을 품는다 (굴복 또는 연합) */
function absorb(world, c, win, lose, how, story = null) {
  const LW = chiefOf(world, win), LL = chiefOf(world, lose);
  const loseSids = new Set(lose.sids);
  for (const sid of lose.sids) { const s = world.settlements.get(sid); if (s) { s.tribe = win.id; world.dirtySettlements.add(sid); } if (!win.sids.includes(sid)) win.sids.push(sid); }
  lose.gone = true; lose.goneTo = win.id; lose.goneHow = how; lose.goneAt = Math.round(world.clock); lose.sids = [];
  for (const d of lose.known || []) if (!(win.known || []).includes(d)) (win.known = win.known || []).push(d);
  for (const [oid, r] of Object.entries(lose.rel)) { if (oid === win.id) continue; const o = c.tribes[oid]; if (!o || o.gone) continue; const v = Math.min(win.rel[oid] ?? r, r); win.rel[oid] = v; o.rel[win.id] = v; }
  const conquest = how === '굴복';
  for (const sid of win.sids) for (const a of world.residents(sid)) {
    if (loseSids.has(a.sid)) {
      remember(world, a, conquest ? `우리 부족이 ${tribeLabel(win)}에 무릎을 꿇었다` : `우리 부족이 ${wa(tribeLabel(win))} 하나가 되었다`, 8, conquest ? -0.7 : 0.4, LW ? [LW.id] : [], 'power');
      if (conquest) feel(a, -12, '굴욕', tribeLabel(win));
    } else if (chance(0.6)) remember(world, a, conquest ? `${tribeLabel(lose)}이(가) 우리에게 굴복했다` : `${wa(tribeLabel(lose))} 한 부족이 되었다`, 6, 0.5, [], 'power');
  }
  if (LL) {
    if (conquest) { remember(world, LL, `족장 자리를 잃었다 — ${tribeLabel(win)}에 굴복`, 9, -0.9, LW ? [LW.id] : [], 'power'); lifeEvent(world, LL, `${tribeLabel(win)}에 굴복, 족장에서 물러남`, '🏳️'); feel(LL, -30, '굴욕', '굴복'); think(world, LL, '언젠가… 아니다. 살아남은 것만으로도 다행이다.'); }
    else { remember(world, LL, `${wa(tribeLabel(win))} 하나가 되고, 원로로 남았다`, 8, 0.4, LW ? [LW.id] : [], 'power'); lifeEvent(world, LL, `${tribeLabel(win)}과 연합, 원로가 됨`, '🤝'); LL.rep = clamp(LL.rep + 10, -100, 100); }
    world.dirtyAnnMeta.add(LL.id);
  }
  const text = story || (conquest ? `${tribeLabel(win)}이(가) ${reul(tribeLabel(lose))} 굴복시켰다 — 이제 ${lose.name}은(는) ${win.name} 부족의 땅` : `${wa(tribeLabel(win))} ${tribeLabel(lose)}이(가) 하나로 합쳤다`);
  c.history.push({ t: Math.round(world.clock), text, icon: conquest ? '⚔️' : '🤝' });
  world.news('war', `${conquest ? '⚔️' : '🤝'} ${text}`, { country: c.code, sid: win.home, anns: [LW?.id, LL?.id].filter(x => x != null) }, 3);
  world.dirtyCountries.add(c.code);
}
/** 혼인으로 맺어진다: 두 부족의 혼기 찬 젊은이 */
function alliance(world, c, A, B) {
  const single = (t, g) => tribePeople(world, t).filter(a => !a.owner && !a.spouse && !a.partner && !a.engaged && !a.travel && a.gender === g && ageOf(a, world.clock) >= 18 && ageOf(a, world.clock) <= 32 && a.orient !== 'gay');
  const [x, y] = chance(0.5) ? [A, B] : [B, A];
  const groom = pick(single(x, 'M')), bride = pick(single(y, 'F'));
  if (!groom || !bride) return null;
  // 신부가 신랑네 마을로 간다
  const to = world.settlements.get(groom.sid);
  world.leaveHome(bride);
  if (bride.job) leaveJob(world, bride, '혼인으로 이주', true);
  bride.act = null;
  bride.travel = { until: Math.round(world.clock + 180 + rand() * 240), from: bride.sid, to: to.id };
  bride.sid = to.id; bride.x = 0; bride.z = 0;
  marry(world, groom, bride, true);
  lifeEvent(world, groom, `${tribeLabel(y)}의 ${wa(bride.name)} 혼인`, '💞');
  lifeEvent(world, bride, `${tribeLabel(x)}의 ${wa(groom.name)} 혼인 — ${to.name}(으)로 시집감`, '💞');
  remember(world, bride, `정든 고향을 떠나 ${tribeLabel(x)}의 ${to.name}(으)로 시집왔다`, 9, 0.3, [groom.id], 'love');
  world.dirtyAnnMeta.add(groom.id); world.dirtyAnnMeta.add(bride.id);
  return { groom, bride, gt: x, bt: y };
}

// ── 습격: 채비 → (플레이어에게 묻기) → 반나절 뒤 쳐들어간다 ──
export function planRaid(world, c, att, def) {
  c.raids = c.raids || [];
  if (c.raids.some(r => r.att === att.id && r.def === def.id)) return null;
  const plan = { id: world.newId(), att: att.id, def: def.id, at: Math.round(world.clock + DAY * (0.5 + rand() * 0.4)), join: [], hide: [], guard: [], warned: false };
  c.raids.push(plan);
  world.news('war', `🔥 ${tribeLabel(att)}이(가) ${reul(tribeLabel(def))} 칠 채비를 한다`, { country: c.code, sid: att.home }, 2);
  for (const a of tribePeople(world, att)) { const g = ageOf(a, world.clock); if (a.owner && g >= 16 && g <= 50 && a.id !== att.chief) consult(world, a, 'join_raid', { c: c.code, plan: plan.id, foe: def.id }); }
  for (const a of tribePeople(world, def)) if (a.owner && ageOf(a, world.clock) >= 13 && chance(0.7)) consult(world, a, 'raid_warn', { c: c.code, plan: plan.id, foe: att.id });
  return plan;
}
export function raidPlan(world, code, id) { return (world.countries[code]?.raids || []).find(r => r.id === id) || null; }
function raidsDaily(world, c) {
  if (!c.raids?.length) return;
  for (const plan of [...c.raids]) {
    if (world.clock < plan.at) continue;
    c.raids = c.raids.filter(r => r !== plan);
    const att = c.tribes[plan.att], def = c.tribes[plan.def];
    if (!att || !def || att.gone || def.gone) continue;
    const res = clash(world, c, att, def, '습격', { join: new Set(plan.join), hide: new Set(plan.hide), guard: new Set(plan.guard), warned: plan.warned });
    if (!res) continue;
    const r = clamp((att.rel[def.id] ?? 0) - 15 - rand() * 12, -100, 100);
    att.rel[def.id] = def.rel[att.id] = r;
    const loot = res.win ? moveFood(world, def, att, 0.25 + rand() * 0.15) : 0;
    const toll = [res.dead ? `${res.dead}명 숨짐` : '', res.hurt ? `${res.hurt}명 다침` : ''].filter(Boolean).join(', ');
    world.news('war', `⚔️ ${tribeLabel(att)}이(가) ${reul(tribeLabel(def))} 습격했다 — ${res.win ? `${loot ? '곡식과 짐승을 빼앗아' : '마을을 휩쓸고'} 돌아갔다` : plan.warned ? '미리 알고 기다린 이들에게 쫓겨났다' : '되레 쫓겨났다'}${toll ? ` (${toll})` : ''}`, { country: c.code, sid: def.home }, 2);
    c.history.push({ t: Math.round(world.clock), text: `${tribeLabel(att)}의 ${tribeLabel(def)} 습격 — ${res.win ? '승리' : '패배'}`, icon: '⚔️' });
    const pw = t => power(world, fighters(world, t), chiefOf(world, t), false, t.known || []);
    const winT = res.win ? att : def, loseT = res.win ? def : att;
    if (pw(winT) / Math.max(0.1, pw(loseT)) > 1.8 && (loseT.losses || 0) >= 2 && chance(0.35)) absorb(world, c, winT, loseT, '굴복');
  }
}
/** 선물로 달랜다 */
export function sendGift(world, c, g, h) {
  const what = pick(GIFTS[Math.min(1, c.era)]);
  moveFood(world, g, h, 0.05);
  const r = clamp((g.rel[h.id] ?? 0) + 10 + rand() * 8, -100, 100);
  g.rel[h.id] = h.rel[g.id] = r;
  world.news('town', `🎁 ${tribeLabel(g)}이(가) ${tribeLabel(h)}에 ${reul(what)} 보냈다`, { country: c.code, sid: h.home }, 1);
  const Lh = chiefOf(world, h); if (Lh) remember(world, Lh, `${tribeLabel(g)}이(가) ${reul(what)} 보내왔다`, 4, 0.5, [], 'power');
  return what;
}
/** 혼인으로 맺기 (두 부족 젊은이) */
export function marryTribes(world, c, A, B) {
  const m = alliance(world, c, A, B);
  if (!m) return null;
  const r = clamp((A.rel[B.id] ?? 0) + 12, -100, 100);
  A.rel[B.id] = B.rel[A.id] = r;
  world.news('love', `💞 ${tribeLabel(m.gt)}의 ${wa(m.groom.name)} ${tribeLabel(m.bt)}의 ${m.bride.name}이(가) 혼인으로 두 부족을 맺었다`, { country: c.code, anns: [m.groom.id, m.bride.id] }, 2);
  return m;
}

const GIFTS = [['사슴 고기', '말린 생선', '산딸기 한 바구니', '돌도끼', '짐승 가죽'], ['곡식 몇 자루', '토기 항아리', '짠 옷감', '말린 고기', '씨앗']];
function relationsDaily(world, c) {
  const ts = liveTribes(c);
  if (ts.length < 2) return;
  // 고대 문명의 문턱에 이르면 하나로 뭉치려는 힘이 세진다
  const unify = c.tech >= ERAS[2].tech * 0.8 ? 3 : c.era >= 1 ? 1.4 : 1;
  for (let i = 0; i < ts.length; i++) for (let j = i + 1; j < ts.length; j++) {
    const A = ts[i], B = ts[j];
    if (A.gone || B.gone) continue;
    const LA = chiefOf(world, A), LB = chiefOf(world, B);
    const km = tribeKm(world, A, B);
    const near = clamp(1.5 - km / 70, 0.2, 1.5);
    let r = A.rel[B.id] ?? 0;
    const warm = ((LA?.tr.A ?? 50) + (LB?.tr.A ?? 50)) / 2, greed = Math.max(LA?.amb ?? 50, LB?.amb ?? 50);
    const hunger = hungry(world, A) || hungry(world, B);
    // 저절로: 족장들의 성품 쪽으로 천천히. 사람이 늘수록 땅이 비좁아 부딪친다
    const crowd = clamp((tribePeople(world, A).length + tribePeople(world, B).length) / Math.max(8, km) * 6, 0, 30);
    r += ((warm - 50) * 0.6 - (greed - 50) * 0.4 - (hunger ? 10 : 0) - crowd - r) * 0.012;
    // 오가며 배운다: 사이가 좋을수록, 가까울수록
    for (const [P, Q] of [[A, B], [B, A]]) {
      const teach = (P.known || []).filter(d => !(Q.known || []).includes(d) && EARLY_TECH[d]?.req.every(x => (Q.known || []).includes(x)));
      if (teach.length && chance((0.012 + Math.max(0, r) / 2500) * near)) {
        const d = pick(teach);
        learn(world, c, Q, d);
        world.news('tech', `🗣️ ${tribeLabel(Q)}이(가) 이웃 ${tribeLabel(P)}에게서 '${d}'을(를) 배웠다`, { country: c.code, sid: Q.home }, 1);
        for (const a of tribePeople(world, Q)) if (chance(0.3)) remember(world, a, `이웃 ${tribeLabel(P)}에게서 ${EARLY_TECH[d]?.how?.split(' — ')[0] || d}을(를) 배웠다`, 3, 0.4, [], 'discovery');
      }
    }
    if (chance(0.07 * near)) {
      const pa = tribePeople(world, A).length, pb = tribePeople(world, B).length;
      const big = pa >= pb ? A : B, small = big === A ? B : A, ratio = Math.max(pa, pb) / Math.max(1, Math.min(pa, pb));
      const pw = (t) => power(world, fighters(world, t), chiefOf(world, t), false, t.known || []);
      const strength = pw(big) / Math.max(0.1, pw(small));
      const kind = weighted(['gift', 'marry', 'quarrel', 'raid', 'union', 'yield'], k => ({
        gift: Math.max(0, 35 + r) * (warm / 50),
        marry: r > 0 ? 8 + r * 0.25 : 0,
        quarrel: Math.max(2, 14 + (hunger ? 20 : 0) + (greed - 50) * 0.3 - r * 0.15),
        raid: r < -25 ? (-r - 20) * (greed / 50) * (hunger ? 2 : 1) * unify : 0,
        union: r > 50 && (ratio > 1.3 || unify > 2) ? 10 * unify : 0,
        yield: r < -35 && strength > 1.6 ? 8 * unify : 0,
      })[k]);
      if (kind === 'gift') {
        const [g, h] = chance(0.5) ? [A, B] : [B, A];
        A.rel[B.id] = B.rel[A.id] = r;
        sendGift(world, c, g, h);
        r = A.rel[B.id];
      } else if (kind === 'marry') {
        A.rel[B.id] = B.rel[A.id] = r;
        if (marryTribes(world, c, A, B)) r = A.rel[B.id];
      } else if (kind === 'quarrel') {
        r -= 7 + rand() * 8;
        const why = pick(hunger ? ['얼마 남지 않은 사냥감', '도토리 숲', '물고기 많은 여울'] : ['사냥터 경계', '샘물', '길 잃은 짐승', '모욕적인 말 한마디']);
        world.news('town', `😠 ${wa(tribeLabel(A))} ${tribeLabel(B)}이(가) ${why}을(를) 두고 다퉜다`, { country: c.code, sid: A.home }, 1);
        const fa = fighters(world, A, 0.1), fb = fighters(world, B, 0.1);
        for (const a of fa) remember(world, a, `${tribeLabel(B)} 사람들과 ${why} 때문에 다퉜다`, 4, -0.4, [], 'war');
        for (const a of fb) remember(world, a, `${tribeLabel(A)} 사람들과 ${why} 때문에 다퉜다`, 4, -0.4, [], 'war');
        if (chance(0.25) && fa[0] && fb[0]) hurt(world, chance(0.5) ? fa[0] : fb[0], false, '부족 간 몸싸움');
      } else if (kind === 'raid') {
        // 더 굶주렸거나 더 사나운 족장이 먼저 친다
        const score = t => (hungry(world, t) ? 30 : 0) + (chiefOf(world, t)?.amb || 50) - (chiefOf(world, t)?.tr.A || 50) * 0.5 + rand() * 20;
        const [att, def] = score(A) >= score(B) ? [A, B] : [B, A];
        // 족장이 플레이어의 앤이면 묻는다 (칠까, 달랠까, 참을까). 아니면 채비를 한다
        const LA = chiefOf(world, att);
        if (!(LA?.owner && consult(world, LA, 'chief_raid', { c: c.code, foe: def.id }))) planRaid(world, c, att, def);
        r -= 6;
      } else if (kind === 'union') {
        r += 5;
        absorb(world, c, big, small, '연합');
      } else if (kind === 'yield') {
        const Ls = chiefOf(world, small);
        // 작은 부족의 족장: 겁이 많거나 사람들을 아끼면 싸우지 않고 굽힌다. 아니면 맞서 싸운다
        if (!Ls || Ls.tr.N > 55 || Ls.tr.A > 60 || chance(0.3)) absorb(world, c, big, small, '굴복');
        else {
          world.news('war', `🛡️ ${tribeLabel(small)}의 족장 ${neun(Ls.name)} ${tribeLabel(big)}의 굴복 요구를 거절했다`, { country: c.code, anns: [Ls.id] }, 2);
          const res = clash(world, c, big, small, '전투');
          if (res?.win && chance(0.6)) absorb(world, c, big, small, '굴복');
          r -= 10;
        }
      }
    }
    if (!A.gone && !B.gone) { A.rel[B.id] = B.rel[A.id] = clamp(r, -100, 100); }
  }
  world.dirtyCountries.add(c.code);
}

// ── 나라 세우기 ──
export function foundNation(world, c, t, chosen = null) {
  if (c.nation || t.gone) return;
  const L = chiefOf(world, t) || tribePeople(world, t).filter(a => adult(world, a)).sort((x, y) => world.influence(y) - world.influence(x))[0];
  if (!L) return;
  const land = c.land || landName(c.code);
  const name = chosen || nationName(world, c, t, L);
  c.namingAsk = null;
  c.nation = { name, founded: Math.round(world.clock), founder: L.id, founderName: L.name, tribe: t.id };
  c.name = name;
  c.capital = t.home;
  for (const d of t.known || []) if (!c.discoveries.includes(d)) c.discoveries.push(d);
  c.history.push({ t: Math.round(world.clock), text: `${L.name}이(가) ${reul(land)} 하나로 묶고 '${name}'을(를) 세웠다`, icon: '🏯' });
  world.news('nation', `🏯 ${tribeLabel(t)}의 ${ga(L.name)} ${reul(land)} 하나로 묶고 나라를 세웠다 — 나라 이름은 '${name}'`, { country: c.code, anns: [L.id], sid: t.home }, 4);
  for (const a of tribePeople(world, t)) if (adult(world, a)) {
    remember(world, a, `우리 땅에 '${name}'이라는 나라가 섰다`, 8, 0.5, [L.id], 'power');
    if (chance(0.3)) think(world, a, pick([`이제 우리는 ${name} 사람이다.`, '부족끼리 싸우던 때는 끝났으면 좋겠다.', '나라라는 게 뭘까. 배는 덜 고파질까?']));
  }
  think(world, L, `${name}… 내가 지은 이름이 이 땅의 이름이 되었다.`);
  world.setLeader(c, L, '건국');
  world.dirtyCountries.add(c.code);
}

/** 하나로 묶으려는 족장: 땅이 고대 문명의 문턱에 이르면 가장 세고 야심 찬 부족이 이웃 부족들을 하나씩 품는다 */
const tribePower = (world, t) => power(world, fighters(world, t), chiefOf(world, t), false, t.known || []);
export const tribeStrength = tribePower;
export function absorbTribe(world, c, win, lose, how, story = null) { if (!win.gone && !lose.gone) absorb(world, c, win, lose, how, story); }
/** 말로 하나가 되자고 청한다 — 받아들이면 연합 */
export function proposeUnion(world, c, lead, target) {
  const L = chiefOf(world, lead), T = chiefOf(world, target);
  const r = lead.rel[target.id] ?? 0, ratio = tribePower(world, lead) / Math.max(0.1, tribePower(world, target));
  if (r >= 10 || (ratio > 1.4 && T && T.tr.A > 55) || !T || (ratio > 2.2 && chance(0.5))) {
    absorb(world, c, lead, target, '연합', `${tribeLabel(lead)}의 ${ga(L?.name || '족장')} ${tribeLabel(target)}에 하나가 되자고 청했고, ${tribeLabel(target)}이(가) 받아들였다 — 두 부족이 하나로 합쳤다`);
    return true;
  }
  lead.rel[target.id] = target.rel[lead.id] = clamp(r - 6, -100, 100);
  world.news('power', `🙅 ${tribeLabel(target)}이(가) ${tribeLabel(lead)}의 하나가 되자는 청을 거절했다`, { country: c.code, anns: [L?.id, T?.id].filter(x => x != null) }, 2);
  return false;
}
/** 굴복을 요구한다 — 상대 족장이 판단한다 (플레이어의 앤이면 묻는다) */
export function demandYield(world, c, lead, target) {
  const L = chiefOf(world, lead), T = chiefOf(world, target);
  if (T?.owner && consult(world, T, 'chief_yield', { c: c.code, from: lead.id })) return 'asked';
  const ratio = tribePower(world, lead) / Math.max(0.1, tribePower(world, target));
  if (!T || ratio > 2 || T.tr.N > 62 || (ratio > 1.3 && chance(0.35))) {
    if (T) world.news('war', `🏳️ ${tribeLabel(target)}의 족장 ${neun(T.name)} ${tribeLabel(lead)}의 요구에 싸우지 않고 고개를 숙였다`, { country: c.code, anns: [T.id, L?.id].filter(x => x != null) }, 2);
    absorb(world, c, lead, target, '굴복');
    return 'yield';
  }
  world.news('war', `🛡️ ${tribeLabel(target)}의 족장 ${neun(T.name)} ${tribeLabel(lead)}의 굴복 요구를 거절했다 — 싸움이 벌어졌다`, { country: c.code, anns: [T.id, L?.id].filter(x => x != null) }, 2);
  fightTribe(world, c, lead, target);
  return 'fight';
}
/** 힘으로 친다 — 이기면 대개 굴복을 받아 낸다 */
export function fightTribe(world, c, lead, target) {
  const L = chiefOf(world, lead);
  const res = clash(world, c, lead, target, '전투');
  if (!res) return null;
  const toll = [res.dead ? `${res.dead}명 숨짐` : '', res.hurt ? `${res.hurt}명 다침` : ''].filter(Boolean).join(', ');
  world.news('war', `⚔️ ${wa(tribeLabel(lead))} ${tribeLabel(target)}의 싸움 — ${res.win ? `${tribeLabel(lead)}이(가) 이겼다` : `${tribeLabel(target)}이(가) 막아 냈다`}${toll ? ` (${toll})` : ''}`, { country: c.code, sid: target.home }, 2);
  c.history.push({ t: Math.round(world.clock), text: `${wa(tribeLabel(lead))} ${tribeLabel(target)}의 싸움 — ${res.win ? tribeLabel(lead) : tribeLabel(target)} 승리`, icon: '⚔️' });
  lead.rel[target.id] = target.rel[lead.id] = clamp((lead.rel[target.id] ?? 0) - 15, -100, 100);
  if (res.win && chance(0.65)) absorb(world, c, lead, target, '굴복');
  else if (!res.win && L) { L.rep = clamp(L.rep - 8, -100, 100); remember(world, L, `${wa(tribeLabel(target))}의 싸움에서 졌다`, 8, -0.7, [], 'war'); }
  return res;
}
/** 땅에서 가장 세고 야심 찬 부족 */
function unifier(world, ts) {
  const drive = t => { const L = chiefOf(world, t); return tribePower(world, t) * (0.6 + (L ? L.amb + L.sk.leadership * 0.5 : 40) / 150); };
  return ts.slice().sort((x, y) => drive(y) - drive(x))[0];
}
export function unifyTarget(world, c, lead) {
  return liveTribes(c).filter(t => t !== lead).sort((x, y) => tribeKm(world, lead, x) - tribeKm(world, lead, y))[0] || null;
}
/** 하나로 묶으려는 족장: 땅이 고대 문명의 문턱에 이르면 가장 세고 야심 찬 부족이 이웃 부족들을 하나씩 품는다 */
function unifyDaily(world, c) {
  const ts = liveTribes(c);
  if (ts.length < 2 || c.era < 1 || c.tech < ERAS[2].tech * 0.7 || !chance(0.08)) return;
  const lead = unifier(world, ts);
  const L = chiefOf(world, lead);
  if (!L || world.clock - (lead.lastUnify || -1e12) < DAY * 12) return;
  lead.lastUnify = Math.round(world.clock);
  const target = unifyTarget(world, c, lead);
  if (!target) return;
  if (!lead.unifyNews) {
    lead.unifyNews = true;
    world.news('power', `🗺️ ${tribeLabel(lead)}의 족장 ${neun(L.name)} 이 땅의 부족들을 하나로 묶겠다고 나섰다`, { country: c.code, anns: [L.id] }, 3);
    think(world, L, '이 땅의 부족들이 하나가 되어야 한다. 그 일을 내가 하겠다.');
  }
  // 내 앤이 그 족장이면 어떻게 묶을지 묻는다
  if (L.owner && consult(world, L, 'chief_unify', { c: c.code, target: target.id })) return;
  const T = chiefOf(world, target);
  const r = lead.rel[target.id] ?? 0, ratio = tribePower(world, lead) / Math.max(0.1, tribePower(world, target));
  // 사이가 괜찮거나, 상대 족장이 너그럽고 힘이 밀리면 말로 합친다. 아니면 굴복을 요구한다
  if (r >= 10 || (ratio > 1.4 && T && T.tr.A > 55) || !T) proposeUnion(world, c, lead, target);
  else demandYield(world, c, lead, target);
}

/** 매일: 땅의 부족들 (나라가 서기 전) */
export function tribesDaily(world, c) {
  if (!c.tribes) return;
  if (!c.nation) for (const t of liveTribes(c)) chiefDaily(world, c, t);
  discoveryDaily(world, c);
  if (c.nation) return;
  for (const t of liveTribes(c)) if (!tribePeople(world, t).length && world.clock - t.founded > DAY * 5) {
    t.gone = true; t.goneHow = '흩어짐'; t.goneAt = Math.round(world.clock);
    world.news('city', `🍂 ${tribeLabel(t)}은(는) 이제 남은 사람이 없다`, { country: c.code }, 1);
  }
  relationsDaily(world, c);
  raidsDaily(world, c);
  unifyDaily(world, c);
  const ts = liveTribes(c);
  // 땅이 한 부족으로 묶이면, 농경 시대에 들어선 뒤 그 족장이 나라를 세운다
  if (ts.length === 1 && c.era >= 1 && tribePeople(world, ts[0]).length >= 12) {
    // 내 앤이 나라를 세우는 족장이면, 나라 이름을 묻는다 (하루 반 안에 답이 없으면 앤이 짓는다)
    if (c.namingAsk && world.clock - c.namingAsk < DAY * 1.6) return;
    const L = chiefOf(world, ts[0]);
    if (!c.namingAsk && L?.owner) {
      const names = [...new Set(Array.from({ length: 8 }, () => nationName(world, c, ts[0], L)))].slice(0, 4);
      if (names.length >= 2 && consult(world, L, 'name_nation', { c: c.code, tribe: ts[0].id, names })) { c.namingAsk = Math.round(world.clock); return; }
    }
    foundNation(world, c, ts[0]);
  }
}

// ── 나라 사이 ──
function capitalOf(world, c) { return world.settlements.get(c.capital) || [...world.settlements.values()].find(s => s.country === c.code && !s.abandoned); }
function nationPower(world, c, share) {
  const pref = a => (a.warPref && a.warPref.until > world.clock ? a.warPref.v : null);
  const people = [...world.anns.values()].filter(a => a.alive && a.country === c.code && !a.travel && ageOf(a, world.clock) >= 18 && ageOf(a, world.clock) <= 45 && (a.sick || 0) < 0.5 && pref(a) !== 'stay');
  people.sort((x, y) => (pref(y) === 'go' ? 1000 : 0) + (y.job?.id === 'soldier' ? 100 : 0) + y.ab.ath - ((pref(x) === 'go' ? 1000 : 0) + (x.job?.id === 'soldier' ? 100 : 0) + x.ab.ath));
  const army = people.slice(0, Math.max(3, Math.ceil(people.length * share)));
  const L = c.leader != null ? world.anns.get(c.leader) : null;
  const p = army.reduce((s, a) => s + 0.6 + a.ab.ath / 120 + (a.job?.id === 'soldier' ? 0.8 : 0), 0) * Math.sqrt(ERAS[c.era].prod) * (1 + (L?.sk.leadership || 0) / 300);
  return { army, p };
}
function battle(world, A, B, war) {
  const a = nationPower(world, A, 0.12), b = nationPower(world, B, 0.12);
  if (!a.army.length || !b.army.length) return;
  const att = war.att === A.code ? A : B, def = att === A ? B : A;
  const pa = att === A ? a.p : b.p, pd = (att === A ? b.p : a.p) * 1.2;
  const win = chance(clamp(pa / (pa + pd) + gauss() * 0.1, 0.1, 0.9));
  const [winN, loseN] = win ? [att, def] : [def, att];
  const armyOf = n => (n === A ? a.army : b.army);
  let dead = { [A.code]: 0, [B.code]: 0 };
  const losses = (n, k) => { for (const x of armyOf(n).sort(() => rand() - 0.5).slice(0, k)) if (hurt(world, x, chance(0.5), `${war.name}`) === 'dead') dead[n.code]++; };
  losses(loseN, 2 + Math.floor(rand() * 4)); losses(winN, Math.floor(rand() * 3));
  // 싸우러 나간 사람들은 며칠 집을 비운다
  for (const n of [A, B]) for (const x of armyOf(n)) if (x.alive && !x.travel) { x.act = null; x.travel = { until: Math.round(world.clock + DAY * (1 + rand())), from: x.sid, to: x.sid, kind: 'war', where: war.name }; world.dirtyAnnMeta.add(x.id); remember(world, x, `${war.name}에 나가 싸웠다 — ${x.country === winN.code ? '이겼다' : '졌다'}`, 8, x.country === winN.code ? 0.3 : -0.7, [], 'war'); }
  war.score += winN === att ? 1 : -1;
  war.battles++;
  const toll = dead[A.code] + dead[B.code] ? `${A.name} ${dead[A.code]}명, ${B.name} ${dead[B.code]}명 전사` : '전사자 없음';
  world.news('war', `⚔️ ${war.name}: ${winN.name}이(가) 이겼다 (${toll})`, { country: winN.code }, 3);
}
function endWar(world, A, B, war, why) {
  const att = war.att === A.code ? A : B, def = att === A ? B : A;
  const winner = war.score > 0 ? att : war.score < 0 ? def : null, loser = winner && (winner === A ? B : A);
  let text;
  if (winner) {
    const tribute = Math.round(Math.max(0, loser.treasury) * 0.25);
    loser.treasury -= tribute; winner.treasury += tribute;
    const cs = capitalOf(world, loser), cw = capitalOf(world, winner);
    if (cs && cw) { const f = Math.round(cs.food * 0.2); cs.food -= f; cw.food += f; }
    text = `${ga(war.name)} 끝났다 — ${ga(loser.name)} ${winner.name}에 ${tribute ? `공물을 바치기로 했다` : '고개를 숙였다'}`;
  } else text = `${ga(war.name)} ${why === '지쳐서' ? '양쪽 모두 지쳐' : '왕이 세상을 떠나'} 끝났다 — 승자 없이 강화를 맺었다`;
  for (const n of [A, B]) {
    delete n.wars[n === A ? B.code : A.code];
    n.history.push({ t: Math.round(world.clock), text, icon: '🕊️' });
    n.drel[n === A ? B.code : A.code] = -20;
    world.dirtyCountries.add(n.code);
  }
  world.news('war', `🕊️ ${text}`, { country: A.code }, 3);
}
export function declareWar(world, att, def) {
  att.wars = att.wars || {}; def.wars = def.wars || {};
  if (att.wars[def.code]) return null;
  const name = `${att.name}-${def.name} 전쟁`;
  const w = { att: att.code, since: Math.round(world.clock), score: 0, battles: 0, name };
  att.wars[def.code] = w; def.wars[att.code] = w;
  for (const n of [att, def]) n.history.push({ t: Math.round(world.clock), text: `${ga(att.name)} ${def.name}에 전쟁을 선포했다`, icon: '⚔️' });
  world.news('war', `⚔️ ${ga(att.name)} ${def.name}에 전쟁을 선포했다!`, { country: att.code }, 4);
  for (const a of world.anns.values()) if (a.alive && (a.country === att.code || a.country === def.code) && ageOf(a, world.clock) >= 12) {
    if (chance(0.6)) { remember(world, a, `${ga(att.name)} ${def.name}에 전쟁을 선포했다`, 7, -0.5, [], 'war'); feel(a, -10, '불안', '전쟁'); }
    // 플레이어의 앤: 싸우러 나갈지 묻는다
    const g = ageOf(a, world.clock);
    if (a.owner && g >= 18 && g <= 45) consult(world, a, 'war_enlist', { c: a.country, war: name, foe: a.country === att.code ? def.code : att.code });
  }
  world.dirtyCountries.add(att.code); world.dirtyCountries.add(def.code);
  return w;
}
export function nationsDaily(world) {
  const ns = Object.values(world.countries).filter(c => c.nation && c.pop >= 10);
  for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
    const A = ns[i], B = ns[j];
    const ca = capitalOf(world, A), cb = capitalOf(world, B);
    if (!ca || !cb) continue;
    const km = kmBetween([ca.lon, ca.lat], [cb.lon, cb.lat]);
    const reach = Math.max(A.era, B.era) >= 3 ? 3000 : 1300;
    if (km > reach) continue;
    A.drel = A.drel || {}; B.drel = B.drel || {}; A.wars = A.wars || {}; B.wars = B.wars || {};
    let r = A.drel[B.code] ?? clamp(10 - km / 80 + gauss() * 10, -40, 40);
    const war = A.wars[B.code];
    const LA = A.leader != null ? world.anns.get(A.leader) : null, LB = B.leader != null ? world.anns.get(B.leader) : null;
    if (war) {
      if (chance(1 / 9)) battle(world, A, B, war);
      const days = (world.clock - war.since) / DAY;
      if (Math.abs(war.score) >= 4) endWar(world, A, B, war, '승패');
      else if (days > 40 && chance(0.04 + days / 3000)) endWar(world, A, B, war, '지쳐서');
      else if ((!LA || !LB) && chance(0.2)) endWar(world, A, B, war, '왕의 죽음');
      continue;
    }
    const warm = ((LA?.tr.A ?? 50) + (LB?.tr.A ?? 50)) / 2, greed = Math.max(LA?.amb ?? 50, LB?.amb ?? 50);
    const rival = km < 800 ? 12 : 4;   // 맞닿은 나라끼리는 늘 견제한다
    r += ((warm - 50) * 0.5 - (greed - 50) * 0.5 - rival - r) * 0.01;
    if (chance(0.03 * clamp(1.6 - km / 900, 0.3, 1.6))) {
      const k = weighted(['envoy', 'trade', 'border'], k => ({ envoy: Math.max(0, 25 + r), trade: Math.max(0, 20 + r * 0.5), border: Math.max(3, 22 - r * 0.3 + (greed - 50) * 0.5) })[k]);
      if (k === 'envoy') { r += 8; world.news('gov', `🕊️ ${ga(A.name)} ${B.name}에 사신을 보냈다`, { country: A.code }, 1); }
      else if (k === 'trade') { r += 5; const v = Math.round(ERAS[Math.min(A.era, B.era)].wage * 10); A.treasury += v; B.treasury += v; world.news('gov', `🐪 ${wa(A.name)} ${B.name} 사이에 물건이 오가기 시작했다`, { country: A.code }, 1); }
      else { r -= 12; world.news('war', `🚩 ${wa(A.name)} ${B.name}이(가) 국경을 두고 다퉜다`, { country: A.code }, 2); }
    }
    // 선전포고: 사이가 아주 나쁘고 한쪽 왕이 사납고 욕심이 많을 때
    const hawk = [LA, LB].map(L => L ? (L.amb - L.tr.A) : 0);
    if (r < -40 && Math.max(...hawk) > 10 && chance(0.03)) {
      const att = hawk[0] >= hawk[1] ? A : B, def = att === A ? B : A;
      const K = att === A ? LA : LB;
      // 왕이 플레이어의 앤이면 묻는다
      if (!(K?.owner && consult(world, K, 'king_war', { c: att.code, foe: def.code }))) declareWar(world, att, def);
    }
    A.drel[B.code] = B.drel[A.code] = clamp(r, -100, 100);
  }
}

/** 저장된 세계를 불러올 때: 부족이 없는 땅에 부족을 세운다 (원래 마을 + 이웃 부족 둘) */
export function migrateTribes(world, seedVillage) {
  for (const c of Object.values(world.countries)) {
    if (c.tribes) continue;
    const sets = [...world.settlements.values()].filter(s => s.country === c.code && !s.abandoned);
    c.land = landName(c.code);
    if (!sets.length) { c.tribes = {}; if (!c.nation) c.name = c.land; continue; }
    if (c.era >= 2) {
      // 이미 고대 이후인 땅은 나라가 선 것으로 본다
      c.tribes = {};
      for (const s of sets) makeTribe(world, c, s);
      const main = c.tribes['t' + (c.capital ?? sets[0].id)] || Object.values(c.tribes)[0];
      for (const t of Object.values(c.tribes)) if (t !== main) { for (const sid of t.sids) { const s = world.settlements.get(sid); s.tribe = main.id; main.sids.push(sid); } t.gone = true; t.goneTo = main.id; t.goneHow = '연합'; t.sids = []; }
      main.known = [...c.discoveries];
      const L = c.leader != null ? world.anns.get(c.leader) : null;
      c.nation = { name: c.name, founded: c.founded || 0, founder: L?.id ?? null, founderName: L?.name || '', tribe: main.id };
      continue;
    }
    const L = c.leader != null ? world.anns.get(c.leader) : null;
    c.name = c.land;
    c.leader = null;
    c.tribes = {};
    // 처음 마을과, 거기서 갈라져 나간 마을들은 한 부족이다
    const base = sets.slice().sort((x, y) => world.residents(y.id).length - world.residents(x.id).length || x.founded - y.founded)[0];
    c.capital = base.id;
    const mine = makeTribe(world, c, base);
    mine.known = [...c.discoveries];
    if (sets.some(s => s.buildings.some(id => world.buildings.get(id)?.type === 'cottage'))) for (const d of ['석기 도구', '나무 다루기', '흙벽 쌓기']) if (!mine.known.includes(d)) mine.known.push(d);
    for (const s of sets) if (s !== base) { s.tribe = mine.id; mine.sids.push(s.id); world.dirtySettlements.add(s.id); }
    if (L?.alive) { mine.chief = L.id; mine.since = c.leaderSince || Math.round(world.clock); }
    for (const d of mine.known) if (!c.discoveries.includes(d)) c.discoveries.push(d);
    // 부족 시대가 새로 시작된다: 나라 없이 쌓인 앎은 고대의 문턱 한참 앞에서
    c.tech = Math.min(c.tech, ERAS[1].tech * 1.2);
    // 이웃 부족 둘이 이미 근처에 살고 있었다
    const want = chance(0.5) ? 2 : 3;
    for (let i = 0; i < want; i++) {
      let best = null;
      for (let k = 0; k < 16; k++) {
        const p = randomLandPoint(c.code, [base.lon, base.lat], 0.22, 1.1);
        const all = [...world.settlements.values()].filter(s => s.country === c.code);
        const dmin = Math.min(...all.map(s => kmBetween(p, [s.lon, s.lat])));
        if (dmin < 14) continue;
        const sc = (seaNear(p[0], p[1]) ? 1.2 : 0) + Math.min(dmin, 50) / 25 - Math.max(0, dmin - 90) / 40 + rand();
        if (!best || sc > best.sc) best = { p, sc };
      }
      if (!best) continue;
      const s = makeSettlement(world, c, best.p[0], best.p[1], null);
      const t = makeTribe(world, c, s);
      // 이웃 부족은 원시 시대 기술 몇 가지만 안다 (농사는 아직)
      t.known = ['불의 사용', '석기 도구', ...(chance(0.6) ? ['나무 다루기'] : []), ...(chance(0.4) ? ['사냥 기술'] : [])];
      seedVillage(world, c, s, tribePlan());
      world.news('city', `🏕️ ${base.name} 너머에 ${tribeLabel(t)}이(가) 살고 있었다`, { country: c.code, sid: s.id }, 2);
    }
    c.tribesAtStart = Object.keys(c.tribes).length;
    world.rebuildResidents();
    for (const t of liveTribes(c)) if (t.chief == null) chiefDaily(world, c, t, true);
    world.dirtyCountries.add(c.code);
  }
}

/** 예전 저장본: 원시·농경 시대인데 회사(농장·건설·목재소·기와·공방)가 있으면 마을 공동 일로 돌린다 */
export function migrateEarlyEconomy(world) {
  let n = 0;
  const closed = {};
  for (const co of world.companies.values()) {
    if (co.dead || co.pub) continue;
    const c = world.countries[co.country];
    const def = INDUSTRIES[co.ind];
    if (!c || !def || def.era <= c.era) continue;
    const s = world.settlements.get(co.sid);
    co.dead = true; co.ceo = null;   // 대표가 빠져도 새 대표를 뽑지 않게 먼저
    (closed[c.code] = closed[c.code] || []).push(co.name);
    for (const id of [...co.emp]) {
      const e = world.anns.get(id);
      if (!e || e.job?.co !== co.id) continue;
      leaveJob(world, e, null, true);
      if (co.ind === 'farm' && s && knows(world, s, '농업')) setJob(world, e, 'peasant');
    }
    co.emp = [];
    co.dead = true; co.closed = Math.round(world.clock); co.migrated = true;
    const boss = co.founder != null && world.anns.get(co.founder);
    if (boss?.alive && co.money > 0) boss.money += co.money;
    for (const bid of [co.b, ...(co.bx || [])]) {
      const b = world.buildings.get(bid);
      if (!b) continue;
      b.co = null; b.owner = null;
      if (b.type === 'farm') b.name = `${s?.name || ''} 공동 논밭`;
      else if (!b.done) { world.buildings.delete(b.id); if (s) s.buildings = s.buildings.filter(x => x !== b.id); }
      world.dirtyBuildings.add(b.id);
    }
    world.dirtyCompanies.add(co.id);
    n++;
  }
  // 회사가 맡던 공사도 이제 마을 사람들이 손수
  for (const b of world.buildings.values()) if (b.contractor && world.companies.get(b.contractor)?.migrated) b.contractor = null;
  for (const [code, names] of Object.entries(closed)) {
    const c = world.countries[code];
    world.news('town', `🌾 ${c.name}: ${ERAS[c.era].name}의 마을엔 회사가 없다 — ${names.slice(0, 4).join('·')}${names.length > 4 ? ' 등' : ''}에서 일하던 사람들은 이제 마을 사람들과 함께 농사짓고 집을 짓는다`, { country: code }, 2);
  }
  return n;
}

/** 클라이언트용 */
export function tribesOut(world, c) {
  if (!c.tribes) return [];
  return Object.values(c.tribes).filter(t => !t.gone || world.clock - (t.goneAt || 0) < YEAR * 3).map(t => {
    const L = t.chief != null ? world.anns.get(t.chief) : null;
    return { id: t.id, name: t.name, color: t.color, home: t.home, sids: t.sids, pop: t.gone ? 0 : (t.pop ?? tribePeople(world, t).length),
      chief: L && L.alive && !t.gone ? { id: L.id, name: L.name, wits: Math.round(chiefWits(L)) } : null, known: t.known || [],
      rel: Object.fromEntries(Object.entries(t.rel || {}).filter(([k]) => c.tribes[k] && !c.tribes[k].gone).map(([k, v]) => [k, Math.round(v)])),
      wins: t.wins || 0, losses: t.losses || 0, gone: !!t.gone, goneTo: t.goneTo || null, goneHow: t.goneHow || null };
  });
}
