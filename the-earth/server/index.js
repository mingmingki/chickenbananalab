// The Earth 게임 서버: 여러 개의 월드(서버)를 동시에 돌리고, 웹소켓으로 관찰자에게 실시간 전송한다.
// 저장은 SQLite(server/db.js), 계정·로그인, 관리자 API, 신고, 푸시 알림, 보안 헤더와 접속 제한까지.
import http from 'node:http';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import express from 'express';
import compression from 'compression';
import { WebSocketServer } from 'ws';
import { loadGeo, getCountry } from './geo.js';
import { World, MAX_ANNS_PER_PLAYER } from './sim/world.js';
import { loadWorlds, saveWorld, deleteWorld } from './store.js';
import * as db from './db.js';
import { badName } from './lib/names-filter.js';
import { securityHeaders, inlineScriptHashes, limiter, clientIp } from './lib/security.js';
import { initPush, pushPublicKey, queuePush, flushPush, sendPush } from './push.js';
import { recap, ACHIEVEMENTS } from './sim/achieve.js';
import { ERA_MONEY, perUnit, INS_RATE, WON, eraF } from './sim/budget.js';
import { listedSalary } from './sim/economy.js';
import { ACTS } from './sim/mind.js';
import { ERAS, BUILDINGS, INDUSTRIES, PUBLIC, JOBS, SETTLEMENT_LEVELS, EDU_NAMES, DEPTS, ROAD_GRADES } from './sim/data.js';
import { TRAIT_NAMES, ABILITY_NAMES } from './sim/ann.js';
import { randomName } from './sim/names.js';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const PUBLIC_DIR = join(ROOT, 'public');
const PORT = +process.env.PORT || 3000;
const DEFAULT_SCALE = 24; // 현실 1시간 = 게임 하루 (현실 하루 = 게임 24일, 현실 15일 = 게임 1년)
const RUN_SCALE = process.env.TIME_SCALE ? +process.env.TIME_SCALE : null; // 개발용 가속
const ADMIN_KEY = process.env.ADMIN_KEY || '';
const OPEN_SERVERS = process.env.OPEN_SERVERS === '1'; // 개발용: 누구나 서버를 열 수 있게
const SAVE_EVERY_MS = 60_000;
const STARTED = Date.now();

const geo = loadGeo();
const worlds = loadWorlds();
if (!worlds.size) {
  const w = new World({ id: 's1', name: '제1서버 · 여명', timeScale: DEFAULT_SCALE, clock: 0 });
  worlds.set(w.id, w);
  saveWorld(w);
  console.log('새 월드를 만들었습니다:', w.name);
}
for (const w of worlds.values()) {
  if (w.timeScale === 10) w.timeScale = DEFAULT_SCALE; // 예전 기본값(10배)으로 만든 서버도 새 기준으로
  if (RUN_SCALE) w.runScale = RUN_SCALE;
}
initPush();
db.cleanupSessions();

const app = express();
// 호스팅(Fly·Render 등) 앞단 프록시가 붙여 주는 X-Forwarded-For 를 믿을지
const TRUST = process.env.TRUST_PROXY ?? (process.env.FLY_APP_NAME || process.env.RENDER || process.env.RAILWAY_ENVIRONMENT ? '1' : '0');
app.set('trust proxy', /^\d+$/.test(TRUST) ? +TRUST : TRUST);
app.disable('x-powered-by');
app.use(securityHeaders({ scriptHashes: inlineScriptHashes([join(PUBLIC_DIR, 'index.html'), join(PUBLIC_DIR, 'admin.html')]) }));
app.use(compression());
app.use(express.json({ limit: '64kb' }));
app.use('/api/', limiter(600, 60_000)); // IP당 분당 600회
// 약관·개인정보 처리방침: 운영자 정보는 환경 변수로 채운다
const OP = { name: process.env.OPERATOR_NAME, email: process.env.OPERATOR_EMAIL, date: process.env.LEGAL_DATE, host: process.env.HOST_NAME, region: process.env.HOST_REGION };
const legalCache = {};
for (const page of ['privacy', 'terms']) {
  app.get(['/' + page, `/${page}.html`], (req, res) => {
    legalCache[page] = legalCache[page] || readFileSync(join(PUBLIC_DIR, page + '.html'), 'utf8').replace(/<span data-op="(\w+)">([^<]*)<\/span>/g, (m, k, def) => (OP[k] ? escHtml(OP[k]) : m));
    res.type('html').send(legalCache[page]);
  });
}
app.use(express.static(PUBLIC_DIR, { extensions: ['html'], setHeaders: (res, p) => { if (p.endsWith('sw.js')) res.set('Cache-Control', 'no-cache'); } }));
app.use('/vendor/three', express.static(join(ROOT, 'node_modules', 'three'), { maxAge: '7d' }));

function escHtml(v) { return String(v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const getWorld = (req, res) => {
  const w = worlds.get(req.params.id);
  if (!w) res.status(404).json({ error: '서버를 찾을 수 없습니다' });
  return w;
};

// ── 로그인 ──
const tokenOf = req => { const h = req.get('authorization') || ''; return h.startsWith('Bearer ') ? h.slice(7).trim() : ''; };
const authOf = req => db.sessionAccount(tokenOf(req));
const keyOk = k => { if (!ADMIN_KEY || !k) return false; const a = Buffer.from(String(k)), b = Buffer.from(ADMIN_KEY); return a.length === b.length && timingSafeEqual(a, b); };
function needAuth(req, res, next) {
  const a = authOf(req);
  if (!a) return res.status(401).json({ error: '로그인이 필요합니다' });
  if (a.banned) return res.status(403).json({ error: '이용이 정지된 계정입니다' });
  req.acc = a; next();
}
function needAdmin(req, res, next) {
  const a = authOf(req);
  if (!(a?.admin && !a.banned) && !keyOk(req.get('x-admin-key'))) return res.status(403).json({ error: '관리자만 쓸 수 있습니다' });
  req.acc = a; next();
}
const accOut = a => a && { id: a.id, username: a.username, admin: !!a.admin, created: a.created, prefs: JSON.parse(a.prefs || '{}') };
const USERNAME = /^[가-힣a-zA-Z0-9_]{2,16}$/;

const signupLimit = limiter(5, 3600_000), loginLimit = limiter(12, 600_000);
app.post('/api/auth/signup', signupLimit, (req, res) => {
  const { username, password, agree } = req.body || {};
  const u = String(username || '').trim();
  if (!USERNAME.test(u)) return res.status(400).json({ error: '아이디는 한글·영문·숫자·_ 2~16자로 정해 주세요' });
  const bad = badName(u); if (bad) return res.status(400).json({ error: `아이디: ${bad}` });
  if (typeof password !== 'string' || password.length < 8 || password.length > 72) return res.status(400).json({ error: '비밀번호는 8~72자로 정해 주세요' });
  if (!agree) return res.status(400).json({ error: '이용약관과 개인정보 처리방침에 동의해 주세요 (만 14세 이상)' });
  if (db.accountByName(u)) return res.status(409).json({ error: '이미 있는 아이디입니다' });
  const acc = db.createAccount(u, password);
  res.json({ token: db.newSession(acc.id), account: accOut(acc) });
});
app.post('/api/auth/login', loginLimit, (req, res) => {
  const { username, password } = req.body || {};
  const acc = db.accountByName(String(username || '').trim());
  if (!acc || typeof password !== 'string' || !db.verifyPassword(acc, password)) return res.status(401).json({ error: '아이디 또는 비밀번호가 맞지 않습니다' });
  if (acc.banned) return res.status(403).json({ error: '이용이 정지된 계정입니다' });
  if (!acc.admin && db.isAdminName(acc.username)) { db.setAdmin(acc.id, true); acc.admin = 1; }
  res.json({ token: db.newSession(acc.id), account: accOut(acc) });
});
app.post('/api/auth/logout', (req, res) => { const t = tokenOf(req); if (t) db.endSession(t); res.json({ ok: true }); });
app.post('/api/auth/password', needAuth, loginLimit, (req, res) => {
  const { old, password } = req.body || {};
  if (!db.verifyPassword(req.acc, String(old || ''))) return res.status(401).json({ error: '지금 비밀번호가 맞지 않습니다' });
  if (typeof password !== 'string' || password.length < 8 || password.length > 72) return res.status(400).json({ error: '비밀번호는 8~72자로 정해 주세요' });
  db.changePassword(req.acc.id, password);
  res.json({ ok: true });
});
/** 회원 탈퇴: 계정·세션·푸시 구독을 지우고, 게임 속 플레이어 이름은 '탈퇴한 플레이어'로 바꾼다 (앤들은 계속 살아간다) */
app.post('/api/auth/delete', needAuth, loginLimit, (req, res) => {
  if (!db.verifyPassword(req.acc, String(req.body?.password || ''))) return res.status(401).json({ error: '비밀번호가 맞지 않습니다' });
  for (const g of db.gamesOf(req.acc.id)) {
    const w = worlds.get(g.server), p = w?.players[g.player];
    if (p) { p.name = '탈퇴한 플레이어'; p.token = 'deleted-' + p.token; }
  }
  db.deleteAccount(req.acc.id);
  res.json({ ok: true });
});
function gameOut(g) {
  const w = worlds.get(g.server), p = w?.players[g.player];
  if (!p) return null;
  return { serverId: g.server, serverName: w.name, token: g.ptoken, playerName: p.name, country: p.country, countryName: getCountry(p.country)?.name || p.country, anns: p.anns.length, alive: p.anns.filter(id => w.anns.get(id)?.alive).length, seen: g.seen_clock || 0 };
}
app.get('/api/auth/me', needAuth, (req, res) => {
  res.json({ account: accOut(req.acc), games: db.gamesOf(req.acc.id).map(gameOut).filter(Boolean) });
});
app.put('/api/auth/prefs', needAuth, (req, res) => {
  const p = req.body?.prefs;
  if (!p || typeof p !== 'object') return res.status(400).json({ error: '설정 형식이 맞지 않습니다' });
  db.setPrefs(req.acc.id, p);
  res.json({ ok: true });
});
/** 로그인 전에 이 기기에서 하던 게임을 계정에 붙인다 */
app.post('/api/auth/claim', needAuth, (req, res) => {
  const list = Array.isArray(req.body?.games) ? req.body.games.slice(0, 20) : [];
  let n = 0;
  for (const g of list) {
    const w = worlds.get(String(g.serverId)), p = w?.playerByToken(String(g.token || ''));
    if (!p) continue;
    const owner = db.ownerOfPlayer(w.id, p.id);
    if (owner && owner !== req.acc.id) continue;
    if (!owner) { db.addGame(req.acc.id, w.id, p.id, p.token); n++; }
  }
  res.json({ claimed: n, games: db.gamesOf(req.acc.id).map(gameOut).filter(Boolean) });
});

// ── 게임 정보 ──
app.get('/api/geo', (req, res) => {
  res.set('Cache-Control', 'public, max-age=86400');
  res.type('json').send(geo.publicJson);
});

let metaCache = null;
app.get('/api/meta', (req, res) => {
  metaCache = metaCache || JSON.stringify({
    acts: ACTS, eras: ERAS, buildings: BUILDINGS, industries: INDUSTRIES, publicOrgs: PUBLIC, levels: SETTLEMENT_LEVELS, eduNames: EDU_NAMES, depts: DEPTS, roadGrades: ROAD_GRADES,
    // 시대의 돈: 단위 → 그 시대 돈(perUnit), 오늘날(2026) 가치 만원(to2026), 4대보험 요율, 직업별 연봉표(단위/년)
    money: ERA_MONEY.map((m, e) => ({ ...m, perUnit: perUnit(e), to2026: WON / eraF(e) / 1e4, ins: INS_RATE[e] })),
    payTable: ERA_MONEY.map((_, e) => Object.fromEntries(Object.keys(JOBS).map(k => [k, Math.round(listedSalary(k, e))]))),
    jobs: Object.fromEntries(Object.entries(JOBS).map(([k, j]) => [k, { name: j.name, era: j.era, maxEra: j.maxEra ?? null, pay: j.pay, cat: j.cat, edu: j.edu || 0, self: !!j.self, shift: j.shift }])),
    traitNames: TRAIT_NAMES, abilityNames: ABILITY_NAMES, maxAnns: MAX_ANNS_PER_PLAYER, openServers: OPEN_SERVERS, achievements: ACHIEVEMENTS,
  });
  res.type('json').send(metaCache);
});

app.get('/api/random-name', (req, res) => {
  const culture = String(req.query.culture || 'en').slice(0, 4);
  res.json({ name: randomName(culture, req.query.gender === 'F' ? 'F' : 'M') });
});

app.get('/api/servers', (req, res) => {
  res.json([...worlds.values()].map(w => ({ ...w.summary(), timeScale: w.runScale || w.timeScale })).sort((a, b) => a.created - b.created));
});

function createServer(name) {
  const n = worlds.size + 1;
  let id = 's' + n;
  while (worlds.has(id)) id = 's' + (+id.slice(1) + 1);
  const w = new World({ id, name: String(name || '').trim().slice(0, 30) || `제${n}서버`, timeScale: DEFAULT_SCALE, clock: 0 });
  if (RUN_SCALE) w.runScale = RUN_SCALE;
  worlds.set(id, w);
  saveWorld(w, { backup: true });
  return w;
}
// 새 서버: 언제 열든 1년 1월 1일 0시부터 시작한다 (운영자만, 개발 모드에선 누구나)
app.post('/api/servers', (req, res, next) => (OPEN_SERVERS ? next() : needAdmin(req, res, next)), (req, res) => {
  const name = String(req.body?.name || '').trim();
  const bad = name && badName(name, { reserved: false }); if (bad) return res.status(400).json({ error: `서버 이름: ${bad}` });
  res.json(createServer(name).summary());
});

const joinLimit = limiter(8, 3600_000);
app.post('/api/servers/:id/join', joinLimit, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const acc = authOf(req);
  if (acc?.banned) return res.status(403).json({ error: '이용이 정지된 계정입니다' });
  const { country } = req.body || {};
  const name = String(req.body?.name || '').trim().slice(0, 20);
  if (!name) return res.status(400).json({ error: '플레이어 이름을 입력해 주세요' });
  const bad = badName(name); if (bad) return res.status(400).json({ error: `플레이어 이름: ${bad}` });
  if (!getCountry(country)) return res.status(400).json({ error: '나라를 선택하세요' });
  if (acc && db.gameOn(acc.id, w.id).length >= 3) return res.status(409).json({ error: '한 계정은 한 서버에서 게임을 3개까지 만들 수 있습니다. 이어하기를 눌러 주세요.' });
  const p = w.joinPlayer(name, country);
  if (acc) db.addGame(acc.id, w.id, p.id, p.token);
  saveWorld(w);
  res.json({ token: p.token, player: w.playerOut(p) });
});

const playerOf = (w, req) => w.playerByToken(String(req.query.token || req.body?.token || ''));
app.get('/api/servers/:id/me', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = playerOf(w, req);
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  res.json({ player: w.playerOut(p), ach: p.ach || {}, anns: p.anns.map(id => w.anns.get(id)).filter(Boolean).map(a => w.annMeta(a)), room: MAX_ANNS_PER_PLAYER - p.anns.filter(id => { const a = w.anns.get(id); return a && (a.pdeploy || (a.gen === 1 && !a.parents.length)); }).length });
});
// 다시 들어왔을 때: 그동안 있었던 일
app.get('/api/servers/:id/recap', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = playerOf(w, req);
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  res.json(recap(w, p, +req.query.since || 0));
});
app.get('/api/servers/:id/inbox', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = playerOf(w, req);
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  res.json({ notes: (p.inbox || []).slice(-80), ach: p.ach || {} });
});
app.post('/api/servers/:id/seen', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = playerOf(w, req), acc = authOf(req);
  if (p && acc) db.setSeenClock(acc.id, w.id, p.id, Math.min(w.clock, +req.body?.clock || w.clock));
  res.json({ ok: !!p });
});

// 이 나라에 투입하면 어떻게 되는가: 가정에 아기로 태어나는지, 개척자로 도착하는지
app.get('/api/servers/:id/country/:code', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const code = req.params.code;
  let families = 0;
  for (const a of w.anns.values()) {
    const pid = a.spouse || a.partner;
    if (a.alive && a.country === code && pid && a.id < pid && w.anns.get(pid)?.alive) families++;
  }
  const c = w.countries[code];
  res.json({ families, mode: families ? 'baby' : 'pioneer', pop: c?.pop || 0, era: c?.era ?? 0, eraName: ERAS[c?.era ?? 0].name, settlements: [...w.settlements.values()].filter(s => s.country === code).length });
});

const deployLimit = limiter(20, 600_000);
app.post('/api/servers/:id/deploy', deployLimit, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  if (authOf(req)?.banned) return res.status(403).json({ error: '이용이 정지된 계정입니다' });
  const p = playerOf(w, req);
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  const specs = Array.isArray(req.body?.anns) ? req.body.anns.slice(0, MAX_ANNS_PER_PLAYER) : [];
  if (!specs.length) return res.status(400).json({ error: '앤을 한 명 이상 만들어 주세요' });
  for (const s of specs) {
    const bad = badName(String(s?.name || '').slice(0, 20));
    if (bad) return res.status(400).json({ error: `앤 이름 '${String(s.name).slice(0, 20)}': ${bad}` });
  }
  try {
    const out = w.deployAnns(p, specs);
    saveWorld(w);
    res.json({ anns: out.map(a => w.annMeta(a)) });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.get('/api/servers/:id/ann/:annId', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const d = w.annDetail(+req.params.annId);
  if (!d) return res.status(404).json({ error: '앤을 찾을 수 없습니다' });
  res.json(d);
});

// ── 신고 ──
const reportLimit = limiter(10, 3600_000);
app.post('/api/report', reportLimit, (req, res) => {
  const { server, kind, id, reason } = req.body || {};
  const w = worlds.get(String(server));
  if (!w) return res.status(404).json({ error: '서버를 찾을 수 없습니다' });
  let name = null;
  if (kind === 'ann') name = w.anns.get(+id)?.name;
  else if (kind === 'player') name = w.players[String(id)]?.name;
  else if (kind === 'company') name = w.companies.get(+id)?.name;
  if (!name) return res.status(404).json({ error: '신고할 대상을 찾을 수 없습니다' });
  const why = String(reason || '').trim();
  if (!why) return res.status(400).json({ error: '신고 사유를 골라 주세요' });
  db.addReport(authOf(req)?.id ?? null, w.id, kind, id, name, why + ` (IP ${clientIp(req)})`);
  res.json({ ok: true });
});

// ── 푸시 알림 ──
app.get('/api/push/key', (req, res) => res.json({ key: pushPublicKey() }));
app.post('/api/push/subscribe', needAuth, (req, res) => {
  const sub = req.body?.sub;
  if (!sub || typeof sub.endpoint !== 'string' || !/^https:\/\//.test(sub.endpoint) || !sub.keys?.p256dh || !sub.keys?.auth) return res.status(400).json({ error: '구독 정보가 올바르지 않습니다' });
  db.addPushSub(req.acc.id, { endpoint: sub.endpoint, keys: { p256dh: String(sub.keys.p256dh), auth: String(sub.keys.auth) } });
  res.json({ ok: true });
});
app.post('/api/push/unsubscribe', (req, res) => { if (req.body?.endpoint) db.removePushSub(String(req.body.endpoint)); res.json({ ok: true }); });
app.post('/api/push/test', needAuth, limiter(5, 600_000), async (req, res) => {
  const n = await sendPush(req.acc.id, { title: '🌍 The Earth', body: '알림이 잘 도착합니다. 앤들의 큰 소식을 알려 드릴게요.', tag: 'test', url: '/' });
  res.json({ sent: n });
});

// ── 운영 ──
const stats = { tickMs: 0, tickMax: 0, ticks: 0, slowTicks: 0, wsPeak: 0 };
app.get('/healthz', (req, res) => {
  const lag = Date.now() - lastTickAt;
  res.status(lag < 10_000 ? 200 : 503).json({ ok: lag < 10_000, uptime: Math.round((Date.now() - STARTED) / 1000), worlds: worlds.size, clients: clients.size, tickMs: Math.round(stats.tickMs) });
});

app.get('/api/admin/overview', needAdmin, (req, res) => {
  const mem = process.memoryUsage();
  res.json({
    uptime: Math.round((Date.now() - STARTED) / 1000), node: process.version, mem: { rss: mem.rss, heap: mem.heapUsed },
    tick: { avgMs: Math.round(stats.tickMs * 10) / 10, maxMs: Math.round(stats.tickMax), slow: stats.slowTicks, count: stats.ticks },
    clients: clients.size, wsPeak: stats.wsPeak, accounts: db.accountCount(), openReports: db.openReports(), dbPath: db.dbPath,
    worlds: [...worlds.values()].map(w => ({ ...w.summary(), timeScale: w.runScale || w.timeScale, anns: w.anns.size, buildings: w.buildings.size, companies: [...w.companies.values()].filter(c => !c.dead).length, settlements: w.settlements.size, saveMs: Math.round(w._saveMs || 0), saveKB: Math.round((w._saveBytes || 0) / 1024), watchers: [...clients].filter(c => c.world === w).length })),
  });
});
app.post('/api/admin/servers', needAdmin, (req, res) => {
  const name = String(req.body?.name || '').trim();
  res.json(createServer(name).summary());
});
app.post('/api/admin/servers/:id/pause', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  w.paused = !!req.body?.paused;
  saveWorld(w);
  res.json(w.summary());
});
app.post('/api/admin/servers/:id/rename', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const name = String(req.body?.name || '').trim().slice(0, 30);
  if (!name) return res.status(400).json({ error: '이름을 입력하세요' });
  w.name = name; saveWorld(w);
  res.json(w.summary());
});
app.post('/api/admin/servers/:id/save', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  res.json({ ms: saveWorld(w, { backup: true }) });
});
app.delete('/api/admin/servers/:id', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  if (req.body?.confirm !== w.id) return res.status(400).json({ error: '확인용으로 서버 ID를 정확히 입력하세요' });
  saveWorld(w, { backup: true }); // 지우기 전 마지막 백업은 남겨 둔다
  worlds.delete(w.id);
  deleteWorld(w.id);
  db.deleteGamesOfServer(w.id);
  for (const c of clients) if (c.world === w) c.ws.close(4004, 'server deleted');
  res.json({ ok: true });
});
app.get('/api/admin/servers/:id/players', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  res.json(Object.values(w.players).map(p => {
    const acc = db.ownerOfPlayer(w.id, p.id);
    return { ...w.playerOut(p), joinedReal: p.joinedReal, account: acc ? db.accountById(acc)?.username || null : null, ach: Object.keys(p.ach || {}).length };
  }).sort((a, b) => b.joinedReal - a.joinedReal));
});
app.post('/api/admin/servers/:id/players/:pid/rename', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = w.players[req.params.pid];
  if (!p) return res.status(404).json({ error: '플레이어가 없습니다' });
  p.name = String(req.body?.name || '').trim().slice(0, 20) || '이름 바뀐 플레이어';
  saveWorld(w);
  res.json(w.playerOut(p));
});
/** 플레이어 내보내기: 계정 연결과 접속 토큰을 끊는다. 앤들은 주인 없이 계속 살아간다 */
app.post('/api/admin/servers/:id/players/:pid/remove', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = w.players[req.params.pid];
  if (!p) return res.status(404).json({ error: '플레이어가 없습니다' });
  for (const id of p.anns) { const a = w.anns.get(id); if (a) { a.owner = null; w.dirtyAnnMeta.add(a.id); } }
  delete w.players[p.id];
  db.deleteGamesOfPlayer(w.id, p.id);
  saveWorld(w);
  res.json({ ok: true });
});
app.post('/api/admin/servers/:id/anns/:aid/rename', needAdmin, (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const a = w.anns.get(+req.params.aid);
  if (!a) return res.status(404).json({ error: '앤이 없습니다' });
  a.name = String(req.body?.name || '').trim().slice(0, 20) || '이름없음';
  w.dirtyAnnMeta.add(a.id);
  saveWorld(w);
  res.json(w.annMeta(a));
});
app.get('/api/admin/accounts', needAdmin, (req, res) => res.json(db.listAccounts()));
app.post('/api/admin/accounts/:id/ban', needAdmin, (req, res) => {
  const id = +req.params.id;
  if (req.acc && req.acc.id === id) return res.status(400).json({ error: '자기 계정은 정지할 수 없습니다' });
  db.setBanned(id, !!req.body?.banned);
  if (req.body?.banned) db.endSessionsOf(id);
  res.json({ ok: true });
});
app.post('/api/admin/accounts/:id/admin', needAdmin, (req, res) => {
  const id = +req.params.id;
  if (req.acc && req.acc.id === id && !req.body?.admin) return res.status(400).json({ error: '자기 관리자 권한은 뺄 수 없습니다' });
  db.setAdmin(id, !!req.body?.admin);
  res.json({ ok: true });
});
app.get('/api/admin/reports', needAdmin, (req, res) => res.json(db.listReports()));
app.post('/api/admin/reports/:id', needAdmin, (req, res) => {
  const st = ['open', 'done', 'dismissed'].includes(req.body?.status) ? req.body.status : 'done';
  db.setReportStatus(+req.params.id, st);
  res.json({ ok: true });
});
app.get('/api/admin/backups', needAdmin, (req, res) => res.json(db.listBackups()));
/** 백업으로 되돌리기: 지금 상태도 백업해 두고 바꾼다 */
app.post('/api/admin/backups/restore', needAdmin, (req, res) => {
  const { id, saved } = req.body || {};
  const json = db.getBackup(String(id), +saved);
  if (!json) return res.status(404).json({ error: '백업을 찾을 수 없습니다' });
  const cur = worlds.get(String(id));
  if (cur) saveWorld(cur, { backup: true });
  const w = World.fromJSON(JSON.parse(json));
  if (RUN_SCALE) w.runScale = RUN_SCALE;
  worlds.set(w.id, w);
  saveWorld(w);
  for (const c of clients) if (c.world === cur) c.ws.close(4001, 'restored');
  res.json(w.summary());
});
app.get('/api/admin/errors', needAdmin, (req, res) => res.json(db.recentErrors()));

app.use('/api/', (req, res) => res.status(404).json({ error: '없는 주소입니다' }));
app.use((err, req, res, next) => {
  if (err.type === 'entity.too.large') return res.status(413).json({ error: '요청이 너무 큽니다' });
  if (err.type === 'entity.parse.failed') return res.status(400).json({ error: '요청 형식이 올바르지 않습니다' });
  db.logError('http ' + req.path, err);
  res.status(500).json({ error: '서버 오류가 났습니다' });
});

// ── 실시간 ──
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 4096, perMessageDeflate: { threshold: 1024 } });
const clients = new Set();
const wsPerIp = new Map();
const WS_PER_IP = +process.env.WS_PER_IP || 12;

wss.on('connection', (ws, req) => {
  // 프록시가 덧붙인 마지막 값이 진짜 접속 IP (맨 앞 값은 접속자가 마음대로 넣을 수 있다)
  const ip = (app.get('trust proxy') && req.headers['x-forwarded-for']?.split(',').map(v => v.trim()).filter(Boolean).at(-1)) || req.socket.remoteAddress;
  if ((wsPerIp.get(ip) || 0) >= WS_PER_IP) { ws.close(4029, 'too many connections'); return; }
  wsPerIp.set(ip, (wsPerIp.get(ip) || 0) + 1);
  const url = new URL(req.url, 'http://x');
  const w = worlds.get(url.searchParams.get('server'));
  if (!w) { ws.close(4004, 'no server'); wsPerIp.set(ip, wsPerIp.get(ip) - 1); return; }
  const client = { ws, world: w, watch: null, lastDetail: 0, pid: null, visible: true, seenAt: Date.now(), msgs: 0, msgReset: Date.now() + 10_000 };
  clients.add(client);
  stats.wsPeak = Math.max(stats.wsPeak, clients.size);
  ws.send(JSON.stringify({ t: 'init', ...w.initPayload() }));
  ws.on('message', raw => {
    if (Date.now() > client.msgReset) { client.msgs = 0; client.msgReset = Date.now() + 10_000; }
    if (++client.msgs > 60) return; // 10초에 60개 넘게 보내면 무시
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.t === 'watch') {
      client.watch = m.id ? +m.id : null;
      if (client.watch) ws.send(JSON.stringify({ t: 'ann', ann: w.annDetail(client.watch) }));
    } else if (m.t === 'hello') {
      const p = w.playerByToken(String(m.token || ''));
      client.pid = p?.id || null;
    } else if (m.t === 'vis') {
      client.visible = !!m.v; client.seenAt = Date.now();
    }
  });
  const bye = () => { if (clients.delete(client)) wsPerIp.set(ip, Math.max(0, (wsPerIp.get(ip) || 1) - 1)); };
  ws.on('close', bye);
  ws.on('error', bye);
});

// 지금 앱을 보고 있는 계정인가 (그러면 푸시 대신 화면 안 알림)
function isWatching(accountId) {
  for (const c of clients) {
    if (!c.pid || !c.visible || c.ws.readyState !== 1) continue;
    if (db.ownerOfPlayer(c.world.id, c.pid) === accountId) return true;
  }
  return false;
}

let last = performance.now(), lastTickAt = Date.now();
const errSeen = new Map();
setInterval(() => {
  const now = performance.now();
  const dt = Math.min(5, (now - last) / 1000);
  last = now;
  lastTickAt = Date.now();
  for (const w of worlds.values()) {
    try { w.update(dt); } catch (e) {
      const k = w.id + String(e.message);
      if (!errSeen.has(k) || Date.now() - errSeen.get(k) > 60_000) { errSeen.set(k, Date.now()); console.error('시뮬레이션 오류', w.id, e); db.logError('sim ' + w.id, e); }
    }
    const payload = JSON.stringify({ t: 'tick', ...w.tickPayload(), scale: w.paused ? 0 : (w.runScale || w.timeScale) });
    const notesBy = new Map();
    for (const n of w.newNotes) { if (!notesBy.has(n.pid)) notesBy.set(n.pid, []); notesBy.get(n.pid).push(n); }
    for (const c of clients) {
      if (c.world !== w || c.ws.readyState !== 1) continue;
      if (c.ws.bufferedAmount > 4e6) continue; // 느린 연결은 건너뛴다 (메모리 보호)
      c.ws.send(payload);
      if (c.pid && notesBy.has(c.pid)) c.ws.send(JSON.stringify({ t: 'notes', notes: notesBy.get(c.pid) }));
      if (c.watch && (w.dirtyDetail.has(c.watch) || w.dirtyAct.has(c.watch) || now - c.lastDetail > 2500)) {
        c.lastDetail = now;
        c.ws.send(JSON.stringify({ t: 'ann', ann: w.annDetail(c.watch) }));
      }
    }
    // 푸시: 중요한 소식만 계정별로 모아서
    for (const [pid, list] of notesBy) {
      const acc = db.ownerOfPlayer(w.id, pid);
      if (acc) for (const n of list) if (n.lvl >= 2) queuePush(acc, n, w.name);
    }
    w.resetDirty();
  }
  const took = performance.now() - now;
  stats.ticks++;
  stats.tickMs = stats.tickMs * 0.95 + took * 0.05;
  stats.tickMax = Math.max(stats.tickMax * 0.999, took);
  if (took > 800) stats.slowTicks++;
}, 1000);
setInterval(() => flushPush(isWatching), 30_000);

setInterval(() => {
  for (const w of worlds.values()) { try { saveWorld(w); } catch (e) { console.error('저장 실패', w.id, e); db.logError('save ' + w.id, e); } }
}, SAVE_EVERY_MS);
setInterval(() => db.cleanupSessions(), 6 * 3600_000).unref();

let shuttingDown = false;
const shutdown = code => {
  if (shuttingDown) return; shuttingDown = true;
  for (const w of worlds.values()) { try { saveWorld(w); } catch (e) { console.error(e); } }
  process.exit(code);
};
process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));
process.on('uncaughtException', e => { console.error('처리하지 못한 오류', e); db.logError('uncaught', e); shutdown(1); });
process.on('unhandledRejection', e => { console.error('처리하지 못한 약속', e); db.logError('rejection', e); });

server.listen(PORT, () => {
  console.log(`The Earth 서버 실행 중: http://localhost:${PORT}`);
  for (const w of worlds.values()) console.log(` - ${w.name} (${w.id}) ${w.summary().date}, 시간 배속 x${w.runScale || w.timeScale}${w.paused ? ' (일시정지)' : ''}`);
  if (!db.accountCount()) console.log(' * 처음 가입하는 계정이 관리자가 됩니다. (ADMIN_USERS 환경 변수로도 지정 가능)');
});
