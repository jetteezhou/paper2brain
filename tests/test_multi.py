"""The shared server (P2B_ROOT): accounts, one library per user and per space, roles, agents on members' Macs.
A real server on 127.0.0.1, no other network."""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("P2B_HOME", tempfile.mkdtemp(prefix="p2b-test-"))

import pymupdf  # noqa: E402

from paper2brain import accounts, cursor_api, overleaf, pipeline, repos, rules, server, sources, store  # noqa: E402


class Multi(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"P2B_ROOT": tempfile.mkdtemp(prefix="p2b-root-")})
        self.env.start()
        self.httpd = None

    def tearDown(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.env.stop()

    def user(self, name):
        user, token, lib = accounts.redeem(accounts.invite(), name, password="password1")
        return user, token

    def serve(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, path, body=None, cookie=None, bearer=None, method=None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers,
                                     method=method or ("GET" if body is None else "POST"))
        opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a: None}))
        try:
            with opener.open(req) as r:
                return r.status, json.loads(r.read() or b"null"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers

    def notes(self, lib):
        with store.at_home(store.library_home(lib)), store.tx() as c:
            return [r[0] for r in c.execute("SELECT text FROM notes")]

    def research_with_material(self, lib):
        with store.at_home(store.library_home(lib)):
            store.upsert_paper("arxiv:2410.24164", "pi0")
            tid = store.new_thread("library", text="想法")
            store.add_message(tid, "user", "这条也带走")
            private = store.new_thread("library", text="私人的")
            rid = store.add_research("共享的研究", "user", links=[("paper", "arxiv:2410.24164"), ("thread", tid)])
        return rid, tid, private

    def test_no_library_means_no_access(self):
        with self.assertRaises(RuntimeError):
            store.home()
        a, _ = self.user("甲")
        with store.at_home(store.library_home(a["library"])):
            seen, done = [], threading.Event()
            store.spawn(lambda: (seen.append(store.home()), done.set()))
            done.wait(10)
            self.assertEqual(seen, [store.library_home(a["library"])])

    def test_invites_are_single_use_and_tokens_hashed(self):
        code = accounts.invite()
        user, token, _ = accounts.redeem(code, "甲", password="password1")
        self.assertEqual(accounts.auth(token)[0]["id"], user["id"])
        with self.assertRaises(rules.Rejected):
            accounts.redeem(code, "乙", password="password1")
        self.assertNotIn(token.encode(), (accounts.root() / "hub.sqlite").read_bytes())

    def test_page_files_revalidate(self):
        self.url = self.serve()
        with urllib.request.urlopen(self.url + "/style.css") as r:
            stamp, cache = r.headers["Last-Modified"], r.headers["Cache-Control"]
        self.assertEqual(cache, "no-cache")
        with self.assertRaises(urllib.error.HTTPError) as got:
            urllib.request.urlopen(urllib.request.Request(self.url + "/style.css", headers={"If-Modified-Since": stamp}))
        self.assertEqual((got.exception.code, got.exception.read()), (304, b""))
        a, ta = self.user("甲")
        with store.at_home(store.library_home(a["library"])):
            store.upsert_paper("arxiv:2410.24164", "pi0")
        def get(path, tag=None):
            req = urllib.request.Request(self.url + path, headers={"Cookie": f"p2b_session={ta}", **({"If-None-Match": f'W/"{tag}"'} if tag else {})})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, (r.headers["ETag"] or "").strip('"')
            except urllib.error.HTTPError as e:
                return e.code, None
        code, tag = get("/api/papers/arxiv:2410.24164")
        self.assertEqual((code, get("/api/papers/arxiv:2410.24164", tag)[0]), (200, 304))
        with store.at_home(store.library_home(a["library"])):
            store.set_short_name("arxiv:2410.24164", "π0", "user")
        self.assertEqual(get("/api/papers/arxiv:2410.24164", tag)[0], 200)  # changed: sent again
        self.assertEqual(get("/api/threads?scope=paper:arxiv:2410.24164"), (200, ""))  # discussions are never kept

    def test_name_and_password_sign_in(self):
        self.url = self.serve()
        self.addCleanup(accounts._FAILS.clear)
        a, _ = self.user("Jia")
        for name, password in (("jia", "password2"), ("乙", "short")):  # names are unique ignoring case; 8+ characters
            with self.assertRaises(rules.Rejected):
                accounts.redeem(accounts.invite(), name, password=password)
        self.assertNotIn(b"password1", (accounts.root() / "hub.sqlite").read_bytes())
        code, _, headers = self.call("/api/login", {"name": "jia", "password": "password1"})
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/state", cookie=cookie)[1]["account"]["login"], "Jia")
        self.assertEqual(self.call("/api/me/password", {"name": "Jia", "password": "password3", "old": "wrong"}, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/api/me/password", {"name": "Jia", "password": "password3", "old": "password1"}, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/login", {"name": "Jia", "password": "password1"})[0], 400)
        self.assertEqual(self.call("/api/login", {"name": "Jia", "password": "password3"})[0], 200)
        for _ in range(accounts.LOCK_AFTER):
            self.call("/api/login", {"name": "Jia", "password": "guess"})
        code, got, _ = self.call("/api/login", {"name": "Jia", "password": "password3"})
        self.assertEqual(code, 400)
        self.assertIn("分钟后再试", got["error"])

    def test_space_copies_the_research_and_roles_hold(self):
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        rid, tid, private = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        with store.at_home(store.library_home(space)):
            self.assertEqual(store.get_research(rid)["title"], "共享的研究")
            self.assertTrue(store.get_paper("arxiv:2410.24164"))
            self.assertEqual([m["text"] for m in store.thread_for(thread_id=tid)["messages"]][-1], "这条也带走")
            with self.assertRaises(Exception):
                store.thread_for(thread_id=private)
        with store.at_home(store.library_home(a["library"])):
            self.assertTrue(store.get_research(rid))  # the original stays, so its papers keep showing as used
            self.assertTrue(store.get_paper("arxiv:2410.24164")["research"])
            self.assertTrue(store.thread_for(thread_id=tid))
        self.assertIsNone(accounts.role(b["id"], space))
        accounts.redeem(accounts.invite("join", space, "viewer"), user=b)
        self.assertEqual(accounts.role(b["id"], space), "viewer")
        self.assertEqual(accounts.agent_member(space), a["id"])
        with self.assertRaises(rules.Rejected):
            accounts.set_agent(space, b["id"])  # a viewer cannot run the space's agents
        with self.assertRaises(rules.Rejected):
            accounts.set_role(space, a["id"], "editor")  # the last owner stays

    def test_shared_research_shows_at_home_and_the_owner_moves_it_back(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        rid, tid, _ = self.research_with_material(a["library"])
        pid = "0123456789abcdef01234567"
        with store.at_home(store.library_home(a["library"])):
            mine = store.new_thread("library", text="别的讨论")
            store.set_thread_agent(mine, "bc-mine")
            other = store.add_research("别的研究", "user")
            store.set_overleaf(rid, pid)
            overleaf.local_dir(pid).mkdir(parents=True)
            (overleaf.local_dir(pid) / "main.tex").write_text("还没推送的改动")
            accounts.write_config(a["library"], overleaf_token="olp_x")
            space = accounts.create_space(a, "组会", rid)
            self.assertFalse(overleaf.local_dir(pid).exists())  # moved, not left behind to drift
        with store.at_home(store.library_home(space)):
            self.assertEqual(store.get_writing(rid)["overleaf"], pid)
            self.assertEqual((overleaf.local_dir(pid) / "main.tex").read_text(), "还没推送的改动")
            self.assertEqual(overleaf._token(), "olp_x")
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        home_list = self.call("/api/research?shared=1", cookie=f"p2b_session={ta}")[1]
        self.assertEqual({(x["title"], (x.get("space") or {}).get("name")) for x in home_list}, {("别的研究", None), ("共享的研究", "组会")})
        self.assertEqual([x["title"] for x in self.call("/api/research", cookie=f"p2b_session={ta}")[1]], ["别的研究"])  # pickers: own only, not the pointer
        with store.at_home(store.library_home(space)):
            store.add_message(tid, "user", "在空间里加的")
            (overleaf.local_dir(pid) / "main.tex").write_text("空间里又改的")
        editor = f"p2b_session={tb}; p2b_lib={space}"
        self.assertEqual(self.call(f"/api/research/{rid}/move-home", {}, cookie=editor)[0], 400)  # only the owner
        code, got, headers = self.call(f"/api/research/{rid}/move-home", {}, cookie=f"p2b_session={ta}; p2b_lib={space}")
        self.assertEqual((code, got["library"]), (200, a["library"]))
        with store.at_home(store.library_home(a["library"])):
            self.assertEqual(store.get_research(rid)["title"], "共享的研究")
            self.assertEqual(store.thread_for(thread_id=tid)["messages"][-1]["text"], "在空间里加的")
            self.assertEqual(store.thread_for(thread_id=mine)["agent_id"], "bc-mine")  # the rest of the library untouched
            self.assertTrue(store.get_research(other))
            self.assertEqual(store.get_writing(rid)["overleaf"], pid)
            self.assertEqual((overleaf.local_dir(pid) / "main.tex").read_text(), "空间里又改的")
        self.assertIsNone(accounts.role(b["id"], space))  # the empty space is archived
        self.assertTrue((accounts.root() / "archive" / space / "p2b.sqlite").is_file())
        self.assertNotIn(space, accounts.all_libraries())

    def test_a_checkout_two_researches_share_is_copied_not_moved(self):
        a, _ = self.user("甲")
        pid = "0123456789abcdef01234567"
        with store.at_home(store.library_home(a["library"])):
            one, two = store.add_research("一", "user"), store.add_research("二", "user")
            store.set_overleaf(one, pid)
            store.set_overleaf(two, pid)
            overleaf.local_dir(pid).mkdir(parents=True)
            (overleaf.local_dir(pid) / "main.tex").write_text("x")
            space = accounts.create_space(a, "组会", one)
            self.assertTrue((overleaf.local_dir(pid) / "main.tex").is_file())  # "二" still writes here
        with store.at_home(store.library_home(space)):
            self.assertTrue((overleaf.local_dir(pid) / "main.tex").is_file())

    def test_in_a_space_each_member_sees_and_runs_only_their_own_discussions(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, _ = self.user("乙")
        rid, tid, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        here = store.library_home(space)
        with store.at_home(here), store.viewing(b):
            mine = store.new_thread("library", text="乙的想法")
            store.add_run("cr-b", "ag-b", "chat", mine, "p", "CREATING")
            with self.assertRaises(Exception):
                store.thread_for(thread_id=tid)  # 甲's discussion, brought along when sharing
            self.assertTrue(store.get_research(rid))  # the research itself is everyone's
        with store.at_home(here), store.viewing(a):
            self.assertTrue(store.thread_for(thread_id=tid))
            with self.assertRaises(Exception):
                store.thread_for(thread_id=mine)
            self.assertIsNone(store.get_run("cr-b"))
            self.assertFalse(any(m["thread_id"] == mine for m in store.search("乙的想法")["messages"]))
        self.assertIsNone(cursor_api.claim(accounts.agent_libraries(a), a, ["cursor"]))  # 乙's run waits for 乙's Mac
        self.assertEqual(cursor_api.claim(accounts.agent_libraries(b), b, ["cursor"])["run_id"], "cr-b")
        with store.at_home(store.library_home(a["library"])):
            pid = next(l["ref"] for l in store.get_research(rid)["links"] if l["kind"] == "paper")
        got = self.call(f"/api/papers/{pid}", cookie=f"p2b_session={ta}")[1]["research"]
        self.assertEqual([(x["id"], x["space"]["id"]) for x in got], [(rid, space)])  # the pointer leads to the space
        self.assertEqual(self.call(f"/api/research/{rid}", cookie=f"p2b_session={ta}")[1]["space"]["id"], space)

    def test_from_the_personal_library_a_shared_research_is_changed_in_its_space(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        rid, _, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
            store.upsert_paper("arxiv:2501.00001", "我后来读的")
        with store.at_home(store.library_home(space)):
            store.update_research(rid, title="空间里改过的")
        agent = accounts.issue(a["id"], "agent")
        cli = lambda *argv: self.call("/api/cli", {"argv": list(argv)}, bearer=agent)  # noqa: E731
        self.assertEqual(cli("research", "show", rid)[1]["out"]["title"], "空间里改过的")  # not the pointer's old copy
        self.assertEqual(cli("research", "link", rid, "paper", "arxiv:2501.00001")[0], 200)
        with store.at_home(store.library_home(space)):
            self.assertIn("arxiv:2501.00001", [l["ref"] for l in store.get_research(rid)["links"]])  # the paper came along
        with store.at_home(store.library_home(a["library"])):
            self.assertNotIn("arxiv:2501.00001", [l["ref"] for l in store.get_research(rid)["links"]])
        self.assertIn("空间里改过的", [x["title"] for x in cli("research", "list")[1]["out"]])
        with store.at_home(store.library_home(a["library"])):
            talk = store.new_thread("paper:arxiv:2501.00001", text="这篇对 Apexio 有什么启发")
        self.assertEqual(cli("research", "link", rid, "thread", talk)[0], 200)
        b, _ = self.user("乙")
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        with store.at_home(store.library_home(space)), store.viewing(a):
            self.assertEqual(store.thread_for(thread_id=talk)["messages"][0]["text"], "这篇对 Apexio 有什么启发")
        with store.at_home(store.library_home(space)), store.viewing(b):
            with self.assertRaises(Exception):
                store.thread_for(thread_id=talk)  # linked, yet still 甲's own
        code, got, _ = self.call(f"/api/research/{rid}", {"title": "改指针"}, cookie=f"p2b_session={ta}")
        self.assertEqual(code, 400)
        self.assertIn("组会", got["error"])

    def test_space_papers_and_discussions_point_home_and_come_back(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        rid, _, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        with store.at_home(store.library_home(space)), store.viewing(b):  # 乙在空间里收下一篇论文并讨论它
            store.upsert_paper("arxiv:2501.00001", "乙发现的")
            cap, _ = store.add_capture("cap-b", "https://arxiv.org/abs/2501.00001", "web")
            store.link(cap["id"], "arxiv:2501.00001", "user")
            btid = store.new_thread("paper:arxiv:2501.00001", text="这篇对路吗")
            child = store.new_thread("paper:arxiv:2501.00001", parent_id=btid, text="接着说")
        with store.at_home(store.library_home(space)):  # 归属记录之前收下的（user_id 为空）：全空间共享
            store.upsert_paper("arxiv:2502.00002", "早年收下的")
            cap, _ = store.add_capture("cap-old", "https://arxiv.org/abs/2502.00002", "find")
            store.link(cap["id"], "arxiv:2502.00002", "agent")
        home_b = f"p2b_session={tb}"
        # 参与即复制：乙添加并讨论过的论文已经真的进了乙的个人库，不是指针
        self.assertIn("arxiv:2501.00001", [p["id"] for p in self.call("/api/papers", cookie=home_b)[1]])
        got = self.call("/api/papers?shared=1", cookie=home_b)[1]
        self.assertFalse([p for p in got if p["id"] == "arxiv:2501.00001" and p.get("space")])  # 已在家，不再是指针
        self.assertIn("arxiv:2502.00002", [p["id"] for p in got])  # 存量（归属为空）的仍是指针
        got_a = self.call("/api/papers?shared=1", cookie=f"p2b_session={ta}")[1]
        self.assertFalse([p for p in got_a if p["id"] == "arxiv:2501.00001"])  # 甲没有乙添加的
        self.assertIn("arxiv:2502.00002", [p["id"] for p in got_a])  # 但存量共享的两边都有
        got = self.call("/api/threads/recent?shared=1", cookie=home_b)[1]
        self.assertEqual([t["space"]["name"] for t in got if t["id"] == btid], ["组会"])
        self.assertFalse([t for t in self.call("/api/threads/recent?shared=1", cookie=f"p2b_session={ta}")[1] if t["id"] == btid])
        code, got, _ = self.call(f"/api/threads?id={btid}", cookie=home_b)  # 空间的讨论在个人库里只读直读
        self.assertEqual((code, got["space"]["name"]), (200, "组会"))
        self.assertEqual(got["messages"][0]["text"], "这篇对路吗")
        self.assertEqual(self.call(f"/api/threads?id={btid}", cookie=f"p2b_session={ta}")[0], 400)  # 甲读不到乙的
        self.assertEqual(self.call("/api/papers/arxiv:2501.00001/copy-home", {}, cookie=home_b)[0], 400)  # 已在个人库
        self.assertEqual(self.call("/api/papers/arxiv:2501.00001/copy-home", {}, cookie=f"{home_b}; p2b_lib={space}")[0], 200)
        with store.at_home(store.library_home(b["library"])):
            self.assertTrue(store.get_paper("arxiv:2501.00001")["listed"])
        code, _, _ = self.call(f"/api/research/{rid}/move-home", {}, cookie=f"p2b_session={ta}; p2b_lib={space}")
        self.assertEqual(code, 200)  # 空间随之归档：乙的讨论（连同论文）复制回乙的个人库，树形还在
        self.assertNotIn(space, accounts.all_libraries())
        with store.at_home(store.library_home(b["library"])), store.tx() as c:
            self.assertEqual(c.execute("SELECT parent_id FROM threads WHERE id=?", (child,)).fetchone()[0], btid)
        with store.at_home(store.library_home(b["library"])):
            self.assertEqual(store.thread_for(thread_id=btid)["messages"][0]["text"], "这篇对路吗")
            self.assertTrue(store.get_paper("arxiv:2501.00001"))

    def test_home_copy_pulls_newer_card_report_and_pdf_from_the_space(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        rid, _, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        with store.at_home(store.library_home(space)), store.viewing(b):  # 参与即复制回家（此时无卡、一篇连 PDF 都没有）
            store.new_thread("paper:arxiv:2410.24164", text="怎么看")
            store.upsert_paper("arxiv:2503.00003", "只有元数据")
            store.new_thread("paper:arxiv:2503.00003", text="这篇呢")
        home_b = f"p2b_session={tb}"
        self.assertIsNone(self.call("/api/papers/arxiv:2410.24164", cookie=home_b)[1]["card"])
        with store.at_home(store.library_home(space)):  # 空间的 agent 写出了卡、报告，补上了 PDF
            store.put_card("arxiv:2410.24164", {"tldr": "空间里的卡"}, "agent")
            store.put_report("arxiv:2410.24164", {"sections": []}, "agent")
            doc = pymupdf.open()
            doc.new_page().insert_text((72, 72), "Some Paper")
            pipeline.attach_pdf("arxiv:2503.00003", doc.tobytes())
        got = self.call("/api/papers/arxiv:2410.24164", cookie=home_b)[1]  # 打开家里的副本：卡和报告拉回来了
        self.assertEqual(got["card"]["tldr"], "空间里的卡")
        self.assertTrue(got["report"])
        self.assertTrue(self.call("/api/papers/arxiv:2503.00003", cookie=home_b)[1]["page_count"])  # PDF 也补上了
        with store.at_home(store.library_home(b["library"])):  # 家里自己重写得更勤：空间再写不覆盖
            store.put_card("arxiv:2410.24164", {"tldr": "家里的卡"}, "agent")
            store.put_card("arxiv:2410.24164", {"tldr": "家里的卡 v2"}, "agent")
        with store.at_home(store.library_home(space)):
            store.put_card("arxiv:2410.24164", {"tldr": "空间里的卡 v2"}, "agent")
        self.assertEqual(self.call("/api/papers/arxiv:2410.24164", cookie=home_b)[1]["card"]["tldr"], "家里的卡 v2")

    def test_leaving_a_space_carries_your_discussions_home(self):
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        rid, _, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        with store.at_home(store.library_home(space)), store.viewing(b):
            btid = store.new_thread("library", text="乙在空间里的想法")
        accounts.set_role(space, b["id"], None)
        with store.at_home(store.library_home(b["library"])):
            self.assertEqual(store.thread_for(thread_id=btid)["messages"][0]["text"], "乙在空间里的想法")
        with store.at_home(store.library_home(space)), store.viewing(b):
            self.assertEqual(store.thread_for(thread_id=btid)["messages"][0]["text"], "乙在空间里的想法")  # 原件也还在

    def test_http_sign_in_isolation_and_roles(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, tb = self.user("乙")
        code, _, headers = self.call("/")
        self.assertEqual((code, headers["Location"]), (302, "/join.html"))
        self.assertEqual(self.call("/api/state")[0], 401)
        self.assertEqual(self.call("/api/notes", {"text": "甲的笔记"}, cookie=f"p2b_session={ta}")[0], 200)
        self.assertEqual((self.notes(a["library"]), self.notes(b["library"])), (["甲的笔记"], []))
        state = self.call("/api/state", cookie=f"p2b_session={tb}")[1]
        self.assertEqual(state["account"]["user"]["name"], "乙")
        self.assertEqual(state["account"]["library"], b["library"])
        # b picks a's library in a cookie without being a member: gets their own
        self.assertEqual(self.call("/api/state", cookie=f"p2b_session={tb}; p2b_lib={a['library']}")[1]["account"]["library"], b["library"])
        rid, _, _ = self.research_with_material(a["library"])
        code, got, headers = self.call("/api/spaces", {"research_id": rid, "name": "组会"}, cookie=f"p2b_session={ta}")
        space = got["library"]
        self.assertIn(f"p2b_lib={space}", headers["Set-Cookie"])
        owner = f"p2b_session={ta}; p2b_lib={space}"
        self.addCleanup(accounts._FAILS.clear)
        self.assertEqual(self.call("/api/members/invite", {"role": "viewer"}, cookie=owner)[0], 400)  # owners need to be super admins
        accounts.set_admin(a["id"], "second-pw")
        self.assertTrue(self.call("/api/state", cookie=owner)[1]["account"]["admin"])
        self.assertEqual(self.call("/api/members/invite", {"role": "viewer", "admin_password": "wrong"}, cookie=owner)[0], 400)
        invite = self.call("/api/members/invite", {"role": "viewer", "admin_password": "second-pw"}, cookie=owner)[1]["code"]
        self.assertEqual(self.call("/api/members/invite", {"role": "viewer", "admin_password": "second-pw"},
                                   cookie=f"p2b_session={tb}; p2b_lib={space}")[0], 400)  # b in their own library, not an admin
        self.assertEqual(self.call("/api/join", {"code": invite}, cookie=f"p2b_session={tb}")[0], 200)
        viewer = f"p2b_session={tb}; p2b_lib={space}"
        self.assertEqual(self.call(f"/api/research/{rid}", cookie=viewer)[0], 200)
        self.assertEqual(self.call("/api/notes", {"text": "不行"}, cookie=viewer)[0], 403)
        self.call(f"/api/members/{b['id']}", {"role": "editor"}, cookie=f"p2b_session={ta}; p2b_lib={space}")
        editor = viewer
        self.assertEqual(self.call("/api/notes", {"text": "可以"}, cookie=editor)[0], 200)
        self.assertEqual(self.call(f"/api/research/{rid}/establish", {}, cookie=editor)[0], 400)  # only the owner
        self.assertEqual(self.call(f"/api/research/{rid}", cookie=editor, method="DELETE")[0], 400)

    def test_agent_commands_run_in_the_agents_library(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        agent = accounts.issue(a["id"], "agent")
        self.assertEqual(self.call("/api/state", bearer=agent)[0], 403)  # an agent token only runs ./p2b
        rid, _, _ = self.research_with_material(a["library"])
        with store.at_home(store.library_home(a["library"])):
            space = accounts.create_space(a, "组会", rid)
        with store.at_home(store.library_home(space)):
            store.add_research("只在空间里", "user")
            store.add_run("run-1", "bc-agent-1", "chat", "x", "p", "RUNNING")
        titles = lambda agent_id=None: [x["title"] for x in self.call("/api/cli", {"argv": ["research", "list"], "agent_id": agent_id}, bearer=agent)[1]["out"]]  # noqa: E731
        self.assertNotIn("只在空间里", titles())
        self.assertIn("只在空间里", titles("bc-agent-1"))
        # a path on the server is never read; only files sent along
        code, got, _ = self.call("/api/cli", {"argv": ["card", "put", "arxiv:2410.24164", "--file", "/etc/passwd"]}, bearer=agent)
        self.assertEqual(code, 400)
        self.assertIn("没有随命令一起发来", got["error"])
        code, got, _ = self.call("/api/cli", {"argv": ["card", "put", "arxiv:2410.24164", "--fil=/etc/passwd"]}, bearer=agent)
        self.assertEqual(code, 400)  # an abbreviated flag is still a path on the server
        self.assertIn("没有随命令一起发来", got["error"])
        self.assertEqual(self.call("/api/cli", {"argv": ["admin", "invite", "--url", "x"]}, bearer=agent)[0], 400)
        self.assertIn("usage", self.call("/api/cli", {"argv": ["--help"]}, bearer=agent)[1]["out"]["help"])

    def test_forwarded_commands_bring_their_files_and_the_guide(self):
        self.url = self.serve()
        a, _ = self.user("甲")
        agent = accounts.issue(a["id"], "agent")
        with store.at_home(store.library_home(a["library"])):
            rid = store.add_research("研究", "user")
        draft = json.dumps({"title": "研究", "summary": "一句话", "problem": "问题", "method": "方法", "validation": "验证"})
        # what paper2brain-client sends for ./p2b research propose <id> --file=draft.json
        got = self.call("/api/cli", {"argv": ["research", "propose", rid, "--file", "draft.json"],
                                     "files": {"draft.json": base64.b64encode(draft.encode()).decode()}}, bearer=agent)[1]
        self.assertTrue(got["out"]["proposed"])
        with store.at_home(store.library_home(a["library"])):
            self.assertEqual(store.get_research(rid)["proposal"]["problem"], "问题")
        docs = self.call("/api/cli", {"argv": ["guide"]}, bearer=agent)[1]["out"]["docs"]
        self.assertIn("README.md", docs)
        self.assertIn("./p2b", docs["README.md"])
        # setup.sh only checks its token: the Cursor key and the quick search API stay on the Mac
        self.assertEqual(self.call("/api/agent/connect", {}, bearer=agent)[0], 200)
        with store.at_home(store.library_home(a["library"])):
            with self.assertRaises(cursor_api.AgentError):
                cursor_api._json("GET", "/models")  # the shared server never calls Cursor itself
            space = accounts.create_space(a, "组会", rid)
        with store.at_home(store.library_home(space)):
            self.assertEqual(cursor_api.config()["repo"], cursor_api.CLIENT_REPO)

    def test_tcodex_runs_on_the_members_mac(self):
        self.url = self.serve()
        a, _ = self.user("甲")
        agent = accounts.issue(a["id"], "agent")
        home = store.library_home(a["library"])
        tc = {"id": "gpt-6-sol", "backend": "tcodex"}

        def queued():
            for _ in range(50):
                if any(r["id"].startswith("tc-") and r["status"] == "CREATING" for r in store.unfinished_runs()):  # under its own id
                    return
                threading.Event().wait(0.1)

        with store.at_home(home):
            self.assertTrue(cursor_api._missing("tcodex"))  # no Mac asking yet: TCodex is not offered
            with self.assertRaises(cursor_api.AgentError):
                cursor_api.start_async("chat", store.thread_for("library")["id"], "你好", model=tc)
        self.assertIsNone(self.call("/api/runner/claim", {"wait": 0, "backends": ["tcodex"]}, bearer=agent)[1]["job"])
        with store.at_home(home):
            self.assertEqual(cursor_api._missing("tcodex"), [])
            self.assertIn("gpt-6-sol", [m["id"] for m in cursor_api.list_models()])
            t = store.thread_for("library")
            cursor_api.start_async("chat", t["id"], "你好", model=tc)
            queued()
        job = self.call("/api/runner/claim", {"wait": 0, "backends": ["tcodex"]}, bearer=agent)[1]["job"]
        self.assertEqual((job["prompt"], job["library"], job["session"]), (cursor_api.GUIDE_FIRST + "你好", a["library"], None))
        self.assertIsNone(self.call("/api/runner/claim", {"wait": 0, "backends": ["tcodex"]}, bearer=agent)[1]["job"])  # taken once
        # what paper2brain-client's runner reports while tcodex runs on the Mac, then at the end
        update = lambda **f: self.call("/api/runner/update", {"library": a["library"], "run_id": job["run_id"], **f}, bearer=agent)[1]  # noqa: E731
        self.assertTrue(update(text="", tools=[], session="s-1")["live"])
        self.assertTrue(update(text="答案", tools=[{"name": "command_execution", "status": "done", "args": "./p2b papers"}], done=True, duration_ms=5)["live"])
        with store.at_home(home):
            run = store.get_run(job["run_id"])
            self.assertEqual((run["status"], run["text"], run["tools"][0]["args"]), ("FINISHED", "答案", "./p2b papers"))
            self.assertEqual(store.kv_get(f"codex:{job['agent_id']}"), "s-1")
            cursor_api.start_async("chat", t["id"], "接着说", agent_id=store.thread_for(thread_id=t["id"])["agent_id"])
            queued()
        job2 = self.call("/api/runner/claim", {"wait": 0, "backends": ["tcodex"]}, bearer=agent)[1]["job"]
        self.assertEqual(job2["session"], "s-1")  # the follow-up resumes the same TCodex conversation
        with store.at_home(home):
            store.update_run(job2["run_id"], status="CANCELLED")
        self.assertFalse(self.call("/api/runner/update", {"library": a["library"], "run_id": job2["run_id"], "text": "x"}, bearer=agent)[1]["live"])

    def test_cursor_runs_are_started_by_the_members_mac(self):
        self.url = self.serve()
        a, _ = self.user("甲")
        agent = accounts.issue(a["id"], "agent")
        home = store.library_home(a["library"])
        claim = lambda **f: self.call("/api/runner/claim", {"wait": 0, **f}, bearer=agent)[1]["job"]  # noqa: E731
        with store.at_home(home):
            self.assertTrue(cursor_api._missing("cursor"))
        self.assertIsNone(claim(backends=["cursor"], machine="p2b-a-mac", models=[{"id": "claude-x", "displayName": "Claude X"}]))
        with store.at_home(home):
            self.assertEqual(cursor_api.status()["machine"], "p2b-a-mac")
            self.assertEqual([(m["id"], m["backend"]) for m in cursor_api.list_models()], [("claude-x", "cursor")])
            t = store.thread_for("library")
            cursor_api.start_async("chat", t["id"], "你好", model={"id": "claude-x", "params": [{"id": "thinking", "value": "on"}]})
            for _ in range(50):
                if any(r["id"].startswith("cr-") and r["status"] == "CREATING" for r in store.unfinished_runs()):
                    break
                threading.Event().wait(0.1)
        self.assertIsNone(claim(backends=["tcodex"]))  # a Mac without Cursor does not take it
        self.assertIsNone(claim(backends=["cursor"], take=[]))  # one full of runs leaves it waiting...
        with store.at_home(home):
            self.assertEqual(cursor_api._missing("cursor"), [])  # ...and still counts as there
        job = claim(backends=["cursor"])
        self.assertEqual((job["backend"], job["new"], job["repo"], job["model"]),
                         ("cursor", True, cursor_api.CLIENT_REPO, {"id": "claude-x", "params": [{"id": "thinking", "value": "on"}]}))
        update = lambda run, **f: self.call("/api/runner/update", {"library": a["library"], "run_id": run, **f}, bearer=agent)[1]["live"]  # noqa: E731
        self.assertTrue(update(job["run_id"], text="正在", tools=[]))
        self.assertTrue(update(job["run_id"], text="答案", tools=[], done=True, status="FINISHED", duration_ms=9))
        with store.at_home(home):
            self.assertEqual((store.get_run(job["run_id"])["status"], store.get_run(job["run_id"])["text"]), ("FINISHED", "答案"))
            aid = store.thread_for(thread_id=t["id"])["agent_id"]
            self.assertEqual(aid, job["agent_id"])
            cursor_api.start_async("chat", t["id"], "接着说", agent_id=aid)
            for _ in range(50):
                if any(r["id"].startswith("cr-") and r["status"] == "CREATING" for r in store.unfinished_runs()):
                    break
                threading.Event().wait(0.1)
        job2 = claim(backends=["cursor"])
        self.assertEqual((job2["new"], job2["agent_id"]), (False, aid))
        # the agent is gone at Cursor: the next message starts a fresh one
        self.assertTrue(update(job2["run_id"], text="", tools=[], done=True, error="Cursor API 404：agent not found"))
        with store.at_home(home):
            self.assertIsNone(store.thread_for(thread_id=t["id"])["agent_id"])

    def test_quick_search_calls_are_made_by_the_members_mac(self):
        self.url = self.serve()
        a, ta = self.user("甲")
        b, _ = self.user("乙")
        agent, other = accounts.issue(a["id"], "agent"), accounts.issue(b["id"], "agent")
        home = store.library_home(a["library"])
        with store.at_home(home):
            self.assertIsNone(cursor_api.search_api())  # no Mac with a search API asking: off
        self.assertIsNone(self.call("/api/runner/claim", {"wait": 0, "backends": ["api"]}, bearer=agent)[1]["job"])
        got = {}

        def ask():
            with store.at_home(home):
                got["text"] = cursor_api._chat(cursor_api.search_api(), "起个标题")
        asking = threading.Thread(target=ask)
        asking.start()
        job = self.call("/api/runner/claim", {"wait": 5, "backends": ["api"]}, bearer=agent)[1]["job"]
        self.assertEqual((job["backend"], job["op"], job["prompt"]), ("api", "chat", "起个标题"))
        self.assertFalse(self.call("/api/runner/update", {"call_id": job["call_id"], "result": {"text": "别人的"}}, bearer=other)[1]["live"])
        self.assertTrue(self.call("/api/runner/update", {"call_id": job["call_id"], "result": {"text": "好标题"}}, bearer=agent)[1]["live"])
        asking.join(5)
        self.assertEqual(got["text"], "好标题")
        self.assertEqual(self.call("/api/agent/search-api", {"base_url": "https://x/v1", "model": "m", "api_key": "k"}, cookie=f"p2b_session={ta}")[0], 400)

    def test_outbound_fetches_stay_public(self):
        for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "http://10.0.0.1/", "file:///etc/passwd"):
            with self.assertRaises(rules.Rejected):
                sources.check_public(url)
        sources.check_public("https://8.8.8.8/")
        with self.assertRaises(rules.Rejected):
            repos.local_dir(Path("/tmp/lib"), "/etc")
        with self.assertRaises(rules.Rejected):
            repos.local_dir(Path("/tmp/lib"), "https://8.8.8.8/../../../etc")


if __name__ == "__main__":
    unittest.main()
