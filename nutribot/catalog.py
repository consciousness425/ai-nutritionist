"""Product catalogue: loading, label parsing, bundle grouping and search.

Everything here is deterministic. The LLM never sees raw HTML, only the
structured views produced by `Product.summary()` / `Product.detail()`.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# "Vitamin B6 (Pyridoxine HCl) 5 mg", "Vitamin D3 25 mcg (1000 IU)", "Lactobacillus 10 Billion CFU"
AMOUNT_RE = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>mg|mcg|µg|μg|ug|g|iu|billion cfu|billion|cfu)\b", re.I
)
SERVING_RE = re.compile(r"each\s+(?:\d+\s*)?(?:vegetarian |veg(?:an)? |soft |chewable |effervescent )?"
                        r"(?P<unit>[a-z]+?)s?\s+(?:\(.*?\)\s*)?contains", re.I)
NON_ACTIVE_RE = re.compile(r"aids?:?$|^(excipients|other ingredients|capsule shell|contains:)|"
                           r"tabletting|encapsulation|^vegetarian capsule|^gelatin|^glycerin|^water$|"
                           r"^purified water|^magnesium stearate|^microcrystalline|^silicon dioxide|"
                           r"^lactose monohydrate|^croscarmellose|flavou?r|colou?r", re.I)
PACK_RE = re.compile(r"(?P<n>\d{2,3})\s*(?:x\s*)?(?:veg(?:etarian|an)?\s*|soft\s*|chewable\s*|effervescent\s*)?"
                     r"(?P<unit>capsules?|caps|tablets?|tabs|softgels?|sachets?|gummies|gummy|bottles?|shots?|vegicaps?)\b",
                     re.I)
BUNDLE_RE = re.compile(r"bundle of (\d)", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the this to with
your you our may help helps support supports each daily take contains product""".split())


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def to_unit(amount: float, unit: str, target: str) -> float | None:
    """Convert between mass units (and IU for vitamin D only, handled by caller)."""
    unit = unit.lower().replace("µg", "mcg").replace("μg", "mcg").replace("ug", "mcg")
    scale = {"g": 1e6, "mg": 1e3, "mcg": 1.0}
    if unit in scale and target in scale:
        return amount * scale[unit] / scale[target]
    return None


@dataclass
class Active:
    name: str
    amount: float | None
    unit: str | None
    raw: str

    def as_dict(self) -> dict:
        return {"name": self.name, "amount": self.amount, "unit": self.unit}


@dataclass
class Product:
    raw: dict
    group_key: str = ""
    bundle_size: int = 1
    variants: list["Product"] = field(default_factory=list)  # other pack sizes of the same product

    # ---- basic fields -------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def title(self) -> str:
        return self.raw["title"]

    @property
    def price(self) -> float:
        return self.raw["price_sgd"]

    @property
    def caution_text(self) -> str:
        return " ".join(self.raw["caution"])

    @property
    def collections(self) -> list[str]:
        return self.raw.get("collections", [])

    @cached_property
    def ingredient_text(self) -> str:
        return " ".join(self.raw["ingredients"])

    @cached_property
    def is_for_children(self) -> bool:
        blob = f"{self.title} {' '.join(self.raw['tags'])}".lower()
        return "children" in self.collections or bool(re.search(r"\bkids?\b|children|child", blob))

    @cached_property
    def diet(self) -> list[str]:
        diets = {c for c in self.collections if c in ("vegan", "vegetarian", "halal")}
        free_from = " ".join(self.raw["free_from"]).lower()
        if "suitable for vegans" in free_from or "vegan" in free_from:
            diets.add("vegan")
        if "vegetarian" in free_from:
            diets.add("vegetarian")
        if "vegan" in diets:
            diets.add("vegetarian")
        return sorted(diets)

    # ---- label parsing ------------------------------------------------------------------
    @cached_property
    def serving_unit(self) -> str | None:
        for line in self.raw["ingredients"][:3]:
            m = SERVING_RE.search(line)
            if m:
                return m["unit"].lower()
        return None

    @cached_property
    def actives(self) -> list[Active]:
        out = []
        for line in self.raw["ingredients"]:
            if SERVING_RE.search(line) or NON_ACTIVE_RE.search(line.strip()):
                continue
            m = AMOUNT_RE.search(line)
            # Name = text before the first amount or bracket, e.g. "Vitamin B6 (Pyridoxine HCl) 5 mg"
            name = re.split(r"\s*[\(\d]", line, maxsplit=1)[0].strip(" :-,") or line[:60]
            if m:
                unit = m["unit"].lower().replace("µg", "mcg").replace("μg", "mcg").replace("ug", "mcg")
                out.append(Active(name, float(m["num"].replace(",", "")), unit, line))
            else:
                out.append(Active(name, None, None, line))
        return out

    @cached_property
    def max_servings_per_day(self) -> float | None:
        """Upper end of the label dose, e.g. '1-2 capsules, twice daily' -> 4."""
        text = " ".join(self.raw["dosage"]).lower()
        if not text:
            return None
        text = re.sub(r"\b(one|two|three)\b", lambda m: {"one": "1", "two": "2", "three": "3"}[m[1]], text)
        # Ignore age qualifiers like "13 years & above" and durations like "first 7 days"
        text = re.sub(r"\d+\s*(years?|yrs?|days?|weeks?|months?|minutes?|hours?)", " ", text)
        nums = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|to)?\s*", text) if float(n) <= 10]
        if not nums:
            return None
        per_dose = max(nums)
        times = 3 if re.search(r"(three times|thrice)", text) else 2 if "twice" in text else 1
        return per_dose * times

    @cached_property
    def units_per_pack(self) -> int | None:
        for src in (self.title, self.raw["description"], " ".join(self.raw["dosage"])):
            m = PACK_RE.search(src)
            if m:
                return int(m["n"]) * self.bundle_size
        return None

    @cached_property
    def cost_per_day(self) -> float | None:
        if self.units_per_pack and self.max_servings_per_day:
            days = self.units_per_pack / self.max_servings_per_day
            return round(self.price / days, 2) if days else None
        return None

    def daily_amounts(self) -> list[dict]:
        """Actives scaled to the maximum labelled daily dose."""
        k = self.max_servings_per_day or 1
        return [
            {"name": a.name, "per_serving": a.amount, "max_per_day": round(a.amount * k, 3) if a.amount else None,
             "unit": a.unit}
            for a in self.actives
        ]

    # ---- views for the LLM ---------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.raw["category"],
            "price_sgd": self.price,
            "pack_options": [{"id": v.id, "bundle_of": v.bundle_size, "price_sgd": v.price} for v in self.variants]
            or None,
            "form": self.serving_unit,
            "diet": self.diet,
            "key_actives": [a.name for a in self.actives][:8],
            "blurb": self.raw["description"][:240],
            "url": self.raw["url"],
        }

    def detail(self) -> dict:
        return {
            **self.summary(),
            "description": self.raw["description"],
            "ingredients_label": self.raw["ingredients"],
            "actives_per_serving": [a.as_dict() for a in self.actives],
            "dosage_label": self.raw["dosage"],
            "max_servings_per_day": self.max_servings_per_day,
            "cost_per_day_sgd": self.cost_per_day,
            "free_from": self.raw["free_from"],
            "caution_label": self.raw["caution"],
            "made_in": self.raw["country_of_manufacture"],
            "available": self.raw["available"],
        }

    def search_text(self) -> str:
        r = self.raw
        return " ".join([
            self.title, self.title,  # title weighted x2
            r["category"] or "", " ".join(r["tags"]), " ".join(self.collections).replace("-", " "),
            r["description"], " ".join(a.name for a in self.actives),
        ])


def _group_key(p: dict) -> str:
    t = p["title"].lower()
    t = re.sub(r"\(exp [^)]*\)|\(\s*\d+[- ]month supply\s*\)|-\s*\d+[- ]month supply|\bbundle of \d\b", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class Catalog:
    """In-memory catalogue + BM25 index. At 1,000 SKUs this is a few ms per query;
    see docs/DESIGN.md for the production (Postgres + pgvector hybrid) version."""

    def __init__(self, products: list[dict]):
        all_products = [Product(p) for p in products]
        for p in all_products:
            p.group_key = _group_key(p.raw)
            m = BUNDLE_RE.search(" ".join(p.raw["tags"]) + " " + p.raw["handle"].replace("-", " "))
            p.bundle_size = int(m[1]) if m else 1
            if re.search(r"2[- ]month", p.title, re.I):
                p.bundle_size = max(p.bundle_size, 2)

        self.by_id: dict[str, Product] = {p.id: p for p in all_products}
        # One canonical (smallest pack, cheapest) product per group; the rest become pack options.
        groups: dict[str, list[Product]] = {}
        for p in all_products:
            groups.setdefault(p.group_key, []).append(p)
        self.canonical: list[Product] = []
        for members in groups.values():
            # Collections (halal, vegan, pregnant-support...) are often only set on one pack size.
            shared = sorted({c for m in members for c in m.raw.get("collections", [])})
            for m in members:
                m.raw["collections"] = shared
            members.sort(key=lambda x: (x.bundle_size, x.price))
            head = members[0]
            head.variants = members[1:]
            self.canonical.append(head)
            for other in members[1:]:
                other.variants = []
        self._canonical_of = {p.id: g[0] for g in groups.values() for p in g}
        self._build_index()

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalog":
        return cls(json.loads((path or DATA_DIR / "products.json").read_text()))

    def get(self, product_id: str) -> Product | None:
        return self.by_id.get(str(product_id))

    def canonical_of(self, product_id: str) -> Product | None:
        return self._canonical_of.get(str(product_id))

    def find_by_title(self, text: str) -> Product | None:
        text = text.lower().strip()
        for p in self.canonical:
            if p.title.lower() == text:
                return p
        return None

    # ---- BM25 ------------------------------------------------------------------------------
    def _build_index(self, k1: float = 1.4, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._docs = [Counter(tokens(p.search_text())) for p in self.canonical]
        self._len = [sum(d.values()) for d in self._docs]
        self._avg = sum(self._len) / max(len(self._len), 1)
        df = Counter(t for d in self._docs for t in d)
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        diet: list[str] | None = None,
        max_price: float | None = None,
        form: str | None = None,
        for_children: bool | None = None,
        limit: int = 10,
    ) -> list[tuple[Product, float]]:
        q = tokens(query)
        scored = []
        for p, doc, length in zip(self.canonical, self._docs, self._len):
            if category and (p.raw["category"] or "").lower() != category.lower() \
                    and category.lower().replace(" ", "-") not in p.collections:
                continue
            if diet and not set(d.lower() for d in diet) <= set(p.diet):
                continue
            if max_price is not None and p.price > max_price:
                continue
            if form and (p.serving_unit or "") not in form.lower() and form.lower() not in (p.serving_unit or ""):
                continue
            if for_children is not None and p.is_for_children != for_children:
                continue
            score = 0.0
            for t in q:
                f = doc.get(t, 0)
                if f:
                    score += self._idf[t] * f * (self._k1 + 1) / (
                        f + self._k1 * (1 - self._b + self._b * length / self._avg))
            if score > 0 or not q:
                scored.append((p, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]
