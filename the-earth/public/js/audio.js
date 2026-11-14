// 소리: 파일 없이 웹오디오로 그 자리에서 만든다.
//  - 환경음: 바람, 비·천둥, 낮의 새소리, 밤의 풀벌레, 마을 소음(시대가 오를수록 도시답게), 우주의 낮은 울림
//  - 음악: 시간·시대에 따라 화음이 바뀌는 잔잔한 생성 음악
//  - 효과음: 버튼, 투입, 탄생, 결혼, 죽음, 업적, 로켓
// 브라우저는 사용자가 한 번 누른 다음에야 소리를 낼 수 있어서, 첫 터치 때 깨운다.
const KEY = 'theEarth.sound.v1';
export const DEFAULT_PREFS = { on: true, master: 0.8, music: 0.45, amb: 0.7, sfx: 0.8 };

const mtof = m => 440 * 2 ** ((m - 69) / 12);
const clamp01 = v => Math.max(0, Math.min(1, v));

class Sound {
  constructor() {
    this.prefs = { ...DEFAULT_PREFS };
    try { Object.assign(this.prefs, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* */ }
    this.ctx = null;
    this.sense = null;
    this.nextBird = 0; this.nextCricket = 0; this.nextNote = 0; this.nextChord = 0; this.nextCar = 0;
    this.chordIdx = 0;
    const unlock = () => { this.start(); if (this.ctx?.state === 'running') { removeEventListener('pointerdown', unlock, true); removeEventListener('keydown', unlock, true); } };
    addEventListener('pointerdown', unlock, true);
    addEventListener('keydown', unlock, true);
    document.addEventListener('visibilitychange', () => {
      if (!this.ctx) return;
      if (document.hidden) this.ctx.suspend(); else if (this.prefs.on) this.ctx.resume();
    });
  }

  save() { try { localStorage.setItem(KEY, JSON.stringify(this.prefs)); } catch { /* */ } }
  set(p) { Object.assign(this.prefs, p); this.save(); this.applyGains(); if (this.prefs.on) this.start(); }

  start() {
    if (!this.prefs.on) return;
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      try { this.ctx = new AC(); } catch { return; }
      this.build();
    }
    if (this.ctx.state !== 'running') this.ctx.resume().catch(() => {});
  }

  build() {
    const c = this.ctx;
    this.master = c.createGain(); this.master.connect(c.destination);
    // 잔향 대신 가벼운 메아리
    this.delay = c.createDelay(1.5); this.delay.delayTime.value = 0.42;
    const fb = c.createGain(); fb.gain.value = 0.32;
    const dl = c.createBiquadFilter(); dl.type = 'lowpass'; dl.frequency.value = 2200;
    this.delay.connect(dl); dl.connect(fb); fb.connect(this.delay); dl.connect(this.master);
    this.bus = {};
    for (const k of ['music', 'amb', 'sfx']) { this.bus[k] = c.createGain(); this.bus[k].connect(this.master); }
    this.musicSend = c.createGain(); this.musicSend.gain.value = 0.5; this.musicSend.connect(this.delay);
    // 잡음 원천
    const noise = (kind) => {
      const len = c.sampleRate * 3, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
      let b0 = 0, b1 = 0, b2 = 0, last = 0;
      for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        if (kind === 'white') d[i] = w;
        else if (kind === 'pink') { b0 = 0.997 * b0 + w * 0.029591; b1 = 0.985 * b1 + w * 0.032534; b2 = 0.95 * b2 + w * 0.048056; d[i] = (b0 + b1 + b2 + w * 0.05) * 1.6; }
        else { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; }
      }
      return buf;
    };
    this.noiseBuf = { white: noise('white'), pink: noise('pink'), brown: noise('brown') };
    const loop = (buf, filter, freq, q = 0.7) => {
      const src = c.createBufferSource(); src.buffer = buf; src.loop = true;
      const f = c.createBiquadFilter(); f.type = filter; f.frequency.value = freq; f.Q.value = q;
      const g = c.createGain(); g.gain.value = 0;
      src.connect(f); f.connect(g); g.connect(this.bus.amb); src.start();
      return { f, g };
    };
    this.wind = loop(this.noiseBuf.pink, 'bandpass', 500, 0.6);
    this.rain = loop(this.noiseBuf.white, 'highpass', 900, 0.4);
    this.rainLow = loop(this.noiseBuf.pink, 'lowpass', 700, 0.5);
    this.city = loop(this.noiseBuf.brown, 'lowpass', 260, 0.5);
    // 우주의 낮은 울림
    this.drone = c.createGain(); this.drone.gain.value = 0; this.drone.connect(this.bus.amb);
    for (const [f, t] of [[55, 'sine'], [82.4, 'sine'], [110.6, 'triangle']]) { const o = c.createOscillator(); o.type = t; o.frequency.value = f; const g = c.createGain(); g.gain.value = t === 'triangle' ? 0.08 : 0.25; o.connect(g); g.connect(this.drone); o.start(); }
    this.applyGains();
    this.tick = setInterval(() => this.update(), 120);
  }

  applyGains() {
    if (!this.ctx) return;
    const p = this.prefs, t = this.ctx.currentTime;
    this.master.gain.setTargetAtTime(p.on ? p.master : 0, t, 0.1);
    this.bus.music.gain.setTargetAtTime(p.music * 0.5, t, 0.2);
    this.bus.amb.gain.setTargetAtTime(p.amb, t, 0.2);
    this.bus.sfx.gain.setTargetAtTime(p.sfx * 0.7, t, 0.05);
    if (!p.on) this.ctx.suspend?.();
  }

  /** 장면이 알려 주는 상태: { alt(km), night(0~1), s(가까운 마을), flash, mode } */
  feed(sense) { this.sense = sense; }

  update() {
    const c = this.ctx;
    if (!c || c.state !== 'running' || !this.prefs.on) return;
    const t = c.currentTime, S = this.sense || { alt: 2e4, night: 0, s: null, flash: 0 };
    const alt = S.alt ?? 2e4;
    const near = clamp01((6 - alt) / 5);           // 마을 가까이
    const sky = clamp01((300 - alt) / 250);          // 대기권 안
    const s = S.s, wx = s?.weather?.k || '맑음', season = s?.weather?.season || '봄';
    const rainy = /비|소나기|폭우|태풍/.test(wx), stormy = /폭우|태풍/.test(wx), snowy = /눈|폭설/.test(wx);
    const windy = /태풍|폭설|바람|한파/.test(wx);
    const set = (param, v, tc = 0.8) => param.setTargetAtTime(v, t, tc);
    // 바람: 높이 올라갈수록, 날씨가 거칠수록
    const wAmt = sky * (0.05 + clamp01(alt / 60) * 0.12 + (windy ? 0.25 : 0) + (snowy ? 0.08 : 0));
    set(this.wind.g.gain, wAmt);
    set(this.wind.f.frequency, 350 + Math.sin(t * 0.13) * 160 + Math.sin(t * 0.37) * 80, 1.5);
    // 비
    const rAmt = rainy ? near * (stormy ? 0.55 : 0.3) : 0;
    set(this.rain.g.gain, rAmt * 0.6); set(this.rainLow.g.gain, rAmt * 0.8);
    // 마을 소음: 마을 크기와 시대
    const era = S.era ?? 0, lvl = s?.level ?? 0;
    const cAmt = s ? near * Math.min(1, 0.05 + lvl * 0.06) * (S.night > 0.5 ? 0.5 : 1) * (era >= 5 ? 1.2 : 0.6) : 0;
    set(this.city.g.gain, cAmt * 0.5);
    set(this.city.f.frequency, era >= 5 ? 420 : 220, 2);
    // 우주의 울림
    set(this.drone.gain, clamp01((alt - 800) / 4000) * 0.12, 2);
    // 천둥
    if (S.flash > 0.9 && near > 0.3 && t > (this.lastThunder || 0) + 2) { this.lastThunder = t; setTimeout(() => this.thunder(), 300 + Math.random() * 1500); }
    // 새: 낮, 가까이, 비·눈 아닐 때, 겨울엔 드물게
    if (near > 0.2 && S.night < 0.4 && !rainy && !snowy && t > this.nextBird) {
      this.nextBird = t + (season === '겨울' ? 6 : 1.2) + Math.random() * 4;
      this.bird(near);
    }
    // 풀벌레: 여름·가을 밤
    if (near > 0.2 && S.night > 0.6 && !rainy && (season === '여름' || season === '가을') && t > this.nextCricket) {
      this.nextCricket = t + 0.6 + Math.random() * 1.2;
      this.cricket(near);
    }
    // 자동차: 산업 시대 이후의 낮
    if (near > 0.4 && era >= 5 && lvl >= 2 && S.night < 0.7 && t > this.nextCar) { this.nextCar = t + 3 + Math.random() * 7; this.car(near); }
    this.music(t, S, era);
  }

  // ── 음악 ──
  music(t, S, era) {
    if (this.prefs.music <= 0.001) return;
    if (t < this.nextChord) { if (t > this.nextNote) this.melody(t, S, era); return; }
    const night = (S.night || 0) > 0.5;
    // 낮: 밝은 진행, 밤: 단조풍
    const DAY = [[48, 55, 64, 67], [45, 52, 60, 64], [41, 48, 57, 64], [43, 50, 59, 62]];
    const NIGHT = [[45, 52, 60, 64], [41, 48, 57, 60], [43, 50, 55, 62], [40, 47, 55, 59]];
    const prog = night ? NIGHT : DAY;
    const chord = prog[this.chordIdx++ % prog.length];
    this.curChord = chord;
    const dur = 9;
    this.nextChord = t + dur - 0.5;
    this.nextNote = t + 1.5;
    const c = this.ctx;
    for (const m of chord) {
      const o = c.createOscillator(), g = c.createGain(), f = c.createBiquadFilter();
      o.type = era <= 2 ? 'sine' : 'triangle';
      o.frequency.value = mtof(m); o.detune.value = (Math.random() - 0.5) * 8;
      f.type = 'lowpass'; f.frequency.value = night ? 700 : 1100;
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.05, t + 2.5);
      g.gain.setValueAtTime(0.05, t + dur - 3);
      g.gain.linearRampToValueAtTime(0, t + dur + 1);
      o.connect(f); f.connect(g); g.connect(this.bus.music);
      o.start(t); o.stop(t + dur + 1.2);
    }
  }
  melody(t, S, era) {
    this.nextNote = t + 1.4 + Math.random() * 2.6;
    if (Math.random() < 0.3 || !this.curChord) return;
    const scale = [0, 2, 4, 7, 9]; // 오음음계
    const root = this.curChord[0] + 24;
    const m = root + scale[Math.floor(Math.random() * scale.length)] + (Math.random() < 0.3 ? 12 : 0);
    const c = this.ctx, o = c.createOscillator(), g = c.createGain();
    if (era >= 5) {
      // 현대: 부드러운 전자 피아노 (FM)
      const mod = c.createOscillator(), mg = c.createGain();
      mod.frequency.value = mtof(m) * 2; mg.gain.setValueAtTime(mtof(m) * 1.5, t); mg.gain.exponentialRampToValueAtTime(1, t + 1.2);
      mod.connect(mg); mg.connect(o.frequency); mod.start(t); mod.stop(t + 3);
    } else {
      // 옛 시대: 비브라토가 있는 피리 소리
      const lfo = c.createOscillator(), lg = c.createGain(); lfo.frequency.value = 5; lg.gain.value = 3;
      lfo.connect(lg); lg.connect(o.detune); lfo.start(t); lfo.stop(t + 3);
    }
    o.type = 'sine'; o.frequency.value = mtof(m);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(era >= 5 ? 0.07 : 0.045, t + (era >= 5 ? 0.01 : 0.15));
    g.gain.exponentialRampToValueAtTime(0.0005, t + 2.6);
    o.connect(g); g.connect(this.bus.music); g.connect(this.musicSend);
    o.start(t); o.stop(t + 2.8);
  }

  // ── 환경음 조각 ──
  bird(v) {
    const c = this.ctx, t = c.currentTime, n = 2 + Math.floor(Math.random() * 4), base = 2600 + Math.random() * 1800;
    for (let i = 0; i < n; i++) {
      const o = c.createOscillator(), g = c.createGain(), st = t + i * (0.09 + Math.random() * 0.06);
      o.type = 'sine';
      o.frequency.setValueAtTime(base * (0.9 + Math.random() * 0.2), st);
      o.frequency.exponentialRampToValueAtTime(base * (1.2 + Math.random() * 0.5), st + 0.05);
      o.frequency.exponentialRampToValueAtTime(base * 0.8, st + 0.09);
      g.gain.setValueAtTime(0, st); g.gain.linearRampToValueAtTime(0.03 * v, st + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, st + 0.1);
      o.connect(g); g.connect(this.bus.amb); o.start(st); o.stop(st + 0.12);
    }
  }
  cricket(v) {
    const c = this.ctx, t = c.currentTime, f = 4200 + Math.random() * 600;
    for (let i = 0; i < 3; i++) {
      const o = c.createOscillator(), g = c.createGain(), st = t + i * 0.055;
      o.type = 'sine'; o.frequency.value = f;
      g.gain.setValueAtTime(0, st); g.gain.linearRampToValueAtTime(0.012 * v, st + 0.008); g.gain.linearRampToValueAtTime(0, st + 0.035);
      o.connect(g); g.connect(this.bus.amb); o.start(st); o.stop(st + 0.05);
    }
  }
  car(v) {
    const c = this.ctx, t = c.currentTime;
    const src = c.createBufferSource(); src.buffer = this.noiseBuf.pink;
    const f = c.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 1.2;
    f.frequency.setValueAtTime(300, t); f.frequency.linearRampToValueAtTime(900, t + 1.2); f.frequency.linearRampToValueAtTime(250, t + 2.6);
    const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.05 * v, t + 1.2); g.gain.linearRampToValueAtTime(0, t + 2.8);
    src.connect(f); f.connect(g); g.connect(this.bus.amb); src.start(t, Math.random()); src.stop(t + 3);
  }
  thunder() {
    if (!this.ctx) return;
    const c = this.ctx, t = c.currentTime;
    const src = c.createBufferSource(); src.buffer = this.noiseBuf.brown;
    const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.setValueAtTime(900, t); f.frequency.exponentialRampToValueAtTime(90, t + 3);
    const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.7, t + 0.05); g.gain.exponentialRampToValueAtTime(0.001, t + 4);
    src.connect(f); f.connect(g); g.connect(this.bus.amb); src.start(t, Math.random()); src.stop(t + 4.2);
  }

  // ── 효과음 ──
  tone(freqs, { type = 'sine', gap = 0.09, len = 0.5, vol = 0.18, echo = false } = {}) {
    if (!this.ctx || this.ctx.state !== 'running' || !this.prefs.on) return;
    const c = this.ctx, t = c.currentTime;
    freqs.forEach((f, i) => {
      const o = c.createOscillator(), g = c.createGain(), st = t + i * gap;
      o.type = type; o.frequency.value = f;
      g.gain.setValueAtTime(0, st); g.gain.linearRampToValueAtTime(vol, st + 0.01); g.gain.exponentialRampToValueAtTime(0.0005, st + len);
      o.connect(g); g.connect(this.bus.sfx); if (echo) g.connect(this.delay);
      o.start(st); o.stop(st + len + 0.05);
    });
  }
  sfx(kind) {
    const N = m => mtof(m);
    switch (kind) {
      case 'click': return this.tone([N(84)], { type: 'triangle', len: 0.06, vol: 0.06 });
      case 'open': return this.tone([N(76), N(83)], { gap: 0.05, len: 0.12, vol: 0.06 });
      case 'deploy': return this.tone([N(60), N(64), N(67), N(72), N(76)], { gap: 0.11, len: 0.8, vol: 0.14, echo: true });
      case 'birth': return this.tone([N(84), N(88), N(91)], { gap: 0.14, len: 1.2, vol: 0.1, echo: true });
      case 'wedding': return this.tone([N(79), N(83), N(86), N(91)], { gap: 0.18, len: 1.4, vol: 0.1, echo: true });
      case 'death': return this.tone([N(57), N(53)], { gap: 0.6, len: 2.2, vol: 0.09, type: 'sine', echo: true });
      case 'ach': return this.tone([N(72), N(76), N(79), N(84), N(88)], { gap: 0.08, len: 0.9, vol: 0.12, type: 'triangle', echo: true });
      case 'note': return this.tone([N(81), N(88)], { gap: 0.08, len: 0.5, vol: 0.08, echo: true });
      case 'ask': return this.tone([N(76), N(81), N(79)], { gap: 0.16, len: 0.7, vol: 0.09, type: 'triangle', echo: true });
      case 'rocket': {
        if (!this.ctx || this.ctx.state !== 'running') return;
        const c = this.ctx, t = c.currentTime, src = c.createBufferSource(); src.buffer = this.noiseBuf.brown;
        const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.setValueAtTime(200, t); f.frequency.linearRampToValueAtTime(1200, t + 2); f.frequency.linearRampToValueAtTime(150, t + 6);
        const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.5, t + 0.6); g.gain.linearRampToValueAtTime(0, t + 6);
        src.connect(f); f.connect(g); g.connect(this.bus.sfx); src.start(t); src.stop(t + 6.2);
      }
    }
  }
  /** 생애 사건 아이콘에 맞는 소리 */
  forIcon(icon) {
    const k = { '👶': 'birth', '💍': 'wedding', '🕯️': 'death', '🏆': 'ach', '🚀': 'rocket', '👑': 'ach' }[icon] || 'note';
    this.sfx(k);
  }
}

export const sound = new Sound();
