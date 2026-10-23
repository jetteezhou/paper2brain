// The quick notepad: passages copied or selected anywhere (a PDF page, a report, a discussion) and lines typed in,
// gathered before a discussion. Each copied passage keeps where it came from, so the agent can go back to it.
import { useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { html, get, post, enc, short } from './lib.js';
import { route, go, toast, fail, quoting, openReader } from './state.js';

// ponytail: kept in this browser only (localStorage), not synced across devices; move to the server if that is wanted.
const KEY = 'p2b.notes';
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; } };
export const notes = signal(load());
const save = (list) => { notes.value = list; localStorage.setItem(KEY, JSON.stringify(list)); };
addEventListener('storage', (e) => { if (e.key === KEY) notes.value = load(); });

const SURFACE = { pdf: '原文', card: '快读卡', report: '深读报告' };
const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

/** Where a node on screen sits: the paper (and page, section) it belongs to, else the page it is on. */
export function sourceOf(node) {
  const el = node?.nodeType === 1 ? node : node?.parentElement;
  const host = el?.closest('[data-pid]'), pg = el?.closest('.pdf-page'), sc = el?.closest('[data-quote-scope]');
  const reading = route.value.view === 'read' && route.value.arg?.split('@')[0];
  const src = { hash: location.hash, view: document.title.replace(/ · paper2brain$/, '') };
  const pid = host?.dataset.pid || (reading && el?.closest('#reader') ? reading : null);
  if (pid) Object.assign(src, { pid, page: pg ? +pg.dataset.page : null, section: host?.dataset.section || null, surface: host?.dataset.anchorSurface || (pg ? 'pdf' : null) });
  if (sc) src.scope = sc.dataset.quoteScope;
  return src;
}

/** Add a copied passage (with its source) or a typed line (src null). */
export async function addNote(text, src = null) {
  text = (src ? norm(text) : (text || '').trim()).slice(0, 2000);
  if (!text) return;
  const n = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), text, src };
  save([...notes.value, n]);
  if (src) toast(`已存进便签（共 ${notes.value.length} 条）`);
  if (src?.pid) {
    const p = await get(`/api/papers/${enc(src.pid)}`).catch(() => null);
    if (p?.title) save(notes.value.map((x) => (x.id === n.id ? { ...x, src: { ...x.src, title: p.title } } : x)));
  }
}

const label = (s, n = 30) => (s.pid ? `《${short(s.title || s.pid, n)}》${s.page ? ` p.${s.page}` : ''}${s.surface ? ` · ${SURFACE[s.surface] || s.surface}` : ''}${s.section ? ` · ${s.section}` : ''}`
  : `${s.view || '页面'}页`);
/** One note as the agent reads it: the passage, then where to find it again. */
export const cite = (n) => {
  const s = n.src;
  if (!s) return n.text;
  const where = s.pid ? `${label(s, 300)} [${s.pid}${s.page ? ` p.${s.page}` : ''}]` : `${label(s)}（${s.hash}${s.scope ? `，${s.scope}` : ''}）`;
  return `「${n.text}」\n——摘自${where}`;
};
const openSrc = (n) => (n.src.pid ? openReader(n.src.pid, { page: n.src.page, q: short(n.text, 40) }) : (location.hash = n.src.hash));

// The last copy inside the app: pasted into the notepad, it comes in with its source.
let lastCopy = null;
document.addEventListener('copy', (e) => {
  if (e.target instanceof Element && e.target.closest('.notepad, input, textarea')) return;
  const sel = getSelection(), text = norm(sel?.toString());
  if (text.length < 2 || !sel.rangeCount) return;
  lastCopy = { text, src: sourceOf(sel.getRangeAt(0).commonAncestorContainer) };
  const c = lastCopy;
  toast('已复制', '存进便签', () => addNote(c.text, c.src));
});

export function Notepad() {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState('');
  const list = notes.value;
  if (!open) {
    return html`<button class="np-tab" onClick=${() => setOpen(true)} title="快速便签：从各处攒下想和 agent 讨论的内容">便签${list.length ? html`<span class="n">${list.length}</span>` : null}</button>`;
  }
  const add = (e) => { e?.preventDefault(); if (v.trim()) { addNote(v); setV(''); } };
  const paste = (e) => {
    const t = norm(e.clipboardData?.getData('text/plain'));
    if (lastCopy && t === lastCopy.text) { e.preventDefault(); addNote(lastCopy.text, lastCopy.src); }
  };
  // Into the discussion on screen; with none on screen, a new discussion of the whole library.
  const discuss = async () => {
    const text = `我从几处攒了这些内容，想和你讨论：\n\n${list.map(cite).join('\n\n')}\n\n`;
    try {
      if (!document.querySelector('.chat-input textarea')) go('discuss', (await post('/api/threads', { scope: 'library' })).id);
      quoting.value = { text, raw: true };
      setOpen(false);
      toast('已放进讨论输入框，写上问题再发送', '清空便签', () => save([]));
    } catch (err) { fail(err); }
  };
  return html`<div class="notepad" role="dialog" aria-label="快速便签">
    <div class="np-head"><b>便签</b><span class="hint">${list.length ? `${list.length} 条` : ''}</span><span class="grow"></span>
      ${list.length ? html`<button class="btn sm ghost" onClick=${() => confirm('清空全部便签？') && save([])}>清空</button>` : null}
      <button class="btn sm ghost" onClick=${() => setOpen(false)}>收起</button></div>
    <div class="np-list">${list.length ? list.map((n) => html`<div class="np-item" key=${n.id}>
        <div class=${n.src ? 'np-quote' : 'np-own'}>${n.text}</div>
        <div class="np-src">${n.src ? html`<a href="#" title="回到出处" onClick=${(e) => { e.preventDefault(); openSrc(n); }}>${label(n.src)}</a>` : html`<span class="hint">我写的</span>`}
          <button class="btn sm ghost" aria-label="删除这条" onClick=${() => save(notes.value.filter((x) => x.id !== n.id))}>×</button></div></div>`)
      : html`<div class="hint np-empty">在论文、报告、讨论里选中文字点“存便签”，或复制后点提示里的“存进便签”，也可以直接在下面写或粘贴。</div>`}</div>
    <form class="np-input" onSubmit=${add}>
      <textarea rows="2" value=${v} onInput=${(e) => setV(e.target.value)} onPaste=${paste}
        onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) add(e); }} placeholder="写一句或粘贴，回车加入，Shift+回车换行"></textarea>
      <div class="np-acts"><button class="btn sm">加入</button><span class="grow"></span>
        <button type="button" class="btn sm primary" disabled=${!list.length} title="放进当前讨论的输入框；没有打开的讨论就新开一段" onClick=${discuss}>带去讨论</button></div></form>
  </div>`;
}
