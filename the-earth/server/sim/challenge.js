// 이번 생의 도전과 명예의 전당.
//  · 도전: 내 앤의 나이·시대·형편에 맞는 목표 세 개 ("친구 두 명 더 사귀기", "노래·이야기 하나 짓기", "족장이 되기"…).
//    이루면 별과 덤 속삭임을 받는다 — 속삭임으로 앤을 이끌어 도전을 이루는 고리. 하루(게임)에 한 번 다른 도전으로 바꿀 수 있다.
//  · 명예의 전당: 서버의 플레이어끼리 — 가문(살아 있는 후손), 별, 명성, 퍼진 작품, 업적. 그리고 세계에서 가장 널리 불리는 노래·이야기.
import { ageOf, DAY, weighted, fixJosa, wa } from './util.js';
import { titleOf, leaderTitle } from './tribes.js';
import { SKILLS } from './data.js';
import { giveWhisper } from './whisper.js';
import { award } from './achieve.js';
import { refMonth } from './budget.js';

const ACTIVE = 3;

// 지금 가장 친한 사람 수 등 헤아리기
function countRels(world, a, kinds) {
  let n = 0;
  for (const id in a.rels) if (kinds.includes(a.rels[id].k) && world.anns.get(+id)?.alive) n++;
  return n;
}
const ownsHome = (world, a) => { const h = a.home && world.buildings.get(a.home); return !!(h && h.owner === a.id && h.done); };
const topCreation = (world, a) => Math.max(0, ...(a.made || []).map(id => world.creations?.get(id)?.n || 0));

// cur: 지금 값. delta 가 true 면 받은 때보다 얼마나 늘었는지로 본다
export const CH = {
  friend: { icon: '🫶', stars: 2, w: 3, ok: x => x.age >= 5, goal: () => 2, delta: true, cur: x => countRels(x.world, x.a, ['friend', 'best']), t: g => `친구 ${g}명 더 사귀기`, hint: '속삭임: 말 걸어 봐' },
  best: { icon: '🤞', stars: 3, w: 1.5, ok: x => x.age >= 6 && countRels(x.world, x.a, ['best']) === 0 && countRels(x.world, x.a, ['friend']) > 0, goal: () => 1, cur: x => countRels(x.world, x.a, ['best']), t: () => '가장 친한 친구 만들기', hint: '친구에게 자주 말 걸기' },
  create: { icon: '🎵', stars: 2, w: 2.5, ok: x => x.age >= 5, goal: () => 1, delta: true, cur: x => (x.a.made || []).length, t: (g, arg, x) => (x.age < 13 ? '새 놀이 하나 만들기' : '노래·이야기 하나 짓기'), hint: '속삭임: 하나 지어 봐' },
  spread: { icon: '📣', stars: 3, w: 2, ok: x => (x.a.made || []).length > 0 && topCreation(x.world, x.a) < 6, goal: () => 8, cur: x => topCreation(x.world, x.a), t: g => `내가 지은 것을 ${g}명이 알게 하기`, hint: '사람들과 자주 어울리면 퍼진다' },
  learn: { icon: '📜', stars: 1, w: 1.5, ok: x => x.age >= 4, goal: () => 3, delta: true, cur: x => x.a.lrn || 0, t: g => `노래·이야기·놀이 ${g}가지 배우기`, hint: '어르신·친구와 이야기하기' },
  love: { icon: '💕', stars: 3, w: 2.5, ok: x => x.age >= 16 && x.age < 60 && !x.a.partner && !x.a.spouse, goal: () => 1, cur: x => (x.a.partner || x.a.spouse ? 1 : 0), t: () => '연인 만들기', hint: '설레는 사람에게 마음을 전해 봐' },
  marry: { icon: '💍', stars: 4, w: 3, ok: x => x.age >= 19 && !!x.a.partner && !x.a.spouse, goal: () => 1, cur: x => (x.a.spouse ? 1 : 0), t: () => '결혼하기', hint: '연인을 자주 만나기' },
  baby: { icon: '👶', stars: 4, w: 2, ok: x => !!x.a.spouse && x.age >= 19 && x.age < 44 && x.a.kids.length < 4, goal: () => 1, delta: true, cur: x => x.a.kids.length, t: () => '아이 갖기', hint: '가족과 시간 보내기' },
  home: { icon: '🏠', stars: 3, w: 2, ok: x => x.age >= 18 && !ownsHome(x.world, x.a), goal: () => 1, cur: x => (ownsHome(x.world, x.a) ? 1 : 0), t: () => '내 집 짓기', hint: '나이가 차면 독립한다' },
  chief: { icon: '👑', stars: 6, w: 0.8, ok: x => x.age >= 24 && x.age < 70 && !titleOf(x.world, x.a), goal: () => 1, cur: x => (titleOf(x.world, x.a) ? 1 : 0), t: (g, arg, x) => fixJosa(`${leaderTitle(x.world.countries[x.a.country])}이(가) 되기`), hint: '따르는 사람이 많아지면' },
  discover: { icon: '💡', stars: 5, w: 1.6, ok: x => x.age >= 14 && x.era <= 1, goal: () => 1, delta: true, cur: x => (x.a.discoveries || []).length, t: () => '새로운 것 하나 알아내기', hint: '속삭임: 궁리해 봐' },
  fame: { icon: '⭐', stars: 3, w: 1.2, ok: x => x.age >= 12 && (x.a.fame || 0) < 85, goal: () => 10, delta: true, cur: x => x.a.fame || 0, t: g => `이름 알리기 (명성 +${g})`, hint: '작품·발견·좋은 일' },
  skill: { icon: '🛠️', stars: 2, w: 2, ok: x => x.age >= 8, goal: () => 8, delta: true, pickArg: x => { const e = Object.entries(x.a.sk || {}).filter(([, v]) => v < 85).sort((p, q) => q[1] - p[1]); return (e[0] || ['athletics'])[0]; }, cur: (x, arg) => x.a.sk?.[arg] || 0, t: (g, arg) => `${SKILLS[arg] || '솜씨'} 솜씨 +${g}`, hint: '그 일을 자주 하기' },
  whisper: { icon: '💬', stars: 1, w: 1.5, ok: () => true, goal: () => 3, delta: true, cur: x => x.p.wh?.ok || 0, t: g => `속삭임 ${g}번 들어주게 하기`, hint: '내켜 하는 말을 골라 봐' },
  explore: { icon: '🧭', stars: 1, w: 1.4, ok: x => x.age >= 6, goal: () => 2, delta: true, cur: x => x.a.finds || 0, t: g => `마을 밖에서 ${g}번 무언가 찾기`, hint: '속삭임: 마을 밖을 둘러봐' },
  makeup: { icon: '🤝', stars: 2, w: 2, ok: x => !!rivalOf(x.world, x.a), pickArg: x => rivalOf(x.world, x.a), goal: () => 1, cur: (x, arg) => ((x.a.rels[arg]?.a ?? -100) > -5 ? 1 : 0), t: (g, arg, x) => `${wa(x.world.anns.get(arg)?.name || '그 사람')} 화해하기`, hint: '속삭임: 화해해 봐' },
  mood: { icon: '😊', stars: 1, w: 1, ok: x => x.a.mood < 30, goal: () => 1, cur: x => (x.a.mood >= 45 ? 1 : 0), t: () => '아주 기분 좋은 날 보내기', hint: '쉬기·놀기·좋아하는 사람 만나기' },
  rich: { icon: '💰', stars: 3, w: 1, ok: x => x.era >= 2 && x.age >= 20, goal: x => Math.round(Math.max(x.a.money, 0) + refMonth(x.era) * 3), cur: x => x.a.money, t: () => '석 달 치 벌이 모으기', hint: '일에 힘써 봐' },
  elder: { icon: '🎂', stars: 2, w: 1.5, ok: x => x.age >= 55, goal: x => (Math.floor(x.age / 10) + 1) * 10, cur: x => Math.floor(x.age), t: g => `${g}살 생일 맞기`, hint: '푹 쉬고 잘 먹기' },
};
function rivalOf(world, a) {
  let best = null;
  for (const id in a.rels) { const r = a.rels[id]; const b = world.anns.get(+id); if (b?.alive && r.a <= -25 && (!best || r.a < a.rels[best].a)) best = +id; }
  return best;
}

function ctx(world, p, a) {
  const age = ageOf(a, world.clock);
  return { world, p, a, age, era: world.countries[a.country]?.era ?? 0 };
}

function make(x, k) {
  const d = CH[k];
  const arg = d.pickArg ? d.pickArg(x) : null;
  const goal = d.goal(x);
  const base = d.delta ? d.cur(x, arg) : 0;
  const ch = { k, arg, goal, base, at: Math.round(x.world.clock), ann: x.a.id };
  ch.label = fixJosa(String(d.t(goal, arg, x)));
  return ch;
}

/** 도전 목록을 채우고 (앤이 바뀌면 새로), 지금 진행 정도를 계산한다 */
export function ensureChallenges(world, p) {
  const a = world.mainAnn(p);
  if (!a) return null;
  const C = p.ch || (p.ch = { list: [], stars: 0, done: 0, hist: [], rr: -1 });
  const x = ctx(world, p, a);
  if (x.age < 3) return C;
  C.list = C.list.filter(ch => ch.ann === a.id && CH[ch.k]);
  let guard = 0;
  while (C.list.length < ACTIVE && guard++ < 20) {
    const used = new Set([...C.list.map(c => c.k), ...C.hist.slice(-4)]);
    const keys = Object.keys(CH).filter(k => !used.has(k) && CH[k].ok(x));
    if (!keys.length) break;
    C.list.push(make(x, weighted(keys, k => CH[k].w)));
  }
  return C;
}
function progress(x, ch) {
  const d = CH[ch.k];
  const cur = d.cur(x, ch.arg);
  const v = d.delta ? cur - ch.base : cur;
  return Math.max(0, Math.min(ch.goal, Math.floor(v + 1e-6)));
}

/** 게임 한 시간마다: 이룬 도전이 있으면 별과 덤 속삭임 */
export function challengeHourly(world) {
  for (const p of Object.values(world.players)) {
    if (!p.anns?.length) continue;
    const C = ensureChallenges(world, p);
    const a = world.mainAnn(p);
    if (!C || !a) continue;
    const x = ctx(world, p, a);
    for (const ch of [...C.list]) {
      if (progress(x, ch) < ch.goal) continue;
      const d = CH[ch.k];
      C.list.splice(C.list.indexOf(ch), 1);
      C.stars += d.stars; C.done++;
      C.hist = [...C.hist, ch.k].slice(-8);
      giveWhisper(p, 1);
      world.pushNote(p, { t: Math.round(world.clock), ann: a.id, name: a.name, text: `도전 성공 — ${ch.label} (별 +${d.stars} · 속삭임 +1)`, icon: '🎯', lvl: 2, ch: ch.k });
      if (C.done >= 5) award(world, p, 'ch5');
      if (C.done >= 20) award(world, p, 'ch20');
    }
    if ((p.wh?.ok || 0) >= 10) award(world, p, 'whisper10');
    ensureChallenges(world, p);
  }
}

/** 하루(게임)에 한 번 다른 도전으로 바꾸기 */
export function rerollChallenge(world, p, i) {
  const C = ensureChallenges(world, p);
  const a = world.mainAnn(p);
  if (!C || !a) return { error: '내 앤이 없어요' };
  const day = Math.floor(world.clock / DAY);
  if (C.rr === day) return { error: '오늘은 이미 바꿨어요. 내일 다시 바꿀 수 있어요' };
  const ch = C.list[+i];
  if (!ch) return { error: '없는 도전이에요' };
  const x = ctx(world, p, a);
  const used = new Set([...C.list.map(c => c.k), ...C.hist.slice(-4)]);
  const keys = Object.keys(CH).filter(k => !used.has(k) && CH[k].ok(x));
  if (!keys.length) return { error: '지금은 바꿀 수 있는 도전이 없어요' };
  C.list[+i] = make(x, weighted(keys, k => CH[k].w));
  C.hist = [...C.hist, ch.k].slice(-8);
  C.rr = day;
  return { ok: true };
}

export function challengesOut(world, p) {
  const C = ensureChallenges(world, p);
  const a = world.mainAnn(p);
  if (!C || !a) return { list: [], stars: p.ch?.stars || 0, done: p.ch?.done || 0, canReroll: false };
  const x = ctx(world, p, a);
  return {
    list: C.list.map(ch => ({ k: ch.k, icon: CH[ch.k].icon, label: ch.label, hint: CH[ch.k].hint, stars: CH[ch.k].stars, goal: ch.goal, cur: progress(x, ch), big: ch.goal > 20 })),
    stars: C.stars, done: C.done, canReroll: C.rr !== Math.floor(world.clock / DAY),
  };
}

// ── 명예의 전당 ──
function lineage(world, p) {
  const seen = new Set(), q = [...p.anns];
  let alive = 0, gen = 1;
  while (q.length && seen.size < 3000) {
    const id = q.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    const a = world.anns.get(id);
    if (!a) continue;
    if (a.alive) alive++;
    gen = Math.max(gen, a.gen || 1);
    for (const k of a.kids || []) q.push(k);
  }
  return { alive, gen, ids: seen };
}

export function fameBoard(world, me) {
  const rows = [];
  for (const p of Object.values(world.players)) {
    if (!p.anns?.length) continue;
    const L = lineage(world, p);
    const main = world.mainAnn(p);
    let fame = 0, spread = 0, made = 0, oldest = 0;
    for (const id of L.ids) {
      const a = world.anns.get(id);
      if (!a) continue;
      if (a.owner === p.id || p.anns.includes(a.id)) fame = Math.max(fame, Math.round(a.fame || 0));
      for (const cid of a.made || []) { const c = world.creations?.get(cid); if (c) { spread += c.n; made++; } }
      if (p.anns.includes(a.id)) oldest = Math.max(oldest, Math.floor(((a.alive ? world.clock : a.died) - a.born) / 525600));
    }
    rows.push({
      pid: p.id, name: p.name, color: p.color, me: p.id === me?.id,
      ann: main ? { id: main.id, name: main.name } : null,
      clan: L.alive, gen: L.gen, stars: p.ch?.stars || 0, fame, spread, made, ach: Object.keys(p.ach || {}).length, oldest,
    });
  }
  const top = (k, n = 10) => rows.filter(r => r[k] > 0).sort((x, y) => y[k] - x[k]).map((r, i) => ({ ...r, rank: i + 1, v: r[k] })).filter((r, i) => i < n || r.me);
  // 세계에서 가장 널리 퍼진 작품, 가장 이름난 사람 (플레이어가 아니어도)
  const works = [...(world.creations?.values() || [])].sort((x, y) => y.n - x.n).slice(0, 8).map(c => ({ id: c.id, name: c.name, kind: c.kind, n: c.n, by: c.by, byName: c.byName, alive: !!world.anns.get(c.by)?.alive, mine: !!(me && world.anns.get(c.by)?.owner === me.id) }));
  const people = [...world.anns.values()].filter(a => a.alive && (a.fame || 0) > 5).sort((x, y) => y.fame - x.fame).slice(0, 8)
    .map(a => ({ id: a.id, name: a.name, fame: Math.round(a.fame), title: titleOf(world, a) || null, why: a.discoveries?.length ? `'${a.discoveries.at(-1)}' 발견` : (a.made || []).length ? `${(a.made || []).length}가지 지음` : null, mine: !!(me && a.owner === me.id) }));
  return {
    boards: [
      { k: 'stars', name: '도전의 별', unit: '개', rows: top('stars') },
      { k: 'clan', name: '가문 (살아 있는 후손)', unit: '명', rows: top('clan') },
      { k: 'spread', name: '퍼진 작품', unit: '명이 앎', rows: top('spread') },
      { k: 'fame', name: '명성', unit: '', rows: top('fame') },
      { k: 'ach', name: '업적', unit: '개', rows: top('ach') },
    ],
    works, people, players: rows.length,
  };
}
