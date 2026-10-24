"""Turn a case's resource rows into the ordered steps_json and a deletion status.

Each row has a category (-> required action from deletion_rules.csv) and the state
observed at audit_at. A row whose state already satisfies the policy yields only a
verification step; otherwise it yields a finding, a remediation and the verification
that must pass afterwards.
"""
FINDING, REMEDIATION, VERIFICATION = "FINDING", "REMEDIATION", "VERIFICATION"
OPERATION_ORDER = [  # problem statement table order; steps sort by this within a phase
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE_ORDER = [FINDING, REMEDIATION, VERIFICATION]
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
PRESENT = {"PRESENT_WITH_PII", "PRESENT_NO_PII"}


def plan_row(row):
    """[(phase, operation)] for one resource, from its policy action and observed state."""
    action, state, category = row["required_action"], row["observed_state"], row["category"]
    if category == "DATA_FLOW":
        if state == "ACTIVE" or state in PRESENT:
            return [(FINDING, "REPORT_ACTIVE_DATA_FLOW"), (REMEDIATION, "DISABLE_DATA_FLOW"),
                    (VERIFICATION, "VERIFY_DATA_FLOW_DISABLED")]
        return [(VERIFICATION, "VERIFY_DATA_FLOW_DISABLED")]
    if category == "BACKUP_ARCHIVE":  # retained, but restore must be blocked
        if state == "ACTIVE" or state in PRESENT:  # present with no restore block shown
            return [(FINDING, "REPORT_RESTORE_RISK"), (REMEDIATION, "BLOCK_RESTORE"),
                    (VERIFICATION, "VERIFY_RESTORE_BLOCKED")]
        return [(VERIFICATION, "VERIFY_RESTORE_BLOCKED")]
    if action == "RETAIN":
        return [(VERIFICATION, "VERIFY_RECORD_PRESENT")]
    if action == "DELETE":
        if state in PRESENT:
            return [(FINDING, "REPORT_REMAINING_RECORD"), (REMEDIATION, "DELETE_RECORD"),
                    (VERIFICATION, "VERIFY_RECORD_ABSENT")]
        return [(VERIFICATION, "VERIFY_RECORD_ABSENT")]
    if action in ("REDACT", "ANONYMIZE"):
        verify = "VERIFY_RECORD_REDACTED" if action == "REDACT" else "VERIFY_RECORD_ANONYMIZED"
        if state == "PRESENT_WITH_PII":
            fix = "REDACT_RECORD" if action == "REDACT" else "ANONYMIZE_RECORD"
            return [(FINDING, "REPORT_EXPOSED_PII"), (REMEDIATION, fix), (VERIFICATION, verify)]
        if state in PRESENT:
            return [(VERIFICATION, verify)]
        return [(VERIFICATION, "VERIFY_RECORD_ABSENT")]  # the whole record is already gone
    return []


def case_steps(rows):
    """(deletion_status, steps) for one deletion case's rows."""
    planned = [(phase, op, row) for row in rows for phase, op in plan_row(row)]
    incomplete = any(phase == FINDING for phase, _, _ in planned)
    if not incomplete:
        # a COMPLETE answer must prove each check with evidence from before audit_at; a
        # resource that only later evidence shows is outside the provable state, so drop it
        planned = [p for p in planned if p[2]["observed_evidence_id"] or p[2]["claim_activity_id"]]
    planned.sort(key=lambda p: (PHASE_ORDER.index(p[0]), OPERATION_ORDER.index(p[1]),
                                p[2]["system_id"], p[2]["resource_id"]))
    steps = []
    for n, (phase, op, row) in enumerate(planned, 1):
        # findings cite the evidence they rest on; so does every check of a COMPLETE case.
        # remediation and post-remediation checks are future work, so they cite nothing
        cites = phase == FINDING or (phase == VERIFICATION and not incomplete)
        evidence = (row["observed_evidence_id"] or row["claim_activity_id"] or "") if cites else ""
        steps.append({"step_no": n, "phase": phase, "system_id": row["system_id"],
                      "resource_id": row["resource_id"], "operation": op,
                      "state": STATE[op], "evidence_id": evidence})
    return ("INCOMPLETE" if incomplete else "COMPLETE"), steps
