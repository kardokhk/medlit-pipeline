"""Crossref REST API: the registration record behind every DOI.

Used three ways: as an independent denominator for a journal's output
(every DOI the publisher registered), as the reference for metadata
validation, and as the source of licence and text-mining links that the
publisher deposited.
"""
from __future__ import annotations

from ..identifiers import normalize_doi, normalize_issn

API = "https://api.crossref.org"


def list_journal_works(client, issns: list[str], date_from: str, date_to: str,
                       rows: int = 500) -> tuple[list[dict], dict]:
    filt = ",".join([f"issn:{i}" for i in issns] +
                    [f"from-pub-date:{date_from}", f"until-pub-date:{date_to}"])
    cursor, items, total, first_url = "*", [], None, None
    while True:
        r = client.get(f"{API}/works", {"filter": filt, "rows": rows, "cursor": cursor})
        msg = r.json()["message"]
        first_url = first_url or r.url
        total = msg["total-results"]
        batch = msg["items"]
        for it in batch:
            rec = parse_work(it)
            rec["api_url"] = f"{API}/works/{rec['doi']}"
            rec["retrieved_at"] = r.fetched_at
            items.append(rec)
        cursor = msg.get("next-cursor")
        if not batch or len(items) >= total or not cursor:
            break
    return items, {"source": "crossref", "query": filt, "count": total, "url": first_url,
                   "retrieved_at": r.fetched_at}


def get_work(client, doi: str) -> dict | None:
    r = client.get(f"{API}/works/{doi}")
    if r.status != 200:
        return None
    rec = parse_work(r.json()["message"])
    rec["api_url"], rec["retrieved_at"] = r.url, r.fetched_at
    return rec


def _date_parts(d: dict | None) -> dict:
    if not d or not d.get("date-parts") or not d["date-parts"][0] or d["date-parts"][0][0] is None:
        return {}
    p = list(d["date-parts"][0]) + [None, None]
    return {"year": p[0], "month": p[1], "day": p[2]}


def parse_work(it: dict) -> dict:
    refs = []
    for ref in it.get("reference", []) or []:
        refs.append({"key": ref.get("key"), "doi": normalize_doi(ref.get("DOI")),
                     "doi_asserted_by": ref.get("doi-asserted-by"),
                     "unstructured": ref.get("unstructured")})
    updates = []
    for u in (it.get("updated-by") or []):
        updates.append({"type": u.get("type"), "doi": normalize_doi(u.get("DOI")),
                        "source": u.get("source"), "direction": "updated-by"})
    for u in (it.get("update-to") or []):
        updates.append({"type": u.get("type"), "doi": normalize_doi(u.get("DOI")),
                        "source": u.get("source"), "direction": "update-to"})
    authors = [{"family": a.get("family"), "given": a.get("given"), "orcid": a.get("ORCID"),
                "collective": a.get("name")} for a in it.get("author", []) or []]
    return {
        "source": "crossref",
        "doi": normalize_doi(it.get("DOI")),
        "type": it.get("type"),
        "title": " ".join((it.get("title") or [""])[0].split()),
        "journal": (it.get("container-title") or [None])[0],
        "issns": sorted({normalize_issn(i) for i in it.get("ISSN", []) if normalize_issn(i)}),
        "publisher": it.get("publisher"),
        "member": it.get("member"),
        "volume": it.get("volume"),
        "issue": it.get("issue"),
        "pages": it.get("page") or it.get("article-number"),
        "page": it.get("page"),
        "article_number": it.get("article-number"),
        "published": _date_parts(it.get("published")),
        "published_print": _date_parts(it.get("published-print")),
        "published_online": _date_parts(it.get("published-online")),
        "authors": authors,
        "reference_count": it.get("reference-count"),
        "references": refs,
        "references_open": bool(refs),
        "licenses": [{"url": l.get("URL"), "content_version": l.get("content-version"),
                      "start": _date_parts(l.get("start")), "delay_days": l.get("delay-in-days")}
                     for l in it.get("license", []) or []],
        "tdm_links": [{"url": l.get("URL"), "content_type": l.get("content-type"),
                       "intended_application": l.get("intended-application")}
                      for l in it.get("link", []) or []],
        "updates": updates,
        "retracted": any(u["type"] in {"retraction", "withdrawal", "removal"} and u["direction"] == "updated-by"
                         for u in updates),
        "has_abstract": bool(it.get("abstract")),
        "clinical_trial_numbers": [c.get("clinical-trial-number") for c in it.get("clinical-trial-number", []) or []],
        "source_url": f"https://doi.org/{normalize_doi(it.get('DOI'))}",
    }
