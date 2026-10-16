"""Deterministic processing shared by the CLI (agent) and the web server: no model involved here."""
import pymupdf

from . import rules, sources, store

AUTO_CONFIRM_SCORE = 0.95


def fetch_paper(pid):
    """Metadata, PDF and per-page text for an arXiv paper. Idempotent: skips what is already there."""
    if not pid.startswith("arxiv:"):
        raise rules.Rejected("目前只支持 arXiv 论文（ID 形如 arxiv:2410.24164）")
    paper = store.get_paper(pid)
    if not paper:
        found = sources.arxiv_lookup([pid[6:]])
        if not found:
            raise rules.Rejected(f"arXiv 上找不到 {pid}")
        m = found[0]
        store.upsert_paper(pid, m["title"], m["authors"], m["abstract"], m["published"], m["url"])
    if not store.pages(pid):
        path = store.paper_dir(pid) / "paper.pdf"
        if not path.exists():
            sources.download(f"https://arxiv.org/pdf/{pid[6:]}", path)
        with pymupdf.open(path) as doc:
            store.set_fulltext(pid, path, [page.get_text() for page in doc])
    return store.get_paper(pid)


def _confirm(capture_id, arxiv_id, evidence):
    pid = rules.paper_id(arxiv_id)
    fetch_paper(pid)
    try:
        store.link(capture_id, pid, "rule", evidence=evidence)
    except rules.Rejected:
        pass  # the user removed this link earlier; respect it


def ingest(capture_id):
    """Snapshot the article, find which paper(s) it is about, fetch them. Confident matches are linked;
    anything less becomes candidates for the agent or the user to confirm. Never loses the capture."""
    cap = store.get_capture(capture_id)
    if not cap:
        raise rules.Rejected(f"找不到捕获 {capture_id}")
    store.update_capture(capture_id, status="resolving", note=None)
    notes, info = [], {}
    try:
        direct = rules.find_arxiv_ids(cap["raw"])
        page_ids = []
        for url in rules.find_urls(cap["raw"])[:3]:
            if sources.channel_of(url) == "arxiv":
                continue
            try:
                page = sources.fetch_page(url)
            except Exception as e:  # network or anti-crawling: keep going with what we have
                notes.append(f"抓取 {url[:60]} 失败：{e}")
                continue
            folder = store.home() / "assets" / capture_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "snapshot.html").write_text(page.pop("html"), encoding="utf-8")
            info = info or page
            page_ids += rules.find_arxiv_ids(page["text"] + "\n" + "\n".join(page["links"]))
        page_ids = [i for i in dict.fromkeys(page_ids) if i not in direct]

        for aid in direct:
            _confirm(capture_id, aid, f"原文直接给出了 arXiv:{aid}")
        candidates = []
        if not direct and len(page_ids) == 1:
            _confirm(capture_id, page_ids[0], f"解读里只出现了这一个论文链接 arXiv:{page_ids[0]}")
        elif page_ids:
            for m in sources.arxiv_lookup(page_ids[:8]):
                candidates.append({**m, "why": "解读中出现的论文链接"})
        if not direct and not page_ids:
            guesses = sources.guess_titles(info.get("text") or cap["raw"], info.get("title"))[:2]
            for guess in guesses:
                found = sources.arxiv_search(guess)
                if found and found[0]["score"] >= AUTO_CONFIRM_SCORE:
                    _confirm(capture_id, found[0]["arxiv_id"], f"标题匹配（相似度 {found[0]['score']}）：{guess[:80]}")
                    candidates = []
                    break
                candidates += [{**m, "why": f"按标题检索：{guess[:60]}"} for m in found[:3]]
            if guesses and not candidates and not store.get_capture(capture_id)["papers"]:
                notes.append("在 arXiv 上按这些标题都没找到（可能不在 arXiv 上）：" + "；".join(guesses))

        linked = [p for p in store.get_capture(capture_id)["papers"] if p["state"] == "confirmed"]
        if linked:
            status = "mapped"
        elif candidates:
            status = "needs_confirm"
        elif cap["attachments"]:
            status = "needs_agent"
            notes.append("截图需要 agent 看图识别论文")
        else:
            status = "unmapped"
        store.update_capture(
            capture_id,
            status=status,
            note="\n".join(notes) or None,
            channel=info.get("channel") or ("arxiv" if direct else None),
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
