"""Deterministic processing shared by the CLI (agent) and the web server: no model involved here."""
import hashlib
import re

import pymupdf

from . import rules, sources, store

AUTO_CONFIRM_SCORE = 0.95


def _meta(pid):
    kind, _, ref = pid.partition(":")
    if kind == "arxiv":
        found = sources.arxiv_lookup([ref])
        if not found:
            raise rules.Rejected(f"arXiv 上找不到 {pid}")
        return {**found[0], "pdf_url": f"https://arxiv.org/pdf/{ref}", "arxiv_id": None}
    if kind == "doi":
        return sources.doi_lookup(ref)
    if kind == "openreview":
        return sources.openreview_lookup(ref)
    raise rules.Rejected(f"不认识的论文 ID：{pid}")


def _pages(data):
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [page.get_text() for page in doc], (doc.metadata or {}).get("title") or ""


def fetch_paper(pid, pdf=None):
    """Metadata, PDF and per-page text for arXiv / DOI / OpenReview papers. Returns the stored paper, whose ID may be
    the canonical one (a DOI with an arXiv copy becomes the arXiv paper). Idempotent. A paper whose PDF cannot be
    downloaded is kept with metadata only; uploading a PDF later completes it."""
    pid = rules.parse_pid(pid) or pid
    paper = store.get_paper(pid)
    pdf_url = None
    if not paper:
        try:
            m = _meta(pid)
        except rules.Rejected:
            raise
        except Exception as e:
            raise rules.Rejected(f"查不到 {pid} 的信息：{e}") from None
        if m.get("arxiv_id"):
            return fetch_paper("arxiv:" + m["arxiv_id"], pdf)
        if not m.get("title"):
            raise rules.Rejected(f"查不到 {pid} 的标题")
        store.upsert_paper(pid, m["title"], m["authors"], m["abstract"], m["published"], m["url"])
        pdf_url = m.get("pdf_url")
    elif pid.startswith("arxiv:"):
        pdf_url = f"https://arxiv.org/pdf/{pid[6:]}"
    elif not store.pages(pid) and not pdf and not pid.startswith("pdf:"):
        try:
            pdf_url = _meta(pid).get("pdf_url")
        except Exception:
            pass
    if not store.pages(pid):
        path = store.paper_dir(pid) / "paper.pdf"
        if pdf:
            path.write_bytes(pdf)
        elif not path.exists() and pdf_url:
            try:
                sources.download(pdf_url, path)
            except Exception:
                pass  # metadata only for now
        if path.exists():
            store.set_fulltext(pid, path, _pages(path.read_bytes())[0])
    return store.get_paper(pid)


def paper_from_pdf(data, url=None):
    """Any PDF becomes a paper. An arXiv stamp or a single DOI on page 1 ties it to that paper; otherwise it is
    stored under its content hash with the title read from the PDF."""
    pages, meta_title = _pages(data)
    first = pages[0] if pages else ""
    arxiv = rules.find_arxiv_ids(first)
    dois = [r for r in rules.find_paper_refs(first) if r.startswith("doi:")]
    for ref in (["arxiv:" + arxiv[0]] if arxiv else []) + (dois if len(dois) == 1 else []):
        try:
            return fetch_paper(ref, pdf=data)
        except rules.Rejected:
            pass
    pid = "pdf:" + hashlib.sha256(data).hexdigest()[:16]
    if not store.get_paper(pid):
        lines = [ln.strip() for ln in first.splitlines() if len(ln.strip()) > 8]
        title = meta_title.strip() if len(meta_title.strip()) > 8 else (lines[0] if lines else "未命名 PDF")
        store.upsert_paper(pid, sources.clean_title(title), [], None, None, url)
        path = store.paper_dir(pid) / "paper.pdf"
        path.write_bytes(data)
        store.set_fulltext(pid, path, pages)
    return store.get_paper(pid)


def resolve_paper(s):
    """A paper from anything that names one: stored ID, arXiv/DOI/OpenReview link or number, or a link to a PDF."""
    pid = rules.parse_pid(s)
    if pid:
        return fetch_paper(pid)
    if re.match(r"https?://", (s or "").strip()):
        try:
            data = sources.download(s.strip())
        except Exception as e:
            raise rules.Rejected(f"这个链接既不是论文编号，也下载不到 PDF：{e}") from None
        return paper_from_pdf(data, s.strip())
    raise rules.Rejected(f"认不出论文：{s}（可以是 arXiv 链接或编号、DOI、OpenReview 链接，或 PDF 链接）")


def attach_pdf(pid, data):
    """Complete a metadata-only paper with a PDF the user supplied."""
    if not store.get_paper(pid):
        raise rules.Rejected("论文不在库里")
    if not data.startswith(b"%PDF"):
        raise rules.Rejected("这不是 PDF 文件")
    path = store.paper_dir(pid) / "paper.pdf"
    path.write_bytes(data)
    store.set_fulltext(pid, path, _pages(data)[0])
    return store.get_paper(pid)


def _confirm(capture_id, paper, evidence):
    try:
        store.link(capture_id, paper["id"], "rule", evidence=evidence)
    except rules.Rejected:
        pass  # the user removed this link earlier; respect it


def _candidate(pid, why):
    try:
        m = _meta(pid)
    except Exception:
        return None
    if m.get("arxiv_id"):
        pid = "arxiv:" + m["arxiv_id"]
    return {"pid": pid, "title": m["title"], "authors": m["authors"], "published": m["published"], "url": m["url"], "why": why}


def ingest(capture_id):
    """Snapshot the article, find which paper(s) it is about, fetch them. Confident matches are linked;
    anything less becomes candidates for the agent or the user to confirm. Never loses the capture."""
    cap = store.get_capture(capture_id)
    if not cap:
        raise rules.Rejected(f"找不到捕获 {capture_id}")
    store.update_capture(capture_id, status="resolving", note=None)
    notes, info, linked_any = [], {}, False

    def confirm(make, evidence):
        nonlocal linked_any
        try:
            _confirm(capture_id, make(), evidence)
            linked_any = True
        except rules.Rejected as e:
            notes.append(str(e))

    try:
        direct = rules.find_paper_refs(cap["raw"])
        page_refs = []
        for url in rules.find_urls(cap["raw"])[:3]:
            if rules.find_paper_refs(url):
                continue
            try:
                page = sources.fetch_page(url)
            except Exception as e:  # network or anti-crawling: keep going with what we have
                notes.append(f"抓取 {url[:60]} 失败：{e}")
                continue
            if "pdf" in page:
                confirm(lambda: paper_from_pdf(page["pdf"], page["url"]), "你给的是 PDF 链接")
                continue
            folder = store.home() / "assets" / capture_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "snapshot.html").write_text(page.pop("html"), encoding="utf-8")
            info = info or page
            page_refs += rules.find_paper_refs(page["text"] + "\n" + "\n".join(page["links"]))
        page_refs = [r for r in dict.fromkeys(page_refs) if r not in direct]

        for att in cap["attachments"]:
            if att["mime"] == "application/pdf":
                data = open(store.attachment(att["id"])["path"], "rb").read()
                confirm(lambda: paper_from_pdf(data), f"你上传的 PDF：{att['filename']}")
        for ref in direct:
            confirm(lambda: fetch_paper(ref), f"原文直接给出了 {rules.id_label(ref)}")
        candidates = []
        if not direct and len(page_refs) == 1:
            confirm(lambda: fetch_paper(page_refs[0]), f"解读里只出现了这一个论文链接 {rules.id_label(page_refs[0])}")
        elif page_refs:
            candidates = [c for c in (_candidate(r, "解读中出现的论文链接") for r in page_refs[:6]) if c]
        if not direct and not page_refs and not linked_any:
            guesses = sources.guess_titles(info.get("text") or cap["raw"], info.get("title"))[:2]
            for guess in guesses:
                found = sources.find_paper(guess)
                if found and found[0]["score"] >= AUTO_CONFIRM_SCORE:
                    confirm(lambda: fetch_paper(found[0]["pid"]), f"标题匹配（相似度 {found[0]['score']}）：{guess[:80]}")
                    candidates = []
                    break
                candidates += [{**m, "why": f"按标题检索：{guess[:60]}"} for m in found[:3]]
            if not candidates and not linked_any:
                terms = sources.name_terms(cap["raw"] if not info else " ".join([info.get("title") or "", *guesses]))
                found = sources.keyword_search(terms) if terms else []
                names = terms if len(terms) == 1 else [t for t in terms if sources.distinctive(t)]
                named = [r for r in found if any(sources.names_itself(r["title"], n) for n in names)]
                if len(named) == 1:
                    confirm(lambda: fetch_paper(named[0]["pid"]), f"论文标题就是在命名 {'、'.join(names)}，且只有这一篇")
                else:
                    ranked = named + [r for r in found if r not in named]
                    candidates = [{**r, "why": f"按关键词检索：{r['query']}"} for r in ranked[:5]]
                if not terms:
                    notes.append(("解读里没找到论文链接、英文标题或论文简称" if info else "只有中文描述，规则检索不了")
                                 + "。可以贴链接、英文标题或论文简称，拖入 PDF，或连接 agent 让它识别。")
                elif not candidates and not linked_any:
                    notes.append(f"在 arXiv 和 Crossref 上都没搜到：{' '.join(terms)}。可以贴链接或拖入 PDF。")

        if any(p["state"] == "confirmed" for p in store.get_capture(capture_id)["papers"]):
            status = "mapped"
        elif candidates:
            status = "needs_confirm"
        elif any(a["mime"].startswith("image/") for a in cap["attachments"]):
            status = "needs_agent"
            notes.append("截图需要 agent 看图识别论文")
        else:
            status = "unmapped"
        store.update_capture(
            capture_id,
            status=status,
            note="\n".join(notes) or None,
            channel=info.get("channel") or (re.sub(r":.*", "", direct[0]) if direct else None),
            url=info.get("url"),
            title=info.get("title"),
            author=info.get("author"),
            snapshot_text=info.get("text"),
            links=info.get("links"),
            candidates=candidates,
        )
    except Exception as e:
        store.update_capture(capture_id, status="failed", note="\n".join(notes + [f"处理失败：{e}"]))
        raise
    return store.get_capture(capture_id)


def process(capture_id, agent=True):
    """Rule-based preprocessing, then hand over to the librarian agent when one is configured."""
    from . import cursor_api

    try:
        ingest(capture_id)
    except Exception:
        pass  # recorded on the capture as failed; the agent may still make sense of it
    if agent and cursor_api.status()["enabled"] and cursor_api.config()["auto_agent"]:
        try:
            cursor_api.process_capture(capture_id)
        except Exception as e:
            cap = store.get_capture(capture_id)
            store.update_capture(capture_id, note="\n".join(filter(None, [cap["note"], f"启动 agent 失败：{e}"])))
