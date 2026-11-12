"""PubMed through NCBI E-utilities (ESearch with history, EFetch XML)."""
from __future__ import annotations

from lxml import etree

from ..identifiers import normalize_doi, normalize_issn, normalize_pmcid, normalize_pmid

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "medlit-pipeline"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def journal_query(issns: list[str], year_from: int, year_to: int) -> str:
    j = " OR ".join(f'"{i}"[is]' for i in issns)
    return f"({j}) AND {year_from}:{year_to}[dp]"


def search(client, term: str, batch: int = 9999) -> tuple[list[str], dict]:
    """All PMIDs for a query. ESearch caps retstart at 9,999, so larger sets
    must be split by date (the caller's job; journal-year sets are far smaller)."""
    r = client.get(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": batch,
                                              "retmode": "json", "tool": TOOL})
    res = r.json()["esearchresult"]
    count = int(res["count"])
    if count > batch:
        raise ValueError(f"{count} hits for {term!r}: split the query by date")
    return res["idlist"], {"source": "pubmed", "query": term, "count": count, "url": r.url,
                           "retrieved_at": r.fetched_at}


def search_by_dois(client, dois: list[str], chunk: int = 40) -> dict[str, str]:
    """Map DOIs to PMIDs with [aid] searches; used to explain Crossref-only records."""
    out: dict[str, str] = {}
    for i in range(0, len(dois), chunk):
        part = dois[i:i + chunk]
        term = " OR ".join(f'"{d}"[aid]' for d in part)
        r = client.get(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": 500,
                                                  "retmode": "json", "tool": TOOL})
        pmids = r.json()["esearchresult"]["idlist"]
        if pmids:
            for rec in fetch(client, pmids):
                if rec["doi"] in part:
                    out[rec["doi"]] = rec["pmid"]
    return out


def fetch(client, pmids: list[str], chunk: int = 200) -> list[dict]:
    recs = []
    for i in range(0, len(pmids), chunk):
        part = pmids[i:i + chunk]
        r = client.get(f"{EUTILS}/efetch.fcgi", {"db": "pubmed", "id": ",".join(part),
                                                 "retmode": "xml", "tool": TOOL})
        root = etree.fromstring(r.body)
        for art in root.iter("PubmedArticle"):
            rec = parse_article(art)
            rec["source_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/"
            rec["api_url"] = r.url
            rec["retrieved_at"] = r.fetched_at
            recs.append(rec)
    return recs


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _date(el) -> dict:
    if el is None:
        return {}
    y = el.findtext("Year")
    m = el.findtext("Month")
    d = el.findtext("Day")
    if y is None:
        md = el.findtext("MedlineDate") or ""
        y = md[:4] if md[:4].isdigit() else None
    if m and not m.isdigit():
        m = MONTHS.get(m[:3].lower())
    return {"year": int(y) if y else None, "month": int(m) if m else None,
            "day": int(d) if d else None}


def parse_article(art) -> dict:
    mc = art.find("MedlineCitation")
    a = mc.find("Article")
    j = a.find("Journal")
    ids = {x.get("IdType"): (x.text or "").strip() for x in art.iterfind("PubmedData/ArticleIdList/ArticleId")}
    doi = normalize_doi(ids.get("doi"))
    if not doi:
        for e in a.iterfind("ELocationID"):
            if e.get("EIdType") == "doi":
                doi = normalize_doi(e.text)
    authors = []
    for au in a.iterfind("AuthorList/Author"):
        if au.findtext("CollectiveName"):
            authors.append({"collective": _text(au.find("CollectiveName"))})
            continue
        orcid = next((x.text for x in au.iterfind("Identifier") if x.get("Source") == "ORCID"), None)
        authors.append({"family": au.findtext("LastName"), "given": au.findtext("ForeName"),
                        "orcid": orcid,
                        "affiliations": [_text(x) for x in au.iterfind("AffiliationInfo/Affiliation")]})
    abstract_parts = []
    for t in a.iterfind("Abstract/AbstractText"):
        label = t.get("Label")
        abstract_parts.append((f"{label}: " if label else "") + _text(t))
    corrections = [{"type": c.get("RefType"), "pmid": c.findtext("PMID"), "source": c.findtext("RefSource")}
                   for c in mc.iterfind("CommentsCorrectionsList/CommentsCorrections")
                   if c.get("RefType") in {"RetractionIn", "RetractionOf", "ErratumIn", "ErratumFor",
                                           "ExpressionOfConcernIn", "UpdateIn", "CorrectedandRepublishedIn"}]
    pubtypes = [x.text for x in a.iterfind("PublicationTypeList/PublicationType")]
    edate = a.find("ArticleDate")
    issns = [normalize_issn(x.text) for x in j.iterfind("ISSN")]
    linking = mc.findtext("MedlineJournalInfo/ISSNLinking")
    return {
        "source": "pubmed",
        "pmid": normalize_pmid(mc.findtext("PMID")),
        "doi": doi,
        "pmcid": normalize_pmcid(ids.get("pmc")),
        "title": _text(a.find("ArticleTitle")),
        "abstract": "\n".join(abstract_parts),
        "journal": j.findtext("Title"),
        "journal_abbrev": j.findtext("ISOAbbreviation"),
        "issns": sorted({i for i in issns + [normalize_issn(linking)] if i}),
        "volume": j.findtext("JournalIssue/Volume"),
        "issue": j.findtext("JournalIssue/Issue"),
        "pages": a.findtext("Pagination/MedlinePgn") or next(
            (e.text for e in a.iterfind("ELocationID") if e.get("EIdType") == "pii"), None),
        "pub_date": _date(j.find("JournalIssue/PubDate")),
        "epub_date": _date(edate),
        "authors": authors,
        "language": [x.text for x in a.iterfind("Language")],
        "publication_types": pubtypes,
        "mesh": [_text(x.find("DescriptorName")) for x in mc.iterfind("MeshHeadingList/MeshHeading")],
        "keywords": [_text(x) for x in mc.iterfind("KeywordList/Keyword")],
        "grants": [{"id": g.findtext("GrantID"), "agency": g.findtext("Agency")}
                   for g in a.iterfind("GrantList/Grant")],
        "corrections": corrections,
        "retracted": "Retracted Publication" in pubtypes or any(
            c["type"] == "RetractionIn" for c in corrections),
        "is_retraction_notice": "Retraction of Publication" in pubtypes,
        "status": art.findtext("PubmedData/PublicationStatus"),
    }
