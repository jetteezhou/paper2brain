"""Pure write rules. No I/O: callers pass in the current state, these decide whether a write may land."""
import re
import unicodedata


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
        raise Rejected("深读报告必须是 {sections: [{heading, body, citations}]}")
    if not pages:
        raise Rejected("这篇论文还没有全文（./p2b fetch 没拿到 PDF）。请在回复里请用户上传 PDF，或用 ./p2b fetch <PDF 链接> 补上")
    errors, sections, total = [], [], 0
    for i, s in enumerate(report["sections"], 1):
        if not isinstance(s, dict):
            errors.append(f"第 {i} 节必须是对象")
            continue
        heading, body = _text(s, "heading", errors), _text(s, "body", errors)
        fixed, cite_errors = verify_citations(s.get("citations") or [], pages)
        errors += [f"第 {i} 节：{e}" for e in cite_errors]
        total += len(fixed)
        sections.append({"heading": heading, "body": body, "citations": fixed})
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
        row = {"component": r["component"].strip(), "file": r["file"].strip().lstrip("./"),
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


def check_link(existing, by):
    """The user's word on a capture–paper link is final: the agent may neither re-add a link the user removed
    nor change one the user confirmed."""
    if existing and existing["by"] == "user" and by != "user":
        state = "移除" if existing["state"] == "rejected" else "确认"
        raise Rejected(f"用户已{state}过这条关联，agent 不能修改")


def check_revision(current, base):
    if base is not None and base != current:
        raise Rejected(f"内容已被修改（当前版本 {current}，你基于 {base}），请重新读取后再提交")
