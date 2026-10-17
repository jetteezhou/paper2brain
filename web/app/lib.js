// Shared by every view: templates (htm on Preact), the API, formatting and the small Markdown renderer.
import { h } from 'preact';
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
};
export const Icon = ({ k }) => html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" dangerouslySetInnerHTML=${{ __html: ICON[k] || '' }} />`;
