"""Writing drafts out to Overleaf: Markdown -> LaTeX with \\cite{}, plus the BibTeX of every cited paper."""
import re

from . import rules, store

_CITE = re.compile(r"\[((?:arxiv|doi|openreview|pdf):[^\s\]]+?)(?:\s*p\.?\s*\d+)?\]((?:\s*\[(?:arxiv|doi|openreview|pdf):[^\]]+\])*)", re.I)
_STOP = {"a", "an", "the", "on", "of", "for", "towards", "toward", "with", "via", "and", "in", "to"}


def bib_key(p):
    last = re.sub(r"[^a-z]", "", ((p.get("authors") or ["anon"])[0].split() or ["anon"])[-1].lower()) or "anon"
    year = (p.get("published") or "")[:4]
    word = next((w for w in re.findall(r"[a-z][a-z0-9]{2,}", p["title"].lower()) if w not in _STOP), "paper")
    return f"{last}{year}{word}"


def _tex(s):
    return re.sub(r"([%&#_$])", r"\\\1", s)


def bibtex(p, key):
    kind, _, ref = p["id"].partition(":")
    fields = [f"  title = {{{_tex(p['title'])}}}", f"  author = {{{' and '.join(p.get('authors') or [])}}}"]
    if p.get("published"):
        fields.append(f"  year = {{{p['published'][:4]}}}")
    if kind == "arxiv":
        fields += [f"  eprint = {{{ref}}}", "  archivePrefix = {arXiv}", f"  journal = {{arXiv preprint arXiv:{ref}}}"]
    elif kind == "doi":
        fields.append(f"  doi = {{{ref}}}")
    if p.get("url"):
        fields.append(f"  url = {{{p['url']}}}")
    return f"@article{{{key},\n" + ",\n".join(fields) + "\n}"


def _inline(s):
    s = _tex(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"(?<![\w*])\*([^*]+)\*(?!\w)", r"\\emph{\1}", s)
    return re.sub(r"`([^`]+)`", r"\\texttt{\1}", s)


def to_latex(md, keys):
    """Paragraphs, bullet and numbered lists, bold / italic / code, and citations; enough for an abstract and intro."""
    def cite(m):
        ids = [m.group(1)] + re.findall(r"\[((?:arxiv|doi|openreview|pdf):[^\s\]]+?)(?:\s*p\.?\s*\d+)?\]", m.group(2) or "")
        found = [keys[pid] for pid in (rules.parse_pid(i) for i in ids) if pid in keys]
        return f"\x00CITE{','.join(found)}\x00" if found else m.group(0)

    md = _CITE.sub(cite, md or "")
    out, env = [], None
    for line in md.splitlines():
        m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", line)
        want = ("enumerate" if m and m.group(1)[0].isdigit() else "itemize") if m else None
        if env and want != env:
            out.append(f"\\end{{{env}}}")
            env = None
        if m:
            if not env:
                out.append(f"\\begin{{{want}}}")
                env = want
            out.append(f"  \\item {_inline(m.group(2))}")
        elif re.match(r"^#{1,4}\s", line):
            heading = re.sub(r"^#+\s*", "", line)  # no backslash inside f-string expressions before Python 3.12
            out.append(f"\\paragraph{{{_inline(heading)}}}")
        else:
            out.append(_inline(line))
    if env:
        out.append(f"\\end{{{env}}}")
    return re.sub(r"[ \t]*\x00CITE([^\x00]*)\x00", r"~\\cite{\1}", "\n".join(out)).strip()


def research_export(rid):
    w = store.get_writing(rid)
    related = [g for g in store.get_related(rid)["groups"] if g["text"]]
    text = "\n".join(filter(None, [w["abstract"], w["introduction"], *(g["text"] for g in related)]))
    cited = []
    for m in re.finditer(r"\[((?:arxiv|doi|openreview|pdf):[^\s\]]+?)(?:\s*p\.?\s*\d+)?\]", text, re.I):
        pid = rules.parse_pid(m.group(1))
        if pid and pid not in cited:
            cited.append(pid)
    papers = [p for p in (store.get_paper(pid) for pid in cited) if p]
    keys, used = {}, set()
    for p in papers:
        k, n = bib_key(p), 1
        while k in used:
            n += 1
            k = f"{bib_key(p)}{chr(96 + n)}"
        keys[p["id"]] = k
        used.add(k)
    parts = []
    if w["abstract"]:
        parts.append("\\begin{abstract}\n" + to_latex(w["abstract"], keys) + "\n\\end{abstract}")
    if w["introduction"]:
        parts.append("\\section{Introduction}\n" + to_latex(w["introduction"], keys))
    if related:
        parts.append("\\section{Related Work}\n" + "\n\n".join(f"\\paragraph{{{_inline(g['name'])}}}\n{to_latex(g['text'], keys)}" for g in related))
    return {"latex": "\n\n".join(parts), "bib": "\n\n".join(bibtex(p, keys[p["id"]]) for p in papers)}
