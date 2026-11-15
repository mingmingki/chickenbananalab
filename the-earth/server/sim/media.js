// 방송과 연예: 라디오·TV 방송국, 프로그램과 시청률, 연예인(데뷔·음원 차트·영화·시상식·스캔들), 팬, 그리고 보는 사람들.
//
//   · 연예인 — 가수·배우·코미디언·라디오 DJ·아나운서·유튜버. 연예 일을 처음 맡으면 데뷔하고, 기획사 연습생은 몇 달 연습한 뒤 데뷔한다.
//     명성은 출연작의 시청률·음원 성적·영화 관객·수상으로 오르고, 쉬면 천천히 식는다. 스캔들은 명성을 깎는다.
//   · 프로그램 — 방송국마다 뉴스·드라마·예능·음악 방송(TV), 라디오 쇼를 편성한다. 출연진은 명성 있는 연예인 + 신인.
//     드라마는 몇 달 하고 끝나고, 다른 것들은 오래 간다. 시청률 = 어제 실제로 본 앤의 비율.
//   · 보는 사람 — 저녁·주말에 집에서 라디오(산업 시대)·TV(근대)·유튜브(정보화)를 보고, 주말엔 극장에 간다.
//     재미있게 본 출연자의 팬이 되고, 그 사람의 소식에 같이 기뻐하고 슬퍼한다.
//   · 연말 시상식 — 방송국마다 연기대상·연예대상, 나라의 가요대상. 기록이 남는다.
// 모든 기록은 나라(c.media)에 담긴다 — 세계와 함께 저장된다.
import { clamp, rand, pick, chance, gauss, ageOf, dateOf, localHour, DAY, ga, reul } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';

export const STAR_JOBS = { singer: '가수', actor: '배우', comedian: '코미디언', dj: '라디오 DJ', announcer: '아나운서', creator: '유튜버' };
export const isStar = a => a.alive && a.job && STAR_JOBS[a.job.id];
const KIND = { news: '뉴스', drama: '드라마', variety: '예능', music: '음악 방송', radio: '라디오', docu: '다큐멘터리' };

// ── 이름 짓기 (실제 프로그램·노래 이름과 겹치지 않게 조합으로) ──
const ADJ = ['푸른', '따뜻한', '눈부신', '시린', '붉은', '하얀', '작은', '먼', '다시 만난', '그리운', '낯선', '달콤한', '비밀의', '봄날의', '한여름의', '가을의', '겨울', '마지막', '첫', '우리들의', '빛나는', '조용한'];
const NOUN = ['바다', '계절', '골목', '약속', '편지', '정원', '기억', '도시', '별', '노래', '집', '하늘', '길', '이웃', '시간', '바람', '꽃', '밤', '강', '섬', '거리', '창문', '우산', '기차'];
const SONG = ['밤', '별', '바다', '비', '눈', '꽃', '그대', '여름', '봄날', '기억', '노을', '바람', '달', '편지', '거리', '첫사랑', '하루', '마음', '파도', '우산', '새벽', '불빛'];
const VAR_A = ['주말', '토요', '일요', '한밤의', '우리 동네', '골목', '전국', '오늘의', '신나는', '깜짝', '수요', '금요'];
const VAR_B = ['원정대', '노래방', '웃음 공장', '요리 대결', '퀴즈왕', '토크 카페', '여행기', '캠핑', '운동회', '장기 자랑', '시골 살이', '맛 기행'];
const titleOf = (kind, co, host) => {
  const n = pick(NOUN), n2 = pick(NOUN.filter(x => x !== n)), a = pick(ADJ);
  switch (kind) {
    case 'drama': return pick([`${a} ${n}`, `${n}의 ${n2}`, `${a} ${n}의 ${n2}`, `${n}이 머무는 곳`.replace('이 머무는', /[가-힣]$/.test(n) && (n.charCodeAt(n.length - 1) - 0xac00) % 28 ? '이 머무는' : '가 머무는'), `그 ${n}에서`]);
    case 'variety': return `${pick(VAR_A)} ${pick(VAR_B)}`;
    case 'news': return pick([`${co} 9시 뉴스`, `${co} 아침 뉴스`, `${co} 저녁 종합 뉴스`, `${co} 뉴스 투데이`]);
    case 'music': return pick(['음악 광장', '토요 가요 무대', '라이브 스테이지', '뮤직 스퀘어', `${co} 음악 무대`]);
    case 'radio': return host ? pick([`${host}의 아침 창`, `${host}의 음악 여행`, `${host}의 밤 편지`, `${host}의 오후 수다`]) : pick(['밤의 라디오', '아침의 노래', '오후의 음악실']);
    case 'docu': return pick([`${n} 기행`, `자연의 ${n}`, `${n} 사람들`, `세계 ${n} 탐험`]);
    default: return `${a} ${n}`;
  }
};
const songTitle = () => { const n = pick(SONG); return pick([`${pick(ADJ)} ${n}`, `${n} 속으로`, `너의 ${n}`, `그날의 ${n}`, `${n}처럼`, `안녕, ${n}`, `${n}이 내리면`.replace('이 내리면', ((n.charCodeAt(n.length - 1) - 0xac00) % 28) ? '이 내리면' : '가 내리면')]); };
const filmTitle = () => { const n = pick(NOUN), n2 = pick(NOUN.filter(x => x !== n)); return pick([`${pick(ADJ)} ${n}`, `${n}의 ${n2}`, `${n} 작전`, `마지막 ${n}`, `${n}, 그리고 ${n2}`]); };

/** 같은 이름의 프로그램이 동시에 둘이 되지 않게 */
function uniqueTitle(M, gen) {
  const used = new Set(M.progs.map(p => p.title));
  let t = gen();
  for (let i = 0; i < 8 && used.has(t); i++) t = gen();
  return used.has(t) ? `${t} 2` : t;
}
function media(c) { return (c.media = c.media || { progs: [], songs: [], films: [], hist: [], nid: 1 }); }
const anyAnns = (world, c) => [...world.anns.values()].filter(a => a.alive && a.country === c.code);

/** 연예인이 되었다 */
function debut(world, c, a) {
  const kind = a.job.id;
  a.star = { since: Math.round(world.clock), kind, works: [], awards: [], fans: 0 };
  a.fame = Math.max(a.fame || 0, 5);
  const msg = { singer: '🎤 가수로 데뷔했다', actor: '🎬 배우로 데뷔했다', comedian: '🎙️ 코미디언으로 데뷔했다', dj: '📻 라디오 DJ가 되었다', announcer: '📺 아나운서가 되었다', creator: '📱 유튜브 채널을 열었다' }[kind];
  lifeEvent(world, a, msg.slice(3), msg.slice(0, 2));
  remember(world, a, msg.slice(3), 8, 0.9, [], 'work');
  think(world, a, pick(['드디어 꿈꾸던 무대다. 떨린다.', '이제 시작이다. 사람들이 날 알아봐 줄까?', '이 순간을 오래 기다렸다.']));
  if (kind !== 'announcer') world.news('culture', `${msg.slice(0, 2)} ${ga(a.name)} ${STAR_JOBS[kind]}(으)로 데뷔했다`, { anns: [a.id], country: c.code }, 1);
}

/** 방송국마다 프로그램을 채운다 */
function schedule(world, c, M, co) {
  const mine = M.progs.filter(p => p.co === co.id);
  const tv = co.ind === 'tv';
  const want = tv ? 5 : 3;
  if (mine.length >= want) return;
  const have = new Set(mine.map(p => p.kind));
  const kinds = tv ? ['news', 'drama', 'variety', 'music', 'docu'] : ['radio', 'news', 'radio'];
  // 아직 없는 장르부터, 출연할 사람이 있는 것으로 (배우가 없으면 드라마는 미룬다)
  // 장르마다 하나씩 (뉴스는 아침·저녁 둘, 드라마는 둘까지)
  const cnt = k => mine.filter(p => p.kind === k).length;
  const cap = { news: 2, drama: 2, radio: 2 };
  const order = kinds.filter(k => cnt(k) < (cap[k] || 1)).sort((x, y) => (have.has(x) ? 1 : 0) - (have.has(y) ? 1 : 0) || rand() - 0.5);
  for (const kind of order) if (tryShow(world, c, M, co, kind)) return;
}
function tryShow(world, c, M, co, kind) {
  const stars = anyAnns(world, c).filter(isStar);
  const pickCast = (jobs, n) => {
    const pool = stars.filter(a => jobs.includes(a.job.id) && !(a.star?.busy > world.clock));
    const out = [];
    for (let i = 0; i < n && pool.length; i++) {
      // 이름난 사람이 잘 뽑히지만, 신인에게도 기회가 간다
      pool.sort((x, y) => (y.fame + rand() * 40) - (x.fame + rand() * 40));
      out.push(pool.shift());
    }
    return out;
  };
  const cast = kind === 'drama' ? pickCast(['actor'], 3) : kind === 'variety' ? pickCast(['comedian', 'singer', 'actor'], 4) : kind === 'music' ? pickCast(['singer'], 5)
    : kind === 'radio' ? pickCast(['dj', 'singer', 'comedian'], 1) : kind === 'news' ? pickCast(['announcer'], 2) : [];
  if (kind !== 'docu' && !cast.length) return false;
  const pd = co.emp.map(id => world.anns.get(id)).find(b => b?.job?.id === 'pd');
  const p = {
    id: M.nid++, co: co.id, coName: co.name, kind, title: uniqueTitle(M, () => titleOf(kind, co.name, cast[0]?.name)),
    cast: cast.map(a => a.id), pd: pd?.id || null, start: Math.round(world.clock),
    end: kind === 'drama' ? Math.round(world.clock + (50 + rand() * 60) * DAY) : null,
    rating: 0, peak: 0, views: 0, q: clamp(40 + (pd ? pd.sk.art * 0.4 + pd.ab.cre * 0.2 : 10) + gauss() * 8, 10, 100),
  };
  if (kind === 'drama') for (const a of cast) a.star.busy = p.end;
  M.progs.push(p);
  for (const a of cast) { a.star.works.unshift({ t: p.title, k: kind, at: p.start }); a.star.works.length = Math.min(a.star.works.length, 12); }
  if (kind === 'drama' || kind === 'variety') world.news('culture', `📺 ${co.name} 새 ${KIND[kind]} 〈${p.title}〉 — ${cast.map(a => a.name).join(', ')} 출연`, { anns: cast.map(a => a.id), country: c.code }, 1);
  return true;
}

/** 하루에 한 번 (나라마다) */
export function mediaDaily(world, c) {
  if (c.era < 4) return;
  const M = media(c);
  const all = anyAnns(world, c);
  const today = dateOf(world.clock);
  // 데뷔, 연습생의 데뷔
  for (const a of all) {
    if (isStar(a) && !a.star) debut(world, c, a);
    if (a.job?.id === 'trainee') {
      a.trainDays = (a.trainDays || 0) + 1;
      if (a.trainDays === 1) { lifeEvent(world, a, '연예기획사 연습생이 되었다', '💃'); think(world, a, '매일 노래하고 춤추고… 언젠가 무대에 설 거야.'); }
      if (a.trainDays > 120 && chance(0.006 + (a.ab.cha + a.ab.cre) / 40000)) {
        a.job.id = chance(0.6) ? 'singer' : 'actor';
        a.trainDays = 0;
        debut(world, c, a);
      } else if (a.trainDays > 700 && chance(0.004)) {
        think(world, a, '몇 년을 연습했는데… 데뷔는 내 길이 아닌가 보다.');
        remember(world, a, '연습생 생활을 그만두었다', 6, -0.6, [], 'work');
        a.job.id = 'ent_mgr';
      }
    }
  }
  // 편성
  const stations = [...world.companies.values()].filter(co => !co.dead && co.country === c.code && (co.ind === 'tv' || co.ind === 'radio'));
  for (const co of stations) schedule(world, c, M, co);
  // 시청률: 어제 실제로 본 사람의 비율 (볼 수 있는 사람 = 열 살 이상)
  const audience = Math.max(1, all.filter(a => ageOf(a, world.clock) >= 10).length);
  for (const p of M.progs) {
    const r = clamp(p.views / audience * 100 * 1.6, 0, 70);
    p.rating = Math.round((p.rating * 0.6 + r * 0.4) * 10) / 10;
    p.peak = Math.max(p.peak, p.rating);
    p.views = 0;
    for (const id of p.cast) { const a = world.anns.get(id); if (a?.alive) a.fame = clamp(a.fame + p.rating * 0.004, 0, 100); }
  }
  // 끝나는 드라마
  for (const p of M.progs.filter(p => p.end && world.clock >= p.end)) {
    world.news('culture', `🎬 〈${p.title}〉 종영 — 최고 시청률 ${p.peak}%`, { anns: p.cast, country: c.code }, p.peak > 20 ? 2 : 1);
    for (const id of p.cast) { const a = world.anns.get(id); if (a?.alive) { a.fame = clamp(a.fame + p.peak * 0.4, 0, 100); if (p.peak > 15) remember(world, a, `〈${p.title}〉이(가) 시청률 ${p.peak}%로 끝났다`, 7, 0.8, [], 'work'); } }
    M.hist.unshift({ y: today.year, k: 'end', t: p.title, peak: p.peak, co: p.coName, kind: p.kind });
  }
  M.progs = M.progs.filter(p => !(p.end && world.clock >= p.end) && world.companies.get(p.co) && !world.companies.get(p.co).dead);
  // 노래: 가수는 석 달쯤마다 새 노래를 낸다. 차트 = 명성 + 노래 실력 + 듣는 사람
  for (const a of all) {
    if (a.job?.id !== 'singer' || !a.star) continue;
    if (world.clock - (a.star.lastSong || 0) > (70 + rand() * 60) * DAY && chance(0.05)) {
      const s = { id: M.nid++, t: songTitle(), by: a.id, byName: a.name, at: Math.round(world.clock), q: clamp(a.sk.art * 0.6 + a.ab.cre * 0.4 + gauss() * 12, 5, 100), plays: 0 };
      M.songs.push(s);
      a.star.lastSong = world.clock;
      a.star.works.unshift({ t: s.t, k: 'song', at: s.at }); a.star.works.length = Math.min(a.star.works.length, 12);
      world.news('culture', `🎵 ${a.name} 새 노래 '${s.t}' 발표`, { anns: [a.id], country: c.code }, 1);
    }
  }
  const age = s => (world.clock - s.at) / DAY;
  for (const s of M.songs) s.score = (world.anns.get(s.by)?.fame || 0) * 0.5 + s.q * 0.4 + s.plays * 2 - age(s) * 0.25;
  M.songs = M.songs.filter(s => age(s) < 240).sort((x, y) => y.score - x.score);
  const top = M.songs[0];
  if (top && M.top !== top.id && age(top) > 3) {
    M.top = top.id;
    const a = world.anns.get(top.by);
    world.news('culture', `🥇 ${top.byName}의 '${top.t}' 음원 차트 1위`, { anns: [top.by], country: c.code }, 2);
    if (a?.alive) { a.fame = clamp(a.fame + 6, 0, 100); remember(world, a, `'${top.t}'이(가) 차트 1위에 올랐다`, 8, 0.9, [], 'work'); feel(a, 25, '벅참'); }
  }
  for (const s of M.songs) s.plays = Math.round(s.plays * 0.5);
  // 영화: 극장이 있으면 한두 달에 한 편, 배우가 주연
  const cinemas = [...world.companies.values()].filter(co => !co.dead && co.country === c.code && co.ind === 'cinema');
  if (cinemas.length && chance(1 / 40)) {
    const actors = all.filter(a => a.job?.id === 'actor' && a.star).sort((x, y) => (y.fame + rand() * 30) - (x.fame + rand() * 30)).slice(0, 2);
    if (actors.length) {
      const f = { id: M.nid++, t: filmTitle(), cast: actors.map(a => a.id), castNames: actors.map(a => a.name), at: Math.round(world.clock), q: clamp(actors[0].sk.art * 0.5 + gauss() * 15 + 40, 5, 100), audience: 0 };
      M.films.push(f);
      for (const a of actors) { a.star.works.unshift({ t: f.t, k: 'film', at: f.at }); a.star.works.length = Math.min(a.star.works.length, 12); }
      world.news('culture', `🎞️ 영화 〈${f.t}〉 개봉 — 주연 ${f.castNames.join(', ')}`, { anns: f.cast, country: c.code }, 1);
    }
  }
  M.films = M.films.filter(f => {
    const open = (world.clock - f.at) / DAY < 45;
    if (!open && !f.done) {
      f.done = true;
      for (const id of f.cast) { const a = world.anns.get(id); if (a?.alive) a.fame = clamp(a.fame + Math.min(15, f.audience * 0.3), 0, 100); }
      M.hist.unshift({ y: today.year, k: 'film', t: f.t, aud: f.audience, cast: f.castNames });
    }
    return (world.clock - f.at) / DAY < 120;
  });
  // 연말 시상식 (12월 30일)
  if (today.month === 12 && today.day === 30 && M.awardYear !== today.year) { M.awardYear = today.year; awards(world, c, M, stations, all, today.year); }
  // 명성은 쉬면 천천히 식는다, 팬 수 세기 (일주일에 한 번)
  for (const a of all) if (a.star) a.fame = clamp(a.fame * 0.9985, 0, 100);
  if (today.day % 7 === 0) {
    const fans = {};
    for (const a of all) if (a.fan) fans[a.fan] = (fans[a.fan] || 0) + 1;
    for (const a of all) if (a.star) a.star.fans = fans[a.id] || 0;
  }
  // 스캔들 (드물게)
  for (const a of all) if (a.star && a.fame > 15 && chance(0.00012 * (a.fame / 30))) scandal(world, c, a);
  M.hist.length = Math.min(M.hist.length, 40);
}

function awards(world, c, M, stations, all, year) {
  const give = (a, name, why) => {
    if (!a) return;
    a.fame = clamp(a.fame + 15, 0, 100);
    a.star?.awards.unshift({ y: year, n: name });
    lifeEvent(world, a, `${name} 수상${why ? ` (${why})` : ''}`, '🏆');
    remember(world, a, `${name}을(를) 받았다`, 9, 1, [], 'work');
    think(world, a, pick(['꿈만 같다. 함께해 준 모두에게 고맙다.', '이 상은 나를 믿어 준 사람들의 것이다.', '울지 않으려고 했는데…']));
    feel(a, 35, '감격');
    M.hist.unshift({ y: year, k: 'award', n: name, who: a.name });
    world.news('culture', `🏆 ${name} — ${a.name}${why ? ` (${why})` : ''}`, { anns: [a.id], country: c.code }, 2);
    for (const f of all) if (f.fan === a.id && chance(0.5)) { feel(f, 10, '기쁨'); if (chance(0.3)) think(world, f, `${a.name}이(가) 상을 받았다! 내가 다 기쁘다.`); }
  };
  for (const co of stations.filter(s => s.ind === 'tv')) {
    const works = [...M.progs, ...M.hist.filter(h => h.y === year && h.k === 'end').map(h => ({ title: h.t, peak: h.peak, co: null, kind: h.kind }))];
    // 연기대상: 그해 드라마 배우 중 (시청률 × 연기)
    const actors = all.filter(a => a.job?.id === 'actor' && a.star && a.star.works.some(w => w.k === 'drama' && dateOf(w.at).year === year));
    const best = actors.sort((x, y) => (y.fame + y.sk.art * 0.5) - (x.fame + x.sk.art * 0.5))[0];
    if (best) give(best, `${co.name} 연기대상`, best.star.works.find(w => w.k === 'drama')?.t ? `〈${best.star.works.find(w => w.k === 'drama').t}〉` : '');
    const funny = all.filter(a => (a.job?.id === 'comedian' || a.job?.id === 'singer') && a.star && a.star.works.some(w => w.k === 'variety' && dateOf(w.at).year === year));
    const bestF = funny.sort((x, y) => (y.fame + y.ab.cha * 0.3) - (x.fame + x.ab.cha * 0.3))[0];
    if (bestF) give(bestF, `${co.name} 연예대상`);
    void works;
  }
  // 가요대상: 그해 가장 사랑받은 노래
  const yearSongs = M.songs.filter(s => dateOf(s.at).year === year).sort((x, y) => y.score - x.score);
  if (yearSongs[0]) give(world.anns.get(yearSongs[0].by), `${c.name} 가요대상`, `'${yearSongs[0].t}'`);
}

function scandal(world, c, a) {
  const kinds = [
    a.partner || a.spouse ? ['열애설', +2, '사귀는 사람이 있다는 기사가 났다'] : null,
    a.job.id === 'singer' ? ['표절 논란', -10, '노래가 다른 곡과 비슷하다는 말이 돌았다'] : null,
    a.car && (a.fd?.drink || 0) > 55 ? ['음주운전', -25, '음주운전으로 적발되었다'] : null,
    ['갑질 논란', -12, '함께 일한 사람에게 막 대했다는 이야기가 퍼졌다'],
    ['악성 루머', -4, '사실이 아닌 소문이 퍼졌다'],
  ].filter(Boolean);
  const [name, d, text] = pick(kinds);
  a.fame = clamp(a.fame + d, 0, 100);
  a.stress = clamp(a.stress + (d < 0 ? 25 : 8));
  lifeEvent(world, a, `${name} — ${text}`, d < 0 ? '📰' : '💬');
  remember(world, a, text, 7, d < 0 ? -0.8 : -0.1, [], 'work');
  think(world, a, d < 0 ? pick(['사람들 앞에 나서기가 두렵다.', '어디서부터 잘못된 걸까…', '해명해야 할까, 조용히 있어야 할까.']) : '사생활은 지켜 주면 좋겠는데…');
  world.news('culture', `📰 ${a.name} ${name}`, { anns: [a.id], country: c.code }, d < -15 ? 2 : 1);
  if (d <= -20 && chance(0.4)) { a.star.hiatus = world.clock + 180 * DAY; remember(world, a, '활동을 잠시 멈추기로 했다', 7, -0.6, [], 'work'); }
  // 팬들도 흔들린다
  for (const f of world.anns.values()) if (f.fan === a.id && d < -10 && chance(0.25)) { f.fan = null; if (chance(0.3)) think(world, f, `${a.name}한테 실망했다.`); }
}

// ── 보는 사람 ──
/** 지금 무엇을 볼까 (집: 라디오·TV·유튜브, 주말엔 극장). 없으면 null */
export function pickShow(world, a, s) {
  const c = world.countries[a.country];
  if (!c || c.era < 4) return null;
  const M = media(c);
  const h = localHour(world.clock, s.lon);
  const wk = Math.floor(world.clock / DAY) % 7 >= 5;
  const age = ageOf(a, world.clock);
  // 주말 오후·저녁엔 극장 (극장이 있고 돈이 있으면)
  if (wk && h >= 13 && h < 22 && age >= 12 && M.films.length && chance(0.3)) {
    const cin = s.buildings.map(id => world.buildings.get(id)).find(b => b?.done && b.type === 'cinema' && b.co && !world.companies.get(b.co)?.dead);
    const f = M.films.filter(f => (world.clock - f.at) / DAY < 45).sort((x, y) => appealFilm(a, y) - appealFilm(a, x))[0];
    if (cin && f) return { kind: 'film', id: f.id, title: f.t, where: cin.id, label: `🎬 영화 〈${f.t}〉 (${cin.name})`, cast: f.cast };
  }
  // 정보화 시대: 젊을수록 유튜브
  if (c.era >= 6 && chance(clamp(0.7 - age / 100, 0.1, 0.65))) {
    const creators = [...world.anns.values()].filter(b => b.alive && b.country === a.country && b.job?.id === 'creator');
    const cr = creators.length ? creators.sort((x, y) => (y.fame + rand() * 30 + (a.fan === y.id ? 40 : 0)) - (x.fame + rand() * 30 + (a.fan === x.id ? 40 : 0)))[0] : null;
    return { kind: 'tube', id: cr?.id || null, title: cr ? `${cr.name} 채널` : '여러 동영상', where: 'home', label: `📱 유튜브 — ${cr ? cr.name + ' 채널' : '이것저것'}`, cast: cr ? [cr.id] : [] };
  }
  const tvOk = c.era >= 5;
  const progs = M.progs.filter(p => (tvOk || p.kind === 'radio' || p.kind === 'news') && !(p.kind === 'news' && h < 18 && h > 9));
  if (!progs.length) return null;
  const p = progs.sort((x, y) => appealProg(world, a, y) - appealProg(world, a, x))[0];
  const radio = p.kind === 'radio' || !tvOk;
  return { kind: 'prog', pk: p.kind, id: p.id, title: p.title, where: 'home', label: `${radio ? '📻 라디오' : '📺 TV'} — 〈${p.title}〉`, cast: p.cast };
}
function appealProg(world, a, p) {
  const age = ageOf(a, world.clock);
  // 입소문(시청률)은 조금만, 취향과 그날 기분이 크게 — 한 프로그램이 모두를 독차지하지 않게
  let s = p.q * 0.3 + Math.min(20, p.rating) * 0.4 + rand() * 40;
  for (const id of p.cast) { const b = world.anns.get(id); if (b) s += b.fame * 0.1 + (a.fan === id ? 30 : 0); }
  if (p.kind === 'drama') s += a.tr.O * 0.1 + (a.gender === 'F' ? 4 : 0);
  if (p.kind === 'variety') s += a.tr.E * 0.15 + (age < 30 ? 6 : 0);
  if (p.kind === 'music') s += age < 25 ? 12 : -4;
  if (p.kind === 'news') s += (age - 30) * 0.4 + a.tr.C * 0.1;
  if (p.kind === 'docu') s += a.tr.O * 0.15 + a.ab.int * 0.05 - 6;
  if (p.kind === 'radio') s += age > 40 ? 6 : 0;
  return s;
}
const appealFilm = (a, f) => f.q * 0.5 + rand() * 30 + (f.cast.includes(a.fan) ? 30 : 0);

/** 다 보고 나서: 시청 기록, 팬이 되기, 생각 */
export function watched(world, a, act) {
  const c = world.countries[a.country];
  const M = c && media(c);
  if (!M || !act.show) return;
  const sh = act.show;
  if (sh.kind === 'prog') { const p = M.progs.find(p => p.id === sh.id); if (p) p.views++; if (p?.kind === 'music') for (const s of M.songs.slice(0, 5)) if (p.cast.includes(s.by)) s.plays++; }
  if (sh.kind === 'film') { const f = M.films.find(f => f.id === sh.id); if (f) f.audience++; }
  if (sh.kind === 'tube' && sh.id) { const cr = world.anns.get(sh.id); if (cr?.alive) cr.fame = clamp(cr.fame + 0.004, 0, 100); }
  // 라디오에서 들은 노래
  if (sh.kind === 'prog' && /라디오/.test(act.label || '')) { const s = pick(M.songs.slice(0, 8)); if (s) s.plays++; }
  // 출연자에게 반하기
  const star = sh.cast?.length ? world.anns.get(pick(sh.cast)) : null;
  if (star?.alive && star.id !== a.id && !a.fan && chance(0.004 + star.fame / 12000 + (ageOf(a, world.clock) < 25 ? 0.005 : 0))) {
    a.fan = star.id;
    remember(world, a, `${star.name}의 팬이 되었다`, 4, 0.7, [star.id], 'life');
    think(world, a, pick([`${star.name}, 왜 이제 알았지?`, `${star.name} 너무 좋다!`, `내일도 ${star.name} 나오는 거 봐야지.`]));
  } else if (chance(0.04)) {
    think(world, a, sh.kind === 'film' ? pick([`〈${sh.title}〉 생각보다 재밌었다.`, `〈${sh.title}〉 마지막 장면이 계속 생각난다.`, `〈${sh.title}〉 같이 볼 사람이 있었으면.`])
      : sh.kind === 'tube' ? pick(['한 편만 보려다 두 시간이 갔다.', '알고리즘이 날 너무 잘 안다.', `${sh.title} 새 영상 올라왔다!`, '자기 전에 보는 영상이 제일 재밌다.', '이러다 밤새겠다.'])
      : pick({
        drama: [`〈${sh.title}〉 다음 주가 기다려진다.`, `〈${sh.title}〉 보다가 눈물이 났다.`, `〈${sh.title}〉 주인공이 너무 답답하다!`],
        variety: [`〈${sh.title}〉 보면서 한참 웃었다.`, `〈${sh.title}〉 오늘 회차 재밌었다.`],
        music: [`〈${sh.title}〉에 나온 노래가 머리에서 안 떠난다.`, '요즘 노래는 다 비슷하게 들린다… 나도 늙었나.'],
        news: ['요즘 세상이 참 시끄럽다.', '뉴스를 보니 마음이 무겁다.', '그래도 좋은 소식도 있네.'],
        docu: ['몰랐던 걸 알게 됐다.', `〈${sh.title}〉 보고 나니 여행이 가고 싶다.`],
        radio: [`〈${sh.title}〉에 사연을 보내 볼까.`, '라디오에서 옛날 노래가 나왔다.'],
      }[sh.pk] || [`〈${sh.title}〉 틀어 놓고 깜빡 졸았다.`]));
  }
}

// ── 화면용 ──
export function mediaOut(world, c) {
  const M = c.media;
  if (!M || c.era < 4) return null;
  const nm = id => world.anns.get(id)?.name;
  const stars = [...world.anns.values()].filter(a => a.alive && a.country === c.code && a.star).sort((x, y) => y.fame - x.fame).slice(0, 6)
    .map(a => ({ id: a.id, n: a.name, k: STAR_JOBS[a.star.kind] || STAR_JOBS[a.job?.id] || '연예인', fame: Math.round(a.fame), fans: a.star.fans || 0 }));
  return {
    progs: [...M.progs].sort((x, y) => y.rating - x.rating).slice(0, 6).map(p => ({ t: p.title, k: KIND[p.kind], co: p.coName, r: p.rating, cast: p.cast.map(nm).filter(Boolean).slice(0, 3) })),
    chart: M.songs.slice(0, 5).map(s => ({ t: s.t, by: s.byName })),
    films: M.films.filter(f => (world.clock - f.at) / DAY < 45).map(f => ({ t: f.t, cast: f.castNames, aud: f.audience })),
    stars,
    hist: M.hist.slice(0, 8),
  };
}
export function starOut(world, a) {
  const out = {};
  if (a.star) out.star = { k: STAR_JOBS[a.star.kind] || '연예인', fans: a.star.fans || 0, works: a.star.works.slice(0, 6), awards: a.star.awards.slice(0, 5), since: a.star.since };
  if (a.fan) { const s = world.anns.get(a.fan); if (s) out.fan = { id: s.id, n: s.name, alive: s.alive }; }
  return out;
}
