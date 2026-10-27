"""Agent loop plumbing against a scripted fake Anthropic client (no network)."""

from types import SimpleNamespace as NS

from nutribot import guardrails
from nutribot.agent import NutriBot
from nutribot.catalog import Catalog

CAT = Catalog.load()


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, **input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def msg(*content, stop="end_turn"):
    return NS(content=list(content), stop_reason=stop, stop_details=None)


class FakeClient:
    """Plays back scripted agent responses; guard calls return scripted verdicts."""

    def __init__(self, agent_script, triage="in_scope", reviews=("pass",)):
        self.agent_script = list(agent_script)
        self.reviews = list(reviews)
        self.triage_route = triage
        self.agent_requests = []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(parse=self._parse)

    def _create(self, **kw):
        self.agent_requests.append({**kw, "messages": list(kw["messages"])})  # snapshot
        return self.agent_script.pop(0)

    def _parse(self, **kw):
        fmt = kw["output_format"]
        if fmt is guardrails.Triage:
            return NS(parsed_output=guardrails.Triage(route=self.triage_route, reason="test"))
        verdict = self.reviews.pop(0)
        return NS(parsed_output=guardrails.Review(verdict=verdict, problems=[] if verdict == "pass" else ["bad"]))


def screened_profile_call():
    return tool_use("t1", "update_user_profile", sex="female", pregnancy_status="pregnant",
                    conditions=["none"], medications=["none"], allergies=["none"])


def test_tool_loop_then_recommendation_card():
    folic = CAT.find_by_title("Folic Acid 900mcg").id
    client = FakeClient([
        msg(screened_profile_call(), stop="tool_use"),
        msg(tool_use("t2", "recommend_products", items=[{"product_id": folic, "why_for_this_user": "folate"}]),
            stop="tool_use"),
        msg(text("Folic acid is a good fit.")),
    ])
    bot = NutriBot(client=client, catalog=CAT)
    s = bot.new_session()
    r = bot.chat(s, "I'm pregnant, what should I take?")
    assert r.reply == "Folic acid is a good fit."
    assert [c["title"] for c in r.recommendations] == ["Folic Acid 900mcg"]
    assert r.profile["risk_factors"] == ["pregnancy"]
    # history: user, assistant(tool), user(result), assistant(tool), user(result), assistant(text)
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    # every request carried the same system prompt and tools (cache-stable prefix)
    assert len({id(req["tools"]) for req in client.agent_requests}) == 1
    assert all(req["fallbacks"] == "default" for req in client.agent_requests)


def test_emergency_keyword_short_circuits_without_model_calls():
    client = FakeClient([])
    bot = NutriBot(client=client, catalog=CAT)
    r = bot.chat(bot.new_session(), "I took too many melatonin tablets and feel faint")
    assert "995" in r.reply
    assert client.agent_requests == []


def test_medical_advice_route_adds_operator_note():
    client = FakeClient([msg(text("Please talk to your doctor about that."))], triage="medical_advice")
    bot = NutriBot(client=client, catalog=CAT)
    s = bot.new_session()
    bot.chat(s, "Should I stop my metformin?")
    sent = client.agent_requests[0]["messages"]
    assert sent[-1]["role"] == "system" and "doctor or pharmacist" in sent[-1]["content"]


def test_review_failure_triggers_one_revision_then_passes():
    client = FakeClient([msg(text("draft 1")), msg(text("draft 2"))], reviews=("revise", "pass"))
    bot = NutriBot(client=client, catalog=CAT)
    s = bot.new_session()
    r = bot.chat(s, "hi")
    assert r.reply == "draft 2"
    # rejected draft is not in history; the revision note is
    roles = [m["role"] for m in s.messages]
    assert roles == ["user", "system", "assistant"]
    assert all(not (m["role"] == "assistant" and m["content"][0].text == "draft 1") for m in s.messages)


def test_review_failing_twice_returns_safe_fallback_and_drops_cards():
    client = FakeClient([msg(text("draft 1")), msg(text("draft 2"))], reviews=("revise", "revise"))
    bot = NutriBot(client=client, catalog=CAT)
    r = bot.chat(bot.new_session(), "hi")
    assert r.reply == guardrails.SAFE_FALLBACK_REPLY
    assert r.recommendations == []


def test_refusal_returns_fallback():
    client = FakeClient([msg(text(""), stop="refusal")])
    bot = NutriBot(client=client, catalog=CAT)
    r = bot.chat(bot.new_session(), "hi")
    assert r.reply == guardrails.SAFE_FALLBACK_REPLY
