// 인생의 제도와 고비: 아이 계획, 육아휴직, 은퇴, 병역(대한민국 남성, 근대 이후)
import { clamp, pick, chance, ageOf, DAY, YEAR, ga, wa, reul } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { leaveJob } from './economy.js';
import { book, refMonth } from './budget.js';
import { consult } from './dilemma.js';

/** 부부의 아이 계획 (둘이 함께 정한다) */
export function setKidPlan(world, a, want, years) {
  const plan = { want, until: Math.round(world.clock + years * YEAR) };
  a.kidPlan = { ...plan };
  const sp = a.spouse && world.anns.get(a.spouse);
  if (sp?.alive) sp.kidPlan = { ...plan };
}

/** 휴직 (육아휴직 등): 일자리는 그대로, 그동안은 출근하지 않는다 */
export function startLeave(world, a, why, days, delayDays = 0) {
  if (!a.job) return;
  const now = Math.round(world.clock);
  a.leave = { why, from: now + Math.round(delayDays * DAY), until: now + Math.round((delayDays + days) * DAY), started: !delayDays };
  if (!delayDays) {
    lifeEvent(world, a, `${why} 시작 (${Math.round(days)}일)`, '🍼');
    remember(world, a, `${reul(why)} 냈다`, 6, 0.4, [], 'family');
  }
}
export const onLeave = (world, a) => !!(a.leave && world.clock >= a.leave.from && world.clock < a.leave.until);

/** 하루에 한 번: 휴직 급여와 복직 */
export function leaveDaily(world, a, c) {
  if (!a.leave) return;
  if (!a.job) { a.leave = null; return; } // 일을 그만두면 휴직도 끝
  const now = world.clock;
  if (now >= a.leave.until) {
    const why = a.leave.why;
    a.leave = null;
    if (a.job) {
      lifeEvent(world, a, `${why}을(를) 마치고 복직했다`, '💼');
      think(world, a, pick(['오랜만의 출근이다. 잘할 수 있을까?', '아기 얼굴이 눈에 밟힌다.']));
    }
    return;
  }
  if (now < a.leave.from) return;
  if (!a.leave.started) { a.leave.started = true; lifeEvent(world, a, `${a.leave.why} 시작`, '🍼'); }
  // 나라에서 주는 육아휴직 급여: 보통 일자리 월급의 70% 남짓
  if (c && c.era >= 5) {
    const pay = refMonth(c.era) * 0.7 / 30;
    if (c.treasury > pay * 30) { c.treasury -= pay; a.money += pay; book(world, a, 'inc', '육아휴직 급여', pay); }
  }
}

/** 은퇴 */
export function retire(world, a) {
  if (!a.job) return;
  const co = a.job.co != null ? world.companies.get(a.job.co) : null;
  const ceo = a.job.ceo;
  leaveJob(world, a, '은퇴');
  a.retired = true;
  if (ceo && co) remember(world, a, `${co.name}을(를) 후배들에게 맡겼다`, 7, 0.3, [], 'work');
  think(world, a, pick(['평생 일했다. 이제는 쉬어도 되겠지.', '내일 아침엔 알람 없이 일어나야지.', '막상 그만두니 허전하다.']));
}

// ── 병역 ──
const BRANCH = [['육군', 18], ['육군', 18], ['육군', 18], ['해군', 20], ['공군', 21], ['해병대', 18]];
export const draftable = (world, a, age) => a.country === 'KR' && a.gender === 'M' && (world.countries[a.country]?.era ?? 0) >= 5 && !a.served && !a.army && age >= 19 && age < 29;

export function enlist(world, a, forced = false) {
  if (a.army || a.served) return;
  const [branch, months] = pick(BRANCH);
  const now = Math.round(world.clock);
  const until = now + Math.round(months * 30.4 * DAY);
  if (a.job) leaveJob(world, a, '입대');
  a.act = null;
  a.army = { branch, since: now, until };
  a.travel = { until, from: a.sid, to: a.sid, kind: 'army', where: branch };
  lifeEvent(world, a, `${branch} 입대${forced ? ' — 더는 미룰 수 없었다' : ''} (${months}개월)`, '🎖️');
  remember(world, a, `${branch}에 입대했다. 머리를 짧게 깎았다`, 8, -0.1, [], 'life');
  think(world, a, pick(['18개월… 금방 지나가겠지?', '건강하게만 돌아오자.', '가족들 얼굴이 벌써 그립다.']));
  for (const id of [...a.parents, a.partner].filter(Boolean)) {
    const p = world.anns.get(id);
    if (!p?.alive) continue;
    remember(world, p, `${ga(a.name)} ${branch}에 입대했다`, 6, -0.3, [a.id], 'family');
    think(world, p, id === a.partner ? pick(['기다릴게. 편지 꼭 쓸게.', '18개월… 우리 괜찮겠지?']) : pick(['밥은 잘 먹고 다닐까.', '아프지 말고 건강하게만 돌아와라.']));
  }
  world.dirtyAnnMeta.add(a.id);
}

/** 하루에 한 번: 병역 대상인지 살핀다 (대한민국 남성, 근대 이후) */
export function draftDaily(world, a, age) {
  if (!draftable(world, a, age)) return;
  if (a.health < 32) {
    a.served = 'exempt';
    lifeEvent(world, a, '병역 판정 — 건강 문제로 사회복무요원 대상이 되었다', '📋');
    return;
  }
  if (a.school) return; // 학생은 졸업할 때까지 미룬다
  if (age >= 27.8) { enlist(world, a, true); return; }
  if (a.armyAsk > world.clock) return;
  if (a.owner) {
    if (!a.ask && consult(world, a, 'army', {})) { a.armyAsk = Math.round(world.clock + 30 * DAY); return; }
  }
  if (chance(age < 20 ? 0.002 : 0.006)) enlist(world, a);
}

/** 군 복무 중 하루: 급여, 휴가, 편지 */
export function armyDaily(world, a, c) {
  const pay = refMonth(c?.era ?? 6) * 0.45 / 30;
  if (c && c.treasury > pay * 30) { c.treasury -= pay; a.money += pay; book(world, a, 'inc', '군 복무 급여', pay); }
  a.health = clamp(a.health + 0.05);
  const served = (world.clock - a.army.since) / DAY;
  if (chance(0.012)) {
    const ev = pick([
      ['첫 휴가를 나와 집밥을 먹었다', '🏠', 0.8], ['혹한기 훈련을 버텼다', '🥶', -0.1], ['선임에게 크게 혼났다', '😣', -0.4],
      ['동기들과 몰래 라면을 끓여 먹었다', '🍜', 0.6], ['사격에서 만발을 쐈다', '🎯', 0.7], ['일병으로 진급했다', '🎖️', 0.5],
      ['위문편지를 받았다', '✉️', 0.5], ['야간 경계 근무 중 별을 봤다', '🌌', 0.3],
    ]);
    remember(world, a, ev[0], 4, ev[2], [], 'life');
  }
  const pt = a.partner && world.anns.get(a.partner);
  if (pt?.alive && chance(0.03)) {
    const r = a.rels[pt.id], rp = pt.rels[a.id];
    if (r) r.last = Math.round(world.clock);
    if (rp) rp.last = Math.round(world.clock);
    remember(world, a, `${pt.name}에게서 편지가 왔다`, 4, 0.7, [pt.id], 'love');
  }
  if (served > 300 && !a.army.half) { a.army.half = 1; think(world, a, '반 넘게 왔다. 전역이 보인다!'); }
}

/** 전역 (여행 끝 처리에서 부른다) */
export function discharge(world, a) {
  const branch = a.army?.branch || '군';
  a.army = null;
  a.served = true;
  lifeEvent(world, a, `${branch} 만기 전역`, '🎖️');
  remember(world, a, `${branch}에서 무사히 전역했다`, 8, 0.8, [], 'life');
  think(world, a, pick(['민간인이다! 하고 싶은 게 너무 많다.', '군대에서 배운 것도 많았다.', '이제 진짜 내 인생을 살자.']));
  feel(a, 22, '해방감', '전역');
  for (const id of a.parents) { const p = world.anns.get(id); if (p?.alive) remember(world, p, `${ga(a.name)} 무사히 전역했다`, 6, 0.8, [a.id], 'family'); }
}
