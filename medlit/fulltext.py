"""Full-text retrieval with an explicit access route for every record.

Routes, in order of preference:
  1. PMC Article Datasets (AWS Open Data): JATS XML, PDF and supplementary
     files with published MD5 checksums; licence stated per article.
  2. Europe PMC full-text XML for open-access articles missing from (1).
  3. An openly licensed (Creative Commons) PDF found through OpenAlex at the
     publisher or a repository, fetched as a single polite request. Its version
     (published, accepted manuscript or preprint) is recorded and checked.
Supplementary files deposited in figshare or Zenodo and linked from the text
are fetched through those repositories' APIs when their licence allows,
and verified against the MD5 checksums the repositories publish.
Anything else is not downloaded. It is recorded with the route that would
give lawful access (publisher text-and-data-mining API under the client's
licence, or a manual request), so the gap is visible in the coverage report.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from . import jats
from .sources import europepmc, pmc_cloud

MAGIC = {b"%PDF": "pdf", b"<?xm": "xml", b"<!DO": "html", b"<htm": "html", b"<HTM": "html",
         b"PK\x03\x04": "zip"}


def magic(path: Path) -> str:
    with open(path, "rb") as fh:
        head = fh.read(4)
    return MAGIC.get(head, "other")


def _is_cc(license_str: str | None) -> bool:
    return bool(license_str) and (license_str.lower().startswith("cc") or "creativecommons" in license_str.lower())


def _stem(name: str) -> str:
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def retrieve(client, rec: dict, art_dir: Path, *, pdf: bool = True, supplements: bool = True,
             figures: bool = False, publisher_oa: bool = True) -> dict:
    """Try the routes for one merged record. Returns a result dict; never raises
    for an unavailable article (that is an outcome, not an error)."""
    result = {"route": None, "licence": None, "files": {"xml": [], "pdf": [], "supplement": [], "figure": []},
              "article": None, "attempts": []}
    pmcid = rec.get("pmcid")
    if pmcid:
        man = pmc_cloud.manifest(client, pmcid)
        result["attempts"].append({"route": "pmc_cloud", "found": bool(man)})
        if man:
            return _from_pmc_cloud(client, man, art_dir, result, pdf, supplements, figures)
        if rec.get("epmc_open_access"):
            xml = europepmc.fulltext_xml(client, pmcid)
            result["attempts"].append({"route": "europepmc_xml", "found": bool(xml)})
            if xml:
                return _from_europepmc(client, pmcid, xml, art_dir, result, supplements)
    oa = rec.get("openalex") or {}
    if publisher_oa and oa.get("best_oa_pdf") and _is_cc(oa.get("best_oa_license")):
        dest = art_dir / "article.pdf"
        try:
            dl = client.download(oa["best_oa_pdf"], dest)
            kind = magic(dest)
            dl.update({"name": dest.name, "url": oa["best_oa_pdf"], "magic": kind})
            result["attempts"].append({"route": "open_copy_pdf", "found": kind == "pdf"})
            if kind == "pdf":
                result["files"]["pdf"].append(dl)
                result.update(route="open_copy", licence=oa.get("best_oa_license"),
                              open_copy={"url": oa["best_oa_pdf"], "version": oa.get("best_oa_version")})
                return result
            dest.unlink(missing_ok=True)  # an HTML challenge page is not a full text
        except Exception as exc:  # noqa: BLE001 - outcome is recorded, run continues
            result["attempts"].append({"route": "open_copy_pdf", "found": False, "error": str(exc)[:200]})
    result["route"] = "not_retrieved"
    return result


def _from_pmc_cloud(client, man, art_dir, result, pdf, supplements, figures):
    result["route"] = "pmc_author_manuscript" if man.get("is_manuscript") else "pmc_open_access"
    result["licence"] = man.get("license_code")
    result["pmc_manifest"] = {k: man.get(k) for k in ("pmcid", "version", "license_code", "is_pmc_openaccess",
                                                     "is_manuscript", "is_retracted", "citation",
                                                     "manifest_url", "retrieved_at")}
    url, name, md5 = pmc_cloud.s3_to_https(man["xml_url"])
    dl = client.download(url, art_dir / name, md5)
    dl.update(name=name, url=url)
    result["files"]["xml"].append(dl)
    art = jats.parse(Path(dl["path"]).read_bytes())
    result["article"] = art
    if pdf and man.get("pdf_url"):
        url, name, md5 = pmc_cloud.s3_to_https(man["pdf_url"])
        dl = client.download(url, art_dir / name, md5)
        dl.update(name=name, url=url, magic=magic(Path(dl["path"])))
        result["files"]["pdf"].append(dl)
    supp_stems = {_stem(h) for s in art["supplements"] for h in s["hrefs"]}
    for media in man.get("media_urls") or []:
        url, name, md5 = pmc_cloud.s3_to_https(media)
        role = "supplement" if _stem(name) in supp_stems else "figure"
        if (role == "supplement" and not supplements) or (role == "figure" and not figures):
            result["files"][role].append({"name": name, "url": url, "md5": md5, "skipped": True})
            continue
        sub = "supplements" if role == "supplement" else "figures"
        try:
            dl = client.download(url, art_dir / sub / name, md5)
            dl.update(name=name, url=url)
        except Exception as exc:  # noqa: BLE001
            dl = {"name": name, "url": url, "error": str(exc)[:200]}
        result["files"][role].append(dl)
    if supplements:
        _external(client, art, art_dir, result)
    return result


def _external(client, art, art_dir, result) -> None:
    result["files"].setdefault("supplement_external", [])
    for ext in art.get("external_supplements", []):
        try:
            files, lic = repository_files(client, ext)
        except Exception as exc:  # noqa: BLE001
            result["files"]["supplement_external"].append({"name": ext["id"], "url": ext["href"],
                                                           "error": str(exc)[:200]})
            continue
        if files is None:
            reason = ("project page (registration or protocol), listed not fetched"
                      if ext.get("kind") == "linked project" else "repository not supported")
            result["files"]["supplement_external"].append({"name": ext["id"], "url": ext["href"], "source_id": ext["id"],
                                                           "skipped": True, "reason": reason})
            continue
        if not _is_cc(lic):
            result["files"]["supplement_external"].append({"name": ext["id"], "url": ext["href"], "skipped": True,
                                                           "source_id": ext["id"], "reason": f"licence {lic!r}"})
            continue
        for f in files:
            sub = art_dir / "supplements" / "external" / _safe(ext["id"])
            try:
                dl = client.download(f["url"], sub / _safe(f["name"]), f.get("md5"))
                dl.update(name=f["name"], url=f["url"], licence=lic, source_id=ext["id"])
            except Exception as exc:  # noqa: BLE001
                dl = {"name": f["name"], "url": f["url"], "error": str(exc)[:200], "source_id": ext["id"]}
            result["files"]["supplement_external"].append(dl)


def _safe(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:150] or "file"


MAX_EXTERNAL_BYTES = 200 * 1024 * 1024


def repository_files(client, ext: dict):
    """(files, licence) for a figshare or Zenodo deposit; (None, None) otherwise."""
    if ext["repository"] == "figshare":
        num = ext["id"].rsplit(".", 1)[-1]
        r = client.get(f"https://api.figshare.com/v2/articles/{num}")
        if r.status != 200:
            raise RuntimeError(f"figshare {r.status}")
        d = r.json()
        files = [{"name": f["name"], "url": f["download_url"], "md5": f.get("computed_md5"), "size": f.get("size")}
                 for f in d.get("files", []) if (f.get("size") or 0) <= MAX_EXTERNAL_BYTES]
        return files, (d.get("license") or {}).get("name")
    if ext["repository"] == "zenodo":
        num = ext["id"].rsplit(".", 1)[-1]
        r = client.get(f"https://zenodo.org/api/records/{num}")
        if r.status != 200:
            raise RuntimeError(f"zenodo {r.status}")
        d = r.json()
        files = [{"name": f["key"], "url": f["links"]["self"],
                  "md5": (f.get("checksum") or "").replace("md5:", "") or None, "size": f.get("size")}
                 for f in d.get("files", []) if (f.get("size") or 0) <= MAX_EXTERNAL_BYTES]
        lic = ((d.get("metadata") or {}).get("license") or {}).get("id")
        return files, lic
    return None, None


def _from_europepmc(client, pmcid, xml, art_dir, result, supplements):
    result["route"] = "europepmc_open_access"
    dest = art_dir / f"{pmcid}.xml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(xml)
    result["files"]["xml"].append({"name": dest.name, "path": str(dest), "bytes": len(xml),
                                   "url": f"{europepmc.API}/{pmcid}/fullTextXML"})
    art = jats.parse(xml)
    result["article"] = art
    result["licence"] = art["license"]["type"] or art["license"]["url"]
    if supplements and art["supplements"]:
        r = client.get(f"{europepmc.API}/{pmcid}/supplementaryFiles", cache=False)
        if r.status == 200 and r.body[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(r.body)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    out = art_dir / "supplements" / Path(info.filename).name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(info))
                    result["files"]["supplement"].append({"name": out.name, "path": str(out),
                                                          "bytes": info.file_size, "url": r.url})
    if supplements:
        _external(client, art, art_dir, result)
    return result


def lawful_route(rec: dict) -> str:
    """Where a record that was not retrieved could lawfully come from."""
    oa = rec.get("openalex") or {}
    cr = rec.get("crossref") or {}
    tdm = [l for l in cr.get("tdm_links", []) if l.get("intended_application") == "text-mining"]
    if rec.get("pmcid") and not rec.get("epmc_open_access"):
        base = "In PMC to read, not licensed for bulk download"
    elif oa.get("is_oa") and _is_cc(oa.get("best_oa_license")) and not oa.get("best_oa_pdf"):
        base = "Openly licensed repository copy, landing page only"
    elif oa.get("is_oa") and _is_cc(oa.get("best_oa_license")):
        base = "Openly licensed copy, download refused by host"
    elif oa.get("is_oa"):
        base = "Free to read, no reuse licence"
    else:
        base = "Subscription only"
    if tdm:
        return base + "; publisher text-mining link in Crossref"
    return base
