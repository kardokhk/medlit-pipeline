"""OpenAlex: open-access status and the best legal open copy for each DOI."""
from __future__ import annotations

from ..identifiers import normalize_doi

API = "https://api.openalex.org/works"
SELECT = "id,doi,type,publication_date,open_access,best_oa_location,primary_location,is_retracted"


def by_dois(client, dois: list[str], chunk: int = 50) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(dois), chunk):
        part = dois[i:i + chunk]
        r = client.get(API, {"filter": "doi:" + "|".join(part), "per-page": 200, "select": SELECT})
        if r.status != 200:
            continue
        for w in r.json().get("results", []):
            doi = normalize_doi(w.get("doi"))
            if not doi:
                continue
            oa = w.get("open_access") or {}
            best = w.get("best_oa_location") or {}
            prim = w.get("primary_location") or {}
            out[doi] = {
                "source": "openalex", "openalex_id": w.get("id"), "type": w.get("type"),
                "oa_status": oa.get("oa_status"), "is_oa": oa.get("is_oa"),
                "best_oa_pdf": best.get("pdf_url"), "best_oa_landing": best.get("landing_page_url"),
                "best_oa_license": best.get("license"), "best_oa_version": best.get("version"),
                "primary_license": prim.get("license"),
                "publisher": ((prim.get("source") or {}).get("host_organization_name")),
                "is_retracted": w.get("is_retracted"),
                "api_url": r.url, "retrieved_at": r.fetched_at,
            }
    return out
