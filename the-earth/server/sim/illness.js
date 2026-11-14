// 병: '아프다' 하나가 아니라 실제 병의 이름과 성질.
//
//   · 잠깐 앓는 병 — 감기·독감·장염·몸살·허리 통증·충치·폐렴. 계절(겨울 감기·여름 장염), 나이, 생활 습관, 시대에 따라
//     잘 걸리고, 감기·독감은 식구에게 옮는다. 심한 정도는 예전처럼 a.sick (0~100) 으로 나타난다.
//   · 오래 가는 병 — 고혈압·당뇨(체중·군것질·술·스트레스와 이어진다), 치매, 우울증, 암. 건강의 목표치를 깎고,
//     고혈압·당뇨는 심근경색·뇌졸중 위험을 키운다.
//   · 의원·병원 — 가서 진찰받으면 병 이름을 알게 되고 빨리 낫는다. 근대 이후엔 약으로 고혈압·당뇨를 다스린다 (약값).
//     항생제가 생긴 근대 이후엔 폐렴이 덜 무섭고, 병원이 있으면 심근경색·뇌졸중에서 살아날 가능성이 크다.
import { clamp, rand, pick, chance, ageOf, dateOf, DAY } from './util.js';
import { remember, think, feel, lifeEvent } from './ann.js';

export const ACUTE = {
  cold: { n: '감기', icon: '🤧', sev: [8, 20], cont: 0.05, sym: ['콧물이 줄줄 흐른다.', '목이 칼칼하다.', '재채기가 멈추질 않는다.', '머리가 띵하다.'] },
  flu: { n: '독감', icon: '🤒', sev: [30, 55], cont: 0.07, sym: ['온몸이 쑤시고 열이 난다.', '오한이 든다… 이불을 덮어도 춥다.', '기침할 때마다 가슴이 울린다.'] },
  gastro: { n: '장염', icon: '🤢', sev: [14, 30], sym: ['배가 살살 아프다.', '화장실을 들락날락했다.', '아무것도 못 먹겠다.'] },
  ache: { n: '몸살', icon: '🤕', sev: [10, 24], sym: ['온몸이 욱신거린다.', '두들겨 맞은 것처럼 아프다.'] },
  back: { n: '허리 통증', icon: '🦴', sev: [10, 28], sym: ['허리가 끊어질 것 같다.', '앉았다 일어날 때마다 허리가 찌릿하다.'] },
  tooth: { n: '충치', icon: '🦷', sev: [8, 20], sym: ['이가 시큰거린다.', '단 걸 먹으면 이가 아프다.'] },
  pneumonia: { n: '폐렴', icon: '🫁', sev: [45, 75], sym: ['숨이 차고 열이 내리지 않는다.', '기침에 피가 섞여 나온다… 무섭다.'] },
  // 사건으로 생기는 것
  fever: { n: '열병', icon: '🤒', sev: [20, 45], sym: ['열이 펄펄 끓는다.', '머리가 불덩이 같다.'] },
  injury: { n: '부상', icon: '🩹', sev: [20, 50], sym: ['다친 데가 욱신거린다.', '움직일 때마다 아프다.'] },
  exhaustion: { n: '과로', icon: '😮‍💨', sev: [15, 30], sym: ['몸이 천근만근이다.', '아무것도 할 기운이 없다.'] },
  epidemic: { n: '돌림병', icon: '🦠', sev: [25, 70], cont: 0.08, sym: ['열이 내리지 않는다… 다들 무사할까.', '마을 사람 절반이 앓아누웠다.'] },
};
export const CHRONIC = {
  htn: { n: '고혈압', icon: '🩺', hit: 4 },
  dm: { n: '당뇨', icon: '🩸', hit: 6 },
  dementia: { n: '치매', icon: '🌫️', hit: 8 },
  depression: { n: '우울증', icon: '🌧️', hit: 3 },
  cancer: { n: '암', icon: '🎗️', hit: 12 },
};

const SEASON = { cold: { 겨울: 2.6, 가을: 1.5, 봄: 1.2, 여름: 0.5 }, flu: { 겨울: 5, 가을: 1.2, 봄: 0.8, 여름: 0.1 }, gastro: { 여름: 2.6, 봄: 1, 가을: 1, 겨울: 0.5 } };
const has = (a, k) => (a.chronic || []).some(c => c.k === k);
const eraOf = (world, a) => world.countries[a.country]?.era ?? 0;
/** 의원·병원이 있고 일하는 사람이 있나 */
function medicalCare(world, a) {
  const s = world.settlements.get(a.sid);
  if (!s) return 0;
  const hospital = world.findCivic(s, 'hospital');
  const clinic = world.findCivic(s, 'clinic');
  return hospital ? 2 : clinic && (s.heal || 0) > 0.05 ? 1 : 0;
}

/** 오늘 무슨 병에 걸리나 (하루에 한 번, 아프지 않을 때). 걸리면 true */
export function catchDaily(world, a, age) {
  if (a.sick > 0) return false;
  const s = world.settlements.get(a.sid);
  const season = s?.weather?.season || '봄';
  const era = eraOf(world, a);
  const f = a.fd || {};
  const weak = 1.6 - a.health / 100;       // 몸이 약할수록 잘 걸린다
  const kid = age < 10 ? 2 : age > 70 ? 1.3 : 1;
  const heavy = a.job && ['builder', 'laborer', 'farmer', 'fisher', 'miner', 'sailor', 'worker', 'steelworker', 'rider', 'driver'].includes(a.job.id);
  const odds = {
    cold: 0.0016 * kid * (SEASON.cold[season] || 1) * weak,                          // 어른 한 해 2~3번, 아이는 더
    flu: 0.00022 * (SEASON.flu[season] || 1) * weak * (era >= 6 ? 0.6 : 1),         // 한 철에 열에 한 명꼴 (정보화 시대엔 예방주사)
    gastro: 0.0007 * (SEASON.gastro[season] || 1) * (era <= 3 ? 1.6 : 1),            // 깨끗한 물이 없던 시대엔 더
    ache: 0.0006 * (1 + Math.max(0, a.stress - 50) / 30) * (heavy ? 1.6 : 1),
    back: age < 30 ? 0.0001 : 0.0004 * (heavy ? 2 : 1) * (age > 50 ? 1.5 : 1) * (1 + Math.max(0, (f.bmi || 22) - 27) * 0.1),
    tooth: 0.00035 * (1 + Math.max(0, (f.junk || 20) - 25) / 25) * (era >= 5 ? 0.6 : 1),
    pneumonia: 0.00004 * (age > 65 ? 6 : age < 3 ? 3 : 1) * weak,
  };
  for (const [k, p] of Object.entries(odds)) {
    if (immune(world, a, k)) continue;
    if (chance(p)) { fallIll(world, a, k); return true; }
  }
  return false;
}
/** 앓고 난 병은 한동안 다시 안 걸린다 (감기 한 달, 독감은 그 철 내내) */
const immune = (world, a, k) => (a.imm?.[k] || 0) > world.clock;
export function recovered(world, a) {
  const k = a.ill?.k;
  if (!k) return;
  a.imm = a.imm || {};
  a.imm[k] = world.clock + (k === 'flu' ? 150 : k === 'cold' ? 30 : 10) * DAY;
}

/** k 병에 걸린다 (사건·전염에서도 부른다) */
export function fallIll(world, a, k, sev = null) {
  const d = ACUTE[k];
  if (!d) return;
  a.sick = Math.max(a.sick || 0, sev ?? (d.sev[0] + rand() * (d.sev[1] - d.sev[0])));
  a.ill = { k, since: Math.round(world.clock), dx: false };
  if (a.sick > 25) remember(world, a, `${d.n}에 걸려 앓아누웠다`, 4, -0.5, [], 'health');
  think(world, a, pick(d.sym));
  feel(a, -6, '아픔', d.n);
}

/** 같이 사는 식구에게 옮는다 (하루에 한 번, 아픈 사람 쪽에서) */
export function spreadDaily(world, a) {
  const d = a.ill && ACUTE[a.ill.k];
  if (!d?.cont || !a.home || a.sick <= 0) return;
  for (const b of world.residents(a.sid)) {
    if (b === a || b.home !== a.home || !b.alive || b.sick > 0 || immune(world, b, a.ill.k)) continue;
    if (chance(d.cont * (1.6 - b.health / 100))) {
      fallIll(world, b, a.ill.k);
      if (chance(0.5)) think(world, b, `${a.name}한테서 ${d.n}이 옮았나 보다.`);
    }
  }
}

/** 오래 가는 병이 생기는지 (하루에 한 번). 해마다 생길 확률을 하루로 나눠 쓴다 */
export function chronicDaily(world, a, age) {
  if (age < 25) return;
  if (!a.chronic) initChronic(world, a, age);
  const f = a.fd || {};
  const bmi = f.bmi || 22.5, junk = f.junk || 25, drink = f.drinkAvg || 0;
  const y = p => chance(p / 365);
  const mid = clamp((age - 30) / 30, 0, 2);
  const add = (k, note) => {
    if (has(a, k)) return;
    a.chronic.push({ k, since: Math.round(world.clock), dx: false, ctl: false });
    if (note) think(world, a, note);
  };
  // 고혈압: 나이 · 체중 · 스트레스 · 술 · 짠 음식(한식·중식은 조금 더)
  if (y(0.012 * Math.pow(mid, 1.3) * (1 + Math.max(0, bmi - 24) * 0.15 + Math.max(0, a.stress - 55) * 0.01 + drink * 0.5 + (['ko', 'zh', 'ja'].includes(a.culture) ? 0.2 : 0)))) add('htn', '요즘 뒷목이 뻐근하고 머리가 무겁다.');
  // 당뇨: 나이 · 체중 · 군것질
  if (y(0.004 * Math.pow(mid, 1.3) * (1 + Math.max(0, bmi - 25) * 0.3 + Math.max(0, junk - 35) * 0.02))) add('dm', '자꾸 목이 마르고 피곤하다.');
  // 치매: 일흔 무렵부터
  if (age >= 65 && y(0.004 * Math.exp((age - 70) / 7) * (a.ab.int > 70 ? 0.8 : 1))) add('dementia', '방금 뭘 하려고 했더라…');
  // 암: 나이와 함께 늘고, 술·군것질이 조금 더한다
  if (y(0.0008 * Math.exp((age - 40) / 14) * (1 + drink * 0.5 + Math.max(0, junk - 30) * 0.005))) add('cancer', '요즘 이상하게 살이 빠지고 기운이 없다.');
  // 우울증: 오래 지치고 외로울 때
  if (!has(a, 'depression') && a.stress > 70 && a.mood < -25 && chance(0.003)) add('depression', '아무것도 하기 싫다. 다 의미 없게 느껴진다.');
  else if (has(a, 'depression') && a.mood > 10 && a.stress < 40 && chance(0.01)) {
    a.chronic = a.chronic.filter(c => c.k !== 'depression');
    remember(world, a, '긴 우울의 터널을 빠져나왔다', 6, 0.8, [], 'health');
    think(world, a, '오랜만에 세상이 다시 밝아 보인다.');
  }
  // 갑자기 쓰러지는 병: 심근경색·뇌졸중 (고혈압·당뇨·체중·술이 키우고, 운동이 줄인다)
  const vascular = 0.0005 * Math.exp((age - 40) / 12) * (1 + (has(a, 'htn') ? (ctl(a, 'htn') ? 0.4 : 1) : 0) + (has(a, 'dm') ? (ctl(a, 'dm') ? 0.3 : 0.8) : 0) + Math.max(0, bmi - 27) * 0.1 + drink * 0.3 - Math.min(0.3, (f.exAvg || 0) * 0.3));
  if (y(vascular)) return attack(world, a, age);
  // 암: 진행된다. 근대 이후 병원에서 치료하면 나을 수도
  const ca = a.chronic.find(c => c.k === 'cancer');
  if (ca) {
    const yrs = (world.clock - ca.since) / (365 * DAY);
    const care = medicalCare(world, a), era = eraOf(world, a);
    if (ca.dx && care && era >= 5 && !ca.treated && chance(0.02)) {
      ca.treated = true;
      lifeEvent(world, a, '암 수술과 항암 치료를 받았다', '🎗️');
      a.sick = Math.max(a.sick || 0, 40);
      if (chance(era >= 6 ? 0.7 : 0.5)) { a.chronic = a.chronic.filter(c => c !== ca); remember(world, a, '암을 이겨 냈다', 9, 0.9, [], 'health'); think(world, a, '다시 얻은 삶이다. 하루하루를 소중히 살자.'); return; }
    }
    if (yrs > 0.5) a.sick = Math.max(a.sick || 0, Math.min(90, 20 + yrs * 25));   // 시간이 갈수록 깊어진다
    if (yrs > 0.4 && !ca.dx) ca.dx = true;
  }
}
/** 처음 볼 때: 이미 살아온 나이만큼 병이 있을 수 있다 (나이별 유병률, 체중이 조금 더한다) */
const PREV = {
  htn: [[30, 0.02], [45, 0.08], [60, 0.25], [75, 0.45], [999, 0.6]],
  dm: [[30, 0.005], [45, 0.03], [60, 0.1], [75, 0.2], [999, 0.25]],
  dementia: [[65, 0], [75, 0.04], [85, 0.12], [999, 0.3]],
  cancer: [[45, 0.002], [60, 0.01], [75, 0.02], [999, 0.03]],
};
function initChronic(world, a, age) {
  a.chronic = [];
  const era = eraOf(world, a);
  const bmi = a.fd?.bmi || 22.5;
  for (const [k, tab] of Object.entries(PREV)) {
    const p = tab.find(([lim]) => age < lim)[1] * (k === 'htn' || k === 'dm' ? 1 + Math.max(0, bmi - 25) * 0.2 : 1) * (era <= 3 && (k === 'htn' || k === 'dm') ? 0.6 : 1);
    if (!chance(p)) continue;
    const dx = era >= 4 ? chance(0.7) : k === 'dementia';
    a.chronic.push({ k, since: Math.round(world.clock - rand() * 3 * 365 * DAY), dx, ctl: dx && era >= 5 && k !== 'dementia' && k !== 'cancer' && chance(0.75) });
  }
}
const ctl = (a, k) => (a.chronic || []).some(c => c.k === k && c.ctl);

/** 심근경색·뇌졸중: 병원이 있으면 살아날 가능성이 크다. 반환: 죽음의 이름 또는 null */
function attack(world, a, age) {
  const stroke = chance(0.45);
  const name = stroke ? '뇌졸중' : '심근경색';
  const care = medicalCare(world, a), era = eraOf(world, a);
  const dieP = (era <= 2 ? 0.7 : era <= 4 ? 0.5 : 0.25) * (care === 2 ? 0.55 : care === 1 ? 0.8 : 1) * (age > 80 ? 1.4 : 1);
  if (chance(dieP)) { a.pendingDeath = name; return name; }
  a.sick = Math.max(a.sick || 0, 60);
  a.ill = { k: stroke ? 'stroke' : 'heart', since: Math.round(world.clock), dx: true, n: name };
  lifeEvent(world, a, `${name}으로 쓰러졌다가 ${care ? '병원에서 ' : ''}겨우 살아났다`, '🚑');
  remember(world, a, `${name}으로 쓰러졌다`, 9, -0.9, [], 'health');
  think(world, a, '살아 있다는 게 기적이다. 이제 몸을 챙겨야겠다.');
  if (a.fd) { a.fd.drink = Math.max(0, a.fd.drink - 25); a.fd.dietUntil = world.clock + 90 * DAY; }
  return null;
}

/** 오래 가는 병이 건강 목표치에서 깎는 몫 (약을 먹으면 덜) */
export function chronicHit(a) {
  let h = 0;
  for (const c of a.chronic || []) h += (CHRONIC[c.k]?.hit || 0) * (c.ctl ? 0.4 : 1);
  return h;
}

/** 의원·병원에서 진찰받았다: 병 이름을 알고, 근대 이후엔 약을 받는다 */
export function seeDoctor(world, a) {
  const era = eraOf(world, a);
  if (a.ill && !a.ill.dx) {
    a.ill.dx = true;
    const d = ACUTE[a.ill.k];
    if (d) think(world, a, `의사 선생님이 ${d.n}이라고 했다. ${era >= 5 ? '약 먹고 푹 쉬래.' : '푹 쉬래.'}`);
  }
  // 항생제(근대 이후): 폐렴이 빨리 낫는다
  if (a.ill?.k === 'pneumonia' && era >= 5) a.sick = Math.max(0, a.sick - 15);
  for (const c of a.chronic || []) {
    if (c.dx) { if (!c.ctl && era >= 5 && chance(0.4 + a.tr.C / 200)) c.ctl = true; continue; }
    if (era < 4 && c.k !== 'cancer' && c.k !== 'dementia') continue;   // 혈압계·혈당 검사가 없던 시대
    if (chance(0.6)) {
      c.dx = true;
      const n = CHRONIC[c.k].n;
      lifeEvent(world, a, `${n} 진단을 받았다`, CHRONIC[c.k].icon);
      remember(world, a, `병원에서 ${n}이라는 말을 들었다`, 6, -0.5, [], 'health');
      think(world, a, c.k === 'htn' || c.k === 'dm' ? `${n}이란다. ${era >= 5 ? '약을 꼬박꼬박 먹고 ' : ''}음식을 조심해야겠다.` : c.k === 'depression' ? '마음도 병이 든다는 걸 처음 알았다.' : `${n}이라니… 믿기지 않는다.`);
      if (era >= 5 && (c.k === 'htn' || c.k === 'dm' || c.k === 'depression')) c.ctl = chance(0.5 + a.tr.C / 200);
      if (a.fd && (c.k === 'htn' || c.k === 'dm')) a.fd.dietUntil = world.clock + 60 * DAY;
    }
  }
}

/** 약값을 낼 병의 수 (근대 이후, 다스리는 중인 병) */
export const medsCount = a => (a.chronic || []).filter(c => c.ctl).length;

/** 지금 앓는 병의 이름 (모르면 '몸이 안 좋음') */
export function illName(a) {
  if (!(a.sick > 0)) return null;
  if (a.ill?.n) return a.ill.n;
  const d = a.ill && ACUTE[a.ill.k];
  return d ? d.n : '몸살';
}
/** 증상 한마디 */
export function symptom(a) {
  const d = a.ill && ACUTE[a.ill.k];
  return d ? pick(d.sym) : pick(['몸이 좀 나아져야 할 텐데.', '아프니까 서럽다.']);
}
/** 화면용 */
export function illOut(world, a) {
  const d = a.ill && ACUTE[a.ill.k];
  return {
    now: a.sick > 0 ? { n: illName(a), icon: d?.icon || (a.ill?.n ? '🚑' : '🤒'), dx: !!a.ill?.dx, days: a.ill ? Math.max(1, Math.round((world.clock - a.ill.since) / DAY)) : null } : null,
    chronic: (a.chronic || []).filter(c => c.dx).map(c => ({ n: CHRONIC[c.k].n, icon: CHRONIC[c.k].icon, ctl: c.ctl, since: c.since })),
  };
}
