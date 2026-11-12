// 경제: 회사·공공기관·일자리·임금·생산·세금
import { INDUSTRIES, PUBLIC, JOBS, ERAS, BUILDINGS, RANKS, DISCOVERIES, EDU_NAMES, maxEduForEra } from './data.js';
import { clamp, rand, pick, chance, ageOf, YEAR, ga, neun, reul, wa } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { placeBuilding, addWork, siteFeatures } from './settlement.js';
import { companyName } from './names.js';
import { rel } from './social.js';

JOBS.laborer = { name: '일용직', era: 2, skill: 'construction', self: true, out: 'build', rate: 0.6, fit: { ath: .3 } };
JOBS.peasant = { name: '자영농', era: 2, skill: 'farming', self: true, out: 'food', rate: 2.6, fit: { C: .3, ath: .2 } };

const eraOfAnn = (world, a) => world.countries[a.country]?.era ?? 0;
export const wageBase = era => ERAS[clamp(era, 0, 7)].wage;
export const dailyCost = era => wageBase(era) * 0.5;

export function abilityOrTrait(a, k) {
  return k in a.ab ? a.ab[k] : k in a.tr ? a.tr[k] : 50;
}

const VALUE_OF_SKILL = { art: 'art', science: 'knowledge', athletics: 'sport', commerce: 'wealth', leadership: 'power', farming: 'family', construction: 'mastery', crafting: 'mastery', medicine: 'community', teaching: 'community' };

/** 직업 적합도 0..100: 기술 + 능력/성격 궁합 + 가치관 */
export function jobFit(a, jobKey) {
  const j = JOBS[jobKey];
  let s = 28 + a.sk[j.skill] * 0.45;
  for (const k in j.fit) s += (abilityOrTrait(a, k) - 50) * j.fit[k] * 0.55;
  s += ((a.val[VALUE_OF_SKILL[j.skill]] ?? 50) - 50) * 0.25;
  return clamp(s);
}

export function jobTitle(world, a) {
  if (!a.job) return a.retired ? '은퇴' : null;
  const co = a.job.co && world.companies.get(a.job.co);
  if (a.job.ceo && co) return `${co.name} 대표`;
  const j = JOBS[a.job.id];
  const rank = a.job.rank > 0 ? ` (${RANKS[a.job.rank]})` : '';
  return (co ? `${co.name} ` : '') + (j?.name || '일') + rank;
}

export function companiesIn(world, sid) {
  const out = [];
  for (const co of world.companies.values()) if (co.sid === sid && !co.dead) out.push(co);
  return out;
}

/** 산업 수요 배수: 인구 대비 종사자 수 (경쟁이 심하면 매출이 줄어든다) */
export function demandFactor(world, s, indKey) {
  const key = s.id + ':' + indKey;
  const cached = world.dfCache.get(key);
  if (cached !== undefined) return cached;
  const ind = INDUSTRIES[indKey];
  const era = world.countries[s.country].era;
  let workers = 0;
  for (const co of world.companies.values()) if (co.sid === s.id && co.ind === indKey && !co.dead) workers += Math.max(1, co.emp.length);
  const k = ind.k * ERAS[era].cityK;
  const df = clamp((s.pop + 2) / (Math.max(1, workers) * k), 0.25, 1.4);
  world.dfCache.set(key, df);
  return df;
}

/** 지원 가능한 일자리 */
/** 이 직업에 필요한 학력. 시대가 허락하는 수준, 그리고 나라에서 가장 많이 배운 사람의 수준을 넘지 않는다
 *  (대학이 막 생긴 시대에는 아직 졸업생이 없으니, 가장 많이 배운 사람이 가르친다) */
export function eduReq(world, a, jobKey) {
  const j = JOBS[jobKey];
  const c = world.countries[a.country];
  return Math.min(j.edu || 0, maxEduForEra(c.era), c.topEdu ?? 0);
}
export function hasConstruction(world, sid) {
  for (const co of world.companies.values()) if (co.sid === sid && !co.dead && co.ind === 'construction') return true;
  return false;
}

export function openings(world, a, withBlocked = false) {
  const s = world.settlements.get(a.sid);
  const era = eraOfAnn(world, a);
  const out = [], blocked = [];
  const lv = a.eduLv || 0;
  const water = (() => { const f = siteFeatures(s); return f.sea || f.river || f.lake; })();
  for (const co of companiesIn(world, a.sid)) {
    if (co.emp.length >= co.slots) continue;
    const jobs = co.pub ? PUBLIC[co.ind].jobs : INDUSTRIES[co.ind].jobs;
    for (const jk of jobs) {
      const j = JOBS[jk];
      if (j.era > era || (j.maxEra != null && era > j.maxEra)) continue;
      const op = { co, jobKey: jk, wage: co.wage * (0.75 + a.sk[j.skill] / 120) * (1 + (j.edu || 0) * 0.12), fit: jobFit(a, jk) };
      if (lv < eduReq(world, a, jk)) { blocked.push(op); continue; }
      out.push(op);
    }
  }
  for (const jk in JOBS) {
    const j = JOBS[jk];
    if (!j.self || j.era > era || (j.maxEra != null && era > j.maxEra)) continue;
    if (j.water && !water) continue; // 물가가 없으면 어부도 없다
    out.push({ co: null, jobKey: jk, wage: wageBase(era) * 0.55 * (0.5 + a.sk[j.skill] / 100) * (j.rate / 2 + 0.5), fit: jobFit(a, jk) });
  }
  return withBlocked ? { out, blocked } : out;
}

export function setJob(world, a, jobKey, co = null, opts = {}) {
  if (a.job) leaveJob(world, a, null, true);
  a.job = { id: jobKey, co: co?.id ?? null, since: Math.round(world.clock), rank: 0, sat: 60, ceo: !!opts.ceo, perf: 0 };
  a.retired = false;
  if (co) co.emp.push(a.id);
  const title = jobTitle(world, a);
  a.career.push({ t: Math.round(world.clock), text: title });
  if (a.career.length > 30) a.career.shift();
  world.dirtyAnnMeta.add(a.id);
  return title;
}

export function leaveJob(world, a, reason = null, silent = false) {
  if (!a.job) return;
  const co = a.job.co && world.companies.get(a.job.co);
  if (co) {
    co.emp = co.emp.filter(id => id !== a.id);
    if (co.ceo === a.id) {
      co.ceo = null;
      succession(world, co);
    }
  }
  const title = jobTitle(world, a);
  a.job = null;
  a.unemployedSince = world.clock;
  if (!silent && reason) {
    remember(world, a, `${title} 일을 그만두었다 (${reason})`, 5, reason === '해고' ? -0.7 : -0.1, [], 'work');
    lifeEvent(world, a, `${title} 퇴직 — ${reason}`, '📦');
  }
  world.dirtyAnnMeta.add(a.id);
}

function succession(world, co) {
  // 대표가 떠나면 가장 유능하고 신망 있는 직원이 이어받는다
  let best = null, bs = -1;
  for (const id of co.emp) {
    const e = world.anns.get(id);
    if (!e || !e.alive) continue;
    const s = e.sk.leadership + e.amb * 0.5 + (e.job?.rank || 0) * 10;
    if (s > bs) { bs = s; best = e; }
  }
  if (best && !co.pub) {
    co.ceo = best.id;
    best.job.ceo = true;
    remember(world, best, `${co.name}의 새 대표가 되었다`, 8, 0.8, [], 'work');
    lifeEvent(world, best, `${co.name} 대표 취임`, '🏢');
    world.news('biz', `🏢 ${ga(best.name)} ${co.name}의 새 대표가 되었다`, { anns: [best.id], country: co.country }, 1);
    world.dirtyAnnMeta.add(best.id);
  } else if (!co.pub && co.emp.length === 0) {
    closeCompany(world, co, '대표 부재');
  }
}

export function closeCompany(world, co, why) {
  if (co.dead) return;
  co.dead = true;
  co.closed = Math.round(world.clock);
  for (const id of [...co.emp]) {
    const e = world.anns.get(id);
    if (e && e.job?.co === co.id) {
      leaveJob(world, e, why === '파산' ? '회사 파산' : '회사 폐업');
      feel(e, -12, '불안', co.name);
      think(world, e, '하루아침에 일자리를 잃었다. 앞으로 어떻게 하지…');
    }
  }
  co.emp = [];
  const b = world.buildings.get(co.b);
  if (b) { b.co = null; b.name = b.name + ' (폐업)'; world.dirtyBuildings.add(b.id); }
  world.news('biz', `📉 ${co.name}이(가) ${why}으로 문을 닫았다`, { country: co.country, sid: co.sid }, 1);
  world.dirtyCompanies.add(co.id);
}

/** 채용 시도: 대표/기관의 판단 (실력 + 대표와의 친분) */
export function apply(world, a, opening) {
  const { co, jobKey } = opening;
  if (!co) {
    const title = setJob(world, a, jobKey);
    remember(world, a, `${title}(으)로 살아가기로 했다`, 5, 0.4, [], 'work');
    lifeEvent(world, a, `${title}의 삶을 시작`, '🧺');
    return true;
  }
  const j = JOBS[jobKey];
  let p = 0.35 + a.sk[j.skill] / 200 + jobFit(a, jobKey) / 400;
  const boss = co.ceo && world.anns.get(co.ceo);
  if (boss) {
    const r = boss.rels[a.id];
    if (r) p += r.a / 250; // 아는 사람이면 더 쉽게 (혹은 더 어렵게)
    if (boss.parents.includes(a.id) || a.parents.includes(boss.id) || boss.spouse === a.id) p += 0.3;
  }
  if (chance(clamp(p, 0.05, 0.95))) {
    const title = setJob(world, a, jobKey, co);
    remember(world, a, `${co.name}에 ${JOBS[jobKey].name}(으)로 취직했다`, 6, 0.7, boss ? [boss.id] : [], 'work');
    lifeEvent(world, a, `${co.name} 입사 (${JOBS[jobKey].name})`, '💼');
    feel(a, 15, '기쁨', co.name);
    if (boss) { rel(boss, a).f += 3; remember(world, boss, `${reul(a.name)} ${JOBS[jobKey].name}(으)로 채용했다`, 3, 0.2, [a.id], 'work'); }
    return title;
  }
  remember(world, a, `${co.name}에 지원했지만 떨어졌다`, 3, -0.4, [], 'work');
  feel(a, -6, '실망', co.name);
  return false;
}

/** 회사를 세울 만한 산업 고르기 */
export function bestIndustry(world, a) {
  const era = eraOfAnn(world, a);
  const s = world.settlements.get(a.sid);
  let best = null, bs = -Infinity, best_df = 1;
  const builder = hasConstruction(world, s.id);
  for (const k in INDUSTRIES) {
    const ind = INDUSTRIES[k];
    if (ind.era > era) continue;
    // 건물이 필요한 사업은 지어 줄 건설회사가 있어야 시작할 수 있다 (농장과 건설회사 자신은 예외)
    if (!builder && k !== 'construction' && k !== 'farm') continue;
    const jk = ind.jobs[0];
    let df = demandFactor(world, s, k);
    // 집을 지어 달라는 사람이 많은데 건설회사가 없다 → 큰 기회
    if (k === 'construction' && !builder) df = Math.max(df, 1 + Math.min(0.8, (s.need?.homes || 0) * 0.15));
    if (k === 'farm' && s.food < s.pop * 2) df += 0.3;
    const score = jobFit(a, jk) * 0.6 + a.sk[JOBS[jk].skill] * 0.3 + (df - 1) * 45 + rand() * 12;
    if (score > bs) { bs = score; best = k; best_df = df; }
  }
  return best ? { ind: best, score: bs, df: best_df } : null;
}

export function startupCost(world, indKey, era) {
  const def = BUILDINGS[INDUSTRIES[indKey].building];
  return Math.round((def.cost || 30) * 0.5 + wageBase(era) * 12);
}

export function foundCompany(world, a, indKey) {
  const s = world.settlements.get(a.sid);
  const country = world.countries[a.country];
  const ind = INDUSTRIES[indKey];
  const cost = startupCost(world, indKey, country.era);
  if (a.money < cost) return null;
  const taken = new Set([...world.companies.values()].map(c => c.name));
  const name = companyName(ind, a, a.culture, taken);
  const def = BUILDINGS[ind.building];
  const b = placeBuilding(world, s, ind.building, { era: country.era, owner: a.id, name, budget: def.cost || 0 });
  if (!b) return null;
  a.money -= cost;
  const co = {
    id: world.newId(), name, ind: indKey, pub: false, sid: s.id, country: a.country, founder: a.id, ceo: a.id,
    b: b.id, emp: [], money: cost - (def.cost || 0), founded: Math.round(world.clock), rep: 40,
    slots: 3, wage: wageBase(country.era), rev: 0, revDays: [], lossDays: 0, dead: false,
  };
  b.co = co.id;
  world.companies.set(co.id, co);
  setJob(world, a, ind.jobs[0], co, { ceo: true });
  remember(world, a, `${reul(name)} 창업했다!`, 9, 0.9, [], 'work');
  lifeEvent(world, a, `${name} 창업`, '🏢');
  feel(a, 25, '설렘', name);
  think(world, a, `드디어 내 회사, ${name}. 이제부터가 진짜 시작이다.`);
  const g = a.goals.find(g => g.k === 'company' && !g.done);
  if (g) world.achieveGoal(a, g);
  world.news('biz', `🏢 ${ga(a.name)} ${s.name}에 '${name}'(${ind.name})을(를) 창업했다`, { anns: [a.id], country: a.country, sid: s.id }, 2);
  world.first(country, 'company', `첫 회사 '${name}' 탄생 (${a.name})`);
  if (indKey === 'construction') {
    world.first(country, 'builder', `첫 건설회사 '${name}' — 이제 제대로 된 집을 지을 수 있다`);
    think(world, a, '이제 이 마을 사람들 집은 우리가 짓는다.');
  }
  world.dirtyCompanies.add(co.id);
  return co;
}

export function foundPublic(world, country, s, pubKey, leader) {
  const def = PUBLIC[pubKey];
  const bdef = BUILDINGS[def.building];
  const b = placeBuilding(world, s, def.building, { era: country.era, name: def.name, budget: 0 });
  if (!b) return null;
  const cost = Math.round(wageBase(country.era) * 10 + bdef.work * 0.5);
  country.treasury -= cost;
  b.budget = cost;
  const co = {
    id: world.newId(), name: `${s.name} ${def.name}`, ind: pubKey, pub: true, sid: s.id, country: country.code,
    founder: leader?.id ?? null, ceo: null, b: b.id, emp: [], money: 0, founded: Math.round(world.clock), rep: 50,
    slots: Math.max(2, Math.ceil(s.pop / def.per)), wage: wageBase(country.era) * 1.05, rev: 0, revDays: [], lossDays: 0, dead: false,
  };
  b.co = co.id;
  world.companies.set(co.id, co);
  world.news('gov', `🏛️ ${country.name} 정부가 ${s.name}에 ${reul(def.name)} 세우기 시작했다`, { country: country.code, sid: s.id }, 1);
  world.dirtyCompanies.add(co.id);
  return co;
}

/** 일하는 동안의 생산 (dt 분) */
export function produce(world, a, dt) {
  const j = JOBS[a.job.id];
  if (!j) return;
  const s = world.settlements.get(a.sid);
  const country = world.countries[a.country];
  const era = country.era;
  const hours = dt / 60;
  const skill = a.sk[j.skill];
  const eff = (0.5 + skill / 100) * (0.85 + a.tr.C / 330) * (a.sick > 0 ? 0.5 : 1);
  const prod = ERAS[era].prod;
  const co = a.job.co ? world.companies.get(a.job.co) : null;
  // 기술 숙련
  a.sk[j.skill] = clamp(a.sk[j.skill] + (100 - skill) * 0.0011 * hours * (0.6 + a.ab.int / 120));
  a.job.perf = (a.job.perf || 0) + eff * hours;

  switch (j.out) {
    case 'food': s.food += j.rate * eff * prod * hours; break;
    case 'tech': {
      const pts = j.rate * eff * Math.sqrt(prod) * hours * 0.13 * (0.6 + a.ab.int / 125);
      world.addTech(country, pts, 'job:' + a.job.id);
      a.techMade = (a.techMade || 0) + pts;
      if (chance(0.0012 * hours * j.rate * (a.ab.int / 70) * (0.4 + skill / 100))) world.tryDiscovery(a);
      break;
    }
    case 'build': {
      const site = world.constructionSite(s, a);
      if (site) {
        const work = eff * Math.sqrt(prod) * hours * 1.2;
        const pay = site.budget > 0 ? Math.min(site.budget, (site.budget / Math.max(1, site.work - site.progress)) * work) : 0;
        site.budget -= pay;
        if (co) co.money += pay; else a.money += pay * 0.8;
        if (addWork(world, site, work)) world.onBuilt(site);
      }
      break;
    }
    case 'heal': s.heal = (s.heal || 0) + eff * hours; break;
    case 'teach': s.teach = (s.teach || 0) + eff * hours; break;
    case 'art': country.culturePts = (country.culturePts || 0) + eff * hours; world.addTech(country, eff * hours * 0.15, 'art'); break;
    default: break;
  }
  // 매출 (민간)
  const value = (wageBase(era) / 8) * 1.45 * eff * hours;
  if (co && !co.pub) {
    const df = INDUSTRIES[co.ind] ? demandFactor(world, s, co.ind) : 1;
    const v = value * df;
    co.money += v; co.rev += v;
  } else if (!co) {
    // 자영업: 스스로 번다
    a.money += value * 0.5 * (j.out === 'food' ? 0.8 : 0.7);
  }
}

/** 매일: 급여, 배당, 고용 조정, 파산 */
export function companyDaily(world, co) {
  if (co.dead) return;
  const country = world.countries[co.country];
  const era = country.era;
  co.wage = co.wage * 0.9 + wageBase(era) * (co.pub ? 1.05 : 1) * 0.1;
  const b = world.buildings.get(co.b);
  let payroll = 0;
  const members = co.emp.map(id => world.anns.get(id)).filter(e => e && e.alive);
  co.emp = members.map(e => e.id);
  for (const e of members) {
    const worked = Math.min(1, (e.day.work || 0) / 7);
    if (worked <= 0) continue;
    const j = JOBS[e.job.id];
    const pay = co.wage * (0.75 + e.sk[j.skill] / 120) * (1 + e.job.rank * 0.25) * worked * (e.job.ceo ? 1.2 : 1);
    payroll += pay;
  }
  const fund = co.pub ? country.treasury : co.money;
  const ratio = payroll > 0 ? Math.min(1, Math.max(0, fund) / payroll) : 1;
  for (const e of members) {
    const worked = Math.min(1, (e.day.work || 0) / 7);
    if (worked <= 0) continue;
    const j = JOBS[e.job.id];
    let pay = co.wage * (0.75 + e.sk[j.skill] / 120) * (1 + e.job.rank * 0.25) * worked * (e.job.ceo ? 1.2 : 1) * ratio;
    const tax = era >= 2 ? pay * country.tax : 0;
    country.treasury += tax;
    e.money += pay - tax;
    if (ratio < 0.7) { e.job.sat = clamp(e.job.sat - 6); feel(e, -4, '불만', '월급'); }
  }
  if (co.pub) country.treasury -= payroll * ratio;
  else co.money -= payroll * ratio;

  if (!co.pub) {
    co.revDays.push(Math.round(co.rev));
    if (co.revDays.length > 14) co.revDays.shift();
    const profit = co.rev - payroll;
    co.rev = 0;
    // 대표 배당
    const ceo = co.ceo && world.anns.get(co.ceo);
    const buffer = payroll * 6 + 10;
    if (ceo && co.money > buffer) {
      const div = (co.money - buffer) * 0.35;
      const tax = era >= 2 ? div * country.tax * 1.5 : 0;
      co.money -= div; ceo.money += div - tax; country.treasury += tax;
    }
    co.rep = clamp(co.rep + (profit > 0 ? 0.6 : -0.8));
    if (co.money < 0 || (ratio < 0.5 && payroll > 0)) co.lossDays++;
    else co.lossDays = Math.max(0, co.lossDays - 1);
    // 고용 조정
    const df = INDUSTRIES[co.ind] ? demandFactor(world, world.settlements.get(co.sid), co.ind) : 1;
    const cap = b ? b.cells[2] * b.cells[3] * 6 : 6;
    if (co.money > payroll * 8 + wageBase(era) * 3 && df > 0.95 && co.slots < cap) co.slots++;
    if ((co.lossDays > 3 || df < 0.5) && co.slots > 1) {
      co.slots--;
      if (co.emp.length > co.slots) {
        const victim = members.filter(e => !e.job.ceo).sort((x, y) => x.job.since - y.job.since).pop();
        if (victim) {
          leaveJob(world, victim, '해고');
          feel(victim, -15, '좌절', co.name);
          think(world, victim, `${co.name}에서 잘렸다… 내가 부족했던 걸까.`);
        }
      }
    }
    if (co.lossDays > 12) closeCompany(world, co, '파산');
  } else {
    const s = world.settlements.get(co.sid);
    const def = PUBLIC[co.ind];
    co.slots = Math.max(2, Math.min(b ? b.cells[2] * b.cells[3] * 8 : 8, Math.ceil((s?.pop || 1) / def.per) + 1));
    if (country.treasury < 0 && co.slots > 1) co.slots--;
  }
  world.dirtyCompanies.add(co.id);
}

/** 승진 판단 (매일) */
export function considerPromotion(world, a) {
  if (!a.job || a.job.co == null || a.job.ceo) return;
  const days = (world.clock - a.job.since) / 1440;
  const j = JOBS[a.job.id];
  const need = [0, 45, 65, 82][a.job.rank + 1];
  if (need == null) return;
  if (a.sk[j.skill] >= need && days > 20 * (a.job.rank + 1) && chance(0.04 + a.tr.C / 1500)) {
    a.job.rank++;
    const co = world.companies.get(a.job.co);
    remember(world, a, `${co?.name || ''} ${RANKS[a.job.rank]}(으)로 승진했다`, 7, 0.8, [], 'work');
    lifeEvent(world, a, `${RANKS[a.job.rank]} 승진`, '⭐');
    feel(a, 18, '뿌듯함', '승진');
    think(world, a, '노력이 인정받았다. 더 잘해 봐야지.');
    world.dirtyAnnMeta.add(a.id);
  }
}

export { DISCOVERIES };
