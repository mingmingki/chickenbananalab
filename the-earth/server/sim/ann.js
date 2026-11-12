// 앤(Ann): 성격·능력·야망·외모·기억·감정·목표를 가진 하나의 인간
import { clamp, gauss, rand, pick, YEAR, ageOf, chance, fixJosa } from './util.js';
import { GOALS } from './data.js';

const SKIN = {
  ko: ['#f1d3b8', '#e9c4a3', '#e3b994', '#f3dcc5'], ja: ['#f1d3b8', '#ecc9a8', '#f3dcc5'], zh: ['#efd0b0', '#e6bf98', '#f3dac0'],
  vi: ['#d9ac80', '#c99a6c', '#e2b98f'], in: ['#a8734e', '#bb8559', '#8d5b3b', '#c79a70'], ar: ['#cf9f74', '#bd8a60', '#dbb08a'],
  af: ['#5b3a26', '#6e4630', '#4a2f1f', '#835639'], es: ['#d6a57c', '#e0b893', '#c58f63', '#f0d0b0'], pt: ['#c58f63', '#8e5d3c', '#e0b893', '#6e4630'],
  en: ['#f4d6c2', '#e8bfa3', '#c58f63', '#6e4630', '#f7e0d0'], de: ['#f6dccb', '#efcfb8', '#f9e6d8'], fr: ['#f4d6c2', '#e8bfa3', '#c58f63'],
  it: ['#eccaa8', '#e0b893', '#f2d5ba'], ru: ['#f6dccb', '#efd2bd', '#f9e6d8'],
};
const HAIR = {
  default: ['#1b1410', '#2b1d14', '#3b2a1e', '#1f1f1f'],
  light: ['#6b4a2f', '#a0784f', '#d6b370', '#3b2a1e', '#8c3f22', '#1b1410'],
};
const LIGHT_HAIR = new Set(['en', 'de', 'fr', 'ru', 'it']);

export const TRAIT_KEYS = ['O', 'C', 'E', 'A', 'N'];
export const ABILITY_KEYS = ['int', 'ath', 'cre', 'cha', 'hea'];
export const TRAIT_NAMES = { O: '개방성', C: '성실성', E: '외향성', A: '친화성', N: '신경성' };
export const ABILITY_NAMES = { int: '지능', ath: '운동신경', cre: '창의성', cha: '매력', hea: '건강' };

const rnd50 = (sd = 18) => clamp(Math.round(50 + gauss() * sd), 1, 100);

function mixHex(a, b, t) {
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const ch = s => (s >> 16) & 255;
  const r = Math.round(ch(pa) * (1 - t) + ch(pb) * t);
  const g = Math.round(((pa >> 8) & 255) * (1 - t) + ((pb >> 8) & 255) * t);
  const bl = Math.round((pa & 255) * (1 - t) + (pb & 255) * t);
  return '#' + ((r << 16) | (g << 8) | bl).toString(16).padStart(6, '0');
}

export function deriveValues(a) {
  const { O, C, E, A, N } = a.tr, { int, ath, cre, cha } = a.ab, amb = a.amb;
  const r = () => rand() * 22;
  return {
    family: clamp(25 + A * 0.35 + (100 - O) * 0.1 + (100 - amb) * 0.1 + r()),
    wealth: clamp(amb * 0.45 + (100 - A) * 0.15 + C * 0.1 + r()),
    knowledge: clamp(O * 0.35 + int * 0.4 + r()),
    power: clamp(amb * 0.5 + cha * 0.2 + (100 - A) * 0.15 + r()),
    art: clamp(cre * 0.5 + O * 0.25 + r()),
    adventure: clamp(O * 0.35 + ath * 0.15 + (100 - N) * 0.2 + r()),
    sport: clamp(ath * 0.6 + E * 0.1 + r()),
    community: clamp(A * 0.35 + E * 0.35 + r()),
    mastery: clamp(C * 0.45 + amb * 0.2 + r()),
  };
}

/** 가치관 → 인생 목표 (꿈) */
export function deriveGoals(a) {
  const v = a.val;
  const cand = [
    ['wealth', v.wealth], ['company', v.wealth * 0.6 + a.amb * 0.45], ['power', v.power], ['knowledge', v.knowledge],
    ['family', v.family], ['art', v.art], ['adventure', v.adventure], ['sport', v.sport], ['community', v.community], ['mastery', v.mastery],
  ].sort((x, y) => y[1] - x[1]);
  const goals = [];
  for (const [k, s] of cand) {
    if (goals.length >= 3) break;
    if (goals.length >= 1 && s < 52) break;
    if (k === 'wealth' && goals.some(g => g.k === 'company')) continue;
    if (k === 'company' && goals.some(g => g.k === 'wealth')) continue;
    goals.push({ k, text: GOALS[k].text, done: false, since: null, p: 0 });
  }
  return goals;
}

export function makeAnn(world, spec) {
  const id = world.newId();
  const culture = spec.culture || 'en';
  const gender = spec.gender === 'F' ? 'F' : 'M';
  const age = spec.age ?? 20;
  const tr = {}, ab = {};
  for (const k of TRAIT_KEYS) tr[k] = clamp(Math.round(spec.traits?.[k] ?? rnd50()), 1, 100);
  for (const k of ABILITY_KEYS) ab[k] = clamp(Math.round(spec.abilities?.[k] ?? rnd50(16)), 1, 100);
  const amb = clamp(Math.round(spec.ambition ?? rnd50(20)), 1, 100);
  const skinPool = SKIN[culture] || SKIN.en;
  const hairPool = LIGHT_HAIR.has(culture) ? HAIR.light : HAIR.default;
  const look = spec.look || {
    skin: pick(skinPool),
    hair: pick(hairPool),
    height: gender === 'M' ? 172 + gauss() * 7 : 160 + gauss() * 6,
    style: Math.floor(rand() * 4),
  };
  const r = rand();
  const a = {
    id, name: (spec.name || 'Ann').slice(0, 24), gender,
    orient: spec.orient || (r < 0.9 ? 'straight' : r < 0.95 ? 'gay' : 'bi'),
    culture, owner: spec.owner ?? null, country: spec.country, sid: spec.sid,
    born: spec.born ?? Math.round(world.clock - age * YEAR - rand() * YEAR * 0.9),
    gen: spec.gen || 1, alive: true, died: null, cause: null,
    tr, ab, amb, look,
    val: null, goals: [],
    sk: {}, edu: spec.edu ?? Math.round(clamp(age * 2, 0, 40)),
    nd: { food: 75 + rand() * 20, energy: 70 + rand() * 25, social: 55 + rand() * 30, fun: 55 + rand() * 30 },
    mood: 15, stress: 10, health: clamp(70 + ab.hea * 0.3), conf: clamp(40 + amb * 0.2 + (100 - tr.N) * 0.2),
    emo: { k: '평온', i: 0.3, why: '' },
    money: spec.money ?? 10,
    home: null, job: null, career: [], retired: false,
    rels: {}, spouse: null, partner: null, parents: spec.parents || [], kids: [], preg: null,
    x: spec.x ?? 0, z: spec.z ?? 0, act: null, travel: null,
    mem: [], th: [], why: [], life: [],
    rep: 0, fame: 0, chrono: clamp(gauss() * 1.1, -2.5, 2.5),
    day: { work: 0, meals: 0, talks: 0, best: null, worst: null },
    sick: 0, lastWork: 0, unemployedSince: world.clock, deployed: world.clock,
  };
  a.val = deriveValues(a);
  a.goals = deriveGoals(a);
  const base = Math.min(age, 30);
  const sk = (abil, extra = 0) => clamp(Math.round(base * 0.35 + abil * 0.15 + extra + rand() * 8), 0, 60);
  a.sk = {
    farming: sk(ab.ath * 0.5 + tr.C * 0.3), construction: sk(ab.ath * 0.6 + tr.C * 0.2), crafting: sk(ab.cre * 0.5 + tr.C * 0.3),
    science: sk(ab.int * 0.6), commerce: sk(ab.cha * 0.4 + tr.E * 0.3), leadership: sk(ab.cha * 0.4 + amb * 0.3),
    medicine: sk(ab.int * 0.4 + tr.A * 0.2), art: sk(ab.cre * 0.7), teaching: sk(tr.A * 0.3 + ab.int * 0.3), athletics: sk(ab.ath * 0.8),
  };
  if (age < 3) for (const k in a.sk) a.sk[k] = 0;
  return a;
}

/** 두 부모로부터 아기 앤 (유전: 평균 + 변이) */
export function makeBaby(world, mother, father, name, gender) {
  const inherit = (x, y, sd = 9) => clamp(Math.round((x + y) / 2 + gauss() * sd), 1, 100);
  const tr = {}, ab = {};
  for (const k of TRAIT_KEYS) tr[k] = inherit(mother.tr[k], father.tr[k], 12);
  for (const k of ABILITY_KEYS) ab[k] = inherit(mother.ab[k], father.ab[k], 10);
  const t = rand();
  const look = {
    skin: mixHex(mother.look.skin, father.look.skin, 0.3 + t * 0.4),
    hair: chance(0.5) ? mother.look.hair : father.look.hair,
    height: (gender === 'M' ? 172 : 160) + ((mother.look.height - 160) + (father.look.height - 172)) / 2 + gauss() * 4,
    style: Math.floor(rand() * 4),
  };
  const owner = mother.owner ?? father.owner;
  return makeAnn(world, {
    name, gender, age: 0, born: world.clock, culture: mother.culture, owner, country: mother.country, sid: mother.sid,
    traits: tr, abilities: ab, ambition: inherit(mother.amb, father.amb, 14), look,
    gen: Math.max(mother.gen, father.gen) + 1, parents: [mother.id, father.id], money: 0, edu: 0,
    x: mother.x + 1, z: mother.z + 1,
  });
}

// ── 기억 · 생각 · 감정 ──
const MEM_CAP = 70;
export function remember(world, a, text, imp = 3, val = 0, who = [], kind = 'event') {
  text = fixJosa(text);
  a.mem.push({ t: Math.round(world.clock), text, imp, v: val, who, k: kind });
  if (val > 0 && (!a.day.best || imp * val > a.day.best.s)) a.day.best = { s: imp * val, text };
  if (val < 0 && (!a.day.worst || imp * -val > a.day.worst.s)) a.day.worst = { s: imp * -val, text };
  if (a.mem.length > MEM_CAP) {
    // 망각: 오래되고 덜 중요한 기억부터 잊는다 (중요도 8 이상은 평생 간직)
    const now = world.clock;
    let worst = -1, worstScore = Infinity;
    for (let i = 0; i < a.mem.length - 5; i++) {
      const m = a.mem[i];
      if (m.imp >= 8) continue;
      const ageDays = (now - m.t) / 1440;
      const score = m.imp * Math.exp(-ageDays / (8 + m.imp * 12)) + Math.abs(m.v) * 0.5;
      if (score < worstScore) { worstScore = score; worst = i; }
    }
    a.mem.splice(worst >= 0 ? worst : 0, 1);
  }
}

export function think(world, a, text) {
  if (!text) return;
  text = fixJosa(text);
  for (let i = a.th.length - 1; i >= Math.max(0, a.th.length - 10); i--) if (a.th[i].text === text) return;
  a.th.push({ t: Math.round(world.clock), text });
  if (a.th.length > 40) a.th.shift();
  world.dirtyDetail.add(a.id);
}

export function lifeEvent(world, a, text, icon = '•') {
  a.life.push({ t: Math.round(world.clock), text: fixJosa(text), icon });
  if (a.life.length > 80) a.life.splice(1, 1);
}

const EMO = {
  pos: [[0.75, '행복'], [0.45, '기쁨'], [0.2, '만족']],
  neg: [[0.75, '절망'], [0.45, '슬픔'], [0.2, '실망']],
};
/** 감정 반응: 신경성이 높을수록 부정적 사건에 크게, 외향성이 높을수록 긍정적 사건에 크게 반응 */
export function feel(a, val, label = null, why = '') {
  const k = val < 0 ? 1 + a.tr.N / 100 : 0.8 + a.tr.E / 250;
  const d = val * k;
  a.mood = clamp(a.mood + d, -100, 100);
  if (val < 0) a.stress = clamp(a.stress - d * 0.4);
  const i = Math.min(1, Math.abs(d) / 30);
  if (!label) {
    const table = val >= 0 ? EMO.pos : EMO.neg;
    label = (table.find(([th]) => i >= th) || table[table.length - 1])[1];
  }
  if (i >= (a.emo.i || 0) * 0.6) a.emo = { k: label, i, why };
}

export function ageStage(age) {
  if (age < 3) return 'baby';
  if (age < 13) return 'child';
  if (age < 19) return 'teen';
  if (age < 65) return 'adult';
  return 'elder';
}
export const STAGE_NAMES = { baby: '아기', child: '어린이', teen: '청소년', adult: '성인', elder: '노인' };

export function displayAge(world, a) {
  return Math.floor(ageOf(a, a.alive ? world.clock : a.died));
}
