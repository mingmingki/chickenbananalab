// 앤의 마음: 욕구 → 지각 → 고민(선택지마다 이유와 점수) → 행동 → 결과 → 기억/감정 → 밤마다 되돌아보기
import { clamp, rand, pick, chance, gauss, ageOf, localHour, YEAR, DAY, ga, neun, reul, wa, ro, callName, fixJosa } from './util.js';
import { remember, think, feel, lifeEvent, ageStage } from './ann.js';
import { JOBS, ERAS, BUILDINGS, HOME_BY_ERA, INDUSTRIES, GOALS, SHIFTS } from './data.js';
import { rel, converse, attraction, updateKind } from './social.js';
import {
  openings, apply, jobFit, produce, dailyCost, wageBase, bestIndustry, startupCost, foundCompany, jobTitle, leaveJob, hasConstruction, assignContractor,
} from './economy.js';
import { plazaPoint, wildPoint, shorePoint, doorPoint, placeBuilding, addWork, siteFeatures, buildRule } from './settlement.js';
import { compose } from './thoughts.js';
import { segCenter } from './infra.js';
import { book, monthNet, refMonth, mealPrice, MEAL_LABEL, LEISURE_LABEL } from './budget.js';

export const ACTS = {
  sleep: { label: '잠', emoji: '💤' },
  eat: { label: '식사', emoji: '🍚' },
  work: { label: '일', emoji: '💼' },
  seek_job: { label: '일자리 찾기', emoji: '📋' },
  found_co: { label: '창업 준비', emoji: '🏢' },
  build: { label: '집 짓기', emoji: '🔨' },
  social: { label: '만나러 가는 중', emoji: '🚶' },
  talk: { label: '대화', emoji: '💬' },
  date: { label: '데이트', emoji: '💕' },
  family: { label: '가족과 시간', emoji: '🏠' },
  research: { label: '연구', emoji: '🔬' },
  art: { label: '창작', emoji: '🎨' },
  exercise: { label: '운동', emoji: '🏃' },
  relax: { label: '휴식', emoji: '☕' },
  wander: { label: '산책', emoji: '🌿' },
  reflect: { label: '사색', emoji: '💭' },
  study: { label: '공부', emoji: '📚' },
  play: { label: '놀이', emoji: '🧸' },
  baby: { label: '아기', emoji: '🍼' },
  forage: { label: '먹을거리 찾기', emoji: '🧺' },
  lead: { label: '나랏일', emoji: '👑' },
  shop: { label: '장보기', emoji: '🛍️' },
  migrate: { label: '개척 여행', emoji: '🧭' },
  rest_sick: { label: '요양', emoji: '🤒' },
};

const AVAILABLE = new Set(['relax', 'wander', 'reflect', 'shop', 'exercise', 'art', 'play', 'eat', 'research', 'social']);
const HEAVY = new Set(['work', 'build', 'exercise', 'forage', 'migrate']);

export function isAvailable(world, b) {
  if (!b.alive || b.travel) return false;
  if (!b.act) return true;
  if (b.act.type === 'social' && b.act.ph === 'move') return true;
  return AVAILABLE.has(b.act.type) && b.act.type !== 'social';
}

function speedOf(world, a) {
  const age = ageOf(a, world.clock);
  let v = 62 + a.ab.ath * 0.3;
  if (age < 8) v *= 0.6;
  if (age > 70) v *= 0.7;
  return v; // m/분
}

// ── 욕구 ──
function needs(world, a, dt) {
  const t = a.act?.type;
  const sleeping = t === 'sleep' || (t === 'baby' && a.act.sub === 'sleep');
  a.nd.food = clamp(a.nd.food - (sleeping ? 0.035 : 0.068) * dt);
  if (!sleeping) a.nd.energy = clamp(a.nd.energy - (HEAVY.has(t) ? 0.062 : 0.045) * dt);
  if (t !== 'talk' && t !== 'date' && t !== 'family' && t !== 'play') a.nd.social = clamp(a.nd.social - 0.028 * (0.4 + a.tr.E / 80) * dt);
  if (t !== 'relax' && t !== 'play' && t !== 'art' && t !== 'wander') a.nd.fun = clamp(a.nd.fun - (t === 'work' ? 0.04 : 0.024) * dt);
  // 기분은 욕구 충족과 성격의 기저선 쪽으로 천천히 돌아간다
  const nAvg = (a.nd.food + a.nd.energy + a.nd.social + a.nd.fun) / 4;
  const base = 8 + (50 - a.tr.N) * 0.35 + (nAvg - 55) * 0.5 - a.stress * 0.25 + (a.home ? 4 : -8) + (a.health - 70) * 0.15;
  a.mood += (base - a.mood) * Math.min(1, 0.0035 * dt);
  a.stress = clamp(a.stress + ((a.tr.N * 0.25) - a.stress) * 0.0008 * dt);
  if (a.nd.food < 4) { a.health = clamp(a.health - 0.004 * dt); a.stress = clamp(a.stress + 0.01 * dt); }
  if (a.emo.i > 0) a.emo.i = Math.max(0, a.emo.i - 0.0025 * dt);
  if (a.emo.i < 0.08 && a.emo.k !== '평온') a.emo = { k: a.mood > 25 ? '즐거움' : a.mood < -25 ? '우울' : '평온', i: 0.1, why: '' };
}

// ── 행동 시작 ──
// ── 오늘 하루 (현지 시각 기준 일과 기록) ──
const LOG = {
  sleep: ['😴', '잠자리에 들었다'], work: ['💼', '일'], study: ['🎒', '등교'], eat: ['🍚', '식사'], talk: ['💬', '대화'], date: ['💕', '데이트'],
  family: ['🏠', '가족과 시간'], exercise: ['🏃', '운동'], relax: ['☕', '휴식'], wander: ['🌿', '산책'], shop: ['🛍️', '장보기'], research: ['🔬', '연구'],
  art: ['🎨', '창작'], play: ['🧸', '놀이'], build: ['🔨', '집 짓기'], forage: ['🧺', '먹을거리 찾기'], seek_job: ['📋', '일자리 찾기'], reflect: ['💭', '사색'],
  lead: ['👑', '나랏일'], rest_sick: ['🤒', '요양'],
};
export function logToday(world, a, emoji, text) {
  const s = world.settlements.get(a.sid);
  const lh = world.clock / 60 + (s?.lon || 0) / 15;
  const key = Math.floor(lh / 24);
  if (a.todayKey !== key) { a.yesterday = a.today; a.today = []; a.todayKey = key; }
  text = fixJosa(text);
  const last = a.today[a.today.length - 1];
  if (last && last.x === text) return;
  a.today.push({ h: Math.round(((lh % 24) + 24) % 24 * 60), e: emoji, x: text });
  if (a.today.length > 26) a.today.splice(1, 1);
}
const mealName = h => (h < 5 ? '새벽 요기' : h < 10.5 ? '아침' : h < 15 ? '점심' : h < 17 ? '간식' : '저녁');

/** 이 앤의 근무 시간표 (시대·직업·요일) */
export function shiftOf(world, a) {
  const j = JOBS[a.job.id];
  const era = world.countries[a.country].era;
  const sh = { ...(SHIFTS[j?.shift || 'office'] || SHIFTS.office) };
  if (era < 3 && sh.days === 'week') sh.days = 'six';
  if (a.job.ceo) { sh.end += 1.5; sh.days = sh.days === 'week' ? 'six' : sh.days; }
  return sh;
}
function workday(sh, dayN) {
  const dow = ((dayN % 7) + 7) % 7; // 0~4 평일, 5 토, 6 일
  return sh.days === 'all' || (sh.days === 'six' && dow < 6) || (sh.days === 'week' && dow < 5);
}

function start(world, a, type, dur, place, extra = {}) {
  const s = world.settlements.get(a.sid);
  const L = LOG[type];
  if (L && type !== 'talk' && type !== 'work' && type !== 'eat' && type !== 'study') logToday(world, a, L[0], L[1]);
  let tx = a.x, tz = a.z;
  if (Array.isArray(place)) [tx, tz] = place;
  else if (place === 'home') {
    const h = a.home && world.buildings.get(a.home);
    [tx, tz] = h ? doorPoint(h) : (() => { const cf = world.campfireOf(s); return cf ? doorPoint(cf) : plazaPoint(s, 20); })();
  } else if (place === 'plaza') [tx, tz] = plazaPoint(s);
  else if (place === 'wild') [tx, tz] = wildPoint(s);
  else if (typeof place === 'number') {
    const b = world.buildings.get(place);
    if (b) [tx, tz] = doorPoint(b);
  }
  a.act = { type, ph: 'move', tx, tz, dur, until: 0, started: Math.round(world.clock), ...extra };
  world.dirtyAct.add(a.id);
}

/** 순찰 지점: 길 위의 아무 곳 */
function patrolPoint(world, a) {
  const s = world.settlements.get(a.sid);
  const keys = Object.keys(s.roads || {});
  if (!keys.length) return plazaPoint(s, 30);
  const k = pick(keys), [x, z] = segCenter(k), t = (rand() - 0.5) * 90;
  return k[0] === 'h' ? [x + t, z + (rand() - 0.5) * 4] : [x + (rand() - 0.5) * 4, z + t];
}
const isGuard = a => a.job && (a.job.id === 'police' || a.job.id === 'soldier');

function workPlace(world, a) {
  const j = JOBS[a.job.id];
  const co = a.job.co && world.companies.get(a.job.co);
  if (a.job.site) { const site = world.buildings.get(a.job.site); if (site && !site.done) return site.id; }
  // 경찰·포졸은 절반쯤은 동네를 돌며 순찰한다
  if (isGuard(a) && chance(0.6)) return patrolPoint(world, a);
  if (co) return co.b;
  const s = world.settlements.get(a.sid);
  if (a.job.id === 'fisher') return shorePoint(s) || wildPoint(s);
  if (a.job.id === 'hunter') return wildPoint(s, 'forest');
  if (a.job.id === 'gatherer') return wildPoint(s, chance(0.6) ? 'forest' : null);
  if (j.out === 'food') return 'wild';
  if (j.out === 'build') {
    const site = world.constructionSite(world.settlements.get(a.sid), a);
    return site ? site.id : 'plaza';
  }
  return 'plaza';
}

function homeWork(world, a) {
  // 자기 소유의 미완성 건물
  const s = world.settlements.get(a.sid);
  for (const bid of s.buildings) {
    const b = world.buildings.get(bid);
    if (b && !b.done && (b.owner === a.id || (a.spouse && b.owner === a.spouse))) return b;
  }
  return null;
}

// ── 고민: 모든 선택지를 점수 매기고 이유를 붙인다 ──
export function decide(world, a) {
  const now = world.clock;
  const s = world.settlements.get(a.sid);
  const country = world.countries[a.country];
  const era = country.era;
  const age = ageOf(a, now);
  const stage = ageStage(age);
  const h = localHour(now, s.lon);
  const dayN = Math.floor(now / DAY);

  // 하루 단위 기록은 이 마을의 자정(현지 시각)에 새로 시작
  const lday = Math.floor((now / 60 + s.lon / 15) / 24);
  if (a.day.key !== lday) { a.day.key = lday; a.day.work = 0; a.day.meals = 0; a.day.studied = false; a.day.led = false; a.day.lunch = null; }
  if (stage === 'baby') {
    const sub = h > 20 || h < 7 || a.nd.energy < 40 ? 'sleep' : 'awake';
    if (sub === 'sleep') a.nd.energy = clamp(a.nd.energy + 25);
    a.nd.food = clamp(a.nd.food + 30); a.nd.social = clamp(a.nd.social + 20); a.nd.fun = clamp(a.nd.fun + 20);
    start(world, a, 'baby', 90, 'home', { sub });
    return;
  }

  const opts = [];
  const add = (type, score, reasons, data = {}) => opts.push({ type, score, reasons, ...data });
  const wk = s.weather?.k || '맑음';
  const weatherBad = ['비', '폭우', '눈', '태풍', '한파', '폭설'].includes(wk);
  const cost = dailyCost(era);
  const moneyStress = clamp(((cost * 6) - a.money) / (cost * 6) * 100);
  const goal = k => a.goals.some(g => g.k === k && !g.done);
  const wakeH = wakeHour(world, a, stage);
  const sleepStart = wakeH + (stage === 'child' ? 13.5 : 16), sleepEnd = wakeH;
  const inSleep = h >= sleepStart || h < sleepEnd || (sleepStart >= 24 && h < sleepStart - 24);
  const meal = (h >= 6.5 && h < 9) || (h >= 11.5 && h < 13.5) || (h >= 17.5 && h < 20);

  // 잠
  add('sleep', (100 - a.nd.energy) * 0.95 + (inSleep ? 48 : -45) + (a.nd.energy < 15 ? 40 : 0) - (now - (a.lastWake || -1e9) < 240 && a.nd.energy > 30 ? 90 : 0),
    [`피로 ${Math.round(100 - a.nd.energy)}%`, inSleep ? `잘 시간 (${Math.floor(h)}시)` : null]);
  // 식사
  const foodLeft = s.food >= 1;
  add('eat', (100 - a.nd.food) * 1.15 - 14 + (meal ? 22 : 0) - (a.day.meals >= 3 ? 25 : 0) - (now - (a.lastMeal || -1e9) < 150 ? 40 : 0),
    [`배고픔 ${Math.round(100 - a.nd.food)}%`, meal ? '식사 시간' : null, foodLeft ? null : '먹을 것이 부족함']);
  // 먹을 것이 없으면 직접 구한다
  if (!foodLeft || s.food < s.pop * 1.5) {
    const shortage = clamp(100 - s.food / Math.max(1, s.pop) * 40);
    add('forage', 18 + shortage * 0.45 + (100 - a.nd.food) * 0.3 + a.tr.C * 0.1, ['마을 식량 부족', a.tr.A > 60 ? '모두를 위해' : '살아남아야 한다']);
  }

  const weekday = dayN % 7 < 5;
  const schoolB = a.school ? world.buildings.get(world.companies.get(a.school.org)?.b) : null;
  if (stage === 'child' || stage === 'teen' || a.school) {
    if (h >= 8.5 && h < 15.5 && weekday && !a.day.studied) {
      if (schoolB) add('study', 58 + a.tr.C * 0.22 - (a.tr.C < 30 && chance(0.15) ? 40 : 0), [`${a.school.name} 갈 시간`, a.ab.int > 60 ? '배우는 게 즐겁다' : a.tr.C < 35 ? '학교 가기 싫다…' : null], { school: schoolB });
      else if (stage !== 'adult') add('study', 30 + a.tr.C * 0.15, [a.parents.length ? '부모님께 배우기' : '혼자 익히기', '학교가 없다'], { school: null });
    }
    if (stage !== 'adult') add('play', 30 + (100 - a.nd.fun) * 0.45 + a.tr.E * 0.1 + (weatherBad ? -15 : 0), ['놀고 싶다', a.nd.fun < 40 ? '심심함' : null]);
  }

  // 일
  const adult = stage === 'adult' || stage === 'elder';
  if (a.job && adult) {
    // 근무 시간표대로: 출근 → (점심) → 퇴근. 성실하지 않으면 지각하기도 한다
    const sh = shiftOf(world, a);
    const winter = s.weather?.season === '겨울';
    const sStart = sh.start + (JOBS[a.job.id]?.shift === 'farm' && winter ? 1.5 : 0) + Math.max(0, (45 - a.tr.C) / 100), sEnd = sh.end - (JOBS[a.job.id]?.shift === 'farm' && winter ? 2 : 0);
    const lunch = sh.lunch && h >= 12 && h < 13 && a.day.lunch !== dayN;
    const hoursNeed = sEnd - sStart - (sh.lunch ? 1 : 0);
    if (workday(sh, lday) && h >= sStart - 0.25 && h < sEnd - 0.2 && a.day.work < hoursNeed - 0.2 && !lunch) {
      const next = sh.lunch && h < 12 ? 12 : sEnd;
      add('work', 92 + a.tr.C * 0.12 - (a.tr.C < 30 && chance(0.06) ? 60 : 0) - (a.sick > 30 ? 50 : 0) - (100 - a.nd.energy) * 0.15,
        [a.day.work < 0.1 ? `출근 시간 (${String(Math.floor(sStart)).padStart(2, '0')}시)` : '근무 시간', jobTitle(world, a), a.tr.C > 65 ? '책임감' : a.tr.C < 35 ? '하기 싫지만…' : null],
        { dur: Math.max(20, (next - h) * 60), sh });
    }
    if (workday(sh, lday) && lunch && a.day.work > 0.5 && a.day.lunch !== dayN) add('eat', 96, ['점심시간'], { lunch: true });
  }
  if (!a.job && adult && !a.retired && now - (a.lastSeek || -1e9) > 360) {
    add('seek_job', 38 + moneyStress * 0.45 + a.amb * 0.12 + a.tr.C * 0.1 + (age < 30 ? 8 : 0),
      ['일이 없음', moneyStress > 40 ? `재산 ${Math.round(a.money)}` : null]);
  }
  if (a.job && !a.job.ceo && adult && (a.job.sat < 45 || (!a.job.co && era >= 1)) && now - (a.lastSeek || -1e9) > DAY * (a.job.co ? 6 : 3) && chance(a.job.co ? 0.15 : 0.3)) {
    add('seek_job', 20 + (60 - a.job.sat) * 0.5 + a.amb * 0.15 + (a.job.co ? 0 : 10), [a.job.co ? '지금 일이 맞지 않는다' : '더 안정적인 일을 찾고 싶다']);
  }
  if (stage === 'teen' && age >= 17 && !a.job && era >= 1 && chance(0.2) && now - (a.lastSeek || -1e9) > 1440 * 3) {
    add('seek_job', 22 + a.amb * 0.2, ['용돈을 벌고 싶다']);
  }

  // 집
  if (adult || (stage === 'teen' && age >= 18)) {
    const own = homeWork(world, a);
    const homeB = a.home && world.buildings.get(a.home);
    const livesWithParents = homeB && homeB.owner !== a.id && a.parents.includes(homeB.owner);
    const outdated = homeB && homeB.owner === a.id && BUILDINGS[homeB.type].era < era - 1 && a.money > (BUILDINGS[HOME_BY_ERA[era]].cost || 0) * 1.5;
    if (own) add('build', 34 + a.tr.C * 0.22 + (a.home ? 0 : 22), [`${own.name} 짓는 중 (${Math.round(own.progress / own.work * 100)}%)`], { site: own.id });
    else if (!a.home || (livesWithParents && age > 22 + (100 - a.val.adventure) * 0.1) || (homeB && homeB.res.length > BUILDINGS[homeB.type].cap) || outdated) {
      let t = HOME_BY_ERA[Math.min(era, 7)];
      let why = null;
      // 기와집까지는 손수 짓는다. 벽돌집·연립·아파트는 건설회사가 있어야 한다.
      if (BUILDINGS[t].era >= 3 && !hasConstruction(world, s.id)) {
        s.need = s.need || {}; s.need.builder = (s.need.builder || 0) + 1; s.need.homes = (s.need.homes || 0) + 1;
        t = 'house'; why = '건설회사가 없어 손수 기와집을';
        if (a.home && chance(0.85)) t = null;
        if (chance(0.06)) think(world, a, pick(['연립주택은 건설회사가 있어야 짓는다는데…', '집 지어 주는 회사가 이 마을엔 없다.', '아예 건설회사를 차려 볼까?']));
      }
      // 기와·벽돌이 필요한 집인데 벽돌 가마가 없고 벽돌도 없다면 초가집으로
      if (t && BUILDINGS[t].era >= 2 && (s.bricks || 0) < 5 && ![...world.companies.values()].some(c => c.sid === s.id && c.ind === 'brickworks' && !c.dead)) {
        s.need = s.need || {}; s.need.bricks = (s.need.bricks || 0) + 1;
        if (!a.home) { t = 'cottage'; why = '벽돌이 없어 흙벽돌로'; } else t = null;
        if (chance(0.06)) think(world, a, pick(['기와집을 짓고 싶은데 벽돌과 기와를 구할 데가 없다.', '누가 벽돌 가마라도 차리면 좋을 텐데.', '흙벽돌로는 한계가 있다.']));
      }
      const c = t ? BUILDINGS[t].cost || 0 : 0;
      if (t && (a.money >= c * 0.6 || BUILDINGS[t].era <= 1)) {
        add('build', (a.home ? 22 : 48) + a.tr.C * 0.1 + (outdated ? 6 : 0) - (weatherBad ? 10 : 0), [a.home ? (outdated ? '집이 낡았다' : '독립하고 싶다') : '집이 없다', why || (c ? `비용 ${c}` : '손수 짓는다')], { newHome: t });
      }
    }
    // 품앗이: 친한 사람이 집을 짓고 있으면 거든다
    {
      let best = null;
      for (const id in a.rels) {
        const r = a.rels[id];
        if (r.f < 18 || r.a < 15) continue;
        const b = world.anns.get(+id);
        if (!b || !b.alive || b.sid !== a.sid) continue;
        const site = homeWork(world, b);
        if (site && !site.stalled && BUILDINGS[site.type]?.kind === 'home' && !site.contractor && (!best || r.a > best.r)) best = { site, b, r: r.a };
      }
      if (best && best.site.owner !== a.id) add('build', 6 + a.tr.A * 0.2 + best.r * 0.12 + (era <= 2 ? 8 : 0) - (weatherBad ? 10 : 0), ['품앗이', `${best.b.name}네 ${best.site.name} 짓기 돕기`], { site: best.site.id });
    }
    if (!world.campfireOf(s)) add('build', 70, ['모일 곳(모닥불)이 필요하다'], { civic: 'campfire' });
    const cs = world.communitySite(s);
    if (cs) add('build', 14 + a.tr.A * 0.18 + a.tr.C * 0.12 + (era <= 1 ? 10 : 0) + (cs.type === 'campfire' ? 40 : 0) + (cs.budget > 0 && moneyStress > 30 ? 10 : 0),
      ['마을 일 돕기', `${cs.name} 공사 (${Math.round(cs.progress / cs.work * 100)}%)`], { site: cs.id });
  }

  // 사회적 욕구 / 사랑
  if (stage !== 'baby') {
    const target = pickSocialTarget(world, a, s);
    if (target) {
      const { b, kind, why } = target;
      const lonely = (100 - a.nd.social);
      let sc = lonely * 0.62 * (0.55 + a.tr.E / 100) + 8 + (kind === 'date' ? 14 : 0) + (kind === 'confess' ? 22 : 0) + (kind === 'propose' ? 30 : 0) + (kind === 'comfort' ? 12 : 0);
      if (h > 9 && h < 21) sc += 5;
      add('social', sc, [lonely > 50 ? '외로움' : '이야기하고 싶다', why], { target: b.id, kind });
    }
    const fam = familyNearby(world, a);
    if (fam.length && adult) add('family', 14 + a.val.family * 0.3 + (h >= 18 && h < 21 ? 15 : 0), ['가족과 함께하고 싶다'], { target: pick(fam).id });
  }

  // 자아실현
  if (adult || stage === 'teen') {
    add('research', 1 + a.tr.O * 0.15 + a.ab.int * 0.12 + (goal('knowledge') ? 16 : 0) + (100 - a.nd.fun) * 0.05 - (100 - a.nd.energy) * 0.15,
      [a.tr.O > 60 ? '호기심' : null, goal('knowledge') ? '꿈: 위대한 발견' : null, a.ab.int > 65 ? '생각하는 게 좋다' : null]);
    add('art', 1 + a.ab.cre * 0.18 + (goal('art') ? 16 : 0) + (100 - a.nd.fun) * 0.08,
      [a.ab.cre > 60 ? '영감이 떠오른다' : null, goal('art') ? '꿈: 걸작' : null]);
    add('exercise', 3 + a.ab.ath * 0.22 + (goal('sport') ? 18 : 0) - (100 - a.nd.energy) * 0.35 + (a.health < 60 ? 6 : 0),
      [a.ab.ath > 60 ? '몸을 움직이고 싶다' : null, goal('sport') ? '꿈: 최고의 선수' : null]);
  }
  add('relax', 18 + (100 - a.nd.fun) * 0.35 + a.stress * 0.18, [a.nd.fun < 40 ? '지루함' : '쉬고 싶다', a.stress > 50 ? '스트레스' : null]);
  add('wander', 8 + a.tr.O * 0.12 + (100 - a.nd.fun) * 0.18 + a.val.adventure * 0.06 + (100 - a.nd.social) * 0.1, ['바람 쐬기', a.nd.social < 40 ? '누군가 만날지도' : null]);
  add('reflect', 4 + a.stress * 0.28 + a.tr.N * 0.08 + a.tr.O * 0.05 + (a.day.worst ? 8 : 0), [a.stress > 50 ? '마음이 복잡하다' : '생각을 정리하고 싶다']);
  if (era >= 2 && adult && a.money > cost * 4) add('shop', 4 + (100 - a.nd.fun) * 0.2 + (100 - a.tr.C) * 0.08 + a.val.wealth * 0.04 + Math.min(18, Math.log10(1 + a.money / cost) * 7), [a.money > cost * 60 ? '여유가 있다' : '사고 싶은 게 있다']);

  // 야망: 창업
  if (adult && era >= 1 && age >= 20 && age < 62 && !a.job?.ceo && now - (a.lastFound || -1e9) > DAY * 8) {
    const bi = bestIndustry(world, a);
    const nCo = world.companyCount(s.id);
    const adults = world.residents(s.id).filter(x => ageOf(x, now) >= 19).length;
    // 가게·회사는 어른 다섯 명에 하나꼴. 나머지는 그런 곳에 취직한다
    if (bi && bi.df >= 1.05 && nCo < Math.max(2, adults / 5) && a.amb > 50) {
      const c = startupCost(world, bi.ind, era);
      if (a.money >= c) {
        add('found_co', a.amb * 0.42 + (goal('company') ? 28 : goal('wealth') ? 12 : 0) + (bi.score - 50) * 0.3 + (a.job ? -8 : 6) - 6,
          ['야망', goal('company') ? '꿈: 내 회사' : null, `${INDUSTRIES[bi.ind].name} 사업 기회`], { ind: bi.ind });
      }
    }
  }
  // 지도자
  if (country.leader === a.id && !a.day.led && h >= 9 && h < 17) add('lead', 60 + a.tr.C * 0.2, [`${ERAS[era].leader}로서의 책임`]);
  // 개척
  if (a.wantsMigrate && adult) add('migrate', 55 + a.val.adventure * 0.3, ['새로운 땅에 대한 꿈', a.wantsMigrate]);
  // 아픔
  if (a.sick > 0) add('rest_sick', 40 + a.sick * 0.5, ['몸이 아프다']);

  // 날씨: 궂은 날엔 밖에 나가기 싫다
  if (weatherBad) for (const o of opts) {
    if (['wander', 'exercise', 'forage', 'play'].includes(o.type)) { o.score -= 14; o.reasons.push(`${wk}`); }
    if (['relax', 'family', 'reflect', 'art'].includes(o.type)) o.score += 6;
  } else if (wk === '맑음') for (const o of opts) if (o.type === 'wander' || o.type === 'exercise') { o.score += 5; o.reasons.push('날씨가 좋다'); }
  // 노이즈: 성실하지 않을수록 즉흥적
  const noise = 5 + (100 - a.tr.C) * 0.07;
  for (const o of opts) o.final = o.score + gauss() * noise;
  opts.sort((x, y) => y.final - x.final);
  const choice = opts[0];
  a.why = opts.slice(0, 4).map(o => ({ a: o.type, s: Math.round(o.score), r: o.reasons.filter(Boolean).slice(0, 3), k: o.kind }));
  execute(world, a, choice, { h, era, s, stage, age, inSleep });
}

function familyNearby(world, a) {
  const out = [];
  const ids = [a.spouse, ...a.kids, ...a.parents].filter(Boolean);
  for (const id of ids) {
    const b = world.anns.get(id);
    if (b && b.alive && b.sid === a.sid && isAvailable(world, b) && b.act?.type !== 'sleep') out.push(b);
  }
  return out;
}

/** 누구를 만날까: 친밀도·호감·설렘·새로움·거리를 고려 */
function pickSocialTarget(world, a, s) {
  const pool = world.residents(s.id);
  const age = ageOf(a, world.clock);
  let best = null, bs = -Infinity;
  const sample = pool.length > 40 ? pool.filter(() => rand() < 40 / pool.length) : pool;
  const known = Object.keys(a.rels).map(Number);
  const candidates = new Set(sample);
  for (const id of known) { const b = world.anns.get(id); if (b && b.sid === s.id) candidates.add(b); }
  for (const b of candidates) {
    if (b.id === a.id || !isAvailable(world, b)) continue;
    const ageB = ageOf(b, world.clock);
    if (ageB < 3) continue;
    const kidA = age < 13, kidB = ageB < 13;
    const r = a.rels[b.id];
    if (kidA !== kidB && !(r && (r.k === 'parent' || r.k === 'child' || r.k === 'sibling'))) continue;
    const d = Math.hypot(a.x - b.x, a.z - b.z);
    let sc = -d / 60 + gauss() * 8;
    let kind = 'chat', why = null;
    if (r) {
      sc += r.a * 0.35 + r.f * 0.12 + (r.k === 'best' ? 10 : 0);
      if (a.partner === b.id) { sc += 25 + r.r * 0.2; kind = 'date'; why = `${b.name} 보고 싶다`; }
      else if (r.r > 25) { sc += r.r * 0.45; why = `${b.name}에게 설렌다`; }
      if (r.a < -30) sc -= 40;
    } else {
      sc += (a.tr.O + a.tr.E) * 0.12 - 8;
      why = '새로운 사람을 알고 싶다';
    }
    if (b.stress > 60 && r && r.a > 30 && a.tr.A > 55) { kind = 'comfort'; why = `${ga(b.name)} 힘들어 보인다`; sc += 10; }
    // 고백 / 청혼
    if (r && !a.partner && !a.spouse && !b.partner && !b.spouse && r.r >= 52 && r.f >= 20 && a.conf > 35 && (r.rej || 0) < 2 && world.clock - (r.lastConf || -1e9) > DAY * 120 && chance(0.15 + a.conf / 400)) {
      kind = 'confess'; why = `${b.name}에게 마음을 전하고 싶다`; sc += 15;
    }
    if (r && a.partner === b.id && !a.spouse && r.r >= 62 && r.a >= 45 && age >= 20 && world.clock - (r.since || 0) > DAY * 45 && chance(0.06 + a.val.family / 600)) {
      kind = 'propose'; why = `${wa(b.name)} 평생을 함께하고 싶다`; sc += 20;
    }
    if (sc > bs) { bs = sc; best = { b, kind, why }; }
  }
  return best;
}

// ── 실행 ──
function execute(world, a, o, ctx) {
  const { s, era } = ctx;
  switch (o.type) {
    case 'sleep': start(world, a, 'sleep', 600, 'home', { nap: !ctx.inSleep && a.nd.energy > 15 }); thinkDecision(world, a, o); break;
    case 'eat': {
      // 점심은 일터 근처 식당에서, 저녁은 보통 집에서 (가끔 외식)
      const rest = era >= 2 && a.money > wageBase(era) * 0.3 ? nearestOf(world, s, ['restaurant', 'cafe', 'bakery'], a) : null;
      const out = rest && (o.lunch ? chance(0.75) : chance(0.12 + (a.tr.E - 50) / 300));
      const where = out ? rest.id : o.lunch && a.job ? workPlace(world, a) : 'home';
      if (o.lunch) a.day.lunch = Math.floor(world.clock / DAY);
      a.lastMeal = world.clock;
      const hh = localHour(world.clock, s.lon);
      logToday(world, a, '🍚', `${mealName(hh)} — ${out ? rest.name : o.lunch && a.job ? '일터에서' : '집에서'}`);
      start(world, a, 'eat', o.lunch ? 45 : 35, where, { rest: out ? rest.id : null });
      break;
    }
    case 'forage': {
      const shore = chance(0.5) ? shorePoint(s) : null;
      start(world, a, 'forage', 150, shore || wildPoint(s, 'forest'), { fishing: !!shore });
      thinkDecision(world, a, o); break;
    }
    case 'work': {
      const wp = workPlace(world, a);
      const site = a.job.site && world.buildings.get(a.job.site);
      if (a.day.work < 0.1) logToday(world, a, site ? '🦺' : '💼', `출근 — ${site ? site.name + ' 현장' : jobTitle(world, a)}`);
      else logToday(world, a, '💼', '오후 근무');
      start(world, a, 'work', o.dur, wp, { endH: o.sh?.end });
      thinkDecision(world, a, o); break;
    }
    case 'seek_job': a.lastSeek = world.clock; start(world, a, 'seek_job', 60, 'plaza'); thinkDecision(world, a, o); break;
    case 'study': logToday(world, a, '🎒', o.school ? `등교 — ${a.school?.name || '학교'}` : '집에서 공부'); start(world, a, 'study', o.school ? 390 : 150, o.school ? o.school.id : 'home', { school: !!o.school }); break;
    case 'play': start(world, a, 'play', 90, 'plaza'); break;
    case 'build': {
      let site = o.site ? world.buildings.get(o.site) : null;
      if (o.civic) site = placeBuilding(world, s, o.civic, { era, owner: null });
      if (!site && o.newHome) {
        const c = BUILDINGS[o.newHome].cost || 0;
        const hire = BUILDINGS[o.newHome].era >= 1 && hasConstruction(world, s.id) && a.money >= c * 1.5;
        site = placeBuilding(world, s, o.newHome, { era, owner: a.id, budget: hire ? c : c * 0.5 });
        if (site && hire && assignContractor(world, site)) { remember(world, a, `건설회사에 집 짓는 일을 맡겼다`, 4, 0.3, [], 'home'); }
        if (site) {
          a.money -= Math.min(a.money, c);
          remember(world, a, `내 ${reul(BUILDINGS[o.newHome].name)} 짓기 시작했다`, 5, 0.5, [], 'home');
          think(world, a, pick(['내 손으로 내 집을 짓는다. 설렌다.', '튼튼하게 지어야지.', '여기가 앞으로 내 보금자리가 되겠지.']));
        }
      }
      if (!site) { start(world, a, 'wander', 60, 'plaza'); break; }
      start(world, a, 'build', 180, site.id, { site: site.id });
      break;
    }
    case 'social': {
      const b = world.anns.get(o.target);
      if (!b) { start(world, a, 'relax', 45, 'home'); break; }
      start(world, a, 'social', 0, [b.x + 1.5, b.z + 1], { target: b.id, kind: o.kind });
      thinkDecision(world, a, o, b);
      break;
    }
    case 'family': {
      const b = world.anns.get(o.target);
      if (!b) { start(world, a, 'relax', 45, 'home'); break; }
      start(world, a, 'social', 0, [b.x + 1.2, b.z], { target: b.id, kind: 'family' });
      break;
    }
    case 'research': {
      const place = world.findCivic(s, 'lab') || world.findCivic(s, 'academy');
      start(world, a, 'research', 150, place ? place.id : (a.home ? 'home' : 'wild')); thinkDecision(world, a, o); break;
    }
    case 'art': start(world, a, 'art', 120, a.home && chance(0.5) ? 'home' : 'wild'); thinkDecision(world, a, o); break;
    case 'exercise': start(world, a, 'exercise', 90, world.findWork(s, 'stadium')?.id ?? 'wild'); break;
    case 'relax': start(world, a, 'relax', 60 + rand() * 40, chance(0.6) ? 'home' : 'plaza'); break;
    case 'wander': start(world, a, 'wander', 70, chance(0.5) ? 'plaza' : 'wild'); break;
    case 'reflect': start(world, a, 'reflect', 45, 'home'); break;
    case 'shop': start(world, a, 'shop', 60, world.findWork(s, 'market')?.id ?? 'plaza'); break;
    case 'found_co': start(world, a, 'found_co', 120, 'plaza', { ind: o.ind }); thinkDecision(world, a, o); break;
    case 'lead': start(world, a, 'lead', 180, world.findCivic(s, 'hall')?.id ?? 'plaza'); break;
    case 'migrate': start(world, a, 'migrate', 60, 'wild'); break;
    case 'rest_sick': start(world, a, 'rest_sick', 240, world.findWork(s, 'clinic')?.id ?? 'home'); break;
    default: start(world, a, 'relax', 60, 'home');
  }
}

// ── 문득 드는 생각: 지금 내 삶에서 가장 마음에 걸리는 것을 떠올린다 ──
export function musing(world, a) {
  if (chance(0.65)) { const t = compose(world, a); if (t) return t; }
  const now = world.clock;
  const s = world.settlements.get(a.sid);
  const h = localHour(now, s.lon);
  const c = world.countries[a.country];
  const pool = [];
  const push = (w, t) => { if (t) pool.push([w, t]); };
  // 사랑
  for (const id in a.rels) {
    const r = a.rels[id];
    const b = world.anns.get(+id);
    if (!b || !b.alive) continue;
    if (a.partner === b.id) push(4, pick([`${callName(b.name, a.culture)} 지금 뭐 하고 있을까.`, `${wa(b.name)} 함께 있으면 시간이 너무 빨리 간다.`, a.spouse ? `${b.name}에게 요즘 무심했던 것 같다. 잘해 줘야지.` : `${wa(b.name)} 언젠가 가족이 될 수 있을까?`]));
    else if (r.r > 40 && !a.partner) push(5, pick([`${b.name}… 나를 어떻게 생각할까?`, `${b.name}에게 말해 볼까. 아니야, 아직은…`, `오늘도 ${b.name} 얼굴만 봐도 좋았다.`]));
    else if (r.k === 'rival' || r.k === 'enemy') push(2, pick([`${b.name}에게만은 지고 싶지 않다.`, `${neun(b.name)} 왜 나를 그렇게 싫어할까.`]));
    else if (r.k === 'best') push(1.5, `${b.name} 같은 친구가 있어서 다행이다.`);
    else if (r.ex) push(1, `가끔 ${b.name} 생각이 난다. …그만 생각하자.`);
  }
  // 꿈
  const g = a.goals.find(x => !x.done);
  if (g) push(2.5, pick([`'${g.text}'… 나는 조금씩 가까워지고 있을까?`, a.amb > 65 ? `반드시 ${g.text.replace(/기$/, '고 말겠다.')}` : `${g.text.replace(/기$/, '면')} 얼마나 좋을까.`]));
  // 돈과 일
  const cost = dailyCost(c.era);
  if (a.money < cost * 2 && ageOf(a, now) > 19) push(4, pick(['돈이 바닥났다. 큰일이다.', '이번 주는 아껴 써야 한다.', '이러다 굶는 건 아닐까.']));
  else if (a.money > cost * 120) push(1.5, pick(['이제 좀 여유가 생겼다. 무엇을 해 볼까.', '돈이 행복을 다 주는 건 아니더라.']));
  if (a.job && a.job.sat < 40) push(3, pick([`${JOBS[a.job.id]?.name} 일이 나와 맞는지 모르겠다.`, '매일 같은 일… 이게 내 인생일까?']));
  if (a.job && a.job.sat > 75) push(1.5, `${JOBS[a.job.id]?.name} 일이 점점 좋아진다.`);
  if (!a.job && ageOf(a, now) > 19 && !a.retired) push(3, pick(['나도 쓸모 있는 사람이고 싶다.', '일자리만 있다면…']));
  // 감정의 여운
  if (a.emo.i > 0.4 && a.emo.why) push(3, a.mood > 0 ? `아직도 ${a.emo.why} 생각에 웃음이 난다.` : `${a.emo.why} 일이 계속 마음에 걸린다.`);
  // 가족
  if (a.preg) push(4, pick(['아기는 누구를 닮았을까.', '배 속에서 꼼지락거린다. 신기해.']));
  for (const kid of a.kids.slice(-2)) { const k = world.anns.get(kid); if (k && k.alive && ageOf(k, now) < 15) push(1.5, `${callName(k.name, a.culture)} 많이 컸네. 건강하게만 자라다오.`); }
  if (!a.home) push(3, '비바람을 피할 집이 있었으면…');
  if (a.sick > 0) push(3, '몸이 좀 나아져야 할 텐데.');
  // 세상
  if (c.leader === a.id) push(3, pick(['모두가 나를 보고 있다. 실수하면 안 된다.', `${c.name}을(를) 더 좋은 나라로 만들고 싶다.`]));
  else if (c.leader) { const L = world.anns.get(c.leader); const r = L && a.rels[L.id]; if (r) push(1, r.a > 30 ? `${L.name} ${ERAS[c.era].leader}라면 믿을 수 있다.` : r.a < -20 ? `${ga(L.name)} 정말 나라를 잘 이끌 수 있을까?` : null); }
  if (now - (c.eraAt || 0) < DAY * 30 && c.era > 0) push(2, `${ERAS[c.era].name}… 세상이 정말 많이 변했다.`);
  // 시간과 풍경
  if (h >= 5 && h < 8) push(1, pick(['상쾌한 아침이다.', '해가 뜬다. 오늘은 좋은 일이 있을 것 같아.']));
  if (h >= 18 && h < 21) push(1, pick(['노을이 예쁘다.', '하루가 또 이렇게 저문다.']));
  if (h >= 21 || h < 2) push(0.8, pick(['별이 참 많다.', '고요한 밤이다.']));
  // 성격
  if (a.tr.O > 70) push(1, pick(['저 산 너머엔 뭐가 있을까.', '세상은 왜 이렇게 생겼을까?']));
  if (a.tr.N > 70) push(1, pick(['괜히 불안하다. 아무 일 없겠지?', '다들 나를 어떻게 생각할까.']));
  if (a.tr.E > 70) push(1, '누구라도 만나서 떠들고 싶다!');
  if (a.tr.A < 30) push(0.8, '사람들은 다 자기 생각만 한다.');
  if (!pool.length) return null;
  let tot = pool.reduce((t, p) => t + p[0], 0), r = rand() * tot;
  for (const [w, t] of pool) { r -= w; if (r <= 0) return t; }
  return pool[0][1];
}

// ── 결정할 때의 속마음 ──
function thinkDecision(world, a, o, b = null) {
  if (!chance(o.type === 'sleep' ? 0.18 : 0.5)) return;
  if (chance(0.45)) { const m = musing(world, a); if (m) { think(world, a, m); return; } }
  const C = a.tr.C, N = a.tr.N;
  const lines = {
    sleep: a.nd.energy < 20 ? ['눈이 감긴다… 오늘은 정말 길었다.', '더는 못 버티겠다. 자야지.'] : ['오늘 하루도 끝. 내일은 뭘 할까.', '푹 자고 내일 또 힘내자.', '눈을 감으니 오늘 일이 떠오른다.', '내일은 오늘보다 나은 하루이길.'],
    forage: ['먹을 게 부족하다. 숲에 가 봐야겠다.', '배고픈 사람이 없도록 뭐라도 구해 오자.'],
    work: C > 65 ? ['오늘 할 일은 오늘 끝낸다.', '맡은 일은 확실히.'] : C < 35 ? ['아, 일하기 싫다…', '오늘만 버티자.'] : ['자, 일하러 가 볼까.', '오늘도 열심히.'],
    seek_job: ['일자리를 구해야 한다.', '나한테 맞는 일이 있을까?', a.money < 10 ? '이대로는 굶겠다. 뭐라도 해야 해.' : '내가 잘할 수 있는 일을 찾자.'],
    research: ['저건 왜 저렇게 될까? 알아내고 싶다.', '조금만 더 생각하면 뭔가 보일 것 같은데.', '세상엔 아직 모르는 게 너무 많다.'],
    art: ['머릿속에 떠오른 걸 표현하고 싶다.', '오늘은 뭔가 그릴 수 있을 것 같다.'],
    found_co: ['남 밑에서 일하는 건 이제 그만. 내 일을 하자.', '지금이 기회다. 내 사업을 시작하자.', '성공할 수 있을까? …아니, 해내고 말 거야.'],
    social: b ? (o.kind === 'date' ? [`${b.name} 만나러 가야지.`, `${callName(b.name, a.culture)} 보고 싶다.`]
      : o.kind === 'confess' ? [`오늘은 꼭 ${b.name}에게 말할 거야.`, '심장이 터질 것 같다…']
        : o.kind === 'propose' ? [`${b.name}에게 평생을 약속하고 싶다.`, '오늘이 그날이다.']
          : o.kind === 'comfort' ? [`${b.name}… 괜찮을까? 가 봐야겠다.`]
            : (a.rels[b.id] ? [`${wa(b.name)} 이야기나 할까.`, `${neun(b.name)} 요즘 어떻게 지내려나.`] : [`저 사람은 누구지? 말 걸어 볼까.`, N > 60 ? '말 걸어도 될까… 용기를 내자.' : '새로운 친구를 사귀어 볼까.'])) : [],
  };
  const arr = lines[o.type];
  if (arr && arr.length) think(world, a, pick(arr));
}

// ── 매 틱 ──
export function updateAnn(world, a, dt) {
  if (a.travel) {
    if (world.clock >= a.travel.until) world.arrive(a);
    return;
  }
  needs(world, a, dt);
  let act = a.act;
  if (!act) { decide(world, a); return; }
  if (act.type !== 'sleep' && act.type !== 'eat' && act.type !== 'baby') {
    if (a.nd.food < 5 && world.settlements.get(a.sid).food >= 1) { finishAct(world, a, true); decide(world, a); return; }
    if (a.nd.energy < 3) { finishAct(world, a, true); decide(world, a); return; }
  }
  if (act.ph === 'move') {
    if (act.type === 'social') {
      const b = world.anns.get(act.target);
      if (!b || !isAvailable(world, b) || b.sid !== a.sid) {
        if (b && a.rels[b.id] && chance(0.5)) think(world, a, `${neun(b.name)} 지금 바쁜가 보다.`);
        a.act = null; decide(world, a); return;
      }
      act.tx = b.x + 1.3; act.tz = b.z + 0.6;
    }
    const dx = act.tx - a.x, dz = act.tz - a.z;
    const d = Math.hypot(dx, dz);
    const step = speedOf(world, a) * dt;
    if (d <= step + 0.5) {
      a.x = act.tx; a.z = act.tz;
      act.ph = 'do';
      if (act.repath) act.repath = false; else act.until = world.clock + act.dur;
      world.dirtyAct.add(a.id);
      onArrive(world, a, act);
    } else {
      a.x += dx / d * step; a.z += dz / d * step;
    }
    return;
  }
  onTick(world, a, act, dt);
  if (a.act === act && (world.clock >= act.until || act.done)) {
    finishAct(world, a, false);
    decide(world, a);
  }
}

function onArrive(world, a, act) {
  const s = world.settlements.get(a.sid);
  if (act.type === 'social') {
    const b = world.anns.get(act.target);
    if (!b || !isAvailable(world, b)) { act.done = true; return; }
    const rb = b.rels[a.id];
    const pAccept = 0.55 + (rb ? rb.a / 180 + rb.r / 200 : (b.tr.E - 50) / 200) + (100 - b.nd.social) / 250 - (b.act?.type === 'research' || b.act?.type === 'art' ? 0.2 : 0)
      + (act.kind === 'family' ? 0.35 : 0) + (act.kind === 'date' && b.partner === a.id ? 0.4 : 0);
    if (!chance(clamp(pAccept, 0.08, 0.97))) {
      remember(world, a, `${b.name}에게 말을 걸었지만 시큰둥했다`, 2, -0.3, [b.id], 'talk');
      feel(a, -3, '머쓱함', b.name);
      if (chance(0.5)) think(world, a, pick([`${neun(b.name)} 나랑 이야기하기 싫은가?`, '타이밍이 안 좋았나 보다.']));
      act.done = true;
      return;
    }
    const kind = act.kind || 'chat';
    const isDate = kind === 'date';
    const result = converse(world, a, b, kind === 'family' ? 'family' : kind);
    const dur = isDate ? 100 + rand() * 60 : kind === 'family' ? 80 : 30 + rand() * 40;
    const type = isDate ? 'date' : kind === 'family' ? 'family' : 'talk';
    logToday(world, a, isDate ? '💕' : '💬', `${b.name}${isDate ? '와(과) 데이트' : kind === 'family' ? '와(과) 오붓한 시간' : '와(과) ' + (result.topic || '이야기')}`);
    logToday(world, b, isDate ? '💕' : '💬', `${a.name}${isDate ? '와(과) 데이트' : kind === 'family' ? '와(과) 오붓한 시간' : '와(과) ' + (result.topic || '이야기')}`);
    a.act = { type, ph: 'do', tx: a.x, tz: a.z, until: world.clock + dur, dur, target: b.id, topic: result.topic, line: result.line, started: Math.round(world.clock) };
    b.act = { type, ph: 'do', tx: b.x, tz: b.z, until: world.clock + dur, dur, target: a.id, topic: result.topic, line: result.line, started: Math.round(world.clock) };
    world.dirtyAct.add(a.id); world.dirtyAct.add(b.id);
    world.dirtyDetail.add(a.id); world.dirtyDetail.add(b.id);
    if (result.topic === '고백' || result.topic === '청혼' || result.quarrel) {
      world.news('social', `${result.quarrel ? '😠' : '💬'} ${result.line}`, { anns: [a.id, b.id], country: a.country }, result.quarrel ? 0 : 1);
    } else world.chatter(a, b, result.line);
    // 아기 갖기
    if (kind === 'family' || isDate) maybeConceive(world, a, b);
    return;
  }
  if (act.type === 'eat') {
    if (s.food >= 1) { s.food -= 1; act.fed = 1; } else act.fed = 0.25;
  }
  if (act.type === 'seek_job') {
    const { out: ops, blocked } = openings(world, a, true);
    if (blocked.length && chance(0.5)) {
      const b = blocked.sort((x, y) => y.fit - x.fit)[0];
      if (b.fit > 55) think(world, a, `${JOBS[b.jobKey].name}이(가) 되고 싶지만, ${['', '기초 교육', '중학교', '고등학교', '대학교'][JOBS[b.jobKey].edu || 1]}도 못 나온 나는 안 된다고 한다.`);
    }
    if (!ops.length) {
      think(world, a, '일자리가 하나도 없다…'); feel(a, -5, '불안', '일자리');
      return;
    }
    // 적합도 + 임금 + 야망 (야망이 크면 돈을, 성실하면 적성을)
    for (const op of ops) op.sc = op.fit * (0.7 + a.tr.C / 300) + op.wage / Math.max(1, wageBase(world.countries[a.country].era)) * (20 + a.val.wealth * 0.3) + (op.co ? 14 : 0) + gauss() * 6;
    ops.sort((x, y) => y.sc - x.sc);
    if (a.job) {
      const cur = ops.find(op => op.jobKey === a.job.id && (op.co?.id ?? null) === a.job.co);
      const curSc = cur ? cur.sc : jobFit(a, a.job.id) + 25;
      const better = ops.filter(op => op.sc > curSc + 8 && !(op.jobKey === a.job.id && (op.co?.id ?? null) === a.job.co) && (a.job.co || op.co));
      if (!better.length) { if (chance(0.4)) think(world, a, '둘러봤지만 지금 일만 한 게 없다.'); return; }
      ops.splice(0, ops.length, ...better);
    }
    for (const op of ops.slice(0, 3)) {
      const ok = apply(world, a, op);
      if (ok) {
        think(world, a, op.co ? `${op.co.name}에 붙었다! 열심히 해야지.` : `${JOBS[op.jobKey].name}… 나한테 맞는 일인 것 같다.`);
        break;
      }
    }
    if (!a.job) think(world, a, '또 떨어졌다. 다음엔 꼭…');
  }
  if (act.type === 'found_co') {
    const co = foundCompany(world, a, act.ind);
    a.lastFound = world.clock;
    if (!co) think(world, a, '아직은 때가 아닌가 보다.');
    act.done = !co ? true : false;
  }
  if (act.type === 'migrate') {
    world.foundSettlementBy(a);
  }
}

function maybeConceive(world, a, b) {
  const mother = a.gender === 'F' ? a : b.gender === 'F' ? b : null;
  const father = a.gender === 'M' ? a : b.gender === 'M' ? b : null;
  if (!mother || !father || mother.preg) return;
  if (mother.spouse !== father.id && !(mother.partner === father.id && chance(0.15))) return;
  const age = ageOf(mother, world.clock);
  if (age < 19 || age > 44) return;
  // 옛날엔 아이를 많이 낳았고, 근대 이후엔 적게 낳는다
  const era = world.countries[mother.country].era;
  const want = (x) => x.val.family / 22 + (x.tr.A - 50) / 50 + (era <= 3 ? 2 : era === 4 ? 1 : 0);
  if (mother.kids.length >= Math.max(want(mother), want(father)) + 0.6) return;
  if (mother.kids.length && world.clock - (mother.lastBirth || 0) < YEAR * 1.2) return;
  const fert = clamp(1 - (age - 28) / 22, 0.15, 1) * 0.3;
  if (chance(fert)) {
    mother.preg = { father: father.id, due: Math.round(world.clock + 270 * DAY) };
    remember(world, mother, '아기가 생겼다!', 9, 0.9, [father.id], 'family');
    remember(world, father, `${ga(mother.name)} 임신했다. 내가 아빠가 된다니!`, 9, 0.9, [mother.id], 'family');
    lifeEvent(world, mother, '임신', '🤰');
    think(world, mother, '내 안에 새로운 생명이… 믿기지 않아.');
    think(world, father, '좋은 부모가 될 수 있을까? 아니, 될 거야.');
    feel(mother, 25, '감격', '임신'); feel(father, 22, '감격', '임신');
    world.news('family', `🤰 ${wa(mother.name)} ${ga(father.name)} 아기를 기다리고 있다`, { anns: [mother.id, father.id], country: mother.country }, 1);
  }
}

function onTick(world, a, act, dt) {
  if (act.type === 'sleep') {
    const comfort = a.home ? 1 : 0.8;
    a.nd.energy = clamp(a.nd.energy + 0.2 * comfort * dt);
    a.stress = clamp(a.stress - 0.012 * dt);
    const s = world.settlements.get(a.sid);
    const h = localHour(world.clock, s.lon);
    const wake = wakeHour(world, a, ageStage(ageOf(a, world.clock)));
    // 낮잠은 기운이 돌아오면 끝, 밤잠은 아침까지
    if (act.nap ? a.nd.energy >= 75 : (a.nd.energy > 70 && h >= wake && h < wake + 6) || (a.nd.energy >= 99 && h >= wake - 0.4 && h < wake + 10)) act.done = true;
    if (a.nd.food < 8) act.done = true;
    return;
  }
  if (act.type === 'baby') {
    if (act.sub === 'sleep') a.nd.energy = clamp(a.nd.energy + 0.15 * dt);
    if (a.sick > 0) a.sick = Math.max(0, a.sick - 0.01 * dt);
    return;
  }
  const hours = dt / 60;
  switch (act.type) {
    case 'work':
      if (a.job) { produce(world, a, dt); a.day.work += hours; a.payHours = (a.payHours || 0) + hours; a.lastWork = world.clock; }
      else act.done = true;
      // 순찰: 한 곳에 오래 서 있지 않고 다음 골목으로
      if (isGuard(a) && !act.site && world.clock - (act.patrolT || act.started) > 35 + rand() * 40 && chance(0.7)) {
        act.patrolT = world.clock;
        [act.tx, act.tz] = patrolPoint(world, a);
        act.ph = 'move'; act.repath = true; act.patrol = true;
        world.dirtyAct.add(a.id);
      }
      break;
    case 'forage': {
      const s = world.settlements.get(a.sid);
      s.food += 1.6 * (0.5 + a.sk.farming / 100) * world.prod(a) * hours;
      a.sk.farming = clamp(a.sk.farming + 0.04 * hours);
      break;
    }
    case 'build': {
      const b = world.buildings.get(act.site);
      if (!b || b.done) { act.done = true; break; }
      let work = (0.5 + a.sk.construction / 100) * Math.sqrt(world.prod(a)) * hours * (0.7 + a.ab.ath / 200);
      // 손수 지을 수 없는 건물(관청·가게·연립 등)은 건설회사 없이는 아주 더디다
      if (!buildRule(b).self && !hasConstruction(world, b.sid)) work *= 0.25;
      if (b.owner != null && b.owner !== a.id) { b.helpers = b.helpers || []; if (!b.helpers.includes(a.id)) b.helpers.push(a.id); }
      a.sk.construction = clamp(a.sk.construction + 0.05 * hours);
      if (b.owner !== a.id && b.budget > 0) {
        const pay = Math.min(b.budget, b.budget / Math.max(1, b.work - b.progress) * work);
        b.budget -= pay; a.money += pay * 0.8;
      }
      if (addWork(world, b, work)) { world.onBuilt(b, a); act.done = true; }
      break;
    }
    case 'study': {
      const inSchool = !!(a.school && act.school);
      const rate = (inSchool ? 1.7 : 0.7) * (0.5 + a.ab.int / 100);
      if (inSchool) {
        a.school.days += hours / 6;
        a.school.grade = clamp(a.school.grade + ((a.ab.int * 0.55 + a.tr.C * 0.35 + 10 - a.school.grade) * 0.002 + gauss() * 0.15) * hours);
      }
      a.edu = clamp(a.edu + 0.25 * rate * hours);
      for (const k of ['science', 'teaching', 'commerce', 'art', 'crafting']) a.sk[k] = clamp(a.sk[k] + 0.03 * rate * hours * (k === 'science' ? a.ab.int / 60 : 1));
      a.day.studied = true;
      break;
    }
    case 'research': {
      const c = world.countries[a.country];
      const lab = 1 + (act.tx ? 0.3 : 0);
      const pts = (a.ab.int / 100 + a.sk.science / 100) * 0.22 * Math.sqrt(world.prod(a)) * hours * lab;
      world.addTech(c, pts, 'research');
      a.techMade = (a.techMade || 0) + pts;
      a.sk.science = clamp(a.sk.science + 0.06 * hours * (a.ab.int / 60));
      a.nd.fun = clamp(a.nd.fun + (a.tr.O > 60 ? 0.08 : 0.02) * dt);
      if (chance(0.0009 * hours * (a.ab.int / 60) * (0.5 + a.sk.science / 80))) world.tryDiscovery(a);
      break;
    }
    case 'art': {
      a.sk.art = clamp(a.sk.art + 0.07 * hours * (a.ab.cre / 60));
      a.nd.fun = clamp(a.nd.fun + 0.1 * dt);
      if (a.sk.art > 38 && chance(0.0006 * hours * a.ab.cre / 50 * (a.sk.art / 50))) masterpiece(world, a);
      break;
    }
    case 'exercise':
      a.sk.athletics = clamp(a.sk.athletics + 0.08 * hours * (a.ab.ath / 60));
      a.health = clamp(a.health + 0.01 * dt);
      a.nd.fun = clamp(a.nd.fun + 0.05 * dt);
      a.stress = clamp(a.stress - 0.03 * dt);
      break;
    case 'relax':
      a.nd.fun = clamp(a.nd.fun + 0.15 * dt); a.stress = clamp(a.stress - 0.04 * dt); a.nd.energy = clamp(a.nd.energy + 0.03 * dt);
      break;
    case 'wander':
      a.nd.fun = clamp(a.nd.fun + 0.1 * dt); a.stress = clamp(a.stress - 0.03 * dt);
      // 산책 중 우연한 만남
      if (chance(0.01 * dt * (a.tr.E / 60))) {
        const near = world.residents(a.sid).find(b => b.id !== a.id && isAvailable(world, b) && Math.hypot(b.x - a.x, b.z - a.z) < 60 && (ageOf(b, world.clock) >= 13) === (ageOf(a, world.clock) >= 13));
        if (near) { a.act = null; start(world, a, 'social', 0, [near.x + 1, near.z], { target: near.id, kind: 'chat' }); }
      }
      break;
    case 'play':
      a.nd.fun = clamp(a.nd.fun + 0.25 * dt);
      if (!act.met && chance(0.02 * dt)) {
        const kid = world.residents(a.sid).find(b => b.id !== a.id && b.act?.type === 'play' && !b.act.met && Math.abs(ageOf(b, world.clock) - ageOf(a, world.clock)) < 5);
        if (kid) { act.met = kid.act.met = true; const r = converse(world, a, kid, 'play'); world.chatter(a, kid, r.line); }
      }
      break;
    case 'reflect':
      a.stress = clamp(a.stress - 0.05 * dt);
      break;
    case 'shop': {
      // 여가·쇼핑 예산: 지난달 실수입의 18% + 모아 둔 돈의 1% (적어도 월 15만원). 다 쓰면 구경만 한다
      const eraNow = world.countries[a.country].era;
      const budget = Math.max(refMonth(eraNow) * 0.05, monthNet(a.ledPrev) * 0.18 + a.money * 0.01);
      const leisure = LEISURE_LABEL[Math.min(7, eraNow)];
      const spent = a.led?.exp?.[leisure] || 0;
      const spend = spent >= budget ? 0 : Math.min(a.money * 0.05, budget - spent, wageBase(eraNow) * (0.15 + 0.15 * Math.log10(1 + a.money / 1000))) * (dt / 60);
      a.money -= spend; a.nd.fun = clamp(a.nd.fun + 0.12 * dt);
      book(world, a, 'exp', LEISURE_LABEL[Math.min(7, world.countries[a.country].era)], spend);
      const mk = world.findWork(world.settlements.get(a.sid), 'market');
      const co = mk?.co && world.companies.get(mk.co);
      if (co && !co.dead) { co.money += spend; co.rev += spend; }
      break;
    }
    case 'lead':
      a.sk.leadership = clamp(a.sk.leadership + 0.05 * hours);
      a.day.led = true;
      break;
    case 'rest_sick': {
      const s = world.settlements.get(a.sid);
      const care = (s.heal || 0) > 0.1 ? 2.2 : 1;
      if (care > 1) s.heal -= 0.02 * dt;
      a.sick = Math.max(0, a.sick - 0.012 * care * dt);
      a.health = clamp(a.health + 0.004 * care * dt);
      if (a.sick <= 0) act.done = true;
      break;
    }
    case 'talk': case 'date': case 'family':
      a.nd.social = clamp(a.nd.social + 0.15 * dt);
      a.nd.fun = clamp(a.nd.fun + (act.type === 'date' ? 0.15 : 0.06) * dt);
      if (act.type === 'date') { const b = world.anns.get(act.target); if (b) { const r = rel(a, b); r.r = clamp(r.r + 0.012 * dt); } }
      break;
    default: break;
  }
}

function finishAct(world, a, interrupted) {
  const act = a.act;
  if (!act) return;
  a.act = null;
  world.dirtyAct.add(a.id);
  if (interrupted) return;
  switch (act.type) {
    case 'eat':
      if (act.rest) {
        const rb = world.buildings.get(act.rest);
        const co = rb?.co && world.companies.get(rb.co);
        const ea = era(world, a), price = mealPrice(ea); // 한 끼 사 먹는 값 (시대마다: 1970년 짜장면 100원, 2026년 9,500원)
        if (co && !co.dead && a.money >= price) { a.money -= price; co.money += price; co.rev += price; act.fed = 1; book(world, a, 'exp', MEAL_LABEL[Math.min(7, ea)], price); }
      }
      a.nd.food = clamp(a.nd.food + 58 * (act.fed ?? 1));
      a.lastMeal = world.clock;
      a.day.meals++;
      if (act.fed < 1) { remember(world, a, '먹을 것이 없어 굶주렸다', 3, -0.5, [], 'hunger'); think(world, a, '배가 고프다… 다들 먹을 건 있을까?'); feel(a, -6, '배고픔'); }
      break;
    case 'work':
      if (a.job) {
        const s2 = world.settlements.get(a.sid);
        if (act.endH && localHour(world.clock, s2.lon) >= act.endH - 0.4) {
          logToday(world, a, '🏡', `퇴근 (${Math.round(a.day.work)}시간 근무)`);
          if (chance(0.15)) think(world, a, pick(['퇴근이다! 오늘도 수고했다, 나.', '집에 가서 뭐 먹지?', '오늘은 좀 힘들었다.', '내일도 이 시간에 또…']));
        }
        const fit = jobFit(a, a.job.id);
        a.job.sat = clamp(a.job.sat + (fit - 55) * 0.04 + (a.mood > 0 ? 0.3 : -0.3));
        coworkerChat(world, a);
      }
      break;
    case 'forage':
      if (act.fishing) {
        const s = world.settlements.get(a.sid);
        s.food += 1.5;
        if (chance(0.35)) remember(world, a, `물가에서 ${pick(['붕어', '잉어', '송어', '조개', '고등어', '메기'])}을(를) 잡아 왔다`, 2, 0.4, [], 'work');
        world.first(world.countries[a.country], 'fish', `첫 고기잡이 — ${a.name}`, '🐟');
      } else if (chance(0.3)) remember(world, a, pick(['숲에서 열매와 버섯을 따 왔다', '들에서 먹을 풀뿌리를 캐 왔다', '덫에 걸린 토끼를 가져왔다']), 2, 0.3, [], 'work');
      a.rep = clamp(a.rep + 0.3, -100, 100);
      break;
    case 'reflect': reflect(world, a); break;
    case 'sleep': a.lastWake = world.clock; logToday(world, a, '⏰', '일어났다'); break;
    case 'study': if (act.school) logToday(world, a, '🏡', '하교'); break;
    case 'relax': if (chance(0.3)) { const m = musing(world, a); if (m) think(world, a, m); } break;
    case 'research': if (chance(0.25)) think(world, a, pick(['오늘은 조금 알아낸 것 같다.', '실패도 배움이다.', '답이 거의 보인다. 거의…'])); break;
    case 'exercise': if (chance(0.2)) think(world, a, '땀 흘리고 나니 개운하다!'); break;
    case 'wander': if (chance(0.35)) { const m = chance(0.6) ? musing(world, a) : pick(['하늘이 참 넓다.', `${world.settlements.get(a.sid).name}도 많이 변했네.`, '이 길 끝엔 뭐가 있을까.']); if (m) think(world, a, m); } break;
    case 'date': {
      const b = world.anns.get(act.target);
      if (b && chance(0.5)) remember(world, a, `${wa(b.name)} 데이트를 했다`, 4, 0.7, [b.id], 'love');
      break;
    }
    default: break;
  }
}

const era = (world, a) => world.countries[a.country].era;

function coworkerChat(world, a) {
  if (!a.job?.co || !chance(0.35)) return;
  const co = world.companies.get(a.job.co);
  if (!co) return;
  const mates = co.emp.filter(id => id !== a.id);
  if (!mates.length) return;
  const b = world.anns.get(pick(mates));
  if (!b || !b.alive) return;
  const ra = rel(a, b), rb = rel(b, a);
  const g = ra.c * 3 + (a.tr.A + b.tr.A - 100) * 0.03 + gauss() * 2;
  ra.f = clamp(ra.f + 1.5); rb.f = clamp(rb.f + 1.5);
  ra.a = clamp(ra.a + g, -100, 100); rb.a = clamp(rb.a + g, -100, 100);
  ra.last = rb.last = Math.round(world.clock);
  updateKind(world, a, b); updateKind(world, b, a);
}

function masterpiece(world, a) {
  const titles = ['새벽의 노래', '어머니의 손', '불꽃', '끝없는 들판', '별을 세는 밤', '첫사랑', '침묵', '바람의 기억', '두 개의 달', '고향'];
  const title = pick(titles);
  a.fame = clamp(a.fame + 15, 0, 100);
  a.rep = clamp(a.rep + 8, -100, 100);
  remember(world, a, `걸작 「${title}」을(를) 완성했다`, 9, 1, [], 'art');
  lifeEvent(world, a, `걸작 「${title}」 완성`, '🎨');
  feel(a, 30, '환희', title);
  think(world, a, '이건… 내가 만든 것 중 최고다.');
  const g = a.goals.find(g => g.k === 'art' && !g.done);
  if (g && a.sk.art > 45) world.achieveGoal(a, g);
  world.news('culture', `🎨 ${ga(a.name)} 걸작 「${title}」을(를) 남겼다`, { anns: [a.id], country: a.country }, 2);
}

/** 사색: 최근 기억을 되돌아보며 깨달음, 관계 재평가, 목표 수정 */
function reflect(world, a) {
  const now = world.clock;
  const recent = a.mem.filter(m => now - m.t < DAY * 7);
  // 누구와의 기억이 많았나 → 그 사람에 대한 생각
  const tally = {};
  for (const m of recent) for (const id of m.who) tally[id] = (tally[id] || 0) + m.v * m.imp;
  let top = null, tv = 0;
  for (const id in tally) if (Math.abs(tally[id]) > Math.abs(tv)) { tv = tally[id]; top = +id; }
  const b = top && world.anns.get(top);
  if (b && Math.abs(tv) > 4) {
    const r = rel(a, b);
    if (tv > 0) { r.a = clamp(r.a + 3, -100, 100); think(world, a, pick([`요즘 ${ga(b.name)} 자주 떠오른다. 고마운 사람이다.`, `${wa(b.name)} 함께한 시간이 참 좋았다.`])); }
    else { r.a = clamp(r.a - 3, -100, 100); think(world, a, pick([`${b.name} 때문에 마음이 상한다.`, `${wa(b.name)} 거리를 두는 게 좋겠다.`])); }
    updateKind(world, a, b);
    return;
  }
  const g = a.goals.find(x => !x.done);
  if (g) {
    const age = ageOf(a, now);
    if (age > 55 && (g.k === 'sport' || g.k === 'adventure') && chance(0.3)) {
      g.done = true; g.given = true;
      remember(world, a, `'${g.text}'라는 꿈을 내려놓았다`, 6, -0.3, [], 'goal');
      think(world, a, `'${g.text}'… 이제는 내려놓을 때가 된 것 같다. 그래도 후회는 없다.`);
      return;
    }
    think(world, a, pick([`언젠가 꼭 ${g.text.replace(/기$/, '고 말 거야.')}`, `'${g.text}'. 그 꿈을 위해 오늘 나는 무엇을 했지?`, `나는 왜 '${g.text}'를 바라는 걸까. …그래도 포기할 수 없다.`]));
    return;
  }
  think(world, a, pick(['산다는 건 뭘까.', '지금 이대로도 괜찮은 걸까?', '나는 어떤 사람으로 기억될까.', '오늘이 어제보다 나았으면 좋겠다.']));
}

/** 밤마다 하루를 되돌아본다 (일간 처리에서 호출) */
export function nightlyReview(world, a) {
  const d = a.day;
  if (d.best && (!d.worst || d.best.s >= d.worst.s)) think(world, a, `오늘은 ${d.best.text.replace(/다$/, '던')} 날. 좋은 하루였다.`);
  else if (d.worst) think(world, a, a.tr.N > 60 ? `${d.worst.text.replace(/다$/, '다니')}… 자꾸 그 생각이 난다.` : `${d.worst.text.replace(/다$/, '지만')} 내일은 나아지겠지.`);
  else if (d.talks === 0 && a.tr.E > 55) think(world, a, '오늘은 아무와도 이야기하지 못했다. 외롭다.');
  // 직업 만족도 → 이직 고민
  if (a.job && !a.job.ceo && a.job.sat < 30 && chance(0.12 + (100 - a.tr.C) / 500)) {
    const title = jobTitle(world, a);
    leaveJob(world, a, '적성에 맞지 않아서');
    think(world, a, `${title}… 이건 내 길이 아니었다. 다른 일을 찾아보자.`);
    feel(a, -4, '홀가분함', title);
  }
  a.day.talks = 0; a.day.best = null; a.day.worst = null;
}

function nearestOf(world, s, types, a) {
  let best = null, bd = Infinity;
  for (const id of s.buildings) {
    const b = world.buildings.get(id);
    if (!b || !b.done || !types.includes(b.type) || !b.co) continue;
    const co = world.companies.get(b.co);
    if (!co || co.dead) continue;
    const d = Math.hypot(b.x - a.x, b.z - a.z);
    if (d < bd) { bd = d; best = b; }
  }
  return best;
}

/** 일어날 시각: 일이 일찍 시작하면 일찍 일어난다 (농부·어부·현장) */
function wakeHour(world, a, stage) {
  if (stage === 'child') return 7 + a.chrono * 0.5;
  let w = 6.5 + a.chrono;
  if (a.job && JOBS[a.job.id]) {
    const sh = SHIFTS[JOBS[a.job.id].shift] || SHIFTS.office;
    w = Math.min(w, sh.start - 0.9);
  }
  return Math.max(3, w);
}
