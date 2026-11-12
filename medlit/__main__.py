"""Command line: python -m medlit run CONFIG [--out DIR] [--offline]."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from . import pipeline, report
from .http import Client


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="medlit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="harvest, link, retrieve, validate and export one collection")
    r.add_argument("config", type=Path)
    r.add_argument("--out", type=Path, default=Path("runs"))
    r.add_argument("--cache", type=Path, default=None, help="HTTP cache (default: OUT/http_cache.sqlite)")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--limit", type=int, default=None, help="retrieve full texts for the first N records only")
    r.add_argument("--offline", action="store_true", help="replay cached responses; fail on anything not cached")
    rp = sub.add_parser("report", help="coverage report for one or more finished runs")
    rp.add_argument("runs", type=Path, nargs="+")
    rp.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "run":
        cfg = yaml.safe_load(args.config.read_text())
        run_dir = args.out / cfg["name"]
        client = Client(args.cache or args.out / "http_cache.sqlite", offline=args.offline)
        meta = pipeline.run(client, cfg, run_dir, workers=args.workers, limit=args.limit)
        logging.info("done: %d records in %.0f s; http %s", meta["n_records"], meta["elapsed_s"], meta["http"])
    elif args.cmd == "report":
        report.build(args.runs, args.out)


if __name__ == "__main__":
    main()
