"""Run an ADK agent on one message and return its final text (and the tools it called)."""
import asyncio
from datetime import datetime, timezone

from google.adk.runners import InMemoryRunner
from google.genai import types

from . import callbacks

APP = "deletion_audit"


async def _run(agent, text):
    runner = InMemoryRunner(agent=agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="auditor")
    final, calls = "", []
    async for event in runner.run_async(user_id="auditor", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=text)])):
        for call in event.get_function_calls() or []:
            calls.append(f"{call.name}({', '.join(f'{k}={v}' for k, v in (call.args or {}).items())})")
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final, calls


def run_agent(agent, text, run_id=None):
    """(final_text, tool_calls) for one message; model calls are cached and logged."""
    callbacks.RUN_ID = run_id or datetime.now(timezone.utc).strftime("agent-%Y%m%dT%H%M%SZ")
    return asyncio.run(_run(agent, text))
