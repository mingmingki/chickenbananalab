// 서버가 감당할 수 있는 세계 인구: 쓸 수 있는 메모리(systemd MemoryMax · 컨테이너 한도 · 기계 전체)에 맞춘다.
// 재 본 값 (게임 30일 뒤): 1,300명 → 평소 350MB · 저장할 때 390MB, 5,000명 → 690MB · 820MB, 10,000명 → 1.27GB · 1.53GB
//   → 사람 한 명에 0.13MB 안팎 + 바탕 230MB. 시간이 지나며 기억이 쌓이는 몫까지 넉넉히 둔다.
import { readFileSync } from 'node:fs';
import os from 'node:os';

/** 이 프로세스가 쓸 수 있는 메모리 (바이트) */
export function memLimit() {
  if (process.env.EARTH_MEM_BYTES) return +process.env.EARTH_MEM_BYTES;
  try {
    const cg = readFileSync('/proc/self/cgroup', 'utf8').split('\n').find(l => l.startsWith('0::'))?.slice(3).trim();
    if (cg != null) {
      const v = readFileSync('/sys/fs/cgroup' + (cg === '/' ? '' : cg) + '/memory.max', 'utf8').trim();
      if (v !== 'max' && +v > 0) return +v;
    }
  } catch { /* cgroup v2 가 아니거나 읽을 수 없다 */ }
  try { const n = +readFileSync('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'utf8').trim(); if (n > 0 && n < 1e15) return n; } catch { /* */ }
  return os.totalmem();
}

/** 처음 세계 인구 목표 — 1.5GB 면 5,000명, 600MB 면 1,200명 안팎. EARTH_POP 로 정할 수도 있다 */
export function popTarget() {
  if (process.env.EARTH_POP) return Math.max(200, +process.env.EARTH_POP);
  const mb = memLimit() / 1e6;
  return Math.max(600, Math.min(5000, Math.round((mb * 0.62 - 230) / 0.13 / 100) * 100));
}

/** 인구가 목표를 한참 넘으면 아이가 덜 태어난다 (목표의 1.1배부터 줄어 1.4배면 멈춘다) — 메모리를 지키려고 */
export function birthDamp(pop, target) {
  const p = pop / Math.max(1, target);
  return p < 1.1 ? 1 : Math.max(0, 1 - (p - 1.1) * 3.3);
}
