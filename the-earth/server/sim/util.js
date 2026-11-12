// 공용 도구: 난수, 시간, 한국어 조사

export const rand = () => Math.random();
export const rint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
export const pick = arr => arr[Math.floor(Math.random() * arr.length)];
export const chance = p => Math.random() < p;
export const clamp = (v, a = 0, b = 100) => (v < a ? a : v > b ? b : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export function gauss() {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
export function weighted(items, wfn) {
  let total = 0;
  const ws = items.map(it => { const w = Math.max(0, wfn(it)); total += w; return w; });
  if (total <= 0) return null;
  let r = Math.random() * total;
  for (let i = 0; i < items.length; i++) { r -= ws[i]; if (r <= 0) return items[i]; }
  return items[items.length - 1];
}
/** 두 정수 id 쌍에 대한 결정적 0..1 값 (순서 무관) — '첫눈에 반하는' 화학 반응 */
export function pairHash(a, b) {
  const lo = Math.min(a, b), hi = Math.max(a, b);
  let h = (lo * 374761393 + hi * 668265263) | 0;
  h = (h ^ (h >>> 13)) * 1274126177 | 0;
  h = h ^ (h >>> 16);
  return ((h >>> 0) % 10000) / 10000;
}

// ── 시간 (게임 분 단위, 0 = 1년 1월 1일 00:00) ──
export const HOUR = 60, DAY = 1440, YEAR = 525600;
const MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
export function dateOf(min) {
  const dayN = Math.floor(min / DAY);
  const year = Math.floor(dayN / 365) + 1;
  let doy = dayN % 365;
  const dayOfYear = doy;
  let m = 0;
  while (doy >= MONTH_DAYS[m]) { doy -= MONTH_DAYS[m]; m++; }
  const mod = ((min % DAY) + DAY) % DAY;
  return { year, month: m + 1, day: doy + 1, hour: Math.floor(mod / 60), minute: Math.floor(mod % 60), doy: dayOfYear };
}
export function fmtDate(min) {
  const d = dateOf(min);
  return `${d.year}년 ${d.month}월 ${d.day}일`;
}
export function fmtDateTime(min) {
  const d = dateOf(min);
  return `${d.year}년 ${d.month}월 ${d.day}일 ${String(d.hour).padStart(2, '0')}:${String(d.minute).padStart(2, '0')}`;
}
export const ageOf = (ann, now) => (now - ann.born) / YEAR;
/** 경도에 따른 현지 시각 (시) */
export const localHour = (now, lon) => ((((now / 60) + lon / 15) % 24) + 24) % 24;

// ── 한국어 조사 ──
function hasBatchim(word) {
  if (!word) return false;
  const ch = word.trim().slice(-1);
  const c = ch.charCodeAt(0);
  if (c >= 0xac00 && c <= 0xd7a3) return (c - 0xac00) % 28 !== 0;
  if (/[0-9]/.test(ch)) return '0136780'.includes(ch);
  return /[lmnkptbdgc]$/i.test(ch) || /ng$/i.test(word);
}
function rieul(word) {
  const c = word.trim().slice(-1).charCodeAt(0);
  if (c >= 0xac00 && c <= 0xd7a3) return (c - 0xac00) % 28 === 8;
  return /l$/i.test(word);
}
export const ga = w => w + (hasBatchim(w) ? '이' : '가');
export const neun = w => w + (hasBatchim(w) ? '은' : '는');
export const reul = w => w + (hasBatchim(w) ? '을' : '를');
export const wa = w => w + (hasBatchim(w) ? '과' : '와');
export const ro = w => w + (hasBatchim(w) && !rieul(w) ? '으로' : '로');
export const ya = w => w + (hasBatchim(w) ? '아' : '야');
export const ida = w => w + (hasBatchim(w) ? '이다' : '다');
export const iya = w => w + (hasBatchim(w) ? '이야' : '야');
/** 이름 뒤에 친근한 '이' (예: 민준이) */
export const callName = (name, culture) => {
  if (culture !== 'ko' || name.length < 2) return name;
  const given = name.length >= 3 ? name.slice(1) : name;
  return hasBatchim(given) ? given + '이' : given;
};

/** '…이(가)', '…을(를)', '…(으)로' 같은 자리표시 조사를 앞 글자 받침에 맞게 고친다 (따옴표·괄호 건너뜀) */
const JOSA_RE = /([^\s'"」』)\]])(['"」』)\]]*)(이\(가\)|을\(를\)|은\(는\)|와\(과\)|\(으\)로|\(이\)다)/g;
export function fixJosa(text) {
  return text.replace(JOSA_RE, (m, ch, q, j) => {
    const bat = hasBatchim(ch);
    let out;
    switch (j) {
      case '이(가)': out = bat ? '이' : '가'; break;
      case '을(를)': out = bat ? '을' : '를'; break;
      case '은(는)': out = bat ? '은' : '는'; break;
      case '와(과)': out = bat ? '과' : '와'; break;
      case '(으)로': out = bat && !rieul(ch) ? '으로' : '로'; break;
      default: out = bat ? '이다' : '다';
    }
    return ch + q + out;
  });
}
