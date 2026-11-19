// 처음 마을: 땅의 부족마다 관리자(마을)의 앤 스무 명 남짓이 움집에 모여 산다 (부족은 tribes.js).
//   · 원시 시대에서 시작한다. 도로도 광장도 없고, 불도 농사도 아직 모른다.
//   · 족장이 똑똑할수록 불·돌도끼·나무 다루기·농사·흙벽 쌓기를 빨리 알아낸다 (tribes.js).
//   · 농사를 알면 마을 사람들이 함께 논밭을 일구고, 흙벽 쌓는 법을 알면 초가집을 짓는다.
//   · 플레이어의 앤은 이 마을 가정의 아기로 태어나고, 자라서 꾸린 가정에 다음 앤들이 태어난다.
import { clamp, rand, pick, chance, gauss, ageOf, DAY, YEAR } from './util.js';
import { makeAnn, lifeEvent, remember } from './ann.js';
import { rel, updateKind } from './social.js';
import { placeBuilding, plazaPoint } from './settlement.js';
import { randomName, childName } from './names.js';
import { wageBase, setJob } from './economy.js';
import { knows } from './tribes.js';
import { ERAS } from './data.js';

export const VILLAGE = { couples: 14, singles: 5, elders: 3 };

/** 마을을 채운다 (이미 채웠으면 아무것도 하지 않는다) */
export function seedVillage(world, c, s, plan = VILLAGE) {
  if (s.seeded) return [];
  const mud = c.era >= 2 || knows(world, s, '흙벽 쌓기');   // 흙벽 쌓는 법을 아는 부족은 초가집, 아니면 움집
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
    const b = placeBuilding(world, s, mud ? 'cottage' : 'hut', { era: c.era, owner: owner?.id ?? null, done: true });
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
    lifeEvent(world, a, adult ? `${s.name}에서 나고 자랐다` : `${s.name}의 ${mud ? '초가집' : '움집'}에서 태어났다`, '🏡');
    remember(world, a, `${s.name}은(는) 우리 마을이다`, 8, 0.6, [], 'life');
    world.dirtyAnnMeta.add(a.id);
  }
  s.food = Math.max(s.food, made.length * 8);
  s.seeded = true;
  c.seeded = c.seeded || { at: now, n: 0 };
  c.seeded.n += made.length;
  c.pop = [...world.anns.values()].filter(a => a.alive && a.country === c.code).length;
  s.pop = made.length;
  world.dirtySettlements.add(s.id); world.dirtyCountries.add(c.code);
  world.news('city', `🏡 ${s.name} — ${mud ? '초가집' : '움집'} ${s.buildings.length}채에 ${made.length}명이 모여 사는 ${s.name} 부족`, { country: c.code, sid: s.id }, 1);
  world.rebuildResidents();
  return made;
}

/** 하루에 한 번: 농사를 아는 마을은 함께 논밭을 일군다 (농경 시대엔 농장 회사가 아니라 마을 공동 논밭) */
export function villageDaily(world, c) {
  if (c.era >= 2) return;
  for (const s of world.settlements.values()) {
    if (s.country !== c.code || s.abandoned || !knows(world, s, '농업')) continue;
    // 농사를 알면 채집꾼·사냥꾼 가운데 몇이 하나둘 논밭으로 옮겨 간다
    if (c.era >= 1) {
      const people = world.residents(s.id);
      for (const jk of ['gatherer', 'hunter']) {
        const doing = people.filter(a => a.job?.id === jk && !a.owner);
        const cap = Math.ceil(Math.max(1, s.pop) / (jk === 'gatherer' ? 8 : 7));
        for (const a of doing.slice(cap)) if (chance(0.08)) {
          setJob(world, a, 'peasant');
          remember(world, a, jk === 'gatherer' ? '열매 따러 다니는 대신 논밭을 갈기 시작했다' : '사냥 대신 논밭을 갈기 시작했다', 5, 0.3, [], 'work');
          lifeEvent(world, a, '농사꾼이 되었다', '🌾');
        }
      }
    }
    const fields = s.buildings.map(id => world.buildings.get(id)).filter(b => b && b.type === 'farm');
    const farmers = world.residents(s.id).filter(a => a.job?.id === 'peasant').length;
    const want = Math.max(1, Math.ceil(Math.max(farmers, s.pop * 0.25) / 10));
    if (fields.length >= want || fields.some(b => !b.done)) continue;
    const b = placeBuilding(world, s, 'farm', { era: c.era, owner: null, name: `${s.name} ${fields.length ? fields.length + 1 + '번째 ' : ''}공동 논밭`, budget: 0 });
    if (b && !fields.length) world.news('town', `🌾 ${s.name} 사람들이 함께 첫 논밭을 일구기 시작했다`, { country: c.code, sid: s.id }, 2);
  }
}

/** 저장된 세계를 불러올 때: 아직 원시 시대인 작은 첫 마을에도 마을 사람들을 채우고, 원시 시대 도로는 걷어 낸다 */
export function migrateStart(world) {
  for (const c of Object.values(world.countries)) {
    if (c.era !== 0) continue;
    for (const s of world.settlements.values()) if (s.country === c.code && s.roads && Object.keys(s.roads).length) { s.roads = {}; world.dirtySettlements.add(s.id); }
  }
  // 예전 저장본: 처음 마을은 이미 채워졌다
  for (const c of Object.values(world.countries)) if (c.seeded) { const s = world.settlements.get(c.capital); if (s) s.seeded = true; }
}
