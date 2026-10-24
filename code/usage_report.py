"""Write code/evaluation/usage_report.md for one full-dataset run, from llm_cache/usage.jsonl.

Prices are not hard-coded: set GEMINI_PRICE_INPUT_PER_MTOK and GEMINI_PRICE_OUTPUT_PER_MTOK
(USD per million tokens, from your provider's price list) to get cost estimates.
"""
import json
from collections import defaultdict

from llm import USAGE_LOG, HERE, models, setting

REPORT = HERE / "evaluation" / "usage_report.md"


def write_usage_report(run_id, n_requests):
    entries = []
    if USAGE_LOG.exists():
        entries = [json.loads(line) for line in USAGE_LOG.read_text(encoding="utf-8").splitlines() if line]
    entries = [e for e in entries if e["run_id"] == run_id]
    price_in, price_out = setting("GEMINI_PRICE_INPUT_PER_MTOK"), setting("GEMINI_PRICE_OUTPUT_PER_MTOK")
    priced = price_in is not None and price_out is not None

    failures = [e for e in entries if e.get("failed")]
    entries = [e for e in entries if not e.get("failed")]
    groups = defaultdict(lambda: defaultdict(int))
    for e in entries:
        for key in ((e["model"], e["task"]), (e["model"], "all tasks"), ("all models", "all tasks")):
            g = groups[key]
            g["calls"] += 1
            g["api_calls"] += not e["cached"]
            g["input"] += e["input_tokens"]
            g["output"] += e["output_tokens"]

    def cost(g):
        if not priced:
            return "price not configured"
        usd = g["input"] / 1e6 * float(price_in) + g["output"] / 1e6 * float(price_out)
        return f"${usd:.4f} (${usd / n_requests:.6f} per request)"

    lines = ["# Token usage and cost", "",
             f"Final full-dataset run `{run_id}` that produced `output.csv` ({n_requests} requests).", "",
             "Provider: Google Gemini API, called through LiteLLM over the fallback chain "
             f"`{', '.join(models())}` (a model whose free daily quota is used up hands over to the next). "
             "Everything else in the pipeline is deterministic Python and uses no model.", "",
             "Model answers are cached by a hash of their input, so a re-run makes no new API calls. "
             "`Model calls` counts every answer the run used; `API calls` counts the ones billed during "
             "this run; tokens are those of the answers used (a cached answer carries the tokens it cost "
             "when it was made).", ""]
    if not entries:
        lines += ["**No model was called in this run** (no `GEMINI_API_KEY` / `GEMINI_MODEL` configured): "
                  "intent came from the rule classifier and screenshots from the reference transcription "
                  "in `code/image_extractions/`. Model calls: 0, input tokens: 0, output tokens: 0, "
                  "cost: $0.", ""]
    else:
        lines += ["| Model | Task | Model calls | API calls | Input tokens | Output tokens | Total | "
                  "Avg tokens / request | Estimated cost |", "|---|---|---|---|---|---|---|---|---|"]
        for (model, task), g in sorted(groups.items(), key=lambda kv: (kv[0][0] == "all models", kv[0])):
            total = g["input"] + g["output"]
            lines.append(f"| {model} | {task} | {g['calls']} | {g['api_calls']} | {g['input']:,} | "
                         f"{g['output']:,} | {total:,} | {total / n_requests:,.1f} | {cost(g)} |")
        lines += ["", "Output tokens include the model's thinking tokens where it reports them.", ""]
        if not priced:
            lines += ["Cost: set `GEMINI_PRICE_INPUT_PER_MTOK` / `GEMINI_PRICE_OUTPUT_PER_MTOK` and re-run "
                      "to fill in the estimate.", ""]
    if failures:
        why = defaultdict(int)
        for e in failures:
            why[(e["task"], e["failed"])] += 1
        lines += ["## Calls the API refused", "",
                  "Refused calls return no answer and use no tokens; the pipeline fell back as described "
                  "(intent: the rule classifier decides; vision: the last complete extraction is kept).", "",
                  "| Task | Error | Calls |", "|---|---|---|"]
        lines += [f"| {task} | {err} | {n} |" for (task, err), n in sorted(why.items())]
        lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return f"usage report: {REPORT} ({len(entries)} model calls in {run_id})"
