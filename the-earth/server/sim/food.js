// 밥: 앤들이 실제로 무엇을 먹는가, 먹으면 몸과 마음이 어떻게 달라지는가.
//
//   · 식성 — 매운맛·단맛을 얼마나 좋아하는지, 새 음식을 얼마나 반기는지, 좋아하는·싫어하는 음식,
//     어릴 때 집에서 먹던 '그리운 맛', 커피·술 습관. 먹어 보면서 바뀐다.
//   · 끼니 — 집에서는 식구 중 요리 솜씨 좋은 사람이 차리고 다 같이 먹는다(식구 = 함께 밥 먹는 사람).
//     점심은 식당·구내식당·도시락, 가끔 외식. 식당마다 요리(한식·이탈리아…)와 메뉴, 맛(솜씨)과 소문이 있다.
//   · 재료 — 마을의 먹을거리는 곡물·채소·과일·고기·생선·유제품으로 나뉜다. 바닷가는 생선을, 목장 마을은 고기를
//     더 먹는다. 장사길이 열리면(시장·근대 이후) 무엇이든 구할 수 있다.
//   · 몸 — 든든히 먹으면 일이 잘 되고, 굶으면 힘이 빠진다. 고르게 먹으면 건강해지고, 군것질·가공식품만 먹으면
//     몸이 축난다. 많이 먹고 덜 움직이면 살이 찐다. 커피는 잠을 쫓고, 술은 스트레스를 풀지만 다음 날 숙취가 온다.
//   · 컨디션 — 배부름·기력·기분·스트레스·건강·카페인·숙취·식단을 모아 '일의 능률'이 된다 (일·공부·연구·집짓기·채집).
import { clamp, rand, pick, chance, gauss, ageOf, localHour, dateOf, DAY } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { DISHES, dishesOf, dishById, cuisineOf, CUISINES, CAT_NAMES, FLAVOR_NAMES, SP, SW, SO, RI } from './foods.js';

// ── 날마다 먹는 음식이 정해진 날 (문화권별) ──
const byName = (cu, n) => dishesOf(cu).find(d => d.n === n) || null;
/** 오늘이 특별한 날이면 그날의 음식과 이름: 생일 미역국, 설날 떡국, 추석 송편, 동지 팥죽, 복날 삼계탕, 크리스마스… */
function festive(world, a, h) {
  const t = dateOf(world.clock), b = dateOf(a.born);
  const cu = a.fd?.cu;
  const era = world.countries[a.country]?.era ?? 0;
  if (t.doy === b.doy && ageOf(a, world.clock) >= 1) {
    if (cu === 'ko' && h < 11) return { d: byName('ko', '소고기미역국') || byName('ko', '미역국'), why: '생일 아침 미역국' };
    if (era >= 4 && h >= 17) return { d: byName('world', '생일 케이크'), why: '생일 케이크' };
  }
  if (cu === 'ko' || cu === 'zh') {
    if (t.month === 1 && t.day === 1 && h < 14) return { d: cu === 'ko' ? byName('ko', '떡국') : byName('zh', '수이자오'), why: cu === 'ko' ? '설날 떡국' : '춘절 만두' };
    if (t.month === 9 && t.day === 15) return { d: cu === 'ko' ? byName('ko', '송편') : byName('zh', '월병'), why: cu === 'ko' ? '추석 송편' : '중추절 월병' };
  }
  if (cu === 'ko' && t.month === 12 && t.day === 22) return { d: byName('ko', '동지 팥죽'), why: '동지 팥죽' };
  if (cu === 'ko' && t.month === 7 && (t.day === 15 || t.day === 25) && h >= 11) return { d: byName('ko', '삼계탕'), why: '복날 삼계탕' };
  if (['en', 'de', 'fr', 'it', 'es', 'pt', 'ru'].includes(cu) && t.month === 12 && t.day === 25 && h >= 17) {
    const pickOne = { en: '로스트 비프', de: '슈톨렌', fr: '오리 가슴살 구이', it: '파네토네', es: '코시도 마드릴레뇨', pt: '브라스식 바칼랴우', ru: '올리비에 샐러드' }[cu];
    return { d: byName(cu, pickOne), why: '크리스마스 저녁' };
  }
  return null;
}
import { JOBS } from './data.js';
import { chronicHit } from './illness.js';

// ── 문화권마다 매운맛을 즐기는 정도 (기본값) ──
const SPICE_BASE = { ko: 66, in: 72, vi: 60, zh: 58, af: 52, es: 55, ar: 42, pt: 40, ja: 34, it: 30, en: 30, fr: 24, de: 24, ru: 26 };
/** 체중의 기준점: 문화권 평균 + 타고난 체형. 먹을 것이 귀하던 옛 시대엔 다들 마른 편 */
const bmiSet = (a, era) => (BMI_BASE[a.culture] ?? 23.5) + (a.fd?.bmiOff ?? 0) - (era <= 2 ? 2.5 : era === 3 ? 1.5 : era === 4 ? 0.6 : 0);
// 문화권별 어른 평균 BMI (대략 오늘날 기준)
const BMI_BASE = { en: 26.5, de: 25.8, ru: 25.8, es: 26, pt: 25.4, ar: 26.5, it: 25, fr: 24.4, af: 23.5, in: 22.4, zh: 23.6, ko: 23.6, ja: 22.6, vi: 21.8 };
const COOK_JOBS = new Set(['cook', 'chef', 'baker', 'barista']);
const MAIN_CATS = new Set(['main', 'soup', 'rice', 'noodle']);

/** 앤의 식성 (처음 한 번 만든다. 예전 세계를 불러와도 여기서 채운다) */
export function diet(world, a) {
  if (a.fd) return a.fd;
  const cu = cuisineOf(a.culture);
  const age = ageOf(a, world.clock);
  const eraNow = world.countries[a.country]?.era ?? 0;
  a.fd = {
    cu,
    spicy: clamp(Math.round((SPICE_BASE[a.culture] ?? 40) + gauss() * 16 - (age < 8 ? 30 : 0)), 0, 100),
    sweet: clamp(Math.round(48 + gauss() * 20 + (age < 13 ? 15 : 0)), 0, 100),
    adv: clamp(Math.round(a.tr.O * 0.7 + 10 + gauss() * 12), 0, 100),       // 낯선 음식을 반기는 정도
    hateFl: chance(0.45) ? pick([SP, SW, SO, RI, 64]) : 0,                    // 유난히 싫은 맛
    fav: [], hate: [], comfort: null, recent: [], cus: {},
    bal: 60, junk: 25, fedAt: -1e9, fedQ: 0, kcDay: 0, exDay: 0,
    coffee: clamp(Math.round(30 + a.tr.C * 0.2 + gauss() * 25), 0, 100),
    drink: clamp(Math.round(a.tr.E * 0.35 + a.tr.N * 0.15 + 12 + gauss() * 18), 0, 100),
    // 타고난 체형: 문화권의 평균 + 개인차. 먹을 것이 귀하던 옛 시대엔 다들 마른 편
    bmiOff: Math.round(gauss() * 2.4 * 10) / 10,   // 타고난 체형 (문화권 평균에서 얼마나 벗어나나)
  };
  a.fd.bmi = Math.round(bmiSet(a, eraNow) * 10) / 10;
  if (a.sk.cooking == null) a.sk.cooking = clamp(Math.round(Math.min(age, 40) * 0.6 + a.ab.cre * 0.15 + a.tr.C * 0.1 + rand() * 10 - (age < 10 ? 20 : 0)), 0, 70);
  // 고향 음식 중 둘은 처음부터 좋아한다
  const era = world.countries[a.country]?.era ?? 0;
  const pool = menuPool(world, a, era, cu).filter(d => MAIN_CATS.has(d.cat));
  for (let i = 0; i < 2 && pool.length; i++) { const d = pick(pool); if (!a.fd.fav.includes(d.id)) a.fd.fav.push(d.id); }
  return a.fd;
}

// ── 재료: 마을 먹을거리의 구성 ──
const JOB_SRC = {
  farmer: { grain: 0.6, veg: 0.3, fruit: 0.1 }, peasant: { grain: 0.55, veg: 0.35, fruit: 0.1 }, gatherer: { fruit: 0.45, veg: 0.45, meat: 0.1 },
  hunter: { meat: 1 }, rancher: { meat: 0.6, dairy: 0.4 }, fisher: { fish: 1 }, sailor: { fish: 1 },
};
/** 마을에 먹을거리를 더한다. src: 'fish' 같은 한 가지, 또는 {grain: .6, veg: .4} 같은 비율, 또는 직업 이름 */
export function addFood(s, amt, src = 'grain') {
  if (!(amt > 0)) return;
  s.food += amt;
  const p = pantry(s);
  const mix = typeof src === 'string' ? (JOB_SRC[src] || { [src]: 1 }) : src;
  for (const k in mix) p[k] = (p[k] || 0) + amt * mix[k];
}
function pantry(s) {
  if (!s.pan) { const f = Math.max(10, s.food); s.pan = { grain: f * 0.45, veg: f * 0.3, fruit: f * 0.1, meat: f * 0.1, fish: f * 0.05 }; }
  return s.pan;
}
/** 이 재료가 마을 먹을거리에서 차지하는 몫 (0~1) */
function share(s, k) {
  const p = pantry(s);
  let tot = 0; for (const v of Object.values(p)) tot += v;
  return tot > 0 ? (p[k] || 0) / tot : 0;
}
/** 장사길이 열렸나 (시장이 있거나 근대 이후면 다른 고장 재료도 산다) */
function trades(world, s) {
  const era = world.countries[s.country]?.era ?? 0;
  if (era >= 4) return true;
  if (era < 2) return false;
  return s.buildings.some(id => { const b = world.buildings.get(id); return b && b.done && (b.type === 'market' || b.type === 'supermarket'); });
}
function hasSrc(world, s, src) {
  if (src === 'veg' || src === 'grain') return s.food >= 1;
  if (trades(world, s)) return true;
  return s.food * share(s, src) >= 0.8;
}
/** 한 끼를 먹으면 마을 먹을거리가 그만큼 준다 */
export function consume(world, s, d) {
  if (s.food < 1) return 0.25;
  s.food -= 1;
  const p = pantry(s);
  if (d && p[d.src] > 0) p[d.src] = Math.max(0, p[d.src] - 0.8);
  return 1;
}
/** 하루에 한 번: 재료 구성이 오래된 것부터 줄어든다 */
export function pantryDaily(s) {
  const p = pantry(s);
  let tot = 0;
  for (const k in p) { p[k] *= 0.97; tot += p[k]; }
  const cap = Math.max(20, s.food * 1.2);
  if (tot > cap) for (const k in p) p[k] *= cap / tot;
}

// ── 메뉴 ──
/** 이 시대·이 요리에서 먹을 수 있는 음식 */
function menuPool(world, a, era, cu) {
  const c = world.countries[a.country];
  const fire = !c || era > 0 || c.discoveries?.includes('불의 사용');
  const out = [];
  const take = list => { for (const d of list) if (d.era <= era && (fire || !d.fire)) out.push(d); };
  if (era <= 1) take(dishesOf('prim'));
  if (era >= 1) take(dishesOf(cu));
  return out;
}

/** 맛 점수: 이 앤에게 이 음식이 얼마나 맛있을까 (−5 ~ +6) */
export function taste(world, a, d, opts = {}) {
  const f = diet(world, a);
  let t = 0;
  if (d.fl & SP) t += (f.spicy - 50) / 22;
  if (d.fl & SW) t += (f.sweet - 50) / 30;
  if (f.hateFl && (d.fl & f.hateFl)) t -= 2.2;
  if (f.fav.includes(d.id)) t += 2.6;
  if (f.hate.includes(d.id)) t -= 3.2;
  if (f.comfort === d.id) t += 2.4;
  if (d.cu === f.cu || d.cu === 'prim') t += 0.6;
  else t += (f.adv - 50) / 35 + (f.cus[d.cu] ? 0.4 : 0);
  // 같은 걸 또 먹으면 물린다
  for (let i = 0; i < f.recent.length; i++) {
    const r = dishById(f.recent[i]);
    if (!r) continue;
    if (r.id === d.id) t -= 1.6 - i * 0.12;
    else if (r.cat === d.cat && i < 2) t -= 0.35;
  }
  // 날씨: 추우면 따뜻한 국물, 더우면 시원한 것
  if (opts.cold && d.hot) t += 0.8;
  if (opts.hot && d.hot) t -= 0.6;
  if (opts.hot && /냉|빙수|아이스|콩국수|젤라토|에이드|스무디|가스파초|오크로시카/.test(d.n)) t += 1;
  return t + (opts.jitter ?? gauss() * 0.6);
}

/** 여러 후보에서 맛 점수에 따라 하나 (늘 1등만 고르지는 않는다) */
function choose(world, a, cands, opts) {
  if (!cands.length) return null;
  const sample = cands.length > 36 ? Array.from({ length: 36 }, () => pick(cands)) : cands;
  let best = null, bs = -1e9;
  const dieting = a.fd?.dietUntil > world.clock;
  for (const d of sample) {
    // 다이어트 중: 가볍고 채소 많은 것, 튀김·설탕은 참는다
    const sc = taste(world, a, d, opts) + (opts.score ? opts.score(d) : 0) + (dieting ? d.v * 0.6 - d.j * 1.2 - Math.max(0, d.kc - 50) * 0.04 : 0);
    if (sc > bs) { bs = sc; best = d; }
  }
  return best;
}

/** 식당·카페·빵집의 메뉴 (처음 한 번, 시대가 바뀌면 새로) */
export function menuOf(world, co) {
  const c = world.countries[co.country];
  const era = c?.era ?? 2;
  if (co.menu && co.menuEra === era) return co.menu;
  const founder = world.anns.get(co.founder);
  const named = /반점|각$|루$/.test(co.name) ? 'zh' : /국밥|밥집/.test(co.name) ? 'ko' : null;
  let cu = co.cu || named || (founder ? cuisineOf(founder.culture) : 'en');
  // 근대 이후엔 다른 나라 음식점도 생긴다 (이름에 요리가 드러나지 않고, 마을에 식당이 여럿일 때)
  if (!co.cu && !named && era >= 5 && co.ind === 'restaurant') {
    const s = world.settlements.get(co.sid);
    const n = s ? s.buildings.filter(id => world.buildings.get(id)?.type === 'restaurant').length : 0;
    if (n >= 2 && chance(0.4)) cu = pick(['ko', 'ja', 'zh', 'it', 'in', 'vi', 'es', 'en', 'fr', 'ar']);
  }
  co.cu = cu;
  const ok = d => d.era <= era;
  let pool;
  if (co.ind === 'cafe') pool = [...dishesOf(cu), ...dishesOf('world')].filter(d => ok(d) && (d.dr === 'caf' || d.cat === 'drink' || d.cat === 'dessert' || d.cat === 'bread'));
  else if (co.ind === 'bakery') pool = [...dishesOf(cu), ...dishesOf('world')].filter(d => ok(d) && (d.cat === 'bread' || d.cat === 'dessert'));
  else pool = dishesOf(cu).filter(d => ok(d) && (MAIN_CATS.has(d.cat) || d.cat === 'snack' || d.cat === 'street'));
  if (!pool.length) pool = dishesOf('prim').filter(ok);
  const n = co.ind === 'restaurant' ? 14 : 10;
  const menu = new Set();
  for (let i = 0; i < n * 3 && menu.size < Math.min(n, pool.length); i++) menu.add(pick(pool).id);
  co.menu = [...menu];
  co.menuEra = era;
  return co.menu;
}
const isMeal = d => MAIN_CATS.has(d.cat) || d.cat === 'snack' || d.cat === 'street' || d.cat === 'bread';

/** 식구: 같은 집에 사는 사람들 */
function household(world, a) {
  if (!a.home) return [a];
  return world.residents(a.sid).filter(b => b.home === a.home && b.alive);
}

/**
 * 끼니 정하기. 반환: { where: 'home'|건물id|[x,z], rest: 식당 건물 id 또는 null, dish, cook, how(기록용 말), price }
 * o.lunch = 일하는 날 점심, ctx = { s, era }
 */
export function planMeal(world, a, o, ctx) {
  const { s, era } = ctx;
  const f = diet(world, a);
  const age = ageOf(a, world.clock);
  const h = localHour(world.clock, s.lon);
  const season = s.weather?.season;
  const wopt = { cold: season === '겨울' || s.weather?.k === '한파', hot: s.weather?.k === '무더위' || season === '여름' };
  const meals = menuPool(world, a, era, f.cu).filter(d => isMeal(d));
  const canHome = d => d.home && hasSrc(world, s, d.src);
  const price0 = (ctx.mealPrice || 0);
  const dayN = Math.floor(world.clock / DAY);
  // ⓪ 회식: 회사가 정한 날 저녁엔 다 같이 식당으로 (회사가 낸다)
  const myCo = a.job?.co && world.companies.get(a.job.co);
  if (!o.lunch && h >= 17 && myCo?.hoesik?.day === dayN && age >= 19) {
    const b = world.buildings.get(myCo.hoesik.rest);
    const rco = b?.co && world.companies.get(b.co);
    if (rco && !rco.dead) {
      const menu = menuOf(world, rco).map(dishById).filter(d => d && MAIN_CATS.has(d.cat));
      const d = choose(world, a, menu, { ...wopt, score: d => (d.fl & RI ? 0.6 : 0) + (/삼겹|갈비|구이|전골|탕|찜|치킨|족발|보쌈|회/.test(d.n) ? 1 : 0) });
      if (d) return { where: b.id, rest: b.id, dish: d, how: `${myCo.name} 회식 · ${rco.name}`, hoesik: myCo.id };
    }
  }
  // ① 밖에서: 일하는 날 점심이면 대개, 저녁엔 가끔 (돈이 있고 식당이 있을 때)
  const outWish = o.lunch ? 0.7 : 0.08 + (a.tr.E - 50) / 300 + (a.money > price0 * 30 ? 0.08 : 0) + (f.adv - 50) / 400;
  if (era >= 2 && age >= 12 && a.money > price0 * 2 && chance(outWish)) {
    const r = pickRestaurant(world, a, s, wopt, o.lunch);
    if (r) return r;
  }
  // ② 구내식당 (근대 이후, 직원 많은 회사의 점심)
  const co = a.job?.co && world.companies.get(a.job.co);
  if (o.lunch && co && era >= 4 && co.emp.length >= 8) {
    const d = choose(world, a, meals.filter(d => MAIN_CATS.has(d.cat) && d.t <= 1.3), wopt);
    if (d) return { where: 'work', dish: d, how: '구내식당', canteen: true };
  }
  // ③ 도시락 (일터에서 먹는 점심)
  if (o.lunch && a.job) {
    const d = choose(world, a, meals.filter(d => canHome(d) && (d.cat === 'rice' || d.cat === 'snack' || d.cat === 'bread' || d.cat === 'main')), wopt);
    return { where: 'work', dish: d, how: era <= 1 ? '새참' : '도시락' };
  }
  // ④ 집밥: 이미 차려진 밥상이 있으면 같이 먹고, 없으면 식구 중 솜씨 좋은 사람이 차린다
  const home = a.home && world.buildings.get(a.home);
  // 특별한 날의 음식은 하루 한 번
  const dayKey = Math.floor(world.clock / DAY);
  const fest = !o.lunch && f.festDay !== dayKey && festive(world, a, h);
  if (fest?.d && fest.d.era <= era) {
    f.festDay = dayKey;
    if (home) home.meal = { d: fest.d.id, by: a.id, at: world.clock, who: [a.id] };
    return { where: 'home', dish: fest.d, how: fest.why, fest: true };
  }
  if (home && home.meal && world.clock - home.meal.at < 100) {
    const d = dishById(home.meal.d);
    if (d) { if (!home.meal.who.includes(a.id)) home.meal.who.push(a.id); return { where: 'home', dish: d, cook: home.meal.by, how: '집밥', shared: true }; }
  }
  const fam = household(world, a);
  const kids = fam.some(b => b !== a && ageOf(b, world.clock) < 8);
  // ⑤ 배달: 근대 이후, 피곤하거나 주말이거나 집에 요리할 사람이 마땅치 않을 때 (짜장면·치킨·피자·족발…)
  const bestCook = Math.max(...fam.filter(b => ageOf(b, world.clock) >= 13).map(b => b.sk.cooking ?? 20), 0);
  const wk = dayN % 7 >= 5;
  if (era >= 5 && age >= 14 && !o.lunch && a.money > price0 * 4) {
    const p = 0.05 + (a.nd.energy < 35 ? 0.1 : 0) + (bestCook < 30 ? 0.08 : 0) + (wk ? 0.06 : 0) + (a.fd.dietUntil > world.clock ? -0.1 : 0) + (era >= 6 ? 0.04 : 0);
    if (chance(p)) {
      const r = pickRestaurant(world, a, s, { ...wopt }, false, true);
      if (r) {
        if (home) home.meal = { d: r.dish.id, by: a.id, at: world.clock, who: [a.id] };
        return { ...r, where: 'home', how: `배달 · ${r.how}`, delivery: true };
      }
    }
  }
  const cook = fam.filter(b => ageOf(b, world.clock) >= 13 && (!b.act || b.act.type !== 'work'))
    .sort((x, y) => ((y.sk.cooking ?? 20) + y.tr.A * 0.15) - ((x.sk.cooking ?? 20) + x.tr.A * 0.15))[0] || a;
  const skill = cook.sk.cooking ?? 20;
  const poor = a.money < (price0 || 1) * 3 && era >= 2;
  const d = choose(world, a, meals.filter(d => canHome(d) && d.sk <= skill + 22 && (!poor || d.t <= 1.1)), {
    ...wopt, score: d => (kids && (d.fl & SP) ? -1.5 : 0) + (d.cat === 'snack' && h > 16 ? -1 : 0) + (MAIN_CATS.has(d.cat) ? 0.5 : 0),
  }) || choose(world, a, meals, wopt);
  if (home && d) home.meal = { d: d.id, by: cook.id, at: world.clock, who: [a.id] };
  return { where: 'home', dish: d, cook: cook.id, how: '집밥' };
}

/** 어느 식당으로 갈까: 메뉴가 입에 맞고, 솜씨 좋고, 소문나고, 가까운 곳 */
function pickRestaurant(world, a, s, wopt, lunch, delivery = false) {
  let best = null, bs = -1e9;
  let n = 0;
  for (const id of s.buildings) {
    const b = world.buildings.get(id);
    if (!b || !b.done || !b.co || !(delivery ? ['restaurant'] : ['restaurant', 'cafe', 'bakery']).includes(b.type)) continue;
    const co = world.companies.get(b.co);
    if (!co || co.dead) continue;
    if (++n > 14) break;
    const menu = menuOf(world, co).map(dishById).filter(Boolean);
    const meals = menu.filter(d => isMeal(d));
    if (!meals.length) continue;
    const d = choose(world, a, meals, delivery ? { ...wopt, score: d => (/치킨|피자|짜장|짬뽕|탕수육|족발|보쌈|떡볶이|버거|김밥|덮밥|찜닭|마라/.test(d.n) ? 1.5 : 0) } : wopt);
    const sc = taste(world, a, d, { ...wopt, jitter: 0 }) + (co.q ?? 40) / 25 + (co.rep ?? 40) / 40 - Math.hypot(b.x - a.x, b.z - a.z) / 260
      + (lunch && b.type !== 'restaurant' ? -1.2 : 0) + gauss() * 0.8;
    if (sc > bs) { bs = sc; best = { where: b.id, rest: b.id, dish: d, how: co.name }; }
  }
  return best;
}

/** 먹고 나서: 배부름·기분·식단·체중·좋아하는 음식·그리운 맛·커피·술 */
export function eatDone(world, a, act) {
  const f = diet(world, a);
  const d = dishById(act.dish);
  const fed = act.fed ?? 1;
  const s = world.settlements.get(a.sid);
  const h = localHour(world.clock, s.lon);
  const age = ageOf(a, world.clock);
  if (!d) { a.nd.food = clamp(a.nd.food + 55 * fed); return; }
  // 배부름: 든든한 음식일수록
  const full = (18 + d.kc * 0.85) * fed;
  a.nd.food = clamp(a.nd.food + full);
  f.kcDay += d.kc * fed;
  if (d.cat === 'snack' || d.cat === 'street' || d.cat === 'dessert') f.snackDay = (f.snackDay || 0) + 1;
  // 맛 → 기분
  const t = taste(world, a, d, { jitter: 0 });
  a.mood = clamp(a.mood + t * 1.6, -100, 100);
  if (act.shared && act.withN >= 2) {
    // 식구와 함께 먹은 밥: 마음이 채워지고 정이 든다
    a.nd.social = clamp(a.nd.social + 4 + act.withN * 2);
  }
  // 식단: 단백질·채소는 좋고, 튀김·설탕·가공은 나쁘다. 같은 것만 먹으면 균형이 무너진다
  const varied = !f.recent.slice(0, 3).includes(d.id);
  const score = clamp(38 + d.p * 9 + d.v * 9 - d.j * 13 + (varied ? 8 : -8) + (d.cu === 'prim' ? -5 : 0));
  f.bal += (score - f.bal) * 0.07;
  f.junk += (d.j * 33 - f.junk) * 0.07;
  f.fedAt = world.clock; f.fedQ = clamp((d.kc / 70) * fed * (0.6 + score / 250), 0, 1.2);
  f.recent.unshift(d.id); if (f.recent.length > 8) f.recent.length = 8;
  // 처음 먹어 보는 나라 음식
  if (d.cu !== f.cu && d.cu !== 'prim' && d.cu !== 'world' && !f.cus[d.cu]) {
    remember(world, a, `처음으로 ${CUISINES[d.cu]}을(를) 먹어 봤다 (${d.n})`, 3, t > 0 ? 0.5 : 0, [], 'life');
    think(world, a, t > 1 ? `${d.n}… 생각보다 입에 맞는다!` : t < -1 ? `${d.n}은(는) 내 입맛엔 좀 낯설다.` : `${d.n}, 새로운 맛이다.`);
  }
  f.cus[d.cu] = (f.cus[d.cu] || 0) + 1;
  // 좋아하게 되거나 싫어지게 되거나
  if (t >= 2.4 && !f.fav.includes(d.id) && chance(0.22)) {
    f.fav.unshift(d.id); if (f.fav.length > 6) f.fav.length = 6;
    remember(world, a, `${d.n}이(가) 입에 딱 맞았다`, 2, 0.6, [], 'life');
  } else if (t <= -2.4 && !f.hate.includes(d.id) && chance(0.2)) {
    f.hate.unshift(d.id); if (f.hate.length > 5) f.hate.length = 5;
    if (chance(0.5)) think(world, a, `${d.n}은(는) 정말 안 맞는다. 다음엔 다른 걸 먹어야지.`);
  }
  // 어릴 때 식구가 해 준 밥 → 평생 그리운 맛
  if (age < 13 && act.cook && act.cook !== a.id && d.home && t >= 1.2 && (!f.comfort || chance(0.04))) {
    f.comfort = d.id;
    const c = world.anns.get(act.cook);
    if (c && a.parents.includes(c.id)) f.comfortBy = c.gender === 'F' ? '엄마' : '아빠';
  }
  // 요리한 사람은 솜씨가 늘고, 맛있게 먹어 주면 뿌듯하다
  if (act.cook) {
    const c = world.anns.get(act.cook);
    if (c && c.sk.cooking != null) c.sk.cooking = clamp(c.sk.cooking + 0.15 + d.sk / 200);
    if (c && c !== a && t > 2 && chance(0.06)) think(world, c, `${a.name}이(가) ${d.n}을(를) 맛있게 먹었다. 해 준 보람이 있다.`);
  }
  // 식당: 맛있으면 소문이 난다. 솜씨 없는 집은 가끔 배탈이 난다
  if (act.rest) {
    const b = world.buildings.get(act.rest), co = b?.co && world.companies.get(b.co);
    if (co && (co.q ?? 40) < 30 && chance(0.004 * (30 - (co.q ?? 40)))) {
      a.sick = Math.max(a.sick || 0, 12 + rand() * 14);
      co.rep = clamp((co.rep ?? 40) - 6);
      remember(world, a, `${co.name}에서 먹은 ${d.n} 때문에 배탈이 났다`, 3, -0.5, [], 'health');
      think(world, a, '배가 살살 아프다… 아까 그 음식이 상했나 보다.');
    }
    if (co) {
      // 소문 = 손님들이 느낀 맛의 평균 (맛 점수 0 → 50, 2 → 74) + 주방 솜씨
      const sat = clamp(50 + t * 12 + ((co.q ?? 40) - 45) * 0.5);
      co.rep = (co.rep ?? 40) + (sat - (co.rep ?? 40)) * 0.004;
      if (co.rep >= 78 && !co.famous) { co.famous = true; world.news('biz', `🍽️ ${co.name}이(가) 맛집으로 소문났다 — ${d.n}이(가) 일품이라고`, { country: co.country, sid: co.sid }, 1); }
    }
  }
  // 회식: 동료들과 정이 들고 (대개 술이 돈다)
  if (act.hoesik) {
    const co = world.companies.get(act.hoesik);
    a.nd.social = clamp(a.nd.social + 12);
    if (co) for (const id of co.emp.concat(co.ceo)) {
      const b = world.anns.get(id);
      if (!b || b === a || b.act?.rest !== act.rest) continue;
      const r = a.rels[b.id];
      if (r) { r.f = clamp(r.f + 1.5); r.a = clamp(r.a + (a.tr.E > 45 ? 1.5 : -0.5), -100, 100); }
    }
  }
  // 커피·차: 아침·점심 뒤에 (습관이 있는 어른)
  const era = world.countries[a.country]?.era ?? 0;
  if (age >= 16 && h < 15 && era >= 2 && chance(f.coffee / 140) && d.dr !== 'caf') {
    const dr = drinkOf(world, a, era, 'caf', !act.rest);
    if (dr) { a.cond = a.cond || {}; a.cond.caf = world.clock + 200; a.cond.cafAt = world.clock; act.extra = dr.n; act.extraKind = 'caf'; }
  }
  if (d.dr === 'caf') { a.cond = a.cond || {}; a.cond.caf = world.clock + 200; a.cond.cafAt = world.clock; }
  // 술: 저녁에 (술 습관·스트레스·함께 먹는 사람)
  if (age >= 19 && h >= 17.5 && era >= 1) {
    const wk = Math.floor(world.clock / DAY) % 7;   // 주말 저녁엔 조금 더
    const p = f.drink / 100 * 0.36 + (a.stress > 60 ? 0.12 : 0) + (act.withN >= 2 || act.rest ? 0.08 : 0) + (wk >= 5 ? 0.06 : 0) + (act.hoesik ? 0.55 : 0) - (a.preg ? 1 : 0);
    if (chance(p)) {
      const dr = drinkOf(world, a, era, 'alc', !act.rest);
      if (dr) { drinkAlcohol(world, a, dr, f, act); act.extraKind = 'alc'; }
    }
  }
}

function drinkOf(world, a, era, kind, home = false) {
  const f = diet(world, a);
  // 집에서는 싸고 간단한 것 (커피믹스·드립 커피·차·소주·맥주), 밖에서는 카페 음료·칵테일까지
  const list = [...dishesOf(f.cu), ...(era >= 5 && !home ? dishesOf('world') : [])].filter(d => d.dr === kind && d.era <= era && (!home || d.t <= 0.5));
  return choose(world, a, list, {});
}

function drinkAlcohol(world, a, dr, f, act) {
  let amt = 1 + (f.drink > 65 && chance(0.4) ? 2 : chance(0.3) ? 1 : 0) + (a.stress > 75 && chance(0.3) ? 1 : 0);
  a.nd.fun = clamp(a.nd.fun + 7 * amt);
  a.stress = clamp(a.stress - 5 * amt);
  if (act.withN >= 2 || act.rest) a.nd.social = clamp(a.nd.social + 4 * amt);
  a.health = clamp(a.health - 0.12 * amt);
  f.drinks = (f.drinks || 0) + amt;
  f.drinkDay = (f.drinkDay || 0) + amt;
  act.extra = `${dr.n}${amt >= 3 ? ' (과음)' : amt === 2 ? ' 두어 잔' : ' 한 잔'}`;
  if (amt >= 3) {
    a.cond = a.cond || {};
    a.cond.hang = world.clock + 14 * 60; // 다음 날 낮까지 숙취
    if (chance(0.3)) remember(world, a, `${dr.n}을(를) 너무 많이 마셨다`, 2, -0.2, [], 'life');
  }
}

/** 컨디션: 일·공부의 능률 (0.45 ~ 1.25) 과 그 까닭 */
export function condition(world, a) {
  let k = 1;
  const why = [];
  const f = a.fd;
  const fd = a.nd.food, en = a.nd.energy;
  if (fd < 15) { k *= 0.72; why.push('배고파 힘이 없다'); }
  else if (fd < 35) { k *= 0.88; why.push('출출함'); }
  else if (f && world.clock - f.fedAt < 240 && f.fedQ > 0.6) { k *= 1.05; why.push('든든함'); }
  if (en < 15) { k *= 0.7; why.push('졸림'); }
  else if (en < 35) { k *= 0.87; why.push('피곤함'); }
  if (a.cond?.caf > world.clock) { k *= 1.06; why.push('카페인'); }
  if (a.cond?.hang > world.clock) { k *= 0.8; why.push('숙취'); }
  if (a.stress > 75) { k *= 0.88; why.push('스트레스'); }
  if (a.mood > 40) { k *= 1.04; why.push('기분 좋음'); } else if (a.mood < -40) { k *= 0.9; why.push('울적함'); }
  if (a.health < 45) { k *= 0.85; why.push('몸이 안 좋음'); }
  if ((a.chronic || []).some(c => c.k === 'depression' && !c.ctl)) { k *= 0.85; why.push('우울'); }
  if ((a.chronic || []).some(c => c.k === 'dementia')) { k *= 0.85; why.push('깜빡깜빡함'); }
  if (f && f.bal > 72) { k *= 1.03; why.push('잘 챙겨 먹음'); } else if (f && f.bal < 32) { k *= 0.95; why.push('끼니가 부실함'); }
  return { k: clamp(k, 0.45, 1.25), why };
}
export const condK = (world, a) => condition(world, a).k;

/**
 * 건강의 목표치: 아프지 않으면 건강은 날마다 이쪽으로 조금씩 다가간다.
 * 타고난 체질 · 나이 · 식단(균형·군것질) · 체중 · 운동 습관 · 술 · 스트레스 · 그 시대의 의료
 */
export function healthTarget(world, a, age) {
  const f = a.fd;
  const era = world.countries[a.country]?.era ?? 0;
  let t = 60 + a.ab.hea * 0.25 + (age < 30 ? 8 : age < 45 ? 4 : 0) - Math.max(0, age - 45) * 0.55 - Math.max(0, age - 75) * 0.6;
  if (f) {
    t += (f.bal - 55) * 0.18 - Math.max(0, f.junk - 40) * 0.12;
    t -= Math.max(0, f.bmi - 25) * 1.3 + Math.max(0, 18.5 - f.bmi) * 2.5;
    t += Math.min(8, (f.exAvg ?? 0.2) * 10);
    t -= Math.max(0, (f.drinkAvg ?? 0) - 0.4) * 8;
  }
  t -= Math.max(0, a.stress - 60) * 0.15;
  t += era >= 5 ? 4 : era >= 4 ? 2 : 0;
  t -= chronicHit(a);   // 고혈압·당뇨·치매·우울증·암 (약을 먹으면 덜)
  return clamp(t, 5, 100);
}

/** 하루에 한 번: 식단이 몸에 쌓인다 (건강·체중), 생각과 기억 */
export function dietDaily(world, a) {
  const f = diet(world, a);
  const age = ageOf(a, world.clock);
  // (식단·체중·운동·술은 건강의 '목표치'로 들어간다: healthTarget)
  // 체중 (어른만): 타고난 기준점으로 천천히 다가가되, 군것질·간식·술·중년은 살을 찌우고 운동은 뺀다.
  // 몇 시간씩 굶는 날이 이어지면 빠진다
  f.snackAvg = (f.snackAvg ?? 0.4) * 0.93 + (f.snackDay || 0) * 0.07;
  if (age >= 16) {
    const era = world.countries[a.country]?.era ?? 0;
    const target = bmiSet(a, era) - (f.dietUntil > world.clock ? 1.5 : 0) + (f.junk - 30) * 0.05 + (f.snackAvg - 0.4) * 1.2 + (f.drinkAvg ?? 0) * 0.8
      - Math.min(1.8, (f.exAvg ?? 0.2) * 2.2) + (age >= 35 && age < 65 ? 1.2 : 0) - (age >= 75 ? 1 : 0);
    f.bmi = clamp(f.bmi + (target - f.bmi) * 0.01 - ((f.hungryMin || 0) > 240 ? 0.03 : 0), 15, 42);
    f.bmi = Math.round(f.bmi * 100) / 100;
    if (f.bmi > 27 && chance(0.04)) think(world, a, pick(['요즘 살이 좀 쪘다. 운동 좀 해야겠다.', '바지가 꽉 낀다…', '야식을 줄여야겠다.']));
  }
  // 다이어트: 살이 찐 성실한 사람은 결심하고, 마음 약한 사람은 무너진다
  if (age >= 16 && !(f.dietUntil > world.clock) && f.bmi > 25.5 && chance(0.004 + (a.tr.C - 50) / 8000 + (f.bmi - 25.5) * 0.002)) {
    f.dietUntil = world.clock + (20 + rand() * 50) * DAY;
    think(world, a, pick(['오늘부터 다이어트다! 야식 끊는다.', '이번엔 진짜 살 뺀다.', '샐러드랑 친해져야겠다…']));
  } else if (f.dietUntil > world.clock && chance(a.tr.N / 2500)) {
    f.dietUntil = 0;
    think(world, a, pick(['치킨 앞에서 다이어트가 무너졌다…', '다이어트는 내일부터…', '먹고 싶은 건 먹고 살자.']));
  }
  if (f.junk > 60 && chance(0.05)) think(world, a, pick(['요즘 너무 대충 먹었다. 채소 좀 먹자.', '라면이랑 과자로 때우는 날이 많다…', '집밥이 먹고 싶다.']));
  if (f.bal > 75 && chance(0.02)) think(world, a, '요즘 잘 챙겨 먹어서 그런지 몸이 가볍다.');
  if (f.comfort != null && age >= 20 && chance(0.012)) {
    const d = dishById(f.comfort);
    if (d) think(world, a, `${f.comfortBy ? f.comfortBy + '가 해 주던 ' : '어릴 때 먹던 '}${d.n}이(가) 그립다.`);
  }
  if (a.cond?.hang > world.clock && chance(0.5)) think(world, a, pick(['머리가 깨질 것 같다… 어제 너무 마셨다.', '다시는 이렇게 안 마셔야지.', '해장국이 필요하다.']));
  f.exAvg = (f.exAvg ?? 0.2) * 0.93 + f.exDay * 0.07;            // 운동 습관 (하루 평균 시간)
  f.drinkAvg = (f.drinkAvg ?? 0) * 0.93 + (f.drinkDay || 0) * 0.07; // 술 (하루 평균 잔)
  f.kcDay = 0; f.exDay = 0; f.drinkDay = 0; f.snackDay = 0; f.hungryMin = 0;
  // 요리하는 직업은 일하면서 솜씨가 는다
  if (a.job && COOK_JOBS.has(a.job.id) && a.sk.cooking != null) a.sk.cooking = clamp(a.sk.cooking + 0.35);
}

/** 하루에 한 번: 식당의 솜씨 = 요리하는 직원들의 요리 실력 */
export function kitchenDaily(world, co) {
  // 회식 날 잡기: 산업 시대 이후, 직원 다섯 이상인 회사가 한 달에 한두 번 (평일)
  const era = world.countries[co.country]?.era ?? 0;
  const dayN = Math.floor(world.clock / DAY);
  if (era >= 4 && co.emp.length >= 4 && dayN % 7 < 5 && chance(1 / 22)) {
    const s = world.settlements.get(co.sid);
    let best = null;
    for (const id of s?.buildings || []) {
      const b = world.buildings.get(id);
      const rc = b?.done && b.type === 'restaurant' && b.co && world.companies.get(b.co);
      if (rc && !rc.dead && (!best || (rc.rep ?? 40) > best.rep)) best = { id, rep: rc.rep ?? 40 };
    }
    if (best) co.hoesik = { day: dayN, rest: best.id };
  }
  if (!['restaurant', 'cafe', 'bakery'].includes(co.ind)) return;
  let sum = 0, n = 0;
  for (const id of [co.ceo, ...co.emp]) {
    const b = world.anns.get(id);
    if (!b || !b.alive) continue;
    if (b.job && COOK_JOBS.has(b.job.id) || id === co.ceo) { sum += b.sk.cooking ?? b.sk.crafting ?? 30; n++; }
  }
  co.q = n ? Math.round(sum / n) : 30;
}

/** 배고플 때 떠오르는 음식 이름 */
export function craving(world, a) {
  const f = diet(world, a);
  if (f.comfort != null && chance(0.25)) return dishById(f.comfort)?.n;
  if (f.fav.length && chance(0.6)) return dishById(pick(f.fav))?.n;
  const era = world.countries[a.country]?.era ?? 0;
  const pool = menuPool(world, a, era, f.cu).filter(d => MAIN_CATS.has(d.cat));
  return pool.length ? pick(pool).n : null;
}

/** 화면용: 식성·식단·컨디션 */
export function foodOut(world, a) {
  const f = a.fd;
  if (!f) return null;
  const cond = condition(world, a);
  const nm = id => dishById(id)?.n;
  const bmi = f.bmi;
  const body = bmi < 18.5 ? '마른 편' : bmi < 23 ? '보통' : bmi < 25 ? '조금 통통' : bmi < 30 ? '통통' : '비만';
  return {
    cuisine: CUISINES[f.cu], spicy: f.spicy, sweet: f.sweet, adv: f.adv, hateFl: f.hateFl ? FLAVOR_NAMES[f.hateFl] : null,
    fav: f.fav.map(nm).filter(Boolean), hate: f.hate.map(nm).filter(Boolean),
    comfort: f.comfort != null ? { n: nm(f.comfort), by: f.comfortBy || null } : null,
    recent: f.recent.slice(0, 5).map(nm).filter(Boolean),
    bal: Math.round(f.bal), junk: Math.round(f.junk), bmi: Math.round(bmi * 10) / 10, body,
    coffee: f.coffee, drink: f.drink, cooking: Math.round(a.sk.cooking ?? 0),
    tried: Object.keys(f.cus).filter(k => k !== 'prim' && k !== 'world').map(k => CUISINES[k]),
    cond: { k: Math.round(cond.k * 100), why: cond.why },
    caf: a.cond?.caf > world.clock, hang: a.cond?.hang > world.clock,
  };
}

export const MEAL_EMOJI = { main: '🍽️', soup: '🍲', rice: '🍚', noodle: '🍜', side: '🥢', snack: '🍢', street: '🍢', bread: '🥐', dessert: '🍰', drink: '🥤', alcohol: '🍶' };
export { DISHES, CAT_NAMES };
