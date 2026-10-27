"""FastAPI app: POST /api/chat, GET /api/info, and the React UI (web/dist) at /.

Sessions live in memory for the prototype (Redis / Postgres in production).
Run:  uvicorn nutribot.server:app --reload    (build the UI first: cd web && npm run build)
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import NutriBot
from .claude_code_client import ClaudeCodeError
from .config import AGENT_MODEL, GUARD_MODEL, resolved_backend
from .tools import Session

app = FastAPI(title="Nutri - supplement assistant (prototype)")
_bot: NutriBot | None = None
_sessions: dict[str, Session] = {}
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
log = logging.getLogger("uvicorn.error")

log.info("model backend: %s", resolved_backend())
if resolved_backend() == "claude-code" and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    log.warning("No ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN set; using the Claude Code CLI's own login, "
                "if any. Run `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN before starting the server.")


def bot() -> NutriBot:
    global _bot
    if _bot is None:
        _bot = NutriBot()
    return _bot


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    sid = body.session_id or str(uuid.uuid4())
    session = _sessions.get(sid)
    if session is None:
        if body.session_id:
            raise HTTPException(404, "unknown session")
        session = _sessions[sid] = bot().new_session()
    n_messages = len(session.messages)
    try:
        result = bot().chat(session, body.message)
    except Exception as e:
        del session.messages[n_messages:]  # drop the half-finished turn so the session stays usable
        log.exception("chat turn failed")
        raise HTTPException(*_explain(e)) from e
    return {"session_id": sid, **asdict(result)}


def _explain(e: Exception) -> tuple[int, str]:
    if isinstance(e, anthropic.AuthenticationError) or "authentication method" in str(e):
        return 503, ("No valid credentials. Set ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`, "
                     "in the terminal that runs the server, then restart it.")
    if isinstance(e, ClaudeCodeError):
        return 502, str(e)
    if isinstance(e, anthropic.APIStatusError):
        return 502, f"Anthropic API error {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return 502, "Could not reach the Anthropic API (network problem)."
    return 500, f"{type(e).__name__}: {e}"


@app.get("/api/info")
def info() -> dict:
    return {"backend": resolved_backend(), "agent_model": AGENT_MODEL, "guard_model": GUARD_MODEL}


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def ui_not_built() -> str:
        return ("<p>The web UI isn't built yet. Run <code>cd web && npm install && npm run build</code>, "
                "or use the dev server (<code>npm run dev</code>, port 5173). The API is at /api/chat.</p>")
