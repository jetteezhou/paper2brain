"""Write rules and storage, on a temporary data directory. No network."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ["P2B_HOME"] = tempfile.mkdtemp(prefix="p2b-test-")

from paper2brain import rules, store  # noqa: E402

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

    def test_delete_capture_keeps_paper(self):
        store.link(self.cid, "arxiv:2410.24164", "agent")
        store.delete_capture(self.cid)
        self.assertIsNone(store.get_capture(self.cid))
        self.assertIsNotNone(store.get_paper("arxiv:2410.24164"))
        self.assertFalse(any(t["capture_id"] == self.cid for t in store.list_thoughts()))

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

    def test_topic_tree_is_seeded(self):
        tree = {t["path"]: [c["name"] for c in t["children"]] for t in store.topic_tree()}
        self.assertEqual(tree["VLA"], ["tokenizer", "推理加速", "实时推理"])
        self.assertIn("V-JEPA", tree["WAM"])


if __name__ == "__main__":
    unittest.main()
