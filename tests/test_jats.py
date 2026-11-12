from conftest import FIXTURES

from medlit import jats
from medlit.validate import fulltext_checks, reference_agreement

ART = jats.parse((FIXTURES / "mini_article.xml").read_bytes())


def test_front_matter():
    assert ART["doi"] == "10.1000/test.2024.001"
    assert ART["pmid"] == "12345678" and ART["pmcid"] == "PMC7654321"
    assert ART["title"] == "Effect of X on outcome Y: a cohort study"
    assert ART["license"]["url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert ART["declared_counts"] == {"table-count": 2, "ref-count": 3}


def test_sections_in_reading_order():
    paths = [s["path"] for s in ART["sections"]]
    assert paths == ["Introduction", "Methods", "Methods > Statistics", "Methods", "Results",
                     "Appendix"]
    results = next(s for s in ART["sections"] if s["path"] == "Results")
    assert "Baseline characteristics" not in results["text"]  # table kept out of prose


def test_table_spans_expanded():
    t1, t2 = ART["tables"]
    assert t1["header_rows"] == 2
    assert t1["grid"] == [
        ["Variable", "Group", "Group"],
        ["Variable", "A", "B"],
        ["Demographics", "", ""],  # group heading kept in the first column only
        ["Age", "50", "52"],
        ["BMI, kg/m^2", "HbA_1c 7", "8"],
        ["Sex", "M 10", "M 12"],
        ["Sex", "F 8", "F 9"],
    ]
    assert t1["rectangular"] and t1["footnotes"] == "Values are counts."
    assert t2["image_only"] and not t2["has_markup"]


def test_references():
    r1, r2, r3 = ART["references"]
    assert r1["doi"] == "10.1016/s0140-6736(20)30001-1" and r1["pmid"] == "111"
    assert r1["first_author"] == "Smith" and r1["year"] == "2020"
    assert "Smith J A trial Lancet" in r1["raw"]
    assert r2["doi"] == "10.1136/bmj.l1234"  # DOI found in free text, trailing full stop dropped
    assert r3["doi"] == "10.5281/zenodo.123"  # from an ext-link inside the citation


def test_supplements_and_checks():
    assert ART["supplements"][0]["hrefs"] == ["supp1.docx"]
    canonical = {"doi": "10.1000/test.2024.001", "title": ART["title"]}
    files = {"xml": [{"name": "a.xml", "md5_ok": True}], "pdf": [], "figure": [],
             "supplement": [{"name": "supp1.docx", "md5_ok": True}]}
    codes = {f["code"] for f in fulltext_checks(ART, files, canonical, None)}
    assert "TABLE_IMAGE_ONLY" in codes
    assert "SUPPLEMENT_NOT_RETRIEVED" not in codes
    assert "SHORT_BODY_TEXT" in codes  # the fixture body is a few words long
    files["supplement"] = []
    files["xml"][0]["md5_ok"] = False
    codes = {f["code"] for f in fulltext_checks(ART, files, canonical, None)}
    assert {"SUPPLEMENT_NOT_RETRIEVED", "CHECKSUM_MISMATCH"} <= codes
    am = {f["code"] for f in fulltext_checks(ART, files, canonical, None, "pmc_author_manuscript")}
    assert "SUPPLEMENT_NOT_DISTRIBUTED" in am and "SUPPLEMENT_NOT_RETRIEVED" not in am


def test_reference_agreement():
    cr = [{"doi": "10.1016/s0140-6736(20)30001-1"}, {"doi": "10.9999/missing"}]
    agr = reference_agreement(ART["references"], cr)
    assert agr["shared"] == 1 and agr["recall_vs_crossref"] == 0.5 and agr["precision_vs_crossref"] == 1 / 3
    assert reference_agreement(ART["references"], []) is None


def test_external_supplements_skip_reference_list():
    ext = ART["external_supplements"]
    assert [(e["repository"], e["kind"], e["id"]) for e in ext] == [
        ("figshare", "supplement", "10.2337/figshare.25726644"), ("osf", "linked project", "g3mdu")]
