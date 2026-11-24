// 속삭임: 질문을 기다리지 않고 플레이어가 먼저 앤에게 한마디 건넨다 ("○○에게 말 걸어 봐", "노래 하나 지어 봐"…).
//  · 앤은 명령을 듣지 않는다 — 지금 몸과 마음(피곤함·배고픔·스트레스), 성격, 마음속 목소리를 믿는 정도(heed)에 따라
//    따르거나 마다한다. 화면에는 앤이 얼마나 내켜 하는지가 보인다 (내켜 함 · 그럭저럭 · 내키지 않음).
//  · 따르면 몇 시간 동안 그 일이 하고 싶어진다 (mind.js decide 의 점수에 더해짐). 급한 일(잠·밥·출근)이 먼저다.
//  · 끝나면 어떻게 됐는지 알려 준다: 사이가 가까워졌다 · 노래를 지었다 · 불 피우는 법을 알아냈다 · 산딸기 군락을 찾았다…
//    잘 풀리면 앤은 마음속 목소리를 더 믿게 된다.
//  · 속삭임은 하루(게임)에 세 번까지 — 여덟 시간마다 하나씩 다시 생긴다. 도전을 이루면 하나 더 (challenge.js).
import { clamp, chance, pick, rand, ageOf, neun, reul, wa, fixJosa, localHour } from './util.js';
import { remember, think, feel, ageStage } from './ann.js';
import { makeCreation } from './culture.js';
import { tribeOfA, nextTech } from './tribes.js';
import { siteFeatures } from './settlement.js';
import { addFood } from './food.js';
import { ERAS } from './data.js';

export const WH_MAX = 3;            // 저절로 차는 최대 개수
export const WH_REGEN = 480;        // 게임 8시간(현실 20분)마다 하나
export const WH_BONUS_MAX = 3;      // 도전으로 받은 덤은 따로 (최대 3)
const WAIT = 60 * 20;               // 따르기로 하면 이만큼(게임 20시간) 안에 해 본다

const DEF = {
  talk: { icon: '💬', types: ['social', 'talk', 'date', 'family'] },
  confess: { icon: '💕', types: ['social', 'talk', 'date', 'family'] },
  makeup: { icon: '🤝', types: ['social', 'talk', 'date', 'family'] },
  family: { icon: '🏡', types: ['social', 'family', 'talk'] },
  create: { icon: '🎵', types: ['art'] },
  think: { icon: '💡', types: ['research'] },
  work: { icon: '💼', types: ['work'] },
  forage: { icon: '🧺', types: ['forage'] },
  help: { icon: '🔨', types: ['build'] },
  rest: { icon: '☕', types: ['relax', 'sleep', 'reflect'] },
  explore: { icon: '🧭', types: ['wander'] },
  exercise: { icon: '💪', types: ['exercise'] },
  play: { icon: '🪀', types: ['play'] },
};
const PERSON = new Set(['talk', 'confess', 'makeup', 'family']);
const TYPE = { talk: 'social', confess: 'social', makeup: 'social', family: 'family', create: 'art', think: 'research', work: 'work', forage: 'forage', help: 'build', rest: 'relax', explore: 'wander', exercise: 'exercise', play: 'play' };

/** 남은 속삭임 (시간이 지난 만큼 채운다) */
export function whState(world, p) {
  const w = p.wh || (p.wh = { n: WH_MAX, at: Math.round(world.clock), bonus: 0, used: 0, ok: 0 });
  if (w.n >= WH_MAX) w.at = Math.round(world.clock);
  else {
    const k = Math.floor((world.clock - w.at) / WH_REGEN);
    if (k > 0) { w.n = Math.min(WH_MAX, w.n + k); w.at = w.n >= WH_MAX ? Math.round(world.clock) : w.at + k * WH_REGEN; }
  }
  return { n: w.n, bonus: w.bonus || 0, max: WH_MAX, next: w.n >= WH_MAX ? null : Math.round(w.at + WH_REGEN), used: w.used || 0, ok: w.ok || 0 };
}
function spend(world, p) {
  whState(world, p);
  const w = p.wh;
  if (w.n > 0) { if (w.n >= WH_MAX) w.at = Math.round(world.clock); w.n--; return true; }
  if (w.bonus > 0) { w.bonus--; return true; }
  return false;
}
/** 돌려주기 (기회가 없어 못 했을 때) */
function refund(p) { const w = p.wh; if (!w) return; if (w.n < WH_MAX) w.n++; else w.bonus = Math.min(WH_BONUS_MAX, (w.bonus || 0) + 1); }
export function giveWhisper(p, k = 1) { p.wh = p.wh || { n: WH_MAX, at: 0, bonus: 0 }; p.wh.bonus = Math.min(WH_BONUS_MAX, (p.wh.bonus || 0) + k); }

// ── 앤이 얼마나 내켜 하나 (0~100, 음수도) ──
function mood(a) {
  let m = 0;
  if (a.nd.energy < 20) m -= 35;
  if (a.nd.food < 15) m -= 22;
  if ((a.sick || 0) > 40) m -= 30;
  return m;
}
function lean(world, a, k, b) {
  const r = b ? a.rels[b.id] : null;
  const tr = a.tr, ab = a.ab;
  let v;
  switch (k) {
    case 'talk': v = 42 + (tr.E - 50) * 0.5 + (100 - a.nd.social) * 0.3 + (r ? r.a * 0.2 : -6); break;
    case 'confess': v = 18 + (a.conf || 40) * 0.45 + (r?.r || 0) * 0.35 - tr.N * 0.18; break;
    case 'makeup': v = 22 + tr.A * 0.45 + (r ? r.a * 0.35 : 0); break;
    case 'family': v = 40 + a.val.family * 0.4; break;
    case 'create': v = 22 + ab.cre * 0.5 + tr.O * 0.2 + (100 - a.nd.fun) * 0.1; break;
    case 'think': v = 18 + ab.int * 0.4 + tr.O * 0.3; break;
    case 'work': v = 28 + tr.C * 0.45 + (a.job?.sat ?? 50) * 0.15 - a.stress * 0.15; break;
    case 'forage': v = 38 + tr.C * 0.2 + tr.A * 0.2; break;
    case 'help': v = 28 + tr.A * 0.5; break;
    case 'rest': v = 32 + a.stress * 0.4 + (100 - a.nd.energy) * 0.3 - a.amb * 0.15; break;
    case 'explore': v = 26 + tr.O * 0.3 + a.val.adventure * 0.35; break;
    case 'exercise': v = 20 + ab.ath * 0.5 - (100 - a.nd.energy) * 0.25; break;
    case 'play': v = 68 + tr.E * 0.2; break;
    default: v = 40;
  }
  return Math.round(k === 'rest' ? v : v + mood(a));
}
const feelWord = v => (v >= 62 ? 'yes' : v >= 38 ? 'meh' : 'no');

// ── 지금 건넬 수 있는 말 ──
export function whisperOptions(world, a) {
  if (!a?.alive) return { opts: [], why: '앤이 세상을 떠났어요' };
  const age = ageOf(a, world.clock), stage = ageStage(age);
  if (stage === 'baby') return { opts: [], why: '아직 아기라서 말을 알아듣지 못해요' };
  if (a.travel) return { opts: [], why: '먼 길을 가는 중이라 지금은 들을 수 없어요' };
  const s = world.settlements.get(a.sid), era = world.countries[a.country]?.era ?? 0;
  const out = [];
  const add = (k, t, d, b = null) => out.push({ k, b: b?.id ?? null, t: fixJosa(t), d, icon: DEF[k].icon, lean: lean(world, a, k, b) });
  // 사람: 설레는 사람 · 친한 사람 · 사이 나쁜 사람 · 아직 모르는 사람
  const here = [];
  for (const id in a.rels) {
    const b = world.anns.get(+id);
    if (!b?.alive || b.sid !== a.sid || b.id === a.id || b.travel) continue;
    if (ageOf(b, world.clock) < 3) continue;
    here.push([b, a.rels[id]]);
  }
  const kid = age < 13;
  const sameAge = ([b]) => (ageOf(b, world.clock) < 13) === kid;
  const free = !a.partner && !a.spouse;
  const crush = here.filter(([b, r]) => sameAge([b]) && age >= 16 && ageOf(b, world.clock) >= 16 && Math.abs(ageOf(b, world.clock) - age) < 15 && r.r >= 30 && free && !b.partner && !b.spouse && a.partner !== b.id).sort((x, y) => y[1].r - x[1].r)[0];
  if (crush) {
    add('confess', `${b2(crush[0])}에게 마음을 전해 봐`, crush[1].r >= 50 ? '설레는 사람' : '자꾸 눈이 가는 사람', crush[0]);
  }
  if (a.partner) { const b = world.anns.get(a.partner); if (b?.alive && b.sid === a.sid) add('talk', `${b2(b)} 만나러 가`, '연인', b); }
  const friend = here.filter(([b, r]) => sameAge([b]) && r.a >= 25 && b.id !== a.partner && b.id !== a.spouse && !a.parents.includes(b.id) && !a.kids.includes(b.id) && b.id !== crush?.[0].id).sort((x, y) => (y[1].a + rand() * 20) - (x[1].a + rand() * 20))[0];
  if (friend) add('talk', `${b2(friend[0])}${kid ? '랑 놀아' : '에게 말 걸어 봐'}`, friend[1].k === 'best' ? '가장 친한 친구' : '친구', friend[0]);
  const foe = here.filter(([b, r]) => r.a <= -15 && sameAge([b])).sort((x, y) => x[1].a - y[1].a)[0];
  if (foe) add('makeup', `${wa(foe[0].name)} 화해해 봐`, foe[1].a <= -55 ? '원수 같은 사이' : '사이가 틀어진 사람', foe[0]);
  // 아직 말도 안 해 본 마을 사람 (또래)
  const pool = world.residents(a.sid).filter(b => b.id !== a.id && !a.rels[b.id] && !b.travel && ageOf(b, world.clock) >= 3 && (ageOf(b, world.clock) < 13) === kid);
  // 고른 사람이 몇 시간은 그대로 있게 (보는 사이에 바뀌지 않도록)
  if (pool.length) { const b = pool.sort((x, y) => x.id - y.id)[(a.id * 7 + Math.floor(world.clock / 180)) % pool.length]; add('talk', `${b2(b)}에게 먼저 인사해 봐`, '아직 모르는 사람', b); }
  // 가족
  const fam = [a.spouse, ...a.kids, ...a.parents].map(id => world.anns.get(id)).filter(b => b?.alive && b.sid === a.sid);
  if (fam.length && !kid) add('family', '오늘은 가족과 시간을 보내', '식구들과 오붓하게', pick(fam));
  else if (fam.length && kid) add('family', `${fam.find(b => a.parents.includes(b.id)) ? '엄마 아빠' : '식구들'}한테 가 봐`, '어리광 부리기', fam.find(b => a.parents.includes(b.id)) || fam[0]);
  // 혼자 하는 일
  if (kid) add('play', '친구들이랑 신나게 놀아', '아이들 놀이터에서');
  add('create', kid ? '새 놀이를 만들어 봐' : era === 0 ? '노래나 이야기를 하나 지어 봐' : '뭔가 하나 지어 봐 (노래·이야기·음식…)', '작품은 사람들 사이로 퍼진다');
  if (!kid) add('think', era <= 1 ? '궁리해 봐 — 새로운 걸 알아낼지도' : '궁리해 봐 — 나라의 앎이 늘지도', '똑똑하고 호기심 많을수록');
  if (!kid && era <= 1 && stage !== 'elder') add('forage', '먹을 것을 넉넉히 구해 와', `마을 식량 ${Math.round(s.food)}`);
  if (a.job && !kid && stage !== 'elder') add('work', '일에 힘써 봐', a.job ? '솜씨가 늘고 인정받는다' : '');
  const site = !kid && (world.communitySite(s));
  if (site) add('help', `${reul(site.name)} 짓는 일을 거들어`, '이웃들이 고마워한다');
  add('explore', kid ? '마을 밖을 살짝 둘러봐' : '마을 밖을 둘러봐', '무엇을 찾을지 모른다');
  if (!kid && stage !== 'elder') add('exercise', '몸을 단련해 봐', '건강·운동 솜씨');
  add('rest', '오늘은 푹 쉬어', a.stress > 50 ? `스트레스 ${Math.round(a.stress)}` : '마음을 가라앉힌다');
  for (const o of out) o.f = feelWord(o.lean);
  return { opts: out, why: null };
}
const b2 = b => b.name;

/** 속삭이기 → { ok, follow, line, text } */
export function whisper(world, p, a, k, bid) {
  if (!a || !a.alive || a.owner !== p.id) return { ok: false, error: '내 앤에게만 속삭일 수 있어요' };
  if (a.wh && a.wh.ph !== 'done') return { ok: false, error: '아직 지난 속삭임을 마음에 두고 있어요' };
  const { opts, why } = whisperOptions(world, a);
  if (why) return { ok: false, error: why };
  let o = opts.find(x => x.k === k && (x.b ?? null) === (bid ?? null)) || (PERSON.has(k) ? null : opts.find(x => x.k === k));
  // 목록이 바뀌었어도 그 사람이 아직 마을에 있으면 그대로 (말 걸기·가족)
  if (!o && PERSON.has(k) && bid != null && (k === 'talk' || k === 'family')) {
    const b = world.anns.get(bid);
    if (b?.alive && b.sid === a.sid && !b.travel && b.id !== a.id && ageOf(b, world.clock) >= 3) o = { k, b: b.id, t: fixJosa(k === 'family' ? '오늘은 가족과 시간을 보내' : `${b.name}에게 말 걸어 봐`), lean: lean(world, a, k, b) };
  }
  if (!o) return { ok: false, error: '그사이 상황이 바뀌었어요' };
  if (!spend(world, p)) return { ok: false, error: '속삭임을 다 썼어요. 조금 기다리면 다시 생겨요' };
  p.wh.used = (p.wh.used || 0) + 1;
  const b = o.b != null ? world.anns.get(o.b) : null;
  const heed = a.heed ?? 60;
  const pFollow = clamp(0.12 + heed / 200 + o.lean / 140, 0.1, 0.97);
  const follow = chance(pFollow);
  const now = world.clock;
  let line;
  if (follow) {
    // 자는 중이면 깨어나서
    const asleep = a.act?.type === 'sleep';
    a.wh = { k, b: b?.id ?? null, t: o.t, at: Math.round(now), until: Math.round(now + WAIT + (asleep ? 480 : 0)), ph: 'wait', tries: 0, base: snapshot(world, a, b), pid: p.id };
    line = followLine(a, k, b, asleep);
    // 밤이 깊었으면 내일 아침에
    const h = localHour(now, world.settlements.get(a.sid)?.lon || 0);
    if (!asleep && k !== 'rest' && (h >= 22 || h < 5)) { line += ' …오늘은 늦었으니 내일 아침에.'; a.wh.until += 300; }
    think(world, a, line);
    p.wh.ok = (p.wh.ok || 0) + 1;
    // 하던 한가한 일은 내려놓고 바로
    if (a.act && !asleep && ['relax', 'wander', 'reflect', 'play', 'watch', 'shop', 'exercise', 'art', 'research'].includes(a.act.type) && a.act.type !== TYPE[k]) { a.act = null; world.dirtyAct.add(a.id); }
  } else {
    line = refuseLine(a, k, b, o.lean);
    think(world, a, line);
    remember(world, a, `마음속 목소리가 '${o.t}' 하고 속삭였지만, 내키지 않았다`, 2, -0.1, b ? [b.id] : [], 'voice');
    p.whLast = { t: Math.round(now), k, t2: o.t, text: fixJosa(`마다했다 — "${line}"`), v: -1, ann: a.id, no: true };
  }
  world.dirtyDetail.add(a.id);
  const log = { t: Math.round(now), k, t2: o.t, follow, line: fixJosa(line), ann: a.id };
  p.whLog = [...(p.whLog || []), log].slice(-20);
  return { ok: true, follow, line: fixJosa(line), text: o.t, state: whState(world, p), active: activeOut(world, a) };
}

function snapshot(world, a, b) {
  const r = b ? a.rels[b.id] : null;
  return { ra: r?.a ?? 0, rr: r?.r ?? 0, rk: r?.k ?? null, stress: a.stress, fun: a.nd.fun, made: (a.made || []).length, partner: a.partner ?? null, food: world.settlements.get(a.sid)?.food ?? 0, disc: (a.discoveries || []).length, ath: a.sk.athletics || 0, rep: a.rep };
}

function followLine(a, k, b, asleep) {
  const n = b?.name;
  const L = {
    talk: [`그래, ${n}에게 가 볼까.`, `${n} 요즘 어떻게 지내나 궁금하네.`, `${n}… 한번 말 걸어 볼까.`],
    confess: [`…그래. 용기를 내 보자. ${n}에게 말할 거야.`, '심장이 쿵쾅거린다. 오늘은 꼭.'],
    makeup: [`${wa(n)} 이대로 지낼 수는 없지. 먼저 손을 내밀자.`, '자존심은 잠깐 접어 두자.'],
    family: ['맞다, 요즘 식구들 얼굴을 제대로 못 봤다.', '오늘은 집에 일찍 가야지.'],
    create: ['머릿속에 뭔가 맴돈다. 붙잡아 보자.', '그래, 하나 지어 볼까.'],
    think: ['저건 왜 그럴까… 오늘은 끝까지 궁리해 보자.', '뭔가 알아낼 수 있을 것 같은 기분이다.'],
    work: ['좋아, 오늘은 제대로 해 보자.', '맡은 일은 확실히.'],
    forage: ['다들 배곯지 않게 넉넉히 구해 오자.', '숲에 가 볼까. 오늘은 많이 따 와야지.'],
    help: ['이웃 일인데 거들어야지.', '손이 하나라도 더 있으면 낫겠지.'],
    rest: ['그래, 오늘은 좀 쉬자.', '잠깐 숨 돌리자.'],
    explore: ['마을 밖엔 뭐가 있을까?', '오늘은 못 가 본 데를 가 보자.'],
    exercise: ['몸 좀 움직여 볼까!', '땀 좀 흘려야겠다.'],
    play: ['놀자 놀자!', '오늘은 뭐 하고 놀지?'],
  }[k] || ['그래, 그렇게 해 볼까.'];
  return (asleep ? '(꿈결에) ' : '') + pick(L) + (asleep ? ' …일어나면.' : '');
}
function refuseLine(a, k, b, lv) {
  if (a.nd.energy < 20 && k !== 'rest') return pick(['지금은 너무 피곤해… 나중에.', '눈이 감긴다. 오늘은 무리야.']);
  if (a.nd.food < 15 && k !== 'forage') return pick(['배고파서 아무것도 못 하겠다.', '뭘 좀 먹고 나서…']);
  if ((a.sick || 0) > 40 && k !== 'rest') return '몸이 안 좋아. 지금은 못 해.';
  const n = b?.name;
  const L = {
    talk: a.tr.E < 40 ? [`${n}에게? …괜히 어색할 것 같아.`, '오늘은 그냥 혼자 있고 싶다.'] : [`${n}? 오늘은 별로…`],
    confess: [`${n}에게 고백을? 아직은 용기가 안 나.`, '거절당하면 어떡해… 아직은 아니야.'],
    makeup: [`내가 왜 먼저? ${n}${a.tr.A < 40 ? '이(가) 먼저 사과해야지.' : '도 아직 화가 나 있을 거야.'}`],
    family: ['오늘은 내 시간이 좀 필요해.'],
    create: ['아무것도 떠오르지 않는다. 억지로는 안 돼.'],
    think: ['머리 쓰는 건 오늘은 싫다.'],
    work: ['오늘은 일이 손에 안 잡힌다.'],
    forage: ['다른 사람들이 구해 오겠지.'],
    help: ['내 일도 바쁜데…'],
    rest: a.amb > 60 ? ['쉴 시간이 어딨어. 할 일이 산더미야.'] : ['쉬는 건 나중에.'],
    explore: ['밖은 위험해. 그냥 마을에 있을래.'],
    exercise: ['귀찮다. 오늘은 패스.'],
    play: ['오늘은 놀기 싫어…'],
  }[k] || ['오늘은 내 뜻대로 할래.'];
  return lv < 25 ? pick(L) : pick([...L, '마음속 목소리 말이 맞을 수도 있지만… 오늘은 내 뜻대로 할래.']);
}

/** 지금 마음에 둔 속삭임 (화면에 보여 줄) */
export function activeOut(world, a) {
  const w = a?.wh;
  if (!w || w.ph === 'done') return null;
  return { k: w.k, t: w.t, ph: w.ph, until: w.until, icon: DEF[w.k]?.icon || '💬' };
}

/** decide() 안에서: 마음에 둔 속삭임이 있으면 그 일이 더 하고 싶어진다 */
export function whisperBias(world, a, opts, add, ctx) {
  const w = a.wh;
  if (!w || w.ph === 'done') return;
  const p = world.players[w.pid];
  // 하다가 끊겼으면(상대가 바빠서 등) 다시 기다린다
  if (w.ph === 'doing') { w.ph = 'wait'; w.tries = (w.tries || 0) + 1; }
  if (world.clock > w.until || w.tries > 4) {
    a.wh = null;
    if (p) { refund(p); note(world, p, a, `속삭임 — ${w.t} · 그럴 틈이 없었다 (속삭임을 돌려받았어요)`, 0, '⏳'); }
    return;
  }
  const type = TYPE[w.k];
  const b = w.b != null ? world.anns.get(w.b) : null;
  if (w.b != null && (!b?.alive || b.sid !== a.sid || b.travel)) {
    if (!b?.alive || world.clock > w.until - 60) { a.wh = null; if (p) { refund(p); note(world, p, a, `속삭임 — ${w.t} · ${b?.alive ? `${neun(b.name)} 마을에 없었다` : '그럴 수 없게 되었다'} (돌려받았어요)`, 0, '⏳'); } }
    return;
  }
  const BOOST = 55;
  let o = null;
  if (type === 'social' || type === 'family') {
    // 상대가 자거나 일·학교에 있으면 그 사람이 한가해질 때까지 기다린다
    if (b?.act?.type === 'sleep' || (ctx?.isAvailable && !ctx.isAvailable(world, b))) return;
    o = opts.find(x => (x.type === 'social' || x.type === 'family') && x.target === b.id);
    const kind = w.k === 'confess' ? 'confess' : w.k === 'family' ? null : a.partner === b.id ? 'date' : 'chat';
    if (w.k === 'confess') a.plan = { k: 'confess', b: b.id, until: w.until };
    if (o) { if (kind) o.kind = kind; o.score += BOOST; }
    else {
      add(w.k === 'family' ? 'family' : 'social', 35 + BOOST, [], { target: b.id, kind: kind || undefined });
      o = opts[opts.length - 1];
    }
  } else {
    o = opts.find(x => x.type === type);
    if (type === 'build') {
      const s = world.settlements.get(a.sid);
      const site = world.communitySite(s);
      if (!site) { a.wh = null; if (p) { refund(p); note(world, p, a, `속삭임 — ${w.t} · 공사가 벌써 끝났다 (돌려받았어요)`, 0, '⏳'); } return; }
      if (o && o.site !== site.id) o = null;
      if (!o) { add('build', 30 + BOOST, [], { site: site.id }); o = opts[opts.length - 1]; } else o.score += BOOST;
    } else if (type === 'work') {
      if (!o) return;   // 근무 시간이 아니면 다음 근무 때
      o.score += 20;
    } else if (o) o.score += BOOST;
    else { add(type, 35 + BOOST, []); o = opts[opts.length - 1]; }
  }
  o.reasons = ['마음속 목소리의 속삭임', ...(o.reasons || [])];
  o.wh = 1;
}

/** decide() 가 속삭인 일을 골랐을 때 */
export function whisperChosen(world, a, o) {
  if (!o?.wh || !a.wh) return;
  a.wh.ph = 'doing';
  a.wh.t0 = Math.round(world.clock);
}

/** finishAct 맨 앞에서: 속삭인 일이 끝났으면 어떻게 됐는지 */
export function whisperEnd(world, a, act, interrupted) {
  const w = a.wh;
  if (!w || w.ph !== 'doing') return;
  if (!(DEF[w.k]?.types || []).includes(act.type)) return;
  // 사람을 찾아가는 중(social)에 끝났으면 → 거절당했거나 상대가 자리를 떴다. 대화(talk·date·family)는 이어서
  const p = world.players[w.pid];
  a.wh = null;
  if (!p) return;
  const r = outcome(world, a, w, act, interrupted);
  w.ph = 'done';
  // 잘 풀리면 마음속 목소리를 더 믿는다
  // (질문에 답하는 것보다 천천히 — 속삭임은 자주 하니까)
  const hd = a.heed ?? 60;
  a.heed = clamp(hd + (r.v > 0 ? 0.45 * r.v * (1 - hd / 100) * 2 : r.v < 0 ? -0.6 : 0), 15, Math.max(hd, 90));
  remember(world, a, `마음속 목소리를 따라 ${w.t.replace(/[.!]$/, '')} — ${r.text}`, r.v >= 2 ? 6 : 3, r.v > 0 ? 0.5 : r.v < 0 ? -0.3 : 0.1, w.b != null ? [w.b] : [], 'voice');
  note(world, p, a, `속삭임 — ${w.t} · ${r.text}`, r.v, r.icon || DEF[w.k].icon);
  p.whLast = { t: Math.round(world.clock), k: w.k, t2: w.t, text: fixJosa(r.text), v: r.v, ann: a.id };
  if (r.v > 0) p.whGood = (p.whGood || 0) + 1;
  world.dirtyDetail.add(a.id);
}

function note(world, p, a, text, v, icon) {
  world.pushNote(p, { t: Math.round(world.clock), ann: a.id, name: a.name, text: fixJosa(text), icon, lvl: v >= 2 ? 2 : 1, wh: true });
}

function outcome(world, a, w, act, interrupted) {
  const b = w.b != null ? world.anns.get(w.b) : null;
  const base = w.base || {};
  const s = world.settlements.get(a.sid);
  const c = world.countries[a.country];
  if (interrupted) return { v: 0, text: '하다가 그만둬야 했다' };
  switch (w.k) {
    case 'talk': case 'family': case 'makeup': case 'confess': {
      if (!b) return { v: 0, text: '만나지 못했다' };
      const r = a.rels[b.id] || { a: 0, r: 0 };
      if (act.type === 'social') return { v: -1, text: `${b.name}에게 말을 걸었지만 시큰둥했다`, icon: '😶' };
      const da = Math.round(r.a - base.ra);
      // 대화 한 줄 (예: "민지와 서준은 함께 노을 구경을 하며 시간을 보냈다")
      const said = act.line ? String(act.line).replace(/[.。]$/, '') : `${wa(b.name)} 이야기를 나눴다`;
      if (w.k === 'confess') {
        if (a.partner === b.id && base.partner !== b.id) return { v: 3, text: `${said} — 연인이 되었다!`, icon: '💕' };
        return { v: -1, text: act.line ? said : `마음을 전했지만 ${b.name}의 대답은 아직…`, icon: '💔' };
      }
      if (w.k === 'makeup') {
        if (r.a > -10 || da >= 8) { r.a = Math.max(r.a, -5); return { v: 2, text: `${wa(b.name)} 화해했다 — 앙금이 풀렸다`, icon: '🤝' }; }
        return { v: da > 0 ? 1 : -1, text: da > 0 ? `${wa(b.name)} 조금은 풀렸다 (사이 ${sign(da)})` : `${neun(b.name)} 아직 마음을 열지 않았다`, icon: da > 0 ? '🙂' : '😤' };
      }
      if (r.k !== base.rk && (r.k === 'friend' || r.k === 'best')) return { v: 2, text: `${said} — ${r.k === 'best' ? '가장 친한 친구가 되었다' : '친구가 되었다'}`, icon: '🫶' };
      if (da >= 1) return { v: 1, text: `${said} (사이 ${sign(da)})` };
      if (da <= -3) return { v: -1, text: `${said} (사이 ${sign(da)})`, icon: '😠' };
      return { v: 0, text: said };
    }
    case 'create': {
      const pk = clamp(0.3 + a.ab.cre / 220 + a.tr.O / 500 + (a.insp || 0) * 0.15, 0.25, 0.92);
      if (chance(pk)) {
        a.insp = 0;
        const cr = makeCreation(world, a);
        if (cr) return { v: 2, text: `${cr.kindName} '${cr.name}'을(를) 지었다!`, icon: cr.icon };
      }
      a.insp = Math.min(4, (a.insp || 0) + 1);
      return { v: 0, text: pick(['영감을 모았다 — 다음엔 뭔가 나올 것 같다', '흥얼거려 봤지만 아직 모양이 안 잡힌다 (영감 +1)', '이것저것 해 봤다. 거의 다 왔다 (영감 +1)']), icon: '✨' };
    }
    case 'think': {
      if (c.era <= 1 && c.tribes && !c.nation) {
        const t = tribeOfA(world, a);
        const known = t ? t.known || [] : c.discoveries;
        const d = nextTech(known, c.era);
        const pk = clamp(0.04 + (a.ab.int - 40) / 500 + a.tr.O / 900 + (a.idea || 0) * 0.04, 0.03, 0.3);
        if (d && chance(pk)) {
          a.idea = 0;
          world.tryDiscovery(a, d, { tribe: t });
          if ((a.discoveries || []).length > base.disc) return { v: 3, text: `'${d}'을(를) 알아냈다! 부족이 놀랐다`, icon: '💡' };
        }
        a.idea = Math.min(6, (a.idea || 0) + 1);
        return { v: 0, text: d ? `${d.replace(/의 사용$/, '')}에 대해 궁리했다 — 실마리가 보인다 (궁리 ${a.idea})` : '궁리했지만 아직은…', icon: '🤔' };
      }
      const pts = ERAS[c.era].wage * (1 + a.ab.int / 50);
      world.addTech(c, pts, 'research');
      a.sk.science = Math.min(100, (a.sk.science || 0) + 1.5);
      if (chance(0.06 + a.ab.int / 2000)) { world.tryDiscovery(a); if ((a.discoveries || []).length > base.disc) return { v: 3, text: `새로운 것을 발견했다 — '${a.discoveries.at(-1)}'!`, icon: '💡' }; }
      return { v: 1, text: '작은 깨달음을 얻었다 (나라의 앎이 조금 늘었다)', icon: '📖' };
    }
    case 'work': {
      if (a.job) a.job.sat = clamp(a.job.sat + 3);
      a.rep = clamp(a.rep + 1.5, -100, 100);
      if (chance(0.35)) { feel(a, 6, '뿌듯함', '일'); return { v: 1, text: '열심히 일했다 — 칭찬을 들었다', icon: '👍' }; }
      return { v: 1, text: '열심히 일했다 — 일이 손에 붙는다' };
    }
    case 'forage': {
      const amt = 1.5 + a.ab.ath / 60 + (a.sk.farming || 0) / 80;
      addFood(s, amt, { fruit: 0.4, veg: 0.3, meat: 0.3 });
      a.rep = clamp(a.rep + 1.5, -100, 100);
      return { v: 1, text: `먹을 것을 넉넉히 구해 왔다 (마을 식량 +${amt.toFixed(1)})`, icon: '🧺' };
    }
    case 'help': {
      a.rep = clamp(a.rep + 2.5, -100, 100);
      let n = 0;
      for (const x of world.residents(a.sid)) if (x.id !== a.id && x.rels?.[a.id] && n < 6) { x.rels[a.id].a = clamp(x.rels[a.id].a + 2, -100, 100); n++; }
      return { v: 1, text: `이웃 일을 거들었다 — 마을 사람들이 고마워한다`, icon: '🔨' };
    }
    case 'rest': {
      a.stress = clamp(a.stress - 8);
      const ds = Math.round(base.stress - a.stress);
      return { v: ds >= 5 ? 1 : 0, text: ds >= 1 ? `푹 쉬었다 — 마음이 한결 가볍다 (스트레스 −${ds})` : '쉬었다', icon: '☕' };
    }
    case 'explore': { const r = explore(world, a, s); if (r.v > 0) a.finds = (a.finds || 0) + 1; return r; }
    case 'exercise': {
      a.sk.athletics = Math.min(100, (a.sk.athletics || 0) + 2);
      a.health = clamp((a.health ?? 80) + 0.5);
      return { v: 1, text: `몸이 가벼워졌다 (운동 솜씨 ${Math.round(base.ath)}→${Math.round(a.sk.athletics)})`, icon: '💪' };
    }
    case 'play': {
      const df = Math.round(a.nd.fun - base.fun);
      return { v: 1, text: df > 0 ? `신나게 놀았다 (즐거움 +${df})` : '신나게 놀았다', icon: '🪀' };
    }
  }
  return { v: 0, text: '해 봤다' };
}
const sign = v => (v > 0 ? `+${v}` : `${v}`);

// 마을 밖에서 찾은 것
function explore(world, a, s) {
  const f = siteFeatures(s), sea = f.sea, river = f.river || f.lake;
  const kid = ageOf(a, world.clock) < 13;
  const pool = [
    { w: 3, f: () => { addFood(s, 2.5, 'fruit'); return { v: 1, text: `${pick(['산딸기 군락', '머루 덩굴', '도토리가 잔뜩 떨어진 참나무 숲', '버섯이 돋은 골짜기'])}을(를) 찾았다 (마을 식량 +2.5)`, icon: '🫐' }; } },
    { w: 2, f: () => { feel(a, 8, '설렘', '경치'); a.insp = Math.min(4, (a.insp || 0) + 1); return { v: 1, text: `${pick(['언덕 위에서 마을이 한눈에 보이는 곳', '노을이 유난히 붉게 지는 바위', '물안개가 피어오르는 늪', '들꽃이 가득한 빈터'])}을(를) 찾았다 — 영감이 떠오른다`, icon: '🌄' }; } },
    { w: 1.5, f: () => { remember(world, a, '맑은 샘을 찾았다', 5, 0.6, [], 'nature'); a.nd.food = clamp(a.nd.food + 5); return { v: 1, text: '맑은 샘을 찾았다 — 물맛이 달다', icon: '💧' }; } },
    { w: 1.2, f: () => { a.sk.athletics = Math.min(100, (a.sk.athletics || 0) + 1); return { v: 0, text: `${pick(['사슴', '멧돼지', '여우', '곰'])} 발자국을 따라가 봤다`, icon: '🐾' }; } },
    { w: kid ? 2 : 0.8, f: () => { remember(world, a, '반짝이는 돌을 주워 와 소중히 간직했다', 4, 0.6, [], 'nature'); return { v: 1, text: `${pick(['반짝이는 돌', '무늬가 고운 조개껍데기', '커다란 새 깃털', '매끈한 조약돌'])}을(를) 주웠다 — 보물로 간직한다`, icon: '💎' }; } },
    { w: sea || river ? 1.5 : 0, f: () => { addFood(s, 1.5, 'fish'); return { v: 1, text: `${sea ? '갯바위' : '여울'}에서 물고기 떼를 봤다 (마을 식량 +1.5)`, icon: '🐟' }; } },
    { w: 0.7, f: () => { a.health = clamp((a.health ?? 80) - 3); feel(a, -6, '놀람'); return { v: -1, text: pick(['비탈에서 미끄러져 무릎이 까졌다', '벌집을 건드려 쏘였다', '길을 잃었다가 해 질 녘에야 돌아왔다']), icon: '🩹' }; } },
    { w: 1, f: () => ({ v: 0, text: '별일은 없었지만 바람이 좋았다', icon: '🍃' }) },
  ];
  let r = rand() * pool.reduce((t, x) => t + x.w, 0);
  for (const x of pool) { r -= x.w; if (r <= 0) return x.f(); }
  return pool[pool.length - 1].f();
}

/** 속삭임 창에 보낼 것 */
export function whisperOut(world, p) {
  const a = world.mainAnn(p);
  const st = whState(world, p);
  const o = a ? whisperOptions(world, a) : { opts: [], why: '내 앤이 없어요' };
  return { ...st, ann: a ? { id: a.id, name: a.name, heed: Math.round(a.heed ?? 60) } : null, opts: o.opts, why: o.why, active: activeOut(world, a), last: p.whLast || null };
}
