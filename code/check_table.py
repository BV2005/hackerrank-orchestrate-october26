"""Compare artifacts/sample_deletion_requests_table.csv with dataset/sample_output.csv.

Run: python code/build_table.py sample_deletion_requests.csv && python code/check_table.py
"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# current state each sample step implies for its resource at audit_at
FINDING_STATE = {"REPORT_REMAINING_RECORD": {"PRESENT_WITH_PII", "PRESENT_NO_PII"},
                 "REPORT_EXPOSED_PII": {"PRESENT_WITH_PII"},
                 "REPORT_ACTIVE_DATA_FLOW": {"ACTIVE"}, "REPORT_RESTORE_RISK": {"ACTIVE"}}
VERIFY_STATE = {"VERIFY_RECORD_ABSENT": {"ABSENT"},
                "VERIFY_RECORD_PRESENT": {"PRESENT_WITH_PII", "PRESENT_NO_PII"},
                "VERIFY_RECORD_REDACTED": {"PRESENT_NO_PII"},
                "VERIFY_RECORD_ANONYMIZED": {"PRESENT_NO_PII"},
                "VERIFY_DATA_FLOW_DISABLED": {"DISABLED"}, "VERIFY_RESTORE_BLOCKED": {"BLOCKED"}}


def main():
    with open(ROOT / "dataset/sample_output.csv", encoding="utf-8") as f:
        gold = {r["case_id"]: json.loads(r["steps_json"]) for r in csv.DictReader(f)}
    table = defaultdict(dict)
    with open(ROOT / "artifacts/sample_deletion_requests_table.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            table[r["case_id"]][r["resource_id"]] = r

    tally, problems = Counter(), []
    for case, steps in gold.items():
        expected, cited = {}, {}
        for s in steps:
            rid = s["resource_id"]
            if s["phase"] == "FINDING":
                expected[rid] = FINDING_STATE[s["operation"]]
            elif s["phase"] == "VERIFICATION":
                expected.setdefault(rid, VERIFY_STATE[s["operation"]])
            if s["evidence_id"]:
                cited[rid] = s["evidence_id"]
        found = table.get(case, {})
        tally["gold_resources"] += len(expected)
        tally["table_resources"] += len(found)
        for rid, states in expected.items():
            row = found.get(rid)
            if row is None:
                problems.append(f"{case} MISSING {rid}")
                continue
            tally["found"] += 1
            if row["observed_state"] in states:
                tally["state_ok"] += 1
            else:
                problems.append(f"{case} STATE {rid}: table={row['observed_state']} "
                                f"expected={'/'.join(sorted(states))} [{row['observation_history']}]")
            if rid in cited:
                tally["cited"] += 1
                if row["observed_evidence_id"] == cited[rid]:
                    tally["evidence_ok"] += 1
                else:
                    problems.append(f"{case} EVIDENCE {rid}: table={row['observed_evidence_id']} gold={cited[rid]}")
        tally["extra"] += len(set(found) - set(expected))
        for rid in sorted(set(found) - set(expected)):
            problems.append(f"{case} EXTRA {rid} ({found[rid]['observed_state']}, {found[rid]['link_via']})")

    g = tally["gold_resources"]
    print(f"resource recall     {tally['found']}/{g} = {tally['found'] / g:.1%}")
    print(f"resource precision  {tally['found']}/{tally['table_resources']} = "
          f"{tally['found'] / max(tally['table_resources'], 1):.1%}")
    print(f"state agreement     {tally['state_ok']}/{tally['found']}")
    print(f"evidence agreement  {tally['evidence_ok']}/{tally['cited']}")
    print("\n".join(problems))


if __name__ == "__main__":
    main()
