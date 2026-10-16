"""p2b: the only way the librarian agent writes to the library. Every command prints JSON."""
import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path

from . import pipeline, rules, sources, store


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
        return sources.find_paper(args.title)
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
            rev = store.put_card(pid, rules.validate_card(_read_json(args.file), pages), "agent", args.base_rev)
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
        return {"id": store.add_thought(args.text, "agent", args.capture, args.paper and to_pid(args.paper))}
    if cmd == "note":
        return {"id": store.add_note(args.text, "agent", args.paper and to_pid(args.paper), args.topic, args.thread)}
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
    sub.add_parser("find", help="按标题检索论文（arXiv + Crossref）").add_argument("title")

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

    s = sub.add_parser("thought", help="记一条想法")
    s.add_argument("action", choices=["add"])
    s.add_argument("--text", required=True)
    s.add_argument("--capture")
    s.add_argument("--paper")
    s = sub.add_parser("note", help="记一条讨论结论")
    s.add_argument("action", choices=["add"])
    s.add_argument("--text", required=True)
    s.add_argument("--paper")
    s.add_argument("--topic")
    s.add_argument("--thread")

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
