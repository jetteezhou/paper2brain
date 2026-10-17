// Research: discussions and papers converging on three elements (problem, method, validation), plus code,
// experiments and writing. Only the user establishes a research; the agent's changes arrive as drafts.
import { useEffect, useRef, useState } from 'preact/hooks';
import { html, get, post, del, enc, ago, short, firstLine, idLabel, plain, threadName, live, Md, mdHtml, citeHtml, readBase64 } from './lib.js';
import {
  appState, agentOn, selected, useLive, refresh, toast, fail, openModal, closeModal, openItem, openReader, openThread, openCapture, openResearch,
} from './state.js';
import { ListDetail, TopicsRow, TopicTabs, RunCard } from './papers.js';

const STAGE = { forming: ['构思中', 'chip amber'], established: ['已确立', 'chip green-solid'] };
export const StageChip = ({ stage }) => html`<span class=${STAGE[stage][1]}>${STAGE[stage][0]}</span>`;
export const ResearchChip = ({ x }) => html`<a href="#" class="chip research-chip" title="打开这个研究" onClick=${(e) => { e.preventDefault(); e.stopPropagation(); openResearch(x.id); }}>${STAGE[x.stage][0]} · ${short(x.title, 22)}</a>`;
const ElementsChip = ({ n }) => html`<span class=${`chip ${n === 3 ? 'green' : ''}`} title="研究问题 / 研究方法 / 验证方案">三要素 ${n}/3</span>`;

const FIELDS = [
  ['problem', '研究问题', '要解决什么、为什么重要、现有方法为什么不够'],
  ['method', '研究方法', '核心洞见 + 方法思路（2–5 步，输入输出、关键模块）'],
  ['validation', '验证方案', '最小验证实验：数据 / 平台、对比谁、看什么指标、多大算成功'],
  ['notes', '其他', '和已有工作的区别、风险与未知（可选）'],
];
const ELEMENTS = FIELDS.slice(0, 3);
const CONCLUSION = { supports: ['支持', 'chip green'], refutes: ['反驳', 'chip red'], inconclusive: ['不确定', 'chip'] };
const EXP_STATUS = { planned: '计划中', running: '进行中', done: '完成', abandoned: '放弃' };
const runsOf = (x, kind) => x.runs.filter((r) => r.kind === kind);
const liveRun = (runs) => runs.find((r) => live(r.status));
const Html = ({ s, tag = 'span', cls }) => html`<${tag} class=${cls} dangerouslySetInnerHTML=${{ __html: s }} />`;
const inlineMd = (s) => mdHtml(s).replace(/^<p>|<\/p>$/g, '');

async function act(path, body, ok) {
  try { await post(`/api/research/${path}`, body); if (ok) toast(ok); } catch (e) { fail(e); }
  refresh();
}

function RunsBlock({ runs, label, spec }) {
  if (!runs.length) return null;
  return html`<details class="runs" open=${!!liveRun(runs)}><summary>${label}记录 · ${runs.length} 次 <span class="hint">（调优：改 ${spec}）</span></summary>${runs.map((r) => html`<${RunCard} r=${r} label=${label} />`)}</details>`;
}

// ---------- overview: the three elements ----------

function Proposal({ x }) {
  const p = x.proposal;
  const current = (k) => (k === 'title' ? x.title : k === 'summary' ? x.summary : x.body?.[k]) || '';
  const rows = [['title', '名称'], ['summary', '一句话'], ...FIELDS.map(([k, l]) => [k, l])].filter(([k]) => p[k] && p[k] !== current(k));
  const decide = (op) => {
    if (op === 'accept-establish' && !confirm('采用修改稿并确立这个研究？')) return;
    act(`${enc(x.id)}/proposal`, { action: op === 'discard' ? 'discard' : 'accept', establish: op === 'accept-establish', base_rev: x.revision },
      { accept: '已采用修改稿', 'accept-establish': '已采用并确立', discard: '已放弃修改稿' }[op]);
  };
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动研究</span></div>
    ${rows.length ? rows.map(([k, l]) => html`<div class="pr-row"><div class="pr-label">${l} ${current(k) ? html`<span class="chip">修改</span>` : html`<span class="chip green">新增</span>`}</div><div class="pr-new"><${Md} text=${p[k]} /></div></div>`)
      : html`<div class="hint">和现在的内容一样。</div>`}
    <div class="proposal-acts"><button class="btn primary" onClick=${() => decide('accept')}>采用</button>
      ${p.problem && p.method && p.validation && x.stage !== 'established' ? html`<button class="btn" onClick=${() => decide('accept-establish')}>采用并确立</button>` : null}
      <button class="btn" onClick=${() => openThread(`research:${x.id}`)}>继续调整（去讨论）</button>
      <button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>`;
}

function EstablishBar({ x }) {
  if (x.stage === 'established') {
    return html`<div class="establish done"><div><b>已确立</b>　${ago(x.established_at)}由你确认。之后仍可在讨论里调整、采用修改稿。</div>
      <button class="btn sm ghost" onClick=${() => act(`${enc(x.id)}/establish`, { established: false }, '已撤回确立，回到构思中')}>撤回确立</button></div>`;
  }
  const missing = ELEMENTS.filter(([k]) => !x.body?.[k]).map(([, l]) => l);
  const why = x.proposal ? '先处理上面的修改稿。' : missing.length ? `还缺${missing.join('、')}。可以自己补，也可以在讨论里和 agent 推敲后采用修改稿。` : '三要素都有了。确认无误就可以确立；还想推敲，先去讨论里调整。';
  return html`<div class="establish"><div><b>确立研究</b>　${why}</div>
    ${missing.length || x.proposal ? null : html`<button class="btn primary" onClick=${() => { if (confirm('确认确立这个研究？确立表示你认可现在的研究问题、研究方法和验证方案。')) act(`${enc(x.id)}/establish`, { established: true }, '研究已确立'); }}>确认确立</button>`}</div>`;
}

function EditForm({ x, done }) {
  const submit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = Object.fromEntries(FIELDS.map(([k]) => [k, (f.get(k) || '').trim() || null]));
    try { await post(`/api/research/${enc(x.id)}`, { title: f.get('title'), summary: f.get('summary'), body, base_rev: x.revision }); toast('已保存'); done(); refresh(); } catch (err) { fail(err); }
  };
  return html`<form class="research-form" onSubmit=${submit}>
    <input name="title" class="research-title-input" value=${x.title} placeholder="研究名称" />
    <textarea name="summary" rows="2" placeholder="一句话：做什么、为什么可能有效">${x.summary || ''}</textarea>
    ${FIELDS.map(([k, label, hint]) => html`<label>${label}<span class="hint">　${hint}</span></label><textarea name=${k} rows=${k === 'notes' ? 2 : 4}>${x.body?.[k] || ''}</textarea>`)}
    <p class="hint">可以引用论文：[arxiv:2410.24164 p.3]。保存不会改变确立状态。</p>
    <div style="display:flex;gap:8px"><button class="btn primary">保存</button><button type="button" class="btn ghost" onClick=${done}>取消</button></div></form>`;
}

function Overview({ x, edit }) {
  const run = liveRun(runsOf(x, 'draft')), drafts = runsOf(x, 'draft');
  const ready = x.stage !== 'established' && !x.proposal && ELEMENTS.every(([k]) => x.body?.[k]);
  const draft = async () => {
    try { await post(`/api/research/${enc(x.id)}/draft`); toast('已交给 agent 起草，写好后会作为修改稿等你确认'); refresh(); } catch (e) { fail(e); }
  };
  return html`<div class="tab-acts"><button class=${`btn sm ${ready || x.proposal ? '' : 'primary'}`} title="和 agent 一起推敲；它的调整会作为修改稿交给你确认" onClick=${() => openThread(`research:${x.id}`)}>在讨论里推进</button>
      <button class="btn sm ghost" disabled=${!agentOn.value || !!run} title=${agentOn.value ? '让 agent 根据素材起草三要素，作为修改稿交给你确认' : '需要先连接 agent'} onClick=${draft}>${run ? html`<span class="spin"></span>起草中` : '让 agent 起草'}</button>
      <button class="btn sm ghost" onClick=${edit}>编辑</button></div>
    ${run ? html`<div class="card-wait"><span class="spin"></span>Agent 正在起草：读素材、查库里的相关工作。写好后会作为修改稿等你确认…</div>` : null}
    ${x.proposal ? html`<${Proposal} x=${x} />` : null}
    <div class="elements">${ELEMENTS.map(([k, label, hint]) => x.body?.[k]
      ? html`<div class="element"><h3>${label}</h3><${Md} text=${x.body[k]} /></div>`
      : html`<div class="element blank" title="点一下补上" onClick=${edit}><h3>${label}</h3><div class="hint">${hint}</div></div>`)}
      ${x.body?.notes ? html`<div class="element"><h3>其他</h3><${Md} text=${x.body.notes} /></div>` : null}</div>
    <${EstablishBar} x=${x} />
    ${drafts.length ? html`<details class="runs" open=${!!run}><summary>agent 起草记录 · ${drafts.length} 次 <span class="hint">（调优：改 librarian/research.md）</span></summary>${drafts.map((r) => html`<${RunCard} r=${r} label="起草" />`)}</details>` : null}`;
}

// ---------- materials ----------

function openSource(l) {
  if (l.kind === 'paper') return openReader(l.ref);
  if (l.kind === 'message') return openThread(l.scope, l.thread_id, l.ref);
  if (l.kind === 'thread') return openThread(l.scope, l.ref);
  if (l.kind === 'capture') return openCapture(l.ref);
  if (l.kind === 'note') return l.paper_id && openReader(l.paper_id, { tab: 'notes' });
  if (l.kind === 'doc') window.open(`/api/docs/${enc(l.ref)}`, '_blank');
}

function PaperPicker({ rid }) {
  const [papers] = useLive('/api/papers', { poll: false });
  const [q, setQ] = useState('');
  const pick = async (pid) => { await post(`/api/research/${enc(rid)}/links`, { kind: 'paper', ref: pid }).catch(fail); closeModal(); toast('已关联'); refresh(); };
  const shown = (papers || []).filter((p) => p.title.toLowerCase().includes(q.toLowerCase()));
  return html`<h3>关联库里的论文</h3><input class="picker-filter" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder="按标题筛选…" autofocus />
    <div class="pick-list">${shown.map((p) => html`<div class="pick" onClick=${() => pick(p.id)}><span class="pick-title serif">${p.title}</span><span class="hint">${idLabel(p.id)}</span></div>`)}
      ${papers && !papers.length ? html`<p class="hint">库里还没有论文</p>` : null}</div>`;
}

function Materials({ x }) {
  const kindLabel = (l) => ({ paper: '论文', message: l.role === 'user' ? '你在讨论里说' : 'agent 在讨论里说', thread: '讨论', capture: '收下的内容', note: '笔记', doc: '附件' }[l.kind] || l.kind);
  const unlink = async (e, l) => { e.stopPropagation(); await post(`/api/research/${enc(x.id)}/unlink`, { kind: l.kind, ref: l.ref }).catch(fail); refresh(); };
  return html`<div class="block"><div class="block-title">素材 · ${x.links.length}<span class="line"></span><button class="btn sm ghost" onClick=${() => openModal(html`<${PaperPicker} rid=${x.id} />`)}>+ 关联库里的论文</button></div>
    ${x.links.length ? x.links.map((l) => html`<div class=${`source ${l.missing ? 'missing' : ''}`} title=${l.missing ? '' : '跳过去看'} onClick=${() => !l.missing && openSource(l)}>
        <span class="source-kind">${kindLabel(l)}</span><span class=${`source-label ${l.kind === 'paper' ? 'serif' : ''}`}>${l.missing ? '（已删除）' : short(plain(l.label), 160)}</span>
        ${l.at ? html`<time>${ago(l.at)}</time>` : null}<button class="x" title="移出素材" onClick=${(e) => unlink(e, l)}>×</button></div>`)
      : html`<div class="hint">还没有素材。在讨论、讨论消息或论文卡片上点“加入研究”，就会出现在这里，点一下能跳回去。</div>`}</div>`;
}

// ---------- code (read-only) ----------

function Commits({ id }) {
  const [r] = useLive(`/api/repos/${enc(id)}`, { poll: false });
  if (!r) return html`<span class="spin"></span>`;
  return html`<h3>${r.name} · 最近提交</h3>${(r.commits || []).map((c) => html`<div class="source"><span class="source-kind mono">${c.sha.slice(0, 7)}</span><span class="source-label">${c.subject}<span class="hint">　${c.author}</span></span><time>${ago(c.date)}</time></div>`)}
    <div style="text-align:right;margin-top:10px"><button class="btn" onClick=${closeModal}>关闭</button></div>`;
}

function Code({ x }) {
  const linked = new Set(x.repos.map((r) => r.url.toLowerCase()));
  const official = x.links.filter((l) => l.kind === 'paper' && /^https?:\/\//i.test(l.code || '') && !linked.has(l.code.replace(/\/$/, '').replace(/\.git$/, '').toLowerCase()));
  const papers = x.links.filter((l) => l.kind === 'paper' && !l.missing);
  const run = liveRun(runsOf(x, 'codemap'));
  const [mapPaper, setMapPaper] = useState({});
  const add = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), url = (f.get('url') || '').trim();
    if (!url) return;
    await act(`${enc(x.id)}/repos`, { url, role: f.get('role') }, '已关联，正在克隆');
    e.target.reset();
  };
  return html`<form class="add-line repo-add" onSubmit=${add}><input name="url" placeholder="关联代码仓库：GitHub 链接、git 地址或本机路径" />
      <select name="role"><option value="baseline">baseline</option><option value="own">自己的</option></select><button class="btn sm primary">关联</button></form>
    <p class="hint">只读：克隆到 ~/paper2brain-data/repos，只拉取，从不修改、提交或推送。私有仓库用这台 Mac 的 SSH 凭据。</p>
    ${official.length ? html`<div class="suggest">素材里的论文有官方代码：${official.map((l) => html`<button class="btn sm" onClick=${() => act(`${enc(x.id)}/repos`, { url: l.code, role: 'baseline' }, '已关联，正在克隆')}>关联 ${l.code.replace(/^https?:\/\/(www\.)?github\.com\//, '')}</button>`)}</div>` : null}
    ${run ? html`<div class="card-wait"><span class="spin"></span>Agent 正在读代码、做方法—代码对照…</div>` : null}
    ${x.repos.length ? x.repos.map((r) => html`<div class="repo"><div class="repo-head"><b class="mono">${r.name}</b><span class="chip">${r.role === 'own' ? '自己的' : 'baseline'}</span>
        ${r.status === 'cloning' ? html`<span class="chip violet"><span class="spin"></span>克隆中</span>` : r.status === 'error' ? html`<span class="chip red">出错</span>` : html`<span class="chip green">就绪</span><span class="hint">${(r.head || '').slice(0, 7)} · ${ago(r.pulled_at)}拉取</span>`}
        <span class="grow"></span>
        ${r.url.startsWith('http') ? html`<a class="btn sm ghost" href=${r.url} target="_blank">打开 ↗</a>` : null}
        <button class="btn sm ghost" disabled=${r.status !== 'ready'} onClick=${() => openModal(html`<${Commits} id=${r.id} />`)}>最近提交</button>
        <button class="btn sm ghost" onClick=${async () => { await post(`/api/repos/${enc(r.id)}/pull`).catch(fail); toast('正在拉取'); setTimeout(refresh, 1500); }}>拉取最新</button>
        <button class="btn sm ghost" onClick=${() => act(`${enc(x.id)}/repos/${enc(r.id)}/unlink`, {}, '已移除（本地克隆保留）')}>移除</button></div>
      ${r.status === 'error' ? html`<div class="hint" style="color:var(--red)">${r.error || ''}</div>` : null}
      ${r.status === 'ready' && papers.length ? html`<div class="repo-map"><span class="hint">方法—代码对照：</span>
        <select value=${mapPaper[r.id] || papers[0].ref} onChange=${(e) => setMapPaper({ ...mapPaper, [r.id]: e.target.value })}>${papers.map((p) => html`<option value=${p.ref}>${short(p.label, 60)}</option>`)}</select>
        <button class="btn sm" disabled=${!agentOn.value || !!run} onClick=${() => act(`${enc(x.id)}/codemap`, { repo_id: r.id, paper_id: mapPaper[r.id] || papers[0].ref }, '已交给 agent 读代码，完成后对照表会出现在这里')}>让 agent 对照</button></div>` : null}</div>`)
      : html`<div class="hint" style="margin-top:12px">还没有关联代码。关联 baseline 的官方代码后，agent 可以把论文里的每个组件对到具体代码，并找出论文没写的实现细节。</div>`}
    ${x.code_maps.map((m) => {
      const repo = x.repos.find((r) => r.id === m.repo_id), paper = x.links.find((l) => l.ref === m.paper_id);
      return html`<div class="block"><div class="block-title">方法—代码对照：${short(paper?.label || m.paper_id, 50)} ↔ ${repo?.name || ''} @ ${m.commit_sha.slice(0, 7)}<span class="line"></span><span class="hint">${ago(m.created_at)}</span></div>
        <table class="cmap"><thead><tr><th>组件</th><th>代码</th><th>说明</th></tr></thead><tbody>${m.rows.map((r) => {
          const url = repo && repo.url.startsWith('https://github.com/') ? `${repo.url}/blob/${m.commit_sha}/${r.file}${r.line ? `#L${r.line}` : ''}` : null;
          const loc = `${r.file}${r.line ? `:${r.line}` : ''}`;
          return html`<tr><td><b>${r.component}</b>${r.page ? html` <${Html} s=${citeHtml(m.paper_id, r.page, r.paper_quote)} />` : null}${r.symbol ? html`<div class="hint mono">${r.symbol}</div>` : null}</td>
            <td>${url ? html`<a class="mono" href=${url} target="_blank">${loc}</a>` : html`<span class="mono">${loc}</span>`}${r.snippet ? html`<details><summary class="hint">代码</summary><pre>${r.snippet}</pre></details>` : null}</td>
            <td><${Html} s=${inlineMd(r.note || '')} /></td></tr>`;
        })}</tbody></table></div>`;
    })}
    <${RunsBlock} runs=${runsOf(x, 'codemap')} label="对照" spec="librarian/code.md" />`;
}

// ---------- experiments ----------

function ExpForm({ e, done }) {
  const submit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const results = (f.get('results') || '').split('\n').map((l) => l.split(/[:：]/)).filter((p) => p.length >= 2 && p[0].trim())
      .map(([name, ...v]) => ({ name: name.trim(), value: v.join(':').trim() }));
    try {
      await post(`/api/experiments/${enc(e.id)}`, { title: f.get('title'), target: f.get('target'), setup: f.get('setup'), results,
        status: f.get('status'), conclusion: f.get('conclusion') || null, conclusion_text: f.get('conclusion_text'), base_rev: e.revision });
      toast('已保存'); done(); refresh();
    } catch (err) { fail(err); }
  };
  return html`<form class="exp-card exp-form" onSubmit=${submit}>
    <input name="title" value=${e.title || ''} placeholder="做了什么" />
    <input name="target" value=${e.target || ''} placeholder="验证三要素里的哪一点" />
    <textarea name="setup" rows="2" placeholder="设置：配置、数据、平台、commit">${e.setup || ''}</textarea>
    <textarea name="results" rows="3" placeholder="结果，每行一个：指标: 数值">${(e.results || []).map((r) => `${r.name}: ${r.value}`).join('\n')}</textarea>
    <div style="display:flex;gap:8px"><select name="status">${Object.entries(EXP_STATUS).map(([k, l]) => html`<option value=${k} selected=${e.status === k}>${l}</option>`)}</select>
      <select name="conclusion"><option value="">结论未定</option>${Object.entries(CONCLUSION).map(([k, [l]]) => html`<option value=${k} selected=${e.conclusion === k}>${l}</option>`)}</select></div>
    <textarea name="conclusion_text" rows="2" placeholder="一句话：对研究意味着什么">${e.conclusion_text || ''}</textarea>
    <div style="display:flex;gap:8px"><button class="btn sm primary">保存</button><button type="button" class="btn sm ghost" onClick=${done}>取消</button></div></form>`;
}

function ExpCard({ e }) {
  const [editing, setEditing] = useState(false);
  const run = liveRun(e.runs);
  if (editing) return html`<${ExpForm} e=${e} done=${() => setEditing(false)} />`;
  const remove = async () => { if (!confirm('删除这条实验记录？')) return; await del(`/api/experiments/${enc(e.id)}`).catch(fail); refresh(); };
  return html`<div class=${`exp-card ${e.status === 'abandoned' ? 'abandoned' : ''}`}><div class="exp-head"><b>${e.title || short(firstLine(e.raw), 60) || '未命名实验'}</b>
      <span class="chip">${EXP_STATUS[e.status]}</span>${e.conclusion ? html`<span class=${CONCLUSION[e.conclusion][1]}>${CONCLUSION[e.conclusion][0]}</span>` : null}
      ${run ? html`<span class="chip violet"><span class="spin"></span>agent 整理中</span>` : null}<span class="grow"></span><span class="hint">${ago(e.created_at)}</span></div>
    ${e.target ? html`<div class="exp-row"><span class="hint">验证</span><${Html} s=${inlineMd(e.target)} /></div>` : null}
    ${e.setup ? html`<div class="exp-row"><span class="hint">设置</span><${Html} s=${inlineMd(e.setup)} /></div>` : null}
    ${(e.results || []).length ? html`<div class="exp-row"><span class="hint">结果</span><span>${e.results.map((r, i) => html`${i ? '；' : ''}${r.name} <b class="mono">${r.value}</b>${r.note ? html`<span class="hint">（${r.note}）</span>` : null}`)}</span></div>` : null}
    ${e.conclusion_text ? html`<div class="exp-row"><span class="hint">结论</span><${Html} s=${inlineMd(e.conclusion_text)} /></div>` : null}
    ${e.raw ? html`<details><summary class="hint">原始结果</summary><pre>${e.raw}</pre></details>` : null}
    <div class="exp-acts"><button class="btn sm ghost" onClick=${() => setEditing(true)}>编辑</button>
      <button class="btn sm ghost" disabled=${!(agentOn.value && !run && e.raw && e.edited_by !== 'user')} title=${e.edited_by === 'user' ? '你改过这条记录，agent 不再改动' : ''}
        onClick=${async () => { await post(`/api/experiments/${enc(e.id)}/structure`).catch(fail); toast('已交给 agent 整理'); refresh(); }}>让 agent 整理</button>
      <button class="btn sm ghost" onClick=${remove}>删除</button></div>
    ${e.runs.length && !run && e.runs[0].status === 'ERROR' ? html`<div class="hint" style="color:var(--red)">agent 整理出错：${e.runs[0].error || ''}</div>` : null}</div>`;
}

function Experiments({ x }) {
  const exps = x.experiments;
  const [raw, setRaw] = useState('');
  const add = async (e) => {
    e.preventDefault();
    if (!raw.trim()) return;
    try { await post(`/api/research/${enc(x.id)}/experiments`, { raw: raw.trim() }); setRaw(''); toast(agentOn.value ? '已记下，agent 正在整理' : '已记下'); refresh(); } catch (err) { fail(err); }
  };
  const metrics = [...new Set(exps.flatMap((e) => (e.results || []).map((r) => r.name)))].slice(0, 8);
  return html`<form class="exp-add" onSubmit=${add}>
      <textarea rows="3" value=${raw} onInput=${(e) => setRaw(e.target.value)} placeholder="贴实验结果：评测日志、终端输出、wandb 汇总，或者一句话“LIBERO 跑完了 92.3，比 baseline 高 4 个点”"></textarea>
      <div style="display:flex;gap:8px;align-items:center"><button class="btn primary sm">记下</button>
      <span class="hint">${agentOn.value ? 'agent 会整理成结构化记录，数字必须出自你贴的原文；结论和三要素冲突时会提交修改稿。' : '连接 agent 后会自动整理；现在可以先记下，再手动编辑。'}</span></div></form>
    ${!exps.length ? html`<div class="hint" style="margin-top:12px">还没有实验。每条实验记下验证了三要素的哪一点、结果和结论；失败和放弃的也记，避免以后重踩。</div>` : html`
    ${metrics.length ? html`<div class="block"><div class="block-title">结果汇总<span class="line"></span></div><div class="table-wrap"><table class="cmap"><thead><tr><th>实验</th>${metrics.map((m) => html`<th>${m}</th>`)}<th>结论</th></tr></thead><tbody>
      ${exps.filter((e) => (e.results || []).length).map((e) => html`<tr><td>${e.title || firstLine(e.raw)}</td>${metrics.map((m) => html`<td class="mono">${(e.results.find((r) => r.name === m) || {}).value || ''}</td>`)}
        <td>${e.conclusion ? html`<span class=${CONCLUSION[e.conclusion][1]}>${CONCLUSION[e.conclusion][0]}</span>` : null}</td></tr>`)}</tbody></table></div></div>` : null}
    <div class="block"><div class="block-title">实验记录<span class="line"></span></div>${exps.map((e) => html`<${ExpCard} key=${e.id} e=${e} />`)}</div>`}`;
}

// ---------- writing ----------

const SECTIONS = [['abstract', '摘要', 'Abstract：背景、问题、核心洞见、主要结果（带数字）、意义'], ['introduction', '引言', 'Introduction：背景 → 问题 → 现有不足（引用）→ 洞见与方法 → 结果 → 贡献']];

function Writing({ x }) {
  const w = x.writing, run = liveRun(runsOf(x, 'writing'));
  const [editing, setEditing] = useState(null);
  const [ins, setIns] = useState('');
  const can = agentOn.value && !run, has = w.abstract || w.introduction;
  const draft = async (e) => {
    e.preventDefault();
    try { await post(`/api/research/${enc(x.id)}/writing/draft`, { instruction: ins }); setIns(''); toast('已交给 agent，写好后会作为修改稿等你确认'); refresh(); } catch (err) { fail(err); }
  };
  const save = async (e, k) => {
    e.preventDefault();
    try { await post(`/api/research/${enc(x.id)}/writing`, { [k]: e.target.querySelector('textarea').value, base_rev: w.revision }); setEditing(null); toast('已保存'); refresh(); } catch (err) { fail(err); }
  };
  const exportLatex = async () => {
    const out = await get(`/api/research/${enc(x.id)}/export`).catch(fail);
    if (!out) return;
    try { await navigator.clipboard.writeText(out.latex); toast('LaTeX 已复制，贴到 Overleaf 即可'); }
    catch { openModal(html`<h3>LaTeX</h3><pre>${out.latex}</pre><div style="text-align:right"><button class="btn" onClick=${closeModal}>关闭</button></div>`); }
  };
  const exportBib = async () => {
    const out = await get(`/api/research/${enc(x.id)}/export`).catch(fail);
    if (!out) return;
    Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([out.bib], { type: 'text/plain' })), download: 'references.bib' }).click();
    toast(out.bib ? 'BibTeX 已下载' : '草稿里还没有引用');
  };
  const decide = (op) => act(`${enc(x.id)}/writing/proposal`, { action: op, base_rev: op === 'accept' ? w.revision : undefined }, op === 'discard' ? '已放弃修改稿' : '已采用修改稿');
  return html`<p class="hint" style="margin-top:12px">完整论文在 Overleaf 写；这里用 Markdown 打磨摘要和引言，和 agent 反复迭代。引用写成 [arxiv:2410.24164]，导出时转成 \\cite。</p>
    <form class="add-line" onSubmit=${draft}><input value=${ins} onInput=${(e) => setIns(e.target.value)} disabled=${!can} placeholder="给 agent 的要求（可选）：比如“引言更强调 sim2real 的数据瓶颈”" />
      <button class="btn sm primary" disabled=${!can}>${run ? html`<span class="spin"></span>写作中` : has ? '让 agent 改写' : '让 agent 起草'}</button></form>
    <div class="tab-acts"><button class="btn sm" onClick=${() => openThread(`research:${x.id}`)}>在讨论里迭代</button>
      <button class="btn sm" disabled=${!has} onClick=${exportLatex}>复制 LaTeX</button><button class="btn sm" disabled=${!has} onClick=${exportBib}>下载 BibTeX</button></div>
    ${run ? html`<div class="card-wait"><span class="spin"></span>Agent 正在写：读三要素、实验记录和相关论文。写好后会作为修改稿等你确认…</div>` : null}
    ${w.proposal ? html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的写作修改稿</b><span class="hint">${ago(w.proposal.at)} · 采用后才会改动草稿</span></div>
      ${w.proposal.note ? html`<div class="hint" style="margin-bottom:6px">${w.proposal.note}</div>` : null}
      ${SECTIONS.filter(([k]) => w.proposal[k]).map(([k, l]) => html`<div class="pr-row"><div class="pr-label">${l} ${w[k] ? html`<span class="chip">修改</span>` : html`<span class="chip green">新增</span>`}</div><div class="pr-new"><${Md} text=${w.proposal[k]} /></div></div>`)}
      <div class="proposal-acts"><button class="btn primary" onClick=${() => decide('accept')}>采用</button>
        <button class="btn" onClick=${() => openThread(`research:${x.id}`)}>继续调整（去讨论）</button><button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>` : null}
    ${SECTIONS.map(([k, label, hint]) => editing === k
      ? html`<form class="element writing-form" onSubmit=${(e) => save(e, k)}><h3>${label}</h3><textarea rows="12" ref=${(el) => el?.focus()}>${w[k] || ''}</textarea>
          <div style="display:flex;gap:8px;margin-top:8px"><button class="btn sm primary">保存</button><button type="button" class="btn sm ghost" onClick=${() => setEditing(null)}>取消</button></div></form>`
      : html`<div class=${`element ${w[k] ? '' : 'blank'}`}><h3>${label}<button class="btn sm ghost" style="float:right" onClick=${() => setEditing(k)}>编辑</button></h3>${w[k] ? html`<${Md} text=${w[k]} />` : html`<div class="hint">${hint}</div>`}</div>`)}
    <${RunsBlock} runs=${runsOf(x, 'writing')} label="写作" spec="librarian/writing.md" />`;
}

// ---------- detail and list ----------

const tabOf = {};

function ResearchDetail({ id }) {
  const [x, , err] = useLive(`/api/research/${enc(id)}`);
  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState(tabOf[id] || 'overview');
  if (err) return html`<div class="empty"><b>打不开</b>${err}</div>`;
  if (!x) return html`<div class="empty"><span class="spin"></span></div>`;
  const exps = x.experiments, by = (c) => exps.filter((e) => e.conclusion === c).length;
  const remove = async () => {
    if (!confirm('删除这个研究？它的素材（论文、讨论）都会保留。')) return;
    await del(`/api/research/${enc(x.id)}`).catch(fail);
    selected.value = null;
    toast('已删除');
    refresh();
  };
  const head = html`<div class="src-bar"><${StageChip} stage=${x.stage} /><${ElementsChip} n=${x.elements_done} />
      ${exps.length ? html`<span class="chip" title="实验记录">实验 ${exps.length}（支持 ${by('supports')} · 反驳 ${by('refutes')}）</span>` : null}
      ${x.repos.length ? html`<span class="chip">仓库 ${x.repos.length}</span>` : null}${x.writing.abstract || x.writing.introduction ? html`<span class="chip">有草稿</span>` : null}
      <span>${x.stage === 'established' ? `${ago(x.established_at)}确立` : `更新于 ${ago(x.updated_at)}`}</span><span class="grow"></span>
      <button class="btn sm ghost" onClick=${remove}>删除</button></div>`;
  if (editing) return html`<div class="detail">${head}<${EditForm} x=${x} done=${() => setEditing(false)} /></div>`;
  const tabs = [['overview', '概览'], ['materials', '素材', x.links.length], ['code', '代码', x.repos.length], ['experiments', '实验', exps.length], ['writing', '写作']];
  const pick = (k) => { tabOf[id] = k; setTab(k); };
  const body = tab === 'materials' ? html`<${Materials} x=${x} />` : tab === 'code' ? html`<${Code} x=${x} />` : tab === 'experiments' ? html`<${Experiments} x=${x} />`
    : tab === 'writing' ? html`<${Writing} x=${x} />` : html`<${Overview} x=${x} edit=${() => setEditing(true)} />`;
  return html`<div class="detail">${head}
    <h1 class="research-title">${x.title}</h1>${x.summary ? html`<div class="research-summary"><${Html} s=${inlineMd(x.summary)} /></div>` : null}
    <${TopicsRow} kind="research" owner=${x} />
    <div class="rtabs">${tabs.map(([k, label, n]) => html`<button class=${`tab ${tab === k ? 'on' : ''}`} onClick=${() => pick(k)}>${label}${n ? html`<span class="n">${n}</span>` : null}</button>`)}</div>
    ${body}</div>`;
}

function ResearchItem({ x, on }) {
  return html`<div class=${`item ${on ? 'on' : ''}`} onClick=${() => (selected.value = { kind: 'research', id: x.id })}>
    <div class="item-meta"><${StageChip} stage=${x.stage} /><${ElementsChip} n=${x.elements_done} />${x.has_proposal ? html`<span class="chip violet">修改稿待确认</span>` : null}<time>${ago(x.updated_at)}</time></div>
    <div class="item-title">${x.title}</div>${x.summary ? html`<div class="item-tldr">${x.summary}</div>` : null}
    <div class="item-tags">${x.topics.map((t) => html`<span class="chip topic">${t}</span>`)}${x.link_count ? html`<span class="chip">${x.link_count} 份素材</span>` : null}
      ${x.exp_count ? html`<span class="chip">实验 ${x.exp_count}</span>` : null}${x.repo_count ? html`<span class="chip">仓库 ${x.repo_count}</span>` : null}</div></div>`;
}

export function ResearchView({ topic }) {
  const [list] = useLive('/api/research' + (topic ? `?topic=${enc(topic)}` : ''));
  const [papers] = useLive(topic ? `/api/papers?topic=${enc(topic)}` : null);
  const [name, setName] = useState('');
  const s = selected.value, sel = s?.kind === 'research' ? s.id : null;
  useEffect(() => { if (list?.length && (!sel || !list.some((x) => x.id === sel)) && innerWidth > 900) selected.value = { kind: 'research', id: list[0].id }; }, [list, sel]);
  const create = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const x = await post('/api/research', { title: name.trim(), topics: topic ? [topic] : [] });
      setName(''); toast('已开始研究（构思中）'); selected.value = { kind: 'research', id: x.id }; refresh();
    } catch (err) { fail(err); }
  };
  const title = topic ? topic.replace('/', ' / ') : '研究';
  const groups = ['established', 'forming'].map((stage) => [stage, (list || []).filter((x) => x.stage === stage)]).filter(([, xs]) => xs.length);
  const left = html`<div class="list-head"><h2>${title}</h2><span class="sub">${list?.length ? `${list.length} 个研究` : ''}</span>
      <span class="keys hint"><button class="btn sm primary" onClick=${() => openComposer({ topics: topic ? [topic] : [] })}>开始研究</button></span></div>
    ${topic ? html`<${TopicTabs} topic=${topic} on="research" papers=${papers?.length} research=${list?.length} />` : null}
    <form class="add-line research-new" onSubmit=${create}><input value=${name} onInput=${(e) => setName(e.target.value)} placeholder=${topic ? `在 ${title} 下开始研究：先写个名字，回车` : '开始研究：先写个名字，回车'} /></form>
    ${!list ? html`<div class="empty"><span class="spin"></span></div>` : !list.length ? html`<div class="empty"><b>${topic ? '这个主题下还没有研究' : '还没有研究'}</b>在讨论或论文卡片上点“加入研究”，或者在上面直接“开始研究”。<br />素材攒够了，再和 agent 一起推敲三要素：研究问题、研究方法、验证方案。</div>`
      : groups.map(([stage, xs]) => html`<div class="group-label">${stage === 'established' ? '已确立 · 三要素经你确认' : '构思中 · 汇集素材、推敲三要素'}　${xs.length}</div>${xs.map((x) => html`<${ResearchItem} key=${x.id} x=${x} on=${x.id === sel} />`)}`)}`;
  return html`<${ListDetail} hasSel=${!!sel} list=${left} detail=${sel ? html`<${ResearchDetail} key=${sel} id=${sel} />` : null} />`;
}

// ---------- 加入研究: join an existing research or start one with these materials ----------

function Picker({ ctx }) {
  const [all] = useLive('/api/research', { poll: false });
  const [q, setQ] = useState('');
  const done = (id, message) => { closeModal(); toast(message, '查看', () => openResearch(id)); refresh(); };
  const join = async (x) => {
    try { for (const l of ctx.links) await post(`/api/research/${enc(x.id)}/links`, l); done(x.id, `已作为素材加入「${x.title}」`); } catch (e) { fail(e); }
  };
  const create = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), title = (f.get('title') || '').trim();
    if (!title) { toast('给这个研究起个名字'); return; }
    try { const x = await post('/api/research', { title, summary: f.get('summary'), topics: ctx.topics || [], links: ctx.links || [] }); done(x.id, '已开始研究（构思中）'); } catch (err) { fail(err); }
  };
  const shown = (all || []).filter((x) => x.title.toLowerCase().includes(q.toLowerCase()));
  return html`<h3>${ctx.heading || '加入研究'}</h3>
    ${all?.length ? html`<p class="hint" style="margin:0 0 4px">作为素材加入已有的研究：</p><input class="picker-filter" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder="筛选…" autofocus />
      <div class="pick-list">${shown.map((x) => html`<div class="pick" onClick=${() => join(x)}><${StageChip} stage=${x.stage} /><span class="pick-title">${x.title}</span><span class="hint">${x.topics.join(' ')}</span></div>`)}</div>
      <div class="block-title" style="margin-top:16px">或者开始一个新研究<span class="line"></span></div>` : null}
    <form class="research-create" onSubmit=${create}>
      <input name="title" value=${ctx.title || ''} placeholder="研究名称（30 字以内）" />
      <textarea name="summary" rows="2" placeholder="一句话：想研究什么（可以先空着）">${ctx.summary || ''}</textarea>
      <p class="hint">新研究从<b>构思中</b>开始：先攒素材，再推敲三要素（研究问题、研究方法、验证方案），由你确认确立。${ctx.topics?.length ? `主题：${ctx.topics.join('、')}。` : '主题跟随素材里的论文，之后可以改。'}</p>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="btn" onClick=${closeModal}>取消</button><button class="btn primary">开始研究</button></div></form>`;
}
export const researchPicker = (ctx) => openModal(html`<${Picker} ctx=${ctx} />`);

// ---------- 开始研究: start one from whatever the user already has ----------

const PAPER_RE = /(?:arxiv\.org\/(?:abs|pdf|html)\/|alphaxiv\.org\/(?:abs|overview)\/|huggingface\.co\/papers\/|arxiv\s*[:：]\s*)(\d{4}\.\d{4,5})|\b(10\.\d{4,9}\/[^\s"'<>，。；)）\]]+)|openreview\.net\/(?:forum|pdf)\?id=([\w-]+)/gi;
const REPO_RE = /https?:\/\/(?:github\.com|gitlab\.com|gitee\.com|bitbucket\.org)\/[\w.-]+\/[\w.-]+/gi;
const KIND_NAME = { paper: '论文', thread: '讨论', message: '讨论消息', capture: '收下的内容' };
const fileOk = (f) => f.type === 'application/pdf' || f.type.startsWith('image/') || f.type.startsWith('text/') || /\.(md|markdown|txt)$/i.test(f.name);
const repoName = (u) => u.replace(/^https?:\/\/(www\.)?github\.com\//, '');

function detectRefs(text) {
  const papers = new Set(), repos = new Set();
  for (const m of text.matchAll(PAPER_RE)) papers.add(m[1] ? `arxiv:${m[1]}` : m[2] ? `doi:${m[2].replace(/[.,;:]+$/, '').toLowerCase()}` : `openreview:${m[3]}`);
  for (const m of text.matchAll(REPO_RE)) if (!/arxiv|openreview/.test(m[0])) repos.add(m[0].replace(/\.git$/, '').replace(/\/$/, ''));
  return { papers: [...papers], repos: [...repos] };
}

function SourcePicker({ kind, links, toggle, done }) {
  const [items] = useLive(kind === 'thread' ? '/api/threads/recent' : '/api/papers', { poll: false });
  const [q, setQ] = useState('');
  const label = (x) => (kind === 'thread' ? threadName(x) : x.title);
  const has = (id) => links.some((l) => l.kind === kind && l.ref === id);
  const shown = (items || []).filter((x) => label(x).toLowerCase().includes(q.toLowerCase()));
  return html`<div class="cmp-picker"><input class="picker-filter" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder=${`筛选${KIND_NAME[kind]}…`} autofocus />
    <div class="pick-list">${shown.map((x) => html`<label class="pick"><input type="checkbox" checked=${has(x.id)} onChange=${(e) => toggle(kind, x.id, short(label(x), 80), e.target.checked)} />
      <span class=${`pick-title ${kind === 'paper' ? 'serif' : ''}`}>${short(label(x), 90)}</span></label>`)}
      ${items && !items.length ? html`<p class="hint">还没有${KIND_NAME[kind]}</p>` : null}</div>
    <div style="text-align:right;margin-top:6px"><button class="btn sm" onClick=${done}>完成</button></div></div>`;
}

function Composer({ ctx }) {
  const [c, setC] = useState({ title: '', text: '', links: [], repos: [], files: [], topics: [], roles: {}, ...ctx });
  const [picker, setPicker] = useState(null), [drop, setDrop] = useState(false), [busy, setBusy] = useState(false);
  const [repoUrl, setRepoUrl] = useState(''), [repoRole, setRepoRole] = useState('baseline');
  const ta = useRef();
  useEffect(() => ta.current?.focus(), []);
  const set = (patch) => setC((v) => ({ ...v, ...patch }));
  const addFiles = async (files) => {
    const out = [];
    for (const f of files) {
      if (!fileOk(f)) { toast(`不支持 ${f.name}：只收 PDF、图片和文本`); continue; }
      out.push({ name: f.name, mime: f.type || 'text/markdown', data: await readBase64(f) });
    }
    setC((v) => ({ ...v, files: [...v.files, ...out] }));
  };
  const toggle = (kind, ref, label, on) => setC((v) => ({ ...v, links: on ? [...v.links, { kind, ref, label }] : v.links.filter((l) => !(l.kind === kind && l.ref === ref)) }));
  const d = detectRefs(c.text);
  const papers = d.papers.filter((p) => !c.links.some((l) => l.ref === p)), repos = d.repos.filter((u) => !c.repos.some((r) => r.url === u));
  const topics = appState.value ? appState.value.topics.flatMap((t) => [t, ...t.children]) : [];
  const submit = async (mode) => {
    let title = c.title.trim();
    if (!title && !c.text.trim()) title = short(plain(c.links[0]?.label || c.files[0]?.name || ''), 30);
    const detected = repos.map((u) => ({ url: u, role: c.roles[u] || 'baseline' }));
    setBusy(true);
    try {
      const { id } = await post('/api/research/start', { title, text: c.text, topics: c.topics, links: c.links.map(({ kind, ref }) => ({ kind, ref })), repos: [...c.repos, ...detected], files: c.files });
      closeModal();
      refresh();
      toast(agentOn.value ? '研究已开始，agent 正在读你带来的内容' : '研究已建好');
      if (mode === 'discuss') setTimeout(() => openThread(`research:${id}`), 400);
      else openItem('research', id, 'research');
    } catch (e) { setBusy(false); fail(e); }
  };
  const addRepo = (e) => {
    e.preventDefault();
    const u = repoUrl.trim();
    if (u && !c.repos.some((r) => r.url === u)) set({ repos: [...c.repos, { url: u, role: repoRole }] });
    setRepoUrl('');
  };
  return html`<div class="composer"><h3>开始一个研究</h3>
    <p class="hint" style="margin-top:0">把你已经想到的都带进来：一大段文字、构思稿或白板照片、几段讨论、几篇论文、代码仓库。agent 会先读懂、复述理解、起草三要素，再问你几个关键问题。</p>
    <input class="cmp-title" value=${c.title} onInput=${(e) => set({ title: e.target.value })} placeholder="研究名称（可选，agent 会帮你起）" />
    <div class=${`cmp-box ${drop ? 'drop' : ''}`} onDragOver=${(e) => { e.preventDefault(); e.stopPropagation(); setDrop(true); }} onDragLeave=${() => setDrop(false)}
      onDrop=${(e) => { e.preventDefault(); e.stopPropagation(); setDrop(false); addFiles([...e.dataTransfer.files]); }}>
      <textarea class="cmp-text" rows="8" ref=${ta} value=${c.text} onInput=${(e) => set({ text: e.target.value })}
        onPaste=${(e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); e.stopPropagation(); addFiles(fs); } }}
        placeholder="想研究什么、为什么、已有的思路和计划……长篇也可以。可以直接粘贴或拖入文件（PDF、Markdown、图片）。"></textarea>
      <div id="cmp-files">${c.files.map((f, i) => html`<span class="chip">${f.mime.startsWith('image/') ? '图片' : f.mime === 'application/pdf' ? 'PDF' : '文本'} · ${short(f.name, 30)}<button onClick=${() => set({ files: c.files.filter((_, j) => j !== i) })}>×</button></span>`)}</div></div>
    ${papers.length || repos.length ? html`<div class="cmp-section"><span class="hint">文字里识别到</span><div class="cmp-chips">${papers.map((p) => html`<span class="chip paper-chip">论文 · ${idLabel(p)} ✓</span>`)}
      ${repos.map((u) => html`<span class="chip">仓库 · ${repoName(u)} <button class="role-toggle" onClick=${() => set({ roles: { ...c.roles, [u]: (c.roles[u] || 'baseline') === 'own' ? 'baseline' : 'own' } })}>${(c.roles[u] || 'baseline') === 'own' ? '自己的' : 'baseline'}</button></span>`)}</div>
      <span class="hint">创建时会自动入库和克隆</span></div>` : null}
    <div class="cmp-section"><span class="hint">素材</span><div class="cmp-chips">
      ${c.links.map((l, i) => html`<span class=${`chip ${l.kind === 'paper' ? 'paper-chip' : l.kind === 'thread' ? 'amber' : 'violet'}`}>${KIND_NAME[l.kind] || l.kind} · ${short(l.label || l.ref, 34)}<button onClick=${() => set({ links: c.links.filter((_, j) => j !== i) })}>×</button></span>`)}
      ${c.repos.map((r, i) => html`<span class="chip">仓库 · ${repoName(r.url)}（${r.role === 'own' ? '自己的' : 'baseline'}）<button onClick=${() => set({ repos: c.repos.filter((_, j) => j !== i) })}>×</button></span>`)}
      ${!c.links.length && !c.repos.length ? html`<span class="hint">还没有。可以从下面添加，也可以在讨论、论文列表里多选后进来。</span>` : null}</div>
      <button class="btn sm ghost" onClick=${() => setPicker('thread')}>+ 讨论</button><button class="btn sm ghost" onClick=${() => setPicker('paper')}>+ 论文</button>
      <label class="btn sm ghost">+ 文件<input type="file" multiple hidden accept="application/pdf,image/*,text/*,.md,.markdown,.txt" onChange=${(e) => { addFiles([...e.target.files]); e.target.value = ''; }} /></label></div>
    ${picker ? html`<${SourcePicker} kind=${picker} links=${c.links} toggle=${toggle} done=${() => setPicker(null)} />` : null}
    <div class="cmp-section"><span class="hint">代码仓库</span><form class="add-line" style="flex:1;margin:0" onSubmit=${addRepo}>
      <input value=${repoUrl} onInput=${(e) => setRepoUrl(e.target.value)} placeholder="GitHub 链接、git 地址或本机路径（文字里的 GitHub 链接会自动识别）" />
      <select value=${repoRole} onChange=${(e) => setRepoRole(e.target.value)}><option value="baseline">baseline</option><option value="own">自己的</option></select><button class="btn sm">添加</button></form></div>
    <div class="cmp-section"><span class="hint">主题</span><select class="cmp-topic" value=${c.topics[0] || ''} onChange=${(e) => set({ topics: e.target.value ? [e.target.value] : [] })}>
      <option value="">跟随论文自动归类</option>${topics.map((t) => html`<option value=${t.path}>${t.path.includes('/') ? '　' : ''}${t.name}</option>`)}</select></div>
    <div class="cmp-acts"><span class="hint">${agentOn.value ? '' : 'agent 未连接：会先建好研究、关联素材，起草要等连上 agent。'}</span><span class="grow"></span>
      <button class="btn" onClick=${closeModal}>取消</button>
      <button class="btn" disabled=${busy} title="建好后进入研究页，agent 在后台起草" onClick=${() => submit('create')}>直接创建</button>
      <button class="btn primary" disabled=${busy} title="建好后进入讨论，和 agent 一起推敲" onClick=${() => submit('discuss')}>开始讨论</button></div></div>`;
}
export const openComposer = (ctx = {}) => openModal(html`<${Composer} ctx=${ctx} />`, true);
