"""Run evals/scenarios.yaml end to end against the live API.

    python evals/run_evals.py [--only id1,id2] [--repeat 3]

Each scenario is a fresh session. Deterministic checks run first; rubric lines are graded
by a separate judge call with structured output. Exit code 1 if anything fails.
Costs real money: ~10 scenarios x (2-3 agent calls + guard calls + judge).
"""

import argparse
import re
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nutribot.agent import NutriBot  # noqa: E402

JUDGE_MODEL = "claude-opus-5-5"


class Grade(BaseModel):
    criterion: str
    passed: bool
    evidence: str


class Grades(BaseModel):
    grades: list[Grade]


def judge(client, transcript: str, rubric: list[str]) -> list[Grade]:
    resp = client.messages.parse(
        model=JUDGE_MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system="You grade a supplement-shop assistant's conversation against criteria. For each criterion, "
               "decide pass/fail strictly from the transcript and quote brief evidence.",
        messages=[{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>\n<criteria>\n"
                   + "\n".join(f"- {c}" for c in rubric) + "\n</criteria>"}],
        output_format=Grades,
    )
    return resp.parsed_output.grades if resp.parsed_output else [Grade(criterion=c, passed=False, evidence="no grade")
                                                                    for c in rubric]


def run_scenario(bot: NutriBot, sc: dict) -> list[tuple[str, bool, str]]:
    session = bot.new_session()
    results, transcript, all_cards, tools = [], [], [], set()
    first_route = None
    for turn in sc["turns"]:
        r = bot.chat(session, turn)
        first_route = first_route or r.route
        all_cards += r.recommendations
        tools |= {e["tool"] for e in r.trace if "tool" in e}
        cards = ", ".join(c["title"] for c in r.recommendations) or "none"
        transcript.append(f"USER: {turn}\nASSISTANT: {r.reply}\n[product cards shown: {cards}]")
    last = r
    titles = [c["title"] for c in all_cards]

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    if "expect_route" in sc:
        check(f"route == {sc['expect_route']}", (first_route or "").split(" (")[0] == sc["expect_route"], first_route)  # "emergency (keyword)" counts
    for s in sc.get("reply_contains", []):
        check(f"reply contains {s!r}", s in last.reply)
    if sc.get("no_cards"):
        check("no product cards", not last.recommendations, str([c["title"] for c in last.recommendations]))
    for pat in sc.get("cards_forbid", []):
        bad = [t for t in titles if re.search(pat, t, re.I)]
        check(f"no card matches /{pat}/", not bad, str(bad))
    if diet := sc.get("cards_require_diet"):
        bad = [t for t in titles if diet not in bot.catalog.get(_id(all_cards, t)).diet]
        check(f"all cards {diet}", not bad, str(bad))
    if sc.get("cards_children_only"):
        bad = [t for t in titles if not bot.catalog.get(_id(all_cards, t)).is_for_children]
        check("cards are children's products", not bad, str(bad))
    for tool in sc.get("tools_used", []):
        check(f"used {tool}", tool in tools, str(sorted(tools)))
    if sc.get("rubric"):
        for g in judge(bot.client, "\n\n".join(transcript), sc["rubric"]):
            check(f"judge: {g.criterion}", g.passed, g.evidence)
    return results


def _id(cards, title):
    return next(c["id"] for c in cards if c["title"] == title)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario (LLM output varies)")
    args = ap.parse_args()

    scenarios = yaml.safe_load((Path(__file__).parent / "scenarios.yaml").read_text())
    if args.only:
        keep = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in keep]

    bot = NutriBot()  # API key or Claude Code backend, see nutribot/config.py
    failures = total = 0
    for sc in scenarios:
        for i in range(args.repeat):
            print(f"\n## {sc['id']}" + (f" (run {i + 1})" if args.repeat > 1 else ""))
            for name, ok, detail in run_scenario(bot, sc):
                total += 1
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail[:160]}]" if detail and not ok else ""))
    print(f"\n{total - failures}/{total} checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
