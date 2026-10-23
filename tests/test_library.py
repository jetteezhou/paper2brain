"""Write rules and storage, on a temporary data directory. No network."""
import base64
import os
import tempfile
import unittest
from pathlib import Path

os.environ["P2B_HOME"] = tempfile.mkdtemp(prefix="p2b-test-")

from paper2brain import rules, store  # noqa: E402

AGENT_CFG = {"machine": "m", "repo": "r", "model": None, "models": {}, "api_key": "k", "max_agents": 2, "run_timeout_min": 60,
             "backend": "cursor", "auto_agent": True}
PAGES = [
    "Intro. We propose a flow matching action expert.",
    "Results. Our model controls robots at frequencies of up to 50 Hz for dexterous tasks such as laundry folding.",
]
CARD = {
    "tldr": "t", "problem": "p", "method": "m", "relevance": "r",
    "results": [{"text": "50 Hz", "quote": "frequencies of up to 50 Hz", "page": 1}],
}


class Rules(unittest.TestCase):
    def test_arxiv_ids_from_links_and_mentions_only(self):
        text = "看 https://arxiv.org/abs/2410.24164v2 和 arXiv:2303.04137，还有 hf huggingface.co/papers/2506.07339；日期 2024.10311 不算"
        self.assertEqual(rules.find_arxiv_ids(text), ["2410.24164", "2303.04137", "2506.07339"])

    def test_paper_refs_beyond_arxiv(self):
        text = ("https://doi.org/10.15607/RSS.2023.XIX.026，另见 https://openreview.net/forum?id=AbC_12-x "
                "以及 arXiv 自己的 DOI 10.48550/arXiv.2410.24164。")
        self.assertEqual(rules.find_paper_refs(text),
                         ["arxiv:2410.24164", "doi:10.15607/rss.2023.xix.026", "openreview:AbC_12-x"])

    def test_parse_pid(self):
        self.assertEqual(rules.parse_pid("2410.24164v3"), "arxiv:2410.24164")
        self.assertEqual(rules.parse_pid("arxiv:2410.24164v2"), "arxiv:2410.24164")
        self.assertEqual(rules.parse_pid("DOI:10.1109/CVPR.2024.1"), "doi:10.1109/cvpr.2024.1")
        self.assertEqual(rules.parse_pid("pdf:abc123"), "pdf:abc123")
        self.assertIsNone(rules.parse_pid("https://example.com/some-blog"))

    def test_short_names(self):
        from paper2brain import sources

        self.assertEqual(sources.name_terms("PatchWAM 这篇的 patch 化动作值得看 https://x.com/a"), ["PatchWAM", "patch"])
        self.assertTrue(sources.distinctive("OpenVLA-OFT") and not sources.distinctive("forcing"))
        self.assertTrue(sources.names_itself("An Action Is Worth One Patch: Unified World-Action Modeling with PatchWAM", "PatchWAM"))
        self.assertTrue(sources.names_itself("Octo: An Open-Source Generalist Robot Policy", "Octo"))
        self.assertFalse(sources.names_itself("RL Bootstrapping of OpenVLA-OFT for a Novel Robot Embodiment", "OpenVLA-OFT"))
        self.assertFalse(sources.names_itself("Octo-Tiger's New Hydro Module", "Octo"))

    def test_quote_page_is_corrected_and_fabrication_rejected(self):
        card = rules.validate_card(CARD, PAGES)
        self.assertEqual(card["results"][0]["page"], 2)
        with self.assertRaisesRegex(rules.Rejected, "找不到"):
            rules.validate_card({**CARD, "results": [{"text": "x", "quote": "99% success on all tasks", "page": 2}]}, PAGES)

    def test_quote_tolerates_pdf_line_breaks(self):
        fixed, errors = rules.verify_citations([{"quote": "dexterous tasks such as laundry folding", "page": 2}],
                                               ["for dexter-\nous tasks such as\nlaundry folding."])
        self.assertEqual(errors, [])

    def test_card_links_must_be_http(self):
        ok = rules.validate_card({**CARD, "resources": {"code": "https://github.com/a/b", "project": None}}, PAGES)
        self.assertEqual(ok["resources"]["code"], "https://github.com/a/b")
        for bad in ("javascript:alert(1)", "JaVaScRiPt:x", "data:text/html,x", "//evil.com"):
            with self.assertRaisesRegex(rules.Rejected, "resources.code"):
                rules.validate_card({**CARD, "resources": {"code": bad}}, PAGES)

    def test_code_map_keeps_dotted_paths(self):
        files = {".github/workflows/ci.yml": "name: ci\non: [push, pull_request]\n"}
        rows = [{"component": "ci", "file": "./.github/workflows/ci.yml", "snippet": "on: [push, pull_request]"}]
        self.assertEqual(rules.validate_code_map(rows, PAGES, files.get)[0]["file"], ".github/workflows/ci.yml")

    def test_report_needs_three_citations(self):
        with self.assertRaisesRegex(rules.Rejected, "至少要有 3 条"):
            rules.validate_report({"sections": [{"heading": "h", "points": [{"text": "b[1]", "citations": [{"quote": "flow matching action expert", "page": 1}]}]}]}, PAGES)

    def test_report_every_point_cites_the_pdf(self):
        cite = lambda q: [{"quote": q, "page": 2}]
        ok = {"heading": "h", "points": [{"text": "a[1]", "citations": cite("flow matching action expert")},
                                         {"text": "b[1]", "citations": cite("up to 50 Hz")}, {"text": "c[1]", "citations": cite("laundry folding")}]}
        self.assertEqual(rules.validate_report({"sections": [ok]}, PAGES)["sections"][0]["points"][0]["citations"][0]["page"], 1)
        with self.assertRaisesRegex(rules.Rejected, "第 1 节第 4 段：每段至少要有一条原文引用"):
            rules.validate_report({"sections": [{**ok, "points": ok["points"] + [{"text": "d", "citations": []}]}]}, PAGES)
        with self.assertRaisesRegex(rules.Rejected, "第 1 节第 4 段太大"):
            rules.validate_report({"sections": [{**ok, "points": ok["points"] + [{"text": "长" * 301 + "[1]", "citations": cite("laundry folding")}]}]}, PAGES)
        with self.assertRaisesRegex(rules.Rejected, "第 1 节第 4 段：在依据的那句话后面写"):
            rules.validate_report({"sections": [{**ok, "points": ok["points"] + [{"text": "d[2]", "citations": cite("laundry folding")}]}]}, PAGES)


class Store(unittest.TestCase):
    def setUp(self):
        self.cid = store.new_id()
        store.add_capture(self.cid, "https://example.com/a", "test", thought="为什么收")
        store.upsert_paper("arxiv:2410.24164", "pi0")

    def test_capture_is_idempotent(self):
        cap, created = store.add_capture(self.cid, "different text", "test", thought="again")
        self.assertFalse(created)
        self.assertEqual(cap["raw"], "https://example.com/a")
        self.assertEqual([t["first"] for t in cap["threads"]], ["为什么收"])

    def test_same_text_is_found_for_dedupe(self):
        self.assertIsNotNone(store.same_capture(" https://example.com/a "))
        self.assertIsNone(store.same_capture("https://example.com/other"))

    def test_activity_counts_only_your_actions_today(self):
        from datetime import date

        today = date.today().isoformat()
        before = store.activity()["days"].get(today, 0)
        self.assertGreater(before, 0)  # the capture from setUp
        tid = store.thread_for("library")["id"]
        store.add_message(tid, "user", "问一句")
        store.add_message(tid, "assistant", "答一句")
        store.mark_read("arxiv:2410.24164")
        store.mark_read("arxiv:2410.24164")  # reading the same paper again the same day counts once
        self.assertEqual(store.activity()["days"][today], before + 2)
        kinds = [x["kind"] for x in store.activity_day(today)]
        self.assertEqual((kinds.count("read"), kinds[-2:]), (1, ["message", "read"]))

    def test_current_and_longest_streak(self):
        from datetime import datetime, timedelta, timezone

        with store.tx() as c:
            for ago_days in (1, 2, 3, 30, 31, 32, 33, 34):
                at = (datetime.now(timezone.utc) - timedelta(days=ago_days)).isoformat(timespec="seconds")
                c.execute("INSERT INTO events (at, actor, action, target) VALUES (?, 'user', 'capture.triage', 'x')", (at,))
        a = store.activity()
        self.assertEqual((a["streak"], a["best"]), (4, 5))  # today (setUp's capture) plus the three days before

    def test_empty_capture_rejected(self):
        with self.assertRaises(rules.Rejected):
            store.add_capture(store.new_id(), "  ", "test")

    def test_user_unlink_blocks_agent_relink(self):
        store.link(self.cid, "arxiv:2410.24164", "agent")
        store.unlink(self.cid, "arxiv:2410.24164", "user")
        with self.assertRaisesRegex(rules.Rejected, "用户已移除"):
            store.link(self.cid, "arxiv:2410.24164", "agent")
        store.link(self.cid, "arxiv:2410.24164", "user")
        self.assertEqual(store.get_capture(self.cid)["papers"][0]["state"], "confirmed")

    def test_user_topics_lock_out_agent(self):
        store.set_topics("arxiv:2410.24164", ["VLA/tokenizer"], "agent")
        store.set_topics("arxiv:2410.24164", ["VLA/推理加速"], "user")
        with self.assertRaisesRegex(rules.Rejected, "手动调整"):
            store.set_topics("arxiv:2410.24164", ["WAM"], "agent")
        with self.assertRaisesRegex(rules.Rejected, "未知主题"):
            store.set_topics("arxiv:2410.24164", ["VLA/不存在"], "user")

    def test_card_revision_guards_stale_writes(self):
        rev = store.put_card("arxiv:2410.24164", CARD, "agent")
        store.put_card("arxiv:2410.24164", CARD, "agent", base_rev=rev)
        with self.assertRaisesRegex(rules.Rejected, "已被修改"):
            store.put_card("arxiv:2410.24164", CARD, "agent", base_rev=rev)

    def test_undo_capture_deletes_what_only_it_brought_in(self):
        only, shared, used = "arxiv:2601.00011", "arxiv:2601.00012", "arxiv:2601.00013"
        for pid in (only, shared, used):
            store.upsert_paper(pid, pid)
            store.link(self.cid, pid, "rule")
        other = store.new_id()
        store.add_capture(other, "另一篇解读", "test")
        store.link(other, shared, "rule")
        rid = store.add_research("用到它", "user", links=[("paper", used)])
        store.put_card(only, CARD, "agent")
        store.set_topics(only, ["VLA"], "agent")
        aid = store.add_anchor(only, "card", "flow matching", "user")
        store.add_message(store.thread_for(f"anchor:{aid}")["id"], "user", "这段？")
        store.add_message(store.thread_for(f"paper:{only}")["id"], "user", "讲讲")
        store.new_thread(f"paper:{only}", text="关于这篇的想法")
        store.link_research(rid, "thread", store.get_capture(self.cid)["threads"][0]["id"], "user")
        (store.paper_dir(only) / "pages.json").write_text("[]")

        plan = store.capture_removal(self.cid)
        self.assertEqual([p["id"] for p in plan["papers"]], [only])
        self.assertEqual((len(plan["papers"][0]["anchor"]), len(plan["papers"][0]["thread"]), plan["papers"][0]["card"]), (1, 3, True))
        self.assertEqual({p["id"]: len(p["why"]) for p in plan["kept"]}, {shared: 1, used: 1})
        self.assertEqual((len(plan["threads"]), plan["used_in"]), (1, ["用到它"]))

        store.delete_capture(self.cid)
        self.assertIsNone(store.get_capture(self.cid))
        self.assertIsNone(store.get_paper(only))
        self.assertFalse(store.paper_dir(only, create=False).exists())
        self.assertIsNone(store.get_anchor(aid))
        self.assertFalse(any(t["scope"] in (f"capture:{self.cid}", f"paper:{only}") for t in store.recent_threads()))
        self.assertEqual([l["kind"] for l in store.get_research(rid)["links"]], ["paper"])
        listed = {p["id"] for p in store.list_papers()}
        self.assertTrue({shared, used} <= listed and only not in listed)

    def test_delete_paper_removes_it_everywhere(self):
        pid = "arxiv:2601.00099"
        store.upsert_paper(pid, pid)
        store.link(self.cid, pid, "rule")
        rid = store.add_research("用到它", "user", links=[("paper", pid)])
        aid = store.add_anchor(pid, "card", "flow matching", "user")
        store.add_message(store.thread_for(f"paper:{pid}")["id"], "user", "讲讲")
        trash = store.delete_paper(pid)
        self.assertIsNone(store.get_paper(pid))
        self.assertIsNone(store.get_anchor(aid))
        self.assertEqual(store.get_research(rid)["links"], [])
        self.assertIsNotNone(store.get_capture(self.cid))
        with self.assertRaises(KeyError):
            store.delete_paper(pid)
        self.assertEqual(store.restore_trash(trash)["ref"], pid)
        self.assertIsNotNone(store.get_paper(pid))
        self.assertIsNotNone(store.get_anchor(aid))
        self.assertEqual([l["kind"] for l in store.get_research(rid)["links"]], ["paper"])
        with self.assertRaises(rules.Rejected):
            store.restore_trash(trash)

    def test_trash_keeps_every_delete_until_purged(self):
        a, b = "arxiv:2601.00097", "arxiv:2601.00098"
        for pid in (a, b):
            store.upsert_paper(pid, pid)
        before = store.counts()["trash"]
        ta, tb = store.delete_paper(a), store.delete_paper(b)
        self.assertEqual([x["ref"] for x in store.list_trash()][:2], [b, a])
        self.assertEqual(store.counts()["trash"], before + 2)
        store.restore_trash(ta)  # the older delete is still restorable
        self.assertIsNotNone(store.get_paper(a))
        store.purge_trash(tb)
        self.assertEqual(store.counts()["trash"], before)
        with self.assertRaises(rules.Rejected):
            store.restore_trash(tb)

    def test_delete_thread_and_restore_it_in_place(self):
        root = store.new_thread("library", text="根")
        mid = store.new_thread("library", parent_id=root, text="中间")
        leaf = store.new_thread("library", parent_id=mid, text="分支")
        store.add_message(mid, "user", "再说一句")
        rid = store.add_research("用到讨论", "user", links=[("thread", mid)])
        trash = store.delete_thread(mid)
        with self.assertRaises(rules.Rejected):
            store.thread_for(thread_id=mid)
        self.assertEqual(store.thread_for(thread_id=leaf)["parent_id"], root)  # lifted a level
        self.assertEqual(store.get_research(rid)["links"], [])
        self.assertEqual(store.list_trash()[0]["kind"], "thread")
        back = store.restore_trash(trash)
        self.assertEqual((back["kind"], back["scope"]), ("thread", "library"))
        t = store.thread_for(thread_id=mid)
        self.assertEqual((t["parent_id"], len(t["messages"])), (root, 2))
        self.assertEqual(store.thread_for(thread_id=leaf)["parent_id"], mid)  # hung back
        self.assertEqual([l["kind"] for l in store.get_research(rid)["links"]], ["thread"])
        with self.assertRaises(KeyError):
            store.delete_thread("no-such-thread")

    def test_any_pdf_becomes_a_paper(self):
        import pymupdf

        from paper2brain import pipeline

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Dexterous Hands from Human Videos at Scale")
        doc.new_page().insert_text((72, 72), "We report a 37% gain on the bimanual benchmark.")
        data = doc.tobytes()
        paper = pipeline.paper_from_pdf(data)
        self.assertTrue(paper["id"].startswith("pdf:"))
        self.assertEqual(paper["title"], "Dexterous Hands from Human Videos at Scale")
        self.assertEqual(paper["page_count"], 2)
        self.assertEqual(pipeline.paper_from_pdf(data)["id"], paper["id"])
        card = rules.validate_card({**CARD, "results": [{"text": "37%", "quote": "a 37% gain on the bimanual benchmark", "page": 1}]},
                                   store.pages(paper["id"]))
        self.assertEqual(card["results"][0]["page"], 2)

    def test_research_materials_link_both_ways_and_inherit_topics(self):
        store.upsert_paper("arxiv:2501.00001", "one-step policy")
        store.set_topics("arxiv:2501.00001", ["VLA/推理加速"], "agent")
        tid = store.new_thread("paper:arxiv:2501.00001", text="能不能把动作头蒸馏到一步")
        rid = store.add_research("一步动作头", "user", links=[("thread", tid), ("paper", "arxiv:2501.00001")])
        r = store.get_research(rid)
        self.assertEqual((r["stage"], r["topics"]), ("forming", ["VLA/推理加速"]))
        self.assertEqual({l["kind"] for l in r["links"]}, {"thread", "paper"})
        self.assertEqual(next(l for l in r["links"] if l["kind"] == "thread")["label"], "能不能把动作头蒸馏到一步")
        self.assertEqual(store.get_paper("arxiv:2501.00001")["research"][0]["id"], rid)
        self.assertEqual(store.thread_for(thread_id=tid)["research"][0]["id"], rid)
        self.assertEqual([x["id"] for x in store.list_research("VLA")], [rid])
        with self.assertRaisesRegex(rules.Rejected, "不存在"):
            store.link_research(rid, "message", "nope", "user")

    def test_only_user_establishes_with_all_three_elements(self):
        rid = store.add_research("x", "user")
        with self.assertRaisesRegex(rules.Rejected, "只能由用户确立"):
            store.establish(rid, "agent")
        store.update_research(rid, body={"problem": "p", "method": "m"})
        with self.assertRaisesRegex(rules.Rejected, "还缺：验证方案"):
            store.establish(rid, "user")
        store.update_research(rid, body={"problem": "p", "method": "m", "validation": "v"})
        store.establish(rid, "user")
        self.assertEqual(store.get_research(rid)["stage"], "established")

    def test_agent_draft_waits_for_user(self):
        rid = store.add_research("x", "user")
        rev = store.update_research(rid, title="用户改过的标题")
        draft = {"title": "t", "summary": "s", "problem": "p", "method": "m",
                 "validation": "v [arxiv:2410.24164 p.3]", "notes": "n [arxiv:9999.99999]"}
        with self.assertRaisesRegex(rules.Rejected, "库里没有"):
            rules.validate_research_draft(draft, store.paper_ids())
        draft, cited = rules.validate_research_draft({**draft, "notes": None}, store.paper_ids())
        self.assertEqual(cited, ["arxiv:2410.24164"])
        with self.assertRaisesRegex(rules.Rejected, "已被修改"):
            store.propose(rid, draft, "agent", rev - 1)
        store.propose(rid, draft, "agent", rev)
        self.assertEqual(store.get_research(rid)["title"], "用户改过的标题")
        store.accept_proposal(rid)
        r = store.get_research(rid)
        self.assertEqual((r["title"], r["body"]["validation"], r["proposal"], r["stage"]),
                         ("t", "v [arxiv:2410.24164 p.3]", None, "forming"))

    def test_user_takes_part_of_a_draft_and_keeps_the_rest(self):
        rid = store.add_research("x", "user")
        rev = store.update_research(rid, body={"problem": "我的问题", "method": "我的方法", "notes": "我的备注"})
        store.propose(rid, {"title": "t", "summary": None, "problem": "新问题", "method": "新方法", "validation": "新验证", "notes": None}, "agent", rev)
        store.accept_proposal(rid, fields=["method", "validation"])
        r = store.get_research(rid)
        self.assertEqual((r["title"], r["body"], r["proposal"]),
                         ("x", {"problem": "我的问题", "method": "新方法", "validation": "新验证", "notes": "我的备注"}, None))

    def test_material_roles_belong_to_the_user_and_show_on_the_paper(self):
        store.upsert_paper("arxiv:2501.00091", "seed")
        rid = store.add_research("x", "user", links=[("paper", "arxiv:2501.00091")])
        with self.assertRaisesRegex(rules.Rejected, "只能由用户"):
            store.link_research(rid, "paper", "arxiv:2501.00091", "agent", "baseline")
        with self.assertRaisesRegex(rules.Rejected, "可选"):
            store.link_research(rid, "paper", "arxiv:2501.00091", "user", "whatever")
        store.link_research(rid, "paper", "arxiv:2501.00091", "user", "baseline")
        store.link_research(rid, "paper", "arxiv:2501.00091", "agent")  # re-linking keeps the role
        self.assertEqual(store.get_research(rid)["links"][0]["role"], "baseline")
        self.assertEqual(store.get_paper("arxiv:2501.00091")["research"][0]["role"], "baseline")
        store.link_research(rid, "paper", "arxiv:2501.00091", "user", "")
        self.assertIsNone(store.get_research(rid)["links"][0]["role"])

    def test_discussion_title_keeps_words_and_user_edit_wins(self):
        store.upsert_paper("arxiv:2601.00021", "titled")
        tid = store.new_thread("paper:arxiv:2601.00021", text="和 diffusion policy 对比一下")
        self.assertIn(tid, store.pending_titles(100))
        store.set_thread_title(tid, "π0 的 flow matching 动作头和 Diffusion Policy 差在哪", "agent")
        self.assertNotIn(tid, store.pending_titles(100))
        store.set_thread_title(tid, "比较两种动作生成头", "user")
        with self.assertRaisesRegex(rules.Rejected, "手动改过"):
            store.set_thread_title(tid, "agent 又来改", "agent")
        t = store.thread_for(thread_id=tid)
        self.assertEqual((t["messages"][0]["text"], t["title"], t["title_by"]), ("和 diffusion policy 对比一下", "比较两种动作生成头", "user"))
        self.assertEqual(store.get_paper("arxiv:2601.00021")["threads"][0]["id"], tid)

    def test_discussion_tree_grows_moves_and_survives_deletes(self):
        pid = "arxiv:2601.00022"
        store.upsert_paper(pid, "tree")
        root = store.new_thread(f"paper:{pid}", text="π0 为什么用 flow matching")
        aid = store.add_anchor(pid, "card", "action expert", "user")
        mid = store.thread_for(f"anchor:{aid}")["id"]
        store.add_message(mid, "user", "这段呢")
        leaf = store.new_thread("library", parent_id=mid, text="能不能用到灵巧手上")
        self.assertEqual(store.thread_for(thread_id=leaf)["parent"]["first"], "这段呢")
        store.move_thread(mid, root, "agent")
        self.assertEqual([c["id"] for c in store.thread_for(thread_id=root)["children"]], [mid])
        with self.assertRaisesRegex(rules.Rejected, "它下面"):
            store.move_thread(root, leaf, "user")
        with self.assertRaisesRegex(rules.Rejected, "手动调整"):
            store.move_thread(leaf, None, "agent")  # the user placed it by branching off
        store.move_thread(mid, None, "user")
        with self.assertRaisesRegex(rules.Rejected, "手动调整"):
            store.move_thread(mid, root, "agent")
        store.move_thread(mid, root, "user")
        store.delete_anchor(aid)
        self.assertEqual(store.thread_for(thread_id=leaf)["parent_id"], root)
        with self.assertRaisesRegex(rules.Rejected, "不存在"):
            store.new_thread("library", parent_id=mid)

    def test_branch_from_a_message_inherits_the_path_and_cites_link_back(self):
        pid = "arxiv:2601.00023"
        store.upsert_paper(pid, "cited")
        root = store.new_thread("library", text="动作头怎么选")
        first = store.add_message(root, "assistant", f"flow matching 更快 [{pid} p.3]")
        store.add_message(root, "user", "那 diffusion 呢")
        a = store.new_thread("library", fork=first, text="只看 flow matching")
        b = store.new_thread("library", fork=first, text="只看推理速度")
        t = store.thread_for(thread_id=a)
        self.assertEqual((t["parent_id"], [m["text"] for m in t["path"][0]["messages"]]),
                         (root, ["动作头怎么选", f"flow matching 更快 [{pid} p.3]"]))  # up to the fork, not after it
        self.assertEqual([s["id"] for s in t["siblings"]], [a, b])
        one, two = store.new_thread("topic:sib", text="一"), store.new_thread("topic:sib", text="二")
        store.new_thread("topic:other", text="别的范围")
        self.assertEqual([s["id"] for s in store.thread_for(thread_id=two)["siblings"]], [one, two])  # roots: same scope only
        r = store.thread_for(thread_id=root)
        self.assertEqual(([x["id"] for x in r["messages"][1]["forks"]], r["papers"]), ([a, b], [{"id": pid, "title": "cited"}]))
        self.assertEqual([x["id"] for x in store.cited_in(pid)], [root])
        store.move_thread(b, a, "user")
        self.assertIsNone(store.thread_for(thread_id=b)["fork_message_id"])
        with store.tx() as c:
            store._drop_threads(c, [root])
        self.assertEqual((store.thread_for(thread_id=a)["parent_id"], store.thread_for(thread_id=a)["fork_message_id"]), (None, None))

    def test_experiment_numbers_must_come_from_raw(self):
        raw = "eval libero_spatial: success=0.923 (n=500)\nbaseline pi0: 0.884"
        ok = rules.validate_experiment({"title": "t", "results": [{"name": "LIBERO-Spatial", "value": "0.923"}],
                                        "conclusion": "supports", "conclusion_text": "比 0.884 高"}, raw)
        self.assertEqual(ok["results"][0]["value"], "0.923")
        with self.assertRaisesRegex(rules.Rejected, r"\[0\.95\]"):
            rules.validate_experiment({"title": "t", "results": [{"name": "x", "value": "0.95"}]}, raw)
        with self.assertRaisesRegex(rules.Rejected, "结论里的数字"):
            rules.validate_experiment({"title": "t", "conclusion_text": "提升 12.5 个点"}, raw)
        ok = rules.validate_experiment({"title": "t", "conclusion_text": "超过 0.9 的成功线"}, raw, "验证方案：成功率 ≥ 0.9")
        self.assertEqual(ok["conclusion_text"], "超过 0.9 的成功线")

    def test_experiment_chart_numbers_must_come_from_raw(self):
        raw = "step 1000 loss 0.52\nstep 2000 loss 0.31"
        chart = {"title": "loss", "type": "line", "data": {"labels": [1000, 2000], "datasets": [{"label": "loss", "data": [0.52, 0.31]}]},
                 "options": {"scales": {"y": {"type": "logarithmic"}}}}
        ok = rules.validate_experiment({"title": "t", "charts": [chart]}, raw)
        rid = store.add_research("chart", "user")
        eid = store.add_experiment(rid, raw, "user")
        store.update_experiment(eid, "agent", 1, **ok)
        self.assertEqual(store.get_experiment(eid)["charts"][0]["data"]["datasets"][0]["data"], [0.52, 0.31])
        for bad, msg in (({"data": {"labels": [1000, 3000], "datasets": [{"data": [0.52, 0.31]}]}}, r"\[3000\.0\]"),
                         ({"data": {"datasets": [{"data": [0.5]}]}}, r"\[0\.5\]"),
                         ({"data": {"datasets": [{"data": [{"x": 1000, "y": 0.29}]}]}}, r"\[0\.29\]"),
                         ({"data": {"datasets": [{"data": ["0.52"]}]}}, "只能是数字"), ({"type": "sankey"}, "需要 type")):
            with self.assertRaisesRegex(rules.Rejected, msg):
                rules.validate_experiment({"title": "t", "charts": [{**chart, **bad}]}, raw)

    def test_expected_figures_filled_by_experiment_results(self):
        ctx = "验证方案：成功率 ≥ 0.85"
        known = {"arxiv:2410.24164", "arxiv:2607.26789"}
        setup = {"questions": ["Does it work?"], "method": "ours", "benchmarks": [{"name": "Spatial"}, {"name": "Object"}],
                 "baselines": [{"name": "pi0", "paper": "arxiv:2410.24164"}, {"name": "CheckVLA", "paper": "2607.26789"}],
                 "controls": [{"name": "fixed-rate"}], "metrics": [{"name": "SR", "better": "higher"}]}
        for bad, msg in (({"method": ""}, "setup.method"), ({"controls": [{"name": "ours"}]}, "不能重复"),
                         ({"baselines": [{"name": "pi0", "paper": "arxiv:2410.24164"}, {"name": "periodic"}]}, "已发表的方法"),
                         ({"baselines": setup["baselines"][:1]}, "2-6"), ({"benchmarks": [{"name": "S", "note": "长" * 151}]}, "150 字"),
                         ({"metrics": [{"name": "SR", "better": "up"}]}, "higher / lower"), ({"questions": []}, "questions")):
            with self.assertRaisesRegex(rules.Rejected, msg):
                rules.validate_setup({**setup, **bad}, known)
        design = rules.validate_setup(setup, known)
        self.assertEqual(design["baselines"][1]["paper"], "arxiv:2607.26789")
        words = {"arxiv:2410.24164": "π0: A Vision-Language-Action Flow Model", "arxiv:2607.26789": "CheckVLA: Execution-Time Verification"}.get
        rules.validate_setup({**setup, "baselines": [{"name": "π0 (5Hz)", "paper": "arxiv:2410.24164"}, setup["baselines"][1]]}, known, words)
        with self.assertRaisesRegex(rules.Rejected, "Independent AC Predictor"):
            rules.validate_setup({**setup, "baselines": [setup["baselines"][0], {"name": "Independent AC Predictor", "paper": "2607.26789"}]}, known, words)
        fig = {"title": "主结果", "kind": "bar", "section": "main", "question": 1, "caption": "Success rate.", "metric": "SR",
               "rows": ["Spatial", "Object"], "series": ["ours", "pi0", "CheckVLA", "fixed-rate"], "target": {"value": 0.85}}
        figs = rules.validate_figures([fig], ctx, design)
        self.assertEqual(figs[0]["width"], "full", "a figure that names no width takes the whole row")
        self.assertEqual(rules.validate_figures([{**fig, "kind": "hbar", "width": "half"}], ctx, design)[0]["width"], "half")
        rules.check_coverage(design, figs)
        with self.assertRaisesRegex(rules.Rejected, r"\['CheckVLA'\] 没进主结果"):
            rules.check_coverage(design, rules.validate_figures([{**fig, "series": ["ours", "pi0"]}], ctx, design))
        for bad, msg in (({"target": {"value": 0.9}}, "三要素里找不到"), ({"rows": ["a", "a"]}, "不重复"), ({"kind": "pie"}, "kind"),
                         ({"series": ["ours", "OpenVLA"]}, "不在 setup.baselines"), ({"series": ["ours", "fixed-rate"]}, "已发表的 baseline"),
                         ({"metric": "acc"}, "不在 setup.metrics"), ({"question": 2}, "question"), ({"caption": ""}, "caption"),
                         ({"section": "results"}, "section"), ({"width": "third"}, "width")):
            with self.assertRaisesRegex(rules.Rejected, msg):
                rules.validate_figures([{**fig, **bad}], ctx, design)
        with self.assertRaisesRegex(rules.Rejected, "先定实验设置"):
            rules.validate_figures([fig], ctx)
        rid = store.add_research("figs", "user")
        store.propose_figures(rid, figs, None, "agent", 0, design)
        store.accept_figures(rid, 0)
        self.assertEqual(store.get_figures(rid)["design"]["method"], "ours")
        fid = store.get_figures(rid)["figures"][0]["id"]
        eid = store.add_experiment(rid, "spatial 0.92", "user")
        store.update_experiment(eid, "agent", 1, results=[{"name": "Spatial 成功率", "value": "0.92"}])
        exps = store.get_research(rid)["experiments"]
        cell = {"figure": fid, "row": "Spatial", "series": "ours", "experiment": eid, "result": "Spatial 成功率"}
        for bad, msg in (({"result": "Object"}, "results 里没有"), ({"experiment": "nope"}, "没有实验"), ({"series": "x"}, "没有 row")):
            with self.assertRaisesRegex(rules.Rejected, msg):
                rules.validate_cells([{**cell, **bad}], store.get_figures(rid)["figures"], exps)
        store.fill_cells(rid, rules.validate_cells([cell, {**cell, "row": "Object"}], store.get_figures(rid)["figures"], exps))
        store.fill_cells(rid, [{**cell, "row": "Object", "experiment": None, "result": None}])
        self.assertEqual([c["row"] for c in store.get_figures(rid)["cells"]], ["Spatial"])
        store.update_figures(rid, [{**store.get_figures(rid)["figures"][0], "rows": ["Object"]}], "user", 1)
        self.assertEqual(store.get_figures(rid)["cells"], [])

    def test_agent_cannot_overwrite_user_edited_experiment(self):
        rid = store.add_research("exp", "user")
        eid = store.add_experiment(rid, "success=0.9", "user")
        rev = store.update_experiment(eid, "agent", 1, title="agent 整理")
        rev = store.update_experiment(eid, "user", rev, title="用户改的标题")
        for base in (None, rev):
            with self.assertRaisesRegex(rules.Rejected, "用户已手动改过"):
                store.update_experiment(eid, "agent", base, title="agent 覆盖")
        e = store.get_experiment(eid)
        self.assertEqual((e["title"], e["edited_by"]), ("用户改的标题", "user"))
        store.update_experiment(eid, "agent", rev, asked=True, conclusion="inconclusive")
        e = store.get_experiment(eid)
        self.assertEqual((e["title"], e["conclusion"], e["edited_by"]), ("用户改的标题", "inconclusive", "agent"))

    def test_code_map_snippets_are_verbatim(self):
        files = {"src/policy.py": "import torch\n\ndef chunk(actions, horizon=50):\n    return actions.unfold(0, horizon, 1)\n"}
        rows = [{"component": "action chunk", "paper_quote": "frequencies of up to 50 Hz", "page": 2,
                 "file": "./src/policy.py", "snippet": "def chunk(actions,   horizon=50):", "note": "论文没写 horizon"}]
        ok = rules.validate_code_map(rows, PAGES, files.get)
        self.assertEqual((ok[0]["file"], ok[0]["line"]), ("src/policy.py", 3))
        with self.assertRaisesRegex(rules.Rejected, "找不到"):
            rules.validate_code_map([{**rows[0], "snippet": "def chunk(actions, horizon=16):"}], PAGES, files.get)
        with self.assertRaisesRegex(rules.Rejected, "不存在"):
            rules.validate_code_map([{**rows[0], "file": "nope.py"}], PAGES, files.get)

    def test_writing_proposal_waits_for_user(self):
        rid = store.add_research("w", "user")
        store.update_writing(rid, 0, abstract="我写的摘要")
        draft = rules.validate_writing_draft({"introduction": "引言 [arxiv:2410.24164 p.3]"}, store.paper_ids())
        store.propose_writing(rid, draft, "agent", 1)
        self.assertEqual(store.get_writing(rid)["abstract"], "我写的摘要")
        store.accept_writing(rid)
        w = store.get_writing(rid)
        self.assertEqual((w["abstract"], w["introduction"], w["proposal"]), ("我写的摘要", "引言 [arxiv:2410.24164 p.3]", None))

    def test_writing_discussion_is_its_own_thread(self):
        rid = store.add_research("分开讨论", "user")
        research, writing = store.thread_for(f"research:{rid}"), store.thread_for(f"writing:{rid}")
        self.assertNotEqual(research["id"], writing["id"])
        self.assertEqual((writing["scope_title"], writing["proposal"]), ("分开讨论", False))
        store.propose_writing(rid, {"abstract": "a"}, "agent")
        self.assertEqual((store.thread_for(f"writing:{rid}")["proposal"], store.thread_for(f"research:{rid}")["proposal"]), (True, False))
        related = store.thread_for(f"related:{rid}")
        self.assertNotIn(related["id"], (research["id"], writing["id"]))
        self.assertEqual((related["scope_title"], related["proposal"]), ("分开讨论", False))
        store.propose_related(rid, [], None, "agent")
        self.assertTrue(store.thread_for(f"related:{rid}")["proposal"])
        parts = [store.thread_for(f"{k}:{rid}") for k in ("plan", "code", "experiment")]
        self.assertEqual(len({t["id"] for t in parts} | {research["id"], writing["id"], related["id"]}), 6)
        self.assertEqual([(t["scope_title"], t["proposal"]) for t in parts], [("分开讨论", False)] * 3)
        store.propose_plan(rid, [], None, "agent")
        self.assertTrue(store.thread_for(f"plan:{rid}")["proposal"])

    def test_repo_clone_is_read_only(self):
        import subprocess
        from paper2brain import repos
        src = Path(tempfile.mkdtemp()) / "src"
        src.mkdir()
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        (src / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
        self.assertEqual(repos.normalize("git@github.com:Physical-Intelligence/openpi.git"), "https://github.com/Physical-Intelligence/openpi")
        dst = Path(tempfile.mkdtemp()) / "dst"
        subprocess.run(["git", "clone", "-q", str(src), str(dst)], check=True)
        sha = repos.clone("https://example.invalid/never", dst)
        self.assertEqual(repos.reader(dst, sha)("a.py"), "x = 1\n")
        self.assertIsNone(repos.reader(dst, sha)("missing.py"))
        push = subprocess.run(["git", "-C", str(dst), "remote", "get-url", "--push", "origin"], capture_output=True, text=True).stdout
        self.assertIn("DISABLED", push)

    def test_plan_changes_from_the_agent_wait_for_the_user(self):
        for bad in (None, [{"title": ""}], [{"title": "a", "status": "later"}], [{"title": "a", "due": "11月"}],
                    [{"id": "x", "title": "a"}, {"id": "x", "title": "b"}]):
            with self.assertRaises(rules.Rejected):
                rules.validate_plan(bad)
        rid = store.add_research("排计划", "user")
        mine = rules.validate_plan([{"title": "复现 baseline", "due": "2026-11-01"}, {"title": "写引言"}])
        self.assertEqual(store.update_plan(rid, mine, "user", 0), 1)
        a, b = store.get_plan(rid)["tasks"]
        self.assertEqual((a["status"], a["due"], b["due"]), ("todo", "2026-11-01", None))

        draft = rules.validate_plan([{**a, "status": "done"}, {"title": "最小验证实验", "due": "2026-11-15"}])
        store.propose_plan(rid, draft, "复现做完了，加上验证实验", "agent", 1)
        self.assertEqual(len(store.get_plan(rid)["tasks"]), 2)  # nothing changes until accepted
        with self.assertRaises(rules.Rejected):
            store.update_plan(rid, mine, "user", 0)  # stale revision
        store.accept_plan(rid, 1)
        p = store.get_plan(rid)
        self.assertEqual([(t["title"], t["status"]) for t in p["tasks"]], [("复现 baseline", "done"), ("最小验证实验", "todo")])
        self.assertEqual((p["revision"], p["proposal"], p["edited_by"]), (2, None, "agent"))
        self.assertEqual(store.get_research(rid)["plan"]["revision"], 2)

    def test_related_work_waits_for_the_user_and_becomes_materials(self):
        from paper2brain import export
        store.upsert_paper("arxiv:2410.24164", "π0: A Vision-Language-Action Flow Model", ["Kevin Black"], None, "2024-10-31")
        known = store.paper_ids()
        for bad in (None, [{"name": "", "papers": ["arxiv:2410.24164"]}], [{"name": "a", "papers": []}],
                    [{"name": "a", "papers": ["arxiv:2410.24164"], "text": "see [arxiv:9999.99999]"}],
                    [{"id": "x", "name": "a", "papers": ["2410.24164"]}, {"id": "x", "name": "b", "papers": ["2410.24164"]}]):
            with self.assertRaises(rules.Rejected):
                rules.validate_related(bad, known)
        rid = store.add_research("相关工作", "user")
        groups = rules.validate_related([{"name": "Flow policies", "papers": ["2410.24164"], "text": "Flow matching [arxiv:2410.24164].",
                                          "diff": "它们要逐任务微调"}], known)
        self.assertEqual(groups[0]["papers"], ["arxiv:2410.24164"])
        row = lambda p, *c: {"paper": p, "cells": list(c)}
        for bad in ({"columns": ["微调"], "rows": [row("2410.24164", "✓"), row("ours", "✗")]},  # one column
                    {"columns": ["微调", "真机"], "rows": [row("2410.24164", "✓"), row("ours", "✗", "✓")]},  # short row
                    {"columns": ["微调", "真机"], "rows": [row("2410.24164", "✓", "✓"), row("2410.24164", "✗", "✓")]},  # no ours
                    {"columns": ["微调", "真机"], "rows": [row("9999.99999", "✓", "✓"), row("ours", "✗", "✓")]}):  # not in groups
            with self.assertRaises(rules.Rejected):
                rules.validate_comparison(bad, groups)
        table = rules.validate_comparison({"columns": ["微调", "真机"], "rows": [row("2410.24164", "✓", "✗"), row("ours", "✗", "✓")]}, groups)
        self.assertEqual((table["rows"][0]["paper"], table["rows"][0]["name"]), ("arxiv:2410.24164", None))
        store.propose_related(rid, groups, "第一版", "agent", 0, table)
        self.assertEqual((store.get_related(rid)["groups"], store.get_research(rid)["links"]), ([], []))  # nothing until accepted
        store.accept_related(rid, 0)
        x = store.get_research(rid)
        self.assertEqual(x["related"]["comparison"], table)
        self.assertEqual((x["related"]["revision"], x["related"]["proposal"], x["related"]["groups"][0]["name"]), (1, None, "Flow policies"))
        self.assertEqual([l["ref"] for l in x["links"]], ["arxiv:2410.24164"])
        with self.assertRaises(rules.Rejected):
            store.update_related(rid, [], "user", 0)  # stale revision
        store.update_related(rid, x["related"]["groups"], "user", 1)
        self.assertEqual(store.get_related(rid)["comparison"], table)  # the user's group edits keep the table
        latex = export.research_export(rid)["latex"]
        self.assertIn("\\section{Related Work}\n\\paragraph{Flow policies}\nFlow matching~\\cite{black2024vision}.", latex)

    def test_overleaf_edits_stay_local_until_pushed(self):
        import subprocess
        from paper2brain import overleaf
        git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True, text=True).stdout  # noqa: E731
        pid = "0123456789abcdef01234567"
        self.assertEqual(overleaf.project_id(f"https://www.overleaf.com/project/{pid}"), pid)
        self.assertEqual(overleaf.project_id("https://github.com/jetteezhou/HY-Embodied-Apexio"), "github:jetteezhou/HY-Embodied-Apexio")
        self.assertEqual(overleaf.project_id("git@github.com:a/b.git"), "github:a/b")
        self.assertEqual(overleaf.local_dir("github:a/b"), store.home() / "overleaf" / "github" / "a" / "b")
        with self.assertRaises(rules.Rejected):
            overleaf.project_id("https://www.overleaf.com/read/abc")
        tmp = Path(tempfile.mkdtemp())
        git("init", "-q", "--bare", "-b", "master", str(tmp / "remote.git"))
        git("clone", "-q", str(tmp / "remote.git"), str(tmp / "theirs"))
        (tmp / "theirs" / "main.tex").write_text("Hello\n")
        git("-C", str(tmp / "theirs"), "add", ".")
        git("-C", str(tmp / "theirs"), "commit", "-qm", "init")
        git("-C", str(tmp / "theirs"), "push", "-q", "origin", "HEAD")
        path = overleaf.clone(pid, str(tmp / "remote.git"))
        rid = store.add_research("写论文", "user")
        store.set_overleaf(rid, pid)
        self.assertEqual(store.get_writing(rid)["overleaf"], pid)

        (path / "main.tex").write_text("Hello, world\n")
        self.assertEqual(overleaf.changed(pid), 1)
        self.assertIn("+Hello, world", overleaf.changes(pid)["diff"])
        with self.assertRaises(rules.Rejected):
            overleaf.pull(pid)
        overleaf.discard(pid)
        self.assertEqual((path / "main.tex").read_text(), "Hello\n")

        # someone edits elsewhere in Overleaf meanwhile; the push rebases on top of it
        (tmp / "theirs" / "refs.bib").write_text("@misc{a}\n")
        git("-C", str(tmp / "theirs"), "add", ".")
        git("-C", str(tmp / "theirs"), "commit", "-qm", "bib")
        git("-C", str(tmp / "theirs"), "push", "-q", "origin", "HEAD")
        (path / "main.tex").write_text("Hello, world\n")
        overleaf.push(pid, "agent edit")
        self.assertEqual(overleaf.changed(pid), 0)
        self.assertEqual(git("-C", str(tmp / "remote.git"), "show", "HEAD:main.tex"), "Hello, world\n")
        self.assertEqual(git("-C", str(tmp / "remote.git"), "log", "--format=%s"), "agent edit\nbib\ninit\n")

        # a conflicting edit in Overleaf: the push fails and the local edit survives uncommitted
        git("-C", str(tmp / "theirs"), "pull", "-q", "--rebase")
        (tmp / "theirs" / "main.tex").write_text("Bonjour\n")
        git("-C", str(tmp / "theirs"), "commit", "-qam", "fr")
        git("-C", str(tmp / "theirs"), "push", "-q", "origin", "HEAD")
        (path / "main.tex").write_text("Hallo\n")
        with self.assertRaises(rules.Rejected):
            overleaf.push(pid, "conflict")
        self.assertEqual((path / "main.tex").read_text(), "Hallo\n")
        self.assertEqual(overleaf.changed(pid), 1)

    def test_latex_export_turns_citations_into_cite(self):
        from paper2brain import export
        store.upsert_paper("arxiv:2410.24164", "π0: A Vision-Language-Action Flow Model", ["Kevin Black", "Noah Brown"], None, "2024-10-31")
        rid = store.add_research("e", "user")
        store.update_writing(rid, 0, abstract="We build on **flow matching** [arxiv:2410.24164 p.3].",
                             introduction="- first [arxiv:2410.24164]\n- 50% of cases")
        out = export.research_export(rid)
        self.assertIn(r"\textbf{flow matching}~\cite{black2024vision}.", out["latex"])
        self.assertIn(r"\begin{itemize}", out["latex"])
        self.assertIn(r"50\% of cases", out["latex"])
        self.assertIn("@article{black2024vision,", out["bib"])
        self.assertIn("eprint = {2410.24164}", out["bib"])

    def test_start_research_from_what_the_user_has(self):
        from paper2brain import pipeline
        tid = store.new_thread("library", text="用 latent action 预训练 tokenizer")
        rid, images = pipeline.start_research(
            None, "# 我想做：跨本体的动作 tokenizer\n背景很长……", ["VLA/tokenizer"], [("thread", tid)],
            [("notes.md", "text/markdown", "白板笔记：codebook 512".encode()), ("board.png", "image/png", b"\x89PNG fake")])
        r = store.get_research(rid)
        self.assertEqual(r["title"], "我想做：跨本体的动作 tokenizer")
        kinds = sorted(l["kind"] for l in r["links"])
        self.assertEqual(kinds, ["doc", "doc", "thread"])
        doc = next(l for l in r["links"] if l["kind"] == "doc" and l["label"] == "notes.md")
        self.assertEqual(store.get_doc(doc["ref"])["text"], "白板笔记：codebook 512")
        self.assertEqual(len(images), 1)
        with self.assertRaises(rules.Rejected):
            pipeline.start_research("", "  ", [], [], [])

    def test_anchored_discussion_stays_with_the_passage(self):
        store.upsert_paper("arxiv:2410.24164", "pi0")
        aid = store.add_anchor("arxiv:2410.24164", "pdf", "  action expert\n  that produces ", "user", page=1)
        t = store.thread_for(f"anchor:{aid}")
        store.add_message(t["id"], "user", "为什么要单独的 action expert？")
        a = next(x for x in store.get_paper("arxiv:2410.24164")["anchors"] if x["id"] == aid)
        self.assertEqual((a["quote"], a["page"], a["message_count"]), ("action expert that produces", 1, 1))
        with self.assertRaises(rules.Rejected):
            store.add_anchor("arxiv:2410.24164", "margin", "abc", "user")
        store.delete_anchor(aid)
        self.assertFalse(any(x["id"] == aid for x in store.anchors_for("arxiv:2410.24164")))

    def test_marking_deep_queues_one_report_at_a_time(self):
        from unittest import mock

        from paper2brain import cursor_api

        pid = "arxiv:2410.24164"
        store.set_fulltext(pid, "/tmp/none.pdf", PAGES)
        store.link(self.cid, pid, "user")
        self.assertEqual(store.deep_queue(), [])
        store.set_triage(self.cid, "deep")
        self.assertEqual(store.deep_queue(), [pid])
        started = []

        def fake_deep_read(p):
            started.append(p)
            store.add_run(f"r{len(started)}", "", "deepread", p, "", "CREATING")

        with mock.patch.object(cursor_api, "status", return_value={"enabled": True}), \
                mock.patch.object(cursor_api, "deep_read", side_effect=fake_deep_read):
            cursor_api.deepread_pending()
            cursor_api.deepread_pending()
            self.assertEqual(started, [pid])
            self.assertEqual(next(p for p in store.list_papers() if p["id"] == pid)["report_status"], "running")
            store.update_run("r1", status="ERROR")
            cursor_api.deepread_pending()
            self.assertEqual(started, [pid])

    def test_listed_papers_without_card_get_one_once(self):
        from unittest import mock

        from paper2brain import cursor_api

        pid = "arxiv:2501.00001"
        store.upsert_paper(pid, "no card yet")
        store.set_fulltext(pid, "/tmp/none.pdf", PAGES)
        self.assertNotIn(pid, store.card_queue())  # not in the library yet
        store.link(self.cid, pid, "user")
        store.add_run("cap", "", "capture", self.cid, "", "RUNNING")
        self.assertNotIn(pid, store.card_queue())  # its capture agent writes the card
        store.update_run("cap", status="FINISHED")
        self.assertEqual(store.card_queue()[0], pid)
        started = []

        def fake_write_card(p):
            started.append(p)
            store.add_run(f"c{len(started)}", "", "card", p, "", "CREATING")

        with mock.patch.object(cursor_api, "status", return_value={"enabled": True}), \
                mock.patch.object(cursor_api, "write_card", side_effect=fake_write_card):
            cursor_api.card_pending()
            cursor_api.card_pending()
            self.assertEqual(started, [pid])
            store.update_run("c1", status="ERROR")
            cursor_api.card_pending()
            self.assertEqual(started, [pid])

    def test_translation_runs_once_per_pdf_in_the_background(self):
        import time
        from unittest import mock

        from paper2brain import translate

        pid = "arxiv:2601.00777"
        store.upsert_paper(pid, "Translate Me")
        store.link(self.cid, pid, "user")
        pdf = store.paper_dir(pid) / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 en")
        store.set_fulltext(pid, pdf, PAGES)
        fake = Path(tempfile.mkdtemp()) / "pdf2zh"  # copies the PDF to <stem>-mono.pdf in the -o folder, as pdf2zh does
        fake.write_text('#!/bin/sh\nsrc="$1"; while [ "$1" != -o ]; do shift; done; cp "$src" "$2/$(basename "$src" .pdf)-mono.pdf"\n')
        fake.chmod(0o755)
        paper = lambda: store.get_paper(pid, with_anchors=False)  # noqa: E731
        with mock.patch.object(translate, "BIN", fake):
            self.assertEqual(translate.engine(), "search")
            translate.backfill()  # the quick search API is not set up: nothing starts
            self.assertFalse(translate._busy.locked())
            translate.set_engine("bing")
            self.assertEqual(translate.status(paper()), "pending")
            for _ in range(200):  # other tests' papers with a PDF may come first, one at a time
                if translate.status(paper()) != "pending":
                    break
                translate.backfill()
                time.sleep(0.05)
            self.assertEqual(translate.status(paper()), "done")
            self.assertEqual(translate.zh_path(pid).read_bytes(), b"%PDF-1.4 en")
            store.set_fulltext(pid, pdf, PAGES)  # a new PDF: the old translation is gone
            self.assertEqual(translate.status(paper()), "pending")
            fake.write_text("#!/bin/sh\necho boom >&2; exit 1\n")
            while translate._busy.locked():
                time.sleep(0.05)
            translate.translate(pid)
            self.assertEqual(translate.status(paper()), "failed")
        with mock.patch.object(translate, "BIN", fake.with_name("missing")):
            self.assertIsNone(translate.status(paper()))  # pdf2zh not installed
        translate.set_engine("search")  # switching engines retries the failures
        self.assertEqual(translate.status(paper()), "pending")

    def test_translation_bridge_hands_paragraphs_to_the_quick_search_api(self):
        import json
        import urllib.error
        import urllib.request
        from unittest import mock

        from paper2brain import cursor_api, translate

        with mock.patch.object(cursor_api, "_chat", return_value="你好") as chat, translate._bridge({"base_url": "x"}) as env:
            ask = lambda key: urllib.request.urlopen(urllib.request.Request(  # noqa: E731
                env["OPENAI_BASE_URL"] + "/chat/completions", method="POST", headers={"Authorization": f"Bearer {key}"},
                data=json.dumps({"model": "quick", "messages": [{"role": "user", "content": "Translate: hello"}]}).encode()))
            self.assertEqual(json.loads(ask(env["OPENAI_API_KEY"]).read())["choices"][0]["message"]["content"], "你好")
            self.assertEqual(chat.call_args[0], ({"base_url": "x"}, "Translate: hello"))
            with self.assertRaises(urllib.error.HTTPError):
                ask("wrong")

    def test_figures_are_cropped_from_captions_and_picked_by_number(self):
        import pymupdf

        from paper2brain import pipeline, structure

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 60), "Flow Policies for Bimanual Robots")
        page.insert_textbox(pymupdf.Rect(72, 80, 540, 200), "Body text. " * 60)
        page.draw_rect(pymupdf.Rect(150, 230, 460, 420), color=(0, 0, 1), fill=(0.8, 0.9, 1))
        page.insert_textbox(pymupdf.Rect(72, 430, 540, 470), "Figure 1: Overview of our architecture, from images to actions.")
        page.insert_textbox(pymupdf.Rect(72, 480, 540, 600), "More body text that discusses the results. " * 12)
        page = doc.new_page()
        page.insert_text((72, 40), "2    Flow Policies")  # running header right above the figure
        page.draw_rect(pymupdf.Rect(150, 70, 460, 250), color=(0, 0, 1), fill=(0.8, 0.9, 1))
        page.insert_textbox(pymupdf.Rect(72, 260, 540, 300), "Figure 2: Success rate on every task against the number of demonstrations.")
        paper = pipeline.paper_from_pdf(doc.tobytes())
        s = structure.extract(paper["id"])
        self.assertEqual([(f["n"], f["page"]) for f in s["figures"]], [(1, 1), (2, 2)])
        self.assertLess(s["figures"][1]["h"], (250 - 70 + 20) * 200 / 72)
        self.assertTrue((store.paper_dir(paper["id"]) / "figures" / "1.png").exists())
        self.assertEqual(structure.key_figures(s)[0]["n"], 1)
        card = rules.validate_card({**CARD, "results": [{"text": "x", "quote": "discusses the results", "page": 1}],
                                    "figures": [{"n": 1, "note": "先看动作头"}]}, store.pages(paper["id"]), s["figures"])
        self.assertEqual(card["figures"], [{"n": 1, "note": "先看动作头"}])
        self.assertEqual(structure.key_figures(s, card)[0]["note"], "先看动作头")
        with self.assertRaisesRegex(rules.Rejected, r"图 7 不存在"):
            rules.validate_card({**card, "figures": [{"n": 7}]}, store.pages(paper["id"]), s["figures"])

    def test_title_card_knows_where_to_jump(self):
        pid = "arxiv:2410.24164"
        store.link(self.cid, pid, "user")
        store.put_report(pid, {"sections": [{"heading": "方法", "body": "b", "citations": []}]}, "agent")
        store.add_anchor(pid, "card", "flow matching", "user")
        store.add_message(store.thread_for(f"paper:{pid}")["id"], "user", "为什么用 flow matching？")
        j = next(p for p in store.list_papers() if p["id"] == pid)["jumps"]
        self.assertEqual((j["report"], j["anchors"], j["chat"]), (["方法"], 1, 1))
        mine = next(c for c in store.list_captures("inbox") if c["id"] == self.cid)
        self.assertEqual(mine["papers"][0]["jumps"]["report"], ["方法"])

    def test_find_request_becomes_a_capture_with_its_own_discussion(self):
        from unittest import mock

        from paper2brain import cursor_api, server

        with mock.patch.object(cursor_api, "status", return_value={"enabled": True}), \
                mock.patch.object(cursor_api, "config", return_value=AGENT_CFG), \
                mock.patch.object(cursor_api, "_spawn"):
            cap = server.find({}, {"text": "帮我找 flow matching 做动作头的论文"})
        self.assertEqual((cap["source"], cap["channel"], cap["status"]), ("find", "find", "finding"))
        self.assertEqual(cap["runs"][0]["kind"], "find")
        thread = store.thread_for(f"find:{cap['id']}")
        msgs = thread["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["run_id"], cap["runs"][0]["id"])
        self.assertEqual(thread["agent_id"], cap["runs"][0]["agent_id"])
        self.assertEqual(cap["agent_id"], cap["runs"][0]["agent_id"])
        self.assertIn("flow matching 做动作头", cursor_api.scope_text(f"find:{cap['id']}"))

    def test_clicking_a_citation_takes_an_unlisted_paper_into_the_library_once(self):
        from unittest import mock

        from paper2brain import cursor_api, server

        store.upsert_paper("arxiv:2501.00071", "fetched by the agent, never listed")
        self.assertFalse(store.get_paper("arxiv:2501.00071")["listed"])
        with mock.patch.object(cursor_api, "status", return_value={"enabled": False}):
            first = server.paper_add({}, {}, "arxiv:2501.00071")
            again = server.paper_add({}, {}, "arxiv:2501.00071")
        self.assertEqual((first["added"], first["listed"], again["added"]), (True, True, False))
        self.assertIn("arxiv:2501.00071", [p["id"] for p in store.list_papers()])

    def test_chat_passes_pasted_images_to_the_agent(self):
        from unittest import mock

        from paper2brain import cursor_api, server

        png = base64.b64encode(b"\x89PNG").decode()
        with mock.patch.object(cursor_api, "discuss", return_value="r1") as d:
            server.chat({}, {"scope": "library", "images": [{"mime": "image/png", "data": png}, {"mime": "text/html", "data": png}]})
        self.assertEqual(d.call_args.kwargs["images"], [(b"\x89PNG", "image/png")])
        self.assertEqual(d.call_args.args[1], "（附了 1 张图）")
        with self.assertRaisesRegex(rules.Rejected, "不能为空"):
            server.chat({}, {"scope": "library", "reply": False, "images": [{"mime": "image/png", "data": png}]})

    def test_regenerating_asks_the_last_question_again_of_a_new_agent(self):
        from unittest import mock

        from paper2brain import cursor_api, server

        with mock.patch.object(cursor_api, "status", return_value={"enabled": True}), \
                mock.patch.object(cursor_api, "config", return_value=AGENT_CFG), \
                mock.patch.object(cursor_api, "_spawn"):
            r1 = server.chat({}, {"scope": "library", "text": "哪篇值得深读？"})["run_id"]
            tid = store.thread_for("library")["id"]
            with self.assertRaisesRegex(rules.Rejected, "还在写"):
                server.chat({}, {"scope": "library", "thread_id": tid, "redo": True})
            store.update_run(r1, status="FINISHED")
            r2 = server.chat({}, {"scope": "library", "thread_id": tid, "redo": True, "model": {"id": "other-model"}})["run_id"]
        t = store.thread_for(thread_id=tid)
        self.assertEqual([(m["role"], m["text"]) for m in t["messages"][-2:]], [("user", "哪篇值得深读？"), ("assistant", "")])
        self.assertEqual(sum(m["text"] == "哪篇值得深读？" for m in t["messages"]), 1)
        self.assertEqual(t["messages"][-1]["run_id"], r2)
        self.assertNotEqual(store.get_run(r2)["agent_id"], store.get_run(r1)["agent_id"])
        self.assertEqual(t["model"], {"id": "other-model"})

    def test_finding_from_a_research_uses_its_papers_as_seeds_and_avoids_rejects(self):
        from unittest import mock

        from paper2brain import cursor_api, server

        store.upsert_paper("arxiv:2501.00081", "seed paper")
        store.upsert_paper("arxiv:2501.00082", "off-track paper")
        rid = store.add_research("一步动作头", "user")
        with self.assertRaisesRegex(rules.Rejected, "种子"):
            server.research_find({}, {}, rid)
        store.link_research(rid, "paper", "arxiv:2501.00081", "user", "baseline")
        with mock.patch.object(cursor_api, "status", return_value={"enabled": True}), \
                mock.patch.object(cursor_api, "config", return_value=AGENT_CFG), \
                mock.patch.object(cursor_api, "_spawn"):
            first = server.research_find({}, {}, rid)
            store.link(first["id"], "arxiv:2501.00082", "agent")
            store.unlink(first["id"], "arxiv:2501.00082", "user")
            second = server.research_find({}, {"text": "要有真机"}, rid)
        self.assertEqual(store.get_capture(second["id"])["research"][0]["id"], rid)
        self.assertEqual(store.rejected_in_research(rid), ["arxiv:2501.00082"])
        prompt = store.get_run(second["runs"][0]["id"])["prompt"]
        self.assertIn("arxiv:2501.00081《seed paper》（用户标为：baseline）", prompt)
        self.assertIn("arxiv:2501.00082《off-track paper》", prompt)
        self.assertIn("要有真机", prompt)

    def test_working_names_the_page_a_run_is_on(self):
        rid = store.add_research("灵巧手", "user")
        store.add_run("w-deep", "", "deepread", "arxiv:2410.24164", "", "RUNNING")
        store.add_run("w-card", "", "card", "arxiv:2410.24164", "", "WAITING")
        store.add_run("w-write", "", "writing", rid, "", "WAITING")
        eid = store.add_experiment(rid, "成功率 0.4", "user", title="真机")
        store.add_run("w-exp", "", "experiment", eid, "", "CREATING")
        tid = store.thread_for(f"plan:{rid}")["id"]
        store.add_run("w-chat", "", "chat", tid, "", "RUNNING")
        store.add_run("w-cap", "", "capture", self.cid, "", "QUEUED")
        store.add_run("w-done", "", "deepread", "arxiv:2410.24164", "", "FINISHED")
        aid = store.add_anchor("arxiv:2410.24164", "pdf", "flow matching", "user", page=1)
        at = store.thread_for(f"anchor:{aid}")["id"]
        store.add_run("w-anchor", "", "chat", at, "", "RUNNING")
        by = {x["id"]: x for x in store.working()}
        self.assertNotIn("w-done", by)
        self.assertEqual(by["w-deep"]["go"], {"to": "reader", "id": "arxiv:2410.24164", "tab": "report"})
        self.assertEqual(by["w-deep"]["title"], "pi0")
        self.assertEqual(by["w-card"]["go"], {"to": "paper", "id": "arxiv:2410.24164"})
        self.assertEqual(by["w-write"]["go"], {"to": "research", "id": rid, "tab": "writing"})
        self.assertEqual(by["w-write"]["title"], "灵巧手")
        self.assertEqual((by["w-exp"]["go"]["id"], by["w-exp"]["go"]["tab"]), (rid, "experiments"))
        self.assertIn("真机", by["w-exp"]["title"])
        self.assertEqual(by["w-chat"]["go"], {"to": "research", "id": rid, "tab": "plan", "thread": tid})
        self.assertEqual(by["w-cap"]["go"], {"to": "capture", "id": self.cid})
        self.assertEqual(by["w-anchor"]["go"], {"to": "reader", "id": "arxiv:2410.24164", "tab": "anchor", "anchor": aid, "thread": at})
        ids = [x["id"] for x in store.working() if str(x["id"]).startswith("w-")]
        self.assertLess(ids.index("w-anchor"), ids.index("w-deep"))
        store.add_run("w-fin1", "", "card", "arxiv:2410.24164", "", "FINISHED")
        store.add_run("w-fin2", "", "writing", rid, "", "ERROR")
        store.add_run("w-odd", "", "nope", "x", "", "FINISHED")
        self.assertEqual([x["id"] for x in store.recent_work()], ["w-fin2", "w-fin1", "w-done"])

    def test_topic_tree_is_seeded(self):
        tree = {t["path"]: [c["name"] for c in t["children"]] for t in store.topic_tree()}
        self.assertEqual(tree["VLA"], ["tokenizer", "推理加速", "实时推理"])
        self.assertIn("V-JEPA", tree["WAM"])


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer

        from paper2brain import server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, **headers):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **headers})
        r = c.getresponse()
        r.read()
        return r.status

    def test_only_this_host_and_same_origin_json_writes(self):
        own = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.req("GET", "/api/state"), 200)
        self.assertEqual(self.req("GET", "/api/state", Host=f"localhost:{self.port}"), 200)
        self.assertEqual(self.req("GET", "/api/state", Host=f"evil.example:{self.port}"), 403)
        body = '{"scope": "library", "text": "跨站写入"}'.encode()
        self.assertEqual(self.req("POST", "/api/threads", body, **{"Content-Type": "text/plain"}), 415)
        self.assertEqual(self.req("POST", "/api/threads", body, **{"Content-Type": "application/json", "Origin": "http://evil.example"}), 403)
        self.assertEqual(self.req("POST", "/api/threads", body, **{"Content-Type": "application/json", "Origin": "null"}), 403)
        self.assertEqual(self.req("DELETE", "/api/anchors/x", Origin="http://evil.example"), 403)
        self.assertEqual(self.req("POST", "/api/threads", body, **{"Content-Type": "application/json", "Origin": own}), 200)
        self.assertEqual(sum(t["first"] == "跨站写入" for t in store.recent_threads()), 1)

    def test_pdf_comes_in_byte_ranges(self):
        import http.client
        pid = "arxiv:2601.00888"
        store.upsert_paper(pid, "Ranged")
        pdf = store.paper_dir(pid) / "paper.pdf"
        pdf.write_bytes(b"%PDF-0123456789")
        store.set_fulltext(pid, pdf, PAGES)
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", f"/api/papers/{pid}/pdf", headers={"Host": f"127.0.0.1:{self.port}", "Range": "bytes=5-8"})
        r = c.getresponse()
        self.assertEqual((r.status, r.read(), r.headers["Content-Range"]), (206, b"0123", "bytes 5-8/15"))

    def test_tunnel_host_needs_the_password(self):
        from unittest import mock
        auth = lambda pw: {"Authorization": "Basic " + base64.b64encode(f"me:{pw}".encode()).decode()}  # noqa: E731
        tunnel = "x.trycloudflare.com"
        with mock.patch.dict(os.environ, {"P2B_HOSTS": tunnel, "P2B_PASSWORD": "s3cret"}):
            self.assertEqual(self.req("GET", "/api/state"), 401)  # loopback too: the tunnel arrives from 127.0.0.1
            self.assertEqual(self.req("GET", "/api/state", **auth("wrong")), 401)
            self.assertEqual(self.req("GET", "/api/state", Authorization="Basic !!"), 401)
            self.assertEqual(self.req("GET", "/api/state", Host=tunnel, **auth("s3cret")), 200)
            self.assertEqual(self.req("GET", "/api/state", Host="evil.example", **auth("s3cret")), 403)
            body = '{"scope": "library", "text": "隧道写入"}'.encode()
            self.assertEqual(self.req("POST", "/api/threads", body, Host=tunnel, **auth("s3cret"),
                                      **{"Content-Type": "application/json", "Origin": f"https://{tunnel}"}), 200)
        from paper2brain import server
        with mock.patch.dict(os.environ, {"P2B_PASSWORD": ""}), self.assertRaises(SystemExit):
            server.serve("0.0.0.0", 0)

    def test_lan_reaches_the_ip_it_connected_to_with_the_password(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from unittest import mock

        from paper2brain import server
        ip = server._lan_ip()
        if not ip or ip.startswith("127."):
            self.skipTest("no LAN interface")
        httpd = ThreadingHTTPServer(("0.0.0.0", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]

        def get(host, **headers):
            c = http.client.HTTPConnection(ip, port)
            c.request("GET", "/api/state", headers={"Host": host, **headers})
            r = c.getresponse()
            r.read()
            c.close()
            return r.status
        ok = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
        try:
            with mock.patch.dict(os.environ, {"P2B_PASSWORD": "s3cret"}):
                self.assertEqual(get(f"{ip}:{port}", **ok), 200)
                self.assertEqual(get(f"{ip}:{port}"), 401)
                self.assertEqual(get(f"rebind.example:{port}", **ok), 403)
            with mock.patch.dict(os.environ, {"P2B_PASSWORD": ""}), mock.patch.object(store, "config", lambda: {}):
                self.assertEqual(get(f"{ip}:{port}"), 403)  # no password: the LAN is refused even if bound to it
        finally:
            httpd.shutdown()


class Handoff(unittest.TestCase):
    def test_paths_from_the_other_mac_point_here(self):
        moved, _ = store.add_repo("https://x/a.git", "/Users/old/paper2brain-data/repos/x/a")
        kept, _ = store.add_repo("https://x/b.git", "/opt/elsewhere/b")
        twin, _ = store.add_repo("https://x/c.git", "/Users/old/paper2brain-data2/repos/c")  # shares the prefix, not the folder
        self.assertGreaterEqual(store.rehome("/Users/old/paper2brain-data"), 1)
        self.assertEqual(store.get_repo(moved)["path"], f"{store.home()}/repos/x/a")
        self.assertEqual(store.get_repo(kept)["path"], "/opt/elsewhere/b")
        self.assertEqual(store.get_repo(twin)["path"], "/Users/old/paper2brain-data2/repos/c")
        self.assertEqual(store.rehome(store.home()), 0)


class Sync(unittest.TestCase):
    """Two Macs, each with its own data directory, sharing one iCloud folder."""

    def setUp(self):
        from unittest import mock

        from paper2brain import handoff
        self.mock, self.handoff = mock, handoff
        self.drop, self.a, self.b = (tempfile.mkdtemp(prefix=f"p2b-sync-{n}-") for n in "dab")
        for h in (self.a, self.b):
            Path(h, "config.json").write_text('{"sync": true}')

    def on(self, side):
        from contextlib import ExitStack
        s = ExitStack()
        s.enter_context(self.mock.patch.dict(os.environ, {"P2B_HOME": side}))
        s.enter_context(self.mock.patch.object(self.handoff, "DROP", Path(self.drop)))
        s.enter_context(self.mock.patch.object(self.handoff, "ME", "mac-a" if side == self.a else "mac-b"))
        return s

    def test_discussions_flow_both_ways_newest_change_winning(self):
        from paper2brain import sync
        with self.on(self.a):
            t = store.thread_for("library")["id"]
            store.set_thread_agent(t, "agent-on-a")
            store.add_message(t, "user", "两台一起用")
            store.add_run("tc-1", "agent-on-a", "chat", t, "p", "RUNNING")
            store.add_message(t, "assistant", "", "tc-1")
            sync.run()
        with self.on(self.b):
            sync.run()
            got = store.thread_for(thread_id=t)
            self.assertEqual([m["text"] for m in got["messages"]], ["两台一起用", ""])
            self.assertIsNone(got["agent_id"])  # B starts its own agent
            self.assertIsNone(store.get_run("tc-1"))  # still running on A: not B's to watch
        with self.on(self.a):
            store.update_run("tc-1", status="FINISHED", text="可以")
            sync.run()
        with self.on(self.b):
            sync.run()
            self.assertEqual(store.thread_for(thread_id=t)["messages"][1]["run_text"], "可以")
            store.set_thread_title(t, "B 起的标题", "user")
            sync.run()
        with self.on(self.a):
            sync.run()
            self.assertEqual(store.thread_for(thread_id=t)["title"], "B 起的标题")
            self.assertEqual(store.thread_for(thread_id=t)["agent_id"], "agent-on-a")  # A keeps its own agent
            with store.tx() as c:  # an older change from B arriving late loses to what A already has
                sync._apply(c, {"tbl": "threads", "pk": [t], "at": "2000-01-01T00:00:00.000", "row": {"id": t, "title": "旧"}})
            self.assertEqual(store.thread_for(thread_id=t)["title"], "B 起的标题")

    def test_a_note_waits_for_its_paper_and_bad_lines_are_dropped(self):
        import json

        from paper2brain import sync
        pid, c_dir = "arxiv:2601.00001", Path(self.drop, "changes", "mac-c")
        c_dir.mkdir(parents=True)
        note = {"tbl": "notes", "pk": ["n1"], "at": "2026-01-01T00:00:01.000",
                "row": {"id": "n1", "created_at": "x", "text": "A 的笔记", "by": "user", "paper_id": pid}}
        (c_dir / f"{1:012d}.jsonl").write_text(f'{{"home": "/x"}}\nnot json\n{{"tbl": "notes"}}\n{json.dumps(note)}\n')
        with self.on(self.b):
            sync.run()
            with store.tx() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sync_pending").fetchone()[0], 1)
            paper = {"tbl": "papers", "pk": [pid], "at": "2026-01-01T00:00:02.000", "row": {"id": pid, "created_at": "x", "title": "Only on C"}}
            (c_dir / f"{2:012d}.jsonl").write_text(f'{{"home": "/x"}}\n{json.dumps(paper)}\n')
            sync.run()  # the paper arrives, and the waiting note right after it
            with store.tx() as c:
                self.assertEqual(c.execute("SELECT text FROM notes").fetchone()[0], "A 的笔记")
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sync_pending").fetchone()[0], 0)

    def test_papers_with_their_pdf_topics_and_research_reach_the_other_mac(self):
        from paper2brain import sync
        with self.on(self.a):
            store.upsert_paper("arxiv:2601.00002", "Paper from A")
            pdf = store.paper_dir("arxiv:2601.00002") / "paper.pdf"
            pdf.write_bytes(b"%PDF from A")
            store.set_fulltext("arxiv:2601.00002", pdf, PAGES)
            store.add_topic("同步主题", None, "user")
            store.set_topics("arxiv:2601.00002", ["同步主题"], "user")
            rid = store.add_research("一起做的研究", "user")
            sync.run()
        with self.on(self.b):
            sync.run()
            p = store.get_paper("arxiv:2601.00002", with_anchors=False)
            self.assertTrue(p["pdf_path"].startswith(self.b))  # the path points into B's own folder
            self.assertEqual(Path(p["pdf_path"]).read_bytes(), b"%PDF from A")
            self.assertEqual(p["topics"], ["同步主题"])
            self.assertEqual(store.get_research(rid)["title"], "一起做的研究")
        with self.on(self.a):
            store.edit_topic("同步主题", "改名的主题", "")  # the key itself changes: the old path must go on B too
            sync.run()
        with self.on(self.b):
            sync.run()  # the old topic waits until its papers have moved off it, within the same round
            self.assertEqual(store.get_paper("arxiv:2601.00002", with_anchors=False)["topics"], ["改名的主题"])
            with store.tx() as c:
                paths = {r[0] for r in c.execute("SELECT path FROM topics")}
            self.assertIn("改名的主题", paths)
            self.assertNotIn("同步主题", paths)

    def test_after_pull_this_macs_own_sent_changes_come_back(self):
        import shutil

        from paper2brain import sync
        with self.on(self.a):
            store.counts()
            snap = store.backup("for-b.sqlite")  # A's library, before B's note reaches it
        with self.on(self.b):
            store.add_note("B 记的", "user")
            sync.run()
            shutil.copy(snap, Path(self.b) / "p2b.sqlite")  # what pull does: B takes A's snapshot
            for f in ("p2b.sqlite-wal", "p2b.sqlite-shm"):
                (Path(self.b) / f).unlink(missing_ok=True)
            store._initialized.discard(self.b)
            with store.tx() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
            sync.apply_others(include_self=True)
            with store.tx() as c:
                self.assertEqual(c.execute("SELECT text FROM notes").fetchone()[0], "B 记的")

    def test_keys_and_settings_follow_the_mac_that_changed_them(self):
        import json
        import time

        from paper2brain import sync
        cfg = lambda side: json.loads(Path(side, "config.json").read_text())  # noqa: E731
        Path(self.a, "config.json").write_text(json.dumps({"sync": True, "machine": "a", "cursor_api_key": "k1", "search_api": {"api_key": "g1"}}))
        Path(self.b, "config.json").write_text(json.dumps({"sync": True, "machine": "b", "only_on_b": 1}))
        with self.mock.patch.object(sync.subprocess, "run") as run:  # never restart the real worker
            with self.on(self.a):
                self.assertEqual(sync.sync_config(), "sent")
            with self.on(self.b):
                self.assertEqual(sync.sync_config(), "taken")  # B never synced: it takes A's keys, never sends its empty ones first
                self.assertEqual(cfg(self.b), {"sync": True, "machine": "b", "only_on_b": 1, "cursor_api_key": "k1", "search_api": {"api_key": "g1"}})
                self.assertTrue(any("kickstart" in a.args[0] for a in run.call_args_list))  # a new Cursor key: worker restarts
                self.assertIsNone(sync.sync_config())  # nothing new either way
                time.sleep(0.01)
                Path(self.b, "config.json").write_text(json.dumps({**cfg(self.b), "search_api": {"api_key": "g2"}}))
                self.assertEqual(sync.sync_config(), "sent")
            with self.on(self.a):
                self.assertEqual(sync.sync_config(), "taken")
                self.assertEqual(cfg(self.a)["search_api"], {"api_key": "g2"})
                self.assertEqual(cfg(self.a)["machine"], "a")  # each Mac keeps its own worker name

    def test_a_new_mac_gets_its_own_worker_name_only_when_both_run(self):
        with self.mock.patch.object(self.handoff, "ME", "Zhou's MacBook Air"):
            merge = self.handoff.merge_config
            self.assertEqual(merge({"machine": "p2b-mac", "sync": True}, {}, "office")["machine"], "p2b-zhou-s-macbook-air")
            self.assertEqual(merge({"machine": "p2b-mac"}, {}, "office")["machine"], "p2b-mac")  # taking turns: one name
            self.assertEqual(merge({"machine": "p2b-mac", "sync": True}, {"machine": "home"}, "office")["machine"], "home")
            self.assertEqual(merge({"machine": "p2b-mac", "sync": True, "password": "p"}, {}, "Zhou's MacBook Air")["machine"], "p2b-mac")

    def test_auto_push_only_on_the_owner(self):
        with self.on(self.a), self.mock.patch.object(self.handoff, "_pushed", 0.0):
            store.counts()
            self.handoff.auto_push()
            self.assertEqual(self.handoff.meta()["owner"], "mac-a")
            Path(self.drop, "handoff.json").write_text('{"owner": "mac-b"}')
            self.handoff._pushed = 0.0
            self.handoff.auto_push()
            self.assertEqual(self.handoff.meta()["owner"], "mac-b")  # B took the library: A leaves its snapshot alone
            self.assertFalse(self.handoff.holder())  # nor does A take the inbox or start background agents
        with self.on(self.b):
            self.assertTrue(self.handoff.holder())


class Diagrams(unittest.TestCase):
    def test_names_cannot_leave_the_diagram_folder(self):
        rid = store.add_research("方法图", "user")
        for bad in ("../x", "a/b", "", "Framework", ".."):
            with self.assertRaises(rules.Rejected):
                store.diagram_dir(rid, bad)
        with self.assertRaises(rules.Rejected):
            store.diagram_dir("../etc", "framework")

    def test_render_exports_vector_pdf_and_flags_overflow(self):
        import importlib.util
        if not importlib.util.find_spec("playwright"):
            self.skipTest("没装 playwright")
        import pymupdf
        from paper2brain import diagram
        rid = store.add_research("方法图", "user")
        src = Path(tempfile.mkdtemp()) / "f.html"
        src.write_text('<div id="figure" style="width:600px;font-size:20px"><div style="width:60px;overflow:hidden;white-space:nowrap">'
                       'Residual Flow Expert</div>\\(a_t\\)</div>')
        try:
            out = diagram.render(rid, "framework", src, caption="Overview.")
        except rules.Rejected as e:
            self.skipTest(str(e))
        self.assertEqual(out["width"], 600)
        self.assertTrue(any("溢出" in w for w in out["warnings"]))
        self.assertFalse(any("字号" in w for w in out["warnings"]), "KaTeX subscripts are not checked")
        with pymupdf.open(out["pdf"]) as d:
            self.assertEqual((len(d), len(d[0].get_images())), (1, 0))
        self.assertEqual([d["caption"] for d in store.diagrams(rid)], ["Overview."])
        self.assertEqual(out["kind"], "method")
        diagram.render(rid, "framework", src, kind="motivation")
        self.assertEqual([(d["kind"], d["caption"]) for d in store.diagrams(rid)], [("motivation", "Overview.")], "a re-render keeps what it does not set")
        with self.assertRaises(rules.Rejected):
            diagram.render(rid, "framework", src, kind="teaser")
        src.write_text('<div id="figure" style="width:600px;height:120px;position:relative;font-size:20px">'
                       '<span style="position:absolute;left:0;top:10px">Goal conditioning</span><span style="position:absolute;left:40px;top:14px">Execute</span>'
                       '<span style="position:absolute;left:0;top:70px">Target-step pairing</span>'
                       '<svg style="position:absolute;inset:0" width="600" height="120"><path d="M0,82 H600" stroke="#000" fill="none"/></svg></div>')
        warnings = diagram.render(rid, "framework", src)["warnings"]
        self.assertTrue(any("重叠" in w and "Execute" in w for w in warnings), warnings)
        self.assertTrue(any(w.startswith("连线穿过文字：「Target-step") for w in warnings), warnings)
        store.delete_diagram(rid, "framework")
        self.assertEqual(store.diagrams(rid), [])


class scratch_home:
    """A fresh P2B_HOME for tests that need an empty (or hand-made) database."""
    def __enter__(self):
        self.old = os.environ["P2B_HOME"]
        os.environ["P2B_HOME"] = tempfile.mkdtemp(prefix="p2b-scratch-")
        return Path(os.environ["P2B_HOME"])

    def __exit__(self, *exc):
        os.environ["P2B_HOME"] = self.old


class AgentHarness(unittest.TestCase):
    """A scratch library with the Cursor API mocked out: nothing is sent, started threads are recorded."""
    def setUp(self):
        from unittest import mock

        from paper2brain import cursor_api
        self.api, self.home = cursor_api, scratch_home()
        self.home.__enter__()
        self.cfg = dict(AGENT_CFG)
        self.spawned = []
        self.patches = [mock.patch.object(cursor_api, "config", side_effect=lambda: self.cfg),
                        mock.patch.object(cursor_api, "_spawn", side_effect=lambda fn, *a: self.spawned.append((fn.__name__, a)))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.home.__exit__()

    status = staticmethod(lambda rid: store.get_run(rid)["status"])


class Agents(AgentHarness):
    """Run queue, timeouts and restart recovery."""
    def test_queue_holds_runs_beyond_the_limit_and_one_per_agent(self):
        self.cfg["max_agents"] = 1
        a = self.api.start_async("card", "p1", "写卡 1")
        b = self.api.start_async("card", "p2", "写卡 2")
        self.assertEqual((self.status(a), self.status(b)), ("CREATING", "WAITING"))
        self.assertEqual(len(self.spawned), 1)
        store.update_run(a, status="FINISHED")
        self.api.pump()
        self.assertEqual((self.status(b), len(self.spawned)), ("CREATING", 2))
        self.cfg["max_agents"] = 5
        c = self.api.start_async("chat", "t1", "问 1", agent_id="bc-same")
        d = self.api.start_async("chat", "t1", "问 2", agent_id="bc-same")
        self.assertEqual((self.status(c), self.status(d)), ("CREATING", "WAITING"))

    def test_each_module_creates_agents_on_its_own_model(self):
        import json
        from unittest import mock
        self.cfg["model"] = "composer-2.5"
        self.cfg["models"] = {"research": {"id": "claude-opus-5-5", "params": [{"id": "context", "value": "1m"}]}}
        rid = store.add_research("长讨论", "user")
        with mock.patch.object(self.api, "pump"):
            r = self.api.discuss(f"research:{rid}", "问题成立吗？")
            p = self.api.start_async("card", "p1", "写卡")
            f = self.api.start_async("chat", "t1", "接着问", agent_id="bc-old")
            d = self.api.start_async("motivation", rid, "画动机图")
        launch = lambda run: store.get_run(run)["launch"]["model"]  # noqa: E731
        self.assertEqual(launch(r), self.cfg["models"]["research"])
        self.assertEqual(launch(d), self.cfg["models"]["research"], "drawing keeps the research model until set on its own")
        self.assertEqual(launch(p), {"id": "composer-2.5"})
        self.assertIsNone(launch(f))  # a follow-up keeps the model its agent was created with
        with mock.patch.object(self.api, "_json", return_value={"run": {"id": "x"}}) as call:
            self.api._send("bc-1", "hi", new=True, model=launch(r))
        self.assertEqual(call.call_args.args[2]["model"], self.cfg["models"]["research"])

        (store.home() / "config.json").write_text(json.dumps({"cursor_api_key": "k"}))
        self.api.set_model("deepread", {"id": "gpt-5.6-sol", "params": [{"id": "context", "value": "1m"}]})
        self.api.set_model("research", None)
        saved = json.loads((store.home() / "config.json").read_text())
        self.assertEqual(saved, {"cursor_api_key": "k", "models": {"deepread": {"id": "gpt-5.6-sol", "params": [{"id": "context", "value": "1m"}]}}})
        with self.assertRaises(rules.Rejected):
            self.api.set_model("nope", None)

    def test_switching_model_moves_the_discussion_to_a_new_agent_with_its_context(self):
        from unittest import mock
        with mock.patch.object(self.api, "pump"):
            first = self.api.discuss("library", "第一问")
            t = store.thread_for("library")
            store.update_run(first, status="FINISHED", text="第一答")
            run = self.api.discuss("library", "第二问", t["id"], model=self.api.clean_model({"id": "gpt-6", "backend": "tcodex", "x": 1}))
        r, after = store.get_run(run), store.thread_for(thread_id=t["id"])
        self.assertNotEqual(after["agent_id"], t["agent_id"])
        self.assertEqual((r["agent_id"], r["launch"]["model"], after["model"]), (after["agent_id"], {"id": "gpt-6", "backend": "tcodex"}, {"id": "gpt-6", "backend": "tcodex"}))
        self.assertIn("第一答", r["prompt"])
        with self.assertRaises(rules.Rejected):
            self.api.clean_model({"id": 1})

    def test_new_agent_is_bound_at_once_and_forgotten_if_never_created(self):
        from unittest import mock
        t = store.thread_for("library")
        pid = self.api.start_async("chat", t["id"], "你好")
        aid = store.get_run(pid)["agent_id"]
        self.assertEqual(store.thread_for(thread_id=t["id"])["agent_id"], aid)
        with mock.patch.object(self.api, "_send", side_effect=self.api.AgentError("Cursor API 500")):
            self.api._launch(store.get_run(pid))
        self.assertEqual(self.status(pid), "ERROR")
        self.assertIsNone(store.thread_for(thread_id=t["id"])["agent_id"])

    def test_tcodex_backend_runs_locally_and_resumes_its_session(self):
        from unittest import mock
        bin_dir = Path(tempfile.mkdtemp())
        (bin_dir / "tcodex").write_text(
            '#!/bin/sh\necho "$@" >> "$(dirname "$0")/args"\n'
            'echo \'{"type":"thread.started","thread_id":"s-1"}\'\necho "not json"\n'
            'echo \'{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"./p2b papers"}}\'\n'
            'echo \'{"type":"error","message":"Reconnecting... 2/5 (stream disconnected before completion)"}\'\n'
            'echo \'{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"答案"}}\'\n')
        (bin_dir / "tcodex").chmod(0o755)
        self.cfg["backend"] = "tcodex"
        t = store.thread_for("library")
        with mock.patch.object(self.api, "_PATH", f"{bin_dir}:/usr/bin:/bin"):
            self.assertTrue(self.api.status()["enabled"])
            with mock.patch.object(self.api, "pump"):
                self.api._launch(store.get_run(self.api.start_async("chat", t["id"], "你好")))
                run = store.last_run("chat")
                self.assertEqual((run["status"], run["text"], run["tools"]), ("FINISHED", "答案", [{"name": "command_execution", "status": "done", "args": "./p2b papers"}]))
                aid = store.thread_for(thread_id=t["id"])["agent_id"]
                self.api._launch(store.get_run(self.api.start_async("chat", t["id"], "接着说", agent_id=aid)))
                self.assertEqual(store.last_run("chat")["status"], "FINISHED")
        args = (bin_dir / "args").read_text().splitlines()
        self.assertTrue(args[0].endswith("你好") and "resume" not in args[0])
        self.assertTrue(args[1].endswith("resume s-1 接着说"))
        # both backends at once: research on TCodex, the rest on Cursor; an agent stays on the backend it started on
        self.cfg["backend"] = "cursor"
        self.cfg["models"] = {"research": {"id": "gpt-6-sol", "backend": "tcodex"}}
        with mock.patch.object(self.api, "_PATH", f"{bin_dir}:/usr/bin:/bin"), mock.patch.object(self.api, "pump"):
            self.assertEqual(self.api.status()["backends"], {"cursor": True, "tcodex": True})
            agent = lambda run: store.get_run(run)["agent_id"]  # noqa: E731
            self.assertTrue(agent(self.api.start_async("draft", "r1", "起草")).startswith("codex-"))
            self.assertTrue(agent(self.api.start_async("card", "p1", "写卡")).startswith("bc-"))
            self.assertEqual(agent(self.api.start_async("chat", t["id"], "接着说", agent_id=aid)), aid)
        with mock.patch.object(self.api, "_PATH", "/nowhere"), self.assertRaises(self.api.AgentError):
            self.api.start_async("draft", "r2", "起草")  # TCodex not installed: refused, not sent to Cursor

    def test_stuck_runs_expire(self):
        from unittest import mock
        store.add_run("run-old", "bc-o", "deepread", "p", "", "RUNNING")
        store.update_run("run-old", started_at="2000-01-01T00:00:00+00:00")
        store.add_run("run-new", "bc-n", "deepread", "q", "", "RUNNING")
        store.update_run("run-new", started_at=store.now())
        with mock.patch.object(self.api, "cancel") as cancel:
            self.api.expire_stale()
        self.assertEqual((self.status("run-old"), self.status("run-new")), ("EXPIRED", "RUNNING"))
        cancel.assert_called_once_with("bc-o", "run-old")

    def test_restart_recovers_runs_that_were_being_started(self):
        from unittest import mock
        store.add_run("pending-landed", "bc-landed", "card", "p", "写卡", "CREATING", launch={"new": True})
        store.add_run("pending-lost", "bc-lost", "card", "q", "写卡", "CREATING", launch={"new": True})
        store.add_run("pending-old", "", "card", "r", "写卡", "CREATING")

        def fake(method, path, body=None, timeout=None):
            if path == "/agents/bc-landed":
                return {"latestRunId": "run-landed"}
            raise self.api.AgentError("Cursor API 404")

        with mock.patch.object(self.api, "_json", side_effect=fake):
            self.api.resume_watchers()
        self.assertEqual((self.status("run-landed"), store.get_run("run-landed")["agent_id"]), ("RUNNING", "bc-landed"))
        self.assertEqual(self.status("pending-lost"), "CREATING")  # requeued and sent again with the same agent id
        self.assertEqual(self.status("pending-old"), "ERROR")
        self.assertEqual(sorted(name for name, _ in self.spawned), ["_launch", "_watch"])


class Discussions(AgentHarness):
    def test_history_paper_record_phone_log_and_continuing_the_capture_agent(self):
        from paper2brain import cli
        pid, cid = "arxiv:2410.24164", "sc-phone"
        store.add_capture(cid, "https://arxiv.org/abs/2410.24164", "shortcut")
        store.upsert_paper(pid, "π0")
        store.link(cid, pid, "rule")
        store.update_capture(cid, agent_id="bc-phone")
        old = store.thread_for("library")
        store.add_message(old["id"], "user", "上周问的：π0 和 FAST 的区别")
        store.new_thread("library")
        store.add_message(store.thread_for("library")["id"], "user", "新的问题")
        self.assertEqual([t["first"] for t in store.recent_threads()], ["新的问题", "上周问的：π0 和 FAST 的区别"])

        out = cli.run(cli.parser().parse_args(["discuss", "log", "--paper", pid, "--question", "真机做了几个任务？", "--answer", "7 个 [arxiv:2410.24164 p.9]"]))
        self.assertTrue(out["logged"])
        aid = store.add_anchor(pid, "card", "flow matching", "user")
        store.add_message(store.thread_for(f"anchor:{aid}")["id"], "user", "这段为什么这样设计？")
        d = store.paper_discussions(pid)
        self.assertEqual([[m["text"] for m in t["messages"]] for t in d],
                         [["真机做了几个任务？", "7 个 [arxiv:2410.24164 p.9]"], ["这段为什么这样设计？"]])
        self.assertEqual(d[1]["passage"], "flow matching")
        self.assertIn("discussions", cli.run(cli.parser().parse_args(["show", "paper", pid])))
        self.assertEqual(store.recent_threads()[0]["scope_title"], "π0 ·「flow matching」")

        first = self.api.discuss(f"paper:{pid}", "和 OpenVLA 比呢？")
        run = store.get_run(first)
        self.assertEqual((run["agent_id"], run["launch"]["new"]), ("bc-phone", False))
        self.assertIn("librarian/discuss.md", run["prompt"])
        store.update_run(first, status="FINISHED")
        second = self.api.discuss(f"paper:{pid}", "再展开一点")
        self.assertEqual(store.get_run(second)["prompt"], self.api.WEB_MARK + "再展开一点")


class Topics(unittest.TestCase):
    def test_user_edits_the_tree_and_everything_follows(self):
        with scratch_home():
            pid, cid = "arxiv:2303.04137", store.new_id()
            store.add_capture(cid, "dp", "web")
            store.upsert_paper(pid, "Diffusion Policy")
            store.link(cid, pid, "rule")
            path = store.add_topic("模仿学习", "VLA", "user")
            store.set_topics(pid, [path], "agent")
            rid = store.add_research("r", "user", topics=[path])
            store.add_message(store.thread_for(f"topic:{path}")["id"], "user", "这个方向在解决什么？")
            renamed = store.edit_topic(path, "模仿学习与 BC", "VLA")
            self.assertEqual(store.get_paper(pid)["topics"], ["VLA/模仿学习与 BC"])
            moved = store.edit_topic(renamed, "模仿学习与 BC", "")
            self.assertEqual((moved, store.get_paper(pid)["topics"], store.get_research(rid)["topics"]),
                             ("模仿学习与 BC", ["模仿学习与 BC"], ["模仿学习与 BC"]))
            self.assertEqual(store.thread_for(f"topic:{moved}")["messages"][0]["text"], "这个方向在解决什么？")
            store.edit_topic("WAM", "World Models", "")
            tree = {t["path"]: [ch["path"] for ch in t["children"]] for t in store.topic_tree()}
            self.assertIn("World Models/V-JEPA", tree["World Models"])
            self.assertEqual([p["id"] for p in store.list_papers("模仿学习与 BC")], [pid])
            with self.assertRaisesRegex(rules.Rejected, "子主题"):
                store.edit_topic("VLA", "VLA", "VLM")
            with self.assertRaisesRegex(rules.Rejected, "已经有了"):
                store.add_topic("tokenizer", "VLA", "user")

    def test_deleting_a_topic_moves_its_things_up(self):
        with scratch_home():
            pid, cid = "arxiv:2303.04137", store.new_id()
            store.add_capture(cid, "dp", "web")
            store.upsert_paper(pid, "Diffusion Policy")
            store.link(cid, pid, "rule")
            store.set_topics(pid, ["VLA/tokenizer"], "agent")
            rid = store.add_research("r", "user", topics=["VLA/tokenizer"])
            store.add_message(store.thread_for("topic:VLA/tokenizer")["id"], "user", "tokenizer 怎么选？")
            with self.assertRaisesRegex(rules.Rejected, "子主题"):
                store.delete_topic("VLA")
            store.delete_topic("VLA/tokenizer")
            self.assertEqual((store.get_paper(pid)["topics"], store.get_research(rid)["topics"]), (["VLA"], ["VLA"]))
            self.assertEqual(store.thread_for("topic:VLA")["messages"][0]["text"], "tokenizer 怎么选？")
            self.assertNotIn("tokenizer", [ch["name"] for t in store.topic_tree() for ch in t["children"]])
            store.set_topics(pid, ["WAM/V-JEPA"], "user")
            store.delete_topic("WAM/V-JEPA")
            self.assertEqual(store.get_paper(pid)["topics"], ["WAM"])

    def test_agent_proposes_user_decides(self):
        from paper2brain import cli
        with scratch_home():
            store.upsert_paper("arxiv:2303.04137", "Diffusion Policy")
            store.upsert_paper("arxiv:2304.13705", "ACT")
            store.upsert_paper("arxiv:2410.24164", "π0")
            store.set_topics("arxiv:2304.13705", ["VLA"], "user")  # the user fixed this one
            with self.assertRaisesRegex(rules.Rejected, "--why"):
                store.add_topic("灵巧手", None, "agent")
            out = cli.run(cli.parser().parse_args(["topic", "propose", "--name", "模仿学习", "--parent", "VLA", "--why", "BC 类工作没位置",
                                                   "--paper", "arxiv:2303.04137", "--paper", "2304.13705"]))
            path = out["proposed"]
            self.assertEqual([p["path"] for p in store.topic_proposals()], [path])
            with self.assertRaisesRegex(rules.Rejected, "未知主题"):
                store.set_topics("arxiv:2303.04137", [path], "agent")
            store.set_topics("arxiv:2410.24164", ["VLA"], "agent")  # arrives while the proposal waits: joins it
            self.assertEqual(store.add_topic("模仿学习", "VLA", "agent", "同上", ["arxiv:2410.24164"]), path)
            self.assertEqual(len(store.topic_proposals()), 1)
            store.decide_topic(path, True)
            self.assertEqual(store.get_paper("arxiv:2303.04137")["topics"], [path])
            self.assertIn(path, store.get_paper("arxiv:2410.24164")["topics"])
            with self.assertRaisesRegex(rules.Rejected, "已经有了"):
                store.add_topic("模仿学习", "VLA", "agent", "同上")
            self.assertEqual(store.get_paper("arxiv:2304.13705")["topics"], ["VLA"])
            store.add_topic("灵巧手", None, "agent", "没位置")
            store.decide_topic("灵巧手", False)
            self.assertEqual(store.topic_proposals(), [])
            self.assertNotIn("灵巧手", [t["path"] for t in store.topic_tree()])


class Receiving(unittest.TestCase):
    def test_bare_thought_and_repeated_text_do_not_become_captures(self):
        from paper2brain import pipeline
        with scratch_home():
            got = pipeline.receive("sc-1", "  ", "web", "组会想讲 action chunking 的演化")
            self.assertEqual(got["kind"], "thought")
            self.assertEqual(pipeline.receive("sc-1", "", "web", "组会想讲 action chunking 的演化")["kind"], "thought")
            self.assertEqual([(t["id"], t["scope"], t["first"]) for t in store.recent_threads()],
                             [("sc-1", "library", "组会想讲 action chunking 的演化")])
            self.assertEqual(store.counts()["inbox"], 0)
            pending = pipeline.receive("sc-9", "只有中文描述的一段解读", "web")["capture"]["id"]
            store.update_capture(pending, status="unmapped")
            store.set_triage(pending, "archived")
            self.assertEqual([c["id"] for c in store.list_captures("inbox")], [pending])  # needs the user: stays in sight
            store.set_triage(pending, "ignored")
            self.assertEqual(store.list_captures("inbox"), [])
            first = pipeline.receive("sc-2", "https://arxiv.org/abs/2410.24164", "shortcut")["capture"]
            store.set_triage(first["id"], "archived")
            again = pipeline.receive("sc-3", "https://arxiv.org/abs/2410.24164 ", "shortcut", "真机任务有几个？")
            self.assertEqual((again["kind"], again["capture"]["id"], again["capture"]["triage"]), ("duplicate", "sc-2", "inbox"))
            self.assertEqual([t["first"] for t in again["capture"]["threads"]], ["真机任务有几个？"])
            self.assertEqual(pipeline.receive("sc-2", "别的", "web")["created"], False)

    def test_icloud_inbox_waits_for_the_api_agent_unless_told_otherwise(self):
        import json as _json
        from unittest import mock

        from paper2brain import cursor_api, inbox, pipeline
        with scratch_home() as home, mock.patch.object(inbox, "INBOX", home / "inbox"), \
                mock.patch.object(pipeline, "process") as process, mock.patch.object(cursor_api, "watch"):
            box = home / "inbox"
            box.mkdir()
            write = lambda name, obj: (box / name).write_text(_json.dumps(obj, ensure_ascii=False))
            write("th-1.json", {"id": "th-1", "text": "", "thought": "只是一个念头"})
            write("sc-1.json", {"id": "sc-1", "text": "https://mp.weixin.qq.com/s/abc", "thought": ""})
            inbox.drain()
            self.assertEqual([t["id"] for t in store.recent_threads()], ["th-1"])
            self.assertIsNone(store.get_capture("sc-1"))  # the API's agent may still be taking it
            write("sc-1.agent.json", {"agent": {"id": "bc-phone"}, "run": {"id": "run-phone", "status": "CREATING"}})
            inbox.drain()
            cap = store.get_capture("sc-1")
            self.assertEqual((cap["agent_id"], cap["runs"][0]["id"]), ("bc-phone", "run-phone"))
            process.assert_called_once_with("sc-1", agent=False)
            write("sc-2.json", {"id": "sc-2", "text": "https://mp.weixin.qq.com/s/abc", "thought": "再补一句", "local": True})
            inbox.drain()
            self.assertEqual([t["first"] for t in store.get_capture("sc-1")["threads"]], ["再补一句"])
            self.assertEqual(sorted(p.name for p in box.iterdir()), ["done"])


class Search(unittest.TestCase):
    def test_words_in_any_order_across_authors_titles_discussions_and_full_text(self):
        with scratch_home():
            cid = store.new_id()
            store.add_capture(cid, "https://arxiv.org/abs/2410.24164", "web")
            store.upsert_paper("arxiv:2410.24164", "π0: A Vision-Language-Action Flow Model", ["Kevin Black", "Chelsea Finn"])
            store.link(cid, "arxiv:2410.24164", "rule")
            store.set_fulltext("arxiv:2410.24164", "/tmp/x.pdf", PAGES)
            store.upsert_paper("arxiv:2501.00002", "库外论文", ["Chelsea Finn"])  # not listed: no capture, no research
            tid = store.new_thread("library", text="和 DP 比一下")
            store.set_thread_title(tid, "flow matching 动作头和 diffusion policy 的区别", "agent")
            th = store.thread_for("library")
            store.add_message(th["id"], "user", "库里谁做过灵巧手？")

            def ids(q, group):
                return [x["id"] for x in store.search(q)[group]]

            self.assertEqual(ids("flow 50 Hz", "papers"), ["arxiv:2410.24164"])
            self.assertEqual(ids("Finn chelsea", "papers"), ["arxiv:2410.24164"])
            hit = store.search("laundry")["papers"][0]
            self.assertEqual((hit["hits"], hit["hit_page"]), (["原文"], 2))
            self.assertIn("laundry folding", hit["hit_text"])
            self.assertEqual(ids("diffusion 动作头", "threads"), [tid])
            self.assertEqual(store.search("灵巧手")["messages"][0]["scope"], "library")
            self.assertFalse(store.search("flow 50 Hz")["loose"])
            loose = store.search("flow 不存在的词")  # nothing has every word: any word, flagged
            self.assertEqual(([p["id"] for p in loose["papers"]], loose["loose"]), (["arxiv:2410.24164"], True))
            self.assertEqual(ids("100%", "papers"), [])

    def test_title_hits_rank_first_and_unspaced_chinese_falls_back_to_pairs(self):
        with scratch_home():
            cid = store.new_id()
            store.add_capture(cid, "x", "web")
            store.upsert_paper("arxiv:2501.00011", "某个方法", abstract="用灵巧手抓取")
            store.upsert_paper("arxiv:2501.00012", "灵巧手抓取")
            store.upsert_paper("arxiv:2501.00013", "另一个方法", ["灵巧手"])
            for pid in ("arxiv:2501.00011", "arxiv:2501.00012", "arxiv:2501.00013"):
                store.link(cid, pid, "rule")
            self.assertEqual([p["id"] for p in store.search("灵巧手")["papers"]], ["arxiv:2501.00012", "arxiv:2501.00011", "arxiv:2501.00013"])
            r = store.search("灵巧手的抓取")  # typed without spaces, no literal match
            self.assertEqual((r["loose"], r["papers"][0]["id"]), (True, "arxiv:2501.00012"))


class ShortNames(unittest.TestCase):
    def test_title_rule(self):
        for title, name in [("Long-WAM: Scaling the Context", "Long-WAM"), ("π0.5: a VLA Model", "π0.5"), ("GR00T N1: An Open Model", "GR00T N1"),
                            ("World Action Models are Zero-shot Policies", None), ("Back to the Features: DINO", None), ("Learning to Act: x", None)]:
            self.assertEqual(rules.title_short_name(title), name, title)

    def test_named_from_abstract_by_the_api_user_name_wins_and_search_finds_it(self):
        from unittest import mock
        from paper2brain import cursor_api
        with scratch_home(), mock.patch.object(cursor_api, "_spawn", side_effect=lambda fn, *a: fn(*a)):
            cid = store.new_id()
            store.add_capture(cid, "x", "web")
            store.upsert_paper("arxiv:2609.34981", "What Makes World Action Models Generalize?", abstract="We propose Simple-WAM ...")
            store.upsert_paper("arxiv:2610.10528", "Long-WAM: Scaling the Context of World-Action Models")
            for pid in ("arxiv:2609.34981", "arxiv:2610.10528"):
                store.link(cid, pid, "rule")
            self.assertEqual([p["id"] for p in store.unnamed_papers()], ["arxiv:2609.34981"])  # the title already named Long-WAM
            cursor_api.set_search_api("http://x/v1", "m", "k")
            with mock.patch.object(cursor_api, "_chat", return_value="Simple-WAM\n"):
                cursor_api.name_pending()
            self.assertEqual(store.get_paper("arxiv:2609.34981")["short_name"], "Simple-WAM")
            self.assertEqual([p["id"] for p in store.search("simple-wam")["papers"]], ["arxiv:2609.34981"])
            store.set_short_name("arxiv:2610.10528", "LongWAM", "user")
            store.set_short_name("arxiv:2610.10528", "别的", "agent")
            self.assertEqual(store.get_paper("arxiv:2610.10528")["short_name"], "LongWAM")


class SearchApi(unittest.TestCase):
    def test_key_kept_when_left_empty_and_empty_url_turns_off(self):
        from paper2brain import cursor_api
        with scratch_home():
            with self.assertRaises(rules.Rejected):
                cursor_api.set_search_api("v2.open.venus.oa.com", "m", "k")
            cursor_api.set_search_api("http://x/llmproxy/", "deepseek-v4.1-flash", "secret")
            cursor_api.set_search_api("http://x/llmproxy", "other-model", "")
            self.assertEqual(cursor_api.search_api(), {"base_url": "http://x/llmproxy", "model": "other-model", "api_key": "secret"})
            cursor_api.set_search_api("", "", "")
            self.assertIsNone(cursor_api.search_api())

    @staticmethod
    def _resp(lines=(), body=None):
        import json

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def __iter__(self): return iter(lines)
            def read(self): return json.dumps(body).encode()
        return Resp()

    def _serve(self, gemini_text, chat_text, chunks=()):
        def urlopen(req, timeout=None):
            if "generateContent" in req.full_url:
                return self._resp(body={"candidates": [{"content": {"parts": [{"text": gemini_text}]}, "groundingMetadata": {"groundingChunks": list(chunks)}}]})
            return self._resp(body={"choices": [{"message": {"content": chat_text}}]})
        return urlopen

    def test_quick_question_answers_from_the_page_and_saves_nothing(self):
        import json
        from unittest import mock
        from paper2brain import cursor_api
        with scratch_home():
            with self.assertRaises(rules.Rejected):  # no API configured
                cursor_api.quick_answer("q", "?", pid="arxiv:2410.24164")
            cursor_api.set_search_api("http://x/v1", "m", "k")
            store.upsert_paper("arxiv:2410.24164", "π0")
            store.set_fulltext("arxiv:2410.24164", "/tmp/x.pdf", PAGES)
            rid = store.add_research("一步动作头", "user")
            reply = self._resp(body={"choices": [{"message": {"content": "它说 50 Hz"}}]})
            with mock.patch("urllib.request.urlopen", return_value=reply) as call:
                self.assertEqual(cursor_api.quick_answer("up to 50 Hz", "", pid="arxiv:2410.24164", page=2, surface="pdf"), {"text": "它说 50 Hz"})
                self.assertIn("laundry folding", json.loads(call.call_args[0][0].data)["messages"][0]["content"])  # the page around it
                cursor_api.quick_answer("主结果", "这说明什么", scope=f"experiment:{rid}")
                self.assertIn("一步动作头", json.loads(call.call_args[0][0].data)["messages"][0]["content"])
            self.assertEqual((store.unfinished_runs(), store.anchors_for("arxiv:2410.24164")), ([], []))

    def test_web_find_keeps_only_papers_arxiv_confirms(self):
        import json
        from unittest import mock
        from paper2brain import cursor_api, sources
        said = json.dumps({"papers": [{"arxiv": "2610.12026", "why": "人形 WAM"}, {"arxiv": "2610.99999", "why": "编的"}]})
        gemini = self._resp(body={"candidates": [{"content": {"parts": [{"text": said}]},
                                                  "groundingMetadata": {"groundingChunks": [{"web": {"title": "arxiv.org", "uri": "https://x"}}]}}]})
        meta = [{"arxiv_id": "2610.12026", "title": "HWAM", "authors": ["A", "B", "C", "D"], "published": "2026-10-08", "url": "https://arxiv.org/abs/2610.12026"}]
        with scratch_home():
            cursor_api.set_search_api("https://api.zhizengzeng.com/v1", "m", "k")
            store.upsert_paper("arxiv:2410.24164", "π0")
            with mock.patch("urllib.request.urlopen", return_value=gemini) as call, \
                    mock.patch.object(sources, "arxiv_lookup", return_value=meta) as lookup:
                got = cursor_api.web_find("人形机器人世界动作模型")
                self.assertTrue(call.call_args[0][0].full_url.startswith("https://api.zhizengzeng.com/google/v1beta/models/gemini-3.5-flash-lite:generateContent?key=k"))
                self.assertEqual(lookup.call_args[0][0], ["2610.12026", "2610.99999"])
                self.assertEqual(cursor_api.quick_answer("WAM", "后来有人改进吗", pid="arxiv:2410.24164", web=True)["sources"],
                                 [{"title": "arxiv.org", "url": "https://x"}])
        self.assertEqual(got["papers"], [{"pid": "arxiv:2610.12026", "title": "HWAM", "authors": ["A", "B", "C"], "published": "2026-10-08",
                                          "url": "https://arxiv.org/abs/2610.12026", "why": "人形 WAM", "in_library": False}])

    def test_google_itself_as_the_search_api(self):
        import json
        from unittest import mock
        from paper2brain import cursor_api
        with scratch_home():
            cursor_api.set_search_api("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash-lite", "AQ.k")
            api = cursor_api.search_api()
            with mock.patch("urllib.request.urlopen", return_value=self._resp(body={"choices": [{"message": {"content": "好"}}]})) as call:
                cursor_api._chat(api, "hi")
                self.assertNotIn("thinking", json.loads(call.call_args[0][0].data))
            with mock.patch("urllib.request.urlopen", return_value=self._resp(body={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})) as call:
                cursor_api._web(api, "hi")
                self.assertEqual(call.call_args[0][0].full_url,
                                 "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key=AQ.k")

    def test_arxiv_lookup_falls_back_to_abs_pages_when_rate_limited(self):
        import urllib.error
        from unittest import mock
        from paper2brain import sources
        page = ('<meta name="citation_title" content="RT-Trajectory: Hindsight Sketches" /><meta name="citation_author" content="Gu, Jiayuan" />'
                '<meta name="citation_date" content="2023/11/03" />').encode()

        def get(url, timeout=25):
            if "export.arxiv.org" in url:
                raise urllib.error.HTTPError(url, 429, "", {}, None)
            if url.endswith("2311.01977"):
                return url, page, "text/html"
            raise urllib.error.HTTPError(url, 404, "", {}, None)
        with mock.patch.object(sources, "http_get", side_effect=get), mock.patch.object(sources, "_last_call", [0.0]):
            got = sources.arxiv_lookup(["2311.01977", "2311.99999"])
        self.assertEqual([(f["title"], f["authors"], f["published"]) for f in got], [("RT-Trajectory: Hindsight Sketches", ["Jiayuan Gu"], "2023-11-03")])

    def test_search_overview_checks_web_results(self):
        import json
        from unittest import mock
        from paper2brain import cursor_api, sources
        web = [{"title": "Latent Action Pretraining", "url": "https://arxiv.org/abs/2410.11758", "why": "对"},
               {"title": "完全不同的标题", "url": "https://arxiv.org/abs/2312.13139", "why": "标题对不上"},
               {"title": "编的", "url": "https://arxiv.org/abs/2604.xxxxx", "why": "编号是假的"},
               {"title": "库里已有", "url": "https://arxiv.org/abs/2410.24164", "why": "重复"},
               {"title": "Robot Flow", "url": "https://openreview.net/forum?id=abc", "why": "搜索去过这个站"},
               {"title": "博客", "url": "https://blog.example.com/x", "why": "搜索没去过"}]
        said = json.dumps({"answer": "看视频学动作", "web": web}, ensure_ascii=False)
        gemini = self._serve(said, json.dumps({"ids": ["2410.24164"]}), [{"web": {"title": "openreview.net", "uri": "https://r"}}])
        meta = [{"arxiv_id": "2410.11758", "title": "Latent Action Pretraining from Videos", "authors": [], "published": "2024-10-15", "url": "https://arxiv.org/abs/2410.11758"},
                {"arxiv_id": "2312.13139", "title": "Unleashing Large-Scale Video Generative Pre-training", "authors": [], "published": "2023-12-20", "url": "https://arxiv.org/abs/2312.13139"}]
        with scratch_home():
            cursor_api.set_search_api("https://api.zhizengzeng.com/v1", "m", "k")
            cid = store.new_id()
            store.add_capture(cid, "x", "web")
            store.upsert_paper("arxiv:2410.24164", "π0")
            store.link(cid, "arxiv:2410.24164", "rule")
            with mock.patch("urllib.request.urlopen", side_effect=gemini), mock.patch.object(sources, "arxiv_lookup", return_value=meta):
                r = cursor_api.ai_search("让机器人看视频学操作")
        self.assertEqual((r["answer"], [p["id"] for p in r["papers"]]), ("看视频学动作", ["arxiv:2410.24164"]))
        self.assertEqual([(w["title"], w["url"]) for w in r["web"]], [("Latent Action Pretraining from Videos", "https://arxiv.org/abs/2410.11758"),
                                                                     ("Robot Flow", "https://openreview.net/forum?id=abc")])

    def test_papers_called_by_name_are_looked_up_on_arxiv_first(self):
        import json
        from unittest import mock
        from paper2brain import cursor_api, sources
        long_wam = {"arxiv_id": "2610.10528", "pid": "arxiv:2610.10528", "title": "Long-WAM: Scaling the Context of World-Action Models",
                    "published": "2026-10-07", "url": "https://arxiv.org/abs/2610.10528", "authors": []}
        said = json.dumps({"answer": "两篇都是 WAM", "web": [{"title": "Long-WAM", "url": "https://arxiv.org/abs/2610.10528"}]})
        gemini = self._serve(said, json.dumps({"ids": []}))
        with scratch_home():
            cursor_api.set_search_api("https://api.zhizengzeng.com/v1", "m", "k")
            with mock.patch("urllib.request.urlopen", side_effect=gemini), \
                    mock.patch.object(sources, "keyword_search", side_effect=lambda t: [long_wam] if t == ["Long-WAM"] else []) as ks, \
                    mock.patch.object(sources, "arxiv_lookup", return_value=[]):
                r = cursor_api.ai_search("simple-WAM和Long-WAM")
        self.assertEqual(ks.call_args_list, [mock.call(["simple-WAM"]), mock.call(["Long-WAM"])])
        self.assertEqual(([w["pid"] for w in r["web"]], r["named"]), (["arxiv:2610.10528"], True))

    def test_discussions_are_titled_through_the_api(self):
        from unittest import mock
        from paper2brain import cursor_api
        with scratch_home(), mock.patch.object(cursor_api, "_spawn", side_effect=lambda fn, *a: fn(*a)):
            cursor_api.set_search_api("http://x/v1", "m", "k")
            tid = store.new_thread("library", text="和 DP 比一下")
            with mock.patch("urllib.request.urlopen", return_value=self._resp(body={"choices": [{"message": {"content": "「flow matching 和 DP 的区别」\n"}}]})):
                cursor_api.distill_pending()
            self.assertEqual(store.thread_for(thread_id=tid)["title"], "flow matching 和 DP 的区别")


class Migrations(unittest.TestCase):
    def test_legacy_database_is_upgraded_in_place_after_a_backup(self):
        import sqlite3
        with scratch_home() as home:
            legacy = sqlite3.connect(home / "p2b.sqlite")
            legacy.execute("CREATE TABLE thoughts (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL, "
                           "by TEXT NOT NULL, capture_id TEXT REFERENCES captures(id) ON DELETE CASCADE, paper_id TEXT)")
            legacy.executescript(store.SCHEMA + "CREATE TABLE ideas (id TEXT);")
            legacy.execute("INSERT INTO captures (id, created_at, raw, source) VALUES ('c1', '2026-10-01', 'https://x', 'web')")
            legacy.execute("INSERT INTO thoughts VALUES ('t1', '2026-10-01', '旧想法', 'user', 'c1', NULL)")
            legacy.execute("INSERT INTO captures (id, created_at, raw, source, status) VALUES ('bare', '2026-10-02', '', 'web', 'unmapped')")
            legacy.execute("INSERT INTO thoughts VALUES ('t2', '2026-10-02', '只有一句想法', 'user', 'bare', NULL)")
            legacy.execute("INSERT INTO threads VALUES ('th1', '2026-10-03', 'thought:t1', NULL, 'bc-1')")
            legacy.execute("INSERT INTO messages VALUES ('m1', '2026-10-03', 'th1', 'user', '接着想', NULL)")
            legacy.execute("INSERT INTO threads VALUES ('th2', '2026-10-04', 'thoughts', NULL, NULL)")
            legacy.execute("INSERT INTO research (id, created_at, updated_at, title, by) VALUES ('r1', '2026-10-05', '2026-10-05', 'r', 'user')")
            legacy.execute("INSERT INTO research_links VALUES ('r1', 'thought', 't1', 'user', '2026-10-05')")
            legacy.execute("INSERT INTO research_links VALUES ('r1', 'thread', 'th1', 'user', '2026-10-05')")
            legacy.execute("INSERT INTO topics VALUES ('VLA', 'VLA', NULL, 0)")
            legacy.commit()
            legacy.close()

            t1 = store.get_capture("c1")["threads"]
            self.assertEqual([(t["id"], t["first"]) for t in t1], [("t1", "旧想法")])
            t1 = store.thread_for(thread_id="t1")
            self.assertEqual(([m["text"] for m in t1["messages"]], t1["agent_id"]), (["旧想法", "接着想"], "bc-1"))
            self.assertEqual([(l["kind"], l["ref"]) for l in store.get_research("r1")["links"]], [("thread", "t1")])
            self.assertIsNone(store.get_capture("bare"))
            self.assertEqual(store.thread_for(thread_id="t2")["scope"], "library")
            self.assertEqual(store.thread_for(thread_id="th2")["scope"], "library")
            c = sqlite3.connect(home / "p2b.sqlite")
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], len(store.MIGRATIONS))
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
            self.assertTrue({"messages_thread", "events_target", "threads_parent"} <= names and not {"ideas", "thoughts", "th1"} & names)
            self.assertEqual([r[0] for r in c.execute("SELECT path FROM topics")], ["VLA"])  # user's tree untouched
            before = list((home / "backups").glob("p2b-before-*.sqlite"))
            self.assertEqual(len(before), 1)
            self.assertEqual(sqlite3.connect(before[0]).execute("SELECT text FROM thoughts").fetchone()[0], "旧想法")

    def test_hourly_backup_once_an_hour_keeps_a_week(self):
        with scratch_home() as home:
            store.counts()
            folder = home / "backups"
            for d in range(1, store.BACKUPS_KEPT + 3):
                store.backup(f"p2b-2026{d:04d}-00.sqlite")
            self.assertIsNotNone(store.backup())
            self.assertIsNone(store.backup())
            self.assertEqual(len(list(folder.glob("p2b-[0-9]*.sqlite"))), store.BACKUPS_KEPT)

    def test_rollback_a_library_or_one_research_to_a_snapshot(self):
        with scratch_home():
            keep, gone = store.add_research("留着", "user"), store.add_research("会被删", "user")
            store.backup("t1.sqlite")
            store.update_research(keep, title="改过了")
            store.delete_research(gone)
            store.restore("t1.sqlite", gone)  # one research comes back, the other keeps its newer edit
            self.assertEqual((store.get_research(gone)["title"], store.get_research(keep)["title"]), ("会被删", "改过了"))
            self.assertEqual([r["title"] for r in store.snapshot_research("t1.sqlite")].count("会被删"), 1)
            store.restore("t1.sqlite")  # the whole library
            self.assertEqual(store.get_research(keep)["title"], "留着")
            self.assertTrue(any(n.startswith("p2b-before-restore-") for n, _ in store.backups()))
            with self.assertRaises(rules.Rejected):
                store.restore("../x.sqlite")


if __name__ == "__main__":
    unittest.main()
