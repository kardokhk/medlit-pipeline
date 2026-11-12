"""PMC Article Datasets on AWS Open Data (bucket ``pmc-oa-opendata``).

The bucket holds the PMC Open Access Subset and the Author Manuscript
Dataset, the parts of PMC that NLM makes available for text mining and reuse.
PMC names four services that may be used for automated retrieval (Cloud
Service, OAI-PMH, E-utilities and BioC API) and prohibits systematic retrieval
by any other automated process (https://pmc.ncbi.nlm.nih.gov/tools/openftlist/,
accessed 2026-11-12). An article that is in PMC but absent from the bucket is
recorded as readable but not licensed for bulk retrieval.

Each article version has a JSON manifest with the licence (``license_code``),
whether it is an author manuscript, retraction status, and an MD5 checksum for
every file (JATS XML, PDF, plain text, figures and supplementary files).
Supplementary files of author manuscripts are not distributed.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from lxml import etree

BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def latest_version(client, pmcid: str) -> str | None:
    r = client.get(f"{BUCKET}/", {"list-type": "2", "prefix": f"{pmcid}.", "delimiter": "/"})
    if r.status != 200:
        return None
    root = etree.fromstring(r.body)
    versions = []
    for p in root.iterfind("s3:CommonPrefixes/s3:Prefix", _NS):
        m = re.match(rf"^{pmcid}\.(\d+)/$", p.text or "")
        if m:
            versions.append(int(m.group(1)))
    return f"{pmcid}.{max(versions)}" if versions else None


def manifest(client, pmcid: str) -> dict | None:
    ver = latest_version(client, pmcid)
    if not ver:
        return None
    r = client.get(f"{BUCKET}/{ver}/{ver}.json")
    if r.status != 200:
        return None
    m = r.json()
    m["version_key"] = ver
    m["manifest_url"] = r.url
    m["retrieved_at"] = r.fetched_at
    return m


def s3_to_https(s3_url: str) -> tuple[str, str, str | None]:
    """'s3://bucket/key?md5=..' -> (https url, file name, md5)."""
    parts = urlsplit(s3_url)
    md5 = parse_qs(parts.query).get("md5", [None])[0]
    key = parts.path.lstrip("/")
    return f"{BUCKET}/{key}", key.rsplit("/", 1)[-1], md5
