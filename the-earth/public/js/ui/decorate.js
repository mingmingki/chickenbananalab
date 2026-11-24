// 화면에서 이모지를 걷어 내고 선 아이콘(glyphs.js)으로 바꾼다.
//  · 서버가 보내는 글(소식·연대기·질문·말풍선)과 화면 코드에 박힌 이모지를 따로 고치지 않고,
//    화면에 붙는 순간 바꾼다 (MutationObserver). 3D 이름표처럼 문자열로 만드는 곳은 emojiToGlyph 로.
//  · 표에 없는 이모지는 작은 점으로. ♂ ♀ ♪ 같은 글자 기호는 그대로 둔다.
import { GLYPHS, EMOJI } from './glyphs.js';

const RE = /\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic}️?)*️?/gu;
const HAS = /\p{Extended_Pictographic}/u;
const KEEP = new Set(['♂', '♀', '♪', '©', '®', '™', '‼', '⁉', '↔', '↕', '↩', '↪', '▶', '◀', '⏏', '⬆', '⬇', '⬅', '➡', '↗', '↘', '↙', '↖', '〰', '〽', '©️', '®️']);
const esc = s => s.replace(/[&<>]/g, c => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;'));

/** 아이콘 하나 (글자 크기를 따른다) */
export function glyph(name, tint = '') {
  const b = GLYPHS[name];
  return b ? `<svg class="gl${tint ? ' t-' + tint : ''}" viewBox="0 0 24 24" aria-hidden="true">${b}</svg>` : '';
}
/** 이모지 → [아이콘, 빛깔] */
// glyphs.js 표에 없는 것 (속삭임·도전·명예의 전당에서 쓰는 이모지)
const MORE = { '🫶': ['hand-heart', 'pink'], '🤞': ['hand-heart', 'pink'], '📣': ['volume-2', 'violet'], '🫐': ['sprout', 'green'], '💎': ['gem', 'violet'], '🤔': ['circle-help', 'violet'], '😶': ['meh', 'slate'], '😤': ['angry', 'red'], '🌄': ['sunrise', 'amber'] };
export function glyphOf(e) {
  const base = String(e || '').replace(/️/g, '');
  return EMOJI[base] || MORE[base] || EMOJI[[...base][0]] || MORE[[...base][0]] || null;
}
/** 그림(캔버스)에 그릴 아이콘 — 독립된 SVG 문서 */
export function glyphSVG(name, color = '#1b1a17', size = 120, width = 2) {
  const b = GLYPHS[name] || GLYPHS.dot;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round">${b.replace(/currentColor/g, color)}</svg>`;
}
const keepOrGlyph = m => {
  if (KEEP.has(m) || KEEP.has(m[0])) return m;
  const g = glyphOf(m);
  return g ? glyph(g[0], g[1]) : glyph('dot', 'muted');
};
/** HTML 문자열 안의 이모지를 아이콘으로 (속성 값 안에는 이모지를 넣지 않는 곳에만 쓴다) */
export function emojiToGlyph(html) { return HAS.test(html) ? html.replace(RE, keepOrGlyph) : html; }

const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'OPTION', 'CANVAS', 'TITLE']);
function fixText(t) {
  const p = t.parentNode;
  if (!p || !HAS.test(t.data) || SKIP.has(p.nodeName) || p.closest?.('svg,[data-noglyph]')) return;
  const raw = esc(t.data), html = raw.replace(RE, keepOrGlyph);
  if (html === raw) return;   // 남길 글자 기호뿐
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  p.replaceChild(tpl.content, t);
}
/** 이 요소 안의 글에 있는 이모지를 모두 바꾼다 */
export function decorate(root) {
  if (!root) return;
  if (root.nodeType === 3) return fixText(root);
  if (root.nodeType !== 1 || SKIP.has(root.nodeName) || root.namespaceURI === 'http://www.w3.org/2000/svg') return;
  if (!HAS.test(root.textContent || '')) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const list = [];
  while (w.nextNode()) if (HAS.test(w.currentNode.data)) list.push(w.currentNode);
  for (const t of list) fixText(t);
}
/** 앞으로 화면에 붙는 글도 계속 바꾼다 */
export function autoDecorate(target = document.body) {
  decorate(target);
  const mo = new MutationObserver(ms => {
    for (const m of ms) {
      if (m.type === 'characterData') fixText(m.target);
      else for (const n of m.addedNodes) decorate(n);
    }
  });
  mo.observe(target, { childList: true, subtree: true, characterData: true });
  // 문서 제목(탭 이름)은 글자로 남긴다
  return mo;
}
