"""Pure write rules. No I/O: callers pass in the current state, these decide whether a write may land."""
import re
import unicodedata
import uuid
from datetime import date


class Rejected(ValueError):
    """A proposed write that must not land. The message is shown to the agent or the user as is."""


_ID = r"(\d{4}\.\d{4,5})(?:v\d+)?"
_ARXIV = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html)/|alphaxiv\.org/(?:abs|overview)/|huggingface\.co/papers/|10\.48550/arxiv\.|arxiv\s*[:：]\s*)" + _ID,
    re.I,
)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>，。、；）)】\]]+)", re.I)
_OPENREVIEW = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", re.I)
_URL = re.compile(r"https?://[^\s<>\"'，。、）)】]+", re.I)
PREFIXES = ("arxiv:", "doi:", "openreview:", "pdf:")


def find_arxiv_ids(text):
    """arXiv IDs in links or "arXiv:" mentions, in order. Bare numbers are ignored: too easy to confuse with dates."""
    seen = []
    for m in _ARXIV.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def find_paper_refs(text):
    """Paper identifiers in text, in order: arXiv IDs, DOIs (arXiv's own DOIs count as arXiv), OpenReview IDs."""
    text = text or ""
    refs = ["arxiv:" + a for a in find_arxiv_ids(text)]
    refs += ["doi:" + d.rstrip(".,;:").lower() for d in _DOI.findall(text) if not d.lower().startswith("10.48550/")]
    refs += ["openreview:" + o for o in _OPENREVIEW.findall(text)]
    return list(dict.fromkeys(refs))


def parse_pid(s):
    """A paper ID from what a person or the agent typed: a stored ID, a link, a DOI or a bare arXiv number."""
    s = (s or "").strip()
    if s.lower().startswith(PREFIXES):
        kind, _, ref = s.partition(":")
        kind = kind.lower()
        if kind == "arxiv":
            ref = re.sub(r"v\d+$", "", ref)
        return f"{kind}:{ref.lower() if kind == 'doi' else ref}"
    refs = find_paper_refs(s) or (["arxiv:" + m.group(1)] if (m := re.fullmatch(_ID, s)) else [])
    return refs[0] if refs else None


def find_urls(text):
    return list(dict.fromkeys(u.rstrip(".,;") for u in _URL.findall(text or "")))


def paper_id(arxiv_id):
    return "arxiv:" + arxiv_id


def title_short_name(title):
    """The name a title opens with ("Long-WAM: Scaling ..." -> "Long-WAM"): at most 3 words, each capitalized."""
    head, sep, _ = (title or "").partition(":")
    words = head.split()
    return head.strip() if sep and 1 <= len(words) <= 3 and len(head) <= 30 and all(re.match(r"[A-Z0-9π]", w) for w in words) else None


def id_label(pid):
    kind, _, ref = pid.partition(":")
    return {"arxiv": f"arXiv {ref}", "doi": f"DOI {ref}", "openreview": f"OpenReview {ref}"}.get(kind, "PDF")


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "").replace("\u00ad", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"[‐‑‒–—]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_citations(citations, pages):
    """Every quote must appear verbatim (modulo whitespace and ligatures) in the paper text.
    A wrong page number is corrected when the quote is found elsewhere; a quote found nowhere is an error."""
    norm_pages = [normalize(p) for p in pages]
    fixed, errors = [], []
    for i, c in enumerate(citations, 1):
        quote = (c or {}).get("quote") or ""
        q = normalize(quote)
        if len(q) < 8:
            errors.append(f"第 {i} 条引用太短（至少 8 个字符）：{quote!r}")
            continue
        page = c.get("page")
        if not (isinstance(page, int) and 1 <= page <= len(pages) and q in norm_pages[page - 1]):
            page = next((n for n, p in enumerate(norm_pages, 1) if q in p), None)
        if page is None:
            errors.append(f"第 {i} 条引用在原文中找不到：{quote[:80]!r}")
            continue
        fixed.append({**c, "quote": quote, "page": page})
    return fixed, errors


def _text(d, key, errors, required=True):
    v = d.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    if required:
        errors.append(f"缺少字段 {key}（非空字符串）")
    return None


def validate_card(card, pages, figures=()):
    """figures: the figures extracted from the PDF ({n, ...}); the card may pick up to four of them by number."""
    if not isinstance(card, dict):
        raise Rejected("快读卡必须是 JSON 对象")
    if not pages:
        raise Rejected("这篇论文还没有全文（./p2b fetch 没拿到 PDF）。请在回复里请用户上传 PDF，或用 ./p2b fetch <PDF 链接> 补上")
    errors = []
    out = {k: _text(card, k, errors) for k in ("tldr", "problem", "method", "relevance")}
    results = card.get("results")
    if not isinstance(results, list) or not results:
        errors.append("results 至少要有一条，每条为 {text, quote, page}")
        results = []
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict) or not _text(r, "text", [], True):
            errors.append(f"results 第 {i} 条缺少 text")
    fixed, cite_errors = verify_citations([r for r in results if isinstance(r, dict)], pages)
    errors += cite_errors
    out["results"] = fixed
    res = card.get("resources") or {}
    if not isinstance(res, dict):
        errors.append("resources 必须是对象")
        res = {}
    out["resources"] = {k: res.get(k) for k in ("code", "project", "data", "real_robot", "platform")}
    for k in ("code", "project", "data"):
        if out["resources"][k] is not None and not re.match(r"https?://[^\s\"'<>]+$", str(out["resources"][k])):
            errors.append(f"resources.{k} 只能是 http(s) 链接或 null：{str(out['resources'][k])[:80]!r}")
    takes = card.get("takes") or []
    if not isinstance(takes, list):
        errors.append("takes 必须是数组")
        takes = []
    out["takes"] = [
        {"capture_id": t.get("capture_id"), "take": t["take"].strip(), "consistent": t.get("consistent")}
        for t in takes
        if isinstance(t, dict) and isinstance(t.get("take"), str) and t["take"].strip()
    ]
    picks, known = card.get("figures") or [], sorted(f["n"] for f in figures)
    if not isinstance(picks, list) or len(picks) > 4:
        errors.append("figures 是数组，最多 4 项 [{n, note}]：只放方法 / 架构图和最能说明结果的图")
        picks = []
    out["figures"] = []
    for f in picks:
        n = f.get("n") if isinstance(f, dict) else f
        if n not in known:
            errors.append(f"figures 里的图 {n} 不存在。可用：{known}（./p2b figures <论文> 查看）" if known
                          else "这篇论文没有提取到插图，figures 留空")
            continue
        note = str(f.get("note") or "").strip() if isinstance(f, dict) else ""
        out["figures"].append({"n": n, "note": note or None})
    if errors:
        raise Rejected("快读卡未通过校验：\n- " + "\n- ".join(errors))
    return out


def validate_report(report, pages):
    if not isinstance(report, dict) or not isinstance(report.get("sections"), list) or not report["sections"]:
        raise Rejected("深读报告必须是 {sections: [{heading, points: [{text, citations}]}]}")
    if not pages:
        raise Rejected("这篇论文还没有全文（./p2b fetch 没拿到 PDF）。请在回复里请用户上传 PDF，或用 ./p2b fetch <PDF 链接> 补上")
    errors, sections, total = [], [], 0
    for i, s in enumerate(report["sections"], 1):
        if not isinstance(s, dict):
            errors.append(f"第 {i} 节必须是对象")
            continue
        heading, points = _text(s, "heading", errors), s.get("points")
        if not isinstance(points, list) or not points:
            errors.append(f"第 {i} 节：points 至少要有一段，每段为 {{text, citations}}")
            points = []
        out = []
        for j, pt in enumerate(points, 1):
            where = f"第 {i} 节第 {j} 段"
            if not isinstance(pt, dict) or not _text(pt, "text", [], True):
                errors.append(f"{where}缺少 text")
                continue
            fixed, cite_errors = verify_citations(pt.get("citations") or [], pages)
            errors += [f"{where}：{e}" for e in cite_errors]
            if not pt.get("citations"):
                errors.append(f"{where}：每段至少要有一条原文引用，写明它是从哪句话读出来的")
            n = len(pt.get("citations") or [])
            if {int(k) for k in re.findall(r"\[(\d+)\]", pt["text"])} != set(range(1, n + 1)):
                errors.append(f"{where}：在依据的那句话后面写 [1]、[2]… 标出它对应第几条引用；每条引用都要标，不能标不存在的编号")
            if len(pt["text"].strip()) > 300 or n > 3:
                errors.append(f"{where}太大（{len(pt['text'].strip())} 字、{len(pt.get('citations') or [])} 条引用，上限 300 字、3 条）："
                              "一段只讲一个要点，拆成几段，各自挂上依据的原句")
            total += len(fixed)
            out.append({"text": pt["text"].strip(), "citations": fixed})
        sections.append({"heading": heading, "points": out})
    if total < 3:
        errors.append("全文至少要有 3 条可核对的引用")
    if errors:
        raise Rejected("深读报告未通过校验：\n- " + "\n- ".join(errors))
    return {"sections": sections}


RESEARCH_LABELS = {"problem": "研究问题", "method": "研究方法", "validation": "验证方案", "notes": "其他"}
_PAPER_CITE = re.compile(r"\[((?:arxiv|doi|openreview|pdf):[^\s\]]+?)(?:\s*p\.?\s*\d+)?\]", re.I)


def validate_research_draft(draft, known_papers):
    """The agent's draft of a research: the three elements written, and every [paper p.N] citation pointing at a
    paper in the library."""
    if not isinstance(draft, dict):
        raise Rejected("研究修改稿必须是 JSON 对象")
    errors = []
    out = {"title": _text(draft, "title", errors), "summary": _text(draft, "summary", errors)}
    out.update({k: _text(draft, k, errors, required=k != "notes") for k in RESEARCH_LABELS})
    cited = {parse_pid(m) for v in filter(None, out.values()) for m in _PAPER_CITE.findall(v)}
    missing = sorted(p for p in cited if p and p not in known_papers)
    if missing:
        errors.append(f"引用了库里没有的论文：{missing}（先用 ./p2b fetch 入库，或去掉引用）")
    if errors:
        raise Rejected("研究修改稿未通过校验：\n- " + "\n- ".join(errors))
    return out, sorted(p for p in cited if p)


_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?")


def _numbers(text):
    return {float(n) for n in _NUM.findall((text or "").replace(",", ""))}


CHART_TYPES = ("bar", "line", "scatter", "bubble", "pie", "doughnut", "radar", "polarArea")


def _experiment_charts(charts, seen, errors):
    """Chart.js configs {title, type, data: {labels, datasets: [{label, data}]}, options}. Plotted values and numeric
    labels (x positions) must appear in the raw result; options pass through (JSON carries no callbacks)."""
    if not isinstance(charts or [], list):
        errors.append("charts 必须是数组 [{title, type, data, options}]")
        return []
    out = []
    for i, ch in enumerate(charts or [], 1):
        data = ch.get("data") if isinstance(ch, dict) else None
        if not isinstance(data, dict) or ch.get("type") not in CHART_TYPES:
            errors.append(f"charts 第 {i} 张需要 type（{' / '.join(CHART_TYPES)}）和 data")
            continue
        labels, sets = data.get("labels") or [], data.get("datasets")
        if not isinstance(labels, list) or not isinstance(sets, list) or not sets \
                or not all(isinstance(d, dict) and isinstance(d.get("data"), list) for d in sets):
            errors.append(f"charts 第 {i} 张的 data 需要 labels 数组和非空 datasets [{{label, data: [...]}}]")
            continue
        nums, bad = [str(x) for x in labels if _NUM.fullmatch(str(x).strip())], False
        for d in sets:
            for p in d["data"]:
                pts = p.values() if isinstance(p, dict) else [p]
                if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))) for v in pts):
                    bad = True
                nums += [str(v) for v in pts if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if bad:
            errors.append(f"charts 第 {i} 张的数据点只能是数字、null 或 {{x, y}}")
        missing = sorted({float(n) for n in nums} - seen)
        if missing:
            errors.append(f"charts 第 {i} 张（{ch.get('title') or ch['type']}）的数字 {missing} 在原始结果里找不到")
        if not isinstance(ch.get("options") or {}, dict):
            errors.append(f"charts 第 {i} 张的 options 必须是对象")
        out.append({"title": str(ch.get("title") or "").strip() or None, "type": ch["type"],
                    "data": {"labels": labels, "datasets": sets}, "options": ch.get("options") or {}})
    return out


def validate_experiment(draft, raw, context=""):
    """The agent's structured version of a pasted result. Every number in the results must appear in the raw input,
    so a figure can never be invented on the way from the log to the results table. The conclusion may also cite
    numbers from context (the research's own elements, e.g. a success threshold in the validation plan)."""
    if not isinstance(draft, dict):
        raise Rejected("实验记录必须是 JSON 对象")
    errors = []
    out = {"title": _text(draft, "title", errors), "target": _text(draft, "target", errors, required=False),
           "setup": _text(draft, "setup", errors, required=False),
           "conclusion_text": _text(draft, "conclusion_text", errors, required=False)}
    if draft.get("status", "done") not in ("planned", "running", "done", "abandoned"):
        errors.append("status 只能是 planned / running / done / abandoned")
    if draft.get("conclusion") not in (None, "supports", "refutes", "inconclusive"):
        errors.append("conclusion 只能是 supports / refutes / inconclusive 或 null")
    out["status"], out["conclusion"] = draft.get("status", "done"), draft.get("conclusion")
    results = draft.get("results") or []
    if not isinstance(results, list):
        errors.append("results 必须是数组 [{name, value, note}]")
        results = []
    seen = _numbers(raw)
    out["results"] = []
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict) or not str(r.get("name", "")).strip() or r.get("value") in (None, ""):
            errors.append(f"results 第 {i} 条需要 name 和 value")
            continue
        missing = [n for n in _numbers(str(r["value"])) if n not in seen]
        if missing:
            errors.append(f"results 第 {i} 条（{r['name']}）的数字 {missing} 在原始结果里找不到")
        out["results"].append({"name": str(r["name"]).strip(), "value": str(r["value"]).strip(), "note": (r.get("note") or "").strip() or None})
    out["charts"] = _experiment_charts(draft.get("charts"), seen, errors)
    allowed = seen | _numbers(context) | {n for r in out["results"] for n in _numbers(r["value"])}
    extra = [n for n in _numbers(out["conclusion_text"]) if n not in allowed]
    if extra:
        errors.append(f"结论里的数字 {extra} 在原始结果和研究三要素里都找不到")
    if errors:
        raise Rejected("实验记录未通过校验：\n- " + "\n- ".join(errors))
    return out


def _code_norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def validate_code_map(rows, pages, read_file):
    """Method–code map rows: each paper quote verbatim in the paper, each code snippet verbatim in the file at the
    pinned commit (whitespace-insensitive). Line numbers are recomputed from where the snippet actually is."""
    if not isinstance(rows, list) or not rows:
        raise Rejected("对照表至少要有一行：[{component, paper_quote, page, file, snippet, note}]")
    errors, out = [], []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict) or not _text(r, "component", [], True) or not _text(r, "file", [], True):
            errors.append(f"第 {i} 行需要 component 和 file")
            continue
        row = {"component": r["component"].strip(), "file": r["file"].strip().removeprefix("./"),
               "symbol": (r.get("symbol") or "").strip() or None, "note": (r.get("note") or "").strip() or None}
        if r.get("paper_quote"):
            fixed, cite_errors = verify_citations([{"quote": r["paper_quote"], "page": r.get("page")}], pages)
            errors += [f"第 {i} 行：{e}" for e in cite_errors]
            if fixed:
                row.update(paper_quote=fixed[0]["quote"], page=fixed[0]["page"])
        snippet = (r.get("snippet") or "").strip()
        content = read_file(row["file"])
        if content is None:
            errors.append(f"第 {i} 行：文件 {row['file']} 在这个 commit 里不存在")
        elif len(_code_norm(snippet)) < 10:
            errors.append(f"第 {i} 行：snippet 太短，请从文件里逐字摘一段（至少 10 个字符）")
        elif _code_norm(snippet) not in _code_norm(content):
            errors.append(f"第 {i} 行：snippet 在 {row['file']} 里找不到（要逐字摘录）")
        else:
            first = _code_norm(next((ln for ln in snippet.splitlines() if ln.strip()), ""))
            row["line"] = next((n for n, ln in enumerate(content.splitlines(), 1) if first in _code_norm(ln)), None)
            row["snippet"] = snippet
        out.append(row)
    if errors:
        raise Rejected("方法—代码对照未通过校验：\n- " + "\n- ".join(errors))
    return out


def validate_writing_draft(draft, known_papers):
    if not isinstance(draft, dict) or not any(isinstance(draft.get(k), str) and draft[k].strip() for k in ("abstract", "introduction")):
        raise Rejected("写作修改稿需要 abstract 或 introduction（Markdown 字符串）")
    out = {k: draft[k].strip() for k in ("abstract", "introduction") if isinstance(draft.get(k), str) and draft[k].strip()}
    cited = {parse_pid(m) for v in out.values() for m in _PAPER_CITE.findall(v)}
    missing = sorted(p for p in cited if p and p not in known_papers)
    if missing:
        raise Rejected(f"写作修改稿引用了库里没有的论文：{missing}（先用 ./p2b fetch 入库，或去掉引用）")
    if draft.get("note"):
        out["note"] = str(draft["note"]).strip()
    return out


PLAN_STATUS = ("todo", "doing", "done")


def validate_plan(tasks):
    """A research plan, the user's or the agent's: an ordered list of tasks. A task keeps its id, so a draft can be
    compared with the plan task by task; new tasks get one here."""
    if not isinstance(tasks, list) or len(tasks) > 80:
        raise Rejected("计划必须是任务列表（最多 80 条）")
    out, errors, seen = [], [], set()
    for i, t in enumerate(tasks, 1):
        title = str((t or {}).get("title") or "").strip() if isinstance(t, dict) else ""
        if not title:
            errors.append(f"第 {i} 条缺少 title")
            continue
        status, due = t.get("status") or "todo", t.get("due") or None
        if status not in PLAN_STATUS:
            errors.append(f"第 {i} 条 status 只能是 {' / '.join(PLAN_STATUS)}")
        if due is not None:
            try:
                date.fromisoformat(due)
            except (TypeError, ValueError):
                errors.append(f"第 {i} 条 due 要写成 YYYY-MM-DD")
        tid = str(t.get("id") or "") or uuid.uuid4().hex[:8]
        if tid in seen:
            errors.append(f"第 {i} 条 id 重复：{tid}")
        seen.add(tid)
        out.append({"id": tid, "title": title[:200], "status": status, "due": due, "note": str(t.get("note") or "").strip() or None})
    if errors:
        raise Rejected("计划未通过校验：\n- " + "\n- ".join(errors))
    return out


FIGURE_KINDS = ("table", "bar", "line", "radar")
# The parts of a paper's Experiments section, in the order they appear.
FIGURE_SECTIONS = ("main", "ablation", "analysis", "appendix")


def _named(items, what, errors, extra=(), most=8, least=1):
    """[{name, ...}] with unique non-empty names; extra: the other keys kept (stripped strings). A note is a line in
    the paper's setup, not an essay."""
    out = []
    for it in items if isinstance(items, list) else []:
        name = str((it or {}).get("name") or "").strip()[:60] if isinstance(it, dict) else ""
        if name:
            out.append({"name": name, **{k: str(it.get(k) or "").strip() or None for k in extra}})
    names = [x["name"] for x in out]
    if not least <= len(out) <= most or len(set(names)) < len(names):
        errors.append(f"setup.{what} 要有 {least}-{most} 项，name 不能重复")
    long = [x["name"] for x in out if len(x.get("note") or "") > 150]
    if long:
        errors.append(f"setup.{what} 的 note 每条不超过 150 字（论文里的一句话），过长：{long}")
    return out


def _calls_itself(name, text):
    """Whether a paper's own words (title, opening pages) use this name for the method; "π0 (5Hz)" counts as π0."""
    core = re.sub(r"\s*\(.*?\)\s*", " ", name).strip().lower()
    squash = lambda s: re.sub(r"[\s\-_]+", "", s.lower())  # noqa: E731
    return bool(core) and squash(core) in squash(text or "")


def validate_setup(setup, known_papers=(), paper_words=None):
    """The Experimental Setup a paper opens its experiments with, declared once so every figure uses the same
    names: research questions, benchmarks, baselines (published methods, each a library paper), controls (our own
    variants and simple heuristics), metrics (with which direction is better), our method's name."""
    s = setup if isinstance(setup, dict) else {}
    errors = []
    questions = [str(q).strip() for q in s.get("questions") or [] if str(q).strip()] if isinstance(s.get("questions"), list) else []
    if not 1 <= len(questions) <= 5 or any(len(q) > 200 for q in questions):
        errors.append("setup.questions 要有 1-5 个研究问题（论文里的 Q1、Q2……），每个不超过 200 字")
    method = str(s.get("method") or "").strip()[:40]
    if not method:
        errors.append("setup.method 写我们的方法在表里的名字")
    out = {"questions": questions, "method": method, "benchmarks": _named(s.get("benchmarks"), "benchmarks", errors, ("note",), 6),
           "baselines": _named(s.get("baselines"), "baselines", errors, ("paper", "note"), 6, 2),
           "controls": _named(s.get("controls"), "controls", errors, ("note",), 6, 0),
           "metrics": _named(s.get("metrics"), "metrics", errors, ("unit", "better"), 6),
           "implementation": str(s.get("implementation") or "").strip() or None}
    bad = [m["name"] for m in out["metrics"] if m["better"] not in ("higher", "lower")]
    if bad:
        errors.append(f"setup.metrics 的 better 只能是 higher / lower：{bad}")
    unknown = [b["name"] for b in out["baselines"] if (parse_pid(b["paper"] or "") or b["paper"]) not in known_papers]
    if unknown:
        errors.append(f"setup.baselines 必须是已发表的方法，paper 写库里的论文 id：{unknown}"
                      "（自己方法的变体、固定周期这类简单对照放 controls；论文不在库里先 ./p2b fetch）")
    for b in out["baselines"]:
        b["paper"] = parse_pid(b["paper"] or "") or b["paper"]
    if paper_words and not unknown:
        renamed = [f"{b['name']}（{b['paper']}）" for b in out["baselines"] if not _calls_itself(b["name"], paper_words(b["paper"]))]
        if renamed:
            errors.append(f"setup.baselines 要用论文自己的方法名，这些名字在论文标题和开头几页里找不到：{renamed}"
                          "（借了论文思路的自己的变体放 controls）")
    names = [method] + [x["name"] for k in ("baselines", "controls") for x in out[k]]
    if len(set(names)) < len(names):
        errors.append("method、baselines、controls 的名字不能重复")
    if len(out["implementation"] or "") > 800:
        errors.append("setup.implementation 不超过 800 字：只写论文 Implementation Details 段落的要点，评测协议写进各基准的 note")
    if errors:
        raise Rejected("实验设置未通过校验：\n- " + "\n- ".join(errors))
    return out


def validate_figures(figures, context="", setup=None):
    """The figures and tables the paper is expected to show, planned before the experiments run: a grid of rows ×
    series whose cells experiments fill later (validate_cells). Each sits in a part of the Experiments section,
    answers one of the setup's questions and uses its names. No measured numbers here; a target line may only
    use a number from the research's elements (the success line in the validation plan)."""
    if not setup:
        raise Rejected("先定实验设置（setup）：研究问题、基准、对比方法、指标")
    if not isinstance(figures, list) or len(figures) > 12:
        raise Rejected("预期图表必须是列表（最多 12 张）")
    out, errors, seen = [], [], set()
    allowed = _numbers(context)
    methods = [b["name"] for k in ("baselines", "controls") for b in setup.get(k) or []] + [setup["method"]]
    metrics = [m["name"] for m in setup["metrics"]]
    for i, f in enumerate(figures, 1):
        f = f if isinstance(f, dict) else {}
        title = str(f.get("title") or "").strip()
        if not title:
            errors.append(f"第 {i} 张缺少 title")
            continue
        if f.get("kind") not in FIGURE_KINDS:
            errors.append(f"「{title}」kind 只能是 {' / '.join(FIGURE_KINDS)}")
        if f.get("section") not in FIGURE_SECTIONS:
            errors.append(f"「{title}」section 只能是 {' / '.join(FIGURE_SECTIONS)}")
        if f.get("question") not in range(1, len(setup["questions"]) + 1):
            errors.append(f"「{title}」question 写它回答 setup.questions 里的第几个（1-{len(setup['questions'])}）")
        if not str(f.get("caption") or "").strip():
            errors.append(f"「{title}」缺少 caption（论文里的图注 / 表注）")
        rows, series = ([str(v).strip()[:60] for v in f.get(k) or []] if isinstance(f.get(k), list) else [] for k in ("rows", "series"))
        if not 1 <= len(rows) <= 40 or not all(rows) or len(set(rows)) < len(rows):
            errors.append(f"「{title}」rows 要有 1-40 个不重复的非空名字")
        if not 1 <= len(series) <= 12 or not all(series) or len(set(series)) < len(series):
            errors.append(f"「{title}」series 要有 1-12 个不重复的非空名字")
        metric = f.get("metric") or None
        if metric is None and not (series and all(s in metrics for s in series)):
            errors.append(f"「{title}」metric 写 setup.metrics 里的一个（只有 series 全是指标时可以省略）")
        elif metric is not None and metric not in metrics:
            errors.append(f"「{title}」metric「{metric}」不在 setup.metrics 里")
        if f.get("section") in ("main", "ablation"):
            dims = [d for d in (rows, series) if setup["method"] in d]
            if not dims:
                errors.append(f"「{title}」{'主结果' if f['section'] == 'main' else '消融'}要包含我们的方法「{setup['method']}」")
            elif f["section"] == "main":
                unknown = [v for v in dims[0] if v not in methods]
                if unknown:
                    errors.append(f"「{title}」主结果里的方法 {unknown} 不在 setup.baselines / controls 里")
                elif not any(b["name"] in dims[0] for b in setup["baselines"]):
                    errors.append(f"「{title}」主结果要和已发表的 baseline 比，不能只有自己的变体和对照")
        target = f.get("target") or None
        if target is not None:
            v = target.get("value") if isinstance(target, dict) else None
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors.append(f"「{title}」target 写成 {{value: 数字, label: 说明}}")
            elif float(v) not in allowed:
                errors.append(f"「{title}」target 的 {v} 在研究三要素里找不到（成功线要先写进验证方案）")
            else:
                target = {"value": v, "label": str(target.get("label") or "").strip()[:40] or None}
        fid = str(f.get("id") or "") or uuid.uuid4().hex[:8]
        if fid in seen:
            errors.append(f"第 {i} 张 id 重复：{fid}")
        seen.add(fid)
        out.append({"id": fid, "title": title[:80], "kind": f.get("kind"), "section": f.get("section"), "question": f.get("question"),
                    "caption": str(f.get("caption") or "").strip(), "claim": str(f.get("claim") or "").strip() or None, "metric": metric,
                    "rows": rows, "series": series, "x_title": str(f.get("x_title") or "").strip() or None,
                    "y_title": str(f.get("y_title") or "").strip() or None, "target": target})
    if errors:
        raise Rejected("预期图表未通过校验：\n- " + "\n- ".join(errors))
    return out


def check_coverage(setup, figures):
    """What the whole Experiments section owes the reader, checked on the agent's drafts: every research question
    is answered by a figure, every baseline appears in the main results."""
    errors = [f"研究问题 Q{i} 没有图表回答：补一张，或删掉这个问题" for i in range(1, len(setup["questions"]) + 1)
              if i not in {f["question"] for f in figures}]
    main = {v for f in figures if f["section"] == "main" for v in f["rows"] + f["series"]}
    missing = [b["name"] for b in setup["baselines"] if b["name"] not in main]
    if missing:
        errors.append(f"baseline {missing} 没进主结果：论文的主表要和全部 baseline 比，比不了的从 baselines 里删掉并在回复里说明原因")
    if errors:
        raise Rejected("实验设计未通过校验：\n- " + "\n- ".join(errors))


def validate_cells(cells, figures, experiments):
    """Which experiment result fills which cell of an expected figure: a pointer, never a number, so the value
    always comes from that experiment's checked results. experiment null clears the cell."""
    if not isinstance(cells, list):
        raise Rejected("cells 必须是数组 [{figure, row, series, experiment, result}]")
    figs, exps = {f["id"]: f for f in figures}, {e["id"]: e for e in experiments}
    out, errors = [], []
    for i, c in enumerate(cells, 1):
        c = c if isinstance(c, dict) else {}
        f, eid = figs.get(c.get("figure")), c.get("experiment")
        if not f:
            errors.append(f"第 {i} 格：没有 id 为 {c.get('figure')} 的预期图表")
            continue
        if c.get("row") not in f["rows"] or c.get("series") not in f["series"]:
            errors.append(f"第 {i} 格：「{f['title']}」没有 row={c.get('row')} / series={c.get('series')}")
            continue
        if eid is not None:
            e = exps.get(eid)
            if not e:
                errors.append(f"第 {i} 格：这个研究里没有实验 {eid}")
                continue
            if c.get("result") not in [r["name"] for r in e.get("results") or []]:
                errors.append(f"第 {i} 格：实验 {eid} 的 results 里没有「{c.get('result')}」（先整理那条实验）")
                continue
        out.append({"figure": f["id"], "row": c["row"], "series": c["series"], "experiment": eid, "result": eid and c["result"]})
    if errors:
        raise Rejected("图表格子未通过校验：\n- " + "\n- ".join(errors))
    return out


def validate_related(groups, known_papers):
    """Related work as lines of work: each names library papers, a paragraph for the paper's Related Work section,
    and one sentence on how this research differs. A group keeps its id across drafts, as plan tasks do."""
    if not isinstance(groups, list) or len(groups) > 12:
        raise Rejected("相关工作必须是方向列表（最多 12 条）")
    out, errors, seen = [], [], set()
    for i, g in enumerate(groups, 1):
        g = g if isinstance(g, dict) else {}
        name, text = str(g.get("name") or "").strip(), str(g.get("text") or "").strip()
        if not name:
            errors.append(f"第 {i} 条缺少 name")
            continue
        papers = list(dict.fromkeys(parse_pid(str(p)) or str(p) for p in g.get("papers") or [])) if isinstance(g.get("papers"), list) else []
        if not papers:
            errors.append(f"「{name}」至少要列一篇论文（papers）")
        missing = sorted({p for p in papers + [parse_pid(m) for m in _PAPER_CITE.findall(text)] if p and p not in known_papers})
        if missing:
            errors.append(f"「{name}」用到了库里没有的论文：{missing}（先用 ./p2b fetch 入库，或去掉）")
        gid = str(g.get("id") or "") or uuid.uuid4().hex[:8]
        if gid in seen:
            errors.append(f"第 {i} 条 id 重复：{gid}")
        seen.add(gid)
        out.append({"id": gid, "name": name[:80], "papers": papers, "text": text or None, "diff": str(g.get("diff") or "").strip() or None})
    if errors:
        raise Rejected("相关工作未通过校验：\n- " + "\n- ".join(errors))
    return out


def validate_comparison(table, groups):
    """The side-by-side table of the closest works and ours: columns are dimensions, each row a paper from the
    groups (or "ours", exactly once) with one short cell per column. None means the table is left as it is."""
    if table is None:
        return None
    t = table if isinstance(table, dict) else {}
    cols = [str(c).strip()[:40] for c in t.get("columns") or []] if isinstance(t.get("columns"), list) else []
    rows = t.get("rows") if isinstance(t.get("rows"), list) else []
    if not 2 <= len(cols) <= 8 or not all(cols):
        raise Rejected("对比表 columns 要有 2-8 个非空维度")
    if not 2 <= len(rows) <= 16:
        raise Rejected("对比表 rows 要有 2-16 行（最接近的工作 + 一行 ours）")
    in_groups = {p for g in groups for p in g["papers"]}
    out, errors = [], []
    for i, r in enumerate(rows, 1):
        r = r if isinstance(r, dict) else {}
        paper = "ours" if r.get("paper") == "ours" else parse_pid(str(r.get("paper") or "")) or str(r.get("paper") or "")
        cells = [str(c).strip()[:120] for c in r.get("cells") or []] if isinstance(r.get("cells"), list) else []
        if paper != "ours" and paper not in in_groups:
            errors.append(f"第 {i} 行的论文 {paper or '（空）'} 不在任何方向的 papers 里")
        if len(cells) != len(cols):
            errors.append(f"第 {i} 行有 {len(cells)} 格，应和 columns 一样是 {len(cols)} 格")
        out.append({"paper": paper, "name": str(r.get("name") or "").strip()[:40] or None, "cells": cells})
    if [r["paper"] for r in out].count("ours") != 1:
        errors.append('要有且只有一行 "paper": "ours"')
    if errors:
        raise Rejected("对比表未通过校验：\n- " + "\n- ".join(errors))
    return {"columns": cols, "rows": out}


def check_link(existing, by):
    """The user's word on a capture–paper link is final: the agent may neither re-add a link the user removed
    nor change one the user confirmed."""
    if existing and existing["by"] == "user" and by != "user":
        state = "移除" if existing["state"] == "rejected" else "确认"
        raise Rejected(f"用户已{state}过这条关联，agent 不能修改")


def check_revision(current, base):
    if base is not None and base != current:
        raise Rejected(f"内容已被修改（当前版本 {current}，你基于 {base}），请重新读取后再提交")
