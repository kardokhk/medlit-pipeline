"""Polite HTTP client: per-host pacing, retries with back-off, and a response cache.

The cache is a single SQLite file, so a rerun replays the exact API responses
(and their retrieval times) instead of hitting the services again, and the
run directory does not fill up with thousands of small files.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

USER_AGENT = "medlit-pipeline/0.1 (+https://github.com/kardokhk/medlit-pipeline)"

# Minimum seconds between requests to the same host. NCBI allows 3 requests/s
# without an API key; Crossref's public pool asks for modest, serial traffic.
DEFAULT_INTERVALS = {
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "pmc.ncbi.nlm.nih.gov": 0.34,
    "www.ebi.ac.uk": 0.12,
    "api.crossref.org": 0.25,
    "api.openalex.org": 0.12,
    "pmc-oa-opendata.s3.amazonaws.com": 0.0,
    "s3.amazonaws.com": 0.0,
}


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    fetched_at: str
    from_cache: bool

    def json(self):
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Client:
    def __init__(self, cache_path: Path | str | None, intervals: dict | None = None,
                 timeout: float = 180.0, retries: int = 5, offline: bool = False):
        self.intervals = dict(DEFAULT_INTERVALS, **(intervals or {}))
        self.retries = retries
        self.offline = offline
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self.http = httpx.Client(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
        self.db = None
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(cache_path), check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, url TEXT, status INT,"
                " fetched_at TEXT, body BLOB)")
            self.db.commit()
        self.stats = {"network": 0, "cache": 0, "retries": 0}

    @staticmethod
    def full_url(url: str, params: dict | None) -> str:
        if not params:
            return url
        items = sorted((k, v) for k, v in params.items() if v is not None)
        return f"{url}?{urlencode(items)}"

    def _pace(self, host: str) -> None:
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            gap = self.intervals.get(host, 0.2)
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, *, cache: bool = True,
            ok_statuses: tuple = (200, 404)) -> Response:
        full = self.full_url(url, params)
        key = hashlib.sha256(full.encode()).hexdigest()
        if cache and self.db is not None:
            with self._lock:
                row = self.db.execute(
                    "SELECT status, fetched_at, body FROM cache WHERE key=?", (key,)).fetchone()
            if row:
                self.stats["cache"] += 1
                return Response(full, row[0], zlib.decompress(row[2]), row[1], True)
        if self.offline:
            raise RuntimeError(f"offline mode and not cached: {full}")
        host = urlsplit(full).netloc
        delay = 2.0
        for attempt in range(self.retries + 1):
            self._pace(host)
            r = None
            try:
                r = self.http.get(full)
                status, body = r.status_code, r.content
            except httpx.HTTPError:
                status, body = -1, b""
            if status in ok_statuses:
                break
            if attempt == self.retries:
                raise RuntimeError(f"GET failed after {attempt + 1} attempts ({status}): {full}")
            self.stats["retries"] += 1
            retry_after = r.headers.get("Retry-After") if r is not None else None
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
            delay = min(delay * 2, 120)
        self.stats["network"] += 1
        fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if cache and self.db is not None:
            with self._lock:
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                                (key, full, status, fetched, zlib.compress(body)))
                self.db.commit()
        return Response(full, status, body, fetched, False)

    def download(self, url: str, dest: Path, expected_md5: str | None = None) -> dict:
        """Stream a file to disk and verify its MD5 when one is published."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and expected_md5 and _md5(dest) == expected_md5:
            return {"path": str(dest), "bytes": dest.stat().st_size, "md5": expected_md5,
                    "md5_ok": True, "from_cache": True}
        if self.offline:
            if dest.exists() and not expected_md5:  # no published checksum: replay what was saved
                return {"path": str(dest), "bytes": dest.stat().st_size, "md5": _md5(dest),
                        "md5_ok": None, "from_cache": True}
            raise RuntimeError(f"offline mode and not on disk (or checksum differs): {url}")
        host = urlsplit(url).netloc
        tmp = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(self.retries + 1):
            self._pace(host)
            try:
                h = hashlib.md5()
                with self.http.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise httpx.HTTPStatusError(str(r.status_code), request=r.request, response=r)
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 16):
                            fh.write(chunk)
                            h.update(chunk)
                break
            except httpx.HTTPError:
                if attempt == self.retries:
                    raise
                self.stats["retries"] += 1
                time.sleep(2 * (attempt + 1))
        tmp.replace(dest)
        md5 = h.hexdigest()
        return {"path": str(dest), "bytes": dest.stat().st_size, "md5": md5,
                "md5_ok": (md5 == expected_md5) if expected_md5 else None, "from_cache": False}


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
