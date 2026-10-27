"""The conversational agent: guardrails around a Claude tool-use loop.

    user msg -> emergency regex -> triage (Haiku) -> agent loop (Opus + tools + safety engine)
             -> output review (Haiku) -> [one revision] -> reply + product cards

The message history is append-only (thinking blocks are passed back unchanged), and
operator notes are added as mid-conversation system messages rather than by editing
the cached system prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import anthropic

from . import guardrails
from .catalog import Catalog
from .config import AGENT_EFFORT, AGENT_MODEL, MAX_AGENT_STEPS, make_client
from .prompts import MEDICAL_ADVICE_NOTE, OFF_TOPIC_NOTE, REVISION_NOTE, SYSTEM_PROMPT
from .tools import TOOLS, Session, run_tool

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    reply: str
    route: str
    recommendations: list[dict] = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)


class NutriBot:
    def __init__(self, client: anthropic.Anthropic | None = None, catalog: Catalog | None = None):
        self.client = client or make_client()
        self.catalog = catalog or Catalog.load()

    def new_session(self) -> Session:
        return Session(catalog=self.catalog)

    # ------------------------------------------------------------------------------------
    def chat(self, session: Session, user_text: str) -> TurnResult:
        session.new_turn()

        if guardrails.emergency_keywords(user_text):
            return self._canned(session, user_text, guardrails.EMERGENCY_REPLY, "emergency (keyword)")

        triage = guardrails.triage(self.client, _history_tail(session.messages), user_text)
        session.trace.append({"guard": "triage", "route": triage.route, "reason": triage.reason})
        if triage.route == "emergency":
            return self._canned(session, user_text, guardrails.EMERGENCY_REPLY, "emergency")

        session.messages.append({"role": "user", "content": user_text})
        if triage.route == "medical_advice":
            session.messages.append({"role": "system", "content": MEDICAL_ADVICE_NOTE.format(reason=triage.reason)})
        elif triage.route == "off_topic":
            session.messages.append({"role": "system", "content": OFF_TOPIC_NOTE.format(reason=triage.reason)})

        reply = self._run_agent(session)
        for attempt in range(2):
            verdict = guardrails.review(
                self.client, user_message=user_text, draft=reply, profile=session.profile.describe(),
                not_recommendable=session.not_recommendable_seen,
                recommended=[r["title"] for r in session.recommendations],
            )
            session.trace.append({"guard": "review", "attempt": attempt + 1, "verdict": verdict.verdict,
                                  "problems": verdict.problems})
            if verdict.verdict == "pass":
                break
            session.messages.pop()  # the rejected draft (never re-sent, so history stays append-only)
            if attempt == 1:
                session.recommendations = []
                reply = guardrails.SAFE_FALLBACK_REPLY
                session.messages.append({"role": "assistant", "content": reply})
                break
            note = REVISION_NOTE.format(problems="\n".join(f"- {p}" for p in verdict.problems))
            if session.messages[-1]["role"] == "system":  # fold into the trailing operator note
                note = session.messages.pop()["content"] + "\n\n" + note
            session.messages.append({"role": "system", "content": note})
            reply = self._run_agent(session)

        return TurnResult(reply=reply, route=triage.route, recommendations=session.recommendations,
                          profile=session.profile.describe(), trace=session.trace)

    # ------------------------------------------------------------------------------------
    def _run_agent(self, session: Session) -> str:
        for step in range(MAX_AGENT_STEPS):
            last_step = step == MAX_AGENT_STEPS - 1
            response = self.client.beta.messages.create(
                model=AGENT_MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                tool_choice={"type": "none"} if last_step else {"type": "auto"},
                messages=session.messages,
                output_config={"effort": AGENT_EFFORT},
                cache_control={"type": "ephemeral"},
                # On a safety-classifier decline, re-run on a fallback model chosen by the API.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            session.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                log.warning("agent refusal: %s", response.stop_details)
                session.messages.pop()
                session.messages.append({"role": "assistant", "content": guardrails.SAFE_FALLBACK_REPLY})
                session.recommendations = []
                return guardrails.SAFE_FALLBACK_REPLY

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                results = []
                for block in tool_uses:
                    content, is_error = run_tool(session, block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": content, "is_error": is_error})
                session.messages.append({"role": "user", "content": results})
                continue

            return _text_of(response.content) or "Sorry, I didn't manage to put an answer together. Could you rephrase?"
        raise RuntimeError("agent loop ended without a reply")  # unreachable: last step forbids tools

    def _canned(self, session: Session, user_text: str, reply: str, route: str) -> TurnResult:
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": reply})
        session.trace.append({"guard": "triage", "route": route})
        return TurnResult(reply=reply, route=route, profile=session.profile.describe(), trace=session.trace)


def _text_of(content) -> str:
    return "\n\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def _history_tail(messages: list, n: int = 6) -> str:
    """Recent user/assistant text only (no tool payloads) for the triage model."""
    lines = []
    for m in messages:
        if m["role"] == "system":
            continue
        c = m["content"]
        text = c if isinstance(c, str) else _text_of(c) if m["role"] == "assistant" else ""
        if text:
            lines.append(f"{m['role']}: {text[:600]}")
    return "\n".join(lines[-n:]) or "(start of conversation)"
