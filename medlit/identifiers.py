"""Identifier and title normalisation.

Every comparison between sources goes through these functions, so they are
covered by known-answer tests in tests/test_identifiers.py.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata

_DOI_PREFIXES = re.compile(
    r"^(?:https?://)?(?:dx\.)?(?:doi\.org/)|^doi:\s*|^urn:doi:", re.IGNORECASE
)
_DOI_VALID = re.compile(r"^10\.\d{4,9}/\S+$")
_TAG = re.compile(r"<[^>]+>")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_doi(value: str | None) -> str | None:
    """Return a lower-case bare DOI, or None if the value is not a DOI.

    Handles resolver prefixes, the ``doi:`` scheme, surrounding whitespace,
    percent-encoded slashes and trailing punctuation picked up from
    reference strings.
    """
    if not value:
        return None
    doi = html.unescape(str(value)).strip()
    doi = doi.replace("%2F", "/").replace("%2f", "/")
    prev = None
    while prev != doi:  # prefixes can be stacked ("doi: https://doi.org/...")
        prev = doi
        doi = _DOI_PREFIXES.sub("", doi).strip()
    doi = doi.rstrip(".,;:)]}>​ ").lower()
    # an unbalanced "(" left after stripping ")" means the ")" belonged to the DOI
    if doi.count("(") > doi.count(")") and str(value).rstrip().endswith(")"):
        doi += ")"
    return doi if _DOI_VALID.match(doi) else None


def normalize_pmcid(value: str | int | None) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip().upper()
    if s.startswith("PMC"):
        s = s[3:]
    s = s.split(".")[0]  # drop a version suffix such as PMC123.2
    return f"PMC{int(s)}" if s.isdigit() and int(s) > 0 else None


def normalize_pmid(value: str | int | None) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    return str(int(s)) if s.isdigit() and int(s) > 0 else None


def normalize_title(value: str | None) -> str:
    """Case-folded, accent-stripped, punctuation-free title for matching."""
    if not value:
        return ""
    t = html.unescape(_TAG.sub(" ", str(value)))
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return _NON_ALNUM.sub(" ", t).strip()


def normalize_issn(value: str | None) -> str | None:
    if not value:
        return None
    s = re.sub(r"[^0-9Xx]", "", str(value)).upper()
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 else None


def record_id(doi: str | None, pmid: str | None, pmcid: str | None) -> str:
    """Stable record identifier: derived from the DOI when there is one."""
    if doi:
        return "d" + hashlib.sha1(doi.encode()).hexdigest()[:12]
    if pmid:
        return f"pmid{pmid}"
    if pmcid:
        return pmcid.lower()
    raise ValueError("record has no identifier")
