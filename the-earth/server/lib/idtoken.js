// 휴대폰 앱의 구글·애플 로그인: 앱이 받은 ID 토큰(JWT)을 서버가 직접 검증한다.
// 웹 로그인과 달리 토큰이 앱(클라이언트)을 거쳐 오므로, 발급자의 공개키(JWKS)로 서명까지 확인한다.
import { createPublicKey, createVerify, createHash } from 'node:crypto';

export const PROVIDERS = {
  google: {
    jwks: process.env.GOOGLE_JWKS_URL || 'https://www.googleapis.com/oauth2/v3/certs',
    issuers: ['https://accounts.google.com', 'accounts.google.com'],
    // 웹 클라이언트 ID + 안드로이드·iOS 클라이언트 ID (쉼표로)
    audiences: () => [process.env.GOOGLE_CLIENT_ID, ...String(process.env.GOOGLE_NATIVE_CLIENT_IDS || '').split(',')].map(s => (s || '').trim()).filter(Boolean),
  },
  apple: {
    jwks: process.env.APPLE_JWKS_URL || 'https://appleid.apple.com/auth/keys',
    issuers: ['https://appleid.apple.com'],
    // 앱 번들 ID (예: com.example.theearth), 웹용 서비스 ID
    audiences: () => String(process.env.APPLE_CLIENT_IDS || '').split(',').map(s => s.trim()).filter(Boolean),
  },
};
export const nativeEnabled = kind => PROVIDERS[kind].audiences().length > 0;

const cache = new Map(); // url → { keys, until }
async function keysOf(url, fresh = false) {
  const c = cache.get(url);
  if (c && !fresh && c.until > Date.now()) return c.keys;
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error('공개키를 가져오지 못했습니다');
  const { keys = [] } = await res.json();
  const age = +(/max-age=(\d+)/.exec(res.headers.get('cache-control') || '')?.[1] || 3600);
  cache.set(url, { keys, until: Date.now() + Math.min(age, 86400) * 1000 });
  return keys;
}
const part = s => JSON.parse(Buffer.from(s, 'base64url').toString('utf8'));

/** 검증에 성공하면 토큰 내용(payload), 아니면 오류 */
export async function verifyIdToken(kind, token, nonce = null) {
  const P = PROVIDERS[kind];
  if (!P) throw new Error('알 수 없는 로그인 방식');
  const [h, p, s] = String(token || '').split('.');
  if (!h || !p || !s) throw new Error('토큰 형식이 올바르지 않습니다');
  const header = part(h), payload = part(p);
  if (header.alg !== 'RS256') throw new Error('지원하지 않는 서명 방식');
  let keys = await keysOf(P.jwks);
  let jwk = keys.find(k => k.kid === header.kid);
  if (!jwk) { keys = await keysOf(P.jwks, true); jwk = keys.find(k => k.kid === header.kid); } // 키가 바뀌었을 때
  if (!jwk) throw new Error('서명 키를 찾을 수 없습니다');
  const ok = createVerify('RSA-SHA256').update(`${h}.${p}`).verify(createPublicKey({ key: jwk, format: 'jwk' }), Buffer.from(s, 'base64url'));
  if (!ok) throw new Error('서명이 올바르지 않습니다');
  if (!P.issuers.includes(payload.iss)) throw new Error('발급자가 올바르지 않습니다');
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.some(a => P.audiences().includes(a))) throw new Error('다른 앱의 토큰입니다');
  const now = Date.now() / 1000;
  if (!payload.exp || payload.exp < now - 300) throw new Error('만료된 토큰입니다');
  if (payload.iat && payload.iat > now + 300) throw new Error('시각이 맞지 않는 토큰입니다');
  // 재사용 막기: 앱이 만든 nonce 를 넣었다면 토큰 안의 값(원문 또는 SHA-256)과 같아야 한다
  if (nonce != null) {
    const hashed = createHash('sha256').update(String(nonce)).digest('hex');
    if (payload.nonce !== String(nonce) && payload.nonce !== hashed) throw new Error('nonce 가 맞지 않습니다');
  }
  if (!payload.sub) throw new Error('계정 번호가 없습니다');
  return payload;
}
