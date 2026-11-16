// 종교: 문화권마다 시대에 따라 믿음이 전해지고, 앤마다 믿음과 신앙심이 있다.
//
//   · 전래 — 원시엔 자연·조상에게 비는 민간 신앙. 시대가 열리면 그 문화권의 종교가 전해진다
//     (한국: 무속·민간 신앙 → 고대 불교·유교 → 중세 천주교 → 산업 시대 개신교 / 서유럽: 가톨릭 → 개신교 / 중동: 이슬람 …).
//     처음엔 마음이 열린 몇 사람이 믿기 시작하고, 가족과 이웃을 따라 퍼진다.
//   · 앤의 믿음 — 부모의 믿음을 대개 물려받는다. 힘들 때 기대고, 배우자나 친한 친구를 따라 믿기 시작하기도 한다.
//     근대 이후엔 믿음이 옅어지는 사람이 늘고(무교), 나이 들면 다시 기대기도 한다.
//   · 신앙생활 — 주일 예배·미사, 법회, 금요 예배, 푸자, 신사 참배, 치성, 명절 차례. 신전(교회·절·모스크…)이 있으면 가서,
//     없으면 집에서 기도한다. 마음이 가라앉고(스트레스↓), 같은 믿음의 이웃과 가까워진다. 성탄절·석가탄신일도 챙긴다.
import { clamp, rand, pick, chance, gauss, ageOf, dateOf, DAY } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { rel } from './social.js';

export const FAITHS = {
  folk: { n: '민간 신앙', icon: '🪔', place: '서낭당', rite: '치성', home: '정화수 떠 놓고 빌기' },
  buddhism: { n: '불교', icon: '☸️', place: '절', rite: '법회', home: '염불' },
  confucian: { n: '유교', icon: '📜', place: '사당', rite: '차례', home: '제사' },
  catholic: { n: '천주교', icon: '✝️', place: '성당', rite: '주일 미사', home: '묵주 기도' },
  protestant: { n: '개신교', icon: '✝️', place: '교회', rite: '주일 예배', home: '기도' },
  orthodox: { n: '정교회', icon: '☦️', place: '성당', rite: '주일 예배', home: '기도' },
  islam: { n: '이슬람교', icon: '☪️', place: '모스크', rite: '금요 예배', home: '예배' },
  hindu: { n: '힌두교', icon: '🕉️', place: '사원', rite: '푸자', home: '푸자' },
  shinto: { n: '신도', icon: '⛩️', place: '신사', rite: '참배', home: '가미다나에 절하기' },
  taoism: { n: '도교', icon: '☯️', place: '도관', rite: '제례', home: '향 피우기' },
};
// 문화권별 [믿음, 전해지는 시대, 퍼지는 힘]
const BY_CULTURE = {
  ko: [['folk', 0, 1], ['buddhism', 2, 1.2], ['confucian', 2, 0.8], ['catholic', 3, 0.6], ['protestant', 4, 1.3]],
  ja: [['shinto', 0, 1], ['buddhism', 2, 1.1], ['protestant', 5, 0.25]],
  zh: [['taoism', 0, 1], ['buddhism', 2, 1], ['confucian', 2, 0.8]],
  vi: [['folk', 0, 1], ['buddhism', 2, 1.2], ['catholic', 4, 0.6]],
  in: [['folk', 0, 0.6], ['hindu', 1, 1.6], ['buddhism', 2, 0.6], ['islam', 3, 0.9]],
  ar: [['folk', 0, 0.6], ['islam', 2, 2]],
  ru: [['folk', 0, 0.8], ['orthodox', 2, 1.6], ['islam', 3, 0.5]],
  de: [['folk', 0, 0.8], ['catholic', 2, 1.4], ['protestant', 3, 1.4]],
  en: [['folk', 0, 0.8], ['catholic', 2, 1.1], ['protestant', 3, 1.6]],
  fr: [['folk', 0, 0.8], ['catholic', 2, 1.8], ['protestant', 3, 0.4]],
  it: [['folk', 0, 0.8], ['catholic', 2, 2], ['orthodox', 2, 0.4]],
  es: [['folk', 0, 0.8], ['catholic', 2, 2], ['protestant', 4, 0.6]],
  pt: [['folk', 0, 0.8], ['catholic', 2, 2], ['protestant', 4, 0.7]],
  af: [['folk', 0, 1.2], ['islam', 3, 1.2], ['protestant', 4, 1], ['catholic', 4, 0.9]],
};
const faithsOf = c => BY_CULTURE[c.culture] || BY_CULTURE.en;
const CHRIST = new Set(['catholic', 'protestant', 'orthodox']);

/** 나라의 믿음 상태. 이미 시대가 지난 나라(예전 세계)는 그 시대에 맞는 분포로 따라잡는다 */
function state(c) {
  if (c.faith) return c.faith;
  const S = c.faith = { arrived: {}, stats: [], at: 0, seed: [] };
  for (const [k, era, pow] of faithsOf(c)) {
    if (c.era < era) continue;
    S.arrived[k] = 0;
    const w = era === 0 ? (c.era <= 1 ? 10 : Math.max(0.15, 3 - c.era * 0.45)) * pow : pow * (1 + c.era - era);
    S.seed.push({ k, n: w });
  }
  // 무교: 근대 이후 늘어난다
  S.seed.push({ k: null, n: c.era < 4 ? 0.05 : c.era === 4 ? 0.6 : c.era === 5 ? 3 : 9 });
  return S;
}
export const faithName = k => (k ? FAITHS[k]?.n : '무교');

/** 앤이 처음 믿음을 갖는다: 부모를 따르고, 아니면 고장에 퍼진 믿음 중에서 */
function initFaith(world, a, c) {
  const S = state(c);
  const ps = a.parents.map(id => world.anns.get(id)).filter(p => p?.faith);
  const fromParent = ps.length ? (ps.find(p => p.faith.k && p.faith.dev >= (ps.find(q => q !== p)?.faith.dev ?? 0)) || ps[0]) : null;
  const keep = c.era >= 5 ? 0.78 : 0.92;
  if (fromParent && (fromParent.faith.k ? chance(keep) : chance(0.85))) {
    a.faith = { k: fromParent.faith.k, dev: fromParent.faith.k ? clamp(fromParent.faith.dev * 0.8 + gauss() * 10, 5, 95) : 0 };
    return;
  }
  // 고장의 분포를 따라 (무교 포함). 처음엔 민간 신앙
  const pool = S.stats.length && S.stats.reduce((t, x) => t + x.n, 0) > 20 ? S.stats : S.seed.length ? S.seed : [{ k: faithsOf(c)[0][0], n: 1 }];
  let r = rand() * pool.reduce((s, x) => s + x.n, 0), k = pool[0].k;
  for (const x of pool) { r -= x.n; if (r <= 0) { k = x.k; break; } }
  a.faith = { k: k || null, dev: k ? clamp(25 + rand() * 50 - Math.max(0, c.era - 4) * 8, 5, 90) : 0 };
}

function convert(world, a, k, why) {
  const F = FAITHS[k];
  const old = a.faith?.k;
  a.faith = { k, dev: clamp(45 + rand() * 30, 10, 95), since: Math.round(world.clock) };
  const verb = k === 'buddhism' ? '불교에 귀의했다' : CHRIST.has(k) ? `${F.n} 신자가 되었다${k === 'catholic' ? ' (세례)' : ''}` : `${F.n}을(를) 믿기 시작했다`;
  lifeEvent(world, a, old ? `${faithName(old)}에서 ${F.n}(으)로 — ${verb}` : verb, F.icon);
  remember(world, a, why ? `${why} ${F.n}을(를) 믿게 되었다` : `${F.n}을(를) 믿게 되었다`, 6, 0.5, [], 'life');
}

/** 하루에 한 번 (앤마다): 믿음이 생기고, 깊어지고, 옅어지고, 옮겨 간다 */
export function faithDaily(world, a) {
  const c = world.countries[a.country];
  if (!c) return;
  if (!a.faith) { initFaith(world, a, c); return; }
  const F = a.faith, age = ageOf(a, world.clock);
  if (F.k) {
    let d = -0.0045 * Math.max(0, c.era - 4) * (0.6 + a.tr.O / 100);   // 근대 이후 조금씩 옅어진다
    if (a.stress > 70 || (a.sick || 0) > 30) d += 0.05;                   // 힘들 때 기댄다
    if (age > 60) d += 0.012;
    F.dev = clamp(F.dev + d, 0, 100);
    if (F.dev < 3 && age >= 16 && chance(0.02)) {
      lifeEvent(world, a, `${faithName(F.k)} 신앙을 내려놓았다`, '🍃');
      think(world, a, pick(['예전만큼 믿음이 가지 않는다.', '나는 나대로 살아 보려 한다.']));
      a.faith = { k: null, dev: 0 };
      return;
    }
  }
  // 한 달에 한 번쯤: 배우자·친한 사람을 따라 믿음이 옮겨 가거나 생긴다
  if (age < 14 || !chance(1 / 30)) return;
  const sp = a.spouse && world.anns.get(a.spouse);
  if (sp?.alive && sp.faith?.k && sp.faith.k !== F.k && sp.faith.dev > F.dev + 25 && chance(0.1)) { convert(world, a, sp.faith.k, `${sp.name}을(를) 따라`); return; }
  if (!F.k || F.dev < 20) {
    const need = a.stress > 55 || (a.sick || 0) > 20 || age > 55;
    let best = null;
    for (const [id, r] of Object.entries(a.rels || {})) {
      if (r.a < 40) continue;
      const b = world.anns.get(+id);
      if (b?.alive && b.faith?.k && b.faith.dev > 60 && (!best || b.faith.dev > best.faith.dev)) best = b;
    }
    if (best && chance(need ? 0.12 : 0.03)) convert(world, a, best.faith.k, `${best.name}의 권유로`);
  }
}

/** 나라: 새 믿음이 전해지고, 믿음의 분포를 센다 */
export function religionDaily(world, c) {
  const S = state(c);
  const list = faithsOf(c);
  for (const [k, era, pow] of list) {
    if (c.era < era || S.arrived[k] != null) continue;
    if (era === 0) { S.arrived[k] = Math.round(world.clock); continue; }
    if (!chance(1 / 12)) continue;
    S.arrived[k] = Math.round(world.clock);
    const F = FAITHS[k];
    const people = [...world.anns.values()].filter(a => a.alive && a.country === c.code && ageOf(a, world.clock) >= 16);
    // 마음이 열렸거나 믿음이 옅은 사람 몇이 먼저 믿는다 (퍼지는 힘에 따라)
    const seeds = people.filter(a => !a.faith?.k || a.faith.dev < 45).sort((x, y) => (y.tr.O + rand() * 40) - (x.tr.O + rand() * 40)).slice(0, Math.max(1, Math.round(people.length * 0.05 * pow)));
    for (const a of seeds) convert(world, a, k, '새 가르침을 듣고');
    world.news('culture', `${F.icon} ${c.name}에 ${F.n}이(가) 전해졌다 — ${seeds.slice(0, 2).map(a => a.name).join('·')}${seeds.length > 2 ? ` 등 ${seeds.length}명` : ''}이(가) 처음 믿기 시작했다`, { country: c.code, anns: seeds.slice(0, 3).map(a => a.id) }, 2);
  }
  // 분포 (일주일에 한 번)
  if (world.clock - S.at < 7 * DAY && S.stats.length) return;
  S.at = Math.round(world.clock);
  const n = {};
  let total = 0;
  for (const a of world.anns.values()) if (a.alive && a.country === c.code && a.faith) { const k = a.faith.k || 'none'; n[k] = (n[k] || 0) + 1; total++; }
  S.stats = Object.entries(n).map(([k, v]) => ({ k: k === 'none' ? null : k, n: v, pct: total ? Math.round(v / total * 100) : 0 })).sort((x, y) => y.n - x.n);
}

/** 오늘 예배·기도하러 갈 때인가 (믿음마다 날과 시간이 다르다) → { temple, label, emoji, dur } 또는 null */
export function worshipWindow(world, a, h, lday) {
  const F = a.faith;
  if (!F?.k || F.dev < 12 || a.day?.prayed) return null;
  const k = F.k, d = dateOf(world.clock), dow = lday % 7;   // 5 토 · 6 일
  const D = FAITHS[k];
  if (CHRIST.has(k)) {
    if (d.month === 12 && d.day === 25 && h >= 9 && h < 21) return { temple: true, label: '성탄 예배', emoji: '🎄', dur: 90 };
    if (dow === 6 && h >= 9 && h < 13) return { temple: true, label: D.rite, emoji: '⛪', dur: 80 };
  }
  if (k === 'islam' && dow === 4 && h >= 12 && h < 14) return { temple: true, label: D.rite, emoji: '🕌', dur: 60 };
  if (k === 'buddhism') {
    if (d.month === 5 && d.day === 15 && h >= 9 && h < 20) return { temple: true, label: '석가탄신일 — 연등', emoji: '🏮', dur: 90 };
    if ((d.day === 1 || d.day === 15) && h >= 7 && h < 12) return { temple: true, label: D.rite, emoji: '🪷', dur: 70 };
  }
  if (k === 'confucian' && ((d.month === 1 && d.day === 1) || (d.month === 8 && d.day === 15)) && h >= 7 && h < 11) return { temple: false, label: '명절 차례', emoji: '🕯️', dur: 60 };
  if (k === 'hindu' && h >= 6 && h < 8) return { temple: false, label: D.home, emoji: '🪔', dur: 20 };
  if ((k === 'shinto' || k === 'taoism' || k === 'folk') && d.day === 1 && h >= 7 && h < 12) return { temple: true, label: D.rite, emoji: k === 'shinto' ? '⛩️' : '🪔', dur: 50 };
  return null;
}
/** 힘들 때 집에서 드리는 기도 */
export const homePrayer = a => { const D = FAITHS[a.faith.k]; return { temple: false, label: D.home, emoji: '🙏', dur: 15 + rand() * 15 }; };

/** 예배·기도를 마치고: 마음이 가라앉고, 믿음이 깊어지고, 함께한 이웃과 가까워진다 */
export function worshipped(world, a, act) {
  const F = a.faith;
  if (!F?.k) return;
  const atTemple = !!act.temple;
  F.dev = clamp(F.dev + (atTemple ? 0.7 : 0.25), 0, 100);
  a.stress = clamp(a.stress - (atTemple ? 10 : 6));
  feel(a, atTemple ? 6 : 3, '평안');
  if (atTemple) {
    // 같은 곳에서 같은 믿음으로 예배한 사람들과 조금씩 가까워진다
    for (const b of world.anns.values()) {
      if (b === a || !b.alive || b.sid !== a.sid || b.act?.type !== 'worship' || b.act?.temple !== act.temple) continue;
      const r = rel(a, b); r.f = Math.min(100, r.f + 1.5); r.a = Math.min(100, r.a + 1);
    }
  }
  if (chance(0.18)) {
    const k = F.k;
    think(world, a, pick(CHRIST.has(k) ? ['기도하고 나니 마음이 한결 가볍다.', '오늘 설교가 오래 남는다.', '감사할 일을 세어 보았다.']
      : k === 'buddhism' ? ['마음을 비우니 한결 편하다.', '스님 말씀이 가슴에 남는다.', '모든 것은 지나간다.']
        : k === 'islam' ? ['함께 기도하니 마음이 든든하다.', '오늘도 감사하다.']
          : k === 'confucian' ? ['조상님들께 부끄럽지 않게 살아야지.', '차례를 지내니 가족이 다 모였다.']
            : ['빌고 나니 마음이 놓인다.', '올해도 무사히 지나가기를.', '하늘이 지켜 주시겠지.']));
  }
}

// ── 화면용 ──
export function faithOut(a) {
  if (!a.faith) return null;
  const k = a.faith.k;
  return { k, name: faithName(k), icon: k ? FAITHS[k].icon : '🍃', dev: k ? Math.round(a.faith.dev) : null };
}
export function religionOut(c) {
  const S = c.faith;
  if (!S?.stats?.length) return null;
  return S.stats.slice(0, 6).map(x => ({ n: faithName(x.k), icon: x.k ? FAITHS[x.k].icon : '🍃', pct: x.pct }));
}
