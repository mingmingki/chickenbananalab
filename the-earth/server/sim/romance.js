// 연애와 결혼의 단계
//   설렘 → (소개팅·맞선·중매) → 고백 → 연애 (100일 · 1주년 · 부모님께 인사) → 청혼 → 약혼 (상견례 · 결혼 준비)
//   → 결혼식 (시대마다 다른 예식) → 신혼여행 → 신혼 → 결혼기념일 … 동거(근대 이후) · 파혼 · 이혼 · 재혼
import { clamp, rand, pick, chance, gauss, ageOf, DAY, YEAR, ga, neun, reul, wa, ro, callName } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { rel, marry, updateKind } from './social.js';
import { BUILDINGS } from './data.js';
import { book, refMonth } from './budget.js';
import { consult } from './dilemma.js';

// 시대별 예식장 [큰 잔치, 보통, 조촐하게]
const VENUE = [
  ['온 부족이 모인 모닥불 앞', '모닥불 앞', '모닥불 앞'],
  ['온 마을 잔치 마당', '마을 마당', '집 마당'],
  ['큰 혼례청', '혼례청', '집 마당 초례상 앞'],
  ['큰 초례청 (전통 혼례)', '초례청 (전통 혼례)', '집 마당 초례상 앞 (전통 혼례)'],
  ['큰 예식장', '예식장', '동네 교회·성당'],
  ['호텔 웨딩홀', '웨딩홀', '작은 예식장'],
  ['호텔 웨딩홀', '웨딩홀', '작은 레스토랑 (스몰웨딩)'],
  ['궤도 호텔 연회장', '웨딩홀', '정원 (스몰웨딩)'],
];
const RITE = [
  '모두가 둘러앉아 두 사람의 혼인을 축하했다',
  '마을 사람들이 음식을 나누며 잔치를 열었다',
  '맞절을 하고 술잔을 나눴다',
  '사모관대와 원삼 족두리 차림으로 맞절을 하고 합근례를 올렸다',
  '주례 선생님 앞에서 혼인 서약을 했다',
  '주례사와 축가, 부케 던지기까지 이어졌다',
  '서로 쓴 혼인 서약을 읽고 축가를 들었다',
  '홀로그램 하객들까지 함께 축하했다',
];
const STYLE_IDX = { big: 0, normal: 1, small: 2 };
const STYLE_COST = { big: 3, normal: 1.3, small: 0.45 };
const STYLE_GUESTS = { big: 60, normal: 25, small: 8 };

/** 청혼 승낙 → 약혼 (원시 시대엔 곧바로 혼인) */
export function engage(world, a, b) {
  const era = world.countries[a.country]?.era ?? 0;
  for (const [x, y] of [[a, b], [b, a]]) { const r = rel(x, y); r.r = clamp(r.r + 10); r.t = clamp(r.t + 10); delete r.propWait; }
  if (a.plan?.k === 'propose') a.plan = null;
  if (era === 0) { wedding(world, a, b); return; }
  const now = Math.round(world.clock);
  const days = era <= 1 ? 8 + rand() * 10 : era <= 3 ? 20 + rand() * 25 : 50 + rand() * 70;
  const date = Math.round(now + days * DAY);
  a.engaged = { to: b.id, at: now, date, style: null, meet: now + Math.round(days * 0.3 * DAY) };
  b.engaged = { to: a.id, at: now, date, style: null, meet: a.engaged.meet };
  for (const [x, y] of [[a, b], [b, a]]) {
    remember(world, x, `${wa(y.name)} 약혼했다`, 9, 0.9, [y.id], 'love');
    lifeEvent(world, x, `${wa(y.name)} 약혼 — 결혼식까지 ${Math.round(days)}일`, '💍');
    feel(x, 28, '행복', y.name);
  }
  think(world, a, `${ga(b.name)} "응"이라고 했다. 세상을 다 가진 기분이다.`);
  think(world, b, pick(['결혼이라니… 실감이 안 난다.', `${wa(callName(a.name, b.culture))} 함께라면 괜찮을 거야.`]));
  world.news('love', `💍 ${wa(a.name)} ${ga(b.name)} 약혼했다 — ${Math.round(days)}일 뒤 결혼식`, { anns: [a.id, b.id], country: a.country }, 1);
  const asker = [a, b].find(x => x.owner);
  if (asker) consult(world, asker, 'engaged_style', {});
  world.dirtyAnnMeta.add(a.id); world.dirtyAnnMeta.add(b.id);
}

/** 결혼식 */
export function wedding(world, a, b) {
  const c = world.countries[a.country], era = c?.era ?? 0;
  const style = a.engaged?.style || b.engaged?.style || (a.tr.E + b.tr.E > 125 && a.money + b.money > refMonth(era) * 6 ? 'big' : a.money + b.money < refMonth(era) * 1.5 ? 'small' : 'normal');
  a.engaged = null; b.engaged = null;
  a.cohab = b.cohab = false;
  marry(world, a, b, true);
  const now = Math.round(world.clock);
  a.wedAt = b.wedAt = now; a.annivN = b.annivN = 0;
  // 비용: 둘이 나눠 내고, 형편이 되는 부모님이 보탠다. 근대 이후엔 축의금이 들어온다
  const M = refMonth(era);
  let cost = M * STYLE_COST[style] * (era <= 1 ? 0.25 : era <= 3 ? 0.6 : 1);
  for (const x of [a, b]) for (const pid of x.parents) {
    const p = world.anns.get(pid);
    if (p?.alive && p.money > M * 6) { const help = Math.min(p.money * 0.15, cost * 0.25); p.money -= help; book(world, p, 'exp', '자녀 결혼', help); cost -= help; remember(world, p, `${x.name}의 결혼식 비용을 보탰다`, 4, 0.6, [x.id], 'family'); }
  }
  for (const x of [a, b]) { const v = Math.min(x.money, Math.max(0, cost / 2)); x.money -= v; book(world, x, 'exp', '결혼식', v); }
  // 하객: 두 사람의 가족·친구·동료
  const ids = new Set();
  for (const x of [a, b]) {
    for (const id of [...x.parents, ...x.kids]) ids.add(id);
    for (const id in x.rels) { const r = x.rels[id]; if (['friend', 'best', 'sibling', 'parent', 'acquaintance'].includes(r.k) && (r.k !== 'acquaintance' || r.a > 20)) ids.add(+id); }
  }
  ids.delete(a.id); ids.delete(b.id);
  const guests = [...ids].map(id => world.anns.get(id)).filter(g => g && g.alive && g.country === a.country).slice(0, STYLE_GUESTS[style]);
  let gifts = 0;
  for (const g of guests) {
    remember(world, g, `${wa(a.name)} ${b.name}의 결혼식에 갔다`, 3, 0.6, [a.id, b.id], 'news');
    for (const x of [a, b]) if (g.rels[x.id]) { const r = rel(g, x); r.a = clamp(r.a + 2, -100, 100); r.f = clamp(r.f + 2); }
    if (era >= 4 && g.money > M * 0.5 && ageOf(g, world.clock) >= 20) { const gift = M * (g.rels[a.id]?.k === 'best' || g.rels[b.id]?.k === 'best' ? 0.05 : 0.02); g.money -= gift; book(world, g, 'exp', '경조사비', gift); gifts += gift; }
  }
  if (gifts) for (const x of [a, b]) { x.money += gifts / 2; book(world, x, 'inc', '축의금', gifts / 2); }
  const venue = VENUE[Math.min(7, era)][STYLE_IDX[style]];
  const rite = RITE[Math.min(7, era)];
  for (const [x, y] of [[a, b], [b, a]]) {
    lifeEvent(world, x, `${venue}에서 ${wa(y.name)} 결혼식 — 하객 ${guests.length}명`, '💒');
    remember(world, x, `${venue}에서 ${wa(y.name)} 결혼식을 올렸다. ${rite}`, 10, 1, [y.id], 'love');
  }
  think(world, a, pick([`오늘부터 ${neun(b.name)} 내 가족이다.`, '평생 오늘을 잊지 못할 거야.']));
  think(world, b, pick([`${wa(callName(a.name, b.culture))} 함께라면 뭐든 할 수 있을 것 같아.`, '떨려서 무슨 말을 했는지도 모르겠다.']));
  world.news('love', `💒 ${wa(a.name)} ${ga(b.name)} ${venue}에서 결혼식을 올렸다 (하객 ${guests.length}명)`, { anns: [a.id, b.id], country: a.country }, 2);
  world.first(c, 'wedding', `첫 결혼식 — ${wa(a.name)} ${b.name}`, '💍');
  // 신혼여행 (중세부터). 플레이어의 앤이면 어디로 갈지 묻는다
  if (era >= 3) {
    const asker = [a, b].find(x => x.owner && !x.ask);
    if (!(asker && consult(world, asker, 'honeymoon', {}))) {
      const where = pick([['', '', '', '온천', '경주', '제주도', '동남아 휴양지', '바닷가 리조트'][era], ['', '', '', '금강산', '제주도', '괌', '유럽', '달 궤도 리조트'][era]]);
      const cost2 = M * (era >= 6 ? 1 : 0.5);
      if (a.money + b.money > cost2 * 2) { for (const x of [a, b]) { const v = Math.min(x.money, cost2 / 2); x.money -= v; book(world, x, 'exp', '신혼여행', v); } honeymoon(world, a, where, era >= 5 ? 6 : 4); }
    }
  }
}

/** 신혼여행: 둘이 며칠 마을을 떠난다 */
export function honeymoon(world, a, where, days) {
  const b = a.spouse && world.anns.get(a.spouse);
  const until = Math.round(world.clock + days * DAY);
  for (const x of [a, b]) {
    if (!x || !x.alive || x.travel) continue;
    x.act = null;
    x.travel = { until, from: x.sid, to: x.sid, kind: 'honeymoon', where };
    x.nd.fun = 100; x.stress = clamp(x.stress - 25);
    lifeEvent(world, x, `${where} 신혼여행`, '✈️');
    feel(x, 20, '설렘', where);
    world.dirtyAnnMeta.add(x.id);
  }
  if (b) remember(world, a, `${wa(b.name)} ${where}에서 신혼여행을 보냈다`, 8, 0.9, [b.id], 'love');
  if (b) remember(world, b, `${wa(a.name)} ${where}에서 신혼여행을 보냈다`, 8, 0.9, [a.id], 'love');
}

/** 동거: 한집으로 합친다 */
export function cohabit(world, a, b) {
  const ha = a.home && world.buildings.get(a.home), hb = b.home && world.buildings.get(b.home);
  const fits = h => h && h.done && BUILDINGS[h.type]?.kind === 'home' && h.res.length < BUILDINGS[h.type].cap + 1;
  const target = [ha, hb].filter(fits).sort((p, q) => q.h * q.w - p.h * p.w)[0];
  if (!target) return false;
  world.moveHome(a, target); world.moveHome(b, target);
  a.cohab = b.cohab = true;
  for (const [x, y] of [[a, b], [b, a]]) {
    remember(world, x, `${wa(y.name)} 함께 살기 시작했다`, 7, 0.7, [y.id], 'love');
    lifeEvent(world, x, `${wa(y.name)} 한집에 살기 시작`, '🗝️');
  }
  world.dirtyAnnMeta.add(a.id); world.dirtyAnnMeta.add(b.id);
  return true;
}

/** 하루에 한 번: 연인·부부의 기념일과 단계 (한 쌍에 한 번만: id가 작은 쪽이 처리) */
export function romanceDaily(world, a) {
  // 약혼 상대가 떠났거나 헤어졌으면 약혼도 끝난다
  if (a.engaged) { const t = world.anns.get(a.engaged.to); if (!t || !t.alive || (a.partner !== t.id && a.spouse !== t.id)) a.engaged = null; }
  const pid = a.spouse || a.partner;
  if (!pid || a.id > pid) return;
  const b = world.anns.get(pid);
  if (!b || !b.alive) { if (a.engaged) a.engaged = null; return; }
  if (a.travel || b.travel) return;
  const now = world.clock, era = world.countries[a.country]?.era ?? 0;
  const r = rel(a, b), rb = rel(b, a);
  // 약혼: 상견례 → 결혼식
  if (a.engaged && a.engaged.to === b.id) {
    if (!a.engaged.met && now >= a.engaged.meet) { a.engaged.met = 1; if (b.engaged) b.engaged.met = 1; familiesMeet(world, a, b, era); }
    if (now >= a.engaged.date && b.engaged?.to === a.id) wedding(world, a, b);
    return;
  }
  if (!a.spouse) {
    const since = r.since || now;
    const d = Math.floor((now - since) / DAY);
    // 부모님께 인사드리기
    for (const [x, y, rx] of [[a, b, r], [b, a, rb]]) {
      if (rx.metP || d < 35 || ageOf(x, now) < 19) continue;
      const ps = y.parents.map(id => world.anns.get(id)).filter(p => p && p.alive && p.country === x.country);
      rx.metP = 1;
      if (!ps.length) continue;
      const q = x.tr.A * 0.4 + x.ab.cha * 0.3 + ps.reduce((t, p) => t + p.tr.A + p.tr.O * 0.5, 0) / ps.length * 0.3 + gauss() * 15 - 40;
      for (const p of ps) {
        const rp = rel(p, x), rx2 = rel(x, p);
        rp.a = clamp(rp.a + (q > 0 ? 12 : -6), -100, 100); rp.f = clamp(rp.f + 8); rx2.f = clamp(rx2.f + 8); rx2.a = clamp(rx2.a + (q > 0 ? 8 : -2), -100, 100);
        updateKind(world, p, x); updateKind(world, x, p);
        remember(world, p, `${ga(y.name)} 만나는 ${ga(x.name)} 인사를 왔다 — ${q > 0 ? '참한 사람 같다' : '글쎄, 잘 모르겠다'}`, 5, q > 0 ? 0.5 : -0.2, [x.id, y.id], 'family');
      }
      const names = ps.map(p => p.name).join('·');
      remember(world, x, `${y.name}의 부모님(${names})께 처음 인사를 드렸다 — ${q > 0 ? '따뜻하게 반겨 주셨다' : '분위기가 조금 어색했다'}`, 7, q > 0 ? 0.6 : -0.3, [y.id, ...ps.map(p => p.id)], 'love');
      lifeEvent(world, x, `${y.name}의 부모님께 첫인사`, '🙇');
      think(world, x, q > 0 ? '휴, 다행이다. 좋아해 주시는 것 같아.' : '내가 뭘 잘못했나… 다음엔 더 잘해야지.');
    }
    // 100일 (근대 이후 문화) · 1주년
    if (era >= 5 && d === 100) anniversary(world, a, b, '사귄 지 100일', 0.6);
    if (d > 0 && d % 365 === 0) anniversary(world, a, b, `사귄 지 ${d / 365}주년`, 0.5);
    return;
  }
  // 결혼기념일
  if (a.wedAt != null) {
    const n = Math.floor((now - a.wedAt) / YEAR);
    if (n > (a.annivN || 0)) {
      a.annivN = b.annivN = n;
      const forgot = chance(0.08 + (100 - a.tr.C) / 600 + (100 - b.tr.C) / 600);
      if (forgot) {
        const who = a.tr.C < b.tr.C ? a : b, other = who === a ? b : a;
        const ro2 = rel(other, who); ro2.a = clamp(ro2.a - 6, -100, 100);
        remember(world, other, `${ga(who.name)} 결혼 ${n}주년을 잊어버렸다`, 5, -0.5, [who.id], 'love');
        remember(world, who, `결혼 ${n}주년을 깜빡했다. 크게 혼났다`, 5, -0.4, [other.id], 'love');
        think(world, other, `어떻게 그날을 잊을 수가 있어?`);
        think(world, who, '내가 미쳤지… 꽃이라도 사 가야겠다.');
      } else anniversary(world, a, b, n === 25 ? '은혼식 (결혼 25주년)' : n === 50 ? '금혼식 (결혼 50주년)' : `결혼 ${n}주년`, n >= 25 ? 1 : 0.6, n === 25 || n === 50);
    }
  }
}

function anniversary(world, a, b, what, v, big = false) {
  const gift = pick(['꽃다발', '손편지', '직접 만든 요리', '반지', '목도리', '둘만의 사진첩', '케이크']);
  for (const [x, y] of [[a, b], [b, a]]) {
    const r = rel(x, y); r.r = clamp(r.r + 6 * v); r.a = clamp(r.a + 4 * v, -100, 100);
    remember(world, x, `${wa(y.name)} ${what} — ${reul(gift)} 주고받았다`, big ? 8 : 5, v, [y.id], 'love');
    if (big) lifeEvent(world, x, `${wa(y.name)} ${what}`, '💐');
    feel(x, 10 * v, '행복', y.name);
  }
  if (big) world.news('love', `💐 ${wa(a.name)} ${b.name}의 ${what}`, { anns: [a.id, b.id], country: a.country }, 1);
}

function familiesMeet(world, a, b, era) {
  const pa = a.parents.map(id => world.anns.get(id)).filter(p => p?.alive);
  const pb = b.parents.map(id => world.anns.get(id)).filter(p => p?.alive);
  const what = era >= 4 ? '상견례' : '두 집안 어른들의 만남';
  if (!pa.length || !pb.length) {
    for (const x of [a, b]) remember(world, x, `결혼 준비가 한창이다`, 4, 0.4, [], 'love');
    return;
  }
  let tot = 0;
  for (const p of pa) for (const q of pb) {
    const good = (p.tr.A + q.tr.A) / 2 + gauss() * 18 > 45;
    tot += good ? 1 : -1;
    const rp = rel(p, q), rq = rel(q, p);
    rp.f = clamp(rp.f + 10); rq.f = clamp(rq.f + 10);
    rp.a = clamp(rp.a + (good ? 15 : -8), -100, 100); rq.a = clamp(rq.a + (good ? 15 : -8), -100, 100);
    updateKind(world, p, q); updateKind(world, q, p);
  }
  const ok = tot >= 0;
  for (const x of [a, b]) {
    remember(world, x, `${what} — ${ok ? '양가 부모님이 금세 친해지셨다' : '양가 부모님 사이에 묘한 긴장이 흘렀다'}`, 6, ok ? 0.6 : -0.3, [...pa, ...pb].map(p => p.id), 'love');
    lifeEvent(world, x, what, '🤝');
    think(world, x, ok ? '이제 정말 결혼하는구나.' : '결혼은 둘만의 일이 아니구나…');
  }
}
