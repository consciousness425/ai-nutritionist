"""Input triage and output review around the main agent.

Layers, cheapest first:
  1. `emergency_keywords`  - regex, no model call, can't fail open.
  2. `triage`              - Haiku classifies the message: emergency / medical advice / in scope / off topic.
  3. (main agent runs, with the deterministic safety engine inside its tools)
  4. `review`              - Haiku checks the draft reply against the policy before the user sees it.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

import anthropic
from pydantic import BaseModel

from .config import GUARD_MODEL

log = logging.getLogger(__name__)

EMERGENCY_RE = re.compile(
    r"chest pain|can'?t breathe|cannot breathe|difficulty breathing|short(ness)? of breath|"
    r"overdos|took too many|swallowed (a|the|all|whole)|poison|suicid|kill myself|end my life|self[- ]harm|"
    r"anaphyla|throat (is )?(closing|swelling)|face (is )?swelling|unconscious|passed out|"
    r"seizure (now|right now)|stroke|slurred speech|vomiting blood|severe bleeding|coughing (up )?blood",
    re.I,
)

EMERGENCY_REPLY = (
    "This sounds like it could be urgent, and it's not something I can help with safely. "
    "**Please call 995 (Singapore emergency ambulance) now, or go to the nearest A&E.**\n\n"
    "If you're having thoughts of harming yourself, you can call the Samaritans of Singapore 24-hour "
    "hotline on **1767** or the IMH Mental Health Helpline on **6389 2222**.\n\n"
    "If a supplement might be involved, bring the bottle with you so the medical team can see the label."
)

SAFE_FALLBACK_REPLY = (
    "I'm sorry, I couldn't put together an answer I'm confident is safe for your situation. "
    "Because of what you've shared, please check with your doctor or pharmacist before starting any "
    "supplement. You're welcome to ask me about specific products' ingredients or labels in the meantime."
)


def emergency_keywords(text: str) -> bool:
    return bool(EMERGENCY_RE.search(text))


class Triage(BaseModel):
    route: Literal["emergency", "medical_advice", "in_scope", "off_topic"]
    reason: str


TRIAGE_PROMPT = """You triage messages sent to a supplement shop's nutrition assistant. Classify the LATEST user message.

- emergency: describes symptoms or a situation that may need urgent care now (e.g. chest pain, breathing trouble, severe allergic reaction, overdose or accidental ingestion, thoughts of self-harm).
- medical_advice: asks for something only a doctor or pharmacist should do: diagnose symptoms, interpret test results, start/stop/change a prescription medicine or its dose, use a supplement to treat a disease or instead of prescribed treatment.
- in_scope: supplements, ingredients, products, comparisons, general nutrition and wellness, or answering the assistant's questions.
- off_topic: anything else.

A message can mention a condition and still be in_scope ("I have diabetes, which vitamin C is OK?"). Use medical_advice only when the user is asking for the doctor-only part."""


class Review(BaseModel):
    verdict: Literal["pass", "revise"]
    problems: list[str]


REVIEW_PROMPT = """You review a draft reply from a supplement shop's nutrition assistant before it is shown to the user. Return "revise" if the draft does ANY of the following, otherwise "pass":

1. Recommends, endorses or encourages using a product from the NOT RECOMMENDABLE list. Mentioning such a product to say it is unsuitable, or needs the user's doctor's approval, is fine.
2. Diagnoses the user, interprets their symptoms or test results as a specific condition, or tells them to start, stop, or change a prescription medicine or its dose.
3. Claims a supplement treats, cures or prevents a disease, or suggests it in place of medical treatment.
4. Suggests taking more than the label dose.
5. The user described symptoms that are persistent, severe or unexplained, and the draft does not suggest seeing a doctor.

List each problem concretely so the writer can fix it. Do not flag tone, length or style."""


def triage(client: anthropic.Anthropic, history_tail: str, message: str) -> Triage:
    try:
        resp = client.messages.parse(
            model=GUARD_MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            system=TRIAGE_PROMPT,
            messages=[{"role": "user", "content":
                       f"<recent_conversation>\n{history_tail}\n</recent_conversation>\n\n"
                       f"<latest_user_message>\n{message}\n</latest_user_message>"}],
            output_format=Triage,
        )
        if resp.parsed_output is not None:
            return resp.parsed_output
    except anthropic.APIError as e:
        log.warning("triage failed, defaulting to medical_advice framing: %s", e)
    # Fail safe: if we can't classify, run the agent with the medical-advice reminder on.
    return Triage(route="medical_advice", reason="triage unavailable")


def review(client: anthropic.Anthropic, *, user_message: str, draft: str, profile: dict,
           not_recommendable: dict[str, str], recommended: list[str]) -> Review:
    blocked = "\n".join(f"- {t} ({s})" for t, s in not_recommendable.items()) or "(none)"
    try:
        resp = client.messages.parse(
            model=GUARD_MODEL,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=REVIEW_PROMPT,
            messages=[{"role": "user", "content":
                       f"<user_profile>{profile}</user_profile>\n"
                       f"<not_recommendable>\n{blocked}\n</not_recommendable>\n"
                       f"<product_cards_shown>{recommended}</product_cards_shown>\n"
                       f"<user_message>\n{user_message}\n</user_message>\n"
                       f"<draft_reply>\n{draft}\n</draft_reply>"}],
            output_format=Review,
        )
        if resp.parsed_output is not None:
            return resp.parsed_output
    except anthropic.APIError as e:
        log.warning("review failed: %s", e)
    return Review(verdict="revise", problems=["Automated safety review was unavailable."])
