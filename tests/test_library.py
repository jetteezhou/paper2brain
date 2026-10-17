"""Write rules and storage, on a temporary data directory. No network."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ["P2B_HOME"] = tempfile.mkdtemp(prefix="p2b-test-")

from paper2brain import rules, store  # noqa: E402

AGENT_CFG = {"machine": "m", "repo": "r", "model": None, "api_key": "k", "max_agents": 2, "run_timeout_min": 60}
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
            rules.validate_report({"sections": [{"heading": "h", "body": "b", "citations": [{"quote": "flow matching action expert", "page": 1}]}]}, PAGES)


class Store(unittest.TestCase):
    def setUp(self):
        self.cid = store.new_id()
        store.add_capture(self.cid, "https://example.com/a", "test", thought="为什么收")
        store.upsert_paper("arxiv:2410.24164", "pi0")

    def test_capture_is_idempotent(self):
        cap, created = store.add_capture(self.cid, "different text", "test", thought="again")
        self.assertFalse(created)
        self.assertEqual(cap["raw"], "https://example.com/a")
        self.assertEqual(len(cap["thoughts"]), 1)

    def test_same_text_is_found_for_dedupe(self):
        self.assertIsNotNone(store.same_capture(" https://example.com/a "))
        self.assertIsNone(store.same_capture("https://example.com/other"))

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
        on_paper = store.add_thought("关于这篇的想法", "user", paper_id=only)
        store.link_research(rid, "thought", store.get_capture(self.cid)["thoughts"][0]["id"], "user")
        (store.paper_dir(only) / "pages.json").write_text("[]")

        plan = store.capture_removal(self.cid)
        self.assertEqual([p["id"] for p in plan["papers"]], [only])
        self.assertEqual((len(plan["papers"][0]["anchor"]), len(plan["papers"][0]["thread"]), plan["papers"][0]["card"]), (1, 2, True))
        self.assertEqual({p["id"]: len(p["why"]) for p in plan["kept"]}, {shared: 1, used: 1})
        self.assertEqual((len(plan["thoughts"]), plan["used_in"]), (1, ["用到它"]))

        store.delete_capture(self.cid)
        self.assertIsNone(store.get_capture(self.cid))
        self.assertIsNone(store.get_paper(only))
        self.assertFalse(store.paper_dir(only, create=False).exists())
        self.assertIsNone(store.get_anchor(aid))
        self.assertFalse(any(t["id"] == on_paper or t["capture_id"] == self.cid for t in store.list_thoughts()))
        self.assertEqual([l["kind"] for l in store.get_research(rid)["links"]], ["paper"])
        listed = {p["id"] for p in store.list_papers()}
        self.assertTrue({shared, used} <= listed and only not in listed)

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
        tid = store.add_thought("能不能把动作头蒸馏到一步", "user", paper_id="arxiv:2501.00001")
        rid = store.add_research("一步动作头", "user", links=[("thought", tid), ("paper", "arxiv:2501.00001")])
        r = store.get_research(rid)
        self.assertEqual((r["stage"], r["topics"]), ("forming", ["VLA/推理加速"]))
        self.assertEqual({l["kind"] for l in r["links"]}, {"thought", "paper"})
        self.assertEqual(store.get_paper("arxiv:2501.00001")["research"][0]["id"], rid)
        self.assertTrue(any(t["research"] for t in store.list_thoughts() if t["id"] == tid))
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

    def test_thought_gist_keeps_raw_and_user_edit_wins(self):
        tid = store.add_thought("和 diffusion policy 对比一下", "user", paper_id="arxiv:2410.24164")
        self.assertIn(tid, store.pending_gists(100))
        store.set_gist(tid, "π0 的 flow matching 动作头和 Diffusion Policy 差在哪", "agent")
        self.assertNotIn(tid, store.pending_gists(100))
        store.set_gist(tid, "比较两种动作生成头", "user")
        with self.assertRaisesRegex(rules.Rejected, "手动改过"):
            store.set_gist(tid, "agent 又来改", "agent")
        t = store.get_thought(tid)
        self.assertEqual((t["text"], t["gist"], t["gist_by"], t["paper"]["id"]),
                         ("和 diffusion policy 对比一下", "比较两种动作生成头", "user", "arxiv:2410.24164"))

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
        tid = store.add_thought("用 latent action 预训练 tokenizer", "user")
        rid, images = pipeline.start_research(
            None, "# 我想做：跨本体的动作 tokenizer\n背景很长……", ["VLA/tokenizer"], [("thought", tid)],
            [("notes.md", "text/markdown", "白板笔记：codebook 512".encode()), ("board.png", "image/png", b"\x89PNG fake")])
        r = store.get_research(rid)
        self.assertEqual(r["title"], "我想做：跨本体的动作 tokenizer")
        kinds = sorted(l["kind"] for l in r["links"])
        self.assertEqual(kinds, ["doc", "doc", "thought"])
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
        paper = pipeline.paper_from_pdf(doc.tobytes())
        s = structure.extract(paper["id"])
        self.assertEqual([(f["n"], f["page"]) for f in s["figures"]], [(1, 1)])
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
        self.assertEqual(store.list_captures("inbox")[0]["papers"][0]["jumps"]["report"], ["方法"])

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
        body = '{"text": "跨站写入"}'.encode()
        self.assertEqual(self.req("POST", "/api/thoughts", body, **{"Content-Type": "text/plain"}), 415)
        self.assertEqual(self.req("POST", "/api/thoughts", body, **{"Content-Type": "application/json", "Origin": "http://evil.example"}), 403)
        self.assertEqual(self.req("POST", "/api/thoughts", body, **{"Content-Type": "application/json", "Origin": "null"}), 403)
        self.assertEqual(self.req("DELETE", "/api/anchors/x", Origin="http://evil.example"), 403)
        self.assertEqual(self.req("POST", "/api/thoughts", body, **{"Content-Type": "application/json", "Origin": own}), 200)
        self.assertEqual(sum(t["text"] == "跨站写入" for t in store.list_thoughts()), 1)


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


class Receiving(unittest.TestCase):
    def test_bare_thought_and_repeated_text_do_not_become_captures(self):
        from paper2brain import pipeline
        with scratch_home():
            got = pipeline.receive("sc-1", "  ", "web", "组会想讲 action chunking 的演化")
            self.assertEqual(got["kind"], "thought")
            self.assertEqual(pipeline.receive("sc-1", "", "web", "组会想讲 action chunking 的演化")["kind"], "thought")
            self.assertEqual([t["text"] for t in store.list_thoughts()], ["组会想讲 action chunking 的演化"])
            self.assertEqual(store.counts()["pending"] + store.counts()["inbox"], 0)
            first = pipeline.receive("sc-2", "https://arxiv.org/abs/2410.24164", "shortcut")["capture"]
            store.set_triage(first["id"], "archived")
            again = pipeline.receive("sc-3", "https://arxiv.org/abs/2410.24164 ", "shortcut", "真机任务有几个？")
            self.assertEqual((again["kind"], again["capture"]["id"], again["capture"]["triage"]), ("duplicate", "sc-2", "inbox"))
            self.assertEqual([t["text"] for t in again["capture"]["thoughts"]], ["真机任务有几个？"])
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
            self.assertEqual([t["id"] for t in store.list_thoughts()], ["th-1"])
            self.assertIsNone(store.get_capture("sc-1"))  # the API's agent may still be taking it
            write("sc-1.agent.json", {"agent": {"id": "bc-phone"}, "run": {"id": "run-phone", "status": "CREATING"}})
            inbox.drain()
            cap = store.get_capture("sc-1")
            self.assertEqual((cap["agent_id"], cap["runs"][0]["id"]), ("bc-phone", "run-phone"))
            process.assert_called_once_with("sc-1", agent=False)
            write("sc-2.json", {"id": "sc-2", "text": "https://mp.weixin.qq.com/s/abc", "thought": "再补一句", "local": True})
            inbox.drain()
            self.assertEqual([t["text"] for t in store.get_capture("sc-1")["thoughts"]], ["再补一句"])
            self.assertEqual(sorted(p.name for p in box.iterdir()), ["done"])


class Search(unittest.TestCase):
    def test_words_in_any_order_across_authors_gists_discussions_and_full_text(self):
        with scratch_home():
            cid = store.new_id()
            store.add_capture(cid, "https://arxiv.org/abs/2410.24164", "web")
            store.upsert_paper("arxiv:2410.24164", "π0: A Vision-Language-Action Flow Model", ["Kevin Black", "Chelsea Finn"])
            store.link(cid, "arxiv:2410.24164", "rule")
            store.set_fulltext("arxiv:2410.24164", "/tmp/x.pdf", PAGES)
            store.upsert_paper("arxiv:2501.00002", "库外论文", ["Chelsea Finn"])  # not listed: no capture, no research
            tid = store.add_thought("和 DP 比一下", "user")
            store.set_gist(tid, "flow matching 动作头和 diffusion policy 的区别", "agent")
            th = store.thread_for("library")
            store.add_message(th["id"], "user", "库里谁做过灵巧手？")

            def ids(q, group):
                return [x["id"] for x in store.search(q)[group]]

            self.assertEqual(ids("flow 50 Hz", "papers"), ["arxiv:2410.24164"])
            self.assertEqual(ids("Finn chelsea", "papers"), ["arxiv:2410.24164"])
            hit = store.search("laundry")["papers"][0]
            self.assertEqual((hit["hits"], hit["hit_page"]), (["原文"], 2))
            self.assertIn("laundry folding", hit["hit_text"])
            self.assertEqual(ids("diffusion 动作头", "thoughts"), [tid])
            self.assertEqual(store.search("灵巧手")["messages"][0]["scope"], "library")
            self.assertEqual(ids("flow 不存在的词", "papers"), [])
            self.assertEqual(ids("100%", "papers"), [])


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
            legacy.execute("INSERT INTO topics VALUES ('VLA', 'VLA', NULL, 0)")
            legacy.commit()
            legacy.close()

            self.assertEqual(store.get_capture("c1")["thoughts"][0]["text"], "旧想法")
            self.assertIsNone(store.get_capture("bare"))
            self.assertEqual(next(t for t in store.list_thoughts() if t["id"] == "t2")["capture_id"], None)
            c = sqlite3.connect(home / "p2b.sqlite")
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], len(store.MIGRATIONS))
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
            self.assertTrue({"messages_thread", "events_target"} <= names and "ideas" not in names)
            self.assertEqual([r[0] for r in c.execute("SELECT path FROM topics")], ["VLA"])  # user's tree untouched
            before = list((home / "backups").glob("p2b-before-*.sqlite"))
            self.assertEqual(len(before), 1)
            self.assertEqual(sqlite3.connect(before[0]).execute("SELECT text FROM thoughts").fetchone()[0], "旧想法")

    def test_daily_backup_once_a_day_keeps_seven(self):
        with scratch_home() as home:
            store.counts()
            folder = home / "backups"
            for d in range(1, 10):
                store.backup(f"p2b-202601{d:02d}.sqlite")
            self.assertIsNotNone(store.backup())
            self.assertIsNone(store.backup())
            self.assertEqual(len(list(folder.glob("p2b-[0-9]*.sqlite"))), store.BACKUPS_KEPT)


if __name__ == "__main__":
    unittest.main()
