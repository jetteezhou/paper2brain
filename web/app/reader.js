// The reader: the PDF (PDF.js canvas + text layer, so passages can be selected and discussions pinned to them) beside
// a panel of the paper's card, report, discussion, pinned passages and records. Also the selection popover and the
// figure lightbox, which open from anywhere a card is shown.
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { html, get, post, del, enc, short, idLabel, href, mdHtml, citeHtml, Mark, Md, workHot, openWork, DockGrip } from './lib.js';
import {
  route, baseRoute, appState, readerIntent, go, useLive, refresh, toast, fail, agentOn, openModal, closeModal, registerAction, openReader, quoting, peek, peekPaper, addPaper,
} from './state.js';
import { PaperCard, PaperNotes, PdfButton, RunCard, reportState } from './papers.js';
import { Chat } from './discuss.js';
import { showResearch } from './research.js';

// ---------- marking passages ----------

/** Wrap the passage (whitespace-insensitive) in <mark>s, across element boundaries; returns the last mark (or the first). */
function markText(container, quote, data, cls = 'anchor-mark', wantFirst = false) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest('button, .anchor-count, script') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [], owner = [];
  let text = '';
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    nodes.push(n);
    // hyphens dropped and ligatures unfolded: PDF line breaks split words as "rep-" + "resentation"
    [...n.data].forEach((ch, i) => { for (const c of ch.normalize('NFKC').toLowerCase()) if (!/[\s\-\u00ad]/.test(c)) { text += c; owner.push([nodes.length - 1, i]); } });
  }
  const q = quote.normalize('NFKC').toLowerCase().replace(/[\s\-\u00ad]/g, '');
  const at = q ? text.indexOf(q) : -1;
  if (at < 0) return null;
  const [sn, so] = owner[at], [en, eo] = owner[at + q.length - 1];
  let last = null, first = null;
  for (let k = en; k >= sn; k--) {
    const node = nodes[k], from = k === sn ? so : 0, to = k === en ? eo + 1 : node.data.length;
    if (to <= from || !node.data.slice(from, to).trim()) continue;
    const range = document.createRange();
    range.setStart(node, from);
    range.setEnd(node, to);
    const mark = document.createElement('mark');
    mark.className = cls;
    Object.assign(mark.dataset, data);
    range.surroundContents(mark);
    last = last || mark;
    first = mark;
  }
  return wantFirst ? first : last;
}
function unmark(root, sel) {
  root.querySelectorAll(sel).forEach((m) => m.replaceWith(...m.childNodes));
  root.normalize();
}

function decorateAnchors(p, root) {
  if (!p?.anchors?.length || !root) return;
  for (const a of p.anchors.filter((x) => x.surface !== 'pdf')) {
    const sel = a.surface === 'card' ? `[data-anchor-surface="card"][data-pid="${CSS.escape(p.id)}"]`
      : `[data-anchor-surface="report"][data-pid="${CSS.escape(p.id)}"]${a.section ? `[data-section="${CSS.escape(a.section)}"]` : ''}`;
    const boxes = [...(root.matches(sel) ? [root] : []), ...root.querySelectorAll(sel)];
    for (const box of boxes) {
      if (box.querySelector(`mark[data-id="${a.id}"]`)) continue;
      const last = markText(box, a.quote, { action: 'open-anchor', id: a.id, pid: p.id });
      if (last) {
        const b = Object.assign(document.createElement('button'), { className: 'anchor-count', textContent: a.message_count || '讨论', title: '打开这段的讨论' });
        Object.assign(b.dataset, { action: 'open-anchor', id: a.id, pid: p.id });
        last.after(b);
      }
    }
  }
}

/** Mark the paper's pinned passages inside a card or report after every render. */
export function useAnchorMarks(ref, paper) {
  useEffect(() => { decorateAnchors(paper, ref.current); });
}

// ---------- the PDF ----------

let pdfjs;
const PDFV = { doc: null, pid: null, scale: 1, observer: null, host: null, paper: null, flash: null };
async function pdfLib() {
  if (!pdfjs) {
    pdfjs = await import('/vendor/pdfjs/pdf.min.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.min.mjs';
  }
  return pdfjs;
}
const pageDiv = (n) => PDFV.host?.querySelector(`.pdf-page[data-page="${n}"]`);

async function mountPdf(host, pid, page = 1) {
  const lib = await pdfLib();
  const doc = PDFV.pid === pid && PDFV.doc ? PDFV.doc : await lib.getDocument(`/api/papers/${enc(pid)}/pdf`).promise;
  Object.assign(PDFV, { doc, pid, host });
  const base = (await doc.getPage(1)).getViewport({ scale: 1 });
  PDFV.scale = Math.max(0.6, Math.min(2.4, (host.clientWidth - 36) / base.width));
  PDFV.observer?.disconnect();
  PDFV.observer = new IntersectionObserver((entries) => entries.forEach((e) => e.isIntersecting && renderPage(+e.target.dataset.page)),
    { root: host, rootMargin: '800px 0px' });
  host.innerHTML = '';
  for (let n = 1; n <= doc.numPages; n++) {
    const div = document.createElement('div');
    Object.assign(div.dataset, { page: n, anchorSurface: 'pdf', pid });
    div.className = 'pdf-page';
    div.style.width = `${base.width * PDFV.scale}px`;
    div.style.height = `${base.height * PDFV.scale}px`;
    div.style.setProperty('--scale-factor', PDFV.scale);
    host.append(div);
    PDFV.observer.observe(div);
  }
  if (page > 1) jumpPdf(page);
}

/** Resolves once the page's canvas and text layer are in place (the same promise for concurrent callers). */
function renderPage(n) {
  const div = pageDiv(n);
  if (!div) return Promise.resolve();
  div.dataset.rendered = '1';
  div.rendering ||= drawPage(div, n);
  return div.rendering;
}

async function drawPage(div, n, doc = PDFV.doc, scale = PDFV.scale) {
  const page = await doc.getPage(n);
  const vp = page.getViewport({ scale });
  div.style.width = `${vp.width}px`;
  div.style.height = `${vp.height}px`;
  const dpr = window.devicePixelRatio || 1, canvas = document.createElement('canvas');
  canvas.width = Math.floor(vp.width * dpr);
  canvas.height = Math.floor(vp.height * dpr);
  div.append(canvas);
  await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr === 1 ? null : [dpr, 0, 0, dpr, 0, 0] }).promise;
  const layer = document.createElement('div');
  layer.className = 'textLayer';
  div.append(layer);
  await new (await pdfLib()).TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport: vp }).render();
  if (doc === PDFV.doc) markPage(n);
}

function jumpPdf(page, quote) {
  if (quote) PDFV.flash = { page, quote };
  const div = pageDiv(page);
  if (!div) return;
  div.scrollIntoView({ block: 'start' });
  renderPage(page).then(() => markPage(page));
}

function markPage(n) {
  const div = pageDiv(n), layer = div?.querySelector('.textLayer'), p = PDFV.paper;
  if (!layer || !p) return;
  unmark(layer, 'mark.anchor-hl, mark.rp-hl');
  div.querySelectorAll('.anchor-bubble').forEach((b) => b.remove());
  (p.report?.sections || []).forEach((s, i) => points(s).forEach((pt, j) => pt.citations.filter((c) => c.page === n)
    .forEach((c) => markText(layer, c.quote, { action: 'open-point', pt: `${i}.${j}`, pid: p.id }, `rp-hl rc${i % 6}`))));
  const top = div.getBoundingClientRect().top;
  for (const a of p.anchors.filter((x) => x.surface === 'pdf' && x.page === n)) {
    const first = markText(layer, a.quote, { action: 'open-anchor', id: a.id, pid: p.id }, 'anchor-hl', true);
    if (first) {
      const b = Object.assign(document.createElement('button'), { className: 'anchor-bubble', textContent: a.message_count ? `讨论 ${a.message_count}` : '讨论', title: a.quote });
      Object.assign(b.dataset, { action: 'open-anchor', id: a.id, pid: p.id });
      b.style.top = `${first.getBoundingClientRect().top - top}px`;
      div.append(b);
    }
  }
  const f = PDFV.flash;
  if (f && f.page === n) {
    const first = markText(layer, f.quote, {}, 'flash-hl', true);
    first?.scrollIntoView({ block: 'center' });
    setTimeout(() => unmark(layer, 'mark.flash-hl'), 2600);
    PDFV.flash = null;
  }
}
const markAllPdf = () => PDFV.host?.querySelectorAll('.pdf-page[data-rendered]').forEach((d) => markPage(+d.dataset.page));

function PdfView({ pid, page, wide }) {
  const host = useRef();
  const [error, setError] = useState(null);
  useEffect(() => {
    const top = PDFV.host === host.current ? [...host.current.querySelectorAll('.pdf-page')].find((d) => d.getBoundingClientRect().bottom > 80) : null;
    mountPdf(host.current, pid, top ? +top.dataset.page : page || 1).catch((e) => setError(e.message));
  }, [pid, wide]);
  if (error) return html`<div class="pdf-missing">PDF 打不开：${error}</div>`;
  return html`<div class="pdfv" id="pdfv" ref=${host}><div class="pdf-loading"><span class="spin"></span>正在打开 PDF…</div></div>`;
}

// ---------- panel tabs ----------

/** A report section's paragraphs, each with the sentences it was read from (older reports: one body per section). */
const points = (s) => s.points || [{ text: s.body, citations: s.citations }];
/** The paragraph with its [k] markers turned into page buttons; citations it never marks (older reports) go at the end. */
function pointHtml(pid, pt) {
  const used = new Set();
  const body = mdHtml(pt.text).replace(/\[(\d+)\]/g, (m, k) => {
    const c = pt.citations[k - 1];
    if (!c) return m;
    used.add(c);
    return citeHtml(pid, c.page, c.quote);
  });
  const rest = pt.citations.filter((c) => !used.has(c));
  return body + (rest.length ? `<div class="cites">${rest.map((c) => citeHtml(pid, c.page, c.quote)).join('')}</div>` : '');
}
const hoverPoint = (pt, on) => PDFV.host?.querySelectorAll(`mark.rp-hl[data-pt="${pt}"]`).forEach((m) => m.classList.toggle('on', on));

function Report({ p, section, point }) {
  const ref = useRef();
  useAnchorMarks(ref, p);
  useEffect(() => {
    if (!section) return;
    const target = [...(ref.current?.querySelectorAll('.section') || [])].find((s) => s.dataset.section === section);
    if (target) { target.scrollIntoView({ block: 'start' }); target.classList.add('flash'); }
  }, [section, !!p.report]);
  useEffect(() => {
    const el = point && ref.current?.querySelector(`.rpoint[data-pt="${point.pt}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'center' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }, [point, !!p.report]);
  const run = p.runs.find((x) => x.kind === 'deepread' && (x.status === 'WAITING' || x.status === 'CREATING' || x.status === 'RUNNING' || x.status === 'QUEUED'));
  if (run) {
    return html`<div class="card-wait"><${Mark} spin />Agent 正在写深读报告，通常需要几分钟。可以先读左边的原文，写好后会自动出现在这里。</div>
      ${p.card?.tldr ? html`<div class="tldr">${p.card.tldr}</div>` : null}<${RunCard} r=${run} label="深读" />`;
  }
  if (!p.report) {
    const last = p.runs.find((x) => x.kind === 'deepread');
    return html`<div class="chat-empty" style="margin-top:40px"><b>还没有深读报告</b>
      <p>agent 会通读全文，按“动机 → 方法 → 实验 → 结果 → 局限 → 对你的启发”写报告，每段都能跳回原文；你在讨论里问过的问题会优先回答。</p>
      <button class="btn primary" disabled=${!agentOn.value} onClick=${async (e) => { e.target.disabled = true; try { await post(`/api/papers/${enc(p.id)}/deepread`); refresh(); } catch (err) { fail(err); e.target.disabled = false; } }}>生成深读报告</button>
      ${agentOn.value ? null : html`<p class="hint">需要先连接 agent</p>`}${last ? html`<${RunCard} r=${last} label="上次深读" />` : null}</div>`;
  }
  const toSection = (h) => { const t = [...ref.current.querySelectorAll('.section')].find((s) => s.dataset.section === h); t?.scrollIntoView({ block: 'start' }); };
  return html`<div ref=${ref}><div class="rtoc">${p.report.sections.map((s, i) => html`<button class=${`jump go rc${i % 6}`} title=${s.heading} onClick=${() => toSection(s.heading)}>${short(s.heading, 14)}</button>`)}</div>
    ${p.report.sections.map((s, i) => html`<div class=${`section rc${i % 6}`} data-anchor-surface="report" data-pid=${p.id} data-section=${s.heading}><h3>${s.heading}</h3>
      ${points(s).map((pt, j) => html`<div class="rpoint" data-pt=${`${i}.${j}`} onMouseEnter=${() => hoverPoint(`${i}.${j}`, true)} onMouseLeave=${() => hoverPoint(`${i}.${j}`, false)}>
        <div class="md" dangerouslySetInnerHTML=${{ __html: pointHtml(p.id, pt) }} /></div>`)}</div>`)}
    <div style="margin-top:20px"><button class="btn sm ghost" disabled=${!agentOn.value} onClick=${async () => { try { await post(`/api/papers/${enc(p.id)}/deepread`); refresh(); } catch (e) { fail(e); } }}>重新生成</button></div></div>`;
}

const SURFACE = { card: '快读卡', report: '深读报告', pdf: '原文' };
const anchorWhere = (a) => `${SURFACE[a.surface]}${a.page ? ` p.${a.page}` : ''}${a.section ? ` · ${a.section}` : ''}`;

function Anchors({ p, open }) {
  if (!p.anchors.length) {
    return html`<div class="chat-empty" style="margin-top:40px"><b>还没有划词讨论</b><p class="hint">在快读卡、深读报告或左边原文里选中一段文字，就能围绕它发起讨论；讨论会一直挂在那段文字旁边，随时点开接着聊。</p></div>`;
  }
  return html`${p.anchors.map((a) => html`<div class="source" onClick=${() => open(a.id)}><span class="source-kind">${anchorWhere(a)}</span>
    <span class="source-label">「${short(a.quote, 90)}」<div class="hint">${a.message_count ? `${a.message_count} 条 · ${short(a.last_text || '', 60)}` : '还没开始聊'}</div></span></div>`)}`;
}

function AnchorPanel({ p, id, back, locate, threadId }) {
  const a = p.anchors.find((x) => x.id === id);
  if (!a) return html`<div class="chat-empty">这段划词讨论已删除</div>`;
  const remove = async () => {
    if (a.message_count && !confirm(`删除这段划词和它的 ${a.message_count} 条讨论？`)) return;
    await del(`/api/anchors/${enc(a.id)}`).catch(fail);
    back();
    refresh();
  };
  return html`<div class="anchor-head"><button class="btn sm ghost" onClick=${back}>← 全部划词</button><span class="chip">${anchorWhere(a)}</span><span class="grow"></span>
      <button class="btn sm ghost" onClick=${() => locate(a)}>定位到原处</button><button class="btn sm ghost danger" onClick=${remove}>删除</button></div>
    <blockquote class="anchor-quote">${a.quote}</blockquote>
    <div class="anchor-chat"><${Chat} key=${threadId || a.id} scope=${`anchor:${a.id}`} threadId=${threadId} /></div>`;
}

/** Same corner logo as the sidebar: the reader covers that one, so the menu stays reachable while reading. */
function WorkLogo() {
  const hot = workHot(appState.value);
  return html`<button type="button" class="reader-brand" title=${hot ? '跳到正在工作的页面' : '最近三次 agent 工作'} aria-label=${hot ? '正在工作' : '最近的工作'} onClick=${(e) => openWork.fn?.(e)}><${Mark} quiet cls="brand-mark" spin=${hot} /></button>`;
}

// ---------- the reader ----------

const wideSig = signal(localStorage.getItem('p2b.wide') === '1');
const closeReader = () => go(baseRoute.value.view, baseRoute.value.arg);

export function Reader() {
  const [pid, pageArg] = (route.value.arg || '').split('@');
  const [p] = useLive(pid ? `/api/papers/${enc(pid)}` : null);
  const [view, setView] = useState({});
  const prevReport = useRef(null);
  useEffect(() => { if (pid) post(`/api/papers/${enc(pid)}/read`).catch(() => {}); }, [pid]);
  useEffect(() => { if (p) document.title = `${p.short_name || short(p.title, 40)} · paper2brain`; }, [p?.title, p?.short_name]);
  // the intent from whoever opened the reader (or asked again while it is open): tab, page, section, anchor, thread
  useEffect(() => {
    const it = readerIntent.value;
    setView((v) => ({ ...v, ...it, tab: it.tab || (it.anchor ? 'anchor' : v.tab) }));
    if (it.page && PDFV.pid === pid) jumpPdf(it.page, it.q);
    else if (it.page && it.q) PDFV.flash = { page: it.page, quote: it.q };
  }, [readerIntent.value, pid]);
  useEffect(() => {
    if (!p) return;
    PDFV.paper = p;
    markAllPdf();
    const state = reportState(p);
    if (prevReport.current === 'running' && state === 'done' && view.tab !== 'report') toast('深读报告写好了', '去看', () => setView((v) => ({ ...v, tab: 'report' })));
    prevReport.current = state;
  }, [p]);
  if (!p) return html`<div id="reader"><div class="reader-top"><${WorkLogo} /><button class="btn sm ghost" onClick=${closeReader}>← 返回</button><div class="title"><span class="spin"></span></div></div></div>`;
  const tab = view.tab || (reportState(p) ? 'report' : 'card');
  const setTab = (t, extra = {}) => setView((v) => ({ ...v, tab: t, ...extra }));
  const locate = (a) => {
    if (a.surface === 'pdf') return jumpPdf(a.page, a.quote);
    setTab(a.surface);
    setTimeout(() => { const m = document.querySelector(`#panel mark[data-id="${a.id}"]`); if (m) { m.scrollIntoView({ block: 'center' }); m.classList.add('flash'); } }, 80);
  };
  const tabs = [['card', '快读卡'], ['report', '深读报告'], ['chat', '讨论'], ['anchors', '划词', p.anchors.length], ['notes', '记录', p.threads.length + p.notes.length + (p.cited_in?.length || 0)]];
  const chatty = tab === 'chat' || tab === 'anchor';
  let body;
  if (tab === 'card') body = html`<${PaperCard} paper=${p} inReader />`;
  else if (tab === 'report') body = html`<${Report} p=${p} section=${view.section} point=${view.point} />`;
  else if (tab === 'chat') body = html`<${Chat} key=${`c-${view.threadId || ''}`} scope=${`paper:${p.id}`} threadId=${view.threadId} />`;
  else if (tab === 'anchors') body = html`<${Anchors} p=${p} open=${(id) => setTab('anchor', { anchor: id })} />`;
  else if (tab === 'anchor') body = html`<${AnchorPanel} key=${view.anchor} p=${p} id=${view.anchor} back=${() => setTab('anchors')} locate=${locate} threadId=${view.threadId} />`;
  else body = html`<${PaperNotes} p=${p} flush />`;
  return html`<div id="reader">
    <div class="reader-top">
      <${WorkLogo} />
      <button class="btn sm ghost" onClick=${closeReader}>← 返回</button>
      <div class="title">${p.title}</div>
      ${p.pdf_path && p.outline?.length ? html`<select class="toc" title="跳到原文的某一节" onChange=${(e) => { const [pg, ...t] = e.target.value.split('|'); e.target.value = ''; if (pg) jumpPdf(+pg, t.join('|')); }}>
        <option value="">原文目录…</option>${p.outline.map((s) => html`<option value=${`${s.page}|${s.title}`}>${'　'.repeat(Math.min(s.level, 3) - 1)}${short(s.title, 48)}　p.${s.page}</option>`)}</select>` : null}
      ${p.url ? html`<a class="btn sm ghost" href=${href(p.url)} target="_blank" rel="noopener">${idLabel(p.id).split(' ')[0]} ↗</a>` : null}
      <button class="btn sm ghost" onClick=${() => { wideSig.value = !wideSig.value; localStorage.setItem('p2b.wide', wideSig.value ? '1' : '0'); }}>调整面板宽度</button>
    </div>
    <div class=${`reader-body ${wideSig.value ? 'wide' : ''}`}>
      ${p.pdf_path ? html`<${PdfView} pid=${p.id} page=${+pageArg || view.page || 1} wide=${wideSig.value} />`
        : html`<div class="pdf-missing"><div style="text-align:center">还没有 PDF<br /><${PdfButton} paper=${p.id} /></div></div>`}
      <div class="panel"><${DockGrip} /><div class="tabs" id="tabs">${tabs.map(([k, label, n]) => html`<button class=${`tab ${tab === k || (k === 'anchors' && tab === 'anchor') ? 'on' : ''}`} onClick=${() => setTab(k)}>${label}${n ? html`<span class="n">${n}</span>` : null}</button>`)}</div>
        <div class=${`panel-body ${chatty ? 'chatwrap' : ''}`} id="panel">${body}</div></div>
    </div></div>`;
}

/** Open a pinned passage's discussion: in the reader if it shows that paper, else open the reader there. */
function openAnchor(id, pid) {
  openReader(pid || route.value.arg?.split('@')[0], { tab: 'anchor', anchor: id });
}
registerAction('open-anchor', (el) => { if (!window.getSelection().toString().trim()) openAnchor(el.dataset.id, el.dataset.pid); });
registerAction('open-point', (el) => { if (!window.getSelection().toString().trim()) readerIntent.value = { tab: 'report', point: { pt: el.dataset.pt } }; });
registerAction('cite', (el) => {
  const pid = el.dataset.pid, page = +el.dataset.page, q = el.title === '跳到原文' ? null : el.title;
  if (route.value.view === 'read' && route.value.arg?.split('@')[0] === pid && PDFV.pid === pid) jumpPdf(page, q);
  else peekPaper(pid, page, q);
});

// ---------- peek: the PDF in a floating window, to check a detail without leaving the page ----------

/** Its own PDF.js document and pages, so it never disturbs the reader's; the quoted line flashes once its page draws.
 * Pages follow the window's width: stretched at once while resizing, redrawn sharp once it settles. */
function PeekPdf({ pid, page, q }) {
  const host = useRef();
  const [error, setError] = useState(null);
  useEffect(() => {
    let doc, io, ro, timer, dead = false, flashed = !q;
    (async () => {
      doc = await (await pdfLib()).getDocument(`/api/papers/${enc(pid)}/pdf`).promise;
      const base = (await doc.getPage(1)).getViewport({ scale: 1 }), el = host.current;
      const fitScale = () => Math.max(0.5, (el.clientWidth - 24) / base.width);
      let scale = fitScale();
      if (dead) return;
      // each drawing goes into its own box, so a redraw can drop a stale one even while it is still rendering
      const size = (div) => {
        const w = `${base.width * scale}px`, h = `${base.height * scale}px`;
        Object.assign(div.style, { width: w, height: h });
        div.style.setProperty('--scale-factor', scale);
        if (div.firstChild) Object.assign(div.firstChild.style, { width: w, height: h });
      };
      io = new IntersectionObserver((es) => es.forEach((e) => {
        const div = e.target, n = +div.dataset.page;
        if (!e.isIntersecting || div.firstChild) return;
        const box = div.appendChild(Object.assign(document.createElement('div'), { className: 'peek-box' }));
        drawPage(box, n, doc, scale).then(() => {
          const m = !flashed && n === page && markText(box.querySelector('.textLayer'), q, {}, 'flash-hl', true);
          if (m) { flashed = true; m.scrollIntoView({ block: 'center' }); setTimeout(() => unmark(box, 'mark.flash-hl'), 2600); }
        });
      }), { root: el, rootMargin: '600px 0px' });
      el.innerHTML = '';
      const divs = [];
      for (let n = 1; n <= doc.numPages; n++) {
        const div = Object.assign(document.createElement('div'), { className: 'pdf-page' });
        div.dataset.page = n;
        size(div);
        el.append(div);
        divs.push(div);
        io.observe(div);
      }
      el.querySelector(`.pdf-page[data-page="${page || 1}"]`)?.scrollIntoView({ block: 'start' });
      ro = new ResizeObserver(() => {
        const s = fitScale();
        if (Math.abs(s - scale) < 0.005) return;
        const at = el.scrollTop / el.scrollHeight;
        scale = s;
        divs.forEach(size);
        el.scrollTop = at * el.scrollHeight;
        clearTimeout(timer);
        timer = setTimeout(() => divs.forEach((d) => { if (d.firstChild) { d.firstChild.remove(); io.unobserve(d); io.observe(d); } }), 250);
      });
      ro.observe(el);
    })().catch((e) => !dead && setError(e.message));
    return () => { dead = true; clearTimeout(timer); ro?.disconnect(); io?.disconnect(); doc?.destroy(); };
  }, [pid, page, q]);
  if (error) return html`<div class="pdf-missing">PDF 打不开：${error}</div>`;
  return html`<div class="peek-pdf" ref=${host}><div class="pdf-loading"><span class="spin"></span>正在打开 PDF…</div></div>`;
}

export function Peek() {
  const pk = peek.value;
  const [p, reload, err] = useLive(pk ? `/api/papers/${enc(pk.pid)}` : null, { poll: false });
  const [pos, setPos] = useState(null);
  const [drag, setDrag] = useState(null);
  const [adding, setAdding] = useState(null);
  const mine = adding && adding.pid === pk?.pid ? adding : null;
  useEffect(() => {
    if (!pk || mine || !(err || p?.listed === false)) return;
    setAdding({ pid: pk.pid });
    addPaper(pk.pid).then((np) => { if (np.id !== pk.pid) peek.value = { ...pk, pid: np.id }; else reload(); })
      .catch((e) => setAdding({ pid: pk.pid, error: e.message }));
  }, [pk?.pid, err, p?.listed]);
  if (!pk) return null;
  const down = (e) => {
    if (e.target.closest('button')) return;
    const r = e.currentTarget.parentElement.getBoundingClientRect();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ dx: e.clientX - r.left, dy: e.clientY - r.top });
  };
  const move = (e) => drag && setPos({ x: Math.max(0, e.clientX - drag.dx), y: Math.max(0, e.clientY - drag.dy) });
  const full = () => { peek.value = null; openReader(pk.pid, { tab: 'card', page: pk.page, q: pk.q }); };
  return html`<div class="peek" role="dialog" aria-label="速览论文" style=${pos ? { left: `${pos.x}px`, top: `${pos.y}px`, right: 'auto' } : null}>
    <div class="peek-top" onPointerDown=${down} onPointerMove=${move} onPointerUp=${() => setDrag(null)}>
      <b title=${p?.title}>${p ? short(p.title, 60) : '…'}</b>${pk.page ? html`<span class="hint">p.${pk.page}</span>` : null}<span class="grow"></span>
      <button class="btn sm ghost" onClick=${full}>在阅读器打开</button><button class="btn sm ghost" aria-label="关闭" onClick=${() => { peek.value = null; }}>×</button></div>
    ${pk.q ? html`<div class="peek-q hint" title=${pk.q}>找这句：${short(pk.q, 90)}</div>` : null}
    ${mine?.error && !p ? html`<div class="pdf-missing"><div style="text-align:center">收不下这篇论文：${mine.error}<br />${pk.pid.startsWith('arxiv:') ? html`<a class="btn sm ghost" href=${`https://arxiv.org/abs/${pk.pid.slice(6)}`} target="_blank" rel="noopener">在 arXiv 打开</a>` : null}</div></div>`
      : !p ? html`<div class="pdf-loading"><span class="spin"></span>${mine ? '这篇不在论文库里，正在收下（拉取元数据和 PDF）…' : ''}</div>`
      : p.pdf_path ? html`<${PeekPdf} pid=${pk.pid} page=${pk.page} q=${pk.q} />`
      : html`<div class="pdf-missing"><div style="text-align:center">还没有 PDF<br /><${PdfButton} paper=${p.id} /></div></div>`}</div>`;
}

// ---------- selection popover ----------

/** Grow a selection that starts or ends mid-word to whole words (Latin text). */
function wholeWords(range, text) {
  const w = /[A-Za-z0-9]/;
  const { startContainer: s, startOffset: so, endContainer: e, endOffset: eo } = range;
  if (s.nodeType === 3 && so > 0 && w.test(s.data[so - 1]) && w.test(s.data[so] || '')) text = s.data.slice(0, so).match(/[A-Za-z0-9]*$/)[0] + text;
  if (e.nodeType === 3 && eo < e.data.length && w.test(e.data[eo]) && w.test(e.data[eo - 1] || '')) text += e.data.slice(eo).match(/^[A-Za-z0-9]*/)[0];
  return text;
}

export function SelectionPopup() {
  const [ctx, setCtx] = useState(null);
  const [v, setV] = useState('');
  const [ans, setAns] = useState(null);
  const box = useRef(), input = useRef(), cur = useRef();
  cur.current = ctx;
  useEffect(() => {
    const up = (e) => {
      if (box.current?.contains(e.target)) return;
      setTimeout(() => {
        const sel = window.getSelection();
        if (!sel.rangeCount || sel.isCollapsed || box.current?.contains(sel.anchorNode)) return;
        const range = sel.getRangeAt(0);
        const text = wholeWords(range, sel.toString()).replace(/\s+/g, ' ').trim();
        if (text.length < 2) return;
        const node = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
        const host = node.closest('[data-anchor-surface]'), into = node.closest('.quotable')?.closest('[data-quote-scope]');
        const editing = document.activeElement?.matches('input, textarea') && into?.contains(document.activeElement);
        if (!host && !into || editing) return;
        const pg = node.closest('.pdf-page'), rect = range.getBoundingClientRect(), at = { top: rect.top, bottom: rect.bottom, left: rect.left };
        setV(''); setAns(null);
        const base = { quote: text.slice(0, 2000), rect: at, range: range.cloneRange() };
        if (into) return setCtx({ ...base, scope: into.dataset.quoteScope });
        setCtx({ ...base, pid: host.dataset.pid, surface: host.dataset.anchorSurface, page: pg ? +pg.dataset.page : null, section: host.dataset.section || null });
      }, 0);
    };
    const down = (e) => { if (!box.current?.contains(e.target)) setCtx(null); };
    document.addEventListener('mouseup', up);
    document.addEventListener('mousedown', down);
    return () => { document.removeEventListener('mouseup', up); document.removeEventListener('mousedown', down); };
  }, []);
  // Our own highlight of the passage: the native selection goes away as soon as the question box takes focus.
  useEffect(() => {
    if (!ctx || !window.CSS?.highlights) return;
    CSS.highlights.set('selpop', new Highlight(ctx.range));
    return () => CSS.highlights.delete('selpop');
  }, [ctx]);
  useEffect(() => {
    const el = box.current;
    if (!ctx || !el) return;
    const top = ctx.rect.top > 150 && ctx.rect.top - el.offsetHeight - 8 >= 8 ? ctx.rect.top - el.offsetHeight - 8 : ctx.rect.bottom + 8;
    el.style.top = `${Math.max(8, Math.min(top, innerHeight - el.offsetHeight - 8))}px`;
    el.style.left = `${Math.min(innerWidth - el.offsetWidth - 8, Math.max(8, ctx.rect.left))}px`;
  }, [ctx, ans]);
  if (!ctx) return null;
  const done = () => { setCtx(null); window.getSelection().removeAllRanges(); };
  const question = () => ans?.q || v.trim();
  // "快速问": answered here from what is on this page; nothing is saved and no discussion sees it.
  const quick = async (web) => {
    const c = ctx, q = v.trim() || '这段是什么意思？';
    setAns({ q, web });
    try {
      const r = await post('/api/quick', { ...(c.scope ? { scope: c.scope } : { pid: c.pid, page: c.page, surface: c.surface }), quote: c.quote, question: q, web });
      if (cur.current === c) setAns({ q, web, text: r.text, sources: r.sources || [] });
    } catch (e) { if (cur.current === c) setAns({ q, web, error: e.message }); }
  };
  const answer = ans ? html`<div class="selpop-ans">${ans.text ? html`<${Md} text=${ans.text} />
      ${ans.sources?.length ? html`<div class="hint">网上来源：${ans.sources.map((x) => html`<a href=${x.url} target="_blank">${short(x.title || x.url, 30)}</a>　`)}</div>` : null}`
    : ans.error ? html`<span class="hint" style="color:var(--red)">${ans.error}</span>` : html`<span class="spin"></span> <span class="hint">${ans.web ? '在网上查…' : '几秒内回答…'}</span>`}</div>` : null;
  const keep = (e) => { if (e.target.tagName !== 'INPUT') e.preventDefault(); };
  const field = (ph) => html`<form onSubmit=${(e) => { e.preventDefault(); quick(false); }}><input ref=${input} value=${v} onInput=${(e) => setV(e.target.value)} placeholder=${ph} /></form>`;
  const quickBtn = html`<button class="btn sm primary" title="几秒内在这里回答，只看这一页附近的内容，不保存" onClick=${() => quick(false)}>快速问</button>
    ${appState.value?.agent?.search_api ? html`<button class="btn sm" title="可以上网搜索补充，答案带网上来源，不保存（约 5 秒）" onClick=${() => quick(true)}>上网查</button>` : null}`;
  if (ctx.scope) {
    // A new discussion for the agent, so it never lands in the middle of the one in progress.
    const deepen = async () => {
      const c = ctx, q = question() || '这段是什么意思？';
      done();
      try {
        const t = await post('/api/threads', { scope: c.scope });
        await post('/api/chat', { scope: c.scope, thread_id: t.id, text: `「${c.quote}」\n${q}` });
        const k = c.scope.split(':')[0];
        showResearch(c.scope.slice(k.length + 1), { research: 'overview', related: 'materials', experiment: 'experiments' }[k] || k, t.id);
        refresh();
      } catch (e) { fail(e); }
    };
    return html`<div id="selpop" ref=${box} onMouseDown=${keep}>
      <div class="selpop-quote">「${short(ctx.quote, 48)}」</div>${field('问点什么，回车快速回答')}${answer}
      <div class="selpop-acts">${quickBtn}
        ${agentOn.value ? html`<button class="btn sm" title="新开一段讨论，让 agent 查这个研究的全部资料来回答" onClick=${deepen}>${ans ? '让 agent 深入' : '新开讨论问 agent'}</button>` : null}
        <button class="btn sm ghost" title="把这段引用放进右侧讨论的输入框，接着写想怎么改" onClick=${() => { quoting.value = { scope: ctx.scope, text: ctx.quote }; done(); }}>加入讨论</button></div></div>`;
  }
  const discuss = async () => {
    const c = ctx, text = question();
    done();
    try {
      const { anchor, error } = await post(`/api/papers/${enc(c.pid)}/anchors`, { pid: c.pid, surface: c.surface, quote: c.quote, page: c.page, section: c.section, text });
      refresh();
      openAnchor(anchor.id, c.pid);
      if (error) toast(`划词已保存，但没能发出问题：${error}`, null, null, true);
    } catch (e) { fail(e); }
  };
  const jot = async () => {
    if (!v.trim()) { input.current.placeholder = '先写下你的想法，再点“记下来”'; input.current.focus(); return; }
    const c = ctx, note = v.trim();
    done();
    try { await post('/api/threads', { scope: `paper:${c.pid}`, text: `关于「${short(c.quote, 120)}」：${note}` }); toast('已记下，开了一段关于这篇的讨论'); refresh(); } catch (e) { fail(e); }
  };
  return html`<div id="selpop" ref=${box} onMouseDown=${keep}>
    <div class="selpop-quote">「${short(ctx.quote, 48)}」</div>${field('围绕这段问点什么，回车快速回答')}${answer}
    <div class="selpop-acts">${quickBtn}<button class="btn sm" title="新开一段关于这段的讨论，让 agent 翻全文、查库里别的论文来回答" onClick=${discuss}>${ans ? '让 agent 深入' : '讨论这段'}</button>
      <button class="btn sm ghost" title="只记下，不叫 agent 回答" onClick=${jot}>记下来</button></div></div>`;
}

// ---------- figure lightbox ----------

const figSrc = (pid, n) => `/api/papers/${enc(pid)}/figures/${n}`;

function Lightbox({ pid, list, start }) {
  const [i, setI] = useState(start);
  useEffect(() => {
    const key = (e) => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') setI((x) => Math.max(0, Math.min(list.length - 1, x + (e.key === 'ArrowRight' ? 1 : -1)))); };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, []);
  const f = list[i];
  return html`<div class="lightbox"><div class="lb-head"><b>图 ${f.n}</b><span class="hint">p.${f.page}${list.length > 1 ? ` · ${i + 1} / ${list.length}` : ''}</span><span class="grow"></span>
      <button class="btn sm" onClick=${() => { closeModal(); openReader(pid, { page: f.page, q: short(f.caption, 40) }); }}>在原文中看 →</button>
      <button class="btn sm ghost" onClick=${closeModal}>关闭 <kbd>Esc</kbd></button></div>
    <div class="lb-stage">${list.length > 1 ? html`<button class="figs-nav prev" disabled=${!i} onClick=${() => setI(i - 1)}>‹</button>` : null}
      <img src=${figSrc(pid, f.n)} alt=${`图 ${f.n}`} />
      ${list.length > 1 ? html`<button class="figs-nav next" disabled=${i >= list.length - 1} onClick=${() => setI(i + 1)}>›</button>` : null}</div>
    ${f.note ? html`<p class="lb-note">${f.note}</p>` : null}<p class="lb-caption">${f.caption}</p></div>`;
}

export async function openFigure(pid, n, all) {
  const p = await get(`/api/papers/${enc(pid)}`).catch(fail);
  const list = p && (all ? p.figures : p.key_figures.length ? p.key_figures : p.figures);
  if (!list?.length) return;
  openModal(html`<${Lightbox} pid=${pid} list=${list} start=${Math.max(0, list.findIndex((f) => f.n === +n))} />`, true);
}
