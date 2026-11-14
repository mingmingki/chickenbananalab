// 앤이 묻는다 — 인생의 갈림길에서 앤은 '마음속 목소리'(플레이어)에게 묻는다.
//
// · 플레이어의 답은 명령이 아니라 조언이다. 앤은 대개 따르지만, 조언이 자기 마음과 크게 어긋나면
//   성격(친화성·야망)과 '마음의 소리를 믿는 정도'(heed)에 따라 자기 뜻대로 하기도 한다.
// · 정해진 시간 안에 답이 없으면 앤은 혼자 정한다. 앤은 언제나 스스로 사는 존재다.
// · 갈림길은 나이(어린이~노년)·성별·시대·가족·직업·관계·돈·건강에 따라 열리고,
//   사람 이름·직업·회사·금액·장소가 들어가 같은 갈림길도 앤마다 다른 질문이 된다.
//
// 두 종류가 있다.
//   1) 상황 갈림길: 앤이 원래 혼자 정하던 순간(고백·청혼·이직·창업·이주·은퇴·진학·이혼…)에 끼어든다.
//      묻지 못하면(질문함이 가득 찼거나 '묻지 않기') 예전처럼 앤이 바로 정한다.
//   2) 일상 갈림길: 하루에 한 번, 나이와 형편에 맞는 고민이 문득 생긴다.
import { clamp, rand, pick, chance, gauss, ageOf, DAY, YEAR, ga, neun, reul, wa, ro, callName, fixJosa } from './util.js';
import { remember, think, feel, lifeEvent, ageStage } from './ann.js';
import { rel, updateKind, attraction, converse } from './social.js';
import { JOBS, ERAS, INDUSTRIES, SCHOOLS, BUILDINGS } from './data.js';
import { openings, apply, leaveJob, jobTitle } from './economy.js';
import { book, refMonth, perUnit, ERA_MONEY } from './budget.js';

const SMALL_WAIT = 12 * 60;     // 일상 갈림길: 게임 12시간 (실제 30분)
const BIG_WAIT = 24 * 60;       // 큰 갈림길: 게임 하루 (실제 1시간)
const MAX_PENDING = 6;          // 한 플레이어가 동시에 받는 질문 수
const DAY_CAP = 5;              // 하루(게임)에 받는 질문 수 (큰 갈림길은 +3)
const LOG_CAP = 60;
export const ASK_MODES = ['all', 'big', 'off'];

// ── 그 시대의 돈으로 ──
export function fmtMoney(units, era) {
  const M = ERA_MONEY[clamp(era, 0, 7)];
  const x = Math.abs(units * perUnit(era));
  if (M.cur === '조개') return `조개 ${Math.max(1, Math.round(x))}개`;
  if (M.cur === '곡식') { const s = Math.floor(x / 100), d = Math.round(x % 100); return `${s ? `${s}섬 ` : ''}${d || !s ? `${d}되` : ''}`.trim(); }
  if (M.cur === '전') { const g = Math.floor(x / 1000), j = Math.round(x % 1000); return `${g ? `${g}관 ` : ''}${j || !g ? `${j}전` : ''}`.trim(); }
  if (M.cur === '냥') return x < 1 ? `${Math.max(1, Math.round(x * 10))}전` : `${Math.round(x)}냥`;
  if (x >= 1e8) return `${(x / 1e8).toFixed(x >= 1e9 ? 0 : 1)}억원`;
  if (x >= 1e4) return `${Math.round(x / 1e4).toLocaleString()}만원`;
  return `${(Math.round(x / 100) * 100).toLocaleString()}원`;
}

// ── 맥락 ──
function ctx(world, a) {
  const c = world.countries[a.country], s = world.settlements.get(a.sid);
  const now = world.clock, age = ageOf(a, now), era = c?.era ?? 0;
  const near = ids => ids.map(id => world.anns.get(id)).filter(b => b && b.alive && b.sid === a.sid && !b.travel);
  const rk = (b, k) => b && a.rels[b.id]?.k === k;
  const x = {
    w: world, a, c, s, now, age, era, stage: ageStage(age), M: refMonth(era), F: a.gender === 'F',
    fmt: u => fmtMoney(u, era),
    get: id => { const b = id != null ? world.anns.get(id) : null; return b && b.alive ? b : null; },
    here: id => { const b = x.get(id); return b && b.sid === a.sid && !b.travel ? b : null; },
    call: b => callName(b.name, a.culture),
    spend: (u, cat = '기타') => { const v = Math.max(0, Math.min(a.money, u)); a.money -= v; book(world, a, 'exp', cat, v); return v; },
    earn: (u, cat = '기타') => { a.money += u; book(world, a, 'inc', cat, u); },
    mem: (t, imp = 4, v = 0, who = []) => remember(world, a, t, imp, v, who, 'choice'),
    think: t => think(world, a, t),
    feel: (v, k, why = '') => feel(a, v, k, why),
    life: (t, i = '•') => lifeEvent(world, a, t, i),
    stress: d => { a.stress = clamp(a.stress + d); },
    fun: d => { a.nd.fun = clamp(a.nd.fun + d); },
    health: d => { a.health = clamp(a.health + d); },
    rep: d => { a.rep = clamp(a.rep + d, -100, 100); },
    sk: (k, d) => { if (a.sk[k] != null) a.sk[k] = clamp(a.sk[k] + d); },
    bond: (b, aff, fam = 3, back = 0.8) => {
      if (!b) return;
      const r = rel(a, b), rb = rel(b, a);
      r.a = clamp(r.a + aff, -100, 100); rb.a = clamp(rb.a + aff * back, -100, 100);
      r.f = clamp(r.f + fam); rb.f = clamp(rb.f + fam);
      r.last = rb.last = Math.round(now);
      updateKind(world, a, b); updateKind(world, b, a);
    },
    other: (b, t, imp = 3, v = 0) => b && remember(world, b, t, imp, v, [a.id], 'event'),
    otherThink: (b, t) => b && think(world, b, t),
    plan: (k, data = {}, days = 7) => { a.plan = { k, ...data, until: Math.round(now + days * DAY) }; },
    friends: () => near(Object.keys(a.rels).map(Number).filter(id => ['friend', 'best'].includes(a.rels[id].k))),
    friend: () => pick(x.friends()),
    best: () => x.friends().sort((p, q) => a.rels[q.id].a - a.rels[p.id].a)[0] || null,
    parents: () => near(a.parents),
    parent: () => pick(near(a.parents)),
    kids: () => near(a.kids),
    kid: () => pick(near(a.kids)),
    sibs: () => near(Object.keys(a.rels).map(Number).filter(id => a.rels[id].k === 'sibling')),
    sib: () => pick(x.sibs()),
    mate: () => x.here(a.spouse || a.partner),
    spouse: () => x.get(a.spouse),
    grandkids: () => near(a.kids.flatMap(id => world.anns.get(id)?.kids || [])),
    coworkers: () => { const co = a.job?.co != null && world.companies.get(a.job.co); return co ? near(co.emp.filter(id => id !== a.id)) : []; },
    coworker: () => pick(x.coworkers()),
    boss: () => { const co = a.job?.co != null && world.companies.get(a.job.co); const b = co && co.ceo !== a.id && x.here(co.ceo); return b || null; },
    neighbor: () => { const h = a.home && world.buildings.get(a.home); const res = world.residents(a.sid).filter(b => b.id !== a.id && b.home && b.home !== a.home && ageOf(b, now) >= 18 && h && Math.hypot((world.buildings.get(b.home)?.x ?? 1e9) - h.x, (world.buildings.get(b.home)?.z ?? 1e9) - h.z) < 160); return pick(res) || null; },
    peer: (span = 2) => pick(world.residents(a.sid).filter(b => b.id !== a.id && Math.abs(ageOf(b, now) - age) <= span)) || null,
    co: () => (a.job?.co != null ? world.companies.get(a.job.co) : null),
    isRk: rk,
    // 시대에 맞는 말
    E: arr => arr[Math.min(arr.length - 1, era)],
  };
  return x;
}

// ── 갈림길 정의 ──
const DEFS = {};
const PRO = []; // 일상 갈림길 (하루 한 번 후보)
/**
 * D(id, def)
 *  cat, icon, big(큰 결정), ages [최소, 최대), era [최소, 최대], w (일상 후보 가중치), cool (같은 앤에게 다시 묻기까지 일)
 *  when(x) → 일상 갈림길 조건 (자료 객체 또는 true)
 *  ok(x, d) → 답할 때 아직 유효한가
 *  q(x, d) → 질문, opts(x, d) → [{ k, t, lean(x,d), run(x,d) → 결과 문장 | {t, v} }]
 */
function D(id, def) {
  DEFS[id] = { id, cat: 'life', icon: '❓', ages: [0, 200], w: 1, cool: 30, ...def };
  if (def.when) PRO.push(id);
}
const O = (k, t, lean, run) => ({ k, t, lean, run });
const hb = b => (b ? b.name : '누군가');

// ══════════════════════════ 사랑 · 결혼 (상황) ══════════════════════════
D('confess', {
  cat: 'love', icon: '💗', big: true,
  ok: (x, d) => { const b = x.here(d.b); return b && !b.partner && !b.spouse && !x.a.partner && !x.a.spouse; },
  q: (x, d) => { const b = x.get(d.b); return x.pick2([`${b.name}만 보면 가슴이 뛴다. 마음을 고백해 볼까?`, `${x.call(b)}에게 좋아한다고 말해도 될까?`, `요즘 자꾸 ${b.name} 생각이 난다. 용기를 내 볼까?`]); },
  opts: (x, d) => {
    const r = x.a.rels[d.b] || { r: 0, f: 0 };
    return [
      O('go', '고백한다', x => x.a.conf * 0.6 + r.r * 0.5 + x.a.tr.E * 0.2, (x, d) => { x.plan('confess', { b: d.b }, 6); x.think(pick(['그래, 말하자. 후회하는 것보단 낫다.', '심장이 터질 것 같지만… 해 보자.'])); return '다음에 만나면 마음을 전하기로 했다'; }),
      x.era >= 2 ? O('letter', x.era >= 6 ? '긴 메시지로 마음을 전한다' : '편지로 마음을 전한다', x => x.a.tr.O * 0.3 + (100 - x.a.tr.E) * 0.4, (x, d) => { x.plan('confess', { b: d.b, soft: 1 }, 6); x.sk('art', 1); return `밤새 ${x.era >= 6 ? '메시지를' : '편지를'} 쓰고 고쳤다. 곧 전할 것이다`; }) : null,
      O('wait', '조금 더 친해진 뒤에', x => x.a.tr.N * 0.5 + (60 - r.f) * 0.5, (x, d) => { const b = x.get(d.b); if (b) rel(x.a, b).lastConf = Math.round(x.now - 80 * DAY); x.think('서두르지 말자. 조금만 더 가까워지면…'); return '좀 더 가까워질 때까지 기다리기로 했다'; }),
      O('drop', '마음을 접는다', x => (100 - x.a.conf) * 0.4 - 15, (x, d) => { const b = x.get(d.b); if (b) { const rr = rel(x.a, b); rr.r = clamp(rr.r * 0.5); rr.lastConf = Math.round(x.now); } x.feel(-8, '아쉬움', b?.name); x.think('친구로 지내는 게 나을지도 몰라.'); return '마음을 접고 좋은 친구로 남기로 했다'; }),
    ];
  },
});
D('confessed', {
  cat: 'love', icon: '💌', big: true, wait: 12 * 60,
  ok: (x, d) => { const b = x.get(d.by); return b && !b.partner && !b.spouse && !x.a.partner && !x.a.spouse; },
  q: (x, d) => { const b = x.get(d.by); return `${ga(b.name)} 나를 좋아한다고 고백했다. 받아 줄까?`; },
  opts: (x, d) => {
    const b = x.get(d.by); const r = x.a.rels[d.by] || { r: 0, a: 0 };
    const att = b ? attraction(x.w, x.a, b) : 0;
    return [
      O('yes', '받아 준다', x => r.r * 0.9 + r.a * 0.3 + (att - 50) * 0.6, (x, d) => { const b = x.get(d.by); if (!b) return '상대가 떠나 버렸다'; x.w.acceptConfession(b, x.a); return `${wa(b.name)} 연인이 되었다`; }),
      O('time', '조금만 시간을 달라고 한다', x => 18 + x.a.tr.N * 0.2, (x, d) => { const b = x.get(d.by); if (b) { const rb = rel(b, x.a); rb.lastConf = Math.round(x.now - 90 * DAY); rel(x.a, b).r = clamp((x.a.rels[b.id]?.r || 0) + 8); remember(x.w, b, `${ga(x.a.name)} 생각할 시간을 달라고 했다`, 5, 0, [x.a.id], 'love'); } return '천천히 생각해 보기로 했다'; }),
      O('no', '정중히 거절한다', x => 45 - r.r * 0.8 - (att - 50) * 0.5, (x, d) => { const b = x.get(d.by); if (!b) return ''; x.w.rejectConfession(b, x.a); return `${b.name}의 마음을 정중히 거절했다`; }),
    ];
  },
});
D('propose', {
  cat: 'love', icon: '💍', big: true,
  ok: (x, d) => { const b = x.here(d.b); return b && x.a.partner === b.id && !x.a.spouse && !x.a.engaged; },
  q: (x, d) => { const b = x.get(d.b); return x.pick2([`${wa(b.name)} 평생을 함께하고 싶다. 청혼할까?`, `${x.call(b)}에게 결혼하자고 말해도 될까?`]); },
  opts: (x, d) => {
    const r = x.a.rels[d.b] || { r: 0, a: 0 };
    const ring = x.M * (x.era >= 5 ? 1.2 : 0.4);
    return [
      O('now', '청혼한다', x => r.r * 0.5 + x.a.val.family * 0.3 + x.a.conf * 0.2, (x, d) => { x.plan('propose', { b: d.b }, 6); return '다음 데이트에서 청혼하기로 했다'; }),
      x.a.money > ring * 1.5 ? O('ring', `${x.E(['', '', '옥가락지', '은가락지', '금반지', '반지', '반지', '반지'])}을(를) 준비해 특별한 날에`, x => x.a.tr.C * 0.3 + x.a.tr.O * 0.2 + 10, (x, d) => { x.spend(ring, '청혼 준비'); x.plan('propose', { b: d.b, ring: 1 }, 10); x.think('잊지 못할 날로 만들어 주고 싶다.'); return `${x.fmt(ring)}을(를) 들여 청혼을 준비했다`; }) : null,
      O('later', '아직은 이르다', x => x.a.tr.N * 0.4 + (60 - r.r) * 0.6, (x, d) => { const b = x.get(d.b); if (b) rel(x.a, b).propWait = Math.round(x.now + 60 * DAY); return '조금 더 함께 지내 본 뒤에 정하기로 했다'; }),
      x.era >= 5 ? O('live', '먼저 함께 살아 본다', x => x.a.tr.O * 0.5 - 10, (x, d) => { const b = x.here(d.b); if (!b) return ''; const rr = rel(x.a, b); rr.propWait = Math.round(x.now + 120 * DAY); return x.w.cohabit(x.a, b) ? `${wa(b.name)} 함께 살기로 했다` : `${wa(b.name)} 함께 살 집을 알아보기로 했다`; }) : null,
    ];
  },
});
D('proposed', {
  cat: 'love', icon: '💍', big: true, wait: 18 * 60,
  ok: (x, d) => { const b = x.get(d.by); return b && x.a.partner === b.id && !x.a.spouse; },
  q: (x, d) => { const b = x.get(d.by); return `${ga(b.name)} ${d.ring ? '반지를 내밀며 ' : ''}청혼했다. 받아들일까?`; },
  opts: (x, d) => {
    const r = x.a.rels[d.by] || { r: 0, a: 0 };
    return [
      O('yes', '승낙한다', x => r.r * 0.6 + r.a * 0.3 + x.a.val.family * 0.3 + (d.ring ? 6 : 0) - 30, (x, d) => { const b = x.get(d.by); if (!b) return ''; x.w.acceptProposal(b, x.a); return `${b.name}의 청혼을 받아들였다`; }),
      O('wait', '조금 더 생각해 본다', x => 20 + x.a.tr.N * 0.25, (x, d) => { const b = x.get(d.by); if (b) { const rb = rel(b, x.a); rb.propWait = Math.round(x.now + 45 * DAY); remember(x.w, b, `${ga(x.a.name)} 청혼에 조금 더 생각해 보겠다고 했다`, 5, -0.2, [x.a.id], 'love'); } return '대답을 미루었다'; }),
      O('no', '거절한다', x => 40 - r.r * 0.7, (x, d) => { const b = x.get(d.by); if (!b) return ''; x.w.rejectProposal(b, x.a); return '청혼을 거절했다'; }),
    ];
  },
});
D('breakup', {
  cat: 'love', icon: '💔', big: true,
  ok: (x, d) => x.a.partner === d.b && !x.a.spouse,
  q: (x, d) => { const b = x.get(d.b); return d.cold ? `${wa(b.name)} 예전 같지 않다. 설렘이 식은 걸까. 이대로 만나도 될까?` : `요즘 ${wa(b.name)} 자꾸 다툰다. 헤어지는 게 나을까?`; },
  opts: (x, d) => [
    O('end', '헤어진다', x => -(x.a.rels[d.b]?.a || 0) * 0.6 + (d.cold ? 15 : 5), (x, d) => { const b = x.get(d.b); if (!b) return ''; x.w.breakUp(x.a, b, false); return `${wa(b.name)} 헤어졌다`; }),
    O('talk', '솔직하게 대화해 본다', x => x.a.tr.A * 0.4 + (x.a.rels[d.b]?.r || 0) * 0.3, (x, d) => {
      const b = x.get(d.b); if (!b) return '';
      const ok = chance(0.45 + (x.a.tr.A + b.tr.A) / 400);
      x.a.relHold = b.relHold = Math.round(x.now + 30 * DAY);
      if (ok) { x.bond(b, 14, 4, 1); rel(x.a, b).r = clamp(rel(x.a, b).r + 10); rel(b, x.a).r = clamp(rel(b, x.a).r + 8); x.mem(`${wa(b.name)} 밤새 이야기하며 서로를 다시 이해했다`, 6, 0.6, [b.id]); return { t: '밤새 이야기를 나누고 다시 가까워졌다', v: 1 }; }
      x.bond(b, -4, 2); x.mem(`${wa(b.name)} 대화를 해 봤지만 서로 상처만 남았다`, 5, -0.5, [b.id]); return { t: '대화를 해 봤지만 쉽게 풀리지 않았다', v: -0.5 };
    }),
    O('pause', '잠시 시간을 갖는다', x => 15 + x.a.tr.N * 0.2, (x, d) => { const b = x.get(d.b); x.a.relHold = Math.round(x.now + 20 * DAY); if (b) b.relHold = x.a.relHold; x.stress(-4); return '잠시 거리를 두고 생각해 보기로 했다'; }),
  ],
});
D('divorce', {
  cat: 'family', icon: '💔', big: true,
  ok: (x, d) => x.a.spouse === d.b,
  q: (x, d) => { const b = x.get(d.b); return `${wa(b.name)} 부부로 사는 게 너무 힘들다. 이혼해야 할까?`; },
  opts: (x, d) => {
    const kids = x.kids().filter(k => ageOf(k, x.now) < 19).length;
    const fee = x.M * (x.era >= 5 ? 0.4 : 0.1);
    return [
      O('divorce', '이혼한다', x => -(x.a.rels[d.b]?.a || 0) * 0.7 - kids * 6 - x.a.val.family * 0.15 + 10, (x, d) => { const b = x.get(d.b); if (!b) return ''; x.w.breakUp(x.a, b, true); return `${wa(b.name)} 이혼했다`; }),
      O('counsel', x.era >= 5 ? '부부 상담을 받아 본다' : x.era >= 2 ? '집안 어른께 중재를 부탁한다' : '모닥불 앞에서 서로의 마음을 털어놓는다', x => x.a.tr.A * 0.35 + x.a.tr.O * 0.15 + kids * 4, (x, d) => {
        const b = x.get(d.b); if (!b) return '';
        if (x.era >= 5) x.spend(fee, '상담비');
        x.a.relHold = b.relHold = Math.round(x.now + 45 * DAY);
        const ok = chance(0.5 + (x.a.tr.A + b.tr.A - 100) / 300);
        if (ok) { x.bond(b, 18, 5, 1); x.mem(`${wa(b.name)} ${x.era >= 5 ? '상담을 받으며' : '마음을 털어놓으며'} 서로를 다시 보게 되었다`, 7, 0.7, [b.id]); x.stress(-12); return { t: '서로의 마음을 다시 확인했다', v: 1 }; }
        x.mem(`${wa(b.name)} 노력해 봤지만 골은 깊었다`, 6, -0.6, [b.id]); x.stress(8); return { t: '노력했지만 쉽게 좋아지지 않았다', v: -0.5 };
      }),
      kids ? O('endure', '아이들을 생각해 참는다', x => x.a.val.family * 0.4 + kids * 8 - x.a.tr.N * 0.1, (x, d) => { x.a.relHold = Math.round(x.now + 60 * DAY); x.stress(14); x.think('아이들이 클 때까지만…'); return '아이들을 생각해 참기로 했다'; }) : null,
    ];
  },
});
D('engaged_style', {
  cat: 'love', icon: '💒', big: true,
  ok: (x, d) => !!x.a.engaged,
  q: (x, d) => { const b = x.get(x.a.engaged?.to); return `${wa(hb(b))} 결혼식을 어떻게 올릴까?`; },
  opts: (x) => {
    const M = x.M;
    return [
      O('big', x.E(['온 부족이 모이는 큰 잔치', '온 마을 잔치', '성대한 혼례', '성대한 혼례', '큰 예식장에서', '호텔 웨딩홀에서', '하객 300명 웨딩홀', '궤도 호텔 연회장에서']), x => x.a.tr.E * 0.4 + x.a.val.power * 0.2 + (x.a.money > M * 6 ? 15 : -10), (x) => { x.a.engaged.style = 'big'; if (x.get(x.a.engaged.to)?.engaged) x.get(x.a.engaged.to).engaged.style = 'big'; return '모두를 초대해 크게 올리기로 했다'; }),
      O('normal', '평범하게', x => 25, (x) => { x.a.engaged.style = 'normal'; const b = x.get(x.a.engaged.to); if (b?.engaged) b.engaged.style = 'normal'; return '가족과 친구들을 불러 평범하게 올리기로 했다'; }),
      O('small', x.era >= 6 ? '가까운 사람만 부르는 스몰웨딩' : '가족끼리 조촐하게', x => (100 - x.a.tr.E) * 0.3 + x.a.tr.C * 0.2 + (x.a.money < M * 2 ? 15 : 0), (x) => { x.a.engaged.style = 'small'; const b = x.get(x.a.engaged.to); if (b?.engaged) b.engaged.style = 'small'; return '가까운 사람들만 불러 조촐하게 올리기로 했다'; }),
    ];
  },
});
D('honeymoon', {
  cat: 'love', icon: '✈️', era: [3, 7],
  ok: (x, d) => !!x.a.spouse,
  q: (x) => `신혼여행은 어디로 갈까?`,
  opts: (x) => {
    const far = x.E(['', '', '', '금강산 유람', '제주도', '동남아 휴양지', '유럽', '달 궤도 리조트']), near = x.E(['', '', '', '온천', '경주', '제주도', '제주도', '바닷가 리조트']);
    const cFar = x.M * (x.era >= 6 ? 2 : 1), cNear = x.M * 0.5;
    return [
      x.a.money > cFar ? O('far', `${far}(으)로 멀리`, x => x.a.tr.O * 0.4 + x.a.val.adventure * 0.3 - 10, (x) => { x.spend(cFar, '신혼여행'); x.w.honeymoon(x.a, far, 7); return `${far}(으)로 신혼여행을 떠났다 (${x.fmt(cFar)})`; }) : null,
      O('near', `${near}(으)로 가까이`, x => 25, (x) => { x.spend(cNear, '신혼여행'); x.w.honeymoon(x.a, near, 4); return `${near}(으)로 신혼여행을 다녀왔다`; }),
      O('save', '돈을 아껴 신혼집에 보탠다', x => x.a.tr.C * 0.3 + (x.a.money < cFar ? 20 : 0), (x) => { x.think('여행은 나중에 가도 돼. 우리 집이 먼저야.'); return '여행 대신 신혼살림에 보태기로 했다'; }),
    ];
  },
});
D('kids', {
  cat: 'family', icon: '👶', big: true, ages: [22, 46], w: 2.2, cool: 300,
  when: x => {
    const sp = x.spouse();
    if (!sp || x.a.preg || sp.preg || (x.a.kidPlan && x.a.kidPlan.until > x.now)) return null;
    const mom = x.F ? x.a : sp.gender === 'F' ? sp : null;
    if (!mom || ageOf(mom, x.now) > 43) return null;
    return { n: x.a.kids.length };
  },
  q: (x, d) => { const sp = x.spouse(); return d.n ? `${wa(hb(sp))} 아이를 하나 더 가져 볼까?` : `${wa(hb(sp))} 이제 아이를 가져 볼까?`; },
  opts: (x, d) => [
    O('yes', d.n ? '하나 더 낳자' : '아이를 갖자', x => x.a.val.family * 0.6 + (x.era <= 3 ? 15 : 0) - d.n * (x.era >= 5 ? 14 : 5) - (x.a.money < x.M * 2 ? 12 : 0), (x) => { x.w.setKidPlan(x.a, 'yes', 3); x.think(pick(['우리를 닮은 아이… 상상만 해도 좋다.', '좋은 부모가 되고 싶다.'])); return '아이를 갖기로 했다'; }),
    O('later', '조금 더 자리를 잡은 뒤에', x => 22 + (x.a.money < x.M * 3 ? 18 : 0) + x.a.amb * 0.1, (x) => { x.w.setKidPlan(x.a, 'later', 1); return '형편이 나아지면 갖기로 했다'; }),
    O('no', d.n ? '지금 아이들로 충분하다' : '우리 둘이면 충분하다', x => 10 + d.n * 10 + (100 - x.a.val.family) * 0.25, (x) => { x.w.setKidPlan(x.a, 'no', 5); return d.n ? '지금 아이들을 잘 키우기로 했다' : '둘이서 사는 삶을 택했다'; }),
  ],
});
D('parental_leave', {
  cat: 'family', icon: '🍼', big: true, era: [5, 7],
  ok: (x, d) => !!x.a.job && x.here(d.baby),
  q: (x, d) => { const sp = x.spouse(); const baby = x.get(d.baby); return `${hb(baby)}이(가) 태어났다. ${sp?.job ? '누가 육아휴직을 쓸까?' : '육아휴직을 쓸까?'}`; },
  opts: (x, d) => {
    const sp = x.spouse();
    return [
      O('me', '내가 육아휴직을 쓴다', x => x.a.val.family * 0.4 + x.a.tr.A * 0.1 - x.a.amb * 0.2 + (sp?.job ? 0 : -5) + (x.F ? 6 : 0), (x) => { x.w.startLeave(x.a, '육아휴직', x.era >= 6 ? 365 : 180); return '육아휴직을 내고 아기를 돌보기로 했다'; }),
      sp?.job ? O('spouse', `${x.call(sp)}에게 부탁한다`, x => x.a.amb * 0.3 + (x.F ? 0 : 6) + 10, (x) => { const s2 = x.spouse(); if (s2?.job) { x.w.startLeave(s2, '육아휴직', x.era >= 6 ? 365 : 180); remember(x.w, s2, '육아휴직을 내고 아기를 돌보기로 했다', 6, 0.4, [x.a.id], 'family'); } return `${s2?.name || '배우자'}이(가) 육아휴직을 쓰기로 했다`; }) : null,
      sp?.job ? O('both', '둘이 반씩 나눠 쓴다', x => x.a.tr.A * 0.3 + x.a.tr.O * 0.2 + (x.era >= 6 ? 10 : -10), (x) => { const half = x.era >= 6 ? 180 : 90; x.w.startLeave(x.a, '육아휴직', half); const s2 = x.spouse(); if (s2?.job) x.w.startLeave(s2, '육아휴직', half, half); return '육아휴직을 반씩 나눠 쓰기로 했다'; }) : null,
      O('grand', x.parents().length ? '부모님께 도움을 청한다' : '아이 돌봄 서비스를 알아본다', x => x.a.amb * 0.25 + (x.parents().length ? 15 : 0), (x) => { const p = x.parent(); if (p) { x.bond(p, 5, 4); remember(x.w, p, `${x.a.name}네 아기를 돌봐 주기로 했다`, 5, 0.5, [x.a.id], 'family'); p.nd.energy = clamp(p.nd.energy - 10); return `${p.name}께서 아기를 봐 주시기로 했다`; } x.spend(x.M * 0.6, '돌봄 서비스'); return '돌봄 서비스를 쓰며 일을 계속하기로 했다'; }),
    ];
  },
});

// ══════════════════════════ 일 · 돈 (상황) ══════════════════════════
D('job_offer', {
  cat: 'work', icon: '📨', big: true,
  ok: (x, d) => !!x.a.job,
  q: (x, d) => { const co = d.co != null ? x.w.companies.get(d.co) : null; const j = JOBS[d.job]?.name || '일'; return `${co ? co.name + '에서 ' : ''}${j} 자리를 제안받았다${d.more > 0 ? ` (월급 ${Math.round(d.more * 100)}% ↑)` : ''}. 옮길까?`; },
  opts: (x, d) => [
    O('move', '옮긴다', x => 20 + x.a.tr.O * 0.3 + x.a.val.wealth * 0.2 + (d.more || 0) * 60 - (x.a.job?.sat || 50) * 0.3, (x, d) => {
      const { out } = openings(x.w, x.a);
      const op = out.find(o => o.jobKey === d.job && (o.co?.id ?? null) === (d.co ?? null));
      if (!op) return { t: '그사이 그 자리는 다른 사람에게 돌아갔다', v: -0.3 };
      const before = jobTitle(x.w, x.a);
      if (apply(x.w, x.a, op)) { x.think(`새로운 시작이다. ${before} 시절도 고마웠다.`); return { t: `${jobTitle(x.w, x.a)}(으)로 옮겼다`, v: 0.5 }; }
      return { t: '면접에서 떨어졌다', v: -0.5 };
    }),
    O('stay', '지금 일터에 남는다', x => x.a.tr.C * 0.3 + (x.a.job?.sat || 50) * 0.4 + (x.a.job?.rank || 0) * 6, (x) => { if (x.a.job) x.a.job.sat = clamp(x.a.job.sat + 4); x.a.lastSeek = x.now + 60 * DAY; return '지금 일터에 남기로 했다'; }),
    x.a.job?.co != null && x.era >= 4 ? O('nego', '제안서를 들고 월급 협상을 한다', x => x.a.amb * 0.3 + x.a.ab.cha * 0.2 - 5, (x) => {
      const co = x.co(); if (!x.a.job) return '';
      if (co && co.money > x.M * 10 && chance(0.35 + x.a.ab.cha / 300)) { x.a.job.raise = (x.a.job.raise || 1) * 1.08; x.a.job.sat = clamp(x.a.job.sat + 8); return { t: '월급을 8% 올려 받고 남았다', v: 1 }; }
      x.a.job.sat = clamp(x.a.job.sat - 5); const b = x.boss(); if (b) x.bond(b, -4, 1); return { t: '협상은 실패했고 분위기만 어색해졌다', v: -0.5 };
    }) : null,
  ],
});
D('quit', {
  cat: 'work', icon: '😮‍💨', big: true,
  ok: (x) => !!x.a.job && !x.a.job.ceo,
  q: (x) => `${jobTitle(x.w, x.a)} 일이 너무 맞지 않는다. 그만둘까?`,
  opts: (x) => [
    O('quit', '그만둔다', x => (100 - (x.a.job?.sat || 50)) * 0.5 + (100 - x.a.tr.C) * 0.2 + (x.a.money > x.M * 3 ? 10 : -15), (x) => { const t = jobTitle(x.w, x.a); leaveJob(x.w, x.a, '적성에 맞지 않아서'); x.feel(-3, '홀가분함', t); return `${t} 일을 그만두었다`; }),
    O('hold', '조금만 더 버틴다', x => x.a.tr.C * 0.5 + (x.a.money < x.M * 2 ? 20 : 0), (x) => { if (!x.a.job) return ''; x.a.job.hold = Math.round(x.now + 40 * DAY); x.a.job.sat = clamp(x.a.job.sat + 10); x.stress(6); return '이를 악물고 조금 더 버티기로 했다'; }),
    x.boss() ? O('talk', '상사에게 고민을 털어놓는다', x => x.a.tr.E * 0.3 + x.a.tr.A * 0.2, (x) => { const b = x.boss(); if (!x.a.job) return ''; x.a.job.hold = Math.round(x.now + 30 * DAY); if (b && chance(0.4 + b.tr.A / 250)) { x.a.job.sat = clamp(x.a.job.sat + 22); x.bond(b, 6, 3); return { t: `${b.name}이(가) 일을 조정해 주었다`, v: 1 }; } x.a.job.sat = clamp(x.a.job.sat + 4); return { t: '이야기는 했지만 크게 달라진 건 없다', v: -0.2 }; }) : null,
  ],
});
D('found', {
  cat: 'work', icon: '🏢', big: true,
  q: (x, d) => `모아 둔 돈으로 ${INDUSTRIES[d.ind]?.name || '가게'}을(를) 차려 볼까? (창업 비용 ${x.fmt(d.cost || 0)})`,
  opts: (x, d) => [
    O('go', '창업한다', x => x.a.amb * 0.5 + x.a.tr.O * 0.2 + (x.a.goals.some(g => g.k === 'company' && !g.done) ? 18 : 0) - x.a.tr.N * 0.15, (x, d) => { x.plan('found', { ind: d.ind }, 10); x.think('해 보자. 내 이름을 건 가게를!'); return '창업을 결심했다'; }),
    O('later', '돈을 더 모은 뒤에', x => x.a.tr.C * 0.3 + x.a.tr.N * 0.2 + 10, (x) => { x.a.lastFound = Math.round(x.now + 90 * DAY); return '좀 더 준비한 뒤에 하기로 했다'; }),
    O('drop', '꿈을 접는다', x => (100 - x.a.amb) * 0.4 - 10, (x) => { x.a.lastFound = Math.round(x.now + 2 * YEAR); x.a.amb = clamp(x.a.amb - 4); x.think('지금 삶도 나쁘지 않아.'); return '창업의 꿈을 접었다'; }),
  ],
});
D('migrate', {
  cat: 'life', icon: '🧭', big: true,
  q: (x, d) => `${d.why === '모험심' ? '저 너머 새 땅이 자꾸 부른다.' : `${x.s.name}은(는) 너무 붐빈다.`} 가족과 함께 새 마을을 개척하러 떠날까?`,
  opts: (x, d) => [
    O('go', '떠난다', x => x.a.val.adventure * 0.6 + x.a.tr.O * 0.2 - Object.values(x.a.rels).filter(r => r.k === 'best').length * 4, (x, d) => { x.a.wantsMigrate = d.why; return '새 땅을 찾아 떠날 준비를 시작했다'; }),
    O('stay', '여기 남는다', x => x.a.val.family * 0.3 + x.a.val.community * 0.3 + (x.a.home ? 10 : 0), (x) => { x.a.noMigrateUntil = Math.round(x.now + 2 * YEAR); return `${x.s.name}에 남기로 했다`; }),
    O('wait', '1년쯤 더 생각해 본다', x => 18 + x.a.tr.N * 0.15, (x) => { x.a.noMigrateUntil = Math.round(x.now + YEAR); return '조금 더 생각해 보기로 했다'; }),
  ],
});
D('retire', {
  cat: 'work', icon: '🌅', big: true,
  ok: (x) => !!x.a.job,
  q: (x) => `${Math.floor(x.age)}살. ${jobTitle(x.w, x.a)} 일을 이제 그만 내려놓을까?`,
  opts: (x) => [
    O('retire', '은퇴한다', x => (x.age - 60) * 3 + (100 - x.a.health) * 0.3 + (x.a.money > x.M * 24 ? 15 : -10), (x) => { x.w.retire(x.a); return '은퇴했다'; }),
    O('more', '몇 년 더 일한다', x => x.a.tr.C * 0.3 + x.a.val.mastery * 0.2 + (x.a.money < x.M * 12 ? 20 : 0), (x) => { x.a.retireAfter = Math.round(x.now + 2 * YEAR); return '아직은 일할 수 있다며 계속 일하기로 했다'; }),
  ],
});
D('college', {
  cat: 'school', icon: '🎓', big: true,
  ok: (x) => !x.a.school,
  q: (x, d) => `${x.E(['학당', '학당', '학당', '서원', '대학', '대학', '대학', '대학'])}에 갈까, 바로 일을 시작할까?`,
  opts: (x, d) => [
    O('univ', '대학에 간다', x => d.want, (x, d) => { const st = SCHOOLS.find(s => s.key === 'univ'); const org = x.w.companies.get(d.org); if (!org || org.dead) return '그사이 대학이 문을 닫았다'; x.w.enroll(x.a, st, org, x.s, x.c); return '대학에 들어갔다'; }),
    O('work', '바로 일을 시작한다', x => 52, (x) => { x.life('대학 대신 일을 시작하기로 했다', '🧰'); x.think('돈을 벌며 세상을 먼저 배우자.'); return '대학 대신 일을 시작했다'; }),
    x.era >= 6 ? O('gap', '1년 쉬며 하고 싶은 일을 찾는다', x => x.a.tr.O * 0.4 + 5, (x) => { x.a.noSchool = x.a.noSchool || {}; x.a.noSchool.univ = 0; x.a.gapYear = Math.round(x.now + YEAR); x.fun(20); return '1년 동안 하고 싶은 일을 찾아보기로 했다'; }) : null,
  ],
});
D('army', {
  cat: 'life', icon: '🎖️', big: true,
  ok: (x) => !x.a.served && !x.a.army,
  q: (x) => `병역 의무. ${x.age < 20 ? '스무 살 전에' : '지금'} 입대할까, 조금 미룰까?`,
  opts: (x) => [
    O('now', '지금 입대한다', x => x.a.tr.C * 0.4 + (x.age > 22 ? 20 : 0) + (x.a.job ? -5 : 8), (x) => { x.w.enlist(x.a); return '입대했다'; }),
    x.age < 27 ? O('later', '1년 뒤에 간다', x => 25 + (x.a.partner ? 10 : 0) + (x.a.job ? 8 : 0), (x) => { x.a.armyAsk = Math.round(x.now + YEAR); return '1년 뒤에 입대하기로 했다'; }) : null,
  ],
});
D('election', {
  cat: 'power', icon: '🗳️', big: true,
  ok: (x, d) => x.c.nextElection === d.e,
  q: (x) => `${x.c.name} ${ERAS[x.era].leader} 선거가 다가온다. 출마해 볼까?`,
  opts: (x, d) => [
    O('run', '출마한다', x => x.a.amb * 0.4 + x.a.val.power * 0.4 + x.a.ab.cha * 0.1 - 25, (x, d) => { x.a.runFor = { e: d.e, yes: 1 }; x.life('선거에 출마했다', '🗳️'); x.think('세상을 바꿀 기회다.'); return '출마를 결심했다'; }),
    O('no', '나가지 않는다', x => 30 + (100 - x.a.val.power) * 0.2, (x, d) => { x.a.runFor = { e: d.e, yes: 0 }; return '이번엔 투표만 하기로 했다'; }),
  ],
});
D('leave_home', {
  cat: 'home', icon: '🏠', ages: [21, 40], w: 1.2, cool: 200,
  when: x => {
    const h = x.a.home && x.w.buildings.get(x.a.home);
    if (!h || h.owner === x.a.id || !x.a.parents.includes(h.owner) || x.a.indepWait > x.now || x.a.spouse) return null;
    return true;
  },
  q: (x) => `아직 부모님 집에 산다. 독립해서 내 집을 마련할까?`,
  opts: (x) => [
    O('go', '독립한다', x => x.a.val.adventure * 0.3 + x.a.amb * 0.2 + (x.age - 22) * 2 + (x.a.money > x.M * 6 ? 10 : -10), (x) => { x.plan('indep', {}, 30); x.a.indepWait = 0; return '독립하기로 했다'; }),
    O('save', '돈을 더 모은다', x => x.a.tr.C * 0.3 + (x.a.money < x.M * 6 ? 20 : 0) + x.a.val.family * 0.2, (x) => { x.a.indepWait = Math.round(x.now + YEAR); const p = x.parent(); if (p) x.bond(p, 3, 3); return '부모님 집에서 조금 더 지내며 돈을 모으기로 했다'; }),
  ],
});

// ══════════════════════════ 일상 갈림길 — 어린이 ══════════════════════════
D('c_homework', {
  cat: 'school', icon: '📒', ages: [7, 16], w: 1, cool: 60,
  when: x => { const b = x.a.school && x.peer(1); return b ? { b: b.id } : null; },
  q: (x, d) => `${ga(hb(x.get(d.b)))} 숙제를 베끼게 해 달라고 한다.`,
  opts: (x, d) => [
    O('show', '보여 준다', x => x.a.tr.A * 0.5 - x.a.tr.C * 0.2 + 10, (x, d) => { const b = x.get(d.b); x.bond(b, 5, 3); if (x.a.school && chance(0.3)) { x.a.school.grade = clamp(x.a.school.grade - 3); return { t: '선생님께 들켜 둘 다 혼났다', v: -0.5 }; } return `${b?.name || '친구'}이(가) 고마워했다`; }),
    O('together', '같이 풀자고 한다', x => x.a.tr.C * 0.3 + x.a.tr.A * 0.3, (x, d) => { const b = x.get(d.b); x.bond(b, 6, 4); if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 1.5); if (b?.school) b.school.grade = clamp(b.school.grade + 2); return { t: '같이 숙제를 하며 더 친해졌다', v: 1 }; }),
    O('no', '안 된다고 한다', x => x.a.tr.C * 0.5 - x.a.tr.A * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, -4, 1); return `${b?.name || '친구'}이(가) 조금 서운해했다`; }),
  ],
});
D('c_relay', {
  cat: 'school', icon: '🏃', ages: [7, 18], w: 0.8, cool: 365,
  when: x => x.a.school && x.a.ab.ath > 45,
  q: (x) => x.era <= 3 ? '마을 달리기 시합에 아이들 대표로 뽑혔다. 나갈까?' : `${x.E(['', '', '', '', '운동회', '운동회', '체육대회', '체육대회'])} 계주 대표로 뽑혔다. 나갈까?`,
  opts: (x) => [
    O('run', '나간다', x => x.a.ab.ath * 0.5 + x.a.tr.E * 0.2, (x) => { x.sk('athletics', 3); x.fun(25); if (chance(0.3 + x.a.ab.ath / 300)) { x.life('계주에서 역전승을 했다!', '🥇'); x.feel(20, '뿌듯함'); x.a.conf = clamp(x.a.conf + 5); return { t: '역전승을 해 영웅이 되었다', v: 1 }; } x.mem('계주에서 넘어졌지만 끝까지 달렸다', 4, 0.2); return '넘어졌지만 끝까지 달렸다'; }),
    O('pass', '다른 친구에게 양보한다', x => x.a.tr.N * 0.4 + x.a.tr.A * 0.2, (x) => { const b = x.peer(1); if (b) x.bond(b, 4, 2); return '응원단장을 맡았다'; }),
  ],
});
D('c_pet', {
  cat: 'family', icon: '🐶', ages: [6, 14], w: 0.8, cool: 999,
  when: x => !x.a.pet && x.parent() && true,
  q: (x) => `${x.E(['새끼 늑대', '강아지', '강아지', '강아지', '강아지', '강아지', '강아지', '로봇 강아지'])}을(를) 키우고 싶다. 엄마 아빠를 졸라 볼까?`,
  opts: (x) => [
    O('beg', '조른다', x => x.a.tr.E * 0.3 + (100 - x.a.tr.C) * 0.2 + 20, (x) => { const p = x.parent(); if (p && chance(0.45 + p.tr.A / 250)) { const nm = pick(['복실이', '누렁이', '초코', '콩이', '보리', '두부']); x.a.pet = nm; x.life(`${ro(nm)} 이름 지은 강아지가 생겼다`, '🐶'); x.fun(30); x.bond(p, 6, 3); return { t: `${p.name}이(가) 허락해 주었다! 이름은 ${nm}`, v: 1 }; } if (p) x.bond(p, -2, 1); return { t: '안 된다는 대답만 들었다', v: -0.3 }; }),
    O('promise', '산책은 내가 다 하겠다고 약속한다', x => x.a.tr.C * 0.5 + 5, (x) => { const p = x.parent(); if (p && chance(0.6 + p.tr.A / 300)) { const nm = pick(['바둑이', '나비', '별이', '구름이']); x.a.pet = nm; x.life(`약속을 걸고 강아지 ${reul(nm)} 데려왔다`, '🐶'); x.fun(25); x.a.tr.C = clamp(x.a.tr.C + 1); return { t: `강아지 ${nm}이(가) 식구가 되었다`, v: 1 }; } return '조금 더 크면 키우자고 하셨다'; }),
    O('wait', '참는다', x => x.a.tr.C * 0.3 + x.a.tr.A * 0.2, (x) => { x.think('나중에 어른이 되면 꼭 키울 거야.'); return '꾹 참기로 했다'; }),
  ],
});
D('c_sibling', {
  cat: 'family', icon: '🧸', ages: [5, 16], w: 1, cool: 90,
  when: x => { const b = x.sibs().find(s => ageOf(s, x.now) < x.age); return b ? { b: b.id } : null; },
  q: (x, d) => `동생 ${ga(hb(x.get(d.b)))} 내 물건을 망가뜨렸다!`,
  opts: (x, d) => [
    O('tell', '엄마 아빠한테 이른다', x => (100 - x.a.tr.A) * 0.4 + x.a.tr.N * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, -5, 2); return `${b?.name || '동생'}이(가) 혼나고 엉엉 울었다`; }),
    O('forgive', '괜찮다고 해 준다', x => x.a.tr.A * 0.6, (x, d) => { const b = x.get(d.b); x.bond(b, 7, 3); x.otherThink(b, `${x.call(x.a)}… 고마워.`); return { t: '동생이 고마워하며 꼭 안겼다', v: 1 }; }),
    O('fix', '같이 고친다', x => x.a.tr.C * 0.3 + x.a.ab.cre * 0.3, (x, d) => { const b = x.get(d.b); x.bond(b, 6, 4); x.sk('craft', 1); return { t: '둘이서 머리를 맞대고 고쳤다', v: 1 }; }),
  ],
});
D('c_class_pres', {
  cat: 'school', icon: '🙋', ages: [9, 18], w: 0.7, cool: 365,
  when: x => x.a.school && x.era >= 4,
  q: (x) => `${x.age < 13 ? '반장' : '학생회장'} 선거가 있다. 나가 볼까?`,
  opts: (x) => [
    O('run', '나간다', x => x.a.tr.E * 0.4 + x.a.amb * 0.3 + x.a.ab.cha * 0.2 - 20, (x) => { if (chance(0.3 + x.a.ab.cha / 250 + x.a.rep / 300)) { x.life(`${x.age < 13 ? '반장' : '학생회장'}에 뽑혔다`, '🎖️'); x.a.conf = clamp(x.a.conf + 6); x.rep(3); return { t: '당선되었다!', v: 1 }; } x.mem('선거에 나갔지만 떨어졌다', 4, -0.3); x.a.conf = clamp(x.a.conf + 1); return { t: '아깝게 떨어졌지만 용기를 얻었다', v: 0 }; }),
    O('no', '안 나간다', x => (100 - x.a.tr.E) * 0.4, (x) => '이번엔 친구를 응원하기로 했다'),
  ],
});
D('c_bully', {
  cat: 'friend', icon: '🛡️', ages: [8, 18], w: 0.6, cool: 200,
  when: x => { const b = x.peer(2); return b ? { b: b.id } : null; },
  q: (x, d) => `${ga(hb(x.get(d.b)))} 아이들에게 괴롭힘당하는 걸 봤다.`,
  opts: (x, d) => [
    O('teacher', x.era >= 4 ? '선생님께 알린다' : '어른들께 알린다', x => x.a.tr.C * 0.3 + x.a.tr.A * 0.3, (x, d) => { const b = x.get(d.b); x.bond(b, 10, 3); x.rep(2); x.other(b, `${ga(x.a.name)} 어른들께 알려 줘서 괴롭힘이 멈췄다`, 6, 0.8); return { t: '어른들이 나서서 괴롭힘이 멈췄다', v: 1 }; }),
    O('stop', '직접 막아선다', x => x.a.ab.ath * 0.3 + (100 - x.a.tr.N) * 0.3 + x.a.tr.A * 0.1, (x, d) => { const b = x.get(d.b); x.bond(b, 14, 4); if (chance(0.5)) { x.health(-3); x.mem('친구를 감싸다 한 대 맞았다', 5, 0.2); return { t: '한 대 맞았지만 친구를 지켰다', v: 0.5 }; } x.a.conf = clamp(x.a.conf + 6); return { t: '아이들이 물러났다. 친구가 울먹이며 고마워했다', v: 1 }; }),
    O('ignore', '모른 척한다', x => x.a.tr.N * 0.4 - x.a.tr.A * 0.2 + 10, (x) => { x.stress(6); x.think('내가 뭘 할 수 있겠어… 그래도 마음이 무겁다.'); return '모른 척 지나쳤다'; }),
  ],
});
D('c_allowance', {
  cat: 'money', icon: '🪙', ages: [7, 15], w: 0.8, cool: 120,
  when: x => x.era >= 2 && x.parent(),
  q: (x) => `용돈을 받았다. 어디에 쓸까?`,
  opts: (x) => [
    O('save', '저금한다', x => x.a.tr.C * 0.5, (x) => { x.earn(x.M * 0.02, '용돈'); x.a.tr.C = clamp(x.a.tr.C + 0.5); return '저금통이 묵직해졌다'; }),
    O('snack', x.E(['', '', '엿을 사 먹는다', '엿을 사 먹는다', '불량식품을 사 먹는다', '떡볶이를 사 먹는다', '마라탕을 사 먹는다', '마라탕을 사 먹는다']), x => (100 - x.a.tr.C) * 0.4 + x.a.tr.E * 0.2, (x) => { x.fun(20); const b = x.friend(); if (b) x.bond(b, 4, 3); return b ? `${wa(b.name)} 나눠 먹었다` : '맛있게 먹었다'; }),
    O('gift', x.F ? '엄마 아빠 선물을 산다' : '부모님 선물을 산다', x => x.a.tr.A * 0.4 + x.a.val.family * 0.2, (x) => { const p = x.parent(); x.bond(p, 8, 3); x.other(p, `${ga(x.a.name)} 용돈을 모아 선물을 사 왔다`, 6, 1); return { t: '부모님이 크게 감동하셨다', v: 1 }; }),
  ],
});
D('c_found_money', {
  cat: 'life', icon: '👛', ages: [7, 15], w: 0.5, cool: 400,
  when: x => x.era >= 2,
  q: (x) => `길에서 ${x.E(['', '', '엽전 꾸러미', '엽전 꾸러미', '천 원짜리', '만 원짜리', '오만 원권', '전자 지갑'])}을(를) 주웠다.`,
  opts: (x) => [
    O('return', x.era >= 4 ? '경찰서에 갖다준다' : '어른께 갖다 드린다', x => x.a.tr.C * 0.4 + x.a.tr.A * 0.3, (x) => { x.rep(3); x.feel(8, '뿌듯함'); x.a.tr.C = clamp(x.a.tr.C + 0.5); return { t: '주인을 찾아 주고 칭찬을 받았다', v: 1 }; }),
    O('keep', '가진다', x => (100 - x.a.tr.C) * 0.4 + 5, (x) => { x.earn(x.M * 0.03, '주운 돈'); x.fun(10); if (x.a.tr.C > 45) x.think('주인이 찾고 있을 텐데…'); return '몰래 챙겼다'; }),
  ],
});

// ══════════════════════════ 일상 갈림길 — 10대 ══════════════════════════
D('t_club', {
  cat: 'school', icon: '🎸', ages: [13, 19], w: 1, cool: 365,
  when: x => x.a.school && x.era >= 4,
  q: (x) => `동아리에 들어갈까, 공부에 집중할까?`,
  opts: (x) => {
    const clubs = [['밴드부', 'art'], ['축구부', 'athletics'], ['과학반', 'research'], ['연극반', 'art'], ['요리반', 'craft'], ['방송반', 'trade']];
    const c1 = pick(clubs);
    return [
      O('club', `${c1[0]}에 들어간다`, x => x.a.tr.E * 0.3 + x.a.tr.O * 0.3 + (c1[1] === 'athletics' ? x.a.ab.ath * 0.2 : x.a.ab.cre * 0.2), (x) => { x.a.club = c1[0]; x.sk(c1[1], 4); x.fun(25); const b = x.peer(1); if (b) x.bond(b, 6, 6); x.life(`${c1[0]}에 들어갔다`, '🎸'); return `${c1[0]}에서 새 친구들을 만났다`; }),
      O('study', '공부에 집중한다', x => x.a.tr.C * 0.4 + x.a.amb * 0.2, (x) => { if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 4); x.stress(4); return '성적이 조금 올랐다'; }),
    ];
  },
});
D('t_parttime', {
  cat: 'money', icon: '🧃', ages: [16, 20], w: 0.8, cool: 200,
  when: x => x.era >= 4 && !x.a.job,
  q: (x) => `방학 동안 ${pick(['편의점', '패스트푸드점', '카페', '전단지 돌리기', '식당 서빙'])} 아르바이트를 할까?`,
  opts: (x) => [
    O('work', '한다', x => x.a.val.wealth * 0.3 + x.a.tr.C * 0.2 + (x.a.money < x.M * 0.3 ? 15 : 0), (x) => { x.earn(x.M * 0.6, '아르바이트'); x.a.nd.energy = clamp(x.a.nd.energy - 20); x.life('처음으로 아르바이트를 해서 돈을 벌었다', '🧃'); x.think('돈 버는 게 이렇게 힘든 거구나.'); return { t: `한 달 일해 ${x.fmt(x.M * 0.6)}을(를) 벌었다`, v: 1 }; }),
    O('rest', '방학엔 쉬고 논다', x => (100 - x.a.tr.C) * 0.3 + x.a.tr.E * 0.2, (x) => { x.fun(30); const b = x.friend(); if (b) x.bond(b, 6, 6); return '친구들과 실컷 놀았다'; }),
    O('study', '학원에 다닌다', x => x.a.amb * 0.3 + x.a.tr.C * 0.2, (x) => { x.spend(x.M * 0.3, '교육'); if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 5); x.stress(5); return '방학 내내 공부했다'; }),
  ],
});
D('t_fight_parent', {
  cat: 'family', icon: '🚪', ages: [13, 20], w: 1, cool: 90,
  when: x => { const p = x.parent(); return p ? { p: p.id } : null; },
  q: (x, d) => `${x.pw(x.get(d.p))}와(과) 크게 다퉜다. 방문을 쾅 닫고 들어왔는데…`,
  opts: (x, d) => [
    O('sorry', '먼저 사과한다', x => x.a.tr.A * 0.5 - x.a.tr.N * 0.1, (x, d) => { const p = x.get(d.p); x.bond(p, 10, 4, 1); x.otherThink(p, '다 컸네, 우리 애.'); return { t: '사과하자 부모님도 미안하다고 하셨다', v: 1 }; }),
    O('sulk', '방에 틀어박힌다', x => x.a.tr.N * 0.4 + (100 - x.a.tr.A) * 0.3, (x, d) => { const p = x.get(d.p); x.bond(p, -5, 1); x.stress(6); return '며칠 동안 말을 섞지 않았다'; }),
    O('talk', '왜 화났는지 차분히 말한다', x => x.a.tr.O * 0.3 + x.a.tr.A * 0.2 + x.age, (x, d) => { const p = x.get(d.p); x.bond(p, 6, 5, 1); rel(x.a, p).t = clamp(rel(x.a, p).t + 6); return { t: '처음으로 부모님과 어른처럼 이야기했다', v: 1 }; }),
  ],
});
D('t_dream', {
  cat: 'school', icon: '🌠', ages: [14, 21], w: 1, cool: 999,
  when: x => { const g = x.a.goals.find(g => ['art', 'sport', 'adventure', 'knowledge'].includes(g.k) && !g.done); return g ? { g: g.k } : null; },
  q: (x, d) => `${({ art: '예술가', sport: '운동선수', adventure: '탐험가', knowledge: '과학자' })[d.g]}이(가) 되고 싶다. 하지만 다들 안정적인 길을 가라고 한다.`,
  opts: (x, d) => [
    O('dream', '꿈을 좇는다', x => x.a.tr.O * 0.4 + x.a.amb * 0.2 + x.a.val[d.g === 'sport' ? 'sport' : d.g] * 0.2, (x, d) => { const k = { art: 'art', sport: 'athletics', adventure: 'athletics', knowledge: 'research' }[d.g]; x.sk(k, 6); x.a.dreamPath = d.g; x.life('꿈을 따라가기로 마음먹었다', '🌠'); x.think('남들이 뭐라 해도 내 길을 갈 거야.'); return { t: '꿈을 향해 연습을 시작했다', v: 0.5 }; }),
    O('safe', '안정적인 길을 택한다', x => x.a.tr.C * 0.3 + x.a.tr.N * 0.2 + x.a.val.wealth * 0.2, (x, d) => { const g = x.a.goals.find(g => g.k === d.g && !g.done); if (g) g.text = g.text.replace(/기$/, '기 (취미로)'); x.a.goals.push({ k: 'wealth', text: '부자 되기', done: false }); return '꿈은 취미로 남겨 두기로 했다'; }),
    O('both', '둘 다 해 본다', x => x.a.tr.C * 0.3 + x.a.ab.int * 0.2, (x, d) => { x.stress(8); x.sk({ art: 'art', sport: 'athletics', adventure: 'athletics', knowledge: 'research' }[d.g], 3); if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 2); return '공부와 꿈을 함께 잡아 보기로 했다'; }),
  ],
});
D('t_skip', {
  cat: 'school', icon: '🏞️', ages: [13, 19], w: 0.6, cool: 120,
  when: x => { const b = x.a.school && x.friend(); return b ? { b: b.id } : null; },
  q: (x, d) => `${ga(hb(x.get(d.b)))} 오늘 수업 빼먹고 ${pick(['바다 보러', '놀러', '오락실에', '강가에'])} 가자고 한다.`,
  opts: (x, d) => [
    O('go', '따라간다', x => (100 - x.a.tr.C) * 0.5 + x.a.tr.E * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, 8, 5); x.fun(35); if (chance(0.45)) { if (x.a.school) x.a.school.grade = clamp(x.a.school.grade - 3); const p = x.parent(); if (p) x.bond(p, -4, 1); return { t: '신나게 놀았지만 들켜서 크게 혼났다', v: -0.3 }; } x.mem(`${wa(b?.name || '친구')} 학교를 빼먹고 놀았다 — 평생 기억할 것 같다`, 5, 0.6); return '아무도 모르게 하루를 신나게 보냈다'; }),
    O('no', '수업에 간다', x => x.a.tr.C * 0.6, (x, d) => { const b = x.get(d.b); x.bond(b, -2, 1); return '수업을 들었다. 친구는 혼자 갔다'; }),
    O('after', '수업 끝나고 가자고 한다', x => x.a.tr.C * 0.3 + x.a.tr.A * 0.3, (x, d) => { const b = x.get(d.b); x.bond(b, 4, 4); x.fun(15); return '방과 후에 같이 놀았다'; }),
  ],
});
D('t_exam_night', {
  cat: 'school', icon: '📝', ages: [12, 23], w: 0.7, cool: 90,
  when: x => !!x.a.school,
  q: (x) => `내일이 ${x.age >= 17 && x.era >= 4 ? (x.era >= 5 ? '수능' : '입학시험') : x.era <= 3 ? '훈장님의 시험' : '기말고사'}(이)다. 오늘 밤 어떻게 할까?`,
  opts: (x) => [
    O('allnight', '밤을 새워 공부한다', x => x.a.tr.C * 0.4 + x.a.amb * 0.3 + x.a.tr.N * 0.1, (x) => { x.a.nd.energy = clamp(x.a.nd.energy - 40); const ok = chance(0.5 + x.a.ab.int / 300); if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + (ok ? 4 : -1)); return ok ? { t: '밤샘 덕분에 시험을 잘 봤다', v: 1 } : { t: '시험 중에 졸아 버렸다', v: -0.5 }; }),
    O('sleep', '푹 자고 컨디션을 챙긴다', x => (100 - x.a.tr.N) * 0.3 + x.a.ab.int * 0.2, (x) => { if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 2); x.stress(-4); return '맑은 정신으로 시험을 봤다'; }),
    O('group', '친구들과 같이 공부한다', x => x.a.tr.E * 0.4, (x) => { const b = x.friend(); if (b) x.bond(b, 5, 5); if (x.a.school) x.a.school.grade = clamp(x.a.school.grade + 1); return '같이 공부하다 수다가 더 길었다'; }),
  ],
});

// ══════════════════════════ 일상 갈림길 — 청년 · 연애 ══════════════════════════
D('blind_date', {
  cat: 'love', icon: '☕', ages: [20, 45], w: 1.6, cool: 90,
  when: x => {
    if (x.a.partner || x.a.spouse || x.age < 20) return null;
    const pool = x.w.residents(x.a.sid).filter(b => b.id !== x.a.id && !b.partner && !b.spouse && Math.abs(ageOf(b, x.now) - x.age) < 7 && attraction(x.w, x.a, b) > 30 && attraction(x.w, b, x.a) > 20 && !(x.a.rels[b.id]?.n > 3));
    const b = pool.sort((p, q) => attraction(x.w, x.a, q) - attraction(x.w, x.a, p))[0];
    const by = x.friend() || x.parent() || x.coworker();
    return b ? { b: b.id, by: by?.id ?? null } : null;
  },
  q: (x, d) => { const by = x.get(d.by); const how = x.E(['부족 어른이 짝을 맺어 주겠다고 한다', '마을 어른이 혼처를 알아봐 주겠다고 한다', '중매쟁이가 혼처를 들고 왔다', '중매쟁이가 혼처를 들고 왔다', '맞선 자리가 들어왔다', '소개팅 제안이 들어왔다', '소개팅 제안이 들어왔다', '매칭 서비스가 어울리는 사람을 찾았다']); return `${by ? `${ga(by.name)} ` : ''}${how}. 상대는 ${Math.floor(ageOf(x.get(d.b), x.now))}살 ${x.get(d.b).name}. 나가 볼까?`; },
  ok: (x, d) => { const b = x.here(d.b); return b && !b.partner && !b.spouse && !x.a.partner && !x.a.spouse; },
  opts: (x, d) => [
    O('go', '나가 본다', x => x.a.tr.E * 0.3 + x.a.tr.O * 0.3 + x.a.val.family * 0.2, (x, d) => {
      const b = x.here(d.b); if (!b) return '';
      const res = converse(x.w, x.a, b, 'date');
      const r = rel(x.a, b), rb = rel(b, x.a);
      const att = attraction(x.w, x.a, b), attB = attraction(x.w, b, x.a);
      const where = x.E(['모닥불 가', '정자나무 아래', '주막', '주막', '다방', '카페', '카페', '전망 라운지']);
      if (!res.quarrel && att > 45 && attB > 40) { r.r = clamp(r.r + 14); rb.r = clamp(rb.r + 10); x.mem(`${where}에서 ${b.name}을(를) 처음 만났다. 마음이 설렌다`, 6, 0.7, [b.id]); remember(x.w, b, `${where}에서 ${x.a.name}을(를) 만났다. 괜찮은 사람 같다`, 5, 0.5, [x.a.id], 'love'); const by = x.get(d.by); if (by) x.bond(by, 3, 1); return { t: `${where}에서 ${b.name}을(를) 만났다. 서로 마음에 들어 다시 만나기로 했다`, v: 1 }; }
      r.r = clamp(r.r + 2); x.mem(`${b.name}을(를) 만났지만 서로 잘 맞지 않았다`, 3, -0.1, [b.id]);
      return { t: `${b.name}을(를) 만났지만 별다른 느낌은 없었다`, v: -0.2 };
    }),
    O('no', '정중히 사양한다', x => (100 - x.a.tr.E) * 0.3 + (100 - x.a.val.family) * 0.2 + x.a.amb * 0.1, (x) => '아직은 혼자가 편하다며 사양했다'),
  ],
});
D('cohabit', {
  cat: 'love', icon: '🗝️', ages: [20, 60], era: [5, 7], w: 1, cool: 200,
  when: x => { const b = x.mate(); if (!b || x.a.spouse || x.a.cohab || b.home === x.a.home) return null; const r = x.a.rels[b.id]; return r && r.r > 50 && x.now - (r.since || x.now) > 120 * DAY ? { b: b.id } : null; },
  q: (x, d) => `${wa(hb(x.get(d.b)))} 함께 살아 볼까?`,
  opts: (x, d) => [
    O('yes', '함께 산다', x => x.a.tr.O * 0.4 + (x.a.rels[d.b]?.r || 0) * 0.3 - x.a.val.family * 0.1, (x, d) => { const b = x.here(d.b); if (!b) return ''; return x.w.cohabit(x.a, b) ? { t: `${wa(b.name)} 한집에 살게 되었다`, v: 0.5 } : '함께 살 집이 마땅치 않아 미뤘다'; }),
    O('marry', '결혼하고 나서', x => x.a.val.family * 0.4 + x.a.tr.C * 0.2, (x) => '결혼한 뒤에 같이 살기로 했다'),
  ],
});
D('first_salary', {
  cat: 'money', icon: '💵', ages: [17, 35], w: 3, cool: 999,
  when: x => x.a.job && !x.a.flags?.firstPayAsk && x.now - x.a.job.since > 25 * DAY && x.era >= 2,
  q: (x) => `첫 월급을 받았다! 어디에 쓸까?`,
  opts: (x) => [
    O('parents', x.parents().length ? '부모님 선물을 산다' : '고마운 분께 선물을 한다', x => x.a.val.family * 0.4 + x.a.tr.A * 0.3, (x) => { x.a.flags = { ...(x.a.flags || {}), firstPayAsk: 1, firstSalary: 1 }; const p = x.parent() || x.friend(); x.spend(x.M * 0.25, '선물'); x.bond(p, 10, 3); x.other(p, `${ga(x.a.name)} 첫 월급으로 선물을 사 왔다`, 7, 1); if (p) x.otherThink(p, '다 키웠구나…'); return { t: `${p ? p.name + '께 ' : ''}선물을 드렸다. 눈시울이 붉어지셨다`, v: 1 }; }),
    O('save', '전부 저축한다', x => x.a.tr.C * 0.5 + x.a.val.wealth * 0.2, (x) => { x.a.flags = { ...(x.a.flags || {}), firstPayAsk: 1, firstSalary: 1 }; x.a.tr.C = clamp(x.a.tr.C + 0.5); return '첫 월급을 고스란히 저축했다'; }),
    O('me', '나에게 선물한다', x => (100 - x.a.tr.C) * 0.3 + x.a.tr.E * 0.2 + 10, (x) => { x.a.flags = { ...(x.a.flags || {}), firstPayAsk: 1, firstSalary: 1 }; const it = x.E(['', '', '비단 옷', '비단 옷', '손목시계', '좋은 구두', '최신 휴대폰', '홀로그램 안경']); x.spend(x.M * 0.3, '쇼핑'); x.fun(30); return `수고한 나에게 ${reul(it)} 선물했다`; }),
  ],
});
D('overtime', {
  cat: 'work', icon: '🌙', ages: [19, 65], w: 1.2, cool: 30,
  when: x => x.a.job && !x.a.job.ceo && x.era >= 4 && (x.mate() || x.kids().length) && true,
  q: (x) => { const m = x.mate(); return `상사가 오늘 야근을 하라고 한다. 그런데 ${m ? `${wa(m.name)} 저녁 약속이` : '아이와 놀아 주기로 한 약속이'} 있다.`; },
  opts: (x) => [
    O('work', '야근한다', x => x.a.tr.C * 0.4 + x.a.amb * 0.3 + (x.a.job?.rank || 0) * 4, (x) => { if (x.a.job) x.a.job.sat = clamp(x.a.job.sat - 2); x.a.nd.energy = clamp(x.a.nd.energy - 15); const b = x.boss(); if (b) x.bond(b, 4, 2); const m = x.mate() || x.kid(); if (m) x.bond(m, -5, 1); x.earn(x.M * 0.03, '야근 수당'); return '밤늦게까지 일했다. 약속은 미뤘다'; }),
    O('home', '약속을 지킨다', x => x.a.val.family * 0.5 + x.a.tr.A * 0.2, (x) => { const m = x.mate() || x.kid(); if (m) x.bond(m, 6, 4); const b = x.boss(); if (b && chance(0.4)) x.bond(b, -3, 1); x.stress(-6); return { t: `${m ? m.name + '와(과) ' : ''}즐거운 저녁을 보냈다`, v: 1 }; }),
    O('half', '일을 집에 싸 들고 간다', x => x.a.tr.C * 0.3 + 10, (x) => { x.stress(5); x.a.nd.energy = clamp(x.a.nd.energy - 8); return '저녁은 같이 먹고 밤에 일을 마저 했다'; }),
  ],
});
D('lend', {
  cat: 'money', icon: '🤝', ages: [18, 90], w: 1.2, cool: 120,
  when: x => { const b = x.friend() || x.sib(); if (!b || x.a.money < x.M * 1.5) return null; return { b: b.id, amt: Math.round(x.M * (0.3 + rand() * 1.2) * 100) / 100 }; },
  q: (x, d) => `${ga(hb(x.get(d.b)))} ${x.fmt(d.amt)}만 빌려 달라고 한다. ${pick(['급한 일이 생겼다며', '사업이 어렵다며', '집세가 밀렸다며', '병원비가 필요하다며'])}.`,
  opts: (x, d) => [
    O('lend', '빌려준다', x => x.a.tr.A * 0.4 + (x.a.rels[d.b]?.a || 0) * 0.3 - (x.a.money < d.amt * 3 ? 20 : 0), (x, d) => { const b = x.get(d.b); if (!b) return ''; const v = x.spend(d.amt, '빌려준 돈'); b.money += v; x.bond(b, 8, 3); x.a.loans = x.a.loans || []; x.a.loans.push({ to: b.id, amt: v, at: Math.round(x.now), due: Math.round(x.now + (30 + rand() * 120) * DAY) }); if (x.a.loans.length > 6) x.a.loans.shift(); return `${b.name}에게 ${x.fmt(v)}을(를) 빌려주었다`; }),
    O('half', '절반만 빌려준다', x => x.a.tr.C * 0.3 + 15, (x, d) => { const b = x.get(d.b); if (!b) return ''; const v = x.spend(d.amt / 2, '빌려준 돈'); b.money += v; x.bond(b, 3, 2); x.a.loans = x.a.loans || []; x.a.loans.push({ to: b.id, amt: v, at: Math.round(x.now), due: Math.round(x.now + 60 * DAY) }); return `${x.fmt(v)}만 빌려주었다`; }),
    O('no', '거절한다', x => (100 - x.a.tr.A) * 0.4 + x.a.tr.C * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, -7, 1); return `${b?.name || '친구'}이(가) 서운해했다`; }),
  ],
});
D('wallet', {
  cat: 'life', icon: '👛', ages: [16, 95], w: 0.5, cool: 500,
  when: x => x.era >= 2,
  q: (x) => `길에서 두툼한 ${x.E(['', '', '돈주머니', '돈주머니', '지갑', '지갑', '지갑', '지갑'])}을(를) 주웠다. 신분증도 들어 있다.`,
  opts: (x) => [
    O('return', '주인을 찾아 준다', x => x.a.tr.C * 0.4 + x.a.tr.A * 0.4, (x) => { x.rep(5); x.feel(10, '뿌듯함'); const b = x.adultStranger(); if (b) { x.bond(b, 12, 3); x.other(b, `잃어버린 지갑을 ${ga(x.a.name)} 찾아 주었다`, 6, 0.9); return { t: `주인 ${b.name}이(가) 몇 번이고 고맙다고 했다`, v: 1 }; } return { t: '주인을 찾아 돌려주었다', v: 1 }; }),
    O('keep', '돈만 챙긴다', x => (100 - x.a.tr.C) * 0.4 + (100 - x.a.tr.A) * 0.2 + (x.a.money < x.M ? 15 : 0) - 15, (x) => { x.earn(x.M * 0.2, '주운 돈'); x.stress(8); x.rep(-1); return '돈만 챙기고 지갑은 우체통에 넣었다'; }),
  ],
});
D('health_check', {
  cat: 'health', icon: '🩺', ages: [35, 100], era: [5, 7], w: 0.9, cool: 365,
  when: x => true,
  q: (x) => `${x.E(['약초꾼의 진맥', '약초꾼의 진맥', '의원의 진맥', '의원의 진맥', '병원 검진', '종합 건강검진', '종합 건강검진', '정밀 유전자 검진'])}을(를) 받아 볼까? (${x.fmt(x.M * 0.15)})`,
  opts: (x) => [
    O('go', '받는다', x => x.a.tr.C * 0.4 + x.a.tr.N * 0.3 + (x.age - 40) * 0.6, (x) => { x.spend(x.M * 0.15, '의료'); if (chance(0.15 + x.age / 400)) { x.a.sick = Math.max(0, x.a.sick - 20); x.health(4); x.life('건강검진에서 병을 일찍 찾아 치료했다', '🩺'); return { t: '병을 일찍 찾아내 치료를 시작했다', v: 1 }; } x.health(1.5); return { t: '다행히 큰 이상은 없었다', v: 0.5 }; }),
    O('skip', '바쁘니까 다음에', x => (100 - x.a.tr.C) * 0.3 + (100 - x.a.tr.N) * 0.2, (x) => { if (chance(0.06)) x.a.sick = Math.max(x.a.sick, 25); return '다음으로 미뤘다'; }),
  ],
});
D('sick_work', {
  cat: 'health', icon: '🤒', ages: [18, 75], w: 2, cool: 30,
  when: x => x.a.sick > 15 && x.a.job && !x.a.job.ceo,
  q: (x) => `몸이 아프다. 그래도 출근할까?`,
  opts: (x) => [
    O('rest', '쉬면서 병원에 간다', x => (100 - x.a.health) * 0.4 + x.a.sick * 0.4, (x) => { x.a.sick = Math.max(0, x.a.sick - 10); x.spend(x.M * 0.03, '의료'); x.a.restDay = Math.round(x.now + DAY); return { t: '하루 푹 쉬었더니 한결 나아졌다', v: 1 }; }),
    O('work', '참고 출근한다', x => x.a.tr.C * 0.4 + (x.a.money < x.M ? 20 : 0), (x) => { x.a.sick = clamp(x.a.sick + 6); const b = x.coworker(); if (b && chance(0.25)) b.sick = Math.max(b.sick, 15); if (x.a.job) x.a.job.sat = clamp(x.a.job.sat - 2); return { t: '꾸역꾸역 일했지만 병이 더 깊어졌다', v: -0.5 }; }),
  ],
});
D('team_dinner', {
  cat: 'work', icon: '🍻', ages: [19, 65], era: [4, 7], w: 1, cool: 40,
  when: x => x.a.job?.co != null && x.coworkers().length >= 2,
  q: (x) => `오늘 ${x.era >= 6 ? '팀 회식' : '회식'}이(가) 있다. 갈까?`,
  opts: (x) => [
    O('go', '간다', x => x.a.tr.E * 0.5 + x.a.amb * 0.2, (x) => { for (const b of x.coworkers().slice(0, 4)) x.bond(b, 3, 4); x.fun(15); x.a.nd.energy = clamp(x.a.nd.energy - 12); return '동료들과 왁자지껄 저녁을 먹었다'; }),
    O('first', '1차만 하고 일찍 빠진다', x => 30 + x.a.tr.C * 0.1, (x) => { for (const b of x.coworkers().slice(0, 2)) x.bond(b, 2, 2); return '1차만 하고 집에 갔다'; }),
    O('skip', '쉬고 싶다고 빠진다', x => (100 - x.a.tr.E) * 0.5, (x) => { x.stress(-4); x.fun(8); return '집에서 푹 쉬었다'; }),
  ],
});
D('witness', {
  cat: 'work', icon: '⚖️', ages: [19, 70], w: 0.5, cool: 400,
  when: x => { const b = x.coworker(); return b && x.a.job?.co != null ? { b: b.id } : null; },
  q: (x, d) => `동료 ${ga(hb(x.get(d.b)))} 회삿돈을 몰래 빼돌리는 걸 봤다.`,
  opts: (x, d) => [
    O('report', '윗사람에게 알린다', x => x.a.tr.C * 0.5 + (100 - x.a.tr.A) * 0.1, (x, d) => { const b = x.get(d.b); x.bond(b, -25, 0); x.rep(4); const co = x.co(); if (co && b?.job?.co === co.id && chance(0.6)) { leaveJob(x.w, b, '해고'); return { t: `${b.name}은(는) 해고되었다`, v: 0.5 }; } return '알렸지만 흐지부지 넘어갔다'; }),
    O('talk', '직접 그만두라고 말한다', x => x.a.tr.A * 0.3 + (100 - x.a.tr.N) * 0.3, (x, d) => { const b = x.get(d.b); if (b && chance(0.5 + x.a.ab.cha / 300)) { x.bond(b, 4, 3); return { t: `${b.name}이(가) 잘못을 인정하고 돈을 돌려놓았다`, v: 1 }; } x.bond(b, -10, 1); return { t: `${b.name}은(는) 시치미를 뗐다`, v: -0.3 }; }),
    O('ignore', '못 본 척한다', x => x.a.tr.N * 0.3 + 10, (x) => { x.stress(6); return '모른 척했지만 마음이 편치 않다'; }),
  ],
});
D('neighbor_noise', {
  cat: 'home', icon: '🔊', ages: [18, 100], w: 0.7, cool: 120,
  when: x => { const b = x.neighbor(); return b ? { b: b.id } : null; },
  q: (x, d) => `이웃 ${neun(hb(x.get(d.b)))} 밤마다 너무 시끄럽다.`,
  opts: (x, d) => [
    O('visit', '찾아가 정중히 말한다', x => x.a.tr.A * 0.3 + (100 - x.a.tr.N) * 0.3, (x, d) => { const b = x.get(d.b); if (b && chance(0.5 + b.tr.A / 200)) { x.bond(b, 4, 3); return { t: `${b.name}이(가) 미안하다며 떡을 돌렸다`, v: 1 }; } x.bond(b, -8, 2); return { t: '말다툼만 하고 돌아왔다', v: -0.5 }; }),
    O('bear', '참는다', x => x.a.tr.A * 0.4 + x.a.tr.N * 0.2, (x) => { x.stress(6); x.a.nd.energy = clamp(x.a.nd.energy - 10); return '귀마개를 하고 잤다'; }),
    O('report', x.era >= 4 ? '관리실·경찰에 신고한다' : '마을 어른께 말한다', x => (100 - x.a.tr.A) * 0.4, (x, d) => { const b = x.get(d.b); x.bond(b, -12, 1); return '조용해졌지만 이웃과 서먹해졌다'; }),
  ],
});
D('donate', {
  cat: 'life', icon: '🎁', ages: [18, 110], w: 0.7, cool: 120,
  when: x => { const cs = x.w.communitySite(x.s); return x.a.money > x.M * 2 && cs ? { site: cs.id } : null; },
  q: (x, d) => `마을 ${x.w.buildings.get(d.site)?.name || '공사'}에 보탤 돈을 모으고 있다.`,
  opts: (x, d) => [
    O('big', `${x.fmt(x.M * 0.5)}을(를) 낸다`, x => x.a.val.community * 0.5 + x.a.tr.A * 0.2 - 10, (x, d) => { const b = x.w.buildings.get(d.site); const v = x.spend(x.M * 0.5, '기부'); if (b) b.budget = (b.budget || 0) + v; x.rep(5); x.feel(8, '뿌듯함'); x.life(`마을 ${b?.name || '공사'}에 큰돈을 보탰다`, '🎁'); return { t: '마을 사람들이 고마워했다', v: 1 }; }),
    O('small', `${x.fmt(x.M * 0.08)}만 낸다`, x => 30, (x, d) => { const b = x.w.buildings.get(d.site); const v = x.spend(x.M * 0.08, '기부'); if (b) b.budget = (b.budget || 0) + v; x.rep(1); return '마음만큼 보탰다'; }),
    O('labor', '돈 대신 일손을 보탠다', x => x.a.ab.ath * 0.3 + x.a.tr.C * 0.2, (x, d) => { x.a.nd.energy = clamp(x.a.nd.energy - 15); const b = x.w.buildings.get(d.site); if (b && !b.done) b.progress = Math.min(b.work, b.progress + 4); x.rep(2); return '주말에 공사장에 나가 일을 거들었다'; }),
  ],
});
D('car', {
  cat: 'money', icon: '🚗', ages: [21, 75], era: [5, 7], w: 0.8, cool: 365,
  when: x => !x.a.car && x.a.money > x.M * 8,
  q: (x) => `차를 한 대 살까? (${x.fmt(x.M * 6)} 정도)`,
  opts: (x) => [
    O('buy', '산다', x => x.a.val.wealth * 0.2 + x.a.tr.E * 0.2 + (x.kids().length ? 15 : 0) + (x.a.money > x.M * 20 ? 15 : 0), (x) => { x.spend(x.M * 6, '자동차 구입'); x.a.car = true; x.life('첫 차를 샀다', '🚗'); x.fun(25); x.w.dirtyAnnMeta.add(x.a.id); return '첫 차를 뽑았다'; }),
    O('bus', '버스로 충분하다', x => x.a.tr.C * 0.3 + (100 - x.a.val.wealth) * 0.3, (x) => '대중교통으로 다니기로 했다'),
  ],
});
D('invest', {
  cat: 'money', icon: '📈', ages: [24, 80], era: [4, 7], w: 0.8, cool: 200,
  when: x => (x.a.money > x.M * 4 ? { b: (x.friend() || x.coworker())?.id ?? null, amt: Math.round(x.a.money * 0.3 * 100) / 100 } : null),
  q: (x, d) => `${x.get(d.b) ? ga(x.get(d.b).name) + ' ' : ''}${pick(x.era >= 6 ? ['코인이 오른다며', '이 주식은 무조건 오른다며', '땅값이 뛸 거라며'] : ['이 회사 주식이 오른다며', '땅을 사 두면 오른다며', '계를 들자며'])} 투자를 권한다.`,
  opts: (x, d) => {
    const amt = Math.min(x.a.money, d.amt || x.a.money * 0.3);
    return [
      O('all', `${x.fmt(amt)}을(를) 투자한다`, x => (100 - x.a.tr.N) * 0.3 + x.a.val.wealth * 0.4 + x.a.tr.O * 0.1 - 25, (x) => { const v = x.spend(amt, '투자'); const r = rand(); if (r < 0.35) { const g = v * (1.4 + rand()); x.earn(g, '투자 수익'); x.life(`투자로 큰돈을 벌었다 (+${x.fmt(g - v)})`, '📈'); return { t: `대박! ${x.fmt(g)}이(가) 되었다`, v: 1 }; } if (r < 0.65) { x.earn(v * 1.05, '투자 수익'); return '조금 올랐다'; } x.earn(v * 0.3, '투자 손실 회수'); x.feel(-15, '절망', '투자'); x.life(`투자로 큰돈을 잃었다 (-${x.fmt(v * 0.7)})`, '📉'); return { t: `${x.fmt(v * 0.7)}을(를) 잃었다`, v: -1 }; }),
      O('little', '조금만 해 본다', x => 30 + x.a.tr.C * 0.1, (x) => { const v = x.spend(x.a.money * 0.05, '투자'); x.earn(v * (0.6 + rand() * 0.9), '투자 수익'); return '조금 넣어 보고 공부하기로 했다'; }),
      O('no', '안 한다', x => x.a.tr.N * 0.3 + x.a.tr.C * 0.3, (x) => '세상에 공짜는 없다며 거절했다'),
    ];
  },
});
D('promotion_offer', {
  cat: 'work', icon: '📈', ages: [26, 63], w: 0.8, cool: 300,
  when: x => x.a.job?.co != null && !x.a.job.ceo && (x.a.job.rank || 0) < 3 && x.now - x.a.job.since > 200 * DAY,
  q: (x) => `팀장 자리를 맡아 달라고 한다. 월급은 오르지만 책임도 무거워진다.`,
  opts: (x) => [
    O('yes', '맡는다', x => x.a.amb * 0.5 + x.a.tr.C * 0.2 - x.a.tr.N * 0.2, (x) => { if (!x.a.job) return ''; x.a.job.rank = Math.min(3, (x.a.job.rank || 0) + 1); x.a.job.sat = clamp(x.a.job.sat + 6); x.stress(10); x.life(`${jobTitle(x.w, x.a)}(으)로 승진했다`, '📈'); return { t: '승진했다. 어깨가 무겁다', v: 1 }; }),
    O('no', '지금 자리가 좋다', x => (100 - x.a.amb) * 0.4 + x.a.val.family * 0.2, (x) => { x.stress(-3); return '지금 일에 집중하기로 했다'; }),
  ],
});

// ══════════════════════════ 일상 갈림길 — 가족 · 중년 ══════════════════════════
D('chores', {
  cat: 'family', icon: '🧺', ages: [22, 85], w: 1, cool: 60,
  when: x => { const sp = x.spouse(); return sp && x.a.home && sp.home === x.a.home ? { b: sp.id } : null; },
  q: (x, d) => `${wa(hb(x.get(d.b)))} 집안일 분담으로 언성이 높아졌다.`,
  opts: (x, d) => [
    O('fair', '반반 나누자고 정한다', x => x.a.tr.A * 0.3 + x.a.tr.C * 0.3, (x, d) => { const b = x.get(d.b); x.bond(b, 6, 3, 1); x.a.nd.energy = clamp(x.a.nd.energy - 5); return { t: '요일을 나눠 집안일 표를 만들었다', v: 1 }; }),
    O('me', '내가 더 하겠다고 한다', x => x.a.tr.A * 0.5 - 5, (x, d) => { const b = x.get(d.b); x.bond(b, 8, 3, 1); x.stress(5); return '이번엔 내가 더 하기로 했다'; }),
    O('argue', '내가 맞다고 끝까지 따진다', x => (100 - x.a.tr.A) * 0.5 + x.a.tr.N * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, -9, 1, 1); return { t: '며칠 동안 냉랭했다', v: -0.5 }; }),
  ],
});
D('inlaws', {
  cat: 'family', icon: '🏡', ages: [22, 75], w: 0.8, cool: 120,
  when: x => { const sp = x.spouse(); if (!sp) return null; const ip = sp.parents.map(id => x.here(id)).find(Boolean); return ip ? { p: ip.id, b: sp.id } : null; },
  q: (x, d) => `배우자의 부모님 ${ga(hb(x.get(d.p)))} 이번 명절에 꼭 와서 며칠 묵고 가라고 하신다.`,
  opts: (x, d) => [
    O('go', '기꺼이 간다', x => x.a.tr.A * 0.4 + x.a.val.family * 0.3, (x, d) => { const p = x.get(d.p), b = x.get(d.b); x.bond(p, 8, 5); x.bond(b, 4, 2); x.a.nd.energy = clamp(x.a.nd.energy - 10); return { t: '명절 내내 북적북적 즐겁게 보냈다', v: 1 }; }),
    O('short', '하루만 있다 온다', x => 30, (x, d) => { const p = x.get(d.p); x.bond(p, 2, 3); return '하룻밤만 자고 돌아왔다'; }),
    O('own', '이번엔 우리 부모님 댁에 간다', x => (100 - x.a.tr.A) * 0.3 + (x.parents().length ? 15 : -20), (x, d) => { const p = x.get(d.p), b = x.get(d.b); x.bond(p, -4, 1); x.bond(b, -3, 1); const mp = x.parent(); if (mp) x.bond(mp, 6, 4); return '우리 부모님 댁에 다녀왔다. 배우자는 조금 서운해했다'; }),
  ],
});
D('parent_care', {
  cat: 'family', icon: '🧓', ages: [30, 80], w: 1.4, cool: 200,
  when: x => { const p = x.parents().find(p => ageOf(p, x.now) > 68 && (p.sick > 15 || p.health < 45)); return p ? { p: p.id } : null; },
  q: (x, d) => `${x.pw(x.get(d.p))}의 건강이 많이 안 좋아지셨다.`,
  opts: (x, d) => [
    O('live', '집으로 모신다', x => x.a.val.family * 0.5 + x.a.tr.A * 0.2 + (x.era <= 4 ? 15 : 0) - (x.spouse() ? 5 : 0), (x, d) => { const p = x.get(d.p); const h = x.a.home && x.w.buildings.get(x.a.home); if (p && h) { x.w.moveHome(p, h); x.bond(p, 12, 6, 1); x.stress(8); p.stress = clamp(p.stress - 10); x.life(`${x.pw(p)}을(를) 모시고 살게 되었다`, '🧓'); return { t: `${x.pw(p)}을(를) 집으로 모셨다`, v: 0.5 }; } return '모실 집이 마땅치 않아 자주 찾아뵙기로 했다'; }),
    O('visit', '자주 찾아뵌다', x => 30 + x.a.tr.C * 0.1, (x, d) => { const p = x.get(d.p); x.bond(p, 6, 4); x.a.nd.energy = clamp(x.a.nd.energy - 8); if (p) p.health = clamp(p.health + 2); return '주말마다 찾아뵙기로 했다'; }),
    x.era >= 5 ? O('care', '요양 시설을 알아본다', x => x.a.amb * 0.2 + (x.a.money > x.M * 6 ? 15 : -10) + (100 - x.a.val.family) * 0.2, (x, d) => { const p = x.get(d.p); x.spend(x.M * 1.2, '요양비'); if (p) { p.health = clamp(p.health + 6); p.sick = Math.max(0, p.sick - 15); x.bond(p, -2, 2); } x.think('이게 최선일까… 마음이 무겁다.'); return '좋은 요양 시설에 모셨다'; }) : null,
  ],
});
D('kid_path', {
  cat: 'family', icon: '🎒', ages: [36, 75], w: 1, cool: 300,
  when: x => { const k = x.kids().find(k => { const g = ageOf(k, x.now); return g > 15 && g < 22 && k.goals.some(q => ['art', 'sport', 'adventure'].includes(q.k) && !q.done); }); return k ? { k: k.id } : null; },
  q: (x, d) => { const k = x.get(d.k); const g = k.goals.find(q => ['art', 'sport', 'adventure'].includes(q.k) && !q.done); return `${neun(x.call(k))} ${({ art: '예술', sport: '운동', adventure: '세상 구경' })[g.k]}을(를) 하겠다고 한다. 어떻게 할까?`; },
  opts: (x, d) => [
    O('cheer', '응원한다', x => x.a.tr.O * 0.4 + x.a.tr.A * 0.3, (x, d) => { const k = x.get(d.k); x.bond(k, 12, 4, 1); x.spend(x.M * 0.4, '교육'); if (k) { k.conf = clamp(k.conf + 8); remember(x.w, k, `${x.call(x.a)}이(가) 내 꿈을 응원해 주었다`, 8, 0.9, [x.a.id], 'family'); } return { t: '아이가 눈물을 글썽이며 고마워했다', v: 1 }; }),
    O('safe', '안정적인 길을 권한다', x => x.a.tr.C * 0.3 + x.a.tr.N * 0.3 + x.a.val.wealth * 0.2, (x, d) => { const k = x.get(d.k); x.bond(k, -8, 2); if (k && chance(0.5)) { const g = k.goals.find(q => ['art', 'sport', 'adventure'].includes(q.k) && !q.done); if (g) g.text = g.text.replace(/기$/, '기 (접어 둔 꿈)'); return '아이는 고개를 숙이고 공부를 택했다'; } return '아이는 반발하며 자기 길을 가겠다고 했다'; }),
    O('deal', '1년만 해 보라고 한다', x => 30 + x.a.tr.A * 0.1, (x, d) => { const k = x.get(d.k); x.bond(k, 4, 3); return '1년 동안 해 보고 다시 이야기하기로 했다'; }),
  ],
});
D('midlife', {
  cat: 'life', icon: '🌀', ages: [40, 58], w: 0.7, cool: 999,
  when: x => x.a.mood < 15 || x.a.job?.sat < 50 || x.a.goals.some(g => !g.done),
  q: (x) => `${Math.floor(x.age)}살. 문득 이대로 늙어도 괜찮을까 싶다.`,
  opts: (x) => [
    O('hobby', '새 취미를 시작한다', x => x.a.tr.O * 0.4 + 10, (x) => { const h = pick(['기타', '마라톤', '도자기', '등산', '사진', '그림', '춤', '요리', '낚시']); x.a.hobby = h; x.life(`늦깎이 ${h} 취미를 시작했다`, '🎐'); x.fun(30); x.stress(-12); return { t: `${reul(h)} 시작했다. 다시 설렌다`, v: 1 }; }),
    O('trip', '혼자 긴 여행을 떠난다', x => x.a.val.adventure * 0.4 + (x.a.money > x.M * 2 ? 10 : -15), (x) => { x.spend(x.M * 0.8, '여행'); x.a.nd.fun = 100; x.stress(-25); x.life('혼자 긴 여행을 다녀왔다', '🎒'); return { t: '돌아오니 세상이 조금 달라 보였다', v: 1 }; }),
    O('work', '일에 더 몰두한다', x => x.a.amb * 0.4 + x.a.tr.C * 0.2, (x) => { if (x.a.job) x.a.job.sat = clamp(x.a.job.sat + 5); x.stress(5); return '다시 일에 매달렸다'; }),
    O('family', '가족과 시간을 더 보낸다', x => x.a.val.family * 0.5, (x) => { for (const k of [x.spouse(), ...x.kids()].filter(Boolean).slice(0, 3)) x.bond(k, 6, 5); x.stress(-10); return { t: '가족과 저녁을 함께하는 날이 늘었다', v: 1 }; }),
  ],
});
D('company_grow', {
  cat: 'work', icon: '🏗️', ages: [20, 80], w: 1.2, cool: 120,
  when: x => { const co = x.co(); return x.a.job?.ceo && co && !co.dead && co.money > x.M * 8 ? { co: co.id } : null; },
  q: (x, d) => `${x.w.companies.get(d.co)?.name}에 여유 자금이 생겼다. 어떻게 할까?`,
  opts: (x, d) => [
    O('hire', '사람을 더 뽑는다', x => x.a.amb * 0.4 + x.a.tr.O * 0.2, (x, d) => { const co = x.w.companies.get(d.co); if (!co) return ''; co.slots = (co.slots || 1) + 2; return { t: '직원 두 명을 더 뽑기로 했다', v: 0.5 }; }),
    O('bonus', '직원들에게 상여금을 준다', x => x.a.tr.A * 0.5, (x, d) => { const co = x.w.companies.get(d.co); if (!co) return ''; const pool = co.money * 0.15; co.money -= pool; const emps = co.emp.map(id => x.w.anns.get(id)).filter(Boolean); for (const e of emps) { e.money += pool / Math.max(1, emps.length); book(x.w, e, 'inc', '상여금', pool / Math.max(1, emps.length)); if (e.job) e.job.sat = clamp(e.job.sat + 8); if (e.id !== x.a.id) x.bond(e, 5, 2); } x.rep(2); return { t: `직원 ${emps.length}명에게 상여금을 나눠 주었다`, v: 1 }; }),
    O('save', '위기에 대비해 쌓아 둔다', x => x.a.tr.C * 0.4 + x.a.tr.N * 0.2, (x) => '어려운 때를 대비해 아껴 두기로 했다'),
    O('pay', '내 몫을 챙긴다', x => x.a.val.wealth * 0.4 + (100 - x.a.tr.A) * 0.2 - 10, (x, d) => { const co = x.w.companies.get(d.co); if (!co) return ''; const v = co.money * 0.2; co.money -= v; x.earn(v, '배당'); return `${x.fmt(v)}을(를) 배당받았다`; }),
  ],
});

// ══════════════════════════ 일상 갈림길 — 노년 ══════════════════════════
D('inherit', {
  cat: 'family', icon: '📜', ages: [62, 120], w: 1, cool: 999,
  when: x => x.a.money > x.M * 10 && x.kids().length,
  q: (x) => `모은 재산(${x.fmt(x.a.money)})을 어떻게 할까?`,
  opts: (x) => [
    O('give', '자식들에게 미리 나눠 준다', x => x.a.val.family * 0.5 + x.a.tr.A * 0.2, (x) => { const ks = x.kids(); const v = x.a.money * 0.5; for (const k of ks) { k.money += v / ks.length; book(x.w, k, 'inc', '증여', v / ks.length); x.bond(k, 8, 3); } x.spend(v, '증여'); return { t: `자식 ${ks.length}명에게 ${x.fmt(v)}을(를) 나눠 주었다`, v: 1 }; }),
    O('keep', '노후 자금으로 둔다', x => x.a.tr.C * 0.3 + x.a.tr.N * 0.3, (x) => '내 노후는 내가 책임지기로 했다'),
    O('donate', '일부를 사회에 기부한다', x => x.a.val.community * 0.5 - 5, (x) => { const v = x.spend(x.a.money * 0.25, '기부'); x.c.treasury = (x.c.treasury || 0) + v; x.rep(10); x.a.fame = (x.a.fame || 0) + 3; x.life(`재산 ${x.fmt(v)}을(를) 나라에 기부했다`, '🎁'); return { t: '사람들이 오래 기억할 것이다', v: 1 }; }),
  ],
});
D('grandkid_care', {
  cat: 'family', icon: '👵', ages: [50, 95], w: 1.2, cool: 200,
  when: x => { const k = x.kids().find(k => k.kids.some(g => { const b = x.here(g); return b && ageOf(b, x.now) < 7; }) && k.job); return k ? { k: k.id } : null; },
  q: (x, d) => `${neun(x.call(x.get(d.k)))} 맞벌이라 손주를 봐 달라고 한다.`,
  opts: (x, d) => [
    O('yes', '기꺼이 봐 준다', x => x.a.val.family * 0.5 + x.a.tr.A * 0.2 - (100 - x.a.health) * 0.2, (x, d) => { const k = x.get(d.k); x.bond(k, 8, 4); for (const g of (k?.kids || []).map(id => x.here(id)).filter(Boolean)) x.bond(g, 10, 8); x.a.nd.energy = clamp(x.a.nd.energy - 15); x.fun(20); return { t: '손주 웃음소리에 하루가 짧다', v: 1 }; }),
    O('some', '일주일에 사흘만', x => 30, (x, d) => { const k = x.get(d.k); x.bond(k, 3, 3); return '사흘만 봐 주기로 했다'; }),
    O('no', '이제는 내 인생을 살고 싶다', x => (100 - x.a.val.family) * 0.3 + x.a.tr.O * 0.2 + (100 - x.a.health) * 0.3, (x, d) => { const k = x.get(d.k); x.bond(k, -5, 1); x.fun(10); return '미안하지만 거절했다'; }),
  ],
});
D('reconcile', {
  cat: 'friend', icon: '🕊️', ages: [45, 120], w: 0.8, cool: 999,
  when: x => { const id = Object.keys(x.a.rels).find(id => { const r = x.a.rels[id]; return (r.k === 'enemy' || r.k === 'rival' || r.a < -30) && x.here(+id); }); return id ? { b: +id } : null; },
  q: (x, d) => `${wa(hb(x.get(d.b)))} 등 돌린 지 오래다. 이제라도 먼저 손을 내밀까?`,
  opts: (x, d) => [
    O('reach', '먼저 찾아간다', x => x.a.tr.A * 0.4 + (x.age - 50) * 0.5, (x, d) => { const b = x.get(d.b); if (!b) return ''; if (chance(0.45 + b.tr.A / 250)) { const r = rel(x.a, b), rb = rel(b, x.a); r.a = Math.max(r.a, 10); rb.a = Math.max(rb.a, 5); updateKind(x.w, x.a, b); updateKind(x.w, b, x.a); x.life(`오랜 앙금을 풀고 ${wa(b.name)} 화해했다`, '🕊️'); x.stress(-15); return { t: '술 한잔에 오랜 앙금이 풀렸다', v: 1 }; } x.bond(b, -2, 1); return { t: `${neun(b.name)} 아직 마음을 열지 않았다`, v: -0.3 }; }),
    O('letter', '짧은 편지를 보낸다', x => (100 - x.a.tr.E) * 0.3 + x.a.tr.A * 0.2, (x, d) => { const b = x.get(d.b); x.bond(b, 12, 1); return '답장은 없었지만 마음이 가벼워졌다'; }),
    O('no', '그냥 둔다', x => (100 - x.a.tr.A) * 0.4, (x) => '지난 일은 지난 일로 두기로 했다'),
  ],
});
D('elder_remarry', {
  cat: 'love', icon: '🌷', ages: [55, 110], w: 1, cool: 400,
  when: x => { if (x.a.spouse || x.a.partner) return null; const id = Object.keys(x.a.rels).find(id => x.a.rels[id].r > 28 && x.here(+id) && !x.here(+id).spouse && !x.here(+id).partner); return id ? { b: +id } : null; },
  q: (x, d) => `혼자 된 지 오래다. ${wa(hb(x.get(d.b)))} 함께 있으면 마음이 편하다.`,
  opts: (x, d) => [
    O('open', '마음을 연다', x => x.a.tr.O * 0.4 + x.a.tr.E * 0.2 + (x.a.rels[d.b]?.r || 0) * 0.3, (x, d) => { const b = x.here(d.b); if (!b) return ''; x.plan('confess', { b: b.id }, 10); rel(x.a, b).r = clamp(rel(x.a, b).r + 10); return '용기를 내어 마음을 전하기로 했다'; }),
    O('friend', '좋은 벗으로 지낸다', x => 35 + x.a.tr.N * 0.1, (x, d) => { const b = x.here(d.b); x.bond(b, 6, 4); return '말동무로 지내기로 했다'; }),
    O('kids', '자식들 생각에 망설인다', x => x.a.val.family * 0.3, (x) => { x.stress(4); return '자식들 눈치에 마음을 접었다'; }),
  ],
});
D('last_trip', {
  cat: 'life', icon: '🧳', ages: [75, 120], w: 0.6, cool: 999,
  when: x => x.a.health > 35,
  q: (x) => `다리 힘이 남아 있을 때 꼭 가 보고 싶은 곳이 있다.`,
  opts: (x) => [
    O('home', '태어난 고향에 가 본다', x => x.a.val.family * 0.3 + 20, (x) => { x.spend(x.M * 0.3, '여행'); x.stress(-20); x.mem('고향 땅을 다시 밟았다. 어릴 적 그대로였다', 8, 0.9); return { t: '어릴 적 뛰놀던 골목을 걸었다', v: 1 }; }),
    O('family', '자식·손주와 함께 가족 여행', x => x.a.val.family * 0.5, (x) => { x.spend(x.M * 0.6, '여행'); for (const k of [...x.kids(), ...x.grandkids()].slice(0, 5)) x.bond(k, 8, 5); x.life('온 가족이 함께 여행을 다녀왔다', '🧳'); return { t: '평생 남을 가족사진을 찍었다', v: 1 }; }),
    O('stay', '그냥 집이 제일 좋다', x => (100 - x.a.tr.O) * 0.4 + (100 - x.a.health) * 0.3, (x) => { x.stress(-5); return '집에서 조용히 지내기로 했다'; }),
  ],
});
D('elder_center', {
  cat: 'friend', icon: '♟️', ages: [65, 120], w: 0.8, cool: 180,
  when: x => x.a.retired || x.age > 70,
  q: (x) => `${x.E(['모닥불가 노인 모임', '정자나무 그늘', '사랑방', '사랑방', '경로당', '경로당', '경로당', '실버 커뮤니티 센터'])}에서 같이 놀자고 부른다.`,
  opts: (x) => [
    O('go', '나간다', x => x.a.tr.E * 0.5 + 10, (x) => { const b = x.peer(8); if (b) x.bond(b, 6, 6); x.fun(20); x.a.nd.social = clamp(x.a.nd.social + 30); return { t: `${b ? wa(b.name) + ' ' : ''}${pick(['장기를 두며', '화투를 치며', '바둑을 두며', '옛날이야기를 하며'])} 하루를 보냈다`, v: 1 }; }),
    O('teach', '젊은이들에게 기술을 가르친다', x => x.a.tr.O * 0.2 + x.a.val.community * 0.3, (x) => { const y = x.w.residents(x.a.sid).find(b => { const g = ageOf(b, x.now); return g > 15 && g < 30; }); if (y) { x.bond(y, 6, 4); const sk = Object.keys(x.a.sk).sort((p, q) => x.a.sk[q] - x.a.sk[p])[0]; if (y.sk[sk] != null) y.sk[sk] = clamp(y.sk[sk] + 3); } x.rep(2); return '평생 익힌 솜씨를 젊은이에게 전했다'; }),
    O('home', '집에 있는다', x => (100 - x.a.tr.E) * 0.5, (x) => '집에서 라디오를 들었다'),
  ],
});

// ══════════════════════════ 정치 · 공동체 ══════════════════════════
D('vote', {
  cat: 'power', icon: '🗳️', ages: [19, 120], era: [4, 7], w: 3, cool: 300,
  when: x => { const c = x.c; if (!c.nextElection || c.nextElection - x.now > 10 * DAY || c.nextElection < x.now) return null; const cands = [...x.w.anns.values()].filter(b => b.alive && b.country === c.code && b.amb > 50 && ageOf(b, x.now) >= 25 && b.id !== x.a.id).sort((p, q) => x.w.influence(q) - x.w.influence(p)).slice(0, 3); return cands.length >= 2 ? { e: c.nextElection, cands: cands.map(b => b.id) } : null; },
  q: (x) => `곧 ${ERAS[x.era].leader} 선거다. 누구를 찍을까?`,
  opts: (x, d) => d.cands.map(id => { const b = x.get(id); return b ? O('v' + id, `${b.name} (${jobTitle(x.w, b) || '무직'})`, x => (x.a.rels[id]?.a || 0) * 0.6 + b.ab.cha * 0.2 + b.rep * 0.2, (x) => { x.a.vote = { e: d.e, for: id }; x.bond(b, 3, 1, 0.3); return `${b.name}에게 한 표를 주기로 했다`; }) : null; }).concat([O('none', '투표하지 않는다', x => (100 - x.a.val.community) * 0.2 - 10, (x) => { x.a.vote = { e: d.e, for: null }; return '투표를 하지 않기로 했다'; })]),
});

// ── 헬퍼 (맥락에 얹는다) ──
function enrich(x) {
  x.pick2 = arr => arr[(x.a.id + Math.floor(x.now / DAY)) % arr.length];
  x.pw = p => (p ? (p.gender === 'F' ? (x.age < 20 ? '엄마' : '어머니') : (x.age < 20 ? '아빠' : '아버지')) : '부모님');
  x.adultStranger = () => pick(x.w.residents(x.a.sid).filter(b => b.id !== x.a.id && ageOf(b, x.now) > 18 && !x.a.rels[b.id])) || null;
  return x;
}
const X = (world, a) => enrich(ctx(world, a));

// ══════════════════════════ 묻기 · 답하기 ══════════════════════════
const pubQ = q => ({ id: q.id, k: q.k, ann: q.ann, name: q.name, cat: q.cat, icon: q.icon, big: q.big, q: q.q, opts: q.opts, t: q.t, until: q.until });

/** 앤이 마음속 목소리에게 묻는다. 물었으면 true (호출한 쪽은 답을 기다린다), 못 물었으면 false (앤이 바로 정한다) */
export function consult(world, a, id, d = {}) {
  const def = DEFS[id];
  if (!def || !a || !a.owner || !a.alive || a.ask || a.travel) return false;
  const p = world.players[a.owner];
  if (!p) return false;
  const mode = p.askMode || 'all';
  if (mode === 'off' || (mode === 'big' && !def.big)) return false;
  p.asks = p.asks || [];
  if (p.asks.length >= MAX_PENDING) return false;
  const day = Math.floor(world.clock / DAY);
  if (!p.askDay || p.askDay.d !== day) p.askDay = { d: day, n: 0 };
  if (p.askDay.n >= DAY_CAP + (def.big ? 3 : 0)) return false;
  const x = X(world, a);
  let q, opts;
  try {
    opts = def.opts(x, d).filter(Boolean);
    if (opts.length < 2) return false;
    q = def.q(x, d);
  } catch (e) { return false; }
  const now = Math.round(world.clock);
  const rec = {
    id: 'q' + world.newId(), k: id, ann: a.id, name: a.name, cat: def.cat, icon: def.icon, big: !!def.big,
    q: fixJosa(q), opts: opts.map(o => ({ k: o.k, t: fixJosa(o.t) })), d, t: now, until: now + (def.wait || (def.big ? BIG_WAIT : SMALL_WAIT)),
  };
  a.ask = rec.id;
  p.asks.push(rec);
  p.askDay.n++;
  a.askSeen = a.askSeen || {};
  a.askSeen[id] = now;
  if (a.heed == null) a.heed = initHeed(a);
  world.pushNote(p, { t: now, ann: a.id, name: a.name, text: rec.q, icon: def.icon || '❓', lvl: def.big ? 3 : 2, ask: pubQ(rec) });
  think(world, a, pick(['어떻게 해야 할까…', '쉽게 정할 수가 없다. 마음속 목소리는 뭐라고 할까.', '이럴 땐 마음의 소리를 들어 보자.', '한참을 고민했다.']));
  world.dirtyDetail.add(a.id);
  return true;
}

function initHeed(a) {
  return clamp(62 + (a.tr.A - 50) * 0.3 + (a.tr.N - 50) * 0.15 - (a.amb - 50) * 0.15 - (a.tr.O - 50) * 0.1, 30, 88);
}

/** 플레이어가 답했다 (또는 시간이 다 됐다: ans = null) */
export function resolveAsk(world, p, q, ans = null) {
  const i = (p.asks || []).indexOf(q);
  if (i >= 0) p.asks.splice(i, 1);
  const a = world.anns.get(q.ann);
  if (a && a.ask === q.id) a.ask = null;
  const def = DEFS[q.k];
  let out;
  if (!a || !a.alive || !def) out = { did: null, didT: null, heed: null, text: '이제는 물을 수 없는 일이 되었다' };
  else {
    const x = X(world, a);
    let valid = true;
    try { valid = !def.ok || def.ok(x, q.d); } catch { valid = false; }
    if (!valid) out = { did: null, didT: null, heed: null, text: '그사이 상황이 바뀌어 없던 일이 되었다' };
    else {
      let opts = [];
      try { opts = def.opts(x, q.d).filter(Boolean); } catch { opts = []; }
      if (!opts.length) out = { did: null, didT: null, heed: null, text: '그사이 상황이 바뀌었다' };
      else {
        // 앤 자신의 마음: 선택지마다 끌림 + 그날의 기분
        const lean = {};
        let own = opts[0], ownS = -Infinity;
        for (const o of opts) {
          let s = 0;
          try { s = o.lean ? o.lean(x, q.d) : 0; } catch { s = 0; }
          s += gauss() * 8;
          lean[o.k] = s;
          if (s > ownS) { ownS = s; own = o; }
        }
        const advised = ans != null ? opts.find(o => o.k === ans) : null;
        let chosen = own, heed = null;
        if (advised) {
          if (advised === own) { heed = true; chosen = advised; }
          else {
            const gap = Math.max(0, lean[own.k] - lean[advised.k]);
            const pFollow = clamp((a.heed ?? initHeed(a)) / 100 * 1.08 - gap / 140 + (a.tr.A - 50) / 500 - (a.amb - 50) / 700, 0.12, 0.96);
            heed = chance(pFollow);
            chosen = heed ? advised : own;
          }
        }
        let r = '';
        try { r = chosen.run(x, q.d) || ''; } catch (e) { r = ''; }
        const res = typeof r === 'string' ? { t: r, v: 0 } : r;
        if (advised) a.heed = clamp((a.heed ?? initHeed(a)) + (heed ? 0.8 + (res.v || 0) * 2.5 : -0.4), 15, 95);
        if (heed === true && advised !== own) think(world, a, pick(['내 마음은 달랐지만, 마음속 목소리를 믿어 보기로 했다.', '처음엔 망설였지만 그 말이 맞는 것 같다.']));
        else if (heed === false) think(world, a, pick(['미안하지만 이번만큼은 내 뜻대로 하겠다.', '마음속 목소리는 다르게 말했지만… 나는 나를 믿는다.', '아무리 생각해도 이게 맞다.']));
        else if (heed === true) think(world, a, pick(['역시 그렇지. 마음이 정해졌다.', '마음속 목소리도 같은 생각이다.']));
        remember(world, a, `"${q.q.replace(/\.$/, '')}" — ${fixJosa(chosen.t)}`, def.big ? 6 : 3, res.v > 0 ? 0.4 : res.v < 0 ? -0.3 : 0.1, [], 'choice');
        out = { did: chosen.k, didT: fixJosa(chosen.t), own: own.k, heed, text: fixJosa(res.t || ''), v: res.v || 0 };
      }
    }
  }
  const log = { ...pubQ(q), ans, at: Math.round(world.clock), ...out };
  (p.askLog = p.askLog || []).push(log);
  if (p.askLog.length > LOG_CAP) p.askLog.splice(0, p.askLog.length - LOG_CAP);
  const head = !out.did ? '' : ans == null ? '답을 기다리다 스스로 정했다' : out.heed ? '당신의 조언을 따랐다' : '고민 끝에 자기 뜻대로 했다';
  world.pushNote(p, { t: log.at, ann: q.ann, name: q.name, text: `${head ? head + ' — ' : ''}${out.didT || ''}${out.text ? `${out.didT ? ' · ' : ''}${out.text}` : ''}`, icon: out.heed === false ? '🙅' : '💭', lvl: 1, askDone: { id: q.id, did: out.did, heed: out.heed } });
  if (a) world.dirtyDetail.add(a.id);
  return log;
}

/** 매 분: 시간이 다 된 질문은 앤이 혼자 정한다 */
export function askTick(world) {
  for (const p of Object.values(world.players)) {
    if (!p.asks || !p.asks.length) continue;
    for (const q of [...p.asks]) {
      const a = world.anns.get(q.ann);
      if (world.clock >= q.until || !a || !a.alive) resolveAsk(world, p, q, null);
    }
  }
}

/** 하루에 한 번: 일상 갈림길 (플레이어의 앤만) */
export function askDaily(world, a, age) {
  if (!a.owner || a.ask || a.travel || age < 6) return;
  if (world.clock - (a.askedAt || -1e9) < DAY * 2) return;
  const p = world.players[a.owner];
  if (!p || (p.askMode || 'all') !== 'all') return;
  if (!chance(0.25)) return;
  const x = X(world, a);
  const cands = [];
  for (const id of PRO) {
    const def = DEFS[id];
    if (age < def.ages[0] || age >= def.ages[1]) continue;
    if (def.era && (x.era < def.era[0] || x.era > def.era[1])) continue;
    const seen = a.askSeen?.[id];
    if (seen != null && world.clock - seen < def.cool * DAY) continue;
    let d = null;
    try { d = def.when(x); } catch { d = null; }
    if (d) cands.push([def, d === true ? {} : d]);
  }
  if (!cands.length) return;
  let tot = cands.reduce((t, c) => t + c[0].w, 0), r = rand() * tot;
  let ch = cands[0];
  for (const c of cands) { r -= c[0].w; if (r <= 0) { ch = c; break; } }
  if (consult(world, a, ch[0].id, ch[1])) a.askedAt = world.clock;
}

/** 빌려준 돈: 갚을 날이 되면 성실한 사람은 갚고, 아니면 떼먹는다 */
export function loansDaily(world, a) {
  if (!a.loans?.length) return;
  for (const L of [...a.loans]) {
    if (world.clock < L.due) continue;
    a.loans.splice(a.loans.indexOf(L), 1);
    const b = world.anns.get(L.to);
    if (!b) continue;
    const pay = b.alive && b.money > L.amt && chance(0.35 + b.tr.C / 200 + (b.rels[a.id]?.a || 0) / 300);
    if (pay) {
      b.money -= L.amt; a.money += L.amt; book(world, a, 'inc', '돌려받은 돈', L.amt);
      remember(world, a, `${ga(b.name)} 빌린 돈을 갚았다`, 3, 0.4, [b.id], 'money');
      const r = rel(a, b); r.t = clamp(r.t + 6);
    } else if (b.alive) {
      remember(world, a, `${ga(b.name)} 빌려 간 돈을 갚지 않는다`, 5, -0.6, [b.id], 'money');
      const r = rel(a, b); r.a = clamp(r.a - 12, -100, 100); r.t = clamp(r.t - 15);
      updateKind(world, a, b);
      think(world, a, pick([`${neun(b.name)} 돈 이야기만 하면 말을 돌린다.`, '돈 잃고 사람 잃는다더니…']));
    }
  }
}

/** 클라이언트용 */
export function asksOut(world, p) {
  return { asks: (p.asks || []).map(pubQ), log: (p.askLog || []).slice(-30).reverse(), mode: p.askMode || 'all', clock: Math.round(world.clock) };
}
export const DILEMMA_COUNT = () => Object.keys(DEFS).length;
export { DEFS as DILEMMAS };
export const _ctx = (world, a) => X(world, a); // 시험용
