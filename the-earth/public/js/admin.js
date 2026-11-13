// 관리자 페이지: 서버 열기·멈추기·지우기, 플레이어·앤 이름 바꾸기·내보내기, 신고 처리, 계정 정지, 백업 되돌리기, 오류 기록.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const AUTH = 'theEarth.auth.v1';
let key = sessionStorage.getItem('earth.adminKey') || '';
const token = () => { try { return JSON.parse(localStorage.getItem(AUTH) || 'null')?.token || ''; } catch { return ''; } };

async function api(path, body, method) {
  const headers = {};
  if (token()) headers.Authorization = 'Bearer ' + token();
  if (key) headers['X-Admin-Key'] = key;
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method: method || (body ? 'POST' : 'GET'), headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}
const when = t => t ? new Date(t).toLocaleString('ko-KR') : '-';
const dur = s => s > 86400 ? `${Math.floor(s / 86400)}일 ${Math.floor(s % 86400 / 3600)}시간` : s > 3600 ? `${Math.floor(s / 3600)}시간 ${Math.floor(s % 3600 / 60)}분` : `${Math.floor(s / 60)}분`;
const mb = b => `${(b / 1048576).toFixed(0)}MB`;

let tab = 'overview', servers = [], curServer = null;

async function start() {
  if (!token() && !key) { $('#login').classList.remove('hidden'); return; }
  try {
    const o = await api('/api/admin/overview');
    $('#login').classList.add('hidden');
    $('#main').classList.remove('hidden');
    $('#who').textContent = key ? '관리자 키로 접속' : '관리자 계정';
    servers = o.worlds;
    curServer = curServer || servers[0]?.id;
    render();
  } catch (e) {
    $('#login').classList.remove('hidden');
    $('#main').classList.add('hidden');
    if (token() || key) $('#lg-err').textContent = e.message;
  }
}

$('#lg-go').onclick = async () => {
  try {
    const r = await api('/api/auth/login', { username: $('#lg-user').value, password: $('#lg-pass').value });
    localStorage.setItem(AUTH, JSON.stringify({ token: r.token, account: r.account }));
    if (!r.account.admin) throw new Error('관리자 계정이 아닙니다');
    start();
  } catch (e) { $('#lg-err').textContent = e.message; }
};
$('#lg-key-go').onclick = () => { key = $('#lg-key').value.trim(); sessionStorage.setItem('earth.adminKey', key); start(); };
$('#tabs').onclick = e => {
  const b = e.target.closest('[data-tab]');
  if (!b) return;
  tab = b.dataset.tab;
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  render();
};

async function render() {
  const body = $('#body');
  try {
    const o = await api('/api/admin/overview');
    servers = o.worlds;
    $('#rep-n').textContent = o.openReports || '';
    if (tab === 'overview') body.innerHTML = overview(o);
    if (tab === 'servers') body.innerHTML = serversView(o);
    if (tab === 'players') body.innerHTML = await playersView();
    if (tab === 'reports') body.innerHTML = reportsView(await api('/api/admin/reports'));
    if (tab === 'accounts') body.innerHTML = accountsView(await api('/api/admin/accounts'));
    if (tab === 'backups') body.innerHTML = backupsView(await api('/api/admin/backups'));
    if (tab === 'errors') body.innerHTML = errorsView(await api('/api/admin/errors'));
  } catch (e) { body.innerHTML = `<div class="card err">${esc(e.message)}</div>`; }
}

const overview = o => `<div class="card"><h2>서버 상태</h2><div class="stats">
  <div class="stat"><b>${dur(o.uptime)}</b><small>가동 시간</small></div>
  <div class="stat"><b>${o.tick.avgMs}ms</b><small>1초 처리 시간 (최대 ${o.tick.maxMs}ms, 느림 ${o.tick.slow}회)</small></div>
  <div class="stat"><b>${mb(o.mem.rss)}</b><small>메모리 (힙 ${mb(o.mem.heap)})</small></div>
  <div class="stat"><b>${o.clients}</b><small>지금 접속 (최고 ${o.wsPeak})</small></div>
  <div class="stat"><b>${o.accounts}</b><small>가입 계정</small></div>
  <div class="stat"><b>${o.openReports}</b><small>처리 안 된 신고</small></div>
  </div><p class="sub">Node ${esc(o.node)} · DB ${esc(o.dbPath)}</p></div>
  <div class="card"><h2>월드</h2>${worldTable(o.worlds, false)}</div>`;

const worldTable = (ws, acts) => `<div class="tablewrap"><table><tr><th>서버</th><th>날짜</th><th>인구</th><th>플레이어</th><th>건물·회사</th><th>관전</th><th>저장</th>${acts ? '<th></th>' : ''}</tr>
  ${ws.map(w => `<tr><td><b>${esc(w.name)}</b> <span class="sub">${w.id}</span> ${w.paused ? '<span class="tag paused">일시정지</span>' : ''}</td><td>${esc(w.date)}<br><span class="sub">×${w.timeScale}</span></td><td>${w.population} / ${w.anns}</td><td>${w.players}</td><td>${w.buildings} · ${w.companies}</td><td>${w.watchers}</td><td>${w.saveMs}ms<br><span class="sub">${w.saveKB}KB</span></td>
  ${acts ? `<td><div class="acts"><button class="small" data-a="pause" data-id="${w.id}" data-v="${w.paused ? 0 : 1}">${w.paused ? '▶ 다시 시작' : '⏸ 일시정지'}</button><button class="small" data-a="rename" data-id="${w.id}">이름</button><button class="small" data-a="save" data-id="${w.id}">지금 저장·백업</button><button class="small danger" data-a="delete" data-id="${w.id}">삭제</button></div></td>` : ''}</tr>`).join('')}</table></div>`;

const serversView = o => `<div class="card"><h2>새 서버 열기</h2><div class="row"><input id="new-name" placeholder="서버 이름 (예: 제2서버 · 새벽)" maxlength="30"><button class="primary" data-a="create">열기 — 1년 1월 1일부터</button></div></div>
  <div class="card"><h2>서버 관리</h2>${worldTable(o.worlds, true)}<p class="sub">일시정지하면 시간이 멈추고 접속자 화면에 '점검 중'이 표시됩니다. 삭제 전 마지막 백업이 남습니다.</p></div>`;

async function playersView() {
  const opts = servers.map(w => `<option value="${w.id}" ${w.id === curServer ? 'selected' : ''}>${esc(w.name)}</option>`).join('');
  const list = curServer ? await api(`/api/admin/servers/${curServer}/players`) : [];
  return `<div class="card"><div class="row"><h2 style="margin:0">플레이어</h2><select id="srv-pick">${opts}</select></div>
    <div class="tablewrap"><table><tr><th>이름</th><th>계정</th><th>나라</th><th>앤 (생존)</th><th>업적</th><th>가입</th><th></th></tr>
    ${list.map(p => `<tr><td><b>${esc(p.name)}</b> <span class="sub">${p.id}</span></td><td>${esc(p.account || '손님')}</td><td>${esc(p.country)}</td><td>${p.anns} (${p.alive})</td><td>${p.ach}</td><td>${when(p.joinedReal)}</td>
      <td><div class="acts"><button class="small" data-a="prename" data-id="${p.id}">이름 바꾸기</button><button class="small danger" data-a="premove" data-id="${p.id}">내보내기</button></div></td></tr>`).join('') || '<tr><td colspan="7" class="sub">플레이어가 없습니다</td></tr>'}</table></div>
    <h2 style="margin-top:16px">앤 이름 바꾸기</h2><div class="row"><input id="ann-id" placeholder="앤 번호" style="width:110px"><input id="ann-name" placeholder="새 이름" maxlength="20"><button data-a="arename">바꾸기</button></div>
    <p class="sub">내보낸 플레이어의 앤들은 주인 없이 세상에 남아 계속 살아갑니다.</p></div>`;
}

const reportsView = list => `<div class="card"><h2>신고</h2><div class="tablewrap"><table><tr><th>#</th><th>시각</th><th>서버</th><th>대상</th><th>사유</th><th>상태</th><th></th></tr>
  ${list.map(r => `<tr><td>${r.id}</td><td>${when(r.created)}</td><td>${esc(r.server)}</td><td>${esc(r.target_kind)} <b>${esc(r.target_name)}</b> <span class="sub">${esc(r.target_id)}</span></td><td>${esc(r.reason)}</td><td><span class="tag ${r.status === 'open' ? 'open' : 'ok'}">${r.status === 'open' ? '대기' : r.status === 'done' ? '처리함' : '기각'}</span></td>
    <td><div class="acts">${r.target_kind === 'ann' ? `<button class="small" data-a="rep-rename-ann" data-id="${r.id}" data-s="${esc(r.server)}" data-t="${esc(r.target_id)}">앤 이름 바꾸기</button>` : ''}${r.target_kind === 'player' ? `<button class="small" data-a="rep-rename-player" data-id="${r.id}" data-s="${esc(r.server)}" data-t="${esc(r.target_id)}">플레이어 이름 바꾸기</button>` : ''}<button class="small" data-a="rep" data-id="${r.id}" data-v="done">처리함</button><button class="small" data-a="rep" data-id="${r.id}" data-v="dismissed">기각</button></div></td></tr>`).join('') || '<tr><td colspan="7" class="sub">신고가 없습니다</td></tr>'}</table></div></div>`;

const accountsView = list => `<div class="card"><h2>계정 (최근 200)</h2><div class="tablewrap"><table><tr><th>#</th><th>아이디</th><th>가입</th><th>마지막 접속</th><th>상태</th><th></th></tr>
  ${list.map(a => `<tr><td>${a.id}</td><td><b>${esc(a.username)}</b> ${a.admin ? '<span class="tag ok">관리자</span>' : ''}</td><td>${when(a.created)}</td><td>${when(a.last_seen)}</td><td>${a.banned ? '<span class="tag open">정지</span>' : '정상'}</td>
    <td><div class="acts"><button class="small ${a.banned ? '' : 'danger'}" data-a="ban" data-id="${a.id}" data-v="${a.banned ? 0 : 1}">${a.banned ? '정지 풀기' : '이용 정지'}</button><button class="small" data-a="admin" data-id="${a.id}" data-v="${a.admin ? 0 : 1}">${a.admin ? '관리자 빼기' : '관리자로'}</button></div></td></tr>`).join('')}</table></div></div>`;

const backupsView = list => `<div class="card"><h2>백업 (월드마다 최근 24개, 1시간 간격)</h2><div class="tablewrap"><table><tr><th>서버</th><th>시각</th><th>크기</th><th></th></tr>
  ${list.map(b => `<tr><td>${esc(b.id)}</td><td>${when(b.saved)}</td><td>${Math.round(b.size / 1024)}KB</td><td><button class="small danger" data-a="restore" data-id="${esc(b.id)}" data-v="${b.saved}">이 시점으로 되돌리기</button></td></tr>`).join('') || '<tr><td colspan="4" class="sub">백업이 없습니다</td></tr>'}</table></div>
  <p class="sub">되돌리기 전에 지금 상태도 백업해 둡니다.</p></div>`;

const errorsView = list => `<div class="card"><h2>최근 오류</h2>${list.map(e => `<div style="margin-bottom:10px"><b>${when(e.t)}</b> <span class="tag">${esc(e.where_)}</span><pre>${esc(e.msg)}</pre></div>`).join('') || '<p class="sub">기록된 오류가 없습니다 👍</p>'}</div>`;

$('#body').onchange = e => { if (e.target.id === 'srv-pick') { curServer = e.target.value; render(); } };
$('#body').onclick = async e => {
  const b = e.target.closest('[data-a]');
  if (!b) return;
  const { a, id, v } = b.dataset;
  try {
    if (a === 'create') { await api('/api/admin/servers', { name: $('#new-name').value }); }
    if (a === 'pause') await api(`/api/admin/servers/${id}/pause`, { paused: v === '1' });
    if (a === 'rename') { const n = prompt('새 서버 이름'); if (!n) return; await api(`/api/admin/servers/${id}/rename`, { name: n }); }
    if (a === 'save') { const r = await api(`/api/admin/servers/${id}/save`, {}); alert(`저장·백업 완료 (${Math.round(r.ms)}ms)`); }
    if (a === 'delete') { const c = prompt(`정말 지우려면 서버 ID '${id}'를 입력하세요. 모든 앤이 사라집니다.`); if (c !== id) return; await api(`/api/admin/servers/${id}`, { confirm: c }, 'DELETE'); }
    if (a === 'prename') { const n = prompt('새 플레이어 이름'); if (!n) return; await api(`/api/admin/servers/${curServer}/players/${id}/rename`, { name: n }); }
    if (a === 'premove') { if (!confirm('이 플레이어를 내보낼까요? 앤들은 주인 없이 계속 살아갑니다.')) return; await api(`/api/admin/servers/${curServer}/players/${id}/remove`, {}); }
    if (a === 'arename') { await api(`/api/admin/servers/${curServer}/anns/${+$('#ann-id').value}/rename`, { name: $('#ann-name').value }); alert('바꿨습니다'); }
    if (a === 'rep') await api(`/api/admin/reports/${id}`, { status: v });
    if (a === 'rep-rename-ann') { const n = prompt('새 앤 이름'); if (!n) return; await api(`/api/admin/servers/${b.dataset.s}/anns/${b.dataset.t}/rename`, { name: n }); await api(`/api/admin/reports/${id}`, { status: 'done' }); }
    if (a === 'rep-rename-player') { const n = prompt('새 플레이어 이름'); if (!n) return; await api(`/api/admin/servers/${b.dataset.s}/players/${b.dataset.t}/rename`, { name: n }); await api(`/api/admin/reports/${id}`, { status: 'done' }); }
    if (a === 'ban') await api(`/api/admin/accounts/${id}/ban`, { banned: v === '1' });
    if (a === 'admin') await api(`/api/admin/accounts/${id}/admin`, { admin: v === '1' });
    if (a === 'restore') { if (!confirm(`${id} 서버를 ${when(+v)} 시점으로 되돌릴까요? 그 뒤의 일은 사라집니다 (지금 상태는 백업해 둡니다).`)) return; await api('/api/admin/backups/restore', { id, saved: +v }); }
    render();
  } catch (err) { alert(err.message); }
};

start();
setInterval(() => { if (tab === 'overview' && !$('#main').classList.contains('hidden')) render(); }, 10_000);
