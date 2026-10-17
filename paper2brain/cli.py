"""p2b: the only way the librarian agent writes to the library. Every command prints JSON."""
import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path

from . import pipeline, repos, rules, sources, store, structure


def to_pid(s):
    pid = rules.parse_pid(s)
    if not pid:
        raise rules.Rejected(f"认不出论文 ID：{s}（例：arxiv:2410.24164、doi:10.1109/xxx、pdf:… ；用 ./p2b show capture 查看已有 ID）")
    return pid


def _read_json(path):
    try:
        return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())
    except json.JSONDecodeError as e:
        raise rules.Rejected(f"JSON 格式错误：{e}")


def _pages(spec, total):
    if not spec:
        return range(1, total + 1)
    a, _, b = spec.partition("-")
    return range(max(1, int(a)), min(total, int(b or a)) + 1)


def run(args):
    cmd = args.cmd
    if cmd == "capture":
        images = [(Path(p).name, Path(p).read_bytes(), mimetypes.guess_type(p)[0] or "image/png") for p in args.image]
        cap, created = store.add_capture(args.id or str(uuid.uuid4()), args.text, args.source, args.thought, images)
        return {"created": created, "capture": cap}
    if cmd == "ingest":
        return pipeline.ingest(args.capture_id)
    if cmd == "fetch":
        p = pipeline.resolve_paper(args.paper)
        return {k: p[k] for k in ("id", "title", "page_count", "card_rev", "report_rev")}
    if cmd == "find":
        if args.keywords:
            return sources.keyword_search(args.title.split())
        return sources.find_paper(args.title)
    if cmd == "figures":
        pid = to_pid(args.paper)
        s = structure.get(pid)
        figdir = store.paper_dir(pid) / "figures"
        return {"figures": [{**f, "image": str(figdir / f"{f['n']}.png")} for f in s["figures"]], "outline": s["outline"]}
    if cmd == "link":
        pid = pipeline.resolve_paper(args.paper)["id"]
        store.link(args.capture_id, pid, "agent", evidence=args.evidence)
        store.update_capture(args.capture_id, status="mapped")
        return {"linked": pid, "has_fulltext": bool(store.pages(pid))}
    if cmd == "unlink":
        store.unlink(args.capture_id, to_pid(args.paper), "agent")
        return {"unlinked": True}
    if cmd in ("card", "report"):
        pid = to_pid(args.paper)
        pages = store.pages(pid)
        if cmd == "card":
            card = rules.validate_card(_read_json(args.file), pages, structure.get(pid)["figures"])
            rev = store.put_card(pid, card, "agent", args.base_rev)
        else:
            rev = store.put_report(pid, rules.validate_report(_read_json(args.file), pages), "agent", args.base_rev)
        return {"accepted": True, "revision": rev}
    if cmd == "topics":
        return store.topic_tree()
    if cmd == "topic":
        pid = to_pid(args.paper)
        store.set_topics(pid, args.paths, "agent")
        return {"paper": pid, "topics": args.paths}
    if cmd == "thought":
        if args.action == "show":
            return [store.get_thought(i) for i in args.ids]
        if args.action == "gist":
            store.set_gist(args.ids[0], args.text, "agent")
            return {"ok": True}
        if not args.text:
            raise rules.Rejected("--text 不能为空")
        return {"id": store.add_thought(args.text, "agent", args.capture, args.paper and to_pid(args.paper), args.gist)}
    if cmd == "note":
        return {"id": store.add_note(args.text, "agent", args.paper and to_pid(args.paper), args.topic, args.thread)}
    if cmd == "doc":
        d = store.get_doc(args.id)
        if not d:
            raise rules.Rejected("附件不存在")
        return {**d, "text": (d["text"] or "（图片附件，没有文字；内容已随提示词发给你）")[:60000]}
    if cmd == "repo":
        repo = store.get_repo(args.id)
        if not repo:
            raise rules.Rejected("仓库不存在")
        return {**repo, "recent_commits": repos.log(repo["path"], 8) if repo["status"] == "ready" else []}
    if cmd == "codemap":
        repo, pid = store.get_repo(args.repo), to_pid(args.paper)
        if not repo or repo["status"] != "ready":
            raise rules.Rejected("仓库还没克隆好")
        commit = repos.head(repo["path"])
        rows = rules.validate_code_map(_read_json(args.file), store.pages(pid), repos.reader(repo["path"], commit))
        store.put_code_map(args.research, repo["id"], pid, commit, rows, "agent")
        return {"accepted": True, "commit": commit, "rows": len(rows)}
    if cmd == "experiment":
        if args.action == "add":
            return {"id": store.add_experiment(args.research, args.raw, "agent", args.title)}
        e = store.get_experiment(args.id)
        if not e:
            raise rules.Rejected("实验不存在")
        if args.action == "show":
            e.pop("runs")
            return e
        body = (store.get_research(e["research_id"]) or {}).get("body") or {}
        draft = rules.validate_experiment(_read_json(args.file), e["raw"], "\n".join(filter(None, body.values())))
        rev = store.update_experiment(args.id, "agent", args.base_rev, **draft)
        return {"accepted": True, "revision": rev}
    if cmd == "writing":
        if args.action == "show":
            return store.get_writing(args.id)
        draft = rules.validate_writing_draft(_read_json(args.file), store.paper_ids())
        store.propose_writing(args.id, draft, "agent", args.base_rev)
        return {"proposed": True, "note": "写作修改稿已交给用户确认；采用前不会改动草稿"}
    if cmd == "research":
        if args.action == "list":
            return store.list_research("" if args.unsorted else (args.topic[0] if args.topic else None))
        if args.action == "add":
            links = [("paper", to_pid(p)) for p in args.paper] + [("thought", t) for t in args.thought] + \
                    [("thread", t) for t in args.thread] + [("capture", t) for t in args.capture]
            return {"id": store.add_research(args.title, "agent", args.summary, args.topic, links)}
        if args.action == "show":
            return store.get_research(args.id)
        if args.action == "link":
            ref = to_pid(args.ref) if args.kind == "paper" else args.ref
            store.link_research(args.id, args.kind, ref, "agent")
            return {"linked": [args.kind, ref]}
        if args.action == "propose":
            draft, cited = rules.validate_research_draft(_read_json(args.file), store.paper_ids())
            store.propose(args.id, draft, "agent", args.base_rev)
            for pid in cited:
                store.link_research(args.id, "paper", pid, "agent")
            return {"proposed": True, "note": "修改稿已交给用户确认；用户采用前不会改动研究内容"}
    if cmd == "show":
        if args.kind == "capture":
            cap = store.get_capture(args.id)
            if cap:
                cap["snapshot_text"] = (cap["snapshot_text"] or "")[:8000]
                cap["runs"] = [{k: r[k] for k in ("id", "kind", "status", "created_at")} for r in cap["runs"]]
            return cap
        p = store.get_paper(to_pid(args.id))
        if p:
            p.pop("runs")
        return p
    if cmd == "text":
        pid = to_pid(args.paper)
        pages = store.pages(pid)
        if not pages:
            raise rules.Rejected("这篇论文还没有全文。用 ./p2b fetch <PDF 链接> 补上，或请用户在网页上传 PDF")
        return [{"page": n, "text": pages[n - 1]} for n in _pages(args.pages, len(pages))]
    if cmd == "papers":
        return store.list_papers("" if args.unsorted else args.topic)
    if cmd == "thoughts":
        return store.list_thoughts()
    if cmd == "search":
        return store.search(args.query)
    if cmd == "inbox":
        from . import inbox
        return inbox.drain()
    if cmd == "serve":
        from . import server
        server.serve(args.host, args.port)


def parser():
    p = argparse.ArgumentParser(prog="p2b", description="paper2brain 论文库命令行")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("capture", help="收下一条内容（按 --id 幂等）")
    s.add_argument("action", choices=["add"])
    s.add_argument("--id")
    s.add_argument("--text", default="")
    s.add_argument("--thought")
    s.add_argument("--source", default="cli")
    s.add_argument("--image", action="append", default=[])

    sub.add_parser("ingest", help="规则预处理：抓快照、找论文、拉全文").add_argument("capture_id")
    sub.add_parser("fetch", help="拉取论文元数据与全文：arXiv/DOI/OpenReview 链接或编号，或 PDF 链接").add_argument("paper")
    s = sub.add_parser("find", help="按标题检索论文（arXiv + Crossref）；--keywords 按关键词（全部命中）检索 arXiv")
    s.add_argument("title")
    s.add_argument("--keywords", action="store_true")
    sub.add_parser("figures", help="论文插图（编号、页码、图注、图片路径）和章节目录").add_argument("paper")

    s = sub.add_parser("link", help="把捕获关联到论文")
    s.add_argument("capture_id")
    s.add_argument("paper")
    s.add_argument("--evidence", required=True, help="为什么判定是这篇")
    s = sub.add_parser("unlink")
    s.add_argument("capture_id")
    s.add_argument("paper")

    for name in ("card", "report"):
        s = sub.add_parser(name, help=f"提交{'快读卡' if name == 'card' else '深读报告'}（引用逐字核对）")
        s.add_argument("action", choices=["put"])
        s.add_argument("paper")
        s.add_argument("--file", required=True, help="JSON 文件路径，- 表示标准输入")
        s.add_argument("--base-rev", type=int)

    sub.add_parser("topics", help="主题树")
    s = sub.add_parser("topic", help="设置论文主题（覆盖 agent 之前的设置）")
    s.add_argument("action", choices=["set"])
    s.add_argument("paper")
    s.add_argument("paths", nargs="+")

    s = sub.add_parser("thought", help="想法：add 记一条 / show 查看（含论文、研究、同类想法）/ gist 写一句话提炼")
    s.add_argument("action", choices=["add", "show", "gist"])
    s.add_argument("ids", nargs="*", help="show/gist 时的想法 id")
    s.add_argument("--text", help="add：想法原文；gist：一句话提炼")
    s.add_argument("--gist", help="add 时可同时给出一句话提炼")
    s.add_argument("--capture")
    s.add_argument("--paper")
    s = sub.add_parser("note", help="记一条讨论结论")
    s.add_argument("action", choices=["add"])
    s.add_argument("--text", required=True)
    s.add_argument("--paper")
    s.add_argument("--topic")
    s.add_argument("--thread")

    sub.add_parser("doc", help="研究附件的全文（用户开研究时带来的构思稿、笔记等）").add_argument("id")
    sub.add_parser("repo", help="代码仓库（只读）：本地路径、当前 commit、最近提交").add_argument("id")
    s = sub.add_parser("codemap", help="提交方法—代码对照（论文引用和代码片段都逐字核对）")
    s.add_argument("action", choices=["put"])
    s.add_argument("--research", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--paper", required=True)
    s.add_argument("--file", required=True)
    s = sub.add_parser("experiment", help="实验记录：add / show / put 提交整理结果（数字须出自原始结果）")
    s.add_argument("action", choices=["add", "show", "put"])
    s.add_argument("id", nargs="?")
    s.add_argument("--research")
    s.add_argument("--raw", default="")
    s.add_argument("--title")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s = sub.add_parser("writing", help="研究的摘要与引言草稿：show / propose 提交修改稿待用户确认")
    s.add_argument("action", choices=["show", "propose"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)

    s = sub.add_parser("research", help="研究：list / add 新建（构思中）/ show / link 关联素材 / propose 提交修改稿待用户确认")
    s.add_argument("action", choices=["list", "add", "show", "link", "propose"])
    s.add_argument("id", nargs="?", help="show/link/propose 时的研究 id")
    s.add_argument("kind", nargs="?", choices=["paper", "thought", "message", "thread", "capture", "note"])
    s.add_argument("ref", nargs="?")
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--topic", action="append", default=[])
    s.add_argument("--paper", action="append", default=[])
    s.add_argument("--thought", action="append", default=[])
    s.add_argument("--thread", action="append", default=[])
    s.add_argument("--capture", action="append", default=[])
    s.add_argument("--file", help="propose：JSON 文件路径，- 表示标准输入")
    s.add_argument("--base-rev", type=int)
    s.add_argument("--unsorted", action="store_true")

    s = sub.add_parser("show")
    s.add_argument("kind", choices=["capture", "paper"])
    s.add_argument("id")
    s = sub.add_parser("text", help="读论文原文（按页）")
    s.add_argument("paper")
    s.add_argument("--pages", help="如 3-5")
    s = sub.add_parser("papers")
    s.add_argument("--topic")
    s.add_argument("--unsorted", action="store_true")
    sub.add_parser("thoughts")
    sub.add_parser("search").add_argument("query")
    sub.add_parser("inbox", help="处理 iCloud 收件箱").add_argument("action", choices=["drain"])
    s = sub.add_parser("serve", help="启动 Web 界面")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    return p


def main():
    args = parser().parse_args()
    try:
        out = run(args)
    except rules.Rejected as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=1))
        sys.exit(2)
    if out is not None:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
