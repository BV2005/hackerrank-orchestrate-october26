## Failures found and improvements made

Measured with `code/check_table.py` (resource-level, sample cases) while building the case x resource table, then with this report once steps were generated. Numbers are resource recall / precision on the 32 samples.

| # | Failure found | Change | Effect |
|---|---|---|---|
| 1 | Matching requests to activity by time alone: 137-565 activity rows per request window, nearest-time pick right in 14/32 | Join evidence on identifiers (current + historical emails, phones, hashes, user_id); time only as a tie-break | Basis of the pipeline |
| 2 | Scoped snapshot dumps treated as full dumps: their omissions marked live records ABSENT (e.g. the ghost `account_005` in a failover replica) | Explicit observations beat absence inferred from omission; a surviving replica copy beats a clean primary | 87.0% -> 96.6% precision; evidence agreement 54/61 -> 61/61 |
| 3 | PII-scan hits (`matched_value`) ignored; backup log fields (`eligible`, `deletion_block`) unread | Treat scan matches as exposed PII; map restore evaluation to ACTIVE / BLOCKED | State agreement 74 -> 83 of 84 |
| 4 | Ownership limited to evidence captured before audit_at dropped links only later files show (e.g. `audit_record_4902`) | Ownership may come from any evidence; observed state stays limited to evidence by audit_at | Recall 75.7% -> 82.0% |
| 5 | Retained records (security audit) omitted from scoped dumps reported as deleted | Omission never proves a retained record was deleted | 5 false ABSENT states removed |
| 6 | Deleted accounts keep no identifier: 219 test cases with 2-6 candidate account deletes in the run window | Elimination across all requests, then a time-gap guess (validated at 48% vs ~33% chance on evidence-linked cases), flagged `time_guess` | Recall 82.0% -> 88.3%, precision 96.8% -> 94.2% |
| 7 | Anonymized analytics keep no identifier | Link through the run's ANONYMIZE_RECORD event using the 390/449 s step rhythm, single-demand elimination | Recall 88.3% -> 91.9% |
| 8 | Storage objects (`object_19`) reported instead of the support record holding the attachment | Drop `object_` ids | One false resource removed |
| 9 | 11 screenshots unread; for about a dozen test cases they are the latest evidence and show records still live (e.g. `account_5762` for case_34) | Screenshot facts transcribed into `code/image_extractions/` and loaded as evidence | Test-case states corrected; sample state agreement 93 -> 96 |
| 10 | Intent rules misread "remove any remaining copy of my profile" as an export request, and missed "confirm that the profile is gone" | Narrowed the export pattern; added deletion-confirmation wording | 32/32 sample requests classified as deletion; 10 test requests NOT_APPLICABLE |
| 11 | A COMPLETE case cited no evidence for resources that only later evidence shows (case_46) | Such checks are left out of COMPLETE answers; the validator enforces citations | Output passes validation |
| 12 | First step-level run: status 29/32. case_07 / case_10 support records redacted as messages, but the answers delete them (an identity-document attachment and a replica contact lookup, no conversation) | A support record is a message (rule 05, REDACT) only when evidence shows conversation content (conversation collection, message or requester fields, ticket reads); otherwise it belongs to the subject alone (rule 06, DELETE) | Status 29/32 -> 31/32 |
| 13 | case_12 `analytics_9914` anonymized, but the answer deletes it: it is a contact lookup keyed by the hash of the subject's superseded email | Ownership links are tagged current vs historical; a redact/anonymize record linked only through identifiers the subject no longer holds is a secondary identifier (rule 02, DELETE) | Status 32/32; step precision 93.4% -> 97.4%, recall 88.6% -> 95.3%; operation/state/evidence field accuracy 100% |

| 14 | Gemini (free tier) hit 503 overloads, then its 20-requests/day quota; sequential calls made one slow screenshot block the rest, and a quota error stopped the whole audit | Independent calls run concurrently (4 at a time); 503 / short 429 back off, a long quota wait fails fast; a failed model call degrades to the rules (intent) or keeps the last complete extraction (vision) | A quota-exhausted run finishes in seconds with valid output |
| 15 | First Gemini-vs-transcription comparison reported identical outputs, but `main.py` had re-created the model extraction before the "reference" run, so both runs used the model | The evaluation now re-audits the test set with the reference itself and counts changed answers | Real difference: 2 answers (case_34 cites the newer screenshot for `support_9538`; case_46 drops the tombstone check, because the model read the user_17 screen as a bare FOUND, which proves existence but not redaction) |

| 16 | One free-tier model (20 requests/day) cannot cover the run: 10 of 11 intent batches went unanswered | Model calls go through LiteLLM over a fallback chain of Gemini models, each with its own daily quota; a model out of quota (or rejecting the request) hands over to the next, short overloads retry in place | Live run: gemini-3.8-flash and 3.7-flash refused (quota), 3.6-flash answered 9 batches, 3.5-flash took the one 3.6 dropped (503); model opinion for 518/518 test requests, agreeing with the rules on all 518 |

Decisions backed by the sample answers:
- The legal-hold register PDF is not parsed: its six rows all name sample-case users whose answers contain no legal hold.
- Name matching was tested as an extra identifier and dropped: it added no correct link.

Known limits:
- Deleted-account guesses are right about half the time; a wrong guess changes one verification step, never the case status (every candidate account is deleted).
- `case_04` has no linkable resource: its account and analytics record carry no identifier and appear in no run window.
