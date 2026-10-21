"""Fetching from the outside world: article pages (WeChat, Xiaohongshu, generic) and arXiv."""
import difflib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from . import rules

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(), r.headers.get("Content-Type", "")


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "section", "li", "h1", "h2", "h3", "h4", "tr", "blockquote", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self._skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.links.append(href)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_text(fragment):
    p = _Text()
    p.feed(fragment)
    text = re.sub(r"[ \t\u00a0\u3000]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n", text).strip(), p.links


def _meta(page, name):
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)', page, re.I
    ) or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']', page, re.I)
    return html.unescape(m.group(1)).strip() if m else None


def _js_var(page, name):
    m = re.search(rf"var\s+{name}\s*=\s*(?:htmlDecode\()?['\"]([^'\"]*)['\"]", page)
    return html.unescape(m.group(1)).strip() if m and m.group(1).strip() else None


def channel_of(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    if "mp.weixin.qq.com" in host:
        return "wechat"
    if "xhslink.com" in host or "xiaohongshu.com" in host:
        return "xhs"
    if "arxiv.org" in host or "alphaxiv.org" in host or "huggingface.co" in host:
        return "arxiv"
    return "web"


def fetch_page(url):
    """Snapshot of an article: title, account/author, plain text, outgoing links. A PDF comes back as {"pdf": bytes}.
    Raises on network failure."""
    final, body, ctype = http_get(url)
    if body.startswith(b"%PDF"):
        return {"url": final, "pdf": body}
    page = body.decode("utf-8", errors="replace")
    channel = channel_of(final)
    if channel == "wechat":
        m = re.search(r'id="js_content"[^>]*>(.*?)<script', page, re.S)
        text, links = html_text(m.group(1) if m else page)
        title = _meta(page, "og:title") or _js_var(page, "msg_title")
        author = _js_var(page, "nickname") or _meta(page, "og:article:author")
        source = _js_var(page, "msg_source_url")
        if source:
            links.append(source)
        if not text and ("环境异常" in page or "verify" in final):
            raise RuntimeError("公众号要求验证，暂时抓不到正文")
    else:
        text, links = html_text(page)
        title = _meta(page, "og:title") or (re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I) or [None, None])[1]
        title = html.unescape(title).strip() if title else None
        author = _meta(page, "og:article:author") or _meta(page, "author")
        desc = _meta(page, "og:description") or _meta(page, "description")
        if channel == "xhs" and desc:
            text = desc + "\n" + text[:3000]
    return {
        "url": final, "channel": channel, "title": title, "author": author,
        "text": text[:40000], "links": list(dict.fromkeys(links))[:200], "html": page,
    }


_TITLE_HINT = re.compile(r"(?:论文(?:标题|题目|名称)?|标题|题目|title|paper)\s*[:：]\s*(.+)", re.I)


def _titleish(line):
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]*", line)
    latin = sum(len(w) for w in words)
    return 4 <= len(words) <= 30 and latin > 0.6 * len(line.replace(" ", "")) and "http" not in line


def guess_titles(text, page_title=None):
    """English paper titles hiding in a Chinese write-up: the page title, an explicit "论文标题：" line,
    then lines that read like a title (mostly Latin words, 4–30 of them)."""
    out = []
    if page_title:
        t = re.sub(r"^\s*[\[【(（][^\]】)）]{0,12}[\]】)）]\s*", "", page_title).strip()
        if _titleish(t):
            out.append(t)
    for line in (text or "").splitlines():
        m = _TITLE_HINT.search(line.strip())
        if m and _titleish(m.group(1).strip(" 《》\"'")):
            out.append(m.group(1).strip(" 《》\"'"))
    for line in (text or "").splitlines():
        line = line.strip(" 《》\"'")
        if not _TITLE_HINT.search(line) and _titleish(line):
            out.append(line)
    return list(dict.fromkeys(out))[:4]


# ---------- arXiv ----------

_NS = {"a": "http://www.w3.org/2005/Atom"}


def clean_title(title):
    """arXiv titles carry inline LaTeX such as "$\\pi_0$"; keep the readable part."""
    greek = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "lambda": "λ", "mu": "μ",
             "pi": "π", "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω"}
    title = re.sub(r"\\(" + "|".join(greek) + r")\b", lambda m: greek[m.group(1)], title)
    title = re.sub(r"\$([^$]*)\$", lambda m: re.sub(r"[\\_{}^]|mathrm|text", "", m.group(1)), title)
    return re.sub(r"\s+", " ", title).strip()
_last_call = [0.0]


def _arxiv_query(params):
    wait = 3 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    _, body, _ = http_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params))
    out = []
    for e in ET.fromstring(body).findall("a:entry", _NS):
        abs_url = e.findtext("a:id", "", _NS)
        ids = rules.find_arxiv_ids(abs_url)
        title = clean_title(e.findtext("a:title", "", _NS))
        if not ids or not title or title == "Error":
            continue
        out.append({
            "arxiv_id": ids[0],
            "title": title,
            "authors": [a.findtext("a:name", "", _NS) for a in e.findall("a:author", _NS)],
            "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", _NS)).strip(),
            "published": e.findtext("a:published", "", _NS)[:10],
            "url": f"https://arxiv.org/abs/{ids[0]}",
        })
    return out


def _abs_page(arxiv_id):
    """One paper from its arxiv.org/abs page (citation_* meta tags); None when arXiv has no such paper."""
    try:
        _, body, _ = http_get(f"https://arxiv.org/abs/{arxiv_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    page = body.decode("utf-8", errors="replace")
    title = _meta(page, "citation_title")
    if not title:
        return None
    authors = [" ".join(reversed(html.unescape(a).split(", ", 1))) for a in re.findall(r'name="citation_author" content="([^"]*)"', page)]
    return {"arxiv_id": arxiv_id, "title": clean_title(title), "authors": authors,
            "abstract": re.sub(r"\s+", " ", _meta(page, "citation_abstract") or "").strip(),
            "published": (_meta(page, "citation_date") or "").replace("/", "-"), "url": f"https://arxiv.org/abs/{arxiv_id}"}


def arxiv_lookup(arxiv_ids):
    if not arxiv_ids:
        return []
    try:
        return _arxiv_query({"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)})
    except urllib.error.HTTPError:
        # export.arxiv.org rate-limits (429) for minutes at a time while arxiv.org/abs still answers
        return [f for f in map(_abs_page, arxiv_ids) if f]


def title_score(a, b):
    return difflib.SequenceMatcher(None, rules.normalize(a), rules.normalize(b)).ratio()


_STOP = {"with", "from", "for", "the", "and", "via", "towards", "toward", "using", "into", "over", "your", "that"}


def arxiv_search(title, limit=5):
    """Exact title phrase first, then all distinctive words, then any of them; ranked by title similarity."""
    words = re.findall(r"[A-Za-z0-9]+", title)
    if len(words) < 2:
        return []
    key = [w for w in words if len(w) > 3 and w.lower() not in _STOP][:8]
    results = []
    for query in ('ti:"' + " ".join(words) + '"', " AND ".join(f"ti:{w}" for w in key), " OR ".join(f"ti:{w}" for w in key)):
        if query and not results:
            results = _arxiv_query({"search_query": query, "max_results": limit * 4 if " OR " in query else limit})
    for r in results:
        r["score"] = round(title_score(title, r["title"]), 3)
        r["query"] = title
    return [r for r in sorted(results, key=lambda r: -r["score"]) if r["score"] >= 0.45][:limit]


def download(url, path=None):
    _, body, ctype = http_get(url, timeout=90)
    if not body.startswith(b"%PDF"):
        raise RuntimeError(f"下载到的不是 PDF（{ctype}）")
    if path:
        path.write_bytes(body)
    return body


# ---------- DOI, title search beyond arXiv, OpenReview ----------

def _json(url):
    _, body, _ = http_get(url)
    return json.loads(body)


def _openalex_meta(w):
    words = w.get("abstract_inverted_index") or {}
    abstract = " ".join(k for k, _ in sorted(((k, i) for k, v in words.items() for i in v), key=lambda x: x[1])) or None
    arxiv = None
    for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
        ids = rules.find_arxiv_ids((loc or {}).get("landing_page_url") or "")
        if ids:
            arxiv = ids[0]
            break
    oa = w.get("best_oa_location") or {}
    return {
        "title": clean_title(w.get("title") or ""),
        "authors": [a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")],
        "abstract": abstract,
        "published": w.get("publication_date"),
        "pdf_url": oa.get("pdf_url"),
        "arxiv_id": arxiv,
    }


def doi_lookup(doi):
    """Metadata for a DOI: OpenAlex first (it also knows open-access PDFs and arXiv copies), Crossref as fallback."""
    try:
        meta = _openalex_meta(_json(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}"))
        if meta["title"]:
            return {**meta, "url": f"https://doi.org/{doi}"}
    except Exception:
        pass
    m = _json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto=paper2brain@localhost")["message"]
    pdf = next((ln["URL"] for ln in m.get("link") or [] if ln.get("content-type") == "application/pdf"), None)
    return {
        "title": clean_title((m.get("title") or [""])[0]),
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in m.get("author") or []],
        "abstract": re.sub(r"<[^>]+>", "", m.get("abstract") or "") or None,
        "published": "-".join(str(x) for x in (m.get("issued", {}).get("date-parts") or [[None]])[0] if x),
        "pdf_url": pdf, "arxiv_id": None, "url": f"https://doi.org/{doi}",
    }


def crossref_search(title, limit=5):
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": limit, "mailto": "paper2brain@localhost",
                                "select": "DOI,title,author,issued,type"})
    out = []
    for m in _json("https://api.crossref.org/works?" + q)["message"]["items"]:
        t = clean_title((m.get("title") or [""])[0])
        if not t:
            continue
        out.append({
            "pid": "doi:" + m["DOI"].lower(), "title": t,
            "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in m.get("author") or []],
            "published": "-".join(str(x) for x in (m.get("issued", {}).get("date-parts") or [[None]])[0] if x),
            "url": f"https://doi.org/{m['DOI']}", "score": round(title_score(title, t), 3), "query": title,
        })
    return out


def openreview_lookup(note_id):
    try:
        note = _json(f"https://api2.openreview.net/notes?id={note_id}")["notes"][0]["content"]
    except Exception as e:
        raise RuntimeError(f"OpenReview 访问失败（{e}）。可以在浏览器下载 PDF 后拖进来") from None
    val = lambda k: (note.get(k) or {}).get("value") if isinstance(note.get(k), dict) else note.get(k)
    return {"title": clean_title(val("title") or ""), "authors": val("authors") or [], "abstract": val("abstract"),
            "published": None, "pdf_url": f"https://openreview.net/pdf?id={note_id}", "arxiv_id": None,
            "url": f"https://openreview.net/forum?id={note_id}"}


_NAME = re.compile(r"[A-Za-zπ][A-Za-z0-9π.+\-]*[A-Za-z0-9]")


def name_terms(text):
    """Latin words a person would search with: model names (PatchWAM, OpenVLA-OFT, π0.5) and plain keywords."""
    text = re.sub(r"https?://\S+", " ", text or "")
    return list(dict.fromkeys(t for t in _NAME.findall(text) if len(t) >= 3 and t.lower() not in _STOP))[:6]


def distinctive(term):
    """A coined name rather than an ordinary word: internal capitals, digits or π."""
    return len(term) >= 4 and bool(re.search(r"[a-z][A-Z]|[A-Z]{2}|\d|π", term))


def names_itself(title, name):
    """The title introduces this name ("PatchWAM: ...", "... with PatchWAM", "(PatchWAM)"), rather than building on
    a method of that name ("RL Bootstrapping of OpenVLA-OFT ...")."""
    n = re.escape(name)
    return bool(re.search(rf"^{n}\s*[:：]|^{n}\s+[-–—]\s|\b(?:with|via|introducing)\s+{n}\s*$|\({n}\)", title.strip(), re.I))


def keyword_search(terms, limit=6):
    """Short names and keywords: arXiv full-text fields, ranked by how many terms the title contains."""
    query = " AND ".join(f'all:"{t}"' for t in terms[:4])
    results = _arxiv_query({"search_query": query, "max_results": limit * 2}) if query else []
    phrase = " ".join(terms).lower()
    for r in results:
        hits = sum(bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", r["title"], re.I)) for t in terms)
        score = 1.0 if phrase in r["title"].lower() else 0.5 + 0.45 * hits / len(terms)
        r.update(pid="arxiv:" + r["arxiv_id"], score=round(score, 3), query=" ".join(terms))
    return sorted(results, key=lambda r: -r["score"])[:limit]


def find_paper(title, limit=5):
    """Title search: arXiv first, then Crossref (conference and journal papers with a DOI)."""
    found = [{**r, "pid": "arxiv:" + r["arxiv_id"]} for r in arxiv_search(title, limit)]
    if not found or found[0]["score"] < 0.95:
        try:
            found += [r for r in crossref_search(title, limit) if r["score"] >= 0.6]
        except Exception:
            pass
    return sorted(found, key=lambda r: -r["score"])[:limit]
