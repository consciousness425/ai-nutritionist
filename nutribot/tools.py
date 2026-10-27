"""Tools exposed to the LLM, and their handlers.

Design rule: the model never gets a product without the safety verdict attached, and
the only way to put a product in front of the user as a recommendation is
`recommend_products`, which re-checks eligibility in code and refuses violations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .catalog import Catalog, Product
from .profile import UserProfile
from .safety import Level, assess, stack_totals


@dataclass
class Session:
    catalog: Catalog
    profile: UserProfile = field(default_factory=UserProfile)
    messages: list = field(default_factory=list)
    # Per-turn outputs, reset at the start of each user turn.
    recommendations: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    not_recommendable_seen: dict[str, str] = field(default_factory=dict)  # title -> status

    def new_turn(self) -> None:
        self.recommendations, self.trace, self.not_recommendable_seen = [], [], {}


TOOLS = [
    {
        "name": "update_user_profile",
        "description": (
            "Record facts the user has told you about the person who will take the supplement. "
            "Call this as soon as the user mentions any of these, before searching. Only include "
            "fields the user actually stated; never guess. Use [\"none\"] when the user says they "
            "have none. Lists are merged with what is already stored. Returns the stored profile, "
            "derived risk factors, and which screening questions are still unanswered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "age": {"type": "integer"},
                "sex": {"type": "string", "enum": ["female", "male", "other"]},
                "pregnancy_status": {
                    "type": "string",
                    "enum": ["pregnant", "breastfeeding", "trying_to_conceive", "none", "not_applicable"],
                },
                "conditions": {"type": "array", "items": {"type": "string"},
                               "description": "Medical conditions in the user's words, e.g. 'type 2 diabetes'"},
                "medications": {"type": "array", "items": {"type": "string"},
                                "description": "Medicines in the user's words, e.g. 'warfarin', 'the pill'"},
                "allergies": {"type": "array", "items": {"type": "string"}},
                "diet": {"type": "array", "items": {"type": "string", "enum": ["vegan", "vegetarian", "halal"]}},
                "goals": {"type": "array", "items": {"type": "string"},
                          "description": "What they want help with, e.g. 'sleep', 'joint comfort'"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_products",
        "description": (
            "Search the store catalogue. Each result carries a safety status computed for the "
            "current profile: results are split into `suitable` (OK/CAUTION), "
            "`needs_doctor_approval` and `not_suitable`. Run several searches with different "
            "wording (ingredient names, benefit words) when a need is broad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Benefit, ingredient or product words"},
                "category": {"type": "string",
                             "description": "Optional, e.g. Joint, Eye, Heart, Skin, Sleep, Gut & Digestion"},
                "diet": {"type": "array", "items": {"type": "string", "enum": ["vegan", "vegetarian", "halal"]}},
                "max_price_sgd": {"type": "number"},
                "form": {"type": "string", "description": "capsule, tablet, softgel, sachet, gummy..."},
                "for_children": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 15},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product_details",
        "description": "Full label for one product: ingredients with doses, dosage, warnings, "
                       "free-from claims, price, and its safety verdict for this profile.",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_products",
        "description": (
            "Side-by-side comparison of 2-5 products for this user: actives and daily amounts, "
            "shared vs. unique ingredients, form, diet suitability, price, safety verdict for this "
            "profile, and the combined daily intake vs. upper limits if taken together. Use it for "
            "any 'which is better for me' or 'can I take these together' question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
            },
            "required": ["product_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recommend_products",
        "description": (
            "Present products to the user as recommendations (rendered as product cards). This is "
            "the ONLY way to recommend. It is rejected if screening questions are unanswered or a "
            "product is not suitable for this profile. Call it once per turn, at the end, with "
            "1-4 products, and then write your reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "why_for_this_user": {"type": "string"},
                        },
                        "required": ["product_id", "why_for_this_user"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
]


# ------------------------------------------------------------------------------------------
def run_tool(session: Session, name: str, args: dict) -> tuple[str, bool]:
    """Execute a tool. Returns (json_result, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        result = handler(session, **args)
        is_error = isinstance(result, dict) and "error" in result
    except TypeError as e:  # bad/missing arguments
        result, is_error = {"error": f"invalid arguments: {e}"}, True
    session.trace.append({"tool": name, "input": args, "error": is_error,
                          "summary": _summarise(name, result)})
    return json.dumps(result, ensure_ascii=False), is_error


def _verdict_brief(session: Session, p: Product) -> dict:
    v = assess(p, session.profile)
    if not v.recommendable:
        session.not_recommendable_seen[p.title] = v.level.name
    return v.as_dict()


def _screening(session: Session) -> dict:
    missing = session.profile.missing_screening()
    return {"screening_complete": not missing, "missing_screening": missing,
            **({"note": "Safety statuses are provisional until screening is complete."} if missing else {})}


def update_user_profile(session: Session, **fields) -> dict:
    session.profile = session.profile.merge(fields)
    return {"profile": session.profile.describe(), **_screening(session)}


def search_products(session: Session, query: str, category: str | None = None, diet: list[str] | None = None,
                    max_price_sgd: float | None = None, form: str | None = None,
                    for_children: bool | None = None, limit: int = 8) -> dict:
    diet = diet or session.profile.diet or None
    if for_children is None and session.profile.age is not None:
        for_children = session.profile.age < 18 or None
    hits = session.catalog.search(query, category=category, diet=diet, max_price=max_price_sgd,
                                  form=form, for_children=for_children, limit=limit)
    out = {"suitable": [], "needs_doctor_approval": [], "not_suitable": []}
    for p, score in hits:
        v = assess(p, session.profile)
        if not v.recommendable:
            session.not_recommendable_seen[p.title] = v.level.name
        reasons = sorted({f.reason for f in v.findings if f.level >= Level.CAUTION})
        if v.level == Level.EXCLUDED:
            out["not_suitable"].append({"id": p.id, "title": p.title, "why": reasons})
        elif v.level == Level.DOCTOR_FIRST:
            out["needs_doctor_approval"].append({"id": p.id, "title": p.title, "why": reasons})
        else:
            out["suitable"].append({**p.summary(), "relevance": round(score, 2), "safety": v.level.name,
                                    "warnings": reasons})
    return {"query": query, "filters": {"category": category, "diet": diet, "max_price_sgd": max_price_sgd,
                                        "form": form, "for_children": for_children},
            **_screening(session), **out}


def get_product_details(session: Session, product_id: str) -> dict:
    p = session.catalog.get(product_id)
    if not p:
        return {"error": f"no product with id {product_id}"}
    return {**p.detail(), "safety_for_this_user": _verdict_brief(session, p), **_screening(session)}


def _norm_active(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b(extract|powder|standardi[sz]ed|complex|blend|fresh|organic|natural)\b", "", n)
    n = re.sub(r"vitamin\s+([a-z])\s*(\d*)", r"vitamin \1\2", n)
    return re.sub(r"\s+", " ", n).strip(" ,-")


def compare_products(session: Session, product_ids: list[str]) -> dict:
    products, missing = [], []
    for pid in product_ids:
        p = session.catalog.get(pid)
        (products.append(p) if p else missing.append(pid))
    if missing:
        return {"error": f"unknown product ids: {missing}"}
    if len(products) < 2:
        return {"error": "need at least two products"}

    actives = {p.id: {_norm_active(a.name) for a in p.actives} for p in products}
    shared = set.intersection(*actives.values())
    rows = []
    for p in products:
        rows.append({
            "id": p.id,
            "title": p.title,
            "price_sgd": p.price,
            "form": p.serving_unit,
            "max_servings_per_day": p.max_servings_per_day,
            "cost_per_day_sgd": p.cost_per_day,
            "diet": p.diet,
            "free_from": p.raw["free_from"],
            "daily_actives": p.daily_amounts(),
            "unique_actives": sorted(actives[p.id] - shared),
            "label_cautions": p.raw["caution"],
            "safety_for_this_user": _verdict_brief(session, p),
        })
    return {
        "products": rows,
        "shared_actives": sorted(shared),
        "if_taken_together": {
            "daily_totals_vs_upper_limits": stack_totals(products),
            "note": "Upper limits are adult limits for total intake from supplements at the maximum "
                    "label dose. Food intake is not included.",
        },
        "data_gaps": "Pack counts are not published by the store, so cost per day is usually unknown; "
                     "compare on pack price and dose instead.",
        **_screening(session),
    }


def recommend_products(session: Session, items: list[dict]) -> dict:
    missing = session.profile.missing_screening()
    if missing:
        return {"error": "Screening incomplete - ask the user these before recommending: " + "; ".join(missing)}
    accepted, rejected = [], []
    for item in items:
        p = session.catalog.get(item["product_id"])
        if not p:
            rejected.append({"product_id": item["product_id"], "reason": "unknown product id"})
            continue
        v = assess(p, session.profile)
        if not v.recommendable:
            session.not_recommendable_seen[p.title] = v.level.name
            rejected.append({"product_id": p.id, "title": p.title, "status": v.level.name,
                             "reason": [f.reason for f in v.findings if f.level >= Level.DOCTOR_FIRST]})
            continue
        card = {"id": p.id, "title": p.title, "price_sgd": p.price, "url": p.raw["url"], "image": p.raw["image"],
                "why": item["why_for_this_user"],
                "warnings": sorted({f.reason for f in v.findings if f.level == Level.CAUTION}),
                "pack_options": p.summary()["pack_options"]}
        accepted.append(card)
    session.recommendations = accepted
    result = {"accepted": [{"id": c["id"], "title": c["title"], "warnings_shown_to_user": c["warnings"]}
                           for c in accepted]}
    if rejected:
        result["rejected"] = rejected
        result["instruction"] = ("Rejected products must not be recommended. You may tell the user they "
                                 "need a doctor's approval or are unsuitable, and why.")
        if not accepted:
            result["error"] = "no products were accepted"
    return result


HANDLERS = {
    "update_user_profile": update_user_profile,
    "search_products": search_products,
    "get_product_details": get_product_details,
    "compare_products": compare_products,
    "recommend_products": recommend_products,
}


def _summarise(name: str, result: dict) -> str:
    if "error" in result:
        return result["error"]
    if name == "search_products":
        return (f"{len(result['suitable'])} suitable, {len(result['needs_doctor_approval'])} need doctor, "
                f"{len(result['not_suitable'])} excluded")
    if name == "update_user_profile":
        p = result["profile"]
        return f"risk factors: {', '.join(p['risk_factors']) or 'none'}; screening complete: {p['screening_complete']}"
    if name == "get_product_details":
        return f"{result['title']}: {result['safety_for_this_user']['status']}"
    if name == "compare_products":
        return " vs ".join(f"{r['title']} ({r['safety_for_this_user']['status']})" for r in result["products"])
    if name == "recommend_products":
        return f"accepted {len(result['accepted'])}, rejected {len(result.get('rejected', []))}"
    return ""
