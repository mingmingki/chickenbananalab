// 저장소: SQLite (Node 내장 node:sqlite). 계정, 로그인 세션, 월드 스냅숏(+백업), 신고, 푸시 구독, 설정.
// DATA_DIR/earth.db 한 파일. 예전 JSON 월드 파일이 있으면 처음 한 번 가져온다.
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, readdirSync, readFileSync, existsSync, renameSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
export const DATA_DIR = process.env.DATA_DIR || join(ROOT, 'data', 'worlds');
mkdirSync(DATA_DIR, { recursive: true });
const db = new DatabaseSync(join(DATA_DIR, 'earth.db'));
db.exec(`
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = NORMAL;
  CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, json TEXT NOT NULL, saved INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS world_backups (id TEXT NOT NULL, saved INTEGER NOT NULL, json TEXT NOT NULL, PRIMARY KEY (id, saved));
  CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pass TEXT NOT NULL,
    created INTEGER NOT NULL, last_seen INTEGER, admin INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, prefs TEXT DEFAULT '{}');
  CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account INTEGER NOT NULL, created INTEGER NOT NULL, last INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS games (account INTEGER NOT NULL, server TEXT NOT NULL, player TEXT NOT NULL, ptoken TEXT NOT NULL, created INTEGER NOT NULL, seen_clock REAL DEFAULT 0,
    PRIMARY KEY (account, server, player));
  CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL, reporter INTEGER, server TEXT, target_kind TEXT, target_id TEXT, target_name TEXT, reason TEXT, status TEXT DEFAULT 'open');
  CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, account INTEGER NOT NULL, json TEXT NOT NULL, created INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER NOT NULL, where_ TEXT, msg TEXT);
`);
// 구글 로그인: 계정에 구글 계정 고유번호(sub)를 붙인다 (예전 DB에는 칸이 없으니 추가)
// 애플 로그인(휴대폰 앱)도 같은 방식으로 apple_sub
for (const col of ['google_sub', 'apple_sub']) {
  if (!db.prepare('PRAGMA table_info(accounts)').all().some(c => c.name === col)) db.exec(`ALTER TABLE accounts ADD COLUMN ${col} TEXT`);
  db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS accounts_${col} ON accounts(${col}) WHERE ${col} IS NOT NULL`);
}

// ── 월드 ──
const stmt = {
  saveWorld: db.prepare('INSERT INTO worlds (id, json, saved) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET json = excluded.json, saved = excluded.saved'),
  allWorlds: db.prepare('SELECT id, json FROM worlds'),
  delWorld: db.prepare('DELETE FROM worlds WHERE id = ?'),
  backup: db.prepare('INSERT OR REPLACE INTO world_backups (id, saved, json) VALUES (?, ?, ?)'),
  trimBackups: db.prepare('DELETE FROM world_backups WHERE id = ? AND saved NOT IN (SELECT saved FROM world_backups WHERE id = ? ORDER BY saved DESC LIMIT ?)'),
  listBackups: db.prepare('SELECT id, saved, length(json) AS size FROM world_backups ORDER BY saved DESC LIMIT 50'),
  getBackup: db.prepare('SELECT json FROM world_backups WHERE id = ? AND saved = ?'),
};
export function saveWorldJson(id, json) { stmt.saveWorld.run(id, json, Date.now()); }
export function deleteWorld(id) { stmt.delWorld.run(id); }
/** 한 시간에 한 번쯤: 최근 24개만 남기는 백업 */
export function backupWorld(id, json) { const t = Date.now(); stmt.backup.run(id, t, json); stmt.trimBackups.run(id, id, 24); }
export const listBackups = () => stmt.listBackups.all();
export const getBackup = (id, saved) => stmt.getBackup.get(id, saved)?.json || null;
/** 저장된 월드들 (JSON 문자열). 처음이면 예전 JSON 파일을 가져온다 */
export function loadWorldJsons() {
  const rows = stmt.allWorlds.all();
  if (!rows.length) {
    for (const f of readdirSync(DATA_DIR)) {
      if (!f.endsWith('.json')) continue;
      try {
        const json = readFileSync(join(DATA_DIR, f), 'utf8');
        const id = JSON.parse(json).id || f.replace(/\.json$/, '');
        saveWorldJson(id, json);
        renameSync(join(DATA_DIR, f), join(DATA_DIR, f + '.imported'));
        rows.push({ id, json });
        console.log('예전 월드 파일을 DB로 옮겼습니다:', f);
      } catch (e) { console.error('월드 파일 가져오기 실패', f, e.message); }
    }
  }
  return rows;
}

// ── 계정 ──
const hashPw = (pw, salt = randomBytes(16).toString('hex')) => `${salt}:${scryptSync(pw, salt, 64).toString('hex')}`;
const checkPw = (pw, stored) => {
  const [salt, h] = String(stored).split(':');
  if (!salt || !h) return false;
  const a = Buffer.from(h, 'hex'), b = scryptSync(pw, salt, 64);
  return a.length === b.length && timingSafeEqual(a, b);
};
const q = {
  byName: db.prepare('SELECT * FROM accounts WHERE username = ?'),
  byId: db.prepare('SELECT * FROM accounts WHERE id = ?'),
  insert: db.prepare('INSERT INTO accounts (username, pass, created, last_seen, admin, google_sub, apple_sub) VALUES (?, ?, ?, ?, ?, ?, ?)'),
  byGoogle: db.prepare('SELECT * FROM accounts WHERE google_sub = ?'),
  setGoogle: db.prepare('UPDATE accounts SET google_sub = ? WHERE id = ?'),
  byApple: db.prepare('SELECT * FROM accounts WHERE apple_sub = ?'),
  setApple: db.prepare('UPDATE accounts SET apple_sub = ? WHERE id = ?'),
  count: db.prepare('SELECT COUNT(*) AS n FROM accounts'),
  setAdmin: db.prepare('UPDATE accounts SET admin = ? WHERE id = ?'),
  setBan: db.prepare('UPDATE accounts SET banned = ? WHERE id = ?'),
  seen: db.prepare('UPDATE accounts SET last_seen = ? WHERE id = ?'),
  prefs: db.prepare('UPDATE accounts SET prefs = ? WHERE id = ?'),
  list: db.prepare("SELECT id, username, created, last_seen, admin, banned, google_sub IS NOT NULL AS google, apple_sub IS NOT NULL AS apple, pass != '' AS haspw FROM accounts ORDER BY id DESC LIMIT 200"),
  newSession: db.prepare('INSERT INTO sessions (token, account, created, last) VALUES (?, ?, ?, ?)'),
  session: db.prepare('SELECT s.token, s.last, a.* FROM sessions s JOIN accounts a ON a.id = s.account WHERE s.token = ?'),
  touch: db.prepare('UPDATE sessions SET last = ? WHERE token = ?'),
  endSession: db.prepare('DELETE FROM sessions WHERE token = ?'),
  endSessionsOf: db.prepare('DELETE FROM sessions WHERE account = ?'),
  setPass: db.prepare('UPDATE accounts SET pass = ? WHERE id = ?'),
  delAccount: db.prepare('DELETE FROM accounts WHERE id = ?'),
  delGamesOf: db.prepare('DELETE FROM games WHERE account = ?'),
  delGamesOfPlayer: db.prepare('DELETE FROM games WHERE server = ? AND player = ?'),
  delPushOf: db.prepare('DELETE FROM push_subs WHERE account = ?'),
  oldSessions: db.prepare('DELETE FROM sessions WHERE last < ?'),
  addGame: db.prepare('INSERT OR IGNORE INTO games (account, server, player, ptoken, created) VALUES (?, ?, ?, ?, ?)'),
  games: db.prepare('SELECT * FROM games WHERE account = ? ORDER BY created DESC'),
  gameByServer: db.prepare('SELECT * FROM games WHERE account = ? AND server = ?'),
  seenClock: db.prepare('UPDATE games SET seen_clock = ? WHERE account = ? AND server = ? AND player = ?'),
  delGamesOfServer: db.prepare('DELETE FROM games WHERE server = ?'),
  ownerOfPlayer: db.prepare('SELECT account FROM games WHERE server = ? AND player = ?'),
};
export const SESSION_DAYS = 60;
const adminNames = () => String(process.env.ADMIN_USERS || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
/** 관리자: ADMIN_USERS 환경 변수에 적힌 아이디. 비어 있으면 처음 가입한 계정 */
export const isAdminName = name => adminNames().includes(String(name).toLowerCase());
/** 계정 만들기. 구글·애플로만 가입하면 비밀번호 칸은 비어 있다 (비밀번호로는 로그인할 수 없음). social = { kind: 'google'|'apple', sub } */
export function createAccount(username, password, social = null) {
  const first = q.count.get().n === 0;
  const admin = isAdminName(username) || (first && !adminNames().length) ? 1 : 0;
  const r = q.insert.run(username, password ? hashPw(password) : '', Date.now(), Date.now(), admin, social?.kind === 'google' ? social.sub : null, social?.kind === 'apple' ? social.sub : null);
  return q.byId.get(r.lastInsertRowid);
}
export const accountByGoogle = sub => q.byGoogle.get(String(sub));
export const setGoogle = (id, sub) => q.setGoogle.run(sub, id);
export const accountBySocial = (kind, sub) => (kind === 'apple' ? q.byApple : q.byGoogle).get(String(sub));
export const setSocial = (kind, id, sub) => (kind === 'apple' ? q.setApple : q.setGoogle).run(sub, id);
export const accountByName = name => q.byName.get(name);
export const accountById = id => q.byId.get(id);
export const verifyPassword = (acc, pw) => checkPw(pw, acc.pass);
export function newSession(accountId) {
  const token = randomBytes(32).toString('base64url');
  q.newSession.run(token, accountId, Date.now(), Date.now());
  return token;
}
export function sessionAccount(token) {
  if (!token) return null;
  const row = q.session.get(token);
  if (!row) return null;
  if (Date.now() - row.last > SESSION_DAYS * 864e5) { q.endSession.run(token); return null; }
  if (Date.now() - row.last > 6e5) { q.touch.run(Date.now(), token); q.seen.run(Date.now(), row.id); }
  return row;
}
export const endSession = token => q.endSession.run(token);
export const endSessionsOf = id => q.endSessionsOf.run(id);
export function changePassword(id, pw) { q.setPass.run(hashPw(pw), id); }
/** 탈퇴: 계정, 로그인 세션, 게임 연결, 푸시 구독을 모두 지운다 (신고 기록의 신고자 번호는 비운다) */
export function deleteAccount(id) {
  q.endSessionsOf.run(id); q.delGamesOf.run(id); q.delPushOf.run(id); q.delAccount.run(id);
  db.prepare('UPDATE reports SET reporter = NULL WHERE reporter = ?').run(id);
}
export const deleteGamesOfPlayer = (server, player) => q.delGamesOfPlayer.run(server, player);
/** 오래된 로그인 기록(60일)과 처리 끝난 신고(1년)를 지운다 — 개인정보 처리방침의 보유 기간 */
export function cleanupSessions() {
  q.oldSessions.run(Date.now() - SESSION_DAYS * 864e5);
  db.prepare("DELETE FROM reports WHERE status != 'open' AND created < ?").run(Date.now() - 365 * 864e5);
}
export const setAdmin = (id, v) => q.setAdmin.run(v ? 1 : 0, id);
export const setBanned = (id, v) => q.setBan.run(v ? 1 : 0, id);
export const listAccounts = () => q.list.all();
export const accountCount = () => q.count.get().n;
export function setPrefs(id, prefs) { q.prefs.run(JSON.stringify(prefs).slice(0, 4000), id); }
export const addGame = (account, server, player, ptoken) => q.addGame.run(account, server, player, ptoken, Date.now());
export const gamesOf = account => q.games.all(account);
export const gameOn = (account, server) => q.gameByServer.all(account, server);
export const setSeenClock = (account, server, player, clock) => q.seenClock.run(clock, account, server, player);
export const deleteGamesOfServer = server => q.delGamesOfServer.run(server);
export const ownerOfPlayer = (server, player) => q.ownerOfPlayer.get(server, player)?.account ?? null;

// ── 신고 ──
const r = {
  add: db.prepare('INSERT INTO reports (created, reporter, server, target_kind, target_id, target_name, reason) VALUES (?, ?, ?, ?, ?, ?, ?)'),
  list: db.prepare("SELECT * FROM reports ORDER BY status = 'open' DESC, id DESC LIMIT 200"),
  set: db.prepare('UPDATE reports SET status = ? WHERE id = ?'),
  openCount: db.prepare("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'"),
};
export const addReport = (reporter, server, kind, id, name, reason) => r.add.run(Date.now(), reporter, server, kind, String(id), String(name).slice(0, 60), String(reason).slice(0, 300));
export const listReports = () => r.list.all();
export const setReportStatus = (id, status) => r.set.run(status, id);
export const openReports = () => r.openCount.get().n;

// ── 푸시 구독 · 설정 ──
const p = {
  add: db.prepare('INSERT INTO push_subs (endpoint, account, json, created) VALUES (?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET account = excluded.account, json = excluded.json'),
  del: db.prepare('DELETE FROM push_subs WHERE endpoint = ?'),
  of: db.prepare('SELECT * FROM push_subs WHERE account = ?'),
  getKv: db.prepare('SELECT v FROM kv WHERE k = ?'),
  setKv: db.prepare('INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v'),
  err: db.prepare('INSERT INTO errors (t, where_, msg) VALUES (?, ?, ?)'),
  errs: db.prepare('SELECT * FROM errors ORDER BY id DESC LIMIT 100'),
  trimErr: db.prepare('DELETE FROM errors WHERE id < (SELECT MAX(id) - 1000 FROM errors)'),
};
export const addPushSub = (account, sub) => p.add.run(sub.endpoint, account, JSON.stringify(sub), Date.now());
export const removePushSub = endpoint => p.del.run(endpoint);
export const pushSubsOf = account => p.of.all(account).map(r => JSON.parse(r.json));
export const kvGet = k => p.getKv.get(k)?.v ?? null;
export const kvSet = (k, v) => p.setKv.run(k, String(v));
export function logError(where, err) { try { p.err.run(Date.now(), where, String(err?.stack || err).slice(0, 2000)); p.trimErr.run(); } catch { /* */ } }
export const recentErrors = () => p.errs.all();
export const dbPath = join(DATA_DIR, 'earth.db');
