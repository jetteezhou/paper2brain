// App-wide state as signals, the hash router, and the few cross-view moves (open a paper, a thread, a research).
import { signal, computed } from '@preact/signals';
import { useEffect, useRef, useState, useCallback } from 'preact/hooks';
import { get, enc } from './lib.js';

// ---------- routing: #/view/arg ; #/read/<pid>@<page> opens the reader over the last view ----------

function parseHash() {
  const [view, ...rest] = location.hash.replace(/^#\/?/, '').split('/');
  return { view: view || 'inbox', arg: rest.length ? decodeURIComponent(rest.join('/')) : null };
}
export const route = signal(parseHash());
/** The view under the reader, so closing the reader goes back to it. */
export const baseRoute = signal(route.value.view === 'read' ? { view: 'inbox', arg: null } : route.value);
/** The item selected in a list view: { kind, id }. A new view starts with nothing selected, unless openItem chose it. */
export const selected = signal(null);
selected.subscribe(() => document.getElementById('detail')?.scrollTo(0, 0));
let keepSelection = false;
window.addEventListener('hashchange', () => {
  const r = parseHash(), b = baseRoute.value;
  if (r.view !== 'read') {
    if (!keepSelection && (r.view !== b.view || r.arg !== b.arg)) selected.value = null;
    baseRoute.value = r;
  }
  keepSelection = false;
  route.value = r;
});
export const go = (view, arg) => { location.hash = `#/${view}${arg != null ? '/' + enc(arg) : ''}`; };

export function openItem(kind, id, view, arg = null) {
  selected.value = { kind, id };
  const r = baseRoute.value;
  if (route.value.view === 'read' || r.view !== view || r.arg !== arg) { keepSelection = true; go(view, arg); }
}

// ---------- server state, polling ----------

export const appState = signal(null);
export const agentOn = computed(() => !!appState.value?.agent?.enabled);
export async function loadState() {
  try { appState.value = await get('/api/state'); } catch { /* server restarting */ }
}
/** Bumped every 2.5 s and after every write: live views refetch on it. */
export const tick = signal(0);
export const refresh = () => { tick.value++; loadState(); };
setInterval(() => { if (!document.hidden) tick.value++; }, 2500);
setInterval(() => { if (!document.hidden) loadState(); }, 7500);

/** Fetch url now and on every tick; the component re-renders only when the JSON changed. */
export function useLive(url, { poll = true } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const last = useRef('');
  const load = useCallback(async () => {
    if (!url) return;
    try {
      const d = await get(url);
      const s = JSON.stringify(d);
      if (s !== last.current) { last.current = s; setData(d); }
      setError(null);
    } catch (e) { setError(e.message); }
  }, [url]);
  useEffect(() => { last.current = ''; setData(null); load(); }, [url]);
  const t = tick.value;
  useEffect(() => { if (poll && t) load(); }, [t]);
  return [data, load, error];
}

// ---------- toast & modal ----------

export const toastSig = signal(null);
let toastTimer;
export function toast(text, action, onAction, isError) {
  toastSig.value = { text, action, onAction, isError };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastSig.value = null), action ? 6000 : 3000);
}
export const fail = (e) => toast(e?.message || String(e), null, null, true);

/** A vnode shown in the modal; wide: the large composer layout. */
export const modalSig = signal(null);
export const openModal = (vnode, wide = false) => { modalSig.value = { vnode, wide }; };
export const closeModal = () => { modalSig.value = null; };

// ---------- actions inside agent-written HTML (Markdown citations), handled by one click delegate ----------

const ACTIONS = {};
export const registerAction = (name, fn) => { ACTIONS[name] = fn; };
document.addEventListener('click', (e) => {
  const el = e.target.closest?.('[data-action]');
  if (el && ACTIONS[el.dataset.action]) { e.preventDefault(); e.stopPropagation(); ACTIONS[el.dataset.action](el, e); }
});

// ---------- moving across views ----------

/** What the reader should show when it opens: { tab, page, q, section, anchor, threadId, mid }. */
export const readerIntent = signal({});
export function openReader(pid, intent = {}) {
  readerIntent.value = intent;
  location.hash = `#/read/${enc(pid)}${intent.page ? '@' + intent.page : ''}`;
}
/** The message to flash once a discussion is on screen. */
export const highlight = signal(null);
/** A passage selected on a page, waiting to be quoted into that scope's discussion input: { scope, text }. */
export const quoting = signal(null);
/** A discussion: a paper's own discussions open in the reader beside the paper, the rest in the discussion view.
 * Without threadId: the scope's latest discussion (created on first use). */
export async function openThread(scope, threadId, mid) {
  highlight.value = mid || null;
  if (scope?.startsWith('paper:')) return openReader(scope.slice(6), { tab: 'chat', threadId, mid });
  if (!threadId) {
    try { threadId = (await get(`/api/threads?scope=${enc(scope)}`)).id; } catch (e) { fail(e); return; }
  }
  selected.value = { kind: 'thread', id: threadId };
  go('discuss', threadId);
}
export const openPaper = (pid) => openItem('paper', pid, 'papers');
export const openResearch = (id) => openItem('research', id, 'research');
export const openCapture = (id) => openItem('capture', id, 'inbox');

registerAction('open-paper', (el) => openPaper(el.dataset.pid));
registerAction('cite', (el) => openReader(el.dataset.pid, { page: +el.dataset.page, q: el.getAttribute('title') }));
