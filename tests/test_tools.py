import json

from nutribot.catalog import Catalog
from nutribot.tools import Session, run_tool

CAT = Catalog.load()


def call(session, name, **args):
    out, is_error = run_tool(session, name, args)
    return json.loads(out), is_error


def test_recommend_refused_until_screening_complete():
    s = Session(catalog=CAT)
    pid = CAT.find_by_title("Antioxidant Vitamin-C 500mg").id
    out, err = call(s, "recommend_products", items=[{"product_id": pid, "why_for_this_user": "x"}])
    assert err and "Screening incomplete" in out["error"]
    assert s.recommendations == []


def test_recommend_rejects_excluded_product_for_pregnant_user():
    s = Session(catalog=CAT)
    call(s, "update_user_profile", sex="female", pregnancy_status="pregnant",
         conditions=["none"], medications=["none"], allergies=["none"])
    bad = CAT.find_by_title("Melatonin 5mg").id
    good = CAT.find_by_title("Folic Acid 900mcg").id
    out, err = call(s, "recommend_products", items=[{"product_id": bad, "why_for_this_user": "sleep"},
                                                    {"product_id": good, "why_for_this_user": "folate"}])
    assert not err
    assert [c["title"] for c in s.recommendations] == ["Folic Acid 900mcg"]
    assert out["rejected"][0]["status"] == "EXCLUDED"
    assert "Melatonin 5mg" in s.not_recommendable_seen


def test_search_splits_results_by_safety():
    s = Session(catalog=CAT)
    call(s, "update_user_profile", sex="female", pregnancy_status="pregnant",
         conditions=["none"], medications=["none"], allergies=["none"])
    out, _ = call(s, "search_products", query="sleep melatonin")
    assert not out["suitable"]
    assert any("Melatonin" in r["title"] for r in out["not_suitable"])


def test_profile_lists_accumulate_and_none_does_not_erase():
    s = Session(catalog=CAT)
    call(s, "update_user_profile", medications=["warfarin"])
    call(s, "update_user_profile", medications=["metformin"])
    call(s, "update_user_profile", medications=["none"])
    assert s.profile.medications == ["warfarin", "metformin"]
    assert {"anticoagulant", "diabetes_medication"} <= s.profile.risk_factors()


def test_compare_reports_overlap_and_safety():
    s = Session(catalog=CAT)
    a = CAT.find_by_title("Multivitamins And Minerals").id
    b = CAT.find_by_title("Zinc Citrate High Absorption 15mg").id
    out, err = call(s, "compare_products", product_ids=[a, b])
    assert not err
    assert {r["title"] for r in out["products"]} == {"Multivitamins And Minerals", "Zinc Citrate High Absorption 15mg"}
    assert any(t["nutrient"] == "zinc" for t in out["if_taken_together"]["daily_totals_vs_upper_limits"])
    assert all("safety_for_this_user" in r for r in out["products"])


def test_unknown_ids_are_errors_not_crashes():
    s = Session(catalog=CAT)
    out, err = call(s, "get_product_details", product_id="nope")
    assert err
    out, err = call(s, "compare_products", product_ids=["nope", "nada"])
    assert err
    out, err = call(s, "search_products", wrong_arg=1)
    assert err and "invalid arguments" in out["error"]
