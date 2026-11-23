// 앤이 지어내는 것: 노래 · 이야기 · 놀이 · 음식 · 도구 · 속담 · 춤.
//  · 창의성과 호기심이 큰 앤일수록, 그 일을 하는 앤일수록(이야기꾼·주술사·도구 장인·요리사…) 자주 지어낸다.
//  · 이름은 지은 사람의 삶에서 나온다 — 고향의 자연(바다·강·산·숲), 계절, 잃은 사람, 사랑하는 사람, 아이, 부족 싸움.
//  · 대화·가족 시간·아이들 놀이로 퍼진다: 할머니가 손주에게 옛이야기를 들려주고, 아이들은 새 놀이를 가르쳐 주고받는다.
//  · 많이 퍼지면 마을의 노래가 되고, 지은 사람은 이름을 얻는다. 도구는 기술을, 노래와 춤은 즐거움을 준다.
import { clamp, rand, pick, chance, ageOf, DAY, YEAR, ga, neun, reul, wa, fixJosa } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { siteFeatures } from './settlement.js';
import { ERAS } from './data.js';

export const KINDS = {
  song: { name: '노래', icon: '🎵', verb: '불렀다', learn: '배웠다' },
  story: { name: '이야기', icon: '📜', verb: '들려주었다', learn: '들었다' },
  game: { name: '놀이', icon: '🪀', verb: '하며 놀았다', learn: '배웠다' },
  dish: { name: '음식', icon: '🍲', verb: '만들어 먹었다', learn: '배웠다' },
  tool: { name: '도구', icon: '🛠️', verb: '써 보였다', learn: '배웠다' },
  saying: { name: '속담', icon: '💬', verb: '말해 주었다', learn: '마음에 새겼다' },
  dance: { name: '춤', icon: '💃', verb: '추었다', learn: '배웠다' },
};

// ── 이름 재료 ──
const NATURE = {
  sea: ['바다', '파도', '갈매기', '썰물', '조개껍데기', '고래'], river: ['강물', '여울', '물고기', '버드나무', '징검다리'], lake: ['호수', '물안개', '물새'],
  mountain: ['산', '바위', '메아리', '산마루', '구름'], forest: ['숲', '도토리', '사슴', '부엉이', '단풍잎'], desert: ['모래바람', '낙타', '오아시스', '별밤'],
  snow: ['눈보라', '얼음', '순록', '긴 겨울밤'], tropical: ['야자수', '앵무새', '소나기', '큰 잎사귀'], any: ['별', '보름달', '노을', '바람', '새벽', '들꽃', '반딧불'],
};
const SEASON = { 봄: ['봄비', '꽃바람', '새싹'], 여름: ['여름밤', '매미', '소나기'], 가을: ['가을 달', '낙엽', '추수'], 겨울: ['첫눈', '긴 밤', '화롯불'] };
const BEAST = ['곰', '호랑이', '늑대', '멧돼지', '독수리', '여우', '큰 물고기', '뱀'];
const HERO = ['꼬마 사냥꾼', '외눈 할머니', '용감한 소녀', '게으른 형', '꾀 많은 막내', '바보 나무꾼', '말 못 하는 아이', '떠돌이 이야기꾼'];
const GAMES = ['돌멩이 숨기기', '그림자 밟기', '나뭇잎 배 띄우기', '막대 멀리 던지기', '외발 뛰기', '깃털 불기', '조개 맞히기', '흙떡 쌓기', '풀피리 겨루기', '실뜨기', '돌 차기', '눈 감고 술래', '나무 타기 겨루기', '조약돌 공기'];
const TOOLS = [
  ['뼈바늘', '돌칼 손잡이', '나무 작살', '풀 그물', '가죽 물주머니', '새 덫', '불씨 상자', '돌망치'],
  ['돌보습', '반달 돌칼', '가락바퀴', '지게', '절구', '통발', '토기 뚜껑', '물동이 받침', '볍씨 고르는 체'],
  ['쟁기 날', '수레 굴대', '베틀 북', '두레박', '숫돌', '키', '대나무 그릇'],
  ['물레방아 날개', '풀무', '맷돌 손잡이', '나침반 바늘', '도르래'],
];
const DISH = [
  ['구운 물고기 꼬치', '산딸기 즙', '돌판에 구운 고기', '도토리 가루 반죽', '훈제 고기', '꿀 바른 열매'],
  ['도토리묵', '조 죽', '쑥떡', '말린 생선 국', '콩 지짐', '보리 개떡', '산나물 무침'],
  ['된장국', '김치 비슷한 것', '수수부꾸미', '버섯 전골', '떡국'],
];
const PROVERB = [
  n => `${n}은(는) 하루아침에 생기지 않는다`, () => '배고픈 사냥꾼은 겁이 없다', n => `${n}을(를) 보면 내일 날씨를 안다`, () => '함께 든 돌은 가볍다',
  () => '말 많은 사냥꾼은 빈손으로 돌아온다', () => '씨앗을 아끼면 가을이 빈다', () => '불은 나누어도 줄지 않는다', () => '아이의 웃음은 집의 지붕보다 든든하다',
  n => `${n} 아래에서 한 약속은 깨지지 않는다`, () => '천천히 걷는 사람이 길을 잃지 않는다', () => '빈 그릇이 소리가 크다', () => '눈물로 지은 집은 비가 새지 않는다',
];

function natureWords(world, a) {
  const s = world.settlements.get(a.sid);
  const f = s ? siteFeatures(s) : {};
  const out = [...NATURE.any];
  for (const k of ['sea', 'river', 'lake', 'mountain']) if (f[k]) out.push(...NATURE[k], ...NATURE[k]);
  if (f.biome && NATURE[f.biome]) out.push(...NATURE[f.biome]);
  if (f.biome !== 'desert') out.push(...NATURE.forest);
  const sea = s?.weather?.season;
  if (sea && SEASON[sea]) out.push(...SEASON[sea], ...SEASON[sea]);
  return out;
}
/** 이 사람의 삶에서 나온 사람: 잃은 사람, 사랑하는 사람, 아이 */
function lifePeople(world, a) {
  const lost = (a.mem || []).filter(m => m.k === 'death' && m.who?.length).slice(-3).map(m => world.anns.get(m.who[0])).filter(Boolean);
  const kids = a.kids.map(id => world.anns.get(id)).filter(k => k?.alive && ageOf(k, world.clock) < 6);
  const love = a.spouse ? world.anns.get(a.spouse) : a.partner ? world.anns.get(a.partner) : null;
  return { lost, kids, love };
}
const given = n => (n.length >= 3 && /[가-힣]/.test(n) ? n.slice(1) : n.split(' ')[0]);

function nameFor(world, a, kind) {
  const era = world.countries[a.country]?.era ?? 0;
  const N = pick(natureWords(world, a));
  const { lost, kids, love } = lifePeople(world, a);
  switch (kind) {
    case 'song': {
      const o = [`${N} 노래`, `${N}의 노래`, `${N} 타령`, `${N}을(를) 기다리며`];
      if (lost.length) o.push(`그리운 ${given(pick(lost).name)}`, `${given(pick(lost).name)}을(를) 보내며`);
      if (love) o.push(`${given(love.name)}에게`, `${N} 아래 ${given(love.name)}`);
      if (kids.length) o.push(`${given(pick(kids).name)} 자장가`, `${N} 자장가`);
      if (era >= 1) o.push('풍년가', '모내기 노래', '김매기 노래');
      if ((a.mem || []).some(m => m.k === 'war' && world.clock - m.t < YEAR)) o.push('싸움터에서 돌아온 날', '피 흘린 들판');
      return pick(o);
    }
    case 'story': {
      const B = pick(BEAST), H = pick(HERO);
      return pick([`${N}에 사는 ${B}`, `불을 훔친 ${H}`, `${H}와(과) 큰 ${B}`, `${N} 너머의 마을`, `별이 된 ${H}`, `${H}의 지혜`, `${B}을(를) 속인 ${H}`, `${N}이(가) 울던 밤`]);
    }
    case 'game': return chance(0.4) ? `${pick(['새', '거꾸로', '둘이서', '편 갈라'])} ${pick(GAMES)}` : pick(GAMES);
    case 'dish': return pick(DISH[Math.min(2, era)]);
    case 'tool': return pick(TOOLS[Math.min(3, era)]);
    case 'saying': return pick(PROVERB)(N);
    case 'dance': return pick([`${N} 춤`, `${pick(BEAST)} 흉내 춤`, '불 둘레 춤', '비 부르는 춤', era >= 1 ? '풍년 춤' : '사냥 떠나는 춤', `${N} 맞이 춤`]);
  }
  return N;
}

/** 이 사람이 지어낼 만한 것 (나이·일·성격) */
function kindFor(a, age) {
  const j = a.job?.id || '';
  const w = {
    game: age < 14 ? 6 : 0,
    song: age >= 12 ? 2 + (j === 'shaman' || j === 'storyteller' ? 3 : 0) + a.sk.art / 40 : age >= 6 ? 0.6 : 0,
    story: age >= 16 ? 1.5 + (j === 'storyteller' ? 5 : 0) + (age >= 55 ? 2.5 : 0) : 0,
    dish: age >= 16 ? 0.8 + (/cook|chef|baker/.test(j) ? 4 : 0) + a.sk.cooking / 50 : 0,
    tool: age >= 16 ? 0.6 + (/toolmaker|craftsman|blacksmith|peasant|farmer|fisher|hunter|builder/.test(j) ? 2.5 : 0) + a.sk.crafting / 50 : 0,
    saying: age >= 45 ? 1 + (a.ab.int + a.tr.A) / 120 : 0,
    dance: age >= 12 && age < 45 ? 0.8 + a.tr.E / 60 : 0,
  };
  const tot = Object.values(w).reduce((s, v) => s + v, 0);
  let r = rand() * tot;
  for (const [k, v] of Object.entries(w)) { r -= v; if (r <= 0) return k; }
  return 'song';
}

function learnIt(a, id) {
  a.cre = a.cre || [];
  if (a.cre.includes(id)) return false;
  a.cre.push(id);
  if (a.cre.length > 24) a.cre.shift();
  return true;
}

/** 하루에 한 번: 지어낼까 */
export function createDaily(world, a) {
  const age = ageOf(a, world.clock);
  if (age < 5 || a.travel || a.mood < -40) return;
  const p = 0.0028 * (a.ab.cre / 60) ** 2 * (0.6 + a.tr.O / 120) * (1 + (a.job?.id === 'storyteller' || a.job?.id === 'shaman' ? 1 : 0)) * (a.nd.fun < 30 ? 1.3 : 1);
  if (!chance(p)) return;
  const kind = kindFor(a, age);
  const name = fixJosa(nameFor(world, a, kind)).replace(/\s+/g, ' ').trim();
  world.creations = world.creations || new Map();
  for (const c of world.creations.values()) if (c.name === name && c.sid === a.sid && c.kind === kind) return; // 같은 마을에 같은 것이 이미 있다
  const s = world.settlements.get(a.sid);
  const cr = { id: world.newId(), kind, name, by: a.id, byName: a.name, sid: a.sid, country: a.country, t: Math.round(world.clock), n: 1, era: world.countries[a.country]?.era ?? 0 };
  world.creations.set(cr.id, cr);
  learnIt(a, cr.id);
  a.made = [...(a.made || []), cr.id].slice(-12);
  const K = KINDS[kind];
  remember(world, a, `'${name}'(이)라는 ${K.name}을(를) 지었다`, 7, 0.8, [], 'art');
  lifeEvent(world, a, `${K.name} '${name}' 지음`, K.icon);
  feel(a, 15, '뿌듯함', name);
  think(world, a, pick({
    song: [`흥얼거리다 보니 노래가 됐다. '${name}'… 괜찮은데?`, `'${name}'. 이 노래를 누가 따라 불러 줄까.`],
    story: [`'${name}' — 아이들에게 들려주면 좋아하겠다.`, '이야기가 저절로 떠올랐다. 잊기 전에 누구에게 들려줘야지.'],
    game: [`새 놀이를 만들었다! '${name}'. 내일 친구들한테 알려 줘야지.`, `'${name}' 하자고 하면 다들 좋아할 거야.`],
    dish: [`'${name}'… 이렇게 해 먹으니 훨씬 맛있다.`, '이 맛을 식구들에게도 보여 주고 싶다.'],
    tool: [`'${name}'을(를) 만들었다. 이제 일이 한결 수월하겠다.`, '손에 착 붙는다. 이웃에게도 만들어 줘야지.'],
    saying: [`"${name}" — 살아 보니 그렇더라.`, `젊은 사람들에게 꼭 해 주고 싶은 말이 생겼다: "${name}"`],
    dance: [`'${name}'… 몸이 저절로 움직였다.`, '다음 모닥불 가에서 이 춤을 춰 봐야지.'],
  }[kind]));
  if (kind === 'tool') { const c = world.countries[a.country]; world.addTech(c, ERAS[c.era].wage * 6, 'tool'); a.techMade = (a.techMade || 0) + ERAS[c.era].wage * 6; }
  if (a.owner) world.news('art', `${K.icon} ${ga(a.name)} ${K.name} '${name}'을(를) 지었다`, { anns: [a.id], country: a.country, sid: a.sid }, 1);
  world.dirtyDetail.add(a.id);
}

/**
 * 대화 중에 알려 주기. 퍼뜨릴 것이 있으면 { topic, line } (social.js converse 가 쓴다)
 * kind: 'family' 는 어르신이 아이에게 이야기를, 'play' 는 아이들끼리 놀이를
 */
export function shareCreation(world, a, b, kind = 'chat') {
  if (!a.cre?.length || !world.creations) return null;
  const ageA = ageOf(a, world.clock), ageB = ageOf(b, world.clock);
  const cand = a.cre.map(id => world.creations.get(id)).filter(c => c && !(b.cre || []).includes(c.id)).filter(c => {
    if (c.kind === 'game') return ageB < 15 || kind === 'play';
    if (c.kind === 'saying') return ageB >= 10;
    if (c.kind === 'tool' || c.kind === 'dish') return ageB >= 12;
    return ageB >= 3;
  });
  if (!cand.length) return null;
  // 어르신은 이야기와 속담을, 아이들은 놀이를 먼저
  cand.sort((x, y) => score(y) - score(x));
  function score(c) { return (c.kind === 'story' && ageA >= 50 && ageB < 14 ? 5 : 0) + (c.kind === 'game' && kind === 'play' ? 5 : 0) + (c.by === a.id ? 2 : 0) + Math.log(1 + c.n) + rand(); }
  const c = cand[0], K = KINDS[c.kind];
  learnIt(b, c.id);
  c.n++;
  const mine = c.by === a.id;
  const elderKid = ageA >= 50 && ageB < 14;
  let line;
  if (c.kind === 'story') line = elderKid ? `${a.gender === 'F' ? '할머니' : '할아버지'} ${neun(a.name)} ${b.name}에게 옛이야기 '${c.name}'을(를) 들려주었다` : `${neun(a.name)} ${b.name}에게 '${c.name}' 이야기를 들려주었다`;
  else if (c.kind === 'game') line = `${wa(a.name)} ${neun(b.name)} '${c.name}'을(를) 하며 놀았다`;
  else if (c.kind === 'song') line = `${neun(a.name)} ${b.name}에게 ${mine ? '자기가 지은 ' : ''}노래 '${c.name}'을(를) 가르쳐 주었다`;
  else if (c.kind === 'saying') line = `${neun(a.name)} ${b.name}에게 "${c.name}"(이)라고 말해 주었다`;
  else if (c.kind === 'dance') line = `${wa(a.name)} ${neun(b.name)} 함께 '${c.name}'을(를) 추었다`;
  else line = `${neun(a.name)} ${b.name}에게 ${K.name} '${c.name}' 만드는 법을 알려 주었다`;
  remember(world, b, c.kind === 'saying' ? `${ga(a.name)} "${c.name}"(이)라고 해 주었다` : `${a.name}에게서 ${K.name} '${c.name}'을(를) ${K.learn}`, elderKid ? 6 : 4, 0.6, [a.id], 'art');
  b.nd.fun = clamp(b.nd.fun + (c.kind === 'game' || c.kind === 'song' || c.kind === 'dance' ? 12 : 5));
  if (elderKid && chance(0.5)) think(world, b, pick([`'${c.name}'… 또 들려 달라고 해야지.`, '할머니 할아버지 이야기는 언제 들어도 재미있다.', '나도 크면 이 이야기를 들려줘야지.']));
  // 널리 퍼지면 소식이 되고, 지은 사람은 이름을 얻는다
  const maker = world.anns.get(c.by);
  if ([8, 25, 60, 150].includes(c.n)) {
    const town = world.settlements.get(c.sid)?.name || '';
    world.news('art', `${K.icon} ${c.byName}이(가) 지은 ${K.name} '${c.name}'이(가) ${c.n >= 60 ? '온 땅에' : `${town} 둘레에`} 퍼졌다 — ${c.n}명이 안다`, { anns: [c.by], country: c.country, sid: c.sid }, c.n >= 25 ? 2 : 1);
    if (maker?.alive) {
      maker.fame = clamp((maker.fame || 0) + (c.n >= 25 ? 6 : 3), 0, 100);
      remember(world, maker, `내가 지은 '${c.name}'을(를) ${c.n}명이 알게 되었다`, c.n >= 25 ? 8 : 6, 0.9, [], 'art');
      if (c.n >= 25) think(world, maker, pick([`'${c.name}'을(를) 모르는 사람이 없다니… 꿈만 같다.`, '내가 떠나도 이건 남겠구나.']));
      if (c.n >= 25) { const g = maker.goals?.find(g => (g.k === 'art' || g.k === 'fame') && !g.done); if (g) world.achieveGoal(maker, g); }
    }
  }
  return { topic: K.name, line };
}

/** 하루에 한 번: 아는 노래를 흥얼거리고, 아는 놀이를 떠올린다 (생각과 즐거움) */
export function cultureMood(world, a) {
  if (!a.cre?.length || !chance(0.12)) return;
  const c = world.creations?.get(pick(a.cre));
  if (!c) return;
  if (c.kind === 'song' || c.kind === 'dance') { a.nd.fun = clamp(a.nd.fun + 6); think(world, a, c.kind === 'song' ? `♪ '${c.name}'을(를) 흥얼거렸다.` : `'${c.name}' 생각에 어깨가 들썩인다.`); }
  else if (c.kind === 'saying' && chance(0.5)) think(world, a, `"${c.name}" — ${c.byName}의 말이 오늘따라 떠오른다.`);
  else if (c.kind === 'story' && chance(0.4)) think(world, a, `'${c.name}' 이야기가 생각난다.`);
}

/** 오래되고 아무도 모르는 것은 잊힌다 */
export function pruneCreations(world) {
  if (!world.creations?.size) return;
  const known = new Set();
  for (const a of world.anns.values()) if (a.alive) for (const id of a.cre || []) known.add(id);
  for (const [id, c] of world.creations) if (!known.has(id) && world.clock - c.t > YEAR) world.creations.delete(id);
}

/** 화면용: 앤이 지은 것과 아는 것 */
export function creationsOf(world, a) {
  const out = id => { const c = world.creations?.get(id); return c ? { id: c.id, kind: c.kind, icon: KINDS[c.kind].icon, kindName: KINDS[c.kind].name, name: c.name, by: c.byName, byId: c.by, n: c.n } : null; };
  return { made: (a.made || []).map(out).filter(Boolean), knows: (a.cre || []).filter(id => !(a.made || []).includes(id)).slice(-10).map(out).filter(Boolean) };
}
/** 화면용: 이 마을 사람들이 가장 많이 아는 것 */
export function townCulture(world, sid) {
  if (!world.creations?.size) return [];
  const cnt = new Map();
  for (const a of world.residents(sid)) for (const id of a.cre || []) cnt.set(id, (cnt.get(id) || 0) + 1);
  return [...cnt.entries()].sort((x, y) => y[1] - x[1]).slice(0, 6).map(([id, n]) => { const c = world.creations.get(id); return c && { kind: c.kind, icon: KINDS[c.kind].icon, kindName: KINDS[c.kind].name, name: c.name, by: c.byName, here: n }; }).filter(Boolean);
}
