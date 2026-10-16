"""Fetching from the outside world: article pages (WeChat, Xiaohongshu, generic) and arXiv."""
import difflib
import html
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
    """Snapshot of an article: title, account/author, plain text, outgoing links. Raises on network failure."""
    final, body, ctype = http_get(url)
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


def arxiv_lookup(arxiv_ids):
    return _arxiv_query({"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}) if arxiv_ids else []


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


def download(url, path):
    _, body, ctype = http_get(url, timeout=90)
    if not body.startswith(b"%PDF"):
        raise RuntimeError(f"下载到的不是 PDF（{ctype}）")
    path.write_bytes(body)
