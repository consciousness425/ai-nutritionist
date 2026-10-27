import os

# Main conversational agent: multi-step tool use and nuanced, personalised comparisons.
AGENT_MODEL = os.getenv("NUTRIBOT_AGENT_MODEL", "claude-opus-5-5")
# effort: low | medium | high | xhigh | max. Opus 5.5 defaults to medium; set explicitly.
AGENT_EFFORT = os.getenv("NUTRIBOT_AGENT_EFFORT", "medium")

# Guard model for input triage and output review: small, fast, structured output.
GUARD_MODEL = os.getenv("NUTRIBOT_GUARD_MODEL", "claude-haiku-5-5")

MAX_AGENT_STEPS = 12  # tool-use round trips per user turn

