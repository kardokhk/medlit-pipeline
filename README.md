# medlit-pipeline

Collects biomedical journal articles at scale with full text, tables, references and supplementary files, matches and deduplicates records across sources, validates the metadata, and reports exactly what was collected, what was not, and why.

It downloads only through routes whose terms allow automated retrieval and reuse (PMC Cloud Service, Europe PMC open access, Creative Commons copies, and data repositories), and records the licence for every item. Items without such a route are listed with the route that would give lawful access, not fetched.

## What it does

| Step | Sources | Output |
|---|---|---|
| Harvest | Crossref (every DOI the publisher registered), PubMed E-utilities, Europe PMC | Source records with the exact request and retrieval time |
| Link | DOI, PMID, PMCID; then title, year and journal (similarity at least 0.95, labelled) | One record per article; identifier conflicts flagged, never merged silently |
| Explain gaps | Look up each missing item in the source that lacks it | "Indexed but dated outside the window" or "not indexed" for each item |
| Enrich | NCBI ID Converter, OpenAlex | PMCIDs, open-access status and licence |
| Retrieve | PMC Cloud Service (AWS), Europe PMC, open copies, figshare and Zenodo | JATS XML, PDF, supplementary files; MD5 checked against the published value |
| Parse | JATS XML | Sections in reading order, tables as CSV (merged cells expanded), figure captions, references with DOI and PMID |
| Validate | Crossref against PubMed against Europe PMC; full text against declared contents | Flags with severity: identifier conflicts, title, year, volume, issue, page and author mismatches, retractions, corrections, missing supplements, image-only tables, checksum failures |
| Report | All of the above | SQLite database, CSV and JSON Lines exports, per-article folders, coverage report with figures |

## Run

Python 3.10 or later.

```sh
pip install -r requirements.txt
python -m pytest -q tests
python -m medlit run configs/bmc_medicine_2024.yaml --out runs
python -m medlit run configs/diabetes_care_2024.yaml --out runs
python -m medlit report runs/bmc_medicine_2024 runs/diabetes_care_2024 --out runs/report
```

or `bash run_all.sh`. A collection is defined by a small YAML file:

```yaml
name: diabetes_care_2024
label: Diabetes Care, 2024
mode: journal            # or "query" with pubmed_query and europepmc_query
issns: ["0149-5992", "1935-5548"]
years: [2024, 2024]
fulltext: {pdf: true, supplements: true, figures: false, publisher_oa: true}
```

Every API response is cached in `runs/http_cache.sqlite`. An interrupted run resumes from the cache, and `--offline` rebuilds all outputs from the cached responses and the files already in the run directory, without network access (checked on the proof of concept: identical `records.csv` and `flags.csv`).

## Outputs (per collection)

| Path | Contents |
|---|---|
| `medlit.sqlite` | Tables `records`, `sources`, `authors`, `flags`, `sections`, `tables`, `refs`, `files` |
| `records.csv`, `records.jsonl` | One row per article: identifiers, metadata, access route, licence, completeness, flag counts |
| `flags.csv` | Every validation flag with its severity and detail |
| `articles/<record_id>/` | `article.json` (structured full text), `tables/*.csv`, `references.csv`, the XML and PDF, `supplements/` |
| `run.json` | Configuration, queries as run with hit counts, timings and request counts |

`record_id` is derived from the DOI (or the PMID when there is no DOI), so it is stable across runs.

## Proof of concept

BMC Medicine 2024 (fully open access) and Diabetes Care 2024 (hybrid). The coverage report produced by `medlit report` sets out the counts, routes, licences, completeness and validation results for both.

## Politeness

Requests are paced per host (NCBI 3 per second without an API key; Crossref public pool), retried with back-off, and identified by a User-Agent that names this repository. No email address is sent. Add an NCBI API key or a Crossref contact address in `medlit/http.py` for larger collections.

## Limitations

- Tables supplied only as images are flagged, not converted; they need OCR.
- Repository supplements are fetched from figshare and Zenodo; Dryad and OSF links are listed, not fetched.
- Scopus and publisher text-mining APIs need the user's own keys and entitlement and are not wired in.
- ESearch returns at most 9,999 PMIDs per query; larger sets must be split by date.

## Licence

MIT
