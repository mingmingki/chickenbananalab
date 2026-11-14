// 속마음 조합기: AI 없이, (감정에 따른 첫마디) × (지금 마음에 걸리는 주제와 그 변주) × (성격에 따른 끝맺음)
// 주제마다 여러 문장 틀과 낱말 묶음이 있어 수만 가지 생각이 만들어진다.
import { pick, chance, ageOf, localHour, dateOf, callName, ga, wa, reul, neun } from './util.js';
import { JOBS, ERAS, BUILDINGS } from './data.js';
import { siteFeatures } from './settlement.js';

export const SEASON = m => (m >= 3 && m <= 5 ? '봄' : m >= 6 && m <= 8 ? '여름' : m >= 9 && m <= 11 ? '가을' : '겨울');
export function seasonOf(world, s) {
  const m = dateOf(world.clock).month;
  const mm = s && s.lat < 0 ? ((m + 5) % 12) + 1 : m;
  return SEASON(mm);
}

const W = {
  season: {
    봄: ['꽃 냄새가 바람에 실려 온다', '새싹이 돋는 걸 보니 마음이 간질간질하다', '겨우내 굳었던 몸이 풀리는 것 같다', '나비가 날아다닌다', '봄볕이 나른하다'],
    여름: ['땀이 비 오듯 흐른다', '매미 소리가 귀를 찌른다', '해가 길어서 하루가 길다', '그늘만 찾게 된다', '밤에도 후텁지근하다'],
    가을: ['하늘이 높고 파랗다', '나뭇잎이 붉게 물들었다', '바람이 선선해서 좋다', '어쩐지 마음이 쓸쓸해진다', '거둬들일 게 많은 계절이다'],
    겨울: ['손끝이 꽁꽁 언다', '입김이 하얗게 나온다', '따뜻한 국물이 생각난다', '눈 덮인 마을이 조용하다', '해가 너무 일찍 진다'],
  },
  weather: {
    맑음: ['햇살이 눈부시다', '구름 한 점 없다', '이런 날은 뭐든 잘될 것 같다'],
    구름: ['하늘이 잔뜩 흐리다', '해가 숨어 버렸다', '비가 오려나'],
    비: ['빗소리가 차분하다', '비 냄새가 난다', '옷이 다 젖었다'],
    소나기: ['갑자기 쏟아지는 소나기에 다들 뛰어간다', '소나기가 지나가고 무지개가 떴다'],
    폭우: ['비가 무섭게 퍼붓는다', '물이 불어서 걱정이다'],
    눈: ['눈이 소복소복 쌓인다', '온 세상이 하얗다', '눈사람을 만들고 싶다'],
    바람: ['바람이 세차게 분다', '바람에 지붕이 덜컹거린다'],
    안개: ['안개가 자욱해서 앞이 잘 안 보인다', '안개 속 마을이 꿈속 같다'],
    무더위: ['숨이 턱턱 막히는 더위다', '시원한 물에 뛰어들고 싶다'],
    한파: ['살을 에는 추위다', '이불 밖으로 나가기가 싫다'],
    태풍: ['태풍이 온다더니 바람이 심상치 않다', '집이 버텨 줄지 모르겠다'],
  },
  nature: {
    sea: ['파도 소리가 들린다', '바다 냄새가 짭조름하다', '수평선 너머엔 뭐가 있을까', '갈매기가 끼룩거린다', '바닷물이 반짝인다'],
    river: ['강물 흐르는 소리가 좋다', '강가 버드나무가 흔들린다', '강에 비친 하늘이 예쁘다', '물고기가 펄떡 뛰어올랐다'],
    lake: ['호수가 거울처럼 잔잔하다', '호숫가에 새들이 모여 있다'],
    mountain: ['산 위로 구름이 걸려 있다', '저 산꼭대기에 오르면 뭐가 보일까', '산이 마을을 지켜 주는 것 같다', '산에서 메아리가 돌아온다'],
    forest: ['숲에서 새소리가 들린다', '나무 냄새가 상쾌하다', '숲 속은 서늘하다', '다람쥐가 도토리를 물고 간다'],
  },
  pets: ['강아지', '고양이', '토끼', '병아리', '거북이', '앵무새'],
  animals: ['사슴', '토끼', '여우', '멧돼지', '꿩', '두루미', '다람쥐', '고라니', '부엉이', '청설모'],
  fish: ['붕어', '잉어', '송어', '메기', '은어', '고등어', '전어', '도미', '숭어', '가자미'],
  food: [['도토리묵', 0], ['구운 생선', 0], ['산딸기', 0], ['고기 구이', 0], ['보리밥', 1], ['된장국', 1], ['떡', 1], ['국수', 2], ['만두', 2], ['비빔밥', 3], ['빵', 3], ['스튜', 4], ['카레', 5], ['라면', 5], ['피자', 6], ['떡볶이', 6], ['우주식', 7]],
  subjects: [['글자', 1], ['셈', 1], ['역사', 2], ['노래', 1], ['그림', 1], ['과학', 3], ['체육', 3], ['외국어', 4], ['컴퓨터', 6], ['코딩', 6]],
  games: ['술래잡기', '숨바꼭질', '공기놀이', '딱지치기', '비석치기', '고무줄놀이', '팽이치기', '연날리기', '물수제비', '모래성 쌓기'],
  hobbies: ['노래', '그림', '나무 깎기', '별 보기', '낚시', '산책', '요리', '이야기 짓기', '춤', '꽃 가꾸기'],
};

const OPEN = {
  happy: ['아,', '와,', '후후,', '오늘따라', '왠지', '음~', '이상하게'],
  calm: ['음…', '그러고 보니', '문득', '가만히 생각해 보면', '요즘', '새삼', ''],
  sad: ['하아…', '괜히', '왜 이렇게', '아무래도', '그냥…'],
  angry: ['정말이지,', '어휴,', '도대체', '참나,'],
  anxious: ['혹시', '괜찮을까…', '자꾸만', '설마'],
  love: ['두근두근…', '아,', '자꾸만'],
};
const CLOSE = {
  O: ['세상은 참 신기하다.', '새로운 걸 해 보고 싶다.', '아직 모르는 게 너무 많다.', '내일은 다른 길로 가 봐야지.'],
  C: ['할 일부터 끝내자.', '내일 계획을 세워 둬야지.', '차근차근 하면 된다.', '게으름 피우면 안 돼.'],
  E: ['누구랑 이야기하고 싶다.', '다 같이 모이면 좋겠다.', '심심하다, 누구 없나?'],
  A: ['다들 잘 지냈으면 좋겠다.', '오늘은 누구를 도와줄까.', '서로 아끼며 살아야지.'],
  N: ['괜히 마음이 불안하다.', '잘못되면 어떡하지.', '걱정이 꼬리를 문다.'],
  amb: ['언젠가 큰일을 해낼 거다.', '여기서 멈출 수는 없다.', '더 높이 올라가야 한다.'],
  lowC: ['뭐, 어떻게든 되겠지.', '내일 생각하자.'],
};

function mood(a) {
  const k = a.emo?.k || '';
  if (['설렘'].includes(k)) return 'love';
  if (['분노', '불만'].includes(k)) return 'angry';
  if (['불안', '걱정'].includes(k) || a.stress > 60) return 'anxious';
  if (a.mood < -20 || ['슬픔', '상심', '절망', '외로움', '우울', '좌절'].includes(k)) return 'sad';
  if (a.mood > 25) return 'happy';
  return 'calm';
}

function dominant(a) {
  const c = [['O', a.tr.O], ['C', a.tr.C], ['E', a.tr.E], ['A', a.tr.A], ['N', a.tr.N], ['amb', a.amb]].sort((x, y) => y[1] - x[1]);
  if (a.tr.C < 25 && chance(0.4)) return 'lowC';
  return c[0][1] > 62 ? c[0][0] : null;
}

const eraFood = era => W.food.filter(f => f[1] <= era).map(f => f[0]);

/** 지금 이 앤의 마음에 떠오를 법한 주제들 (가중치, 문장 생성기) */
function topics(world, a) {
  const now = world.clock;
  const s = world.settlements.get(a.sid);
  const c = world.countries[a.country];
  const age = ageOf(a, now);
  const f = siteFeatures(s);
  const season = seasonOf(world, s);
  const wk = s.weather?.k || '맑음';
  const T = [];
  const add = (w, gen) => T.push([w, gen]);
  const nm = id => world.anns.get(id);

  add(1.2, () => pick([`${pick(W.season[season])}.`, `${season}이다. ${pick(W.season[season])}.`, `벌써 ${season}이구나.`]));
  if (W.weather[wk]) add(1.4, () => `${pick(W.weather[wk])}.`);
  const nat = Object.keys(W.nature).filter(k => f[k] || (k === 'forest'));
  if (nat.length) add(1.3, () => `${pick(W.nature[pick(nat)])}.`);
  if (chance(0.3)) add(0.6, () => pick([`아까 ${pick(W.animals)} 한 마리가 지나갔다.`, `${pick(W.animals)} 발자국을 봤다.`, `${reul(pick(W.animals))} 쫓아가 보고 싶다.`]));
  if (a.nd.food < 55) add(2 + (55 - a.nd.food) / 15, () => pick([`${pick(eraFood(c.era))} 먹고 싶다.`, '배가 꼬르륵거린다.', `오늘 저녁은 ${pick(eraFood(c.era))}였으면.`]));
  if (a.nd.energy < 30) add(2, () => pick(['눈꺼풀이 무겁다.', '조금만 누웠다 가고 싶다.', '몸이 천근만근이다.']));
  if (a.nd.social < 35) add(2, () => pick(['외롭다.', '누가 말 좀 걸어 줬으면.', '혼자 있는 게 오늘은 싫다.']));
  if (a.nd.fun < 35) add(1.5, () => pick([`${pick(W.hobbies)}라도 해야겠다.`, '너무 지루하다.', '뭐 재미있는 일 없나?']));

  // 아이·학생
  if (age < 13) {
    add(2, () => pick([`${pick(W.games)} 하고 싶다!`, `내일은 친구들이랑 ${pick(W.games)} 해야지.`, `${reul(pick(W.pets))} 키우고 싶다.`, '어른이 되면 뭐가 될까?', `엄마 아빠는 왜 맨날 일만 할까?`]));
  }
  if (a.school) {
    const subj = W.subjects.filter(x => x[1] <= Math.max(1, c.era)).map(x => x[0]);
    add(2.4, () => pick([`오늘 ${pick(subj)} 시간이 재미있었다.`, `${pick(subj)}는 너무 어렵다…`, `시험이 다가온다.`, `${a.school.name}에서 제일 좋은 건 쉬는 시간이다.`, a.school.grade > 75 ? '선생님께 칭찬받았다!' : a.school.grade < 40 ? '공부가 손에 안 잡힌다.' : '숙제가 너무 많다.']));
  }
  // 일
  if (a.job) {
    const j = JOBS[a.job.id];
    const jobLines = {
      fisher: ['오늘은 고기가 잘 잡힐까', '그물 손질을 해야겠다', `${pick(W.fish)}가 제철이다`],
      hunter: ['발자국을 잘 읽어야 한다', `큰 ${pick(W.animals)}을(를) 잡고 싶다`, '활시위가 느슨해졌다'],
      gatherer: ['저 숲엔 버섯이 많을 거다', '산딸기 덤불을 찾았다', '독버섯은 조심해야 한다'],
      farmer: ['올해 농사가 잘돼야 할 텐데', '밭에 물을 대야 한다', '잡초가 끝도 없다'],
      builder: ['기둥을 똑바로 세워야 한다', '내가 지은 집에 사람들이 산다니 뿌듯하다', '망치질하다 손가락을 찧었다'],
      teacher: ['아이들이 말을 안 듣는다', '아이들이 자라는 걸 보면 보람차다', '내일 수업은 뭘 가르칠까'],
      merchant: ['물건 값이 또 올랐다', '단골손님이 늘었다', '오늘 장사는 별로였다'],
      doctor: ['아픈 사람이 늘었다', '오늘 한 사람을 살렸다', '피곤하지만 쉴 수가 없다'],
      scholar: ['책을 더 읽어야 한다', '스승의 말이 맞았다', '새로운 생각이 떠올랐다'],
      scientist: ['실험이 또 실패했다', '가설이 맞는 것 같다', '조금만 더 하면 된다'],
      artist: ['영감이 오지 않는다', '그리고 싶은 장면이 생겼다', '내 작품을 누가 알아줄까'],
      programmer: ['버그가 안 잡힌다', '코드가 한 번에 돌았다!', '모니터를 너무 오래 봤다'],
    }[a.job.id];
    add(1.8, () => pick([...(jobLines || []).map(x => x + '.'), `${j?.name || '일'} 일도 이제 손에 익었다.`, a.job.sat < 40 ? `${j?.name || '이 일'}… 계속해야 할까?` : a.job.sat > 70 ? `${j?.name || '내'} 일이 즐겁다.` : '일이 끝나면 뭘 할까.']));
    if (a.job.ceo) add(1.5, () => pick(['직원들 월급을 줄 수 있을까.', '회사를 더 키우고 싶다.', '경쟁 회사가 신경 쓰인다.']));
  } else if (age >= 19 && !a.retired) add(2, () => pick(['일자리를 구해야 하는데.', '나도 쓸모 있는 사람이고 싶다.', '뭘 해서 먹고살까.']));
  // 사랑
  if (a.partner) { const p = nm(a.partner); if (p?.alive) add(2.2, () => pick([`${callName(p.name, a.culture)} 보고 싶다.`, `${wa(p.name)} 함께라서 다행이다.`, a.spouse ? `${p.name}에게 요즘 무심했던 것 같다.` : `${wa(p.name)} 언젠가 가정을 꾸릴 수 있을까.`, `${p.name}이(가) 좋아하는 ${reul(pick(eraFood(c.era)))} 해 줘야지.`])); }
  for (const id in a.rels) {
    const r = a.rels[id], b = nm(+id);
    if (!b || !b.alive) continue;
    if (r.r > 40 && a.partner !== b.id) add(2.5, () => pick([`${b.name}… 나를 어떻게 생각할까?`, `오늘도 ${b.name} 얼굴만 봐도 좋았다.`, `${b.name}에게 말해 볼까. 아니야, 아직은…`, `${b.name} 옆에 있으면 말이 꼬인다.`]));
    else if (r.k === 'best') add(0.8, () => pick([`${b.name} 같은 친구가 있어서 다행이다.`, `${wa(b.name)} ${pick(W.games.concat(W.hobbies))} 하던 때가 그립다.`]));
    else if (r.k === 'rival' || r.k === 'enemy') add(0.9, () => pick([`${b.name}에게만은 지고 싶지 않다.`, `${neun(b.name)} 왜 나를 싫어할까.`, `${b.name} 얼굴만 봐도 속이 끓는다.`]));
  }
  // 가족
  for (const id of a.kids.slice(-3)) { const k = nm(id); if (k?.alive) { const ka = ageOf(k, now); add(1.4, () => ka < 3 ? pick([`${callName(k.name, a.culture)} 밤새 울어서 한숨도 못 잤다.`, `${callName(k.name, a.culture)} 웃는 얼굴에 피로가 싹 가신다.`]) : ka < 19 ? pick([`${callName(k.name, a.culture)} 많이 컸다.`, `${k.name} 학교는 잘 다니고 있을까.`, `${k.name}이(가) 커서 뭐가 될까.`]) : pick([`${k.name}이(가) 잘 살고 있겠지.`, `${k.name} 얼굴 본 지 오래됐다.`])); } }
  for (const id of a.parents) { const p = nm(id); if (p) add(p.alive ? 0.8 : 1.4, () => p.alive ? pick([`${age < 19 ? '엄마 아빠' : '부모님'} 생각이 난다.`, `${p.name}께 효도해야 하는데.`]) : pick([`${p.name}이(가) 보고 싶다.`, `${p.name}이(가) 살아 계셨다면 뭐라고 하셨을까.`])); }
  // 돈
  const wage = ERAS[c.era].wage;
  if (age >= 19 && a.money < wage * 2) add(2.5, () => pick(['돈이 바닥났다.', '이번 달은 어떻게 버티지.', '아껴 써야 한다.']));
  else if (a.money > wage * 150) add(0.8, () => pick(['이제 좀 여유가 생겼다.', '돈이 행복을 다 주지는 않더라.', '이 돈으로 뭘 하면 좋을까.']));
  // 꿈
  const g = a.goals.find(x => !x.done);
  if (g) add(1.4, () => pick([`'${g.text}'… 조금씩 가까워지고 있을까?`, a.amb > 65 ? `반드시 ${g.text.replace(/기$/, '고 말겠다.')}` : `${g.text.replace(/기$/, '면')} 좋겠다.`, `${g.text}. 그게 내 꿈이다.`]));
  // 마을과 세상
  const fresh = s.buildings.map(id => world.buildings.get(id)).filter(b => b?.done && now - (b.built || 0) < 1440 * 15 && b.type !== 'hut');
  if (fresh.length) add(1.2, () => { const b = pick(fresh); return pick([`새로 생긴 ${BUILDINGS[b.type].name}에 가 보고 싶다.`, `${s.name}에 ${BUILDINGS[b.type].name}이(가) 생겼다니!`]); });
  if (now - (c.eraAt || 0) < 1440 * 40 && c.era > 0) add(1.5, () => pick([`${ERAS[c.era].name}… 세상이 정말 빨리 변한다.`, '옛날 방식은 이제 안 통하는 걸까.']));
  if (c.leader && c.leader !== a.id) { const L = nm(c.leader); const r = L && a.rels[L.id]; if (L) add(0.6, () => r && r.a < -20 ? `${ga(L.name)} 정말 나라를 잘 이끌 수 있을까?` : (r && r.a > 30 ? `${L.name} ${ERAS[c.era].leader}라면 믿을 수 있다.` : `${L.name} ${neun(ERAS[c.era].leader)} 요즘 무슨 생각을 할까.`)); }
  if (c.leader === a.id) add(2, () => pick(['모두가 나를 보고 있다.', `${c.name}을(를) 더 좋은 나라로 만들고 싶다.`, '결정 하나하나가 무겁다.']));
  // 기억
  const big = a.mem.filter(m => m.imp >= 6 && now - m.t > 1440 * 3);
  if (big.length) add(1.3, () => { const m = pick(big); return m.v >= 0 ? pick([`가끔 '${m.text}' 그날이 떠오른다.`, `'${m.text}'… 좋은 기억이다.`]) : pick([`아직도 '${m.text}' 그 일이 마음에 걸린다.`, `'${m.text}'… 잊고 싶다.`]); });
  // 몸과 나이
  if (a.sick > 0) add(2.5, () => pick(['몸이 좀 나아져야 할 텐데.', '기침이 멎질 않는다.', '아프니까 서럽다.']));
  if (age > 60) add(1.2, () => pick([`벌써 ${Math.floor(age)}살이라니.`, '젊을 땐 몰랐다, 시간이 이렇게 빠른 줄.', '손주들 얼굴이 보고 싶다.', '무릎이 쑤시는 걸 보니 비가 오려나.']));
  if (!a.home && age >= 16) add(2, () => pick(['비바람 막을 집이 있었으면.', '오늘 밤은 어디서 자지.']));
  // 나이대마다 마음에 걸리는 것 (남녀노소)
  const single = !a.partner && !a.spouse;
  if (age >= 3 && age < 7) add(1.6, () => pick(['나도 형아·언니처럼 학교 가고 싶다.', '엄마 아빠랑 같이 자고 싶다.', '왜 하늘은 파래?', '오늘 간식은 뭘까?', '어린이집 친구가 보고 싶다.'.replace('어린이집', c.era >= 5 ? '어린이집' : '옆집')]));
  if (age >= 13 && age < 19) add(2, () => pick(['어른들은 내 마음을 몰라.', '친구들이 나를 어떻게 볼까.', '하고 싶은 게 뭔지 아직 모르겠다.', '거울 속 내가 낯설다.', '빨리 어른이 되고 싶다.', '공부는 왜 해야 하는 걸까.',
    c.era >= 6 ? '답장이 안 와서 괜히 휴대폰만 들여다본다.' : '편지에 뭐라고 써야 할까.', a.tr.N > 60 ? '다들 나만 빼고 즐거워 보인다.' : '내일은 뭐 하고 놀지?']));
  if (age >= 19 && age < 30) add(1.8, () => pick(['나는 뭘 하며 살아야 할까.', '친구들은 다 자리를 잡아 가는 것 같은데.', '자유롭지만 불안하다.', single ? '연애는 언제쯤 할 수 있을까.' : '이 사람이 정말 내 사람일까.',
    c.era >= 5 ? '월세 내고 나면 남는 게 없다.' : '내 손으로 먹고사는 게 이렇게 어렵구나.', a.army || a.served === true ? '군대에서 배운 것도 있긴 하다.' : '세상은 생각보다 넓다.']));
  if (age >= 30 && age < 40) add(1.6, () => pick(['일도 가정도 다 잘하고 싶은데 몸이 하나다.', '부모님이 부쩍 늙으셨다.', '밤새도 멀쩡하던 몸이 아니다.', single ? '혼자 사는 삶도 나쁘지 않다. 정말로.' : '우리 둘, 잘 살고 있는 걸까.',
    c.era >= 5 ? '내 집 마련은 언제쯤 가능할까.' : '올해는 꼭 집을 넓혀야지.']));
  if (age >= 40 && age < 60) add(1.6, () => pick(['아이들 학비가 걱정이다.', '언제까지 일할 수 있을까.', '건강을 챙겨야 할 나이다.', '나도 한때 꿈이 있었는데.', '부모님 병원 모시고 가는 날이 늘었다.', '거울 속에 아버지·어머니 얼굴이 보인다.']));
  if (age >= 60 && age < 80) add(1.4, () => pick([a.retired ? '은퇴하고 나니 하루가 참 길다.' : '이 나이에도 일할 수 있어 다행이다.', '손주 재롱이 요즘 낙이다.', '젊은 사람들 세상이다.', '건강할 때 여행을 다녀야지.', '하루하루가 선물 같다.']));
  if (age >= 80) add(1.6, () => pick(['먼저 간 친구들이 보고 싶다.', '오늘도 무사히 하루가 갔다.', '자식들에게 짐이 되고 싶지 않다.', '젊을 때 사진을 보면 남 같다.', '살아 보니 별것 아닌 일로 너무 애태웠다.']));
  // 지금 인생의 단계
  if (a.engaged) { const p = nm(a.engaged.to); if (p) add(2.5, () => pick([`${wa(p.name)} 결혼이라니, 아직도 꿈 같다.`, '결혼 준비할 게 산더미다.', '결혼식 날 비가 오면 어쩌지.', '청첩장을 누구한테까지 돌려야 하나.'])); }
  if (a.wedAt != null && now - a.wedAt < 1440 * 365) { const p = nm(a.spouse); if (p?.alive) add(1.8, () => pick([`아직도 신혼 같다.`, `${callName(p.name, a.culture)}랑 사는 게 이런 거구나.`, '치약 짜는 방법 하나로도 싸울 수 있다니.'])); }
  if (a.leave && now >= a.leave.from && now < a.leave.until) add(2.4, () => pick(['아기랑 하루 종일 붙어 있으니 시간이 어떻게 가는지 모르겠다.', '회사 일은 다 잊었다. 지금은 이게 내 일이다.', '잠 한 번 푹 자 보는 게 소원이다.', '복직하면 잘 적응할 수 있을까.']));
  if (a.ask) add(1.5, () => pick(['마음속 목소리는 뭐라고 할까…', '정하기가 너무 어렵다.', '누가 대신 정해 줬으면 좋겠다. …아니, 결국 내 인생이지.']));
  if (a.kidPlan?.want === 'yes' && !a.preg) add(1.2, () => pick(['우리를 닮은 아이… 언제쯤 올까.', '아기 이름을 미리 지어 둘까.']));
  // 철학
  if (a.tr.O > 60) add(0.8, () => pick(['우리는 어디서 왔을까?', '별은 왜 빛날까?', '죽으면 어떻게 될까.', '저 하늘 끝엔 뭐가 있을까.', `${c.era >= 6 ? '우주에도 우리 같은 사람들이 살까.' : '세상 끝까지 가 보고 싶다.'}`]));
  const h = localHour(now, s.lon);
  if (h >= 20 || h < 3) add(0.8, () => pick(['별이 쏟아질 것 같다.', '고요한 밤이다.', '달이 밝다.']));
  if (h >= 5 && h < 8) add(0.8, () => pick(['새벽 공기가 맑다.', '해가 뜬다.']));
  return T;
}

/** 조합된 속마음 한 줄 */
export function compose(world, a) {
  const T = topics(world, a);
  if (!T.length) return null;
  let tot = T.reduce((t, x) => t + x[0], 0), r = Math.random() * tot, gen = T[0][1];
  for (const [w, g] of T) { r -= w; if (r <= 0) { gen = g; break; } }
  let core = gen();
  if (!core) return null;
  const m = mood(a);
  const parts = [];
  if (chance(0.4)) { const o = pick(OPEN[m]); if (o) parts.push(o); }
  parts.push(core);
  const d = dominant(a);
  if (d && chance(0.28)) parts.push(pick(CLOSE[d]));
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}
