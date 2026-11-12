"""Record linkage across sources.

Pass 1 joins source records that share a DOI, PMID or PMCID (union-find).
A cluster that ends up holding two different DOIs or two different PMIDs is
not silently accepted: it is flagged as an identifier conflict for review.

Pass 2 links clusters that share no identifier but have the same normalised
title, the same year (within one) and the same journal, with a similarity of
at least 0.95. Such links are labelled so they can be audited. Notices whose
titles start "Correction to", "Erratum" or "Retraction" are never linked by
title, because their titles copy the article they amend.
"""
from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from .identifiers import normalize_title

NOTICE_PREFIXES = ("correction", "erratum", "corrigendum", "retraction", "expression of concern",
                   "notice of retraction", "withdrawn")


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _keys(rec: dict) -> list[str]:
    return [f"{k}:{rec[k]}" for k in ("doi", "pmid", "pmcid") if rec.get(k)]


def _year(rec: dict) -> int | None:
    for k in ("pub_year",):
        if rec.get(k):
            return rec[k]
    for k in ("published", "pub_date", "epub_date"):
        d = rec.get(k) or {}
        if d.get("year"):
            return d["year"]
    return None


def link(records: list[dict], title_threshold: float = 95.0) -> list[dict]:
    """Group source records into clusters. Returns one dict per cluster."""
    uf = UnionFind(len(records))
    first_with_key: dict[str, int] = {}
    for i, rec in enumerate(records):
        for key in _keys(rec):
            if key in first_with_key:
                uf.union(first_with_key[key], i)
            else:
                first_with_key[key] = i

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(records)):
        groups[uf.find(i)].append(i)

    # pass 2: title linkage between clusters with no identifier in common
    clusters = list(groups.values())
    meta = []
    for members in clusters:
        recs = [records[i] for i in members]
        title = next((normalize_title(r["title"]) for r in recs if r.get("title")), "")
        years = {y for y in (_year(r) for r in recs) if y}
        sources = {r["source"] for r in recs}
        id_types = {k for r in recs for k in ("doi", "pmid") if r.get(k)}
        meta.append((title, years, sources, id_types))
    by_block: dict[str, list[int]] = defaultdict(list)
    for ci, (title, *_rest) in enumerate(meta):
        if title and not title.startswith(NOTICE_PREFIXES) and len(title) >= 20:
            by_block[title[:12]].append(ci)
    cuf = UnionFind(len(clusters))
    participants: set[int] = set()
    for block in by_block.values():
        for x in range(len(block)):
            for y in range(x + 1, len(block)):
                a, b = block[x], block[y]
                ta, ya, sa, ia = meta[a]
                tb, yb, sb, ib = meta[b]
                if sa & sb:  # same source on both sides: these are distinct items
                    continue
                if ("doi" in ia and "doi" in ib) or ("pmid" in ia and "pmid" in ib):
                    continue  # both carry the identifier and it differs: not the same item
                if ya and yb and min(abs(p - q) for p in ya for q in yb) > 1:
                    continue
                if fuzz.ratio(ta, tb) >= title_threshold:
                    cuf.union(a, b)
                    participants.update((a, b))
    merged: dict[int, list[int]] = defaultdict(list)
    for ci, members in enumerate(clusters):
        merged[cuf.find(ci)].extend(members)
    linked_by_title = {cuf.find(ci) for ci in participants}

    out = []
    for root, members in merged.items():
        recs = [records[i] for i in sorted(members)]
        dois = sorted({r["doi"] for r in recs if r.get("doi")})
        pmids = sorted({r["pmid"] for r in recs if r.get("pmid")})
        pmcids = sorted({r["pmcid"] for r in recs if r.get("pmcid")})
        by_source = defaultdict(list)
        for r in recs:
            by_source[r["source"]].append(r)
        out.append({
            "members": recs,
            "by_source": dict(by_source),
            "dois": dois, "pmids": pmids, "pmcids": pmcids,
            "id_conflict": len(dois) > 1 or len(pmids) > 1 or len(pmcids) > 1,
            "within_source_duplicates": {s: len(v) for s, v in by_source.items() if len(v) > 1},
            "linked_by_title": root in linked_by_title,
        })
    return out
