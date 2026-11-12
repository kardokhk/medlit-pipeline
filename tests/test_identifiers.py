import pytest

from medlit.identifiers import (normalize_doi, normalize_issn, normalize_pmcid, normalize_pmid,
                                normalize_title, record_id)


@pytest.mark.parametrize("raw,expected", [
    ("10.1186/S12916-024-03754-9", "10.1186/s12916-024-03754-9"),
    ("https://doi.org/10.1000/xyz", "10.1000/xyz"),
    ("http://dx.doi.org/10.1000/xyz", "10.1000/xyz"),
    ("doi: 10.1000/xyz.", "10.1000/xyz"),
    ("doi:https://doi.org/10.1000/abc;", "10.1000/abc"),
    ("10.1000%2Fabc", "10.1000/abc"),
    ("10.1016/S0140-6736(20)30001-1", "10.1016/s0140-6736(20)30001-1"),
    ("(10.1000/abc)", None),  # leading bracket is not part of a DOI
    ("not a doi", None),
    ("", None),
    (None, None),
])
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


def test_pmcid_pmid_issn():
    assert normalize_pmcid("pmc123.2") == "PMC123"
    assert normalize_pmcid("123") == "PMC123"
    assert normalize_pmcid("PMCx") is None
    assert normalize_pmid(" 0039736748 ") == "39736748"
    assert normalize_pmid("0") is None
    assert normalize_issn("1935 5548") == "1935-5548"
    assert normalize_issn("0000-000x") == "0000-000X"
    assert normalize_issn("123") is None


def test_title_and_record_id():
    assert normalize_title("Café <i>au</i> lait: a Trial!") == "cafe au lait a trial"
    assert record_id("10.1/a", "1", None) == record_id("10.1/a", None, "PMC1")
    assert record_id(None, "77", None) == "pmid77"
    with pytest.raises(ValueError):
        record_id(None, None, None)
