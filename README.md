# Ghost Data Still Exists — deletion audit agent

HackerRank Orchestrate, October 2026. For each of the 518 deletion requests the system decides
whether the deletion is `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE`, and writes the ordered
findings, remediation and verification steps to `output.csv`.

## How it works

```
request ─► 1 intent ─► 2 identity ─► 3 evidence ─► 4 resource linking ─► 5 state at audit_at
                                                                                 │
                         output.csv ◄─ 8 validate ◄─ 7 steps + status ◄─ 6 policy rule
```

| Stage | Module | What it does |
|---|---|---|
| 1 Intent | `code/intent.py` | Rules (non-deletion intents and "keep the account" first, then deletion terms in 6+ languages), plus a model second opinion through LiteLLM. Non-deletion requests become `NOT_APPLICABLE` with `[]`. |
| 2 Identity | `code/identity.py` | The request email resolves to a `user_id` and every email, phone and hash the person ever held (`user_identities.csv` + `user_audit_log.csv`). |
| 3 Evidence | `code/evidence.py`, `code/vision.py` | Every evidence file becomes records: SQL tables and replicas, JSON exports and snapshots, logs grouped by trace, the handover file, PDFs, and the 11 screenshots read by a vision model. |
| 4 Linking | `code/build_table.py` | Resources are tied to the person through identifiers, catalog link rows and references between records; deleted accounts and anonymized analytics, which keep no identifier, through the timing of the deletion run. |
| 5 State | `code/build_table.py` | The latest evidence captured at or before `audit_at` decides each record's state. A surviving replica copy beats a clean primary; workflow statuses and activity results are claims, never proof. |
| 6–7 Policy and steps | `code/steps.py` | `deletion_rules.csv` gives the required treatment; a record that breaks it yields a finding, a remediation and a verification; a compliant one yields an evidence-backed verification. Any finding makes the case `INCOMPLETE`. Steps are ordered by phase, operation, `system_id`, `resource_id`. |
| 8 Validate | `code/validate.py` | Hard checks before writing: row set and order, numbering, operation/phase/state pairs, citations, no evidence captured after `audit_at`. |

Every scored field is produced by deterministic Python. Models are used where code cannot do
the job (reading screenshots, multilingual intent, explaining cases), and their output is
validated before it can affect an answer.

### AI agent (Google ADK)

`code/audit_agent/` is a Google ADK 2.13 application:

- **`audit_coordinator`** (the `root_agent`) answers questions about any case, citing evidence
  ids, using read-only tools: `list_cases`, `get_case`, `get_evidence`, `get_activity`,
  `list_account_candidates`, `explain_rules`. Every evidence tool applies the case's
  `audit_at` cutoff itself.
- **`case_reviewer`** investigates a case whose deleted account was placed by timing and
  returns a schema-enforced `ReviewDecision`. `code/review.py` accepts a decision only if it
  names a listed candidate not already placed in another case. Measured on the flagged sample
  cases it did not beat the timing rule (7/10 after the guard), so its decisions are not
  applied to `output.csv`.
- Callbacks cache identical model requests and log token usage; models are reached through
  LiteLLM with the same fallback chain as the pipeline.

## Setup

Python 3.11+ (developed on 3.13, Windows).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r code/requirements.txt     # Windows
# .venv/bin/python -m pip install -r code/requirements.txt       # macOS / Linux
```

Model access (optional — see "Reproducing without a key"). Put settings in a `.env` file at
the repository root (never committed) or in the environment:

```bash
GEMINI_API_KEY=...          # Google AI Studio key (on Windows a saved user variable also works)
LLM_MODELS=gemini/gemini-3.5-flash-lite,gemini/gemini-3.1-flash-lite,gemini/gemini-3.8-flash,gemini/gemini-3.7-flash,gemini/gemini-3.6-flash,gemini/gemini-3.5-flash
# optional, for cost estimates in usage_report.md (USD per million tokens)
GEMINI_PRICE_INPUT_PER_MTOK=...
GEMINI_PRICE_OUTPUT_PER_MTOK=...
```

`LLM_MODELS` is a LiteLLM fallback chain: on the free tier each model has its own daily
request quota, so a model that is out of quota hands over to the next one.

## Run

From the repository root:

```bash
.venv/Scripts/python code/main.py                    # writes output.csv and code/evaluation/usage_report.md
.venv/Scripts/python -X utf8 code/evaluation/main.py # writes code/evaluation/evaluation_report.md
.venv/Scripts/python code/vision.py                  # re-read the screenshots with the model
.venv/Scripts/python -X utf8 code/ask_agent.py "Why is case_34 INCOMPLETE?"
.venv/Scripts/python -X utf8 code/review.py samples 10   # reviewer agent on flagged sample cases
adk web code                                         # chat with the coordinator in the ADK web UI
```

### Reproducing without a key

Every model answer used for the submitted `output.csv` is cached in `code/llm_cache/` (keyed
by a hash of the request), so `python code/main.py` reproduces `output.csv` byte for byte with
no API key and no network. Without the cache and without a key, intent falls back to the rules
and screenshots to `code/image_extractions/reference_transcription.json`.

## Results

Full details in `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.

| On the 32 public sample cases | Result |
|---|---|
| Deletion status accuracy | 32/32 |
| Step-level precision / recall | 97.4% / 95.3% |
| Operation, state and evidence id on steps for the right resource | 100% each |
| Additional evaluation cases (created for the project) | 5/5 |

The samples were also used during development, so they are not a held-out score. On the 518
test cases (no public answers): an independent checker finds 0 format or logic errors and all
721 findings cite a file that mentions the resource; a hand audit of 22 seeded test cases agrees
with the deletion status in 22/22 and with the full step list in 19/22.

Known limits: about 219 test cases have a deleted account that no evidence names; it is placed
by the timing of the deletion run, which is right roughly half the time. A wrong pick changes
one verification step, never the case status.

## Layout

```
code/
  main.py              end-to-end run -> output.csv
  intent.py identity.py evidence.py build_table.py steps.py validate.py
  llm.py vision.py usage_report.py    model layer (LiteLLM), screenshot extraction, usage report
  audit_agent/         Google ADK agents, tools, callbacks, runner
  ask_agent.py review.py
  image_extractions/   screenshot records (model output + reference transcription)
  llm_cache/           cached model answers and the token usage log
  evaluation/          main.py, evaluation_report.md, usage_report.md, findings, spot check
  requirements.txt
```

No API keys, credentials or real personal data are included; all people and records in the
dataset are fictional.
