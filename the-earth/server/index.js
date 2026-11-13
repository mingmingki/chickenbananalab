// The Earth 게임 서버: 여러 개의 월드(서버)를 동시에 돌리고, 웹소켓으로 관찰자에게 실시간 전송한다.
import http from 'node:http';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import compression from 'compression';
import { WebSocketServer } from 'ws';
import { loadGeo, getCountry } from './geo.js';
import { World, MAX_ANNS_PER_PLAYER } from './sim/world.js';
import { loadWorlds, saveWorld } from './store.js';
import { ERA_MONEY, perUnit, INS_RATE, WON, eraF } from './sim/budget.js';
import { listedSalary } from './sim/economy.js';
import { ACTS } from './sim/mind.js';
import { ERAS, BUILDINGS, INDUSTRIES, PUBLIC, JOBS, SETTLEMENT_LEVELS, EDU_NAMES, DEPTS, ROAD_GRADES } from './sim/data.js';
import { TRAIT_NAMES, ABILITY_NAMES } from './sim/ann.js';
import { randomName } from './sim/names.js';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const PORT = +process.env.PORT || 3000;
const DEFAULT_SCALE = 24; // 현실 1시간 = 게임 하루 (현실 하루 = 게임 24일, 현실 15일 = 게임 1년)
const RUN_SCALE = process.env.TIME_SCALE ? +process.env.TIME_SCALE : null; // 개발용 가속
const ADMIN_KEY = process.env.ADMIN_KEY || '';
const SAVE_EVERY_MS = 60_000;

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

const app = express();
app.use(compression());
app.use(express.json({ limit: '256kb' }));
app.use(express.static(join(ROOT, 'public'), { extensions: ['html'] }));
app.use('/vendor/three', express.static(join(ROOT, 'node_modules', 'three'), { maxAge: '7d' }));

const getWorld = (req, res) => {
  const w = worlds.get(req.params.id);
  if (!w) res.status(404).json({ error: '서버를 찾을 수 없습니다' });
  return w;
};

app.get('/api/geo', (req, res) => {
  res.set('Cache-Control', 'public, max-age=86400');
  res.type('json').send(geo.publicJson);
});

app.get('/api/meta', (req, res) => {
  res.json({
    acts: ACTS, eras: ERAS, buildings: BUILDINGS, industries: INDUSTRIES, publicOrgs: PUBLIC, levels: SETTLEMENT_LEVELS, eduNames: EDU_NAMES, depts: DEPTS, roadGrades: ROAD_GRADES,
    // 시대의 돈: 단위 → 그 시대 돈(perUnit), 오늘날(2026) 가치 만원(to2026), 4대보험 요율, 직업별 연봉표(단위/년)
    money: ERA_MONEY.map((m, e) => ({ ...m, perUnit: perUnit(e), to2026: WON / eraF(e) / 1e4, ins: INS_RATE[e] })),
    payTable: ERA_MONEY.map((_, e) => Object.fromEntries(Object.keys(JOBS).map(k => [k, Math.round(listedSalary(k, e))]))),
    jobs: Object.fromEntries(Object.entries(JOBS).map(([k, j]) => [k, { name: j.name, era: j.era, maxEra: j.maxEra ?? null, pay: j.pay, cat: j.cat, edu: j.edu || 0, self: !!j.self, shift: j.shift }])),
    traitNames: TRAIT_NAMES, abilityNames: ABILITY_NAMES, maxAnns: MAX_ANNS_PER_PLAYER, adminRequired: !!ADMIN_KEY,
  });
});

app.get('/api/random-name', (req, res) => {
  const culture = String(req.query.culture || 'en').slice(0, 4);
  res.json({ name: randomName(culture, req.query.gender === 'F' ? 'F' : 'M') });
});

app.get('/api/servers', (req, res) => {
  res.json([...worlds.values()].map(w => ({ ...w.summary(), timeScale: w.runScale || w.timeScale })).sort((a, b) => a.created - b.created));
});

// 새 서버: 언제 열든 1년 1월 1일 0시부터 시작한다
app.post('/api/servers', (req, res) => {
  if (ADMIN_KEY && req.body?.adminKey !== ADMIN_KEY) return res.status(403).json({ error: '관리자 키가 필요합니다' });
  const n = worlds.size + 1;
  let id = 's' + n;
  while (worlds.has(id)) id = 's' + (+id.slice(1) + 1);
  const name = String(req.body?.name || '').trim().slice(0, 30) || `제${n}서버`;
  const w = new World({ id, name, timeScale: DEFAULT_SCALE, clock: 0 });
  if (RUN_SCALE) w.runScale = RUN_SCALE;
  worlds.set(id, w);
  saveWorld(w);
  res.json(w.summary());
});

app.post('/api/servers/:id/join', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const { name, country } = req.body || {};
  if (!getCountry(country)) return res.status(400).json({ error: '나라를 선택하세요' });
  const p = w.joinPlayer(name, country);
  saveWorld(w);
  res.json({ token: p.token, player: w.playerOut(p) });
});

app.get('/api/servers/:id/me', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = w.playerByToken(String(req.query.token || ''));
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  res.json({ player: w.playerOut(p), anns: p.anns.map(id => w.anns.get(id)).filter(Boolean).map(a => w.annMeta(a)), room: MAX_ANNS_PER_PLAYER - p.anns.filter(id => { const a = w.anns.get(id); return a && (a.pdeploy || (a.gen === 1 && !a.parents.length)); }).length });
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

app.post('/api/servers/:id/deploy', (req, res) => {
  const w = getWorld(req, res); if (!w) return;
  const p = w.playerByToken(String(req.body?.token || ''));
  if (!p) return res.status(404).json({ error: '플레이어를 찾을 수 없습니다' });
  const specs = Array.isArray(req.body?.anns) ? req.body.anns : [];
  if (!specs.length) return res.status(400).json({ error: '앤을 한 명 이상 만들어 주세요' });
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

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws', perMessageDeflate: { threshold: 1024 } });
const clients = new Set();

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, 'http://x');
  const w = worlds.get(url.searchParams.get('server'));
  if (!w) { ws.close(4004, 'no server'); return; }
  const client = { ws, world: w, watch: null, lastDetail: 0 };
  clients.add(client);
  ws.send(JSON.stringify({ t: 'init', ...w.initPayload() }));
  ws.on('message', raw => {
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.t === 'watch') {
      client.watch = m.id ? +m.id : null;
      if (client.watch) ws.send(JSON.stringify({ t: 'ann', ann: w.annDetail(client.watch) }));
    }
  });
  ws.on('close', () => clients.delete(client));
  ws.on('error', () => clients.delete(client));
});

let last = performance.now();
setInterval(() => {
  const now = performance.now();
  const dt = Math.min(5, (now - last) / 1000);
  last = now;
  for (const w of worlds.values()) {
    try { w.update(dt); } catch (e) { console.error('시뮬레이션 오류', w.id, e); }
    const payload = JSON.stringify({ t: 'tick', ...w.tickPayload(), scale: w.runScale || w.timeScale });
    for (const c of clients) {
      if (c.world !== w || c.ws.readyState !== 1) continue;
      c.ws.send(payload);
      if (c.watch && (w.dirtyDetail.has(c.watch) || w.dirtyAct.has(c.watch) || now - c.lastDetail > 2500)) {
        c.lastDetail = now;
        c.ws.send(JSON.stringify({ t: 'ann', ann: w.annDetail(c.watch) }));
      }
    }
    w.resetDirty();
  }
}, 1000);

setInterval(() => { for (const w of worlds.values()) saveWorld(w); }, SAVE_EVERY_MS);
const shutdown = () => {
  for (const w of worlds.values()) { try { saveWorld(w); } catch (e) { console.error(e); } }
  process.exit(0);
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

server.listen(PORT, () => {
  console.log(`The Earth 서버 실행 중: http://localhost:${PORT}`);
  for (const w of worlds.values()) console.log(` - ${w.name} (${w.id}) ${w.summary().date}, 시간 배속 x${w.runScale || w.timeScale}`);
});
