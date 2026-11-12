"""Coverage report: one Markdown file, figures and a JSON of every number in it.

All numbers in the report are computed here from the run outputs, so the
report can be regenerated and every figure traced to records.jsonl.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"]
SANS = ["Helvetica", "Nimbus Sans", "TeX Gyre Heros", "Arimo", "Liberation Sans", "Arial", "DejaVu Sans"]
MM = 1 / 25.4

ROUTE_LABELS = {
    "pmc_open_access": "PMC Open Access Subset",
    "pmc_author_manuscript": "PMC Author Manuscript Dataset",
    "europepmc_open_access": "Europe PMC open access",
    "open_copy": "Openly licensed copy via OpenAlex",
    "not_retrieved": "Not retrieved",
    "error": "Retrieval error",
}


def _style() -> None:
    family = next((f for f in SANS if any(f == x.name for x in font_manager.fontManager.ttflist)), "DejaVu Sans")
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [family], "font.size": 7, "axes.labelsize": 7,
        "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5, "axes.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "xtick.major.width": 0.6,
        "ytick.major.width": 0.6, "pdf.fonttype": 42, "savefig.dpi": 600, "legend.frameon": False,
    })


def load(run_dir: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((run_dir / "run.json").read_text())
    recs = [json.loads(line) for line in open(run_dir / "records.jsonl", encoding="utf-8")]
    for r in recs:
        p = run_dir / "articles" / r["record_id"] / "article.json"
        r["_art"] = json.loads(p.read_text()) if p.exists() else None
    return meta, recs


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def summarise(meta: dict, recs: list[dict]) -> dict:
    n = len(recs)
    q = {x["source"]: x["count"] for x in meta["queries"]}
    sources_used = [s for s in ("crossref", "pubmed", "europepmc") if s in q]
    presence = Counter(r["presence"] for r in recs)
    retrieved = [r for r in recs if r["_art"]]
    research = [r for r in recs if r["category"] in ("research", "review")]
    route = Counter(r["access"]["route"] for r in recs)
    route_research = Counter(r["access"]["route"] for r in research)
    lic = Counter((r["access"]["licence"] or "none") for r in recs if r["access"]["route"] != "not_retrieved")
    lawful = Counter(r["access"]["lawful_route_if_missing"] for r in recs if r["access"]["lawful_route_if_missing"])
    cats = Counter(r["category"] for r in recs)
    flags = Counter()
    flag_records = Counter()
    for r in recs:
        seen = set()
        for f in r["flags"]:
            flags[(f["code"], f["severity"])] += 1
            if f["code"] not in seen:
                flag_records[f["code"]] += 1
                seen.add(f["code"])
    tables = [t for r in retrieved for t in r["_art"]["tables"]]
    refs = [x for r in retrieved for x in r["_art"]["references"]]
    supp_declared = sum(len(r["_art"]["supplements"]) for r in retrieved)
    supp_got = sum(1 for r in retrieved for f in r["files"].get("supplement", [])
                   if not f.get("error") and not f.get("skipped"))
    ext = [f for r in retrieved for f in r["files"].get("supplement_external", [])]
    ext_ids = {(r["record_id"], x["id"]) for r in retrieved for x in r["_art"].get("external_supplements", [])
               if x.get("kind", "supplement") == "supplement"}
    proj_ids = {(r["record_id"], x["id"]) for r in retrieved for x in r["_art"].get("external_supplements", [])
                if x.get("kind") == "linked project"}
    ext_ok_ids = {(r["record_id"], f.get("source_id")) for r in retrieved
                  for f in r["files"].get("supplement_external", []) if not f.get("error") and not f.get("skipped")}
    files = [f for r in recs for v in r["files"].values() for f in v if not f.get("skipped") and not f.get("error")]
    md5_checked = [f for f in files if f.get("md5_ok") is not None]
    agreement = [r["reference_agreement"] for r in retrieved if r.get("reference_agreement")]
    pdfs = sum(1 for r in recs if any(f.get("magic") == "pdf" for f in r["files"].get("pdf", [])))
    declared_tables = [r for r in retrieved if "table-count" in r["_art"]["declared_counts"]]
    return {
        "label": meta.get("label") or meta["collection"],
        "collection": meta["collection"],
        "window": meta["config"]["years"],
        "issns": meta["config"].get("issns"),
        "queries": meta["queries"],
        "source_counts": q,
        "sources_used": sources_used,
        "n_source_records": meta["n_source_records"],
        "n_records": n,
        "duplicates_removed": meta["n_source_records"] - n,
        "presence": dict(presence.most_common()),
        "ids": {k: sum(1 for r in recs if r[k]) for k in ("doi", "pmid", "pmcid")},
        "categories": dict(cats.most_common()),
        "n_research": len(research),
        "routes": dict(route.most_common()),
        "routes_research": dict(route_research.most_common()),
        "licences": dict(lic.most_common()),
        "lawful_routes": dict(lawful.most_common()),
        "n_fulltext": len(retrieved),
        "n_fulltext_research": sum(1 for r in research if r["_art"]),
        "n_pdf": pdfs,
        "n_fulltext_complete": sum(1 for r in recs if r["completeness"]["fulltext_complete"]),
        "n_metadata_complete": sum(1 for r in recs if r["completeness"]["metadata_complete"]),
        "metadata_field_missing": {k: sum(1 for r in recs if not r["completeness"]["metadata_fields"][k])
                                   for k in recs[0]["completeness"]["metadata_fields"]} if recs else {},
        "body_words_median": statistics.median([r["_art"]["body_words"] for r in retrieved]) if retrieved else None,
        "tables": {"n": len(tables), "parsed": sum(1 for t in tables if t["has_markup"]),
                   "image_only": sum(1 for t in tables if t["image_only"]),
                   "ragged": sum(1 for t in tables if t["has_markup"] and not t["rectangular"]),
                   "articles_with_declared_count": len(declared_tables),
                   "declared_count_matches": sum(1 for r in declared_tables
                                                 if r["_art"]["declared_counts"]["table-count"] == len(r["_art"]["tables"]))},
        "figures": sum(len(r["_art"]["figures"]) for r in retrieved),
        "references": {"n": len(refs), "with_doi": sum(1 for x in refs if x["doi"]),
                       "with_pmid": sum(1 for x in refs if x["pmid"])},
        "reference_agreement": {
            "n_articles": len(agreement),
            "median_recall": statistics.median([a["recall_vs_crossref"] for a in agreement]) if agreement else None,
            "median_precision": statistics.median([a["precision_vs_crossref"] for a in agreement]) if agreement else None,
            "q1_recall": _q(agreement, "recall_vs_crossref", 0.25),
            "q1_precision": _q(agreement, "precision_vs_crossref", 0.25),
            "pooled_recall": (sum(a["shared"] for a in agreement) / sum(a["crossref_dois"] for a in agreement))
            if agreement else None,
            "pooled_precision": (sum(a["shared"] for a in agreement) / sum(a["fulltext_dois"] for a in agreement))
            if agreement else None,
        },
        "supplements": {"declared": supp_declared, "retrieved": supp_got},
        "external_supplements": {
            "articles": len({rid for rid, _ in ext_ids}),
            "project_links": len(proj_ids), "project_link_articles": len({rid for rid, _ in proj_ids}),
            "deposits": len(ext_ids), "deposits_retrieved": len(ext_ok_ids & ext_ids),
            "files": sum(1 for f in ext if not f.get("error") and not f.get("skipped")),
            "not_retrieved": len(ext_ids - ext_ok_ids)},
        "n_metadata_complete_research": sum(1 for r in research if r["completeness"]["metadata_complete"]),
        "open_copies": Counter((r["access"].get("open_copy") or {}).get("version") for r in recs
                               if r["access"].get("open_copy")),
        "files": {"n": len(files), "bytes": sum(f.get("bytes") or 0 for f in files),
                  "md5_checked": len(md5_checked), "md5_ok": sum(1 for f in md5_checked if f["md5_ok"])},
        "flags": {f"{c}|{s}": v for (c, s), v in sorted(flags.items(), key=lambda x: (-x[1], x[0]))},
        "flag_records": dict(flag_records),
        "retracted": sum(1 for r in recs if r["retracted"]),
        "elapsed_s": meta["elapsed_s"], "http": meta["http"],
        "started": meta["started"], "finished": meta["finished"],
    }


def _q(rows, key, p):
    vals = sorted(a[key] for a in rows)
    if not vals:
        return None
    return statistics.quantiles(vals, n=4, method="inclusive")[0] if len(vals) > 1 else vals[0]


# ---------------------------------------------------------------- figures
def fig_routes(summaries: list[dict], out: Path) -> None:
    _style()
    order = ["pmc_open_access", "pmc_author_manuscript", "europepmc_open_access", "open_copy",
             "not_retrieved", "error"]
    present = [k for k in order if any(s["routes"].get(k) for s in summaries)]
    colours = dict(zip(order, [OKABE_ITO[0], OKABE_ITO[5], OKABE_ITO[2], OKABE_ITO[4], "#BBBBBB", OKABE_ITO[1]]))
    hatches = dict(zip(order, ["", "////", "....", "xxxx", "", "\\\\\\\\"]))
    fig, axes = plt.subplots(1, 2, figsize=(183 * MM, 52 * MM), gridspec_kw={"wspace": 0.55})
    for ax, key, title in ((axes[0], "routes", "All items"), (axes[1], "routes_research", "Research articles and reviews")):
        labels = [s["label"] for s in summaries]
        lefts = [0.0] * len(summaries)
        for k in present:
            vals = [s[key].get(k, 0) for s in summaries]
            tot = [max(1, sum(s[key].values())) for s in summaries]
            fr = [100 * v / t for v, t in zip(vals, tot)]
            bars = ax.barh(labels, fr, left=lefts, color=colours[k], hatch=hatches[k], edgecolor="white",
                           linewidth=0.5, label=ROUTE_LABELS[k])
            for b, v, f in zip(bars, vals, fr):
                if f >= 7:
                    ax.text(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2, str(v),
                            ha="center", va="center", fontsize=6,
                            color="white" if k in ("pmc_open_access", "error") else "black")
            lefts = [a + b for a, b in zip(lefts, fr)]
        ax.set_xlim(0, 100)
        ax.set_xlabel("Share of items (%); numbers are counts")
        ax.set_title(title, fontsize=7, loc="left", fontweight="bold")
        ax.invert_yaxis()
    handles, labs = axes[0].get_legend_handles_labels()
    fig.legend(handles, labs, loc="lower center", ncol=min(5, len(labs)), bbox_to_anchor=(0.5, -0.02))
    fig.subplots_adjust(left=0.16, right=0.98, top=0.88, bottom=0.36)
    _save(fig, out / "fig_access_routes")


def fig_components(summaries: list[dict], out: Path) -> None:
    _style()
    rows = []
    for s in summaries:
        ft = max(1, s["n_fulltext"])
        rows.append((s["label"], [
            ("Full text retrieved\n(of all items)", s["n_fulltext"], s["n_records"]),
            ("Tables as cell data\n(of tables)", s["tables"]["parsed"], s["tables"]["n"]),
            ("References with DOI\n(of references)", s["references"]["with_doi"], s["references"]["n"]),
            ("Packaged supplements\nretrieved (of declared)", s["supplements"]["retrieved"], s["supplements"]["declared"]),
            ("Repository supplements\nretrieved (of linked)", s["external_supplements"]["deposits_retrieved"], s["external_supplements"]["deposits"]),
            ("Files matching\npublished MD5", s["files"]["md5_ok"], s["files"]["md5_checked"]),
        ]))
    cats = [c for c, _, _ in rows[0][1]]
    fig, ax = plt.subplots(figsize=(183 * MM, 68 * MM))
    width = 0.8 / len(rows)
    markers = ["o", "s", "^", "D"]
    for i, (label, vals) in enumerate(rows):
        xs = [j + (i - (len(rows) - 1) / 2) * width for j in range(len(cats))]
        ys = [100 * n / d if d else 0 for _, n, d in vals]
        ax.bar(xs, ys, width=width * 0.92, color=OKABE_ITO[i], hatch=["", "////", "...."][i % 3],
               edgecolor="white", linewidth=0.5, label=label)
        for x, y, (_, n, d) in zip(xs, ys, vals):
            ax.text(x, y + 1.5, f"{n:,}/{d:,}" if d else "none", ha="center", va="bottom", fontsize=5.5, rotation=90)
    ax.set_xticks(range(len(cats)), cats)
    ax.set_ylim(0, 125)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Percentage")
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=len(rows), bbox_to_anchor=(0.5, 0.0))
    fig.subplots_adjust(left=0.07, right=0.99, top=0.97, bottom=0.27)
    _save(fig, out / "fig_components")


def _save(fig, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(f"{stem}.pdf")
    fig.savefig(f"{stem}.png", dpi=600)
    plt.close(fig)


# ---------------------------------------------------------------- markdown
def build(run_dirs: list[Path], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    loaded = [load(Path(d)) for d in run_dirs]
    summaries = [summarise(m, r) for m, r in loaded]
    fig_routes(summaries, out)
    fig_components(summaries, out)
    (out / "coverage.json").write_text(json.dumps(summaries, indent=1, default=str))
    (out / "coverage_report.md").write_text(render(summaries, loaded), encoding="utf-8")
    return {"summaries": summaries}


def _row(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def render(S: list[dict], loaded) -> str:
    L = []
    hdr = ["Measure"] + [s["label"] for s in S]
    sep = ["---"] + ["---:" for _ in S]
    L += ["## Sources and reconciliation", "",
          "Each collection was harvested independently from every source with that source's own date "
          "definition, then linked on DOI, PMID and PMCID. Items missing from one source's result set were "
          "looked up there by identifier, so each gap has a stated reason.", "",
          _row(hdr), _row(sep)]
    for src, name in (("crossref", "Crossref (registered DOIs)"), ("pubmed", "PubMed"), ("europepmc", "Europe PMC")):
        L.append(_row([name] + [f"{s['source_counts'].get(src, 'not used'):,}" if src in s["source_counts"]
                                else "not used" for s in S]))
    L.append(_row(["Source records harvested"] + [f"{s['n_source_records']:,}" for s in S]))
    L.append(_row(["Unique items after linkage"] + [f"**{s['n_records']:,}**" for s in S]))
    for k, name in (("doi", "with DOI"), ("pmid", "with PMID"), ("pmcid", "with PMCID")):
        L.append(_row([f"Items {name}"] + [f"{s['ids'][k]:,} ({pct(s['ids'][k], s['n_records'])})" for s in S]))
    L += ["", "Table: Harvest counts, linkage and identifier coverage.", ""]

    L += ["Why items appear in some sources and not others:", ""]
    reasons = sorted({k for s in S for k in s["presence"]}, key=lambda k: (k != "in all sources", k))
    L += [_row(["Presence"] + [s["label"] for s in S]), _row(sep)]
    for k in reasons:
        L.append(_row([k] + [f"{s['presence'].get(k, 0):,}" for s in S]))
    L += ["", "Table: Presence of each item across sources. \"Dated outside the window\" means the item is "
          "indexed there but that source assigns it a different publication date (online-first versus issue "
          "date).", ""]

    L += ["## Item types", "", _row(["Type"] + [s["label"] for s in S]), _row(sep)]
    for k in sorted({k for s in S for k in s["categories"]}, key=lambda k: -sum(s["categories"].get(k, 0) for s in S)):
        L.append(_row([k] + [f"{s['categories'].get(k, 0):,}" for s in S]))
    L += ["", "Table: Item types, from PubMed publication types and the JATS article type.", ""]

    L += ["## Access routes and licences", "",
          "![Access route for every item, all items (left) and research articles and reviews (right).]"
          "(fig_access_routes.png){width=100%}", "",
          _row(["Route"] + [s["label"] for s in S]), _row(sep)]
    for k in [k for k in ROUTE_LABELS if any(s["routes"].get(k) for s in S)]:
        L.append(_row([ROUTE_LABELS[k]] + [f"{s['routes'].get(k, 0):,} ({pct(s['routes'].get(k, 0), s['n_records'])})"
                                           for s in S]))
    L += ["", "Table: How each item's full text was obtained.", ""]
    L += [_row(["Licence of retrieved items"] + [s["label"] for s in S]), _row(sep)]
    for k in sorted({k for s in S for k in s["licences"]}, key=lambda k: -sum(s["licences"].get(k, 0) for s in S)):
        L.append(_row([k] + [f"{s['licences'].get(k, 0):,}" for s in S]))
    L += ["", "Table: Licence recorded for each retrieved full text (PMC manifest or the publisher's licence "
          "statement).", ""]
    L += [_row(["Items not retrieved: lawful route"] + [s["label"] for s in S]), _row(sep)]
    for k in sorted({k for s in S for k in s["lawful_routes"]}, key=lambda k: -sum(s["lawful_routes"].get(k, 0) for s in S)):
        L.append(_row([k] + [f"{s['lawful_routes'].get(k, 0):,}" for s in S]))
    L += ["", "Table: Items without a retrievable licensed full text, by the route that would give lawful "
          "access. None of these were downloaded.", ""]

    L += ["## Completeness of retrieved full texts", "",
          "![Component completeness. Labels give counts.](fig_components.png){width=100%}", "",
          _row(hdr), _row(sep)]
    L.append(_row(["Full texts retrieved (JATS XML)"] + [f"{s['n_fulltext']:,} ({pct(s['n_fulltext'], s['n_records'])})" for s in S]))
    L.append(_row(["Research articles and reviews with full text"] +
                  [f"{s['n_fulltext_research']:,} of {s['n_research']:,} ({pct(s['n_fulltext_research'], s['n_research'])})" for s in S]))
    L.append(_row(["PDFs retrieved and verified as PDF"] + [f"{s['n_pdf']:,}" for s in S]))
    L.append(_row(["Median body length (words)"] + [f"{s['body_words_median']:,.0f}" if s["body_words_median"] else "n/a" for s in S]))
    L.append(_row(["Tables"] + [f"{s['tables']['n']:,}" for s in S]))
    L.append(_row(["  parsed to cell data (CSV)"] + [f"{s['tables']['parsed']:,} ({pct(s['tables']['parsed'], s['tables']['n'])})" for s in S]))
    L.append(_row(["  image only (needs OCR)"] + [f"{s['tables']['image_only']:,}" for s in S]))
    if any(s["tables"]["articles_with_declared_count"] for s in S):
        L.append(_row(["  table count equals publisher's declared count"] +
                      [f"{s['tables']['declared_count_matches']:,} of {s['tables']['articles_with_declared_count']:,} articles" for s in S]))
    L.append(_row(["Figures (captions and file names)"] + [f"{s['figures']:,}" for s in S]))
    L.append(_row(["References parsed"] + [f"{s['references']['n']:,}" for s in S]))
    L.append(_row(["  with DOI"] + [f"{s['references']['with_doi']:,} ({pct(s['references']['with_doi'], s['references']['n'])})" for s in S]))
    L.append(_row(["  with PMID"] + [f"{s['references']['with_pmid']:,} ({pct(s['references']['with_pmid'], s['references']['n'])})" for s in S]))
    L.append(_row(["Supplementary files declared in the article"] + [f"{s['supplements']['declared']:,}" for s in S]))
    L.append(_row(["  retrieved"] + [f"{s['supplements']['retrieved']:,} ({pct(s['supplements']['retrieved'], s['supplements']['declared'])})" for s in S]))
    L.append(_row(["Articles linking supplements in a data repository"] + [f"{s['external_supplements']['articles']:,}" for s in S]))
    L.append(_row(["  repository deposits retrieved"] + [f"{s['external_supplements']['deposits_retrieved']:,} of {s['external_supplements']['deposits']:,}" for s in S]))
    L.append(_row(["  files retrieved from repositories"] + [f"{s['external_supplements']['files']:,}" for s in S]))
    L.append(_row(["Links to project pages (OSF registrations, protocols); listed, not fetched"] +
                  [f"{s['external_supplements']['project_links']:,}" for s in S]))
    L.append(_row(["Files downloaded"] + [f"{s['files']['n']:,} ({s['files']['bytes'] / 1e9:.2f} GB)" for s in S]))
    L.append(_row(["  matching the published MD5 checksum"] + [f"{s['files']['md5_ok']:,} of {s['files']['md5_checked']:,}" for s in S]))
    L.append(_row(["Items passing every full-text check"] + [f"{s['n_fulltext_complete']:,}" for s in S]))
    L += ["", "Table: Completeness of the retrieved full texts.", ""]

    L += ["## Reference lists checked against an independent copy", "",
          "Where the publisher also deposited the reference list with Crossref, the DOIs parsed from the full "
          "text were compared with the deposited DOIs. Recall is the share of deposited DOIs found in the parsed "
          "list; precision is the share of parsed DOIs that are also in the deposit.", "",
          _row(hdr), _row(sep)]
    L.append(_row(["Articles compared"] + [f"{s['reference_agreement']['n_articles']:,}" for s in S]))
    for k, name in (("pooled_recall", "Pooled recall"), ("pooled_precision", "Pooled precision"),
                    ("median_recall", "Median recall per article"), ("q1_recall", "Lower quartile, recall per article"),
                    ("median_precision", "Median precision per article")):
        L.append(_row([name] + [f"{100 * s['reference_agreement'][k]:.1f}%" if s["reference_agreement"][k] is not None
                                else "n/a" for s in S]))
    L += ["", "Table: Agreement between parsed and deposited reference DOIs.", ""]

    L += ["## Metadata validation", "",
          "Crossref (publisher deposit), PubMed (NLM curation) and Europe PMC were compared field by field for "
          "every linked item.", "",
          _row(["Check (severity)"] + [s["label"] for s in S]), _row(sep)]
    keys = sorted({k for s in S for k in s["flags"]}, key=lambda k: (-sum(s["flags"].get(k, 0) for s in S), k))
    for k in keys:
        code, sev = k.split("|")
        L.append(_row([f"{code} ({sev})"] + [f"{s['flags'].get(k, 0):,}" for s in S]))
    L.append(_row(["Items with metadata complete"] + [f"{s['n_metadata_complete']:,} ({pct(s['n_metadata_complete'], s['n_records'])})" for s in S]))
    L.append(_row(["Research articles and reviews with metadata complete"] + [f"{s['n_metadata_complete_research']:,} of {s['n_research']:,} ({pct(s['n_metadata_complete_research'], s['n_research'])})" for s in S]))
    L.append(_row(["Retracted items"] + [f"{s['retracted']:,}" for s in S]))
    L += ["", "Table: Validation flags raised (a record can carry several).", ""]

    L += ["## Run log", "", _row(hdr), _row(sep)]
    L.append(_row(["Started (UTC)"] + [s["started"] for s in S]))
    L.append(_row(["Elapsed (minutes)"] + [f"{s['elapsed_s'] / 60:.1f}" for s in S]))
    L.append(_row(["API requests (network, cached replays)"] +
                  [f"{s['http']['network']:,}, {s['http']['cache']:,}" for s in S]))
    L += ["", "Table: Run times and request counts. Queries as run are listed in run.json in each run directory.", ""]
    return "\n".join(L) + "\n"
