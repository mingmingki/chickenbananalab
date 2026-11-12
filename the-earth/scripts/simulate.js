// 헤드리스 시뮬레이션: 게임 시간을 빠르게 돌려서 세계가 어떻게 흘러가는지 본다
// 사용법: node scripts/simulate.js [일수=120] [플레이어수=2] [국가=KR]
import { World } from '../server/sim/world.js';
import { loadGeo } from '../server/geo.js';
import { fmtDateTime, ageOf, DAY } from '../server/sim/util.js';
import { ERAS, SETTLEMENT_LEVELS } from '../server/sim/data.js';
import { ACTS } from '../server/sim/mind.js';

const days = +(process.argv[2] || 120);
const nPlayers = +(process.argv[3] || 2);
const country = process.argv[4] || 'KR';
loadGeo();
const w = new World({ id: 'sim', name: 'sim', timeScale: 24 });
w.techSrc = {};
const KO = ['민준', '서연', '도윤', '지우', '하준', '서윤', '시우', '하은', '주원', '지민', '예준', '수아'];
for (let p = 0; p < nPlayers; p++) {
  const pl = w.joinPlayer('P' + (p + 1), country);
  const specs = [];
  for (let i = 0; i < 8; i++) specs.push({ name: '김이박최'[p % 4] + KO[(i + p * 3) % KO.length], gender: i % 2 ? 'F' : 'M', age: 18 + Math.floor(Math.random() * 12), ambition: 30 + Math.random() * 60 });
  w.deployAnns(pl, specs);
}

const t0 = Date.now();
let lastReport = 0;
const counts = {};
const final_ = false;
for (let m = 0; m < days * DAY; m++) {
  w.step(1);
  if (m % 30 === 0) for (const a of w.anns.values()) if (a.alive && a.act) counts[a.act.type] = (counts[a.act.type] || 0) + 1;
  if (!final_ && w.clock - lastReport >= DAY * Math.max(10, Math.floor(days / 12))) {
    lastReport = w.clock;
    report();
  }
  w.resetDirty();
}
report(true);

function report(final = false) {
  const alive = [...w.anns.values()].filter(a => a.alive);
  const c = w.countries[country];
  const jobs = {};
  for (const a of alive) { const k = a.job ? a.job.id + (a.job.ceo ? '(대표)' : '') : (ageOf(a, w.clock) < 19 ? '(아이)' : '(무직)'); jobs[k] = (jobs[k] || 0) + 1; }
  const homes = alive.filter(a => a.home).length;
  const married = alive.filter(a => a.spouse).length / 2;
  const couples = alive.filter(a => a.partner && !a.spouse).length / 2;
  const kids = alive.filter(a => ageOf(a, w.clock) < 19).length;
  const dead = [...w.anns.values()].filter(a => !a.alive).length;
  const friends = alive.reduce((s, a) => s + Object.values(a.rels).filter(r => r.k === 'friend' || r.k === 'best').length, 0) / Math.max(1, alive.length);
  const avgMoney = alive.reduce((s, a) => s + a.money, 0) / Math.max(1, alive.length);
  const avgMood = alive.reduce((s, a) => s + a.mood, 0) / Math.max(1, alive.length);
  console.log(`\n=== ${fmtDateTime(w.clock)} (실행 ${((Date.now() - t0) / 1000).toFixed(1)}s) ===`);
  console.log(`인구 ${alive.length} (아이 ${kids}, 사망 ${dead}) | 집 있음 ${homes} | 부부 ${married} 연인 ${couples} | 평균 친구 ${friends.toFixed(1)} | 평균 재산 ${avgMoney.toFixed(0)} | 평균 기분 ${avgMood.toFixed(0)}`);
  console.log(`${c.name}: ${ERAS[c.era].name} 기술 ${c.tech.toFixed(0)}/${ERAS[c.era + 1]?.tech} 국고 ${c.treasury.toFixed(0)} 지도자 ${c.leader ? w.anns.get(c.leader)?.name : '-'} 발견 [${c.discoveries.join(', ')}]`);
  for (const s of w.settlements.values()) {
    const bs = s.buildings.map(id => w.buildings.get(id)).filter(Boolean);
    const types = {};
    for (const b of bs) types[b.type + (b.done ? '' : '*')] = (types[b.type + (b.done ? '' : '*')] || 0) + 1;
    console.log(`  ${s.name} [${SETTLEMENT_LEVELS[s.level].name}] 인구 ${s.pop} 식량 ${s.food.toFixed(0)} 건물 ${JSON.stringify(types)}`);
  }
  const cos = [...w.companies.values()].filter(c => !c.dead);
  console.log(`회사 ${cos.length}: ${cos.map(c => `${c.name}(${c.emp.length}명,${c.money.toFixed(0)})`).join(', ')}  폐업 ${[...w.companies.values()].filter(c => c.dead).length}`);
  console.log('직업', JSON.stringify(jobs));
  console.log('기술 출처', Object.entries(w.techSrc).sort((a,b)=>b[1]-a[1]).map(([k,v])=>k+' '+v.toFixed(0)).join(', '));
  if (final) {
    const tot = Object.values(counts).reduce((s, v) => s + v, 0);
    console.log('행동 비율', Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${ACTS[k]?.label || k} ${(v / tot * 100).toFixed(1)}%`).join(', '));
    const sample = alive.filter(a => a.gen === 1).slice(0, 2);
    for (const a of sample) {
      console.log(`\n--- ${a.name} (${Math.floor(ageOf(a, w.clock))}세) 기분 ${a.mood.toFixed(0)} 감정 ${a.emo.k} 재산 ${a.money.toFixed(0)} 꿈: ${a.goals.map(g => g.text + (g.done ? '✓' : '')).join(' / ')}`);
      console.log('생각:', a.th.slice(-6).map(t => t.text).join(' | '));
      console.log('기억:', a.mem.filter(m => m.imp >= 5).slice(-6).map(m => m.text).join(' | '));
      console.log('고민:', JSON.stringify(a.why));
      console.log('인생:', a.life.map(l => l.icon + l.text).join(' / '));
    }
    console.log('\n최근 뉴스:');
    for (const e of w.events.slice(-25)) console.log(' ', fmtDateTime(e.t), e.text);
  }
}
