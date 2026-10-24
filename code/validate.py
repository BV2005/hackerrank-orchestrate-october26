"""Hard checks on the answer before output.csv is written."""
from steps import OPERATION_ORDER, PHASE_ORDER, STATE

PHASE_OF = {op: ("FINDING" if i < 4 else "REMEDIATION" if i < 9 else "VERIFICATION")
            for i, op in enumerate(OPERATION_ORDER)}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate(answers, requests, captured_at):
    """Raise ValueError listing every violation; return warnings.

    answers: case_id -> (status, steps). A deletion case with no linked resource at all
    comes out COMPLETE with no steps: allowed, but returned as a warning.
    """
    errors, warnings = [], []
    if set(answers) != {r["case_id"] for r in requests}:
        errors.append("answer set differs from the request set")
    audit = {r["case_id"]: r["audit_at"] for r in requests}
    for case_id, (status, steps) in answers.items():
        where = f"{case_id}:"
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errors.append(f"{where} bad status {status}")
        if status == "NOT_APPLICABLE" and steps:
            errors.append(f"{where} NOT_APPLICABLE must have exactly [] steps")
        if status != "NOT_APPLICABLE" and not steps:
            warnings.append(f"{where} deletion case with no linked resource")
        phases = [s["phase"] for s in steps]
        if status == "COMPLETE" and set(phases) - {"VERIFICATION"}:
            errors.append(f"{where} COMPLETE may only verify")
        if status == "INCOMPLETE" and "FINDING" not in phases:
            errors.append(f"{where} INCOMPLETE without a finding")
        keys = [(PHASE_ORDER.index(s["phase"]), OPERATION_ORDER.index(s["operation"]),
                 s["system_id"], s["resource_id"]) for s in steps]
        if keys != sorted(keys):
            errors.append(f"{where} steps out of order")
        for n, s in enumerate(steps, 1):
            if set(s) != FIELDS or s["step_no"] != n:
                errors.append(f"{where} step {n} malformed or misnumbered")
            elif PHASE_OF[s["operation"]] != s["phase"] or STATE[s["operation"]] != s["state"]:
                errors.append(f"{where} step {n} operation/phase/state mismatch")
            future = s["phase"] == "REMEDIATION" or (s["phase"] == "VERIFICATION" and status == "INCOMPLETE")
            if future and s["evidence_id"]:
                errors.append(f"{where} step {n} future work must not cite evidence")
            if not future and not s["evidence_id"]:
                errors.append(f"{where} step {n} needs an evidence id")
            cap = captured_at.get(s["evidence_id"])
            if cap and cap > audit[case_id]:
                errors.append(f"{where} step {n} cites {s['evidence_id']} captured after audit_at")
    if errors:
        raise ValueError(f"{len(errors)} validation errors:\n" + "\n".join(errors[:40]))
    return warnings
