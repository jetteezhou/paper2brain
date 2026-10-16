// paper2brain web UI. No build step: plain ES module, HTML strings, one delegated click handler.

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const enc = encodeURIComponent;

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}
const get = (u) => api('GET', u);
const post = (u, b = {}) => api('POST', u, b);

const S = {
  view: 'inbox', arg: null, state: null, list: [], sel: null, detail: null,
  open: new Set(['VLM', 'VLA', 'WAM']), images: [], reader: null, threads: {},
  picker: null, scope: 'library', thoughts: [], search: null, seen: {}, scopeTitles: {},
};

const VIEWS = {
  inbox: { title: '收件箱', kind: 'captures', empty: ['收件箱是空的', '把公众号、小红书或 arXiv 链接粘贴到上面，回车就收下。<br>截图可以直接 ⌘V 粘贴。'] },
  pending: { title: '待处理', kind: 'captures', empty: ['没有待处理的', '拿不准是哪篇、没找到论文或处理失败的捕获会放在这里，等你说一句“是这篇”。'] },
  deep: { title: '待深读', kind: 'captures', empty: ['还没有待深读的', '在收件箱里按 <kbd>D</kbd>，或在卡片上点“待深读”。'] },
  archived: { title: '已存档', kind: 'captures', empty: ['还没有存档', '看过卡片、暂时不深读的，按 <kbd>A</kbd> 存档。'] },
  ignored: { title: '已忽略', kind: 'captures', empty: ['没有忽略的内容', '忽略只是移出收件箱，不代表不感兴趣。'] },
  papers: { title: '全部论文', kind: 'papers', empty: ['库里还没有论文', '收下的内容关联到论文后会出现在这里。'] },
  topic: { kind: 'papers', empty: ['这个主题下还没有论文', 'agent 会自动归类；你也可以在论文卡片上手动调整主题。'] },
  unsorted: { title: '未归类', kind: 'papers', empty: ['都归好类了', 'agent 拿不准的论文会留在这里。'] },
  thoughts: { title: '想法', kind: 'page' },
  research: { title: '研究', kind: 'research', empty: ['还没有研究', '在讨论、想法或论文卡片上点“加入研究”，或者在上面直接开一个。<br>素材攒够了，再和 agent 一起推敲三要素：研究问题、研究方法、验证方案。'] },
  discuss: { title: '讨论', kind: 'page' },
  search: { title: '搜索', kind: 'search' },
};

const ICON = {
  inbox: '<path d="M3 13h5l2 3h4l2-3h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
  pending: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  deep: '<path d="M4 5h6a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H4z"/><path d="M20 5h-6a3 3 0 0 0-3 3"/><path d="M20 5v13h-7"/>',
  papers: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  thoughts: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
  discuss: '<path d="M4 5h16v11H9l-5 4z"/>',
  research: '<path d="M12 3l2.2 5.6L20 9.3l-4.5 3.8 1.4 5.9L12 15.9 7.1 19l1.4-5.9L4 9.3l5.8-.7z"/>',
  unsorted: '<path d="M4 7h16M4 12h10M4 17h6"/>',
  archived: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/>',
  ignored: '<circle cx="12" cy="12" r="9"/><path d="m8 8 8 8"/>',
};
const icon = (k) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[k] || ''}</svg>`;

// ---------- formatting ----------

function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  const hm = d.toTimeString().slice(0, 5);
  if (d.toDateString() === new Date().toDateString()) return hm;
  if (d.toDateString() === new Date(Date.now() - 864e5).toDateString()) return `昨天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
const day = (iso) => {
  const d = new Date(iso);
  if (d.toDateString() === new Date().toDateString()) return '今天';
  if (d.toDateString() === new Date(Date.now() - 864e5).toDateString()) return '昨天';
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
};
const CHANNEL = { wechat: '公众号', xhs: '小红书', arxiv: 'arXiv', doi: 'DOI', openreview: 'OpenReview', web: '网页' };
const idLabel = (pid) => {
  const [k, ...r] = pid.split(':');
  const ref = r.join(':');
  return { arxiv: `arXiv ${ref}`, doi: `DOI ${ref}`, openreview: `OpenReview ${ref}` }[k] || 'PDF';
};
const channel = (c) => CHANNEL[c.channel] || (c.source === 'shortcut' ? 'iPhone' : c.source === 'web' ? '手动' : '命令行');
const authors = (a) => (a || []).length ? a.slice(0, 3).join(', ') + (a.length > 3 ? ' 等' : '') : '';
const firstLine = (s) => (s || '').split('\n').find((l) => l.trim()) || '';
const short = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s || '');
const live = (status) => status === 'CREATING' || status === 'RUNNING';
const agentOn = () => S.state?.agent?.enabled;

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[(arxiv:\d{4}\.\d{4,5})\s*p\.?\s*(\d+)\]/gi, (m, id, p) => cite(id.toLowerCase(), +p))
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
    const h = line.match(/^#{1,4}\s+(.*)$/);
    if (h) out.push(`<h4>${inline(h[1])}</h4>`);
    else if (/^>\s?/.test(line)) out.push(`<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join('');
}
const md = (src) => `<div class="md">${String(src || '').replace(/\r/g, '').split('```')
  .map((b, i) => (i % 2 ? `<pre><code>${esc(b.replace(/^\w*\n/, ''))}</code></pre>` : blocks(b))).join('')}</div>`;
const cite = (pid, page, quote) =>
  `<button class="cite" data-action="cite" data-pid="${esc(pid)}" data-page="${page}" title="${esc(quote || '跳到原文')}">p.${page}</button>`;

// ---------- toast & modal ----------

let toastTimer;
function toast(text, action, onAction, isError) {
  const t = $('#toast');
  t.className = 'show' + (isError ? ' error' : '');
  t.innerHTML = `<span>${esc(text)}</span>${action ? `<button>${esc(action)}</button>` : ''}`;
  if (action) t.querySelector('button').onclick = () => { t.className = ''; onAction(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ''), action ? 6000 : 3000);
}
const fail = (e) => toast(e.message || String(e), null, null, true);

function modal(html) {
  closeModal();
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.onclick = (e) => e.target === bg && closeModal();
  document.body.append(bg);
}
const closeModal = () => document.querySelector('.modal-bg')?.remove();

function setupModal() {
  const a = S.state.agent;
  modal(`<h3>${a.enabled ? 'Agent 已连接' : '连接 Cursor agent'}</h3>
  <p class="hint">快读卡、深读报告和讨论由 Cursor agent 在这台 Mac 上完成；没连接时只做规则预处理（识别链接、抓取快照、下载全文）。</p>
  <ol>
    <li>安装 Cursor CLI 并登录（只需一次）：<pre>curl https://cursor.com/install -fsS | bash\nagent login</pre></li>
    <li>在仓库目录启动本机 worker（保持运行）：<pre>agent worker start --name p2b-mac --worker-dir "${esc(a.repo_dir)}"</pre></li>
    <li>在 <a href="https://cursor.com/dashboard/integrations" target="_blank">Cursor Dashboard</a> 创建 API key，写入 <code>${esc(a.home)}/config.json</code>：
      <pre>{\n  "cursor_api_key": "key_...",\n  "machine": "p2b-mac"\n}</pre></li>
    <li>重启 <code>./p2b serve</code>。</li>
  </ol>
  ${a.enabled ? `<p>当前 worker：<b>${esc(a.machine)}</b>${a.auto_agent ? '，收下后自动交给 agent 整理。' : '，自动整理已关闭（config.json 中 auto_agent=false）。'}</p>` : `<p class="hint">当前缺少：${esc(a.missing.join('、'))}</p>`}
  <p class="hint">调优 agent：修改仓库里的 <code>librarian/*.md</code>，再在捕获详情里点“重新整理”。</p>
  <div style="text-align:right;margin-top:12px"><button class="btn" data-action="close-modal">知道了</button></div>`);
}

// ---------- routing ----------

function parseHash() {
  const [view, ...rest] = location.hash.replace(/^#\/?/, '').split('/');
  const arg = rest.length ? decodeURIComponent(rest.join('/')) : null;
  if (view === 'read' && arg) {
    S.readerPushed = !!S.pushing;
    S.pushing = false;
    const [pid, page] = arg.split('@');
    openReader(pid, page ? +page : undefined, false);
    return;
  }
  closeReader(false);
  const v = VIEWS[view] ? view : 'inbox';
  if (v !== S.view || arg !== S.arg) {
    S.view = v; S.arg = arg; S.sel = null; S.detail = null; S.list = []; S.seen = {};
    render();
    load();
  }
}
const go = (view, arg) => { location.hash = `#/${view}${arg != null ? '/' + enc(arg) : ''}`; };

// ---------- loading ----------

async function loadState() {
  S.state = await get('/api/state');
  renderSide();
}

async function load() {
  const v = VIEWS[S.view];
  try {
    if (v.kind === 'captures') S.list = await get(`/api/captures?view=${S.view}`);
    else if (v.kind === 'papers') {
      const q = S.view === 'topic' ? `?topic=${enc(S.arg)}` : S.view === 'unsorted' ? '?unsorted=1' : '';
      S.list = await get('/api/papers' + q);
    } else if (v.kind === 'research') S.list = await get('/api/research' + (S.arg ? `?topic=${enc(S.arg)}` : ''));
    else if (S.view === 'search') S.search = await get(`/api/search?q=${enc(S.arg || '')}`);
    else if (S.view === 'thoughts') S.thoughts = await get('/api/thoughts');
    else if (S.view === 'discuss') { await loadThread(scopeKey(), S.threadId); S.threadId = null; }
    if ((S.view === 'topic' || S.view === 'research') && S.arg) {
      const other = await get(S.view === 'topic' ? `/api/research?topic=${enc(S.arg)}` : `/api/papers?topic=${enc(S.arg)}`);
      S.otherCount = other.length;
    }
  } catch (e) { fail(e); }
  renderBody();
  const want = S.wantSel;
  S.wantSel = null;
  if (want) select(want.kind, want.id);
  else if (!S.sel && v.kind !== 'page' && S.list.length && innerWidth > 900) select(itemKind(S.list[0]), S.list[0].id);
}

const itemKind = (x) => (x.stage !== undefined ? 'research' : x.raw !== undefined ? 'capture' : 'paper');

async function select(kind, id) {
  S.sel = { kind, id };
  S.picker = null;
  document.querySelectorAll('.item.on').forEach((el) => el.classList.remove('on'));
  document.querySelector(`.item[data-id="${CSS.escape(id)}"]`)?.classList.add('on');
  $('#body').classList.add('show-detail');
  await loadDetail(true);
}

async function loadDetail(force) {
  if (!S.sel) return;
  try {
    let d;
    if (S.sel.kind === 'capture') {
      const cap = await get(`/api/captures/${enc(S.sel.id)}`);
      const papers = await Promise.all(cap.papers.filter((p) => p.state === 'confirmed').map((p) => get(`/api/papers/${enc(p.id)}`)));
      d = { cap, papers };
    } else if (S.sel.kind === 'research') d = { research: await get(`/api/research/${enc(S.sel.id)}`) };
    else d = { papers: [await get(`/api/papers/${enc(S.sel.id)}`)] };
    if (force || changed('detail', d)) { S.detail = d; renderDetail(); }
  } catch (e) {
    S.detail = { error: e.message };
    renderDetail();
  }
}

function changed(key, value) {
  const json = JSON.stringify(value);
  if (S.seen[key] === json) return false;
  S.seen[key] = json;
  return true;
}

async function loadThread(scope, threadId) {
  const keep = !threadId && S.threads[scope]?.pinned ? S.threads[scope].id : threadId;
  const t = await get(keep ? `/api/threads?id=${enc(keep)}` : `/api/threads?scope=${enc(scope)}`);
  t.pinned = !!keep;
  if (scope.startsWith('research:')) {
    const x = await get(`/api/research/${enc(scope.slice(9))}`).catch(() => null);
    if (x) { S.scopeTitles[scope] = x.title; t.proposal = !!x.proposal; }
  }
  S.threads[scope] = t;
  return t;
}

// ---------- sidebar ----------

function renderSide() {
  const c = S.state.counts;
  const item = (view, label, n, hot) =>
    `<div class="nav-item ${S.view === view && !(view === 'research' && S.arg) ? 'on' : ''}" data-action="go" data-view="${view}">${icon(view)}<span>${label}</span>${n ? `<span class="n ${hot ? 'hot' : ''}">${n}</span>` : ''}</div>`;
  const inTopic = (path) => (S.view === 'topic' || S.view === 'research') && S.arg === path;
  const tree = S.state.topics.map((t) => {
    const open = S.open.has(t.path);
    const on = inTopic(t.path);
    return `<div class="nav-item ${on ? 'on' : ''}" data-action="go" data-view="topic" data-arg="${esc(t.path)}">
        <span class="caret ${open ? 'open' : ''}" data-action="toggle-tree" data-path="${esc(t.path)}"><svg width="10" height="10" viewBox="0 0 10 10"><path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${esc(t.name)}</span>${t.count ? `<span class="n">${t.count}</span>` : ''}</div>` +
      (open ? t.children.map((ch) => `<div class="nav-item tree-child ${inTopic(ch.path) ? 'on' : ''}" data-action="go" data-view="topic" data-arg="${esc(ch.path)}"><span>${esc(ch.name)}</span>${ch.count ? `<span class="n">${ch.count}</span>` : ''}</div>`).join('') : '');
  }).join('');
  $('#nav').innerHTML =
    item('inbox', '收件箱', c.inbox) + item('pending', '待处理', c.pending, true) + item('deep', '待深读', c.deep) +
    item('papers', '全部论文', c.papers) + item('thoughts', '想法', c.thoughts) + item('research', '研究', c.research) + item('discuss', '讨论') +
    `<div class="nav-label">主题</div>${tree}${item('unsorted', '未归类', c.unsorted)}` +
    `<div class="nav-label">归档</div>${item('archived', '已存档', c.archived)}${item('ignored', '已忽略', c.ignored)}`;
  const a = S.state.agent;
  $('#agent-status').innerHTML = a.enabled
    ? `<span class="dot ok"></span><span>Agent 已连接 · ${esc(a.machine)}</span>`
    : `<span class="dot"></span><span>Agent 未连接 · 只做规则预处理<br><a>如何连接</a></span>`;
}

// ---------- body ----------

function render() {
  renderSide && S.state && renderSide();
  renderBody();
  renderDetail();
}

function renderBody() {
  const v = VIEWS[S.view];
  $('#body').classList.toggle('single', v.kind === 'page');
  if (S.view === 'thoughts') { $('#list').innerHTML = ''; $('#detail').innerHTML = thoughtsPage(); return; }
  if (S.view === 'discuss') { $('#list').innerHTML = ''; $('#detail').innerHTML = discussPage(); scrollChat(); return; }
  $('#list').innerHTML = S.view === 'search' ? searchList() : listHtml();
}

function listHead(title, sub, keys) {
  return `<div class="list-head"><h2>${esc(title)}</h2><span class="sub">${sub}</span>${keys ? `<span class="keys hint">${keys}</span>` : ''}</div>`;
}

function topicTabs() {
  if (!(S.view === 'topic' || S.view === 'research') || !S.arg) return '';
  const papers = S.view === 'topic' ? S.list.length : S.otherCount, research = S.view === 'research' ? S.list.length : S.otherCount;
  return `<div class="seg"><button class="${S.view === 'topic' ? 'on' : ''}" data-action="go" data-view="topic" data-arg="${esc(S.arg)}">论文 ${papers ?? ''}</button>
    <button class="${S.view === 'research' ? 'on' : ''}" data-action="go" data-view="research" data-arg="${esc(S.arg)}">研究 ${research ?? ''}</button></div>`;
}

function listHtml() {
  const v = VIEWS[S.view];
  const title = S.arg && (S.view === 'topic' || S.view === 'research') ? S.arg.replace('/', ' / ') : v.title;
  if (v.kind === 'research') return researchList(title);
  const keys = v.kind === 'captures' && ['inbox', 'pending'].includes(S.view) ? '<kbd>J</kbd><kbd>K</kbd> 移动 <kbd>D</kbd> 深读 <kbd>A</kbd> 存档' : '<kbd>J</kbd><kbd>K</kbd> 移动 <kbd>↵</kbd> 阅读';
  const head = listHead(title, S.list.length ? `${S.list.length} 条` : '', S.list.length ? keys : '') + topicTabs();
  if (!S.list.length) return head + `<div class="empty"><b>${v.empty[0]}</b>${v.empty[1]}</div>`;
  return head + S.list.map((x) => (v.kind === 'captures' ? captureItem(x) : paperItem(x))).join('');
}

const STAGE = { forming: ['构思中', 'chip amber'], established: ['已确立', 'chip green-solid'] };
const stageChip = (s) => `<span class="${STAGE[s][1]}">${STAGE[s][0]}</span>`;
const elementsChip = (n) => `<span class="chip ${n === 3 ? 'green' : ''}" title="研究问题 / 研究方法 / 验证方案">三要素 ${n}/3</span>`;

function researchList(title) {
  const placeholder = S.arg ? `在 ${S.arg.replace('/', ' / ')} 下开一个研究：先写个名字…（回车）` : '开一个研究：先写个名字…（回车）';
  let h = listHead(title, S.list.length ? `${S.list.length} 个研究` : '', '') + topicTabs() +
    `<form class="add-line research-new" data-form="research-new" data-topic="${esc(S.arg || '')}"><input placeholder="${esc(placeholder)}"></form>`;
  if (!S.list.length) return h + `<div class="empty"><b>${S.arg ? '这个主题下还没有研究' : VIEWS.research.empty[0]}</b>${VIEWS.research.empty[1]}</div>`;
  for (const stage of ['established', 'forming']) {
    const items = S.list.filter((x) => x.stage === stage);
    if (!items.length) continue;
    h += `<div class="group-label">${stage === 'established' ? '已确立 · 三要素经你确认' : '构思中 · 汇集素材、推敲三要素'}　${items.length}</div>` + items.map(researchItem).join('');
  }
  return h;
}

function researchItem(x) {
  return `<div class="item ${S.sel?.id === x.id ? 'on' : ''}" data-action="select" data-kind="research" data-id="${x.id}">
    <div class="item-meta">${stageChip(x.stage)}${elementsChip(x.elements_done)}${x.has_proposal ? '<span class="chip violet">修改稿待确认</span>' : ''}<time>${ago(x.updated_at)}</time></div>
    <div class="item-title">${esc(x.title)}</div>
    ${x.summary ? `<div class="item-tldr">${esc(x.summary)}</div>` : ''}
    <div class="item-tags">${x.topics.map((t) => `<span class="chip topic">${esc(t)}</span>`).join('')}${x.link_count ? `<span class="chip">${x.link_count} 份素材</span>` : ''}</div>
  </div>`;
}

function statusChip(c) {
  if (c.status === 'saved' || c.status === 'resolving') return '<span class="chip"><span class="spin"></span>规则预处理中</span>';
  if (live(c.run_status)) return '<span class="chip violet"><span class="spin"></span>Agent 整理中</span>';
  if (c.status === 'needs_confirm') return '<span class="chip amber">待确认论文</span>';
  if (c.status === 'needs_agent') return '<span class="chip amber">截图待识别</span>';
  if (c.status === 'unmapped') return '<span class="chip">没找到论文</span>';
  if (c.status === 'failed') return '<span class="chip red">预处理失败</span>';
  if (c.run_status === 'ERROR') return '<span class="chip red">Agent 出错</span>';
  return '';
}

function captureItem(c) {
  const p = c.papers[0];
  const title = c.title || firstLine(c.raw) || (c.image_count ? `附件 ${c.image_count} 个` : c.thought ? '一个想法' : '');
  const topics = [...new Set(c.papers.flatMap((x) => x.topics))];
  const triageBtns = S.view === 'inbox' || S.view === 'pending'
    ? `<div class="item-actions"><button class="btn sm ghost" data-action="triage" data-id="${c.id}" data-t="deep" title="待深读 (D)">深读</button><button class="btn sm ghost" data-action="triage" data-id="${c.id}" data-t="archived" title="存档 (A)">存档</button><button class="btn sm ghost" data-action="triage" data-id="${c.id}" data-t="ignored" title="忽略 (X)">忽略</button></div>`
    : VIEWS[S.view].kind === 'captures' ? `<div class="item-actions"><button class="btn sm ghost" data-action="triage" data-id="${c.id}" data-t="inbox">移回收件箱</button></div>` : '';
  return `<div class="item ${S.sel?.id === c.id ? 'on' : ''}" data-action="select" data-kind="capture" data-id="${c.id}">
    <div class="item-meta"><span class="src">${esc(channel(c))}${c.author ? ' · ' + esc(c.author) : ''}</span><time>${ago(c.created_at)}</time></div>
    <div class="item-title">${esc(short(title, 120))}</div>
    ${p ? `<div class="item-paper"><span>${esc(p.title)}${c.papers.length > 1 ? ` <span class="hint">等 ${c.papers.length} 篇</span>` : ''}</span></div>` : ''}
    ${p?.tldr ? `<div class="item-tldr">${esc(p.tldr)}</div>` : ''}
    ${c.thought ? `<div class="item-thought">${esc(c.thought)}</div>` : ''}
    <div class="item-tags">${topics.map((t) => `<span class="chip topic">${esc(t)}</span>`).join('')}${statusChip(c)}</div>
    ${triageBtns}
  </div>`;
}

function paperItem(p) {
  return `<div class="item ${S.sel?.id === p.id ? 'on' : ''}" data-action="select" data-kind="paper" data-id="${esc(p.id)}">
    <div class="item-meta"><span class="src">${esc(idLabel(p.id))}</span>${p.published ? `<span>· ${esc(p.published.slice(0, 7))}</span>` : ''}<time>${ago(p.last_captured)}</time></div>
    <div class="item-paper no-arrow" style="margin-top:0;font-size:14.5px">${esc(p.title)}</div>
    <div class="item-meta" style="margin:3px 0 0">${esc(authors(p.authors))}</div>
    ${p.tldr ? `<div class="item-tldr">${esc(p.tldr)}</div>` : ''}
    <div class="item-tags">${p.topics.map((t) => `<span class="chip topic">${esc(t)}</span>`).join('')}${p.deep ? '<span class="chip amber">待深读</span>' : ''}${p.has_report ? '<span class="chip green">有深读报告</span>' : ''}</div>
  </div>`;
}

function searchList() {
  const r = S.search;
  if (!r) return listHead('搜索', '');
  const total = r.papers.length + r.captures.length + r.thoughts.length + r.notes.length;
  let h = listHead(`“${S.arg}”`, `${total} 条结果`);
  if (!total) return h + '<div class="empty"><b>库里没找到</b>换个关键词试试：论文英文标题、方法名、公众号名或你想法里的词。</div>';
  if (r.papers.length) h += '<div class="group-label">论文</div>' + r.papers.map((p) => paperItem({ ...p, topics: [] })).join('');
  if (r.captures.length) h += '<div class="group-label">解读与捕获</div>' + r.captures.map((c) => captureItem({ ...c, papers: [], status: '' })).join('');
  if (r.thoughts.length) h += '<div class="group-label">想法</div>' + r.thoughts.map((t) => `<div class="item" ${t.capture_id ? `data-action="select" data-kind="capture" data-id="${t.capture_id}"` : t.paper_id ? `data-action="select" data-kind="paper" data-id="${esc(t.paper_id)}"` : ''}><div class="item-thought" style="white-space:normal">${esc(t.text)}</div><div class="item-meta" style="margin-top:4px"><time>${ago(t.created_at)}</time></div></div>`).join('');
  if (r.notes.length) h += '<div class="group-label">笔记</div>' + r.notes.map((n) => `<div class="item" ${n.paper_id ? `data-action="select" data-kind="paper" data-id="${esc(n.paper_id)}"` : ''}><div class="item-tldr" style="-webkit-line-clamp:4">${esc(n.text)}</div><div class="item-meta" style="margin-top:4px"><time>${ago(n.created_at)}</time></div></div>`).join('');
  return h;
}

// ---------- detail ----------

function renderDetail() {
  const el = $('#detail');
  if (VIEWS[S.view].kind === 'page') return;
  const d = S.detail;
  if (!S.sel || !d) { el.innerHTML = S.list.length ? '<div class="empty" style="padding-top:120px">选择左侧一条查看</div>' : ''; return; }
  if (d.error) { el.innerHTML = `<div class="empty"><b>打不开</b>${esc(d.error)}</div>`; return; }
  const back = innerWidth <= 900 ? '<button class="btn sm ghost" data-action="back">← 返回列表</button>' : '';
  el.innerHTML = `<div class="detail">${back}${d.research ? researchDetail(d.research) : d.cap ? captureDetail(d.cap, d.papers) : paperDetail(d.papers[0])}</div>`;
}

const FIELDS = [
  ['problem', '研究问题', '要解决什么、为什么重要、现有方法为什么不够'],
  ['method', '研究方法', '核心洞见 + 方法思路（2–5 步，输入输出、关键模块）'],
  ['validation', '验证方案', '最小验证实验：数据 / 平台、对比谁、看什么指标、多大算成功'],
  ['notes', '其他', '和已有工作的区别、风险与未知（可选）'],
];
const ELEMENTS = FIELDS.slice(0, 3);

function researchDetail(x) {
  const run = x.runs.find((r) => live(r.status));
  const canDraft = agentOn() && !run;
  let h = `<div class="src-bar">${stageChip(x.stage)}${elementsChip(x.elements_done)}
    <span>${x.stage === 'established' ? `${ago(x.established_at)}确立` : `更新于 ${ago(x.updated_at)}`}</span><span class="grow"></span>
    <button class="btn sm" data-action="research-discuss" data-id="${x.id}" title="和 agent 一起推敲；它的调整会作为修改稿交给你确认">在讨论里推进</button>
    <button class="btn sm" data-action="research-draft" data-id="${x.id}" ${canDraft ? '' : 'disabled'} title="${agentOn() ? '让 agent 根据素材起草三要素，作为修改稿交给你确认' : '需要先连接 agent'}">${run ? '<span class="spin"></span>起草中' : '让 agent 起草'}</button>
    <button class="btn sm" data-action="research-edit" data-id="${x.id}">${S.editResearch === x.id ? '取消编辑' : '编辑'}</button>
    <button class="btn sm ghost" data-action="research-delete" data-id="${x.id}">删除</button></div>`;
  if (S.editResearch === x.id) return h + researchForm(x) + materialsBlock(x);
  h += `<h1 class="research-title">${esc(x.title)}</h1>${x.summary ? `<div class="research-summary">${inline(x.summary)}</div>` : ''}${topicsRow(x, 'research')}`;
  if (run) h += '<div class="card-wait"><span class="spin"></span>Agent 正在起草：读素材、查库里的相关工作。写好后会作为修改稿等你确认…</div>';
  if (x.proposal) h += proposalBlock(x);
  h += `<div class="elements">${ELEMENTS.map(([k, label, hint]) => `<div class="element ${x.body?.[k] ? '' : 'empty'}"><h3>${label}</h3>${x.body?.[k]
    ? md(x.body[k]) : `<div class="hint">${hint}</div><button class="btn sm ghost" data-action="research-edit" data-id="${x.id}">补上</button>`}</div>`).join('')}
    ${x.body?.notes ? `<div class="element"><h3>其他</h3>${md(x.body.notes)}</div>` : ''}</div>`;
  h += establishBar(x) + materialsBlock(x);
  if (x.runs.length) h += `<details class="runs" ${run ? 'open' : ''}><summary>agent 起草记录 · ${x.runs.length} 次 <span class="hint">（调优：改 librarian/research.md）</span></summary>${x.runs.map((r) => runCard(r, '起草')).join('')}</details>`;
  return h;
}

function establishBar(x) {
  if (x.stage === 'established') {
    return `<div class="establish done"><div><b>已确立</b>　${ago(x.established_at)}由你确认。之后仍可在讨论里调整、采用修改稿。</div>
      <button class="btn sm ghost" data-action="research-reopen" data-id="${x.id}">撤回确立</button></div>`;
  }
  const missing = ELEMENTS.filter(([k]) => !x.body?.[k]).map(([, l]) => l);
  const why = x.proposal ? '先处理上面的修改稿。' : missing.length ? `还缺${missing.join('、')}。可以自己补，也可以在讨论里和 agent 推敲后采用修改稿。` : '三要素都有了。确认无误就可以确立；还想推敲，先去讨论里调整。';
  return `<div class="establish"><div><b>确立研究</b>　${why}</div>
    <button class="btn primary" data-action="research-establish" data-id="${x.id}" ${missing.length || x.proposal ? 'disabled' : ''}>确认确立</button></div>`;
}

function proposalBlock(x) {
  const p = x.proposal;
  const current = (k) => (k === 'title' ? x.title : k === 'summary' ? x.summary : x.body?.[k]) || '';
  const rows = [['title', '名称'], ['summary', '一句话'], ...FIELDS.map(([k, l]) => [k, l])].filter(([k]) => p[k] && p[k] !== current(k));
  return `<div class="proposal"><div class="proposal-head"><b>Agent 提交的修改稿</b><span class="hint">${ago(p.at)} · 采用后才会改动研究</span></div>
    ${rows.length ? rows.map(([k, l]) => `<div class="pr-row"><div class="pr-label">${l} ${current(k) ? '<span class="chip">修改</span>' : '<span class="chip green">新增</span>'}</div><div class="pr-new">${md(p[k])}</div></div>`).join('') : '<div class="hint">和现在的内容一样。</div>'}
    <div class="proposal-acts"><button class="btn primary" data-action="proposal" data-id="${x.id}" data-op="accept">采用</button>
      ${p.problem && p.method && p.validation && x.stage !== 'established' ? `<button class="btn" data-action="proposal" data-id="${x.id}" data-op="accept-establish">采用并确立</button>` : ''}
      <button class="btn" data-action="research-discuss" data-id="${x.id}">继续调整（去讨论）</button>
      <button class="btn ghost" data-action="proposal" data-id="${x.id}" data-op="discard">放弃</button></div></div>`;
}

function researchForm(x) {
  return `<form class="research-form" data-form="research-edit" data-id="${x.id}" data-rev="${x.revision}">
    <input name="title" class="research-title-input" value="${esc(x.title)}" placeholder="研究名称">
    <textarea name="summary" rows="2" placeholder="一句话：做什么、为什么可能有效">${esc(x.summary || '')}</textarea>
    ${FIELDS.map(([k, label, hint]) => `<label>${label}<span class="hint">　${hint}</span></label><textarea name="${k}" rows="${k === 'notes' ? 2 : 4}">${esc(x.body?.[k] || '')}</textarea>`).join('')}
    <p class="hint">可以引用论文：[arxiv:2410.24164 p.3]。保存不会改变确立状态。</p>
    <div style="display:flex;gap:8px"><button class="btn primary">保存</button><button type="button" class="btn ghost" data-action="research-edit" data-id="${x.id}">取消</button></div></form>`;
}

function materialsBlock(x) {
  const kindLabel = (l) => ({ paper: '论文', thought: '想法', message: l.role === 'user' ? '你在讨论里说' : 'agent 在讨论里说', thread: '讨论', capture: '收下的内容', note: '笔记' }[l.kind]);
  const rows = x.links.map((l) => `<div class="source ${l.missing ? 'missing' : ''}" ${l.missing ? '' : `data-action="open-source" data-kind="${l.kind}" data-ref="${esc(l.ref)}" data-paper="${esc(l.paper_id || '')}" data-capture="${esc(l.capture_id || '')}" data-scope="${esc(l.scope || '')}" data-thread="${esc(l.thread_id || '')}"`} title="${l.missing ? '' : '跳过去看'}">
      <span class="source-kind">${kindLabel(l)}</span><span class="source-label ${l.kind === 'paper' ? 'serif' : ''}">${esc(l.missing ? '（已删除）' : short(plain(l.label), 160))}</span>
      ${l.at ? `<time>${ago(l.at)}</time>` : ''}<button class="x" data-action="research-unlink" data-id="${x.id}" data-kind="${l.kind}" data-ref="${esc(l.ref)}" title="移出素材">×</button></div>`).join('');
  return `<div class="block"><div class="block-title">素材 · ${x.links.length}<span class="line"></span><button class="btn sm ghost" data-action="research-add-paper" data-id="${x.id}">+ 关联库里的论文</button></div>
    ${rows || '<div class="hint">还没有素材。在讨论消息、想法或论文卡片上点“加入研究”，就会出现在这里，点一下能跳回去。</div>'}</div>`;
}

const plain = (s) => (s || '').replace(/\[(?:arxiv|doi|openreview|pdf):[^\]]*?(?:\s*p\.?\s*(\d+))?\]/gi, (m, p) => (p ? `(p.${p})` : ''))
  .replace(/[*`#>]+/g, '').replace(/\s+/g, ' ').trim();
const researchChip = (x) => `<a href="#" class="chip research-chip" data-action="open-research" data-id="${x.id}" title="打开这个研究">${STAGE[x.stage][0]} · ${esc(short(x.title, 22))}</a>`;
const thoughtResearch = (t, paperId) => `${(t.research || []).map(researchChip).join('')}<button class="btn sm ghost" data-action="thought-research" data-id="${t.id}" data-paper="${esc(paperId || t.about_paper || t.paper_id || '')}" data-text="${esc(t.text)}" title="作为素材加入研究">→ 研究</button>`;

function captureDetail(c, papers) {
  const triage = { inbox: '收件箱', deep: '待深读', archived: '已存档', ignored: '已忽略' };
  const tb = (t, label) => `<button class="btn sm ${c.triage === t ? 'primary' : ''}" data-action="triage" data-id="${c.id}" data-t="${c.triage === t ? 'inbox' : t}">${label}</button>`;
  const rawExtra = c.raw && c.raw.replace(/https?:\/\/\S+/g, '').trim();
  const running = c.runs.find((r) => live(r.status));
  let h = `<div class="src-bar"><span class="chip">${esc(channel(c))}</span>${c.author ? `<span>${esc(c.author)}</span>` : ''}<span>${ago(c.created_at)}</span>
    ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">打开原文 ↗</a>` : ''}<span class="grow"></span>
    ${tb('deep', '待深读')}${tb('archived', '存档')}${tb('ignored', '忽略')}
    <button class="btn sm ghost" data-action="undo-capture" data-id="${c.id}" title="删除这条捕获及由它产生的内容">撤销收下</button></div>
    <div class="hint" style="margin-top:4px">当前在：${triage[c.triage]}</div>`;
  if (c.title) h += `<div class="cap-title">${esc(c.title)}</div>`;
  if (rawExtra || (!c.title && c.raw)) h += `<div class="raw">${inline(c.raw)}</div>`;
  if (c.attachments.length) h += `<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">${c.attachments.map((a) => a.mime.startsWith('image/')
    ? `<a href="/api/attachments/${a.id}" target="_blank" class="thumb" style="width:96px;height:96px"><img src="/api/attachments/${a.id}"></a>`
    : `<a href="/api/attachments/${a.id}" target="_blank" class="chip">${esc(a.filename.replace(/^\d+-/, ''))} ↗</a>`).join('')}</div>`;
  const capPaper = papers[0]?.id;
  h += `<div class="thoughts">${c.thoughts.map((t) => `<div class="thought"><span>${esc(t.text)}<small>${t.by === 'agent' ? 'agent 记录 · ' : ''}${ago(t.created_at)}</small></span><span class="thought-acts">${thoughtResearch(t, capPaper)}</span></div>`).join('')}
    <form class="add-line" data-form="thought" data-capture="${c.id}"><input placeholder="${c.thoughts.length ? '再补一句想法…' : '补一句想法：为什么收它？'}"><button class="btn sm">记下</button></form></div>`;

  if (c.status === 'needs_confirm' && !papers.length) {
    h += `<div class="notice"><h3>这条解读讲的是哪篇论文？</h3>${(c.candidates || []).map((m) => `
      <div class="cand"><div class="cand-body"><div class="cand-title">${esc(m.title)}</div>
      <div class="cand-meta">${esc(authors(m.authors))} · ${esc(m.published || '')} · <a href="${esc(m.url)}" target="_blank">${esc(idLabel(m.pid))}</a>${m.score ? ` · 标题相似度 ${Math.round(m.score * 100)}%` : ''}</div>
      <div class="cand-meta">${esc(m.why || '')}</div></div>
      <button class="btn sm primary" data-action="link" data-id="${c.id}" data-paper="${esc(m.pid)}">就是这篇</button></div>`).join('')}
      ${manualLink(c.id, '都不是？粘贴论文链接、DOI 或 PDF 链接，或把 PDF 拖进来')}</div>`;
  } else if (!papers.length && c.status !== 'saved' && c.status !== 'resolving') {
    const msg = { unmapped: '没找到对应的论文', failed: '规则预处理失败', needs_agent: '截图需要 agent 看图识别', mapped: '没有关联的论文' }[c.status] || '';
    h += `<div class="notice info"><h3>${msg}</h3>${c.note ? `<div class="hint" style="white-space:pre-wrap">${esc(c.note)}</div>` : ''}
      <p class="hint">${running ? 'Agent 正在处理…' : agentOn() ? '可以让 agent 再看一次，或者直接告诉我是哪篇。' : '连接 agent 后可以自动识别；也可以直接告诉我是哪篇。'}</p>
      ${manualLink(c.id, '粘贴论文链接、DOI 或 PDF 链接')}${pdfButton({ capture: c.id })}</div>`;
  } else if (c.status === 'saved' || c.status === 'resolving') {
    h += `<div class="card-wait"><span class="spin"></span>规则预处理中：识别链接、抓取解读快照、下载论文全文…</div>`;
  }
  h += papers.map((p) => paperCard(p, { capture: c })).join('');
  h += runsBlock(c);
  return h;
}

const manualLink = (cid, placeholder) =>
  `<form class="add-line" data-form="link" data-capture="${cid}"><input placeholder="${esc(placeholder)}"><button class="btn sm">关联</button></form>`;
const pdfButton = ({ capture, paper }) =>
  `<label class="btn sm" style="margin-top:8px">上传 PDF<input type="file" accept="application/pdf" hidden data-upload="${capture ? 'capture' : 'paper'}" data-id="${esc(capture || paper)}"></label>`;

function runsBlock(c) {
  const label = { capture: '整理', chat: '讨论', deepread: '深读' };
  const runs = c.runs;
  return `<details class="runs" ${runs.some((r) => live(r.status)) ? 'open' : ''}>
    <summary>处理记录 · ${runs.length ? `${runs.length} 次 agent 运行` : agentOn() ? '还没有 agent 运行' : 'agent 未连接'} <span class="hint">（调优：改 librarian/capture.md 后重新整理）</span></summary>
    <div style="display:flex;gap:8px;margin-top:10px">
      <button class="btn sm" data-action="reprocess" data-id="${c.id}" data-agent="1" ${agentOn() ? '' : 'disabled'}>重新整理（规则 + agent）</button>
      <button class="btn sm ghost" data-action="reprocess" data-id="${c.id}" data-agent="">只重跑规则预处理</button>
      ${c.agent_id ? `<a class="btn sm ghost" href="https://cursor.com/agents/${esc(c.agent_id)}" target="_blank">在 Cursor 中查看 ↗</a>` : ''}
    </div>
    ${runs.map((r) => runCard(r, label[r.kind])).join('')}
  </details>`;
}

function runStatus(r) {
  if (live(r.status)) return `<span class="chip violet"><span class="spin"></span>${r.status === 'CREATING' ? '启动中' : '运行中'}</span>`;
  return { FINISHED: '<span class="chip green">完成</span>', ERROR: '<span class="chip red">出错</span>', CANCELLED: '<span class="chip">已取消</span>', EXPIRED: '<span class="chip">已过期</span>' }[r.status] || `<span class="chip">${esc(r.status)}</span>`;
}

function runCard(r, label) {
  const tools = r.tools || [];
  return `<div class="run"><div class="run-head"><b>${esc(label || r.kind)}</b>${runStatus(r)}<span>${ago(r.created_at)}</span>${r.duration_ms ? `<span>· ${Math.round(r.duration_ms / 1000)}s</span>` : ''}<span class="grow"></span>
    ${live(r.status) ? `<button class="btn sm ghost" data-action="cancel-run" data-id="${r.id}">停止</button>` : ''}</div>
    ${tools.length ? `<div class="tools">${tools.slice(-8).map((t) => `<div>${t.status === 'running' ? '…' : '✓'} ${esc(t.name)} ${esc(t.args || '')}</div>`).join('')}</div>` : ''}
    ${r.text ? `<div style="margin-top:8px">${md(r.text)}</div>` : ''}
    ${r.error ? `<div class="hint" style="color:var(--red)">${esc(r.error)}</div>` : ''}
    <details><summary class="hint" style="cursor:pointer">发给 agent 的提示词</summary><pre>${esc(r.prompt)}</pre></details></div>`;
}

function paperDetail(p) {
  let h = paperCard(p, {});
  if (p.captures.length) {
    h += `<div class="block"><div class="block-title">来源 · ${p.captures.length}<span class="line"></span></div>${p.captures.map((c) => `
      <div class="item" style="padding:9px 0;border:0" data-action="select" data-kind="capture" data-id="${c.id}">
        <div class="item-meta"><span class="src">${esc(channel(c))}${c.author ? ' · ' + esc(c.author) : ''}</span><time>${ago(c.created_at)}</time></div>
        <div>${esc(c.title || short(firstLine(c.raw), 80))}</div></div>`).join('')}</div>`;
  }
  h += notesBlock(p);
  return h;
}

function notesBlock(p) {
  return `<div class="block"><div class="block-title">想法与笔记<span class="line"></span></div>
    ${p.thoughts.map((t) => `<div class="thought"><span>${esc(t.text)}<small>${ago(t.created_at)}</small></span><span class="thought-acts">${thoughtResearch(t, p.id)}</span></div>`).join('')}
    ${p.notes.map((n) => `<div class="run" style="margin-top:6px">${md(n.text)}<div class="hint">${n.by === 'agent' ? 'agent 记录的讨论结论' : '笔记'} · ${ago(n.created_at)}</div></div>`).join('')}
    <form class="add-line" data-form="thought" data-paper="${esc(p.id)}"><input placeholder="记一个想法，会和这篇论文绑在一起"><button class="btn sm">记下</button></form></div>`;
}

function paperCard(p, { capture, inReader } = {}) {
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
      return `<div class="take">${src ? `<b>${esc(src.author || channel(src))}</b>：` : ''}${esc(t.take)}${t.consistent === false ? ' <span class="chip red">与原文不符</span>' : ''}</div>`;
    }).join('');
    body = `<div class="tldr">${esc(card.tldr)}</div>
      <dl class="kv"><dt>问题</dt><dd>${inline(card.problem)}</dd><dt>方法</dt><dd>${inline(card.method)}</dd>
      <dt>结果</dt><dd><ul class="results">${card.results.map((r) => `<li>${inline(r.text)} ${cite(p.id, r.page, r.quote)}</li>`).join('')}</ul></dd></dl>
      <div class="res-row">${res.code ? `<a href="${esc(res.code)}" target="_blank">代码 ↗</a>` : '<span>代码：<b>未知</b></span>'}
        ${res.project ? `<a href="${esc(res.project)}" target="_blank">项目页 ↗</a>` : ''}${res.data ? `<a href="${esc(res.data)}" target="_blank">数据 ↗</a>` : ''}
        <span>真机实验：<b>${robot}</b></span>${res.platform ? `<span>平台：<b>${esc(res.platform)}</b></span>` : ''}</div>
      <div class="relevance"><b>和你的关系</b>　${inline(card.relevance)}</div>
      ${takes ? `<div class="takes"><div class="block-title" style="margin:10px 0 4px">解读观点<span class="line"></span></div>${takes}</div>` : ''}`;
  } else {
    const wait = capRun || deepRun
      ? '<div class="card-wait"><span class="spin"></span>Agent 正在写快读卡…</div>'
      : agentOn()
        ? `<div class="card-wait">还没有快读卡。${capture ? `<button class="btn sm" data-action="reprocess" data-id="${capture.id}" data-agent="1">让 agent 整理</button>` : ''}</div>`
        : '<div class="card-wait">快读卡由 agent 生成（未连接）。先看摘要：</div>';
    body = wait + (p.abstract ? `<div class="abstract">${esc(p.abstract)}</div>` : '');
  }
  return `<article class="paper">
    ${inReader ? '' : `<h1 class="paper-title">${esc(p.title)}</h1>`}
    <div class="paper-meta">${esc(authors(p.authors))}${p.published ? ' · ' + esc(p.published) : ''} · ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(idLabel(p.id))} ↗</a>` : esc(idLabel(p.id))}${p.page_count ? ` · ${p.page_count} 页` : ' · <span style="color:var(--amber)">没有全文</span>'}
    ${capture && p.captures?.length > 1 ? ` · <a href="#" data-action="select" data-kind="paper" data-id="${esc(p.id)}">共 ${p.captures.length} 个来源</a>` : ''}
    ${capture ? ` · <a href="#" data-action="unlink" data-id="${capture.id}" data-paper="${esc(p.id)}" title="这条捕获不是讲这篇">不是这篇？</a>` : ''}</div>
    ${topicsRow(p)}
    ${p.page_count ? '' : `<div class="notice" style="margin-top:12px">没下载到这篇论文的 PDF（可能需要登录或不公开）。上传 PDF 后才能写快读卡、深读和对照原文。<br>${pdfButton({ paper: p.id })}</div>`}
    ${body}
    <div class="research-row"><span class="hint">研究</span>${(p.research || []).map(researchChip).join('') || '<span class="hint">还没有用到这篇</span>'}
      <button class="btn sm ghost" data-action="paper-research" data-pid="${esc(p.id)}">+ 加入研究</button></div>
    ${inReader ? '' : `<div class="paper-actions">
      <button class="btn primary" data-action="read" data-pid="${esc(p.id)}">进入深读 →</button>
      <button class="btn" data-action="read" data-pid="${esc(p.id)}" data-tab="chat">讨论这篇</button>
      <button class="btn ${deep ? 'primary' : ''}" data-action="paper-deep" data-pid="${esc(p.id)}" data-on="${deep ? '' : '1'}">${deep ? '已在待深读' : '加入待深读'}</button></div>`}
  </article>`;
}

function topicsRow(p, kind = 'paper') {
  const open = S.picker === p.id;
  const attrs = (topic) => `data-kind="${kind}" data-pid="${esc(p.id)}" data-topic="${esc(topic)}"`;
  const chips = p.topics.map((t) => `<span class="chip topic">${esc(t)}<button data-action="topic-remove" ${attrs(t)} title="移除">×</button></span>`).join('');
  const picker = open ? `<div class="picker" data-stop>${S.state.topics.map((t) => `
      <label><input type="checkbox" data-action="topic-toggle" ${attrs(t.path)} ${p.topics.includes(t.path) ? 'checked' : ''}>${esc(t.name)}</label>
      ${t.children.map((ch) => `<label class="child"><input type="checkbox" data-action="topic-toggle" ${attrs(ch.path)} ${p.topics.includes(ch.path) ? 'checked' : ''}>${esc(ch.name)}</label>`).join('')}`).join('')}
      ${kind === 'paper' ? '<div class="foot">你调整后，agent 不会再改这篇论文的主题。</div>' : ''}</div>` : '';
  const hint = kind === 'research' ? '' : p.topics_locked ? '<span class="hint">· 你调整过</span>' : p.topics.length ? '<span class="hint">· agent 归类</span>' : '';
  return `<div class="topics-row">${chips || '<span class="chip">未归类</span>'}
    <button class="btn sm ghost" data-action="picker" data-pid="${esc(p.id)}">${p.topics.length ? '调整' : '+ 归类'}</button>${hint}${picker}</div>`;
}

// ---------- reader ----------

async function openReader(pid, page, push = true) {
  if (push) { S.pushing = true; location.hash = `#/read/${enc(pid)}${page ? '@' + page : ''}`; return; }
  const fresh = S.reader?.pid !== pid;
  S.reader = { ...(fresh ? { tab: S.pendingTab || 'card' } : S.reader), pid, page: page || S.reader?.page || 1 };
  S.pendingTab = null;
  try {
    S.reader.paper = await get(`/api/papers/${enc(pid)}`);
  } catch (e) { fail(e); return; }
  renderReader(fresh);
  if (S.reader.tab === 'chat') await loadThread(`paper:${pid}`).then(renderPanel);
}

function closeReader(nav = true) {
  if (!S.reader) return;
  S.reader = null;
  $('#reader').hidden = true;
  $('#reader').innerHTML = '';
  if (nav) S.readerPushed ? history.back() : go(S.view, S.arg);
  S.readerPushed = false;
}

function renderReader(fresh) {
  const r = S.reader, p = r.paper, el = $('#reader');
  el.hidden = false;
  if (fresh || !el.querySelector('.pdf, .pdf-missing')) {
    el.innerHTML = `<div class="reader-top">
        <button class="btn sm ghost" data-action="close-reader">← 返回 <kbd>Esc</kbd></button>
        <div class="title">${esc(p.title)}</div>
        ${p.url ? `<a class="btn sm ghost" href="${esc(p.url)}" target="_blank">${esc(idLabel(p.id).split(' ')[0])} ↗</a>` : ''}
        <button class="btn sm ghost" data-action="wide">调整面板宽度</button>
      </div>
      <div class="reader-body ${S.wide ? 'wide' : ''}">
        ${p.pdf_path ? `<iframe class="pdf" src="/api/papers/${enc(p.id)}/pdf#page=${r.page}"></iframe>` : `<div class="pdf-missing"><div style="text-align:center">还没有 PDF<br>${pdfButton({ paper: p.id })}</div></div>`}
        <div class="panel"><div class="tabs" id="tabs"></div><div class="panel-body" id="panel"></div></div>
      </div>`;
  }
  renderPanel();
}

function jump(page) {
  const f = $('#reader .pdf');
  if (f) f.src = `/api/papers/${enc(S.reader.pid)}/pdf?p=${page}#page=${page}`;
  S.reader.page = page;
}

function renderPanel() {
  const r = S.reader;
  if (!r) return;
  const p = r.paper;
  const tabs = [['card', '快读卡'], ['report', '深读报告'], ['chat', '讨论'], ['notes', '想法与笔记', p.thoughts.length + p.notes.length]];
  $('#tabs').innerHTML = tabs.map(([k, label, n]) => `<button class="tab ${r.tab === k ? 'on' : ''}" data-action="tab" data-tab="${k}">${label}${n ? `<span class="n">${n}</span>` : ''}</button>`).join('');
  const panel = $('#panel');
  panel.classList.toggle('chatwrap', r.tab === 'chat');
  if (r.tab === 'card') panel.innerHTML = paperCard(p, { inReader: true });
  else if (r.tab === 'report') panel.innerHTML = reportHtml(p);
  else if (r.tab === 'notes') panel.innerHTML = notesBlock(p).replace('class="block"', 'class="block" style="margin-top:0"');
  else { panel.innerHTML = chatHtml(`paper:${p.id}`); scrollChat(); }
}

function reportHtml(p) {
  const run = p.runs.find((x) => x.kind === 'deepread' && live(x.status));
  if (run) return `<div class="card-wait"><span class="spin"></span>Agent 正在写深读报告，通常需要几分钟…</div>${runCard(run, '深读')}`;
  if (!p.report) {
    const last = p.runs.find((x) => x.kind === 'deepread');
    return `<div class="chat-empty" style="margin-top:40px"><b>还没有深读报告</b>
      <p>agent 会通读全文，按“动机 → 方法 → 实验 → 结果 → 局限 → 对你的启发”写报告，每段都能跳回原文；你记过的想法和问题会优先回答。</p>
      <button class="btn primary" data-action="deepread" data-pid="${esc(p.id)}" ${agentOn() ? '' : 'disabled'}>生成深读报告</button>
      ${agentOn() ? '' : '<p class="hint">需要先连接 agent</p>'}
      ${last ? runCard(last, '上次深读') : ''}</div>`;
  }
  return p.report.sections.map((s) => `<div class="section"><h3>${esc(s.heading)}</h3>${md(s.body)}
      ${s.citations.length ? `<div class="cites">${s.citations.map((c) => cite(p.id, c.page, c.quote)).join('')}</div>` : ''}</div>`).join('') +
    `<div style="margin-top:20px"><button class="btn sm ghost" data-action="deepread" data-pid="${esc(p.id)}" ${agentOn() ? '' : 'disabled'}>重新生成</button></div>`;
}

// ---------- discussion ----------

const SUGGEST = {
  paper: ['用三句话讲清这篇的核心贡献', '和 π0、OpenVLA 相比有什么不同？', '真机实验做了哪些任务、成功率多少？'],
  topic: ['这个方向最近在解决什么问题？', '列出代表性方法和它们的关键区别'],
  thoughts: ['把我最近的想法归纳成几个方向', '哪些想法和库里的论文有关？'],
  library: ['最近收的论文里，哪几篇值得深读？', '库里有谁做过灵巧手相关的工作？'],
  research: ['这个研究的问题成立吗？最强的反对意见是什么？', '和库里最接近的工作相比，方法上的区别在哪？', '帮我把验证方案收敛到一个最小实验'],
};

function scopeLabel(scope) {
  const [k, ...rest] = scope.split(':');
  const ref = rest.join(':');
  if (k === 'research') return `研究《${S.scopeTitles[scope] || '…'}》`;
  return k === 'paper' ? '这篇论文' : k === 'topic' ? `主题 ${ref}` : k === 'thoughts' ? '我的想法' : '整个论文库';
}

function chatHtml(scope) {
  const t = S.threads[scope];
  const kind = scope.split(':')[0];
  let msgs;
  if (!t) msgs = '<div class="chat-empty"><span class="spin"></span></div>';
  else if (!t.messages.length) {
    msgs = `<div class="chat-empty"><b>讨论${esc(scopeLabel(scope))}</b><p class="hint">回答会引用原文页码；值得留下的结论可以存成笔记。</p>
      <div class="sugs">${SUGGEST[kind].map((s) => `<button class="sug" data-action="sug" data-scope="${esc(scope)}">${esc(s)}</button>`).join('')}</div></div>`;
  } else msgs = t.messages.map((m) => msgHtml(m, scope)).join('');
  const disabled = !agentOn();
  return `<div class="chat" data-scope="${esc(scope)}">
    <div class="chat-head">范围：${esc(scopeLabel(scope))}${t?.proposal ? `<a href="#" class="chip violet" data-action="open-research" data-id="${esc(scope.slice(9))}">有修改稿待你确认 →</a>` : ''}${t?.pinned ? `<span class="chip amber">${ago(t.created_at)}的一段讨论</span>` : ''}<span class="grow"></span>
      ${t?.pinned ? `<button class="btn sm ghost" data-action="chat-latest" data-scope="${esc(scope)}">回到最新对话</button>` : ''}
      ${t?.agent_id ? `<a class="btn sm ghost" href="https://cursor.com/agents/${esc(t.agent_id)}" target="_blank">在 Cursor 中继续 ↗</a>` : ''}
      ${t?.messages.length ? `<button class="btn sm ghost" data-action="chat-new" data-scope="${esc(scope)}">新对话</button>` : ''}</div>
    <div class="msgs">${msgs}</div>
    <form class="chat-input" data-form="chat" data-scope="${esc(scope)}">
      <textarea rows="1" placeholder="${disabled ? 'Agent 未连接，暂时不能讨论' : '问点什么… 回车发送，Shift+回车换行'}" ${disabled ? 'disabled' : ''}></textarea>
      <button class="btn primary" ${disabled ? 'disabled' : ''}>发送</button></form></div>`;
}

function msgHtml(m, scope) {
  const flash = S.highlight === m.id ? ' flash' : '';
  const researchActs = `${(m.research || []).map(researchChip).join('')}<button class="btn sm ghost" data-action="msg-research" data-scope="${esc(scope)}" data-mid="${m.id}" title="作为素材加入研究">加入研究</button>`;
  if (m.role === 'user') return `<div class="msg-wrap user" id="m-${m.id}"><div class="msg user${flash}">${esc(m.text)}</div><div class="msg-foot right">${researchActs}</div></div>`;
  const text = m.run_text || m.text;
  const running = live(m.run_status);
  const tools = m.run_tools || [];
  return `<div class="msg bot${flash}" id="m-${m.id}">${text ? md(text) : ''}
    ${running ? `<div class="typing"><span class="spin"></span>${text ? '还在写…' : tools.length ? '正在查阅论文库…' : 'Agent 启动中…'}</div>
      ${tools.length ? `<div class="tools">${tools.slice(-3).map((x) => `<div>${x.status === 'running' ? '…' : '✓'} ${esc(x.name)} ${esc(x.args || '')}</div>`).join('')}</div>` : ''}` : ''}
    ${m.run_status === 'ERROR' ? `<div class="hint" style="color:var(--red)">agent 出错了${m.run_error ? '：' + esc(m.run_error) : ''}</div>` : ''}
    ${!running && text ? `<div class="msg-foot"><button class="btn sm ghost" data-action="save-note" data-scope="${esc(scope)}" data-mid="${m.id}">存为笔记</button>${researchActs}</div>` : ''}</div>`;
}

function scrollChat() {
  const target = S.highlight && document.getElementById(`m-${S.highlight}`);
  if (target) {
    target.scrollIntoView({ block: 'center' });
    clearTimeout(S.flashTimer);
    S.flashTimer = setTimeout(() => (S.highlight = null), 4000);
    return;
  }
  document.querySelectorAll('.msgs').forEach((el) => (el.scrollTop = el.scrollHeight));
}

const scopeKey = () => S.scope;

function discussPage() {
  const topics = S.state ? S.state.topics.flatMap((t) => [t, ...t.children]) : [];
  const scope = S.scope;
  const chip = (s, label) => `<button class="scope ${scope === s ? 'on' : ''}" data-action="scope" data-scope="${esc(s)}">${esc(label)}</button>`;
  return `<div class="page"><div style="padding:18px 20px 10px"><h2 class="page-title">讨论</h2>
    <div class="scope-bar">${chip('library', '整个论文库')}${chip('thoughts', '我的想法')}${scope.startsWith('research:') ? chip(scope, scopeLabel(scope)) : ''}
      <select class="scope ${scope.startsWith('topic:') ? 'on' : ''}" data-change="scope"><option value="">按主题…</option>
      ${topics.map((t) => `<option value="topic:${esc(t.path)}" ${scope === 'topic:' + t.path ? 'selected' : ''}>${t.path.includes('/') ? '　' : ''}${esc(t.name)}</option>`).join('')}</select></div></div>
    <div style="flex:1;min-height:0;border-top:1px solid var(--line)">${chatHtml(scope)}</div></div>`;
}

async function sendChat(scope, text) {
  if (!text.trim()) return;
  try {
    const t = S.threads[scope];
    await post('/api/chat', { scope, text, thread_id: t?.pinned ? t.id : null });
    await loadThread(scope);
    refreshChat(scope);
  } catch (e) {
    fail(e);
    return false;
  }
  return true;
}

function refreshChat(scope) {
  const box = document.querySelector(`.chat[data-scope="${CSS.escape(scope)}"]`);
  if (!box) return;
  const input = box.querySelector('textarea');
  const draft = input?.value, focused = document.activeElement === input;
  const wrap = box.parentElement;
  wrap.innerHTML = chatHtml(scope);
  const ni = wrap.querySelector('textarea');
  if (ni && draft) ni.value = draft;
  if (ni && focused) ni.focus();
  scrollChat();
}

// ---------- thoughts page ----------

function thoughtsPage() {
  let lastDay = '';
  const items = S.thoughts.map((t) => {
    const d = day(t.created_at);
    const head = d !== lastDay ? `<div class="t-day">${d}</div>` : '';
    lastDay = d;
    const about = t.about_paper
      ? `<a href="#" data-action="read" data-pid="${esc(t.about_paper)}" data-tab="notes">${esc(short(t.paper_title, 60))}</a>`
      : t.capture_id ? `<a href="#" data-action="goto-capture" data-id="${t.capture_id}">${esc(short(t.capture_title || '来自一条捕获', 50))}</a>` : '<span>独立想法</span>';
    return `${head}<div class="t-item"><div class="t-text">${esc(t.text)}</div><div class="t-meta"><span>${ago(t.created_at)}</span>·${about}${t.by === 'agent' ? '<span class="chip">agent 记录</span>' : ''}<span class="grow"></span>${thoughtResearch(t)}</div></div>`;
  }).join('');
  return `<div class="page pad"><h2 class="page-title">想法</h2><p class="hint" style="margin:0 0 12px">一闪念都在这里。和论文绑在一起的，会在深读和讨论时被调出来。</p>
    <form class="add-line" data-form="thought"><input placeholder="记一个想法…（回车保存）" style="padding:9px 12px;font-size:14.5px"><button class="btn primary">记下</button></form>
    ${items || '<div class="empty"><b>还没有想法</b>收下内容时在“想法”一栏顺手写一句，或者在这里直接记。</div>'}
    <div style="margin-top:20px"><button class="btn" data-action="discuss-thoughts">和 agent 聊聊我的想法 →</button></div></div>`;
}

// ---------- capture bar ----------

const capText = $('#cap-text'), capThought = $('#cap-thought');

function autosize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px'; }

const accepted = (f) => f.type.startsWith('image/') || f.type === 'application/pdf';
const readBase64 = (f) => new Promise((ok, bad) => {
  const reader = new FileReader();
  reader.onload = () => ok(reader.result.split(',')[1]);
  reader.onerror = bad;
  reader.readAsDataURL(f);
});

function renderImages() {
  $('#cap-images').innerHTML = S.images.map((img, i) => `<div class="thumb">${img.mime === 'application/pdf'
    ? `<div style="display:grid;place-items:center;height:100%;font-size:11px;padding:4px;text-align:center;color:var(--muted);word-break:break-all"><b style="color:var(--red)">PDF</b>${esc(short(img.name, 18))}</div>`
    : `<img src="data:${img.mime};base64,${img.data}">`}<button type="button" data-action="rm-image" data-i="${i}">×</button></div>`).join('');
}

async function addFiles(files) {
  for (const f of files) {
    if (!accepted(f)) { toast(`不支持 ${f.name}：只收图片和 PDF`); continue; }
    if (S.images.length >= 5) { toast('一次最多 5 个文件'); break; }
    S.images.push({ mime: f.type, name: f.name, data: await readBase64(f) });
    renderImages();
  }
  capText.focus();
}

async function submitCapture() {
  const text = capText.value.trim(), thought = capThought.value.trim(), images = S.images;
  if (!text && !images.length && !thought) { capText.focus(); return; }
  capText.value = ''; capThought.value = ''; S.images = []; renderImages(); autosize(capText);
  try {
    const cap = await post('/api/captures', { text, thought, images });
    if (cap.duplicate) toast(`${ago(cap.created_at)}收过这条，已移回收件箱${thought ? '并补上想法' : ''}`);
    else toast('已收下，正在整理', '撤销', async () => {
      await api('DELETE', `/api/captures/${enc(cap.id)}`).catch(fail);
      capText.value = text; capThought.value = thought; S.images = images; renderImages(); autosize(capText); capText.focus();
      refresh(true);
    });
    if (S.view !== 'inbox') go('inbox');
    else { await loadState(); await load(); }
    select('capture', cap.id);
  } catch (e) {
    capText.value = text; capThought.value = thought; S.images = images; renderImages();
    fail(e);
  }
}

$('#capture').addEventListener('submit', (e) => { e.preventDefault(); submitCapture(); });
capText.addEventListener('input', () => autosize(capText));
for (const el of [capText, capThought]) {
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitCapture(); }
  });
}

document.addEventListener('paste', (e) => {
  const files = [...(e.clipboardData?.files || [])].filter(accepted);
  const typing = e.target instanceof Element && e.target.closest('input, textarea');
  if (files.length && (!typing || e.target === capText || e.target === capThought)) {
    e.preventDefault();
    addFiles(files);
  } else if (!typing && !S.reader) {
    const text = e.clipboardData.getData('text');
    if (text) { e.preventDefault(); capText.value = text; autosize(capText); capText.focus(); }
  }
});

let dragDepth = 0;
document.addEventListener('dragenter', (e) => { if (e.dataTransfer?.types.includes('Files')) { dragDepth++; $('#cap-box').classList.add('drop'); } });
document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#cap-box').classList.remove('drop'); } });
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  $('#cap-box').classList.remove('drop');
  if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  else {
    const text = e.dataTransfer.getData('text');
    if (text) { capText.value = text; autosize(capText); capText.focus(); }
  }
});

// ---------- actions ----------

async function triage(id, t, quiet) {
  const prev = (S.list.find((x) => x.id === id) || S.detail?.cap || {}).triage;
  const idx = S.list.findIndex((x) => x.id === id);
  await post(`/api/captures/${enc(id)}/triage`, { triage: t });
  const label = { deep: '已加入待深读', archived: '已存档', ignored: '已忽略', inbox: '已移回收件箱' }[t];
  if (!quiet) toast(label, prev && prev !== t ? '撤销' : null, async () => { await post(`/api/captures/${enc(id)}/triage`, { triage: prev }); refresh(true); });
  await loadState();
  const v = VIEWS[S.view];
  if (v.kind === 'captures' && S.view !== 'pending') {
    S.list = await get(`/api/captures?view=${S.view}`);
    renderBody();
    if (S.sel?.id === id && !S.list.some((x) => x.id === id)) {
      const next = S.list[Math.min(idx, S.list.length - 1)];
      if (next) select('capture', next.id); else { S.sel = null; S.detail = null; renderDetail(); }
      return;
    }
  }
  loadDetail(true);
}

async function saveTopics(kind, id, topics) {
  try {
    await post(`/api/${kind === 'research' ? 'research' : 'papers'}/${enc(id)}/topics`, { topics });
    await loadState();
    if (S.reader?.pid === id) { S.reader.paper = await get(`/api/papers/${enc(id)}`); renderPanel(); }
    loadDetail(true);
    if (VIEWS[S.view].kind !== 'page') load();
  } catch (e) { fail(e); }
}

const topicOwner = (el) => (el.dataset.kind === 'research' ? S.detail.research
  : S.reader?.paper?.id === el.dataset.pid ? S.reader.paper : S.detail.papers.find((x) => x.id === el.dataset.pid));

// ---------- research: gather materials from anywhere, jump back to them ----------

const firstSentence = (s, n = 30) => short((s || '').replace(/[#*`>\-\[\]]/g, '').split(/[。！？\n.!?]/).find((x) => x.trim())?.trim() || '', n);

async function researchPicker(ctx) {
  S.pickerCtx = ctx;
  const all = await get('/api/research').catch(() => []);
  const row = (x) => `<div class="pick" data-action="research-join" data-id="${x.id}" data-name="${esc(x.title.toLowerCase())}">${stageChip(x.stage)}<span class="pick-title">${esc(x.title)}</span><span class="hint">${esc(x.topics.join(' '))}</span></div>`;
  modal(`<h3>${esc(ctx.heading || '加入研究')}</h3>
    ${all.length ? `<p class="hint" style="margin:0 0 4px">作为素材加入已有的研究：</p><input class="picker-filter" data-filter="pick" placeholder="筛选…"><div class="pick-list">${all.map(row).join('')}</div>
      <div class="block-title" style="margin-top:16px">或者开一个新研究<span class="line"></span></div>` : ''}
    <form class="research-create" data-form="research-create">
      <input name="title" value="${esc(ctx.title || '')}" placeholder="研究名称（30 字以内）">
      <textarea name="summary" rows="2" placeholder="一句话：想研究什么（可以先空着）">${esc(ctx.summary || '')}</textarea>
      <p class="hint">新研究从<b>构思中</b>开始：先攒素材，再推敲三要素（研究问题、研究方法、验证方案），由你确认确立。${ctx.topics?.length ? `主题：${esc(ctx.topics.join('、'))}。` : '主题跟随素材里的论文，之后可以改。'}</p>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="btn" data-action="close-modal">取消</button><button class="btn primary">开新研究</button></div>
    </form>`);
  (all.length ? $('.picker-filter') : $('.research-create input')).focus();
}

function afterResearch(id, message) {
  closeModal();
  toast(message, '查看', () => openItem('research', id, 'research'));
  loadState();
  if (S.reader) get(`/api/papers/${enc(S.reader.pid)}`).then((p) => { S.reader.paper = p; if (S.reader.tab !== 'chat') renderPanel(); });
  for (const scope of Object.keys(S.threads)) loadThread(scope).then(() => refreshChat(scope));
  if (S.view === 'thoughts') get('/api/thoughts').then((t) => { S.thoughts = t; renderBody(); });
  loadDetail(true);
}

function openItem(kind, id, view) {
  const fromReader = !!S.reader;
  if (fromReader) closeReader(false);
  if (S.view === view && !S.arg) { if (fromReader) go(view); select(kind, id); return; }
  S.wantSel = { kind, id };
  go(view);
}

async function openThreadAt(scope, threadId, mid) {
  S.highlight = mid || null;
  if (scope.startsWith('paper:')) {
    const pid = scope.slice(6);
    await loadThread(scope, threadId);
    if (S.reader?.pid === pid) { S.reader.tab = 'chat'; renderPanel(); } else { S.pendingTab = 'chat'; openReader(pid); }
    return;
  }
  S.scope = scope;
  S.threadId = threadId;
  const fromReader = !!S.reader;
  if (fromReader) closeReader(false);
  if (S.view === 'discuss') { if (fromReader) go('discuss'); load(); } else go('discuss');
}

const openSource = {
  paper: (el) => openReader(el.dataset.ref),
  thought: (el) => (el.dataset.paper ? (S.pendingTab = 'notes', openReader(el.dataset.paper)) : openItem('capture', el.dataset.capture, 'inbox')),
  message: (el) => openThreadAt(el.dataset.scope, el.dataset.thread, el.dataset.ref),
  thread: (el) => openThreadAt(el.dataset.scope, el.dataset.ref),
  capture: (el) => openItem('capture', el.dataset.ref, 'inbox'),
  note: (el) => el.dataset.paper && (S.pendingTab = 'notes', openReader(el.dataset.paper)),
};

async function libraryPaperPicker(rid) {
  const papers = await get('/api/papers');
  S.pickerCtx = { rid };
  modal(`<h3>关联库里的论文</h3><input class="picker-filter" data-filter="pick" placeholder="按标题筛选…">
    <div class="pick-list">${papers.map((p) => `<div class="pick" data-action="research-pick-paper" data-pid="${esc(p.id)}" data-name="${esc(p.title.toLowerCase())}"><span class="pick-title serif">${esc(p.title)}</span><span class="hint">${esc(idLabel(p.id))}</span></div>`).join('') || '<p class="hint">库里还没有论文</p>'}</div>`);
  $('.picker-filter').focus();
}

async function researchPost(path, body, okMessage) {
  try {
    await post(`/api/research/${path}`, body);
    if (okMessage) toast(okMessage);
  } catch (e) {
    fail(e);
  }
  loadDetail(true);
  load();
  loadState();
}

const actions = {
  go: (el) => { if (el.dataset.view === 'topic' && S.view === 'topic' && S.arg === el.dataset.arg) return; go(el.dataset.view, el.dataset.arg); },
  'toggle-tree': (el, e) => { e.stopPropagation(); const p = el.dataset.path; S.open.has(p) ? S.open.delete(p) : S.open.add(p); renderSide(); },
  select: (el, e) => { e.preventDefault(); select(el.dataset.kind, el.dataset.id); },
  back: () => $('#body').classList.remove('show-detail'),
  triage: (el, e) => { e.stopPropagation(); triage(el.dataset.id, el.dataset.t).catch(fail); },
  'undo-capture': async (el) => {
    if (!confirm('撤销这次收下？这条捕获、它的想法和关联会被删除，论文本身会保留。')) return;
    await api('DELETE', `/api/captures/${enc(el.dataset.id)}`).catch(fail);
    toast('已撤销');
    S.sel = null; S.detail = null; refresh(true);
  },
  link: async (el) => {
    el.disabled = true; el.innerHTML = '<span class="spin"></span>拉取全文…';
    try { await post(`/api/captures/${enc(el.dataset.id)}/link`, { paper: el.dataset.paper }); toast('已关联'); refresh(true); }
    catch (e) { fail(e); el.disabled = false; el.textContent = '就是这篇'; }
  },
  unlink: async (el, e) => {
    e.preventDefault();
    if (!confirm('这条捕获不是讲这篇论文？之后 agent 也不会再把它们关联起来。')) return;
    await post(`/api/captures/${enc(el.dataset.id)}/unlink`, { paper: el.dataset.paper }).catch(fail);
    refresh(true);
  },
  reprocess: async (el) => {
    await post(`/api/captures/${enc(el.dataset.id)}/reprocess`, { agent: !!el.dataset.agent }).catch(fail);
    toast(el.dataset.agent ? '已重新交给规则和 agent' : '正在重跑规则预处理');
    setTimeout(() => refresh(true), 600);
  },
  'cancel-run': async (el) => { await post(`/api/runs/${enc(el.dataset.id)}/cancel`).catch(fail); refresh(true); },
  read: (el, e) => { e.preventDefault(); S.pendingTab = el.dataset.tab || null; if (S.reader && S.reader.pid === el.dataset.pid) { S.reader.tab = el.dataset.tab || 'card'; renderPanel(); } else openReader(el.dataset.pid); },
  'close-reader': () => closeReader(),
  wide: () => { S.wide = !S.wide; $('#reader .reader-body').classList.toggle('wide', S.wide); },
  tab: async (el) => {
    S.reader.tab = el.dataset.tab;
    renderPanel();
    if (el.dataset.tab === 'chat') { await loadThread(`paper:${S.reader.pid}`); renderPanel(); }
  },
  cite: (el, e) => {
    e.preventDefault(); e.stopPropagation();
    const page = +el.dataset.page, pid = el.dataset.pid;
    if (S.reader?.pid === pid) jump(page); else { S.pendingTab = 'card'; openReader(pid, page); }
  },
  picker: (el, e) => { e.stopPropagation(); S.picker = S.picker === el.dataset.pid ? null : el.dataset.pid; S.reader ? renderPanel() : renderDetail(); },
  'topic-toggle': (el) => {
    const set = new Set(topicOwner(el).topics);
    el.checked ? set.add(el.dataset.topic) : set.delete(el.dataset.topic);
    saveTopics(el.dataset.kind, el.dataset.pid, [...set]);
  },
  'topic-remove': (el, e) => {
    e.stopPropagation();
    saveTopics(el.dataset.kind, el.dataset.pid, topicOwner(el).topics.filter((t) => t !== el.dataset.topic));
  },
  'open-research': (el, e) => { e.preventDefault(); e.stopPropagation(); openItem('research', el.dataset.id, 'research'); },
  'open-source': (el, e) => { if (e.target.closest('.x')) return; openSource[el.dataset.kind](el); },
  'thought-research': (el, e) => {
    e.stopPropagation();
    const links = [{ kind: 'thought', ref: el.dataset.id }, ...(el.dataset.paper ? [{ kind: 'paper', ref: el.dataset.paper }] : [])];
    researchPicker({ heading: '把这个想法加入研究', title: firstSentence(el.dataset.text), summary: el.dataset.text, links });
  },
  'msg-research': (el) => {
    const scope = el.dataset.scope, m = S.threads[scope].messages.find((x) => x.id === el.dataset.mid);
    const text = plain(m.run_text || m.text), [k, ...rest] = scope.split(':'), ref = rest.join(':');
    const links = [{ kind: 'message', ref: m.id }, ...(k === 'paper' ? [{ kind: 'paper', ref }] : [])];
    researchPicker({ heading: '把这段讨论加入研究', title: firstSentence(text), summary: short(text, 300), links, topics: k === 'topic' ? [ref] : [] });
  },
  'paper-research': (el) => researchPicker({ heading: '把这篇论文加入研究', links: [{ kind: 'paper', ref: el.dataset.pid }] }),
  'research-join': async (el) => {
    try {
      for (const l of S.pickerCtx.links) await post(`/api/research/${enc(el.dataset.id)}/links`, l);
      afterResearch(el.dataset.id, `已作为素材加入「${el.querySelector('.pick-title').textContent}」`);
    } catch (e) { fail(e); }
  },
  'research-edit': (el) => { S.editResearch = S.editResearch === el.dataset.id ? null : el.dataset.id; renderDetail(); $('.research-form textarea[name="problem"]')?.focus(); },
  'research-draft': async (el) => {
    try { await post(`/api/research/${enc(el.dataset.id)}/draft`); toast('已交给 agent 起草，写好后会作为修改稿等你确认'); loadDetail(true); }
    catch (e) { fail(e); }
  },
  'research-discuss': (el) => {
    const scope = `research:${el.dataset.id}`;
    S.scopeTitles[scope] = S.detail.research.title;
    openThreadAt(scope);
  },
  proposal: (el) => {
    const op = el.dataset.op, x = S.detail.research;
    if (op === 'accept-establish' && !confirm('采用修改稿并确立这个研究？')) return;
    researchPost(`${enc(el.dataset.id)}/proposal`, { action: op === 'discard' ? 'discard' : 'accept', establish: op === 'accept-establish', base_rev: x.revision },
      { accept: '已采用修改稿', 'accept-establish': '已采用并确立', discard: '已放弃修改稿' }[op]);
  },
  'research-establish': (el) => {
    if (!confirm('确认确立这个研究？确立表示你认可现在的研究问题、研究方法和验证方案。')) return;
    researchPost(`${enc(el.dataset.id)}/establish`, { established: true }, '研究已确立');
  },
  'research-reopen': (el) => researchPost(`${enc(el.dataset.id)}/establish`, { established: false }, '已撤回确立，回到构思中'),
  'research-delete': async (el) => {
    if (!confirm('删除这个研究？它的素材（论文、想法、讨论）都会保留。')) return;
    await api('DELETE', `/api/research/${enc(el.dataset.id)}`).catch(fail);
    S.sel = null; S.detail = null; toast('已删除'); load(); loadState(); renderDetail();
  },
  'research-unlink': async (el, e) => {
    e.stopPropagation();
    await post(`/api/research/${enc(el.dataset.id)}/unlink`, { kind: el.dataset.kind, ref: el.dataset.ref }).catch(fail);
    loadDetail(true);
  },
  'research-add-paper': (el) => libraryPaperPicker(el.dataset.id),
  'research-pick-paper': async (el) => {
    await post(`/api/research/${enc(S.pickerCtx.rid)}/links`, { kind: 'paper', ref: el.dataset.pid }).catch(fail);
    closeModal(); toast('已关联'); loadDetail(true);
  },
  'chat-latest': async (el) => { S.threads[el.dataset.scope].pinned = false; await loadThread(el.dataset.scope); refreshChat(el.dataset.scope); },
  'paper-deep': async (el) => {
    await post(`/api/papers/${enc(el.dataset.pid)}/triage`, { triage: el.dataset.on ? 'deep' : 'inbox' }).catch(fail);
    toast(el.dataset.on ? '已加入待深读' : '已移回收件箱');
    refresh(true);
  },
  deepread: async (el) => {
    el.disabled = true;
    try {
      await post(`/api/papers/${enc(el.dataset.pid)}/deepread`);
      S.reader.paper = await get(`/api/papers/${enc(el.dataset.pid)}`);
      renderPanel();
    } catch (e) { fail(e); el.disabled = false; }
  },
  sug: (el) => sendChat(el.dataset.scope, el.textContent),
  'chat-new': async (el) => { S.threads[el.dataset.scope] = await post('/api/threads', { scope: el.dataset.scope }); refreshChat(el.dataset.scope); },
  'save-note': async (el) => {
    const scope = el.dataset.scope, t = S.threads[scope], m = t.messages.find((x) => x.id === el.dataset.mid);
    const [k, ...rest] = scope.split(':'), ref = rest.join(':');
    try {
      await post('/api/notes', { text: m.run_text || m.text, paper_id: k === 'paper' ? ref : null, topic: k === 'topic' ? ref : null, thread_id: t.id });
      el.textContent = '已存为笔记'; el.disabled = true;
      if (S.reader) S.reader.paper = await get(`/api/papers/${enc(S.reader.pid)}`);
    } catch (e) { fail(e); }
  },
  scope: async (el) => { S.scope = el.dataset.scope; renderBody(); await loadThread(S.scope); renderBody(); },
  'discuss-thoughts': async () => { S.scope = 'thoughts'; go('discuss'); },
  'goto-capture': (el, e) => { e.preventDefault(); openItem('capture', el.dataset.id, 'inbox'); },
  'rm-image': (el) => { S.images.splice(+el.dataset.i, 1); renderImages(); },
  'close-modal': closeModal,
};

document.addEventListener('click', (e) => {
  if (S.picker && !e.target.closest('[data-stop], [data-action="picker"]')) { S.picker = null; S.reader ? renderPanel() : renderDetail(); }
  const el = e.target.closest('[data-action]');
  if (el && actions[el.dataset.action]) actions[el.dataset.action](el, e);
  if (e.target.closest('#agent-status')) setupModal();
});

document.addEventListener('change', async (e) => {
  const up = e.target.dataset.upload;
  if (up && e.target.files[0]) {
    const f = e.target.files[0], label = e.target.parentElement;
    label.innerHTML = '<span class="spin"></span>读取 PDF…';
    try {
      const data = await readBase64(f);
      if (up === 'paper') await post(`/api/papers/${enc(e.target.dataset.id)}/pdf`, { data });
      else await post(`/api/captures/${enc(e.target.dataset.id)}/pdf`, { data });
      toast('PDF 已加入');
      if (S.reader) { S.reader.paper = await get(`/api/papers/${enc(S.reader.pid)}`); renderReader(true); }
      refresh(true);
    } catch (err) { fail(err); refresh(true); }
    return;
  }
  if (e.target.dataset.change === 'scope' && e.target.value) {
    S.scope = e.target.value; renderBody(); await loadThread(S.scope); renderBody();
  }
});

document.addEventListener('input', (e) => {
  if (e.target.dataset.filter !== 'pick') return;
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('.pick').forEach((el) => (el.style.display = !q || el.dataset.name.includes(q) ? '' : 'none'));
});

async function submitResearchForm(form) {
  const kind = form.dataset.form, f = new FormData(form);
  if (kind === 'research-new') {
    const title = form.querySelector('input').value.trim();
    if (!title) return;
    const x = await post('/api/research', { title, topics: form.dataset.topic ? [form.dataset.topic] : [] });
    form.querySelector('input').value = '';
    toast('已开新研究（构思中）');
    S.wantSel = { kind: 'research', id: x.id };
    loadState(); load();
  } else if (kind === 'research-create') {
    const title = (f.get('title') || '').trim();
    if (!title) { toast('给这个研究起个名字'); return; }
    const c = S.pickerCtx;
    const x = await post('/api/research', { title, summary: f.get('summary'), topics: c.topics || [], links: c.links || [] });
    afterResearch(x.id, '已开新研究（构思中）');
  } else if (kind === 'research-edit') {
    const body = Object.fromEntries(FIELDS.map(([k]) => [k, (f.get(k) || '').trim() || null]));
    await post(`/api/research/${enc(form.dataset.id)}`, { title: f.get('title'), summary: f.get('summary'), body, base_rev: +form.dataset.rev });
    S.editResearch = null;
    toast('已保存');
    loadDetail(true); load();
  }
}

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('[data-form]');
  if (!form) return;
  e.preventDefault();
  if (form.dataset.form.startsWith('research-')) {
    try { await submitResearchForm(form); } catch (err) { fail(err); if (/已被修改/.test(err.message)) loadDetail(true); }
    return;
  }
  const input = form.querySelector('input, textarea');
  const value = input.value.trim();
  if (!value) return;
  const kind = form.dataset.form;
  try {
    if (kind === 'thought') {
      await post('/api/thoughts', { text: value, capture_id: form.dataset.capture || null, paper_id: form.dataset.paper || null });
      input.value = '';
      toast('已记下');
      if (S.view === 'thoughts') { S.thoughts = await get('/api/thoughts'); renderBody(); }
      if (S.reader) { S.reader.paper = await get(`/api/papers/${enc(S.reader.pid)}`); renderPanel(); }
      loadState(); loadDetail(true);
    } else if (kind === 'link') {
      input.disabled = true;
      await post(`/api/captures/${enc(form.dataset.capture)}/link`, { paper: value });
      toast('已关联');
      refresh(true);
    } else if (kind === 'chat') {
      input.value = '';
      autosize(input);
      if (!(await sendChat(form.dataset.scope, value))) input.value = value;
    }
  } catch (err) {
    input.disabled = false;
    fail(err);
  }
});

document.addEventListener('keydown', (e) => {
  const target = e.target instanceof Element ? e.target : document.body;
  if (target.matches('.chat-input textarea')) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); e.target.form.requestSubmit(); }
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (S.reader) closeReader(); capText.focus(); return; }
  if (e.key === 'Escape') {
    if (document.querySelector('.modal-bg')) return closeModal();
    if (S.picker) { S.picker = null; return S.reader ? renderPanel() : renderDetail(); }
    if (S.reader) return closeReader();
    document.activeElement?.blur();
    return;
  }
  if (target.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (S.reader) return;
  if (e.key === '/') { e.preventDefault(); $('#search').focus(); return; }
  const v = VIEWS[S.view];
  if (v.kind === 'page' || !S.list.length) return;
  const i = S.list.findIndex((x) => x.id === S.sel?.id);
  const key = e.key.toLowerCase();
  if (key === 'j' || e.key === 'ArrowDown' || key === 'k' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = S.list[Math.max(0, Math.min(S.list.length - 1, i + (key === 'j' || e.key === 'ArrowDown' ? 1 : -1)))];
    select(itemKind(next), next.id);
    document.querySelector(`.item[data-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter' && S.detail?.papers?.[0]) {
    openReader(S.detail.papers[0].id);
  } else if (v.kind === 'captures' && S.sel && { d: 1, a: 1, x: 1 }[key]) {
    triage(S.sel.id, { d: 'deep', a: 'archived', x: 'ignored' }[key]).catch(fail);
  }
});

$('#search').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) go('search', e.target.value.trim());
  if (e.key === 'Escape') e.target.blur();
});

window.addEventListener('hashchange', parseHash);

// ---------- live refresh ----------

async function refresh(force) {
  try {
    await loadState();
    const v = VIEWS[S.view];
    const editing = document.activeElement?.closest?.('#list, #detail') && document.activeElement.matches('input, textarea, select');
    if (['captures', 'papers', 'research'].includes(v.kind)) {
      const list = v.kind === 'captures' ? await get(`/api/captures?view=${S.view}`)
        : v.kind === 'research' ? await get('/api/research' + (S.arg ? `?topic=${enc(S.arg)}` : ''))
          : await get('/api/papers' + (S.view === 'topic' ? `?topic=${enc(S.arg)}` : S.view === 'unsorted' ? '?unsorted=1' : ''));
      const typingInList = document.activeElement?.closest?.('#list') && document.activeElement.matches('input');
      if ((force || changed('list', list)) && !typingInList) { S.list = list; renderBody(); }
    }
    // the agent and the iPhone change things behind our back; loadDetail only re-renders when the data changed
    if (S.sel && !editing && !S.picker && !S.editResearch && !document.querySelector('.modal-bg')) await loadDetail(force);
    if (S.view === 'thoughts' && force) { S.thoughts = await get('/api/thoughts'); renderBody(); }
    if (S.reader) {
      const p = await get(`/api/papers/${enc(S.reader.pid)}`);
      if (force || changed('reader', p)) {
        S.reader.paper = p;
        const typing = document.activeElement?.closest?.('#panel') && document.activeElement.matches('input, textarea');
        if (!typing && !S.picker && S.reader.tab !== 'chat') renderPanel();
      }
    }
    for (const [scope, t] of Object.entries(S.threads)) {
      if (t.messages.some((m) => live(m.run_status))) {
        await loadThread(scope);
        refreshChat(scope);
      }
    }
  } catch (e) { /* server restarting; next tick */ }
}

setInterval(refresh, 2500);

(async function init() {
  await loadState().catch(fail);
  if (!location.hash) location.hash = '#/inbox';
  parseHash();
  if (S.view === 'inbox' && !S.list.length) load();
})();
