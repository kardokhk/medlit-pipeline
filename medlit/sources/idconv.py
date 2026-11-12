"""NCBI PMC ID Converter: PMID <-> PMCID <-> DOI for articles in PMC."""
from __future__ import annotations

from ..identifiers import normalize_doi, normalize_pmcid, normalize_pmid

API = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"


def convert(client, ids: list[str], chunk: int = 200) -> list[dict]:
    out = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        r = client.get(API, {"ids": ",".join(part), "format": "json", "tool": "medlit-pipeline"})
        for rec in r.json().get("records", []):
            if rec.get("status") == "error":
                continue
            out.append({"source": "idconv", "requested": str(rec.get("requested-id")),
                        "pmid": normalize_pmid(rec.get("pmid")),
                        "pmcid": normalize_pmcid(rec.get("pmcid")),
                        "doi": normalize_doi(rec.get("doi")),
                        "api_url": r.url, "retrieved_at": r.fetched_at})
    return out
