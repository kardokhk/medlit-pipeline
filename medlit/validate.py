"""Metadata validation across sources and completeness checks on full texts.

Every check emits a flag {code, severity, detail}. Severity:
  error   the record or file is wrong or incomplete and must not be used as is
  warning the sources disagree or a component is degraded; review
  info    expected differences worth knowing (for example online-first dates)
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .identifiers import normalize_title


def flag(code: str, severity: str, detail: str = "") -> dict:
    return {"code": code, "severity": severity, "detail": detail}


def _norm_simple(v) -> str:
    return re.sub(r"[^0-9a-z]", "", str(v or "").lower())


def _issue(v) -> str:
    """'Supplement_1', 'Suppl 1', '1 Suppl 1' -> 's1'; plain issues keep their number."""
    s = str(v or "").lower()
    m = re.search(r"suppl(?:ement)?[ _.-]*(\d+)?", s)
    if m:
        return "s" + (m.group(1) or "")
    return _norm_simple(s)


def _first_page(v) -> str:
    s = str(v or "").strip()
    return _norm_simple(re.split(r"[-–,]", s)[0]) if s else ""


def _family(authors: list[dict]) -> str:
    for a in authors or []:
        name = a.get("family") or a.get("collective")
        if name:
            return normalize_title(name)
    return ""


def metadata_checks(cl: dict) -> list[dict]:
    out = []
    if cl["id_conflict"]:
        out.append(flag("ID_CONFLICT", "error",
                        f"DOIs {cl['dois']} PMIDs {cl['pmids']} PMCIDs {cl['pmcids']}"))
    for src, n in cl["within_source_duplicates"].items():
        kinds = sorted({r.get("epmc_source") or src for r in cl["by_source"][src]})
        out.append(flag("DUPLICATE_IN_SOURCE", "info", f"{n} records in {src} ({', '.join(kinds)}); merged"))
    if cl["linked_by_title"]:
        out.append(flag("LINKED_BY_TITLE", "info", "no shared identifier; linked on title, year and journal"))
    cr = (cl["by_source"].get("crossref") or [None])[0]
    pm = (cl["by_source"].get("pubmed") or [None])[0]
    ep = next((r for r in cl["by_source"].get("europepmc", []) if r.get("epmc_source") == "MED"),
              (cl["by_source"].get("europepmc") or [None])[0])
    if cr and pm:
        sim = fuzz.ratio(normalize_title(cr["title"]), normalize_title(pm["title"]))
        if sim < 90:
            out.append(flag("TITLE_MISMATCH", "warning",
                            f"similarity {sim:.0f}; Crossref: {cr['title'][:120]!r}; PubMed: {pm['title'][:120]!r}"))
        cy = (cr.get("published") or {}).get("year")
        py = (pm.get("pub_date") or {}).get("year")
        if cy and py and cy != py:
            out.append(flag("YEAR_DIFFERS", "info" if abs(cy - py) == 1 else "warning",
                            f"Crossref first published {cy}; PubMed issue date {py}"))
        for field, fn in (("volume", _norm_simple), ("issue", _issue), ("pages", _first_page)):
            a, b = fn(cr.get("page" if field == "pages" else field)), fn(pm.get(field))
            if a and b and a != b:
                out.append(flag(f"{field.upper()}_MISMATCH", "warning",
                                f"Crossref {cr.get(field)!r}; PubMed {pm.get(field)!r}"))
        fa, fb = _family(cr.get("authors")), _family(pm.get("authors"))
        if fa and fb and fa != fb and fuzz.ratio(fa, fb) < 85:
            out.append(flag("FIRST_AUTHOR_MISMATCH", "warning", f"Crossref {fa!r}; PubMed {fb!r}"))
        na, nb = len(cr.get("authors") or []), len(pm.get("authors") or [])
        if na and nb and na != nb:
            out.append(flag("AUTHOR_COUNT_DIFFERS", "info", f"Crossref {na}; PubMed {nb}"))
        if cr.get("issns") and pm.get("issns") and not set(cr["issns"]) & set(pm["issns"]):
            out.append(flag("ISSN_MISMATCH", "warning", f"Crossref {cr['issns']}; PubMed {pm['issns']}"))
    if ep and pm and ep.get("doi") and pm.get("doi") and ep["doi"] != pm["doi"]:
        out.append(flag("DOI_DIFFERS_EPMC_PUBMED", "warning", f"{ep['doi']} vs {pm['doi']}"))
    retracted_by = [r["source"] for r in cl["members"] if r.get("retracted")]
    if retracted_by:
        out.append(flag("RETRACTED", "error", "retraction recorded by " + ", ".join(sorted(set(retracted_by)))))
    if pm and any(c["type"] == "ErratumIn" for c in pm.get("corrections", [])):
        out.append(flag("HAS_CORRECTION", "info", "PubMed lists an erratum"))
    elif cr and any(u["type"] in ("correction", "erratum", "corrigendum") and u["direction"] == "updated-by"
                    for u in cr.get("updates", [])):
        out.append(flag("HAS_CORRECTION", "info", "Crossref lists a correction"))
    return out


def fulltext_checks(art: dict, files: dict, canonical: dict, crossref: dict | None,
                    route: str | None = None) -> list[dict]:
    """Checks on one retrieved full text. `files` maps role -> list of download results."""
    out = []
    for role, items in files.items():
        for f in items:
            if f.get("md5_ok") is False:
                out.append(flag("CHECKSUM_MISMATCH", "error", f"{role}: {f['name']}"))
            if f.get("error"):
                out.append(flag("DOWNLOAD_FAILED", "error", f"{role}: {f['name']}: {f['error']}"))
    for f in files.get("pdf", []):
        if f.get("magic") and f["magic"] != "pdf":
            out.append(flag("PDF_NOT_PDF", "error", f"{f['name']} starts like {f['magic']}"))
    if art.get("doi") and canonical.get("doi") and art["doi"] != canonical["doi"]:
        out.append(flag("FULLTEXT_DOI_MISMATCH", "error", f"XML {art['doi']} vs record {canonical['doi']}"))
    if art.get("title") and canonical.get("title"):
        sim = fuzz.ratio(normalize_title(art["title"]), normalize_title(canonical["title"]))
        if sim < 85:
            out.append(flag("FULLTEXT_TITLE_MISMATCH", "warning", f"similarity {sim:.0f}"))
    research = art.get("article_type") in ("research-article", "review-article", "systematic-review", "brief-report")
    if not art.get("sections"):
        out.append(flag("NO_BODY_TEXT", "error" if research else "info", f"article-type {art.get('article_type')}"))
    elif research and art["body_words"] < 1000:
        out.append(flag("SHORT_BODY_TEXT", "warning", f"{art['body_words']} words"))
    if research and not art.get("abstract"):
        out.append(flag("NO_ABSTRACT", "warning"))
    dc = art.get("declared_counts", {})
    n_tables = len(art["tables"])
    if "table-count" in dc and dc["table-count"] != n_tables:
        out.append(flag("TABLE_COUNT_MISMATCH", "warning", f"declared {dc['table-count']}, found {n_tables}"))
    for t in art["tables"]:
        if t["image_only"]:
            out.append(flag("TABLE_IMAGE_ONLY", "warning", f"{t['label'] or t['id']}: image without cell markup"))
        elif t["has_markup"] and not t["rectangular"]:
            out.append(flag("TABLE_RAGGED", "warning", f"{t['label'] or t['id']}: rows of unequal width in source"))
    n_refs = len(art["references"])
    if "ref-count" in dc and dc["ref-count"] != n_refs:
        out.append(flag("REF_COUNT_MISMATCH_DECLARED", "warning", f"declared {dc['ref-count']}, found {n_refs}"))
    if crossref and crossref.get("reference_count") and crossref["references_open"]:
        if abs(crossref["reference_count"] - n_refs) > max(2, 0.05 * n_refs):
            out.append(flag("REF_COUNT_MISMATCH_CROSSREF", "warning",
                            f"Crossref {crossref['reference_count']}, full text {n_refs}"))
    for f in files.get("supplement_external", []):
        if f.get("skipped") and str(f.get("reason", "")).startswith("project page"):
            out.append(flag("LINKED_PROJECT_PAGE", "info", f"{f['url']}: {f['reason']}"))
        elif f.get("error") or f.get("skipped"):
            out.append(flag("EXTERNAL_SUPPLEMENT_NOT_RETRIEVED", "warning",
                            f"{f['name']}: {f.get('error') or f.get('reason')}"))
    got = {f["name"] for f in files.get("supplement", []) if not f.get("error")}
    for s in art["supplements"]:
        if not any(_href_matches(h, got) for h in s["hrefs"]):
            if route == "pmc_author_manuscript":
                # PMC does not distribute supplements of author manuscripts in the datasets
                out.append(flag("SUPPLEMENT_NOT_DISTRIBUTED", "warning",
                                f"{s['label'] or s['id']}: author manuscript; fetch from the publisher page"))
            else:
                out.append(flag("SUPPLEMENT_NOT_RETRIEVED", "error",
                                f"{s['label'] or s['id']}: {', '.join(s['hrefs']) or 'no file reference'}"))
    return out


def open_copy_checks(open_copy: dict | None) -> list[dict]:
    if not open_copy:
        return []
    v = open_copy.get("version")
    if v != "publishedVersion":
        return [flag("OPEN_COPY_NOT_VERSION_OF_RECORD", "warning",
                     f"{v or 'unknown version'} at {open_copy['url'][:120]}")]
    return []


def _href_matches(href: str, names: set[str]) -> bool:
    base = href.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    return base in names or any(n.rsplit(".", 1)[0] == stem for n in names)


def reference_agreement(art_refs: list[dict], cr_refs: list[dict]) -> dict | None:
    """DOI-level agreement between references parsed from the full text and the
    list the publisher deposited with Crossref (an independent copy)."""
    a = {r["doi"] for r in art_refs if r.get("doi")}
    b = {r["doi"] for r in cr_refs if r.get("doi")}
    if not a or not b:
        return None
    inter = len(a & b)
    return {"fulltext_dois": len(a), "crossref_dois": len(b), "shared": inter,
            "recall_vs_crossref": inter / len(b), "precision_vs_crossref": inter / len(a)}
