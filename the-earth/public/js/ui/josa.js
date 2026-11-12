// 한국어 조사: 앞 글자 받침에 따라 이/가, 을/를, 은/는, 와/과, (으)로
const hasBatchim = ch => { const c = ch.charCodeAt(0); if (c >= 0xac00 && c <= 0xd7a3) return (c - 0xac00) % 28 !== 0; return /[lmnkptbdgc0136780]/i.test(ch); };
const rieul = ch => { const c = ch.charCodeAt(0); return c >= 0xac00 && c <= 0xd7a3 ? (c - 0xac00) % 28 === 8 : /l/i.test(ch); };
export function josa(word, pair) {
  const ch = String(word).trim().slice(-1) || '가';
  const b = hasBatchim(ch);
  switch (pair) {
    case '이/가': return word + (b ? '이' : '가');
    case '을/를': return word + (b ? '을' : '를');
    case '은/는': return word + (b ? '은' : '는');
    case '와/과': return word + (b ? '과' : '와');
    case '으로/로': return word + (b && !rieul(ch) ? '으로' : '로');
    default: return word;
  }
}
