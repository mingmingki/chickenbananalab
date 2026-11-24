// 공유 카드: 앤의 순간(족장이 됨, 나라를 세움, 노래가 퍼짐, 결혼, 아기…)이나 일생을 그림 한 장으로.
// 휴대폰에서는 공유 창(카카오톡·문자·인스타)으로 바로 보내고, 안 되면 그림 저장 + 링크 복사.
// 링크를 누른 사람은 그 앤을 바로 들여다본다 (/?w=서버&a=앤).
import { avatarURL } from './avatar.js';

const W = 1080, H = 1350;
const C = { paper: '#fff6e0', ink: '#1b1a17', k: '#1b1a17', y: '#ffd23f', soft: '#6b6458', line: '#e9dcc0' };

function wrap(g, text, maxW) {
  const out = [];
  let line = '';
  for (const ch of String(text)) {
    if (ch === '\n') { out.push(line); line = ''; continue; }
    const t = line + ch;
    if (g.measureText(t).width > maxW && line) {
      // 단어 중간을 덜 자르도록: 마지막 공백에서 끊는다
      const sp = line.lastIndexOf(' ');
      if (sp > line.length * 0.5) { out.push(line.slice(0, sp)); line = line.slice(sp + 1) + ch; } else { out.push(line); line = ch; }
    } else line = t;
  }
  if (line) out.push(line);
  return out;
}
function round(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
const img = src => new Promise(res => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = src; });

/** 앤을 바로 들여다보는 링크 */
export function annLink(serverId, annId) {
  const u = new URL(location.origin + '/');
  u.searchParams.set('w', serverId); u.searchParams.set('a', annId);
  return u.toString();
}

/**
 * 카드 그리기 → Blob
 * o: { ann (meta: n, g, lk, age), color, icon, title, sub, lines[], foot }
 */
export async function drawCard(o) {
  try { await Promise.all([document.fonts.load('90px "Black Han Sans"'), document.fonts.load('40px "Pretendard"')]); } catch { /* 글꼴이 없어도 그린다 */ }
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const g = cv.getContext('2d');
  const TITLE = '"Black Han Sans", "Apple SD Gothic Neo", sans-serif', BODY = '"Pretendard", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif';
  // 종이 바탕 + 굵은 테두리 (스티커 느낌)
  g.fillStyle = '#2b2a27'; g.fillRect(0, 0, W, H);
  g.fillStyle = C.paper; round(g, 36, 36, W - 72, H - 72, 48); g.fill();
  g.lineWidth = 10; g.strokeStyle = C.k; g.stroke();
  // 위쪽 노란 띠
  g.save(); round(g, 36, 36, W - 72, 300, 48); g.clip(); g.fillStyle = C.y; g.fillRect(36, 36, W - 72, 300); g.restore();
  g.beginPath(); g.moveTo(36, 336); g.lineTo(W - 36, 336); g.lineWidth = 8; g.stroke();
  g.fillStyle = C.ink; g.font = `64px ${TITLE}`; g.textBaseline = 'top';
  g.fillText('THE EARTH', 96, 92);
  g.font = `34px ${BODY}`; g.fillStyle = '#3d3a33';
  g.fillText('지구 위에서 스스로 살아가는 사람들', 96, 176);
  // 앤 얼굴
  const face = await img(avatarURL({ ...o.ann, age: o.ann.age ?? 30 }, o.color || '#58b4ff', 256));
  const fx = W - 96 - 230, fy = 70;
  g.save(); g.beginPath(); g.arc(fx + 115, fy + 115, 115, 0, Math.PI * 2); g.closePath(); g.clip();
  if (face) g.drawImage(face, fx, fy, 230, 230); g.restore();
  g.beginPath(); g.arc(fx + 115, fy + 115, 115, 0, Math.PI * 2); g.lineWidth = 10; g.strokeStyle = C.k; g.stroke();
  // 큰 제목
  let y = 390;
  g.fillStyle = C.ink; g.font = `112px ${BODY}`;
  if (o.icon) { g.fillText(o.icon, 92, y - 6); }
  g.font = `76px ${TITLE}`;
  for (const l of wrap(g, o.title, W - 200 - (o.icon ? 0 : 0)).slice(0, 3)) { g.fillText(l, o.icon ? 230 : 96, y + 14); y += 92; }
  y = Math.max(y + 10, 560);
  // 이름 · 부족/나라 · 나이
  g.font = `bold 48px ${BODY}`; g.fillStyle = C.ink;
  g.fillText(o.ann.n || '', 96, y); y += 64;
  if (o.sub) { g.font = `36px ${BODY}`; g.fillStyle = C.soft; for (const l of wrap(g, o.sub, W - 192).slice(0, 2)) { g.fillText(l, 96, y); y += 48; } }
  y += 18;
  g.beginPath(); g.moveTo(96, y); g.lineTo(W - 96, y); g.setLineDash([16, 14]); g.lineWidth = 4; g.strokeStyle = C.line; g.stroke(); g.setLineDash([]);
  y += 36;
  // 이야기 몇 줄
  g.font = `38px ${BODY}`; g.fillStyle = C.ink;
  for (const ln of (o.lines || []).slice(0, 6)) {
    const rows = wrap(g, ln, W - 250);
    if (y + rows.length * 52 > H - 220) break;
    g.fillStyle = C.y; g.beginPath(); g.arc(112, y + 22, 10, 0, Math.PI * 2); g.fill(); g.lineWidth = 3; g.strokeStyle = C.k; g.stroke();
    g.fillStyle = C.ink;
    for (const r of rows) { g.fillText(r, 140, y); y += 52; }
    y += 14;
  }
  // 아래: 서버·날짜 + 들어오라는 말
  g.font = `32px ${BODY}`; g.fillStyle = C.soft;
  g.fillText(o.foot || '', 96, H - 190);
  g.font = `40px ${TITLE}`; g.fillStyle = C.ink;
  g.fillText('이 앤의 지금을 보러 오세요 →', 96, H - 140);
  return new Promise(res => cv.toBlob(b => res(b), 'image/png'));
}

/** 공유하기: 휴대폰 공유 창 → 안 되면 미리보기 창에서 저장 · 링크 복사 */
export async function shareCard(o, { modal, toast } = {}) {
  const blob = await drawCard(o);
  if (!blob) { toast?.('그림을 만들지 못했어요'); return; }
  const file = new File([blob], 'the-earth.png', { type: 'image/png' });
  const text = `${o.title} — ${o.ann.n} · The Earth`;
  try {
    if (navigator.canShare?.({ files: [file] })) { await navigator.share({ files: [file], title: 'The Earth', text: `${text}\n${o.url}`, url: o.url }); return; }
  } catch (e) { if (e?.name === 'AbortError') return; }
  const url = URL.createObjectURL(blob);
  const res = modal ? await modal(`<h3>📸 공유 카드</h3>
    <img src="${url}" alt="" style="width:100%;max-width:360px;display:block;margin:8px auto;border-radius:14px;border:3px solid var(--k)">
    <div class="acts"><a class="btn" href="${url}" download="the-earth.png" data-m="save">그림 저장</a><button class="btn primary" data-m="copy">링크 복사</button><button class="btn ghost" data-m="close">닫기</button></div>`) : null;
  if (res === 'copy') { try { await navigator.clipboard.writeText(`${text}\n${o.url}`); toast?.('링크를 복사했어요'); } catch { toast?.(o.url); } }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
