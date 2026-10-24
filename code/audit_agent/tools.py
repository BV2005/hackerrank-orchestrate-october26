"""Read-only tools the audit agents call. Plain functions: ADK turns each signature and
docstring into a tool schema.

Every evidence tool takes the case_id and applies that case's audit_at cutoff itself, so
an agent cannot look at evidence captured after the audit time even by mistake. Nothing
here writes: the answer in output.csv is only ever produced by the deterministic pipeline.
"""
import json
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_table import DATASET, REQUEST_FILES, RULES, build_rows, read_csv  # noqa: E402
from evidence import load_evidence  # noqa: E402
from main import audit  # noqa: E402

MAX_ITEMS = 12  # keep tool results small: every token is sent back to the model


@lru_cache(maxsize=None)
def _data():
    """Load requests, answers, table rows, evidence and activity once per process."""
    requests, answers, rows = {}, {}, defaultdict(list)
    for name in REQUEST_FILES:  # audited per file, exactly as main.py does, so caches match
        reqs = read_csv(name)
        requests.update({r["case_id"]: r for r in reqs})
        answers.update(audit(reqs))
        table, _ = build_rows(reqs)
        for row in table:
            rows[row["case_id"]].append(row)
    records, _, _ = load_evidence(DATASET)
    by_resource = defaultdict(list)
    for rec in records:
        by_resource[rec["resource_id"]].append(rec)
    activity = defaultdict(list)
    for a in read_csv("system_activity.csv"):
        activity[a["resource_id"]].append(a)
    return requests, answers, rows, by_resource, activity


def _case(case_id):
    requests = _data()[0]
    if case_id not in requests:
        raise ValueError(f"unknown case_id {case_id!r}; use list_cases to see valid ids")
    return requests[case_id]


def list_cases(status: str = "", flagged_only: bool = False) -> dict:
    """List audited cases.

    Args:
        status: optional filter: COMPLETE, INCOMPLETE or NOT_APPLICABLE.
        flagged_only: if true, only cases whose deleted account was placed by timing
            rather than by evidence (the ones worth reviewing).
    Returns: counts and up to 50 matching case ids.
    """
    _, answers, rows, _, _ = _data()
    ids = [c for c, a in answers.items() if not status or a[0] == status.upper()]
    if flagged_only:
        ids = [c for c in ids if any("time_guess" in r["link_via"] for r in rows[c])]
    ids.sort(key=lambda c: int(c.rsplit("_", 1)[1]))
    return {"matching": len(ids), "case_ids": ids[:50]}


def get_case(case_id: str) -> dict:
    """Everything the pipeline concluded for one case: the request, the intent decision,
    the deletion status, the steps, and one line per linked resource (category, required
    action, observed state at audit_at, the evidence that showed it, how it was linked).

    Args:
        case_id: e.g. "case_34".
    """
    req = _case(case_id)
    status, steps, intent = _data()[1][case_id]
    resources = [{
        "resource_id": r["resource_id"], "category": r["category"], "required_action": r["required_action"],
        "observed_state": r["observed_state"], "evidence_id": r["observed_evidence_id"],
        "linked_by": r["link_via"][:160],
        **({"other_account_candidates": r["unresolved_account_candidates"]} if r["unresolved_account_candidates"] else {}),
    } for r in _data()[2][case_id]]
    return {"case_id": case_id, "email": req["email"], "requested_at": req["requested_at"],
            "reported_status": req["reported_status"], "reported_at": req["reported_at"],
            "audit_at": req["audit_at"], "request_text": req["request_text"],
            "intent": {"decision": intent[0], "reason": intent[1]},
            "pipeline_status": status, "steps": steps, "resources": resources}


def get_evidence(case_id: str, resource_id: str) -> dict:
    """Evidence records for one resource, limited to captures at or before the case's
    audit_at (latest first). Each record: file_id, captured_at, container, fields.

    Args:
        case_id: the case whose audit time applies, e.g. "case_34".
        resource_id: e.g. "account_5762".
    """
    cutoff = _case(case_id)["audit_at"]
    recs = sorted((r for r in _data()[3].get(resource_id, []) if r["captured_at"] <= cutoff),
                  key=lambda r: r["captured_at"], reverse=True)
    out = [{"file_id": r["file_id"], "captured_at": r["captured_at"], "container": r["container"],
            "fields": {k: v for k, v in r["fields"].items() if v not in (None, "") and not k.startswith("_")}}
           for r in recs[:MAX_ITEMS]]
    hidden = sum(1 for r in _data()[3].get(resource_id, []) if r["captured_at"] > cutoff)
    return {"resource_id": resource_id, "audit_at": cutoff, "records": out,
            "records_after_audit_at_withheld": hidden}


def get_activity(case_id: str, resource_id: str) -> dict:
    """system_activity rows for one resource up to the case's audit_at. These are claims
    made by the workflow (reported_result is not proof of the resulting state).

    Args:
        case_id: the case whose audit time applies.
        resource_id: e.g. "account_5762".
    """
    cutoff = _case(case_id)["audit_at"]
    rows = [{k: a[k] for k in ("activity_id", "event_time", "event_type", "source_system", "target_system",
                                "reported_result", "details")}
            for a in _data()[4].get(resource_id, []) if a["event_time"] <= cutoff]
    return {"resource_id": resource_id, "audit_at": cutoff, "claims": rows[-MAX_ITEMS:]}


def list_account_candidates(case_id: str) -> dict:
    """For a case whose deleted account no evidence names: the account deletions in its
    deletion-run window (40-80 min before reported_at), with their times, the pipeline's
    current pick, and the times of the user's other run events. Deleted accounts keep no
    identifier, so timing and run structure are the only signals.

    Args:
        case_id: e.g. "case_21".
    """
    req = _case(case_id)
    rows = _data()[2][case_id]
    pick = next((r for r in rows if "time_guess" in r["link_via"]), None)
    if pick is None:
        return {"case_id": case_id, "note": "the account is linked by evidence or not ambiguous"}
    candidates = [pick["resource_id"]] + [c for c in pick["unresolved_account_candidates"].split(";")
                                          if c and c != pick["resource_id"]]
    activity = _data()[4]
    deletes = {c: next((a for a in activity.get(c, []) if a["event_type"] == "DELETE_RECORD"), {})
               for c in candidates}
    own_events = sorted(
        (a["event_time"], a["resource_id"], a["event_type"], a["reported_result"])
        for r in rows if not r["resource_id"].startswith("account_")
        for a in activity.get(r["resource_id"], []) if a["source_system"] == "deletion_worker")
    return {"case_id": case_id, "reported_at": req["reported_at"], "pipeline_pick": pick["resource_id"],
            "candidates": [{"resource_id": c, "delete_event_time": deletes[c].get("event_time"),
                            "activity_id": deletes[c].get("activity_id"),
                            "reported_result": deletes[c].get("reported_result")} for c in candidates],
            "user_run_events": own_events[:MAX_ITEMS]}


def explain_rules() -> dict:
    """The deletion policy (deletion_rules.csv) and how steps are ordered in an answer."""
    return {"rules": [{k: r[k] for k in ("data_category", "condition", "required_action")} for r in RULES.values()],
            "ordering": "phases FINDING, REMEDIATION, VERIFICATION; within a phase the operation order of the "
                        "problem statement, then system_id, then resource_id",
            "evidence": "findings and COMPLETE checks cite evidence captured at or before audit_at; remediation "
                        "and post-remediation checks cite nothing"}


def _json(value):  # used by the runner to print tool results compactly
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
