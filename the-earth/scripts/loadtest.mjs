// 부하 시험: 관전자 N명이 웹소켓으로 붙고, 플레이어 P명이 가입·투입했을 때 서버가 버티는지 본다.
// 사용법: RATE_LIMIT=off WS_PER_IP=10000 로 서버를 띄운 뒤
//   node scripts/loadtest.mjs --url http://localhost:3000 --server s1 --clients 200 --players 30 --seconds 60
import WebSocket from 'ws';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const URL_ = arg('url', 'http://localhost:3000'), SERVER = arg('server', 's1');
const N = +arg('clients', 100), P = +arg('players', 20), SEC = +arg('seconds', 60);
const COUNTRIES = ['KR', 'JP', 'US', 'FR', 'BR', 'EG', 'IN', 'AU', 'CN', 'DE'];

const post = async (path, body) => {
  const t = performance.now();
  const r = await fetch(URL_ + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${path} ${r.status} ${j.error || ''}`);
  return { j, ms: performance.now() - t };
};

const stats = { open: 0, fail: 0, closed: 0, msgs: 0, bytes: 0, init: [], gaps: [] };
const socks = [];
const wsUrl = URL_.replace(/^http/, 'ws') + '/ws?server=' + SERVER;
console.log(`관전자 ${N}명 접속 중…`);
for (let i = 0; i < N; i++) {
  const t0 = performance.now();
  const ws = new WebSocket(wsUrl);
  let last = 0;
  ws.on('open', () => stats.open++);
  ws.on('message', d => {
    stats.msgs++; stats.bytes += d.length;
    const now = performance.now();
    if (!last) stats.init.push(now - t0);
    else stats.gaps.push(now - last);
    last = now;
    if (i % 10 === 0 && Math.random() < 0.05) ws.send(JSON.stringify({ t: 'watch', id: 1 + Math.floor(Math.random() * 200) }));
  });
  ws.on('error', () => stats.fail++);
  ws.on('close', () => stats.closed++);
  socks.push(ws);
  if (i % 20 === 19) await new Promise(r => setTimeout(r, 100));
}

console.log(`플레이어 ${P}명 가입·투입 중…`);
const lat = { join: [], deploy: [] };
let errs = 0;
await Promise.all(Array.from({ length: P }, async (_, i) => {
  try {
    const { j, ms } = await post(`/api/servers/${SERVER}/join`, { name: `부하${i}`, country: COUNTRIES[i % COUNTRIES.length] });
    lat.join.push(ms);
    const anns = Array.from({ length: 12 }, (_, k) => ({ name: `앤${i}-${k}`, gender: k % 2 ? 'F' : 'M', age: 20 + k }));
    const d = await post(`/api/servers/${SERVER}/deploy`, { token: j.token, anns });
    lat.deploy.push(d.ms);
  } catch (e) { errs++; if (errs < 4) console.log('  오류:', e.message); }
}));

const t0 = Date.now();
const samples = [];
while (Date.now() - t0 < SEC * 1000) {
  await new Promise(r => setTimeout(r, 5000));
  const h = await fetch(URL_ + '/healthz').then(r => r.json()).catch(() => null);
  samples.push(h?.tickMs ?? -1);
  process.stdout.write(`  ${Math.round((Date.now() - t0) / 1000)}초: 접속 ${stats.open - stats.closed}, 1초 처리 ${h?.tickMs ?? '?'}ms\n`);
}
const pct = (a, p) => { const s = [...a].sort((x, y) => x - y); return Math.round(s[Math.floor(s.length * p)] || 0); };
console.log('\n── 결과 ──');
console.log(`웹소켓: 열림 ${stats.open}/${N}, 실패 ${stats.fail}, 끊김 ${stats.closed}`);
console.log(`첫 화면 데이터: 중앙값 ${pct(stats.init, 0.5)}ms, 95% ${pct(stats.init, 0.95)}ms`);
console.log(`실시간 간격: 중앙값 ${pct(stats.gaps, 0.5)}ms, 99% ${pct(stats.gaps, 0.99)}ms (1000ms에 가까울수록 좋음)`);
console.log(`받은 데이터: ${(stats.bytes / 1048576).toFixed(1)}MB (관전자 1명당 초당 ${(stats.bytes / N / ((Date.now() - t0) / 1000 + 5) / 1024).toFixed(1)}KB)`);
console.log(`가입 ${lat.join.length}/${P}: 중앙값 ${pct(lat.join, 0.5)}ms · 투입: 중앙값 ${pct(lat.deploy, 0.5)}ms, 최대 ${pct(lat.deploy, 0.99)}ms · 오류 ${errs}`);
console.log(`서버 1초 처리 시간: ${samples.join(', ')}ms`);
for (const s of socks) s.close();
process.exit(0);
