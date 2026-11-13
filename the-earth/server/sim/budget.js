// 돈과 가계부.
// 게임 화폐 1단위 = 1,530원. '정보화 시대'의 물가·임금이 2026년 한국과 같고, 그 이전 시대는 시대의 임금 수준만큼 낮다(현재 가치로 환산).
// 월급에서는 2026년 기준 4대보험(근로자 부담 약 9.72%)과 소득세(누진, 실효세율)가 빠지고,
// 매일 식비·주거비·공과금·통신비·교통비·의류·보험·자녀 교육비가 나간다. 모든 돈의 드나듦은 앤의 월별 가계부에 남는다.
import { ERAS, BUILDINGS } from './data.js';
import { clamp, ageOf, dateOf, chance, pick } from './util.js';
import { lifeEvent } from './ann.js';

export const WON = 1530;          // 원 / 단위
export const REF_PAY = 3600;      // 연봉 기준값(만원): 이 연봉의 직업이 payMul 1
export const WORKDAYS = 261;      // 1년 근무일
export const wageOfEra = era => ERAS[clamp(era, 0, 7)].wage;
export const eraF = era => wageOfEra(era) / ERAS[6].wage;
/** 2026년 원 → 그 시대의 게임 단위 */
export const unitsOf = (won, era) => won / WON * eraF(era);
export const wonOf = units => units * WON;

// ── 공제 (2026년) ──
// 국민연금 4.75% + 건강보험 3.595% + 장기요양 0.4724% + 고용보험 0.9%
export const INS_RATE = 0.0972;
// 소득세+지방소득세 실효세율 (연봉 만원 → 비율). 간이세액표·연말정산 평균을 단순화한 값
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
  const annual = fullDay * WORKDAYS * WON / 1e4 / eraF(era);
  const lever = clamp((country.tax || 0.12) / 0.12, 0.7, 1.5); // 지도자가 세금을 올리면 소득세도 오른다
  return { ins: gross * INS_RATE, tax: gross * incomeTaxRate(annual) * lever };
}

// ── 가계부 ──
const monthKey = clock => { const d = dateOf(clock); return d.year * 12 + d.month; };
/** kind: inc(수입) / ded(공제) / exp(지출) */
export function book(world, a, kind, cat, amt) {
  if (!(amt > 0)) return;
  const k = monthKey(world.clock);
  if (!a.led || a.led.k !== k) {
    if (a.led) a.ledPrev = a.led;
    a.led = { k, inc: {}, ded: {}, exp: {} };
  }
  a.led[kind][cat] = (a.led[kind][cat] || 0) + amt;
}
const sum = o => Object.values(o || {}).reduce((t, v) => t + v, 0);
export function ledgerOut(l) {
  if (!l) return null;
  const conv = o => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Math.round(v * WON / 100) * 100]).filter(([, v]) => v > 0).sort((x, y) => y[1] - x[1]));
  const d = { year: Math.floor((l.k - 1) / 12), month: ((l.k - 1) % 12) + 1 };
  return { y: d.year, m: d.month, inc: conv(l.inc), ded: conv(l.ded), exp: conv(l.exp), net: Math.round((sum(l.inc) - sum(l.ded) - sum(l.exp)) * WON / 100) * 100 };
}
export const monthNet = l => (l ? sum(l.inc) - sum(l.ded) : 0);

// ── 생활비 (2026년 원 / 월) ──
const RENT = { townhouse: 550000, apartment: 900000, skytower: 1800000 };
const UPKEEP = { hut: 0, cottage: 20000, house: 30000, brickhouse: 60000, townhouse: 100000, apartment: 250000, skytower: 450000 };
const LEVEL_F = [0.55, 0.65, 0.8, 1, 1.2, 1.4]; // 마을 → 메가시티: 집값·월세
const EDU_FEE = { kinder: 200000, elem: 400000, middle: 500000, high: 600000 };
// 지출이 흘러가는 회사 (같은 마을에 있으면 그 회사 매출이 된다)
const SINK = {
  식비: ['retail', 'mall', 'convenience', 'trade'], 교통비: ['bus', 'taxi'], '자동차 유지비': ['repair'], 통신비: ['telecom'],
  '의류·생활용품': ['mall', 'retail', 'tailor'], '보험·의료': ['insurance', 'pharmacy', 'clinic', 'dental'], 교육비: ['hagwon'], 병원비: ['clinic', 'pharmacy', 'dental', 'oriental'],
};
function sinkTo(world, s, cat, amt) {
  const inds = SINK[cat];
  if (!inds || !s) return;
  const key = s.id + ':' + cat;
  let list = world.sinkCache?.get(key);
  if (!list) {
    list = [...world.companies.values()].filter(c => !c.dead && !c.pub && c.sid === s.id && inds.includes(c.ind)).map(c => c.id);
    (world.sinkCache ||= new Map()).set(key, list);
  }
  if (!list.length) return;
  const co = world.companies.get(pick(list));
  if (co && !co.dead) { co.money += amt; co.rev += amt; }
}

/** 하루치 생활비. 반환: 모자란 금액 */
export function livingDaily(world, a, c) {
  const era = c.era, now = world.clock;
  const s = world.settlements.get(a.sid);
  const perDay = won => unitsOf(won, era) / 30;
  const sp = a.spouse && world.anns.get(a.spouse);
  const together = sp && sp.alive && sp.home && sp.home === a.home;
  const head = !together || a.id < sp.id; // 함께 사는 부부는 한 사람이 집안 살림을 낸다
  let short = 0;
  const pay = (cat, amt, opts = {}) => {
    if (!(amt > 0)) return;
    let left = amt;
    for (const who of together ? [a, sp] : [a]) {
      const p = Math.min(who.money, left);
      if (p > 0) { who.money -= p; left -= p; book(world, who, 'exp', cat, p); if (opts.to) opts.to(p); else sinkTo(world, s, cat, p); }
      if (left <= 1e-9) break;
    }
    short += left;
  };
  const age = ageOf(a, now);
  // 개인 지출
  pay('식비', perDay(era >= 1 ? 400000 : 150000));
  if (era >= 6) pay('통신비', perDay(55000));
  if (era >= 2) pay('의류·생활용품', perDay(era >= 5 ? 90000 : 50000));
  if (era >= 5) pay('보험·의료', perDay(60000));
  if (a.sick > 0 && era >= 3) pay('병원비', perDay(450000) * Math.min(1, a.sick / 30));
  // 교통: 근대부터는 돈이 넉넉하면 차를 산다
  if (era >= 5) {
    const rich = a.money * WON / eraF(era) > 30000000 || monthNet(a.ledPrev) * WON / eraF(era) > 3800000;
    if (!a.car && rich && age >= 21 && age < 80 && chance(0.01)) {
      const price = unitsOf(28000000, era);
      if (a.money > price * 1.5) {
        a.money -= price; book(world, a, 'exp', '자동차 구입', price); a.car = true;
        lifeEvent(world, a, '첫 자동차를 샀다', '🚗');
      }
    }
    if (a.car) pay('자동차 유지비', perDay(350000)); else pay('교통비', perDay(65000));
  } else if (era >= 4) pay('교통비', perDay(30000));
  if (!head) return short;
  // 집안 살림 (가장)
  const home = a.home && world.buildings.get(a.home);
  if (home) {
    const lv = LEVEL_F[clamp(s?.level ?? 0, 0, 5)];
    if (home.dev && RENT[home.type] && era >= 4) {
      const dev = home.dev !== 'gov' && world.companies.get(home.dev);
      pay('월세', perDay(RENT[home.type] * lv), { to: amt => { if (dev && !dev.dead) { dev.money += amt; dev.rev += amt; } else c.treasury += amt; } });
    } else if (UPKEEP[home.type] && home.owner != null) pay('관리비·수선', perDay(UPKEEP[home.type] * (BUILDINGS[home.type]?.era >= 4 ? lv : 1)));
  }
  const kids = a.kids.map(id => world.anns.get(id)).filter(k => k && k.alive && ageOf(k, now) < 19 && (!k.home || k.home === a.home));
  const members = 1 + (together ? 1 : 0) + kids.length;
  if (era >= 4) pay('공과금', perDay(era >= 5 ? 70000 + 25000 * members : 40000));
  if (era >= 5 && era < 6) pay('통신비', perDay(25000));
  for (const k of kids) {
    const ka = ageOf(k, now);
    pay('식비', perDay(ka < 4 ? 150000 : 220000));
    if (era >= 6 && ka >= 10) pay('통신비', perDay(30000));
    if (era >= 5 && k.school) {
      const fee = EDU_FEE[k.school.key];
      if (fee) {
        const afford = monthNet(a.ledPrev) * WON / eraF(era) > 3000000 || a.money * WON / eraF(era) > 15000000;
        pay('교육비', perDay(fee * (afford ? 1 : 0.4)));
      }
    }
  }
  return short;
}

/** 국민연금: 근대부터 65세 이상 일하지 않는 앤은 매달 연금을 받는다 (2025년 평균 약 68만원) */
export function pensionDaily(world, a, c) {
  if (c.era < 5 || a.job || ageOf(a, world.clock) < 65) return;
  const amt = unitsOf(680000, c.era) / 30;
  const paid = Math.min(amt, Math.max(0, c.treasury));
  if (paid <= 0) return;
  c.treasury -= paid; a.money += paid;
  book(world, a, 'inc', '국민연금', paid);
}
