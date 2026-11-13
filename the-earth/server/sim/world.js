// 세계: 시계(1년 1월 1일 0시에서 시작), 나라, 정착지, 건물, 회사, 앤, 플레이어
import { onLifeEvent, dailyAchievements, award } from './achieve.js';
import { randomUUID } from 'node:crypto';
import { clamp, rand, pick, chance, gauss, ageOf, dateOf, fmtDate, localHour, YEAR, DAY, ga, neun, reul, wa, ida, fixJosa } from './util.js';
import { ERAS, DISCOVERIES, BUILDINGS, PUBLIC, JOBS, GOALS, HOME_BY_ERA, SETTLEMENT_LEVELS, INDUSTRIES, SCHOOLS, EDU_NAMES, SHIFTS } from './data.js';
import { makeAnn, makeBaby, remember, think, feel, lifeEvent, ageStage, STAGE_NAMES, TRAIT_NAMES, ABILITY_NAMES, deriveValues, deriveGoals } from './ann.js';
import { makeSettlement, placeBuilding, removeBuilding, updateSettlementLevel, plazaPoint, CELL, siteFeatures, ensureSite, buildRule } from './settlement.js';
import { updateAnn, nightlyReview, ACTS, isAvailable } from './mind.js';
import { rel, decayRelations, breakUp, opinionText, updateKind } from './social.js';
import { companyDaily, considerPromotion, jobTitle, leaveJob, foundPublic, dailyCost, wageBase, setJob, assignContractor, siteName, hasConstruction, fullDayPay, annualOf } from './economy.js';
import { childName, givenOf, familyName } from './names.js';
import { runEvents, rollWeather } from './events.js';
import { syncRoads, hallDepts, planRoadWorks, roadProgress, ROAD_GRADES } from './infra.js';
import { safetyDaily, safetyTick, fireOut } from './safety.js';
import { livingDaily, pensionDaily, book, ledgerOut, monthNet } from './budget.js';
import { getCountry, randomLandPoint, seaNear } from '../geo.js';
import { RELATION_NAMES, SKILLS } from './data.js';

const PLAYER_COLORS = ['#ff6b6b', '#4dabf7', '#ffd43b', '#69db7c', '#da77f2', '#ff922b', '#3bc9db', '#f783ac', '#a9e34b', '#9775fa', '#ffa8a8', '#74c0fc'];
export const MAX_ANNS_PER_PLAYER = 12;

export class World {
  constructor(meta = {}) {
    this.id = meta.id || 's1';
    this.name = meta.name || '제1서버';
    this.created = meta.created || Date.now();
    this.timeScale = meta.timeScale || 24;
    this.clock = meta.clock || 0;
    this.nextIdV = meta.nextIdV || 1;
    this.countries = {};
    this.settlements = new Map();
    this.buildings = new Map();
    this.companies = new Map();
    this.anns = new Map();
    this.players = {};
    this.events = [];
    this.chat = [];
    this.nextEventId = 1;
    this.lastDay = Math.floor(this.clock / DAY);
    this.resetDirty();
    this.dfCache = new Map();
    this._bySid = new Map();
    this.residentsStamp = -1;
  }

  resetDirty() {
    this.dirtySettlements = new Set();
    this.dirtyBuildings = new Set();
    this.removedBuildings = new Set();
    this.dirtyCompanies = new Set();
    this.dirtyAnnMeta = new Set();
    this.dirtyAct = new Set();
    this.dirtyDetail = new Set();
    this.dirtyCountries = new Set();
    this.newEvents = [];
    this.newChat = [];
    this.newNotes = [];
  }

  newId() { return this.nextIdV++; }

  // ── 기록 ──
  news(kind, text, refs = {}, imp = 1) {
    const e = { id: this.nextEventId++, t: Math.round(this.clock), kind, text: fixJosa(text), anns: refs.anns || [], country: refs.country || null, sid: refs.sid || null, imp };
    this.events.push(e);
    if (this.events.length > 500) this.events.splice(0, this.events.length - 500);
    this.newEvents.push(e);
    return e;
  }
  /** 플레이어 알림함 (다시 들어왔을 때 요약, 실시간 알림, 푸시) */
  pushNote(p, n) {
    (p.inbox = p.inbox || []).push(n);
    if (p.inbox.length > 150) p.inbox.splice(0, p.inbox.length - 150);
    this.newNotes.push({ pid: p.id, ...n });
  }
  onLifeEvent(a, text, icon) { onLifeEvent(this, a, text, icon); }
  chatter(a, b, line) {
    const c = { t: Math.round(this.clock), a: a.id, b: b.id, sid: a.sid, line: fixJosa(line) };
    this.chat.push(c);
    if (this.chat.length > 120) this.chat.shift();
    this.newChat.push(c);
  }

  // ── 조회 도우미 ──
  residents(sid) {
    if (this.residentsStamp !== this.clock) this.rebuildResidents();
    return this._bySid.get(sid) || [];
  }
  rebuildResidents() {
    this._bySid.clear();
    for (const a of this.anns.values()) {
      if (!a.alive || a.travel) continue;
      let arr = this._bySid.get(a.sid);
      if (!arr) this._bySid.set(a.sid, (arr = []));
      arr.push(a);
    }
    this.residentsStamp = this.clock;
  }
  prod(a) { return ERAS[this.countries[a.country].era].prod; }
  findCivic(s, type) {
    for (const id of s.buildings) { const b = this.buildings.get(id); if (b && b.type === type && b.done) return b; }
    return null;
  }
  findWork(s, type) { return this.findCivic(s, type); }
  /** 주인 없는(공동체·공공) 미완성 건물 */
  communitySite(s) {
    let best = null;
    for (const id of [...s.buildings, ...(s.works || [])]) {
      const b = this.buildings.get(id);
      if (!b || b.done || b.owner != null || (b.infra && b.contractor)) continue;
      if (!best || b.type === 'campfire' || b.started < best.started) best = b;
    }
    return best;
  }
  companyCount(sid) { let n = 0; for (const co of this.companies.values()) if (co.sid === sid && !co.dead && !co.pub) n++; return n; }
  campfireOf(s) {
    for (const id of s.buildings) { const b = this.buildings.get(id); if (b && (b.type === 'campfire' || b.type === 'hall')) return b; }
    return null;
  }
  constructionSite(s, a) {
    let best = null, bs = -Infinity;
    for (const id of [...s.buildings, ...(s.works || [])]) {
      const b = this.buildings.get(id);
      if (!b || b.done) continue;
      const def = BUILDINGS[b.type];
      let sc = (def.kind === 'civic' ? 30 : def.kind === 'work' ? 20 : 10) + (b.budget > 0 ? 15 : 0) + (this.clock - b.started) / DAY;
      if (a && b.owner === a.id) sc += 50;
      sc -= Math.hypot(b.x - (a?.x || 0), b.z - (a?.z || 0)) / 100;
      if (sc > bs) { bs = sc; best = b; }
    }
    return best;
  }

  // ── 집 ──
  moveHome(a, b) {
    if (a.home === b.id) return;
    this.leaveHome(a);
    b.res.push(a.id);
    a.home = b.id;
    this.dirtyAnnMeta.add(a.id);
  }
  leaveHome(a) {
    const old = a.home && this.buildings.get(a.home);
    if (old) old.res = old.res.filter(id => id !== a.id);
    a.home = null;
    this.dirtyAnnMeta.add(a.id);
  }

  onRoadProgress(b) { roadProgress(this, b); }
  onBuilt(b, builder = null) {
    const def = BUILDINGS[b.type];
    const s = this.settlements.get(b.sid);
    this.dirtyBuildings.add(b.id);
    if (b.burnt) { b.burnt = false; b.fire = null; }
    if (b.infra) roadProgress(this, b);
    // 현장 사람들: 준공을 함께 맞는다
    if (b.contractor) {
      const co = this.companies.get(b.contractor);
      const months = Math.max(1, Math.round((this.clock - (b.started || this.clock)) / (DAY * 30)));
      if (co) {
        co.sites = (co.sites || []).filter(id => id !== b.id);
        for (const id of co.emp) {
          const e = this.anns.get(id);
          if (!e || e.job?.site !== b.id) continue;
          e.job.site = null;
          if (b.work >= 800) {
            lifeEvent(this, e, `'${siteName(this, b)}' 준공 — ${months}개월 만에`, '🏗️');
            remember(this, e, `우리가 지은 ${b.name}이(가) 드디어 완공되었다`, 6, 0.8, [], 'work');
            feel(e, 14, '뿌듯함', b.name);
            if (chance(0.5)) think(this, e, pick([`${b.name}… 내 손으로 올린 건물이다.`, '저 건물을 볼 때마다 생각나겠지.', '다음 현장은 어디려나.']));
          }
          this.dirtyAnnMeta.add(e.id);
        }
        if (b.work >= (def.kind === 'home' ? 20000 : 3000)) this.news('build', `🏁 ${co.name}이(가) '${siteName(this, b)}'을(를) ${months}개월 만에 준공했다`, { sid: b.sid, country: s.country }, 1);
      }
    }
    const cty = this.countries[s.country];
    if (b.infra) {
      const months = Math.max(1, Math.round((this.clock - (b.started || this.clock)) / (DAY * 30)));
      this.news('build', `🛣️ ${s.name} '${b.name}' 공사가 ${months}개월 만에 끝났다 — 이제 ${ROAD_GRADES[b.grade].name}`, { sid: b.sid, country: s.country }, 1);
      this.first(cty, 'road:' + b.grade, `첫 ${ROAD_GRADES[b.grade].name} — ${s.name} ${b.name.split(' ')[0]}`, '🛣️');
      for (const a of this.residents(s.id)) if (chance(0.15)) remember(this, a, `${b.name.split(' ')[0]}이(가) ${ROAD_GRADES[b.grade].name}(으)로 새로 단장됐다`, 2, 0.4, [], 'town');
      return;
    }
    if (b.type === 'campfire') this.first(cty, 'fire', `${s.name}에 첫 불을 피웠다`, '🔥');
    if (def.kind === 'home') this.first(cty, 'home:' + b.type, `첫 ${def.name}이(가) ${s.name}에 세워졌다`, '🏠');
    if (PUBLIC[b.type] || ['seodang', 'school', 'middle', 'high', 'university', 'kinder', 'clinic', 'market', 'hall'].includes(b.type)) this.first(cty, 'bld:' + b.type, `첫 ${def.name} — ${s.name}`, '🏛️');
    if (def.kind === 'home') {
      const owner = b.owner && this.anns.get(b.owner);
      if (owner && owner.alive) {
        const family = [owner, owner.spouse && this.anns.get(owner.spouse), ...owner.kids.map(id => this.anns.get(id)).filter(k => k && ageOf(k, this.clock) < 19)];
        for (const m of family) if (m && m.alive && m.sid === b.sid) this.moveHome(m, b);
        const how = { hut: '나뭇가지와 흙으로 손수 움집을 지었다', cottage: '흙벽돌을 찍어 쌓고 짚을 얹어 초가집을 지었다', house: '구운 벽돌을 쌓고 기와를 올려 기와집을 지었다', brickhouse: '붉은 벽돌로 2층 벽돌집을 지었다' }[b.type] || `${reul(def.name)} 완성했다`;
        const helpers = (b.helpers || []).map(id => this.anns.get(id)).filter(x => x && x.alive && x.id !== owner.id && x.id !== owner.spouse);
        remember(this, owner, `${how}${helpers.length ? ` — ${helpers.slice(0, 3).map(x => x.name).join(', ')}이(가) 품앗이로 도와주었다` : ''}`, 7, 0.9, helpers.map(x => x.id), 'home');
        lifeEvent(this, owner, `${def.name} 완공${helpers.length ? ` (이웃 ${helpers.length}명의 품앗이)` : ''}`, '🏠');
        for (const h of helpers) { const r = rel(owner, h), rh = rel(h, owner); r.a = clamp(r.a + 6, -100, 100); rh.a = clamp(rh.a + 3, -100, 100); remember(this, h, `${owner.name}네 ${def.name} 짓는 일을 도왔다`, 3, 0.5, [owner.id], 'home'); }
        feel(owner, 20, '뿌듯함', def.name);
        think(this, owner, pick(['드디어 내 집이다!', '비바람 걱정 없이 잘 수 있겠다.', '이 집에서 행복하게 살자.']));
      }
    } else if (def.kind === 'civic' || def.kind === 'work') {
      if (b.type !== 'campfire') this.news('build', `🏗️ ${s.name}에 ${b.name}${b.name.endsWith(def.name) ? '' : `(${def.name})`}이(가) 완공되었다`, { sid: s.id, country: s.country }, def.kind === 'civic' ? 1 : 0);
      else this.news('build', `🔥 ${s.name}에 첫 모닥불이 피어올랐다`, { sid: s.id, country: s.country }, 1);
      if (builder) remember(this, builder, `${b.name} 건설을 마무리했다`, 4, 0.5, [], 'work');
    }
    updateSettlementLevel(this, s);
  }

  /** 나라의 '처음' — 문명이 어떻게 시작되었는지가 나라의 연대기에 남는다 */
  first(c, key, text, icon = '✨') {
    if (!c) return false;
    c.firsts = c.firsts || {};
    if (c.firsts[key]) return false;
    c.firsts[key] = Math.round(this.clock);
    text = fixJosa(text);
    c.history.push({ t: Math.round(this.clock), text, icon });
    if (c.history.length > 80) c.history.splice(0, c.history.length - 80);
    this.news('first', `${icon} ${c.name} 최초: ${text}`, { country: c.code }, 2);
    this.dirtyCountries.add(c.code);
    return true;
  }

  achieveGoal(a, g) {
    if (g.done) return;
    g.done = true; g.at = Math.round(this.clock);
    remember(this, a, `꿈을 이루었다: ${g.text}`, 10, 1, [], 'goal');
    lifeEvent(this, a, `꿈을 이룸 — ${g.text}`, GOALS[g.k]?.icon || '🌟');
    feel(a, 40, '환희', g.text);
    think(this, a, `'${g.text}'… 정말로 이뤄냈다. 다음 꿈은 뭘까?`);
    a.conf = clamp(a.conf + 15);
    this.news('goal', `🌟 ${ga(a.name)} 평생의 꿈 '${g.text}'을(를) 이루었다`, { anns: [a.id], country: a.country }, 2);
  }

  // ── 기술 ──
  addTech(c, pts, src = 'etc') {
    c.tech += pts;
    if (this.techSrc) this.techSrc[src] = (this.techSrc[src] || 0) + pts;
    this.dirtyCountries.add(c.code);
  }
  /** 발견 시도: 시대 안에서 기술이 쌓일수록 발견 확률이 올라간다 (발견이 시대 전체에 고르게 퍼지도록) */
  tryDiscovery(a) {
    const c = this.countries[a.country];
    const list = DISCOVERIES[c.era] || [];
    const left = list.filter(d => !c.discoveries.includes(d));
    if (!left.length) return;
    const cur = ERAS[c.era].tech, nxt = ERAS[c.era + 1]?.tech ?? cur * 2 + 1000;
    const progress = (c.tech - cur) / (nxt - cur);
    const made = list.length - left.length;
    if (progress < made / list.length * 0.9 && !chance(0.15)) return;
    const d = pick(left);
    c.discoveries.push(d);
    const next = ERAS[c.era + 1];
    const bonus = next ? (next.tech - ERAS[c.era].tech) * 0.015 : 800;
    this.addTech(c, bonus, 'discovery');
    a.fame = clamp(a.fame + 25, 0, 100);
    a.rep = clamp(a.rep + 10, -100, 100);
    a.discoveries = [...(a.discoveries || []), d];
    remember(this, a, `'${d}'을(를) 발견했다!`, 10, 1, [], 'discovery');
    lifeEvent(this, a, `'${d}' 발견`, '💡');
    feel(a, 35, '환희', d);
    think(this, a, pick([`유레카! ${d}… 이걸로 세상이 달라질 거야.`, `됐다! 드디어 ${reul(d)} 알아냈어!`]));
    const g = a.goals.find(g => g.k === 'knowledge' && !g.done);
    if (g) this.achieveGoal(a, g);
    this.news('tech', `💡 ${c.name}의 ${ga(a.name)} '${d}'을(를) 발견했다`, { anns: [a.id], country: c.code }, 3);
    c.history.push({ t: Math.round(this.clock), text: `'${d}' 발견 — ${a.name}`, icon: '💡' });
    for (const other of this.residents(a.sid)) if (other.id !== a.id && chance(0.5)) remember(this, other, `${ga(a.name)} '${d}'을(를) 발견했다고 한다`, 4, 0.3, [a.id], 'news');
  }

  checkEra(c) {
    const next = ERAS[c.era + 1];
    if (!next || c.tech < next.tech) return;
    c.era++;
    c.eraAt = Math.round(this.clock);
    c.history.push({ t: Math.round(this.clock), text: `${next.name} 개막`, icon: '🌅' });
    this.news('era', `🌅 ${c.name}이(가) ${next.name}에 들어섰다!`, { country: c.code }, 4);
    for (const a of this.anns.values()) if (a.alive && a.country === c.code && ageOf(a, this.clock) > 10) {
      remember(this, a, `세상이 ${next.name}${ro_(next.name)} 바뀌었다`, 7, 0.6, [], 'era');
      if (chance(0.4)) think(this, a, pick(['세상이 이렇게 빨리 변하다니.', '새로운 시대다. 나도 뒤처지지 말아야지.', '옛날이 그립기도 하다.']));
    }
    if (c.era >= 2 && c.tax < 0.05) c.tax = 0.1;
    this.dirtyCountries.add(c.code);
  }

  // ── 나라 ──
  ensureCountry(code) {
    if (this.countries[code]) return this.countries[code];
    const g = getCountry(code);
    if (!g) throw new Error('unknown country');
    const c = this.countries[code] = {
      code, name: g.name, culture: g.culture, era: 0, eraAt: Math.round(this.clock), tech: 0, discoveries: [], leader: null, leaderSince: null,
      treasury: 0, tax: 0.0, founded: Math.round(this.clock), capital: null, culturePts: 0, nextElection: null, history: [], pop: 0,
    };
    this.dirtyCountries.add(code);
    return c;
  }

  influence(a) {
    let liked = 0, n = 0;
    for (const b of this.residents(a.sid)) { const r = b.rels[a.id]; if (r) { liked += r.a; n++; } }
    return a.ab.cha * 0.3 + a.amb * 0.25 + a.sk.leadership * 0.35 + a.rep * 0.3 + (n ? liked / n : 0) * 0.4 + a.fame * 0.2 + (ageOf(a, this.clock) > 30 ? 5 : 0);
  }

  setLeader(c, a, how) {
    const prev = c.leader && this.anns.get(c.leader);
    c.leader = a.id; c.leaderSince = Math.round(this.clock);
    const title = ERAS[c.era].leader;
    remember(this, a, `${c.name}의 ${title}${ga_(title)} 되었다`, 10, 1, [], 'power');
    lifeEvent(this, a, `${c.name}의 ${title} (${how})`, '👑');
    feel(a, 30, '감격', title);
    think(this, a, a.tr.A > 60 ? '모두가 행복한 나라를 만들고 싶다.' : a.amb > 70 ? '이 나라를 내 뜻대로 크게 키워 보겠다.' : '무거운 자리다. 잘 해낼 수 있을까.');
    const g = a.goals.find(g => g.k === 'power' && !g.done);
    if (g) this.achieveGoal(a, g);
    c.tax = clamp(0.08 + (100 - a.tr.A) * 0.0012 + a.amb * 0.0005, 0.04, 0.3);
    if (c.era < 2) c.tax = 0;
    c.capital = a.sid;
    if (!this.first(c, 'leader', `첫 ${title} ${a.name}`, '👑')) c.history.push({ t: Math.round(this.clock), text: `${a.name} ${title} 취임 (${how})`, icon: '👑' });
    this.news('power', `👑 ${ga(a.name)} ${c.name}의 ${title}${ga_(title)} 되었다 (${how})`, { anns: [a.id], country: c.code }, 3);
    this.dirtyCountries.add(c.code);
    this.dirtyAnnMeta.add(a.id);
    if (prev) this.dirtyAnnMeta.add(prev.id);
  }

  electLeader(c) {
    const people = [...this.anns.values()].filter(a => a.alive && a.country === c.code && ageOf(a, this.clock) >= 19);
    if (!people.length) return;
    const cands = people.filter(a => a.amb > 50 && ageOf(a, this.clock) >= 25).sort((x, y) => this.influence(y) - this.influence(x)).slice(0, 4);
    if (cands.length < 2) {
      const top = people.sort((x, y) => this.influence(y) - this.influence(x))[0];
      if (top) this.setLeader(c, top, '추대');
      return;
    }
    const votes = new Map(cands.map(x => [x.id, 0]));
    for (const v of people) {
      let best = null, bs = -Infinity;
      for (const cd of cands) {
        const r = v.rels[cd.id];
        const sc = (cd.id === v.id ? 200 : 0) + (r ? r.a * 0.6 + r.t * 0.2 : 0) + cd.ab.cha * 0.3 + cd.rep * 0.3 + cd.fame * 0.3 + (cd.sid === v.sid ? 8 : 0) + gauss() * 12;
        if (sc > bs) { bs = sc; best = cd; }
      }
      votes.set(best.id, votes.get(best.id) + 1);
    }
    const ranking = [...votes.entries()].sort((a, b) => b[1] - a[1]);
    const winner = this.anns.get(ranking[0][0]);
    const total = people.length;
    this.news('power', `🗳️ ${c.name} 선거: ${cands.map(x => `${x.name} ${votes.get(x.id)}표`).join(', ')}`, { country: c.code, anns: cands.map(x => x.id) }, 2);
    for (const cd of cands) if (cd.id !== winner.id) {
      remember(this, cd, `선거에서 ${winner.name}에게 졌다 (${votes.get(cd.id)}/${total}표)`, 7, -0.6, [winner.id], 'power');
      feel(cd, -18, '좌절', '선거');
      rel(cd, winner).a -= 8;
      updateKind(this, cd, winner);
    }
    this.setLeader(c, winner, `선거 ${Math.round(ranking[0][1] / total * 100)}% 득표`);
    c.nextElection = Math.round(this.clock + YEAR * 4);
  }

  countryDaily(c) {
    const people = [...this.anns.values()].filter(a => a.alive && a.country === c.code);
    c.pop = people.length;
    // 나라 살림: 하루 세수·공무원 인건비의 이동 평균
    c.taxAvg = (c.taxAvg ?? (c.taxIn || 0)) * 0.95 + (c.taxIn || 0) * 0.05;
    c.pubAvg = (c.pubAvg ?? (c.pubOut || 0)) * 0.95 + (c.pubOut || 0) * 0.05;
    c.taxIn = 0; c.pubOut = 0;
    if (!people.length) return;
    c.topEdu = people.reduce((m, a) => ageOf(a, this.clock) >= 19 ? Math.max(m, a.eduLv || 0) : m, 0);
    const leader = c.leader && this.anns.get(c.leader);
    // 지도자 공백 → 선출/계승/추대
    if (!leader || !leader.alive || leader.country !== c.code) {
      if (c.leader && leader && !leader.alive) c.history.push({ t: Math.round(this.clock), text: `${leader.name} 서거` });
      c.leader = null;
      const adults = people.filter(a => ageOf(a, this.clock) >= 19);
      if (adults.length >= 2 && this.clock - c.founded > DAY * 5) {
        if (c.era >= 4) this.electLeader(c);
        else if (c.era >= 2 && leader) {
          const heir = leader.kids.map(id => this.anns.get(id)).filter(k => k && k.alive && ageOf(k, this.clock) >= 18).sort((x, y) => x.born - y.born)[0];
          if (heir) this.setLeader(c, heir, '왕위 계승');
          else this.setLeader(c, adults.sort((x, y) => this.influence(y) - this.influence(x))[0], '추대');
        } else this.setLeader(c, adults.sort((x, y) => this.influence(y) - this.influence(x))[0], '추대');
      }
    } else {
      // 원시·농경: 더 영향력 있는 자가 나타나면 족장이 바뀔 수 있다
      if (c.era < 2 && this.clock - c.leaderSince > DAY * 60 && chance(0.05)) {
        const top = people.filter(a => ageOf(a, this.clock) >= 19).sort((x, y) => this.influence(y) - this.influence(x))[0];
        if (top && top.id !== leader.id && this.influence(top) > this.influence(leader) + 12) {
          remember(this, leader, `${ga(top.name)} 새 ${ERAS[c.era].leader}${ga_(ERAS[c.era].leader)} 되었다`, 7, -0.6, [top.id], 'power');
          this.setLeader(c, top, '세력 교체');
        }
      }
      if (c.era >= 4 && c.nextElection && this.clock >= c.nextElection) this.electLeader(c);
      this.govern(c, this.anns.get(c.leader));
    }
    // 우주 시대: 우주 센터에서 로켓이 발사된다
    if (c.era >= 7 && chance(0.06)) {
      const pad = [...this.buildings.values()].find(b => b.type === 'spacecenter' && b.done && this.settlements.get(b.sid)?.country === c.code);
      if (pad) {
        const crew = people.filter(a => a.job && (a.job.id === 'astronaut' || a.job.id === 'rocketeer'));
        const astro = crew.find(a => a.job.id === 'astronaut');
        const names = ['새벽', '누리', '하늘', '미르', '별빛', '아리랑', 'Aurora', 'Pioneer', 'Odyssey'];
        const ship = `${pick(names)}-${Math.floor(rand() * 9) + 1}호`;
        if (astro) {
          remember(this, astro, `${ship}를 타고 우주로 날아올랐다`, 10, 1, crew.map(x => x.id), 'space');
          lifeEvent(this, astro, `${ship} 탑승, 우주 비행`, '🚀');
          feel(astro, 40, '경이', '우주');
          think(this, astro, '지구가… 이렇게 작고 아름다웠구나.');
        }
        for (const a of crew) if (a !== astro) remember(this, a, `우리가 만든 ${ship}가 하늘로 솟아올랐다`, 8, 0.9, [], 'space');
        this.addTech(c, (ERAS[c.era].tech) * 0.004, 'space');
        this.news('space', `🚀 ${c.name} ${pad ? this.settlements.get(pad.sid).name : ''}에서 ${ship} 발사${astro ? ` — 우주비행사 ${astro.name}` : ''}`, { country: c.code, sid: pad.sid, anns: astro ? [astro.id] : [] }, 3);
      }
    }
    // 생활 속 기술 축적
    this.addTech(c, people.length * 0.12 * Math.sqrt(ERAS[c.era].prod), 'passive');
    this.checkEra(c);
  }

  /** 지도자의 국정: 성격에 따라 무엇을 먼저 짓는지가 달라진다 */
  govern(c, L) {
    if (!L || c.era < 2) return;
    const lead = 1 + L.sk.leadership / 200;
    const edu = 22 + L.tr.A * 0.25 + L.val.family * 0.25 + L.tr.O * 0.1;
    const prefs = [
      ['hall', 30 + L.tr.C * 0.3 + L.amb * 0.2],
      ['academy', 20 + L.tr.O * 0.4 + L.ab.int * 0.2],
      ['temple', 10 + L.tr.A * 0.3 + L.tr.N * 0.1],
      ['seodang', edu + 6], ['kinder', edu - 8], ['school', edu + 8], ['middle', edu + 2], ['high', edu], ['univ', edu - 4 + L.ab.int * 0.2],
      ['lab', 20 + L.tr.O * 0.4 + L.ab.int * 0.3],
      ['hospital', 22 + L.tr.A * 0.3], ['police', 16 + L.tr.C * 0.25], ['fire', 15 + L.tr.A * 0.2], ['post', 10 + L.tr.C * 0.1],
      ['station', 12 + L.amb * 0.15], ['power', 14 + L.tr.O * 0.15],
    ].sort((a, b) => b[1] - a[1]);
    // 관청과 학교가 먼저다: 그게 모자란 동안에는 다른 기관은 미루고 돈을 모은다
    let pending = false;
    const sids = [...new Set([c.capital, ...[...this.settlements.values()].filter(s => s.country === c.code).map(s => s.id)])];
    for (const pass of [0, 1]) {
      if (pass === 1 && pending) break;
      for (const sid of sids) {
        const s = this.settlements.get(sid);
        if (!s) continue;
        for (const [key] of prefs) {
          const def = PUBLIC[key];
          const essential = key === 'hall' || (!!def.school && key !== 'univ');
          if ((pass === 0) !== essential) continue;
          if (def.era > c.era || (def.maxEra != null && c.era > def.maxEra)) continue;
          if (key === 'hall' && sid !== c.capital && s.pop < 30) continue;
          if (key === 'univ' && (sid !== c.capital || c.pop < 35)) continue;
          const have = [...this.companies.values()].filter(co => co.pub && co.ind === key && co.sid === sid && !co.dead).length;
          let want;
          if (def.school) {
            // 학교는 그 나이의 아이들이 있어야 세운다
            const range = { seodang: [7, 16], kinder: [4, 7], school: [7, 13], middle: [13, 16], high: [16, 19], univ: [17, 23] }[key];
            const kids = this.residents(sid).filter(x => { const g = ageOf(x, this.clock); return g >= range[0] && g < range[1]; }).length;
            want = kids >= 3 ? 1 + Math.floor(kids / 45) : 0;
          } else if (def.pop) want = s.pop >= def.pop ? 1 + Math.floor(s.pop / (def.per * 4)) : 0;
          else want = key === 'hall' ? 1 : s.pop >= def.per * 2 ? 1 + Math.floor(s.pop / 300) : 0;
          if (have >= want) continue;
          const cost = wageBase(c.era) * 10 + BUILDINGS[def.building].work * 0.5;
          if (c.treasury <= cost * 1.5 / lead) {
            if (!essential) continue;
            // 꼭 필요한 관청·학교를 지을 돈이 없으면 결국 세금을 올린다
            pending = true;
            c.unfunded = (c.unfunded || 0) + 1;
            if (c.unfunded > 45 && c.tax < 0.3) {
              c.unfunded = 0;
              c.tax = Math.min(0.3, c.tax + 0.02);
              this.news('gov', `💰 ${L.name} ${ERAS[c.era].leader}: "${s.name}에 ${reul(def.name)} 세우려면 돈이 필요합니다" — 세금을 ${Math.round(c.tax * 100)}%로 올렸다`, { country: c.code, anns: [L.id] }, 1);
            }
            continue;
          }
          if (essential) c.unfunded = 0;
          const org = foundPublic(this, c, s, key, L);
          if (org) {
            remember(this, L, `${s.name}에 ${reul(def.name)} 세우도록 명했다`, 5, 0.5, [], 'power');
            think(this, L, key === 'academy' || key === 'lab' ? '배움이 나라를 키운다.' : def.school ? '아이들이 미래다.' : key === 'temple' ? '사람들의 마음을 보살펴야 한다.' : '나라에는 질서가 필요하다.');
            return;
          }
        }
      }
    }
    c.pending = pending;
    const wage = wageBase(c.era);
    // 곳간이 넘치면 세금을 내린다
    if (!pending && c.treasury > wage * 600 && c.tax > 0.05) {
      c.richDays = (c.richDays || 0) + 1;
      if (c.richDays > 60) {
        c.richDays = 0;
        c.tax = Math.max(0.04, c.tax - 0.02);
        this.news('gov', `💸 ${L.name} ${ERAS[c.era].leader}: 나라 곳간이 넉넉하다 — 세금을 ${Math.round(c.tax * 100)}%로 내렸다`, { country: c.code, anns: [L.id] }, 1);
      }
    } else c.richDays = 0;
    // 복지: 마음이 따뜻한 지도자일수록 가난한 이웃을 돕는다
    if (c.treasury > wage * 40 && chance(0.2 + L.tr.A / 250)) {
      const cost = dailyCost(c.era);
      const poor = [...this.anns.values()].filter(a => a.alive && a.country === c.code && ageOf(a, this.clock) >= 19 && a.money < cost * 3);
      for (const p of poor.slice(0, 30)) {
        const aid = wage * 2;
        if (c.treasury < aid) break;
        c.treasury -= aid; p.money += aid; book(this, p, 'inc', '생활 지원금', aid);
        remember(this, p, `나라에서 생활 지원금을 받았다`, 3, 0.4, [L.id], 'money');
        const r = rel(p, L); r.a = clamp(r.a + 2, -100, 100);
      }
    }
    // 연구 지원 (호기심 많은 지도자)
    if (!pending && c.treasury > wage * 30 && L.tr.O > 55) {
      const grant = Math.min(c.treasury * 0.03, wage * 8);
      c.treasury -= grant;
      this.addTech(c, grant / wage * 1.2, 'grant');
    }
  }

  // ── 플레이어 ──
  joinPlayer(name, countryCode) {
    const c = this.ensureCountry(countryCode);
    const id = 'p' + this.newId();
    const used = new Set(Object.values(this.players).map(p => p.color));
    const color = PLAYER_COLORS.find(x => !used.has(x)) || pick(PLAYER_COLORS);
    const p = { id, token: randomUUID(), name: String(name || '플레이어').slice(0, 20), country: c.code, color, joined: Math.round(this.clock), joinedReal: Date.now(), anns: [], eraAtJoin: c.era, ach: {}, inbox: [] };
    this.players[id] = p;
    this.news('player', `🌍 새로운 플레이어 ${ga(p.name)} ${c.name}에서 문명을 시작한다`, { country: c.code }, 2);
    return p;
  }
  playerByToken(token) { return Object.values(this.players).find(p => p.token === token) || null; }

  homeSettlement(code) {
    const list = [...this.settlements.values()].filter(s => s.country === code);
    if (list.length) return list.sort((a, b) => b.pop - a.pop)[0];
    const g = getCountry(code);
    const c = this.ensureCountry(code);
    // 사람들은 물가에 모여 산다: 해안이나 강을 고른다
    let pick_ = null;
    for (let i = 0; i < 6; i++) {
      const p = randomLandPoint(code, g.centroid, 0, 1.4);
      const sea = seaNear(p[0], p[1]);
      const sc = (sea ? 2 + (sea.km < 15 ? 1 : 0) : 0) + rand() * 2;
      if (!pick_ || sc > pick_.sc) pick_ = { p, sc };
    }
    const [lon, lat] = pick_.p;
    const s = makeSettlement(this, c, lon, lat, null);
    c.capital = s.id;
    this.news('city', `⛺ ${c.name}의 첫 정착지 ${s.name}이(가) 생겼다`, { country: code, sid: s.id }, 2);
    return s;
  }

  deployAnns(player, specs) {
    // 투입 한도는 직접 투입한 1세대만 센다 (태어난 후손은 제외)
    const room = MAX_ANNS_PER_PLAYER - player.anns.filter(id => this.anns.get(id)?.pdeploy || this.anns.get(id)?.gen === 1 && !this.anns.get(id)?.parents.length).length;
    if (room <= 0) throw new Error(`한 플레이어는 앤을 최대 ${MAX_ANNS_PER_PLAYER}명까지 투입할 수 있습니다`);
    const c = this.ensureCountry(player.country);
    // 이 나라에 가정이 있으면, 앤은 무작위 가정에 아기로 태어난다
    if (this.pickFamily(c.code)) {
      const born = [];
      for (const raw of specs.slice(0, room)) {
        // 가정이 모자라면 이미 고른 가정에 형제·쌍둥이로
        const fam = this.pickFamily(c.code, born.map(b => b.fam)) || this.pickFamily(c.code);
        if (!fam) break;
        const baby = this.bornIntoFamily(player, sanitizeSpec(raw), fam);
        born.push({ baby, fam });
      }
      if (born.length) {
        award(this, player, 'first_deploy');
        this.news('player', `👶 ${player.name}의 앤 ${born.length}명이 ${c.name}의 가정에서 태어났다`, { country: c.code, anns: born.map(b => b.baby.id) }, 2);
        this.rebuildResidents();
        return born.map(b => b.baby);
      }
    }
    const s = this.homeSettlement(player.country);
    const out = [];
    for (const raw of specs.slice(0, room)) {
      const spec = sanitizeSpec(raw);
      const [x, z] = plazaPoint(s, 25);
      const a = makeAnn(this, { ...spec, culture: c.culture, owner: player.id, country: c.code, sid: s.id, x, z, money: wageBase(c.era) * 5 });
      this.anns.set(a.id, a);
      player.anns.push(a.id);
      a.pdeploy = true;
      remember(this, a, `${s.name}에 첫발을 내디뎠다`, 9, 0.6, [], 'life');
      lifeEvent(this, a, `개척자로 ${c.name} ${s.name}에 도착했다 — 아직 아무것도 없는 자연`, '🌱');
      think(this, a, pick(['여기는 어디지? …새로운 삶이 시작되는구나.', '낯선 땅이다. 그래도 왠지 설렌다.', '살아남아야 한다. 그리고 행복해져야지.']));
      out.push(a);
      this.dirtyAnnMeta.add(a.id);
    }
    // 같이 투입된 앤들은 서로 조금 안다
    for (const a of out) for (const b of out) if (a !== b) { const r = rel(a, b); r.f = Math.max(r.f, 5); r.k = 'acquaintance'; }
    award(this, player, 'first_deploy');
    this.news('player', `👥 ${player.name}의 앤 ${out.length}명이 ${c.name} ${s.name}에 도착했다`, { country: c.code, sid: s.id, anns: out.map(a => a.id) }, 2);
    this.rebuildResidents();
    s.pop = this.residents(s.id).length; c.pop = [...this.anns.values()].filter(a => a.alive && a.country === c.code).length;
    this.dirtySettlements.add(s.id); this.dirtyCountries.add(c.code);
    return out;
  }

  /** 아기를 맞을 수 있는 가정 (부부·연인, 같은 마을) */
  pickFamily(code, exclude = []) {
    const now = this.clock;
    const fams = [];
    for (const a of this.anns.values()) {
      if (!a.alive || a.country !== code || a.travel) continue;
      const pid = a.spouse || a.partner;
      if (!pid || a.id > pid) continue;
      const b = this.anns.get(pid);
      if (!b || !b.alive || b.sid !== a.sid || b.travel) continue;
      const ages = [ageOf(a, now), ageOf(b, now)];
      if (Math.min(...ages) < 18 || Math.max(...ages) > 58) continue;
      if (exclude.some(f => f.a === a)) continue;
      const mother = a.gender === 'F' ? a : b.gender === 'F' ? b : null;
      const father = a.gender === 'M' ? a : b.gender === 'M' ? b : null;
      const bio = !!(mother && father && ageOf(mother, now) <= 46 && !mother.preg);
      const recent = mother && now - (mother.lastBirth || -1e9) < YEAR * 0.8;
      const w = (a.spouse ? 2 : 1) * (1 + (a.val.family + b.val.family) / 100) / (1 + a.kids.length * 0.5) * (recent ? 0.15 : 1);
      fams.push({ a, b, mother, father, bio, w });
    }
    if (!fams.length) return null;
    let tot = fams.reduce((t, f) => t + f.w, 0), r = rand() * tot;
    for (const f of fams) { r -= f.w; if (r <= 0) return f; }
    return fams[fams.length - 1];
  }

  bornIntoFamily(player, spec, fam) {
    const { a, b, mother, father, bio } = fam;
    const m = mother || a, f = father || b;
    const gender = spec.gender;
    const name = familyName(m.culture, gender, (father || a).name, givenOf(spec.name, m.culture));
    const baby = makeBaby(this, m, f, name, gender);
    // 플레이어가 정한 성격·능력·야망은 이 아이의 타고난 기질이 된다
    baby.tr = { ...spec.traits }; baby.ab = { ...spec.abilities }; baby.amb = spec.ambition;
    if (spec.orient) baby.orient = spec.orient;
    baby.val = deriveValues(baby); baby.goals = deriveGoals(baby);
    baby.health = clamp(70 + baby.ab.hea * 0.3);
    baby.owner = player.id; baby.pdeploy = true;
    baby.parents = [a.id, b.id];
    this.anns.set(baby.id, baby);
    player.anns.push(baby.id);
    a.kids.push(baby.id); b.kids.push(baby.id);
    if (bio) { mother.lastBirth = Math.round(this.clock); }
    const home = a.home && this.buildings.get(a.home) || b.home && this.buildings.get(b.home);
    if (home) this.moveHome(baby, home);
    const s = this.settlements.get(a.sid);
    const c = this.countries[a.country];
    const where = home ? `${home.name === BUILDINGS[home.type].name ? '' : home.name + ' '}${BUILDINGS[home.type].name}` : '모닥불 곁 천막';
    const parentsTxt = `${wa(a.name)} ${b.name}`;
    for (const p of [a, b]) {
      const r = rel(p, baby); r.a = 85; r.f = 50; r.t = 90; r.k = 'child';
      const rb = rel(baby, p); rb.a = 70; rb.f = 50; rb.t = 90; rb.k = 'parent';
      remember(this, p, bio ? `${gender === 'M' ? '아들' : '딸'} ${ga(name)} 태어났다` : `${gender === 'M' ? '아들' : '딸'} ${reul(name)} 입양했다`, 10, 1, [baby.id], 'family');
      lifeEvent(this, p, `${gender === 'M' ? '아들' : '딸'} ${name} ${bio ? '탄생' : '입양'}`, '👶');
      feel(p, 35, '감격', name);
      think(this, p, bio ? pick([`${name}… 세상에 온 걸 환영해.`, '이렇게 작은 손이라니.', '이 아이를 위해서라면 뭐든 할 수 있을 것 같다.']) : `${name}, 오늘부터 우리 가족이야.`);
    }
    for (const sibId of [...new Set([...a.kids, ...b.kids])]) {
      if (sibId === baby.id) continue;
      const sib = this.anns.get(sibId);
      if (sib && sib.alive) { const r = rel(sib, baby); r.a = 50; r.f = 20; r.k = 'sibling'; const rb = rel(baby, sib); rb.a = 50; rb.f = 20; rb.k = 'sibling'; remember(this, sib, `동생 ${ga(name)} 생겼다`, 6, 0.7, [baby.id], 'family'); }
    }
    lifeEvent(this, baby, `${c.name} ${s.name}, ${parentsTxt}의 ${where}에서 ${bio ? '태어났다' : '새 가족을 만났다'}`, '👶');
    remember(this, baby, '세상에 태어났다', 10, 1, [a.id, b.id], 'life');
    this.first(c, 'baby', `첫 아기 ${name} — ${parentsTxt}의 아이`, '👶');
    this.news('family', `👶 ${parentsTxt}의 가정에 ${gender === 'M' ? '아들' : '딸'} ${ga(name)} ${bio ? '태어났다' : '찾아왔다'} (${this.players[player.id]?.name}의 앤)`, { anns: [baby.id, a.id, b.id], country: c.code }, 2);
    this.dirtyAnnMeta.add(baby.id); this.dirtyAnnMeta.add(a.id); this.dirtyAnnMeta.add(b.id);
    return baby;
  }

  // ── 학교: 나이에 맞는 학교가 마을에 있어야 다닌다 ──
  schoolDaily(a, age, s, c) {
    const stage = SCHOOLS.find(x => age >= x.ages[0] && age < x.ages[1]);
    const cur = a.school;
    if (cur && (!stage || stage.key !== cur.key)) {
      const def = SCHOOLS.find(x => x.key === cur.key);
      const years = def.ages[1] - def.ages[0];
      const needDays = years * 140;
      const org = this.companies.get(cur.org);
      if (cur.days >= needDays * 0.55) {
        a.eduLv = Math.max(a.eduLv || 0, def.level);
        const rank = cur.grade > 85 ? ' — 수석 졸업!' : cur.grade > 70 ? ' (우수한 성적)' : '';
        lifeEvent(this, a, `${cur.name} 졸업${rank}`, '🎓');
        remember(this, a, `${cur.name}을(를) 졸업했다${rank}`, def.level >= 3 ? 8 : 6, 0.7, [], 'school');
        think(this, a, def.key === 'univ' ? '드디어 졸업! 이제 진짜 어른의 세계다.' : pick(['졸업이라니, 시간 참 빠르다.', '친구들이랑 헤어지는 게 아쉽다.', '다음엔 어떤 일이 기다리고 있을까?']));
        for (const pid of a.parents) { const p = this.anns.get(pid); if (p?.alive) remember(this, p, `${ga(a.name)} ${cur.name}을(를) 졸업했다`, 6, 0.8, [a.id], 'family'); }
        if (def.key === 'univ') this.first(c, 'graduate', `첫 대학 졸업생 ${a.name}`, '🎓');
        else if (def.level >= 1) this.first(c, 'grad' + def.key, `첫 ${def.name} 졸업생 ${a.name}`, '🎓');
      } else if (def.level > 0) {
        lifeEvent(this, a, `${cur.name}을(를) 끝까지 다니지 못했다`, '📕');
        remember(this, a, `${cur.name}을(를) 마치지 못했다`, 5, -0.4, [], 'school');
      }
      a.school = null;
      this.dirtyAnnMeta.add(a.id);
    }
    if (!stage || a.school) return;
    a.noSchool = a.noSchool || {};
    if (stage.optional) {
      if (a.noSchool[stage.key]) return;
      a.noSchool[stage.key] = 1;
      const want = a.ab.int * 0.35 + a.tr.O * 0.2 + a.amb * 0.25 + a.tr.C * 0.15 + (a.eduLv >= 3 ? 10 : -30) + gauss() * 10;
      const org = this.findSchool(s, stage);
      if (org && want > 52) this.enroll(a, stage, org, s, c);
      else if (org) { lifeEvent(this, a, '대학 대신 일을 시작하기로 했다', '🧰'); think(this, a, '공부는 이만하면 됐다. 이제 돈을 벌어야지.'); }
      return;
    }
    const org = this.findSchool(s, stage);
    if (org) { this.enroll(a, stage, org, s, c); return; }
    s.need = s.need || {};
    s.need.school = (s.need.school || 0) + 1;
    if (!a.noSchool[stage.key] && stage.level >= 1 && c.era >= 2) {
      a.noSchool[stage.key] = 1;
      lifeEvent(this, a, `${s.name}에는 ${stage.name}이(가) 없어 학교에 가지 못했다`, '🏚️');
      think(this, a, '다른 동네 아이들은 학교에 간다던데…');
      for (const pid of a.parents) { const p = this.anns.get(pid); if (p?.alive && chance(0.5)) think(this, p, `우리 마을에도 학교가 있으면 ${a.name}도 배울 수 있을 텐데.`); }
    }
  }
  findSchool(s, stage) {
    let best = null;
    for (const co of this.companies.values()) {
      if (co.dead || co.sid !== s.id || !co.pub || !stage.orgs.includes(co.ind)) continue;
      const b = this.buildings.get(co.b);
      if (!b?.done) continue;
      if (!best || stage.orgs.indexOf(co.ind) < stage.orgs.indexOf(best.ind)) best = co;
    }
    return best;
  }
  enroll(a, stage, org, s, c) {
    const name = `${s.name} ${PUBLIC[org.ind].name}`;
    a.school = { key: stage.key, org: org.id, name, since: Math.round(this.clock), days: 0, grade: 50 + (a.ab.int - 50) * 0.4 + (a.tr.C - 50) * 0.2 };
    lifeEvent(this, a, `${name} 입학`, stage.key === 'univ' ? '🏫' : '🎒');
    remember(this, a, `${name}에 입학했다`, 6, 0.6, [], 'school');
    think(this, a, stage.key === 'elem' ? pick(['학교는 어떤 곳일까? 두근두근.', '친구를 많이 사귀고 싶다!', '선생님이 무서우면 어떡하지…']) : stage.key === 'univ' ? '대학생이 됐다. 하고 싶은 공부를 마음껏 해 보자.' : pick(['새 학교, 새 친구들.', '공부가 점점 어려워지겠지.']));
    for (const pid of a.parents) { const p = this.anns.get(pid); if (p?.alive) { remember(this, p, `${ga(a.name)} ${name}에 들어갔다`, 5, 0.6, [a.id], 'family'); if (chance(0.5)) think(this, p, `${a.name}, 학교 잘 다녀오렴.`); } }
    this.first(c, 'student:' + stage.key, `첫 ${stage.name} 학생 ${a.name}`, '🎒');
    this.dirtyAnnMeta.add(a.id);
  }

  // ── 이주 · 개척 ──
  foundSettlementBy(a) {
    const from = this.settlements.get(a.sid);
    const c = this.countries[a.country];
    const [lon, lat] = randomLandPoint(c.code, [from.lon, from.lat], 0.35, 2.2);
    const s = makeSettlement(this, c, lon, lat, a);
    const travelers = [a];
    if (a.spouse) { const sp = this.anns.get(a.spouse); if (sp && sp.alive && sp.sid === a.sid) travelers.push(sp); }
    for (const id of a.kids) { const k = this.anns.get(id); if (k && k.alive && k.sid === a.sid && ageOf(k, this.clock) < 19) travelers.push(k); }
    for (const id in a.rels) {
      const r = a.rels[id];
      const f = this.anns.get(+id);
      if (f && f.alive && f.sid === a.sid && !travelers.includes(f) && (r.k === 'best' || r.k === 'friend') && f.val.adventure > 50 && chance(0.4) && ageOf(f, this.clock) >= 19 && this.countries[f.country].leader !== f.id) travelers.push(f);
    }
    const km = Math.hypot((lon - from.lon) * 111 * Math.cos(lat * Math.PI / 180), (lat - from.lat) * 111);
    const until = this.clock + Math.min(DAY * 3, (km / 25) * 60 + 120);
    for (const t of travelers) {
      if (t.job) leaveJob(this, t, t === a ? '개척을 떠나며' : '가족을 따라 이주');
      this.leaveHome(t);
      t.act = null;
      t.travel = { until, from: from.id, to: s.id };
      t.sid = s.id;
      t.x = 0; t.z = 0;
      t.wantsMigrate = null;
      this.dirtyAnnMeta.add(t.id);
    }
    remember(this, a, `정든 ${reul(from.name)} 떠나 새 땅 ${s.name}${ro_(s.name)} 향했다`, 9, 0.5, travelers.filter(t => t !== a).map(t => t.id), 'life');
    lifeEvent(this, a, `${s.name} 개척`, '🧭');
    think(this, a, '저 너머에 우리만의 마을을 세우자.');
    const g = a.goals.find(g => g.k === 'adventure' && !g.done);
    if (g) this.achieveGoal(a, g);
    this.news('city', `🧭 ${ga(a.name)} ${travelers.length}명과 함께 ${from.name}을(를) 떠나 새 정착지 ${s.name}을(를) 개척한다`, { anns: travelers.map(t => t.id), country: c.code, sid: s.id }, 2);
  }

  arrive(a) {
    a.travel = null;
    const s = this.settlements.get(a.sid);
    const [x, z] = plazaPoint(s, 20);
    a.x = x; a.z = z;
    remember(this, a, `${s.name}에 도착했다`, 4, 0.3, [], 'life');
    this.dirtyAnnMeta.add(a.id);
    this.rebuildResidents();
  }

  // ── 하루 단위 ──
  annDaily(a) {
    const now = this.clock;
    const age = ageOf(a, now);
    const c = this.countries[a.country];
    const s = this.settlements.get(a.sid);
    const today = dateOf(now), bday = dateOf(a.born);
    if (today.doy === bday.doy && age >= 1) {
      const y = Math.floor(age + 0.01);
      const before = ageStage(y - 1), after = ageStage(y);
      if (before !== after) {
        lifeEvent(this, a, `${STAGE_NAMES[after]}가 되었다`, '🎂');
        if (after === 'adult') {
          remember(this, a, '어른이 되었다', 8, 0.5, [], 'life');
          think(this, a, '이제 어른이다. 내 힘으로 살아가야 해.');
        }
        this.dirtyAnnMeta.add(a.id);
      } else if (y % 10 === 0) {
        lifeEvent(this, a, `${y}번째 생일`, '🎂');
        think(this, a, y >= 60 ? `벌써 ${y}살이라니. 세월 참 빠르다.` : `${y}살. 나는 잘 살아가고 있는 걸까?`);
      }
      this.dirtyAnnMeta.add(a.id);
    }
    // 사망
    let mu = 0.00006 * Math.exp(0.085 * age) * ERAS[c.era].mort * (1.6 - a.health / 100) * (a.ab.hea > 70 ? 0.8 : a.ab.hea < 30 ? 1.3 : 1);
    if (age < 1) mu += 0.025 * ERAS[c.era].mort;
    if (a.sick > 60) mu += 0.6;
    if (a.nd.food < 3) mu += 0.8;
    if (chance(mu / 365)) { this.die(a, a.sick > 60 ? '병' : a.nd.food < 3 ? '굶주림' : age > 60 ? '노환' : '사고'); return; }
    // 질병
    if (a.sick <= 0 && chance(0.0012 * (1 + age / 40) * (1.6 - a.health / 100) * ERAS[c.era].mort)) {
      a.sick = 25 + rand() * 60;
      remember(this, a, '몸이 아프기 시작했다', 5, -0.6, [], 'health');
      think(this, a, '몸이 무겁다… 쉬어야겠다.');
      feel(a, -8, '불안', '병');
    }
    if (a.sick > 0) {
      a.health = clamp(a.health - a.sick * 0.015);
      const worsen = chance(0.25 * ERAS[c.era].mort / 1.5) ? 5 : -(2 + a.health / 40 + (s.heal > 0.5 ? 3 : 0));
      a.sick = clamp(a.sick + worsen);
      if (a.sick <= 0) { remember(this, a, '병이 나았다', 4, 0.6, [], 'health'); think(this, a, '드디어 몸이 가볍다!'); }
    }
    else a.health = clamp(a.health + 0.2 - Math.max(0, age - 50) * 0.01);
    // 출산
    if (a.preg && now >= a.preg.due) this.birth(a);
    // 생활비: 식비·주거비·공과금·통신비·교통비·의류·보험·자녀 교육비 (가계부에 남는다)
    if (age >= 19) {
      pensionDaily(this, a, c);
      const short = livingDaily(this, a, c);
      if (short > 0) {
        a.stress = clamp(a.stress + 6);
        if (chance(0.3)) { remember(this, a, '돈이 없어 생활이 힘들다', 4, -0.5, [], 'money'); think(this, a, '이번 달은 어떻게 버티지…'); }
      }
    }
    // 은퇴
    if (a.job && age >= 63 && !a.job.ceo && chance(0.01 + (age - 63) * 0.01 + (a.money > dailyCost(c.era) * 200 ? 0.03 : 0))) {
      leaveJob(this, a, '은퇴');
      a.retired = true;
      think(this, a, '평생 일했다. 이제는 쉬어도 되겠지.');
    }
    if (a.job?.ceo && age >= 68 && chance(0.02)) {
      const co = this.companies.get(a.job.co);
      leaveJob(this, a, '은퇴');
      a.retired = true;
      if (co) remember(this, a, `${co.name}을(를) 후배들에게 맡겼다`, 7, 0.3, [], 'work');
    }
    if (age >= 3.5 && age < 24) this.schoolDaily(a, age, s, c);
    if (!a.home && age >= 19) { s.need = s.need || {}; s.need.homes = (s.need.homes || 0) + 1; }
    if (a.sick > 30 && !this.findCivic(s, 'clinic')) { s.need = s.need || {}; s.need.clinic = (s.need.clinic || 0) + 1; }
    runEvents(this, a, { age, s, c });
    considerPromotion(this, a);
    if (a.job && !a.job.co) {
      const j = JOBS[a.job.id];
      if (j?.maxEra != null && c.era > j.maxEra) {
        const title = j.name;
        leaveJob(this, a, '시대가 바뀌어');
        think(this, a, `${title} 일은 이제 아무도 찾지 않는다. 새로운 일을 배워야 해.`);
      }
    }
    // 연애 관계 점검
    if (a.partner) {
      const p = this.anns.get(a.partner);
      if (!p || !p.alive) a.partner = null;
      else if (a.id < p.id) {
        const r = rel(a, p), rp = rel(p, a);
        if ((r.a < -10 || rp.a < -10) && chance(0.15)) breakUp(this, a, p, !!a.spouse && (r.a < -25 || rp.a < -25) && chance(0.4));
        else if (!a.spouse && r.r < 15 && rp.r < 15 && chance(0.08)) breakUp(this, a, p, false);
      }
    }
    // 집 없는 앤: 빈 집을 찾는다 / 갓난아이는 엄마 집
    if (!a.home) {
      const mom = a.parents.map(id => this.anns.get(id)).find(p => p && p.alive && p.home && p.sid === a.sid);
      if (age < 19 && mom) this.moveHome(a, this.buildings.get(mom.home));
      else for (const bid of s.buildings) {
        const b = this.buildings.get(bid);
        if (!b || !b.done || BUILDINGS[b.type].kind !== 'home') continue;
        if (b.dev && b.res.length < BUILDINGS[b.type].cap) {
          this.moveHome(a, b);
          if (a.spouse) { const sp = this.anns.get(a.spouse); if (sp?.alive && !sp.home) this.moveHome(sp, b); }
          lifeEvent(this, a, `${b.name}에 세 들어 살게 되었다`, '🏢');
          remember(this, a, `${b.name}에 입주했다`, 6, 0.6, [], 'home');
          break;
        }
        if (!b.dev && b.res.length === 0) {
          this.moveHome(a, b);
          if (!b.owner || !this.anns.get(b.owner)?.alive) b.owner = a.id;
          remember(this, a, `빈 ${reul(BUILDINGS[b.type].name)} 얻어 들어가 살게 되었다`, 5, 0.5, [], 'home');
          break;
        }
      }
    }
    // 개척 욕구
    if (!a.wantsMigrate && age >= 22 && age < 55 && c.leader !== a.id) {
      const sCount = [...this.settlements.values()].filter(x => x.country === a.country).length;
      const crowd = s.pop / (25 * (1 + c.era * 0.7));
      // 사람은 대개 모여 산다: 정착지는 인구 40~50명에 하나 정도만 새로 생긴다 (마을이 커져야 도시가 된다)
      if (a.goals.some(g => g.k === 'adventure' && !g.done) && s.pop >= 20 && sCount < 1 + c.pop / 45 && chance(0.004 * (1 + crowd))) a.wantsMigrate = '모험심';
      else if (crowd > 1 && sCount < 1 + c.pop / 40 && chance(0.0015 * crowd * a.val.adventure / 50)) a.wantsMigrate = '이곳은 너무 붐빈다';
    }
    // 목표 진척
    for (const g of a.goals) {
      if (g.done || age < 16) continue;
      if (g.k === 'wealth' && a.money > wageBase(c.era) * 250) this.achieveGoal(a, g);
      if (g.k === 'family' && a.spouse && a.kids.length >= 2) this.achieveGoal(a, g);
      if (g.k === 'community' && Object.values(a.rels).filter(r => r.k === 'friend' || r.k === 'best').length >= 6) this.achieveGoal(a, g);
      if (g.k === 'sport' && a.sk.athletics >= 85) this.achieveGoal(a, g);
      if (g.k === 'mastery' && a.job && a.sk[JOBS[a.job.id]?.skill] >= 85) this.achieveGoal(a, g);
    }
    // 새로운 꿈이 생기기도 한다
    if (a.goals.every(g => g.done) && age > 18 && chance(0.02)) {
      const pool = Object.keys(GOALS).filter(k => !a.goals.some(g => g.k === k));
      const k = pool.sort((x, y) => (a.val[y] ?? a.amb) - (a.val[x] ?? a.amb))[0];
      if (k) { a.goals.push({ k, text: GOALS[k].text, done: false }); think(this, a, `새로운 꿈이 생겼다. ${GOALS[k].text}.`); }
    }
    decayRelations(this, a);
    nightlyReview(this, a);
    a.conf = clamp(a.conf + (a.mood > 20 ? 0.3 : a.mood < -20 ? -0.3 : 0));
  }

  birth(mother) {
    const father = this.anns.get(mother.preg.father);
    const gender = chance(0.51) ? 'M' : 'F';
    const name = childName(mother.culture, gender, father?.name, mother.name);
    const baby = makeBaby(this, mother, father || mother, name, gender);
    this.anns.set(baby.id, baby);
    mother.kids.push(baby.id);
    if (father) father.kids.push(baby.id);
    mother.preg = null;
    mother.lastBirth = Math.round(this.clock);
    if (mother.home) this.moveHome(baby, this.buildings.get(mother.home));
    for (const p of [mother, father]) if (p) {
      const r = rel(p, baby); r.a = 85; r.f = 50; r.t = 90; r.k = 'child';
      const rb = rel(baby, p); rb.a = 70; rb.f = 50; rb.t = 90; rb.k = 'parent';
      remember(this, p, `${gender === 'M' ? '아들' : '딸'} ${ga(name)} 태어났다`, 10, 1, [baby.id], 'family');
      lifeEvent(this, p, `${gender === 'M' ? '아들' : '딸'} ${name} 탄생`, '👶');
      feel(p, 35, '감격', name);
      if (p.owner) { const pl = this.players[p.owner]; if (pl && baby.owner === p.owner && !pl.anns.includes(baby.id)) pl.anns.push(baby.id); }
    }
    // 형제
    for (const sibId of mother.kids) {
      if (sibId === baby.id) continue;
      const sib = this.anns.get(sibId);
      if (sib && sib.alive) { const r = rel(sib, baby); r.a = 50; r.f = 20; r.k = 'sibling'; const rb = rel(baby, sib); rb.a = 50; rb.f = 20; rb.k = 'sibling'; remember(this, sib, `동생 ${ga(name)} 태어났다`, 6, 0.7, [baby.id], 'family'); }
    }
    think(this, mother, `${name}… 세상에 온 걸 환영해.`);
    if (father) think(this, father, `내가 아빠라니. ${reul(name)} 꼭 지켜 줄 거야.`);
    {
      const hb = mother.home && this.buildings.get(mother.home);
      const where = hb ? BUILDINGS[hb.type].name : '모닥불 곁 천막';
      const cc = this.countries[mother.country];
      lifeEvent(this, baby, `${cc.name} ${this.settlements.get(baby.sid).name}, ${father ? `${wa(father.name)} ${mother.name}` : mother.name}의 ${where}에서 태어났다`, '👶');
      this.first(cc, 'baby', `첫 아기 ${name} — ${mother.name}의 아이`, '👶');
    }
    remember(this, baby, '세상에 태어났다', 10, 1, [mother.id], 'life');
    const fam = mother.kids.length;
    if (mother.goals.some(g => g.k === 'family' && !g.done) && mother.spouse && fam >= 2) this.achieveGoal(mother, mother.goals.find(g => g.k === 'family'));
    this.news('family', `👶 ${father ? `${wa(mother.name)} ${father.name} 사이에서` : `${ga(mother.name)}`} ${gender === 'M' ? '아들' : '딸'} ${ga(name)} 태어났다`, { anns: [baby.id, mother.id, father?.id].filter(Boolean), country: mother.country }, 2);
    this.dirtyAnnMeta.add(baby.id); this.dirtyAnnMeta.add(mother.id); if (father) this.dirtyAnnMeta.add(father.id);
  }

  die(a, cause) {
    a.alive = false;
    a.died = Math.round(this.clock);
    a.cause = cause;
    const age = Math.floor(ageOf(a, a.died));
    if (a.job) leaveJob(this, a, null, true);
    this.leaveHome(a);
    a.act = null;
    lifeEvent(this, a, `${fmtDate(this.clock)} ${age}세로 세상을 떠남 (${cause})`, '🕯️');
    const heirs = [a.spouse, ...a.kids].map(id => this.anns.get(id)).filter(x => x && x.alive);
    if (heirs.length && a.money > 0) { const share = a.money / heirs.length; for (const h of heirs) { h.money += share; book(this, h, 'inc', '상속', share); } a.money = 0; }
    for (const id in a.rels) {
      const b = this.anns.get(+id);
      if (!b || !b.alive) continue;
      const r = b.rels[a.id];
      if (!r) continue;
      const close = r.k === 'spouse' || r.k === 'partner' || r.k === 'child' || r.k === 'parent' || r.k === 'sibling' || r.k === 'best';
      if (close || r.a > 40) {
        remember(this, b, `${ga(a.name)} 세상을 떠났다`, close ? 10 : 6, -0.9, [a.id], 'death');
        feel(b, close ? -35 : -15, '슬픔', a.name);
        b.stress = clamp(b.stress + (close ? 30 : 10));
        think(this, b, close ? pick([`${a.name}… 아직 해 주고 싶은 말이 많았는데.`, `${a.name} 없는 내일을 어떻게 살아가지.`, `${a.name}, 고마웠어. 잘 가.`]) : `${a.name}의 명복을 빈다.`);
        if (r.k === 'spouse') { b.spouse = null; b.partner = null; lifeEvent(this, b, `배우자 ${a.name}와(과) 사별`, '🕯️'); }
        if (r.k === 'partner') b.partner = null;
      } else if (r.a < -40) {
        remember(this, b, `${ga(a.name)} 죽었다고 한다`, 4, 0, [a.id], 'death');
        if (chance(0.5)) think(this, b, '미워했지만… 마음이 이상하다.');
      }
    }
    const c = this.countries[a.country];
    const isLeader = c.leader === a.id;
    this.news('death', `🕯️ ${isLeader ? ERAS[c.era].leader + ' ' : ''}${ga(a.name)} ${age}세로 세상을 떠났다 (${cause})`, { anns: [a.id], country: a.country }, isLeader ? 3 : a.owner ? 2 : 1);
    this.dirtyAnnMeta.add(a.id);
    this.dirtyAct.add(a.id);
  }

  settlementDaily(s) {
    const pop = this.residents(s.id).length;
    s.needPrev = s.need || {};
    s.need = {};
    s.weather = rollWeather(this, s);
    s.pop = pop;
    s.food = Math.max(0, s.food * 0.985); // 썩는다
    const granary = this.findCivic(s, 'granary');
    const cap = 40 + pop * 6 + (granary ? 300 : 0);
    if (s.food > cap) s.food = cap;
    s.heal = (s.heal || 0) * 0.5; s.teach = (s.teach || 0) * 0.5;
    updateSettlementLevel(this, s);
    // 도로: 사람이 다니면 흙길, 관청 부서가 계획하면 공사를 거쳐 좋아진다
    syncRoads(this, s);
    s.works = (s.works || []).filter(id => {
      const b = this.buildings.get(id);
      if (!b) return false;
      if (b.done) { removeBuilding(this, b); return false; }
      if (!b.contractor) assignContractor(this, b);
      return true;
    });
    const hallCo = [...this.companies.values()].find(co => co.pub && co.ind === 'hall' && co.sid === s.id && !co.dead);
    if (hallCo && this.buildings.get(hallCo.b)?.done) {
      hallDepts(this, s, hallCo);
      if (chance(0.12)) planRoadWorks(this, s, hallCo, wageBase(this.countries[s.country].era));
    }
    // 눈: 춥고 눈이 오면 쌓이고, 따뜻하면 녹는다
    const wk = s.weather?.k, temp = s.weather?.temp ?? 10;
    s.snow = Math.max(0, Math.min(1, (s.snow || 0) + (wk === '폭설' ? 0.6 : wk === '눈' ? 0.3 : 0) * (temp <= 2 ? 1 : 0.3) - (temp > 2 ? 0.08 * (temp - 1) : 0.02) - (wk === '비' || wk === '소나기' || wk === '폭우' ? 0.3 : 0)));
    safetyDaily(this, s);
    // 농경 시대: 곡물 창고가 필요해진다
    const c = this.countries[s.country];
    if (c.era >= 1 && !granary && pop >= 6 && ![...s.buildings].some(id => this.buildings.get(id)?.type === 'granary')) placeBuilding(this, s, 'granary', { era: c.era, budget: 0 });
    // 시공사를 못 구한 공사는 건설회사가 생기면 맡긴다
    for (const bid of s.buildings) {
      const b = this.buildings.get(bid);
      if (!b || b.done || b.contractor) continue;
      if (!buildRule(b).self || (b.owner && (this.anns.get(b.owner)?.money || 0) > (BUILDINGS[b.type].cost || 0) * 3)) assignContractor(this, b);
      if (!b.contractor && !buildRule(b).self) { s.need = s.need || {}; s.need.builder = (s.need.builder || 0) + 1; }
    }
    // 산업 시대 이후: 집이 모자라면 건설회사나 정부가 연립·아파트를 짓는다 (분양·임대)
    if (c.era >= 4 && (s.needPrev?.homes || 0) >= 3 && chance(0.15) && !s.buildings.some(id => { const b = this.buildings.get(id); return b && !b.done && b.dev; })) {
      const type = HOME_BY_ERA[Math.min(c.era, 7)];
      const cost = BUILDINGS[type].cost || 0;
      const dev = [...this.companies.values()].filter(co => !co.dead && co.ind === 'construction' && co.sid === s.id && co.money > cost * 0.4).sort((x, y) => y.money - x.money)[0];
      const pubOk = !dev && c.treasury > cost * 0.6 && hasConstruction(this, s.id);
      if (dev || pubOk) {
        const b = placeBuilding(this, s, type, { era: c.era, budget: cost * 0.5, name: `${s.name} ${dev ? dev.name.replace(/(건설|건축|종합건설)$/, '') : '시민'} ${BUILDINGS[type].name}` });
        if (b) {
          b.dev = dev ? dev.id : 'gov';
          if (dev) dev.money -= cost * 0.4; else c.treasury -= cost * 0.6;
          assignContractor(this, b, dev?.id);
          this.news('build', `🏢 ${dev ? dev.name : c.name + ' 정부'}이(가) ${s.name}에 ${reul(BUILDINGS[type].name)} 짓기 시작했다 (${BUILDINGS[type].cap}세대)`, { sid: s.id, country: s.country }, 1);
        }
      }
    }
    // 아무도 살지 않는 낡은 집은 허물어진다 (도시의 세대교체)
    for (const bid of [...s.buildings]) {
      const b = this.buildings.get(bid);
      if (!b || !b.done || BUILDINGS[b.type].kind !== 'home' || b.res.length) continue;
      if (BUILDINGS[b.type].era < c.era - 1 && chance(0.03)) { removeBuilding(this, b); }
    }
    // 사람이 모두 떠난 정착지
    if (pop === 0 && this.clock - s.founded > DAY * 10 && ![...this.anns.values()].some(a => a.alive && a.travel?.to === s.id)) {
      if (!s.abandoned) { s.abandoned = true; this.news('city', `🏚️ ${s.name}은(는) 이제 아무도 살지 않는다`, { sid: s.id, country: s.country }, 1); }
    } else s.abandoned = false;
    this.dirtySettlements.add(s.id);
  }

  daily() {
    this.dfCache.clear();
    this.sinkCache = new Map();
    this.rebuildResidents();
    for (const s of this.settlements.values()) this.settlementDaily(s);
    for (const a of [...this.anns.values()]) if (a.alive) this.annDaily(a);
    for (const co of this.companies.values()) companyDaily(this, co);
    for (const c of Object.values(this.countries)) this.countryDaily(c);
    for (const s of this.settlements.values()) s.pop = this.residents(s.id).length;
    this.rebuildResidents();
    dailyAchievements(this);
  }

  // ── 시계 ──
  step(dt) {
    this.clock += dt;
    this.rebuildResidents();
    for (const a of this.anns.values()) if (a.alive) updateAnn(this, a, dt);
    for (const s of this.settlements.values()) if (s.fireAt || s.fireOff) safetyTick(this, s);
    const day = Math.floor(this.clock / DAY);
    if (day !== this.lastDay) { this.lastDay = day; this.daily(); }
  }
  update(realDtSec) {
    if (this.paused) return;
    let minutes = realDtSec * (this.runScale || this.timeScale) / 60;
    while (minutes > 1e-9) {
      const dt = Math.min(1, minutes);
      this.step(dt);
      minutes -= dt;
    }
  }

  // ── 저장 ──
  toJSON() {
    return {
      v: 1, id: this.id, name: this.name, created: this.created, timeScale: this.timeScale, clock: this.clock, nextIdV: this.nextIdV, paused: !!this.paused,
      nextEventId: this.nextEventId, countries: this.countries, settlements: [...this.settlements.values()], buildings: [...this.buildings.values()],
      companies: [...this.companies.values()], anns: [...this.anns.values()], players: this.players, events: this.events.slice(-300), chat: this.chat.slice(-60),
    };
  }
  static fromJSON(o) {
    const w = new World(o);
    w.nextEventId = o.nextEventId || 1;
    w.countries = o.countries || {};
    for (const s of o.settlements || []) { ensureSite(s); w.settlements.set(s.id, s); }
    for (const b of o.buildings || []) w.buildings.set(b.id, b);
    for (const c of o.companies || []) w.companies.set(c.id, c);
    for (const a of o.anns || []) w.anns.set(a.id, a);
    w.players = o.players || {};
    w.events = o.events || [];
    w.chat = o.chat || [];
    w.lastDay = Math.floor(w.clock / DAY);
    w.paused = !!o.paused;
    for (const p of Object.values(w.players)) { p.ach = p.ach || {}; p.inbox = p.inbox || []; if (p.eraAtJoin == null) p.eraAtJoin = 0; }
    return w;
  }

  // ── 클라이언트용 ──
  summary() {
    const alive = [...this.anns.values()].filter(a => a.alive).length;
    const d = dateOf(this.clock);
    return {
      id: this.id, name: this.name, created: this.created, timeScale: this.timeScale, clock: this.clock,
      date: `${d.year}년 ${d.month}월 ${d.day}일`, players: Object.keys(this.players).length, population: alive,
      countries: Object.keys(this.countries).length, paused: !!this.paused,
    };
  }
  annMeta(a) {
    const c = this.countries[a.country];
    return {
      id: a.id, n: a.name, g: a.gender, o: a.owner, c: a.country, s: a.sid, b: a.born, al: a.alive, d: a.died, j: jobTitle(this, a),
      h: a.home, sp: a.spouse, p: a.partner, lk: a.look, ld: c?.leader === a.id ? ERAS[c.era].leader : null, gen: a.gen, tr: a.travel ? 1 : 0,
      jk: a.job?.id || null, ceo: !!a.job?.ceo, sc: a.school?.name || null,
    };
  }
  annPos(a) {
    return [a.id, a.sid, Math.round(a.x * 10) / 10, Math.round(a.z * 10) / 10, a.alive ? (a.act ? a.act.type : 'idle') : 'dead', a.act?.target || 0, a.travel ? 1 : 0, a.act?.ph === 'move' ? 1 : 0];
  }
  buildingOut(b) {
    let crew = 0;
    if (!b.done && b.contractor) { const co = this.companies.get(b.contractor); if (co) for (const id of co.emp) if (this.anns.get(id)?.job?.site === b.id) crew++; }
    return { id: b.id, sid: b.sid, type: b.type, name: b.name, x: b.x, z: b.z, w: b.w, d: b.d, h: b.h, done: b.done, p: Math.round(b.progress / b.work * 100) / 100, era: b.era, co: b.co, owner: b.owner, res: b.res.length,
      ct: b.contractor || null, crew, work: b.work, started: b.started, built: b.built, stalled: !!b.stalled, dev: b.dev || null,
      ...(b.infra ? { infra: true, segs: b.segs, segDone: b.segDone || 0, grade: b.grade, perSeg: b.perSeg } : {}), ...fireOut(this, b) };
  }
  settlementOut(s) {
    const inst = [];
    for (const co of this.companies.values()) if (co.sid === s.id && !co.dead) inst.push(co.pub ? co.ind : 'co:' + co.ind);
    return {
      id: s.id, name: s.name, country: s.country, lon: s.lon, lat: s.lat, level: s.level, levelName: SETTLEMENT_LEVELS[s.level].name, pop: s.pop, food: Math.round(s.food), founded: s.founded, abandoned: !!s.abandoned, radius: Math.round(s.radius),
      site: s.site, feat: siteFeatures(s), weather: s.weather || null, need: s.needPrev || {}, inst, roads: s.roads || {}, snow: Math.round((s.snow || 0) * 100) / 100,
    };
  }
  countryOut(c) {
    const L = c.leader && this.anns.get(c.leader);
    return {
      code: c.code, name: c.name, era: c.era, eraName: ERAS[c.era].name, tech: Math.round(c.tech), nextTech: ERAS[c.era + 1]?.tech ?? null, curTech: ERAS[c.era].tech,
      leader: L ? { id: L.id, name: L.name, title: ERAS[c.era].leader } : null, pop: c.pop, treasury: Math.round(c.treasury), tax: Math.round(c.tax * 100),
      discoveries: c.discoveries, capital: c.capital, history: c.history.slice(-40), founded: c.founded, topEdu: c.topEdu || 0,
    };
  }
  companyOut(co) {
    let cap = 0;
    for (const bid of [co.b, ...(co.bx || [])]) { const b = this.buildings.get(bid); if (b) cap += BUILDINGS[b.type]?.jobs || 6; }
    // 직원 평균 연봉(만원)과 직무 구성
    let paySum = 0, n = 0;
    const roles = {};
    for (const id of co.emp) { const e = this.anns.get(id); if (!e?.job) continue; paySum += annualOf(fullDayPay(this, e, e.job.id, co), e.job.id); n++; roles[e.job.id] = (roles[e.job.id] || 0) + 1; }
    return { id: co.id, name: co.name, ind: co.ind, pub: co.pub, indName: co.pub ? PUBLIC[co.ind].name : INDUSTRIES[co.ind].name, sid: co.sid, country: co.country, ceo: co.ceo, founder: co.founder, emp: co.emp.length, slots: co.slots, cap, sites: (co.sites || []).length, money: Math.round(co.money), founded: co.founded, dead: co.dead, b: co.b, depts: co.depts || null, pay: n ? Math.round(paySum / n) : 0, roles };
  }
  playerOut(p) { return { id: p.id, name: p.name, country: p.country, color: p.color, anns: p.anns.length, alive: p.anns.filter(id => this.anns.get(id)?.alive).length, joined: p.joined }; }

  initPayload() {
    return {
      world: this.summary(),
      countries: Object.values(this.countries).map(c => this.countryOut(c)),
      settlements: [...this.settlements.values()].map(s => this.settlementOut(s)),
      buildings: [...this.buildings.values()].map(b => this.buildingOut(b)),
      companies: [...this.companies.values()].filter(c => !c.dead).map(c => this.companyOut(c)),
      players: Object.values(this.players).map(p => this.playerOut(p)),
      anns: [...this.anns.values()].map(a => this.annMeta(a)),
      pos: [...this.anns.values()].filter(a => a.alive).map(a => this.annPos(a)),
      events: this.events.slice(-120),
      chat: this.chat.slice(-40),
    };
  }

  tickPayload() {
    // 위치는 바뀐 앤만 보낸다 (휴대폰 데이터 절약). 15초마다 한 번은 전부 보내서 놓친 것을 맞춘다
    this.tickN = (this.tickN || 0) + 1;
    const full = this.tickN % 15 === 0;
    const last = this._lastPos || (this._lastPos = new Map());
    const pos = [];
    for (const a of this.anns.values()) {
      if (!a.alive && !this.dirtyAct.has(a.id)) { last.delete(a.id); continue; }
      const row = this.annPos(a), key = row.join(',');
      if (full || last.get(a.id) !== key) { pos.push(row); last.set(a.id, key); }
    }
    const p = { clock: this.clock, pos };
    if (full) p.full = 1;
    if (this.dirtyAnnMeta.size) p.anns = [...this.dirtyAnnMeta].map(id => this.anns.get(id)).filter(Boolean).map(a => this.annMeta(a));
    if (this.dirtyBuildings.size) p.buildings = [...this.dirtyBuildings].map(id => this.buildings.get(id)).filter(Boolean).map(b => this.buildingOut(b));
    if (this.removedBuildings.size) p.removed = [...this.removedBuildings];
    if (this.dirtySettlements.size) p.settlements = [...this.dirtySettlements].map(id => this.settlements.get(id)).filter(Boolean).map(s => this.settlementOut(s));
    if (this.dirtyCountries.size) p.countries = [...this.dirtyCountries].map(c => this.countries[c]).filter(Boolean).map(c => this.countryOut(c));
    if (this.dirtyCompanies.size) p.companies = [...this.dirtyCompanies].map(id => this.companies.get(id)).filter(Boolean).map(c => this.companyOut(c));
    if (this.newEvents.length) p.events = this.newEvents;
    if (this.newChat.length) p.chat = this.newChat;
    return p;
  }

  annDetail(id) {
    const a = this.anns.get(id);
    if (!a) return null;
    const now = a.alive ? this.clock : a.died;
    const age = ageOf(a, now);
    const nm = x => { const b = x && this.anns.get(x); return b ? { id: b.id, name: b.name, alive: b.alive } : null; };
    const rels = Object.entries(a.rels).map(([id, r]) => {
      const b = this.anns.get(+id);
      return b ? { id: +id, name: b.name, alive: b.alive, k: r.k, kind: RELATION_NAMES[r.k] || r.k, f: Math.round(r.f), a: Math.round(r.a), t: Math.round(r.t), r: Math.round(r.r), op: opinionText(r) } : null;
    }).filter(Boolean).sort((x, y) => (y.f + Math.abs(y.a) + y.r) - (x.f + Math.abs(x.a) + x.r)).slice(0, 24);
    const act = a.act;
    const s = this.settlements.get(a.sid);
    const c = this.countries[a.country];
    const home = a.home && this.buildings.get(a.home);
    const co = a.job?.co && this.companies.get(a.job.co);
    const siblings = new Set();
    for (const pid of a.parents) { const p = this.anns.get(pid); if (p) for (const k of p.kids) if (k !== a.id) siblings.add(k); }
    return {
      ...this.annMeta(a), age: Math.floor(age), ageExact: age, stage: STAGE_NAMES[ageStage(age)], culture: a.culture, orient: a.orient,
      tr: a.tr, ab: a.ab, amb: a.amb, val: a.val, goals: a.goals, sk: a.sk, skillNames: SKILLS, traitNames: TRAIT_NAMES, abilityNames: ABILITY_NAMES,
      nd: a.nd, mood: Math.round(a.mood), stress: Math.round(a.stress), health: Math.round(a.health), conf: Math.round(a.conf), emo: a.emo,
      money: Math.round(a.money), edu: Math.round(a.edu), rep: Math.round(a.rep), fame: Math.round(a.fame), sick: Math.round(a.sick), cause: a.cause,
      job: a.job ? { title: jobTitle(this, a), id: a.job.id, name: JOBS[a.job.id]?.name, since: a.job.since, rank: a.job.rank, sat: Math.round(a.job.sat), co: co ? { id: co.id, name: co.name } : null, ceo: a.job.ceo } : null,
      career: a.career.slice(-10), retired: a.retired,
      family: { spouse: nm(a.spouse), partner: nm(a.partner), parents: a.parents.map(nm).filter(Boolean), kids: a.kids.map(nm).filter(Boolean), siblings: [...siblings].map(nm).filter(Boolean) },
      preg: a.preg ? { due: a.preg.due } : null,
      rels,
      act: act ? { type: act.type, label: ACTS[act.type]?.label, emoji: ACTS[act.type]?.emoji, ph: act.ph, target: nm(act.target), topic: act.topic, line: act.line, until: act.until } : null,
      mem: a.mem.slice(-30).reverse(), th: a.th.slice(-25).reverse(), why: a.why, life: a.life,
      home: home ? { id: home.id, name: home.name, done: home.done } : null,
      settlement: s ? { id: s.id, name: s.name } : null, country: c ? { code: c.code, name: c.name } : null,
      owner: a.owner && this.players[a.owner] ? { id: a.owner, name: this.players[a.owner].name, color: this.players[a.owner].color } : null,
      techMade: Math.round(a.techMade || 0), discoveries: a.discoveries || [], wantsMigrate: a.wantsMigrate || null,
      school: a.school ? { name: a.school.name, key: a.school.key, grade: Math.round(a.school.grade), since: a.school.since } : null,
      eduLv: a.eduLv || 0, eduName: EDU_NAMES[a.eduLv || 0], born: a.born, pdeploy: !!a.pdeploy,
      today: a.today || [], yesterday: a.yesterday || [], shift: a.job && JOBS[a.job.id]?.shift ? SHIFTS[JOBS[a.job.id].shift].label : null,
      site: a.job?.site && this.buildings.get(a.job.site) ? { id: a.job.site, name: siteName(this, this.buildings.get(a.job.site)), p: Math.round(this.buildings.get(a.job.site).progress / this.buildings.get(a.job.site).work * 100) } : null,
      fin: this.finOut(a, co, home),
    };
  }
  /** 돈: 연봉, 이번 달·지난달 가계부, 재산, 집, 차 (모두 원) */
  finOut(a, co, home) {
    const c = this.countries[a.country];
    const salary = a.job ? annualOf(fullDayPay(this, a, a.job.id, co || null), a.job.id) : 0;
    const spouseHome = a.spouse && this.anns.get(a.spouse)?.home === a.home;
    const housing = !home ? '집 없음' : home.dev ? '월세' : home.owner === a.id || (spouseHome && home.owner === a.spouse) ? '자가' : home.owner != null ? '가족 집에 함께' : '공동 거처';
    return { cash: Math.round(a.money * 100) / 100, salary: Math.round(salary * 100) / 100, era: c?.era ?? 0, led: ledgerOut(a.led), prev: ledgerOut(a.ledPrev), housing, car: !!a.car };
  }
}

function sanitizeSpec(raw = {}) {
  const num = (v, d, lo = 1, hi = 100) => clamp(Math.round(v != null && v !== '' && Number.isFinite(+v) ? +v : d), lo, hi);
  const rnd = (sd = 18) => clamp(Math.round(50 + gauss() * sd), 5, 95);
  const traits = {}, abilities = {};
  // 정하지 않은 값은 무작위 (모두가 똑같은 사람이 되지 않도록)
  for (const k of ['O', 'C', 'E', 'A', 'N']) traits[k] = num(raw.traits?.[k], rnd());
  for (const k of ['int', 'ath', 'cre', 'cha', 'hea']) abilities[k] = num(raw.abilities?.[k], rnd(15));
  // 능력치 총합 제한 (슈퍼맨 방지)
  const sum = Object.values(abilities).reduce((s, v) => s + v, 0);
  if (sum > 300) for (const k in abilities) abilities[k] = Math.max(1, Math.round(abilities[k] * 300 / sum));
  const name = String(raw.name || '').trim().slice(0, 20) || '이름없음';
  return {
    name, gender: raw.gender === 'F' ? 'F' : 'M', age: num(raw.age, 22, 16, 45), traits, abilities, ambition: num(raw.ambition, rnd(22)),
    orient: ['straight', 'gay', 'bi'].includes(raw.orient) ? raw.orient : undefined,
  };
}

// 받침에 따른 조사 (지역 도우미)
function ro_(w) { const c = w.charCodeAt(w.length - 1); const b = c >= 0xac00 && c <= 0xd7a3 ? (c - 0xac00) % 28 : 0; return b && b !== 8 ? '으로' : '로'; }
function ga_(w) { const c = w.charCodeAt(w.length - 1); const b = c >= 0xac00 && c <= 0xd7a3 ? (c - 0xac00) % 28 : 0; return b ? '이' : '가'; }
