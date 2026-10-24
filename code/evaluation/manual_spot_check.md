## Manual spot check of test-set answers

The 518 test cases have no public answers, so 16 of them were audited by hand. Cases were
picked with a fixed seed from each risky group: non-deletion requests, screenshot-anchored
cases, deleted accounts placed by timing, requests made with an old email, and random
COMPLETE / INCOMPLETE cases. For each case an independent script (not the pipeline's parser
or linking) printed the request, the subject's identifiers from `user_identities.csv` and
`user_audit_log.csv`, every raw evidence snippet mentioning one of them or a resource in the
answer (marked before / after `audit_at`), and the activity rows; the verdict comes from
reading that evidence.

| Case | Pipeline | Key evidence (captured before audit_at unless noted) | Verdict |
|---|---|---|---|
| case_538 | NOT_APPLICABLE | "Please remove me from marketing lists only" | correct |
| case_541 | NOT_APPLICABLE | "Please change the email address used for this account" | correct |
| case_34 | INCOMPLETE | workflow claimed COMPLETE; 09-15 log: account 404; 09-26 screenshot (audit 09-27): live profile with name, email, phone; `support_9538` attachment scan, and the workflow queued it as DELETE | correct (ghost account) |
| case_39 | INCOMPLETE | 08-07 screenshot (audit 08-08): `data_flow_1121` continuous sync; activity later shows it re-creating `search_4801` | correct |
| case_55 | INCOMPLETE | 04-29 screenshot (audit 04-30): `backup_9287` with no restore block; a later restore re-created `search_4882` | correct |
| case_63 | INCOMPLETE | 09-16 screenshot: `marketing_001` live (catalog links it to the subject) | status correct; no account step - no evidence names an account and no deletion run exists |
| case_278 | INCOMPLETE | 06-03 capture, 25 min before audit: search hit, identifiable analytics, connector enabled; run log shows those deletions failing | correct; account placed by timing, corroborated by the subject's own connector check 111 min after it |
| case_183 | COMPLETE | retained audit record; account placed by timing, absent only by omission | plausible; account id unverifiable |
| case_326 | COMPLETE | 05-09 trace: account 404, connector disabled; run logged anonymize / redact / disable / block as completed | correct |
| case_43 | INCOMPLETE | workflow logged delete SUCCESS + replication checkpoint; on audit day the account lookup returns the profile (`rows=1`, 200) | correct (ghost account) |
| case_137 | COMPLETE | 08-12 handover at audit time: account NOT_FOUND, audit record retained | correct |
| case_167 | COMPLETE | 07-28 02:53: account 404, audit record retained; a file captured after audit_at (07-30) shows the account back, correctly ignored | correct |
| case_491 | COMPLETE | account 404 and audit record 21 min before audit; `search_8910` absent only by omission from a 02-13 dump (first seen 02-20, after audit) | plausible; search check weakly evidenced |
| case_177 | INCOMPLETE | 07-23 inventory 17 min before audit: account (DELETION_PENDING, email) and search document with email still present | correct |
| case_417 | INCOMPLETE | 03-25 syslog 1 min before audit: search hit and account lookup returning the profile | correct |
| case_140 | INCOMPLETE | 08-12 export at audit time: account NOT_FOUND; analytics IDENTIFIABLE, connector ACTIVE, search and support present | correct |

Result: deletion status judged correct in 16/16; steps judged fully correct in 13/16; the other
three carry the known limits (an account no evidence names, an account placed by timing, a
check resting on omission). This is an independent reading of the evidence, not the hidden
answer key.
