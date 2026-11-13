// 업적과 알림: 내 앤의 큰 사건(탄생·결혼·죽음·창업·졸업…)을 플레이어에게 알리고, 처음 이룬 일은 업적으로 남긴다.
import { ERAS } from './data.js';
import { refMonth } from './budget.js';
import { ageOf } from './util.js';

// 알림으로 보낼 생애 사건 (아이콘 → 중요도 1~3). 3은 푸시 알림도 바로 보낸다
export const NOTE_ICONS = {
  '👶': 3, '💍': 3, '🕯️': 3, '👑': 3, '🚀': 3, '🏢': 2, '💡': 2, '🔥': 2, '🧭': 2, '🎓': 2, '🏠': 2, '🎨': 2,
  '💕': 1, '💔': 1, '💼': 1, '🎖️': 1, '⭐': 1, '🤰': 1, '⛓️': 1, '🚗': 1, '🎂': 1, '📦': 1,
};

export const ACHIEVEMENTS = {
  first_deploy: { icon: '🌱', name: '첫걸음', desc: '처음으로 앤을 투입했다' },
  first_job: { icon: '💼', name: '첫 출근', desc: '내 앤이 처음으로 일자리를 얻었다' },
  first_love: { icon: '💕', name: '첫사랑', desc: '내 앤에게 연인이 생겼다' },
  wedding: { icon: '💍', name: '백년가약', desc: '내 앤이 결혼했다' },
  first_baby: { icon: '👶', name: '다음 세대', desc: '내 앤에게 아이가 태어났다' },
  gen3: { icon: '👨‍👩‍👧', name: '삼대', desc: '3세대 후손이 태어났다' },
  gen5: { icon: '🌳', name: '오대손', desc: '5세대 후손이 태어났다' },
  clan20: { icon: '🏘️', name: '대가족', desc: '살아 있는 내 앤과 후손이 20명이 넘었다' },
  clan100: { icon: '🏙️', name: '명문가', desc: '살아 있는 내 앤과 후손이 100명이 넘었다' },
  graduate: { icon: '🎓', name: '학사모', desc: '내 앤이 대학을 졸업했다' },
  home: { icon: '🏠', name: '내 집 마련', desc: '내 앤이 집을 지었다' },
  founder: { icon: '🏢', name: '창업가', desc: '내 앤이 회사를 세웠다' },
  ceo: { icon: '🧑‍💼', name: '대표님', desc: '내 앤이 회사 대표가 되었다' },
  rich: { icon: '💰', name: '부자', desc: '내 앤이 그 시대 월급 3년치가 넘는 돈을 모았다' },
  leader: { icon: '👑', name: '나라의 얼굴', desc: '내 앤이 나라의 지도자가 되었다' },
  discovery: { icon: '💡', name: '발견', desc: '내 앤이 새로운 것을 발견했다' },
  masterpiece: { icon: '🎨', name: '걸작', desc: '내 앤이 걸작을 남겼다' },
  pioneer: { icon: '🧭', name: '개척자', desc: '내 앤이 새 마을을 개척했다' },
  centenarian: { icon: '🎂', name: '백수', desc: '내 앤이 100세를 넘겼다' },
  astronaut: { icon: '🚀', name: '우주로', desc: '내 앤이 우주를 비행했다' },
  ...Object.fromEntries(ERAS.slice(1).map((e, i) => [`era${i + 1}`, { icon: '🌅', name: e.name, desc: `내 나라가 ${e.name} 시대를 열었다` }])),
};

/** 업적을 준다 (처음 한 번만) */
export function award(world, p, key) {
  const def = ACHIEVEMENTS[key];
  if (!def || !p) return false;
  p.ach = p.ach || {};
  if (p.ach[key]) return false;
  p.ach[key] = Math.round(world.clock);
  world.pushNote(p, { t: Math.round(world.clock), ann: null, name: null, text: `업적 달성 — ${def.name}: ${def.desc}`, icon: '🏆', lvl: 3, ach: key });
  return true;
}

/** 생애 사건이 일어날 때: 알림을 남기고, 해당되는 업적을 준다 */
export function onLifeEvent(world, a, text, icon) {
  const lvl = NOTE_ICONS[icon];
  const p = a.owner && world.players[a.owner];
  if (!lvl || !p) return;
  // 막 태어난 아기의 '태어났다'는 부모 쪽 알림과 겹치니 넘긴다
  if (icon === '👶' && world.clock - a.born < 2 && a.parents?.some(id => world.anns.get(id)?.owner === a.owner)) return;
  world.pushNote(p, { t: Math.round(world.clock), ann: a.id, name: a.name, text, icon, lvl });
  const k = { '💼': 'first_job', '🎖️': 'first_job', '💕': 'first_love', '💍': 'wedding', '🏠': 'home', '👑': 'leader', '💡': 'discovery', '🎨': 'masterpiece', '🧭': 'pioneer', '🚀': 'astronaut' }[icon];
  if (k) award(world, p, k);
  if (icon === '👶' && text.includes('탄생')) award(world, p, 'first_baby');
  if (icon === '🎓' && text.includes('대학')) award(world, p, 'graduate');
  if (icon === '🏢') award(world, p, 'ceo');
}

/** 하루에 한 번: 숫자로 따지는 업적 */
export function dailyAchievements(world) {
  const founders = new Set();
  for (const co of world.companies.values()) if (!co.pub && co.founder) founders.add(co.founder);
  for (const p of Object.values(world.players)) {
    let alive = 0, maxGen = 0;
    for (const id of p.anns) {
      const a = world.anns.get(id);
      if (!a) continue;
      maxGen = Math.max(maxGen, a.gen || 1);
      if (!a.alive) continue;
      alive++;
      if (a.money > refMonth(world.countries[a.country]?.era ?? 0) * 36) award(world, p, 'rich');
      if (ageOf(a, world.clock) >= 100) award(world, p, 'centenarian');
      if (founders.has(a.id)) award(world, p, 'founder');
    }
    if (p.anns.length) award(world, p, 'first_deploy');
    if (maxGen >= 3) award(world, p, 'gen3');
    if (maxGen >= 5) award(world, p, 'gen5');
    if (alive >= 20) award(world, p, 'clan20');
    if (alive >= 100) award(world, p, 'clan100');
    const c = world.countries[p.country];
    if (c) for (let e = 1; e <= c.era; e++) if ((p.eraAtJoin ?? 0) < e) award(world, p, `era${e}`);
  }
}

/** 다시 들어왔을 때 보여 줄 '그동안 있었던 일' */
export function recap(world, p, since) {
  since = Math.max(0, Math.min(+since || 0, world.clock));
  const notes = (p.inbox || []).filter(n => n.t > since);
  let alive = 0, born = 0, died = 0;
  for (const id of p.anns) {
    const a = world.anns.get(id);
    if (!a) continue;
    if (a.alive) alive++;
    if (a.born > since && a.gen > 1) born++;
    if (!a.alive && a.died > since) died++;
  }
  const c = world.countries[p.country];
  const history = (c?.history || []).filter(h => h.t > since).slice(-8);
  return { since, now: Math.round(world.clock), days: Math.floor((world.clock - since) / 1440), alive, born, died, notes: notes.slice(-40), history, era: c?.era ?? 0, eraName: ERAS[c?.era ?? 0].name, pop: c?.pop || 0 };
}
