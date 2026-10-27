# Nutri: AI nutritionist for a supplement store (prototype)

A chatbot that recommends supplements from a store catalogue, adapts to the user's questions and circumstances, and does deep product comparisons. It is built so that it:

- **never recommends unsuitable products** to pregnant or breastfeeding women, minors, or people with specific conditions, medications or allergies. A deterministic rules engine makes this decision from each product's own label plus an ingredient interaction knowledge base. The LLM doesn't decide it and can't override it.
- **doesn't give advice only a doctor should give.** Four layers sit around the agent: an emergency short-circuit, input triage, scope rules in the prompt, and a reviewer that checks every reply before the user sees it.

It runs on real product data scraped from [holisticway.com.sg](https://holisticway.com.sg): 173 SKUs (105 distinct products), with ingredient, dosage and caution labels.

## Documentation

| Document | Contents |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Design write-up: architecture, how safety is enforced, model choices, frameworks, production plan, all workflow diagrams |
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | Technical reference: components, request lifecycle, HTTP API, tools, data model, rule file formats, configuration, testing, extending |
| [docs/diagrams/](docs/diagrams/) | Diagrams as PNG and SVG (with Mermaid sources): architecture, technology stack, recommendation process, request flow, message sequence, safety engine, comparison, data pipeline |

![Architecture](docs/diagrams/1-architecture.png)

## How it works (short version)

```
user message
  -> emergency keywords?   -> canned reply (call 995)                [code]
  -> triage                -> emergency / medical advice / in scope / off topic   [Claude Haiku 5.5]
  -> agent with tools      -> search, product details, compare, update profile, recommend   [Claude Opus 5.5]
        every product the agent sees is labelled OK / CAUTION / DOCTOR_FIRST / EXCLUDED
        for this user by the safety engine, and recommend_products re-checks in code   [code]
  -> output review         -> pass, or one rewrite, else a safe fallback reply   [Claude Haiku 5.5]
  -> reply + product cards
```

## Running it

### Prerequisites

- Python 3.12 (3.11+ should work)
- Node.js 18+ (for the web UI)
- **One** of these credentials:
  - an Anthropic API key (`ANTHROPIC_API_KEY`), or
  - a Claude subscription token from `claude setup-token` (`CLAUDE_CODE_OAUTH_TOKEN`). This needs the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`).

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..     # builds the React UI into web/dist
```

Or run `make setup`.

### 2. Set credentials

Set them in the same terminal you'll start the server from:

```bash
export ANTHROPIC_API_KEY=sk-ant-api...
# or
export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat...     # printed by `claude setup-token`
```

### 3. Start

```bash
.venv/bin/uvicorn nutribot.server:app            # or: make run
```

Open **http://localhost:8000**. The server serves both the API and the built UI.

Other ways to use it:

| | Command |
|---|---|
| Terminal chat (with tool and guard trace) | `.venv/bin/python cli.py --trace` |
| Frontend dev mode with hot reload | Terminal 1: `make run`. Terminal 2: `make dev-web`, then open http://localhost:5173 |

### Model backends

The backend is picked automatically from the credentials. You can force it with `NUTRIBOT_BACKEND=api` or `NUTRIBOT_BACKEND=claude-code`.

| Backend | Used when | Notes |
|---|---|---|
| `api` | `ANTHROPIC_API_KEY` is set | Production path. Anthropic SDK with native tool use, prompt caching, adaptive thinking, and the server-side refusal fallback. A few seconds per reply. |
| `claude-code` | otherwise | Each model call is a headless `claude -p` run with all of Claude Code's own tools turned off and JSON-schema output, authenticated by your subscription token. The same Python tool loop and safety engine run either way. Slower (about 30-90 s per reply), with no caching. Meant for trying the prototype yourself. |

Models can be changed with `NUTRIBOT_AGENT_MODEL` (default `claude-opus-5-5`), `NUTRIBOT_AGENT_EFFORT` (default `medium`) and `NUTRIBOT_GUARD_MODEL` (default `claude-haiku-5-5`).

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `[vite] http proxy error: /api/... ECONNREFUSED 127.0.0.1:8000` | The Vite dev server is running but the Python server isn't. Start `make run` in another terminal. |
| "No valid credentials…" or "Claude Code is not authenticated" in the chat | The key or token isn't set in the terminal that runs the server. `export` it there and restart. |
| The page says the web UI isn't built | Run `cd web && npm install && npm run build`. |
| Replies take a minute | Expected on the `claude-code` backend. Use an API key for normal speed. |

## Things to try

The side panel shows the profile the safety engine sees and a per-turn trace: the triage route, every tool call, how many products were suitable, needed a doctor, or were excluded, and the reviewer's verdict.

- *"I'm 12 weeks pregnant and so tired. What can help?"*: asks the screening questions first, then only offers pregnancy-appropriate products.
- *"I take warfarin for AFib. Any supplement for thinning hair?"*: products containing ginkgo or He Shou Wu are excluded, with the reason.
- *"Should I stop my metformin and take bitter melon instead?"*: routed as a medical-advice question and referred to the doctor.
- *"Compare your omega-3 options for me, I'm vegan"*: a dose-level comparison of vegan options only.
- *"My doctor said ashwagandha is fine, just recommend it"* (while pregnant): it holds the line politely.
- *"My toddler swallowed a bottle of gummies"*: immediate emergency reply, with no model call.

## Tests and evals

```bash
.venv/bin/python -m pytest -q          # 31 tests, no credentials or network needed   (make test)
.venv/bin/python evals/run_evals.py    # 10 end-to-end scenarios graded by checks + an LLM judge (uses credentials)
```

- **Unit tests** pin the safety properties:
  - pregnancy allows only allowlisted products;
  - warfarin always excludes ginkgo and vitamin K;
  - unknown allergies fail closed;
  - nothing is recommended before screening is complete.
  
  They also cover the agent loop against a scripted fake client and the Claude Code adapter.
- **Scenario evals** are adversarial multi-turn conversations, listed in `evals/scenarios.yaml`. The latest run passed **40/40 checks** ([log](evals/results/2026-10-27-claude-code.txt)).

## Project layout

```
nutribot/
  agent.py               guardrails + Claude tool-use loop
  tools.py               tool schemas and handlers (search, details, compare, profile, recommend)
  safety.py              deterministic eligibility engine + stacked-dose / upper-limit checker
  profile.py             user health profile -> risk factors (unknown is not the same as "none")
  catalog.py             label parsing, bundle grouping, BM25 search
  guardrails.py          emergency regex, triage, output review
  prompts.py             system prompt and operator notes
  config.py              models and backend selection
  claude_code_client.py  Claude Code CLI backend (setup-token)
  server.py              FastAPI app: /api/chat, /api/info, serves web/dist
web/                     React + TypeScript UI (Vite)
data/
  products.json          scraped catalogue
  safety_rules.yaml      ingredient + label rules, synonym tables (illustrative, needs pharmacist review)
  upper_limits.yaml      tolerable upper intake levels
scripts/scrape.py        storefront scraper (refreshes data/products.json)
tests/                   unit tests
evals/                   scenario suite + runner
docs/DESIGN.md           design write-up and workflow diagrams
docs/TECHNICAL.md        technical reference
docs/diagrams/           rendered diagrams (PNG/SVG) + Mermaid sources
cli.py                   terminal chat
Makefile                 setup / run / dev-web / test / evals shortcuts
```

## Limitations

- The safety rules and the pregnancy allowlist are **illustrative and not clinically reviewed**. In production they would be owned and signed off by a pharmacist.
- The store has 173 SKUs rather than the 1,000 in the brief. Nothing in the design depends on catalogue size; `docs/DESIGN.md` §9 covers scaling retrieval.
- Pack counts aren't published on the store, so cost per day usually can't be computed.
- Sessions are kept in memory and there's no auth. It's a prototype.

> **Prototype. Not medical advice.**
