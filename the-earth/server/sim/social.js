// 관계와 상호작용: 대화, 뒷담화(믿음의 전파), 다툼, 위로, 고백, 청혼, 결혼
import { clamp, rand, pick, chance, gauss, pairHash, ageOf, ga, neun, reul, wa, iya, callName } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { GOALS, JOBS } from './data.js';

const TRAITS4 = ['O', 'C', 'E', 'A'];

/** a가 b를 바라보는 관계 (없으면 만든다) */
export function rel(a, b) {
  let r = a.rels[b.id];
  if (!r) {
    let diff = 0;
    for (const k of TRAITS4) diff += Math.abs(a.tr[k] - b.tr[k]);
    const sim = 1 - diff / 400; // 0..1
    r = a.rels[b.id] = { f: 0, a: 0, t: 25, r: 0, c: Math.round((sim * 2 - 1) * 100) / 100, k: 'stranger', n: 0, last: 0 };
  }
  return r;
}
export const relOf = (a, id) => a.rels[id];

export function isFamily(a, b) {
  return a.parents.includes(b.id) || b.parents.includes(a.id) || (a.parents.length && a.parents.some(p => b.parents.includes(p)));
}

export function relKind(a, b, r) {
  if (a.spouse === b.id) return 'spouse';
  if (a.partner === b.id) return 'partner';
  if (a.parents.includes(b.id)) return 'parent';
  if (b.parents.includes(a.id)) return 'child';
  if (a.parents.length && a.parents.some(p => b.parents.includes(p))) return 'sibling';
  if (r.ex) return 'ex';
  if (r.a <= -55) return 'enemy';
  if (r.a <= -25) return 'rival';
  if (r.r >= 45) return 'crush';
  if (r.a >= 65 && r.f >= 55) return 'best';
  if (r.a >= 30 && r.f >= 22) return 'friend';
  if (r.f >= 4) return 'acquaintance';
  return 'stranger';
}

export function updateKind(world, a, b) {
  const r = rel(a, b);
  const before = r.k;
  r.k = relKind(a, b, r);
  if (before !== r.k) {
    r.fl = r.fl || {};
    if (r.fl[r.k]) return r;
    if (['friend', 'best', 'rival', 'enemy'].includes(r.k)) r.fl[r.k] = 1;
    if (r.k === 'friend' && before === 'acquaintance') {
      remember(world, a, `${wa(b.name)} 친구가 되었다`, 5, 0.6, [b.id], 'bond');
      think(world, a, `${callName(b.name, a.culture)}… 이제 친구라고 불러도 되겠지?`);
    } else if (r.k === 'best') {
      remember(world, a, `${neun(b.name)} 이제 내 가장 친한 친구다`, 7, 0.8, [b.id], 'bond');
    } else if (r.k === 'rival' && before !== 'enemy') {
      remember(world, a, `${wa(b.name)} 나는 도저히 맞지 않는다`, 5, -0.5, [b.id], 'bond');
    } else if (r.k === 'enemy') {
      remember(world, a, `${b.name}만큼은 용서할 수 없다`, 6, -0.8, [b.id], 'bond');
    }
  }
  return r;
}

/** 이성(또는 동성) 매력: a가 b에게 느끼는 끌림 0..100 */
export function attraction(world, a, b) {
  const ageA = ageOf(a, world.clock), ageB = ageOf(b, world.clock);
  if (ageA < 17 || ageB < 17) return 0;
  if (isFamily(a, b)) return 0;
  const sameSex = a.gender === b.gender;
  if (a.orient === 'straight' && sameSex) return 0;
  if (a.orient === 'gay' && !sameSex) return 0;
  const gap = Math.abs(ageA - ageB);
  const r = rel(a, b);
  const chem = pairHash(a.id * 7 + 1, b.id * 7 + 1) * 45; // 서로에게만 느끼는 '케미'
  return clamp(b.ab.cha * 0.38 + b.ab.ath * 0.08 + chem + r.c * 15 - Math.max(0, gap - 4) * 3.5 - (b.spouse ? 20 : 0));
}

// ── 대화 주제 ──
function recentStory(a, now) {
  const recent = a.mem.filter(m => now - m.t < 1440 * 5 && m.imp >= 5 && m.k !== 'talk');
  return recent.length ? recent[recent.length - 1] : null;
}

function mutualAcquaintance(world, a, b) {
  const cands = [];
  for (const id in a.rels) {
    if (+id === b.id) continue;
    const ra = a.rels[id];
    const rb = b.rels[id];
    if (ra.f > 8 && rb && rb.f > 3 && Math.abs(ra.a) > 20) {
      const c = world.anns.get(+id);
      if (c) cands.push(c);
    }
  }
  return cands.length ? pick(cands) : null;
}

/**
 * 두 앤의 대화 한 번. kind: chat / date / confess / propose / comfort / play / family
 * 반환: 대화를 묘사하는 문장
 */
export function converse(world, a, b, kind = 'chat') {
  const now = world.clock;
  const ra = rel(a, b), rb = rel(b, a);
  const first = ra.n === 0;
  ra.n++; rb.n++;
  ra.last = rb.last = Math.round(now);
  a.day.talks++; b.day.talks++;
  a.nd.social = clamp(a.nd.social + 22 + a.tr.E * 0.1);
  b.nd.social = clamp(b.nd.social + 22 + b.tr.E * 0.1);

  if (kind === 'confess') return confess(world, a, b);
  if (kind === 'propose') return propose(world, a, b);

  // 대화의 질: 성격 궁합, 친화성, 기분, 매력, 신경성, 운
  const q = ra.c * 22 + (a.tr.A + b.tr.A - 100) * 0.18 + (a.mood + b.mood) * 0.08 + (a.ab.cha + b.ab.cha - 100) * 0.1
    - (a.tr.N + b.tr.N - 100) * 0.1 + (ra.a + rb.a) * 0.08 + gauss() * 16 + (kind === 'date' ? 8 : 0) + (kind === 'family' ? 10 : 0);
  let topic, line;
  const story = recentStory(a, now);
  const other = mutualAcquaintance(world, a, b);
  const r = rand();

  if (kind === 'comfort' || (b.stress > 55 && a.tr.A > 50 && r < 0.5)) {
    // 고민 상담: 스트레스가 많은 b를 a가 위로
    topic = '고민';
    const heal = 10 + a.tr.A * 0.2 + rb.a * 0.1;
    b.stress = clamp(b.stress - heal);
    rb.a = clamp(rb.a + 4 + a.tr.A * 0.05, -100, 100); rb.t = clamp(rb.t + 5);
    ra.a = clamp(ra.a + 2, -100, 100);
    line = `${neun(b.name)} ${a.name}에게 고민을 털어놓았고, ${neun(a.name)} 진심으로 들어주었다`;
    remember(world, b, `힘들 때 ${ga(a.name)} 내 이야기를 들어주었다`, 5, 0.7, [a.id], 'talk');
    remember(world, a, `${b.name}의 고민을 들어주었다`, 3, 0.3, [b.id], 'talk');
    think(world, b, `${callName(a.name, b.culture)} 덕분에 마음이 조금 가벼워졌어.`);
    feel(b, 10, '위안', a.name);
  } else if (other && r < 0.28) {
    // 뒷담화: a의 c에 대한 의견이 b에게 전파된다 (b의 a에 대한 신뢰만큼)
    topic = '소문';
    const opA = a.rels[other.id].a;
    const rbc = rel(b, other);
    const shift = (opA - rbc.a) * 0.12 * (rb.t / 100) * (1 - b.tr.O / 250);
    rbc.a = clamp(rbc.a + shift, -100, 100);
    updateKind(world, b, other);
    const tone = opA > 20 ? `칭찬` : opA < -20 ? `험담` : `이야기`;
    line = `${neun(a.name)} ${b.name}에게 ${other.name}에 대한 ${reul(tone)} 늘어놓았다`;
    remember(world, b, `${ga(a.name)} ${other.name}에 대해 ${opA > 20 ? '좋게' : opA < -20 ? '나쁘게' : '이런저런'} 말했다`, 3, 0, [a.id, other.id], 'gossip');
    if (Math.abs(shift) > 2) think(world, b, opA > 0 ? `${neun(other.name)} 그런 사람이었구나. 다시 봐야겠어.` : `${other.name}… 조심해야겠다.`);
  } else if (story && r < 0.5) {
    topic = '근황';
    line = `${neun(a.name)} ${b.name}에게 최근 일을 이야기했다: "${story.text}"`;
    rb.f = clamp(rb.f + 2);
  } else if (kind === 'date') {
    topic = pick(['산책', '노을 구경', '별 보기', '맛있는 음식', '미래 이야기']);
    line = `${wa(a.name)} ${neun(b.name)} 함께 ${topic}을(를) 하며 시간을 보냈다`;
  } else if (kind === 'family') {
    topic = '가족';
    line = `${neun(a.name)} 가족 ${wa(b.name)} 오붓한 시간을 보냈다`;
  } else if (kind === 'play') {
    topic = '놀이';
    line = `${wa(a.name)} ${neun(b.name)} ${pick(['술래잡기', '공놀이', '흙장난', '숨바꼭질', '그림 그리기'])}를 하며 놀았다`;
  } else {
    const g = a.goals.find(x => !x.done);
    const opts = [
      ['일', a.job ? `${neun(a.name)} ${b.name}에게 ${JOBS[a.job.id]?.name || '일'} 일에 대해 이야기했다` : null],
      ['꿈', g ? `${neun(a.name)} ${b.name}에게 '${g.text}'라는 꿈을 털어놓았다` : null],
      ['농담', `${wa(a.name)} ${neun(b.name)} 실없는 농담을 주고받으며 웃었다`],
      ['날씨', `${wa(a.name)} ${neun(b.name)} 날씨와 먹을거리 이야기를 나눴다`],
      ['세상', `${wa(a.name)} ${neun(b.name)} 나라의 앞날에 대해 토론했다`],
      ['취미', `${wa(a.name)} ${neun(b.name)} 서로의 취미에 대해 이야기했다`],
    ].filter(x => x[1]);
    [topic, line] = pick(opts);
    if (topic === '꿈') { rb.f = clamp(rb.f + 3); rb.t = clamp(rb.t + 2); }
  }

  // 관계 변화
  const famGain = first ? 6 : 2.5 + rand() * 3;
  ra.f = clamp(ra.f + famGain); rb.f = clamp(rb.f + famGain);
  let quarrel = false;
  if (q > 12) {
    const g = q * 0.14;
    ra.a = clamp(ra.a + g * (0.7 + a.tr.A / 200), -100, 100);
    rb.a = clamp(rb.a + g * (0.7 + b.tr.A / 200), -100, 100);
    ra.t = clamp(ra.t + 1.5); rb.t = clamp(rb.t + 1.5);
    a.nd.fun = clamp(a.nd.fun + 8); b.nd.fun = clamp(b.nd.fun + 8);
  } else if (q < -18 && chance(0.35 + (a.tr.N + b.tr.N) / 400 - (a.tr.A + b.tr.A) / 500)) {
    quarrel = true;
    const loss = 6 + Math.abs(q) * 0.15;
    ra.a = clamp(ra.a - loss, -100, 100); rb.a = clamp(rb.a - loss, -100, 100);
    ra.t = clamp(ra.t - 4); rb.t = clamp(rb.t - 4);
    line = `${wa(a.name)} ${neun(b.name)} ${topic} 이야기를 하다가 크게 다퉜다`;
    feel(a, -10, '분노', b.name); feel(b, -10, a.tr.N > 60 ? '서운함' : '분노', a.name);
    remember(world, a, `${wa(b.name)} 말다툼을 했다`, 4, -0.6, [b.id], 'talk');
    remember(world, b, `${wa(a.name)} 말다툼을 했다`, 4, -0.6, [a.id], 'talk');
    think(world, a, pick([`${neun(b.name)} 왜 항상 저런 식이지?`, `내가 너무 심했나…`, `다시는 ${wa(b.name)} 그 얘기 안 할 거야.`]));
  } else {
    ra.a = clamp(ra.a + q * 0.05, -100, 100); rb.a = clamp(rb.a + q * 0.05, -100, 100);
  }

  // 설렘: 끌림이 크고 대화가 좋으면 로맨스가 자란다
  if (!quarrel) for (const [x, y, rx] of [[a, b, ra], [b, a, rb]]) {
    if (x.spouse && x.spouse !== y.id) continue;
    const att = attraction(world, x, y);
    if (att > 52 && q > 0) {
      const before = rx.r;
      rx.r = clamp(rx.r + (att - 45) * 0.12 + (kind === 'date' ? 6 : 0));
      if (before < 30 && rx.r >= 30) {
        remember(world, x, `${y.name}에게 설레는 마음이 생겼다`, 6, 0.6, [y.id], 'love');
        think(world, x, pick([`${callName(y.name, x.culture)} 생각이 자꾸 난다…`, `왜 ${y.name} 앞에서만 말이 꼬이지?`, `${wa(y.name)} 또 이야기하고 싶다.`]));
        feel(x, 8, '설렘', y.name);
      }
    }
  }

  if (first) {
    remember(world, a, `${wa(b.name)} 처음 이야기를 나눴다`, 3, q > 0 ? 0.4 : -0.1, [b.id], 'meet');
    remember(world, b, `${wa(a.name)} 처음 이야기를 나눴다`, 3, q > 0 ? 0.4 : -0.1, [a.id], 'meet');
    think(world, a, q > 10 ? `${neun(b.name)} 꽤 괜찮은 사람 같아.` : q < -10 ? `${neun(b.name)} 나랑은 좀 안 맞는 것 같다.` : `${b.name}… 아직은 잘 모르겠다.`);
  } else if (!quarrel && q > 25 && chance(0.4)) {
    remember(world, a, `${wa(b.name)} 즐거운 대화를 나눴다`, 2, 0.4, [b.id], 'talk');
    remember(world, b, `${wa(a.name)} 즐거운 대화를 나눴다`, 2, 0.4, [a.id], 'talk');
  }
  if (!quarrel) { feel(a, q > 0 ? 3 + q * 0.08 : q * 0.05); feel(b, q > 0 ? 3 + q * 0.08 : q * 0.05); }
  a.rep = clamp(a.rep + (q > 0 ? 0.15 : -0.1), -100, 100);
  b.rep = clamp(b.rep + (q > 0 ? 0.15 : -0.1), -100, 100);
  updateKind(world, a, b); updateKind(world, b, a);
  return { topic, line, quarrel };
}

function confess(world, a, b) {
  const ra = rel(a, b), rb = rel(b, a);
  ra.lastConf = Math.round(world.clock);
  if (a.plan?.k === 'confess' && a.plan.b === b.id) a.plan = null;
  const free = !b.partner && !b.spouse;
  // 고백받은 앤이 플레이어의 앤이면, 받아 줄지 마음속 목소리에게 묻는다
  if (free && b.owner && world.consult(b, 'confessed', { by: a.id })) {
    remember(world, a, `${b.name}에게 마음을 고백했다. 대답을 기다리고 있다`, 7, 0.2, [b.id], 'love');
    think(world, a, '대답이 언제 올까… 하루가 일 년 같다.');
    feel(a, 6, '두근거림', b.name);
    return { topic: '고백', line: `${neun(a.name)} ${b.name}에게 마음을 고백했고, ${neun(b.name)} 조금 생각할 시간을 달라고 했다`, quarrel: false };
  }
  const yes = free && (rb.r > 32 || (rb.a > 55 && attraction(world, b, a) > 55 && chance(0.5)));
  if (yes) {
    confessYes(world, a, b);
    return { topic: '고백', line: `${neun(a.name)} ${b.name}에게 마음을 고백했고, ${neun(b.name)} 웃으며 받아들였다`, quarrel: false };
  }
  confessNo(world, a, b);
  return { topic: '고백', line: `${neun(a.name)} ${b.name}에게 고백했지만 정중히 거절당했다`, quarrel: false };
}

/** 고백이 받아들여졌다: a가 고백한 사람, b가 받아 준 사람 */
export function confessYes(world, a, b) {
  const ra = rel(a, b), rb = rel(b, a);
  a.partner = b.id; b.partner = a.id;
  ra.r = clamp(ra.r + 15); rb.r = clamp(rb.r + 20);
  ra.since = rb.since = Math.round(world.clock);
  delete ra.metP; delete rb.metP;
  for (const [x, y] of [[a, b], [b, a]]) {
    remember(world, x, `${wa(y.name)} 연인이 되었다`, 8, 0.9, [y.id], 'love');
    lifeEvent(world, x, `${wa(y.name)} 연인이 되었다`, '💕');
    feel(x, 25, '설렘', y.name);
    updateKind(world, x, y);
  }
  think(world, a, `${ga(b.name)} 받아줬다! 꿈만 같아.`);
  think(world, b, `사실 나도 ${ga(callName(a.name, b.culture))} 좋았어.`);
  world.news('love', `💕 ${wa(a.name)} ${ga(b.name)} 연인이 되었다`, { anns: [a.id, b.id], country: a.country }, 1);
  world.first(world.countries[a.country], 'couple', `첫 연인 — ${wa(a.name)} ${b.name}`, '💕');
  world.dirtyAnnMeta.add(a.id); world.dirtyAnnMeta.add(b.id);
}

/** 고백이 거절되었다 */
export function confessNo(world, a, b) {
  const ra = rel(a, b);
  ra.rej = (ra.rej || 0) + 1; ra.lastConf = Math.round(world.clock);
  ra.r = clamp(ra.r - 18 - ra.rej * 10); ra.a = clamp(ra.a - 3, -100, 100);
  a.conf = clamp(a.conf - 8);
  remember(world, a, `${b.name}에게 고백했지만 거절당했다`, 7, -0.8, [b.id], 'love');
  remember(world, b, `${ga(a.name)} 내게 고백했지만 받아줄 수 없었다`, 5, -0.2, [a.id], 'love');
  feel(a, -22, '상심', b.name);
  think(world, a, pick(['괜히 말했나… 바보 같아.', `${neun(b.name)} 나를 그렇게 생각하지 않았구나.`, '시간이 지나면 괜찮아지겠지.']));
  think(world, b, `${callName(a.name, b.culture)}에게 미안하다. 하지만 마음은 어쩔 수 없잖아.`);
  updateKind(world, a, b);
}

function propose(world, a, b) {
  const ra = rel(a, b), rb = rel(b, a);
  const ring = a.plan?.k === 'propose' && a.plan.ring;
  if (a.plan?.k === 'propose') a.plan = null;
  if (b.partner === a.id && b.owner && world.consult(b, 'proposed', { by: a.id, ring: ring ? 1 : 0 })) {
    remember(world, a, `${b.name}에게 청혼했다. 대답을 기다린다`, 8, 0.3, [b.id], 'love');
    think(world, a, '제발… 제발 "응"이라고 해 줘.');
    return { topic: '청혼', line: `${neun(a.name)} ${b.name}에게 청혼했고, ${neun(b.name)} 놀란 얼굴로 대답을 고민하고 있다`, quarrel: false };
  }
  const yes = b.partner === a.id && rb.r > 45 && rb.a > 35 && chance(0.55 + rb.r / 300 + b.val.family / 400 + (ring ? 0.1 : 0));
  if (yes) {
    world.engage(a, b);
    return { topic: '청혼', line: `${neun(a.name)} ${b.name}에게 청혼했고, ${neun(b.name)} 눈물을 흘리며 승낙했다`, quarrel: false };
  }
  proposeNo(world, a, b);
  return { topic: '청혼', line: `${neun(a.name)} ${b.name}에게 청혼했지만 ${neun(b.name)} 조금 더 생각해 보겠다고 했다`, quarrel: false };
}

/** 청혼이 받아들여지지 않았다 (hard: 분명히 거절) */
export function proposeNo(world, a, b, hard = false) {
  const ra = rel(a, b);
  ra.r = clamp(ra.r - (hard ? 15 : 6));
  ra.propWait = Math.round(world.clock + (hard ? 120 : 45) * 1440);
  if (hard) {
    remember(world, a, `${b.name}에게 청혼했지만 거절당했다`, 8, -0.8, [b.id], 'love');
    remember(world, b, `${ga(a.name)} 청혼했지만 받아들일 수 없었다`, 6, -0.3, [a.id], 'love');
    feel(a, -25, '상심', b.name);
    think(world, a, pick(['우리 관계는 뭐였을까.', '내가 너무 앞서갔나.']));
    if (chance(0.4)) breakUp(world, a, b, false);
    return;
  }
  remember(world, a, `${b.name}에게 청혼했지만 아직은 이르다는 대답을 들었다`, 6, -0.5, [b.id], 'love');
  remember(world, b, `${ga(a.name)} 청혼했지만 아직 마음의 준비가 되지 않았다`, 5, 0, [a.id], 'love');
  feel(a, -12, '실망', b.name);
  think(world, a, '조금 더 기다려야 하나…');
}

export function marry(world, a, b, quiet = false) {
  a.spouse = b.id; b.spouse = a.id;
  a.engaged = b.engaged = null;
  a.partner = b.id; b.partner = a.id;
  for (const [x, y] of [[a, b], [b, a]]) {
    const r = rel(x, y);
    r.r = clamp(r.r + 15); r.a = clamp(r.a + 10, -100, 100); r.t = clamp(r.t + 15);
    remember(world, x, `${wa(y.name)} 결혼했다`, 10, 1, [y.id], 'love');
    if (!quiet) lifeEvent(world, x, `${wa(y.name)} 결혼`, '💍');
    feel(x, 35, '행복', y.name);
    updateKind(world, x, y);
  }
  think(world, a, `오늘부터 ${neun(b.name)} 내 가족이다.`);
  think(world, b, `${wa(callName(a.name, b.culture))} 함께라면 뭐든 할 수 있을 것 같아.`);
  // 한 집으로 합친다
  const ha = a.home && world.buildings.get(a.home), hb = b.home && world.buildings.get(b.home);
  const target = ha && hb ? (hb.h * hb.w > ha.h * ha.w ? hb : ha) : ha || hb;
  if (target) { world.moveHome(a, target); world.moveHome(b, target); }
  if (!quiet) {
    world.news('love', `💍 ${wa(a.name)} ${ga(b.name)} 결혼했다`, { anns: [a.id, b.id], country: a.country }, 2);
    world.first(world.countries[a.country], 'wedding', `첫 결혼식 — ${wa(a.name)} ${b.name}`, '💍');
  }
  world.dirtyAnnMeta.add(a.id); world.dirtyAnnMeta.add(b.id);
  // 친구들도 기뻐한다
  for (const x of [a, b]) for (const id in x.rels) {
    const r = x.rels[id];
    if (r.k === 'friend' || r.k === 'best' || r.k === 'parent' || r.k === 'sibling') {
      const f = world.anns.get(+id);
      if (f && f.alive) remember(world, f, `${ga(x.name)} 결혼했다`, 3, 0.4, [x.id], 'news');
    }
  }
}

export function breakUp(world, a, b, divorce = false) {
  const broke = a.engaged?.to === b.id;
  const lived = !divorce && a.cohab && b.cohab;
  a.partner = null; b.partner = null;
  a.engaged = b.engaged = null; a.cohab = b.cohab = false;
  if (a.plan?.b === b.id) a.plan = null;
  if (b.plan?.b === a.id) b.plan = null;
  if (divorce) { a.spouse = null; b.spouse = null; }
  if (broke) for (const x of [a, b]) lifeEvent(world, x, '파혼', '💔');
  if (lived) { const leaver = a.money >= b.money ? b : a; if (leaver.home) world.leaveHome(leaver); }
  for (const [x, y] of [[a, b], [b, a]]) {
    const r = rel(x, y);
    r.r = clamp(r.r * 0.3); r.ex = true; r.a = clamp(r.a - 15, -100, 100);
    remember(world, x, divorce ? `${wa(y.name)} 이혼했다` : `${wa(y.name)} 헤어졌다`, divorce ? 9 : 7, -0.8, [y.id], 'love');
    lifeEvent(world, x, divorce ? `${wa(y.name)} 이혼` : `${wa(y.name)} 이별`, '💔');
    feel(x, -25, '상심', y.name);
    updateKind(world, x, y);
  }
  think(world, a, pick(['우리는 처음부터 맞지 않았던 걸까.', '이제 혼자다… 오히려 홀가분하기도 하다.', '그래도 좋았던 날들은 잊지 못하겠지.']));
  if (divorce) {
    // 한 사람은 집을 떠난다
    const leaver = a.money >= b.money ? b : a;
    if (leaver.home) world.leaveHome(leaver);
    world.news('love', `💔 ${wa(a.name)} ${ga(b.name)} 이혼했다`, { anns: [a.id, b.id], country: a.country }, 1);
  }
  world.dirtyAnnMeta.add(a.id); world.dirtyAnnMeta.add(b.id);
}

/** 매일 밤: 관계는 만나지 않으면 조금씩 옅어진다 */
export function decayRelations(world, a) {
  const now = world.clock;
  let n = 0;
  for (const id in a.rels) {
    const r = a.rels[id];
    n++;
    const days = (now - r.last) / 1440;
    if (days > 3) {
      // 오랜 우정은 쉽게 사라지지 않는다 (만난 횟수와 함께한 시간이 바닥을 만든다)
      const floorF = Math.min(35, r.n * 0.4);
      const floorA = r.fl?.best ? 40 : r.fl?.friend ? 25 : 10;
      if (r.f > floorF) r.f = clamp(r.f - 0.25);
      if (r.a > floorA) r.a -= 0.15;
      if (r.a < -10) r.a += 0.1; // 미움도 시간이 지나면 옅어진다
      if (r.r > 0 && a.partner !== +id) r.r = clamp(r.r - 0.4);
    }
    r.k = relKind(a, world.anns.get(+id) || { id: +id, parents: [] }, r);
  }
  if (n > 70) {
    // 거의 모르는 사람은 잊는다
    const keys = Object.keys(a.rels).filter(id => a.rels[id].f < 6 && Math.abs(a.rels[id].a) < 12 && a.rels[id].r < 5);
    for (const id of keys.slice(0, n - 60)) delete a.rels[id];
  }
}

/** 상대 앤에 대한 한 줄 평 (UI용) */
export function opinionText(r) {
  if (r.a >= 75) return '정말 소중한 사람';
  if (r.a >= 45) return '좋은 사람';
  if (r.a >= 15) return '괜찮은 사람';
  if (r.a > -15) return '그럭저럭';
  if (r.a > -45) return '불편한 사람';
  return '싫은 사람';
}

export function goalText(k) { return GOALS[k]?.text || k; }
