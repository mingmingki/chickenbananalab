// 이름 필터: 욕설·혐오 표현·운영자 사칭을 막는다. 플레이어 이름, 앤 이름, 서버 이름, 계정 이름에 쓴다.
// 공백·숫자·특수문자를 빼고 비교해서 '시 1 발' 같은 우회도 잡는다.
const KO = [
  '시발', '씨발', '씨빨', '시빨', '씨팔', '시팔', '씨벌', 'ㅅㅂ', 'ㅆㅂ', 'ㅅ ㅂ', '병신', '븅신', '빙신', 'ㅂㅅ', '좆', '존나', '좃나', '졸라',
  '개새끼', '개새기', '개색', '개쌔', '새끼', '쌔끼', '미친놈', '미친년', '미친새', '지랄', 'ㅈㄹ', '엿먹', '닥쳐', '꺼져', '니미', '느금', '느개비', '니애미', '니애비',
  '섹스', '보지', '자지', '걸레년', '창녀', '창놈', '한남충', '김치녀', '일베', '메갈', '틀딱', '짱깨', '쪽바리', '깜둥이', '흑형', '강간', '성폭행', '몰카',
  '자살', '히틀러', '나치', '장애새', '애자', '등신', '호로', '후레', '썅', '씹', 'fuck',
];
const EN = ['fuck', 'shit', 'bitch', 'cunt', 'nigger', 'nigga', 'faggot', 'retard', 'whore', 'slut', 'pussy', 'rapist', 'hitler', 'nazi', 'porn', 'penis', 'vagina', 'asshole', 'motherf'];
// 짧아서 이름 속에 우연히 들어갈 수 있는 말은 단어 전체가 같을 때만
const EN_WORD = ['dick', 'cock', 'ass', 'fag', 'rape', 'sex', 'cum', 'tit', 'tits'];
const RESERVED = ['관리자', '운영자', '운영진', '어드민', '시스템', 'admin', 'administrator', 'operator', 'moderator', 'system', 'gm', 'theearth', '더어스'];

const squash = s => String(s).normalize('NFKC').toLowerCase()
  .replace(/[0@]/g, 'o').replace(/[1!|]/g, 'i').replace(/3/g, 'e').replace(/\$|5/g, 's').replace(/7/g, 't')
  .replace(/[^a-z가-힣ㄱ-ㅎㅏ-ㅣ]/g, '');
const hangul = s => String(s).normalize('NFC').replace(/[^가-힣ㄱ-ㅎㅏ-ㅣ]/g, '');

/** 문제가 있으면 이유(문자열), 괜찮으면 null */
export function badName(name, { reserved = true } = {}) {
  const raw = String(name || '');
  if (/[\u0000-\u001f\u007f​-‏‪-‮⁦-⁩]/.test(raw)) return '쓸 수 없는 글자가 들어 있습니다';
  if (/https?:|www\.|\.com|\.kr|discord|t\.me/i.test(raw)) return '이름에 주소를 넣을 수 없습니다';
  const s = squash(raw), h = hangul(raw);
  for (const w of KO) if (h.includes(hangul(w)) && hangul(w) || s.includes(squash(w)) && squash(w)) return '부적절한 말이 들어 있습니다';
  for (const w of EN) if (s.includes(w)) return '부적절한 말이 들어 있습니다';
  const words = raw.toLowerCase().split(/[^a-z]+/).filter(Boolean);
  for (const w of EN_WORD) if (words.includes(w)) return '부적절한 말이 들어 있습니다';
  if (reserved) for (const w of RESERVED) if (s === squash(w) || w.length >= 3 && s.startsWith(squash(w))) return '운영자로 오해할 수 있는 이름은 쓸 수 없습니다';
  return null;
}
/** 앤 이름처럼 걸러서 대신 쓸 이름을 돌려주는 경우 */
export const cleanName = (name, fallback) => (badName(name) ? fallback : name);
