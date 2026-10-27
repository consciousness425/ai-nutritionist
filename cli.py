"""Terminal chat with the assistant.   python cli.py [--trace]"""

import argparse
import json

from nutribot.agent import NutriBot


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true", help="print tool calls and guard decisions")
    args = ap.parse_args()

    bot = NutriBot()
    session = bot.new_session()
    print("Nutri (prototype). Ctrl-D to quit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except EOFError:
            break
        if not text:
            continue
        try:
            result = bot.chat(session, text)
        except Exception as e:  # credentials / network: report and keep the session going
            print(f"\n[error] {type(e).__name__}: {e}\n")
            continue
        if args.trace:
            for e in result.trace:
                print("  ·", json.dumps(e, ensure_ascii=False)[:200])
        print(f"\nnutri> {result.reply}\n")
        for r in result.recommendations:
            print(f"  [card] {r['title']}  S${r['price_sgd']:.2f}  {r['url']}")
            for w in r["warnings"]:
                print(f"         ⚠ {w}")
        if result.recommendations:
            print()


if __name__ == "__main__":
    main()
