// 철거: 낡은 건물을 허물어야 그 자리에 새로 지을 수 있다.
// 철거 현장(임시 현장)이 열리면 철거업체(없으면 건설회사, 그것도 없으면 일용직·이웃)가 사람을 보내 해체한다.
// 다 허물면 건물이 사라지고, 집이 모자란 마을이면 나라가 그 자리에 새 집을 짓는다.
import { BUILDINGS, HOME_BY_ERA } from './data.js';
import { assignContractor, wageBase } from './economy.js';
import { placeBuilding, removeBuilding } from './settlement.js';
import { lifeEvent, think, remember } from './ann.js';
import { chance, pick, reul } from './util.js';

/** 허무는 데 드는 일(대략 사람×시간): 덩치와 구조에 따라 */
export function demolitionWork(b) {
  const def = BUILDINGS[b.type] || {};
  const era = def.era ?? 0;
  const k = era <= 1 ? 0.25 : era <= 3 ? 0.4 : 0.5;   // 흙·나무집은 쉽고, 철근 콘크리트는 오래 걸린다
  return Math.round(Math.min(20000, Math.max(16, b.w * b.d * Math.max(2, b.h) * k)));
}

/** 철거 현장 열기 (이미 열려 있으면 그대로) */
export function startDemolition(world, s, target, why = '낡은 빈집') {
  if (!target || target.demo || !target.done) return null;
  const c = world.countries[s.country];
  const work = demolitionWork(target);
  // 비용: 나라가 낸다 (모자라면 예산 없이 — 회사는 일한 만큼 매출로 번다)
  const cost = Math.round(work * wageBase(c.era) / 8 * 0.9);
  const pay = c.treasury > cost * 2 ? cost : 0;
  c.treasury -= pay;
  const side = target.d / 2 + 4;
  const b = {
    id: world.newId(), type: 'demolish', sid: s.id, name: `${target.name} 철거`, target: target.id,
    x: target.x + target.w * 0.35, z: target.z + side, w: 8, d: 6, h: 0, rot: 0, cells: [0, 0, 0, 0],
    done: false, progress: 0, work, budget: pay, started: Math.round(world.clock), era: c.era, owner: null, co: null, res: [],
  };
  world.buildings.set(b.id, b);
  s.works = s.works || [];
  s.works.push(b.id);
  target.demo = b.id; target.demoP = 0;
  s.demoCount = (s.demoCount || 0) + 1;
  world.dirtyBuildings.add(b.id); world.dirtyBuildings.add(target.id);
  assignContractor(world, b);
  const co = b.contractor && world.companies.get(b.contractor);
  if (work >= 120 || co) world.news('build', `🚧 ${co ? co.name : s.name + ' 사람들'}이(가) ${s.name}의 ${reul(target.name)} 허물기 시작했다 (${why})`, { sid: s.id, country: s.country }, 1);
  return b;
}

/** 철거 진척을 건물에 비춘다 (화면에서 조금씩 허물어져 보이게) */
export function demolitionProgress(world, site) {
  const t = world.buildings.get(site.target);
  if (!t) return;
  const p = Math.min(1, site.progress / Math.max(1, site.work));
  if (Math.abs((t.demoP || 0) - p) >= 0.04 || p >= 1) { t.demoP = Math.round(p * 100) / 100; world.dirtyBuildings.add(t.id); }
}

/** 다 허물었다: 건물을 없애고, 필요하면 그 자리에 새로 짓는다 */
export function finishDemolition(world, site) {
  const t = world.buildings.get(site.target);
  const s = world.settlements.get(site.sid);
  if (!t || !s) return;
  const lot = t.cells && t.cells[2] ? [t.cells[0], t.cells[1]] : null;
  const name = t.name, kind = BUILDINGS[t.type]?.kind;
  removeBuilding(world, t);
  const co = site.contractor && world.companies.get(site.contractor);
  world.news('build', `🧱 ${s.name}의 ${name} 철거가 끝났다${co ? ` — ${co.name}` : ''} (${Math.round(site.mh || 0)}인시)`, { sid: s.id, country: s.country }, 1);
  // 일한 사람들
  if (co) for (const id of co.emp) {
    const e = world.anns.get(id);
    if (!e || e.job?.site !== site.id) continue;
    e.job.site = null;
    lifeEvent(world, e, `'${name}' 철거를 마쳤다`, '🧱');
    if (chance(0.3)) think(world, e, pick(['허물어야 새로 짓는다.', '먼지를 한 바가지는 먹은 것 같다.', `저 ${name}에도 누군가의 세월이 있었겠지.`, '다음 현장은 어디려나.']));
    world.dirtyAnnMeta.add(e.id);
  }
  // 집이 모자라면 그 자리에 새 집 (나라 돈으로 짓기 시작한다)
  const c = world.countries[s.country];
  if (kind === 'home' && lot && c.era >= 2 && ((s.needPrev?.homes || 0) > 0 || chance(0.6))) {
    const type = c.era >= 4 ? 'townhouse' : HOME_BY_ERA[Math.min(c.era, 3)] || 'house';
    const def = BUILDINGS[type];
    if (def && def.cells[0] === 1 && def.cells[1] === 1) {
      const cost = def.cost || 0;
      if (c.treasury > cost * 0.8) {
        const nb = placeBuilding(world, s, type, { era: c.era, budget: cost * 0.5, lot, name: `${s.name} 새 ${def.name}` });
        if (nb) { c.treasury -= cost * 0.5; nb.dev = 'gov'; assignContractor(world, nb); }
      }
    }
  }
}
