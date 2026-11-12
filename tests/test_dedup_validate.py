from medlit.dedup import link
from medlit.validate import metadata_checks


def rec(source, **kw):
    base = {"source": source, "doi": None, "pmid": None, "pmcid": None, "title": "", "harvested": True}
    base.update(kw)
    return base


T = "Long enough title about heart failure outcomes"


def test_identifier_linkage_and_chain():
    recs = [rec("crossref", doi="10.1/a", title=T),
            rec("pubmed", doi="10.1/a", pmid="1", title=T),
            rec("europepmc", pmid="1", pmcid="PMC9", title=T),
            rec("crossref", doi="10.1/b", title="A different paper on diabetes care")]
    cl = link(recs)
    assert len(cl) == 2
    big = max(cl, key=lambda c: len(c["members"]))
    assert big["dois"] == ["10.1/a"] and big["pmids"] == ["1"] and big["pmcids"] == ["PMC9"]
    assert not big["id_conflict"]


def test_conflict_is_flagged_not_hidden():
    recs = [rec("pubmed", doi="10.1/a", pmid="1", title=T),
            rec("europepmc", doi="10.1/zzz", pmid="1", title=T)]
    cl = link(recs)
    assert len(cl) == 1 and cl[0]["id_conflict"]
    assert any(f["code"] == "ID_CONFLICT" for f in metadata_checks(cl[0]))


def test_title_linkage_rules():
    a = rec("crossref", doi="10.1/a", title=T, published={"year": 2024})
    b = rec("pubmed", pmid="5", title=T + ".", pub_date={"year": 2024})
    assert len(link([a, b])) == 1 and link([a, b])[0]["linked_by_title"]
    # same source on both sides: two distinct items, never merged
    c = rec("crossref", doi="10.1/c", title=T, published={"year": 2024})
    assert len(link([a, c])) == 2
    # years too far apart
    d = rec("pubmed", pmid="6", title=T, pub_date={"year": 2019})
    assert len(link([a, d])) == 2
    # correction notices copy the title of the article they correct
    e = rec("crossref", doi="10.1/e", title="Correction to: " + T, published={"year": 2024})
    f = rec("pubmed", pmid="7", title="Correction to: " + T, pub_date={"year": 2024})
    assert len(link([e, f])) == 2


def test_metadata_mismatches():
    cr = rec("crossref", doi="10.1/a", title=T, published={"year": 2023}, volume="12", issue="3",
             pages="100-110", authors=[{"family": "Smith"}], issns=["1111-1111"])
    pm = rec("pubmed", doi="10.1/a", pmid="1", title=T, pub_date={"year": 2024}, volume="12", issue="4",
             pages="100", authors=[{"family": "Jones"}], issns=["1111-1111"], corrections=[],
             publication_types=[])
    codes = {f["code"]: f["severity"] for f in metadata_checks(link([cr, pm])[0])}
    assert codes["YEAR_DIFFERS"] == "info"
    assert codes["ISSUE_MISMATCH"] == "warning"
    assert "PAGES_MISMATCH" not in codes  # same first page
    assert codes["FIRST_AUTHOR_MISMATCH"] == "warning"
    assert "TITLE_MISMATCH" not in codes
