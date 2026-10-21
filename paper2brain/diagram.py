"""Method / framework diagrams and motivation figures for the paper: the agent writes one HTML file (CSS layout + inline SVG), headless
Chromium turns its #figure element into a vector PDF for LaTeX and a 3x PNG the agent looks at to refine it."""
import json
import math
import shutil
import urllib.parse
from pathlib import Path

from . import rules, store

KATEX = Path(__file__).resolve().parent.parent / "web" / "vendor" / "katex"
KINDS = ("method", "motivation")  # framework diagram on the code tab, Figure 1 / teaser on the overview

# Flaws easy to miss in a screenshot: text spilling out of its box or over other text, wires through labels,
# parts outside the canvas, type too small for print.
CHECK = """() => {
  const fig = document.getElementById('figure'), F = fig.getBoundingClientRect(), out = new Set();
  const els = [...fig.querySelectorAll('*')].filter((el) => !el.closest('.katex'));  // subscripts are small on purpose
  for (const el of els) {
    const r = el.getBoundingClientRect(), what = (el.textContent || '').trim().slice(0, 30) || '<' + el.tagName.toLowerCase() + '>';
    if (!r.width && !r.height) continue;
    if (r.left < F.left - 1 || r.top < F.top - 1 || r.right > F.right + 1 || r.bottom > F.bottom + 1) out.add('超出画布：' + what);
    if (!(el instanceof SVGElement) && el.clientWidth && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) out.add('文字溢出盒子：' + what);
  }
  const small = els.filter((el) => el.childNodes.length && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())
    && parseFloat(getComputedStyle(el).fontSize) < MIN_PX).map((el) => el.textContent.trim().slice(0, 20));
  if (small.length) out.add(`字号小于 ${MIN_PX}px（印到论文里会小于 7pt）：` + small.slice(0, 5).join('、'));
  // Ink boxes of the text, one per line of each text node (a formula counts as one box).
  const boxes = [];
  for (const k of fig.querySelectorAll('.katex')) boxes.push({ r: k.getBoundingClientRect(), owner: k, what: k.textContent.trim().slice(0, 20) });
  const walk = document.createTreeWalker(fig, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) {
    if (!n.textContent.trim() || n.parentElement.closest('.katex, style, script')) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 1) boxes.push({ r, owner: n, what: n.textContent.trim().slice(0, 20) });
  }
  const shrink = (r, d) => ({ left: r.left + d, right: r.right - d, top: r.top + d, bottom: r.bottom - d });
  const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.owner !== b.owner && hit(shrink(a.r, 2), shrink(b.r, 2))) out.add(`文字互相重叠：「${a.what}」和「${b.what}」`);
  }
  // Long strokes are wires (icons are short): sample them and see whether any runs through text.
  for (const p of fig.querySelectorAll('svg path, svg line, svg polyline')) {
    const len = p.getTotalLength ? p.getTotalLength() : 0, m = p.getScreenCTM();
    if (len < 40 || !m || getComputedStyle(p).stroke === 'none') continue;
    for (let s = 0; s <= len; s += 3) {
      const q = new DOMPoint(p.getPointAtLength(s).x, p.getPointAtLength(s).y).matrixTransform(m);
      const b = boxes.find((b) => hit(shrink(b.r, 1), { left: q.x, right: q.x, top: q.y, bottom: q.y }));
      if (b) { out.add(`连线穿过文字：「${b.what}」`); break; }
    }
  }
  return [...out].slice(0, 20);
}"""


SANDBOX = "http://p2b.diagram/"


def _sandbox(page, src):
    """Serve the figure from a made-up http origin, from which Chromium refuses file:// loads, and answer only the
    figure and KaTeX: everything else (the network, other files) is aborted."""
    katex = KATEX.resolve()

    def answer(route):
        path = urllib.parse.urlsplit(route.request.url)
        f = None
        if f"{path.scheme}://{path.netloc}/" == SANDBOX:
            f = src if path.path == "/figure.html" else (katex / path.path.removeprefix("/katex/")).resolve()
        if f == src or (f and path.path.startswith("/katex/") and katex in f.parents and f.is_file()):
            return route.fulfill(path=str(f))
        route.abort()

    page.route("**/*", answer)
    return SANDBOX + "figure.html", SANDBOX + "katex/"


def render(rid, name, src, title=None, caption=None, kind=None):
    if not store.get_research(rid):
        raise rules.Rejected("这个研究不存在或已删除")
    if kind not in (None, *KINDS):
        raise rules.Rejected("--kind 是 method（方法框架图）或 motivation（动机图）")
    out = store.diagram_dir(rid, name)
    src = Path(src or "").expanduser().resolve()
    if not src.is_file():
        raise rules.Rejected(f"找不到 HTML 文件：{src}")
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise rules.Rejected("没装 playwright：运行 ops/setup.sh") from None
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 2400, "height": 1600}, device_scale_factor=3)
                url, katex = src.as_uri(), KATEX.as_uri() + "/"
                if store.multi():  # the agent's HTML must not read other files or the network of the shared server
                    url, katex = _sandbox(page, src)
                page.goto(url, wait_until="networkidle")
                if not page.query_selector("#figure"):
                    raise rules.Rejected('HTML 里要有一个 id="figure" 的元素，图就是它（固定 px 宽度）')
                page.add_style_tag(content="html,body{margin:0!important;padding:0!important;background:#fff}#figure{margin:0!important}")
                if "\\(" in page.inner_text("#figure"):
                    page.add_style_tag(url=katex + "katex.min.css")
                    page.add_script_tag(url=katex + "katex.min.js")
                    page.add_script_tag(url=katex + "auto-render.min.js")
                    page.evaluate("renderMathInElement(document.getElementById('figure'),"
                                  " {delimiters: [{left: '\\\\(', right: '\\\\)', display: false}], throwOnError: false})")
                page.evaluate("document.fonts.ready.then(() => 1)")
                # arrows are drawn from the final box positions, so after math and fonts have settled
                page.evaluate("window.layout && window.layout()")
                box = page.locator("#figure").bounding_box()
                w, h = math.ceil(box["width"]), math.ceil(box["height"])
                # 7pt in print: 18px on the 1200px (\textwidth) or 600px (\columnwidth) canvas, more on wider ones
                warnings = page.evaluate(CHECK.replace("MIN_PX", str(max(18, round(w * 0.0144)))))
                page.locator("#figure").screenshot(path=str(tmp / "figure.png"))
                page.pdf(path=str(tmp / "figure.pdf"), width=f"{w}px", height=f"{h + 1}px", print_background=True, page_ranges="1")
            finally:
                browser.close()
    except Error as e:
        shutil.rmtree(tmp, ignore_errors=True)
        hint = "；没装 Chromium 时运行 .venv/bin/python -m playwright install chromium" if "Executable doesn't exist" in str(e) else ""
        raise rules.Rejected(f"渲染失败：{str(e)[:300]}{hint}") from None
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.copyfile(src, tmp / "figure.html")
    old = json.loads((out / "meta.json").read_text()) if (out / "meta.json").is_file() else {}
    meta = {"name": name, "kind": kind or old.get("kind") or "method", "title": title or old.get("title"), "caption": caption or old.get("caption"), "width": w, "height": h, "at": store.now()}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return {**meta, "png": str(out / "figure.png"), "pdf": str(out / "figure.pdf"), "warnings": warnings,
            "next": "打开 png 看图，对照 librarian/diagram.md 的检查清单逐条修改后重新 render，warnings 要清零"}
