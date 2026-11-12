// 앤 만들기: 성격(Big Five), 능력(총 300점), 야망. 투입 후에는 조종할 수 없다.
import { api } from '../net.js';
import { avatarURL } from './avatar.js';
import { josa } from './josa.js';

const TRAITS = [
  ['O', '개방성', '호기심·상상력', '익숙한 것이 좋음', '새로운 것에 끌림'],
  ['C', '성실성', '책임감·계획성', '즉흥적', '꼼꼼하고 부지런함'],
  ['E', '외향성', '사교성·활력', '혼자가 편함', '사람들 속에서 빛남'],
  ['A', '친화성', '공감·배려', '경쟁적·냉정', '다정하고 너그러움'],
  ['N', '신경성', '불안·예민함', '태연하고 침착', '감정의 기복이 큼'],
];
const ABILS = [['int', '지능'], ['ath', '운동신경'], ['cre', '창의성'], ['cha', '매력'], ['hea', '건강']];
export const BUDGET = 300;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const r50 = (sd = 18) => Math.max(5, Math.min(95, Math.round(50 + (Math.random() + Math.random() + Math.random() - 1.5) * sd * 1.4)));

export class Creator {
  constructor(root, opts) {
    this.root = root;
    this.opts = opts; // {culture, countryName, room, onDeploy}
    this.anns = [];
    this.cur = 0;
  }

  async randomAnn(gender) {
    const g = gender || (this.anns.filter(a => a.gender === 'M').length <= this.anns.filter(a => a.gender === 'F').length ? 'M' : 'F');
    let name = '';
    try { name = (await api(`/api/random-name?culture=${this.opts.culture}&gender=${g}`)).name; } catch { name = g === 'M' ? '앤' : '안나'; }
    const ab = {};
    let rest = BUDGET;
    const keys = ABILS.map(x => x[0]);
    const w = keys.map(() => 0.5 + Math.random());
    const ws = w.reduce((s, v) => s + v, 0);
    keys.forEach((k, i) => { ab[k] = Math.max(10, Math.min(95, Math.round(w[i] / ws * rest))); });
    return {
      name, gender: g, age: 18 + Math.floor(Math.random() * 10),
      traits: Object.fromEntries(TRAITS.map(t => [t[0], r50()])), abilities: ab, ambition: r50(22),
      id: Math.random(), look: { style: Math.floor(Math.random() * 4) },
    };
  }

  async open() {
    if (!this.anns.length) {
      const n = Math.min(4, this.opts.room);
      for (let i = 0; i < n; i++) this.anns.push(await this.randomAnn(i % 2 ? 'F' : 'M'));
    }
    this.render();
  }

  render() {
    const $ = s => this.root.querySelector(s);
    $('#creator-country').textContent = `· ${this.opts.countryName}`;
    $('#roster').innerHTML = this.anns.map((a, i) => `
      <div class="roster-item ${i === this.cur ? 'on' : ''}" data-i="${i}">
        <img src="${avatarURL({ ...a, g: a.gender, lk: { ...a.look, skin: '#e9c5a2', hair: '#2b2018' } }, '#58b4ff', 48)}" width="30" height="30" style="border-radius:50%">
        <span class="nm">${esc(a.name)}<br><small class="sub small">${a.gender === 'M' ? '남' : '여'} · ${a.age}세</small></span>
        <button class="x" data-del="${i}" title="빼기">×</button>
      </div>`).join('');
    $('#roster-room').textContent = `${this.anns.length} / ${this.opts.room}명 (한 플레이어 최대 ${this.opts.max}명)`;
    this.root.querySelector('[data-act=add-ann]').disabled = this.anns.length >= this.opts.room;
    this.renderEditor();
    $('#roster').onclick = e => {
      const del = e.target.closest('[data-del]');
      if (del) { this.anns.splice(+del.dataset.del, 1); this.cur = Math.max(0, Math.min(this.cur, this.anns.length - 1)); this.render(); return; }
      const it = e.target.closest('[data-i]');
      if (it) { this.cur = +it.dataset.i; this.render(); }
    };
  }

  renderEditor() {
    const ed = this.root.querySelector('#editor');
    const a = this.anns[this.cur];
    if (!a) { ed.innerHTML = '<p class="empty">앤을 추가하세요.</p>'; return; }
    const used = ABILS.reduce((s, [k]) => s + a.abilities[k], 0);
    ed.innerHTML = `
      <div>
        <h4>기본</h4>
        <label class="field"><span>이름</span><input data-f="name" maxlength="20" value="${esc(a.name)}"></label>
        <div class="ed-row" style="margin-top:12px">
          <div class="seg"><button data-g="M" class="${a.gender === 'M' ? 'on' : ''}">남자</button><button data-g="F" class="${a.gender === 'F' ? 'on' : ''}">여자</button></div>
          <button class="btn small" data-act="rand-one">🎲 무작위</button>
        </div>
        <div class="slider" style="margin-top:12px"><span class="lab">나이</span><input type="range" min="16" max="45" value="${a.age}" data-f="age"><b>${a.age}</b></div>
        <div class="slider"><span class="lab">야망<small>꿈의 크기</small></span><input type="range" min="1" max="100" value="${a.ambition}" data-f="ambition"><b>${a.ambition}</b></div>
      </div>
      <div>
        <h4>능력 <em class="${used > BUDGET ? 'over' : ''}">${used} / ${BUDGET}점</em></h4>
        ${ABILS.map(([k, n]) => `<div class="slider"><span class="lab">${n}</span><input type="range" min="1" max="100" value="${a.abilities[k]}" data-ab="${k}"><b>${a.abilities[k]}</b></div>`).join('')}
      </div>
      <div style="grid-column:1/-1">
        <h4>성격</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 26px">
        ${TRAITS.map(([k, n, d, lo, hi]) => `<div class="slider"><span class="lab">${n}<small>${a.traits[k] < 35 ? lo : a.traits[k] > 65 ? hi : d}</small></span><input type="range" min="1" max="100" value="${a.traits[k]}" data-tr="${k}"><b>${a.traits[k]}</b></div>`).join('')}
        </div>
      </div>
      <div class="preview">
        <img src="${avatarURL({ ...a, g: a.gender, lk: { ...a.look, skin: '#e9c5a2', hair: '#2b2018' } }, '#58b4ff', 96)}" width="72" height="72" style="border-radius:50%">
        <div>${this.describe(a)}</div>
      </div>`;
    ed.oninput = e => {
      const t = e.target;
      if (t.dataset.f === 'name') { a.name = t.value; this.root.querySelector(`.roster-item.on .nm`).firstChild.textContent = t.value; return; }
      if (t.dataset.f) a[t.dataset.f] = +t.value;
      if (t.dataset.tr) a.traits[t.dataset.tr] = +t.value;
      if (t.dataset.ab) {
        const others = ABILS.reduce((s, [k]) => s + (k === t.dataset.ab ? 0 : a.abilities[k]), 0);
        a.abilities[t.dataset.ab] = Math.min(+t.value, BUDGET - others);
        t.value = a.abilities[t.dataset.ab];
      }
      // 드래그가 끊기지 않도록 필요한 부분만 고친다
      t.nextElementSibling.textContent = t.value;
      if (t.dataset.tr) {
        const T = TRAITS.find(x => x[0] === t.dataset.tr);
        t.previousElementSibling.querySelector('small').textContent = a.traits[T[0]] < 35 ? T[3] : a.traits[T[0]] > 65 ? T[4] : T[2];
      }
      const used = ABILS.reduce((sum, [k]) => sum + a.abilities[k], 0);
      const em = ed.querySelector('h4 em');
      em.textContent = `${used} / ${BUDGET}점`;
      ed.querySelector('.preview > div').innerHTML = this.describe(a);
      if (t.dataset.f === 'age') this.root.querySelector('.roster-item.on small').textContent = `${a.gender === 'M' ? '남' : '여'} · ${a.age}세`;
    };
    ed.onclick = async e => {
      const g = e.target.closest('[data-g]');
      if (g) { a.gender = g.dataset.g; this.render(); return; }
      if (e.target.closest('[data-act=rand-one]')) { this.anns[this.cur] = await this.randomAnn(a.gender); this.render(); }
    };
  }

  /** 서버의 가치관 공식과 같은 방식으로 꿈을 미리 짐작 */
  describe(a) {
    const { O, C, E, A, N } = a.traits, { int, ath, cre, cha } = a.abilities, amb = a.ambition;
    const v = {
      '행복한 가정': 25 + A * 0.35 + (100 - O) * 0.1 + (100 - amb) * 0.1, '부자 / 창업': amb * 0.45 + (100 - A) * 0.15 + C * 0.1 + 6,
      '위대한 발견': O * 0.35 + int * 0.4, '나라의 지도자': amb * 0.5 + cha * 0.2 + (100 - A) * 0.15, '걸작 남기기': cre * 0.5 + O * 0.25,
      '새 땅 개척': O * 0.35 + ath * 0.15 + (100 - N) * 0.2, '최고의 운동선수': ath * 0.6 + E * 0.1, '사랑받는 사람': A * 0.35 + E * 0.35, '최고의 장인': C * 0.45 + amb * 0.2,
    };
    const top = Object.entries(v).sort((x, y) => y[1] - x[1]).slice(0, 3).map(x => x[0]);
    const lines = [];
    if (E > 65) lines.push('사람을 만나야 힘이 나는 타입'); else if (E < 35) lines.push('혼자만의 시간이 소중한 타입');
    if (N > 65) lines.push('작은 일에도 마음이 크게 흔들림'); else if (N < 35) lines.push('웬만한 일엔 흔들리지 않음');
    if (C > 65) lines.push('맡은 일은 끝까지 해냄'); else if (C < 35) lines.push('그날 기분 따라 움직임');
    if (A > 65) lines.push('남의 고민을 잘 들어줌'); else if (A < 35) lines.push('싸움을 피하지 않음');
    if (O > 65) lines.push('늘 새로운 걸 궁금해함');
    const nm = josa(a.name || '이 앤', '은/는');
    const dream = josa(top.join(' · '), '을/를');
    return `<p><b>${esc(nm.slice(0, -1))}</b>${nm.slice(-1)} ${lines.slice(0, 3).join(', ') || '무난한 성격'}.</p>
      <p class="sub">아마 <b style="color:var(--accent-2)">${esc(dream.slice(0, -1))}</b>${dream.slice(-1)} 꿈꾸게 될 거예요. 하지만 삶이 어떻게 흘러갈지는 앤 자신만이 정합니다.</p>`;
  }
}
