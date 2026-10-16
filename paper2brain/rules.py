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


def validate_card(card, pages):
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


IDEA_LABELS = {
    "problem": "问题", "insight": "核心洞见", "approach": "方法思路",
    "novelty": "与已有工作的区别", "experiment": "最小验证实验", "risks": "风险与未知",
}
_PAPER_CITE = re.compile(r"\[((?:arxiv|doi|openreview|pdf):[^\s\]]+?)(?:\s*p\.?\s*\d+)?\]", re.I)


def validate_idea_draft(draft, known_papers):
    """An agent's refined idea: every section filled, and every [paper p.N] citation pointing at a paper in the library."""
    if not isinstance(draft, dict):
        raise Rejected("Idea 草稿必须是 JSON 对象")
    errors = []
    title, summary = _text(draft, "title", errors), _text(draft, "summary", errors)
    body = {k: _text(draft, k, errors, required=k != "novelty") for k in IDEA_LABELS}
    cited = {parse_pid(m) for v in [summary or "", *filter(None, body.values())] for m in _PAPER_CITE.findall(v)}
    missing = sorted(p for p in cited if p and p not in known_papers)
    if missing:
        errors.append(f"引用了库里没有的论文：{missing}（先用 ./p2b fetch 入库，或去掉引用）")
    if errors:
        raise Rejected("Idea 草稿未通过校验：\n- " + "\n- ".join(errors))
    return {"title": title, "summary": summary, "body": body, "cited": sorted(p for p in cited if p)}


def check_link(existing, by):
    """The user's word on a capture–paper link is final: the agent may neither re-add a link the user removed
    nor change one the user confirmed."""
    if existing and existing["by"] == "user" and by != "user":
        state = "移除" if existing["state"] == "rejected" else "确认"
        raise Rejected(f"用户已{state}过这条关联，agent 不能修改")


def check_revision(current, base):
    if base is not None and base != current:
        raise Rejected(f"内容已被修改（当前版本 {current}，你基于 {base}），请重新读取后再提交")
