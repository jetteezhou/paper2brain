// The discussion workbench. Discussions form a tree: each node is a run of messages; a branch grows from a whole
// discussion or from one of its messages (fork point) and inherits the path above it. Left: the outline (stable
// order, fold, zoom, drag, Tab / Shift+Tab, j k h l). Right: the river — the path root→current read as one stream
// with tributaries inline where they fork — or the map, questions and conclusions the agent distilled.
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { html, get, post, del, ago, short, plain, threadName, live, Md, firstSentence, enc, fit, touch, Mark, readBase64 } from './lib.js';
import {
  appState, agentOn, useLive, refresh, toast, fail, restoreTrash, openThread, openReader, openCapture, openResearch, highlight, go, quoting,
  openInSpace, takeSwitchIntent,
} from './state.js';
import { researchPicker, openComposer, ResearchChip } from './research.js';
import { PaperChip } from './papers.js';

const SCOPE_KIND = { library: '论文库', topic: '主题', paper: '论文', papers: '多篇论文', find: '找论文', anchor: '划词', capture: '收下', research: '研究', writing: '写作', related: '相关工作', plan: '计划', code: '代码', experiment: '实验' };
const RESEARCH_PART = { writing: '写作', related: '相关工作', plan: '计划', code: '代码', experiment: '实验' };
const ofResearch = (k) => k === 'research' || k in RESEARCH_PART;
// a research grows from many sources, never from one paper or one passage of it
const canCompose = (k) => !ofResearch(k) && k !== 'paper' && k !== 'anchor';
const SUGGEST = {
  paper: ['用三句话讲清这篇的核心贡献', '和 π0、OpenVLA 相比有什么不同？', '真机实验做了哪些任务、成功率多少？'],
  topic: ['这个方向最近在解决什么问题？', '列出代表性方法和它们的关键区别'],
  library: ['最近收的论文里，哪几篇值得深读？', '库里有谁做过灵巧手相关的工作？', '把我最近的讨论归纳成几个方向'],
  anchor: ['这段在说什么？用大白话解释一下', '为什么要这样设计？有没有别的做法', '这和我的研究有什么关系？'],
  capture: ['它讲的是哪篇、核心贡献是什么？', '我收下时的想法成立吗？库里有没有证据', '如果值得做，最小的验证方式是什么？'],
  research: ['这个研究的问题成立吗？最强的反对意见是什么？', '和库里最接近的工作相比，方法上的区别在哪？', '帮我把验证方案收敛到一个最小实验'],
  related: ['和我们最接近的是哪几篇？区别说得清吗', '还漏了哪条重要的研究方向？补找几篇', '把这几条方向的段落改得更像审稿人想看的'],
  writing: ['按三要素和实验记录起草摘要和引言', '引言的故事链哪一环最弱？帮我改', '贡献列表能在正文里找到证据吗？'],
  plan: ['下一件最该做的事是什么？', '按现在的进度赶得上截止吗？不够的话砍什么', '最大的风险排在前面了吗？'],
  code: ['复现这篇最容易踩的坑在哪？对照代码说', '我的方法应该从代码哪里接进去？', '论文和代码有哪些出入？'],
  experiment: ['验证方案的最小实验具体怎么做？', '现有结果支持还是反驳核心洞见？', '下一个实验该做什么？对比谁、看什么指标'],
  find: ['把这几篇按方法路线分一分，列个对比表', '哪篇最值得先深读？为什么', '还漏了哪些重要工作？再补找几篇'],
  papers: ['它们各自解决什么问题？列个对比表', '方法上的关键分歧在哪？各自的证据是什么', '能不能组合起来？最小的验证实验是什么'],
};
const kindOf = (scope) => scope.split(':')[0];

export function scopeLabel(scope, title) {
  const k = kindOf(scope);
  if (k === 'research') return `研究《${title || '…'}》`;
  if (k in RESEARCH_PART) return `研究《${title || '…'}》的${RESEARCH_PART[k]}`;
  if (k === 'capture') return '收下的这条';
  if (k === 'anchor') return '选中的这段';
  if (k === 'find') return `找到的论文「${short(title || '…', 24)}」`;
  if (k === 'papers') return `选中的 ${scope.slice(7).split('|').length} 篇论文`;
  if (k === 'paper') return '这篇论文';
  if (k === 'topic') return `主题 ${scope.slice(6)}`;
  return '整个论文库';
}

function ScopeOpen({ scope }) {
  const k = kindOf(scope), ref = scope.slice(k.length + 1);
  if (k === 'paper') return html`<button class="btn sm ghost" onClick=${() => openReader(ref)}>打开论文</button>`;
  if (ofResearch(k)) return html`<button class="btn sm ghost" onClick=${() => openResearch(ref)}>打开研究</button>`;
  if (k === 'capture' || k === 'find') return html`<button class="btn sm ghost" onClick=${() => openCapture(ref)}>打开收下的内容</button>`;
  return null;
}

// ---------- shared pieces other views use ----------

/** The discussions of a paper or capture as short lines that open them. */
export function ThreadLines({ threads, paperId }) {
  return html`${(threads || []).map((t) => html`<div class="thought">
    <span class="thought-open" title="打开这段讨论" onClick=${() => openThread(t.scope, t.id)}>${threadName(t)}<small>${t.scope_title && !t.scope.startsWith('paper:') ? `${SCOPE_KIND[kindOf(t.scope)]} · ` : ''}${ago(t.created_at)}</small></span>
    <span class="thought-acts">${(t.research || []).map((x) => html`<${ResearchChip} x=${x} />`)}
      <button class="btn sm ghost" title="作为素材加入研究" onClick=${(e) => { e.stopPropagation(); joinResearch(t, paperId); }}>加入研究</button></span></div>`)}`;
}
const joinResearch = (t, paperId) => researchPicker({
  heading: '把这段讨论加入研究', title: firstSentence(threadName(t)), summary: threadName(t),
  links: [{ kind: 'thread', ref: t.id }, ...(paperId ? [{ kind: 'paper', ref: paperId }] : [])],
});

/** One line that opens a new discussion of a scope with what the user wrote, as the first question to the agent
 * (with the agent off it is only written down). */
export function JotForm({ scope, placeholder, onDone }) {
  const [v, setV] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    if (!v.trim()) return;
    try {
      const asking = agentOn.value;
      const t = await post('/api/threads', asking ? { scope } : { scope, text: v.trim() });
      if (asking) await post('/api/chat', { scope, text: v.trim(), thread_id: t.id });
      setV('');
      if (!asking) toast('已记下，开了一段新讨论', '去讨论', () => openThread(t.scope, t.id));
      refresh();
      onDone?.(t);
    } catch (err) { fail(err); }
  };
  return html`<form class="add-line" onSubmit=${submit}><input value=${v} onInput=${(e) => setV(e.target.value)} placeholder=${placeholder} /><button class="btn sm">${agentOn.value ? '发送' : '记下'}</button></form>`;
}

// ---------- the chat: one discussion read as the whole path from the root ----------

/** Keeps the messages pinned to the bottom while you are there (a reply being written, the input growing);
 * scroll up to read and it stays put until the next message. */
function useStickToBottom(box, n, id) {
  const stuck = useRef(true);
  const pin = () => {
    const el = box.current;
    if (!el) return;
    if (highlight.value) stuck.current = false;
    else if (stuck.current) el.scrollTop = el.scrollHeight;
  };
  useEffect(() => { stuck.current = true; pin(); }, [n, id]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onScroll = () => { stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; };
    const mo = new MutationObserver(pin), ro = new ResizeObserver(pin);
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    ro.observe(el);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => { mo.disconnect(); ro.disconnect(); el.removeEventListener('scroll', onScroll); };
  }, [id]);
}

function Msg({ m, t, onFork, shelf }) {
  const ref = useRef();
  const flash = highlight.value === m.id;
  useEffect(() => { if (flash) { ref.current?.scrollIntoView({ block: 'center' }); setTimeout(() => (highlight.value = null), 4000); } }, [flash]);
  const text = m.run_text || m.text, running = live(m.run_status), tools = m.run_tools || [];
  const [stopping, setStopping] = useState(false);
  const stop = async () => { setStopping(true); try { await post(`/api/runs/${enc(m.run_id)}/cancel`); refresh(); } catch (e) { setStopping(false); fail(e); } };
  const addToResearch = () => {
    const k = kindOf(t.scope), ref2 = t.scope.slice(k.length + 1), s = plain(text);
    researchPicker({ heading: '把这段讨论加入研究', title: firstSentence(s), summary: short(s, 300),
      links: [{ kind: 'message', ref: m.id }, ...(k === 'paper' ? [{ kind: 'paper', ref: ref2 }] : [])], topics: k === 'topic' ? [ref2] : [] });
  };
  const acts = onFork ? html`${(m.research || []).map((x) => html`<${ResearchChip} x=${x} />`)}
    <button class="btn sm ghost" onClick=${addToResearch} title="作为素材加入研究">加入研究</button>
    <button class="btn sm ghost" onClick=${() => onFork(m)} title="从这条消息分出一段新讨论，带着到这里为止的上下文">从这里分叉</button>` : null;
  const forks = shelf !== undefined ? shelf
    : m.forks?.length ? html`<div class="forks">↳ 从这里分出：${m.forks.map((f) => html`<a href="#" onClick=${(e) => { e.preventDefault(); openThread(f.scope, f.id); }}>${short(threadName(f), 30)}</a>`)}</div>` : null;
  if (m.role === 'user') {
    return html`<div class="msg-wrap user" id=${`m-${m.id}`} ref=${ref}><div class=${`msg user${flash ? ' flash' : ''}`}>${m.text}</div>
      <div class="msg-foot right">${acts}</div>${forks}</div>`;
  }
  return html`<div class=${`msg bot${flash ? ' flash' : ''}`} id=${`m-${m.id}`} ref=${ref}>${text ? html`<${Md} text=${text} />` : null}
    ${running ? html`<div class="typing"><${Mark} spin />${text ? '还在写…' : tools.length ? '正在查阅论文库…' : m.run_status === 'WAITING' ? '排队中：前面还有 agent 在运行…' : 'Agent 启动中…'}
      ${m.run_id ? html`<button class="btn sm ghost" disabled=${stopping} title="停下这一轮，直接发下一句纠正它" onClick=${stop}>${stopping ? '正在停止…' : '停止'}</button>` : null}</div>
      ${tools.length ? html`<div class="tools">${tools.slice(-3).map((x) => html`<div>${x.status === 'running' ? '…' : '✓'} ${x.name} ${x.args || ''}</div>`)}</div>` : null}` : null}
    ${m.run_status === 'CANCELLED' ? html`<div class="hint">已停止</div>` : null}
    ${m.run_status === 'ERROR' ? html`<div class="hint" style="color:var(--red)">agent 出错了${m.run_error ? '：' + m.run_error : ''}</div>` : null}
    ${!running && text ? html`<div class="msg-foot">${acts}</div>` : null}${forks}</div>`;
}

/** What a branch inherits: the ancestors' messages up to each fork point, folded unless asked for. */
function Inherited({ path }) {
  const [open, setOpen] = useState(false);
  const n = path.reduce((a, s) => a + s.messages.length, 0);
  if (!n) return null;
  const last = path[path.length - 1].messages.slice(-1)[0];
  return html`<div class="inherited">
    <button class="inherited-toggle" onClick=${() => setOpen(!open)}>${open ? '收起' : '展开'}上级讨论带下来的 ${n} 条消息</button>
    ${open ? path.map((s) => html`<div class="inherited-seg"><a href="#" class="hint" onClick=${(e) => { e.preventDefault(); openThread(s.scope, s.id); }}>「${short(threadName(s), 40)}」</a>
        ${s.messages.map((m) => html`<div class=${`inh-msg ${m.role}`}>${m.role === 'user' ? m.text : html`<${Md} text=${m.text} />`}</div>`)}</div>`)
      : last && html`<div class=${`inh-msg ${last.role}`}>${last.role === 'user' ? short(last.text, 160) : short(plain(last.text), 160)}</div>`}</div>`;
}

function TitleLine({ t: thread }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState('');
  const t = { ...thread, first: thread.messages.find((m) => m.role === 'user')?.text };
  if (t.space) return html`<div class="tree-title"><b>${t.title || threadName(t)}</b></div>`; // read through from a space: no renaming here
  const save = async (e) => {
    e.preventDefault();
    try { await post(`/api/threads/${enc(t.id)}`, { title: v }); setEditing(false); toast('已保存标题'); refresh(); } catch (err) { fail(err); }
  };
  if (editing) {
    return html`<form class="add-line tree-title-form" onSubmit=${save}><input value=${v} onInput=${(e) => setV(e.target.value)} placeholder="一句话说清这段讨论在想什么" ref=${(el) => el?.focus()} />
      <button class="btn sm primary">保存</button><button type="button" class="btn sm ghost" onClick=${() => setEditing(false)}>取消</button></form>`;
  }
  return html`<div class="tree-title"><b onDblClick=${() => { setV(threadName(t)); setEditing(true); }}>${t.title || threadName(t)}</b>
    <span class="hint">${t.title ? (t.title_by === 'user' ? '你起的' : 'agent 起的') : agentOn.value ? 'agent 会起个标题' : ''}</span>
    <button class="btn sm ghost" onClick=${() => { setV(threadName(t)); setEditing(true); }}>改标题</button></div>`;
}

/** The input every discussion ends in: Enter sends to the agent (with the agent off, only writes it down). */
export function Composer({ t, reload, draft, setDraft, input }) {
  const on = agentOn.value;
  const [imgs, setImgs] = useState([]);
  const [models] = useLive(on ? '/api/agent/models' : null, { poll: false });
  const [chosen, setChosen] = useState(null);
  useEffect(() => setChosen(null), [t.id]);
  // a model is picked by backend and id together: both backends can offer the same id
  const key = (m) => m && `${m.backend || 'cursor'}:${m.id}`;
  const switchTo = chosen && chosen !== key(t.model) ? models?.models.find((m) => key(m) === chosen) : null;
  const addImgs = async (fs) => {
    fs = fs.filter((f) => /^image\/(png|jpeg|gif|webp)$/.test(f.type));
    if (!fs.length) return false;
    const got = await Promise.all(fs.map(async (f) => ({ mime: f.type, data: await readBase64(f) })));
    setImgs((xs) => [...xs, ...got].slice(0, 5));
    return true;
  };
  const send = async (reply) => {
    const text = draft.trim(), images = reply ? imgs : [];
    if (!text && !images.length) return;
    setDraft(''); if (images.length) setImgs([]);
    const model = reply && switchTo ? { id: switchTo.id, backend: switchTo.backend } : undefined;
    try { await post('/api/chat', { scope: t.scope, text, thread_id: t.id, model, ...(reply ? { images } : { reply: false }) }); if (model) setChosen(null); reload(); refresh(); } catch (e) { setDraft(text); setImgs(images); fail(e); }
  };
  const last = t.messages[t.messages.length - 1];
  const canRedo = on && last?.role === 'assistant' && !live(last.run_status);
  const redo = async () => {
    const model = switchTo ? { id: switchTo.id, backend: switchTo.backend } : undefined;
    try { await post('/api/chat', { scope: t.scope, thread_id: t.id, model, redo: true }); setChosen(null); reload(); refresh(); } catch (e) { fail(e); }
  };
  useEffect(() => fit(input.current), [draft]);
  // a quote with no scope (from the notepad) goes to whichever discussion is on screen
  useEffect(() => {
    const q = quoting.value;
    if (!q || (q.scope && q.scope !== t.scope)) return;
    quoting.value = null;
    setDraft((d) => `${d ? d.trimEnd() + '\n' : ''}${q.raw ? q.text : `「${q.text}」\n`}`);
    setTimeout(() => { const el = input.current; el?.focus(); el?.setSelectionRange(el.value.length, el.value.length); });
  }, [quoting.value, t.scope]);
  const pick = (e) => { addImgs([...e.target.files]); e.target.value = ''; };
  return html`<form class="chat-input" onSubmit=${(e) => { e.preventDefault(); send(on); }}
    onDragOver=${(e) => on && e.preventDefault()}
    onDrop=${(e) => { if (on && [...e.dataTransfer.files].some((f) => f.type.startsWith('image/'))) { e.preventDefault(); addImgs([...e.dataTransfer.files]); } }}>
    ${imgs.length ? html`<div class="chat-imgs">${imgs.map((f, j) => html`<div class="thumb"><img src=${`data:${f.mime};base64,${f.data}`} alt="" />
      <button type="button" aria-label="移除" onClick=${() => setImgs(imgs.filter((_, k) => k !== j))}>×</button></div>`)}</div>` : null}
    ${on ? html`<label class="btn ghost chat-attach" title="附图片（也可以直接粘贴或拖进来），最多 5 张">＋<input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden onChange=${pick} /></label>` : null}
    <textarea rows="1" ref=${input} value=${draft} onInput=${(e) => setDraft(e.target.value)}
      onPaste=${(e) => { const fs = [...(e.clipboardData?.files || [])]; if (on && fs.length) { e.preventDefault(); e.stopPropagation(); addImgs(fs); } }}
      onKeyDown=${(e) => { if (e.key === 'Enter' && !e.isComposing && (e.metaKey || e.ctrlKey || (!e.shiftKey && !touch))) { e.preventDefault(); send(on); } }}
      placeholder=${on ? '问点什么…' : 'Agent 未连接，先记下来'}
      title=${on ? (touch ? '点发送' : '回车发送，Shift+回车换行') : 'Agent 未连接：先记下，连上后再接着聊'}></textarea>
    ${on && models ? html`<select class="chat-model" value=${chosen || key(t.model) || ''} onChange=${(e) => setChosen(e.target.value)}
        title=${switchTo ? '下一句会换到这个模型：新开一个 agent，带上前面说过的' : '这段讨论用的模型'}>
      ${t.model ? null : html`<option value="">默认模型</option>`}
      ${t.model && !models.models.some((m) => key(m) === key(t.model)) ? html`<option value=${key(t.model)}>${t.model.id}</option>` : null}
      ${models.models.map((m) => html`<option value=${key(m)}>${m.displayName || m.id}</option>`)}</select>` : null}
    <span class="grow"></span>
    ${canRedo ? html`<button type="button" class="btn ghost" onClick=${redo}
      title=${switchTo ? `用 ${switchTo.displayName || switchTo.id} 重新回答上一个问题，替换掉现在的回答` : '重新回答上一个问题，替换掉现在的回答（可以先在左边换个模型）'}>重新生成</button>` : null}    <button class="btn primary">${on ? '发送' : '记下'}</button></form>`;
}

/** The opening of an empty discussion: what it is about, plus a few questions to start from. */
function EmptyChat({ t, setDraft, input }) {
  const on = agentOn.value;
  return html`<div class="chat-empty"><b>讨论${scopeLabel(t.scope, t.scope_title)}</b><p class="hint">回答会引用原文页码；值得留下的结论可以存成笔记。</p>
    <div class="sugs">${(SUGGEST[kindOf(t.scope)] || SUGGEST.library).map((s) => html`<button class="sug" disabled=${!on} onClick=${() => { setDraft(s); setTimeout(() => input.current?.focus()); }}>${s}</button>`)}</div></div>`;
}

/** Delete a discussion into the trash: then() moves off it, undo restores it and back() returns to it. */
async function removeThread(t, then, back) {
  let r;
  try { r = await del(`/api/threads/${enc(t.id)}`); } catch (e) { fail(e); return; }
  then();
  refresh();
  const name = t.title || t.messages.find((m) => m.role === 'user' && m.text)?.text || threadName(t);
  toast(`已删除讨论「${short(name, 24)}」，可在“最近删除”找回`, '撤销', async () => { if (await restoreTrash(r.trash_id)) back(); }, false, 10000);
}
const DeleteThread = ({ onClick }) => html`<button class="btn sm ghost danger" onClick=${onClick} title="移到“最近删除”，30 天内可以恢复">删除</button>`;

export function Chat({ scope, threadId, onThread, inWorkbench }) {
  const [tid, setTid] = useState(threadId || null);
  useEffect(() => setTid(threadId || null), [threadId, scope]);
  const [t, reload] = useLive(tid ? `/api/threads?id=${enc(tid)}` : `/api/threads?scope=${enc(scope)}`);
  const [draft, setDraft] = useState('');
  const box = useRef(), input = useRef();
  const n = t?.messages.length || 0;
  useStickToBottom(box, n, t?.id);
  const switchTo = (id) => { setTid(id); onThread?.(id); };
  if (!t) return html`<div class="chat"><div class="chat-empty"><span class="spin"></span></div></div>`;
  const k = kindOf(t.scope);

  const branch = async (fork, fresh) => {
    try {
      const nt = await post('/api/threads', fresh ? { scope: t.scope } : fork ? { scope: t.scope, fork_message_id: fork.id } : { scope: t.scope, parent_id: t.id });
      switchTo(nt.id);
      refresh();
      setTimeout(() => input.current?.focus(), 50);
    } catch (e) { fail(e); }
  };
  const compose = () => {
    const first = t.messages.find((m) => m.role === 'user');
    const ref = t.scope.slice(k.length + 1);
    openComposer({ links: [{ kind: 'thread', ref: t.id, label: t.title || first?.text || scopeLabel(t.scope, t.scope_title) },
      ...t.scope_papers.map((p) => ({ kind: 'paper', ref: p.id, label: p.title }))], topics: k === 'topic' ? [ref] : [] });
  };
  const sib = t.siblings || [], si = sib.findIndex((s) => s.id === t.id);
  const papers = [...t.scope_papers, ...t.papers.filter((p) => !t.scope_papers.some((q) => q.id === p.id))];
  const empty = !n && !t.path.length;

  return html`<div class="chat">
    <div class="chat-head"><span class="chat-scope" title=${scopeLabel(t.scope, t.scope_title)}>范围：${scopeLabel(t.scope, t.scope_title)}</span>
      ${t.proposal ? html`<a href="#" class="chip violet" onClick=${(e) => { e.preventDefault(); openResearch(t.scope.slice(k.length + 1)); }}>有修改稿待你确认 →</a>` : null}
      ${sib.length > 1 ? html`<span class="branch-switch" title=${t.parent_id ? '同一处分出的几个分支' : '这个范围里并列的几段讨论'}>
        <button disabled=${si <= 0} onClick=${() => switchTo(sib[si - 1].id)}>‹</button>${t.parent_id ? '分支' : '讨论'} ${si + 1}/${sib.length}<button disabled=${si >= sib.length - 1} onClick=${() => switchTo(sib[si + 1].id)}>›</button></span>` : null}
      <span class="grow"></span>
      ${inWorkbench ? html`<${ScopeOpen} scope=${t.scope} />` : null}
      ${n && canCompose(k) ? html`<button class="btn sm ghost" onClick=${compose} title="把这段讨论作为素材开始一个研究">开始研究</button>` : null}
      ${n ? html`<button class="btn sm ghost" onClick=${() => branch(null)} title="在这段讨论下面开一段新的">分出新讨论</button>` : null}
      ${n || t.path.length ? html`<button class="btn sm ghost" onClick=${() => branch(null, true)} title="同一范围，不带前面的上下文，从头开始">发起新讨论</button>` : null}
      ${n ? html`<${DeleteThread} onClick=${() => removeThread(t, () => switchTo(t.parent_id || sib.find((s) => s.id !== t.id)?.id || null), () => switchTo(t.id))} />` : null}</div>
    <div class="msgs" ref=${box}>
      ${n || t.path.length ? html`<div class="tree-bar">
        ${t.path.length ? html`<div class="crumbs">${t.path.map((s) => html`<a href="#" onClick=${(e) => { e.preventDefault(); switchTo(s.id); }}>${short(threadName(s), 24)}</a><span>›</span>`)}</div>` : null}
        <${TitleLine} t=${t} />
        ${t.children.filter((c) => !c.fork_message_id).length ? html`<div class="hint">分出去的：${t.children.filter((c) => !c.fork_message_id).map((c) => html`<a href="#" onClick=${(e) => { e.preventDefault(); switchTo(c.id); }}>${short(threadName(c), 30)}</a>　`)}</div>` : null}</div>` : null}
      ${papers.length ? html`<div class="scope-papers"><span class="hint">${k === 'find' ? `已入库 ${t.scope_papers.length} 篇` : '论文'}</span>${papers.map((p) => html`<${PaperChip} id=${p.id} title=${p.title} />`)}</div>` : null}
      <${Inherited} path=${t.path} />
      ${empty ? html`<${EmptyChat} t=${t} setDraft=${setDraft} input=${input} />`
        : t.messages.map((m) => html`<${Msg} key=${m.id} m=${m} t=${t} onFork=${branch} />`)}
      ${!n && t.path.length ? html`<div class="chat-empty"><b>从这里接着想</b><p class="hint">这段分支带着上面的上下文，问点什么，或者先记一句。</p></div>` : null}
    </div>
    <${Composer} t=${t} reload=${reload} draft=${draft} setDraft=${setDraft} input=${input} /></div>`;
}

// ---------- the river: the selected path read as one stream, root at the top, tributaries inline where they fork ----------

/** Tributary cards right where the stream forks: one per branch, the one being read marked. */
function Shelf({ forks, nextId, outflow }) {
  return html`<div class="shelf"><span class="shelf-tag">${outflow ? '下游分出' : '⑂ 岔路'}</span>
    ${forks.map((f) => html`<button key=${f.id} class=${`shelf-card ${f.id === nextId ? 'on' : ''}`}
        title=${f.id === nextId ? '正在读的这条支流' : '河流从这里改道'} onClick=${() => go('discuss', f.id)}>
      <b>${short(threadName(f), 28)}</b><span>${ago(f.created_at)}</span></button>`)}</div>`;
}

/** An ancestor reach: dimmer water already flowed through, the forks still visible where they happened. */
function UpReach({ seg, nextId, onFork }) {
  const [open, setOpen] = useState(() => innerWidth > 900);
  useEffect(() => { if (highlight.value && seg.messages.some((m) => m.id === highlight.value)) setOpen(true); }, [highlight.value]);
  const byMid = {};
  for (const c of seg.children || []) if (c.fork_message_id) (byMid[c.fork_message_id] = byMid[c.fork_message_id] || []).push(c);
  const outflow = (seg.children || []).filter((c) => !c.fork_message_id);
  const forks = (seg.children || []).length;
  return html`<div class=${`reach up ${open ? '' : 'folded'}`}>
    <div class="reach-bar">
      <button class="reach-head" title="溯洄：把这段讨论变成当前" onClick=${() => go('discuss', seg.id)}>⌃ ${short(threadName(seg), 56)}</button>
      <button class="btn sm ghost reach-toggle" onClick=${() => setOpen(!open)}>${open ? '收起上级' : `展开 ${seg.messages.length} 条${forks > 1 ? ` · ${forks} 分支` : ''}`}</button>
    </div>
    ${open ? html`${seg.messages.map((m) => html`<div key=${m.id} id=${`m-${m.id}`} class=${`up-msg ${m.role}${highlight.value === m.id ? ' flash' : ''}`}>
      <div class="up-body">${m.role === 'user' ? m.text : html`<${Md} text=${m.text} />`}</div>
      ${onFork ? html`<button class="btn sm ghost fork-here" title="从这条消息分出一段新讨论" onClick=${() => onFork(m, seg)}>⑂ 分叉</button>` : null}
      ${byMid[m.id]?.length ? html`<${Shelf} forks=${byMid[m.id]} nextId=${nextId} />` : null}</div>`)}
      ${outflow.length ? html`<${Shelf} forks=${outflow} nextId=${nextId} outflow />` : null}` : null}
  </div>`;
}

function River({ t, reload }) {
  const [draft, setDraft] = useState('');
  const box = useRef(), input = useRef();
  const n = t.messages.length, k = kindOf(t.scope);
  useStickToBottom(box, n, t.id);
  useEffect(() => {  // a backlink may point at an upstream message: it flashes right in the stream
    if (highlight.value) { document.getElementById(`m-${highlight.value}`)?.scrollIntoView({ block: 'center' }); setTimeout(() => (highlight.value = null), 4000); }
  }, [t.id, highlight.value]);
  const chain = [...t.path, t];
  const papers = [...t.scope_papers, ...t.papers.filter((p) => !t.scope_papers.some((q) => q.id === p.id))];
  const outflow = t.children.filter((c) => !c.fork_message_id);
  const empty = !n && !t.path.length;

  const fork = async (m, seg) => {
    try {
      const nt = await post('/api/threads', { scope: (seg || t).scope, fork_message_id: m.id });
      go('discuss', nt.id);
      refresh();
      setTimeout(() => input.current?.focus(), 80);
    } catch (e) { fail(e); }
  };
  const branch = async () => {
    try {
      const nt = await post('/api/threads', { scope: t.scope, parent_id: t.id });
      go('discuss', nt.id);
      refresh();
      setTimeout(() => input.current?.focus(), 80);
    } catch (e) { fail(e); }
  };
  const compose = () => {
    const first = t.messages.find((m) => m.role === 'user'), ref = t.scope.slice(k.length + 1);
    openComposer({ links: [{ kind: 'thread', ref: t.id, label: t.title || first?.text || scopeLabel(t.scope, t.scope_title) },
      ...t.scope_papers.map((p) => ({ kind: 'paper', ref: p.id, label: p.title }))], topics: k === 'topic' ? [ref] : [] });
  };

  return html`<div class="river">
    <div class="chat-head"><button class="btn sm ghost m-back" onClick=${() => go('discuss')}>← 列表</button>
      <span class="chat-scope" title=${scopeLabel(t.scope, t.scope_title)}>范围：${scopeLabel(t.scope, t.scope_title)}</span>
      ${t.proposal ? html`<a href="#" class="chip violet" onClick=${(e) => { e.preventDefault(); openResearch(t.scope.slice(k.length + 1)); }}>有修改稿待你确认 →</a>` : null}
      <span class="grow"></span>
      ${t.space ? html`<span class="chip" title="这段讨论在共享空间里，这里只读">共享 · ${t.space.name}</span>
        <button class="btn sm primary" onClick=${() => openInSpace(t.space, 'discuss', t.id)}>去「${t.space.name}」继续 →</button>` : html`
      <${ScopeOpen} scope=${t.scope} />
      ${n && canCompose(k) ? html`<button class="btn sm ghost" onClick=${compose} title="把这段讨论作为素材开始一个研究">开始研究</button>` : null}
      ${n ? html`<button class="btn sm ghost" onClick=${branch} title="在这段讨论下面开一段新的">分出新讨论</button>` : null}
      ${n ? html`<${DeleteThread} onClick=${() => removeThread(t, () => go('discuss', t.parent_id || undefined), () => go('discuss', t.id))} />` : null}`}</div>
    <div class="river-flow" ref=${box}>
      ${papers.length ? html`<div class="scope-papers"><span class="hint">${k === 'find' ? `已入库 ${t.scope_papers.length} 篇` : '论文'}</span>${papers.map((p) => html`<${PaperChip} id=${p.id} title=${p.title} />`)}</div>` : null}
      ${chain.map((seg, i) => i < chain.length - 1
        ? html`<${UpReach} key=${seg.id} seg=${seg} nextId=${chain[i + 1].id} onFork=${t.space ? null : fork} />`
        : html`<div class="reach now" key=${seg.id}>
          ${n || chain.length > 1 ? html`<div class="reach-now">${chain.length > 1 ? html`<span class="reach-tag">下游 · 当前</span>` : null}<${TitleLine} t=${seg} /></div>` : null}
          ${empty ? html`<${EmptyChat} t=${seg} setDraft=${setDraft} input=${input} />`
            : seg.messages.map((m) => html`<${Msg} key=${m.id} m=${m} t=${seg} onFork=${t.space ? null : (mm) => fork(mm)} shelf=${m.forks?.length ? html`<${Shelf} forks=${m.forks} />` : null} />`)}
          ${!n && t.path.length ? html`<div class="chat-empty"><b>从这里接着想</b><p class="hint">这段分支带着上面的上下文，问点什么，或者先记一句。</p></div>` : null}
          ${outflow.length ? html`<${Shelf} forks=${outflow} outflow />` : null}
        </div>`)}
    </div>
    ${t.space ? html`<div class="chat-empty"><p class="hint">只读：这段讨论在共享空间「${t.space.name}」里，内容随空间实时更新。要继续讨论，点上面的按钮进入那个空间。</p></div>`
      : html`<${Composer} t=${t} reload=${reload} draft=${draft} setDraft=${setDraft} input=${input} />`}</div>`;
}

function RiverLoader({ id }) {
  const [t, reload] = useLive(`/api/threads?id=${enc(id)}`);
  if (!t) return html`<div class="river"><div class="chat-empty" style="margin:auto"><span class="spin"></span></div></div>`;
  return html`<${River} t=${t} reload=${reload} />`;
}

// ---------- the outline ----------

const folded = signal(new Set(JSON.parse(localStorage.getItem('p2b.folded') || '[]')));
const setFold = (id, on) => {
  const s = new Set(folded.value);
  on ? s.add(id) : s.delete(id);
  folded.value = s;
  localStorage.setItem('p2b.folded', JSON.stringify([...s]));
};
const zoom = signal(null);
const pane = signal('river');

/** Nodes keyed by id: newest activity first at every level, so the latest discussion sits at the top. */
function buildTree(list) {
  const byId = Object.fromEntries(list.map((t) => [t.id, { ...t, kids: [] }]));
  const roots = [];
  for (const t of Object.values(byId)) (byId[t.parent_id] ? byId[t.parent_id].kids : roots).push(t);
  const when = (t) => t.last_at || t.created_at;
  const desc = (a, b) => (when(a) < when(b) ? 1 : when(a) > when(b) ? -1 : 0);
  for (const t of Object.values(byId)) t.kids.sort(desc);
  roots.sort(desc);
  return { byId, roots };
}
const under = (byId, id, ancestor) => { for (let u = byId[id]?.parent_id; u; u = byId[u]?.parent_id) if (u === ancestor) return true; return false; };

async function move(id, parent, label) {
  try {
    await post(`/api/threads/${enc(id)}`, { parent_id: parent });
    toast(parent ? `已挂到「${short(label, 24)}」下面` : '已放回顶层');
    refresh();
  } catch (e) { fail(e); }
}

function Row({ t, depth, sel, onSelect, tree, renaming, setRenaming, drag, setDrag }) {
  const kids = t.kids.length, isFolded = folded.value.has(t.id);
  const [v, setV] = useState('');
  const rename = async (e) => {
    e.preventDefault();
    setRenaming(null);
    if (v.trim() && v.trim() !== threadName(t)) {
      try { await post(`/api/threads/${enc(t.id)}`, { title: v.trim() }); refresh(); } catch (err) { fail(err); }
    }
  };
  const k = kindOf(t.scope);
  const over = drag && drag.over === t.id;
  return html`<div class=${`ol-row ${sel ? 'on' : ''} ${over ? 'drop' : ''}`} style=${`padding-left:${10 + depth * 18}px`} data-id=${t.id}
      draggable=${!t.space} onDragStart=${(e) => { if (t.space) return; e.dataTransfer.setData('text/x-thread', t.id); e.dataTransfer.effectAllowed = 'move'; setDrag({ id: t.id }); }}
      onDragOver=${(e) => { if (!t.space && drag && drag.id !== t.id && !under(tree.byId, t.id, drag.id)) { e.preventDefault(); if (drag.over !== t.id) setDrag({ ...drag, over: t.id }); } }}
      onDrop=${(e) => { e.preventDefault(); e.stopPropagation(); const id = drag?.id; setDrag(null); if (!t.space && id && id !== t.id && tree.byId[id].parent_id !== t.id) move(id, t.id, threadName(t)); }}
      onDragEnd=${() => setDrag(null)}
      onClick=${() => onSelect(t.id)}>
    <span class=${`ol-caret ${kids ? '' : 'leaf'} ${isFolded ? '' : 'open'}`} onClick=${(e) => { e.stopPropagation(); if (kids) setFold(t.id, !isFolded); }}>${kids ? '▸' : ''}</span>
    <span class="ol-bullet" title="放大：只看这一枝" onClick=${(e) => { e.stopPropagation(); zoom.value = t.id; }}>${kids && isFolded ? '◉' : '•'}</span>
    <div class="ol-body">
      ${renaming === t.id
        ? html`<form onSubmit=${rename}><input class="ol-rename" value=${v} ref=${(el) => { if (el && !el.dataset.f) { el.dataset.f = 1; el.focus(); el.select(); } }}
            onInput=${(e) => setV(e.target.value)} onBlur=${rename} onKeyDown=${(e) => { if (e.key === 'Escape') { e.stopPropagation(); setRenaming(null); } }} /></form>`
        : html`<div class="ol-title" onDblClick=${(e) => { e.stopPropagation(); if (t.space) return; setV(threadName(t)); setRenaming(t.id); }}>${threadName(t)}</div>`}
      <div class="ol-meta"><span class="chip">${SCOPE_KIND[k] || k}</span>${t.space ? html`<span class="chip" title="在共享空间「${t.space.name}」里；这里只读，进去才能继续">共享 · ${t.space.name}</span>` : null}${k !== 'library' && t.scope_title ? html`<span class="src">${short(plain(t.scope_title), 28)}</span>` : null}
        <span>${t.n} 条</span><time>${ago(t.last_at)}</time>${kids && isFolded ? html`<span>· ${kids} 个分支</span>` : null}
        ${t.parent_by === 'agent' ? html`<span class="chip violet" title="agent 觉得它接着上一段；点 × 放回顶层">agent 挂的<button class="x" onClick=${(e) => { e.stopPropagation(); move(t.id, null); }}>×</button></span>` : null}</div>
    </div></div>`;
}

function Outline({ list, selId, onSelect }) {
  const [q, setQ] = useState('');
  const [renaming, setRenaming] = useState(null);
  const [drag, setDrag] = useState(null);
  const topics = appState.value ? appState.value.topics.flatMap((t) => [t, ...t.children]) : [];
  const tree = useMemo(() => buildTree(list), [list]);
  const focus = zoom.value && tree.byId[zoom.value] ? tree.byId[zoom.value] : null;
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (t) => words.every((w) => `${threadName(t)} ${t.first || ''} ${t.scope_title || ''}`.toLowerCase().includes(w));
  const keep = (t) => !words.length || hit(t) || t.kids.some(keep);
  const rows = [];
  const walk = (t, depth) => {
    if (!keep(t)) return;
    rows.push({ t, depth });
    if (words.length || !folded.value.has(t.id)) t.kids.forEach((c) => walk(c, depth + 1));
  };
  (focus ? [focus] : tree.roots).forEach((t) => walk(t, 0));
  const crumbs = [];
  for (let u = focus; u; u = tree.byId[u.parent_id]) crumbs.unshift(u);
  const box = useRef();

  const key = (e) => {
    if (e.target.closest('input, textarea, select')) return;
    const i = rows.findIndex((r) => r.t.id === selId), cur = rows[i]?.t;
    const k = e.key, pick = (t) => { if (t) { e.preventDefault(); onSelect(t.id); setTimeout(() => box.current?.querySelector('.ol-row.on')?.scrollIntoView({ block: 'nearest' })); } };
    if (k === 'j' || k === 'ArrowDown') return pick(rows[Math.min(rows.length - 1, i + 1)]?.t);
    if (k === 'k' || k === 'ArrowUp') return pick(rows[Math.max(0, i - 1)]?.t);
    if (!cur) return;
    if (k === 'h' || k === 'ArrowLeft') { if (cur.kids.length && !folded.value.has(cur.id)) { e.preventDefault(); return setFold(cur.id, true); } return pick(tree.byId[cur.parent_id]); }
    if (k === 'l' || k === 'ArrowRight') { if (cur.kids.length && folded.value.has(cur.id)) { e.preventDefault(); return setFold(cur.id, false); } return pick(cur.kids[0]); }
    if (k === 'Tab') {
      e.preventDefault();
      const sibs = cur.parent_id && tree.byId[cur.parent_id] ? tree.byId[cur.parent_id].kids : tree.roots;
      if (e.shiftKey) { if (cur.parent_id) move(cur.id, tree.byId[cur.parent_id]?.parent_id || null); return; }
      const above = sibs[sibs.indexOf(cur) - 1];
      if (above) move(cur.id, above.id, threadName(above)); else toast('上面没有同级的讨论可以挂');
      return;
    }
    if (k === 'e' || k === 'F2') { e.preventDefault(); return setRenaming(cur.id); }
    if (k === 'z') { e.preventDefault(); zoom.value = cur.id; return; }
    if (k === 'Enter') { e.preventDefault(); document.querySelector('#detail .chat-input textarea')?.focus(); return; }
    if (k === 'b') {
      e.preventDefault();
      post('/api/threads', { scope: cur.scope, parent_id: cur.id }).then((t) => { onSelect(t.id); refresh(); setTimeout(() => document.querySelector('#detail .chat-input textarea')?.focus(), 80); }).catch(fail);
    }
  };

  return html`<div class="outline" tabIndex="0" ref=${box} onKeyDown=${key}>
    <div class="list-head"><h2>讨论</h2><span class="sub">${list.length ? `${list.length} 段` : ''}</span>
      <span class="pane-switch"><div class="seg sm"><button class=${pane.value === 'river' ? 'on' : ''} onClick=${() => (pane.value = 'river')}>河流</button>
        <button class=${pane.value === 'map' ? 'on' : ''} onClick=${() => (pane.value = 'map')}>地图</button></div></span></div>
    <div class="ol-tools">
      <${JotForm} scope="library" placeholder=${agentOn.value ? '问 agent…（回车，开一段新讨论）' : '记一个想法…（回车，开一段新讨论）'} onDone=${(t) => onSelect(t.id)} />
      <div class="scope-bar"><span class="hint">新讨论</span>
        <button class="scope" onClick=${() => post('/api/threads', { scope: 'library' }).then((t) => onSelect(t.id)).catch(fail)}>整个论文库</button>
        <select class="scope" onChange=${(e) => { const s = e.target.value; e.target.value = ''; if (s) post('/api/threads', { scope: s }).then((t) => onSelect(t.id)).catch(fail); }}>
          <option value="">按主题…</option>${topics.map((t) => html`<option value=${`topic:${t.path}`}>${t.path.includes('/') ? '　' : ''}${t.name}</option>`)}</select>
        <input class="ol-filter" type="search" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder="过滤…" /></div>
    </div>
    ${focus ? html`<div class="crumbs ol-crumbs"><a href="#" onClick=${(e) => { e.preventDefault(); zoom.value = null; }}>全部</a>${crumbs.map((c) => html`<span>›</span><a href="#" onClick=${(e) => { e.preventDefault(); zoom.value = c.id; }}>${short(threadName(c), 18)}</a>`)}</div>` : null}
    ${drag ? html`<div class=${`ol-root-drop ${drag.over === 'root' ? 'drop' : ''}`} onDragOver=${(e) => { e.preventDefault(); if (drag.over !== 'root') setDrag({ ...drag, over: 'root' }); }}
      onDrop=${(e) => { e.preventDefault(); const id = drag.id; setDrag(null); if (tree.byId[id]?.parent_id) move(id, null); }}>拖到这里：放回顶层</div>` : null}
    ${rows.length ? rows.map(({ t, depth }) => html`<${Row} key=${t.id} t=${t} depth=${depth} sel=${t.id === selId} onSelect=${onSelect} tree=${tree}
        renaming=${renaming} setRenaming=${setRenaming} drag=${drag} setDrag=${setDrag} />`)
      : html`<div class="empty"><b>${words.length ? '没有匹配的讨论' : '还没有讨论'}</b>${words.length ? '' : html`随手记一个想法、在论文或研究上点“讨论”，都会在这里开一段讨论。<br />在任意一条消息上“从这里分叉”，它就长成一棵树。`}</div>`}
    <div class="ol-keys hint">j k 上下 · h l 折叠、展开或去上下级 · Tab 挂到上面那段下面 · ⇧Tab 提一级 · e 改名 · b 分叉 · z 放大 · 拖拽换位置</div>
  </div>`;
}

// ---------- the map: questions and conclusions the agent distilled. Threads are sources, not nodes. ----------

const CW = 244, CH = 70, DX = 360, DY = 96;

function layoutMap(map) {
  const shown = (map.nodes || []).filter((n) => !n.hidden);
  const byId = Object.fromEntries(shown.map((n) => [n.id, n]));
  const groups = [...(map.groups || [])];
  const have = new Set(groups.map((g) => g.id));
  for (const n of shown) if (!have.has(n.group)) { have.add(n.group); groups.push({ id: n.group, name: n.group }); }
  const pos = {}, regions = [];
  let cursor = 16, bottom = 80;
  for (const g of groups) {
    const nodes = shown.filter((n) => n.group === g.id);
    if (!nodes.length) continue;
    const local = {};
    let row = 0;
    const kidsOf = (id) => nodes.filter((n) => n.parent === id && byId[n.parent]?.group === g.id).sort((a, b) => (a.id < b.id ? -1 : 1));
    const roots = nodes.filter((n) => !(n.parent && byId[n.parent]?.group === g.id)).sort((a, b) => (a.id < b.id ? -1 : 1));
    const seen = new Set();
    const place = (n, d) => {
      if (seen.has(n.id)) return;
      seen.add(n.id);
      const kids = kidsOf(n.id).filter((c) => !seen.has(c.id));
      if (kids.length) {
        kids.forEach((c) => place(c, d + 1));
        local[n.id] = { x: d * DX, y: (local[kids[0].id].y + local[kids[kids.length - 1].id].y) / 2 };
      } else local[n.id] = { x: d * DX, y: row++ * DY };
    };
    (roots.length ? roots : nodes).forEach((n) => { place(n, 0); row += 0.3; });
    nodes.filter((n) => !seen.has(n.id)).forEach((n) => { local[n.id] = { x: 0, y: row++ * DY }; });
    const boxes = Object.values(local);
    const w = Math.max(...boxes.map((p) => p.x), 0) + CW, h = Math.max(...boxes.map((p) => p.y), 0) + CH;
    for (const n of nodes) pos[n.id] = { x: cursor + local[n.id].x, y: 36 + local[n.id].y };
    regions.push({ id: g.id, name: g.name, x: cursor - 12, y: 8, w: w + 24, h: h + 44 });
    bottom = Math.max(bottom, h + 60);
    cursor += w + 64;
  }
  return { pos, regions, w: Math.max(cursor, 1), h: bottom };
}

function edgeEnds(pos, a, b) {
  const A = pos[a], B = pos[b];
  if (!A || !B) return null;
  const right = A.x + CW <= B.x, left = B.x + CW <= A.x;
  let x1, y1, x2, y2;
  if (right) [x1, y1, x2, y2] = [A.x + CW, A.y + CH / 2, B.x, B.y + CH / 2];
  else if (left) [x1, y1, x2, y2] = [A.x, A.y + CH / 2, B.x + CW, B.y + CH / 2];
  else if (A.y < B.y) [x1, y1, x2, y2] = [A.x + CW / 2, A.y + CH, B.x + CW / 2, B.y];
  else [x1, y1, x2, y2] = [A.x + CW / 2, A.y, B.x + CW / 2, B.y + CH];
  const dx = x2 - x1, dy = y2 - y1;
  const d = Math.abs(dx) >= Math.abs(dy)
    ? `M${x1},${y1} C${x1 + dx / 2},${y1} ${x2 - dx / 2},${y2} ${x2},${y2}`
    : `M${x1},${y1} C${x1},${y1 + dy / 2} ${x2},${y2 - dy / 2} ${x2},${y2}`;
  return { d, x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
}

function MapCanvas({ onOpen, threads }) {
  const [map, , err] = useLive('/api/map');
  const [open, setOpen] = useState(() => new Set());
  const spaceThreads = (threads || []).filter((t) => t.space);
  const kept = (map?.nodes || []).filter((n) => !n.hidden);
  const all = Object.fromEntries(kept.map((n) => [n.id, n]));
  const kidsOf = {};
  for (const n of kept) if (all[n.parent]) (kidsOf[n.parent] = kidsOf[n.parent] || []).push(n);
  // the trunk shows: each root and what hangs right under it; deeper levels wait until their parent is opened
  const isRoot = (n) => !all[n.parent];
  const visible = (n) => isRoot(n) || (visible(all[n.parent]) && (isRoot(all[n.parent]) || open.has(n.parent)));
  const shown = kept.filter(visible);
  const lay = useMemo(() => layoutMap({ ...(map || { groups: [] }), nodes: shown }), [map, open]);
  const hidden = (map?.nodes || []).filter((n) => n.hidden);
  const toggle = (id) => setOpen((s) => { const t = new Set(s); t.has(id) ? t.delete(id) : t.add(id); return t; });
  const byId = Object.fromEntries(shown.map((n) => [n.id, n]));
  const box = useRef();
  const [view, setView] = useState({ x: 24, y: 24, k: 1 });
  const [sel, setSel] = useState(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const fitted = useRef(false);
  const pan = useRef(null);
  const node = byId[sel];
  const fit = () => {
    const el = box.current;
    if (!el) return;
    const k = Math.min((el.clientHeight - 48) / lay.h, 1);
    const fits = (span, room) => span * k + 48 <= room;
    setView({ k, x: fits(lay.w, el.clientWidth) ? (el.clientWidth - lay.w * k) / 2 : 16, y: fits(lay.h, el.clientHeight) ? (el.clientHeight - lay.h * k) / 2 : 16 });
  };
  useEffect(() => { if (!fitted.current && shown.length) { fitted.current = true; fit(); } }, [shown.length]);
  const zoomBy = (f, cx, cy) => setView((v) => {
    const k = Math.min(2.5, Math.max(0.25, v.k * f));
    return { k, x: cx - ((cx - v.x) / v.k) * k, y: cy - ((cy - v.y) / v.k) * k };
  });
  const onPath = new Set();
  for (let u = sel; u && byId[u]; u = byId[u].parent) { if (onPath.has(u)) break; onPath.add(u); }
  const curves = [];
  const push = (key, from, to, label, contra, hot) => {
    const e = edgeEnds(lay.pos, from, to);
    if (e) curves.push({ key, ...e, label, contra, hot, w: label.length * 12 + 10 });
  };
  for (const n of shown) if (n.parent && n.rel && lay.pos[n.parent]) {
    const label = n.rel === '回答' && n.partial ? '回答·部分' : n.rel;
    if (n.rel === '回答') push(`s-${n.id}`, n.id, n.parent, label, false, onPath.has(n.id) && onPath.has(n.parent));
    else push(`s-${n.id}`, n.parent, n.id, label, false, onPath.has(n.id) && onPath.has(n.parent));
  }
  for (const e of map?.edges || []) if (lay.pos[e.src] && lay.pos[e.dst]) push(`c-${e.src}-${e.dst}`, e.src, e.dst, e.rel, e.rel === '对照', onPath.has(e.src) && onPath.has(e.dst));
  const pick = (id) => { setSel(id); setEditing(false); setDraft(byId[id]?.text || ''); };
  const save = async (e) => {
    e.preventDefault();
    if (!node || draft.trim() === node.text) { setEditing(false); return; }
    try { await post(`/api/map/nodes/${enc(node.id)}`, { text: draft }); setEditing(false); refresh(); } catch (err) { fail(err); }
  };
  const hide = async () => {
    try { await post(`/api/map/nodes/${enc(node.id)}`, { hidden: true }); setSel(null); refresh(); } catch (err) { fail(err); }
  };
  const restore = async (id) => { try { await post(`/api/map/nodes/${enc(id)}`, { hidden: false }); refresh(); } catch (err) { fail(err); } };
  const down = (e) => {
    if (e.target.closest('.map-card, .map-bar, .delta-tools, .map-hidden')) return;
    pan.current = { sx: e.clientX, sy: e.clientY, x: view.x, y: view.y };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* gone */ }
  };
  if (err) return html`<div class="delta"><div class="empty"><b>地图没载入</b><p class="hint">${err}</p></div></div>`;
  if (!map) return html`<div class="delta"><div class="empty"><span class="spin"></span></div></div>`;
  return html`<div class="delta" ref=${box}
      onPointerDown=${down}
      onPointerMove=${(e) => { const p = pan.current; if (p) setView((v) => ({ ...v, x: p.x + e.clientX - p.sx, y: p.y + e.clientY - p.sy })); }}
      onPointerUp=${() => { pan.current = null; }} onPointerCancel=${() => { pan.current = null; }}
      onClick=${(e) => { if (!e.target.closest('.map-card, .map-bar, .delta-tools, .map-hidden')) setSel(null); }}
      onWheel=${(e) => { e.preventDefault(); const r = box.current.getBoundingClientRect(); zoomBy(Math.exp(-e.deltaY * 0.0012), e.clientX - r.left, e.clientY - r.top); }}>
    <svg width="100%" height="100%">
      <defs><marker id="map-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" /></marker></defs>
      <g transform=${`translate(${view.x},${view.y}) scale(${view.k})`}>
        ${lay.regions.map((g) => html`<g key=${g.id}><rect class="map-region" x=${g.x} y=${g.y} width=${g.w} height=${g.h} rx="12" />
          <text class="map-region-name" x=${g.x + 14} y=${g.y + 20}>${g.name}</text></g>`)}
        ${curves.map((c) => html`<g key=${c.key}><path d=${c.d} class=${`map-edge ${c.contra ? 'contra' : ''} ${c.hot ? 'on' : ''}`} marker-end=${c.contra ? 'none' : 'url(#map-arrow)'} />
          <rect class="map-tag-bg" x=${c.x - c.w / 2} y=${c.y - 9} width=${c.w} height="18" rx="4" />
          <text class="map-tag" x=${c.x} y=${c.y + 4} text-anchor="middle">${c.label}</text></g>`)}
      </g></svg>
    <div class="map-layer" style=${`transform:translate(${view.x}px,${view.y}px) scale(${view.k})`}>
      ${shown.map((n) => {
        const deeper = !isRoot(n) && kidsOf[n.id]?.length;
        return html`<button key=${n.id} class=${`map-card ${n.kind}${isRoot(n) ? ' root' : ''}${n.id === sel ? ' sel' : ''}${onPath.has(n.id) ? ' on' : ''}`}
          style=${`left:${lay.pos[n.id].x}px;top:${lay.pos[n.id].y}px`} onClick=${(e) => { e.stopPropagation(); pick(n.id); if (deeper) toggle(n.id); }}>
        <span class="k">${n.kind === 'question' ? '问' : '结'}</span>
        <span class="t">${n.text}</span>
        ${deeper ? html`<span class="n more">${open.has(n.id) ? '收起' : `展开 ${kidsOf[n.id].length}`}</span>`
          : n.sources.length > 1 ? html`<span class="n">${n.sources.length} 段</span>` : null}
        ${n.text_by === 'user' ? html`<span class="mine" title="你改过，agent 不再改这句">改</span>` : null}
      </button>`;
      })}
    </div>
    ${!shown.length ? html`<div class="empty"><b>${hidden.length ? '节点都藏起来了' : '地图还空着'}</b><p class="hint">${hidden.length ? '右上角可以恢复。' : '能收成问题或结论的讨论，agent 会整理到这里。找论文、改分类这种不会出现。'}</p></div>` : null}
    ${node ? html`<div class="map-bar">
      ${editing ? html`<form onSubmit=${save}><textarea rows="2" value=${draft} onInput=${(e) => setDraft(e.target.value)} />
        <button class="btn sm" type="submit">保存</button><button class="btn sm ghost" type="button" onClick=${() => setEditing(false)}>取消</button></form>`
        : html`<p>${node.text}</p><button class="btn sm ghost" onClick=${() => { setDraft(node.text); setEditing(true); }}>改一句</button>`}
      <button class="btn sm ghost" onClick=${hide}>藏起</button>
      <span class="srcs">${node.sources.map((s) => html`<button key=${s.id} class="btn sm" onClick=${() => onOpen(s.id)}>${short(s.title, 28)}</button>`)}</span>
    </div>` : null}
    <div class="delta-tools">
      ${spaceThreads.length ? html`<details class="map-hidden"><summary>空间里你的讨论 ${spaceThreads.length}</summary>
        ${spaceThreads.map((t) => html`<button key=${t.id} title=${`共享 · ${t.space.name}；这里只读`} onClick=${() => onOpen(t.id)}>${short(threadName(t), 22)}</button>`)}</details>` : null}
      ${hidden.length ? html`<details class="map-hidden"><summary>藏起 ${hidden.length}</summary>
        ${hidden.map((n) => html`<button key=${n.id} onClick=${() => restore(n.id)}>${short(n.text, 22)}</button>`)}</details>` : null}
      <button class="btn sm" onClick=${() => zoomBy(1.3, box.current.clientWidth / 2, box.current.clientHeight / 2)}>＋</button>
      <button class="btn sm" onClick=${() => zoomBy(0.77, box.current.clientWidth / 2, box.current.clientHeight / 2)}>－</button>
      <button class="btn sm" onClick=${fit} title="适应屏幕">⊡</button></div>
    <div class="delta-hint hint">滚轮缩放 · 拖空白平移 · 点节点看出处 · 线上的字是这两个说法的关系</div>
  </div>`;
}

// ---------- the view ----------

export function DiscussView({ threadId }) {
  const [list] = useLive('/api/threads/recent?shared=1');
  const sel = threadId || null;
  const select = (id) => { pane.value = 'river'; go('discuss', id); }; // space threads read through at home; entering the space is a deliberate choice there
  useEffect(() => {
    const want = takeSwitchIntent('discuss');
    if (want) { go('discuss', want.id); return; }
    if (!sel && list?.length && innerWidth > 900) go('discuss', buildTree(list).roots[0]?.id);
  }, [list?.length, sel]);
  const back = () => { pane.value = 'river'; go('discuss'); };
  return html`<div id="body" class=${sel || pane.value === 'map' ? 'show-detail' : ''}>
    <section id="list">${list ? html`<${Outline} list=${list} selId=${sel} onSelect=${select} />` : html`<div class="empty"><span class="spin"></span></div>`}</section>
    <section id="detail">
      ${pane.value === 'map' ? html`<button class="btn sm ghost m-back" onClick=${back}>← 返回列表</button><${MapCanvas} onOpen=${select} threads=${list} />`
        : sel ? html`<div class="detail thread"><div class="chatbox"><${RiverLoader} key=${sel} id=${sel} /></div></div>`
          : html`<div class="empty" style="padding-top:120px">选择左侧一段讨论</div>`}
    </section></div>`;
}
