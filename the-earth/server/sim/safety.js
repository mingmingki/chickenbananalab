// 치안과 소방.
// 경찰(산업 시대 전에는 관청의 포졸)은 동네를 순찰하고, 도둑이 들면 잡으러 나선다. 많을수록 도둑이 줄어든다.
// 불은 겨울과 가뭄에 잘 난다. 소방서가 있으면 소방관이 출동해 금방 끄지만, 없으면 이웃들이 물을 날라도 다 타 버리기 쉽다.
import { JOBS, BUILDINGS } from './data.js';
import { chance, pick, rand, clamp, ageOf, DAY, YEAR, ga, reul } from './util.js';
import { lifeEvent, remember, think, feel } from './ann.js';
import { logToday } from './mind.js';
import { doorPoint } from './settlement.js';
import { seasonOf } from './thoughts.js';

const GUARD_JOBS = ['police', 'soldier'];

/** 이 마을의 치안 인력 (경찰서 경찰관, 없으면 관청의 포졸) */
export function guardsOf(world, s) {
  const out = [];
  for (const co of world.companies.values()) {
    if (co.dead || !co.pub || co.sid !== s.id || (co.ind !== 'police' && co.ind !== 'hall')) continue;
    for (const id of co.emp) { const a = world.anns.get(id); if (a?.alive && GUARD_JOBS.includes(a.job?.id)) out.push(a); }
  }
  return out;
}
export const guardTitle = a => (a.job?.id === 'police' ? '경찰관' : '포졸');
/** 치안 정도 0~1 */
export function coverage(world, s) { return Math.min(1, guardsOf(world, s).length * 30 / Math.max(10, s.pop)); }

/** 도둑 사건: 붙잡히면 되찾는다. 결과 문장을 돌려준다 */
export function policeResponse(world, victim, loss) {
  const s = world.settlements.get(victim.sid);
  const guards = guardsOf(world, s);
  const res = world.residents(s.id);
  // 범인: 가난하고 덜 착한 어른 (없으면 떠돌이 도둑)
  const suspects = res.filter(x => x.id !== victim.id && ageOf(x, world.clock) >= 16 && x.money < loss && x.tr.A + x.tr.C < 80 && !(x.caughtAt && world.clock - x.caughtAt < YEAR * 2));
  const thief = suspects.length && chance(0.7) ? pick(suspects) : null;
  if (!guards.length) {
    if (thief) thief.money += loss * 0.8;
    return null;
  }
  const cover = coverage(world, s);
  const cop = guards.sort((x, y) => (y.sk.athletics + y.tr.C) - (x.sk.athletics + x.tr.C))[Math.floor(rand() * Math.min(2, guards.length))];
  const caught = chance(0.25 + cover * 0.45 + cop.sk.athletics / 400);
  const title = guardTitle(cop);
  if (!caught) {
    if (thief) thief.money += loss * 0.8;
    lifeEvent(world, cop, `${victim.name} 집 도난 사건을 맡았지만 범인을 놓쳤다`, '🔎');
    return `${title} ${ga(cop.name)} 쫓았지만 놓쳤다`;
  }
  victim.money += loss * 0.85;
  lifeEvent(world, cop, `도둑을 붙잡았다 — ${victim.name} 집 도난 사건`, '🚓');
  remember(world, cop, `${victim.name} 집에 든 도둑을 붙잡았다`, 6, 0.7, [victim.id], 'work');
  feel(cop, 14, '뿌듯함', '검거');
  cop.rep = clamp((cop.rep || 0) + 3, -100, 100);
  if (cop.act?.type === 'work') logToday(world, cop, '🚓', `출동 — ${victim.name} 집 도난 사건 범인 검거`);
  remember(world, victim, `${title} ${ga(cop.name)} 도둑을 잡아 잃어버린 재산을 되찾아 주었다`, 5, 0.6, [cop.id], 'event');
  if (thief) {
    lifeEvent(world, thief, `도둑질을 하다 ${title}에게 붙잡혔다`, '⛓️');
    remember(world, thief, `${title} ${cop.name}에게 붙잡혔다. 부끄럽다`, 7, -0.9, [cop.id], 'event');
    thief.rep = clamp((thief.rep || 0) - 25, -100, 100);
    thief.caughtAt = Math.round(world.clock);
    thief.money = Math.max(0, thief.money - loss * 0.2);
    think(world, thief, pick(['다시는 이런 짓 안 할 거야…', '배가 고파서 그랬어. 핑계지만.', '다들 나를 손가락질하겠지.']));
    for (const id in victim.rels) if (+id === thief.id) victim.rels[id].a = Math.max(-100, victim.rels[id].a - 40);
  }
  world.news('social', `🚓 ${s.name}: ${title} ${ga(cop.name)} 도둑을 붙잡았다${thief ? ` — 범인은 ${thief.name}` : ''}`, { sid: s.id, country: s.country, anns: [cop.id] }, 0);
  return `${title} ${ga(cop.name)} 범인을 잡아 되찾아 주었다`;
}

// ── 불 ──
function fireStation(world, s) {
  for (const co of world.companies.values()) {
    if (co.dead || !co.pub || co.sid !== s.id || co.ind !== 'fire') continue;
    if (world.buildings.get(co.b)?.done && co.emp.length) return co;
  }
  return null;
}

/** 매일: 오늘 불이 날지, 언제 어디서 날지 정해 둔다 */
export function safetyDaily(world, s) {
  if (s.fireAt) return;
  const cands = s.buildings.map(id => world.buildings.get(id)).filter(b => b && b.done && !['farm', 'campfire'].includes(b.type) && !b.infra);
  if (cands.length < 3) return;
  const c = world.countries[s.country];
  const season = seasonOf(world, s), wk = s.weather?.k || '맑음';
  let p = cands.length * 0.000028 * (season === '겨울' ? 2 : 1) * ((s.dry || 0) > 10 ? 1.6 : 1) * (c.era >= 5 ? 0.55 : 1);
  if (['비', '소나기', '폭우', '눈', '폭설'].includes(wk)) p *= 0.3;
  if (!chance(p)) return;
  const b = pick(cands);
  s.fireAt = Math.round(world.clock + rand() * DAY);
  s.fireB = b.id;
}

/** 매 분: 정해 둔 시각이 되면 불이 난다 */
export function safetyTick(world, s) {
  if (s.fireOff && world.clock >= s.fireOff.t) { world.dirtyBuildings.add(s.fireOff.b); s.fireOff = null; }
  if (!s.fireAt || world.clock < s.fireAt) return;
  const b = world.buildings.get(s.fireB);
  s.fireAt = null; s.fireB = null;
  if (b && b.done) ignite(world, s, b);
}

function ignite(world, s, b) {
  const def = BUILDINGS[b.type];
  const c = world.countries[s.country];
  const station = fireStation(world, s);
  const wooden = (def.era ?? 0) <= 2;
  const people = [...b.res, ...(b.co ? (world.companies.get(b.co)?.emp || []) : [])].map(id => world.anns.get(id)).filter(a => a?.alive);
  let dur, lost, who = [];
  if (station) {
    // 소방관 출동: 근무 중인 소방관은 바로, 나머지는 비상 소집
    const ff = station.emp.map(id => world.anns.get(id)).filter(a => a?.alive && a.sid === s.id);
    const crew = ff.filter(a => a.act?.type === 'work').concat(ff.filter(a => a.act?.type !== 'work' && a.act?.type !== 'sleep')).slice(0, 8);
    dur = 50 + rand() * 70;
    lost = chance(0.04);
    for (const a of crew) {
      who.push(a.id);
      lifeEvent(world, a, `'${b.name}' 화재 진압 출동`, '🚒');
      remember(world, a, `${b.name}에 난 불을 동료들과 껐다`, 6, 0.6, [], 'work');
      feel(a, 10, '긴장', '화재');
      a.sk.athletics = clamp(a.sk.athletics + 0.6);
      logToday(world, a, '🚒', `출동 — ${b.name} 화재`);
      if (a.act && a.act.type === 'work') {
        const [tx, tz] = doorPoint(b);
        Object.assign(a.act, { tx, tz, ph: 'move', repath: true });
        world.dirtyAct.add(a.id);
      }
    }
    world.news('disaster', `🚒 ${s.name} '${b.name}'에 불 — ${station.name} 소방관 ${crew.length}명이 출동해 ${lost ? '불길을 잡았지만 건물은 다 타 버렸다' : `${Math.round(dur)}분 만에 껐다`}`, { sid: s.id, country: s.country, anns: who }, 1);
  } else {
    // 소방서가 없으면 이웃들이 물을 길어 나른다
    dur = 150 + rand() * 200;
    lost = chance(wooden ? 0.55 : 0.32);
    const helpers = world.residents(s.id).filter(a => ageOf(a, world.clock) >= 15 && a.act?.type !== 'sleep' && chance(0.5)).slice(0, 12);
    for (const a of helpers) { remember(world, a, `${b.name}에 불이 나 다 같이 물을 날랐다`, 5, -0.3, [], 'town'); a.nd.energy = clamp(a.nd.energy - 15); who.push(a.id); }
    world.news('disaster', `🔥 ${s.name} '${b.name}'에 불 — ${helpers.length ? `이웃 ${helpers.length}명이 물을 날랐지만 ${lost ? '모두 타 버렸다' : '겨우 껐다'}` : lost ? '아무도 손쓰지 못해 모두 타 버렸다' : '혼자서 겨우 껐다'}${c.era >= 4 ? ' (소방서가 없다)' : ''}`, { sid: s.id, country: s.country, anns: who }, 1);
    if (c.era >= 4) { s.need = s.need || {}; s.need.fire = (s.need.fire || 0) + 5; }
  }
  b.fire = { t0: Math.round(world.clock), until: Math.round(world.clock + dur) };
  s.fireOff = { b: b.id, t: b.fire.until + 1 };
  for (const a of people) {
    remember(world, a, lost ? `${b.name}이(가) 불에 다 타 버렸다` : `${b.name}에 불이 났지만 큰 피해는 없었다`, lost ? 8 : 5, lost ? -0.9 : -0.3, [], 'event');
    if (lost) { feel(a, -25, '절망', '화재'); a.stress = clamp(a.stress + 25); think(world, a, pick(['모든 게 잿더미가 됐다…', '사람이 안 다친 게 다행이다.', '처음부터 다시 시작해야 한다.'])); }
    if (chance(lost ? 0.15 : 0.05)) { a.health = clamp(a.health - 15); lifeEvent(world, a, '불길 속에서 빠져나오다 다쳤다', '🩹'); }
  }
  if (lost) {
    // 타 버린 건물은 다시 지어야 한다 (뼈대만 남는다)
    b.done = false; b.burnt = true; b.progress = Math.round(b.work * 0.15); b.built = null;
    for (const rid of [...b.res]) { const a = world.anns.get(rid); if (a) world.leaveHome(a); }
    b.budget = Math.max(b.budget || 0, (def.cost || 0) * 0.5); // 이웃 성금·나라 지원으로 다시 짓는다
    const owner = b.owner != null && world.anns.get(b.owner);
    if (owner) lifeEvent(world, owner, `${b.name} 화재로 전소`, '🔥');
  }
  world.dirtyBuildings.add(b.id);
}

/** 클라이언트로 보낼 불·그을음 상태 */
export function fireOut(world, b) {
  const o = {};
  if (b.fire && b.fire.until > world.clock) o.fire = 1;
  if (b.burnt) o.burnt = 1;
  return o;
}
