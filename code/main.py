"""Ghost Data Still Exists - deletion audit pipeline.

Run from the repository root:  python code/main.py [requests_csv] [output_csv]
Defaults: dataset/deletion_requests.csv -> output.csv (repository root).
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from datetime import datetime, timezone

import vision
from build_table import DATASET, ROOT, build_rows, read_csv
from intent import decide
from llm import configured
from steps import case_steps
from usage_report import write_usage_report
from validate import validate


def case_number(case_id):
    return int(case_id.rsplit("_", 1)[1])


def audit(requests, run_id=None):
    """case_id -> (deletion_status, steps, intent) for the given request dicts.

    intent is (decision, reason, source): rules, the model for requests the rules cannot
    place, or a deletion default when no model is configured.
    """
    intents = decide(requests, run_id)
    deletion = [r for r in requests if intents[r["case_id"]][0] != "not_deletion"]
    rows, _ = build_rows(deletion)
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row)
    answers = {}
    for r in requests:
        cid = r["case_id"]
        if intents[cid][0] == "not_deletion":
            answers[cid] = ("NOT_APPLICABLE", [], intents[cid])
        else:
            status, steps = case_steps(by_case.get(cid, []))
            answers[cid] = (status, steps, intents[cid])
    return answers


def write_output(answers, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(answers, key=case_number):
            status, steps, _ = answers[cid]
            w.writerow([cid, status, json.dumps(steps, separators=(",", ":"))])


def main(requests_file="deletion_requests.csv", output_file=None):
    requests = read_csv(requests_file)
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    if configured():
        vision.extract(run_id)  # cached per image: free after the first run
    answers = audit(requests, run_id)
    captured = {r["file_id"]: r["captured_at"] for r in read_csv("evidence_files.csv")}
    warnings = validate({c: a[:2] for c, a in answers.items()}, requests, captured)
    for w in warnings:
        print("warning:", w)
    out = Path(output_file) if output_file else ROOT / "output.csv"
    write_output(answers, out)
    counts = defaultdict(int)
    for status, _, _ in answers.values():
        counts[status] += 1
    print(f"wrote {len(answers)} rows to {out}: {dict(counts)}")
    if requests_file == "deletion_requests.csv" and output_file is None:
        print(write_usage_report(run_id, len(requests)))


if __name__ == "__main__":
    main(*sys.argv[1:])
