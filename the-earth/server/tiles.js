// 지형 높이 타일: 전 세계 실제 고도 자료(AWS 공개 자료 'Terrain Tiles', terrarium 형식)를 받아 와 디스크에 보관하고 나눠 준다.
// 휴대폰은 우리 서버하고만 이야기한다 (밖의 서버로 바로 가지 않는다). 한 번 받은 타일은 다시 받지 않는다.
//   GET /tiles/terrain/{z}/{x}/{y}.png   (z 0~12)
// 환경 변수: TERRAIN_TILES=off 로 끄기, TERRAIN_TILES_URL 로 다른 곳에서 받기, TILE_CACHE_MB 보관 한도(기본 800MB)
import { mkdirSync, readFileSync, writeFileSync, renameSync, statSync, readdirSync, unlinkSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { inflate, deflate } from 'node:zlib';
import { promisify } from 'node:util';
const inflateA = promisify(inflate), deflateA = promisify(deflate);

const UPSTREAM = process.env.TERRAIN_TILES_URL || 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const MAX_Z = 12;
const LIMIT = (+process.env.TILE_CACHE_MB || 800) * 1024 * 1024;
const MAX_INFLIGHT = 8;

export function terrainTiles(app, dataDir) {
  if (process.env.TERRAIN_TILES === 'off') {
    app.get('/tiles/terrain/:z/:x/:y.png', (req, res) => res.status(404).end());
    return;
  }
  const root = join(dataDir, 'tiles', 'terrarium');
  mkdirSync(root, { recursive: true });
  const inflight = new Map();          // 같은 타일을 여러 사람이 동시에 달라 해도 한 번만 받는다
  let active = 0;
  const waiting = [];
  const slot = () => new Promise(r => { if (active < MAX_INFLIGHT) { active++; r(); } else waiting.push(r); });
  const release = () => { const n = waiting.shift(); if (n) n(); else active--; };
  let fails = 0, quietUntil = 0;       // 바깥 서버가 계속 안 되면 잠깐 쉬었다가 다시 시도한다

  const fetchTile = async (z, x, y, file) => {
    await slot();
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 20_000);
      const url = UPSTREAM.replace('{z}', z).replace('{x}', x).replace('{y}', y);
      const r = await fetch(url, { signal: ctl.signal }).finally(() => clearTimeout(timer));
      if (!r.ok) throw new Error('upstream ' + r.status);
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length < 64 || buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not a png');
      const out = await slim(buf);
      mkdirSync(join(root, String(z), String(x)), { recursive: true });
      const tmp = file + '.' + process.pid + '.tmp';
      writeFileSync(tmp, out);
      renameSync(tmp, file);
      fails = 0;
      return out;
    } catch (e) {
      if (++fails >= 12) { quietUntil = Date.now() + 120_000; fails = 0; }
      throw e;
    } finally { release(); }
  };

  app.get('/tiles/terrain/:z/:x/:y.png', async (req, res) => {
    const z = +req.params.z, x = +req.params.x, y = +req.params.y;
    const n = 2 ** z;
    if (!Number.isInteger(z) || !Number.isInteger(x) || !Number.isInteger(y) || z < 0 || z > MAX_Z || x < 0 || y < 0 || x >= n || y >= n) return res.status(400).end();
    const file = join(root, String(z), String(x), y + '.png');
    // 휴대폰 앱(capacitor://localhost)도 받아 갈 수 있게 — 누구에게나 공개된 지형 자료다
    res.set({ 'Cache-Control': 'public, max-age=2592000, immutable', 'Access-Control-Allow-Origin': '*' });
    res.type('png');
    if (existsSync(file)) return res.send(readFileSync(file));
    if (Date.now() < quietUntil) return res.status(503).set('Cache-Control', 'no-store').end();
    const key = `${z}/${x}/${y}`;
    let p = inflight.get(key);
    if (!p) { p = fetchTile(z, x, y, file).finally(() => inflight.delete(key)); inflight.set(key, p); }
    try { res.send(await p); }
    catch { res.status(502).set('Cache-Control', 'no-store').end(); }
  });

  // 보관 한도: 넘으면 오래 전에 받은 것부터 지운다 (켤 때, 그리고 6시간마다)
  const prune = () => {
    try {
      const files = [];
      let total = 0;
      for (const z of readdirSync(root)) for (const x of readdirSync(join(root, z))) for (const f of readdirSync(join(root, z, x))) {
        const p = join(root, z, x, f);
        const s = statSync(p);
        if (f.endsWith('.tmp') && Date.now() - s.mtimeMs > 600_000) { unlinkSync(p); continue; }
        files.push([s.mtimeMs, s.size, p]); total += s.size;
      }
      if (total <= LIMIT) return;
      files.sort((a, b) => a[0] - b[0]);
      // 낮은 배율(z ≤ 6)은 모두가 쓰므로 남긴다
      for (const [, size, p] of files) {
        if (total <= LIMIT * 0.8) break;
        if (/[\\/]([0-6])[\\/]\d+[\\/]\d+\.png$/.test(p)) continue;
        unlinkSync(p); total -= size;
      }
    } catch (e) { console.warn('지형 타일 정리 실패:', e.message); }
  };
  setTimeout(prune, 5_000).unref();
  setInterval(prune, 6 * 3600_000).unref();
}

// ── 타일 줄이기: terrarium 의 B 칸(1/256m 단위 소수점)은 거의 잡음이라 압축이 안 된다. 게임은 1m 단위면 충분하므로
//    B 를 0 으로 바꿔 다시 담는다 (바다가 많은 타일은 1/3 크기로). 모르는 형식이면 그대로 둔다.
const CRC = new Int32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c; });
const crc32 = b => { let c = -1; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; };
const chunk = (type, data) => {
  const head = Buffer.alloc(8); head.writeUInt32BE(data.length, 0); head.write(type, 4, 'latin1');
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
};
export async function slim(buf) {
  try {
    let p = 8, ihdr = null; const idat = [];
    while (p + 8 <= buf.length) {
      const len = buf.readUInt32BE(p), type = buf.toString('latin1', p + 4, p + 8);
      const data = buf.subarray(p + 8, p + 8 + len);
      if (type === 'IHDR') ihdr = data; else if (type === 'IDAT') idat.push(data);
      p += 12 + len;
    }
    if (!ihdr) return buf;
    const w = ihdr.readUInt32BE(0), h = ihdr.readUInt32BE(4);
    if (ihdr[8] !== 8 || ihdr[9] !== 2 || ihdr[12] !== 0 || w > 1024 || h > 1024) return buf;   // 8비트 RGB, 비월 없음만
    const raw = await inflateA(Buffer.concat(idat));
    const bpp = 3, stride = w * bpp;
    if (raw.length < h * (stride + 1)) return buf;
    const img = Buffer.alloc(h * stride);
    for (let y = 0; y < h; y++) {
      const f = raw[y * (stride + 1)], src = y * (stride + 1) + 1, o = y * stride;
      for (let x = 0; x < stride; x++) {
        const a = x >= bpp ? img[o + x - bpp] : 0, b = y ? img[o - stride + x] : 0, c = x >= bpp && y ? img[o - stride + x - bpp] : 0;
        let v = raw[src + x];
        if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
        else if (f === 4) { const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
        img[o + x] = v & 255;
      }
    }
    for (let i = 2; i < img.length; i += 3) img[i] = 0;
    // 줄마다 Paeth 거르기 (지형 그림에선 거의 언제나 가장 잘 줄어든다 — 서버가 오래 붙잡히지 않게 하나만)
    const out = Buffer.alloc(h * (stride + 1)), row = Buffer.alloc(stride);
    for (let y = 0; y < h; y++) {
      const o = y * stride;
      for (let x = 0; x < stride; x++) {
        const a = x >= bpp ? img[o + x - bpp] : 0, b = y ? img[o - stride + x] : 0, c = x >= bpp && y ? img[o - stride + x - bpp] : 0;
        const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c);
        row[x] = (img[o + x] - (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 255;
      }
      out[y * (stride + 1)] = 4; row.copy(out, y * (stride + 1) + 1);
    }
    const res = Buffer.concat([buf.subarray(0, 8), chunk('IHDR', ihdr), chunk('IDAT', await deflateA(out, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
    return res.length < buf.length ? res : buf;
  } catch { return buf; }
}
