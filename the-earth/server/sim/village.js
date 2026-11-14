// 처음 마을: 나라의 첫 정착지에는 관리자(마을)의 앤 수십 명이 초가집에 모여 산다.
//   · 원시 시대에서 시작한다. 도로도 광장도 없다.
//   · 며칠 뒤 누군가 불을 발견하면 모닥불을 피우고 모여 앉는다.
//   · 열흘쯤 뒤 누군가 농사를 알아내면 농경 시대가 열리고 논밭을 일군다.
//   · 플레이어의 앤은 이 마을 가정의 아기로 태어나고, 자라서 꾸린 가정에 다음 앤들이 태어난다.
import { clamp, rand, pick, chance, gauss, ageOf, DAY, YEAR } from './util.js';
import { makeAnn, lifeEvent, remember } from './ann.js';
import { rel, updateKind } from './social.js';
import { placeBuilding, plazaPoint } from './settlement.js';
import { randomName, childName } from './names.js';
import { wageBase, startupCost, foundCompany } from './economy.js';
import { ERAS } from './data.js';

export const VILLAGE = { couples: 14, singles: 5, elders: 3 };

/** 마을을 채운다 (이미 채웠으면 아무것도 하지 않는다) */
export function seedVillage(world, c, s, plan = VILLAGE) {
  if (c.seeded) return [];
  const now = Math.round(world.clock);
  const made = [];
  const add = spec => {
    const [x, z] = plazaPoint(s, 30);
    const a = makeAnn(world, { culture: c.culture, owner: null, country: c.code, sid: s.id, x, z, money: wageBase(c.era) * (3 + rand() * 4), ...spec });
    world.anns.set(a.id, a);
    made.push(a);
    return a;
  };
  const house = (owner, people) => {
    const b = placeBuilding(world, s, 'cottage', { era: 1, owner: owner?.id ?? null, done: true });
    if (!b) return null;
    for (const p of people) world.moveHome(p, b);
    return b;
  };
  // 부부와 아이들
  for (let i = 0; i < plan.couples; i++) {
    const ageM = 21 + Math.floor(rand() * 24), ageF = clamp(ageM - 2 + Math.round(gauss() * 3), 19, 46);
    const m = add({ name: randomName(c.culture, 'M'), gender: 'M', age: ageM, orient: 'straight' });
    const f = add({ name: randomName(c.culture, 'F'), gender: 'F', age: ageF, orient: 'straight' });
    m.spouse = m.partner = f.id; f.spouse = f.partner = m.id;
    const years = Math.max(0.5, Math.min(ageM, ageF) - 18 - rand() * 3);
    m.wedAt = f.wedAt = Math.round(now - years * YEAR); m.annivN = f.annivN = Math.floor(years);
    for (const [x, y] of [[m, f], [f, m]]) {
      const r = rel(x, y);
      r.f = 60 + rand() * 30; r.a = 45 + rand() * 40; r.t = 60 + rand() * 30; r.r = 40 + rand() * 40; r.n = 50; r.since = m.wedAt;
    }
    updateKind(world, m, f); updateKind(world, f, m);
    // 아이: 결혼한 햇수만큼 0~3명
    const kids = [];
    const nk = Math.min(3, Math.floor(rand() * (1 + years / 4)));
    for (let k = 0; k < nk; k++) {
      const age = Math.max(0.2, years - 1 - k * (1.5 + rand() * 2));
      if (age < 0.2) break;
      const g = chance(0.51) ? 'M' : 'F';
      const kid = add({ name: childName(c.culture, g, m.name, f.name), gender: g, age, parents: [f.id, m.id], gen: 2, money: 0, edu: 0 });
      f.kids.push(kid.id); m.kids.push(kid.id);
      for (const p of [m, f]) { const r = rel(p, kid); r.a = 80; r.f = 50; r.t = 85; r.k = 'child'; const rk = rel(kid, p); rk.a = 70; rk.f = 50; rk.t = 85; rk.k = 'parent'; }
      kids.push(kid);
    }
    for (const x of kids) for (const y of kids) if (x !== y) { const r = rel(x, y); r.a = 45; r.f = 40; r.k = 'sibling'; }
    if (kids.length) f.lastBirth = Math.round(kids[kids.length - 1].born);
    house(m, [m, f, ...kids]);
  }
  // 혼자 사는 젊은이들 (두셋이 한 집에)
  const singles = [];
  for (let i = 0; i < plan.singles; i++) {
    const g = i % 2 ? 'F' : 'M';
    singles.push(add({ name: randomName(c.culture, g), gender: g, age: 18 + Math.floor(rand() * 10) }));
  }
  for (let i = 0; i < singles.length; i += 3) house(singles[i], singles.slice(i, i + 3));
  // 어르신들
  for (let i = 0; i < plan.elders; i++) {
    const g = chance(0.5) ? 'M' : 'F';
    const e = add({ name: randomName(c.culture, g), gender: g, age: 56 + Math.floor(rand() * 16) });
    e.rep = 20 + rand() * 20;
    house(e, [e]);
  }
  // 한 마을 사람들: 모두 얼굴은 알고, 몇몇은 친구
  for (const x of made) for (const y of made) {
    if (x === y || x.rels[y.id]?.k === 'spouse' || x.rels[y.id]?.k === 'parent' || x.rels[y.id]?.k === 'child' || x.rels[y.id]?.k === 'sibling') continue;
    if (x.rels[y.id]) continue;
    const kidX = ageOf(x, now) < 13, kidY = ageOf(y, now) < 13;
    const r = rel(x, y), rb = rel(y, x);
    const f = kidX === kidY ? 8 + rand() * 30 : 4 + rand() * 10;
    const a = 10 + gauss() * 18 + (chance(0.15) ? 30 : 0);
    r.f = rb.f = f; r.a = clamp(a, -60, 90); rb.a = clamp(a + gauss() * 8, -60, 90); r.n = rb.n = Math.round(f);
    updateKind(world, x, y); updateKind(world, y, x);
  }
  for (const a of made) {
    const adult = ageOf(a, now) >= 18;
    lifeEvent(world, a, adult ? `${s.name}에서 나고 자랐다` : `${s.name}의 초가집에서 태어났다`, '🏡');
    remember(world, a, `${s.name}은(는) 우리 마을이다`, 8, 0.6, [], 'life');
    world.dirtyAnnMeta.add(a.id);
  }
  s.food = Math.max(s.food, made.length * 8);
  c.seeded = { at: now, n: made.length, fireAt: Math.round(now + (2 + rand()) * DAY), farmAt: Math.round(now + (10 + rand() * 3) * DAY) };
  c.pop = [...world.anns.values()].filter(a => a.alive && a.country === c.code).length;
  s.pop = made.length;
  world.dirtySettlements.add(s.id); world.dirtyCountries.add(c.code);
  world.news('city', `🏡 ${c.name} ${s.name} — 초가집 ${s.buildings.length}채에 ${made.length}명이 모여 사는 원시 마을`, { country: c.code, sid: s.id }, 2);
  world.rebuildResidents();
  return made;
}

/** 하루에 한 번: 처음 마을의 큰 걸음 (불 → 농사) */
export function villageDaily(world, c) {
  const v = c.seeded;
  if (!v || v.done) return;
  const now = world.clock;
  const pickFinder = () => {
    const s = world.settlements.get(c.capital);
    const people = (s ? world.residents(s.id) : []).filter(a => ageOf(a, now) >= 16);
    return people.sort((x, y) => (y.ab.int + y.tr.O + rand() * 30) - (x.ab.int + x.tr.O + rand() * 30))[0] || null;
  };
  if (!c.discoveries.includes('불의 사용') && now >= v.fireAt) {
    const a = pickFinder();
    if (a) world.tryDiscovery(a, '불의 사용');
  }
  if (c.era === 0 && !c.discoveries.includes('농업') && now >= v.farmAt) {
    const a = pickFinder();
    if (a) {
      world.tryDiscovery(a, '농업');
      // 농사를 알게 되면 농경 시대가 열린다
      c.tech = Math.max(c.tech, ERAS[1].tech);
      world.checkEra(c);
      v.paddyAt = Math.round(now + (1 + rand()) * DAY);
    }
  }
  // 농사를 안 지 하루이틀 뒤: 마을 사람들이 곡식을 모아 첫 논밭을 일군다
  if (c.era >= 1 && v.paddyAt && !v.paddy && now >= v.paddyAt) {
    v.paddy = true;
    const s = world.settlements.get(c.capital);
    const hasFarm = s && s.buildings.some(id => world.buildings.get(id)?.type === 'farm');
    const a = !hasFarm && pickFinder();
    if (a) {
      const cost = startupCost(world, 'farm', c.era);
      if (a.money < cost) {
        const give = cost - a.money;
        a.money += give;
        remember(world, a, '마을 사람들이 곡식과 씨앗을 모아 논밭을 일굴 밑천을 대 주었다', 7, 0.8, [], 'work');
      }
      const co = foundCompany(world, a, 'farm');
      if (co) world.news('town', `🌾 ${s.name}에 첫 논밭이 생겼다 — ${a.name}와(과) 마을 사람들이 땅을 갈기 시작했다`, { anns: [a.id], country: c.code, sid: s.id }, 2);
    }
  }
  if (c.era >= 1 && c.discoveries.includes('불의 사용') && v.paddy) v.done = true;
}

/** 저장된 세계를 불러올 때: 아직 원시 시대인 작은 첫 마을에도 마을 사람들을 채우고, 원시 시대 도로는 걷어 낸다 */
export function migrateStart(world) {
  for (const c of Object.values(world.countries)) {
    if (c.era !== 0) continue;
    for (const s of world.settlements.values()) if (s.country === c.code && s.roads && Object.keys(s.roads).length) { s.roads = {}; world.dirtySettlements.add(s.id); }
    if (c.seeded) continue;
    const s = world.settlements.get(c.capital);
    if (!s) continue;
    const pop = [...world.anns.values()].filter(a => a.alive && a.sid === s.id).length;
    if (pop < 15) seedVillage(world, c, s);
  }
}
