"""Run the model calls through the Claude Code CLI instead of the Anthropic API.

For local use with a Claude subscription token (`claude setup-token` ->
CLAUDE_CODE_OAUTH_TOKEN) when there is no API key. It exposes the two client methods the
bot uses, `beta.messages.create` (agent steps) and `messages.parse` (guards), so
agent.py and guardrails.py are unchanged.

Each call is one headless `claude -p` run with every built-in tool disabled and a JSON
schema for the output. Native tool use isn't available that way, so an agent step asks
for {"tool_calls": [...], "reply": "..."} and the Python loop runs the tools exactly as it
does on the API path; the safety engine is the same code either way.

Trade-offs vs the API backend: ~2-3 s CLI start-up per call, no thinking blocks carried
between steps, no prompt caching across steps, no server-side refusal fallback. Use the
API backend for anything that serves real users.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from types import SimpleNamespace as NS

import anthropic

log = logging.getLogger(__name__)

TIMEOUT_S = 300

AGENT_PROTOCOL = """

# How to respond (tool protocol)
You can't call tools natively here. Each response is one JSON object:
- To call tools: put one or more calls in "tool_calls", each with "name" and "input_json" (the
  arguments as a JSON object encoded as a string). Leave "reply" empty. You'll get the results
  in the next request and can call more tools or answer.
- To answer the user: leave "tool_calls" empty and write the message to the user in "reply".
The conversation so far is in <conversation>. <operator_note> entries come from the shop's system,
not the user; follow them.

# Tools
{tools}"""


class ClaudeCodeError(anthropic.APIError):
    """A failed CLI run. Subclasses APIError so the guards' fail-safe handlers catch it."""

    def __init__(self, message: str):
        Exception.__init__(self, message)  # no HTTP request to attach
        self.message, self.request, self.body = message, None, None


class ClaudeCodeClient:
    def __init__(self, executable: str = "claude"):
        if shutil.which(executable) is None:
            raise RuntimeError("Claude Code CLI not found on PATH (npm install -g @anthropic-ai/claude-code)")
        self.executable = executable  # resolved per call: Claude Code auto-updates replace the binary
        self.workdir = tempfile.mkdtemp(prefix="nutribot-cc-")  # empty dir: no CLAUDE.md pickup
        self.beta = NS(messages=NS(create=self._agent_step))
        self.messages = NS(parse=self._parse)

    # ----------------------------------------------------------------------------------------
    def _agent_step(self, *, model, system, tools, tool_choice, messages, output_config=None, **_ignored):
        allow_tools = tool_choice.get("type") != "none"
        schema = {
            "type": "object",
            "properties": {
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "enum": [t["name"] for t in tools]},
                                       "input_json": {"type": "string"}},
                        "required": ["name", "input_json"],
                        "additionalProperties": False,
                    },
                    **({} if allow_tools else {"maxItems": 0}),
                },
                "reply": {"type": "string"},
            },
            "required": ["tool_calls", "reply"],
            "additionalProperties": False,
        }
        tool_docs = "\n\n".join(f"## {t['name']}\n{t['description']}\nInput schema: {json.dumps(t['input_schema'])}"
                                for t in tools)
        prompt = f"<conversation>\n{render_conversation(messages)}\n</conversation>\n\n" + (
            "Give your next step." if allow_tools else "Answer the user now, without calling tools.")
        out = self._run(model=model, system=system + AGENT_PROTOCOL.format(tools=tool_docs), prompt=prompt,
                        schema=schema, effort=(output_config or {}).get("effort"))
        if out.get("stop_reason") == "refusal":
            return NS(content=[NS(type="text", text="")], stop_reason="refusal", stop_details=None)

        step = out["structured_output"]
        content = []
        if step["reply"].strip():
            content.append(NS(type="text", text=step["reply"]))
        for call in step["tool_calls"] if allow_tools else []:
            try:
                args = json.loads(call["input_json"] or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                args = {"_invalid_json": call["input_json"]}  # run_tool reports the error back to the model
            content.append(NS(type="tool_use", id=f"toolu_{uuid.uuid4().hex[:16]}", name=call["name"], input=args))
        has_tools = any(b.type == "tool_use" for b in content)
        return NS(content=content, stop_reason="tool_use" if has_tools else "end_turn", stop_details=None)

    def _parse(self, *, model, system, messages, output_format, output_config=None, **_ignored):
        prompt = "\n\n".join(m["content"] for m in messages if isinstance(m["content"], str))
        out = self._run(model=model, system=system, prompt=prompt, schema=output_format.model_json_schema(),
                        effort=(output_config or {}).get("effort"))
        return NS(parsed_output=output_format.model_validate(out["structured_output"]))

    # ----------------------------------------------------------------------------------------
    def _run(self, *, model: str, system: str, prompt: str, schema: dict, effort: str | None) -> dict:
        cmd = [self.executable, "-p", "--output-format", "json", "--model", model,
               "--system-prompt", system, "--json-schema", json.dumps(schema),
               "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
        if effort:
            cmd += ["--effort", effort]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S,
                                  cwd=self.workdir)
        except subprocess.TimeoutExpired as e:
            raise ClaudeCodeError(f"Claude Code call timed out after {TIMEOUT_S}s") from e
        except FileNotFoundError as e:
            raise ClaudeCodeError("Claude Code CLI not found on PATH (it may be mid-update; retry shortly)") from e
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ClaudeCodeError(_cli_error_text(proc.stderr or proc.stdout)) from None
        if out.get("is_error") or out.get("structured_output") is None and out.get("stop_reason") != "refusal":
            raise ClaudeCodeError(_cli_error_text(str(out.get("result") or out.get("subtype") or proc.stderr)))
        return out


def render_conversation(messages: list[dict]) -> str:
    """API-style message list -> tagged transcript (tool calls and results included)."""
    parts = []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            parts.append(f"<operator_note>\n{content}\n</operator_note>")
            continue
        if isinstance(content, str):
            parts.append(f"<{role}>\n{content}\n</{role}>")
            continue
        inner = []
        for b in content:
            get = b.get if isinstance(b, dict) else lambda k, b=b: getattr(b, k, None)
            kind = get("type")
            if kind == "text" and get("text"):
                inner.append(get("text"))
            elif kind == "tool_use":
                inner.append(f'<tool_call id="{get("id")}" name="{get("name")}">{json.dumps(get("input"))}</tool_call>')
            elif kind == "tool_result":
                err = ' is_error="true"' if get("is_error") else ""
                inner.append(f'<tool_result id="{get("tool_use_id")}"{err}>\n{get("content")}\n</tool_result>')
        if inner:
            parts.append(f"<{role}>\n" + "\n".join(inner) + f"\n</{role}>")
    return "\n\n".join(parts)


def _cli_error_text(raw: str) -> str:
    raw = (raw or "").strip()
    if any(s in raw.lower() for s in ("invalid api key", "not logged in", "please run /login", "oauth", "401")):
        return ("Claude Code is not authenticated. Run `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN "
                f"in the terminal that runs the server. ({raw[:200]})")
    return f"Claude Code call failed: {raw[:500] or 'no output'}"
