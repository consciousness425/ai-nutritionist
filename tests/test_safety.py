"""Deterministic safety engine: the properties the brief cares most about."""

import pytest

from nutribot.catalog import Catalog
from nutribot.profile import UserProfile
from nutribot.safety import Level, assess, stack_totals

CAT = Catalog.load()


def by_title(title):
    p = CAT.find_by_title(title)
    assert p, f"missing product {title!r}"
    return p


def screened(**kw):
    base = dict(sex="female", pregnancy_status="none", conditions=[], medications=[], allergies=[])
    return UserProfile(**{**base, **kw})


def recommendable(profile):
    return {p.title for p in CAT.canonical if assess(p, profile).recommendable}


# ---- pregnancy: default-deny ---------------------------------------------------------------
def test_pregnancy_only_reviewed_products_are_recommendable():
    pregnant = screened(pregnancy_status="pregnant")
    allowed = recommendable(pregnant)
    assert allowed, "pregnant users should still get some options"
    for title in allowed:
        assert "pregnant-support" in by_title(title).collections


@pytest.mark.parametrize("title", ["Melatonin 5mg", "Crown Care Hair Food", "Ashwagandha Mood+"])
def test_pregnancy_excludes_contraindicated_products(title):
    v = assess(by_title(title), screened(pregnancy_status="pregnant"))
    assert v.level == Level.EXCLUDED


def test_label_do_not_use_if_pregnant_is_excluded_even_without_kb_rule():
    hits = [p for p in CAT.canonical if "do not use if you are pregnant" in p.caution_text.lower()]
    assert hits
    for p in hits:
        assert assess(p, screened(pregnancy_status="breastfeeding")).level == Level.EXCLUDED


# ---- conditions & medications ---------------------------------------------------------------
def test_warfarin_blocks_ginkgo_and_vitamin_k():
    warfarin = screened(sex="male", pregnancy_status=None, medications=["Warfarin 5mg"])
    for p in CAT.canonical:
        if "ginkgo" in p.ingredient_text.lower() or "vitamin k" in p.ingredient_text.lower():
            assert assess(p, warfarin).level == Level.EXCLUDED, p.title


def test_liver_disease_blocks_he_shou_wu():
    v = assess(by_title("Crown Care Hair Food"), screened(conditions=["fatty liver disease"]))
    assert v.level == Level.EXCLUDED
    assert any(f.factor == "liver_disease" for f in v.findings)


def test_fish_allergy_excludes_fish_oil_but_shellfish_allergy_does_not():
    p = by_title("Omega-3 Fish Oil 1000mg")
    assert assess(p, screened(allergies=["fish"])).level == Level.EXCLUDED
    assert assess(p, screened(allergies=["shellfish"])).recommendable


def test_under_18_only_gets_childrens_products():
    teen = screened(age=15)
    for title in recommendable(teen):
        assert by_title(title).is_for_children, title


# ---- fail-closed behaviour ------------------------------------------------------------------
def test_unrecognised_allergy_needs_doctor():
    v = assess(by_title("Antioxidant Vitamin-C 500mg"), screened(allergies=["quinoline yellow"]))
    assert v.level == Level.DOCTOR_FIRST


def test_unrecognised_medication_adds_caution_everywhere():
    prof = screened(medications=["zorbitamab"])
    assert "zorbitamab" in prof.unrecognised()["medications"]
    for p in CAT.canonical:
        assert assess(p, prof).level >= Level.CAUTION


def test_unknown_is_not_none():
    assert UserProfile().missing_screening()
    assert not screened().missing_screening()
    assert "pregnancy_status" not in " ".join(UserProfile(sex="male", conditions=[], medications=[],
                                                          allergies=[]).missing_screening())


def test_healthy_adult_has_wide_choice():
    assert len(recommendable(screened())) > 0.8 * len(CAT.canonical)


# ---- every decision is explained -------------------------------------------------------------
def test_every_non_ok_verdict_has_a_reason():
    profiles = [screened(pregnancy_status="pregnant"), screened(medications=["warfarin"]),
                screened(conditions=["kidney disease"]), screened(age=10)]
    for prof in profiles:
        for p in CAT.canonical:
            v = assess(p, prof)
            if v.level > Level.OK:
                assert all(f.reason for f in v.findings)


# ---- stack checker ---------------------------------------------------------------------------
def test_stack_totals_add_up_across_products():
    totals = {t["nutrient"]: t for t in stack_totals([by_title("Multivitamins And Minerals"),
                                                      by_title("Zinc Citrate High Absorption 15mg")])}
    assert totals["zinc"]["total_per_day"] == pytest.approx(30)
    assert len(totals["zinc"]["sources"]) == 2
