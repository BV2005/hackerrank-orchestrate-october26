"""Gemini calls behind a cache and a usage log.

Configuration (environment, or a `.env` file at the repository root - never committed):
  GEMINI_API_KEY (or GOOGLE_API_KEY)   API key
  GEMINI_MODEL                         model id, e.g. the one your key has access to

Every call is cached by a hash of (model, task, prompt, images, schema) under
code/llm_cache/, so re-runs are free and reproducible. Each real API call appends its
token usage to code/llm_cache/usage.jsonl, which build_usage_report() summarizes.
"""
import hashlib
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / "llm_cache"
USAGE_LOG = CACHE / "usage.jsonl"


class NotConfigured(RuntimeError):
    pass


def _dotenv():
    path = ROOT / ".env"
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            pairs[k.strip()] = v.strip().strip('"').strip("'")
    return pairs


def setting(name):
    return os.environ.get(name) or _dotenv().get(name)


def configured():
    return bool((setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")) and setting("GEMINI_MODEL"))


_client = None


def _gemini():
    global _client
    if _client is None:
        key = setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")
        if not key or not setting("GEMINI_MODEL"):
            raise NotConfigured("set GEMINI_API_KEY and GEMINI_MODEL (environment or .env)")
        from google import genai
        _client = genai.Client(api_key=key)
    return _client


def ask_json(task, prompt, schema, images=(), run_id=None):
    """Return the model's JSON answer for `prompt` (+ PNG images), validated by `schema`.

    task:   short label used in the usage report (e.g. 'vision', 'intent')
    images: iterable of (name, png_bytes)
    """
    model = setting("GEMINI_MODEL") or ""
    h = hashlib.sha256()
    for part in (model, task, prompt, json.dumps(schema, sort_keys=True)):
        h.update(part.encode("utf-8"))
    for _, data in images:
        h.update(hashlib.sha256(data).digest())
    cache_file = CACHE / task / f"{h.hexdigest()[:24]}.json"
    if cache_file.exists():
        _log_usage(task, model, run_id, json.loads(cache_file.read_text(encoding="utf-8"))["usage"], cached=True)
        return json.loads(cache_file.read_text(encoding="utf-8"))["answer"]

    from google.genai import types
    contents = [types.Part.from_bytes(data=data, mime_type="image/png") for _, data in images] + [prompt]
    started = time.time()
    response = _gemini().models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=schema, temperature=0))
    meta = response.usage_metadata
    usage = {"input_tokens": meta.prompt_token_count or 0,
             "output_tokens": (meta.candidates_token_count or 0) + (getattr(meta, "thoughts_token_count", 0) or 0),
             "total_tokens": meta.total_token_count or 0, "seconds": round(time.time() - started, 2)}
    answer = json.loads(response.text)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"task": task, "model": model, "usage": usage, "answer": answer},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    _log_usage(task, model, run_id, usage, cached=False)
    return answer


def list_models():
    """Model ids this key can call with generate_content (to choose GEMINI_MODEL)."""
    key = setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")
    if not key:
        raise NotConfigured("set GEMINI_API_KEY (environment or .env)")
    from google import genai
    return sorted(m.name.removeprefix("models/") for m in genai.Client(api_key=key).models.list()
                  if "generateContent" in (m.supported_actions or []))


def _log_usage(task, model, run_id, usage, cached):
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "task": task, "model": model, "cached": cached, **usage}) + "\n")


if __name__ == "__main__":  # python code/llm.py  -> lists the model ids this key can use
    try:
        print("\n".join(list_models()))
    except NotConfigured as e:
        raise SystemExit(str(e))
