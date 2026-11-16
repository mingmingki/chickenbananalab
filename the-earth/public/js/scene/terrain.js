// 실제 지형: 보고 있는 곳 둘레의 실제 고도 자료(위성 측량 SRTM·해저 지형 ETOPO 등을 모은 공개 타일)를 받아
// 한 장의 그림으로 이어 붙인다. 지구 셰이더(earth.js)는 이 그림으로 진짜 산줄기·골짜기·섬 많은 해안·바다 밑 지형을 그린다.
//   · 두 겹: 가운데는 촘촘한 그림(fine), 그 둘레 네 배 넓이는 성긴 그림(coarse). 그 밖은 지구 전체 그림(2K)
//   · 타일 한 장 = 256칸, 칸마다 R·G 에 높이(terrarium: R×256 + G − 32768 m), B 에 땅인지(번지게), A 에 자료가 있는지
//   · 높이는 R·G 에 나눠 담겨 있지만 섞는 셈이 일차식이라, 그래픽 카드가 이웃 칸을 섞어도(선형 보간) 값이 맞다
import * as THREE from 'three';
import { serverUrl } from '../net.js';
import { PHONE } from '../device.js';

const TS = 256;
const D2R = Math.PI / 180;
const clamp = THREE.MathUtils.clamp;
export const mercY = lat => { const r = clamp(lat, -85.05, 85.05) * D2R; return 0.5 - Math.log(Math.tan(Math.PI / 4 + r / 2)) / (2 * Math.PI); };

class Level {
  constructor(N) {
    this.N = N; this.W = N * TS;
    this.tex = new THREE.DataTexture(new Uint8Array(this.W * this.W * 4), this.W, this.W, THREE.RGBAFormat, THREE.UnsignedByteType);
    const t = this.tex;
    t.minFilter = t.magFilter = THREE.LinearFilter; t.generateMipmaps = false;
    t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping; t.colorSpace = THREE.NoColorSpace; t.flipY = false; t.unpackAlignment = 4;
    t.needsUpdate = true;
    this.box = new THREE.Vector4(0, 0, 0, 0);   // (x0, y0, 너비, 높이) — 메르카토르 0~1
    this.shown = '';                            // 지금 보여 주는 칸 (z/x0/y0)
    this.want = null;
  }
}

export class TerrainDetail {
  constructor(uniforms) {
    this.U = uniforms;
    const N = PHONE ? 4 : 5;
    this.fine = new Level(N);
    this.coarse = new Level(N);
    uniforms.detF.value = this.fine.tex; uniforms.boxF.value = this.fine.box; uniforms.sizeF.value = this.fine.W;
    uniforms.detC.value = this.coarse.tex; uniforms.boxC.value = this.coarse.box; uniforms.sizeC.value = this.coarse.W;
    this.cache = new Map();          // 'z/x/y' → Uint8Array(256×256×2: R,G) | 'fail'
    this.loading = new Map();        // 'z/x/y' → Promise
    this.queue = [];
    this.active = 0;
    this.maxCache = PHONE ? 90 : 220;
    this.off = false;                // 서버가 타일을 안 준다 (꺼 둠)
    this.failStreak = 0; this.pauseUntil = 0;
    this.amt = 0;
    this.stable = { key: '', since: 0 };
    this.cv = document.createElement('canvas'); this.cv.width = this.cv.height = TS;
    this.cg = this.cv.getContext('2d', { willReadFrequently: true });
  }

  /** 매 장면: 카메라가 보는 곳(위경도·고도·기울기·방위)에 맞춰 필요한 칸을 정하고 받는다 */
  update(cur, dt) {
    const alt = cur.alt;
    const lon = ((cur.lon + 540) % 360) - 180, lat = cur.lat;
    const on = !this.off && alt < 7000 && Math.abs(lat) < 84;
    this.idle = !on;
    const hasData = this.fine.shown || this.coarse.shown;
    this.amt = clamp(this.amt + (on && hasData ? dt * 2.2 : -dt * 2.2), 0, 1);
    this.U.detAmt.value = this.amt * this.amt * (3 - 2 * this.amt);
    if (!on) return;
    // 비스듬히 볼 때는 앞쪽(멀리)이 더 넓게 보이므로, 칸의 가운데를 앞으로 조금 민다
    const tilt = clamp(cur.tilt, 15, 90) * D2R;
    const fwd = Math.min(alt / Math.tan(tilt) * 0.55, alt * 1.2);
    const hd = cur.heading || 0;
    const cLat = clamp(lat + Math.cos(hd) * fwd / 111.2, -84, 84);
    const cLon = lon + Math.sin(hd) * fwd / (111.2 * Math.max(0.1, Math.cos(lat * D2R)));
    // 그림 한 칸이 화면 한 점쯤 되는 배율
    const cl = Math.cos(cLat * D2R);
    const per = alt / (PHONE ? 550 : 800);
    const zf = Math.log2(40075 * cl / (TS * per));
    let z = this.fine.want?.z ?? -1;
    if (z < 0 || Math.abs(zf - z) > 0.75) z = Math.round(zf);
    z = clamp(z, 4, 12);
    const mx = (cLon + 180) / 360, my = mercY(cLat);
    const fw = this.pick(this.fine, z, mx, my);
    const cw = z - 2 >= 3 ? this.pick(this.coarse, z - 2, mx, my) : null;
    if (!cw && this.coarse.shown) { this.coarse.shown = ''; this.coarse.want = null; this.coarse.box.set(0, 0, 0, 0); }
    // 잠깐 멈춰 있을 때만 받는다 (빠르게 확대·이동하는 중에는 중간 배율 타일을 받지 않는다)
    this.idle = this.fine.shown === fw.key && (!cw || this.coarse.shown === cw.key);   // 필요한 그림이 다 들어와 있다
    const key = fw.key + '|' + (cw?.key || '');
    const now = performance.now();
    if (key !== this.stable.key) { this.stable = { key, since: now }; return; }
    if (now - this.stable.since < 220 || now < this.pauseUntil) return;
    for (const [lv, w] of [[this.fine, fw], [this.coarse, cw]]) {
      if (!w || lv.shown === w.key || lv.want?.key === w.key && lv.want.asked) continue;
      lv.want = w; w.asked = true;
      this.request(lv, w);
    }
  }

  /** 이 배율에서 (mx, my)를 가운데에 두는 N×N 칸. 지금 칸의 가운데 영역 안이면 그대로 둔다 */
  pick(lv, z, mx, my) {
    const n = 2 ** z, N = lv.N;   // (z ≥ 3 이라 언제나 n ≥ N)
    const tx = Math.floor(mx * n), ty = clamp(Math.floor(my * n), 0, n - 1);
    const cur = lv.want;
    if (cur && cur.z === z) {
      const dx = ((tx - cur.x0) % n + n) % n;
      if (dx >= 1 && dx <= N - 2 && ty >= cur.y0 + (cur.y0 > 0 ? 1 : 0) && ty <= cur.y0 + N - 1 - (cur.y0 + N < n ? 1 : 0)) return cur;
    }
    const x0 = ((tx - Math.floor(N / 2)) % n + n) % n;
    const y0 = clamp(ty - Math.floor(N / 2), 0, n - N);
    return { z, x0, y0, N, key: `${z}/${x0}/${y0}` };
  }

  request(lv, w) {
    const n = 2 ** w.z, keys = [];
    const mid = (w.N - 1) / 2;
    for (let j = 0; j < w.N; j++) for (let i = 0; i < w.N; i++) keys.push([Math.hypot(i - mid, j - mid), `${w.z}/${(w.x0 + i) % n}/${w.y0 + j}`]);
    keys.sort((a, b) => a[0] - b[0]);   // 가운데 칸부터
    Promise.all(keys.map(([, k]) => this.tile(k, lv === this.fine))).then(() => {
      if (lv.want !== w) return;          // 그 사이 다른 곳으로 옮겼다
      this.compose(lv, w);
    });
  }

  tile(k, urgent) {
    if (this.cache.has(k)) { const v = this.cache.get(k); this.cache.delete(k); this.cache.set(k, v); return Promise.resolve(v); }
    if (this.loading.has(k)) return this.loading.get(k);
    const p = new Promise(res => { (urgent ? this.queue.unshift.bind(this.queue) : this.queue.push.bind(this.queue))({ k, res }); this.pump(); });
    this.loading.set(k, p);
    p.then(() => this.loading.delete(k));
    return p;
  }

  pump() {
    while (this.active < (PHONE ? 4 : 6) && this.queue.length) {
      const { k, res } = this.queue.shift();
      this.active++;
      this.fetchTile(k).then(v => {
        this.cache.set(k, v);
        while (this.cache.size > this.maxCache) this.cache.delete(this.cache.keys().next().value);
        res(v);
      }).finally(() => { this.active--; this.pump(); });
    }
  }

  async fetchTile(k) {
    try {
      const r = await fetch(serverUrl(`/tiles/terrain/${k}.png`));
      if (r.status === 404) { this.off = true; return 'fail'; }
      if (!r.ok) throw new Error('tile ' + r.status);
      const blob = await r.blob();
      let img;
      try { img = await createImageBitmap(blob, { colorSpaceConversion: 'none', premultiplyAlpha: 'none' }); }
      catch { img = await new Promise((ok, no) => { const im = new Image(); im.onload = () => ok(im); im.onerror = no; im.src = URL.createObjectURL(blob); }); }
      this.cg.clearRect(0, 0, TS, TS);
      this.cg.drawImage(img, 0, 0);
      img.close?.();
      const d = this.cg.getImageData(0, 0, TS, TS).data;
      const out = new Uint8Array(TS * TS * 2);
      for (let i = 0, o = 0; i < d.length; i += 4, o += 2) { out[o] = d[i]; out[o + 1] = d[i + 1]; }
      this.failStreak = 0;
      return out;
    } catch {
      // 계속 실패하면 잠깐 쉬었다가 (서버가 바깥 자료를 못 받는 중)
      if (++this.failStreak > 8) { this.pauseUntil = performance.now() + 30_000; this.failStreak = 0; }
      return 'fail';
    }
  }

  /** 받은 타일들을 한 장으로 잇고, 땅인 곳(높이 > 0)을 번지게 칠한다 */
  compose(lv, w) {
    const W = lv.W, n = 2 ** w.z, N = w.N;
    const px = new Uint8Array(W * W * 4);
    const land = new Uint8Array(W * W);
    let any = false;
    for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
      const t = this.cache.get(`${w.z}/${(w.x0 + i) % n}/${w.y0 + j}`);
      if (!t || t === 'fail') continue;
      any = true;
      for (let y = 0; y < TS; y++) {
        let o = ((j * TS + y) * W + i * TS) * 4, s = y * TS * 2, l = (j * TS + y) * W + i * TS;
        for (let x = 0; x < TS; x++, o += 4, s += 2, l++) {
          const r = t[s], g = t[s + 1];
          px[o] = r; px[o + 1] = g; px[o + 3] = 255;
          land[l] = r * 256 + g > 32768 ? 255 : 0;   // 0m 보다 높으면 땅
        }
      }
    }
    if (!any) return;
    // 땅 표시를 번지게 (바다=0m 와 낮은 땅이 점점이 섞인 해안이 매끈한 선이 되게).
    // 아주 가까운 배율(z10~)은 해안의 점무늬가 심해 5×5 로 — 외딴 점 하나는 반도 안 되어 바다가 된다
    const r = w.z >= 10 ? 2 : 1, k = (2 * r + 1) ** 2;
    const tmp = new Uint16Array(W * W);
    for (let y = 0; y < W; y++) {
      const row = y * W;
      for (let x = 0; x < W; x++) {
        let sum = 0;
        for (let d = -r; d <= r; d++) sum += land[row + clamp(x + d, 0, W - 1)];
        tmp[row + x] = sum;
      }
    }
    for (let y = 0; y < W; y++) {
      const b = y * W;
      for (let x = 0; x < W; x++) {
        let sum = 0;
        for (let d = -r; d <= r; d++) sum += tmp[clamp(y + d, 0, W - 1) * W + x];
        px[(b + x) * 4 + 2] = sum / k + 0.5 | 0;
      }
    }
    lv.tex.image.data = px;
    lv.tex.needsUpdate = true;
    lv.box.set(w.x0 / n, w.y0 / n, N / n, N / n);
    lv.shown = w.key;
  }
}
