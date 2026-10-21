// Shared by every view: templates (htm on Preact), the API, formatting and the small Markdown renderer.
import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { signal } from '@preact/signals';
import htm from 'htm';

export const html = htm.bind(h);
export const enc = encodeURIComponent;

export async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}
export const get = (u) => api('GET', u);
export const post = (u, b = {}) => api('POST', u, b);
export const del = (u) => api('DELETE', u);

/** Grow a textarea to its content; `rows` stays the floor and CSS max-height the ceiling (it scrolls past that). */
export function fit(el) {
  if (!el?.offsetWidth) return;  // hidden: scrollHeight would be 0
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
}
/** Phones have no Shift key handy: there Enter is a newline and the button sends. */
export const touch = matchMedia('(pointer: coarse)').matches;

// The discussion dock's width, shared by every page that has one; dragged or arrowed, double-click resets.
const setDockW = (w) => {
  if (w) document.documentElement.style.setProperty('--dock-w', `${w}px`);
  else document.documentElement.style.removeProperty('--dock-w');
  w ? localStorage.setItem('p2b.dockW', w) : localStorage.removeItem('p2b.dockW');
};
setDockW(+localStorage.getItem('p2b.dockW') || 0);
/** Placed inside the dock; the dock's parent is the split it resizes. */
export function DockGrip() {
  const clamp = (el, w) => Math.round(Math.min(Math.max(w, 280), el.parentElement.parentElement.clientWidth - 360));
  const down = (e) => {
    const el = e.currentTarget, right = el.parentElement.parentElement.getBoundingClientRect().right;
    el.setPointerCapture(e.pointerId);
    el.classList.add('on');
    el.onpointermove = (m) => setDockW(clamp(el, right - m.clientX));
    el.onpointerup = el.onpointercancel = () => { el.onpointermove = el.onpointerup = el.onpointercancel = null; el.classList.remove('on'); };
  };
  const key = (e) => {
    const d = { ArrowLeft: 24, ArrowRight: -24 }[e.key];
    if (d) { e.preventDefault(); setDockW(clamp(e.currentTarget, e.currentTarget.parentElement.offsetWidth + d)); }
  };
  return html`<div class="dock-grip" role="separator" aria-orientation="vertical" aria-label="拖动调整讨论栏宽度" tabIndex="0" title="拖动调整宽度，双击恢复默认"
    onPointerDown=${down} onKeyDown=${key} onDblClick=${() => setDockW(0)}></div>`;
}

// ---------- formatting ----------

export function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  const hm = d.toTimeString().slice(0, 5);
  if (d.toDateString() === new Date().toDateString()) return hm;
  if (d.toDateString() === new Date(Date.now() - 864e5).toDateString()) return `昨天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
export const CHANNEL = { wechat: '公众号', xhs: '小红书', arxiv: 'arXiv', doi: 'DOI', openreview: 'OpenReview', web: '网页', find: '找论文' };
export const idLabel = (pid) => {
  const [k, ...r] = pid.split(':');
  const ref = r.join(':');
  return { arxiv: `arXiv ${ref}`, doi: `DOI ${ref}`, openreview: `OpenReview ${ref}` }[k] || 'PDF';
};
export const channel = (c) => CHANNEL[c.channel] || (c.source === 'shortcut' ? 'iPhone' : c.source === 'web' ? '手动' : '命令行');
export const authors = (a) => (a || []).length ? a.slice(0, 3).join(', ') + (a.length > 3 ? ' 等' : '') : '';
export const firstLine = (s) => (s || '').split('\n').find((l) => l.trim()) || '';
export const short = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s || '');
export const live = (status) => ['WAITING', 'CREATING', 'RUNNING', 'QUEUED'].includes(status);

/** True while some on-screen mark is spinning, so the corner logo can follow without a fresh /api/state. */
export const agentBusy = signal(false);
/** Corner logo spins while a mark on screen is spinning, or a run is still unfinished. */
export const workHot = (state) => agentBusy.value || (state?.working?.length || 0) > 0 || (state?.counts?.running || 0) > 0;
/** The shell assigns `fn`: clicking the corner logo opens pages still working, or the three most recent ones. */
export const openWork = { fn: null };
const _busy = { n: 0 };
const holdBusy = () => {
  agentBusy.value = true;
  _busy.n++;
  return () => { _busy.n = Math.max(0, _busy.n - 1); agentBusy.value = _busy.n > 0; };
};

/** The mark. `spin` turns only the three commas around the pupil; the disc stays put. `quiet` is the corner logo, which follows others instead of counting itself. */
export function Mark({ spin = false, cls = '', quiet = false }) {
  const id = useRef(`mk${Math.random().toString(36).slice(2, 8)}`).current;
  useEffect(() => (spin && !quiet ? holdBusy() : undefined), [spin, quiet]);
  return html`<svg class=${`mark ${cls}${spin ? ' on' : ''}`} viewBox="0 0 64 64" aria-hidden="true">
    <defs><mask id=${id}><circle cx="32" cy="32" r="30" fill="#fff"/><circle cx="32" cy="32" r="9" fill="#000"/></mask></defs>
    <g mask=${`url(#${id})`}>
      <circle cx="32" cy="32" r="30" fill="#fffdf9"/>
      <g transform="translate(32 32)"><g class="tomoe"><g transform="translate(-32 -32)">
        <path d="M32 2A30 30 0 0 1 61.836 35.136A15.19 15.19 0 0 0 38.02 21.103A10.5 10.5 0 0 1 32 2Z" fill="#e23b2e"/>
        <circle cx="32" cy="12.5" r="10.5" fill="#e23b2e"/>
        <path d="M57.981 47A30 30 0 0 1 14.366 56.271A15.19 15.19 0 0 0 38.427 42.662A10.5 10.5 0 0 1 57.981 47Z" fill="#f0a000"/>
        <circle cx="48.887" cy="41.75" r="10.5" fill="#f0a000"/>
        <path d="M6.019 47A30 30 0 0 1 19.798 4.594A15.19 15.19 0 0 0 19.553 32.235A10.5 10.5 0 0 1 6.019 47Z" fill="#0f9d94"/>
        <circle cx="15.113" cy="41.75" r="10.5" fill="#0f9d94"/>
      </g></g></g>
    </g>
  </svg>`;
}
export const plain = (s) => (s || '').replace(/\[(?:arxiv|doi|openreview|pdf):[^\]]*?(?:\s*p\.?\s*(\d+))?\]/gi, (m, p) => (p ? `(p.${p})` : ''))
  .replace(/[*`#>]+/g, '').replace(/\s+/g, ' ').trim();
export const firstSentence = (s, n = 30) => short((s || '').replace(/[#*`>\-\[\]]/g, '').split(/[。！？\n.!?]/).find((x) => x.trim())?.trim() || '', n);
export const threadName = (t) => t?.title || t?.first || '（还没有提问）';
export const readBase64 = (f) => new Promise((ok, bad) => {
  const reader = new FileReader();
  reader.onload = () => ok(reader.result.split(',')[1]);
  reader.onerror = bad;
  reader.readAsDataURL(f);
});
/** Sentence-level diff of two texts: [[op, text]], op '=' kept, '-' removed, '+' added; removals come first. */
export function diffText(a, b) {
  const split = (s) => (s || '').split(/(?<=[。！？；\n]|[.!?;] )/).filter(Boolean);
  const x = split(a), y = split(b), n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  for (let i = 0, j = 0; i < n || j < m;) {
    if (i < n && j < m && x[i] === y[j]) { out.push(['=', x[i++]]); j++; }
    else if (i < n && (j === m || L[i + 1][j] >= L[i][j + 1])) out.push(['-', x[i++]]);
    else out.push(['+', y[j++]]);
  }
  return out;
}
// Links filled in by the agent (from papers it read) may be hostile: only http(s) becomes a live href.
export const href = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');

// ---------- Markdown (agent output) as an HTML string; citations become data-action buttons ----------

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const citeHtml = (pid, page, quote) =>
  `<button class="cite" data-action="cite" data-pid="${esc(pid)}" data-page="${page}" title="${esc(quote || '跳到原文')}">p.${page}</button>`;

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[(arxiv:\d{4}\.\d{4,5})\s*p\.?\s*(\d+)\]/gi, (m, id, p) => citeHtml(id.toLowerCase(), +p))
    .replace(/\[((?:arxiv|doi|openreview|pdf):[^\s\]]+)\]/gi, (m, id) => `<button class="cite" data-action="open-paper" data-pid="${id.replace(/^(\w+):/, (k) => k.toLowerCase())}" title="打开论文卡片">${esc(idLabel(id.replace(/^(\w+):/, (k) => k.toLowerCase())))}</button>`)
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(（])(https?:\/\/[^\s<)）]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function blocks(text) {
  const out = [];
  let list = null;
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
    if (m) {
      const t = /\d/.test(m[1]) ? 'ol' : 'ul';
      if (list !== t) { if (list) out.push(`</${list}>`); out.push(`<${t}>`); list = t; }
      out.push(`<li>${inline(m[2])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    const hd = line.match(/^#{1,4}\s+(.*)$/);
    if (hd) out.push(`<h4>${inline(hd[1])}</h4>`);
    else if (/^>\s?/.test(line)) out.push(`<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join('');
}
export const mdHtml = (src) => String(src || '').replace(/\r/g, '').split('```')
  .map((b, i) => (i % 2 ? `<pre><code>${esc(b.replace(/^\w*\n/, ''))}</code></pre>` : blocks(b))).join('');
/** Agent-written Markdown. Buttons inside it carry data-action, handled by the app-wide click delegate. */
export const Md = ({ text, class: cls = '' }) => html`<div class=${`md ${cls}`} dangerouslySetInnerHTML=${{ __html: mdHtml(text) }} />`;

// ---------- icons ----------

const ICON = {
  inbox: '<path d="M3 13h5l2 3h4l2-3h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
  papers: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  discuss: '<path d="M4 5h16v11H9l-5 4z"/>',
  research: '<path d="M12 3l2.2 5.6L20 9.3l-4.5 3.8 1.4 5.9L12 15.9 7.1 19l1.4-5.9L4 9.3l5.8-.7z"/>',
  unsorted: '<path d="M4 7h16M4 12h10M4 17h6"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13M9 7V4h6v3"/>',
};
export const Icon = ({ k }) => html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" dangerouslySetInnerHTML=${{ __html: ICON[k] || '' }} />`;
