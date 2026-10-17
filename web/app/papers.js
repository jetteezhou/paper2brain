// The paper side: inbox (captures), papers by topic, search, the capture detail and the paper card.
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import {
  html, get, post, del, enc, ago, short, firstLine, idLabel, channel, authors, live, href, plain, mdHtml, citeHtml, readBase64,
} from './lib.js';
import {
  appState, agentOn, selected, useLive, refresh, toast, fail, go, openReader, openThread, openPaper, openResearch, openCapture,
} from './state.js';
import { ThreadLines, JotForm, scopeLabel } from './discuss.js';
import { researchPicker, openComposer, ResearchChip, StageChip } from './research.js';
import { useAnchorMarks, openFigure } from './reader.js';

const VIEWS = {
  inbox: { title: '收件箱', empty: ['收件箱是空的', '在页面任何地方 ⌘V 粘贴公众号、小红书或 arXiv 链接、截图或 PDF，回车就收下。按 ⌘K 输入一句话，可以记成想法、找论文或开始研究。'] },
  deep: { title: '稍后深读', empty: ['还没有稍后深读的', '在收件箱里按 D，或在卡片上点“稍后深读”：agent 会先把深读报告写好。'] },
  archived: { title: '已存档', empty: ['还没有存档', '看过卡片、暂时不深读的，按 A 存档。'] },
  ignored: { title: '已忽略', empty: ['没有忽略的内容', '忽略只是移出收件箱，不代表不感兴趣。'] },
  papers: { title: '论文', empty: ['库里还没有论文', '收下的内容关联到论文后会出现在这里。'] },
  topic: { empty: ['这个主题下还没有论文', 'agent 会自动归类；你也可以在论文卡片上手动调整主题。'] },
  unsorted: { title: '未归类', empty: ['都归好类了', 'agent 拿不准的论文会留在这里。'] },
};
const INBOX_FILTERS = [['inbox', '收件箱'], ['deep', '稍后深读'], ['archived', '已存档'], ['ignored', '已忽略']];
const NEEDS_YOU = ['needs_confirm', 'unmapped', 'failed'];
const wide = () => innerWidth > 900;
const Html = ({ s, tag = 'span', cls }) => html`<${tag} class=${cls} dangerouslySetInnerHTML=${{ __html: s }} />`;
const inlineMd = (s) => mdHtml(s).replace(/^<p>|<\/p>$/g, '');

export const PaperChip = ({ id, title }) => html`<a href="#" class="chip paper-chip" title="打开论文卡片" onClick=${(e) => { e.preventDefault(); e.stopPropagation(); openPaper(id); }}>论文 · ${short(title || id, 26)}</a>`;

// ---------- list + detail layout and selection ----------

export function ListDetail({ list, detail, hasSel }) {
  return html`<div id="body" class=${hasSel ? 'show-detail' : ''}><section id="list">${list}</section><section id="detail">
    ${hasSel && !wide() ? html`<button class="btn sm ghost" onClick=${() => (selected.value = null)}>← 返回列表</button>` : null}${detail}</section></div>`;
}

/** The selected item of this kind, auto-selecting the first one on wide screens. */
function useSel(kind, items) {
  const s = selected.value;
  const id = s?.kind === kind ? s.id : null;
  useEffect(() => {
    if (items && items.length && (!id || !items.some((x) => x.id === id)) && wide()) selected.value = { kind, id: items[0].id };
  }, [items, id]);
  return id;
}

function ListHead({ title, sub, keys }) {
  return html`<div class="list-head"><h2>${title}</h2><span class="sub">${sub}</span>${keys ? html`<span class="keys hint">${keys}</span>` : null}</div>`;
}
const Empty = ({ e }) => html`<div class="empty"><b>${e[0]}</b>${e[1]}</div>`;

// ---------- jumps: from a title card straight to the part of the paper the reader is after ----------

export const reportState = (p) => (p.report ? 'done' : p.runs?.some((r) => r.kind === 'deepread' && live(r.status)) ? 'running' : p.report_status || null);
const KEY_LABEL = { method: '方法', experiments: '实验', conclusion: '结论' };
const Jump = ({ pid, label, intent, cls = '', title = '' }) =>
  html`<button class=${`jump ${cls}`} title=${title} onClick=${(e) => { e.preventDefault(); e.stopPropagation(); openReader(pid, intent); }}>${label}</button>`;

function Jumps({ p, j, full }) {
  const state = reportState(p), pid = p.id;
  const report = state && html`<${Jump} pid=${pid} label=${state === 'done' ? '深读报告 →' : html`<span class="spin"></span>报告生成中`} intent=${{ tab: 'report' }} cls=${state === 'done' ? 'go' : 'run'} />`;
  if (!j) return state ? html`<div class="jumps">${report}</div>` : null;
  const parts = [];
  if (state === 'done' && full && j.report.length) {
    parts.push(html`<span class="jumps-label">深读报告</span>${j.report.map((h) => html`<${Jump} pid=${pid} label=${short(h.replace(/^你关心的[:：]/, '关心：'), 14)} intent=${{ tab: 'report', section: h }} cls="go" title=${h} />`)}`);
  } else if (state) parts.push(report);
  const secs = Object.entries(KEY_LABEL).filter(([k]) => j.sections?.[k]);
  if (secs.length) parts.push(html`<span class="jumps-label">原文</span>${secs.map(([k, label]) => html`<${Jump} pid=${pid} label=${html`${label} <small>p.${j.sections[k].page}</small>`}
    intent=${{ tab: 'card', page: j.sections[k].page, q: j.sections[k].title }} title=${j.sections[k].title} />`)}`);
  if (j.figures) parts.push(html`<button class="jump" title=${full ? '浏览全部插图' : '看方法 / 架构图'} onClick=${(e) => { e.stopPropagation(); openFigure(pid, 0, full); }}>${full ? `全部插图 ${j.figures}` : '架构图'}</button>`);
  if (j.anchors) parts.push(html`<${Jump} pid=${pid} label=${`划词 ${j.anchors}`} intent=${{ tab: 'anchors' }} title="你在原文、快读卡、报告里划过的段落" />`);
  if (j.chat) parts.push(html`<${Jump} pid=${pid} label=${`讨论 ${j.chat}`} intent=${{ tab: 'chat' }} title="接着上次的讨论" />`);
  return parts.length ? html`<div class=${`jumps ${full ? 'full' : ''}`}>${parts}</div>` : null;
}
const fullJumps = (p) => ({ report: (p.report?.sections || []).map((s) => s.heading), sections: p.key_sections, figures: (p.figures || []).length, anchors: (p.anchors || []).length });

// ---------- topics ----------

const openPicker = signal(null);
document.addEventListener('click', (e) => { if (openPicker.value && !e.target.closest('[data-stop]')) openPicker.value = null; });

/** Topic chips with a picker. kind 'paper' | 'research'; owner has id, topics (and topics_locked for papers). */
export function TopicsRow({ kind = 'paper', owner, onSaved }) {
  const key = `${kind}:${owner.id}`, open = openPicker.value === key;
  const save = async (topics) => {
    try { await post(`/api/${kind === 'research' ? 'research' : 'papers'}/${enc(owner.id)}/topics`, { topics }); refresh(); onSaved?.(); } catch (e) { fail(e); }
  };
  const toggle = (path, on) => { const s = new Set(owner.topics); on ? s.add(path) : s.delete(path); save([...s]); };
  const box = (t, cls) => html`<label class=${cls}><input type="checkbox" checked=${owner.topics.includes(t.path)} onChange=${(e) => toggle(t.path, e.target.checked)} />${t.name}</label>`;
  const hint = kind === 'research' ? null : owner.topics_locked ? html`<span class="hint">· 你调整过</span>` : owner.topics.length ? html`<span class="hint">· agent 归类</span>` : null;
  return html`<div class="topics-row" data-stop>
    ${owner.topics.length ? owner.topics.map((t) => html`<span class="chip topic">${t}<button title="移除" onClick=${(e) => { e.stopPropagation(); save(owner.topics.filter((x) => x !== t)); }}>×</button></span>`) : html`<span class="chip">未归类</span>`}
    <button class="btn sm ghost" onClick=${(e) => { e.stopPropagation(); openPicker.value = open ? null : key; }}>${owner.topics.length ? '调整' : '+ 归类'}</button>${hint}
    ${open ? html`<div class="picker" data-stop>${(appState.value?.topics || []).map((t) => html`${box(t)}${t.children.map((ch) => box(ch, 'child'))}`)}
      ${kind === 'paper' ? html`<div class="foot">你调整后，agent 不会再改这篇论文的主题。</div>` : null}</div>` : null}</div>`;
}

// ---------- the paper card ----------

const figSrc = (pid, n) => `/api/papers/${enc(pid)}/figures/${n}`;
const figText = (caption) => caption.replace(/^\s*(?:Figure|Fig\.)\s*\d+\s*[:.|]\s*/i, '');

function Figures({ p }) {
  const figs = p.key_figures || [], track = useRef();
  const [i, setI] = useState(0);
  if (!figs.length) return null;
  const more = (p.figures || []).length - figs.length;
  const step = (d) => track.current.scrollBy({ left: d * track.current.clientWidth, behavior: 'smooth' });
  return html`<div class="figs"><div class="figs-track" ref=${track} onScroll=${(e) => setI(Math.round(e.target.scrollLeft / e.target.clientWidth))}>
      ${figs.map((f) => html`<figure class="fig"><button class="fig-img" title="放大看" onClick=${(e) => { e.stopPropagation(); openFigure(p.id, f.n); }}><img loading="lazy" src=${figSrc(p.id, f.n)} alt=${`图 ${f.n}`} /></button>
        <figcaption>${f.note ? html`<b>${f.note}</b>　` : null}<span class="hint">图 ${f.n} · p.${f.page}</span>　${short(figText(f.caption), 150)}</figcaption></figure>`)}</div>
    ${figs.length > 1 ? html`<button class="figs-nav prev" aria-label="上一张" onClick=${(e) => { e.stopPropagation(); step(-1); }}>‹</button><button class="figs-nav next" aria-label="下一张" onClick=${(e) => { e.stopPropagation(); step(1); }}>›</button>` : null}
    <div class="figs-foot"><span class="figs-count">${figs.length > 1 ? `${i + 1} / ${figs.length}` : ''}</span><span class="grow"></span>
      ${more > 0 ? html`<a href="#" onClick=${(e) => { e.preventDefault(); openFigure(p.id, figs[0].n, true); }}>全部 ${figs.length + more} 张图</a>` : null}</div></div>`;
}

export function PdfButton({ capture, paper }) {
  const [busy, setBusy] = useState(false);
  const up = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setBusy(true);
    try {
      const data = await readBase64(f);
      await post(capture ? `/api/captures/${enc(capture)}/pdf` : `/api/papers/${enc(paper)}/pdf`, { data });
      toast('PDF 已加入');
      refresh();
    } catch (err) { fail(err); }
    setBusy(false);
  };
  return html`<label class="btn sm" style="margin-top:8px">${busy ? html`<span class="spin"></span>读取 PDF…` : '上传 PDF'}<input type="file" accept="application/pdf" hidden onChange=${up} /></label>`;
}

function DeepButton({ p }) {
  const state = reportState(p);
  if (state === 'done') return html`<button class="btn primary" onClick=${() => openReader(p.id, { tab: 'report' })}>看深读报告 →</button>`;
  if (state === 'running') return html`<button class="btn primary" onClick=${() => openReader(p.id, { tab: 'report' })}><span class="spin"></span>报告生成中，进入深读 →</button>`;
  if (agentOn.value && p.page_count) {
    return html`<button class="btn primary" title="打开原文，agent 同时开始写深读报告" onClick=${async (e) => {
      e.target.disabled = true;
      try { await post(`/api/papers/${enc(p.id)}/deepread`); openReader(p.id, { tab: 'report' }); } catch (err) { fail(err); openReader(p.id, { tab: 'card' }); }
    }}>进入深读 →</button>`;
  }
  return html`<button class="btn primary" onClick=${() => openReader(p.id)}>进入深读 →</button>`;
}
export const deepToast = () => (agentOn.value ? '已加入稍后深读，agent 会先把深读报告写好' : '已加入稍后深读');

export function PaperCard({ paper: p, capture, inReader }) {
  const ref = useRef();
  useAnchorMarks(ref, p);
  const card = p.card;
  const deepRun = p.runs?.find((r) => live(r.status));
  const capRun = capture?.runs.find((r) => live(r.status));
  const deep = p.captures?.some((c) => c.triage === 'deep');
  let body;
  if (card) {
    const res = card.resources || {};
    const robot = res.real_robot === true ? '有' : res.real_robot === false ? '无' : '未知';
    const takes = (card.takes || []).map((t) => {
      const src = p.captures?.find((c) => c.id === t.capture_id);
      return html`<div class="take">${src ? html`<b>${src.author || channel(src)}</b>：` : null}${t.take}${t.consistent === false ? html` <span class="chip red">与原文不符</span>` : null}</div>`;
    });
    body = html`<div class="tldr">${card.tldr}</div><${Figures} p=${p} />
      <dl class="kv"><dt>问题</dt><dd><${Html} s=${inlineMd(card.problem)} /></dd><dt>方法</dt><dd><${Html} s=${inlineMd(card.method)} /></dd>
      <dt>结果</dt><dd><ul class="results">${card.results.map((r) => html`<li><${Html} s=${`${inlineMd(r.text)} ${citeHtml(p.id, r.page, r.quote)}`} /></li>`)}</ul></dd></dl>
      <div class="res-row">${res.code ? html`<a href=${href(res.code)} target="_blank" rel="noopener">代码 ↗</a>` : html`<span>代码：<b>未知</b></span>`}
        ${res.project ? html`<a href=${href(res.project)} target="_blank" rel="noopener">项目页 ↗</a>` : null}${res.data ? html`<a href=${href(res.data)} target="_blank" rel="noopener">数据 ↗</a>` : null}
        <span>真机实验：<b>${robot}</b></span>${res.platform ? html`<span>平台：<b>${res.platform}</b></span>` : null}</div>
      <div class="relevance"><b>和你的关系</b>　<${Html} s=${inlineMd(card.relevance)} /></div>
      ${takes.length ? html`<div class="takes"><div class="block-title" style="margin:10px 0 4px">解读观点<span class="line"></span></div>${takes}</div>` : null}`;
  } else {
    const wait = capRun || deepRun ? html`<div class="card-wait"><span class="spin"></span>Agent 正在写快读卡…</div>`
      : agentOn.value ? html`<div class="card-wait">还没有快读卡。${p.page_count ? html`<button class="btn sm" onClick=${async (e) => {
        e.target.disabled = true;
        try { await post(`/api/papers/${enc(p.id)}/card`); toast('已交给 agent 写快读卡'); refresh(); } catch (err) { fail(err); e.target.disabled = false; }
      }}>让 agent 写快读卡</button>` : null}</div>`
        : html`<div class="card-wait">快读卡由 agent 生成（未连接）。先看摘要：</div>`;
    body = html`${wait}${p.abstract ? html`<div class="abstract">${p.abstract}</div>` : null}<${Figures} p=${p} />`;
  }
  const unlink = async (e) => {
    e.preventDefault();
    if (!confirm('这条捕获不是讲这篇论文？之后 agent 也不会再把它们关联起来。')) return;
    await post(`/api/captures/${enc(capture.id)}/unlink`, { paper: p.id }).catch(fail);
    refresh();
  };
  const toggleDeep = async () => {
    await post(`/api/papers/${enc(p.id)}/triage`, { triage: deep ? 'inbox' : 'deep' }).catch(fail);
    toast(deep ? '已移回收件箱' : deepToast());
    refresh();
  };
  return html`<article class="paper" data-anchor-surface="card" data-pid=${p.id} ref=${ref}>
    ${inReader ? null : html`<h1 class="paper-title">${p.title}</h1>`}
    <div class="paper-meta">${authors(p.authors)}${p.published ? ' · ' + p.published : ''} · ${p.url ? html`<a href=${href(p.url)} target="_blank" rel="noopener">${idLabel(p.id)} ↗</a>` : idLabel(p.id)}
      ${p.page_count ? ` · ${p.page_count} 页` : html` · <span style="color:var(--amber)">没有全文</span>`}
      ${capture && p.captures?.length > 1 ? html` · <a href="#" onClick=${(e) => { e.preventDefault(); openPaper(p.id); }}>共 ${p.captures.length} 个来源</a>` : null}
      ${capture ? html` · <a href="#" title="这条捕获不是讲这篇" onClick=${unlink}>不是这篇？</a>` : null}</div>
    <${TopicsRow} owner=${p} />
    <${Jumps} p=${p} j=${fullJumps(p)} full />
    ${p.page_count ? null : html`<div class="notice" style="margin-top:12px">没下载到这篇论文的 PDF（可能需要登录或不公开）。上传 PDF 后才能写快读卡、深读和对照原文。<br /><${PdfButton} paper=${p.id} /></div>`}
    ${body}
    <div class="research-row"><span class="hint">研究</span>${(p.research || []).length ? p.research.map((x) => html`<${ResearchChip} x=${x} />`) : html`<span class="hint">还没有用到这篇</span>`}
      <button class="btn sm ghost" onClick=${() => researchPicker({ heading: '把这篇论文加入研究', links: [{ kind: 'paper', ref: p.id }] })}>加入研究</button></div>
    ${inReader ? null : html`<div class="paper-actions">
      <${DeepButton} p=${p} />
      <button class="btn" onClick=${() => openReader(p.id, { tab: 'chat' })}>讨论这篇</button>
      <button class="btn" onClick=${() => openComposer({ links: [{ kind: 'paper', ref: p.id, label: p.title }] })}>开始研究</button>
      <button class="btn" title=${deep ? '移回收件箱' : 'agent 会先把深读报告写好，你有空时直接看'} onClick=${toggleDeep}>${deep ? '✓ 已在稍后深读' : '稍后深读'}</button></div>`}
  </article>`;
}

/** Discussions of the paper and of its captures, notes, and discussions elsewhere that cite it. */
export function PaperNotes({ p, flush }) {
  return html`<div class="block" style=${flush ? 'margin-top:0' : ''}><div class="block-title">讨论与笔记<span class="line"></span></div>
      <${ThreadLines} threads=${p.threads} paperId=${p.id} />
      ${p.notes.map((n) => html`<div class="run" style="margin-top:6px"><${Html} tag="div" s=${mdHtml(n.text)} cls="md" /><div class="hint">${n.by === 'agent' ? 'agent 记录的讨论结论' : '笔记'} · ${ago(n.created_at)}</div></div>`)}
      <${JotForm} scope=${`paper:${p.id}`} placeholder="记一个想法：开一段关于这篇的讨论（不叫 agent 回答）" /></div>
    ${p.cited_in?.length ? html`<div class="block"><div class="block-title">引用了这篇的讨论 · ${p.cited_in.length}<span class="line"></span></div>
      ${p.cited_in.map((t) => html`<div class="source" onClick=${() => openThread(t.scope, t.id)}><span class="source-kind">${scopeLabel(t.scope, t.scope_title)}</span>
        <span class="source-label">${t.title || t.first || '一段讨论'}</span></div>`)}</div>` : null}`;
}

function PaperDetail({ pid }) {
  const [p, , err] = useLive(`/api/papers/${enc(pid)}`);
  if (err) return html`<div class="empty"><b>打不开</b>${err}</div>`;
  if (!p) return html`<div class="empty"><span class="spin"></span></div>`;
  return html`<div class="detail"><${PaperCard} paper=${p} />
    ${p.captures.length ? html`<div class="block"><div class="block-title">来源 · ${p.captures.length}<span class="line"></span></div>
      ${p.captures.map((c) => html`<div class="item" style="padding:9px 0;border:0" onClick=${() => openCapture(c.id)}>
        <div class="item-meta"><span class="src">${channel(c)}${c.author ? ' · ' + c.author : ''}</span><time>${ago(c.created_at)}</time></div>
        <div>${c.title || short(firstLine(c.raw), 80)}</div></div>`)}</div>` : null}
    <${PaperNotes} p=${p} /></div>`;
}

// ---------- captures ----------

function StatusChip({ c }) {
  if (c.status === 'saved' || c.status === 'resolving') return html`<span class="chip"><span class="spin"></span>正在找原论文</span>`;
  if (live(c.run_status)) return html`<span class="chip violet"><span class="spin"></span>Agent 整理中</span>`;
  if (c.status === 'needs_confirm') return html`<span class="chip amber">待确认论文</span>`;
  if (c.status === 'needs_agent') return html`<span class="chip amber">截图待识别</span>`;
  if (c.status === 'unmapped') return html`<span class="chip">没找到论文</span>`;
  if (c.status === 'failed') return html`<span class="chip red">预处理失败</span>`;
  if (c.run_status === 'ERROR') return html`<span class="chip red">Agent 出错</span>`;
  return null;
}

async function triage(id, t, list, view) {
  const prev = list?.find((x) => x.id === id)?.triage;
  try { await post(`/api/captures/${enc(id)}/triage`, { triage: t }); } catch (e) { fail(e); return; }
  const label = { deep: deepToast(), archived: '已存档', ignored: '已忽略', inbox: '已移回收件箱' }[t];
  toast(label, prev && prev !== t ? '撤销' : null, async () => { await post(`/api/captures/${enc(id)}/triage`, { triage: prev }); refresh(); });
  if (view && list && t !== view && selected.value?.id === id) {
    const i = list.findIndex((x) => x.id === id), next = list.filter((x) => x.id !== id)[Math.min(i, list.length - 2)];
    selected.value = next ? { kind: 'capture', id: next.id } : null;
  }
  refresh();
}

function CaptureItem({ c, on, view, list }) {
  const p = c.papers[0];
  const title = c.title || firstLine(c.raw) || (c.image_count ? `附件 ${c.image_count} 个` : c.thought ? '一个想法' : '');
  const topics = [...new Set(c.papers.flatMap((x) => x.topics))];
  const tb = (t, label, tip) => html`<button class="btn sm ghost" title=${tip || label} onClick=${(e) => { e.stopPropagation(); triage(c.id, t, list, view); }}>${label}</button>`;
  return html`<div class=${`item ${on ? 'on' : ''}`} data-id=${c.id} onClick=${() => (selected.value = { kind: 'capture', id: c.id })}>
    <div class="item-meta"><span class="src">${channel(c)}${c.author ? ' · ' + c.author : ''}</span><time>${ago(c.created_at)}</time></div>
    <div class="item-title">${short(title, 120)}</div>
    ${p ? html`<div class="item-paper"><span>${p.title}${c.papers.length > 1 ? html` <span class="hint">等 ${c.papers.length} 篇</span>` : null}</span></div>` : null}
    ${p?.tldr ? html`<div class="item-tldr">${p.tldr}</div>` : null}
    ${c.thought ? html`<div class="item-thought">${c.thought}</div>` : null}
    <div class="item-tags">${topics.map((t) => html`<span class="chip topic">${t}</span>`)}<${StatusChip} c=${c} /></div>
    ${p ? html`<${Jumps} p=${p} j=${p.jumps} />` : null}
    ${view === 'inbox' ? html`<div class="item-actions">${tb('deep', '稍后深读', '稍后深读 (D)：agent 先把报告写好')}${tb('archived', '存档', '存档 (A)')}${tb('ignored', '忽略', '忽略 (X)')}</div>`
      : view ? html`<div class="item-actions">${tb('inbox', '移回收件箱')}</div>` : null}
  </div>`;
}

function removalText(plan) {
  const lines = ['撤销这次收下？下面这些会被永久删除：', `· 这条收下的内容${plan.threads.length ? `，和它的 ${plan.threads.length} 段讨论` : ''}`];
  for (const p of plan.papers) {
    const parts = [p.card && '快读卡', p.report && '深读报告', p.anchor.length && `${p.anchor.length} 段划词`,
      p.thread.length && `${p.thread.length} 段讨论`, p.note.length && `${p.note.length} 条笔记`].filter(Boolean);
    lines.push(`· 论文《${short(p.title, 40)}》${parts.length ? `及其${parts.join('、')}` : ''}（只有这条收下讲它）`);
  }
  if (plan.used_in.length) lines.push(`· 上面的讨论会从研究《${plan.used_in.join('》《')}》的素材里移除`);
  if (plan.kept.length) lines.push('', '保留：', ...plan.kept.map((p) => `· 论文《${short(p.title, 40)}》：${p.why.join('；')}`));
  return lines.join('\n');
}

function RunStatus({ r }) {
  if (live(r.status)) return html`<span class="chip violet"><span class="spin"></span>${{ WAITING: '排队中', CREATING: '启动中' }[r.status] || '运行中'}</span>`;
  const done = { FINISHED: ['完成', 'chip green'], ERROR: ['出错', 'chip red'], CANCELLED: ['已取消', 'chip'], EXPIRED: ['已过期', 'chip'] }[r.status] || [r.status, 'chip'];
  return html`<span class=${done[1]}>${done[0]}</span>`;
}

export function RunCard({ r, label }) {
  const tools = r.tools || [];
  return html`<div class="run"><div class="run-head"><b>${label || r.kind}</b><${RunStatus} r=${r} /><span>${ago(r.created_at)}</span>${r.duration_ms ? html`<span>· ${Math.round(r.duration_ms / 1000)}s</span>` : null}<span class="grow"></span>
      ${live(r.status) ? html`<button class="btn sm ghost" onClick=${async () => { await post(`/api/runs/${enc(r.id)}/cancel`).catch(fail); refresh(); }}>停止</button>` : null}</div>
    ${tools.length ? html`<div class="tools">${tools.slice(-8).map((t) => html`<div>${t.status === 'running' ? '…' : '✓'} ${t.name} ${t.args || ''}</div>`)}</div>` : null}
    ${r.text ? html`<div style="margin-top:8px"><${Html} tag="div" cls="md" s=${mdHtml(r.text)} /></div>` : null}
    ${r.error ? html`<div class="hint" style="color:var(--red)">${r.error}</div>` : null}
    <details><summary class="hint" style="cursor:pointer">发给 agent 的提示词</summary><pre>${r.prompt}</pre></details></div>`;
}

function RunsBlock({ c }) {
  const label = { capture: '整理', chat: '讨论', deepread: '深读', find: '找论文', card: '快读卡' };
  const find = c.source === 'find';
  const re = async (agent) => {
    await post(`/api/captures/${enc(c.id)}/reprocess`, { agent }).catch(fail);
    toast(agent ? '已重新交给规则和 agent' : '正在重跑规则预处理');
    setTimeout(refresh, 600);
  };
  return html`<details class="runs" open=${c.runs.some((r) => live(r.status))}>
    <summary>处理记录 · ${c.runs.length ? `${c.runs.length} 次 agent 运行` : agentOn.value ? '还没有 agent 运行' : 'agent 未连接'} <span class="hint">（调优：改 ${find ? 'librarian/find.md' : 'librarian/capture.md 后重新整理'}）</span></summary>
    <div style=${`display:${find ? 'none' : 'flex'};gap:8px;margin-top:10px`}>
      <button class="btn sm" disabled=${!agentOn.value} onClick=${() => re(true)}>重新整理（规则 + agent）</button>
      <button class="btn sm ghost" onClick=${() => re(false)}>只重跑规则预处理</button>
      ${c.agent_id ? html`<a class="btn sm ghost" href=${`https://cursor.com/agents/${c.agent_id}`} target="_blank">在 Cursor 中查看 ↗</a>` : null}</div>
    ${c.runs.map((r) => html`<${RunCard} r=${r} label=${label[r.kind]} />`)}</details>`;
}

function ManualLink({ cid, placeholder }) {
  const [v, setV] = useState(''), [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!v.trim()) return;
    setBusy(true);
    try { await post(`/api/captures/${enc(cid)}/link`, { paper: v.trim() }); toast('已关联'); setV(''); refresh(); } catch (err) { fail(err); }
    setBusy(false);
  };
  return html`<form class="add-line" onSubmit=${submit}><input value=${v} disabled=${busy} onInput=${(e) => setV(e.target.value)} placeholder=${placeholder} /><button class="btn sm">关联</button></form>`;
}

function LinkButton({ cid, pid }) {
  const [busy, setBusy] = useState(false);
  return html`<button class="btn sm primary" disabled=${busy} onClick=${async () => {
    setBusy(true);
    try { await post(`/api/captures/${enc(cid)}/link`, { paper: pid }); toast('已关联'); refresh(); } catch (e) { fail(e); setBusy(false); }
  }}>${busy ? html`<span class="spin"></span>拉取全文…` : '就是这篇'}</button>`;
}

function CapturePaper({ pid, capture }) {
  const [p] = useLive(`/api/papers/${enc(pid)}`);
  return p ? html`<${PaperCard} paper=${p} capture=${capture} />` : html`<div class="card-wait"><span class="spin"></span></div>`;
}

function CaptureDetail({ id, view }) {
  const [c, , err] = useLive(`/api/captures/${enc(id)}`);
  if (err) return html`<div class="empty"><b>打不开</b>${err}</div>`;
  if (!c) return html`<div class="empty"><span class="spin"></span></div>`;
  const papers = c.papers.filter((p) => p.state === 'confirmed');
  const running = c.runs.find((r) => live(r.status));
  const tb = (t, label) => html`<button class=${`btn sm ${c.triage === t ? 'primary' : ''}`} onClick=${() => triage(c.id, c.triage === t ? 'inbox' : t, [c], view)}>${label}</button>`;
  const undo = async () => {
    const plan = await get(`/api/captures/${enc(c.id)}/removal`).catch(fail);
    if (!plan || !confirm(removalText(plan))) return;
    await del(`/api/captures/${enc(c.id)}`).catch(fail);
    toast('已撤销');
    selected.value = null;
    refresh();
  };
  const find = c.source === 'find', rawExtra = c.raw && c.raw.replace(/https?:\/\/\S+/g, '').trim();
  let notice = null;
  if (find) {
    notice = html`<div class="notice info"><h3>你让 agent 找的论文</h3><div class="find-ask">${c.raw}</div>
      <p class="hint">${running ? html`<span class="spin"></span> agent 正在检索、挑选、入库，挑中的论文会陆续出现在下面…` : papers.length ? `找到 ${papers.length} 篇，都已入库；每篇的入选理由在讨论里。` : 'agent 没有关联到论文，去讨论里看它的说明，或者换个说法再找。'}</p>
      <button class="btn primary sm" onClick=${() => openThread(`find:${c.id}`)}>${papers.length > 1 ? `一起讨论这 ${papers.length} 篇 →` : '去讨论 →'}</button></div>`;
  } else if (c.status === 'needs_confirm' && !papers.length) {
    notice = html`<div class="notice"><h3>这条解读讲的是哪篇论文？</h3>${(c.candidates || []).map((m) => html`
      <div class="cand"><div class="cand-body"><div class="cand-title">${m.title}</div>
      <div class="cand-meta">${authors(m.authors)} · ${m.published || ''} · <a href=${href(m.url)} target="_blank" rel="noopener">${idLabel(m.pid)}</a>${m.score ? ` · 标题相似度 ${Math.round(m.score * 100)}%` : ''}</div>
      <div class="cand-meta">${m.why || ''}</div></div><${LinkButton} cid=${c.id} pid=${m.pid} /></div>`)}
      <${ManualLink} cid=${c.id} placeholder="都不是？粘贴论文链接、DOI 或 PDF 链接，或把 PDF 拖进来" /></div>`;
  } else if (!papers.length && c.status !== 'saved' && c.status !== 'resolving') {
    const msg = { unmapped: '没找到对应的论文', failed: '规则预处理失败', needs_agent: '截图需要 agent 看图识别', mapped: '没有关联的论文' }[c.status] || '';
    notice = html`<div class="notice info"><h3>${msg}</h3>${c.note ? html`<div class="hint" style="white-space:pre-wrap">${c.note}</div>` : null}
      <p class="hint">${running ? 'Agent 正在处理…' : agentOn.value ? '可以让 agent 再看一次，或者直接告诉我是哪篇。' : '连接 agent 后可以自动识别；也可以直接告诉我是哪篇。'}</p>
      <${ManualLink} cid=${c.id} placeholder="粘贴论文链接、DOI 或 PDF 链接" /><${PdfButton} capture=${c.id} /></div>`;
  } else if (c.status === 'saved' || c.status === 'resolving') {
    notice = html`<div class="card-wait"><span class="spin"></span>正在找原论文：识别链接、抓取解读快照、下载论文全文…</div>`;
  }
  return html`<div class="detail">
    <div class="src-bar"><span class="chip">${channel(c)}</span>${c.author ? html`<span>${c.author}</span>` : null}<span>${ago(c.created_at)}</span>
      ${c.url ? html`<a href=${href(c.url)} target="_blank" rel="noopener">打开原文 ↗</a>` : null}<span class="grow"></span>
      ${tb('deep', '稍后深读')}${tb('archived', '存档')}${tb('ignored', '忽略')}
      <button class="btn sm ghost" title="删除这条捕获及由它产生的内容" onClick=${undo}>撤销收下</button></div>
    <div class="hint" style="margin-top:4px">当前在：${{ inbox: '收件箱', deep: '稍后深读', archived: '已存档', ignored: '已忽略' }[c.triage]}</div>
    ${c.title && !find ? html`<div class="cap-title">${c.title}</div>` : null}
    ${!find && (rawExtra || (!c.title && c.raw)) ? html`<${Html} tag="div" cls="raw" s=${inlineMd(c.raw)} />` : null}
    ${c.attachments.length ? html`<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">${c.attachments.map((a) => a.mime.startsWith('image/')
      ? html`<a href=${`/api/attachments/${a.id}`} target="_blank" class="thumb" style="width:96px;height:96px"><img src=${`/api/attachments/${a.id}`} /></a>`
      : html`<a href=${`/api/attachments/${a.id}`} target="_blank" class="chip">${a.filename.replace(/^\d+-/, '')} ↗</a>`)}</div>` : null}
    <div class="thoughts"><${ThreadLines} threads=${c.threads} paperId=${papers[0]?.id} />
      <${JotForm} scope=${`capture:${c.id}`} placeholder=${c.threads.length ? '再补一句…（记下就开一段讨论，不叫 agent 回答）' : '补一句：为什么收它？'} /></div>
    ${notice}
    ${papers.map((p) => html`<${CapturePaper} key=${p.id} pid=${p.id} capture=${c} />`)}
    <${RunsBlock} c=${c} /></div>`;
}

export function CapturesView({ view }) {
  const [list] = useLive(`/api/captures?view=${view}`);
  const sel = useSel('capture', list);
  const c = appState.value?.counts || {};
  useEffect(() => {
    const key = (e) => {
      const t = e.target instanceof Element ? e.target : document.body;
      if (t.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey || !list?.length || document.querySelector('.modal-bg')) return;
      const i = list.findIndex((x) => x.id === sel), k = e.key.toLowerCase();
      if (k === 'j' || e.key === 'ArrowDown' || k === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        const next = list[Math.max(0, Math.min(list.length - 1, i + (k === 'j' || e.key === 'ArrowDown' ? 1 : -1)))];
        selected.value = { kind: 'capture', id: next.id };
        setTimeout(() => document.querySelector(`#list .item[data-id="${next.id}"]`)?.scrollIntoView({ block: 'nearest' }));
      } else if (e.key === 'Enter' && sel) {
        const p = list[i]?.papers.find((x) => x.state === 'confirmed');
        if (p) openReader(p.id);
      } else if (sel && { d: 1, a: 1, x: 1 }[k]) triage(sel, { d: 'deep', a: 'archived', x: 'ignored' }[k], list, view);
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [list, sel, view]);
  const v = VIEWS[view];
  const needs = view === 'inbox' && list ? list.filter((x) => NEEDS_YOU.includes(x.status)) : [];
  const item = (x) => html`<${CaptureItem} key=${x.id} c=${x} on=${x.id === sel} view=${view} list=${list} />`;
  const head = html`<${ListHead} title=${v.title} sub=${list?.length ? `${list.length} 条` : ''}
      keys=${view === 'inbox' ? html`<kbd>J</kbd><kbd>K</kbd> 移动 <kbd>D</kbd> 深读 <kbd>A</kbd> 存档` : html`<kbd>J</kbd><kbd>K</kbd> 移动 <kbd>↵</kbd> 阅读`} />
    <div class="seg sm">${INBOX_FILTERS.map(([vv, label]) => html`<button class=${view === vv ? 'on' : ''} onClick=${() => go(vv)}>${label}${c[vv] ? html` <span class="hint">${c[vv]}</span>` : null}</button>`)}</div>`;
  const body = !list ? html`<div class="empty"><span class="spin"></span></div>` : !list.length ? html`<${Empty} e=${v.empty} />`
    : html`${needs.length ? html`<div class="group-label needs">需要你确认 · ${needs.length} 条：拿不准是哪篇、没找到论文或处理失败</div>${needs.map(item)}<div class="group-label">其余</div>` : null}
      ${list.filter((x) => !needs.includes(x)).map(item)}`;
  return html`<${ListDetail} hasSel=${!!sel} list=${html`${head}${body}`}
    detail=${sel ? html`<${CaptureDetail} key=${sel} id=${sel} view=${view} />` : list?.length ? html`<div class="empty" style="padding-top:120px">选择左侧一条查看</div>` : null} />`;
}

// ---------- papers ----------

export function PaperItem({ p, on, picked, onPick }) {
  return html`<div class=${`item ${on ? 'on' : ''}${picked ? ' picked' : ''}`} data-id=${p.id}
      onClick=${() => (onPick ? onPick(p.id) : (selected.value = { kind: 'paper', id: p.id }))}>
    <div class="item-meta"><span class="src">${idLabel(p.id)}</span>${p.published ? html`<span>· ${p.published.slice(0, 7)}</span>` : null}<time>${ago(p.last_captured)}</time></div>
    <div class="item-paper no-arrow" style="margin-top:0;font-size:14.5px">${p.title}</div>
    <div class="item-meta" style="margin:3px 0 0">${authors(p.authors)}</div>
    ${p.tldr ? html`<div class="item-tldr">${p.tldr}</div>` : null}
    ${p.hits ? html`<div class="item-meta" style="margin-top:4px">命中：${p.hits.map((x) => html`<span class="chip">${x}${x === '原文' && p.hit_page ? ` p.${p.hit_page}` : ''}</span>`)}</div>` : null}
    ${p.hit_text && !p.hits.some((x) => x !== '原文' && x !== '编号') ? html`<div class="item-tldr">“${p.hit_text}”</div>` : null}
    <div class="item-tags">${(p.topics || []).map((t) => html`<span class="chip topic">${t}</span>`)}${p.deep ? html`<span class="chip amber">稍后深读</span>` : null}</div>
    ${p.jumps ? html`<${Jumps} p=${p} j=${p.jumps} />` : null}</div>`;
}

/** The 论文 / 研究 tabs under a topic. */
export function TopicTabs({ topic, on, papers, research }) {
  return html`<div class="seg"><button class=${on === 'papers' ? 'on' : ''} onClick=${() => go('topic', topic)}>论文 ${papers ?? ''}</button>
    <button class=${on === 'research' ? 'on' : ''} onClick=${() => go('research', topic)}>研究 ${research ?? ''}</button></div>`;
}

export function PapersView({ view, topic }) {
  const q = view === 'topic' ? `?topic=${enc(topic)}` : view === 'unsorted' ? '?unsorted=1' : '';
  const [list] = useLive('/api/papers' + q);
  const [research] = useLive(topic ? `/api/research?topic=${enc(topic)}` : null);
  const [picking, setPicking] = useState(null);
  useEffect(() => setPicking(null), [view, topic]);
  const sel = useSel('paper', list);
  const pick = (id) => { const s = new Set(picking); s.has(id) ? s.delete(id) : s.add(id); setPicking(s); };
  const chosen = () => list.filter((x) => picking.has(x.id));
  const v = VIEWS[view];
  const title = topic ? topic.replace('/', ' / ') : v.title;
  const bar = picking && html`<div class="select-bar"><span>已选 ${picking.size} 篇论文</span><span class="grow"></span>
    <button class="btn sm primary" disabled=${picking.size < 2} title="把选中的几篇放在一起，和 agent 对比着讨论" onClick=${() => openThread(`papers:${chosen().map((x) => x.id).join('|')}`)}>一起讨论</button>
    <button class="btn sm" disabled=${!picking.size} onClick=${() => { const links = chosen().map((x) => ({ kind: 'paper', ref: x.id, label: x.title })); setPicking(null); openComposer({ links }); }}>开始研究</button>
    <button class="btn sm" disabled=${!picking.size} onClick=${() => { const ids = [...picking]; setPicking(null); researchPicker({ heading: `把 ${ids.length} 篇论文加入研究`, links: ids.map((ref) => ({ kind: 'paper', ref })) }); }}>加入研究</button>
    <button class="btn sm ghost" onClick=${() => setPicking(null)}>取消</button></div>`;
  const head = html`<${ListHead} title=${title} sub=${list?.length ? `${list.length} 条` : ''}
      keys=${list?.length ? html`<a href="#" onClick=${(e) => { e.preventDefault(); setPicking(picking ? null : new Set()); }}>${picking ? '退出多选' : '多选'}</a>` : null} />
    ${topic ? html`<${TopicTabs} topic=${topic} on="papers" papers=${list?.length} research=${research?.length} />` : null}${bar}`;
  const body = !list ? html`<div class="empty"><span class="spin"></span></div>` : !list.length ? html`<${Empty} e=${v.empty} />`
    : list.map((p) => html`<${PaperItem} key=${p.id} p=${p} on=${p.id === sel} picked=${picking?.has(p.id)} onPick=${picking ? pick : null} />`);
  return html`<${ListDetail} hasSel=${!!sel} list=${html`${head}${body}`}
    detail=${sel ? html`<${PaperDetail} key=${sel} pid=${sel} />` : list?.length ? html`<div class="empty" style="padding-top:120px">选择左侧一条查看</div>` : null} />`;
}

// ---------- search ----------

export function SearchView({ q }) {
  const [r] = useLive(`/api/search?q=${enc(q)}`, { poll: false });
  const s = selected.value, sel = s && (s.kind === 'paper' || s.kind === 'capture') ? s : null;
  const askAgent = async () => {
    try {
      const t = await post('/api/threads', { scope: 'library' });
      const text = `我记得库里有篇论文，记得的线索是：${q}。帮我在库里找找：换几种说法用 ./p2b search 搜（英文术语、方法名、作者），再看快读卡或原文确认，列出最可能的几篇并说明为什么。`;
      await post('/api/chat', { scope: 'library', text, thread_id: t.id });
      openThread('library', t.id);
    } catch (e) { fail(e); }
  };
  if (!r) return html`<${ListDetail} list=${html`<${ListHead} title="搜索" sub="" />`} />`;
  const total = r.papers.length + r.captures.length + r.threads.length + r.notes.length + r.messages.length + r.research.length;
  const ask = agentOn.value ? html`<button class=${`btn sm ${total ? 'ghost' : 'primary'}`} onClick=${askAgent}>让 agent 在库里找</button>` : null;
  const pickPaper = (id) => (selected.value = { kind: 'paper', id });
  const list = html`<${ListHead} title=${`“${q}”`} sub=${`${total} 条结果`} keys=${html`<span class="hint">每个词都要命中</span>`} />
    ${!total ? html`<div class="empty"><b>库里没找到</b>换几个词试试：英文标题里的词、方法名、作者、公众号名，或你讨论里的说法。<div style="margin-top:12px">${ask}</div></div>` : html`
    ${r.papers.length ? html`<div class="group-label">论文</div>${r.papers.map((p) => html`<${PaperItem} p=${p} on=${sel?.id === p.id} onPick=${pickPaper} />`)}` : null}
    ${r.research.length ? html`<div class="group-label">研究</div>${r.research.map((x) => html`<div class="item" onClick=${() => openResearch(x.id)}><div class="item-meta"><${StageChip} stage=${x.stage} /><time>${ago(x.updated_at)}</time></div>
      <div class="item-title">${x.title}</div>${x.summary ? html`<div class="item-tldr">${x.summary}</div>` : null}</div>`)}` : null}
    ${r.threads.length || r.messages.length ? html`<div class="group-label">讨论</div>
      ${r.threads.map((t) => html`<div class="item" onClick=${() => openThread(t.scope, t.id)}><div class="item-meta"><span>${scopeLabel(t.scope)}</span><time>${ago(t.created_at)}</time></div><div class="item-title">${t.title || t.first}</div></div>`)}
      ${r.messages.map((m) => html`<div class="item" onClick=${() => openThread(m.scope, m.thread_id, m.id)}><div class="item-meta"><span>${m.role === 'user' ? '你说' : 'agent 答'} · ${scopeLabel(m.scope)}</span><time>${ago(m.created_at)}</time></div><div class="item-tldr">${plain(m.text)}</div></div>`)}` : null}
    ${r.notes.length ? html`<div class="group-label">笔记</div>${r.notes.map((n) => html`<div class="item" onClick=${() => n.paper_id && pickPaper(n.paper_id)}><div class="item-tldr" style="-webkit-line-clamp:4">${n.text}</div><div class="item-meta" style="margin-top:4px"><span>agent 记录</span><time>${ago(n.created_at)}</time></div></div>`)}` : null}
    ${r.captures.length ? html`<div class="group-label">收下的内容</div>${r.captures.map((x) => html`<${CaptureItem} c=${{ ...x, papers: [], status: '' }} on=${sel?.id === x.id} />`)}` : null}
    ${ask ? html`<div class="empty" style="padding:20px">没找到想要的？${ask}</div>` : null}`}`;
  const detail = sel?.kind === 'paper' ? html`<${PaperDetail} key=${sel.id} pid=${sel.id} />` : sel?.kind === 'capture' ? html`<${CaptureDetail} key=${sel.id} id=${sel.id} />` : null;
  return html`<${ListDetail} hasSel=${!!sel} list=${list} detail=${detail} />`;
}
