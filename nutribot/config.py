import os

# Main conversational agent: multi-step tool use and nuanced, personalised comparisons.
AGENT_MODEL = os.getenv("NUTRIBOT_AGENT_MODEL", "claude-opus-5-5")
# effort: low | medium | high | xhigh | max. Opus 5.5 defaults to medium; set explicitly.
AGENT_EFFORT = os.getenv("NUTRIBOT_AGENT_EFFORT", "medium")

# Guard model for input triage and output review: small, fast, structured output.
GUARD_MODEL = os.getenv("NUTRIBOT_GUARD_MODEL", "claude-haiku-5-5")

MAX_AGENT_STEPS = 12  # tool-use round trips per user turn

# Which client runs the model calls:
#   api          Anthropic API (ANTHROPIC_API_KEY)
#   claude-code  headless Claude Code CLI with a subscription token (CLAUDE_CODE_OAUTH_TOKEN from
#                `claude setup-token`); for running the prototype locally without an API key
#   auto         api if ANTHROPIC_API_KEY is set, otherwise claude-code
BACKEND = os.getenv("NUTRIBOT_BACKEND", "auto")


def resolved_backend() -> str:
    if BACKEND != "auto":
        return BACKEND
    return "api" if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") else "claude-code"


def make_client():
    if resolved_backend() == "claude-code":
        from .claude_code_client import ClaudeCodeClient
        return ClaudeCodeClient()
    import anthropic
    return anthropic.Anthropic()
