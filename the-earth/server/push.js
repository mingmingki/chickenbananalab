// 웹 푸시 알림: 앱을 닫아 둬도 내 앤의 큰 소식(탄생·결혼·죽음·업적…)을 휴대폰으로 받는다.
// 아이폰은 iOS 16.4 이상에서 '홈 화면에 추가'한 앱에서만 받을 수 있다.
import webpush from 'web-push';
import { kvGet, kvSet, pushSubsOf, removePushSub, logError } from './db.js';

let ready = false, publicKey = '';
export function initPush() {
  let pub = process.env.VAPID_PUBLIC_KEY, priv = process.env.VAPID_PRIVATE_KEY;
  if (!pub || !priv) {
    pub = kvGet('vapid.public'); priv = kvGet('vapid.private');
    if (!pub || !priv) { const k = webpush.generateVAPIDKeys(); pub = k.publicKey; priv = k.privateKey; kvSet('vapid.public', pub); kvSet('vapid.private', priv); }
  }
  const subject = process.env.VAPID_SUBJECT || (process.env.PUBLIC_URL ? process.env.PUBLIC_URL : 'mailto:noreply@example.com');
  try { webpush.setVapidDetails(subject, pub, priv); ready = true; publicKey = pub; } catch (e) { console.error('푸시 설정 실패:', e.message); }
}
export const pushPublicKey = () => (ready ? publicKey : null);

/** 계정의 모든 기기로 보낸다. 만료된 구독은 지운다 */
export async function sendPush(accountId, payload) {
  if (!ready) return 0;
  let sent = 0;
  for (const sub of pushSubsOf(accountId)) {
    try { await webpush.sendNotification(sub, JSON.stringify(payload), { TTL: 6 * 3600, urgency: payload.urgent ? 'high' : 'normal' }); sent++; }
    catch (e) {
      if (e.statusCode === 404 || e.statusCode === 410) removePushSub(sub.endpoint);
      else logError('push', e.body || e.message);
    }
  }
  return sent;
}

// 알림이 한꺼번에 몰리지 않게 계정마다 모아서 보낸다: 중요한 소식은 2분, 나머지는 20분 간격
const queue = new Map(); // account → { notes: [], last: 0 }
export function queuePush(accountId, note, serverName) {
  let q = queue.get(accountId);
  if (!q) queue.set(accountId, q = { notes: [], last: 0, server: serverName });
  q.notes.push(note);
  if (q.notes.length > 30) q.notes.splice(0, q.notes.length - 30);
}
export function flushPush(isWatching) {
  const now = Date.now();
  for (const [acc, q] of queue) {
    if (!q.notes.length) continue;
    const urgent = q.notes.some(n => n.lvl >= 3);
    if (now - q.last < (urgent ? 120_000 : 1_200_000)) continue;
    const notes = q.notes.splice(0);
    q.last = now;
    if (isWatching(acc)) continue; // 지금 앱을 보고 있으면 화면 안 알림으로 충분
    const top = notes.slice().sort((a, b) => b.lvl - a.lvl)[0];
    const title = notes.length > 1 ? `${top.icon} ${top.name ? top.name + ' — ' : ''}외 소식 ${notes.length - 1}건` : `${top.icon} ${top.name || 'The Earth'}`;
    sendPush(acc, { title, body: notes.slice(0, 4).map(n => `${n.icon} ${n.name ? n.name + ': ' : ''}${n.text}`).join('\n'), tag: 'notes', urgent, url: '/' }).catch(() => {});
  }
}
