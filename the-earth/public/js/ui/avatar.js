// 앤 얼굴 그리기 (작은 초상화)
const cache = new Map();

export function avatarURL(a, color = '#58b4ff', size = 64) {
  const look = a.lk || a.look || {};
  const key = [a.id, a.g, look.skin, look.hair, look.style, color, size, a.age < 13 ? 'k' : a.age > 64 ? 'o' : ''].join('|');
  if (cache.has(key)) return cache.get(key);
  const c = document.createElement('canvas'); c.width = c.height = size;
  const g = c.getContext('2d');
  const s = size / 64;
  g.fillStyle = color; g.fillRect(0, 0, size, size);
  const grd = g.createLinearGradient(0, 0, 0, size); grd.addColorStop(0, 'rgba(255,255,255,0.25)'); grd.addColorStop(1, 'rgba(0,0,0,0.25)');
  g.fillStyle = grd; g.fillRect(0, 0, size, size);
  const skin = look.skin || '#e8c39e', hair = a.age > 64 ? '#cfcfcf' : (look.hair || '#2b1d14');
  // 어깨
  g.fillStyle = 'rgba(0,0,0,0.25)'; g.beginPath(); g.ellipse(32 * s, 66 * s, 24 * s, 16 * s, 0, 0, 6.28); g.fill();
  // 뒷머리 (긴 머리)
  const long = a.g === 'F' ? (look.style ?? 1) % 3 !== 2 : (look.style ?? 0) === 3;
  g.fillStyle = hair;
  if (long) { g.beginPath(); g.ellipse(32 * s, 36 * s, 17 * s, 21 * s, 0, 0, 6.28); g.fill(); }
  // 얼굴
  g.fillStyle = skin; g.beginPath(); g.ellipse(32 * s, 33 * s, 13.5 * s, 15.5 * s, 0, 0, 6.28); g.fill();
  // 앞머리
  g.fillStyle = hair; g.beginPath();
  g.ellipse(32 * s, 23 * s, 14.5 * s, 9 * s, 0, Math.PI, 0); g.fill();
  if ((look.style ?? 0) % 2) { g.beginPath(); g.ellipse(25 * s, 23 * s, 8 * s, 6 * s, 0.5, 0, 6.28); g.fill(); }
  // 눈, 입
  g.fillStyle = '#222';
  g.beginPath(); g.arc(27 * s, 34 * s, 1.6 * s, 0, 6.28); g.arc(37 * s, 34 * s, 1.6 * s, 0, 6.28); g.fill();
  g.strokeStyle = 'rgba(120,50,40,0.8)'; g.lineWidth = 1.4 * s; g.beginPath(); g.arc(32 * s, 39 * s, 4 * s, 0.2, Math.PI - 0.2); g.stroke();
  g.fillStyle = 'rgba(255,120,120,0.25)'; g.beginPath(); g.arc(24 * s, 39 * s, 3 * s, 0, 6.28); g.arc(40 * s, 39 * s, 3 * s, 0, 6.28); g.fill();
  const url = c.toDataURL();
  cache.set(key, url);
  return url;
}

export function avatarHTML(a, color, cls = '') {
  return `<div class="avatar ${cls}" style="--c:${color}"><img src="${avatarURL(a, color)}" alt="" style="width:100%;height:100%;display:block"></div>`;
}
