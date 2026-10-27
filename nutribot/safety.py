"""Deterministic product eligibility engine.

This is the component that enforces "don't recommend X to pregnant women / people with
condition Y". It is plain code over reviewed data (data/safety_rules.yaml) so that:
  * it cannot be talked out of a decision by a prompt,
  * every decision carries the rule and evidence that produced it (auditable),
  * clinicians can change the rules without touching prompts or code.

The LLM only ever sees products together with the verdict computed here, and the
`recommend_products` tool refuses anything that is not recommendable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from functools import lru_cache

import yaml

from .catalog import DATA_DIR, Product
from .profile import UserProfile

LIFE_STAGE = {"pregnancy", "breastfeeding", "trying_to_conceive"}
# Stand-in for a pharmacist-reviewed allowlist: the retailer's own "Pregnancy Support"
# collection. Pregnancy is default-deny: anything not on this list needs a doctor's OK.
PREGNANCY_ALLOWLIST_COLLECTION = "pregnant-support"


class Level(IntEnum):
    OK = 0
    CAUTION = 1
    DOCTOR_FIRST = 2
    EXCLUDED = 3


@dataclass
class Finding:
    level: Level
    factor: str
    reason: str
    source: str  # "label" | "ingredient_kb" | "policy"

    def as_dict(self) -> dict:
        return {"level": self.level.name, "applies_because": self.factor, "reason": self.reason,
                "source": self.source}


@dataclass
class Verdict:
    product_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def level(self) -> Level:
        return max((f.level for f in self.findings), default=Level.OK)

    @property
    def recommendable(self) -> bool:
        return self.level <= Level.CAUTION

    def as_dict(self) -> dict:
        # Keep only the most severe finding per (factor, reason) for readability.
        return {"status": self.level.name, "recommendable": self.recommendable,
                "findings": [f.as_dict() for f in sorted(self.findings, key=lambda f: -f.level)]}


@lru_cache
def _rules() -> dict:
    rules = yaml.safe_load((DATA_DIR / "safety_rules.yaml").read_text())
    for r in rules["ingredient_rules"] + rules["label_rules"]:
        r["_re"] = re.compile(r["match"], re.I)
    return rules


def assess(product: Product, profile: UserProfile) -> Verdict:
    rules = _rules()
    factors = profile.risk_factors()
    verdict = Verdict(product.id)
    add = verdict.findings.append

    # 1. Ingredient knowledge base (title + ingredient panel).
    ingredient_blob = f"{product.title} {product.ingredient_text}"
    for rule in rules["ingredient_rules"]:
        if not rule["_re"].search(ingredient_blob):
            continue
        for factor, level in rule["risks"].items():
            if factor in factors:
                add(Finding(Level[level], factor, rule["why"], "ingredient_kb"))

    # 2. The product's own label warnings.
    caution = product.caution_text.lower()
    for rule in rules["label_rules"]:
        m = rule["_re"].search(caution)
        if not m:
            continue
        for factor, level in rule["risks"].items():
            if factor in factors:
                reason = rule.get("note") or f'Label: "{_sentence_around(product.caution_text, m.start())}"'
                add(Finding(Level[level], factor, reason, "label"))

    # 3. Policies.
    life_stage = factors & LIFE_STAGE
    if life_stage:
        if PREGNANCY_ALLOWLIST_COLLECTION in product.collections:
            # Reviewed as appropriate for this life stage: a generic "consult your doctor if
            # pregnant" label line becomes a caution shown to the user, not a block.
            for f in verdict.findings:
                if f.factor in LIFE_STAGE and f.source == "label" and f.level == Level.DOCTOR_FIRST:
                    f.level = Level.CAUTION
                    f.reason += " (confirm with your doctor or obstetrician)"
        else:
            for factor in sorted(life_stage):
                add(Finding(Level.DOCTOR_FIRST, factor,
                            "Not on our reviewed list of products for pregnancy, breastfeeding or "
                            "trying to conceive", "policy"))

    if "under_18" in factors and not product.is_for_children:
        add(Finding(Level.EXCLUDED, "under_18", "Adult product; not recommended for under-18s", "policy"))

    high_risk = factors - {"any"}
    if high_risk and not (product.raw["ingredients"] and product.raw["caution"]):
        add(Finding(Level.DOCTOR_FIRST, sorted(high_risk)[0],
                    "Full ingredient/warning label not available, so it can't be safety-checked", "policy"))

    unknown = profile.unrecognised()
    for kind in ("medications", "conditions"):
        if unknown[kind]:
            add(Finding(Level.CAUTION, f"unrecognised_{kind}",
                        f"Couldn't check against {', '.join(unknown[kind])} - ask a pharmacist "
                        "before combining", "policy"))
    if unknown["allergies"]:
        add(Finding(Level.DOCTOR_FIRST, "unrecognised_allergies",
                    f"Couldn't verify the label against your allergy to {', '.join(unknown['allergies'])}",
                    "policy"))

    verdict.findings = _dedupe(verdict.findings)
    return verdict


def _sentence_around(text: str, pos: int) -> str:
    start = max(text.rfind(".", 0, pos) + 1, 0)
    end = text.find(".", pos)
    return text[start: end + 1 if end != -1 else len(text)].strip()


def _dedupe(findings: list[Finding]) -> list[Finding]:
    best: dict[tuple[str, str], Finding] = {}
    for f in findings:
        key = (f.factor, f.reason)
        if key not in best or f.level > best[key].level:
            best[key] = f
    return list(best.values())


# ------------------------------------------------------------------------------------------
# Stack check: total daily intake across several products vs. tolerable upper limits.
# ------------------------------------------------------------------------------------------
@lru_cache
def _limits() -> dict:
    data = yaml.safe_load((DATA_DIR / "upper_limits.yaml").read_text())["nutrients"]
    for v in data.values():
        v["_re"] = re.compile(v["match"], re.I)
    return data


def stack_totals(products: list[Product]) -> list[dict]:
    from .catalog import to_unit

    totals: dict[str, dict] = {}
    for p in products:
        k = p.max_servings_per_day or 1
        for a in p.actives:
            if a.amount is None:
                continue
            for key, lim in _limits().items():
                if not lim["_re"].search(a.name):
                    continue
                amount = a.amount
                unit = a.unit
                if key == "vitamin_d" and unit == "iu":
                    amount, unit = amount * 0.025, "mcg"
                value = to_unit(amount, unit, lim["unit"])
                if value is None:
                    continue
                t = totals.setdefault(key, {"nutrient": key, "total_per_day": 0.0, "unit": lim["unit"],
                                            "upper_limit": lim["limit"], "sources": []})
                t["total_per_day"] += value * k
                t["sources"].append(f"{p.title}: {round(value * k, 2)} {lim['unit']}")
                break
    out = []
    for t in totals.values():
        t["total_per_day"] = round(t["total_per_day"], 2)
        t["pct_of_upper_limit"] = round(100 * t["total_per_day"] / t["upper_limit"])
        t["over_limit"] = t["total_per_day"] > t["upper_limit"]
        out.append(t)
    return sorted(out, key=lambda t: -t["pct_of_upper_limit"])
