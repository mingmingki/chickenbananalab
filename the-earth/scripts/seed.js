// 개발용: 미리 몇 년을 돌려 둔 데모 월드를 만든다 (화면 확인용).
// 사용법: DATA_DIR=/tmp/earth node scripts/seed.js [일수=900] [서버id=demo]
// 실제 서버는 언제나 1년 1월 1일에 시작한다. 이 스크립트는 별도 DATA_DIR 에서만 쓰자.
import { World } from '../server/sim/world.js';
import { loadGeo } from '../server/geo.js';
import { saveWorld } from '../server/store.js';
import { fmtDate, DAY } from '../server/sim/util.js';

const days = +(process.argv[2] || 900);
const id = process.argv[3] || 'demo';
loadGeo();
const w = new World({ id, name: `데모 서버 (${days}일 진행)`, timeScale: 10 });
const groups = [['한별', 'KR', 10], ['Mika', 'JP', 8], ['Alex', 'US', 8]];
for (const [name, code, n] of groups) {
  const p = w.joinPlayer(name, code);
  const specs = Array.from({ length: n }, (_, i) => ({ name: '', gender: i % 2 ? 'F' : 'M', age: 18 + (i * 3) % 12, ambition: 30 + (i * 17) % 60 }));
  const c = w.countries[code];
  const { randomName } = await import('../server/sim/names.js');
  for (const s of specs) s.name = randomName(c.culture, s.gender);
  w.deployAnns(p, specs);
}
for (let m = 0; m < days * DAY; m++) { w.step(1); if (m % 600 === 0) w.resetDirty(); }
w.resetDirty();
saveWorld(w);
console.log(`${w.name}: ${fmtDate(w.clock)}, 인구 ${[...w.anns.values()].filter(a => a.alive).length}`);
for (const c of Object.values(w.countries)) console.log(` ${c.name} 시대 ${c.era} 기술 ${Math.round(c.tech)}`);
