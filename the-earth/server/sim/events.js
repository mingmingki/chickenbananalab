// 인생의 사건들: 나이·성격·가족·직업·지형(산·바다·강·숲)·계절·날씨·시대에 따라 일어난다.
// 하루에 한 앤에게 최대 한 가지. 같은 사건은 한동안 반복되지 않는다.
import { clamp, rand, pick, chance, gauss, ageOf, dateOf, DAY, YEAR, ga, wa, reul, neun, callName } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';
import { rel, updateKind, attraction } from './social.js';
import { ERAS, JOBS, BUILDINGS, DISCOVERIES } from './data.js';
import { siteFeatures, removeBuilding } from './settlement.js';
import { seasonOf } from './thoughts.js';
import { policeResponse, coverage } from './safety.js';
import { fmtMoney } from './dilemma.js';

const EV = [];
/** 사건 정의: id, [최소 나이, 최대 나이), 하루 확률, 조건, 실행, 재발 간격(일) */
const E = (id, ages, p, cond, run, cool = 40) => EV.push({ id, min: ages[0], max: ages[1], p, cond, run, cool });
const ONCE = 1e9;

const FOODS = ['떡', '군고구마', '찐 옥수수', '산딸기', '구운 생선', '감자전', '수박', '꿀', '엿', '곶감'];
const PLACES_N = { river: ['강가', '여울', '강 모래톱'], sea: ['바닷가', '갯바위', '모래사장'], lake: ['호숫가'], mountain: ['뒷산', '산 계곡', '산마루'], forest: ['숲속', '숲 언저리'] };
const FISH = ['붕어', '잉어', '송어', '메기', '고등어', '전어', '도미', '숭어', '은어', '가자미'];
const ANIMALS = ['사슴', '토끼', '멧돼지', '꿩', '노루', '여우'];
const PETS = ['강아지', '고양이', '토끼', '병아리', '거북이', '까치'];
const PET_NAMES = ['복실이', '누렁이', '나비', '바둑이', '초코', '콩이', '별이', '구름이', '두부', '보리'];
const GAMES = ['술래잡기', '숨바꼭질', '공기놀이', '딱지치기', '비석치기', '고무줄놀이', '팽이치기', '연날리기', '물수제비뜨기', '소꿉놀이'];
const HOBBIES = ['노래', '그림', '나무 깎기', '별 보기', '낚시', '요리', '시 쓰기', '춤', '꽃 가꾸기', '피리 불기', '바둑', '뜨개질'];
const FIRST_WORDS = ['엄마', '아빠', '맘마', '물', '까까', '멍멍', '아니야', '줘'];
const SPORTS = ['씨름', '달리기', '활쏘기', '공차기', '줄다리기', '수영'];
const GIFTS = ['따뜻한 옷', '고운 신발', '맛있는 고기', '약재', '목도리', '그릇'];

function ctxOf(world, a, base) {
  const { s, c } = base;
  const res = world.residents(s.id);
  const now = world.clock;
  const age = base.age;
  const others = f => res.filter(b => b.id !== a.id && f(b));
  const near = (ids) => ids.map(id => world.anns.get(id)).filter(b => b && b.alive && b.sid === a.sid);
  const feat = siteFeatures(s);
  const waters = ['river', 'sea', 'lake'].filter(k => feat[k]);
  return {
    w: world, a, s, c, age, era: c.era, feat, waters, season: seasonOf(world, s), wk: s.weather?.k || '맑음',
    wage: ERAS[c.era].wage,
    fmt: u => fmtMoney(u, c.era),
    mem: (t, imp = 3, v = 0, who = []) => remember(world, a, t, imp, v, who, 'event'),
    life: (t, icon = '•') => lifeEvent(world, a, t, icon),
    think: t => think(world, a, t),
    feel: (v, label, why = '') => feel(a, v, label, why),
    sk: (k, d) => { a.sk[k] = clamp(a.sk[k] + d); },
    money: d => { a.money = Math.max(0, a.money + d); },
    health: d => { a.health = clamp(a.health + d); },
    fun: d => { a.nd.fun = clamp(a.nd.fun + d); },
    stress: d => { a.stress = clamp(a.stress + d); },
    bond: (b, aff, fam = 3) => {
      if (!b) return;
      const r = rel(a, b), rb = rel(b, a);
      r.a = clamp(r.a + aff, -100, 100); rb.a = clamp(rb.a + aff * 0.8, -100, 100);
      r.f = clamp(r.f + fam); rb.f = clamp(rb.f + fam); r.n++; rb.n++;
      r.last = rb.last = Math.round(now);
      updateKind(world, a, b); updateKind(world, b, a);
    },
    other: (b, t, imp = 3, v = 0) => b && remember(world, b, t, imp, v, [a.id], 'event'),
    peer: (span = 3) => pick(others(b => Math.abs(ageOf(b, now) - age) <= span)),
    friend: () => { const l = Object.keys(a.rels).filter(id => ['friend', 'best'].includes(a.rels[id].k)).map(Number); const b = l.length && world.anns.get(pick(l)); return b && b.alive && b.sid === a.sid ? b : null; },
    parent: () => pick(near(a.parents)),
    sibling: () => pick(others(b => a.parents.length && b.parents.some(p => a.parents.includes(p)))),
    partner: () => { const b = world.anns.get(a.spouse || a.partner); return b && b.alive ? b : null; },
    kid: () => pick(near(a.kids)),
    grandkid: () => pick(near(a.kids.flatMap(id => world.anns.get(id)?.kids || []))),
    coworker: () => { const co = a.job?.co && world.companies.get(a.job.co); return co ? pick(near(co.emp.filter(id => id !== a.id))) : null; },
    adult: () => pick(others(b => ageOf(b, now) >= 25)),
    J: (name, josa) => ({ ga, wa, reul, neun }[josa](name)),
  };
}

// ═══ 아기 (0~4세) ═══
E('first_smile', [0, 0.6], 0.04, null, c => { c.life('처음으로 방긋 웃었다', '😊'); const p = c.parent(); c.other(p, `${c.a.name}이(가) 처음으로 웃었다`, 6, 0.9); }, ONCE);
E('first_teeth', [0.4, 1.2], 0.02, null, c => { c.life('첫 이가 났다', '🦷'); }, ONCE);
E('first_word', [0.8, 2], 0.025, null, c => { const w = pick(FIRST_WORDS); c.life(`첫 말을 했다: "${w}"`, '🗣️'); const p = c.parent(); c.other(p, `${c.a.name}이(가) 처음으로 "${w}"라고 말했다`, 7, 1); if (p) think(c.w, p, w === '엄마' || w === '아빠' ? '방금… 날 불렀어!' : `"${w}"라니, 귀여워.`); }, ONCE);
E('first_steps', [0.8, 1.7], 0.025, null, c => { c.life('첫걸음마를 뗐다', '👣'); const p = c.parent(); c.other(p, `${c.a.name}이(가) 첫걸음을 뗐다`, 7, 1); }, ONCE);
E('night_cry', [0, 1.5], 0.02, null, c => { const p = c.parent(); if (p) { p.nd.energy = clamp(p.nd.energy - 20); think(c.w, p, `${callName(c.a.name, p.culture)}이(가) 밤새 울어서 한숨도 못 잤다.`); } }, 6);
E('baby_fever', [0, 4], 0.0015, null, c => { c.a.sick = Math.max(c.a.sick, 20 + rand() * 25); c.life('열이 펄펄 끓어 온 가족이 걱정했다', '🤒'); const p = c.parent(); if (p) { think(c.w, p, '제발 아무 일 없어야 할 텐데…'); p.stress = clamp(p.stress + 15); } }, 365);
E('toddler_mess', [1.5, 4.5], 0.012, null, c => { const it = pick(['물동이', '밥그릇', '꿀단지', '먹물', '곡식 자루']); c.mem(`${reul(it)} 엎어서 혼났다`, 2, -0.2); const p = c.parent(); if (p) think(c.w, p, pick(['한시도 눈을 뗄 수가 없다.', '그래도 저 웃는 얼굴을 보면…'])); }, 20);
E('lullaby', [0, 3], 0.012, c => c.parent(), c => { const p = c.parent(); c.bond(p, 3, 3); c.other(p, `${c.a.name}에게 자장가를 불러 주었다`, 2, 0.5); }, 15);

// ═══ 어린이 (4~13세) ═══
E('play_friend', [4, 13], 0.03, c => c.peer(2), c => {
  const b = c.peer(2); const g = pick(GAMES);
  const first = !Object.values(c.a.rels).some(r => r.k === 'friend' || r.k === 'best');
  c.bond(b, 6, 6); c.fun(25);
  c.mem(`${wa(b.name)} ${reul(g)} 하며 놀았다`, 2, 0.5, [b.id]);
  c.other(b, `${wa(c.a.name)} ${reul(g)} 하며 놀았다`, 2, 0.5);
  if (first) c.life(`첫 친구가 생겼다 — ${b.name}`, '🤝');
}, 5);
E('water_play', [5, 15], 0.02, c => c.waters.length && c.season === '여름', c => {
  const k = pick(c.waters); const pl = pick(PLACES_N[k]); const b = c.peer(3);
  if (chance(0.08)) { c.life(`${pl}에서 물놀이하다 물에 빠질 뻔했다 — ${b ? b.name + '이(가) 구해 주었다' : '겨우 헤엄쳐 나왔다'}`, '🌊'); c.feel(-10, '공포', '물'); if (b) { c.bond(b, 25, 8); c.other(b, `물에 빠진 ${reul(c.a.name)} 구했다`, 7, 0.8); } }
  else { c.mem(`${b ? b.name + '와(과) ' : ''}${pl}에서 신나게 물놀이를 했다`, 3, 0.7, b ? [b.id] : []); c.fun(30); if (b) c.bond(b, 4, 4); }
}, 10);
E('kid_fishing', [6, 15], 0.015, c => c.waters.length, c => {
  const f = pick(FISH); const pl = pick(PLACES_N[pick(c.waters)]); c.sk('farming', 1.5); c.s.food += 1;
  if (!c.a.flags?.firstFish) { c.a.flags = { ...(c.a.flags || {}), firstFish: 1 }; c.life(`${pl}에서 처음으로 ${reul(f)} 잡았다`, '🐟'); }
  else c.mem(`${pl}에서 ${reul(f)} 잡았다`, 3, 0.6);
}, 25);
E('lost_mountain', [6, 14], 0.002, c => c.feat.mountain || true, c => { c.life(`${pick(PLACES_N.mountain)}에서 길을 잃었다가 해가 진 뒤에야 겨우 집을 찾았다`, '🌲'); c.feel(-12, '공포', '산'); const p = c.parent(); if (p) { think(c.w, p, '애가 탄다는 게 이런 거구나.'); c.bond(p, 6, 3); } }, ONCE);
E('bugs', [5, 12], 0.015, c => c.season === '여름', c => { const bug = pick(['잠자리', '매미', '반딧불이', '사슴벌레', '나비']); c.mem(`${reul(bug)} 잡으러 뛰어다녔다`, 2, 0.5); c.fun(20); }, 12);
E('snowman', [4, 14], 0.04, c => c.wk === '눈' || c.wk === '폭설', c => { const b = c.peer(3); c.mem(`${b ? b.name + '와(과) ' : ''}눈사람을 만들고 눈싸움을 했다`, 3, 0.7, b ? [b.id] : []); if (b) c.bond(b, 5, 4); c.fun(30); }, 20);
E('scraped_knee', [4, 12], 0.01, null, c => { c.mem('뛰다가 넘어져 무릎이 까졌다', 2, -0.3); c.health(-1); if (c.a.tr.N > 60) c.think('아파… 엉엉.'); else c.think('이 정도는 괜찮아!'); }, 30);
E('kid_fight', [4, 14], 0.01, c => c.peer(2), c => { const b = c.peer(2); const it = pick(['장난감', '간식', '자리', '공', '그네']); c.bond(b, -12, 2); c.mem(`${wa(b.name)} ${it} 때문에 싸웠다`, 3, -0.5, [b.id]); c.other(b, `${wa(c.a.name)} ${it} 때문에 싸웠다`, 3, -0.5); c.think(c.a.tr.A > 55 ? `${b.name}에게 먼저 사과할까…` : `${neun(b.name)} 정말 미워!`); }, 25);
E('make_up', [4, 18], 0.01, c => Object.keys(c.a.rels).some(id => c.a.rels[id].a < -5 && c.w.anns.get(+id)?.sid === c.a.sid), c => {
  const id = +pick(Object.keys(c.a.rels).filter(id => c.a.rels[id].a < -5 && c.w.anns.get(+id)?.sid === c.a.sid)); const b = c.w.anns.get(id);
  if (!b?.alive) return;
  c.bond(b, 18, 4); c.mem(`${wa(b.name)} 화해했다`, 4, 0.6, [b.id]); c.other(b, `${wa(c.a.name)} 화해했다`, 4, 0.6);
}, 20);
E('praised', [4, 13], 0.012, c => c.parent(), c => { const p = c.parent(); const what = c.a.ab.cre > c.a.ab.int ? '그림' : c.a.ab.ath > c.a.ab.int ? '달리기' : '셈'; c.mem(`${p.name}이(가) 내 ${reul(what)} 칭찬해 주셨다`, 3, 0.7, [p.id]); c.bond(p, 4, 2); c.a.conf = clamp(c.a.conf + 4); }, 25);
E('scolded', [4, 16], 0.012, c => c.parent(), c => { const p = c.parent(); const why = pick(['늦게까지 놀다 와서', '동생을 울려서', '거짓말을 해서', '심부름을 잊어서', '옷을 다 버려서']); c.mem(`${why} ${p.name}께 혼났다`, 3, -0.4, [p.id]); c.think(c.a.tr.A < 40 ? '나만 맨날 혼나.' : '다음엔 안 그래야지.'); }, 25);
E('pet_adopt', [5, 70], 0.004, c => !c.a.pet, c => { const kind = pick(PETS); const n = pick(PET_NAMES); c.a.pet = { kind, name: n, since: Math.round(c.w.clock) }; c.life(`${kind} '${n}'을(를) 키우기 시작했다`, '🐾'); c.feel(15, '기쁨', n); }, ONCE);
E('pet_play', [4, 90], 0.02, c => c.a.pet && !c.a.pet.gone, c => { const p = c.a.pet; c.mem(`${p.name}와(과) 한참을 놀았다`, 2, 0.6); c.fun(20); c.stress(-6); }, 8);
E('pet_gone', [4, 95], 0.0015, c => c.a.pet && !c.a.pet.gone && c.w.clock - c.a.pet.since > YEAR * 6, c => { const p = c.a.pet; p.gone = true; c.life(`오랜 친구 ${p.name}을(를) 떠나보냈다`, '🌈'); c.feel(-25, '슬픔', p.name); c.think(`${p.name}, 고마웠어. 거기선 아프지 마.`); }, ONCE);
E('dream_job', [7, 13], 0.01, null, c => {
  const a = c.a;
  const cand = [['과학자', a.ab.int + a.tr.O], ['화가', a.ab.cre * 2], ['운동선수', a.ab.ath * 2], ['선생님', a.tr.A + a.ab.int], ['의사', a.ab.int + a.tr.A], ['사장님', a.amb * 2], ['요리사', a.ab.cre + a.tr.C], ['탐험가', a.tr.O + a.ab.ath], [ERAS[c.era].leader, a.amb + a.ab.cha]].sort((x, y) => y[1] - x[1]);
  const job = pick(cand.slice(0, 2))[0]; a.childDream = job;
  c.life(`"커서 ${job}이(가) 될 거야!" — 처음 꿈을 말했다`, '🌠');
}, ONCE);
E('help_family', [8, 16], 0.015, c => c.parent()?.job, c => { const p = c.parent(); const jn = JOBS[p.job.id]?.name || '일'; c.mem(`${p.name}의 ${jn} 일을 도왔다`, 2, 0.3, [p.id]); c.sk(JOBS[p.job.id]?.skill || 'crafting', 1.5); c.bond(p, 3, 2); }, 15);
E('lost_tooth', [5.5, 8], 0.02, null, c => { c.life('처음으로 이가 빠졌다 — 지붕 위로 던졌다', '🦷'); }, ONCE);
E('talent', [6, 16], 0.006, null, c => {
  const a = c.a; const best = [['ath', '마을 달리기 시합에서 1등을 했다', '🏃'], ['cre', '그린 그림을 보고 어른들이 깜짝 놀랐다', '🎨'], ['int', '어른들도 못 푸는 문제를 풀어 칭찬받았다', '🧮'], ['cha', '노래를 불러 마을 사람들의 박수를 받았다', '🎤']].sort((x, y) => a.ab[y[0]] - a.ab[x[0]])[0];
  if (a.ab[best[0]] < 62) return;
  a.flags = a.flags || {};
  if (!a.flags['talent_' + best[0]]) { a.flags['talent_' + best[0]] = 1; c.life(best[1], best[2]); } else c.mem(best[1], 4, 0.7);
  a.conf = clamp(a.conf + 8); a.rep = clamp(a.rep + 3, -100, 100); c.feel(15, '뿌듯함', '재능');
}, 200);
E('grand_story', [4, 14], 0.01, c => c.a.parents.some(pid => (c.w.anns.get(pid)?.parents || []).some(g => c.w.anns.get(g)?.alive)), c => {
  const g = c.a.parents.flatMap(pid => c.w.anns.get(pid)?.parents || []).map(id => c.w.anns.get(id)).find(x => x?.alive);
  if (!g) return; const m = g.mem.filter(x => x.imp >= 7); const story = m.length ? pick(m).text : '옛날 옛적 이야기';
  c.mem(`${g.gender === 'F' ? '할머니' : '할아버지'} ${g.name}께 옛이야기를 들었다: "${story}"`, 3, 0.6, [g.id]); c.bond(g, 6, 5);
}, 30);
E('stars', [6, 30], 0.006, c => c.a.tr.O > 55, c => { c.mem('밤하늘의 별자리를 하나하나 세어 보았다', 2, 0.5); c.think(pick(['저 별에도 누가 살까?', '별은 왜 반짝일까.'])); c.sk('science', 0.6); }, 40);
E('harvest_feast', [3, 99], 0.03, c => c.season === '가을' && c.s.food > c.s.pop * 6, c => { const f = pick(FOODS); c.mem(`마을 잔치에서 ${reul(f)} 실컷 먹었다`, 3, 0.7); c.fun(25); c.a.nd.food = clamp(c.a.nd.food + 30); }, 300);

// ═══ 학교 ═══
E('exam', [7, 24], 0.02, c => c.a.school, c => {
  const g = c.a.school.grade + gauss() * 12;
  if (g > 88) { c.life(`${c.a.school.name}에서 시험 1등을 했다!`, '💯'); c.feel(18, '뿌듯함', '시험'); const p = c.parent(); c.other(p, `${c.a.name}이(가) 시험에서 1등을 했다`, 5, 0.9); }
  else if (g < 35) { c.mem('시험을 완전히 망쳤다', 4, -0.6); c.feel(-10, '실망', '시험'); c.think(c.a.tr.C > 55 ? '다음엔 정말 열심히 하자.' : '공부 체질이 아닌가 봐.'); c.a.school.grade = clamp(c.a.school.grade + (c.a.tr.C > 55 ? 3 : -1)); }
  else c.mem(`시험을 봤다 (${g > 65 ? '꽤 잘 봤다' : '그럭저럭'})`, 2, g > 65 ? 0.4 : 0);
}, 45);
E('class_leader', [8, 19], 0.004, c => c.a.school && c.a.ab.cha + c.a.tr.E > 115, c => { c.life(`${c.a.school.name} 반장으로 뽑혔다`, '🎖️'); c.sk('leadership', 3); c.a.conf = clamp(c.a.conf + 8); }, 365);
E('school_trip', [7, 19], 0.006, c => c.a.school && (c.season === '봄' || c.season === '가을'), c => { const pl = pick([...c.waters.flatMap(k => PLACES_N[k]), ...PLACES_N.mountain, '이웃 마을']); const b = c.peer(1); c.mem(`학교에서 ${pl}로 소풍을 갔다${b ? ' — ' + b.name + '와(과) 김밥을 나눠 먹었다' : ''}`, 4, 0.7, b ? [b.id] : []); if (b) c.bond(b, 5, 4); c.fun(30); }, 150);
E('teacher_praise', [7, 22], 0.01, c => c.a.school && c.a.school.grade > 60, c => { c.mem('선생님께 칭찬을 받았다', 3, 0.7); c.a.school.grade = clamp(c.a.school.grade + 2); }, 40);
E('forgot_hw', [7, 18], 0.012, c => c.a.school && c.a.tr.C < 45, c => { c.mem('숙제를 안 해 가서 벌을 섰다', 2, -0.3); c.think('아, 숙제…'); }, 30);
E('contest', [8, 23], 0.004, c => c.a.school, c => { const k = c.a.ab.cre > c.a.ab.ath ? (c.a.ab.cre > c.a.ab.int ? '그림' : '과학') : '체육'; const won = chance((c.a.ab[k === '그림' ? 'cre' : k === '과학' ? 'int' : 'ath'] - 30) / 80); if (won) { c.life(`교내 ${k} 대회에서 상을 받았다`, '🏅'); c.feel(20, '환희', '대회'); c.a.rep = clamp(c.a.rep + 3, -100, 100); } else c.mem(`${k} 대회에 나갔지만 상은 못 받았다`, 3, -0.2); }, 180);
E('bullied', [8, 17], 0.004, c => c.a.school && c.a.tr.N > 55 && c.a.tr.E < 45 && c.peer(2), c => {
  const b = c.peer(2); c.mem(`${b.name}에게 괴롭힘을 당했다`, 6, -0.8, [b.id]); c.bond(b, -25, 2); c.stress(20); c.feel(-18, '슬픔', '괴롭힘');
  const f = c.friend(); if (f && f.tr.A > 55) { c.mem(`${ga(f.name)} 내 편을 들어 주었다`, 6, 0.8, [f.id]); c.bond(f, 15, 5); }
}, 120);
E('school_crush', [12, 20], 0.008, c => c.peer(2), c => {
  const cands = c.w.residents(c.s.id).filter(b => b.id !== c.a.id && Math.abs(ageOf(b, c.w.clock) - c.age) < 2.5 && attraction(c.w, c.a, b) > 40);
  const b = pick(cands); if (!b) return;
  const r = rel(c.a, b); r.r = clamp(r.r + 18); r.f = clamp(r.f + 4);
  c.mem(`자꾸만 ${b.name}에게 눈이 간다`, 5, 0.5, [b.id]);
  if (r.r > 30 && !c.a.flags?.firstLove) { c.a.flags = { ...(c.a.flags || {}), firstLove: b.id }; c.life(`첫사랑 — ${b.name}`, '💘'); }
  c.think(pick([`${b.name}… 오늘도 예뻤다.`.replace('예뻤다', b.gender === 'M' ? '멋있었다' : '예뻤다'), `${b.name}이(가) 나를 봤다! 아니, 착각인가.`, `${b.name} 앞에만 가면 말이 안 나온다.`]));
}, 30);
E('love_letter', [13, 22], 0.004, c => Object.values(c.a.rels).some(r => r.r > 35), c => {
  const id = +pick(Object.keys(c.a.rels).filter(id => c.a.rels[id].r > 35)); const b = c.w.anns.get(id); if (!b?.alive) return;
  const ok = (b.rels[c.a.id]?.r || 0) > 25 || chance(0.25);
  c.life(`${b.name}에게 밤새 쓴 편지를 건넸다 — ${ok ? '수줍은 답장이 왔다' : '아무 대답이 없었다'}`, '💌');
  if (ok) { rel(b, c.a).r = clamp((b.rels[c.a.id]?.r || 0) + 20); c.feel(22, '설렘', b.name); } else c.feel(-15, '상심', b.name);
}, 200);
E('study_night', [14, 24], 0.012, c => c.a.school && c.a.tr.C > 50, c => { c.mem('시험 때문에 밤새 공부했다', 2, -0.1); c.a.nd.energy = clamp(c.a.nd.energy - 25); c.a.school.grade = clamp(c.a.school.grade + 1.5); c.sk('science', 0.8); }, 25);
E('career_worry', [15, 21], 0.01, null, c => { c.think(pick(['앞으로 뭘 하며 살아야 할까.', '나는 뭘 잘하는 걸까?', '어른이 되는 게 무섭다.', `${c.a.childDream ? `어릴 땐 ${c.a.childDream}이(가) 되고 싶었는데…` : '꿈이 뭐냐고 물으면 할 말이 없다.'}`])); c.stress(5); }, 40);
E('rebel', [13, 18], 0.008, c => c.a.tr.A < 50 && c.parent(), c => { const p = c.parent(); c.bond(p, -10, 1); c.mem(`${wa(p.name)} 크게 다퉜다. 방문을 쾅 닫았다`, 4, -0.6, [p.id]); c.other(p, `${wa(c.a.name)} 크게 다퉜다`, 4, -0.5); think(c.w, p, '저 아이도 다 크느라 그런 거겠지…'); }, 50);
E('club', [12, 22], 0.006, c => c.a.school && c.era >= 4 && !c.a.club, c => { const cl = pick(['축구', '합창', '연극', '과학', '미술', '밴드', '바둑', '사진']); c.a.club = cl; c.life(`${cl} 동아리에 들어갔다`, '🎸'); const b = c.peer(2); if (b) c.bond(b, 6, 6); }, ONCE);
E('part_time', [16, 22], 0.008, c => c.era >= 2 && !c.a.job, c => { const w = pick(['장터 짐 나르기', '가게 일', '밭일 품앗이', '아이 돌보기', '배달']); const m = c.wage * (0.6 + rand()); c.money(m); c.mem(`용돈을 벌려고 ${w}을(를) 했다`, 2, 0.2); if (!c.a.flags?.firstPay) { c.a.flags = { ...(c.a.flags || {}), firstPay: 1 }; c.life(`생애 처음으로 돈을 벌었다 (${w})`, '🪙'); } }, 30);

// ═══ 어른 ═══
E('first_salary', [16, 60], 0.05, c => c.a.job && c.a.job.co && !c.a.flags?.firstSalary && c.w.clock - c.a.job.since > DAY * 10, c => {
  c.a.flags = { ...(c.a.flags || {}), firstSalary: 1 }; const p = c.parent(); const g = pick(GIFTS);
  c.life(`첫 월급을 받았다${p ? ` — ${p.name}께 ${reul(g)} 사 드렸다` : ''}`, '💰');
  if (p) { c.money(-c.wage); c.bond(p, 10, 3); c.other(p, `${ga(c.a.name)} 첫 월급으로 ${reul(g)} 사 왔다`, 7, 1); think(c.w, p, '다 키웠구나…'); }
}, ONCE);
E('work_mistake', [17, 70], 0.006, c => c.a.job, c => { c.mem('일하다 큰 실수를 했다', 4, -0.6); c.a.job.sat = clamp(c.a.job.sat - 6); c.stress(12); c.think(c.a.tr.N > 60 ? '다들 나를 한심하게 보겠지.' : '실수는 누구나 한다. 다음엔 잘하자.'); }, 60);
E('boss_praise', [17, 70], 0.006, c => c.a.job?.co && !c.a.job.ceo, c => { const co = c.w.companies.get(c.a.job.co); const boss = co?.ceo && c.w.anns.get(co.ceo); c.mem(`${boss ? boss.name + '께 ' : ''}일 잘한다는 칭찬을 들었다`, 3, 0.7, boss ? [boss.id] : []); c.a.job.sat = clamp(c.a.job.sat + 6); if (boss) c.bond(boss, 5, 2); }, 60);
E('coworker_meal', [17, 70], 0.012, c => c.coworker(), c => { const b = c.coworker(); const f = pick(FOODS); c.bond(b, 5, 5); c.mem(`일 끝나고 ${wa(b.name)} ${reul(f)} 먹으며 이야기했다`, 2, 0.5, [b.id]); }, 15);
E('found_money', [8, 90], 0.002, c => c.era >= 2, c => {
  const m = c.wage * (2 + rand() * 6);
  if (c.a.tr.A + c.a.tr.C > 110) { c.life('길에서 돈주머니를 주워 주인을 찾아 돌려주었다', '👛'); c.a.rep = clamp(c.a.rep + 6, -100, 100); c.feel(8, '뿌듯함'); }
  else { c.money(m); c.mem('길에서 돈주머니를 주웠다 — 몰래 챙겼다', 4, 0.3); if (c.a.tr.C > 50) c.think('주인이 찾고 있을 텐데…'); }
}, 400);
E('robbed', [16, 95], 0.0012, c => c.era >= 2 && c.a.money > c.wage * 5 && !chance(coverage(c.w, c.s) * 0.6), c => {
  const loss = c.a.money * (0.1 + rand() * 0.25); c.money(-loss);
  const caught = policeResponse(c.w, c.a, loss);
  c.life(`도둑이 들어 재산 ${c.fmt(loss)}을(를) 잃었다${caught ? ' — ' + caught : ''}`, '🦹'); c.feel(caught && caught.includes('되찾') ? -5 : -15, '분노', '도둑'); c.stress(caught ? 6 : 15);
}, 300);
E('scammed', [20, 90], 0.0015, c => c.era >= 3 && c.a.money > c.wage * 20 && c.a.tr.O + c.a.ab.int < 110, c => { const loss = c.a.money * (0.2 + rand() * 0.4); c.money(-loss); c.life(`그럴듯한 말에 속아 사기를 당했다 (${c.fmt(loss)})`, '🎭'); c.feel(-25, '절망', '사기'); c.stress(25); c.think('사람을 믿은 내가 바보였다.'); }, 1000);
E('windfall', [18, 95], 0.0008, null, c => { const m = c.wage * (30 + rand() * 120); c.money(m); const how = c.era >= 4 ? '복권에 당첨되었다' : c.feat.mountain ? '산에서 귀한 약초 군락을 발견해 큰돈을 벌었다' : '땅을 일구다 옛 보물을 발견했다'; c.life(`${how}! (${c.fmt(m)})`, '🍀'); c.feel(35, '환희', '횡재'); }, ONCE);
E('housewarming', [19, 90], 0.03, c => c.a.home && c.w.buildings.get(c.a.home)?.owner === c.a.id && c.w.clock - (c.w.buildings.get(c.a.home)?.built || 0) < DAY * 10 && c.friend(), c => { const f = c.friend(); c.mem(`새집에 ${reul(f.name)} 초대해 집들이를 했다`, 4, 0.7, [f.id]); c.bond(f, 6, 4); c.other(f, `${c.a.name}의 집들이에 갔다`, 3, 0.5); }, 400);
E('cooking_fail', [12, 90], 0.008, null, c => { const f = pick(FOODS); c.mem(`${reul(f)} 만들다가 다 태웠다`, 2, -0.2); c.think(pick(['요리는 내 길이 아닌가 보다.', '다음엔 불을 줄여야지.'])); }, 40);
E('hobby_start', [10, 80], 0.004, c => !c.a.hobby, c => { const h = pick(HOBBIES); c.a.hobby = h; c.life(`${reul(h)} 취미로 시작했다`, '🎐'); c.fun(20); }, ONCE);
E('hobby_progress', [10, 95], 0.012, c => c.a.hobby, c => { c.mem(`${c.a.hobby} 실력이 조금 늘었다`, 2, 0.5); c.fun(20); c.stress(-8); if (['그림', '노래', '시 쓰기', '피리 불기', '춤'].includes(c.a.hobby)) c.sk('art', 1); }, 12);
E('hike', [12, 75], 0.008, c => c.feat.mountain, c => { const b = c.friend(); c.mem(`${b ? b.name + '와(과) ' : ''}산 정상에 올라 마을을 내려다보았다`, 4, 0.7, b ? [b.id] : []); c.health(2); c.think(pick([`${c.s.name}이(가) 이렇게 작았구나.`, '여기서 보니 걱정이 다 작아 보인다.'])); if (b) c.bond(b, 5, 4); }, 60);
E('sea_fish', [14, 85], 0.01, c => c.feat.sea, c => { const f = pick(FISH); c.mem(`바다낚시로 커다란 ${reul(f)} 낚았다`, 3, 0.7); c.s.food += 2; c.fun(20); }, 30);
E('river_swim', [10, 60], 0.015, c => (c.feat.river || c.feat.lake) && c.season === '여름', c => { c.mem('뜨거운 날, 강물에 뛰어들어 멱을 감았다', 2, 0.6); c.fun(25); c.a.nd.energy = clamp(c.a.nd.energy + 8); }, 20);
E('work_injury', [17, 70], 0.004, c => c.a.job && ['builder', 'hunter', 'farmer', 'fisher', 'worker', 'blacksmith', 'laborer'].includes(c.a.job.id), c => { c.a.sick = Math.max(c.a.sick, 20 + rand() * 30); c.life(`${JOBS[c.a.job.id].name} 일을 하다 다쳤다`, '🩹'); c.feel(-10, '아픔'); }, 200);
E('big_hunt', [16, 60], 0.01, c => c.a.job?.id === 'hunter', c => { const an = pick(ANIMALS); c.s.food += 12; c.a.rep = clamp(c.a.rep + 4, -100, 100); c.life(`큰 ${reul(an)} 잡아 마을에 잔치가 열렸다`, '🍖'); c.feel(20, '뿌듯함', an); }, 120);
E('boar', [10, 75], 0.002, c => c.era <= 3, c => { c.life('숲에서 성난 멧돼지를 만나 죽을 힘을 다해 도망쳤다', '🐗'); c.feel(-15, '공포'); c.stress(15); }, ONCE);
E('anniversary', [20, 95], 0.02, c => c.a.spouse && c.w.anns.get(c.a.spouse)?.alive && c.w.anns.get(c.a.spouse).sid === c.a.sid, c => { const p = c.w.anns.get(c.a.spouse); c.bond(p, 6, 3); const r = rel(c.a, p); r.r = clamp(r.r + 6); c.mem(`${wa(p.name)} 결혼기념일을 보냈다`, 4, 0.8, [p.id]); }, 330);
E('couple_spat', [17, 95], 0.008, c => c.partner()?.sid === c.a.sid, c => {
  const p = c.partner(); const why = pick(['집안일', '돈 문제', '늦은 귀가', '아이 교육', '시댁·처가', '사소한 말 한마디']);
  const hard = c.a.tr.A + p.tr.A < 90;
  c.bond(p, hard ? -12 : -5, 1); c.mem(`${wa(p.name)} ${why} 때문에 ${hard ? '크게' : ''} 다퉜다`, hard ? 5 : 3, -0.6, [p.id]); c.other(p, `${wa(c.a.name)} ${why} 때문에 다퉜다`, 3, -0.5);
  c.think(c.a.tr.A > 55 ? '내가 먼저 미안하다고 해야지.' : '이번엔 내가 옳아.');
}, 30);
E('parent_ill', [18, 75], 0.004, c => c.a.parents.some(id => { const p = c.w.anns.get(id); return p?.alive && ageOf(p, c.w.clock) > 58; }), c => {
  const p = c.a.parents.map(id => c.w.anns.get(id)).find(x => x?.alive && ageOf(x, c.w.clock) > 58); if (!p) return;
  p.sick = Math.max(p.sick, 25 + rand() * 30); c.mem(`${p.name}께서 편찮으시다`, 6, -0.6, [p.id]); c.stress(15); c.think(pick(['조금 더 자주 찾아뵐 걸.', '부모님이 이렇게 작아지셨구나.']));
}, 300);
E('reunion', [16, 99], 0.006, c => Object.keys(c.a.rels).some(id => { const r = c.a.rels[id]; return r.n > 10 && c.w.clock - r.last > DAY * 60 && c.w.anns.get(+id)?.alive; }), c => {
  const id = +pick(Object.keys(c.a.rels).filter(id => { const r = c.a.rels[id]; return r.n > 10 && c.w.clock - r.last > DAY * 60 && c.w.anns.get(+id)?.alive; }));
  const b = c.w.anns.get(id); c.bond(b, 10, 10); c.mem(`오랜만에 ${reul(b.name)} 만나 밤새 이야기했다`, 5, 0.8, [b.id]); c.other(b, `오랜만에 ${reul(c.a.name)} 만났다`, 5, 0.8);
}, 120);
E('help_neighbor', [12, 90], 0.01, c => c.a.tr.A > 55 && c.adult(), c => { const b = c.adult(); const task = pick(['지붕 고치는 일', '이삿짐', '아이 돌보기', '밭일', '물 긷기', '병간호']); c.bond(b, 10, 4); c.a.rep = clamp(c.a.rep + 2, -100, 100); c.mem(`이웃 ${b.name}의 ${reul(task)} 도와주었다`, 3, 0.6, [b.id]); c.other(b, `${ga(c.a.name)} ${reul(task)} 도와주었다`, 4, 0.8); }, 20);
E('neighbor_feud', [18, 90], 0.004, c => c.a.tr.A < 45 && c.adult(), c => { const b = c.adult(); const why = pick(['땅 경계', '시끄러운 소리', '빌려 간 연장', '닭이 밭을 망친 일', '물길']); c.bond(b, -15, 2); c.mem(`이웃 ${wa(b.name)} ${why} 문제로 언성을 높였다`, 4, -0.6, [b.id]); c.other(b, `${wa(c.a.name)} ${why} 문제로 싸웠다`, 4, -0.6); }, 60);
E('insomnia', [14, 99], 0.012, c => c.a.tr.N > 60 || c.a.stress > 55, c => { c.a.nd.energy = clamp(c.a.nd.energy - 20); c.mem('밤새 뒤척이며 잠을 설쳤다', 2, -0.3); c.think(pick(['머릿속이 시끄럽다.', '양을 백 마리나 셌는데…', '내일 걱정에 잠이 안 온다.'])); }, 10);
E('midlife', [40, 52], 0.004, null, c => { c.life('인생의 절반이 지났다는 생각에 한참을 서성였다', '🍂'); c.think(pick(['나는 잘 살아온 걸까.', '젊은 날의 꿈은 어디로 갔을까.', '이제부터라도 하고 싶은 걸 하자.'])); if (chance(0.3) && !c.a.hobby) c.a.hobby = pick(HOBBIES); }, ONCE);
E('belly', [30, 65], 0.004, c => c.a.ab.ath < 55, c => { c.mem('배가 나왔다. 운동을 해야겠다', 2, -0.2); c.think('내일부터… 아니, 오늘부터 운동하자.'); }, 200);
E('volunteer', [14, 85], 0.006, c => c.a.tr.A > 60 && c.era >= 2, c => { const what = pick(['마을 길 청소', '다리 보수', '홀로 사는 어르신 돌보기', '아이들 글 가르치기']); c.a.rep = clamp(c.a.rep + 4, -100, 100); c.mem(`${what}에 나섰다`, 3, 0.6); c.sk('teaching', 0.5); }, 60);
E('charity', [25, 95], 0.004, c => c.a.tr.A > 60 && c.a.money > c.wage * 60, c => { const amt = c.a.money * 0.1; const poor = c.w.residents(c.s.id).filter(b => b.id !== c.a.id && b.money < c.wage * 3 && ageOf(b, c.w.clock) > 18); if (!poor.length) return; c.money(-amt); for (const b of poor) { b.money += amt / poor.length; remember(c.w, b, `${ga(c.a.name)} 어려운 사람들에게 돈을 나눠 주었다`, 4, 0.7, [c.a.id], 'event'); c.bond(b, 8, 2); } c.life(`가난한 이웃 ${poor.length}명에게 재산을 나눴다`, '🤲'); c.a.rep = clamp(c.a.rep + 8, -100, 100); }, 365);
E('eureka', [16, 85], 0.003, c => c.a.tr.O + c.a.ab.int > 120, c => { c.mem('길을 걷다 기막힌 생각이 떠올랐다', 4, 0.7); c.w.addTech(c.c, ERAS[c.era].wage * 2, 'idea'); c.sk('science', 2); c.think('이걸 왜 지금까지 아무도 몰랐지?'); }, 90);
E('exhibit', [16, 90], 0.004, c => c.a.sk.art > 45 && c.era >= 2, c => { c.life('내 작품이 마을 사람들 앞에 걸렸다', '🖼️'); c.a.fame = clamp(c.a.fame + 4, 0, 100); c.feel(15, '뿌듯함'); }, 300);
E('sports_match', [12, 50], 0.006, c => c.a.ab.ath > 55, c => { const sp = pick(SPORTS); const win = chance((c.a.ab.ath + c.a.sk.athletics) / 220); c.mem(`마을 ${sp} 시합에 나가 ${win ? '우승했다!' : '아깝게 졌다'}`, win ? 5 : 3, win ? 0.8 : -0.2); if (win) { c.a.fame = clamp(c.a.fame + 3, 0, 100); c.sk('athletics', 2); } }, 120);
E('drinks', [19, 85], 0.01, c => c.era >= 1 && c.friend(), c => { const b = c.friend(); c.bond(b, 5, 4); c.mem(`${wa(b.name)} 술잔을 기울이며 속마음을 털어놓았다`, 3, 0.6, [b.id]); c.stress(-10); }, 20);
E('gamble', [18, 80], 0.004, c => c.era >= 2 && c.a.tr.C < 40 && c.a.money > c.wage * 3, c => { const win = chance(0.4); const d = c.a.money * (0.1 + rand() * 0.25); c.money(win ? d : -d); c.mem(win ? '노름판에서 한몫 챙겼다' : '노름판에서 돈을 날렸다', 3, win ? 0.4 : -0.6); }, 40);
E('new_tech', [8, 95], 0.01, c => c.c.discoveries.length && !(c.a.techSeen || []).includes(c.c.discoveries[c.c.discoveries.length - 1]), c => {
  const d = c.c.discoveries[c.c.discoveries.length - 1]; c.a.techSeen = [...(c.a.techSeen || []), d].slice(-10);
  c.mem(`새로 나온 '${d}' 이야기로 온 마을이 떠들썩하다`, 3, 0.4); if (chance(0.5)) c.think(c.a.tr.O > 55 ? `'${d}'… 직접 보고 싶다!` : `'${d}'라니, 세상이 너무 빨리 변한다.`);
}, 10);
E('tax_grumble', [19, 90], 0.004, c => c.era >= 2 && c.c.tax > 0.12 && c.a.tr.A < 50, c => { c.mem('세금이 너무 많다고 투덜거렸다', 2, -0.3); const L = c.w.anns.get(c.c.leader); if (L) { const r = rel(c.a, L); r.a = clamp(r.a - 3, -100, 100); } }, 60);
E('meet_leader', [8, 90], 0.0015, c => c.c.leader && c.c.leader !== c.a.id && c.w.anns.get(c.c.leader)?.sid === c.a.sid, c => { const L = c.w.anns.get(c.c.leader); c.bond(L, 6, 5); c.life(`${ERAS[c.era].leader} ${wa(L.name)} 직접 이야기를 나눴다`, '🤝'); }, 400);
E('job_offer', [22, 60], 0.002, c => c.a.job && c.a.sk[JOBS[c.a.job.id]?.skill] > 60, c => { c.mem('다른 곳에서 함께 일하자는 제안을 받았다', 4, 0.6); c.a.job.sat = clamp(c.a.job.sat + (c.a.amb > 60 ? -8 : 4)); c.think(c.a.amb > 60 ? '더 큰 곳으로 갈 때가 됐나.' : '그래도 여기가 좋다.'); }, 200);
E('burnout', [22, 65], 0.003, c => c.a.job && c.a.stress > 60, c => { c.a.sick = Math.max(c.a.sick, 15); c.mem('일에 지쳐 결국 몸져누웠다', 5, -0.7); c.think('쉬어야겠다. 정말로.'); }, 180);
E('wedding_guest', [8, 95], 0.03, c => c.w.events.slice(-30).some(e => e.kind === 'love' && e.text.includes('결혼') && e.anns.some(id => c.a.rels[id]?.f > 15)), c => {
  const e = c.w.events.slice(-30).reverse().find(e => e.kind === 'love' && e.text.includes('결혼') && e.anns.some(id => c.a.rels[id]?.f > 15)); const b = c.w.anns.get(e.anns.find(id => c.a.rels[id]?.f > 15));
  if (!b) return; c.mem(`${b.name}의 결혼식에 가서 축하해 주었다`, 3, 0.7, [b.id]); c.bond(b, 4, 2); if (!c.a.spouse && c.age > 24) c.think('나는 언제쯤…');
}, 60);
E('travel', [16, 85], 0.003, c => c.era >= 3 && c.a.money > c.wage * 15, c => { const others = [...c.w.settlements.values()].filter(x => x.id !== c.s.id && x.country === c.c.code); const dest = others.length ? pick(others).name : pick([...c.waters.flatMap(k => PLACES_N[k]), '먼 산']); c.money(-c.wage * 4); c.life(`${dest}(으)로 여행을 다녀왔다`, '🧳'); c.fun(40); c.stress(-20); }, 200);

// ═══ 노년 ═══
E('grandkid', [45, 110], 0.02, c => c.grandkid(), c => { const g = c.grandkid(); c.bond(g, 8, 5); c.mem(`손주 ${wa(g.name)} 놀아 주었다`, 3, 0.8, [g.id]); c.other(g, `${c.a.gender === 'F' ? '할머니' : '할아버지'}와(과) 놀았다`, 3, 0.7); c.think(pick(['손주는 정말 눈에 넣어도 안 아프다.', `${callName(g.name, c.a.culture)} 웃음소리에 젊어지는 것 같다.`])); }, 10);
E('first_grandkid', [38, 110], 0.5, c => c.grandkid() && !c.a.flags?.grand, c => { c.a.flags = { ...(c.a.flags || {}), grand: 1 }; const g = c.grandkid(); c.life(`할${c.a.gender === 'F' ? '머니' : '아버지'}가 되었다 — 첫 손주 ${g.name}`, '👵'); c.feel(30, '감격', g.name); }, ONCE);
E('memoir', [60, 110], 0.008, c => c.a.mem.some(m => m.imp >= 8), c => { const m = pick(c.a.mem.filter(m => m.imp >= 8)); const y = c.peer(70) || c.adult(); c.mem(`젊은이들에게 옛날 일을 이야기해 주었다: "${m.text}"`, 3, 0.5); if (y) c.bond(y, 3, 3); }, 30);
E('knees', [58, 110], 0.012, null, c => { c.health(-1.5); c.mem(pick(['무릎이 예전 같지 않다', '허리가 쑤신다', '눈이 침침해졌다', '귀가 잘 안 들린다']), 2, -0.3); }, 60);
E('garden', [55, 110], 0.015, c => c.a.home, c => { const p = pick(['고추', '상추', '호박', '감자', '꽃', '깻잎']); c.mem(`텃밭에 ${reul(p)} 심었다`, 2, 0.5); c.s.food += 0.5; c.stress(-5); }, 25);
E('advice', [55, 110], 0.008, c => c.peer(40), c => { const y = c.w.residents(c.s.id).filter(b => ageOf(b, c.w.clock) < c.age - 25 && ageOf(b, c.w.clock) > 15); const b = pick(y); if (!b) return; c.bond(b, 5, 3); c.a.rep = clamp(c.a.rep + 1, -100, 100); remember(c.w, b, `${c.a.name} 어르신께 좋은 조언을 들었다`, 3, 0.6, [c.a.id], 'event'); }, 30);
E('will', [72, 120], 0.01, null, c => { c.life('남은 이들을 위해 유언을 적어 두었다', '📜'); c.think(pick(['후회 없는 삶이었다.', '조금 더 용기 낼 걸 그랬다.', '사랑한다고 더 자주 말할걸.'])); }, ONCE);
E('retire_party', [60, 80], 0.2, c => c.a.retired && !c.a.flags?.retParty, c => { c.a.flags = { ...(c.a.flags || {}), retParty: 1 }; c.life('동료들이 은퇴 잔치를 열어 주었다', '🎉'); c.feel(15, '뭉클함'); }, ONCE);

// ═══ 생애 단계별 사건 (아기 → 팔순) · 몸의 변화 · 가족과 이웃 · 명절 ═══
const kidAge = (c, k) => ageOf(k, c.w.clock);
const newborn = c => c.a.kids.map(id => c.w.anns.get(id)).find(k => k?.alive && ageOf(k, c.w.clock) < 0.25);
const recentDead = c => { for (const id in c.a.rels) { const r = c.a.rels[id]; if (!['friend', 'best', 'sibling', 'acquaintance'].includes(r.k)) continue; const b = c.w.anns.get(+id); if (b && !b.alive && c.w.clock - b.died < DAY * 3 && b.country === c.a.country) return b; } return null; };
const holiday = c => { const d = dateOf(c.w.clock); return d.month === 1 && d.day <= 2 ? '설날' : d.month === 9 && d.day >= 14 && d.day <= 16 ? '추석' : null; };

// 아기
E('roll_over', [0.25, 0.7], 0.04, null, c => { c.life('처음으로 뒤집기에 성공했다', '🐢'); const p = c.parent(); c.other(p, `${ga(c.a.name)} 뒤집기를 했다!`, 6, 0.9); }, ONCE);
E('baby_food', [0.45, 1], 0.03, null, c => { const f = c.era >= 4 ? pick(['쌀미음', '단호박죽', '감자 퓌레', '소고기죽']) : pick(['곡식죽', '으깬 열매', '묽은 미음']); c.life(`처음으로 ${reul(f)} 먹었다`, '🥣'); const p = c.parent(); if (p) think(c.w, p, pick(['반은 먹고 반은 뱉었다. 그래도 귀엽다.', '오물오물 잘도 먹는다!'])); }, ONCE);
E('first_bday', [1, 1.15], 0.4, c => c.era >= 1, c => {
  const items = c.era >= 6 ? ['실', '연필', '돈', '마이크', '청진기', '마우스', '판사봉'] : c.era >= 4 ? ['실', '연필', '돈', '쌀', '청진기'] : ['실', '붓', '엽전', '쌀', '활'];
  const it = pick(items); c.a.dol = it;
  const mean = { 실: '오래 살 거래', 연필: '공부를 잘할 거래', 붓: '학자가 될 거래', 돈: '부자가 될 거래', 엽전: '부자가 될 거래', 쌀: '먹을 걱정 없이 살 거래', 마이크: '노래를 잘할 거래', 청진기: '의사가 될 거래', 마우스: '컴퓨터를 잘할 거래', 판사봉: '판사가 될 거래', 활: '용감한 사람이 될 거래' }[it];
  c.life(`${c.era >= 4 ? '돌잔치' : '돌상'}에서 ${reul(it)} 잡았다 — ${mean}`, '🎂');
  for (const p of c.a.parents.map(id => c.w.anns.get(id)).filter(p => p?.alive)) { remember(c.w, p, `${c.a.name}의 첫 생일 — 돌잡이로 ${reul(it)} 잡았다`, 7, 0.9, [c.a.id], 'family'); feel(p, 15, '행복', c.a.name); }
}, ONCE);
E('daycare_first', [1, 4], 0.02, c => c.era >= 5 && c.a.parents.some(id => c.w.anns.get(id)?.job), c => { c.life('어린이집에 처음 간 날 — 엄마 아빠 손을 놓지 않으려고 울었다', '🧸'); const p = c.parent(); if (p) think(c.w, p, '나도 같이 울 뻔했다. 잘 지내겠지?'); }, ONCE);
E('baby_grandparent', [0, 4], 0.015, c => c.a.parents.some(pid => (c.w.anns.get(pid)?.parents || []).some(g => c.w.anns.get(g)?.alive && c.w.anns.get(g)?.sid === c.a.sid)), c => {
  const g = c.a.parents.flatMap(pid => c.w.anns.get(pid)?.parents || []).map(id => c.w.anns.get(id)).find(x => x?.alive && x.sid === c.a.sid); if (!g) return;
  c.bond(g, 6, 6); remember(c.w, g, `손주 ${reul(c.a.name)} 안아 주었다`, 3, 0.8, [c.a.id], 'family'); think(c.w, g, pick(['이 녀석, 제 아비 어릴 적이랑 똑같네.', '손주는 그냥 보고만 있어도 좋다.']));
}, 15);

// 유아·어린이
E('tantrum', [2.5, 6], 0.012, c => c.era >= 3 && c.parent(), c => { const p = c.parent(); const where = c.era >= 5 ? '마트' : '장터'; c.mem(`${where}에서 장난감 사 달라고 드러누웠다`, 2, -0.2, [p.id]); think(c.w, p, pick(['쥐구멍에라도 숨고 싶었다.', '오늘은 절대 안 사 준다. …아마도.'])); }, 40);
E('sibling_jealous', [2, 8], 0.03, c => c.a.parents.length && c.w.residents(c.a.sid).some(b => b.id !== c.a.id && b.parents.some(p => c.a.parents.includes(p)) && ageOf(b, c.w.clock) < 0.6), c => {
  c.mem('엄마 아빠가 동생만 예뻐하는 것 같다', 3, -0.3); c.think(pick(['나도 아기 할래!', '동생은 왜 맨날 울기만 해?']));
  const p = c.parent(); if (p && p.tr.A > 50) { c.bond(p, 5, 3); c.mem(`${p.name}이(가) 나를 꼭 안아 주며 "넌 첫째라 더 소중해"라고 했다`, 4, 0.7, [p.id]); }
}, ONCE);
E('first_errand', [5, 8.5], 0.02, c => c.era >= 2 && c.parent(), c => { const it = c.era >= 5 ? pick(['두부 한 모', '우유', '콩나물']) : pick(['소금', '두부', '막걸리 한 되']); const ok = chance(0.6 + c.a.tr.C / 300); c.life(ok ? `처음으로 혼자 심부름을 다녀왔다 — ${it}` : `첫 심부름 — ${reul(it)} 사러 갔다가 거스름돈을 잃어버렸다`, '🛒'); c.a.conf = clamp(c.a.conf + (ok ? 5 : 1)); }, ONCE);
E('picnic', [5, 13], 0.01, c => c.a.school && c.era >= 4 && ['봄', '가을'].includes(c.season), c => { const b = c.peer(1); const f = c.era >= 5 ? '김밥' : '주먹밥'; c.mem(`소풍날 ${f} 도시락을 먹고 보물찾기를 했다${b ? ` — ${wa(b.name)} 같이` : ''}`, 4, 0.8, b ? [b.id] : []); c.fun(30); if (b) c.bond(b, 5, 5); if (chance(0.25)) c.life('소풍 보물찾기에서 보물 쪽지를 찾았다!', '🗺️'); }, 150);
E('school_show', [6, 13], 0.006, c => c.a.school && c.era >= 4, c => { const role = c.a.tr.E > 60 ? pick(['주인공', '사회자']) : c.a.ab.cre > 60 ? '무대 그림 담당' : pick(['나무 3', '합창단', '지나가는 사람 2']); c.life(`학예회에서 ${role}을(를) 맡았다`, '🎭'); const p = c.parent(); if (p) remember(c.w, p, `${c.a.name}의 학예회를 보러 갔다 — ${role}`, 5, 0.9, [c.a.id], 'family'); }, 365);
E('dictation', [7, 9.5], 0.012, c => c.a.school, c => { const g = c.a.school.grade + gauss() * 15; if (g > 70) { c.mem('받아쓰기 100점을 받았다!', 3, 0.8); c.feel(10, '뿌듯함'); const p = c.parent(); if (p) c.bond(p, 3, 2); } else if (g < 35) { c.mem('받아쓰기에서 빵점을 받았다… 몰래 숨겼다', 3, -0.5); } else c.mem(`받아쓰기 ${Math.round(clamp(g, 40, 95) / 10) * 10}점`, 1, 0.1); }, 20);
E('bday_party', [5, 13], 0.006, c => c.era >= 4 && c.friend(), c => { const fs = Object.keys(c.a.rels).filter(id => ['friend', 'best'].includes(c.a.rels[id].k)).slice(0, 5).map(id => c.w.anns.get(+id)).filter(b => b?.alive); c.mem(`생일잔치에 친구 ${fs.length}명이 왔다`, 4, 0.9, fs.map(b => b.id)); for (const b of fs) c.bond(b, 4, 4); c.fun(35); }, 300);
E('santa', [4, 9], 0.6, c => c.era >= 4 && dateOf(c.w.clock).month === 12 && dateOf(c.w.clock).day === 25, c => { const g = c.era >= 6 ? pick(['로봇 장난감', '인형', '보드게임', '자전거']) : pick(['양말 속 사탕', '인형', '팽이', '크레파스']); c.mem(`일어나 보니 머리맡에 ${ga(g)} 놓여 있었다`, 4, 0.9); c.think(pick(['산타 할아버지는 진짜 있어!', '밤새 안 자고 기다렸는데…'])); }, 300);
E('lunch_box', [7, 18], 0.008, c => c.a.school && c.parent(), c => { const p = c.parent(); c.mem(`${p.name}이(가) 싸 준 도시락에 쪽지가 들어 있었다`, 3, 0.7, [p.id]); c.bond(p, 4, 2); }, 60);

// 10대 — 몸과 마음의 변화
E('puberty', [11, 15], 0.01, null, c => {
  if (c.a.gender === 'F') { c.life('첫 월경을 했다 — 놀랐지만 가족이 축하해 주었다', '🌸'); const p = c.parent(); if (p) { c.bond(p, 6, 3); remember(c.w, p, `${ga(c.a.name)} 어른이 되어 간다`, 5, 0.6, [c.a.id], 'family'); } }
  else { c.life('목소리가 갈라지기 시작했다 — 변성기', '🗣️'); c.think('내 목소리가 왜 이래…'); }
}, ONCE);
E('growth_spurt', [11, 16], 0.008, null, c => { const cm = 6 + Math.round(rand() * 8); c.mem(`일 년 사이에 키가 ${cm}cm나 컸다`, 3, 0.5); c.a.nd.food = clamp(c.a.nd.food - 10); c.think('옷이 다 작아졌다.'); }, ONCE);
E('acne', [13, 19], 0.008, null, c => { c.mem('얼굴에 여드름이 났다. 거울 보기가 싫다', 2, -0.3); c.a.conf = clamp(c.a.conf - 2); }, 120);
E('fan', [11, 19], 0.006, c => c.era >= 4, c => { const who = c.era >= 6 ? pick(['아이돌 그룹', '프로게이머', '웹툰 작가', '축구 선수']) : c.era >= 5 ? pick(['가수', '농구 선수', '만화가']) : pick(['라디오 드라마 성우', '영화배우']); c.mem(`좋아하는 ${who} 이야기만 나오면 눈이 반짝인다`, 2, 0.5); c.fun(20); const b = c.peer(1); if (b) c.bond(b, 4, 4); }, 200);
E('class_trip', [13, 19], 0.006, c => c.a.school && c.era >= 4, c => { const where = c.era >= 6 ? pick(['제주도', '경주', '일본 오사카']) : pick(['경주', '설악산', '부여']); const b = c.friend(); c.life(`${where}로 수학여행을 갔다${b ? ` — ${b.name}와(과) 밤새 이야기했다` : ''}`, '🚌'); if (b) c.bond(b, 8, 8); c.fun(40); }, 1000);
E('teen_drift', [12, 19], 0.006, c => c.friend(), c => { const b = c.friend(); c.bond(b, -10, 1); c.mem(`${wa(b.name)} 사소한 일로 사이가 틀어졌다`, 4, -0.5, [b.id]); c.think(pick([`${neun(b.name)} 요즘 다른 애들이랑만 다닌다.`, '친구 관계는 왜 이렇게 어려울까.'])); }, 120);
E('secret_crush', [13, 19], 0.008, c => c.peer(1), c => { const b = c.peer(1); const r = rel(c.a, b); r.r = clamp(r.r + 8); c.mem(`${b.name}만 보면 얼굴이 빨개진다. 아무한테도 말 못 했다`, 4, 0.5, [b.id]); c.think(pick([`${b.name}… 나를 알기는 할까?`, '일기장에만 몰래 적어 둬야지.'])); }, 200);
E('first_phone', [10, 15], 0.01, c => c.era >= 6, c => { c.life('처음으로 내 휴대폰이 생겼다', '📱'); c.fun(30); const p = c.parent(); if (p) think(c.w, p, '하루에 한 시간만 하기로 약속했는데… 지켜질까.'); }, ONCE);
E('dye_hair', [15, 20], 0.004, c => c.era >= 5, c => { const col = pick(['노란색', '빨간색', '초록색', '은색']); c.mem(`머리를 ${col}으로 물들였다`, 3, 0.5); const p = c.parent(); if (p) { c.bond(p, -4, 1); think(c.w, p, '저게 무슨 머리야…'); } }, 400);
E('diary', [10, 18], 0.006, c => c.a.tr.O > 50 || c.a.tr.N > 60, c => { c.mem('일기장에 아무에게도 못 한 이야기를 적었다', 2, 0.3); c.stress(-6); }, 40);

// 청년
E('license', [19, 35], 0.006, c => c.era >= 5 && !c.a.flags?.license, c => { c.a.flags = { ...(c.a.flags || {}), license: 1 }; if (chance(0.7)) c.life('운전면허 시험에 붙었다', '🚘'); else { c.a.flags.license = 0; c.mem('운전면허 도로 주행에서 떨어졌다', 3, -0.4); } }, 60);
E('interview_fail', [19, 40], 0.01, c => !c.a.job && !c.a.retired && c.era >= 4, c => { c.mem('면접에서 또 떨어졌다', 4, -0.6); c.a.conf = clamp(c.a.conf - 3); c.think(pick(['나는 어디가 부족한 걸까.', '다음엔 붙을 거야. 반드시.'])); const p = c.parent(); if (p) c.other(p, `${ga(c.a.name)} 기운이 없어 보여 밥을 해 먹였다`, 3, 0.4); }, 25);
E('trip_friends', [19, 40], 0.005, c => c.friend() && c.era >= 3, c => { const b = c.friend(); const where = c.era >= 6 ? pick(['강릉 바다', '부산', '제주도', '도쿄', '방콕']) : c.era >= 4 ? pick(['바닷가', '계곡', '온천']) : pick(['계곡', '이웃 마을']); c.mem(`${wa(b.name)} ${where}로 여행을 다녀왔다`, 5, 0.8, [b.id]); c.bond(b, 8, 8); c.fun(45); c.stress(-15); c.money(-c.wage * 3); }, 200);
E('gym', [18, 60], 0.006, c => c.era >= 5, c => { if (c.a.tr.C > 55) { c.mem('헬스장에 다니기 시작했다. 벌써 석 달째다', 3, 0.6); c.health(2); c.sk('athletics', 2); } else { c.mem('헬스장 1년 회원권을 끊고 사흘 나갔다', 2, -0.2); c.money(-c.wage * 2); } }, 300);
E('night_snack', [16, 70], 0.01, c => c.era >= 5, c => { const f = pick(['치킨', '라면', '떡볶이', '족발', '피자']); const m = c.partner() || c.friend(); c.mem(`밤 11시에 ${m ? wa(m.name) + ' ' : ''}${reul(f)} 시켜 먹었다`, 2, 0.5, m ? [m.id] : []); if (m) c.bond(m, 3, 3); c.fun(15); }, 20);
E('blank_morning', [24, 60], 0.006, c => c.a.job && c.a.stress > 60, c => { c.mem('아침에 눈을 떴는데 아무것도 하기 싫었다. 번아웃이다', 5, -0.6); c.a.job.sat = clamp(c.a.job.sat - 8); c.think(pick(['나는 뭘 위해 이렇게 달리고 있지?', '잠깐 멈춰도 괜찮을까.'])); }, 120);
E('class_reunion', [25, 80], 0.004, c => c.peer(3), c => { const b = c.peer(3); const y = Math.round(c.age - 18); c.mem(`${y}년 만의 동창회에서 ${reul(b.name)} 다시 만났다`, 4, 0.6, [b.id]); c.bond(b, 8, 6); c.think(pick(['다들 변했는데 웃는 건 그대로다.', '그 시절로 돌아간 것 같았다.'])); }, 365);

// 임신·출산·육아 (몸의 일은 그 몸을 가진 사람에게, 돌봄은 누구에게나)
E('morning_sick', [17, 47], 0.08, c => c.a.preg && c.a.preg.due - c.w.clock > DAY * 190, c => { c.mem('입덧이 심해 아무것도 못 먹었다', 4, -0.4); c.a.nd.food = clamp(c.a.nd.food - 15); const p = c.partner(); if (p) { c.bond(p, p.tr.A > 50 ? 6 : -3, 3); think(c.w, p, p.tr.A > 50 ? '먹고 싶은 게 있으면 새벽이라도 사 올게.' : '나도 피곤한데…'); } }, 20);
E('baby_kick', [17, 47], 0.08, c => c.a.preg && c.a.preg.due - c.w.clock < DAY * 150, c => { c.mem('배 속의 아기가 처음으로 발로 찼다', 7, 0.9); const p = c.partner(); if (p) { remember(c.w, p, '배에 손을 대니 아기가 발로 찼다', 7, 0.9, [c.a.id], 'family'); c.bond(p, 6, 3); } }, ONCE);
E('prenatal', [17, 47], 0.05, c => c.a.preg && c.era >= 5, c => { const p = c.partner(); c.mem(`${p ? wa(p.name) + ' ' : ''}산부인과에서 아기 초음파 사진을 받았다`, 6, 0.9, p ? [p.id] : []); if (p) remember(c.w, p, '초음파 속 아기의 심장 소리를 들었다', 6, 0.9, [c.a.id], 'family'); }, 60);
E('nesting', [17, 60], 0.05, c => { const p = c.partner(); return (c.a.preg || p?.preg) && (c.a.preg || p.preg).due - c.w.clock < DAY * 40; }, c => { const it = c.era >= 5 ? pick(['배냇저고리', '아기 침대', '젖병', '카시트']) : pick(['포대기', '배냇저고리', '요람']); c.mem(`아기 맞을 준비 — ${reul(it)} 장만했다`, 4, 0.7); c.money(-c.wage * 2); }, 30);
E('postpartum', [17, 47], 0.5, c => c.a.lastBirth && c.w.clock - c.a.lastBirth < DAY * 5, c => { const where = c.era >= 6 ? '산후조리원' : c.era >= 4 ? '친정에서 산후조리' : '집에서 몸조리'; c.mem(`${where} — 몸이 천근만근이다`, 5, 0.2); c.health(-2); const m = c.a.parents.map(id => c.w.anns.get(id)).find(p => p?.alive); if (m && c.era < 6) { c.bond(m, 8, 5); remember(c.w, m, `${c.a.name}의 산후조리를 도왔다`, 5, 0.6, [c.a.id], 'family'); } if (c.era >= 6) c.money(-c.wage * 15); }, 300);
E('baby_blues', [17, 47], 0.03, c => c.a.lastBirth && c.w.clock - c.a.lastBirth < DAY * 60 && c.a.tr.N > 50, c => {
  c.mem('이유 없이 눈물이 난다. 아기는 예쁜데 나는 자꾸 작아진다', 6, -0.6); c.stress(12);
  const p = c.partner(); if (p && p.tr.A > 45) { c.bond(p, 8, 4); c.stress(-10); remember(c.w, p, `${ga(c.a.name)} 많이 힘들어 보여 밤중 수유를 대신했다`, 5, 0.4, [c.a.id], 'family'); c.mem(`${ga(p.name)} 밤새 아기를 봐 주었다. 조금 숨이 쉬어진다`, 5, 0.6, [p.id]); }
  else c.think('누가 내 얘기 좀 들어줬으면…');
}, 90);
E('first_diaper', [17, 70], 0.2, c => newborn(c), c => { const k = newborn(c); c.mem(`${k.name}의 기저귀를 처음 갈았다 — ${c.a.tr.C > 55 ? '생각보다 잘했다' : '온 방이 난리가 났다'}`, 4, 0.6, [k.id]); c.bond(k, 4, 4); }, 300);
E('night_feed', [17, 70], 0.06, c => newborn(c), c => { const k = newborn(c); const sp = c.partner(); c.a.nd.energy = clamp(c.a.nd.energy - 25); c.mem(`새벽 3시, ${k.name}에게 ${c.era >= 4 ? '분유를 타' : '젖을 물려'} 먹였다`, 2, 0.2, [k.id]); if (sp && chance(0.5)) c.bond(sp, sp.tr.A > 50 ? 3 : -3, 1); }, 4);
E('kid_school_start', [25, 70], 0.3, c => c.a.kids.some(id => { const k = c.w.anns.get(id); return k?.alive && k.school?.key === 'elem' && c.w.clock - k.school.since < DAY * 3; }), c => { const k = c.a.kids.map(id => c.w.anns.get(id)).find(k => k?.alive && k.school?.key === 'elem' && c.w.clock - k.school.since < DAY * 3); c.mem(`${k.name}의 초등학교 입학식 — 가방이 몸보다 컸다`, 6, 0.9, [k.id]); c.think(pick(['언제 이렇게 컸지.', '잘 적응해야 할 텐데.'])); }, 300);
E('parent_teacher', [28, 65], 0.004, c => c.a.kids.some(id => c.w.anns.get(id)?.school), c => { const k = c.a.kids.map(id => c.w.anns.get(id)).find(k => k?.school); const g = k.school.grade; c.mem(`${k.name}의 담임 선생님과 상담을 했다 — ${g > 70 ? '칭찬만 듣고 왔다' : g < 40 ? '걱정스러운 이야기를 들었다' : '친구들과 잘 지낸다고 한다'}`, 4, g > 70 ? 0.7 : g < 40 ? -0.4 : 0.3, [k.id]); }, 180);
E('teen_talk', [33, 65], 0.008, c => c.a.kids.some(id => { const k = c.w.anns.get(id); return k?.alive && k.sid === c.a.sid && ageOf(k, c.w.clock) >= 13 && ageOf(k, c.w.clock) < 19; }), c => {
  const k = c.a.kids.map(id => c.w.anns.get(id)).find(k => k?.alive && k.sid === c.a.sid && ageOf(k, c.w.clock) >= 13 && ageOf(k, c.w.clock) < 19);
  if (chance(0.3 + c.a.tr.A / 250 + c.a.tr.O / 400)) { c.bond(k, 8, 4, 1); c.mem(`사춘기 ${k.name}이(가) 오랜만에 속마음을 털어놓았다`, 5, 0.8, [k.id]); }
  else { c.bond(k, -6, 2); c.mem(`${k.name}에게 한마디 했다가 방문이 쾅 닫혔다`, 3, -0.4, [k.id]); c.think('나도 저 나이 땐 저랬나…'); }
}, 40);
E('kid_exam_parent', [38, 65], 0.01, c => c.era >= 5 && c.a.kids.some(id => { const k = c.w.anns.get(id); return k?.alive && ageOf(k, c.w.clock) >= 17.5 && ageOf(k, c.w.clock) < 19 && k.school; }), c => { const k = c.a.kids.map(id => c.w.anns.get(id)).find(k => k?.alive && ageOf(k, c.w.clock) >= 17.5 && k.school); c.mem(`수험생 ${k.name}을(를) 위해 새벽마다 도시락을 쌌다`, 4, 0.5, [k.id]); c.bond(k, 4, 3); c.stress(5); }, 60);

// 중년의 몸
E('menopause', [46, 56], 0.006, c => c.a.gender === 'F', c => {
  c.life('갱년기가 찾아왔다 — 얼굴이 화끈거리고 잠을 설친다', '🌡️'); c.stress(10); c.feel(-8, '울적함');
  const p = c.partner(); if (p) { if (p.tr.A > 50) { c.bond(p, 8, 3); c.mem(`${ga(p.name)} 말없이 창문을 열어 주고 손을 잡아 주었다`, 5, 0.7, [p.id]); } else c.bond(p, -4, 1); }
  const k = c.kid(); if (k && ageOf(k, c.w.clock) > 15) remember(c.w, k, '엄마가 요즘 많이 힘들어 보인다', 3, -0.2, [c.a.id], 'family');
}, ONCE);
E('andropause', [50, 62], 0.004, c => c.a.gender === 'M', c => { c.mem('요즘 드라마만 봐도 눈물이 난다. 기운도 예전 같지 않다', 4, -0.3); c.stress(6); c.think(pick(['나도 나이를 먹는구나.', '괜히 서글프다.'])); const p = c.partner(); if (p) c.bond(p, 3, 2); }, ONCE);
E('presbyopia', [42, 56], 0.008, null, c => { c.mem(`글씨가 흐릿해서 ${c.era >= 6 ? '휴대폰' : '신문'}을(를) 멀리 들고 봤다 — 노안이다`, 3, -0.3); c.think('돋보기를 써야 하나…'); }, ONCE);
E('gray_hair', [36, 52], 0.01, null, c => { c.mem('거울 속에서 흰머리를 발견했다', 2, -0.2); c.think(pick(['하나 뽑으면 두 개 난다던데.', '세월이 머리 위에 앉았다.'])); }, ONCE);
E('health_warn', [45, 80], 0.004, c => c.era >= 4, c => { const w = pick(['혈압이 높다', '혈당이 높다', '콜레스테롤 수치가 높다']); c.mem(`의사가 ${w}고 했다. 짠 음식을 줄여야 한다`, 4, -0.4); if (c.a.tr.C > 55) { c.health(3); c.mem('매일 저녁 걷기 시작했다', 3, 0.5); } else c.health(-2); }, 365);
E('empty_nest', [44, 75], 0.02, c => c.a.kids.length && c.a.kids.every(id => { const k = c.w.anns.get(id); return !k || !k.alive || (ageOf(k, c.w.clock) >= 19 && k.home !== c.a.home); }) && !c.a.flags?.emptyNest, c => { c.a.flags = { ...(c.a.flags || {}), emptyNest: 1 }; c.life('마지막 아이까지 독립했다 — 집이 텅 빈 것 같다', '🪺'); c.feel(-10, '허전함'); const p = c.partner(); if (p) { c.bond(p, 6, 4); c.mem(`${wa(p.name)} 오랜만에 단둘이 저녁을 먹었다`, 4, 0.6, [p.id]); } }, ONCE);
E('loan_done', [40, 70], 0.004, c => c.era >= 5 && c.a.home && c.w.buildings.get(c.a.home)?.owner === c.a.id, c => { c.life('집 대출을 다 갚았다', '🏡'); c.feel(18, '홀가분함'); }, ONCE);

// 노년 — 잔치와 몸
E('hwangap', [60, 60.5], 0.4, null, c => { const how = c.era >= 6 ? '가족끼리 조촐하게 식사를 했다' : c.era >= 4 ? '자식들이 잔칫상을 차려 주었다' : '온 마을이 모여 환갑잔치를 열었다'; c.life(`환갑 — ${how}`, '🎂'); for (const k of c.a.kids.map(id => c.w.anns.get(id)).filter(k => k?.alive)) { c.bond(k, 6, 3); remember(c.w, k, `${c.a.name}의 환갑을 축하해 드렸다`, 5, 0.8, [c.a.id], 'family'); } c.feel(15, '뭉클함'); }, ONCE);
E('chilsun', [70, 70.5], 0.4, null, c => { c.life('칠순 — 자식과 손주들이 큰절을 올렸다', '🎂'); c.feel(15, '뭉클함'); for (const g of c.a.kids.flatMap(id => c.w.anns.get(id)?.kids || []).map(id => c.w.anns.get(id)).filter(g => g?.alive)) c.bond(g, 4, 3); }, ONCE);
E('palsun', [80, 80.5], 0.4, null, c => { c.life('팔순 — 여든 해를 살았다', '🎂'); c.think(pick(['여든 해가 꿈만 같다.', '아직도 하고 싶은 게 있다.'])); }, ONCE);
E('learn_phone', [62, 95], 0.006, c => c.era >= 6, c => { const g = c.grandkid(); c.mem(`${g ? '손주 ' + g.name + '에게 ' : ''}휴대폰으로 영상 통화하는 법을 배웠다`, 3, 0.6, g ? [g.id] : []); if (g) c.bond(g, 6, 4); c.sk('science', 1); }, 200);
E('morning_walk', [60, 110], 0.01, null, c => { const b = c.peer(6); c.mem(`새벽 ${c.feat.river ? '강변' : c.feat.mountain ? '뒷산' : '공원'}을 ${b ? b.name + '와(과) ' : ''}걸었다`, 1, 0.4, b ? [b.id] : []); c.health(0.6); if (b) c.bond(b, 2, 3); }, 6);
E('funeral', [15, 120], 0.6, c => recentDead(c), c => { const b = recentDead(c); c.mem(`${b.name}의 장례식에 다녀왔다`, 6, -0.7, [b.id]); c.feel(-12, '슬픔', b.name); c.think(pick([`${b.name}… 편히 쉬어.`, '있을 때 더 잘할걸.', '사람은 결국 떠나는구나.'])); }, 3);
E('great_grandkid', [65, 120], 0.3, c => c.a.kids.some(k => (c.w.anns.get(k)?.kids || []).some(g => (c.w.anns.get(g)?.kids || []).length)) && !c.a.flags?.ggk, c => { c.a.flags = { ...(c.a.flags || {}), ggk: 1 }; c.life('증손주가 태어났다 — 4대가 함께다', '👶'); c.feel(25, '감격'); }, ONCE);
E('cane', [78, 120], 0.006, null, c => { c.life('지팡이를 짚기 시작했다', '🦯'); c.think('다리가 말을 안 듣는다.'); }, ONCE);
E('name_mixup', [80, 120], 0.008, c => c.grandkid(), c => { const g = c.grandkid(); c.mem(`손주 ${g.name}을(를) 자꾸 다른 이름으로 불렀다`, 2, -0.2, [g.id]); c.other(g, `${c.a.gender === 'F' ? '할머니' : '할아버지'}가 나를 엄마 이름으로 불렀다`, 2, 0.1); }, 120);
E('old_album', [70, 120], 0.008, c => c.a.mem.some(m => m.imp >= 9), c => { const m = pick(c.a.mem.filter(m => m.imp >= 9)); c.mem(`빛바랜 사진을 보다가 그날이 떠올랐다: "${m.text}"`, 3, 0.4); }, 90);

// 형제·이웃·직장·명절
E('sibling_call', [22, 95], 0.008, c => c.sibling(), c => { const b = c.sibling(); c.bond(b, 4, 4); c.mem(`오랜만에 ${wa(b.name)} ${c.era >= 5 ? '통화하며' : '만나'} 옛날이야기를 했다`, 2, 0.5, [b.id]); }, 45);
E('sibling_rivalry', [5, 18], 0.01, c => c.sibling(), c => { const b = c.sibling(); if (chance(0.5)) { c.bond(b, -6, 3); c.mem(`${wa(b.name)} ${pick(['리모컨', '마지막 과자', '방', '자전거'])} 때문에 싸웠다`, 2, -0.3, [b.id]); } else { c.bond(b, 5, 4); c.mem(`${b.name}이(가) 내 편을 들어 주었다`, 3, 0.6, [b.id]); } }, 20);
E('neighbor_share', [20, 110], 0.008, c => c.a.home, c => { const b = c.adult(); if (!b) return; const f = c.era >= 5 ? pick(['김치', '전', '과일', '떡']) : pick(FOODS); c.bond(b, 5, 4); c.mem(`이웃 ${b.name}에게 ${reul(f)} 나눠 주었다`, 2, 0.5, [b.id]); c.other(b, `이웃 ${ga(c.a.name)} ${reul(f)} 갖다주었다`, 2, 0.5); }, 40);
E('coworker_cover', [18, 70], 0.008, c => c.coworker(), c => { const b = c.coworker(); c.bond(b, 7, 4); c.mem(`일이 밀렸는데 ${ga(b.name)} 말없이 도와주었다`, 3, 0.6, [b.id]); }, 40);
E('holiday', [0, 120], 0.9, c => c.era >= 1 && holiday(c) && (c.parent() || c.kid()), c => {
  const h = holiday(c);
  const fam = [...c.a.parents, ...c.a.kids, ...(c.a.spouse ? [c.a.spouse] : [])].map(id => c.w.anns.get(id)).filter(b => b?.alive && b.sid === c.a.sid);
  for (const b of fam) c.bond(b, 3, 3);
  if (c.age < 19 && c.era >= 3 && h === '설날') { const m = c.wage * (1 + rand() * 3); c.money(m); c.mem(`설날에 세배를 하고 세뱃돈 ${c.fmt(m)}을(를) 받았다`, 3, 0.8); }
  else if (h === '설날') c.mem(`설날 — ${c.era >= 4 ? '떡국을 먹고' : '차례를 지내고'} 한 살 더 먹었다`, 3, 0.6);
  else c.mem(`추석 — 온 가족이 모여 송편을 빚고 보름달을 봤다`, 3, 0.7);
  if (c.age > 40 && c.era >= 4 && chance(0.15)) c.mem('명절 음식 준비로 허리가 끊어질 것 같았다 — 다음엔 다 같이 나눠서 하자고 했다', 3, -0.2);
}, 300);

/** 하루 한 번: 이 앤에게 일어날 수 있는 일 중 하나 */
export function runEvents(world, a, base) {
  if (a.travel) return;
  const age = base.age;
  a.evLast = a.evLast || {};
  const today = Math.floor(world.clock / DAY);
  let ctx = null;
  const n = EV.length, start = Math.floor(rand() * n);
  for (let k = 0; k < n; k++) {
    const e = EV[(start + k) % n];
    if (age < e.min || age >= e.max) continue;
    const last = a.evLast[e.id];
    if (last != null && (e.cool >= ONCE || today - last < e.cool)) continue;
    if (!chance(e.p)) continue;
    ctx = ctx || ctxOf(world, a, base);
    try {
      if (e.cond && !e.cond(ctx)) continue;
      e.run(ctx);
    } catch (err) { continue; }
    a.evLast[e.id] = today;
    world.dirtyDetail.add(a.id);
    break;
  }
}

// ═══ 날씨와 마을 전체의 일 ═══
const WEATHER = {
  봄: [['맑음', 5], ['구름', 3], ['비', 2], ['바람', 1.2], ['안개', 0.8]],
  여름: [['맑음', 4], ['구름', 2], ['소나기', 2], ['비', 1.5], ['폭우', 0.7], ['무더위', 2]],
  가을: [['맑음', 6], ['구름', 2], ['비', 1], ['바람', 1.5], ['안개', 1]],
  겨울: [['맑음', 3], ['구름', 2.5], ['눈', 2.5], ['한파', 1], ['바람', 1]],
};
export function rollWeather(world, s) {
  const season = seasonOf(world, s);
  const f = siteFeatures(s);
  const opts = WEATHER[season].map(([k, w]) => [k, w]);
  if (f.biome === 'desert') { for (const o of opts) if (['비', '소나기', '폭우', '눈', '안개'].includes(o[0])) o[1] *= 0.15; opts.push(['무더위', 3]); }
  if (f.biome === 'tropical') { for (const o of opts) if (o[0] === '눈' || o[0] === '한파') o[1] = 0; opts.push(['소나기', 3]); }
  if (f.biome === 'snow' && season !== '여름') opts.push(['눈', 3], ['한파', 2]);
  if (f.sea && season === '여름') opts.push(['태풍', 0.25]);
  if (season === '겨울' && f.biome !== 'tropical') opts.push(['폭설', 0.3]);
  let tot = opts.reduce((t, o) => t + o[1], 0), r = rand() * tot, k = '맑음';
  for (const [kk, w] of opts) { r -= w; if (r <= 0) { k = kk; break; } }
  const wet = ['비', '소나기', '폭우', '눈', '폭설', '태풍'].includes(k);
  s.dry = wet ? 0 : (s.dry || 0) + 1;
  s.rainy = k === '폭우' || k === '비' ? (s.rainy || 0) + 1 : 0;
  const base = { 봄: 14, 여름: 27, 가을: 15, 겨울: -2 }[season] + (f.biome === 'desert' ? 9 : f.biome === 'tropical' ? 8 : f.biome === 'snow' ? -12 : 0);
  const temp = Math.round(base + (k === '무더위' ? 7 : k === '한파' ? -9 : 0) + gauss() * 3);
  const w = { k, season, temp };
  settlementEvents(world, s, w, f);
  return w;
}

function everyone(world, s, text, imp, v, kind = 'town') {
  for (const a of world.residents(s.id)) remember(world, a, text, imp, v, [], kind);
}
function settlementEvents(world, s, w, f) {
  const res = world.residents(s.id);
  if (!res.length) return;
  const c = world.countries[s.country];
  const d = dateOf(world.clock);
  // 새해
  if (d.month === 1 && d.day === 1) {
    for (const a of res) { remember(world, a, `${d.year}년 새해를 맞았다`, 3, 0.4, [], 'town'); if (chance(0.4)) think(world, a, pick(['올해는 꼭 좋은 일이 있기를.', '새해 다짐: 더 부지런해지기.', '또 한 살 먹었구나.'])); }
  }
  // 홍수: 강가 마을에 폭우가 이어지면
  if (f.river && w.k === '폭우' && s.rainy >= 2 && chance(0.35)) {
    s.food *= 0.7;
    const huts = s.buildings.map(id => world.buildings.get(id)).filter(b => b && (b.type === 'hut' || !b.done));
    let lost = 0;
    for (const b of huts) { if (b.done && chance(0.15)) { removeBuilding(world, b); lost++; } else if (!b.done) { b.progress *= 0.6; world.dirtyBuildings.add(b.id); } }
    everyone(world, s, `강이 넘쳐 ${s.name}이(가) 물에 잠겼다${lost ? ` — 집 ${lost}채가 떠내려갔다` : ''}`, 7, -0.8);
    for (const a of res) { a.stress = clamp(a.stress + 20); if (chance(0.3)) think(world, a, pick(['물이 이렇게 무서운 줄 몰랐다.', '다 같이 다시 일으켜 세우자.', '강 가까이 사는 게 아니었어.'])); }
    world.news('disaster', `🌊 홍수: ${s.name}이(가) 물에 잠겼다${lost ? ` (집 ${lost}채 유실)` : ''}`, { sid: s.id, country: s.country }, 2);
    world.first(c, 'flood', `${s.name}에 첫 홍수`, '🌊');
  }
  // 태풍
  if (w.k === '태풍' && chance(0.6)) {
    let lost = 0;
    for (const id of [...s.buildings]) { const b = world.buildings.get(id); if (b && b.type === 'hut' && chance(0.2)) { removeBuilding(world, b); lost++; } }
    s.food *= 0.85;
    everyone(world, s, `태풍이 ${s.name}을(를) 휩쓸고 지나갔다`, 6, -0.7);
    world.news('disaster', `🌀 태풍이 ${s.name}을(를) 덮쳤다${lost ? ` (움집 ${lost}채 무너짐)` : ''}`, { sid: s.id, country: s.country }, 2);
  }
  // 가뭄
  if (s.dry === 25 && w.season === '여름') {
    s.droughtUntil = world.clock + DAY * 12;
    everyone(world, s, '비가 오지 않아 땅이 쩍쩍 갈라졌다', 5, -0.6);
    world.news('disaster', `☀️ 가뭄: ${s.name}에 한 달 가까이 비가 오지 않는다`, { sid: s.id, country: s.country }, 1);
  }
  if (s.droughtUntil && world.clock < s.droughtUntil) s.food = Math.max(0, s.food - res.length * 0.4);
  // 풍년
  if (w.season === '가을' && d.day === 15 && d.month === (s.lat < 0 ? 4 : 10)) {
    const farms = s.buildings.filter(id => world.buildings.get(id)?.type === 'farm').length;
    if (farms && !(s.droughtUntil && world.clock - s.droughtUntil < DAY * 60)) {
      s.food += farms * 40;
      everyone(world, s, `풍년이다! ${s.name}에 추수 잔치가 열렸다`, 5, 0.8);
      for (let i = 0; i < Math.min(12, res.length); i++) { const a = pick(res), b = pick(res); if (a !== b) { const r = rel(a, b), rb = rel(b, a); r.f = clamp(r.f + 3); rb.f = clamp(rb.f + 3); r.a = clamp(r.a + 3, -100, 100); rb.a = clamp(rb.a + 3, -100, 100); } }
      world.news('town', `🌾 ${s.name} 추수 잔치 — 풍년`, { sid: s.id, country: s.country }, 1);
    }
  }
  // 지진 (산이 많은 곳, 드물게)
  if (f.mountain && chance(0.00025)) {
    let lost = 0;
    for (const id of [...s.buildings]) { const b = world.buildings.get(id); if (b && b.done && BUILDINGS[b.type].era <= 1 && chance(0.12)) { removeBuilding(world, b); lost++; } }
    for (const a of res) if (chance(0.06)) a.sick = Math.max(a.sick, 30 + rand() * 30);
    everyone(world, s, `땅이 흔들렸다 — ${s.name}에 지진이 났다`, 8, -0.8);
    world.news('disaster', `🌋 지진: ${s.name}${lost ? ` (건물 ${lost}채 무너짐)` : ''}`, { sid: s.id, country: s.country }, 3);
  }
  // 돌림병
  if (res.length >= 10 && chance(0.0006 * ERAS[c.era].mort * (world.findCivic(s, 'clinic') ? 0.4 : 1))) {
    const name = pick(['열병', '고뿔', '역병', '마마', '독감']);
    for (const a of res) if (chance(0.3)) a.sick = Math.max(a.sick, 25 + rand() * 45);
    everyone(world, s, `${s.name}에 ${name}이(가) 돌았다`, 7, -0.7);
    world.news('disaster', `🦠 ${s.name}에 ${name}이(가) 퍼지고 있다`, { sid: s.id, country: s.country }, 2);
  }
  // 산불
  if (s.dry > 15 && w.season !== '겨울' && chance(0.01)) {
    everyone(world, s, '마을 뒤 숲에 산불이 나 모두가 물을 길어 날랐다', 6, -0.5);
    for (const a of res) if (ageOf(a, world.clock) > 14) { a.nd.energy = clamp(a.nd.energy - 30); a.rep = clamp(a.rep + 1, -100, 100); }
    world.news('disaster', `🔥 ${s.name} 근처 숲에 산불`, { sid: s.id, country: s.country }, 1);
  }
  // 경이로운 하늘
  if (chance(0.0015)) {
    const sky = pick(['밤하늘에 별똥별이 비처럼 쏟아졌다', '하늘에 쌍무지개가 떴다', '낮인데 해가 검게 가려졌다 — 일식이다', '밤하늘에 초록빛 오로라가 일렁였다', '꼬리가 긴 혜성이 나타났다']);
    everyone(world, s, sky, 6, 0.6);
    for (const a of res) if (chance(0.3)) think(world, a, pick(['세상은 정말 신비롭다.', '무슨 징조일까?', '평생 잊지 못할 거야.']));
  }
}
