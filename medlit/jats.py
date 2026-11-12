"""JATS XML to structured article data: sections, tables, figures, references,
supplementary materials, licence and the counts the publisher declared.

Tables are expanded to a rectangular grid (rowspan and colspan are filled in)
so they can be written as CSV without losing alignment.
"""
from __future__ import annotations

import re

from lxml import etree

from .identifiers import normalize_doi, normalize_pmcid, normalize_pmid

XLINK = "{http://www.w3.org/1999/xlink}href"
ALI = "{http://www.niso.org/schemas/ali/1.0/}license_ref"
_WS = re.compile(r"\s+")


def _t(el) -> str:
    if el is None:
        return ""
    return _WS.sub(" ", "".join(el.itertext())).strip()


def _t_cell(el) -> str:
    """Cell text with superscripts and subscripts marked (kg/m^2, PRS_AD, HbA_1c),
    so units and indices are not run together with the text."""
    if el is None:
        return ""
    if el.find(".//sup") is None and el.find(".//sub") is None:
        return _t(el)
    clone = etree.fromstring(etree.tostring(el))
    for tag, mark in (("sup", "^"), ("sub", "_")):
        for x in clone.iter(tag):
            if _t(x):
                x.text = mark + (x.text or "")
    return _t(clone)


def _t_fields(el) -> str:
    """Text of a structured citation, with a space between its fields."""
    if el is None:
        return ""
    return _WS.sub(" ", " ".join(t.strip() for t in el.itertext() if t.strip())).strip()


def _strip_ns(root) -> None:
    """Drop namespaces except xlink/ali attributes, which are read by full name."""
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{") and "mathml" not in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def parse(xml: bytes) -> dict:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.fromstring(xml, parser)
    if root.tag != "article":
        found = root.find(".//article")
        if found is None:
            raise ValueError("no <article> element")
        root = found
    ali_refs = [el.text.strip() for el in root.iter(ALI) if el.text]
    _strip_ns(root)
    front = root.find("front")
    meta = front.find("article-meta") if front is not None else None
    jmeta = front.find("journal-meta") if front is not None else None
    body = root.find("body")
    back = root.find("back")

    ids = {}
    if meta is not None:
        for aid in meta.iterfind("article-id"):
            ids[aid.get("pub-id-type")] = (aid.text or "").strip()
    lic = meta.find("permissions/license") if meta is not None else None
    license_info = {
        "type": lic.get("license-type") if lic is not None else None,
        "url": (lic.get(XLINK) if lic is not None else None) or (ali_refs[0] if ali_refs else None),
        "text": _t(lic)[:500] if lic is not None else None,
    }
    counts = {}
    cnt = meta.find("counts") if meta is not None else None
    if cnt is not None:
        for c in cnt:
            if isinstance(c.tag, str) and c.get("count", "").isdigit():
                counts[c.tag] = int(c.get("count"))

    sections = []
    if body is not None:
        _walk_sections(body, [], sections)
    if back is not None:
        for app in back.iter("app"):
            _walk_sections(app, [_t(app.find("title")) or "Appendix"], sections)

    tables = [parse_table(tw, i) for i, tw in enumerate(root.iter("table-wrap"), 1)]
    figures = [{"id": f.get("id"), "label": _t(f.find("label")), "caption": _t(f.find("caption")),
                "graphics": [g.get(XLINK) for g in f.iter("graphic") if g.get(XLINK)]}
               for f in root.iter("fig")]
    supplements = []
    for s in root.iter("supplementary-material"):
        hrefs = [s.get(XLINK)] + [m.get(XLINK) for m in s.iter("media", "inline-supplementary-material")]
        hrefs = [h for h in dict.fromkeys(hrefs) if h]
        supplements.append({"id": s.get("id"), "label": _t(s.find("label")),
                            "caption": _t(s.find("caption"))[:1000], "hrefs": hrefs,
                            "mimetype": s.get("mimetype")})
    references = [parse_ref(r) for r in root.iter("ref")] if back is not None else []
    external = external_supplements(root)

    abstract = ""
    if meta is not None:
        abs_el = next((a for a in meta.iterfind("abstract") if a.get("abstract-type") in (None, "", "structured")), None)
        abstract = _t(abs_el)
    body_words = sum(s["n_words"] for s in sections)
    return {
        "article_type": root.get("article-type"),
        "doi": normalize_doi(ids.get("doi")),
        "pmid": normalize_pmid(ids.get("pmid")),
        "pmcid": normalize_pmcid(ids.get("pmcid") or ids.get("pmc")),
        "title": _t(meta.find("title-group/article-title")) if meta is not None else "",
        "journal": _t(jmeta.find(".//journal-title")) if jmeta is not None else "",
        "abstract": abstract,
        "license": license_info,
        "declared_counts": counts,
        "sections": sections,
        "body_words": body_words,
        "tables": tables,
        "figures": figures,
        "supplements": supplements,
        "references": references,
        "external_supplements": external,
        "keywords": [_t(k) for k in meta.iter("kwd")] if meta is not None else [],
        "funding": [_t(f) for f in meta.iter("funding-source")] if meta is not None else [],
        "data_availability": next((_t(s) for s in root.iter("sec") if s.get("sec-type") == "data-availability"), None),
    }


REPOSITORIES = (
    ("figshare", re.compile(r"(10\.\d{4,9}/(?:m9\.)?figshare\.(\d+))", re.I)),
    ("zenodo", re.compile(r"(10\.5281/zenodo\.(\d+))", re.I)),
    ("dryad", re.compile(r"(10\.5061/dryad\.[a-z0-9]+)", re.I)),
    ("osf", re.compile(r"(?:osf\.io/([a-z0-9]{5}))", re.I)),
)


def external_supplements(root) -> list[dict]:
    """Supplementary material deposited in a data repository and linked from the
    text (common for ADA, PLOS and Wiley journals), outside the article package.
    Links inside the reference list are citations, not supplements, and are skipped."""
    out, seen = [], set()
    for el in root.iter("ext-link", "uri", "self-uri"):
        anc = el.getparent()
        in_refs = False
        while anc is not None:
            if anc.tag in ("ref-list", "ref"):
                in_refs = True
                break
            anc = anc.getparent()
        if in_refs:
            continue
        href = (el.get(XLINK) or el.text or "").strip()
        for repo, rx in REPOSITORIES:
            m = rx.search(href)
            if m:
                key = m.group(1).lower()
                if key not in seen:
                    seen.add(key)
                    # OSF links are usually study registrations or project pages, not supplement files
                    kind = "linked project" if repo == "osf" else "supplement"
                    out.append({"repository": repo, "kind": kind, "id": key, "href": href, "label": _t(el)[:200]})
                break
    return out


def _walk_sections(el, path, out) -> None:
    """Emit text blocks in reading order: a section's own paragraphs before its
    subsections, and paragraphs that follow a subsection as a separate block."""
    buf: list[str] = []

    def flush():
        if buf:
            text = "\n\n".join(buf)
            out.append({"path": " > ".join(path) if path else "(body)", "sec_type": el.get("sec-type"),
                        "text": text, "n_words": len(text.split())})
            buf.clear()

    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.tag == "sec":
            flush()
            title = _t(child.find("title")) or "(untitled)"
            _walk_sections(child, path + [title], out)
        elif child.tag in ("p", "list", "disp-quote", "boxed-text", "def-list", "statement"):
            # tables and figures inside a paragraph are captured separately
            clone = etree.fromstring(etree.tostring(child))
            for drop in list(clone.iter("table-wrap", "fig")):
                drop.getparent().remove(drop)
            txt = _t(clone)
            if txt:
                buf.append(txt)
    flush()


def parse_table(tw, index: int) -> dict:
    tbls = list(tw.iter("table"))
    grid, header_rows = [], 0
    if tbls:
        grid, header_rows = table_grid(tbls[0])
    widths = {len(r) for r in grid}
    return {
        "id": tw.get("id") or f"table{index}",
        "label": _t(tw.find("label")),
        "caption": _t(tw.find("caption")),
        "footnotes": _t(tw.find("table-wrap-foot")),
        "has_markup": bool(tbls),
        "image_only": not tbls and tw.find(".//graphic") is not None,
        "n_markup_tables": len(tbls),
        "header_rows": header_rows,
        "n_rows": len(grid),
        "n_cols": max(widths) if widths else 0,
        "rectangular": len(widths) <= 1,
        "grid": grid,
    }


def table_grid(table) -> tuple[list[list[str]], int]:
    """Expand a JATS/XHTML table into a rectangular list of rows.

    A cell with rowspan=n/colspan=m is repeated into every slot it covers, so
    each CSV column keeps its meaning. Returns (grid, number of header rows).
    """
    rows, header_rows = [], 0
    for section in ("thead", "tbody", "tfoot", None):
        if section:
            trs = [tr for sec in table.iterfind(section) for tr in sec.iterfind("tr")]
        else:
            trs = list(table.iterfind("tr"))
        if section == "thead":
            header_rows = len(trs)
        rows.extend(trs)
    if header_rows == 0 and rows and all(c.tag == "th" for c in rows[0] if isinstance(c.tag, str)):
        header_rows = 1
    grid: list[list[str | None]] = []
    pending: dict[tuple[int, int], str] = {}  # (row, col) -> text carried down by rowspan
    single_span: set[int] = set()  # body rows made of one cell spanning several columns
    for r, tr in enumerate(rows):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
        if r >= header_rows and len(cells) == 1 and _span(cells[0].get("colspan")) > 1:
            single_span.add(r)
        row: list[str | None] = []
        col = 0

        def fill_pending():
            nonlocal col
            while (r, col) in pending:
                row.append(pending.pop((r, col)))
                col += 1

        fill_pending()
        for cell in tr:
            if not isinstance(cell.tag, str) or cell.tag not in ("td", "th"):
                continue
            txt = _t_cell(cell)
            cs = _span(cell.get("colspan"))
            rs = _span(cell.get("rowspan"))
            for _ in range(cs):
                row.append(txt)
                for k in range(1, rs):
                    pending[(r + k, col)] = txt
                col += 1
                fill_pending()
        fill_pending()
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    # a body row that is one cell spanning the table is a group heading
    # ("Diagnosis", "Patient age"): keep it in the first column only
    for r in single_span:
        grid[r] = [grid[r][0]] + [""] * (len(grid[r]) - 1)
    # pad short rows (malformed source tables) so the CSV stays rectangular;
    # the parser reports this via 'rectangular' before padding
    rect = [list(r) + [""] * (width - len(r)) for r in grid]
    return rect, header_rows


def _span(v) -> int:
    try:
        n = int(str(v).strip())
        return n if 0 < n < 1000 else 1
    except (TypeError, ValueError):
        return 1


def parse_ref(ref) -> dict:
    cit = None
    for tag in ("element-citation", "mixed-citation", "citation", "nlm-citation"):
        cit = ref.find(f".//{tag}")
        if cit is not None:
            break
    src = cit if cit is not None else ref
    pub_ids = {p.get("pub-id-type"): (p.text or "").strip() for p in src.iter("pub-id")}
    raw = _t_fields(src) if src.tag == "element-citation" else _t(src)
    doi = normalize_doi(pub_ids.get("doi"))
    if not doi:
        for ext in src.iter("ext-link"):
            cand = normalize_doi(ext.get(XLINK) or ext.text)
            if cand:
                doi = cand
                break
    if not doi:
        m = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+)", raw)
        if m:
            doi = normalize_doi(m.group(1))
    first = src.find(".//name/surname")
    if first is None:
        first = src.find(".//string-name/surname")
    year = src.findtext(".//year")
    return {
        "id": ref.get("id"),
        "label": _t(ref.find("label")),
        "publication_type": cit.get("publication-type") if cit is not None else None,
        "first_author": _t(first) or (_t(src.find(".//collab")) or None),
        "year": (year or "").strip()[:4] or None,
        "title": _t(src.find(".//article-title")) or _t(src.find(".//chapter-title")) or None,
        "source": _t(src.find(".//source")) or None,
        "volume": _t(src.find(".//volume")) or None,
        "fpage": _t(src.find(".//fpage")) or _t(src.find(".//elocation-id")) or None,
        "doi": doi,
        "pmid": normalize_pmid(pub_ids.get("pmid")),
        "pmcid": normalize_pmcid(pub_ids.get("pmcid")),
        "raw": raw[:2000],
    }
