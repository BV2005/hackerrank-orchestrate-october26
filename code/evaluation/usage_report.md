# Token usage and cost

Final full-dataset run `run-20261024T151443Z` that produced `output.csv` (518 requests).

Provider: Google (Gemini API, `google-genai` SDK). Everything else in the pipeline is deterministic Python and uses no model.

Model answers are cached by a hash of their input, so a re-run makes no new API calls. `Model calls` counts every answer the run used; `API calls` counts the ones billed during this run; tokens are those of the answers used (a cached answer carries the tokens it cost when it was made).

**No model was called in this run** (no `GEMINI_API_KEY` / `GEMINI_MODEL` configured): intent came from the rule classifier and screenshots from the reference transcription in `code/image_extractions/`. Model calls: 0, input tokens: 0, output tokens: 0, cost: $0.
