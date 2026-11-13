// 돈과 가계부 — 시대마다 돈도, 월급 구조도, 생활비도 다르다.
//
// 게임 속 돈은 '단위'로 셈하고, 화면에는 그 시대의 돈으로 보여 준다:
//   원시 조개 → 농경 곡식(섬·되) → 고대 전(錢) → 중세 냥 → 산업 원(1960~70년대 물가) → 근대 원(1990년대) → 정보화 원(2026년) → 우주 원(2060년대 예상)
// 시대마다 '보통 일자리' 한 달 월급이 그 시대의 실제 수준이 되도록 환산한다 (ERA_MONEY.month).
// 월급 구조(누가 얼마나 더 버는가)도 시대마다 다르고(ERA_PAY), 생활비 항목·비중도 다르다(ERA_LIVING).
import { ERAS, BUILDINGS } from './data.js';
import { clamp, ageOf, dateOf, chance, pick } from './util.js';
import { lifeEvent } from './ann.js';

export const WON = 1530;          // 정보화 시대 1단위 ≈ 1,530원 (2026년)
export const REF_PAY = 3600;      // 연봉표 기준값(만원): 이 연봉의 직업이 배율 1
export const WORKDAYS = 261;      // 주5일 1년 근무일
export const wageOfEra = era => ERAS[clamp(era, 0, 7)].wage;
export const eraF = era => wageOfEra(era) / ERAS[6].wage;
/** 그 시대 '보통 일자리'의 한 달 월급(단위) */
export const refMonth = era => wageOfEra(era) * 21.75;
/** 2026년 원 → 정보화 시대 기준 단위 (실질 가치) */
export const unitsOf = (won, era) => won / WON * eraF(era);

// ── 시대의 돈 ──
// month: 보통 일자리 한 달 월급을 그 시대 돈으로. note: 화면 설명
export const ERA_MONEY = [
  { cur: '조개', month: 40, note: '원시 시대엔 돈이 없다 — 조개·곡식·가죽으로 물물교환한다. (보통 사람 한 달 몫 ≈ 조개 40개)' },
  { cur: '곡식', month: 60, note: '농경 시대: 곡식으로 셈한다. 1섬 = 100되. (보통 사람 한 달 품삯 ≈ 곡식 60되)' },
  { cur: '전', month: 300, note: '고대: 동전(錢)이 생겼다. 1관 = 1,000전. (보통 사람 한 달 품삯 ≈ 300전)' },
  { cur: '냥', month: 6, note: '중세: 상평통보. 1냥 = 10전 = 100푼. 쌀 한 섬이 5냥 남짓. (보통 사람 한 달 품삯 ≈ 6냥)' },
  { cur: '원', month: 20000, note: '산업 시대: 1960~70년대 물가. 짜장면 한 그릇 100원 안팎, 버스비 10원, 공장 노동자 월급 2만원 남짓.' },
  { cur: '원', month: 1100000, note: '근대: 1990년대 물가. 짜장면 2,500원, 버스비 400원, 직장인 월급 100만원 남짓.' },
  { cur: '원', month: 3000000, note: '정보화 시대: 2026년 한국 물가. 짜장면 7,500원, 최저임금 시급 10,320원, 근로자 중위 월급 약 285만원.' },
  { cur: '원', month: 7500000, note: '우주 시대: 2060년대 예상 물가 — 지금의 두 배 남짓. 단순 반복 일은 자동화로 줄었다.' },
];
/** 단위 → 그 시대 돈의 수 */
export const perUnit = era => ERA_MONEY[clamp(era, 0, 7)].month / refMonth(clamp(era, 0, 7));

// ── 시대별 월급 구조 ──
// exp: 연봉 격차를 얼마나 벌리나 (1 = 오늘날, 작을수록 다 같이 비슷하게 번다)
// cat/job: 그 시대에 특히 대우받던(혹은 덜 받던) 분야·직업의 배율
export const ERA_PAY = [
  { exp: 0.25, job: { shaman: 1.6, toolmaker: 1.2, hunter: 1.1 } },
  { exp: 0.4, job: { craftsman: 1.2, builder: 1.1, merchant: 1.2, shaman: 1.4 } },
  { exp: 0.6, cat: { '공공·안전': 1.5, 종교: 1.6 }, job: { scholar: 1.6, merchant: 1.3, blacksmith: 1.3, teacher: 1.3 } },
  { exp: 0.7, cat: { '공공·안전': 1.4, 종교: 1.4, '의료·보건': 0.9 }, job: { merchant: 1.4, blacksmith: 1.3, teacher: 1.2, kmd: 1.3, scholar: 1.3 } },
  { exp: 0.85, cat: { '제조·생산': 1.15, '교육·돌봄': 0.85, 'IT·연구': 1.1 }, job: { miner: 1.4, sailor: 1.3, banker: 1.3, doctor: 0.8, lawyer: 1.2, driver: 1.2 } },
  { exp: 0.95, cat: { '법률·금융·사무': 1.15, 'IT·연구': 0.9 }, job: { lawyer: 1.3, banker: 1.15, programmer: 0.85, steelworker: 1.1 } },
  { exp: 1 },
  { exp: 1.05, cat: { 'IT·연구': 1.3, '판매·서비스': 0.85, 운송: 0.8 }, job: { astronaut: 1.6, rocketeer: 1.4, ai_eng: 1.4, cashier: 0.7, call_agent: 0.6, cvs_part: 0.7, taxi_driver: 0.6, bus_driver: 0.7, caregiver: 1.2, nurse: 1.15 } },
];
/** 이 시대에 이 직업의 연봉 배율 (기준 연봉 3,600만원 = 1) */
export function eraPayMul(key, job, era) {
  const P = ERA_PAY[clamp(era, 0, 7)];
  const rel = (job?.pay || REF_PAY) / REF_PAY;
  return Math.pow(rel, P.exp) * (P.cat?.[job?.cat] || 1) * (P.job?.[key] || 1);
}

// ── 시대별 법·제도 ──
export const MIN_WAGE = [0, 0, 0, 0, 0, 0.35, 0.6, 0.62];        // 최저임금(하루, 보통 일자리 일당 대비) — 1988년부터, 2026년 시급 10,320원
export const INS_RATE = [0, 0, 0, 0, 0, 0.048, 0.0972, 0.12];    // 4대보험 근로자 부담 — 1990년대 약 4.8%, 2026년 9.72%
export const PENSION = [0, 0, 0, 0, 0, 0.1, 0.23, 0.25];
// 나라 수입: 부가가치세(물건값에 들어 있는 몫), 법인세(회사 이익의 몫) — 한국 부가세 10%(1977~), 법인세 9~24%
export const VAT = [0, 0, 0.02, 0.03, 0.05, 10 / 110, 10 / 110, 10 / 110];
export const CORP_TAX = [0, 0, 0.08, 0.1, 0.12, 0.18, 0.19, 0.2];          // 국민연금(보통 월급 대비) — 2025년 평균 약 68만원
const MEAL = [0.08, 0.08, 0.09, 0.1, 0.11, 0.05, 0.069, 0.06];    // 한 끼 사 먹는 값(보통 일당 대비): 1970년 짜장면 100원, 2026년 9,500원
export const MEAL_LABEL = ['나눠 먹기', '주막', '주막', '주막', '식당', '외식', '외식', '외식'];
export const LEISURE_LABEL = ['물물교환', '장터 나들이', '장터 나들이', '장터·놀이', '시장·극장', '쇼핑·여가', '쇼핑·여가', '쇼핑·여가'];
export const mealPrice = era => MEAL[clamp(era, 0, 7)] * wageOfEra(era);

// 소득세+지방소득세 실효세율 (2026년 기준 연봉 만원 → 비율). 간이세액표·연말정산 평균을 단순화
const TAX_PTS = [[0, 0], [1500, 0], [2000, 0.006], [2600, 0.012], [3000, 0.017], [3600, 0.026], [4000, 0.032], [5000, 0.048], [6000, 0.062], [7000, 0.076], [8000, 0.09], [10000, 0.115], [15000, 0.17], [20000, 0.21], [30000, 0.26], [50000, 0.31], [1e9, 0.33]];
export function incomeTaxRate(annual) {
  for (let i = 1; i < TAX_PTS.length; i++) {
    const [x1, y1] = TAX_PTS[i - 1], [x2, y2] = TAX_PTS[i];
    if (annual <= x2) return y1 + (y2 - y1) * (annual - x1) / (x2 - x1);
  }
  return 0.33;
}
/** 급여에서 빠지는 돈. fullDay = 하루를 다 일했을 때의 급여(연봉 환산용) */
export function deductions(country, gross, fullDay, era) {
  if (era < 5) return { ins: 0, tax: gross * (country.tax || 0) }; // 근대 이전: 나라가 정한 조세만
  const annual = fullDay * WORKDAYS * WON / 1e4 / eraF(era); // 오늘날 가치로 환산한 연봉
  const lever = clamp((country.tax || 0.12) / 0.12, 0.7, 1.5); // 지도자가 세금을 올리면 소득세도 오른다
  return { ins: gross * INS_RATE[era], tax: gross * incomeTaxRate(annual) * lever };
}

// ── 가계부 ──
const monthKey = clock => { const d = dateOf(clock); return d.year * 12 + d.month; };
/** kind: inc(수입) / ded(공제) / exp(지출) */
export function book(world, a, kind, cat, amt) {
  if (!(amt > 0)) return;
  const k = monthKey(world.clock);
  if (!a.led || a.led.k !== k) {
    if (a.led) a.ledPrev = a.led;
    a.led = { k, inc: {}, ded: {}, exp: {}, era: world.countries[a.country]?.era ?? 0 };
  }
  a.led[kind][cat] = (a.led[kind][cat] || 0) + amt;
}
const sum = o => Object.values(o || {}).reduce((t, v) => t + v, 0);
/** 가계부를 화면용으로 (금액은 단위, 소수 둘째 자리) */
export function ledgerOut(l) {
  if (!l) return null;
  const conv = o => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Math.round(v * 100) / 100]).filter(([, v]) => v > 0).sort((x, y) => y[1] - x[1]));
  return { y: Math.floor((l.k - 1) / 12), m: ((l.k - 1) % 12) + 1, era: l.era ?? null, inc: conv(l.inc), ded: conv(l.ded), exp: conv(l.exp), net: Math.round((sum(l.inc) - sum(l.ded) - sum(l.exp)) * 100) / 100 };
}
export const monthNet = l => (l ? sum(l.inc) - sum(l.ded) : 0);

// ── 시대별 생활비 (보통 월급 대비 비중) ──
// food: 어른 한 명 먹을거리 / kidFood: 아이 / cloth: 옷·생활용품 / fuel: [이름, 집당, 식구당] 땔감·연탄·공과금
// phone: [이름, 비중, 'house'이면 집당] / transit: 대중교통 / car: [이름, 유지비, 차값(달치)] / ins: 보험·의료
// rent: 셋집 월세(집 종류별) / edu: 아이 학비·학원비(학교별) / med: 아플 때
export const ERA_LIVING = [
  { food: ['먹을거리', 0.25], kidFood: 0.12 },
  { food: ['곡식·찬거리', 0.3], cloth: ['옷감·짚신', 0.06], kidFood: 0.15 },
  { food: ['곡식·찬거리', 0.3], cloth: ['옷감·신발', 0.07], fuel: ['땔감', 0.04, 0], med: ['약값', 0.15], kidFood: 0.15 },
  { food: ['쌀·찬거리', 0.32], cloth: ['옷감·생활용품', 0.07], fuel: ['땔나무', 0.05, 0.005], med: ['의원 약값', 0.15], edu: ['서당 훈장님 사례', { seodang: 0.05, elem: 0.05 }], kidFood: 0.15 },
  { food: ['쌀·부식', 0.38], cloth: ['옷·생활용품', 0.06], fuel: ['연탄·전기', 0.05, 0.008], transit: ['버스비', 0.03], ins: null, med: ['병원·약값', 0.2], rent: ['셋방 월세', { townhouse: 0.2, apartment: 0.3, skytower: 0.5 }], edu: ['학비·과외', { elem: 0.06, middle: 0.08, high: 0.1 }], kidFood: 0.18 },
  { food: ['식비', 0.28], cloth: ['의류·생활용품', 0.05], fuel: ['공과금', 0.04, 0.01], phone: ['집전화·삐삐', 0.02, 'house'], transit: ['버스·지하철비', 0.04], car: ['자가용 유지비', 0.15, 9], ins: ['의료보험·병원', 0.02], med: ['병원비', 0.15], rent: ['월세', { townhouse: 0.2, apartment: 0.32, skytower: 0.6 }], edu: ['학원비', { kinder: 0.06, elem: 0.12, middle: 0.16, high: 0.2 }], kidFood: 0.14 },
  { food: ['식비', 0.133], cloth: ['의류·생활용품', 0.03], fuel: ['공과금', 0.023, 0.008], phone: ['통신비', 0.018], transit: ['교통비', 0.022], car: ['자동차 유지비', 0.117, 9.3], ins: ['보험·의료', 0.02], med: ['병원비', 0.15], rent: ['월세', { townhouse: 0.183, apartment: 0.3, skytower: 0.6 }], edu: ['학원비', { kinder: 0.067, elem: 0.133, middle: 0.167, high: 0.2 }], kidFood: 0.073 },
  { food: ['식비', 0.11], cloth: ['의류·생활용품', 0.025], fuel: ['에너지 요금', 0.02, 0.006], phone: ['통신·클라우드', 0.025], transit: ['자율주행 이동 구독', 0.05], ins: ['보험·의료', 0.04], med: ['병원비', 0.12], rent: ['월세', { townhouse: 0.18, apartment: 0.3, skytower: 0.55 }], edu: ['AI 맞춤 학습', { kinder: 0.05, elem: 0.08, middle: 0.1, high: 0.12 }], kidFood: 0.06 },
];
const UPKEEP = { hut: 0, cottage: 0.007, house: 0.01, brickhouse: 0.02, townhouse: 0.033, apartment: 0.083, skytower: 0.15 }; // 관리비·수선 (보통 월급 대비)
const LEVEL_F = [0.55, 0.65, 0.8, 1, 1.2, 1.4]; // 마을 → 메가시티: 집값·월세
// 지출이 흘러가는 회사 (같은 마을에 있으면 그 회사 매출이 된다)
const SINK = {
  food: ['retail', 'mall', 'convenience', 'trade', 'farm'], transit: ['bus', 'taxi'], car: ['repair'], phone: ['telecom'],
  cloth: ['mall', 'retail', 'tailor'], ins: ['insurance', 'pharmacy', 'clinic', 'dental'], edu: ['hagwon'], med: ['clinic', 'pharmacy', 'dental', 'oriental'],
};
function sinkTo(world, s, kind, amt) {
  const inds = SINK[kind];
  if (!inds || !s) return;
  const key = s.id + ':' + kind;
  let list = world.sinkCache?.get(key);
  if (!list) {
    list = [...world.companies.values()].filter(c => !c.dead && !c.pub && c.sid === s.id && inds.includes(c.ind)).map(c => c.id);
    (world.sinkCache ||= new Map()).set(key, list);
  }
  if (!list.length) return;
  const co = world.companies.get(pick(list));
  if (co && !co.dead) {
    // 물건값 안의 부가가치세는 나라로
    const c = world.countries[s.country], vat = amt * VAT[clamp(c?.era ?? 0, 0, 7)];
    if (c && vat > 0) { c.treasury += vat; c.taxIn = (c.taxIn || 0) + vat; }
    co.money += amt - vat; co.rev += amt - vat;
  }
}

/** 하루치 생활비. 반환: 모자란 금액 */
export function livingDaily(world, a, c) {
  const era = clamp(c.era, 0, 7), now = world.clock;
  const L = ERA_LIVING[era];
  const M = refMonth(era);
  const perDay = share => share * M / 30;
  const s = world.settlements.get(a.sid);
  const sp = a.spouse && world.anns.get(a.spouse);
  const together = sp && sp.alive && sp.home && sp.home === a.home;
  const head = !together || a.id < sp.id; // 함께 사는 부부는 한 사람이 집안 살림을 낸다
  let short = 0;
  const pay = (label, kind, amt, to = null) => {
    if (!(amt > 0)) return;
    let left = amt;
    for (const who of together ? [a, sp] : [a]) {
      const p = Math.min(who.money, left);
      if (p > 0) { who.money -= p; left -= p; book(world, who, 'exp', label, p); if (to) to(p); else sinkTo(world, s, kind, p); }
      if (left <= 1e-9) break;
    }
    short += left;
  };
  const age = ageOf(a, now);
  // 개인 지출
  pay(L.food[0], 'food', perDay(L.food[1]));
  if (L.phone && L.phone[2] !== 'house') pay(L.phone[0], 'phone', perDay(L.phone[1]));
  if (L.cloth) pay(L.cloth[0], 'cloth', perDay(L.cloth[1]));
  if (L.ins) pay(L.ins[0], 'ins', perDay(L.ins[1]));
  if (a.sick > 0 && L.med) pay(L.med[0], 'med', perDay(L.med[1]) * Math.min(1, a.sick / 30));
  // 이동: 차가 있는 시대엔 넉넉하면 차를 산다
  if (L.car) {
    const rich = a.money > M * 10 || monthNet(a.ledPrev) > M * 1.25;
    if (!a.car && rich && age >= 21 && age < 80 && chance(0.01)) {
      const price = M * L.car[2];
      if (a.money > price * 1.5) {
        a.money -= price; book(world, a, 'exp', '자동차 구입', price); a.car = true;
        lifeEvent(world, a, '첫 자동차를 샀다', '🚗');
      }
    }
    if (a.car) pay(L.car[0], 'car', perDay(L.car[1]));
    else if (L.transit) pay(L.transit[0], 'transit', perDay(L.transit[1]));
  } else if (L.transit) pay(L.transit[0], 'transit', perDay(L.transit[1]));
  if (!head) return short;
  // 집안 살림 (가장)
  const home = a.home && world.buildings.get(a.home);
  if (home) {
    const lv = LEVEL_F[clamp(s?.level ?? 0, 0, 5)];
    const rent = L.rent?.[1]?.[home.type];
    if (home.dev && rent) {
      const dev = home.dev !== 'gov' && world.companies.get(home.dev);
      pay(L.rent[0], 'rent', perDay(rent * lv), amt => { if (dev && !dev.dead) { dev.money += amt; dev.rev += amt; } else c.treasury += amt; });
    } else if (UPKEEP[home.type] && home.owner != null && era >= 2) pay(era >= 4 ? '관리비·수선' : '집 손보기', 'upkeep', perDay(UPKEEP[home.type] * (era >= 4 ? lv : 1)));
  }
  const kids = a.kids.map(id => world.anns.get(id)).filter(k => k && k.alive && ageOf(k, now) < 19 && (!k.home || k.home === a.home));
  const members = 1 + (together ? 1 : 0) + kids.length;
  if (L.fuel) pay(L.fuel[0], 'fuel', perDay(L.fuel[1] + L.fuel[2] * members));
  if (L.phone && L.phone[2] === 'house') pay(L.phone[0], 'phone', perDay(L.phone[1]));
  for (const k of kids) {
    const ka = ageOf(k, now);
    pay(L.food[0], 'food', perDay(L.kidFood * (ka < 4 ? 0.7 : 1)));
    if (L.phone && L.phone[2] !== 'house' && ka >= 10) pay(L.phone[0], 'phone', perDay(L.phone[1] * 0.55));
    if (L.edu && k.school) {
      const fee = L.edu[1][k.school.key];
      if (fee) {
        const afford = monthNet(a.ledPrev) > M || a.money > M * 5;
        pay(L.edu[0], 'edu', perDay(fee * (afford ? 1 : 0.4)));
      }
    }
  }
  return short;
}

/** 국민연금: 근대부터 65세 이상 일하지 않는 앤은 매달 연금을 받는다 */
export function pensionDaily(world, a, c) {
  const share = PENSION[clamp(c.era, 0, 7)];
  if (!share || a.job || ageOf(a, world.clock) < 65) return;
  const amt = share * refMonth(c.era) / 30;
  const paid = Math.min(amt, Math.max(0, c.treasury));
  if (paid <= 0) return;
  c.treasury -= paid; a.money += paid;
  book(world, a, 'inc', '국민연금', paid);
}
