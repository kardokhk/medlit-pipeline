import pytest

from medlit.pipeline import normalize_licence
from medlit.validate import _issue, open_copy_checks


@pytest.mark.parametrize("code,art_url,expected", [
    ("CC BY", None, "CC BY"),
    ("cc-by-nc-nd", None, "CC BY-NC-ND"),
    ("CC BY-NC-SA", None, "CC BY-NC-SA"),
    (None, "https://creativecommons.org/licenses/by-nc/4.0/", "CC BY-NC"),
    (None, "https://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
    ("TDM", None, "text mining permitted (author manuscript)"),
    (None, "https://www.diabetesjournals.org/journals/pages/license", "publisher licence (www.diabetesjournals.org)"),
])
def test_normalize_licence(code, art_url, expected):
    art = {"license": {"url": art_url, "type": None, "text": None}} if art_url else None
    assert normalize_licence(code, art) == expected


def test_issue_normalisation():
    assert _issue("Supplement_1") == _issue("1 Suppl 1") == "s1"
    assert _issue("3") == "3" and _issue("3") != _issue("4")


def test_open_copy_version():
    assert open_copy_checks({"url": "x", "version": "publishedVersion"}) == []
    assert open_copy_checks({"url": "x", "version": "submittedVersion"})[0]["code"] == "OPEN_COPY_NOT_VERSION_OF_RECORD"
    assert open_copy_checks(None) == []


def test_article_category():
    from medlit.pipeline import article_category
    pm = {"publication_types": ["Journal Article"]}
    assert article_category(pm, None, None, "Comment on Smith et al. A trial") == "letter"
    assert article_category(pm, None, None, "Response to Comment on Smith et al.") == "letter"
    assert article_category(pm, None, None, "A cohort study of X") == "research"
    assert article_category({"publication_types": ["Published Erratum"]}, None, None, "Correction to: X") == "correction"
    assert article_category(None, {"type": "journal-article"}, None, "Issues and Events") == "front matter (not in PubMed)"
