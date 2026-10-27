"""The user's health profile and its reduction to risk factors.

`None` always means "not asked yet", `[]` / "none" means "asked, and there is nothing".
That distinction is what lets the system fail closed: an unknown is never treated as a no.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .catalog import DATA_DIR

PregnancyStatus = Literal["pregnant", "breastfeeding", "trying_to_conceive", "none", "not_applicable"]


@lru_cache
def _synonyms() -> dict:
    rules = yaml.safe_load((DATA_DIR / "safety_rules.yaml").read_text())
    return {k: rules[k] for k in ("condition_synonyms", "medication_synonyms", "allergy_synonyms")}


def _classify(items: list[str], table: dict[str, str], prefix: str = "") -> tuple[set[str], list[str]]:
    known, unknown = set(), []
    for item in items:
        text = item.lower().strip()
        if text in ("", "none", "no", "nil", "n/a"):
            continue
        hits = {prefix + key for key, pattern in table.items() if re.search(pattern, text)}
        if text in table:  # already a canonical key
            hits.add(prefix + text)
        if hits:
            known |= hits
        else:
            unknown.append(item)
    return known, unknown


class UserProfile(BaseModel):
    age: int | None = None
    sex: Literal["female", "male", "other"] | None = None
    pregnancy_status: PregnancyStatus | None = None
    conditions: list[str] | None = None
    medications: list[str] | None = None
    allergies: list[str] | None = None
    diet: list[str] = Field(default_factory=list)  # vegan / vegetarian / halal
    goals: list[str] = Field(default_factory=list)

    # ---- derived ---------------------------------------------------------------------------
    def risk_factors(self) -> set[str]:
        syn = _synonyms()
        factors: set[str] = {"any"}
        if self.pregnancy_status in ("pregnant", "breastfeeding", "trying_to_conceive"):
            factors.add("pregnancy" if self.pregnancy_status == "pregnant" else self.pregnancy_status)
        if self.age is not None and self.age < 18:
            factors.add("under_18")
        cond, _ = _classify(self.conditions or [], syn["condition_synonyms"])
        meds, _ = _classify(self.medications or [], syn["medication_synonyms"])
        allergies, _ = _classify(self.allergies or [], syn["allergy_synonyms"], prefix="allergy:")
        factors |= cond | meds | allergies
        if any(m.strip().lower() not in ("none", "no", "") for m in self.medications or []):
            factors.add("any_medication")
        if any(c.strip().lower() not in ("none", "no", "") for c in self.conditions or []):
            factors.add("any_condition")
        return factors

    def unrecognised(self) -> dict[str, list[str]]:
        """Free-text entries we could not map to a risk factor. Handled fail-closed."""
        syn = _synonyms()
        return {
            "conditions": _classify(self.conditions or [], syn["condition_synonyms"])[1],
            "medications": _classify(self.medications or [], syn["medication_synonyms"])[1],
            "allergies": _classify(self.allergies or [], syn["allergy_synonyms"])[1],
        }

    def missing_screening(self) -> list[str]:
        """Questions that must be answered before any product can be recommended."""
        missing = []
        if self.pregnancy_status is None and self.sex != "male":
            missing.append("pregnancy_status (pregnant, breastfeeding, trying to conceive, or none)")
        if self.conditions is None:
            missing.append("conditions (any medical conditions, or none)")
        if self.medications is None:
            missing.append("medications (any prescription or regular medicines, or none)")
        if self.allergies is None:
            missing.append("allergies (any allergies, or none)")
        return missing

    def merge(self, update: dict) -> "UserProfile":
        data = self.model_dump()
        for key, value in update.items():
            if value is None or key not in data:
                continue
            if isinstance(data[key], list) and isinstance(value, list) and data[key]:
                # Lists accumulate (a newly mentioned medication doesn't erase earlier ones),
                # except an explicit ["none"] which only fills an unanswered field.
                if value and all(v.lower() in ("none", "no") for v in value):
                    continue
                value = list(dict.fromkeys([v for v in data[key] if v.lower() not in ("none", "no")] + value))
            data[key] = value
        return UserProfile(**data)

    def describe(self) -> dict:
        return {
            **self.model_dump(),
            "risk_factors": sorted(self.risk_factors() - {"any"}),
            "unrecognised": {k: v for k, v in self.unrecognised().items() if v},
            "screening_complete": not self.missing_screening(),
        }
