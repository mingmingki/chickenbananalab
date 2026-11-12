// 경제: 회사·공공기관·일자리·임금·생산·세금
import { INDUSTRIES, PUBLIC, JOBS, ERAS, BUILDINGS, RANKS, DISCOVERIES, EDU_NAMES, maxEduForEra, deptName } from './data.js';
import { clamp, rand, pick, chance, ageOf, YEAR, ga, neun, reul, wa, ro } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { placeBuilding, addWork, siteFeatures, buildRule } from './settlement.js';
import { companyName } from './names.js';
import { rel } from './social.js';


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
  const site = a.job.site && world.buildings.get(a.job.site);
  return (co ? `${co.name} ` : '') + (a.job.dept ? deptName(a.job.dept) + ' ' : '') + (j?.name || '일') + rank + (site && !site.done ? ` · ${site.name} 현장` : '');
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

/** 회사가 쓰는 건물들의 정원 합 (식당 15명, 공장 150명, 제철소 600명…) */
export function capacityOf(world, co) {
  let cap = 0;
  for (const bid of [co.b, ...(co.bx || [])]) { const b = world.buildings.get(bid); if (b) cap += BUILDINGS[b.type]?.jobs || 6; }
  return Math.max(2, cap);
}
/** 지형 조건: 바다·물가·산·숲·경치 */
export function featureOk(s, need) {
  if (!need) return true;
  const f = siteFeatures(s);
  if (need === 'sea') return !!f.sea;
  if (need === 'water') return !!(f.sea || f.river || f.lake);
  if (need === 'mountain') return !!(f.mountain || (s.site?.mtn || 0) > 260);
  if (need === 'forest') return f.biome !== 'desert';
  if (need === 'scenic') return !!(f.sea || f.lake || f.mountain);
  return true;
}
export function countryHas(world, code, ind) {
  for (const co of world.companies.values()) if (!co.dead && co.country === code && co.ind === ind) return true;
  return false;
}
export function siteName(world, b) {
  const s = world.settlements.get(b.sid);
  return `${s?.name || ''} ${b.name}${BUILDINGS[b.type]?.kind === 'home' ? ' 신축' : ' 공사'}`;
}
/** 시공사 정하기: 그 마을 건설회사 중 일이 가장 덜 밀린 곳 */
export function assignContractor(world, b, prefer = null) {
  if (b.contractor || b.done) return !!b.contractor;
  const cos = [...world.companies.values()].filter(c => !c.dead && c.ind === 'construction' && c.sid === b.sid);
  if (!cos.length) return false;
  const load = c => (c.sites || []).reduce((t, id) => { const x = world.buildings.get(id); return t + (x && !x.done ? x.work - x.progress : 0); }, 0) / Math.max(1, c.emp.length);
  const co = (prefer && cos.find(c => c.id === prefer)) || cos.sort((x, y) => load(x) - load(y) || y.rep - x.rep)[0];
  b.contractor = co.id;
  co.sites = [...(co.sites || []).filter(id => world.buildings.get(id) && !world.buildings.get(id).done), b.id];
  if (b.work >= (BUILDINGS[b.type]?.kind === 'home' ? 20000 : 3000)) world.news('build', `🏗️ ${co.name}이(가) '${siteName(world, b)}'을(를) 맡아 착공했다`, { sid: b.sid, country: co.country }, 1);
  world.dirtyBuildings.add(b.id);
  return true;
}
/** 건설회사: 직원들을 현장에 배치한다. 한 번 배치되면 준공까지 그 현장으로 출근한다 */
export function crewDaily(world, co) {
  co.sites = (co.sites || []).filter(id => { const b = world.buildings.get(id); return b && !b.done; });
  const crew = co.emp.map(id => world.anns.get(id)).filter(e => e && e.alive && e.job?.co === co.id);
  const count = new Map(co.sites.map(id => [id, 0]));
  for (const e of crew) { if (e.job.site && count.has(e.job.site)) count.set(e.job.site, count.get(e.job.site) + 1); else e.job.site = null; }
  for (const e of crew) {
    if (e.job.site || !co.sites.length) continue;
    let best = null, bs = -1;
    for (const id of co.sites) { const b = world.buildings.get(id); const r = (b.work - b.progress) / (count.get(id) + 1); if (r > bs) { bs = r; best = b; } }
    e.job.site = best.id;
    count.set(best.id, count.get(best.id) + 1);
    if (best.work >= 800) {
      lifeEvent(world, e, `${co.name} '${siteName(world, best)}' 현장에 배치되었다`, '🦺');
      remember(world, e, `'${siteName(world, best)}' 현장으로 발령이 났다`, 4, 0.2, [], 'work');
    }
    world.dirtyAnnMeta.add(e.id);
  }
  // 필요한 인원: 남은 일이 많을수록 사람을 더 뽑는다
  co.crewNeed = co.sites.reduce((t, id) => { const b = world.buildings.get(id); return t + Math.ceil((b.work - b.progress) / 2200); }, 0);
}

export function openings(world, a, withBlocked = false) {
  const s = world.settlements.get(a.sid);
  const era = eraOfAnn(world, a);
  const out = [], blocked = [];
  const lv = a.eduLv || 0;
  const water = (() => { const f = siteFeatures(s); return f.sea || f.river || f.lake; })();
  for (const co of companiesIn(world, a.sid)) {
    if (co.emp.length >= co.slots) continue;
    const hq = world.buildings.get(co.b);
    if (hq && !hq.done && co.ind !== 'construction') continue; // 건물이 다 지어져야 직원을 뽑는다
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
  // 대표는 망한 업종을 한동안 다시 하지 않는다
  const boss = co.ceo != null && world.anns.get(co.ceo);
  if (boss && why === '파산') boss.bust = { ind: co.ind, t: Math.round(world.clock) };
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
  world.news('biz', `📉 ${co.name}이(가) ${ro(why)} 문을 닫았다`, { country: co.country, sid: co.sid }, 1);
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
    if (a.bust && a.bust.ind === k && world.clock - a.bust.t < YEAR * 2) continue; // 한 번 망해 본 업종
    // 가게·대장간처럼 고대 이후의 사업 건물은 건설회사가 지어 줘야 한다 (농장·공방·벽돌 가마·건설회사는 손수)
    if (!builder && (BUILDINGS[ind.building]?.era ?? 0) >= 2) continue;
    if (!featureOk(s, ind.needs)) continue;                       // 바다가 없으면 조선소도 없다
    if (ind.requires && !countryHas(world, a.country, ind.requires)) continue; // 제철소가 있어야 조선·자동차
    if (k === 'construction' && s.pop < 15) continue; // 작은 마을은 품앗이로 짓는다
    if (k === 'construction' && builder) {
      // 건설회사는 현장이 넘쳐 기존 회사들이 감당을 못할 때만 새로 생긴다 (작은 마을에 1인 건설사가 난립하지 않게)
      const cons = companiesIn(world, s.id).filter(c => c.ind === 'construction');
      const crew = cons.reduce((t, c) => t + c.emp.length, 0), need = cons.reduce((t, c) => t + (c.crewNeed || 0), 0);
      if (cons.length >= 1 + Math.floor(s.pop / 150) || need < crew * 1.5 + 4) continue;
    }
    // 같은 업종 회사가 사람을 뽑고 있으면 차리지 않고 그리로 들어간다
    if (companiesIn(world, s.id).some(c => c.ind === k && c.slots > c.emp.length)) continue;
    const jk = ind.jobs[0];
    let df = demandFactor(world, s, k);
    // 집을 지어 달라는 사람이 많은데 건설회사가 없다 → 큰 기회
    if (k === 'construction' && !builder) df = Math.max(df, 1 + Math.min(0.8, ((s.need?.builder || 0) + (s.needPrev?.builder || 0)) * 0.1 + (era >= 4 ? (s.needPrev?.homes || 0) * 0.15 : 0)));
    // 벽돌이 없어 공사가 멈춘 곳이 있다 → 벽돌 가마를 차릴 기회
    if (k === 'brickworks') { const kilns = companiesIn(world, s.id).filter(c => c.ind === 'brickworks').length; df = kilns ? df : Math.max(df, 1 + Math.min(0.9, ((s.need?.bricks || 0) + (s.needPrev?.bricks || 0)) * 0.08 + (era >= 2 ? 0.25 : 0)) * Math.min(1, s.pop / 25)); }
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
  if (!buildRule(b).self) assignContractor(world, b);
  remember(world, a, `${reul(name)} 창업했다!`, 9, 0.9, [], 'work');
  lifeEvent(world, a, `${name} 창업`, '🏢');
  feel(a, 25, '설렘', name);
  think(world, a, `드디어 내 회사, ${name}. 이제부터가 진짜 시작이다.`);
  const g = a.goals.find(g => g.k === 'company' && !g.done);
  if (g) world.achieveGoal(a, g);
  world.news('biz', `🏢 ${ga(a.name)} ${s.name}에 '${name}'(${ind.name})을(를) 창업했다`, { anns: [a.id], country: a.country, sid: s.id }, 2);
  world.first(country, 'company', `첫 회사 '${name}' 탄생 (${a.name})`);
  if (indKey === 'brickworks') world.first(country, 'kiln', `첫 벽돌 가마 '${name}' — 이제 기와집과 벽돌집을 지을 수 있다`, '🧱');
  if (indKey === 'construction') {
    world.first(country, 'builder', `첫 건설회사 '${name}' — 이제 관청·시장 같은 큰 건물을 지을 수 있다`);
    think(world, a, '이제 이 마을 사람들 집은 우리가 짓는다.');
  }
  world.dirtyCompanies.add(co.id);
  return co;
}

export function foundPublic(world, country, s, pubKey, leader) {
  const def = PUBLIC[pubKey];
  const bdef = BUILDINGS[def.building];
  const nm = country.era >= 4 && def.modern ? def.modern : def.name;
  const b = placeBuilding(world, s, def.building, { era: country.era, name: nm, budget: 0 });
  if (!b) return null;
  const cost = Math.round(wageBase(country.era) * 6 + bdef.work * wageBase(country.era) / 8 * 0.25);
  country.treasury -= cost;
  b.budget = cost;
  assignContractor(world, b);
  const co = {
    id: world.newId(), name: `${s.name} ${nm}`, ind: pubKey, pub: true, sid: s.id, country: country.code,
    founder: leader?.id ?? null, ceo: null, b: b.id, emp: [], money: 0, founded: Math.round(world.clock), rep: 50,
    slots: Math.max(2, Math.ceil(s.pop / def.per)), wage: wageBase(country.era) * 1.05, rev: 0, revDays: [], lossDays: 0, dead: false,
  };
  b.co = co.id;
  world.companies.set(co.id, co);
  world.news('gov', `🏛️ ${country.name} 정부가 ${s.name}에 ${reul(nm)} 세우기로 했다`, { country: country.code, sid: s.id }, 1);
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
      let site = a.job.site ? world.buildings.get(a.job.site) : null;
      if (site && site.done) site = null;
      if (!site && !co) site = world.constructionSite(s, a); // 일용직은 아무 현장이나
      a.onSite = !!site;
      if (site) {
        const work = eff * (1 + era * 0.12) * hours;
        const pay = site.budget > 0 ? Math.min(site.budget, (site.budget / Math.max(1, site.work - site.progress)) * work) : 0;
        site.budget -= pay;
        if (co) co.money += pay; else a.money += pay * 0.8;
        if (addWork(world, site, work)) world.onBuilt(site);
      }
      break;
    }
    case 'bricks': {
      const water = (() => { const f = siteFeatures(s); return f.river || f.lake || f.sea; })();
      const cap = 300 + s.pop * 25;
      if ((s.bricks || 0) < cap) s.bricks = (s.bricks || 0) + j.rate * eff * Math.sqrt(prod) * hours * (water ? 1 : 0.6);
      break;
    }
    case 'ore': country.ore = (country.ore || 0) + j.rate * eff * hours; break;
    case 'steel': {
      const use = Math.min(country.ore || 0, hours * 1.2);
      country.ore = (country.ore || 0) - use;
      country.steel = (country.steel || 0) + j.rate * eff * hours * (0.4 + 0.6 * use / (hours * 1.2 || 1));
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
    let df = INDUSTRIES[co.ind] ? demandFactor(world, s, co.ind) : 1;
    // 건설회사는 일감(현장)이 있어야 번다
    if (co.ind === 'construction') df = a.onSite ? 1.05 : 0.6; // 일감이 없을 땐 수리·보수
    // 조선·중공업·자동차는 철이 있어야 제대로 돌아간다
    if (['shipbuilding', 'machinery', 'auto'].includes(co.ind)) {
      if ((country.steel || 0) >= hours * 0.6) { country.steel -= hours * 0.6; df *= 1.25; } else df *= 0.55;
    }
    const v = value * df;
    co.money += v; co.rev += v;
  } else if (!co) {
    // 자영업: 스스로 벌고, 나라에 세금(조세)을 낸다
    const earn = value * 0.5 * (j.out === 'food' ? 0.8 : 0.7);
    const tax = era >= 2 ? earn * country.tax * 0.7 : 0;
    a.money += earn - tax; country.treasury += tax; country.taxIn = (country.taxIn || 0) + tax;
  }
}

/** 매일: 급여, 배당, 고용 조정, 파산 */
export function companyDaily(world, co) {
  if (co.dead) return;
  const country = world.countries[co.country];
  const era = country.era;
  // 일손이 모자라면 임금을 올린다 (낼 형편이 될 때). 사람이 차면 서서히 시세로 돌아간다
  const vacant = co.slots > co.emp.length;
  const canPay = co.pub ? !country.pending && (country.pubAvg || 0) < (country.taxAvg || 0) * 0.65 : (co.pl || 0) >= 0 && co.money > wageBase(era) * 10;
  co.prem = Math.max(0, Math.min(0.6, (co.prem || 0) + (vacant && canPay ? 0.01 : -0.004)));
  co.wage = co.wage * 0.9 + wageBase(era) * (co.pub ? 1.05 : 1) * (1 + co.prem) * 0.1;
  const b = world.buildings.get(co.b);
  // 창업 초기(사옥·가마를 짓는 중)에는 대표가 월급 없이 일한다
  const startup = !co.pub && b && !b.done;
  let payroll = 0;
  const members = co.emp.map(id => world.anns.get(id)).filter(e => e && e.alive);
  co.emp = members.map(e => e.id);
  for (const e of members) {
    const worked = Math.min(1.2, (e.payHours || 0) / 7);
    if (worked <= 0 || (startup && e.job.ceo)) continue;
    const j = JOBS[e.job.id];
    const pay = co.wage * (0.75 + e.sk[j.skill] / 120) * (1 + e.job.rank * 0.25) * worked * (e.job.ceo ? 1.2 : 1);
    payroll += pay;
  }
  const fund = co.pub ? country.treasury : co.money;
  const ratio = payroll > 0 ? Math.min(1, Math.max(0, fund) / payroll) : 1;
  for (const e of members) {
    const worked = Math.min(1.2, (e.payHours || 0) / 7);
    if (worked <= 0 || (startup && e.job.ceo)) continue;
    const j = JOBS[e.job.id];
    let pay = co.wage * (0.75 + e.sk[j.skill] / 120) * (1 + e.job.rank * 0.25) * worked * (e.job.ceo ? 1.2 : 1) * ratio;
    const tax = era >= 2 ? pay * country.tax : 0;
    country.treasury += tax; country.taxIn = (country.taxIn || 0) + tax;
    e.money += pay - tax;
    if (ratio < 0.7) { e.job.sat = clamp(e.job.sat - 6); feel(e, -4, '불만', '월급'); }
  }
  if (co.pub) { country.treasury -= payroll * ratio; country.pubOut = (country.pubOut || 0) + payroll * ratio; }
  else co.money -= payroll * ratio;
  for (const e of members) e.payHours = 0;

  if (!co.pub) {
    co.revDays.push(Math.round(co.rev));
    if (co.revDays.length > 14) co.revDays.shift();
    const profit = co.rev - payroll;
    co.pl = (co.pl || 0) * 0.92 + profit * 0.08; // 최근 손익 흐름
    co.rev = 0;
    // 대표 배당
    const ceo = co.ceo && world.anns.get(co.ceo);
    const buffer = payroll * 12 + wageBase(era) * 4; // 회사에 남겨 두는 돈 (키우려면 쌓아야 한다)
    if (ceo && co.money > buffer) {
      const div = (co.money - buffer) * 0.35;
      const tax = era >= 2 ? div * country.tax * 1.5 : 0;
      co.money -= div; ceo.money += div - tax; country.treasury += tax; country.taxIn = (country.taxIn || 0) + tax;
    }
    co.rep = clamp(co.rep + (profit > 0 ? 0.6 : -0.8));
    if (startup && co.money >= 0) co.lossDays = 0;
    else if (co.money < 0 || (ratio < 0.5 && payroll > 0)) co.lossDays++;
    else co.lossDays = Math.max(0, co.lossDays - 1);
    // 고용 조정: 장사가 잘되면 건물 정원까지 늘리고, 꽉 차면 건물을 하나 더 짓는다
    let df = INDUSTRIES[co.ind] ? demandFactor(world, world.settlements.get(co.sid), co.ind) : 1;
    const cap = capacityOf(world, co);
    if (co.ind === 'construction') {
      crewDaily(world, co);
      // 현장이 없어도 기본 인력(반장·기능공 몇 명)은 유지한다
      const base = Math.min(cap, 3);
      const want = Math.max(base, Math.min(cap, 1 + co.crewNeed));
      if (want > co.slots && co.money > payroll * 2) co.slots = Math.min(want, co.slots + Math.max(1, Math.floor(co.slots * 0.2)));
      df = co.sites.length || co.slots <= base ? 1 : 0.45;
    } else if (co.money > payroll * 3 + wageBase(era) * 2 && df > 0.95 && co.pl >= 0 && co.slots < cap) co.slots = Math.min(cap, co.slots + Math.max(1, Math.floor(co.slots * 0.08)));
    if (b && co.slots >= cap && co.emp.length >= cap * 0.9 && df > 1 && co.money > (BUILDINGS[b?.type]?.cost || 100) * 3 && chance(0.03)) {
      const s2 = world.settlements.get(co.sid);
      const nb = placeBuilding(world, s2, b.type, { era, name: co.name + ' 2관', budget: BUILDINGS[b.type].cost || 0, co: co.id });
      if (nb) {
        co.money -= BUILDINGS[b.type].cost || 0;
        co.bx = [...(co.bx || []), nb.id];
        if (!buildRule(nb).self) assignContractor(world, nb);
        world.news('biz', `📈 ${co.name}이(가) 사업을 넓힌다 — 새 ${BUILDINGS[b.type].name} 착공 (직원 ${co.emp.length}명)`, { sid: co.sid, country: co.country }, 1);
      }
    }
    if ((co.lossDays > 3 || df < 0.5 || (co.pl < -wageBase(era) * 0.5 && co.money < payroll * 6)) && co.slots > 1) {
      // 적자면 먼저 채용을 멈추고, 그래도 안 되면 한 명씩 줄인다
      if (co.slots > co.emp.length) co.slots = Math.max(1, co.emp.length);
      else co.slots--;
      if (co.emp.length > co.slots) {
        const victim = members.filter(e => !e.job.ceo).sort((x, y) => x.job.since - y.job.since).pop();
        if (victim) {
          leaveJob(world, victim, '해고');
          feel(victim, -15, '좌절', co.name);
          think(world, victim, `${co.name}에서 잘렸다… 내가 부족했던 걸까.`);
        }
      }
    }
    if (co.lossDays > (co.ind === 'construction' ? 30 : 12)) closeCompany(world, co, '파산');
  } else {
    const s = world.settlements.get(co.sid);
    const def = PUBLIC[co.ind];
    let need = Math.ceil((s?.pop || 1) / def.per) + 1;
    if (def.school) need = Math.ceil([...world.anns.values()].filter(x => x.alive && x.school?.org === co.id).length / 18) + 1;
    // 공공기관은 나라 살림이 감당할 만큼만 뽑는다
    const target = Math.max(1, Math.min(capacityOf(world, co), need));
    // 공무원 인건비는 세수의 65%까지 (나머지는 학교·관청을 지을 돈으로 모은다). 학교를 지을 돈을 모으는 동안은 채용 동결
    const afford = !country.pending && ((country.pubAvg || 0) + co.wage < (country.taxAvg || 0) * 0.65 || country.treasury > payroll * 90 + wageBase(era) * 60);
    if (target < co.slots) co.slots = target;
    else if (target > co.slots && afford) co.slots++;
    if (country.treasury < payroll * 6 && co.slots > 1) co.slots = Math.max(1, Math.min(co.slots, co.emp.length) - (country.treasury < 0 ? 1 : 0));
    // 인건비가 세수를 넘으면 구조조정: 학교·관청이 아닌 곳부터 천천히 줄인다
    const over = (country.pubAvg || 0) > (country.taxAvg || 0) * 0.8 && country.treasury < payroll * 30;
    const essential = co.ind === 'hall' || def.school;
    if (over && co.slots > 1 && (!essential || country.treasury <= 0) && chance(essential ? 0.04 : 0.1)) {
      co.slots = Math.max(1, Math.min(co.slots, co.emp.length) - 1);
      if (co.emp.length > co.slots) {
        const victim = members.sort((x, y) => x.job.since - y.job.since).pop();
        if (victim) {
          leaveJob(world, victim, '구조조정');
          feel(victim, -12, '좌절', co.name);
          think(world, victim, `나라 살림이 어렵다며 ${co.name}에서 자리가 없어졌다…`);
        }
      }
    }
    // 공채·임용: 자리가 비어 있으면 마을에서 알맞은 사람(무직·자영업)을 찾아 부른다
    if (co.emp.length < co.slots && (!b || b.done) && chance(co.emp.length ? 0.12 : 0.5)) {
      const jk = def.jobs.find(k => { const j = JOBS[k]; return j.era <= era && !(j.maxEra != null && era > j.maxEra); });
      const j = jk && JOBS[jk];
      const pool = j ? world.residents(co.sid).filter(x => {
        const g = ageOf(x, world.clock);
        return g >= 19 && g < 62 && !x.school && !x.travel && (!x.job || x.job.co == null) && (x.eduLv || 0) >= eduReq(world, x, jk);
      }) : [];
      pool.sort((x, y) => (jobFit(y, jk) + y.sk[j.skill]) - (jobFit(x, jk) + x.sk[j.skill]));
      const cand = pool[0];
      if (cand && (!cand.job || chance(0.6))) {
        setJob(world, cand, jk, co);
        remember(world, cand, `${co.name}에 ${j.name}(으)로 임용되었다`, 6, 0.7, [], 'work');
        lifeEvent(world, cand, `${co.name} ${j.name} 임용`, '🎖️');
        feel(cand, 12, '뿌듯함', co.name);
      }
    }
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
