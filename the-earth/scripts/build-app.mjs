// 휴대폰 앱(Capacitor)에 넣을 화면 파일 만들기: public/ 과 three.js 를 app/www/ 로 복사하고 서버 주소를 박아 넣는다.
// 사용법: node scripts/build-app.mjs https://내서버주소   → 그다음 app/ 에서 npx cap sync
import { cpSync, rmSync, mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const server = (process.argv[2] || process.env.EARTH_SERVER || '').replace(/\/$/, '');
if (!/^https:\/\//.test(server)) { console.error('서버 주소(https://...)를 적어 주세요: node scripts/build-app.mjs https://the-earth.fly.dev'); process.exit(1); }
const out = join(ROOT, 'app', 'www');
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
// 웹 전용 파일(관리자, 서비스 워커, 구글 웹 로그인 복귀 화면, 약관)은 서버에 있으니 앱에는 넣지 않는다
const skip = new Set(['admin.html', 'sw.js', 'google-done.html', 'privacy.html', 'terms.html', 'manifest.webmanifest']);
cpSync(join(ROOT, 'public'), out, { recursive: true, filter: src => !skip.has(src.split(/[\\/]/).pop()) && !/js[\\/](admin|google-done)\.js$/.test(src) });
cpSync(join(ROOT, 'node_modules', 'three', 'build'), join(out, 'vendor', 'three', 'build'), { recursive: true });
let html = readFileSync(join(out, 'index.html'), 'utf8');
html = html.replace('<meta charset="utf-8" />', `<meta charset="utf-8" />\n  <meta name="earth-server" content="${server}" />`)
  .replace(/\s*<link rel="manifest"[^>]*>/, '');
writeFileSync(join(out, 'index.html'), html);
if (!existsSync(join(ROOT, 'app', 'capacitor.config.json'))) console.warn('app/capacitor.config.json 이 없습니다');
console.log(`app/www 를 만들었습니다 (서버: ${server}). 이제: cd app && npx cap sync`);
