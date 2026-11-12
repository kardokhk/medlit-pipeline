"""Europe PMC REST API (search with cursor paging, full-text XML fallback)."""
from __future__ import annotations

from ..identifiers import normalize_doi, normalize_issn, normalize_pmcid, normalize_pmid

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def journal_query(issns: list[str], year_from: int, year_to: int) -> str:
    j = " OR ".join(f'ISSN:"{i}"' for i in issns)
    return f"({j}) AND PUB_YEAR:[{year_from} TO {year_to}]"


def search(client, query: str, page_size: int = 250) -> tuple[list[dict], dict]:
    cursor, out, total, first_url = "*", [], None, None
    while True:
        r = client.get(f"{API}/search", {"query": query, "format": "json", "resultType": "core",
                                         "pageSize": page_size, "cursorMark": cursor})
        js = r.json()
        first_url = first_url or r.url
        total = js["hitCount"]
        res = js.get("resultList", {}).get("result", [])
        for it in res:
            rec = parse_result(it)
            rec["api_url"], rec["retrieved_at"] = r.url, r.fetched_at
            out.append(rec)
        nxt = js.get("nextCursorMark")
        if not res or nxt == cursor or len(out) >= total:
            break
        cursor = nxt
    return out, {"source": "europepmc", "query": query, "count": total, "url": first_url,
                 "retrieved_at": r.fetched_at}


def parse_result(it: dict) -> dict:
    ji = it.get("journalInfo") or {}
    jj = ji.get("journal") or {}
    src, eid = it.get("source"), it.get("id")
    return {
        "source": "europepmc",
        "epmc_id": f"{src}:{eid}",
        "epmc_source": src,  # MED = PubMed, PMC, PPR = preprint, AGR, CBA, ...
        "pmid": normalize_pmid(it.get("pmid")),
        "pmcid": normalize_pmcid(it.get("pmcid")),
        "doi": normalize_doi(it.get("doi")),
        "title": " ".join((it.get("title") or "").split()),
        "journal": jj.get("title"),
        "issns": sorted({i for i in (normalize_issn(jj.get("issn")), normalize_issn(jj.get("essn"))) if i}),
        "volume": ji.get("volume"),
        "issue": ji.get("issue"),
        "pages": (it.get("pageInfo")),
        "pub_year": int(it["pubYear"]) if str(it.get("pubYear", "")).isdigit() else None,
        "first_publication_date": it.get("firstPublicationDate"),
        "is_open_access": it.get("isOpenAccess") == "Y",
        "in_pmc": it.get("inPMC") == "Y",
        "has_pdf": it.get("hasPDF") == "Y",
        "has_suppl": it.get("hasSuppl") == "Y",
        "license": it.get("license"),
        "author_manuscript": it.get("authMan") == "Y" or it.get("nihAuthMan") == "Y" or it.get("epmcAuthMan") == "Y",
        "publication_types": (it.get("pubTypeList") or {}).get("pubType", []),
        "full_text_urls": [{"url": u.get("url"), "availability": u.get("availability"),
                            "style": u.get("documentStyle"), "site": u.get("site")}
                           for u in (it.get("fullTextUrlList") or {}).get("fullTextUrl", [])],
        "source_url": f"https://europepmc.org/article/{src}/{eid}",
    }


def fulltext_xml(client, pmcid: str) -> bytes | None:
    r = client.get(f"{API}/{pmcid}/fullTextXML")
    return r.body if r.status == 200 and r.body.lstrip().startswith(b"<") else None


def lookup(client, ids: list[tuple[str, str]], chunk: int = 50) -> list[dict]:
    """Find records by identifier: ids are ("pmid", value) or ("doi", value) pairs."""
    out = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        terms = [f'(EXT_ID:{v} AND SRC:MED)' if k == "pmid" else f'DOI:"{v}"' for k, v in part]
        recs, _ = search(client, " OR ".join(terms), page_size=100)
        out.extend(recs)
    return out
