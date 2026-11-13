// 서버(월드) 저장소: SQLite(server/db.js)에 월드 스냅숏을 저장하고, 한 시간마다 백업을 남긴다.
import { World } from './sim/world.js';
import { loadWorldJsons, saveWorldJson, backupWorld, deleteWorld as dbDelete, DATA_DIR } from './db.js';
export { DATA_DIR };

const BACKUP_EVERY_MS = 3600_000;

export function loadWorlds() {
  const worlds = new Map();
  for (const { id, json } of loadWorldJsons()) {
    try {
      const w = World.fromJSON(JSON.parse(json));
      w._lastBackup = Date.now();
      worlds.set(w.id, w);
    } catch (e) {
      console.error('월드 불러오기 실패', id, e.message);
    }
  }
  return worlds;
}

/** 저장에 걸린 시간(ms)을 돌려준다 */
export function saveWorld(w, { backup = false } = {}) {
  const t0 = performance.now();
  const json = JSON.stringify(w.toJSON());
  saveWorldJson(w.id, json);
  if (backup || !w._lastBackup || Date.now() - w._lastBackup > BACKUP_EVERY_MS) { backupWorld(w.id, json); w._lastBackup = Date.now(); }
  w._saveMs = performance.now() - t0;
  w._saveBytes = json.length;
  return w._saveMs;
}

export function deleteWorld(id) { dbDelete(id); }
