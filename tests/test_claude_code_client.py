"""Claude Code backend adapter: request rendering and response mapping (CLI stubbed out)."""

import json
from types import SimpleNamespace as NS

from nutribot import guardrails
from nutribot.agent import NutriBot
from nutribot.catalog import Catalog
from nutribot.claude_code_client import ClaudeCodeClient, render_conversation

CAT = Catalog.load()


def stub_client(outputs):
    """ClaudeCodeClient whose CLI runs return scripted structured outputs."""
    client = ClaudeCodeClient.__new__(ClaudeCodeClient)
    client.beta = NS(messages=NS(create=client._agent_step))
    client.messages = NS(parse=client._parse)
    client.calls = []
    outputs = list(outputs)

    def run(**kw):
        client.calls.append(kw)
        return {"structured_output": outputs.pop(0), "stop_reason": "end_turn"}

    client._run = run
    return client


def test_tool_calls_round_trip_through_the_agent_loop():
    folic = CAT.find_by_title("Folic Acid 900mcg").id
    profile = {"sex": "female", "pregnancy_status": "pregnant", "conditions": ["none"],
               "medications": ["none"], "allergies": ["none"]}
    client = stub_client([
        {"route": "in_scope", "reason": "supplement question"},                       # triage
        {"tool_calls": [{"name": "update_user_profile", "input_json": json.dumps(profile)}], "reply": ""},
        {"tool_calls": [{"name": "recommend_products", "input_json": json.dumps(
            {"items": [{"product_id": folic, "why_for_this_user": "folate"}]})}], "reply": ""},
        {"tool_calls": [], "reply": "Folic acid is a good fit."},
        {"verdict": "pass", "problems": []},                                          # review
    ])
    bot = NutriBot(client=client, catalog=CAT)
    r = bot.chat(bot.new_session(), "I'm pregnant, what should I take?")
    assert r.reply == "Folic acid is a good fit."
    assert [c["title"] for c in r.recommendations] == ["Folic Acid 900mcg"]
    # the third agent step saw both earlier tool calls and their results in the transcript
    last_agent_prompt = client.calls[3]["prompt"]
    assert last_agent_prompt.count("<tool_call ") == 2 and last_agent_prompt.count("<tool_result ") == 2


def test_last_step_forbids_tools_and_bad_json_becomes_tool_error():
    client = stub_client([{"tool_calls": [{"name": "search_products", "input_json": "{not json"}], "reply": ""}])
    resp = client.beta.messages.create(model="m", system="s", tools=[{"name": "search_products", "description": "d",
                                       "input_schema": {}}], tool_choice={"type": "auto"}, messages=[
                                       {"role": "user", "content": "hi"}])
    assert resp.stop_reason == "tool_use" and "_invalid_json" in resp.content[0].input

    client = stub_client([{"tool_calls": [], "reply": "done"}])
    client.beta.messages.create(model="m", system="s", tools=[{"name": "x", "description": "d", "input_schema": {}}],
                                tool_choice={"type": "none"}, messages=[{"role": "user", "content": "hi"}])
    assert client.calls[0]["schema"]["properties"]["tool_calls"]["maxItems"] == 0


def test_guard_parse_returns_pydantic_model():
    client = stub_client([{"route": "medical_advice", "reason": "medication change"}])
    t = guardrails.triage(client, "(start of conversation)", "Should I stop my metformin?")
    assert t.route == "medical_advice"


def test_render_conversation_marks_operator_notes():
    text = render_conversation([{"role": "user", "content": "hi"}, {"role": "system", "content": "be careful"}])
    assert "<user>\nhi\n</user>" in text and "<operator_note>\nbe careful\n</operator_note>" in text
