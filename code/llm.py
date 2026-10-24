"""Model calls through LiteLLM, over a fallback chain, behind a cache and a usage log.

Configuration (environment, a `.env` file at the repository root - never committed - or,
on Windows, the saved user environment):
  LLM_MODELS       comma-separated LiteLLM model ids, tried in order, e.g.
                   gemini/gemini-3.8-flash,gemini/gemini-3.7-flash,gemini/gemini-3.5-flash
                   (defaults to gemini/<GEMINI_MODEL> when only GEMINI_MODEL is set)
  GEMINI_API_KEY   key for gemini/* models; other providers use LiteLLM's usual variables
                   (e.g. OPENROUTER_API_KEY, GROQ_API_KEY)

Free tiers cap requests per model per day, so a chain of models multiplies the free
quota: a model whose quota is exhausted (or that rejects the request) hands over to the
next one, while short overloads (503, brief 429) are retried on the same model.

Every answer is cached by a hash of (task, prompt, images, schema) under code/llm_cache/,
so re-runs are free and reproducible, and the usage log records which model answered.
"""
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / "llm_cache"
USAGE_LOG = CACHE / "usage.jsonl"
RETRIES = 4  # extra attempts per model on transient errors, backing off 5 s .. 40 s
WORKERS = 4  # concurrent requests: independent calls overlap, capped to stay under rate limits
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
             "MISTRAL_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
_lock = threading.Lock()  # guards the usage log across threads


class NotConfigured(RuntimeError):
    pass


class AllModelsFailed(RuntimeError):
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


def _windows_user_env(name):
    """A variable saved in the Windows user environment after this process started."""
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0] or None
    except OSError:
        return None


def setting(name):
    return os.environ.get(name) or _dotenv().get(name) or _windows_user_env(name)


def models():
    """The configured fallback chain of LiteLLM model ids."""
    chain = setting("LLM_MODELS")
    if chain:
        return [m.strip() for m in chain.split(",") if m.strip()]
    return [f"gemini/{setting('GEMINI_MODEL')}"] if setting("GEMINI_MODEL") else []


def _export_keys():
    """Make keys found in .env / the Windows user environment visible to LiteLLM."""
    for name in KEY_NAMES:
        value = setting(name)
        if value and not os.environ.get(name):
            os.environ[name] = value


def configured():
    return bool(models()) and any(setting(n) for n in KEY_NAMES)


def _cache_key(task, prompt, schema, images, model=""):
    h = hashlib.sha256()
    for part in (model, task, prompt, json.dumps(schema, sort_keys=True)):
        h.update(part.encode("utf-8"))
    for _, data in images:
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:24]


def _json_schema(schema):
    """Gemini-style `nullable: true` -> standard JSON Schema `anyOf [..., null]` (LiteLLM
    converts it back for Gemini and passes it through for other providers)."""
    if isinstance(schema, list):
        return [_json_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _json_schema(v) for k, v in schema.items() if k != "nullable"}
    if schema.get("nullable"):
        inner = {k: v for k, v in out.items() if k not in ("description",)}
        out = {"anyOf": [inner, {"type": "null"}]}
    return out


def _retry_delay(error):
    """Seconds the provider asks us to wait before retrying, if it says (Gemini RetryInfo)."""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", str(error))
    return float(m.group(1)) if m else 0.0


def _quota_exhausted(error):
    """A rate limit that will not clear soon: a daily cap, or a long requested wait."""
    text = str(error)
    return "PerDay" in text or "per day" in text.lower() or _retry_delay(error) > 120


def ask_json(task, prompt, schema, images=(), run_id=None):
    """Return the model's JSON answer for `prompt` (+ PNG images), constrained by `schema`.

    task:   short label used in the usage report (e.g. 'vision', 'intent')
    images: iterable of (name, png_bytes)
    Tries each model in models() until one answers; raises AllModelsFailed otherwise.
    """
    images = list(images)
    chain = models()
    if not chain:
        raise NotConfigured("set LLM_MODELS (or GEMINI_MODEL) and a provider key (environment or .env)")
    key = _cache_key(task, prompt, schema, images)
    legacy = [_cache_key(task, prompt, schema, images, m.split("/", 1)[-1]) for m in chain]
    for k in [key] + legacy:  # answers cached before the LiteLLM switch keyed on the bare model id
        cache_file = CACHE / task / f"{k}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            # answers cached before the LiteLLM switch name the model without its provider
            model = cached["model"] if "/" in cached["model"] else f"gemini/{cached['model']}"
            _log_usage(task, model, run_id, cached["usage"], cached=True)
            return cached["answer"]

    import base64
    import litellm
    litellm.suppress_debug_info = True  # errors are handled below; skip LiteLLM's help banner
    _export_keys()
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                 base64.standard_b64encode(data).decode("ascii")}}
               for _, data in images] + [{"type": "text", "text": prompt}]
    response_format = {"type": "json_schema",
                       "json_schema": {"name": task, "schema": _json_schema(schema)}}
    errors = []
    for model in chain:
        started = time.time()
        for attempt in range(RETRIES + 1):
            try:
                response = litellm.completion(model=model, messages=[{"role": "user", "content": content}],
                                              response_format=response_format, temperature=0)
                answer = json.loads(response.choices[0].message.content)
            except litellm.AuthenticationError:
                raise  # a bad key will not get better on another model
            except (litellm.RateLimitError, litellm.ServiceUnavailableError, litellm.InternalServerError,
                    litellm.Timeout, litellm.APIConnectionError) as e:
                if isinstance(e, litellm.RateLimitError) and _quota_exhausted(e) or attempt == RETRIES:
                    errors.append((model, e))
                    _log_failure(task, model, run_id, started, e)
                    break  # next model in the chain
                time.sleep(min(40, 5 * 2 ** attempt))
                continue
            except (litellm.BadRequestError, litellm.APIError, json.JSONDecodeError) as e:
                errors.append((model, e))  # this model rejected the request or returned bad JSON
                _log_failure(task, model, run_id, started, e)
                break
            usage = {"input_tokens": response.usage.prompt_tokens or 0,
                     "output_tokens": response.usage.completion_tokens or 0,  # includes thinking
                     "total_tokens": response.usage.total_tokens or 0,
                     "seconds": round(time.time() - started, 2)}
            cache_file = CACHE / task / f"{key}.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"task": task, "model": model, "usage": usage, "answer": answer},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
            _log_usage(task, model, run_id, usage, cached=False)
            return answer
    raise AllModelsFailed("; ".join(f"{m}: {type(e).__name__} {str(e)[:160]}" for m, e in errors))


def ask_json_many(jobs):
    """Run several ask_json(**job) calls concurrently. Results come back in job order; a
    job that failed returns its exception instead of an answer, so one unavailable call
    does not discard the others."""
    def run(job):
        try:
            return ask_json(**job)
        except Exception as e:  # reported per job; callers decide how to degrade
            return e
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(run, jobs))


def list_models():
    """Gemini model ids this key can call with generate_content (to build LLM_MODELS)."""
    key = setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")
    if not key:
        raise NotConfigured("set GEMINI_API_KEY (environment or .env)")
    from google import genai
    client = genai.Client(api_key=key)  # keep a reference: the pager closes with the client
    return sorted("gemini/" + m.name.removeprefix("models/") for m in client.models.list()
                  if "generateContent" in (m.supported_actions or []))


def _log_failure(task, model, run_id, started, error):
    _log_usage(task, model, run_id, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                                     "seconds": round(time.time() - started, 2)},
               cached=False, failed=f"{type(error).__name__}")


def _log_usage(task, model, run_id, usage, cached, failed=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    entry = {"run_id": run_id, "task": task, "model": model, "cached": cached, **usage}
    if failed:
        entry["failed"] = failed  # refused or errored call: no answer, no tokens billed
    with _lock, open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":  # python code/llm.py  -> lists the Gemini model ids this key can use
    try:
        print("\n".join(list_models()))
    except NotConfigured as e:
        raise SystemExit(str(e))
