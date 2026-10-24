"""ADK model callbacks: answer cache and token usage log, shared with the batch pipeline.

before_model_callback returns a stored LlmResponse when the exact same request (model,
instructions, conversation, tool set) was answered before, so ADK skips the model call;
after_model_callback stores new answers and logs their tokens to llm_cache/usage.jsonl.
ADK passes these by keyword, so the parameter names must stay callback_context /
llm_request / llm_response.
"""
import hashlib
import json
import sys
import threading
from pathlib import Path

from google.adk.models.llm_response import LlmResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm import CACHE, _log_usage  # noqa: E402

AGENT_CACHE = CACHE / "agent"
RUN_ID = None  # set by the runner so usage lines group per run
_pending = {}  # (invocation_id, agent_name) -> cache key of the request in flight
_lock = threading.Lock()


def _key(llm_request):
    config = llm_request.config
    payload = {
        "model": llm_request.model,
        "system": str(getattr(config, "system_instruction", "") or ""),
        "contents": [c.model_dump(mode="json", exclude_none=True) for c in llm_request.contents],
        "tools": sorted(getattr(llm_request, "tools_dict", {}) or {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def before_model(callback_context, llm_request):
    key = _key(llm_request)
    path = AGENT_CACHE / callback_context.agent_name / f"{key}.json"
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        _log_usage(f"agent:{callback_context.agent_name}", stored["model"], RUN_ID, stored["usage"], cached=True)
        return LlmResponse.model_validate(stored["response"])
    with _lock:
        _pending[(callback_context.invocation_id, callback_context.agent_name)] = (key, llm_request.model)
    return None


def after_model(callback_context, llm_response):
    with _lock:
        key, model = _pending.pop((callback_context.invocation_id, callback_context.agent_name), (None, None))
    if key is None or llm_response.partial or llm_response.content is None:
        return None
    meta = llm_response.usage_metadata
    usage = {"input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
             "output_tokens": (getattr(meta, "candidates_token_count", 0) or 0)
             + (getattr(meta, "thoughts_token_count", 0) or 0),
             "total_tokens": getattr(meta, "total_token_count", 0) or 0, "seconds": 0}
    path = AGENT_CACHE / callback_context.agent_name / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": llm_response.model_version or model, "usage": usage,
                                "response": llm_response.model_dump(mode="json", exclude_none=True)},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    _log_usage(f"agent:{callback_context.agent_name}", llm_response.model_version or model, RUN_ID, usage,
               cached=False)
    return None
