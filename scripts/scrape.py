"""Scrape the Holistic Way Shopify storefront into data/products.json.

Two sources per product:
  1. /products.json      - title, price, tags, type, variants, description
  2. /products/<handle>  - the accordion tabs (INGREDIENTS, DOSAGE, CAUTION, ...)
     which carry the label information the safety engine needs.

Plus collection membership (halal, vegan, children, pregnant-support, ...) from
/collections/<handle>/products.json.

Usage:  python scripts/scrape.py [--delay 1.0]
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://holisticway.com.sg"
OUT = Path(__file__).resolve().parent.parent / "data" / "products.json"
UA = "nutribot-takehome-scraper/0.1 (research prototype)"

TAB_RE = re.compile(
    r"<summary>\s*(?P<title>[^<]+?)\s*<span></span>\s*</summary>.*?"
    r'<div class="collapsible-tab__text">(?P<body>.*?)</div>\s*</div>\s*</details>',
    re.S,
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def html_to_lines(fragment: str) -> list[str]:
    """Turn an HTML fragment into clean, non-empty text lines (one per <li>/<p>/<br>)."""
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip(" •\t") for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def parse_tabs(page_html: str) -> dict[str, list[str]]:
    tabs = {}
    for m in TAB_RE.finditer(page_html):
        title = re.sub(r"\s+", " ", html.unescape(m["title"])).strip().upper()
        tabs[title] = html_to_lines(m["body"])
    return tabs


def list_products() -> list[dict]:
    products, page = [], 1
    while True:
        batch = json.loads(fetch(f"{BASE}/products.json?limit=250&page={page}"))["products"]
        if not batch:
            return products
        products.extend(batch)
        page += 1


# Collections that carry facts we filter or reason on (diet, audience, pregnancy marketing).
COLLECTIONS = ["halal", "vegan", "vegetarian", "pregnant-support", "children", "mens-health",
               "womens-health", "sleep", "liver-health", "joints", "eye-health", "immune-support",
               "gut-digestion", "brain-heart-health", "energy", "weight-management", "healthy-ageing",
               "beauty-skin", "hair", "placenta-supplements", "fish-oil"]


def collection_membership(delay: float) -> dict[str, list[str]]:
    membership: dict[str, list[str]] = {}
    for handle in COLLECTIONS:
        try:
            batch = json.loads(fetch(f"{BASE}/collections/{handle}/products.json?limit=250"))["products"]
        except Exception as e:
            print(f"  ! collection {handle}: {e}", file=sys.stderr)
            continue
        for p in batch:
            membership.setdefault(str(p["id"]), []).append(handle)
        time.sleep(delay)
    return membership


def normalise(p: dict, tabs: dict[str, list[str]], collections: list[str]) -> dict:
    variant = p["variants"][0]
    return {
        "id": str(p["id"]),
        "handle": p["handle"],
        "title": p["title"].strip(),
        "url": f"{BASE}/products/{p['handle']}",
        "vendor": p["vendor"],
        "category": p["product_type"] or None,
        "tags": p["tags"],
        "collections": collections,
        "price_sgd": float(variant["price"]),
        "compare_at_price_sgd": float(variant["compare_at_price"]) if variant.get("compare_at_price") else None,
        "available": any(v["available"] for v in p["variants"]),
        "description": " ".join(html_to_lines(p["body_html"] or "")),
        "image": p["images"][0]["src"] if p["images"] else None,
        "ingredients": tabs.get("INGREDIENTS", []),
        "dosage": tabs.get("DOSAGE") or tabs.get("RECOMMENDED DOSAGE", []),
        "free_from": tabs.get("NO ADDED", []),
        "caution": tabs.get("CAUTION", []),
        "country_of_manufacture": tabs.get("COUNTRY OF MANUFACTURE", []),
        "other_label_sections": {
            k: v for k, v in tabs.items()
            if k not in {"INGREDIENTS", "DOSAGE", "RECOMMENDED DOSAGE", "NO ADDED", "CAUTION",
                         "COUNTRY OF MANUFACTURE"}
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between page requests")
    args = ap.parse_args()

    raw = list_products()
    print(f"{len(raw)} products listed", file=sys.stderr)
    membership = collection_membership(args.delay)
    out = []
    for i, p in enumerate(raw, 1):
        try:
            tabs = parse_tabs(fetch(f"{BASE}/products/{p['handle']}"))
        except Exception as e:  # keep going; a product with no label data is flagged downstream
            print(f"  ! {p['handle']}: {e}", file=sys.stderr)
            tabs = {}
        out.append(normalise(p, tabs, membership.get(str(p["id"]), [])))
        print(f"  [{i}/{len(raw)}] {p['handle']} tabs={sorted(tabs)}", file=sys.stderr)
        time.sleep(args.delay)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {OUT} ({len(out)} products)", file=sys.stderr)


if __name__ == "__main__":
    main()
