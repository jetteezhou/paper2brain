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

from paper2brain import accounts, cursor_api, repos, rules, server, sources, store  # noqa: E402


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
        user, token, lib = accounts.redeem(accounts.invite(), name)
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
        user, token, _ = accounts.redeem(code, "甲")
        self.assertEqual(accounts.auth(token)[0]["id"], user["id"])
        with self.assertRaises(rules.Rejected):
            accounts.redeem(code, "乙")
        self.assertNotIn(token.encode(), (accounts.root() / "hub.sqlite").read_bytes())

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
            self.assertIsNone(store.get_research(rid))  # moved: one copy to work on
            self.assertTrue(store.get_paper("arxiv:2410.24164"))  # its materials stay
            self.assertTrue(store.thread_for(thread_id=tid))
            self.assertTrue(list((store.home() / "backups").glob("p2b-before-share-*.sqlite")))
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
        with store.at_home(store.library_home(a["library"])):
            mine = store.new_thread("library", text="别的讨论")
            store.set_thread_agent(mine, "bc-mine")
            other = store.add_research("别的研究", "user")
            space = accounts.create_space(a, "组会", rid)
        accounts.redeem(accounts.invite("join", space, "editor"), user=b)
        home_list = self.call("/api/research?shared=1", cookie=f"p2b_session={ta}")[1]
        self.assertEqual({(x["title"], (x.get("space") or {}).get("name")) for x in home_list}, {("别的研究", None), ("共享的研究", "组会")})
        self.assertEqual([x["title"] for x in self.call("/api/research", cookie=f"p2b_session={ta}")[1]], ["别的研究"])  # pickers: own only
        with store.at_home(store.library_home(space)):
            store.add_message(tid, "user", "在空间里加的")
        editor = f"p2b_session={tb}; p2b_lib={space}"
        self.assertEqual(self.call(f"/api/research/{rid}/move-home", {}, cookie=editor)[0], 400)  # only the owner
        code, got, headers = self.call(f"/api/research/{rid}/move-home", {}, cookie=f"p2b_session={ta}; p2b_lib={space}")
        self.assertEqual((code, got["library"]), (200, a["library"]))
        with store.at_home(store.library_home(a["library"])):
            self.assertEqual(store.get_research(rid)["title"], "共享的研究")
            self.assertEqual(store.thread_for(thread_id=tid)["messages"][-1]["text"], "在空间里加的")
            self.assertEqual(store.thread_for(thread_id=mine)["agent_id"], "bc-mine")  # the rest of the library untouched
            self.assertTrue(store.get_research(other))
        self.assertIsNone(accounts.role(b["id"], space))  # the empty space is archived
        self.assertTrue((accounts.root() / "archive" / space / "p2b.sqlite").is_file())
        self.assertNotIn(space, accounts.all_libraries())

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
        invite = self.call("/api/members/invite", {"role": "viewer"}, cookie=f"p2b_session={ta}; p2b_lib={space}")[1]["code"]
        self.assertEqual(self.call("/api/members/invite", {"role": "viewer"}, cookie=f"p2b_session={tb}; p2b_lib={space}")[0], 200)  # b's own library
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
        # setup.sh registers the worker, the key and the quick search API in the user's own library
        code, got, _ = self.call("/api/agent/connect", {"cursor_api_key": "key_x", "machine": "p2b-a-mac",
                                 "search_api": {"base_url": "https://8.8.8.8/v1", "model": "m", "api_key": "s"}}, bearer=agent)
        self.assertEqual((code, got["search_api"]), (200, True))
        self.assertEqual(self.call("/api/agent/connect", {"cursor_api_key": "k", "machine": "m",
                                   "search_api": {"base_url": "http://10.0.0.1/v1", "model": "m", "api_key": "s"}}, bearer=agent)[0], 400)
        with store.at_home(store.library_home(a["library"])):
            self.assertEqual((cursor_api.config()["machine"], cursor_api.search_api()["model"]), ("p2b-a-mac", "m"))
            space = accounts.create_space(a, "组会", rid)
        with store.at_home(store.library_home(space)):
            self.assertEqual(cursor_api.search_api()["model"], "m")  # a space uses its agent member's
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
        self.assertIsNone(self.call("/api/tcodex/claim", {"wait": 0}, bearer=agent)[1]["job"])
        with store.at_home(home):
            self.assertEqual(cursor_api._missing("tcodex"), [])
            self.assertIn("gpt-6-sol", [m["id"] for m in cursor_api.list_models()])
            t = store.thread_for("library")
            cursor_api.start_async("chat", t["id"], "你好", model=tc)
            queued()
        job = self.call("/api/tcodex/claim", {"wait": 0}, bearer=agent)[1]["job"]
        self.assertEqual((job["prompt"], job["library"], job["session"]), (cursor_api.GUIDE_FIRST + "你好", a["library"], None))
        self.assertIsNone(self.call("/api/tcodex/claim", {"wait": 0}, bearer=agent)[1]["job"])  # taken once
        # what paper2brain-client's runner reports while tcodex runs on the Mac, then at the end
        update = lambda **f: self.call("/api/tcodex/update", {"library": a["library"], "run_id": job["run_id"], **f}, bearer=agent)[1]  # noqa: E731
        self.assertTrue(update(text="", tools=[], session="s-1")["live"])
        self.assertTrue(update(text="答案", tools=[{"name": "command_execution", "status": "done", "args": "./p2b papers"}], done=True, duration_ms=5)["live"])
        with store.at_home(home):
            run = store.get_run(job["run_id"])
            self.assertEqual((run["status"], run["text"], run["tools"][0]["args"]), ("FINISHED", "答案", "./p2b papers"))
            self.assertEqual(store.kv_get(f"codex:{job['agent_id']}"), "s-1")
            cursor_api.start_async("chat", t["id"], "接着说", agent_id=store.thread_for(thread_id=t["id"])["agent_id"])
            queued()
        job2 = self.call("/api/tcodex/claim", {"wait": 0}, bearer=agent)[1]["job"]
        self.assertEqual(job2["session"], "s-1")  # the follow-up resumes the same TCodex conversation
        with store.at_home(home):
            store.update_run(job2["run_id"], status="CANCELLED")
        self.assertFalse(self.call("/api/tcodex/update", {"library": a["library"], "run_id": job2["run_id"], "text": "x"}, bearer=agent)[1]["live"])

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
