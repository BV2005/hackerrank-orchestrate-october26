# Token usage and cost

Final full-dataset run `run-20261024T175614Z` that produced `output.csv` (518 requests).

Provider: Google Gemini API, called through LiteLLM over the fallback chain `gemini/gemini-3.5-flash-lite, gemini/gemini-3.1-flash-lite, gemini/gemini-3.8-flash, gemini/gemini-3.7-flash, gemini/gemini-3.6-flash, gemini/gemini-3.5-flash` (a model whose free daily quota is used up hands over to the next). Everything else in the pipeline is deterministic Python and uses no model.

Model answers are cached by a hash of their input, so a re-run makes no new API calls. `Model calls` counts every answer the run used; `API calls` counts the ones billed during this run; tokens are those of the answers used (a cached answer carries the tokens it cost when it was made).

| Model | Task | Model calls | API calls | Input tokens | Output tokens | Total | Avg tokens / request | Estimated cost |
|---|---|---|---|---|---|---|---|---|
| gemini/gemini-3.5-flash | all tasks | 1 | 0 | 2,872 | 2,631 | 5,503 | 10.6 | price not configured |
| gemini/gemini-3.5-flash | intent | 1 | 0 | 2,872 | 2,631 | 5,503 | 10.6 | price not configured |
| gemini/gemini-3.6-flash | all tasks | 9 | 0 | 23,725 | 30,116 | 53,841 | 103.9 | price not configured |
| gemini/gemini-3.6-flash | intent | 9 | 0 | 23,725 | 30,116 | 53,841 | 103.9 | price not configured |
| gemini/gemini-3.8-flash | all tasks | 12 | 0 | 18,275 | 26,243 | 44,518 | 85.9 | price not configured |
| gemini/gemini-3.8-flash | intent | 1 | 0 | 2,838 | 3,065 | 5,903 | 11.4 | price not configured |
| gemini/gemini-3.8-flash | vision | 11 | 0 | 15,437 | 23,178 | 38,615 | 74.5 | price not configured |
| all models | all tasks | 22 | 0 | 44,872 | 58,990 | 103,862 | 200.5 | price not configured |

Output tokens include the model's thinking tokens where it reports them.

Cost: set `GEMINI_PRICE_INPUT_PER_MTOK` / `GEMINI_PRICE_OUTPUT_PER_MTOK` and re-run to fill in the estimate.
