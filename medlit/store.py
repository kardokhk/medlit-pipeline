"""Outputs: per-article folders, a JSON Lines file, flat CSVs and one SQLite database."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE records (record_id TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT,
  journal TEXT, issns TEXT, publisher TEXT, year INT, volume TEXT, issue TEXT, pages TEXT,
  category TEXT, crossref_type TEXT, jats_article_type TEXT, n_authors INT, has_abstract INT,
  retracted INT, presence TEXT, access_route TEXT, licence TEXT, lawful_route_if_missing TEXT,
  fulltext INT, pdf INT, n_sections INT, body_words INT, n_tables INT, n_tables_parsed INT,
  n_figures INT, n_references INT, n_references_with_doi INT, n_supplements_declared INT,
  n_supplements_retrieved INT, metadata_complete INT, fulltext_complete INT,
  n_errors INT, n_warnings INT);
CREATE TABLE sources (record_id TEXT, source TEXT, harvested INT, source_id TEXT, source_url TEXT,
  api_url TEXT, retrieved_at TEXT);
CREATE TABLE authors (record_id TEXT, position INT, family TEXT, given TEXT, collective TEXT,
  orcid TEXT, affiliations TEXT);
CREATE TABLE flags (record_id TEXT, code TEXT, severity TEXT, detail TEXT);
CREATE TABLE sections (record_id TEXT, position INT, path TEXT, sec_type TEXT, n_words INT, text TEXT);
CREATE TABLE tables (record_id TEXT, table_id TEXT, label TEXT, caption TEXT, footnotes TEXT,
  n_rows INT, n_cols INT, header_rows INT, image_only INT, rectangular INT, csv_path TEXT);
CREATE TABLE refs (record_id TEXT, position INT, ref_id TEXT, publication_type TEXT,
  first_author TEXT, year TEXT, title TEXT, source TEXT, volume TEXT, fpage TEXT, doi TEXT,
  pmid TEXT, pmcid TEXT, raw TEXT);
CREATE TABLE files (record_id TEXT, role TEXT, name TEXT, bytes INT, md5 TEXT, md5_ok INT,
  url TEXT, skipped INT, error TEXT);
"""


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "table"


def write_article(art_dir: Path, rec: dict, art: dict) -> None:
    art_dir.mkdir(parents=True, exist_ok=True)
    tdir = art_dir / "tables"
    for t in art["tables"]:
        if t["grid"]:
            tdir.mkdir(exist_ok=True)
            t["csv_path"] = f"tables/{_safe(t['label'] or t['id'])}.csv"
            with open(art_dir / t["csv_path"], "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows(t["grid"])
        else:
            t["csv_path"] = None
    with open(art_dir / "references.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["label", "first_author", "year", "title", "source", "volume", "fpage", "doi", "pmid", "pmcid", "raw"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(art["references"])
    out = {"record_id": rec["record_id"], "doi": rec["doi"], "pmid": rec["pmid"], "pmcid": rec["pmcid"],
           "licence": rec["access"]["licence"], **{k: v for k, v in art.items() if k != "tables"},
           "tables": [{k: v for k, v in t.items() if k != "grid"} for t in art["tables"]]}
    (art_dir / "article.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def completeness(rec: dict, art: dict | None) -> dict:
    research = rec.get("category") in ("research", "review")
    meta_fields = {
        "doi": bool(rec["doi"]), "pmid": bool(rec["pmid"]), "title": bool(rec["title"]),
        "authors": bool(rec["authors"]), "journal": bool(rec["journal"]), "year": bool(rec["year"]),
        "volume": bool(rec["volume"]), "pages": bool(rec["pages"]),
        "abstract": bool(rec["abstract"]) or not research,
    }
    out = {"metadata_fields": meta_fields, "metadata_complete": all(meta_fields.values())}
    files = rec.get("files", {})
    if art:
        supp_declared = len(art["supplements"])
        supp_got = sum(1 for f in files.get("supplement", []) if not f.get("error") and not f.get("skipped"))
        tables_ok = sum(1 for t in art["tables"] if t["has_markup"])
        errors = [f for f in rec["flags"] if f["severity"] == "error"]
        ft = {
            "xml": True,
            "pdf": any(f.get("magic") == "pdf" for f in files.get("pdf", [])),
            "body_text": bool(art["sections"]),
            "tables_parsed": f"{tables_ok}/{len(art['tables'])}",
            "references": len(art["references"]),
            "supplements": f"{supp_got}/{supp_declared}",
            "checksums_ok": all(f.get("md5_ok") is not False for v in files.values() for f in v),
        }
        out["fulltext"] = ft
        out["fulltext_complete"] = (ft["body_text"] or not research) and tables_ok == len(art["tables"]) \
            and supp_got >= supp_declared and ft["checksums_ok"] and not errors
    else:
        out["fulltext"] = {"pdf": any(f.get("magic") == "pdf" for f in files.get("pdf", []))}
        out["fulltext_complete"] = False
    return out


def export(run_dir: Path, records: list[dict], meta: dict) -> None:
    write_jsonl(run_dir / "records.jsonl", records)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    db_path = run_dir / "medlit.sqlite"
    if db_path.exists():
        db_path.unlink()
    db = sqlite3.connect(str(db_path))
    db.executescript(SCHEMA)
    for rec in records:
        rid = rec["record_id"]
        art_path = run_dir / "articles" / rid / "article.json"
        art = json.loads(art_path.read_text()) if art_path.exists() else None
        sev = [f["severity"] for f in rec["flags"]]
        comp = rec["completeness"]
        files = rec.get("files", {})
        db.execute("INSERT INTO records VALUES (" + ",".join("?" * 37) + ")", (
            rid, rec["doi"], rec["pmid"], rec["pmcid"], rec["title"], rec["journal"], ";".join(rec["issns"]),
            rec["publisher"], rec["year"], rec["volume"], rec["issue"], rec["pages"], rec["category"],
            rec["crossref_type"], rec["jats_article_type"], len(rec["authors"]), int(bool(rec["abstract"])),
            int(rec["retracted"]), rec["presence"], rec["access"]["route"], rec["access"]["licence"],
            rec["access"]["lawful_route_if_missing"], int(art is not None), int(comp["fulltext"].get("pdf", False)),
            len(art["sections"]) if art else None, art["body_words"] if art else None,
            len(art["tables"]) if art else None,
            sum(1 for t in art["tables"] if t["has_markup"]) if art else None,
            len(art["figures"]) if art else None, len(art["references"]) if art else None,
            sum(1 for r in art["references"] if r.get("doi")) if art else None,
            len(art["supplements"]) if art else None,
            sum(1 for f in files.get("supplement", []) if not f.get("error") and not f.get("skipped")) if art else None,
            int(comp["metadata_complete"]), int(comp["fulltext_complete"]),
            sev.count("error"), sev.count("warning")))
        for s in rec["sources"]:
            db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?)", (rid, s["source"], int(s["harvested"]), s["id"],
                                                                     s["source_url"], s["api_url"], s["retrieved_at"]))
        for i, a in enumerate(rec["authors"], 1):
            db.execute("INSERT INTO authors VALUES (?,?,?,?,?,?,?)", (rid, i, a.get("family"), a.get("given"),
                                                                     a.get("collective"), a.get("orcid"),
                                                                     " | ".join(a.get("affiliations") or [])))
        for f in rec["flags"]:
            db.execute("INSERT INTO flags VALUES (?,?,?,?)", (rid, f["code"], f["severity"], f["detail"]))
        for role, items in files.items():
            for f in items:
                db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)", (
                    rid, role, f.get("name"), f.get("bytes"), f.get("md5"),
                    None if f.get("md5_ok") is None else int(f["md5_ok"]), f.get("url"),
                    int(bool(f.get("skipped"))), f.get("error")))
        if art:
            for i, s in enumerate(art["sections"], 1):
                db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?)", (rid, i, s["path"], s["sec_type"],
                                                                        s["n_words"], s["text"]))
            for t in art["tables"]:
                db.execute("INSERT INTO tables VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    rid, t["id"], t["label"], t["caption"], t["footnotes"], t["n_rows"], t["n_cols"],
                    t["header_rows"], int(t["image_only"]), int(t["rectangular"]),
                    f"articles/{rid}/{t['csv_path']}" if t.get("csv_path") else None))
            for i, r in enumerate(art["references"], 1):
                db.execute("INSERT INTO refs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    rid, i, r["id"], r["publication_type"], r["first_author"], r["year"], r["title"], r["source"],
                    r["volume"], r["fpage"], r["doi"], r["pmid"], r["pmcid"], r["raw"]))
    db.commit()
    # flat CSV of the record table for spreadsheet users
    cur = db.execute("SELECT * FROM records ORDER BY year, journal, record_id")
    cols = [d[0] for d in cur.description]
    with open(run_dir / "records.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(cur.fetchall())
    cur = db.execute("SELECT * FROM flags ORDER BY severity, code, record_id")
    with open(run_dir / "flags.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur.fetchall())
    db.close()
