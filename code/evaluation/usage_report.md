# Token usage and cost

Final full-dataset run `run-20261024T155821Z` that produced `output.csv` (518 requests).

Provider: Google (Gemini API, `google-genai` SDK). Everything else in the pipeline is deterministic Python and uses no model.

Model answers are cached by a hash of their input, so a re-run makes no new API calls. `Model calls` counts every answer the run used; `API calls` counts the ones billed during this run; tokens are those of the answers used (a cached answer carries the tokens it cost when it was made).

| Model | Task | Model calls | API calls | Input tokens | Output tokens | Total | Avg tokens / request | Estimated cost |
|---|---|---|---|---|---|---|---|---|
| gemini-3.8-flash | all tasks | 12 | 0 | 18,275 | 26,243 | 44,518 | 85.9 | price not configured |
| gemini-3.8-flash | intent | 1 | 0 | 2,838 | 3,065 | 5,903 | 11.4 | price not configured |
| gemini-3.8-flash | vision | 11 | 0 | 15,437 | 23,178 | 38,615 | 74.5 | price not configured |
| all models | all tasks | 12 | 0 | 18,275 | 26,243 | 44,518 | 85.9 | price not configured |

Output tokens include the model's thinking tokens where it reports them.

Cost: set `GEMINI_PRICE_INPUT_PER_MTOK` / `GEMINI_PRICE_OUTPUT_PER_MTOK` and re-run to fill in the estimate.

## Calls the API refused

Refused calls return no answer and use no tokens; the pipeline fell back as described (intent: the rule classifier decides; vision: the last complete extraction is kept).

| Task | Error | Calls |
|---|---|---|
| intent | 429 RESOURCE_EXHAUSTED | 10 |
