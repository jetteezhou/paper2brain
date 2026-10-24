"""Method / framework diagrams and motivation figures for the paper: the agent writes one HTML file (CSS layout + inline SVG), headless
Chromium turns its #figure element into a vector PDF for LaTeX and a 3x PNG the agent looks at to refine it."""
import html as html_lib
import json
import math
import re
import shutil
import urllib.parse
from pathlib import Path

from . import rules, store

KATEX = Path(__file__).resolve().parent.parent / "web" / "vendor" / "katex"
CHARTJS = Path(__file__).resolve().parent.parent / "web" / "vendor" / "chartjs" / "chart.umd.min.js"
KINDS = ("method", "motivation", "chart")  # framework diagram on the code tab, Figure 1 / teaser, exported result chart

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


def _chromium(src, tmp):
    """src's #figure → figure.png + figure.pdf inside the prepared tmp dir; returns (width, height, warnings)."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise rules.Rejected("没装 playwright：运行 ops/setup.sh") from None
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
        hint = "；没装 Chromium 时运行 .venv/bin/python -m playwright install chromium" if "Executable doesn't exist" in str(e) else ""
        raise rules.Rejected(f"渲染失败：{str(e)[:300]}{hint}") from None
    return w, h, warnings


def render(rid, name, src, title=None, caption=None, kind=None, extra=None):
    if not store.get_research(rid):
        raise rules.Rejected("这个研究不存在或已删除")
    if kind not in (None, *KINDS):
        raise rules.Rejected("--kind 是 method（方法框架图）或 motivation（动机图）")
    out = store.diagram_dir(rid, name)
    src = Path(src or "").expanduser().resolve()
    if not src.is_file():
        raise rules.Rejected(f"找不到 HTML 文件：{src}")
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        w, h, warnings = _chromium(src, tmp)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.copyfile(src, tmp / "figure.html")
    old = json.loads((out / "meta.json").read_text()) if (out / "meta.json").is_file() else {}
    meta = {"name": name, "kind": kind or old.get("kind") or "method", "title": title or old.get("title"),
            "caption": caption or old.get("caption"), "width": w, "height": h, "at": store.now(), **(extra or {})}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return {**meta, "png": str(out / "figure.png"), "pdf": str(out / "figure.pdf"), "warnings": warnings,
            "next": "打开 png 看图，对照 librarian/diagram.md 的检查清单逐条修改后重新 render，warnings 要清零"}


# ---------- result charts: a planned figure (its rows x series filled by experiments) as a PDF/PNG for the paper ----------

def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _metric(design, name):
    return next((m for m in (design or {}).get("metrics") or [] if m["name"] == name), None)


def _metric_label(m):
    if not m:
        return ""
    return m["name"] + (f" ({m['unit']})" if m.get("unit") else "") + (" ↓" if m.get("better") == "lower" else " ↑")


def _figure_html(fig, vals, design):
    """The figure as a self-contained print page: inline Chart.js for graphs (the sandbox blocks CDN and file loads),
    a styled table for kind=table. Same shape as the on-page FigureCard in web/app/research.js."""
    kind, rows, series = fig["kind"], fig["rows"], fig["series"]
    w = 600 if fig.get("width") == "half" else 1200
    esc = lambda s: html_lib.escape(str(s))  # noqa: E731
    if kind == "table":
        col_metric = lambda s: _metric(design, fig.get("metric") or s)  # noqa: E731
        best = {}
        for s in series:
            m, ns = col_metric(s), [v for v in (_num(vals.get((r, s))) for r in rows) if v is not None]
            best[s] = (min if m and m.get("better") == "lower" else max)(ns) if m and len(ns) > 1 else None
        arrow = lambda m: (" ↓" if m.get("better") == "lower" else " ↑") if m else ""  # noqa: E731
        head = "".join(f"<th>{esc(s)}{'' if fig.get('metric') else esc(arrow(col_metric(s)))}</th>" for s in series)
        body = ""
        for r in rows:
            tds = "".join(
                f"<td{' class=best' if best[s] is not None and _num(vals.get((r, s))) == best[s] else ''}>{esc(vals[(r, s)])}</td>"
                if (r, s) in vals else "<td class=na>—</td>" for s in series)
            body += f"<tr{' class=ours' if design and r == design.get('method') else ''}><td>{esc(r)}</td>{tds}</tr>"
        title = esc(_metric_label(_metric(design, fig.get("metric"))))
        return f"""<!doctype html><meta charset="utf-8"><style>
  #figure {{ width: {w}px; background: #fff; padding: 24px; box-sizing: border-box;
           font: 18px "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1f2328; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #d0d4d9; padding: 8px 14px; text-align: left; }}
  th {{ background: #f1f3f4; font-weight: 600; }}
  td:not(:first-child) {{ font-variant-numeric: tabular-nums; }}
  .best {{ font-weight: 700; }} .na {{ color: #9aa0a6; }} .ours td {{ background: #f6f8fa; }}
</style><div id="figure"><table><thead><tr><th>{title}</th>{head}</tr></thead><tbody>{body}</tbody></table></div>"""
    h = min(520, 28 * len(rows) + 48) * (w // 600) if kind == "hbar" else round(w * (0.8 if kind == "radar" else 0.55))
    y_label = fig.get("y_title") or _metric_label(_metric(design, fig.get("metric")))
    datasets = [{"label": s, "data": [_num(vals.get((r, s))) for r in rows], "spanGaps": True} for s in series]
    t = fig.get("target")
    if t:
        datasets.append({**({"type": "line"} if kind in ("bar", "hbar") else {}), "label": t.get("label") or f"目标 {t['value']}",
                         "data": [t["value"]] * len(rows), "borderDash": [6, 4], "pointRadius": 0, "fill": False})
    axis = lambda t: {"title": {"display": bool(t), "text": t or ""}}  # noqa: E731
    options = {} if kind == "radar" else ({"indexAxis": "y", "scales": {"x": axis(y_label), "y": axis(fig.get("x_title"))}}
                                          if kind == "hbar" else {"scales": {"x": axis(fig.get("x_title")), "y": axis(y_label)}})
    config = {"type": "bar" if kind == "hbar" else kind, "data": {"labels": rows, "datasets": datasets},
              "options": {**options, "responsive": False, "animation": False, "devicePixelRatio": 3,
                          "plugins": {"legend": {"display": len(datasets) > 1}}}}
    config_js = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><meta charset="utf-8"><style>
  html, body {{ margin: 0 }} #figure {{ width: {w}px; height: {h}px; background: #fff; }}
</style><div id="figure"><canvas id="c" width="{w}" height="{h}"></canvas></div>
<script>{CHARTJS.read_text()}</script>
<script>
  Chart.defaults.font.family = '"Helvetica Neue", Helvetica, Arial, sans-serif';
  Chart.defaults.font.size = 18; Chart.defaults.color = '#3c4043';
  new Chart(document.getElementById('c'), {config_js});
</script>"""


def render_chart(rid, fid):
    """An experiment-page figure (bar/hbar/line/radar/table) with its filled cells → vector-page PDF + PNG, named chart-<fid>
    among the research's diagrams so the existing file routes serve it; kind 'chart' keeps it out of the diagram blocks."""
    x = store.get_research(rid)
    if not x:
        raise rules.Rejected("这个研究不存在或已删除")
    fg = store.get_figures(rid)
    fig = next((f for f in fg["figures"] if f["id"] == fid), None)
    if not fig:
        raise rules.Rejected("没有找到这张图表：id 见 ./p2b figures-plan show")
    exps = {e["id"]: e for e in x["experiments"]}
    vals = {}
    for c in fg["cells"]:
        if c["figure"] != fid or not c.get("experiment"):
            continue
        e = exps.get(c["experiment"])
        r = e and next((r for r in e.get("results") or [] if r["name"] == c["result"]), None)
        if r:
            vals[(c["row"], c["series"])] = r["value"]
    name = "chart-" + (re.sub(r"[^a-z0-9-]+", "-", str(fid).lower()).strip("-")[:30] or "fig")
    src = store.diagram_dir(rid, name).with_name(f".{name}.src.html")  # hidden temp; diagrams() only reads */meta.json
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(_figure_html(fig, vals, fg.get("design")))
    try:
        out = render(rid, name, src, title=fig["title"], caption=fig.get("caption"), kind="chart", extra={"figure": fid})
    finally:
        src.unlink(missing_ok=True)
    out.pop("next")
    return out
