"""p2b: the only way the librarian agent writes to the library. Every command prints JSON."""
import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path

from . import diagram, overleaf, pipeline, repos, rules, sources, store, structure


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
        got = pipeline.receive(args.id or str(uuid.uuid4()), args.text, args.source, args.thought, images)
        if got["kind"] == "thought":
            return {"created": True, "thread_id": got["thread_id"], "note": "只有一句想法，已记成一段讨论的开头，不用再处理"}
        if got["kind"] == "duplicate":
            return {"created": False, "duplicate_of": got["capture"]["id"],
                    "note": "这段内容以前收过，这句想法已记到那条下面，不用再处理"}
        return {"created": got["created"], "capture": got["capture"]}
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
        return {"tree": store.topic_tree(), "proposed": store.topic_proposals()}
    if cmd == "topic":
        if args.action == "propose":
            path = store.add_topic(args.name, args.parent, "agent", args.why, [to_pid(p) for p in args.paper_for])
            return {"proposed": path, "note": "已交给用户确认；采用后才能用，你列的论文会自动归进去"}
        if not args.paper or not args.paths:
            raise rules.Rejected("用法：./p2b topic set <论文> <主题> [<主题> ...]")
        pid = to_pid(args.paper)
        store.set_topics(pid, args.paths, "agent")
        return {"paper": pid, "topics": args.paths}
    if cmd == "threads":
        return [{k: t[k] for k in ("id", "parent_id", "title", "first", "scope", "scope_title", "n", "last_at")}
                for t in store.recent_threads()]
    if cmd == "thread":
        if args.action == "show":
            return [_thread_brief(i) for i in args.ids]
        if len(args.ids) != 1:
            raise rules.Rejected(f"用法：./p2b thread {args.action} <讨论 id> ...")
        if args.action == "title":
            store.set_thread_title(args.ids[0], args.text, "agent")
        else:
            if not args.parent:
                raise rules.Rejected("--parent 写上级讨论的 id，或 root 表示放回顶层")
            store.move_thread(args.ids[0], None if args.parent == "root" else args.parent, "agent")
        return {"ok": True}
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
    if cmd == "diagram":
        if args.action == "list":
            return [{**d, "html": str(store.diagram_dir(args.id, d["name"]) / "figure.html")} for d in store.diagrams(args.id)]
        return diagram.render(args.id, args.name, args.file, args.title, args.caption, args.kind)
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
        rev = store.update_experiment(args.id, "agent", args.base_rev, args.asked, **draft)
        return {"accepted": True, "revision": rev}
    if cmd == "writing":
        if args.action == "show":
            return store.get_writing(args.id)
        draft = rules.validate_writing_draft(_read_json(args.file), store.paper_ids())
        store.propose_writing(args.id, draft, "agent", args.base_rev)
        return {"proposed": True, "note": "写作修改稿已交给用户确认；采用前不会改动草稿"}
    if cmd == "plan":
        if args.action == "show":
            return store.get_plan(args.id)
        draft = _read_json(args.file)
        tasks = rules.validate_plan(draft.get("tasks") if isinstance(draft, dict) else None)
        store.propose_plan(args.id, tasks, str(draft.get("note") or "").strip() or None, "agent", args.base_rev)
        return {"proposed": True, "note": "计划修改稿已交给用户确认；采用前不会改动计划"}
    if cmd == "figures-plan":
        if args.action == "show":
            return store.get_figures(args.id)
        draft, x = _read_json(args.file), store.get_research(args.id) or {}
        if args.action == "fill":
            cells = rules.validate_cells(draft.get("cells") if isinstance(draft, dict) else None,
                                         store.get_figures(args.id)["figures"], x.get("experiments") or [])
            store.fill_cells(args.id, cells)
            return {"filled": len(cells)}
        draft = draft if isinstance(draft, dict) else {}
        named = {r["paper"]: r["name"] for r in ((x.get("related") or {}).get("comparison") or {}).get("rows") or []}
        words = lambda pid: " ".join([(store.get_paper(pid, with_anchors=False) or {}).get("title") or "", named.get(pid, ""),
                                      *store.pages(pid)[:2]])  # noqa: E731
        design = rules.validate_setup(draft.get("setup"), store.paper_ids(), words)
        figures = rules.validate_figures(draft.get("figures"), "\n".join(filter(None, (x.get("body") or {}).values())), design)
        rules.check_coverage(design, figures)
        store.propose_figures(args.id, figures, str(draft.get("note") or "").strip() or None, "agent", args.base_rev, design)
        return {"proposed": True, "note": "实验设计修改稿已交给用户确认；采用后再用 fill 把实验结果填进格子"}
    if cmd == "related":
        if args.action == "show":
            return store.get_related(args.id)
        draft = _read_json(args.file)
        groups = rules.validate_related(draft.get("groups") if isinstance(draft, dict) else None, store.paper_ids())
        comparison = rules.validate_comparison(draft.get("comparison"), groups)
        store.propose_related(args.id, groups, str(draft.get("note") or "").strip() or None, "agent", args.base_rev, comparison)
        return {"proposed": True, "note": "相关工作修改稿已交给用户确认；采用后其中的论文会加入研究素材"}
    if cmd == "overleaf":
        pid = store.get_writing(args.id)["overleaf"]
        if not pid:
            raise rules.Rejected("这个研究没有关联 Overleaf 项目")
        if args.action == "pull":
            overleaf.pull(pid)
        return {"path": str(overleaf.local_dir(pid)), "unpushed_files": overleaf.changes(pid)["files"]}
    if cmd == "research":
        if args.action == "list":
            return store.list_research("" if args.unsorted else (args.topic[0] if args.topic else None))
        if args.action == "add":
            links = [("paper", to_pid(p)) for p in args.paper] + [("thread", t) for t in args.thread] + \
                    [("capture", t) for t in args.capture]
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
            p["discussions"] = store.paper_discussions(p["id"])
        return p
    if cmd == "discuss":
        pid = to_pid(args.paper)
        if not store.get_paper(pid):
            raise rules.Rejected(f"论文 {pid} 不在库里")
        if not args.question.strip():
            raise rules.Rejected("--question 不能为空")
        thread = store.thread_for(f"paper:{pid}")
        store.add_message(thread["id"], "user", args.question.strip())
        if (args.answer or "").strip():
            store.add_message(thread["id"], "assistant", args.answer.strip())
        return {"logged": True, "thread": thread["id"]}
    if cmd == "text":
        pid = to_pid(args.paper)
        pages = store.pages(pid)
        if not pages:
            raise rules.Rejected("这篇论文还没有全文。用 ./p2b fetch <PDF 链接> 补上，或请用户在网页上传 PDF")
        return [{"page": n, "text": pages[n - 1]} for n in _pages(args.pages, len(pages))]
    if cmd == "papers":
        return store.list_papers("" if args.unsorted else args.topic)
    if cmd == "search":
        return store.search(args.query)
    if cmd == "inbox":
        from . import inbox
        return inbox.drain()
    if cmd == "serve":
        from . import server
        server.serve(args.host, args.port)


def _thread_brief(tid):
    t = store.thread_for(thread_id=tid)
    return {"id": t["id"], "scope": t["scope"], "title": t["title"], "title_by": t["title_by"], "parent": t["parent"],
            "fork_message_id": t["fork_message_id"], "path": [{k: s[k] for k in ("id", "title", "first")} for s in t["path"]],
            "children": t["children"], "research": t["research"], "papers": t["papers"],
            "messages": [{"role": m["role"], "at": m["created_at"], "text": (m["run_text"] or m["text"] or "")[:1500]}
                         for m in t["messages"]]}


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

    sub.add_parser("topics", help="主题树（tree）和待用户确认的提议（proposed）")
    s = sub.add_parser("topic", help="set 设置论文主题（覆盖 agent 之前的设置）/ propose 提议新主题，用户确认后生效")
    s.add_argument("action", choices=["set", "propose"])
    s.add_argument("paper", nargs="?")
    s.add_argument("paths", nargs="*")
    s.add_argument("--name", help="propose：新主题名")
    s.add_argument("--parent", help="propose：放在哪个一级主题下（不写就是一级主题）")
    s.add_argument("--why", help="propose：为什么现有主题放不下")
    s.add_argument("--paper", dest="paper_for", action="append", default=[], help="propose：采用后归进去的论文")

    sub.add_parser("threads", help="讨论树：每段讨论的 id、上级（parent_id）、标题或开头一句、范围、最近活动")
    s = sub.add_parser("thread", help="讨论：show 查看消息、上级和下级 / title 起标题 / move 挂到另一段讨论下面")
    s.add_argument("action", choices=["show", "title", "move"])
    s.add_argument("ids", nargs="+", help="讨论 id（show 可以给多个）")
    s.add_argument("--text", help="title：一句话标题（≤50 字，见 librarian/discuss.md）")
    s.add_argument("--parent", help="move：上级讨论的 id，root 表示放回顶层")
    s = sub.add_parser("note", help="记一条讨论结论")
    s.add_argument("action", choices=["add"])
    s.add_argument("--text", required=True)
    s.add_argument("--paper")
    s.add_argument("--topic")
    s.add_argument("--thread")

    s = sub.add_parser("discuss", help="把用户在 Cursor 手机端的追问和你的回答记到这篇论文的讨论里（网页上能看到）")
    s.add_argument("action", choices=["log"])
    s.add_argument("--paper", required=True)
    s.add_argument("--question", required=True, help="用户原话")
    s.add_argument("--answer", help="你的回答（Markdown）")

    sub.add_parser("doc", help="研究附件的全文（用户开研究时带来的构思稿、笔记等）").add_argument("id")
    sub.add_parser("repo", help="代码仓库（只读）：本地路径、当前 commit、最近提交").add_argument("id")
    s = sub.add_parser("codemap", help="提交方法—代码对照（论文引用和代码片段都逐字核对）")
    s.add_argument("action", choices=["put"])
    s.add_argument("--research", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--paper", required=True)
    s.add_argument("--file", required=True)
    s = sub.add_parser("diagram", help="论文插图（方法框架图、动机图）：render 把 HTML/SVG 渲染成论文用的 PDF 和预览 PNG；list 看已有的图（见 librarian/diagram.md）")
    s.add_argument("action", choices=["render", "list"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--kind", choices=["method", "motivation"], help="method 方法框架图（默认），motivation 动机图；改已有的图时不用写")
    s.add_argument("--name", help="图的名字（小写英文和连字符），同名覆盖")
    s.add_argument("--file", help="HTML 文件")
    s.add_argument("--title", help="简短的中文名，例如“整体框架图”")
    s.add_argument("--caption", help="论文图注（英文）")
    s = sub.add_parser("experiment", help="实验记录：add / show / put 提交整理结果（数字须出自原始结果）")
    s.add_argument("action", choices=["add", "show", "put"])
    s.add_argument("id", nargs="?")
    s.add_argument("--research")
    s.add_argument("--raw", default="")
    s.add_argument("--title")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s.add_argument("--asked", action="store_true", help="用户在讨论里确认了这次修改，可以改他手动改过的记录")
    s = sub.add_parser("writing", help="研究的摘要与引言草稿：show / propose 提交修改稿待用户确认")
    s.add_argument("action", choices=["show", "propose"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s = sub.add_parser("plan", help="研究的计划：show 任务和进度 / propose 提交计划修改稿待用户确认")
    s.add_argument("action", choices=["show", "propose"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s = sub.add_parser("figures-plan", help="研究的预期图表：show / propose 提交结构修改稿待用户确认 / fill 用实验结果填格子")
    s.add_argument("action", choices=["show", "propose", "fill"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s = sub.add_parser("related", help="研究的相关工作：show 现有方向 / propose 提交修改稿待用户确认")
    s.add_argument("action", choices=["show", "propose"])
    s.add_argument("id", help="研究 id")
    s.add_argument("--file")
    s.add_argument("--base-rev", type=int)
    s = sub.add_parser("overleaf", help="研究关联的 Overleaf 项目：show 本地目录和未推送的改动 / pull 拉取最新（有未推送改动时拒绝）")
    s.add_argument("action", choices=["show", "pull"])
    s.add_argument("id", help="研究 id")

    s = sub.add_parser("research", help="研究：list / add 新建（构思中）/ show / link 关联素材 / propose 提交修改稿待用户确认")
    s.add_argument("action", choices=["list", "add", "show", "link", "propose"])
    s.add_argument("id", nargs="?", help="show/link/propose 时的研究 id")
    s.add_argument("kind", nargs="?", choices=["paper", "message", "thread", "capture", "note"])
    s.add_argument("ref", nargs="?")
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--topic", action="append", default=[])
    s.add_argument("--paper", action="append", default=[])
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
