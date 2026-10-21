// paper2brain web UI: Preact + htm + signals, no build step. This file is the shell: sidebar, capture bar, routing.
import { render } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { html, get, post, del, enc, short, ago, readBase64, idLabel, threadName, Icon, fit, Mark, workHot, openWork } from './lib.js';
import {
  route, baseRoute, go, appState, loadState, refresh, toast, toastSig, fail, restoreTrash, modalSig, openModal, closeModal,
  selected, openThread, openCapture, openReader, openPaper, openResearch, useLive, peekPaper,
} from './state.js';
import { CapturesView, PapersView, SearchView, ListDetail, openWebFind, addAndOpen } from './papers.js';
import { ResearchView, openComposer, researchPicker, showResearch } from './research.js';
import { DiscussView } from './discuss.js';
import { Reader, SelectionPopup, Peek } from './reader.js';

/** An overlay's value, kept on screen for a moment after it goes away so it can animate out: [shown, leaving]. */
function useLeaving(v, ms = 160) {
  const [last, setLast] = useState(v);
  useEffect(() => {
    if (v) { setLast(v); return; }
    const id = setTimeout(() => setLast(null), ms);
    return () => clearTimeout(id);
  }, [v]);
  return [v || last, !v && !!last];
}

// ---------- sidebar ----------

const openTopics = signal(new Set(JSON.parse(localStorage.getItem('p2b.openTopics') || '[]')));
const toggleTopic = (path) => {
  const s = new Set(openTopics.value);
  s.has(path) ? s.delete(path) : s.add(path);
  openTopics.value = s;
  localStorage.setItem('p2b.openTopics', JSON.stringify([...s]));
};
const INBOX_VIEWS = ['inbox', 'deep', 'archived', 'ignored'];

function NavItem({ view, label, n, hot, icon = view, arg }) {
  const r = baseRoute.value;
  const on = view === 'inbox' ? INBOX_VIEWS.includes(r.view) : r.view === view && (arg ? r.arg === arg : !(view === 'research' && r.arg));
  return html`<div class=${`nav-item ${on ? 'on' : ''}`} role="link" tabIndex="0" aria-current=${on ? 'page' : null} onClick=${() => go(view, arg)}>
    <${Icon} k=${icon} /><span>${label}</span>${n ? html`<span class=${`n ${hot ? 'hot' : ''}`} title=${hot ? `其中 ${hot} 条需要你确认` : ''}>${n}</span>` : null}</div>`;
}

function MainNav() {
  const c = appState.value?.counts || {};
  return html`<${NavItem} view="inbox" label="收件箱" n=${c.inbox} hot=${c.pending} />
    <${NavItem} view="papers" label="论文" n=${c.papers} />
    <${NavItem} view="discuss" label="讨论" />
    <${NavItem} view="research" label="研究" n=${c.research} />`;
}

function TopicTree() {
  const s = appState.value;
  if (!s) return null;
  const r = baseRoute.value;
  const inTopic = (path) => (r.view === 'topic' || r.view === 'research') && r.arg === path;
  const edit = (path) => html`<button class="nav-edit" title="改名 / 移动 / 删除" aria-label=${`编辑主题 ${path}`} onClick=${(e) => { e.stopPropagation(); topicModal(path); }}>⋯</button>`;
  return html`${s.topics.map((t) => {
    const open = openTopics.value.has(t.path) || (r.view === 'topic' && r.arg?.startsWith(t.path + '/'));
    return html`<div class=${`nav-item ${inTopic(t.path) ? 'on' : ''}`} role="link" tabIndex="0" aria-current=${inTopic(t.path) ? 'page' : null} onClick=${() => go('topic', t.path)}>
        <span class=${`caret ${open ? 'open' : ''}`} role="button" tabIndex="0" aria-expanded=${open} aria-label=${open ? '收起子主题' : '展开子主题'}
          onClick=${(e) => { e.stopPropagation(); toggleTopic(t.path); }}>
          <svg width="10" height="10" viewBox="0 0 10 10"><path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <span>${t.name}</span>${t.count ? html`<span class="n">${t.count}</span>` : null}${edit(t.path)}</div>
      ${open && t.children.map((ch) => html`<div class=${`nav-item tree-child ${inTopic(ch.path) ? 'on' : ''}`} role="link" tabIndex="0" aria-current=${inTopic(ch.path) ? 'page' : null} onClick=${() => go('topic', ch.path)}>
        <span>${ch.name}</span>${ch.count ? html`<span class="n">${ch.count}</span>` : null}${edit(ch.path)}</div>`)}`;
  })}
  ${s.topic_proposals.length ? html`<div class="nav-item proposals" role="button" tabIndex="0" onClick=${proposalsModal}><span>agent 提议了 ${s.topic_proposals.length} 个新主题</span></div>` : null}`;
}

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const dayLabel = (k) => { const [, m, d] = k.split('-'); return `${+m}月${+d}日`; };

/** Levels 1–4 split your active days into quartiles, so the shades fit however busy you usually are. */
function heatLevels(counts) {
  const v = Object.values(counts).sort((x, y) => x - y);
  const q = [0.25, 0.5, 0.75].map((p) => v[Math.floor(p * (v.length - 1))] ?? 0);
  return (n) => (!n ? 0 : 1 + q.filter((t) => n > t).length);
}

/** GitHub-style daily activity: one column per week (Monday on top), today in the last column. */
function HeatGrid({ weeks, on, onPick }) {
  const a = appState.value.activity.days, level = heatLevels(a);
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const end = d.getTime();
  d.setDate(d.getDate() - (weeks - 1) * 7 - ((d.getDay() + 6) % 7));
  const days = [];
  for (; d.getTime() <= end; d.setDate(d.getDate() + 1)) days.push(ymd(d));
  return html`<div class="heat-grid">${days.map((k) => html`<button class=${`l${level(a[k])}${k === on ? ' on' : ''}`}
    title=${`${dayLabel(k)} · ${a[k] ? `${a[k]} 次` : '没有活动'}`} aria-label=${`${dayLabel(k)} ${a[k] || 0} 次`} onClick=${() => onPick(k)} />`)}</div>`;
}

const SIDE_WEEKS = 18;
function Heatmap() {
  const s = appState.value?.activity;
  if (!s) return null;
  const since = new Date(Date.now() - SIDE_WEEKS * 7 * 864e5);
  const total = Object.entries(s.days).reduce((t, [k, n]) => t + (k >= ymd(since) ? n : 0), 0);
  return html`<div class="heat"><${HeatGrid} weeks=${SIDE_WEEKS} onPick=${activityModal} />
    <div class="hint">最近 ${SIDE_WEEKS} 周 ${total} 次${s.streak ? ` · 连续 ${s.streak} 天` : ''} · <a href="#" onClick=${(e) => { e.preventDefault(); activityModal(); }}>全年</a></div></div>`;
}

const TRIAGE_LABEL = { deep: '稍后深读', archived: '存档', ignored: '忽略', inbox: '移回收件箱' };
/** One action of the day: what it was, and where clicking takes you. */
function activityRow(x) {
  const jump = (fn) => () => { closeModal(); fn(); };
  const row = {
    capture: ['收下', x.text, jump(() => openCapture(x.ref))],
    message: ['讨论', x.text, jump(() => openThread(x.extra, x.ref))],
    anchor: ['划词', `“${x.text}”${x.extra ? ` · ${x.extra}` : ''}`, jump(() => openReader(x.ref, { tab: 'anchors' }))],
    note: ['笔记', x.text, x.ref ? jump(() => openPaper(x.ref)) : null],
    triage: [TRIAGE_LABEL[x.text] || '分拣', x.extra, jump(() => openCapture(x.ref))],
    read: ['阅读', x.extra || x.ref, jump(() => openReader(x.ref))],
  }[x.kind];
  return html`<div class=${`act-row ${row[2] ? '' : 'static'}`} role=${row[2] ? 'link' : null} tabIndex=${row[2] ? 0 : null} onClick=${row[2]}><time>${new Date(x.at).toTimeString().slice(0, 5)}</time>
    <span class="chip">${row[0]}</span><span class="act-text">${short(row[1] || '', 90)}</span></div>`;
}

function Activity({ day: first }) {
  const [day, setDay] = useState(first || ymd(new Date()));
  const [items] = useLive(`/api/activity/${day}`, { poll: false });
  const s = appState.value.activity;
  const total = Object.values(s.days).reduce((t, n) => t + n, 0);
  return html`<h3>活动</h3>
    <p class="hint" style="margin-top:0">过去一年 ${total} 次 · 活跃 ${Object.keys(s.days).length} 天 · 当前连续 ${s.streak} 天 · 最长连续 ${s.best} 天</p>
    <div class="heat wide"><${HeatGrid} weeks=${53} on=${day} onPick=${setDay} /></div>
    <div class="block-title" style="margin-top:16px">${dayLabel(day)} · ${items ? `${items.length} 次` : ''}<span class="line"></span></div>
    ${!items ? html`<span class="spin"></span>` : items.length ? items.map(activityRow) : html`<p class="hint">这天没有活动。</p>`}
    <div style="text-align:right;margin-top:12px"><button class="btn" onClick=${closeModal}>关闭</button></div>`;
}
const activityModal = (day) => openModal(html`<${Activity} key=${day || 'today'} day=${day} />`, true);

const workAt = signal(null);
const RUN_STATUS = { WAITING: '排队中', CREATING: '启动中', QUEUED: '排队中', RUNNING: '进行中', FINISHED: '完成', ERROR: '失败', CANCELLED: '已停止' };
function workLists() {
  const live = appState.value?.working || [];
  const skip = new Set(live.map((w) => w.id));
  const recent = (appState.value?.recent || []).filter((w) => !skip.has(w.id));
  return { live, recent, items: [...live, ...recent] };
}

function jumpWork(w) {
  const g = w.go || {};
  if (g.to === 'reader') openReader(g.id, { tab: g.tab, threadId: g.thread, anchor: g.anchor });
  else if (g.to === 'paper') openPaper(g.id);
  else if (g.to === 'capture') openCapture(g.id);
  else if (g.to === 'thread') openThread(g.scope, g.id);
  else if (g.to === 'research') showResearch(g.id, g.tab, g.thread);
  else if (g.to === 'discuss') go('discuss');
}

async function toggleWork(e) {
  if (workAt.value) { workAt.value = null; return; }
  const box = e.currentTarget.getBoundingClientRect();
  const place = () => {
    const width = 320;
    let left = box.left;
    if (left + width > innerWidth - 8) left = Math.max(8, innerWidth - 8 - width);
    workAt.value = { top: box.bottom + 6, left };
  };
  if (workLists().items.length) { place(); loadState(); return; }
  await loadState();
  if (workLists().items.length) place();
  else toast('还没有 agent 做过工作');
}
openWork.fn = toggleWork;

function WorkMenuHost() {
  const [at, leaving] = useLeaving(workAt.value);
  return at ? html`<${WorkMenu} at=${at} leaving=${leaving} />` : null;
}

function WorkMenu({ at, leaving }) {
  const { live, recent, items } = workLists();
  const [i, setI] = useState(0);
  const close = () => { workAt.value = null; };
  const cur = Math.min(i, Math.max(0, items.length - 1));
  const stamp = items.map((w) => w.id).join();
  useEffect(() => { if (!items.length) close(); }, [items.length]);
  useEffect(() => {
    const down = (e) => { if (!e.target.closest?.('.work-menu, .brand, .m-brand, .reader-brand')) close(); };
    const key = (e) => {
      if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); return; }
      if (!items.length || (e.target instanceof Element && e.target.closest('input, textarea, select'))) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopImmediatePropagation();
        setI((n) => (Math.min(n, items.length - 1) + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length);
      } else if (e.key === 'Enter') {
        const w = items[cur];
        if (!w?.go) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        jumpWork(w);
        close();
      }
    };
    document.addEventListener('mousedown', down);
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key, true); };
  }, [cur, stamp]);
  useEffect(() => { document.querySelector('.work-menu .work-item.on')?.scrollIntoView({ block: 'nearest' }); }, [cur, stamp]);
  if (!items.length) return null;
  const row = (w, j) => html`<button type="button" role="menuitem" disabled=${!w.go} class=${`work-item ${j === cur ? 'on' : ''}`}
    onMouseMove=${() => i !== j && setI(j)} onClick=${() => { if (w.go) { jumpWork(w); close(); } }}>
    <span class="work-label">${w.label}</span><span class="work-title">${w.title}</span><span class="hint">${RUN_STATUS[w.status] || w.status}</span></button>`;
  const label = live.length && recent.length ? '正在工作和最近的工作' : live.length ? '正在工作' : '最近的工作';
  return html`<div class=${`work-menu${leaving ? ' leaving' : ''}`} style=${`top:${at.top}px;left:${at.left}px`} role="menu" aria-label=${label}>
    ${live.length ? html`<div class="work-head">正在工作</div>${live.map((w, j) => row(w, j))}` : null}
    ${recent.length ? html`<div class="work-head">最近的工作</div>${recent.map((w, j) => row(w, live.length + j))}` : null}</div>`;
}

function Brand() {
  const hot = workHot(appState.value);
  return html`<button type="button" class="brand" title=${hot ? '跳到正在工作的页面' : '最近三次 agent 工作'}
    aria-haspopup="menu" aria-expanded=${!!workAt.value} onClick=${toggleWork}>
    <${Mark} quiet cls="brand-mark" spin=${hot} />paper2brain</button>`;
}

function MobileMark() {
  const hot = workHot(appState.value);
  return html`<button type="button" class="m-brand" title=${hot ? '跳到正在工作的页面' : '最近三次 agent 工作'} aria-label=${hot ? '正在工作' : '最近的工作'} onClick=${toggleWork}><${Mark} quiet cls="brand-mark" spin=${hot} /></button>`;
}

function Sidebar() {
  const a = appState.value?.agent;
  return html`<aside id="side">
    <${Brand} />
    <${PalTrigger} />
    <${Libraries} />
    <nav class="nav">
      <${MainNav} />
      <div class="nav-label">主题<button class="nav-add" title="新建主题" aria-label="新建主题" onClick=${() => topicModal()}>+</button></div>
      <${TopicTree} />
      <${NavItem} view="unsorted" label="未归类" n=${appState.value?.counts?.unsorted} />
      <${NavItem} view="trash" label="最近删除" n=${appState.value?.counts?.trash} />
    </nav>
    <${Heatmap} />
    <div class="agent-status" onClick=${setupModal}>${a?.enabled
      ? html`<span class="dot ok"></span><span>Agent 已连接 · ${a.machine}</span>`
      : html`<span class="dot"></span><span>Agent 未连接 · 只做规则预处理<br /><a>如何连接</a></span>`}</div>
  </aside>`;
}

// ---------- topic modals ----------

const allTopics = () => appState.value.topics.flatMap((t) => [t, ...t.children]);

function TopicForm({ path }) {
  const t = path && allTopics().find((x) => x.path === path);
  const parent = path?.includes('/') ? path.split('/')[0] : '';
  const roots = appState.value.topics.filter((x) => x.path !== path);
  const submit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = path
        ? await post('/api/topics/edit', { path, name: f.get('name'), parent: f.get('parent') ?? parent })
        : await post('/api/topics', { name: f.get('name'), parent: f.get('parent') });
      closeModal();
      toast(path ? '主题已更新' : `已新建主题「${r.path}」`);
      const b = baseRoute.value;
      if (path && b.arg === path) go(b.view, r.path);
      refresh();
    } catch (err) { fail(err); }
  };
  const kind = parent ? '子主题' : '主题';
  const remove = async () => {
    if (!confirm(parent
      ? `删除子主题「${t.name}」？\n\n它下面的论文、研究、笔记和讨论都会归到上一级主题「${parent}」，不会丢失。\n子主题本身删除后不能恢复。`
      : `删除主题「${t.name}」？\n\n它下面的论文会变成「未归类」，研究和讨论都保留（讨论移到「整个论文库」）。\n主题本身删除后不能恢复。`)) return;
    try { await post('/api/topics/delete', { path }); } catch (err) { fail(err); return; }
    closeModal();
    toast(parent ? `已删除子主题「${t.name}」，内容归到「${parent}」` : `已删除主题「${t.name}」`);
    const b = baseRoute.value;
    if (b.view === 'topic' && b.arg === path) parent ? go('topic', parent) : go('papers');
    refresh();
  };
  return html`<h3>${t ? `改名、移动或删除${kind}` : '新建主题'}</h3>
    <form class="research-create" onSubmit=${submit}>
      <input name="name" defaultValue=${t?.name || ''} placeholder="主题名，比如：模仿学习" maxlength="30" aria-label="主题名" autofocus />
      <label class="hint">放在 <select name="parent" disabled=${!!t?.children?.length} title=${t?.children?.length ? '有子主题的一级主题不能移动' : ''}>
        <option value="">（作为一级主题）</option>${roots.map((r) => html`<option value=${r.path} selected=${r.path === parent}>${r.name}</option>`)}</select> 下面</label>
      ${t ? html`<p class="hint">这个主题下的论文、研究和讨论会跟着一起改名或移动。</p>` : null}
      <div style="display:flex;gap:8px;justify-content:flex-end">${t ? html`<button type="button" class="btn ghost danger" style="margin-right:auto"
        disabled=${!!t.children?.length} title=${t.children?.length ? '先删除或移走它下面的子主题' : ''} onClick=${remove}>删除${kind}</button>` : null}
        <button type="button" class="btn" onClick=${closeModal}>取消</button><button class="btn primary">${t ? '保存' : '新建'}</button></div>
    </form>`;
}
const topicModal = (path) => openModal(html`<${TopicForm} path=${path} />`);

function Proposals() {
  const ps = appState.value?.topic_proposals || [];
  if (!ps.length) { closeModal(); return null; }
  const decide = async (p, accept) => {
    try { await post('/api/topics/decide', { path: p.path, accept }); } catch (e) { fail(e); return; }
    toast(accept ? `已采用主题「${p.path}」` : '已忽略这个提议');
    refresh();
  };
  return html`<h3>agent 提议的新主题</h3><p class="hint" style="margin-top:0">采用后才会出现在主题树里，agent 列出的论文会自动归进去（你手动调整过主题的论文除外）。</p>
    ${ps.map((p) => html`<div class="cand"><div class="cand-body"><div class="cand-title">${p.parent ? `${p.parent} / ${p.name}` : p.name}</div>
      <div class="cand-meta">${p.reason || ''}${p.for_papers?.length ? ` · 采用后归入 ${p.for_papers.length} 篇论文` : ''}</div></div>
      <button class="btn sm primary" onClick=${() => decide(p, true)}>采用</button>
      <button class="btn sm ghost" onClick=${() => decide(p, false)}>不要</button></div>`)}
    <div style="text-align:right;margin-top:10px"><button class="btn" onClick=${closeModal}>关闭</button></div>`;
}
const proposalsModal = () => openModal(html`<${Proposals} />`);

/** One model per module: a select for the model, one more per parameter it takes (context window, reasoning effort...). */
function Models() {
  const [d, reload, err] = useLive('/api/agent/models', { poll: false });
  if (err) return html`<p class="hint">读不到模型列表：${err}</p>`;
  if (!d) return html`<span class="spin"></span>`;
  // a model is picked by backend and id together: both backends can offer the same id
  const key = (m) => m && `${m.backend || 'cursor'}:${m.id}`;
  const byKey = Object.fromEntries(d.models.map((m) => [key(m), m]));
  const save = async (module, model) => { try { await post('/api/agent/models', { module, model }); toast('已保存，之后新开的讨论和任务用这个模型'); reload(); } catch (e) { fail(e); } };
  const setParam = (mod, pid, value) => {
    const params = (mod.model.params || []).filter((p) => p.id !== pid);
    save(mod.id, { ...mod.model, params: value ? [...params, { id: pid, value }] : params });
  };
  const pick = (mod, k) => { const m = byKey[k]; save(mod.id, m ? { id: m.id, backend: m.backend } : null); };
  return html`<div class="models">${d.modules.map((mod) => {
    const m = byKey[key(mod.model)], val = (pid) => (mod.model.params || []).find((p) => p.id === pid)?.value || '';
    return html`<div class="model-row"><div class="model-label">${mod.label}</div><div class="model-picks">
      <select value=${key(mod.model) || ''} onChange=${(e) => pick(mod, e.target.value)}>
        <option value="">${d.fallback ? `默认（${d.fallback}）` : appState.value.agent.backend === 'tcodex' ? '默认（TCodex 的默认模型）' : '默认（Cursor 账户的默认模型）'}</option>
        ${d.models.map((x) => html`<option value=${key(x)}>${x.displayName || x.id}</option>`)}</select>
      ${(m?.parameters || []).map((p) => html`<select value=${val(p.id)} title=${p.displayName || p.id} onChange=${(e) => setParam(mod, p.id, e.target.value)}>
        <option value="">${p.displayName || p.id}：默认</option>${p.values.map((v) => html`<option value=${v.value}>${p.displayName || p.id}：${v.displayName || v.value}</option>`)}</select>`)}
    </div></div>`;
  })}</div>`;
}

/** Starts the CLI's own sign-in (browser or link) on this Mac and shows what it printed. */
function Login({ cli, name }) {
  const [r, setR] = useState(null), [busy, setBusy] = useState(false);
  const start = async () => { setBusy(true); try { setR(await post('/api/agent/login', { cli })); } catch (e) { fail(e); } setBusy(false); };
  return html`<span><button class="btn sm" disabled=${busy} onClick=${start}>${busy ? '启动中…' : r && !r.done ? '看登录进度' : `登录 ${name}`}</button>
    ${r ? html`<div class="hint">${r.url ? html`<a href=${r.url} target="_blank">在浏览器里完成登录</a>` : null}
      ${r.output ? html`<pre>${r.output}</pre>` : html`<span>已启动，浏览器没弹出时稍后点“看登录进度”</span>`}${r.done ? html`<b>登录命令已结束。</b>` : null}</div>` : null}</span>`;
}

/** The OpenAI-compatible API the search bar asks directly for its AI row; the key is never sent back. */
function SearchApi() {
  const [d, reload, err] = useLive('/api/agent/search-api', { poll: false });
  if (err) return html`<p class="hint">读不到搜索接口配置：${err}（刚更新过代码的话，重启 ./p2b serve）</p>`;
  if (!d) return html`<span class="spin"></span>`;
  const save = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await post('/api/agent/search-api', { base_url: f.get('base_url'), model: f.get('model'), api_key: f.get('api_key'), web_model: f.get('web_model') });
      toast(f.get('base_url') ? '已保存，搜索框的 AI 推荐直接调这个接口' : '已关闭，AI 推荐改用 TCodex');
      reload(); refresh();
    } catch (err) { fail(err); }
  };
  // defaultValue, not value: the state refresh re-renders this and would put the saved text back over the typing
  return html`<form class="research-create" key=${d.base_url + d.model + d.web_model} onSubmit=${save}>
    <input name="base_url" defaultValue=${d.base_url} placeholder="接口地址，比如谷歌官方 https://generativelanguage.googleapis.com/v1beta/openai 或 https://api.zhizengzeng.com/v1（留空关闭）" aria-label="接口地址" />
    <input name="model" defaultValue=${d.model} placeholder="快速模型：搜索框推荐、快速问、起标题，比如 gemini-3.5-flash-lite" aria-label="快速模型" />
    <input name="web_model" defaultValue=${d.web_model} placeholder="联网模型：上网找论文、上网查（留空用 gemini-3.5-flash-lite，走 Gemini 谷歌搜索）" aria-label="联网模型" />
    <input name="api_key" type="password" autocomplete="off" placeholder=${d.has_key ? '已保存 API key，留空不改' : 'API key'} aria-label="API key" />
    <div style="text-align:right"><button class="btn sm primary">保存</button></div></form>`;
}

/** Who holds the library, how fresh the iCloud snapshot is, and the pull that takes it over on this Mac. */
function Handoff() {
  const [d, , err] = useLive('/api/handoff', { poll: false });
  const [busy, setBusy] = useState(false);
  if (err) return html`<p class="hint">读不到交接状态：${err}</p>`;
  if (!d) return html`<span class="spin"></span>`;
  const s = d.snapshot;
  const pull = async () => {
    if (!confirm(`用 iCloud 里 ${s.host} 在 ${s.at} 的快照替换这台 Mac 的论文库？\n本机现在的数据库会先备份到 backups/；${d.sync ? '两台之间已同步的改动会再补回来。' : '这台上之后没交出去的改动会被覆盖。'}\n服务会重启约一分钟。`)) return;
    setBusy(true);
    try { await post('/api/handoff/pull', {}); } catch (e) { setBusy(false); return fail(e); }
    toast('正在从 iCloud 接手，服务会重启，好了自动刷新');
    const t0 = Date.now();
    await new Promise((r) => setTimeout(r, 8000));
    while (Date.now() - t0 < 300000) {
      try {
        const h = await get('/api/handoff');
        if (h.holder && h.snapshot?.owner === h.me) { location.reload(); return; }
        if (['先停掉', '还没', 'Traceback'].some((w) => h.pull_log.includes(w))) { setBusy(false); return fail(h.pull_log.trim().split('\n').pop()); }
      } catch { /* restarting */ }
      await new Promise((r) => setTimeout(r, 3000));
    }
    setBusy(false);
    fail('接手超时，看 /tmp/paper2brain-pull.log');
  };
  return html`<p class="hint" style="margin-top:0">${!s ? '还没交接过：在终端运行一次 ops/handoff.py push，之后持有论文库的 Mac 每半小时自动放一份快照。'
      : html`iCloud 快照：${s.host} · ${s.at}。持有论文库的是 <b>${s.owner || '（已交出，等另一台接手）'}</b>${d.holder ? '（这台）' : ''}。
        ${d.sync ? '两台同时用的同步已打开，改动每 20 秒互通。' : '同步未打开（config.json 里 "sync": true）。'}`}</p>
    ${s && !(d.holder && s.owner === d.me) ? html`<button class="btn sm" disabled=${busy} onClick=${pull}>${busy ? '接手中…' : '从 iCloud 接手到这台'}</button>` : null}
    ${s && d.holder && s.owner === d.me ? html`<p class="hint">这台就是持有者，不用接手。</p>` : null}`;
}

// ---------- the shared server: libraries, the agent on the user's own Mac, sharing a research ----------

/** Which library the page shows (the user's own or a shared space); switching reloads everything. */
function Libraries() {
  const acc = appState.value?.account;
  if (!acc) return null;
  const pick = async (e) => {
    try { await post('/api/libraries/switch', { library: e.target.value }); } catch (err) { return fail(err); }
    location.hash = '';
    location.reload();
  };
  return html`<select class="lib-pick" value=${acc.library} onChange=${pick} aria-label="切换论文库" style="margin:4px 12px 8px;max-width:calc(100% - 24px)">
    ${acc.libraries.map((l) => html`<option value=${l.id}>${l.kind === 'space' ? '共享 · ' : ''}${l.name}${l.role === 'viewer' ? '（只读）' : ''}</option>`)}</select>`;
}

const joinLink = (code) => `${location.origin}/join.html#${code}`;
const Copyable = ({ text }) => html`<pre style="user-select:all;white-space:pre-wrap;word-break:break-all">${text}</pre>`;

function Account() {
  const acc = appState.value.account, a = appState.value.agent;
  const [got, setGot] = useState({});
  const [researches] = useLive('/api/research', { poll: false });
  const personal = acc.libraries.find((l) => l.kind === 'personal')?.id, space = acc.library !== personal, owner = acc.role === 'owner';
  const call = async (key, url, body, after) => {
    try { const r = await post(url, body); setGot((g) => ({ ...g, [key]: r })); if (after) after(r); } catch (e) { fail(e); }
  };
  const reload = () => { location.hash = ''; location.reload(); };
  const share = (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    if (!f.get('research_id')) return fail('先选一个研究');
    call('space', '/api/spaces', { research_id: f.get('research_id'), name: f.get('name') }, () => { toast('已共享到新空间，正在打开'); setTimeout(reload, 600); });
  };
  const setRole = (m, role) => call('role', `/api/members/${m.id}`, { role }, () => { toast(role ? '已修改' : '已移出'); refresh(); });
  const leave = () => confirm('离开这个空间？之后要别人重新邀请才能回来。') && call('leave', `/api/members/${acc.user.id}`, { role: null }, reload);
  return html`<div class="block-title" style="margin-top:0">本机 agent<span class="line"></span></div>
  <p class="hint" style="margin-top:0">${acc.user.name}，你的 agent 在你自己的 Mac 上运行，用你的 Cursor key，只能通过这个网页按你的权限读写。
    ${a.enabled ? html`<b>✓ 已登记 worker ${a.machine}</b>（Mac 关机时 agent 不会回复，其他功能照常）` : '还没登记：在你的 Mac 上运行下面的命令。'}</p>
  ${got.token ? html`<p class="hint">口令只显示这一次（重新生成会让旧的失效）。在 Mac 的终端里运行：</p>
    <${Copyable} text=${`git clone ${a.repo} ~/paper2brain 2>/dev/null; ~/paper2brain/ops/client.sh ${location.origin} ${got.token.token}`} />`
    : html`<button class="btn sm" onClick=${() => call('token', '/api/me/agent-token', {})}>生成本机 agent 的口令</button>`}
  <div class="block-title" style="margin-top:14px">${space ? '这个共享空间' : '共享研究'}<span class="line"></span></div>
  ${space ? html`<div>${acc.members.map((m) => html`<div class="cand"><div class="cand-body"><div class="cand-title">${m.name}${m.id === acc.user.id ? '（你）' : ''}</div>
      <div class="cand-meta">${m.id === acc.agent_member ? '空间的 agent 用 ta 的 key 和 Mac' : ''}</div></div>
      ${owner ? html`<select value=${m.role} onChange=${(e) => setRole(m, e.target.value)} aria-label="角色"><option value="viewer">只读</option><option value="editor">编辑</option><option value="owner">所有者</option></select>
        ${m.id !== acc.agent_member && m.role !== 'viewer' ? html`<button class="btn sm ghost" onClick=${() => call('agent', '/api/members/agent', { user_id: m.id }, refresh)}>由 ta 负责 agent</button>` : null}
        ${m.id !== acc.user.id ? html`<button class="btn sm ghost danger" onClick=${() => confirm(`把 ${m.name} 移出空间？`) && setRole(m, null)}>移出</button>` : null}`
      : html`<span class="hint">${{ viewer: '只读', editor: '编辑', owner: '所有者' }[m.role]}</span>`}</div>`)}</div>
    ${owner ? html`<p class="hint">邀请：<button class="btn sm" onClick=${() => call('invite', '/api/members/invite', { role: 'editor' })}>生成可编辑的邀请链接</button>
      <button class="btn sm" onClick=${() => call('invite', '/api/members/invite', { role: 'viewer' })}>生成只读的邀请链接</button></p>
      ${got.invite ? html`<p class="hint">一次有效，7 天内用：</p><${Copyable} text=${joinLink(got.invite.code)} />` : null}`
      : html`<p><button class="btn sm ghost danger" onClick=${leave}>离开这个空间</button></p>`}`
  : html`<p class="hint" style="margin-top:0">把一个研究连同它用到的论文、讨论、附件、代码仓库和图复制到一个新的共享空间，再邀请别人；个人库里的原研究保留不动，之后在空间里一起改。</p>
    <form class="research-create" onSubmit=${share}>
      <select name="research_id" aria-label="要共享的研究"><option value="">选一个研究…</option>${(researches || []).map((x) => html`<option value=${x.id}>${x.title}</option>`)}</select>
      <input name="name" placeholder="空间的名字（不写就用“共享空间”）" maxlength="60" aria-label="空间的名字" />
      <div style="text-align:right"><button class="btn sm primary">共享到新空间</button></div></form>`}
  <div class="block-title" style="margin-top:14px">在其他设备登录<span class="line"></span></div>
  ${got.login ? html`<p class="hint" style="margin-top:0">在那台设备的浏览器里打开（一次有效）：</p><${Copyable} text=${joinLink(got.login.code)} />`
    : html`<button class="btn sm" onClick=${() => call('login', '/api/me/login-link', {})}>生成登录链接</button>`}`;
}

function Setup() {
  const a = appState.value.agent, ok = a.backends || {};
  if (appState.value.account) return html`<h3>账号、agent 与共享</h3><${Account} />
    ${a.enabled && appState.value.account.role === 'owner' ? html`<div class="block-title" style="margin-top:14px">模型<span class="line"></span></div><${Models} key=${a.machine} />` : null}
    <div style="text-align:right;margin-top:12px"><button class="btn" onClick=${closeModal}>关闭</button></div>`;
  const switchTo = async (backend) => { try { await post('/api/agent/backend', { backend }); toast('已保存，选“默认”的模块之后用这个后端'); refresh(); } catch (e) { fail(e); } };
  const mark = (b) => (ok[b] ? '✓ 已连接' : '未连接');
  return html`<h3>${a.enabled ? 'Agent 已连接' : '连接 agent'}</h3>
  <p class="hint">快读卡、深读报告和讨论由 agent 在这台 Mac 上完成；没连接时只做规则预处理（识别链接、抓取快照、下载全文）。
    两个后端可以同时用：每个模块在下面单独选模型（Cursor 或 TCodex 的），已经开始的讨论一直留在它开始时的后端上。</p>
  <p class="hint">Cursor agent：${mark('cursor')} · TCodex（腾讯内网）：${mark('tcodex')} · 选“默认”的模块用
    <select value=${a.backend} onChange=${(e) => switchTo(e.target.value)} aria-label="默认后端">
    <option value="cursor">Cursor</option><option value="tcodex">TCodex</option></select></p>
  ${ok.tcodex ? html`<p class="hint">TCodex 登录过期或换账号：<${Login} cli="tcodex" name="TCodex" /></p>` : html`<details open=${a.backend === 'tcodex'}><summary>连接 TCodex</summary><ol>
    <li>安装 TCodex：<pre>curl -fsSL https://mirrors.tencent.com/repository/generic/tcodex/tcodex/install.sh | sh</pre></li>
    <li>登录一次：<${Login} cli="tcodex" name="TCodex" />（或在启动服务的环境里设置 <code>TCODEX_AUTH_TOKEN</code>）</li>
  </ol></details>`}
  ${ok.cursor ? null : html`<details open=${a.backend === 'cursor'}><summary>连接 Cursor agent</summary><ol>
    <li>安装 Cursor CLI 并登录（只需一次）：<pre>curl https://cursor.com/install -fsS | bash${'\n'}agent login</pre></li>
    <li>在仓库目录启动本机 worker（保持运行）：<pre>agent worker start --name p2b-mac --worker-dir "${a.repo_dir}"</pre></li>
    <li>在 <a href="https://cursor.com/dashboard/integrations" target="_blank">Cursor Dashboard</a> 创建 API key，写入 <code>${a.home}/config.json</code>：
      <pre>{${'\n'}  "cursor_api_key": "key_...",${'\n'}  "machine": "p2b-mac"${'\n'}}</pre></li>
    <li>重启 <code>./p2b serve</code>。</li>
  </ol></details>`}
  ${a.enabled ? html`<p>当前 worker：<b>${a.machine}</b>${a.auto_agent ? '，收下后自动交给 agent 整理。' : '，自动整理已关闭（config.json 中 auto_agent=false）。'}</p>` : html`<p class="hint">当前缺少：${a.missing.join('、')}</p>`}
  ${a.enabled ? html`<div class="block-title" style="margin-top:14px">模型<span class="line"></span></div>
    <p class="hint" style="margin-top:0">每个模块可以用不同的模型。研究页的长讨论适合推理和表达强、上下文选 1m 的模型。改了之后，新开的讨论和任务才用新模型；已经在进行的讨论沿用原来的模型，想换就“分出新讨论”（带上下文）或“发起新讨论”（从头开始）。</p>
    <${Models} key=${a.machine} />` : null}
  <div class="block-title" style="margin-top:14px">快速搜索接口<span class="line"></span></div>
  <p class="hint" style="margin-top:0">搜索框停顿 1 秒后，第一条的 AI 推荐直接调这个 OpenAI 兼容接口（关闭思考，通常 1–8 秒）；不填就用上面“快速搜索”选的 TCodex 模型（约 4 秒）。
    ${a.search_ai ? '✓ 已可用' : '未可用'}</p>
  <${SearchApi} />
  <div class="block-title" style="margin-top:14px">两台 Mac<span class="line"></span></div>
  <${Handoff} />
  <p class="hint">调优 agent：修改仓库里的 <code>librarian/*.md</code>，再在捕获详情里点“重新整理”。</p>
  <div style="text-align:right;margin-top:12px"><button class="btn" onClick=${closeModal}>知道了</button></div>`;
}
const setupModal = () => appState.value && openModal(html`<${Setup} />`);

// ---------- the one bar (⌘K): capture, search, find papers, jot a thought, start a research, act on this paper, go anywhere ----------

const FIND_RE = /^\s*(?:请|麻烦)?\s*(?:帮我|帮忙|给我)\s*(?:找|搜|推荐)|^\s*(?:找|搜)(?:一下|几篇|一些|些)?[^\n]{0,60}(?:论文|paper)/i;
const accepted = (f) => f.type.startsWith('image/') || f.type === 'application/pdf';
/** What is in the bar. It survives closing the bar, so a half-written capture is never lost. */
const cap = signal({ text: '', thought: '', files: [] });
const palOpen = signal(false);
const clearCap = () => { cap.value = { text: '', thought: '', files: [] }; };
const openPalette = (text) => { if (text != null) cap.value = { ...cap.value, text }; palOpen.value = true; };

async function addFiles(files) {
  palOpen.value = true;
  for (const f of files) {
    if (!accepted(f)) { toast(`不支持 ${f.name}：只收图片和 PDF`); continue; }
    if (cap.value.files.length >= 5) { toast('一次最多 5 个文件'); break; }
    const data = await readBase64(f);
    cap.value = { ...cap.value, files: [...cap.value.files, { mime: f.type, name: f.name, data }] };
  }
}

async function jot(text) {
  clearCap();
  try {
    const t = await post('/api/threads', { scope: 'library', text });
    toast('已记下，开了一段新讨论', '去讨论', () => openThread(t.scope, t.id));
    refresh();
  } catch (e) { cap.value = { ...cap.value, text }; fail(e); }
}

async function ask(text) {
  clearCap();
  try {
    const t = await post('/api/threads', { scope: 'library' });
    await post('/api/chat', { scope: 'library', text, thread_id: t.id });
    openThread('library', t.id);
    refresh();
  } catch (e) { cap.value = { ...cap.value, text }; fail(e); }
}

function startResearch() {
  const { text, thought, files } = cap.value;
  const fs = files.map((f, i) => ({ name: f.name || `截图-${i + 1}.png`, mime: f.mime, data: f.data }));
  clearCap();
  openComposer({ text: [text.trim(), thought.trim()].filter(Boolean).join('\n\n'), files: fs });
}

async function submitFind(text) {
  clearCap();
  try {
    const c = await post('/api/find', { text });
    refresh();
    if (!c.agent) { toast('Agent 未连接：先按关键词在 arXiv 检索了候选，确认后入库'); openCapture(c.id); return; }
    toast('已交给 agent 去找，找到的论文会入库，并在这里直接开聊');
    const t = await get(`/api/threads?scope=${encodeURIComponent(`find:${c.id}`)}`).catch(() => null);
    if (t) openThread(t.scope, t.id);
  } catch (e) { cap.value = { ...cap.value, text }; fail(e); }
}

async function submitCapture() {
  const { text, thought, files } = cap.value, t = text.trim(), th = thought.trim();
  if (!t && !files.length && !th) return;
  clearCap();
  try {
    const c = await post('/api/captures', { text: t, thought: th, images: files });
    if (c.thread_id) { toast('已记下，开了一段新讨论', '去看', () => openThread('library', c.thread_id)); refresh(); return; }
    if (c.duplicate) toast(`${ago(c.created_at)}收过这条，已移回收件箱${th ? '，这句想法记到了它下面' : ''}`);
    else toast('已收下，正在整理', '撤销', async () => { await del(`/api/captures/${c.id}`).catch(fail); cap.value = { text, thought, files }; refresh(); });
    refresh();
    openCapture(c.id);
  } catch (e) { cap.value = { text, thought, files }; fail(e); }
}

const NAV = [['inbox', '收件箱'], ['papers', '论文'], ['discuss', '讨论'], ['research', '研究'], ['unsorted', '未归类'], ['trash', '最近删除']];

const named = (p) => (p.short_name ? `${p.short_name} · ${p.title}` : p.title);

/** Every row the bar offers for what is typed. nav: a jump or a lookup (the text is done with); the rest consume it. */
function paletteItems(t, link, files, hits, ai) {
  const out = [], add = (group, key, icon, label, hint, run, nav) => out.push({ group, key, icon, label, hint, run, nav });
  const s = selected.value, r = route.value;
  const pid = r.view === 'read' ? r.arg?.split('@')[0] : s?.kind === 'paper' ? s.id : null;
  const say = short(t.replace(/\s+/g, ' '), 36);
  if (ai?.q === t && ai.r && !link && !files.length) {
    if (ai.r.answer) { add('AI 推荐', 'ai:say', '✦', ai.r.answer, '↵ 接着和 agent 聊', () => ask(t)); out[out.length - 1].wrap = true; }
    const lib = () => ai.r.papers.forEach((p) => {
      add('AI 推荐 · 库里', `ai:${p.id}`, '▤', named(p), short(p.tldr || idLabel(p.id), 40), () => openPaper(p.id), true);
      out[out.length - 1].peek = () => peekPaper(p.id);
    });
    const web = () => (ai.r.web || []).forEach((w) => {
      add('AI 推荐 · 网上', `aiw:${w.url}`, '↗', w.title, `${w.pid ? `${idLabel(w.pid)} · ${w.published}` : new URL(w.url).hostname} · ${short(w.why, 30)}`,
        () => window.open(w.url, '_blank', 'noopener'), true);
      if (w.pid) out[out.length - 1].add = () => addAndOpen(w.pid);
    });
    // a paper typed by name and found on arXiv is what was asked for: it goes before the library's related ones
    if (ai.r.named) { web(); lib(); } else { lib(); web(); }
  }
  if (link || files.length) {
    add('收下', 'cap', '＋', files.length && !t ? `收下 ${files.length} 个文件` : '收下', '找到原论文、写快读卡、归类', submitCapture);
    add('收下', 'res', '☆', '以它开始一个研究', '', startResearch);
  } else if (t) {
    const find = () => {
      if (appState.value?.agent?.search_api) add('用这段话', 'web', '⌕', `上网找论文：${say}`, '约 5 秒列出网上的论文，挑了再收下', () => { clearCap(); openWebFind(t, () => submitFind(t)); });
      add('用这段话', 'find', '⌕', `找论文：${say}`, 'agent 检索 3–6 篇入库，直接开聊', () => submitFind(t));
    };
    if (FIND_RE.test(t)) find();
    add('库里', 'search', '⌕', `搜索「${say}」`, '全部结果', () => go('search', t), true);
    if (hits?.q === t) {
      const h = hits.r;
      h.papers.slice(0, 4).forEach((p) => {
        add('库里', `p:${p.id}`, '▤', named(p), idLabel(p.id), () => openPaper(p.id), true);
        out[out.length - 1].peek = () => peekPaper(p.id);
      });
      h.threads.slice(0, 3).forEach((x) => add('库里', `t:${x.id}`, '◌', threadName(x), '讨论', () => openThread(x.scope, x.id), true));
      h.research.slice(0, 3).forEach((x) => add('库里', `r:${x.id}`, '☆', x.title, '研究', () => openResearch(x.id), true));
    }
    if (appState.value?.agent?.enabled) add('用这段话', 'ask', '◌', `和 agent 讨论：${say}`, '整个论文库，比如改主题树、归纳方向', () => ask(t));
    add('用这段话', 'jot', '✎', '记成一个想法', '开一段讨论，不叫 agent 回答', () => jot(t));
    if (!FIND_RE.test(t)) find();
    add('用这段话', 'cap', '＋', '收下这段文字', '按标题找原论文', submitCapture);
    add('用这段话', 'res', '☆', '以它开始一个研究', '', startResearch);
  }
  if (pid && !link && !files.length) {
    if (r.view !== 'read') add('这篇论文', 'c:read', '↗', '阅读原文', '', () => openReader(pid), true);
    if (r.view !== 'read') add('这篇论文', 'c:peek', '◫', '速览原文', '悬浮窗，不离开当前页', () => peekPaper(pid), true);
    add('这篇论文', 'c:chat', '◌', '讨论这篇', '', () => openReader(pid, { tab: 'chat' }), true);
    add('这篇论文', 'c:res', '☆', '加入研究', '', () => researchPicker({ heading: '把这篇论文加入研究', links: [{ kind: 'paper', ref: pid }] }), true);
  }
  if (!link && !files.length) {
    const topics = (appState.value?.topics || []).flatMap((x) => [x, ...x.children]);
    const q = t.toLowerCase();
    [...NAV.map(([v, label]) => ({ key: `n:${v}`, label, run: () => go(v) })),
      ...topics.map((x) => ({ key: `n:topic:${x.path}`, label: `主题 · ${x.path.replace('/', ' / ')}`, run: () => go('topic', x.path) }))]
      .filter((x) => !q || x.label.toLowerCase().includes(q))
      .forEach((x) => add('前往', x.key, '→', x.label, '', x.run, true));
  }
  return out;
}

function Palette({ leaving }) {
  const { text, thought, files } = cap.value;
  const t = text.trim(), link = /https?:\/\//.test(t);
  const [hits, setHits] = useState(null);
  const [sel, setSel] = useState(null);
  const ta = useRef(), box = useRef(), back = useRef(document.activeElement);
  useEffect(() => {
    ta.current?.focus();
    return () => setTimeout(() => { if (document.activeElement === document.body) back.current?.focus?.(); });
  }, []);
  useEffect(() => fit(ta.current), [text]);
  useEffect(() => {
    if (!t || link) return setHits(null);
    const id = setTimeout(() => get(`/api/search?q=${encodeURIComponent(t)}`).then((r) => setHits({ q: t, r })).catch(() => {}), 160);
    return () => clearTimeout(id);
  }, [t, link]);
  // Like a search engine's AI answer: once typing pauses, a quick model reads the catalog and its picks go on top.
  const [ai, setAi] = useState(null), cur = useRef();
  const aiWanted = t.length >= 2 && !link && !files.length && appState.value?.agent?.search_ai;
  useEffect(() => {
    setAi(null);
    if (!aiWanted) return;
    const id = setTimeout(() => {
      setAi({ q: t });
      get(`/api/search/ai?q=${encodeURIComponent(t)}`).then((r) => {
        setAi((a) => (a?.q === t ? { q: t, r: r || { answer: '', papers: [] } } : a));
        setSel((s) => s ?? cur.current);
      }).catch(() => setAi((a) => (a?.q === t ? null : a)));
    }, 1000);
    return () => clearTimeout(id);
  }, [t, aiWanted]);
  const items = paletteItems(t, link, files, hits, ai);
  // Selection follows the row, not the index, so results arriving while typing never move it.
  const i = Math.max(0, items.findIndex((x) => x.key === sel));
  cur.current = items[i]?.key;
  const run = (x, peeking) => {
    if (!x) return;
    (peeking && x.peek ? x.peek : x.run)();
    palOpen.value = false;
    if (x.nav) clearCap();
  };
  const key = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length];
      setSel(n?.key);
      setTimeout(() => box.current?.querySelector('.pal-item.on')?.scrollIntoView({ block: 'nearest' }));
    } else if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); run(items[i], e.altKey); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); palOpen.value = false; }
  };
  const set = (k) => (e) => { cap.value = { ...cap.value, [k]: e.target.value }; setSel(null); };
  let group = null;
  return html`<div class=${`pal-bg${leaving ? ' leaving' : ''}`} onMouseDown=${(e) => { if (e.target === e.currentTarget) palOpen.value = false; }}>
    <div class="pal" id="palette" role="dialog" aria-label="收下、搜索、找论文">
      <textarea ref=${ta} rows="1" class="pal-input" value=${text} onInput=${set('text')} onKeyDown=${key}
        placeholder="粘贴链接、截图或 PDF 收下；输入文字搜索、找论文、记想法、跳转…" aria-label="收下、搜索、找论文"></textarea>
      ${files.length ? html`<div class="pal-files">${files.map((f, j) => html`<div class="thumb">${f.mime === 'application/pdf'
        ? html`<div class="thumb-pdf"><b>PDF</b>${short(f.name, 18)}</div>` : html`<img src=${`data:${f.mime};base64,${f.data}`} alt="" />`}
        <button type="button" aria-label="移除" onClick=${() => { cap.value = { ...cap.value, files: files.filter((_, k) => k !== j) }; }}>×</button></div>`)}</div>` : null}
      ${link || files.length ? html`<div class="pal-thought"><span>想法</span><input value=${thought} onInput=${set('thought')} onKeyDown=${key}
        placeholder="顺手记一句为什么收（可选），会开一段讨论" /></div>` : null}
      <div class="pal-list" ref=${box} role="listbox">${ai?.q === t && !ai.r ? html`<div class="pal-group" aria-live="polite">AI 推荐 <span class="spin"></span>${appState.value?.agent?.search_api ? ' 正在库里和网上找…' : ''}</div>` : null}${items.map((x, j) => {
        const head = x.group !== group ? html`<div class="pal-group">${(group = x.group)}</div>` : null;
        return html`${head}<div key=${x.key} role="option" aria-selected=${j === i} class=${`pal-item ${j === i ? 'on' : ''}${x.wrap ? ' pal-answer' : ''}`}
            onMouseMove=${() => sel !== x.key && setSel(x.key)} onClick=${() => run(x)}>
          <span class="pal-icon">${x.icon}</span><span class="pal-label">${x.label}</span>${x.hint ? html`<span class="pal-hint">${x.hint}</span>` : null}
          ${x.peek ? html`<button type="button" class="btn sm ghost pal-peek" title="悬浮窗看原文，不离开当前页（⌥↵）" onClick=${(e) => { e.stopPropagation(); run(x, true); }}>速览</button>` : null}
          ${x.add ? html`<button type="button" class="btn sm pal-peek" title="加入论文库并打开它的卡片，agent 会写快读卡" onClick=${(e) => { e.stopPropagation(); run({ ...x, run: x.add }); }}>加入论文库</button>` : null}
          ${j === i ? html`<kbd>↵</kbd>` : null}</div>`;
      })}</div>
      <div class="pal-foot hint"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>↵</kbd> 执行</span>${items[i]?.peek ? html`<span><kbd>⌥↵</kbd> 速览</span>` : null}<span><kbd>⇧↵</kbd> 换行</span><span><kbd>Esc</kbd> 关闭，内容会留着</span></div>
    </div></div>`;
}

/** Always mounted: paste or drop anywhere opens the bar with it; dragging files shows where they will go. */
function PaletteHost() {
  const [drop, setDrop] = useState(false);
  const [open, leaving] = useLeaving(palOpen.value);
  useEffect(() => {
    let depth = 0;
    const enter = (e) => { if (e.dataTransfer?.types.includes('Files')) { depth++; setDrop(true); } };
    const leave = () => { if (--depth <= 0) { depth = 0; setDrop(false); } };
    const over = (e) => e.preventDefault();
    const dropped = (e) => {
      if (e.defaultPrevented) return;
      e.preventDefault(); depth = 0; setDrop(false);
      if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
      else { const t = e.dataTransfer.getData('text'); if (t) openPalette(t); }
    };
    const paste = (e) => {
      const fs = [...(e.clipboardData?.files || [])].filter(accepted);
      const typing = e.target instanceof Element && e.target.closest('input, textarea');
      const inBar = e.target instanceof Element && e.target.closest('#palette');
      if (fs.length && (!typing || inBar)) { e.preventDefault(); addFiles(fs); }
      else if (!typing && route.value.view !== 'read') {
        const t = e.clipboardData.getData('text');
        if (t) { e.preventDefault(); openPalette(t); }
      }
    };
    document.addEventListener('dragenter', enter); document.addEventListener('dragleave', leave);
    document.addEventListener('dragover', over); document.addEventListener('drop', dropped); document.addEventListener('paste', paste);
    return () => { for (const [k, f] of [['dragenter', enter], ['dragleave', leave], ['dragover', over], ['drop', dropped], ['paste', paste]]) document.removeEventListener(k, f); };
  }, []);
  return html`${drop ? html`<div class="drop-veil"><div>松手就收下：图片、截图或 PDF</div></div>` : null}${open ? html`<${Palette} leaving=${leaving} />` : null}`;
}

const PalTrigger = ({ cls = '' }) => html`<button class=${`pal-trigger ${cls}`} onClick=${() => openPalette()} title="收下、搜索、找论文、跳转（⌘K 或 /）">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
  <span>收下、搜索、找论文…</span><kbd>⌘K</kbd></button>`;

// ---------- recently deleted ----------

const TRASH_DAYS = 30;  // store.TRASH_DAYS
const TRASH_KIND = { paper: '论文', thread: '讨论' };

function TrashView() {
  const [items, reload] = useLive('/api/trash');
  const open = (r) => (r.kind === 'paper' ? openPaper(r.ref) : openThread(r.scope, r.ref));
  const restore = async (x) => {
    const r = await restoreTrash(x.id);
    if (r) { toast(`已恢复${TRASH_KIND[x.kind]}「${short(x.title || '', 24)}」`, '查看', () => open(r)); reload(); }
  };
  const purge = async (x) => {
    if (!confirm(`永久删除「${x.title}」？删除后不能再恢复。`)) return;
    try { await del(`/api/trash/${enc(x.id)}`); refresh(); reload(); } catch (e) { fail(e); }
  };
  const left = (x) => Math.max(1, TRASH_DAYS - Math.floor((Date.now() - new Date(x.deleted_at)) / 864e5));
  return html`<${ListDetail} single detail=${html`<div class="page pad">
    <h2 class="page-title">最近删除</h2>
    <p class="hint" style="margin-top:0">删除的论文和讨论会在这里保留 ${TRASH_DAYS} 天，之后自动永久删除。</p>
    ${!items ? html`<div class="empty"><span class="spin"></span></div>`
      : !items.length ? html`<div class="empty"><b>没有最近删除的内容</b>删掉的论文和讨论会先来到这里，${TRASH_DAYS} 天内都能恢复。</div>`
      : html`<div class="trash-list">${items.map((x) => html`<div class="trash-row" key=${x.id}>
          <span class="chip">${TRASH_KIND[x.kind] || x.kind}</span>
          <div class="trash-main"><div class=${`trash-title ${x.kind === 'paper' ? 'serif' : ''}`}>${x.title || '（无标题）'}</div>
            <div class="hint">${ago(x.deleted_at)} 删除${x.detail ? ` · ${x.detail}` : ''} · 还剩 ${left(x)} 天</div></div>
          <button class="btn sm" onClick=${() => restore(x)}>恢复</button>
          <button class="btn sm ghost danger" onClick=${() => purge(x)}>永久删除</button></div>`)}</div>`}
  </div>`} />`;
}

// ---------- body, overlays ----------

function Body() {
  const r = baseRoute.value;
  if (INBOX_VIEWS.includes(r.view)) return html`<${CapturesView} view=${r.view} />`;
  if (r.view === 'papers' || r.view === 'topic' || r.view === 'unsorted') return html`<${PapersView} view=${r.view} topic=${r.view === 'topic' ? r.arg : null} />`;
  if (r.view === 'search') return html`<${SearchView} q=${r.arg || ''} />`;
  if (r.view === 'research') return html`<${ResearchView} topic=${r.arg} />`;
  if (r.view === 'discuss') return html`<${DiscussView} threadId=${r.arg} />`;
  if (r.view === 'trash') return html`<${TrashView} />`;
  go('inbox');
  return null;
}

function Toast() {
  const t = toastSig.value;
  return html`<div id="toast" class=${t ? `show${t.isError ? ' error' : ''}` : ''} role=${t?.isError ? 'alert' : 'status'} aria-live=${t?.isError ? 'assertive' : 'polite'}>${t && html`<span>${t.text}</span>
    ${t.action ? html`<button onClick=${() => { toastSig.value = null; t.onAction(); }}>${t.action}</button>` : null}
    ${t.isError ? html`<button class="toast-x" aria-label="关闭" onClick=${() => { toastSig.value = null; }}>×</button>` : null}`}</div>`;
}

/** Backdrop click and Esc: ask first only if the user actually edited something in the modal. */
function dismissModal() {
  if (!modalSig.value?.edited || confirm('关闭会丢掉还没保存的内容，确定吗？')) closeModal();
}

function Modal() {
  const [m, leaving] = useLeaving(modalSig.value);
  const box = useRef(), open = !!modalSig.value;
  // Focus moves into the dialog (unless its content already focused something) and returns where it came from.
  useEffect(() => {
    if (!open) return;
    const prev = modalSig.value.back;
    if (!box.current?.contains(document.activeElement)) box.current?.querySelector('input:not([type=hidden]), textarea, select, button, [tabindex]')?.focus({ preventScroll: true });
    return () => setTimeout(() => { if (!document.activeElement || document.activeElement === document.body || box.current?.contains(document.activeElement)) prev?.focus?.(); });
  }, [open]);
  if (!m) return null;
  // Preact fills inputs via the value property, so defaultValue can't tell prefilled text from user edits.
  return html`<div class=${`modal-bg${leaving ? ' leaving' : ''}`} onMouseDown=${(e) => { if (e.target === e.currentTarget) dismissModal(); }}>
    <div class=${`modal ${m.wide ? 'wide' : ''}`} ref=${box} role="dialog" aria-modal="true" onInput=${() => { m.edited = true; }}>${m.vnode}</div></div>`;
}

function App() {
  const reading = route.value.view === 'read';
  // ponytail: the palette doesn't make the page inert (its actions focus the page before it unmounts), so Tab can leave it.
  const blocked = !!modalSig.value;
  const b = baseRoute.value;
  useEffect(() => {
    if (reading) return;  // the reader titles the window with its paper
    const label = { inbox: '收件箱', deep: '稍后深读', archived: '已存档', ignored: '已忽略', papers: '论文', unsorted: '未归类', search: '搜索', research: '研究', discuss: '讨论', trash: '最近删除' }[b.view];
    document.title = `${b.view === 'topic' ? b.arg : label || ''} · paper2brain`;
  }, [reading, b.view, b.arg]);
  useEffect(() => {
    loadState();
    const key = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palOpen.value = !palOpen.value; return; }
      if (e.key === 'Escape' && modalSig.value) { dismissModal(); return; }
      const t = e.target instanceof Element ? e.target : document.body;
      if (t.closest('input, textarea, select, [contenteditable]') || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); openPalette(); }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, []);
  return html`<div id="app" inert=${blocked}>
      <${Sidebar} />
      <main id="main">
        <nav id="mnav" aria-label="主导航"><${MobileMark} /><${MainNav} /></nav>
        <${PalTrigger} cls="mpal" />
        <${Body} />
      </main>
    </div>
    <${WorkMenuHost} />
    ${reading ? html`<div inert=${blocked}><${Reader} /></div>` : null}
    <${SelectionPopup} />
    <${Peek} />
    <${PaletteHost} />
    <${Toast} />
    <${Modal} />`;
}

// Every textarea grows with what you type and opens already fitted to prefilled text; ⌘/Ctrl+Enter submits its form.
document.addEventListener('input', (e) => { if (e.target instanceof HTMLTextAreaElement) fit(e.target); });
const fitted = new WeakSet();
// Segmented controls and tab bars draw their selection as one mark that slides to the chosen button.
function slideMarks() {
  for (const bar of document.querySelectorAll('.seg, .tabs, .rtabs')) {
    const on = bar.querySelector(':scope > .on');
    const box = on ? [on.offsetLeft, on.offsetTop, on.offsetWidth, on.offsetHeight] : [0, 0, 0, 0];
    const v = box.map((n) => `${n}px`).join(' ');
    if (bar.dataset.box === v) continue;
    bar.dataset.box = v;
    ['x', 'y', 'w', 'h'].forEach((k, j) => bar.style.setProperty(`--mark-${k}`, `${box[j]}px`));
    if (!bar.dataset.mark) requestAnimationFrame(() => { bar.dataset.mark = '1'; });
  }
}
new MutationObserver(() => {
  for (const t of document.getElementsByTagName('textarea')) if (!fitted.has(t) && t.offsetWidth) { fitted.add(t); fit(t); }
  slideMarks();
}).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class'] });
addEventListener('resize', slideMarks);
document.fonts?.ready.then(slideMarks);
// Clickable rows that aren't native buttons (role="link"/"button" + tabIndex) answer Enter and Space like one.
document.addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target instanceof Element && e.target.matches('[role=link][tabindex], [role=button][tabindex]')) { e.preventDefault(); e.target.click(); }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.defaultPrevented && e.target instanceof HTMLTextAreaElement && e.target.form) { e.preventDefault(); e.target.form.requestSubmit(); }
});

if (!location.hash) location.hash = '#/inbox';
render(html`<${App} />`, document.getElementById('root'));
