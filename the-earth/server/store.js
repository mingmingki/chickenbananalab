// 서버(월드) 저장소: data/worlds/<id>.json
import { mkdirSync, readdirSync, readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { World } from './sim/world.js';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
export const DATA_DIR = process.env.DATA_DIR || join(ROOT, 'data', 'worlds');

export function loadWorlds() {
  mkdirSync(DATA_DIR, { recursive: true });
  const worlds = new Map();
  for (const f of readdirSync(DATA_DIR)) {
    if (!f.endsWith('.json')) continue;
    try {
      const w = World.fromJSON(JSON.parse(readFileSync(join(DATA_DIR, f), 'utf8')));
      worlds.set(w.id, w);
    } catch (e) {
      console.error('월드 불러오기 실패', f, e.message);
    }
  }
  return worlds;
}

export function saveWorld(w) {
  mkdirSync(DATA_DIR, { recursive: true });
  const file = join(DATA_DIR, `${w.id}.json`);
  const tmp = file + '.tmp';
  writeFileSync(tmp, JSON.stringify(w.toJSON()));
  renameSync(tmp, file);
}

export function worldFileExists(id) {
  return existsSync(join(DATA_DIR, `${id}.json`));
}
