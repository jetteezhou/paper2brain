"""What a researcher navigates a PDF by: its figures (cropped to images) and its section outline. Rule-based, no model.
Cached per paper in structure.json next to the PDF; figures/<n>.png hold the crops."""
import json
import re

import pymupdf

from . import store

VERSION = 1
CAPTION = re.compile(r"^\s*(?:Figure|Fig\.)\s*(\d+)\s*[:.|]", re.I)
_TABLE = re.compile(r"^\s*Table\s*\d+", re.I)
_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^\n]{2,80})$")
_NAMED = re.compile(r"^(Abstract|Introduction|Related Work|Background|Method|Methods|Approach|Experiments?|Results|"
                    r"Discussion|Limitations?|Conclusions?|References|Appendix)$", re.I)
KEY = {
    "method": (r"method|approach|model|framework|architecture|design|overview|formulation|tokeniz|policy|pipeline|system",
               r"related|prelim|background|introduction|case study|experiment|evaluation|result|conclusion|appendix|limitation|data"),
    "experiments": (r"experiment|evaluation|results", r"appendix"),
    "conclusion": (r"limitation|conclusion|discussion", r"appendix"),
}
KEY_FIGURE = re.compile(r"\b(?:overview|architecture|framework|pipeline|method|approach)\b", re.I)


def _figures(page):
    """(n, caption, box) for each "Figure N:" caption on the page. The figure is the graphics between the caption
    and the body text above it, in the caption's column (or across the page when the caption spans both)."""
    area = page.rect
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    graphics = [pymupdf.Rect(d["rect"]) for d in page.get_drawings()] + [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    graphics = [r for r in graphics if r.get_area() < 0.9 * area.get_area() and (r.width > 1 or r.height > 1)]
    mid, out = area.width / 2, []
    for b in blocks:
        m = CAPTION.match(b[4])
        if not m:
            continue
        cap = pymupdf.Rect(b[:4])
        wide = cap.x0 < mid - 30 and cap.x1 > mid + 30
        x0, x1 = (0, area.width) if wide else (0, mid) if cap.x1 <= mid + 30 else (mid, area.width)
        inside = (lambda r: True) if wide else (lambda r: x0 <= (r.x0 + r.x1) / 2 <= x1)
        top = 0
        for o in blocks:
            r = pymupdf.Rect(o[:4])
            if o is b or r.y1 > cap.y0 - 1 or not inside(r):
                continue
            paragraph = len(o[4]) > 250 or (o[4].count("\n") >= 3 and len(o[4]) > 150 and r.width > 0.6 * (x1 - x0))
            if paragraph or CAPTION.match(o[4]) or _TABLE.match(o[4]):
                top = max(top, r.y1)
        band = pymupdf.Rect(x0, top, x1, cap.y0)
        within = lambda r: r.intersects(band) and r.y0 >= top - 2 and r.y1 <= cap.y0 + 2 and inside(r)  # noqa: E731
        found = [r for r in graphics if within(r)]
        if not found:
            continue
        box = pymupdf.Rect(found[0])
        for r in found[1:] + [pymupdf.Rect(o[:4]) for o in blocks if o is not b and within(pymupdf.Rect(o[:4]))]:
            box |= r
        box = (box + (-4, -4, 4, 4)) & (area if wide else pymupdf.Rect(x0 - 10, 0, x1 + 10, area.height))
        if box.width >= 60 and box.height >= 40:
            out.append((int(m.group(1)), re.sub(r"\s+", " ", b[4]).strip(), box))
    return out


def _outline(doc):
    """The PDF's bookmarks when it has them (LaTeX papers usually do), else numbered bold or large headings."""
    toc = [{"level": lv, "title": re.sub(r"\s+", " ", t).strip(), "page": pg} for lv, t, pg in doc.get_toc() if pg >= 1]
    if toc:
        return toc[:80]
    sizes = {}
    lines = []
    for n, page in enumerate(doc, 1):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                for s in spans:
                    sizes[round(s["size"])] = sizes.get(round(s["size"]), 0) + len(s["text"])
                lines.append((n, " ".join(s["text"].strip() for s in spans), spans))
    body = max(sizes, key=sizes.get) if sizes else 10
    out = []
    for n, text, spans in lines:
        strong = all(s["flags"] & 16 for s in spans) or min(s["size"] for s in spans) >= body + 0.8
        m = _HEADING.match(text)
        if strong and (m or _NAMED.match(text)) and len(text) <= 80:
            out.append({"level": m.group(1).count(".") + 1 if m else 1, "title": text, "page": n})
    return out[:80]


def _key_sections(outline):
    top = [s for s in outline if s["level"] == 1] or outline
    out = {}
    for key, (want, avoid) in KEY.items():
        hit = next((s for s in top if re.search(want, s["title"], re.I) and not re.search(avoid, s["title"], re.I)), None)
        if hit:
            out[key] = hit
    return out


def extract(pid):
    """Crop every captioned figure to a PNG and read the outline. Returns the structure (also cached)."""
    p = store.get_paper(pid, with_anchors=False)
    folder = store.paper_dir(pid)
    out = {"version": VERSION, "figures": [], "outline": [], "key": {}}
    if p and p["pdf_path"]:
        figdir = folder / "figures"
        figdir.mkdir(exist_ok=True)
        with pymupdf.open(p["pdf_path"]) as doc:
            seen = set()
            for number, page in enumerate(doc, 1):
                for n, caption, box in _figures(page):
                    if n in seen:
                        continue
                    seen.add(n)
                    pix = page.get_pixmap(clip=box, dpi=150)
                    pix.save(figdir / f"{n}.png")
                    out["figures"].append({"n": n, "page": number, "caption": caption[:600], "w": pix.width, "h": pix.height})
            out["outline"] = _outline(doc)
        out["key"] = _key_sections(out["outline"])
    (folder / "structure.json").write_text(json.dumps(out, ensure_ascii=False))
    return out


def get(pid):
    """The cached structure, extracted on first use."""
    path = store.paper_dir(pid) / "structure.json"
    if path.exists():
        s = json.loads(path.read_text())
        if s.get("version") == VERSION:
            return s
    return extract(pid)


def key_figures(structure, card=None):
    """The figures worth showing on the quick card: the agent's picks when the card has them, else the teaser plus
    the figures whose caption opens like an overview / architecture / method figure."""
    figs = {f["n"]: f for f in structure.get("figures", [])}
    picked = [{**figs[x["n"]], "note": x.get("note")} for x in (card or {}).get("figures") or [] if x.get("n") in figs]
    if picked:
        return picked
    lead = lambda f: re.split(r"\.\s", CAPTION.sub("", f["caption"]), maxsplit=1)[0]  # noqa: E731
    auto = [f for f in figs.values() if f["n"] == 1 or KEY_FIGURE.search(lead(f))]
    return sorted(auto, key=lambda f: f["n"])[:4]


def backfill(limit=3):
    """Extract a few papers that have a PDF but no current structure yet (existing libraries, background loop)."""
    done = 0
    for p in store.list_papers():
        path = store.paper_dir(p["id"]) / "structure.json"
        current = path.exists() and json.loads(path.read_text()).get("version") == VERSION
        if not current and store.get_paper(p["id"], with_anchors=False)["pdf_path"]:
            extract(p["id"])
            done += 1
            if done >= limit:
                break
    return done
