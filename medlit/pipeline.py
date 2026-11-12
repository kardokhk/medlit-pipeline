"""End-to-end collection: harvest -> link -> enrich -> retrieve -> validate -> export."""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import dedup, fulltext, store, validate
from .identifiers import normalize_title, record_id
from .sources import crossref, europepmc, idconv, openalex, pubmed

log = logging.getLogger("medlit")

SOURCES = ("crossref", "pubmed", "europepmc")


# ---------------------------------------------------------------- harvest
def harvest(client, cfg: dict) -> tuple[list[dict], list[dict]]:
    raw, queries = [], []
    y0, y1 = cfg["years"]
    if cfg["mode"] == "journal":
        issns = cfg["issns"]
        items, q = crossref.list_journal_works(client, issns, f"{y0}-01-01", f"{y1}-12-31")
        raw += items
        queries.append(q)
        pmids, q = pubmed.search(client, pubmed.journal_query(issns, y0, y1))
        raw += pubmed.fetch(client, pmids)
        queries.append(q)
        items, q = europepmc.search(client, europepmc.journal_query(issns, y0, y1))
        raw += items
        queries.append(q)
    elif cfg["mode"] == "query":
        pmids, q = pubmed.search(client, cfg["pubmed_query"])
        raw += pubmed.fetch(client, pmids)
        queries.append(q)
        items, q = europepmc.search(client, cfg["europepmc_query"])
        raw += items
        queries.append(q)
    else:
        raise ValueError(f"unknown mode {cfg['mode']}")
    for r in raw:
        r["harvested"] = True
    return raw, queries


# ---------------------------------------------------------------- cross-lookups
def cross_lookup(client, clusters: list[dict], cfg: dict) -> None:
    """For each cluster missing from a source, look it up there by identifier, so
    a one-source record can be explained (dated outside the window, or genuinely
    absent) rather than just counted."""
    sources_used = {"crossref", "pubmed", "europepmc"} if cfg["mode"] == "journal" else {"pubmed", "europepmc"}
    need_cr = [c for c in clusters if c["dois"] and "crossref" not in c["by_source"]]
    for c in need_cr:
        rec = crossref.get_work(client, c["dois"][0])
        _attach(c, rec)
    need_pm = [c for c in clusters if "pubmed" not in c["by_source"]]
    by_pmid = [c for c in need_pm if c["pmids"]]
    if by_pmid:
        found = {r["pmid"]: r for r in pubmed.fetch(client, [c["pmids"][0] for c in by_pmid])}
        for c in by_pmid:
            _attach(c, found.get(c["pmids"][0]))
    by_doi = [c for c in need_pm if not c["pmids"] and c["dois"]]
    if by_doi:
        mapping = pubmed.search_by_dois(client, [c["dois"][0] for c in by_doi])
        if mapping:
            found = {r["pmid"]: r for r in pubmed.fetch(client, sorted(set(mapping.values())))}
            for c in by_doi:
                _attach(c, found.get(mapping.get(c["dois"][0])))
    need_ep = [c for c in clusters if "europepmc" not in c["by_source"] and (c["pmids"] or c["dois"])]
    if need_ep and "europepmc" in sources_used:
        ids = [("pmid", c["pmids"][0]) if c["pmids"] else ("doi", c["dois"][0]) for c in need_ep]
        recs = europepmc.lookup(client, ids)
        by_key = {}
        for r in recs:
            for k in ("pmid", "doi"):
                if r.get(k):
                    by_key.setdefault((k, r[k]), r)
        for c, key in zip(need_ep, ids):
            _attach(c, by_key.get(key))
    # refresh identifier sets after lookups
    for c in clusters:
        c["dois"] = sorted({r["doi"] for r in c["members"] if r.get("doi")})
        c["pmids"] = sorted({r["pmid"] for r in c["members"] if r.get("pmid")})
        c["pmcids"] = sorted({r["pmcid"] for r in c["members"] if r.get("pmcid")})
        c["id_conflict"] = len(c["dois"]) > 1 or len(c["pmids"]) > 1 or len(c["pmcids"]) > 1


def _attach(cluster: dict, rec: dict | None) -> None:
    if not rec:
        return
    rec = dict(rec, harvested=False)
    cluster["members"].append(rec)
    cluster["by_source"].setdefault(rec["source"], []).append(rec)


# ---------------------------------------------------------------- canonical record
def _first(*vals):
    return next((v for v in vals if v not in (None, "", [], {})), None)


def article_category(pm: dict | None, cr: dict | None, jats_type: str | None, title: str) -> str:
    pts = set((pm or {}).get("publication_types", []))
    t = normalize_title(title)
    if pts & {"Retraction of Publication"} or t.startswith(("retraction", "notice of retraction")):
        return "retraction notice"
    if pts & {"Published Erratum"} or t.startswith(dedup.NOTICE_PREFIXES[:3]) or jats_type == "correction":
        return "correction"
    if t.startswith(("comment on", "response to comment", "reply to", "letter to the editor")):
        return "letter"
    if pts & {"Randomized Controlled Trial", "Clinical Trial", "Clinical Trial, Phase III",
              "Clinical Trial, Phase II", "Observational Study", "Multicenter Study", "Comparative Study",
              "Validation Study", "Evaluation Study"}:
        return "research"
    if pts & {"Systematic Review", "Meta-Analysis", "Review"} or jats_type in ("review-article", "systematic-review"):
        return "review"
    if pts & {"Editorial", "Comment", "Introductory Journal Article", "Interview", "News"} or \
            jats_type in ("editorial", "news", "introduction"):
        return "editorial or comment"
    if pts & {"Letter"} or jats_type in ("letter", "reply"):
        return "letter"
    if pts & {"Practice Guideline", "Guideline", "Consensus Development Conference"}:
        return "guideline"
    if pts & {"Journal Article"} or jats_type in ("research-article", "brief-report"):
        return "research"
    if pm is None and cr is not None:
        return "front matter (not in PubMed)"
    return "other"


def canonical(cl: dict) -> dict:
    g = lambda s: (cl["by_source"].get(s) or [None])[0]  # noqa: E731
    pm, cr = g("pubmed"), g("crossref")
    eps = cl["by_source"].get("europepmc", [])
    ep = next((r for r in eps if r.get("epmc_source") == "MED"), eps[0] if eps else None)
    doi = cr["doi"] if cr and cl["id_conflict"] else _first(*cl["dois"][:1])
    pmid = pm["pmid"] if pm and cl["id_conflict"] else _first(*cl["pmids"][:1])
    pmcid = _first(*(cl["pmcids"][:1]))
    year = _first((pm or {}).get("pub_date", {}).get("year"), (cr or {}).get("published", {}).get("year"),
                  (ep or {}).get("pub_year"))
    title = _first((pm or {}).get("title"), (cr or {}).get("title"), (ep or {}).get("title")) or ""
    rec = {
        "record_id": record_id(doi, pmid, pmcid),
        "doi": doi, "pmid": pmid, "pmcid": pmcid,
        "title": title,
        "journal": _first((pm or {}).get("journal"), (cr or {}).get("journal"), (ep or {}).get("journal")),
        "issns": sorted(set((pm or {}).get("issns", []) + (cr or {}).get("issns", []) + (ep or {}).get("issns", []))),
        "publisher": (cr or {}).get("publisher"),
        "year": year,
        "volume": _first((pm or {}).get("volume"), (cr or {}).get("volume"), (ep or {}).get("volume")),
        "issue": _first((pm or {}).get("issue"), (cr or {}).get("issue"), (ep or {}).get("issue")),
        "pages": _first((pm or {}).get("pages"), (cr or {}).get("pages"), (ep or {}).get("pages")),
        "published_online": (cr or {}).get("published_online") or (pm or {}).get("epub_date") or {},
        "published_print": (cr or {}).get("published_print") or (pm or {}).get("pub_date") or {},
        "crossref_type": (cr or {}).get("type"),
        "publication_types": (pm or {}).get("publication_types") or (ep or {}).get("publication_types") or [],
        "authors": (pm or {}).get("authors") or (cr or {}).get("authors") or [],
        "abstract": (pm or {}).get("abstract") or "",
        "mesh": (pm or {}).get("mesh", []),
        "keywords": (pm or {}).get("keywords", []),
        "grants": (pm or {}).get("grants", []),
        "language": (pm or {}).get("language", []),
        "retracted": any(r.get("retracted") for r in cl["members"]),
        "epmc_open_access": any(r.get("is_open_access") for r in eps),
        "epmc_in_pmc": any(r.get("in_pmc") for r in eps),
        "crossref": cr,
        "sources": [{"source": r["source"], "harvested": r.get("harvested", False),
                     "id": r.get("epmc_id") or r.get("pmid") or r.get("doi"),
                     "source_url": r.get("source_url"), "api_url": r.get("api_url"),
                     "retrieved_at": r.get("retrieved_at")} for r in cl["members"]],
        "in_window": {s: any(r["source"] == s and r.get("harvested") for r in cl["members"]) for s in SOURCES},
        "exists_in": {s: s in cl["by_source"] for s in SOURCES},
    }
    return rec


def presence_reason(rec: dict, cfg: dict) -> str:
    """Why a record appears in some sources' result sets but not others."""
    used = SOURCES if cfg["mode"] == "journal" else ("pubmed", "europepmc")
    inw = [s for s in used if rec["in_window"][s]]
    if len(inw) == len(used):
        return "in all sources"
    missing = [s for s in used if not rec["in_window"][s]]
    parts = []
    for s in missing:
        if rec["exists_in"][s]:
            parts.append(f"{s}: indexed, dated outside the window")
        else:
            parts.append(f"{s}: not indexed")
    return "; ".join(parts)


_CC = re.compile(r"cc[ -_]?(by(?:[ -_](?:nc|sa|nd))*)|creativecommons\.org/licenses/([a-z-]+)", re.I)


def normalize_licence(code: str | None, art: dict | None) -> str | None:
    """One spelling per licence: 'CC BY-NC-ND', 'CC0', 'publisher licence (host)',
    'text mining permitted (author manuscript)'."""
    cands = [code] + ([art["license"].get("url"), art["license"].get("type")] if art else [])
    for c in cands:
        if not c:
            continue
        if str(c).strip().upper() == "TDM":
            return "text mining permitted (author manuscript)"
        if "publicdomain/zero" in str(c) or str(c).strip().lower() in ("cc0", "cc-0"):
            return "CC0"
        m = _CC.search(str(c))
        if m:
            body = (m.group(1) or m.group(2) or "").lower().replace("_", "-").replace(" ", "-")
            return "CC " + body.upper()
    for c in cands:
        if c and str(c).startswith("http"):
            return f"publisher licence ({urlsplit(c).netloc})"
    if art and art["license"].get("text"):
        return "publisher licence (statement in article)"
    return code


# ---------------------------------------------------------------- run
def run(client, cfg: dict, run_dir: Path, workers: int = 8, limit: int | None = None) -> dict:
    t0 = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("harvest %s", cfg["name"])
    raw, queries = harvest(client, cfg)
    store.write_jsonl(run_dir / "sources" / "harvested.jsonl", raw)
    log.info("harvested %d source records", len(raw))

    clusters = dedup.link(raw)
    log.info("linked into %d records", len(clusters))
    cross_lookup(client, clusters, cfg)

    pmids_no_pmc = [c["pmids"][0] for c in clusters if c["pmids"] and not c["pmcids"]]
    conv = {r["pmid"]: r for r in idconv.convert(client, pmids_no_pmc) if r.get("pmid")}
    for c in clusters:
        if c["pmids"] and not c["pmcids"] and conv.get(c["pmids"][0], {}).get("pmcid"):
            r = conv[c["pmids"][0]]
            c["members"].append(dict(r, harvested=False))
            c["by_source"].setdefault("idconv", []).append(r)
            c["pmcids"] = [r["pmcid"]]

    records = [canonical(c) for c in clusters]
    oa = openalex.by_dois(client, sorted({r["doi"] for r in records if r["doi"]}))
    for rec, cl in zip(records, clusters):
        rec["openalex"] = oa.get(rec["doi"]) if rec["doi"] else None
        rec["flags"] = validate.metadata_checks(cl)
        rec["presence"] = presence_reason(rec, cfg)
    if limit:
        records = records[:limit]

    ft = cfg.get("fulltext", {})
    art_root = run_dir / "articles"

    def work(rec):
        try:
            return fulltext.retrieve(client, rec, art_root / rec["record_id"], pdf=ft.get("pdf", True),
                                     supplements=ft.get("supplements", True), figures=ft.get("figures", False),
                                     publisher_oa=ft.get("publisher_oa", True))
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the run
            log.exception("retrieval failed for %s", rec["record_id"])
            return {"route": "error", "error": str(exc)[:300], "files": {}, "article": None, "attempts": []}

    log.info("retrieving full texts for %d records", len(records))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, records))

    for rec, res in zip(records, results):
        art = res.get("article")
        rec["access"] = {"route": res["route"], "licence": normalize_licence(res.get("licence"), art),
                         "open_copy": res.get("open_copy"),
                         "attempts": res.get("attempts", []), "pmc_manifest": res.get("pmc_manifest"),
                         "lawful_route_if_missing": None if art or res["files"].get("pdf") else fulltext.lawful_route(rec)}
        rec["files"] = {k: [{x: f.get(x) for x in ("name", "bytes", "md5", "md5_ok", "magic", "url", "skipped", "error",
                                                "reason", "licence", "source_id")}
                            for f in v] for k, v in res["files"].items()}
        cr = rec.get("crossref")
        rec["flags"] += validate.open_copy_checks(res.get("open_copy"))
        if art:
            rec["flags"] += validate.fulltext_checks(art, res["files"], rec, cr, res["route"])
            rec["reference_agreement"] = validate.reference_agreement(art["references"], (cr or {}).get("references", []))
            rec["jats_article_type"] = art["article_type"]
            store.write_article(art_root / rec["record_id"], rec, art)
        else:
            rec["jats_article_type"] = None
            rec["reference_agreement"] = None
            for f in res["files"].get("pdf", []):
                if f.get("magic") and f["magic"] != "pdf":
                    rec["flags"].append(validate.flag("PDF_NOT_PDF", "error", f["name"]))
        pm_like = {"publication_types": rec["publication_types"]} if rec["exists_in"]["pubmed"] else None
        rec["category"] = article_category(pm_like, cr, rec["jats_article_type"], rec["title"])
        rec["completeness"] = store.completeness(rec, art)

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"collection": cfg["name"], "label": cfg.get("label"), "config": cfg, "queries": queries,
            "started": started, "finished": finished, "elapsed_s": round(time.time() - t0, 1),
            "http": client.stats, "n_source_records": len(raw), "n_records": len(records)}
    store.export(run_dir, records, meta)
    return meta

