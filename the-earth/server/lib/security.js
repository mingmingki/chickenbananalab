// 보안: 응답 헤더(CSP 등), 접속 제한(IP당 요청 수), 클라이언트 IP.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

/** index.html 같은 페이지 안의 인라인 <script>(importmap)를 CSP 해시로 허용한다 */
export function inlineScriptHashes(files) {
  const out = new Set();
  for (const f of files) {
    try {
      const html = readFileSync(f, 'utf8');
      for (const m of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)) out.add(`'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`);
    } catch { /* 없는 파일 */ }
  }
  return [...out];
}

export function securityHeaders({ scriptHashes = [] } = {}) {
  const csp = [
    "default-src 'self'",
    `script-src 'self' ${scriptHashes.join(' ')}`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob:",
    "connect-src 'self' ws: wss:",
    "worker-src 'self'",
    "manifest-src 'self'",
    "media-src 'self' blob: data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
  return (req, res, next) => {
    res.set({
      'Content-Security-Policy': csp,
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
      'Referrer-Policy': 'strict-origin-when-cross-origin',
      'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
      'Cross-Origin-Opener-Policy': 'same-origin',
    });
    if (req.secure) res.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
    next();
  };
}

export const clientIp = req => req.ip || req.socket?.remoteAddress || '?';

/** IP(또는 key 함수)마다 windowMs 동안 max 번까지 */
export function limiter(max, windowMs, keyFn = clientIp) {
  if (process.env.RATE_LIMIT === 'off') { const pass = (req, res, next) => next(); pass.hit = () => true; return pass; } // 부하 시험용
  const hits = new Map();
  setInterval(() => { const now = Date.now(); for (const [k, v] of hits) if (v.reset <= now) hits.delete(k); }, Math.min(windowMs, 600_000)).unref();
  const fn = (req, res, next) => {
    const k = keyFn(req), now = Date.now();
    let v = hits.get(k);
    if (!v || v.reset <= now) { v = { n: 0, reset: now + windowMs }; hits.set(k, v); }
    if (++v.n > max) {
      res.set('Retry-After', String(Math.ceil((v.reset - now) / 1000)));
      return res.status(429).json({ error: '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.' });
    }
    next();
  };
  fn.hit = k => { const now = Date.now(); let v = hits.get(k); if (!v || v.reset <= now) { v = { n: 0, reset: now + windowMs }; hits.set(k, v); } return ++v.n <= max; };
  return fn;
}
