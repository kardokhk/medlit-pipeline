"""Package a small, browsable sample of finished runs.

All record-level outputs (records.csv, flags.csv, records.jsonl) are kept in
full; full-text folders are copied for a handful of Creative Commons-licensed
articles chosen to show
the cases that matter (tables, packaged and repository supplements, an
image-only table, a retracted article, an open copy that is not the version
of record), with large files left out to keep the sample small.

usage: python tools/make_sample.py OUT_DIR RUN_DIR [RUN_DIR ...]
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

MAX_FILE = 1_500_000  # bytes; larger PDFs and supplements are listed but not copied


def pick(recs: list[dict], n: int = 6) -> list[dict]:
    chosen, seen = [], set()

    def shareable(r):
        # full-text files are copied only for Creative Commons articles
        return r["access"]["route"] == "not_retrieved" or str(r["access"]["licence"] or "").startswith("CC")

    def take(pred, why):
        for r in recs:
            if r["record_id"] not in seen and shareable(r) and pred(r):
                seen.add(r["record_id"])
                chosen.append((r, why))
                return

    codes = lambda r: {f["code"] for f in r["flags"]}  # noqa: E731
    take(lambda r: r["category"] == "research" and r["completeness"]["fulltext_complete"]
         and len(r["files"].get("supplement", [])) >= 2, "complete research article with supplements")
    take(lambda r: r["files"].get("supplement_external") and any(not f.get("error") and not f.get("skipped")
                                                                 for f in r["files"]["supplement_external"]),
         "supplements retrieved from a data repository")
    take(lambda r: "TABLE_IMAGE_ONLY" in codes(r), "table supplied only as an image")
    take(lambda r: "RETRACTED" in codes(r), "retracted article")
    take(lambda r: "OPEN_COPY_NOT_VERSION_OF_RECORD" in codes(r), "open copy that is not the version of record")
    take(lambda r: r["category"] == "review" and r["completeness"]["fulltext_complete"], "review article")
    take(lambda r: r["access"]["route"] == "not_retrieved" and r["category"] == "research",
         "research article without a licensed full text")
    return chosen[:n + 2]


def main(out: Path, runs: list[Path]) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index = []
    for run in runs:
        dest = out / run.name
        dest.mkdir()
        for f in ("records.csv", "flags.csv", "records.jsonl", "run.json"):
            shutil.copy2(run / f, dest / f)
        recs = [json.loads(line) for line in open(run / "records.jsonl", encoding="utf-8")]
        ids = []
        for rec, why in pick(recs):
            rid = rec["record_id"]
            ids.append(rid)
            index.append({"collection": run.name, "record_id": rid, "doi": rec["doi"], "why": why,
                          "title": rec["title"]})
            src = run / "articles" / rid
            if not src.exists():
                continue
            for p in src.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(src)
                    target = dest / "articles" / rid / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if p.stat().st_size <= MAX_FILE or p.suffix in (".xml", ".json", ".csv"):
                        shutil.copy2(p, target)
                    else:
                        target.with_name(target.name + ".NOT_INCLUDED.txt").write_text(
                            f"{p.stat().st_size:,} bytes; left out of the sample to keep it small.\n")
        # SQLite with every record, flag, file and reference, and full text sections for the sample only
        db_src = sqlite3.connect(str(run / "medlit.sqlite"))
        db_dst = sqlite3.connect(str(dest / "medlit_sample.sqlite"))
        db_src.backup(db_dst)
        db_src.close()
        q = ",".join("?" * len(ids)) or "''"
        db_dst.execute(f"DELETE FROM sections WHERE record_id NOT IN ({q})", ids)
        db_dst.commit()
        db_dst.execute("VACUUM")
        db_dst.close()
    (out / "sample_index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])
