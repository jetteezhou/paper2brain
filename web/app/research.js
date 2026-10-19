// Research: discussions and papers converging on three elements (problem, method, validation), plus code,
// experiments and writing. Only the user establishes a research; the agent's changes arrive as drafts.
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal, effect } from '@preact/signals';
import { html, get, post, del, enc, ago, short, firstLine, idLabel, plain, threadName, live, Md, mdHtml, citeHtml, readBase64, diffText, Mark } from './lib.js';
import {
  appState, agentOn, selected, useLive, refresh, toast, fail, openModal, closeModal, openItem, openReader, openThread, openCapture, openResearch, quoting,
} from './state.js';
import { ListDetail, TopicsRow, TopicTabs, RunCard, openCompare, PaperChip } from './papers.js';
import { Chat } from './discuss.js';

const STAGE = { forming: ['构思中', 'chip amber'], established: ['已确立', 'chip green-solid'] };
export const StageChip = ({ stage }) => html`<span class=${STAGE[stage][1]}>${STAGE[stage][0]}</span>`;
export const ROLE_NAME = { method: '方法借鉴', baseline: 'baseline', support: '支持证据', counter: '反面证据', reproduce: '待复现' };
export const ResearchChip = ({ x }) => html`<a href="#" class="chip research-chip" title="打开这个研究" onClick=${(e) => { e.preventDefault(); e.stopPropagation(); openResearch(x.id); }}>${STAGE[x.stage][0]} · ${short(x.title, 22)}${x.role ? ` · 作为 ${ROLE_NAME[x.role]}` : ''}</a>`;
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

/** The discussion docked beside the research, on unless the user hid it. */
const dockOn = signal(localStorage.getItem('p2b.dock') !== 'off');
const setDock = (on) => { dockOn.value = on; localStorage.setItem('p2b.dock', on ? 'on' : 'off'); };
effect(() => { if (quoting.value) setDock(true); });
// The research list starts folded: the topic rarely changes, the detail tabs need the room.
const listOn = signal(localStorage.getItem('p2b.rlist') === 'on');
const setList = (on) => { listOn.value = on; localStorage.setItem('p2b.rlist', on ? 'on' : 'off'); };
const talk = () => { setDock(true); setTimeout(() => document.querySelector('.research-dock textarea')?.focus(), 80); };

async function act(path, body, ok) {
  try { await post(`/api/research/${path}`, body); if (ok) toast(ok); } catch (e) { fail(e); }
  refresh();
}

function RunsBlock({ runs, label, spec }) {
  if (!runs.length) return null;
  return html`<details class="runs" open=${!!liveRun(runs)}><summary>${label}记录 · ${runs.length} 次 <span class="hint">（调优：改 ${spec}）</span></summary>${runs.map((r) => html`<${RunCard} r=${r} label=${label} />`)}</details>`;
}

// ---------- overview: the three elements ----------

const Diff = ({ a, b }) => html`<div class="diff">${diffText(a, b).map(([op, t]) => (op === '=' ? t : html`<${op === '+' ? 'ins' : 'del'}>${t}<//>`))}</div>`;

/** One row per changed part of a draft: what is there now against what the agent proposes, ticked to take it. */
function DraftRows({ rows, cur, p, pick, setPick }) {
  const flip = (k) => { const s = new Set(pick); s.has(k) ? s.delete(k) : s.add(k); setPick(s); };
  return rows.map(([k, l]) => html`<div class=${`pr-row ${pick.has(k) ? '' : 'off'}`}>
    <label class="pr-label"><span><input type="checkbox" checked=${pick.has(k)} onChange=${() => flip(k)} /> ${l}</span>${cur(k) ? html`<span class="chip">修改</span>` : html`<span class="chip green">新增</span>`}</label>
    <div class="pr-new quotable">${cur(k) ? html`<${Diff} a=${cur(k)} b=${p[k]} />` : html`<${Md} text=${p[k]} />`}</div></div>`);
}

function Proposal({ x }) {
  const p = x.proposal;
  const current = (k) => (k === 'title' ? x.title : k === 'summary' ? x.summary : x.body?.[k]) || '';
  const rows = [['title', '名称'], ['summary', '一句话'], ...FIELDS.map(([k, l]) => [k, l])].filter(([k]) => p[k] && p[k] !== current(k));
  const [pick, setPick] = useState(() => new Set(rows.map(([k]) => k)));
  const all = pick.size === rows.length;
  const establishable = x.stage !== 'established' && ELEMENTS.every(([k]) => (pick.has(k) && p[k]) || x.body?.[k]);
  const decide = (op) => {
    if (op === 'accept-establish' && !confirm('采用修改稿并确立这个研究？')) return;
    act(`${enc(x.id)}/proposal`, { action: op === 'discard' ? 'discard' : 'accept', establish: op === 'accept-establish', base_rev: x.revision, fields: [...pick] },
      { accept: '已采用修改稿', 'accept-establish': '已采用并确立', discard: '已放弃修改稿' }[op]);
  };
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动研究${rows.length > 1 ? ' · 可以只勾选其中几项' : ''}</span></div>
    ${rows.length ? html`<${DraftRows} rows=${rows} cur=${current} p=${p} pick=${pick} setPick=${setPick} />` : html`<div class="hint">和现在的内容一样。</div>`}
    <div class="proposal-acts"><button class="btn primary" disabled=${rows.length && !pick.size} onClick=${() => decide('accept')}>${all ? '采用' : `采用勾选的 ${pick.size} 项，其余放弃`}</button>
      ${establishable ? html`<button class="btn" onClick=${() => decide('accept-establish')}>${all ? '采用并确立' : '采用勾选的并确立'}</button>` : null}
      <button class="btn" onClick=${talk}>继续调整（去讨论）</button>
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
  return html`<div class="tab-acts"><button class=${`btn sm ${ready || x.proposal ? '' : 'primary'}`} title="和 agent 一起推敲；它的调整会作为修改稿交给你确认" onClick=${talk}>在讨论里推进</button>
      <button class="btn sm ghost" disabled=${!agentOn.value || !!run} title=${agentOn.value ? '让 agent 根据素材起草三要素，作为修改稿交给你确认' : '需要先连接 agent'} onClick=${draft}>${run ? html`<${Mark} spin />起草中` : '让 agent 起草'}</button>
      <button class="btn sm ghost" onClick=${edit}>编辑</button></div>
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在起草：读素材、查库里的相关工作。写好后会作为修改稿等你确认…</div>` : null}
    ${x.proposal ? html`<${Proposal} key=${x.proposal.at} x=${x} />` : null}
    <${Diagrams} x=${x} kind="motivation" hideRuns />
    <div class="elements">${ELEMENTS.map(([k, label, hint]) => x.body?.[k]
      ? html`<div class="element"><h3>${label}</h3><${Md} class="quotable" text=${x.body[k]} /></div>`
      : html`<div class="element blank" title="点一下补上" onClick=${edit}><h3>${label}</h3><div class="hint">${hint}</div></div>`)}
      ${x.body?.notes ? html`<div class="element"><h3>其他</h3><${Md} class="quotable" text=${x.body.notes} /></div>` : null}</div>
    <${EstablishBar} x=${x} />
    <${RunsBlock} runs=${runsOf(x, 'motivation')} label="画动机图" spec="librarian/diagram.md" />
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
  return html`<h3>关联库里的论文</h3><input class="picker-filter" type="search" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder="按标题筛选…" autofocus />
    <div class="pick-list">${shown.map((p) => html`<div class="pick" onClick=${() => pick(p.id)}><span class="pick-title serif">${p.title}</span><span class="hint">${idLabel(p.id)}</span></div>`)}
      ${papers && !papers.length ? html`<p class="hint">库里还没有论文</p>` : null}</div>`;
}

/** In the research's own words, only one per paper; the user sets it. */
export const RoleSelect = ({ value, onChange }) => html`<select class="role-select" value=${value || ''} title="这篇在研究里的用途" onClick=${(e) => e.stopPropagation()} onChange=${(e) => onChange(e.target.value)}>
  <option value="">用途…</option>${Object.entries(ROLE_NAME).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>`;

function FindSeeds({ x, seeds }) {
  const [text, setText] = useState(''), [busy, setBusy] = useState(false);
  const go = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { const c = await post(`/api/research/${enc(x.id)}/find`, { text }); closeModal(); toast('已交给 agent 去找，这一轮会出现在素材里'); refresh(); openCapture(c.id); } catch (err) { setBusy(false); fail(err); }
  };
  return html`<h3>以这些论文为种子找更多</h3>
    <p class="hint" style="margin-top:0">agent 从种子的共同方向出发找；之前几轮里你点过“不对路？”的论文会被避开。觉得对路的论文加入研究，就成了下一轮的种子。</p>
    <div class="pick-list">${seeds.map((l) => html`<div class="pick"><span class="pick-title serif">${short(l.label, 90)}</span>${l.role ? html`<span class="chip">${ROLE_NAME[l.role]}</span>` : null}</div>`)}</div>
    <form class="research-create" style="margin-top:10px" onSubmit=${go}><textarea rows="2" value=${text} onInput=${(e) => setText(e.target.value)} placeholder="补充要求（可选）：比如“要有真机实验”“2024 年以后”“换一条技术路线”"></textarea>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="btn" onClick=${closeModal}>取消</button><button class="btn primary" disabled=${busy || !agentOn.value}>${agentOn.value ? '开始找' : '需要先连接 agent'}</button></div></form>`;
}

function Materials({ x }) {
  const kindLabel = (l) => ({ paper: '论文', message: l.role === 'user' ? '你在讨论里说' : 'agent 在讨论里说', thread: '讨论', capture: '收下的内容', note: '笔记', doc: '附件' }[l.kind] || l.kind);
  const unlink = async (e, l) => { e.stopPropagation(); await post(`/api/research/${enc(x.id)}/unlink`, { kind: l.kind, ref: l.ref }).catch(fail); refresh(); };
  const setRole = (l, role) => act(`${enc(x.id)}/links`, { kind: 'paper', ref: l.ref, role }, role ? `已标为${ROLE_NAME[role]}` : '已清除用途');
  const papers = x.links.filter((l) => l.kind === 'paper' && !l.missing);
  return html`<div class="block"><div class="block-title">素材 · ${x.links.length}<span class="line"></span>
      ${papers.length ? html`<button class="btn sm ghost" title="ResearchRabbit 式：用这些论文表达方向，逐轮修正" onClick=${() => openModal(html`<${FindSeeds} x=${x} seeds=${papers} />`)}>以论文为种子找更多</button>` : null}
      ${papers.length > 1 ? html`<button class="btn sm ghost" onClick=${() => openCompare(papers.map((l) => l.ref))}>对比论文</button>` : null}
      <button class="btn sm ghost" onClick=${() => openModal(html`<${PaperPicker} rid=${x.id} />`)}>+ 关联库里的论文</button></div>
    ${papers.length ? html`<p class="hint" style="margin:0 0 6px">给论文标上用途（baseline、方法借鉴、反面证据……），agent 起草三要素时会按用途使用它们。</p>` : null}
    ${x.links.length ? x.links.map((l) => html`<div class=${`source ${l.missing ? 'missing' : ''}`} title=${l.missing ? '' : '跳过去看'} onClick=${() => !l.missing && openSource(l)}>
        <span class="source-kind">${kindLabel(l)}</span><span class=${`source-label ${l.kind === 'paper' ? 'serif' : ''}`}>${l.missing ? '（已删除）' : short(plain(l.label), 160)}</span>
        ${l.kind === 'paper' && !l.missing ? html`<${RoleSelect} value=${l.role} onChange=${(r) => setRole(l, r)} />` : null}
        ${l.at ? html`<time>${ago(l.at)}</time>` : null}<button class="x" title="移出素材" onClick=${(e) => unlink(e, l)}>×</button></div>`)
      : html`<div class="hint">还没有素材。在讨论、讨论消息或论文卡片上点“加入研究”，就会出现在这里，点一下能跳回去。</div>`}</div>`;
}

// ---------- related work: lines of work, each a paragraph of the paper's Related Work ----------

const linkTitles = (x) => Object.fromEntries(x.links.filter((l) => l.kind === 'paper').map((l) => [l.ref, l.label]));

function Comparison({ t, title }) {
  return html`<div class="cmp-wrap"><table class="cmp"><thead><tr><th>工作</th>${t.columns.map((c) => html`<th>${c}</th>`)}</tr></thead>
    <tbody>${t.rows.map((r) => html`<tr class=${r.paper === 'ours' ? 'ours' : ''}>
      <td>${r.paper === 'ours' ? html`<b>${r.name || '我们'}</b>` : html`${r.name ? html`<b>${r.name}</b><br />` : null}<${PaperChip} id=${r.paper} title=${title[r.paper] || idLabel(r.paper)} />`}</td>
      ${r.cells.map((c) => html`<td>${c}</td>`)}</tr>`)}</tbody></table></div>`;
}

function RelatedProposal({ x }) {
  const r = x.related, p = r.proposal, cur = Object.fromEntries(r.groups.map((g) => [g.id, g]));
  const kept = new Set(p.groups.map((g) => g.id));
  const changed = (o, g) => o.name !== g.name || o.text !== g.text || o.diff !== g.diff || o.papers.join() !== g.papers.join();
  const rows = [...p.groups.map((g) => (!cur[g.id] ? ['新增', 'green', g] : changed(cur[g.id], g) ? ['修改', '', g] : null)).filter(Boolean),
    ...r.groups.filter((g) => !kept.has(g.id)).map((g) => ['删除', 'red', g])];
  const newTable = p.comparison && JSON.stringify(p.comparison) !== JSON.stringify(r.comparison) ? p.comparison : null;
  const decide = (action) => act(`${enc(x.id)}/related/proposal`, { action, base_rev: r.revision }, action === 'discard' ? '已放弃修改稿' : '已采用，方向里的论文已加入素材');
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的相关工作修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动，方向里的论文会加入素材</span></div>
    ${p.note ? html`<div class="hint" style="margin-bottom:6px">${p.note}</div>` : null}
    ${rows.length ? rows.map(([k, cls, g]) => html`<div class="pr-row"><label class="pr-label"><span><b>${g.name}</b>${k === '修改' && cur[g.id].name !== g.name ? html`<span class="hint">（原：${cur[g.id].name}）</span>` : null}</span><span class=${`chip ${cls}`}>${k}</span></label>
        ${k === '删除' ? null : html`<div class="pr-new quotable">${k === '修改' ? html`<${Diff} a=${cur[g.id].text || ''} b=${g.text || ''} />` : html`<${Md} text=${g.text || ''} />`}
          ${g.diff ? html`<div class="hint">我们的区别：${g.diff}</div>` : null}<div class="hint">${g.papers.length} 篇论文</div></div>`}</div>`)
      : newTable ? null : html`<div class="hint">和现在的一样。</div>`}
    ${newTable ? html`<div class="pr-row"><label class="pr-label"><b>对比表</b><span class="chip">${r.comparison ? '修改' : '新增'}</span></label>
      <${Comparison} t=${newTable} title=${linkTitles(x)} /></div>` : null}
    <div class="proposal-acts"><button class="btn primary" onClick=${() => decide('accept')}>采用</button>
      <button class="btn" onClick=${talk}>继续调整（在右侧讨论里说）</button><button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>`;
}

function GroupForm({ g, done, save }) {
  const submit = (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    save({ ...g, name: f.get('name').trim(), text: f.get('text').trim() || null, diff: f.get('diff').trim() || null }).then((ok) => ok && done());
  };
  return html`<form class="element writing-form" onSubmit=${submit}><input name="name" class="task-title" value=${g.name} placeholder="方向名" aria-label="方向名" />
    <textarea name="text" rows="6" placeholder="写进论文 Related Work 的一段，引用写成 [arxiv:2410.24164]">${g.text || ''}</textarea>
    <input name="diff" class="task-title" value=${g.diff || ''} placeholder="我们和这条线的区别，一句话" aria-label="我们的区别" />
    <div style="display:flex;gap:8px;margin-top:8px"><button class="btn sm primary">保存</button><button type="button" class="btn sm ghost" onClick=${done}>取消</button></div></form>`;
}

function RelatedWork({ x }) {
  const r = x.related, run = liveRun(runsOf(x, 'related'));
  const [editing, setEditing] = useState(null);
  const title = linkTitles(x);
  const save = async (groups) => {
    try { await post(`/api/research/${enc(x.id)}/related`, { groups, base_rev: r.revision }); refresh(); return true; } catch (e) { fail(e); return false; }
  };
  const draft = async () => {
    try { await post(`/api/research/${enc(x.id)}/related/draft`); toast('已交给 agent 梳理，写好后会作为修改稿等你确认'); refresh(); } catch (e) { fail(e); }
  };
  return html`<div class="block"><div class="block-title">相关工作 · ${r.groups.length} 条方向<span class="line"></span>
      <button class="btn sm ghost" disabled=${!agentOn.value || !!run} title=${agentOn.value ? '按三要素和素材分出研究方向，缺的论文会补找入库' : '需要先连接 agent'} onClick=${draft}>
        ${run ? html`<${Mark} spin />梳理中` : r.groups.length ? '让 agent 更新' : '让 agent 梳理'}</button></div>
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在梳理相关工作：读三要素和素材，补找最接近的工作…</div>` : null}
    ${r.proposal ? html`<${RelatedProposal} key=${r.proposal.at} x=${x} />` : null}
    ${r.comparison ? html`<div class="element"><h3>和最接近的工作对比</h3><${Comparison} t=${r.comparison} title=${title} /></div>` : null}
    ${r.groups.map((g) => editing === g.id ? html`<${GroupForm} key=${g.id} g=${g} done=${() => setEditing(null)} save=${(ng) => save(r.groups.map((o) => (o.id === g.id ? ng : o)))} />`
      : html`<div class="element" key=${g.id}><h3>${g.name}<span style="float:right"><button class="btn sm ghost" onClick=${() => setEditing(g.id)}>编辑</button>
          <button class="btn sm ghost" onClick=${() => confirm(`删除方向「${g.name}」？论文仍留在素材里。`) && save(r.groups.filter((o) => o.id !== g.id))}>删除</button></span></h3>
        <div class="scope-papers">${g.papers.map((pid) => html`<${PaperChip} id=${pid} title=${title[pid] || idLabel(pid)} />`)}</div>
        ${g.text ? html`<${Md} class="quotable" text=${g.text} />` : null}${g.diff ? html`<div class="relevance"><b>我们的区别</b>　<span class="quotable">${g.diff}</span></div>` : null}</div>`)}
    ${!r.groups.length && !r.proposal ? html`<div class="hint">还没有梳理。agent 会把这个领域分成几条研究方向，每条写清做到哪、局限在哪、我们差在哪；它们也是论文 Related Work 一节的段落（写作页可见、随 LaTeX 导出）。</div>` : null}
    <${RunsBlock} runs=${runsOf(x, 'related')} label="梳理相关工作" spec="librarian/related.md" /></div>`;
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
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在读代码、做方法—代码对照…</div>` : null}
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
        <table class="cmap"><thead><tr><th>组件</th><th>代码</th><th>说明</th></tr></thead><tbody class="quotable">${m.rows.map((r) => {
          const url = repo && repo.url.startsWith('https://github.com/') ? `${repo.url}/blob/${m.commit_sha}/${r.file}${r.line ? `#L${r.line}` : ''}` : null;
          const loc = `${r.file}${r.line ? `:${r.line}` : ''}`;
          return html`<tr><td><b>${r.component}</b>${r.page ? html` <${Html} s=${citeHtml(m.paper_id, r.page, r.paper_quote)} />` : null}${r.symbol ? html`<div class="hint mono">${r.symbol}</div>` : null}</td>
            <td>${url ? html`<a class="mono" href=${url} target="_blank">${loc}</a>` : html`<span class="mono">${loc}</span>`}${r.snippet ? html`<details><summary class="hint">代码</summary><pre>${r.snippet}</pre></details>` : null}</td>
            <td><${Html} s=${inlineMd(r.note || '')} /></td></tr>`;
        })}</tbody></table></div>`;
    })}
    <${RunsBlock} runs=${runsOf(x, 'codemap')} label="对照" spec="librarian/code.md" />
    <${Diagrams} x=${x} />`;
}

// The two kinds of paper illustration: [title, run kind, placeholder for a new one, placeholder for a change, waiting, empty]
const DIAGRAM = {
  motivation: ['动机图', 'motivation', '想画什么（可选）：baseline 在哪一步失败、和我们的对比、要突出的数字……', '要怎么改：例如失败场景更具体、洞见那句再短些、加上成功率对比',
    'Agent 正在画动机图：读研究问题和相关工作、渲染、看图、反复修改…', 'agent 按研究问题和核心洞见画论文的 Figure 1：现有方法在哪失败、我们的不同在哪，一张图讲清楚。'],
  method: ['方法图', 'diagram', '想画什么（可选）：整体框架图、动作头的结构、和 baseline 的结构对比……', '要怎么改：例如突出动作头、换成单栏、箭头标上 token 维度',
    'Agent 正在画图：读方法和代码、渲染、看图、反复修改…', 'agent 按研究方法和代码画论文的框架图。'],
};

function Diagrams({ x, kind = 'method', hideRuns = false }) {
  const [title, runKind, newHint, editHint, waiting, empty] = DIAGRAM[kind];
  const run = liveRun(runsOf(x, runKind)), mine = x.diagrams.filter((d) => (d.kind || 'method') === kind);
  const [ins, setIns] = useState(''), [target, setTarget] = useState('');
  const url = (d, f) => `/api/research/${enc(x.id)}/diagrams/${d.name}/${f}`;
  const draw = async (e) => {
    e.preventDefault();
    try { await post(`/api/research/${enc(x.id)}/diagrams`, { instruction: ins, name: target || null, kind }); setIns(''); toast('已交给 agent 画图，画好会出现在这里'); refresh(); } catch (err) { fail(err); }
  };
  const remove = async (d) => {
    if (!confirm(`删除「${d.title || d.name}」？`)) return;
    try { await del(`/api/research/${enc(x.id)}/diagrams/${d.name}`); toast('已删除'); } catch (err) { fail(err); }
    refresh();
  };
  return html`<div class="block"><div class="block-title">${title}<span class="line"></span></div>
    <form class="add-line" onSubmit=${draw}>
      ${mine.length ? html`<select value=${target} onChange=${(e) => setTarget(e.target.value)}><option value="">画新图</option>${mine.map((d) => html`<option value=${d.name}>改「${d.title || d.name}」</option>`)}</select>` : null}
      <input value=${ins} onInput=${(e) => setIns(e.target.value)} placeholder=${target ? editHint : newHint} />
      <button class="btn sm primary" disabled=${!agentOn.value || !!run}>${target ? '让 agent 改' : '让 agent 画'}</button></form>
    ${run ? html`<div class="card-wait"><${Mark} spin />${waiting}</div>` : null}
    ${mine.map((d) => html`<figure class="diagram" key=${d.name}>
      <a href=${url(d, 'figure.pdf')} target="_blank"><img src=${`${url(d, 'figure.png')}?v=${encodeURIComponent(d.at)}`} alt=${d.caption || d.title || d.name} /></a>
      <figcaption><b>${d.title || d.name}</b>${d.caption ? html`　<span class="quotable">${d.caption}</span>` : null}</figcaption>
      <div class="diagram-bar"><span class="hint mono">${d.name} · ${d.width}px 宽 · ${ago(d.at)}</span><span class="grow"></span>
        <a class="btn sm ghost" href=${url(d, 'figure.pdf')} download=${`${d.name}.pdf`}>PDF</a>
        <a class="btn sm ghost" href=${url(d, 'figure.png')} download=${`${d.name}.png`}>PNG</a>
        <a class="btn sm ghost" href=${url(d, 'figure.html')} download=${`${d.name}.html`}>源文件</a>
        <button class="btn sm ghost" onClick=${() => remove(d)}>删除</button></div></figure>`)}
    ${!mine.length && !run ? html`<div class="hint">${empty}HTML/SVG 渲染成矢量 PDF，agent 自己看图反复修改；PDF 可以直接放进 LaTeX，也可以在讨论里让它放进 Overleaf 论文。</div>` : null}
    ${hideRuns ? null : html`<${RunsBlock} runs=${runsOf(x, runKind)} label=${`画${title}`} spec="librarian/diagram.md" />`}</div>`;
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

let chartjs;
const loadChart = () => chartjs ||= import('/vendor/chartjs/chart.umd.min.js').then(() => {
  const css = getComputedStyle(document.documentElement), v = (k) => css.getPropertyValue(k).trim();
  const d = globalThis.Chart.defaults; // not d.borderColor: changing it turns off the default palette
  d.color = v('--muted'); d.scale.grid.color = v('--line'); d.font.family = v('--sans');
  return globalThis.Chart;
});

function ExpChart({ c }) {
  const ref = useRef();
  useEffect(() => {
    let chart, gone = false;
    loadChart().then((Chart) => {
      if (gone) return;
      chart = new Chart(ref.current, { type: c.type, data: structuredClone(c.data),
        options: { maintainAspectRatio: false, ...c.options, plugins: { ...c.options?.plugins, title: { display: !!c.title, text: c.title || '' } } } });
    }).catch(fail);
    return () => { gone = true; chart?.destroy(); };
  }, [JSON.stringify(c)]);
  return html`<div class="exp-chart"><canvas ref=${ref} role="img" aria-label=${c.title || '实验图表'}></canvas></div>`;
}

function ExpCard({ e }) {
  const [editing, setEditing] = useState(false);
  const run = liveRun(e.runs);
  if (editing) return html`<${ExpForm} e=${e} done=${() => setEditing(false)} />`;
  const remove = async () => { if (!confirm('删除这条实验记录？')) return; await del(`/api/experiments/${enc(e.id)}`).catch(fail); refresh(); };
  return html`<div class=${`exp-card ${e.status === 'abandoned' ? 'abandoned' : ''}`}><div class="exp-head"><b>${e.title || short(firstLine(e.raw), 60) || '未命名实验'}</b>
      <span class="chip">${EXP_STATUS[e.status]}</span>${e.conclusion ? html`<span class=${CONCLUSION[e.conclusion][1]}>${CONCLUSION[e.conclusion][0]}</span>` : null}
      ${run ? html`<span class="chip violet"><${Mark} spin />agent 整理中</span>` : null}<span class="grow"></span><span class="hint">${ago(e.created_at)}</span></div>
    ${e.target ? html`<div class="exp-row"><span class="hint">验证</span><${Html} cls="quotable" s=${inlineMd(e.target)} /></div>` : null}
    ${e.setup ? html`<div class="exp-row"><span class="hint">设置</span><${Html} cls="quotable" s=${inlineMd(e.setup)} /></div>` : null}
    ${(e.results || []).length ? html`<div class="exp-row"><span class="hint">结果</span><span class="quotable">${e.results.map((r, i) => html`${i ? '；' : ''}${r.name} <b class="mono">${r.value}</b>${r.note ? html`<span class="hint">（${r.note}）</span>` : null}`)}</span></div>` : null}
    ${(e.charts || []).length ? html`<div class="exp-charts">${e.charts.map((c, i) => html`<${ExpChart} key=${i} c=${c} />`)}</div>` : null}
    ${e.conclusion_text ? html`<div class="exp-row"><span class="hint">结论</span><${Html} cls="quotable" s=${inlineMd(e.conclusion_text)} /></div>` : null}
    ${e.raw ? html`<details><summary class="hint">原始结果</summary><pre class="quotable">${e.raw}</pre></details>` : null}
    <div class="exp-acts"><button class="btn sm ghost" onClick=${() => setEditing(true)}>编辑</button>
      <button class="btn sm ghost" disabled=${!(agentOn.value && !run && e.raw && e.edited_by !== 'user')} title=${e.edited_by === 'user' ? '你改过这条记录，agent 不再改动' : ''}
        onClick=${async () => { await post(`/api/experiments/${enc(e.id)}/structure`).catch(fail); toast('已交给 agent 整理'); refresh(); }}>让 agent 整理</button>
      <button class="btn sm ghost" onClick=${remove}>删除</button></div>
    ${e.runs.length && !run && e.runs[0].status === 'ERROR' ? html`<div class="hint" style="color:var(--red)">agent 整理出错：${e.runs[0].error || ''}</div>` : null}</div>`;
}

// ---------- experiment design: the paper's Experiments section, planned before the runs, filled by their results ----------

const FIG_KIND = { table: '表', bar: '柱状图', line: '折线图', radar: '雷达图' };
const FIG_SECTION = { main: '主结果', ablation: '消融实验', analysis: '分析', appendix: '附录' };
const num = (v) => { const n = parseFloat(String(v).replace(/,/g, '')); return Number.isFinite(n) ? n : null; };
const cellKey = (r, s) => `${r}\u0000${s}`;
const metricOf = (d, name) => d?.metrics.find((m) => m.name === name);
const arrow = (m) => (m ? (m.better === 'lower' ? '↓' : '↑') : '');
const metricLabel = (m) => (m ? `${m.name}${m.unit ? ` (${m.unit})` : ''} ${arrow(m)}` : '');

/** Paper order (by section, then as listed), Tables and Figures numbered separately as LaTeX does. */
function numbered(figs) {
  const order = Object.keys(FIG_SECTION), n = { Table: 0, Figure: 0 };
  return [...figs].sort((a, b) => order.indexOf(a.section) - order.indexOf(b.section))
    .map((f) => { const k = f.kind === 'table' ? 'Table' : 'Figure'; n[k] += 1; return [f, `${k} ${n[k]}`]; });
}

function DesignView({ d }) {
  return html`<div class="exp-card design">
    <div class="exp-row"><span class="hint">问题</span><ol class="quotable">${d.questions.map((q) => html`<li>${q}</li>`)}</ol></div>
    <div class="exp-row"><span class="hint">基准</span><span class="quotable">${d.benchmarks.map((b, i) => html`${i ? '；' : ''}<b>${b.name}</b>${b.note ? html`<span class="hint">（${b.note}）</span>` : null}`)}</span></div>
    <div class="exp-row"><span class="hint">对比</span><span class="quotable"><b>${d.method}</b>（我们）vs ${d.baselines.map((b, i) => html`${i ? '、' : ''}<span title=${[b.paper, b.note].filter(Boolean).join(' · ')}>${b.name}</span>`)}</span></div>
    ${d.controls?.length ? html`<div class="exp-row"><span class="hint">对照</span><span class="quotable">${d.controls.map((b, i) => html`${i ? '、' : ''}<span title=${b.note || ''}>${b.name}</span>`)}<span class="hint">（自己方法的变体与简单对照）</span></span></div>` : null}
    <div class="exp-row"><span class="hint">指标</span><span class="quotable">${d.metrics.map(metricLabel).join('；')}</span></div>
    ${d.implementation ? html`<div class="exp-row"><span class="hint">实现</span><${Html} cls="quotable" s=${inlineMd(d.implementation)} /></div>` : null}</div>`;
}

function Sections({ x, d, figs, remove }) {
  const list = numbered(figs);
  return Object.entries(FIG_SECTION).map(([k, name]) => {
    const fs = list.filter(([f]) => f.section === k);
    return fs.length ? html`<div class="fig-section"><div class="fig-section-title">${name}</div><div class="fig-grid">
      ${fs.map(([f, label]) => html`<${FigureCard} key=${f.id} x=${x} f=${f} d=${d} label=${label} remove=${remove && (() => remove(f))} />`)}</div></div>` : null;
  });
}

/** Each filled cell with the experiment result it points at; a deleted or re-structured experiment leaves its cell empty. */
function cellsOf(x, f) {
  const exps = Object.fromEntries(x.experiments.map((e) => [e.id, e])), out = {};
  for (const c of x.figures.cells) {
    const e = c.figure === f.id && exps[c.experiment], r = e && (e.results || []).find((r) => r.name === c.result);
    if (r) out[cellKey(c.row, c.series)] = { ...r, e };
  }
  return out;
}

function FigureCard({ x, f, d, label, remove }) {
  const cells = cellsOf(x, f), at = (r, s) => cells[cellKey(r, s)];
  const n = Object.keys(cells).length, total = f.rows.length * f.series.length;
  const sources = [...new Set(Object.values(cells).map(({ e }) => e.title || short(firstLine(e.raw), 30)))];
  const colMetric = (s) => metricOf(d, f.metric || s);
  const best = Object.fromEntries(f.series.map((s) => {
    const m = colMetric(s), vals = f.rows.map((r) => at(r, s) && num(at(r, s).value)).filter((v) => v != null);
    return [s, m && vals.length > 1 ? (m.better === 'lower' ? Math.min : Math.max)(...vals) : null];
  }));
  const axis = (t) => ({ title: { display: !!t, text: t || '' } });
  const chart = f.kind !== 'table' && { type: f.kind, data: { labels: f.rows, datasets: [
    ...f.series.map((s) => ({ label: s, data: f.rows.map((r) => (at(r, s) ? num(at(r, s).value) : null)), spanGaps: true })),
    ...(f.target ? [{ ...(f.kind === 'bar' ? { type: 'line' } : {}), label: f.target.label || `目标 ${f.target.value}`, data: f.rows.map(() => f.target.value), borderDash: [6, 4], pointRadius: 0, fill: false }] : [])] },
    options: f.kind === 'radar' ? {} : { scales: { x: axis(f.x_title), y: axis(f.y_title || metricLabel(metricOf(d, f.metric))) } } };
  const caption = f.caption ? html`<div class="fig-caption quotable"><b>${label}:</b> ${f.caption}</div>` : null;
  const q = d?.questions[f.question - 1];
  return html`<div class="exp-card"><div class="exp-head"><b>${label}</b><span class="quotable">${f.title}</span>
      ${q ? html`<span class="chip" title=${q}>Q${f.question}</span>` : null}<span class="chip">${FIG_KIND[f.kind]}</span>
      <span class=${`chip ${n === total ? 'green' : ''}`} title="已经有实验结果的格子">已填 ${n}/${total}</span><span class="grow"></span>
      ${remove ? html`<span class="task-acts"><button class="x" title="删除这张图表" onClick=${remove}>×</button></span>` : null}</div>
    ${f.claim ? html`<div class="hint quotable">要证明：${f.claim}</div>` : null}
    ${chart ? html`<${ExpChart} c=${chart} />${caption}` : html`${caption}<div class="table-wrap"><table class="cmap paper"><thead><tr><th>${metricLabel(metricOf(d, f.metric))}</th>
        ${f.series.map((s) => html`<th>${s}${!f.metric && colMetric(s) ? ` ${arrow(colMetric(s))}` : ''}</th>`)}</tr></thead>
      <tbody>${f.rows.map((r) => html`<tr class=${r === d?.method ? 'ours' : ''}><td>${r}</td>${f.series.map((s) => {
        const c = at(r, s), v = c && c.value;
        return html`<td class="mono" title=${c ? `${c.e.title || ''}${c.note ? ` · ${c.note}` : ''}` : '还没有实验结果'}>${!c ? html`<span class="hint">—</span>` : num(v) === best[s] ? html`<b>${v}</b>` : v}</td>`;
      })}</tr>`)}</tbody></table></div>`}
    ${f.target && !chart ? html`<div class="hint">目标：${f.target.label || ''} ${f.target.value}</div>` : null}
    <div class="hint">${sources.length ? `数字来自：${sources.join('、')}` : '还没有实验结果填进来；记下实验后 agent 会把对应的结果填进格子。'}</div></div>`;
}

function FiguresProposal({ x }) {
  const fg = x.figures, p = fg.proposal, cur = Object.fromEntries(fg.figures.map((f) => [f.id, f]));
  const kept = new Set(p.figures.map((f) => f.id));
  const names = (a, b, what) => { const add = b.filter((v) => !a.includes(v)), gone = a.filter((v) => !b.includes(v));
    return [add.length && `加${what}：${add.join('、')}`, gone.length && `删${what}：${gone.join('、')}`].filter(Boolean); };
  const diff = (o, f) => [o.title !== f.title && `改名：${o.title}`, o.section !== f.section && `${FIG_SECTION[o.section] || '未分节'} → ${FIG_SECTION[f.section]}`,
    o.kind !== f.kind && `${FIG_KIND[o.kind]} → ${FIG_KIND[f.kind]}`, o.question !== f.question && `改答 Q${f.question}`, o.metric !== f.metric && `指标改为 ${f.metric || '按列'}`,
    ...names(o.rows, f.rows, '行'), ...names(o.series, f.series, '系列'), JSON.stringify(o.target) !== JSON.stringify(f.target) && '目标线有改动',
    (o.caption || '') !== (f.caption || '') && '图注有改动', (o.claim || '') !== (f.claim || '') && '论点有改动'].filter(Boolean);
  const shape = (f) => `${FIG_SECTION[f.section]} · ${FIG_KIND[f.kind]} · ${f.rows.length} 行 × ${f.series.length} 系列${f.claim ? ` · ${f.claim}` : ''}`;
  const rows = [...p.figures.map((f) => (!cur[f.id] ? ['新增', 'green', f, []] : diff(cur[f.id], f).length ? ['修改', '', f, diff(cur[f.id], f)] : null)).filter(Boolean),
    ...fg.figures.filter((f) => !kept.has(f.id)).map((f) => ['删除', 'red', f, []])];
  const design = p.design && JSON.stringify(p.design) !== JSON.stringify(fg.design);
  const decide = async (action) => {
    try {
      const r = await post(`/api/research/${enc(x.id)}/figures/proposal`, { action, base_rev: fg.revision });
      toast(action === 'discard' ? '已放弃修改稿' : r.run_id ? '已采用，agent 正在把已有实验结果填进去' : '已采用实验设计');
    } catch (e) { fail(e); }
    refresh();
  };
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的实验设计修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动实验设计</span></div>
    ${p.note ? html`<div class="hint" style="margin-bottom:6px">${p.note}</div>` : null}
    ${design ? html`<div class="task-diff"><span class=${`chip ${fg.design ? '' : 'green'}`}>${fg.design ? '修改' : '新增'}</span><span>实验设置</span>
      <span class="hint">${p.design.questions.length} 个研究问题 · ${p.design.benchmarks.length} 个基准 · ${p.design.baselines.length} 个对比方法 · ${p.design.metrics.length} 个指标</span></div>` : null}
    ${rows.length ? rows.map(([k, cls, f, why]) => html`<div class="task-diff"><span class=${`chip ${cls}`}>${k}</span><span class=${`quotable ${k === '删除' ? 'struck' : ''}`}>${f.title}</span>
        <span class="hint">${why.length ? why.join('；') : shape(f)}</span></div>`) : design ? null : html`<div class="hint">和现在的实验设计一样。</div>`}
    ${design || rows.length ? html`<details><summary class="hint">预览采用后的实验部分</summary>${p.design ? html`<${DesignView} d=${p.design} />` : null}
      <${Sections} x=${x} d=${p.design || fg.design} figs=${p.figures} /></details>` : null}
    <div class="proposal-acts"><button class="btn primary" onClick=${() => decide('accept')}>采用</button>
      <button class="btn" onClick=${talk}>继续调整（去讨论）</button><button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>`;
}

function Figures({ x }) {
  const fg = x.figures, run = liveRun(runsOf(x, 'figures'));
  const [ins, setIns] = useState('');
  const draft = async (e) => {
    e.preventDefault();
    try { await post(`/api/research/${enc(x.id)}/figures/draft`, { instruction: ins }); setIns(''); toast('已交给 agent，设计好后会作为修改稿等你确认'); refresh(); } catch (err) { fail(err); }
  };
  const remove = (f) => confirm(`删除图表「${f.title}」？已填的格子会一起清掉，实验记录不受影响。`)
    && act(`${enc(x.id)}/figures`, { figures: fg.figures.filter((g) => g.id !== f.id), base_rev: fg.revision }, '已删除');
  return html`<div class="block"><div class="block-title">实验设计<span class="hint">　按论文 Experiments 一节组织：研究问题 → 实验设置 → 主结果 → 消融 → 分析</span><span class="line"></span></div>
    <form class="add-line" onSubmit=${draft}><input value=${ins} onInput=${(e) => setIns(e.target.value)} disabled=${!agentOn.value || !!run}
        placeholder=${agentOn.value ? '给 agent 的要求（可选）：比如“主表加上 OpenVLA-OFT”“加一张数据量的消融”' : '需要先连接 agent'} />
      <button class="btn sm primary" disabled=${!agentOn.value || !!run}>${run ? html`<${Mark} spin />处理中` : fg.design ? '让 agent 更新实验设计' : '让 agent 设计实验'}</button></form>
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在处理实验设计：定实验设置和图表，或把实验结果填进格子…</div>` : null}
    ${fg.proposal ? html`<${FiguresProposal} key=${fg.proposal.at} x=${x} />` : null}
    ${fg.design ? html`<${DesignView} d=${fg.design} />` : null}
    ${fg.figures.length ? html`<${Sections} x=${x} d=${fg.design} figs=${fg.figures} remove=${remove} />`
      : fg.proposal ? null : html`<div class="hint">还没有实验设计。先按论文的样子定下要回答的研究问题、基准、对比方法和指标，以及主结果、消融、分析各放哪些图表，再去跑实验：每个格子就是一个要跑的实验，跑完的结果会被填进来。</div>`}
    <${RunsBlock} runs=${runsOf(x, 'figures')} label="实验设计" spec="librarian/experiment.md" /></div>`;
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
      <span class="hint">${agentOn.value ? 'agent 会整理成结构化记录，数字必须出自你贴的原文，并把结果填进实验设计的图表；结论和三要素冲突时会提交修改稿。' : '连接 agent 后会自动整理；现在可以先记下，再手动编辑。'}</span></div></form>
    <${Figures} x=${x} />
    ${!exps.length ? html`<div class="hint" style="margin-top:12px">还没有实验。每条实验记下验证了三要素的哪一点、结果和结论；失败和放弃的也记，避免以后重踩。</div>` : html`
    ${metrics.length ? html`<div class="block"><div class="block-title">结果汇总<span class="line"></span></div><div class="table-wrap"><table class="cmap"><thead><tr><th>实验</th>${metrics.map((m) => html`<th>${m}</th>`)}<th>结论</th></tr></thead><tbody>
      ${exps.filter((e) => (e.results || []).length).map((e) => html`<tr><td>${e.title || firstLine(e.raw)}</td>${metrics.map((m) => html`<td class="mono">${(e.results.find((r) => r.name === m) || {}).value || ''}</td>`)}
        <td>${e.conclusion ? html`<span class=${CONCLUSION[e.conclusion][1]}>${CONCLUSION[e.conclusion][0]}</span>` : null}</td></tr>`)}</tbody></table></div></div>` : null}
    <div class="block"><div class="block-title">实验记录<span class="line"></span></div>${exps.map((e) => html`<${ExpCard} key=${e.id} e=${e} />`)}</div>`}`;
}

// ---------- plan ----------

const TASK_STATUS = { todo: '待办', doing: '进行中', done: '完成' };
const today = () => new Date().toLocaleDateString('sv');
const overdue = (t) => t.due && t.status !== 'done' && t.due < today();
const planStats = (tasks) => ({ done: tasks.filter((t) => t.status === 'done').length, total: tasks.length, late: tasks.filter(overdue).length });

function PlanProposal({ x }) {
  const pl = x.plan, p = pl.proposal, cur = Object.fromEntries(pl.tasks.map((t) => [t.id, t]));
  const kept = new Set(p.tasks.map((t) => t.id));
  const diff = (o, t) => [o.title !== t.title && `改名：${o.title}`, o.status !== t.status && `${TASK_STATUS[o.status]} → ${TASK_STATUS[t.status]}`,
    o.due !== t.due && `截止 ${o.due || '无'} → ${t.due || '无'}`, (o.note || '') !== (t.note || '') && '备注有改动'].filter(Boolean);
  const rows = [...p.tasks.map((t) => (!cur[t.id] ? ['新增', 'green', t, []] : diff(cur[t.id], t).length ? ['修改', '', t, diff(cur[t.id], t)] : null)).filter(Boolean),
    ...pl.tasks.filter((t) => !kept.has(t.id)).map((t) => ['删除', 'red', t, []])];
  const same = p.tasks.length - rows.filter(([k]) => k !== '删除').length;
  const decide = (action) => act(`${enc(x.id)}/plan/proposal`, { action, base_rev: pl.revision }, action === 'discard' ? '已放弃修改稿' : '已采用新计划');
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的计划修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动计划${same ? ` · 另有 ${same} 条不变` : ''}</span></div>
    ${p.note ? html`<div class="hint" style="margin-bottom:6px">${p.note}</div>` : null}
    ${rows.length ? rows.map(([k, cls, t, why]) => html`<div class="task-diff"><span class=${`chip ${cls}`}>${k}</span><span class=${`quotable ${k === '删除' ? 'struck' : ''}`}>${t.title}</span>
        <span class="hint">${why.length ? why.join('；') : [TASK_STATUS[t.status], t.due && `截止 ${t.due}`].filter(Boolean).join(' · ')}</span></div>`)
      : html`<div class="hint">和现在的计划一样。</div>`}
    <div class="proposal-acts"><button class="btn primary" onClick=${() => decide('accept')}>采用</button>
      <button class="btn" onClick=${talk}>继续调整（去讨论）</button><button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>`;
}

function Plan({ x }) {
  const pl = x.plan, tasks = pl.tasks, run = liveRun(runsOf(x, 'plan')), s = planStats(tasks);
  const [title, setTitle] = useState(''), [due, setDue] = useState(''), [ins, setIns] = useState('');
  const save = async (next) => { try { await post(`/api/research/${enc(x.id)}/plan`, { tasks: next, base_rev: pl.revision }); } catch (e) { fail(e); } refresh(); };
  const patch = (id, f) => save(tasks.map((t) => (t.id === id ? { ...t, ...f } : t)));
  const move = (i, d) => { const n = [...tasks]; [n[i], n[i + d]] = [n[i + d], n[i]]; save(n); };
  const add = (e) => { e.preventDefault(); if (!title.trim()) return; save([...tasks, { title: title.trim(), due: due || null, status: 'todo' }]); setTitle(''); setDue(''); };
  const draft = async (e) => {
    e.preventDefault();
    try { await post(`/api/research/${enc(x.id)}/plan/draft`, { instruction: ins }); setIns(''); toast('已交给 agent，排好后会作为修改稿等你确认'); refresh(); } catch (err) { fail(err); }
  };
  return html`${s.total ? html`<div class="plan-progress"><div class="bar"><span style=${`width:${(100 * s.done) / s.total}%`}></span></div>
      <span>完成 ${s.done}/${s.total}</span>${s.late ? html`<span class="chip red">逾期 ${s.late}</span>` : null}</div>` : null}
    <form class="add-line" onSubmit=${draft}><input value=${ins} onInput=${(e) => setIns(e.target.value)} disabled=${!agentOn.value || !!run}
        placeholder=${agentOn.value ? '给 agent 的要求（可选）：比如“CoRL 截稿 11 月 20 日，倒排一下”“实验跑完了，更新进度”' : '需要先连接 agent'} />
      <button class="btn sm primary" disabled=${!agentOn.value || !!run}>${run ? html`<${Mark} spin />排计划中` : tasks.length ? '让 agent 更新计划' : '让 agent 制定计划'}</button></form>
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在排计划：读三要素、实验记录和写作进度。排好后会作为修改稿等你确认…</div>` : null}
    ${pl.proposal ? html`<${PlanProposal} key=${pl.proposal.at} x=${x} />` : null}
    <div class="tasks">${tasks.map((t, i) => html`<div key=${t.id} class=${`task ${t.status} ${overdue(t) ? 'late' : ''}`}>
        <select class="role-select" value=${t.status} onChange=${(e) => patch(t.id, { status: e.target.value })}>${Object.entries(TASK_STATUS).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <div class="task-body"><input class="task-title" value=${t.title} onChange=${(e) => e.target.value.trim() && patch(t.id, { title: e.target.value.trim() })} />
          ${t.note ? html`<div class="hint quotable">${t.note}</div>` : null}</div>
        <input type="date" class="task-due" value=${t.due || ''} title="截止日期" onChange=${(e) => patch(t.id, { due: e.target.value || null })} />
        <span class="task-acts"><button class="x" title="上移" disabled=${!i} onClick=${() => move(i, -1)}>↑</button><button class="x" title="下移" disabled=${i === tasks.length - 1} onClick=${() => move(i, 1)}>↓</button>
          <button class="x" title="删除" onClick=${() => confirm(`删除任务「${t.title}」？`) && save(tasks.filter((u) => u.id !== t.id))}>×</button></span></div>`)}</div>
    <form class="add-line" onSubmit=${add}><input value=${title} onInput=${(e) => setTitle(e.target.value)} placeholder="加一条任务：做什么（一到两周能做完）" />
      <input type="date" class="task-due" value=${due} onInput=${(e) => setDue(e.target.value)} /><button class="btn sm">添加</button></form>
    ${!tasks.length && !pl.proposal ? html`<div class="hint" style="margin-top:8px">还没有计划。可以自己一条条加，也可以让 agent 根据三要素、实验和写作进度排一份，你确认后生效。在讨论里说“帮我把计划往后顺延一周”也行。</div>` : null}
    <${RunsBlock} runs=${runsOf(x, 'plan')} label="排计划" spec="librarian/plan.md" />`;
}

// ---------- writing ----------

const SECTIONS = [['abstract', '摘要', 'Abstract：背景、问题、核心洞见、主要结果（带数字）、意义'], ['introduction', '引言', 'Introduction：背景 → 问题 → 现有不足（引用）→ 洞见与方法 → 结果 → 贡献']];

function WritingProposal({ x }) {
  const w = x.writing, p = w.proposal;
  const rows = SECTIONS.filter(([k]) => p[k] && p[k] !== w[k]);
  const [pick, setPick] = useState(() => new Set(rows.map(([k]) => k)));
  const all = pick.size === rows.length;
  const decide = (op) => act(`${enc(x.id)}/writing/proposal`, { action: op, base_rev: op === 'accept' ? w.revision : undefined, fields: [...pick] }, op === 'discard' ? '已放弃修改稿' : '已采用修改稿');
  return html`<div class="proposal"><div class="proposal-head"><b>Agent 提交的写作修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动草稿</span></div>
    ${p.note ? html`<div class="hint" style="margin-bottom:6px">${p.note}</div>` : null}
    ${rows.length ? html`<${DraftRows} rows=${rows} cur=${(k) => w[k] || ''} p=${p} pick=${pick} setPick=${setPick} />` : html`<div class="hint">和现在的内容一样。</div>`}
    <div class="proposal-acts"><button class="btn primary" disabled=${rows.length && !pick.size} onClick=${() => decide('accept')}>${all ? '采用' : `采用勾选的 ${pick.size} 项，其余放弃`}</button>
      <button class="btn" onClick=${talk}>继续调整（去写作讨论）</button><button class="btn ghost" onClick=${() => decide('discard')}>放弃</button></div></div>`;
}

const viaGithub = (pid) => pid.startsWith('github:');

function OverleafChanges({ x }) {
  const [c, reload] = useLive(`/api/research/${enc(x.id)}/overleaf`);
  const gh = viaGithub(x.writing.overleaf), dest = gh ? 'GitHub' : 'Overleaf';
  const [msg, setMsg] = useState(''), [busy, setBusy] = useState(false);
  const run = async (op, body, ok) => {
    setBusy(true);
    try { await post(`/api/research/${enc(x.id)}/overleaf/${op}`, body); toast(ok); if (op === 'push') setMsg(''); } catch (e) { fail(e); }
    setBusy(false); reload(); refresh();
  };
  if (!c) return html`<span class="spin"></span>`;
  if (!c.files.length) return html`<div class="hint">没有未推送的改动。在讨论里让 agent 改论文，改动会先出现在这里，你确认后再推送到 ${dest}。${gh ? '让 agent 动手前，先在 Overleaf 里把改动推到 GitHub（Menu → GitHub → Push Overleaf changes to GitHub），再点上面的“拉取最新”。' : ''}</div>`;
  return html`<div class="proposal"><div class="proposal-head"><b>未推送的改动 · ${c.files.length} 个文件</b><span class="hint">推送前 ${dest} 上的论文不会变</span></div>
    <div class="cmp-chips">${c.files.map((f) => html`<span class="chip mono">${f.status} ${f.file}</span>`)}</div>
    <pre class="ol-diff">${c.diff.split('\n').map((l) => html`<div class=${/^\+(?!\+\+)/.test(l) ? 'add' : /^-(?!--)/.test(l) ? 'del' : /^@@/.test(l) ? 'hunk' : ''}>${l || ' '}</div>`)}</pre>
    <form class="add-line" onSubmit=${(e) => { e.preventDefault(); run('push', { message: msg }, gh ? '已推送到 GitHub：在 Overleaf 里 Menu → GitHub → Pull GitHub changes into Overleaf' : '已推送到 Overleaf'); }}>
      <input value=${msg} onInput=${(e) => setMsg(e.target.value)} placeholder=${`这次改了什么（可选，作为 ${dest} 的提交说明）`} />
      <button class="btn sm primary" disabled=${busy}>推送到 ${dest}</button>
      <button type="button" class="btn sm ghost" disabled=${busy} onClick=${() => confirm(`放弃所有未推送的改动？本地文件会回到 ${dest} 上的版本。`) && run('discard', {}, '已放弃改动')}>放弃改动</button></form></div>`;
}

function Overleaf({ x }) {
  const pid = x.writing.overleaf;
  const [url, setUrl] = useState(''), [busy, setBusy] = useState(false);
  const link = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await post(`/api/research/${enc(x.id)}/overleaf`, { url }); setUrl(''); toast('已关联 Overleaf 项目'); refresh(); } catch (err) { fail(err); }
    setBusy(false);
  };
  const pull = async () => { try { await post(`/api/research/${enc(x.id)}/overleaf/pull`); toast('已拉取最新'); refresh(); } catch (e) { fail(e); } };
  const unlink = () => confirm('解除关联？本地克隆保留，没推送的改动也还在。') && act(`${enc(x.id)}/overleaf/unlink`, {}, '已解除关联');
  if (!pid) {
    return html`<div class="block"><div class="block-title">Overleaf 项目<span class="line"></span></div>
      <form class="add-line" onSubmit=${link}><input value=${url} onInput=${(e) => setUrl(e.target.value)} placeholder="粘贴 Overleaf 项目链接，或它同步的 GitHub 仓库链接" />
        <button class="btn sm primary" disabled=${busy || !url.trim()}>${busy ? html`<span class="spin"></span>克隆中` : '关联'}</button></form>
      <p class="hint">关联后可以在讨论里让 agent 直接改论文的 LaTeX；改动先留在本机，你看过再推送。两种接法：Overleaf 项目链接需要 Overleaf 的 Git 集成（账户设置里生成 Git token，写进 config.json 的 <code>overleaf_token</code>）；项目已经开了 GitHub 同步的，直接粘 GitHub 仓库链接，用这台 Mac 的 SSH 凭据。</p></div>`;
  }
  return html`<div class="block"><div class="block-title">Overleaf 项目${viaGithub(pid) ? html`<span class="hint">　经 GitHub 同步：${pid.slice(7)}</span>` : null}<span class="line"></span>
      <a class="btn sm ghost" href=${viaGithub(pid) ? `https://github.com/${pid.slice(7)}` : `https://www.overleaf.com/project/${pid}`} target="_blank">打开 ↗</a>
      <button class="btn sm ghost" disabled=${!!x.overleaf_changed} title=${x.overleaf_changed ? '先推送或放弃未推送的改动' : ''} onClick=${pull}>拉取最新</button>
      <button class="btn sm ghost" onClick=${unlink}>解除关联</button></div>
    <${OverleafChanges} x=${x} /></div>`;
}

function Writing({ x }) {
  const w = x.writing, run = liveRun(runsOf(x, 'writing'));
  const [editing, setEditing] = useState(null);
  const related = x.related.groups.filter((g) => g.text);
  const can = agentOn.value && !run, has = w.abstract || w.introduction || related.length;
  const draft = async () => {
    try { await post(`/api/research/${enc(x.id)}/writing/draft`); toast('已交给 agent，写好后会作为修改稿等你确认'); refresh(); } catch (err) { fail(err); }
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
  return html`<p class="hint">完整论文在 Overleaf 写；这里用 Markdown 打磨摘要和引言。写作要求在右侧的写作讨论里说（和概览里推敲三要素的讨论分开），可以一句接一句地改；agent 改完的摘要引言作为修改稿等你确认，Overleaf 的改动在页面最下面查看和推送。引用写成 [arxiv:2410.24164]，导出时转成 \\cite。</p>
    <div class="tab-acts"><button class="btn sm" disabled=${!can} title="不带要求，让 agent 按研究现状一次写完" onClick=${draft}>${run ? html`<${Mark} spin />写作中` : w.abstract || w.introduction ? '让 agent 改写' : '让 agent 起草'}</button>
      <button class="btn sm" disabled=${!has} onClick=${exportLatex}>复制 LaTeX</button><button class="btn sm" disabled=${!has} onClick=${exportBib}>下载 BibTeX</button></div>
    ${run ? html`<div class="card-wait"><${Mark} spin />Agent 正在写：读三要素、实验记录和相关论文。写好后会作为修改稿等你确认…</div>` : null}
    ${w.proposal ? html`<${WritingProposal} key=${w.proposal.at} x=${x} />` : null}
    ${SECTIONS.map(([k, label, hint]) => editing === k
      ? html`<form class="element writing-form" onSubmit=${(e) => save(e, k)}><h3>${label}</h3><textarea rows="12" ref=${(el) => el?.focus()}>${w[k] || ''}</textarea>
          <div style="display:flex;gap:8px;margin-top:8px"><button class="btn sm primary">保存</button><button type="button" class="btn sm ghost" onClick=${() => setEditing(null)}>取消</button></div></form>`
      : html`<div class=${`element ${w[k] ? '' : 'blank'}`}><h3>${label}<button class="btn sm ghost" style="float:right" onClick=${() => setEditing(k)}>编辑</button></h3>${w[k] ? html`<${Md} class="quotable" text=${w[k]} />` : html`<div class="hint">${hint}</div>`}</div>`)}
    <div class=${`element ${related.length ? '' : 'blank'}`}><h3>相关工作<span class="hint" style="float:right;font-weight:400">由素材页的“相关工作”拼成，在那里修改</span></h3>
      ${related.length ? related.map((g) => html`<${Md} class="quotable" text=${`**${g.name}.** ${g.text}`} />`) : html`<div class="hint">Related Work：每条研究方向一段。先在素材页让 agent 梳理相关工作。</div>`}</div>
    <${RunsBlock} runs=${runsOf(x, 'writing')} label="写作" spec="librarian/writing.md" />
    <${Overleaf} x=${x} />`;
}

// ---------- detail and list ----------

const tabOf = {};

function ResearchDetail({ id }) {
  const [x, , err] = useLive(`/api/research/${enc(id)}`);
  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState(tabOf[id] || 'overview');
  if (err) return html`<div class="empty"><b>打不开</b>${err}</div>`;
  if (!x) return html`<div class="empty"><span class="spin"></span></div>`;
  const exps = x.experiments, by = (c) => exps.filter((e) => e.conclusion === c).length, ps = planStats(x.plan.tasks);
  const toTab = (k) => (e) => { e.preventDefault(); tabOf[id] = k; setTab(k); };
  const remove = async () => {
    if (!confirm('删除这个研究？它的素材（论文、讨论）都会保留。')) return;
    await del(`/api/research/${enc(x.id)}`).catch(fail);
    selected.value = null;
    toast('已删除');
    refresh();
  };
  const head = html`<div class="src-bar"><button class="btn sm ghost wide-only" title="研究列表" onClick=${() => setList(!listOn.value)}>${listOn.value ? '‹ 收起列表' : '› 研究列表'}</button><${StageChip} stage=${x.stage} /><${ElementsChip} n=${x.elements_done} />
      ${exps.length ? html`<span class="chip" title="实验记录">实验 ${exps.length}（支持 ${by('supports')} · 反驳 ${by('refutes')}）</span>` : null}
      ${x.repos.length ? html`<span class="chip">仓库 ${x.repos.length}</span>` : null}${x.writing.abstract || x.writing.introduction ? html`<span class="chip">有草稿</span>` : null}
      ${ps.total ? html`<a href="#" class=${`chip ${ps.late ? 'red' : ps.done === ps.total ? 'green' : ''}`} title="计划进度" onClick=${toTab('plan')}>计划 ${ps.done}/${ps.total}${ps.late ? ` · 逾期 ${ps.late}` : ''}</a>` : null}
      ${x.plan.proposal ? html`<a href="#" class="chip violet" onClick=${toTab('plan')}>计划修改稿待确认</a>` : null}
      ${x.related.proposal ? html`<a href="#" class="chip violet" onClick=${toTab('materials')}>相关工作修改稿待确认</a>` : null}
      ${x.figures.proposal ? html`<a href="#" class="chip violet" onClick=${toTab('experiments')}>实验设计修改稿待确认</a>` : null}
      ${x.overleaf_changed ? html`<a href="#" class="chip violet" title="agent 改了论文，去写作页查看并推送" onClick=${toTab('writing')}>Overleaf 改动待推送 ${x.overleaf_changed}</a>` : null}
      <span>${x.stage === 'established' ? `${ago(x.established_at)}确立` : `更新于 ${ago(x.updated_at)}`}</span><span class="grow"></span>
      <button class="btn sm ghost" onClick=${remove}>删除</button></div>`;
  const scope = `${{ overview: 'research', materials: 'related', experiments: 'experiment' }[tab] || tab}:${x.id}`;
  const split = (main) => html`<div class=${`research-split ${dockOn.value ? '' : 'off'}`}><div class="research-main" data-quote-scope=${scope}><div class="detail">${main}</div></div>
    ${dockOn.value ? html`<aside class="research-dock"><${Chat} key=${scope} scope=${scope} /></aside>` : null}</div>`;
  if (editing) return split(html`${head}<${EditForm} x=${x} done=${() => setEditing(false)} />`);
  const tabs = [['overview', '概览'], ['plan', '计划', x.plan.tasks.length], ['materials', '素材', x.links.length], ['code', '代码', x.repos.length], ['experiments', '实验', exps.length], ['writing', '写作']];
  const pick = (k) => { tabOf[id] = k; setTab(k); };
  const body = tab === 'plan' ? html`<${Plan} x=${x} />` : tab === 'materials' ? html`<${RelatedWork} x=${x} /><${Materials} x=${x} />` : tab === 'code' ? html`<${Code} x=${x} />` : tab === 'experiments' ? html`<${Experiments} x=${x} />`
    : tab === 'writing' ? html`<${Writing} x=${x} />` : html`<${Overview} x=${x} edit=${() => setEditing(true)} />`;
  return split(html`${head}
    <h1 class="research-title">${x.title}</h1>${x.summary ? html`<div class="research-summary"><${Html} s=${inlineMd(x.summary)} /></div>` : null}
    <${TopicsRow} kind="research" owner=${x} />
    <div class="rtabs">${tabs.map(([k, label, n]) => html`<button class=${`tab ${tab === k ? 'on' : ''}`} onClick=${() => pick(k)}>${label}${n ? html`<span class="n">${n}</span>` : null}</button>`)}
      <span class="grow"></span><button class="btn sm ghost" title="每个标签有自己的讨论，右侧跟着标签切换；概览里聊研究本身" onClick=${() => setDock(!dockOn.value)}>${dockOn.value ? '收起讨论 ›' : '‹ 讨论'}</button></div>
    ${body}`);
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
  return html`<${ListDetail} hasSel=${!!sel} single=${!!sel && !listOn.value} list=${left} detail=${sel ? html`<${ResearchDetail} key=${sel} id=${sel} />` : null} />`;
}

// ---------- 加入研究: join an existing research or start one with these materials ----------

function Picker({ ctx }) {
  const [all] = useLive('/api/research', { poll: false });
  const [q, setQ] = useState(''), [role, setRole] = useState('');
  const hasPaper = (ctx.links || []).some((l) => l.kind === 'paper');
  const done = (id, message) => { closeModal(); toast(message, '查看', () => openResearch(id)); refresh(); };
  const withRole = (l) => (role && l.kind === 'paper' ? { ...l, role } : l);
  const join = async (x) => {
    try { for (const l of ctx.links) await post(`/api/research/${enc(x.id)}/links`, withRole(l)); done(x.id, `已作为素材加入「${x.title}」`); } catch (e) { fail(e); }
  };
  const create = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), title = (f.get('title') || '').trim();
    if (!title) { toast('给这个研究起个名字'); return; }
    try {
      const x = await post('/api/research', { title, summary: f.get('summary'), topics: ctx.topics || [], links: ctx.links || [] });
      if (role) for (const l of ctx.links.filter((l) => l.kind === 'paper')) await post(`/api/research/${enc(x.id)}/links`, withRole(l));
      done(x.id, '已开始研究（构思中）');
    } catch (err) { fail(err); }
  };
  const shown = (all || []).filter((x) => x.title.toLowerCase().includes(q.toLowerCase()));
  return html`<h3>${ctx.heading || '加入研究'}</h3>
    ${hasPaper ? html`<p class="hint" style="margin:0 0 8px">在研究里的用途（可选）：<${RoleSelect} value=${role} onChange=${setRole} /></p>` : null}
    ${all?.length ? html`<p class="hint" style="margin:0 0 4px">作为素材加入已有的研究：</p><input class="picker-filter" type="search" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder="筛选…" autofocus />
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
  return html`<div class="cmp-picker"><input class="picker-filter" type="search" value=${q} onInput=${(e) => setQ(e.target.value)} placeholder=${`筛选${KIND_NAME[kind]}…`} autofocus />
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
      openItem('research', id, 'research');
      if (mode === 'discuss') talk();
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
