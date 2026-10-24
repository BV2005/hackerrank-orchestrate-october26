"""Batch review of flagged cases by the case_reviewer agent, validated before use.

Run from the repository root:
  python code/review.py samples [N]   review up to N flagged sample cases and score them
                                      against sample_output.csv
  python code/review.py test [N]      review up to N flagged test cases -> code/audit_agent/reviews.json

A decision is accepted only if it names one of the case's listed candidates. Accepted
'choose_other' decisions are applied by `python code/main.py --review`.
"""
import json
import sys
from pathlib import Path

from audit_agent import tools
from audit_agent.agent import case_reviewer
from audit_agent.runner import run_agent

REVIEWS = Path(__file__).resolve().parent / "audit_agent" / "reviews.json"


def review(case_id):
    """(accepted decision or None, raw answer, tool calls) for one flagged case."""
    candidates = tools.list_account_candidates(case_id)
    # an account the pipeline already places in another case is not free to take
    rows = tools._data()[2]
    taken = {r["resource_id"] for c, rs in rows.items() if c != case_id for r in rs
             if r["resource_id"].startswith("account_")}
    allowed = {c["resource_id"] for c in candidates.get("candidates", [])} - (taken - {candidates.get("pipeline_pick")})
    text, calls = run_agent(case_reviewer, f"Review {case_id}.")
    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        return None, text, calls
    ok = (decision.get("case_id") == case_id and decision.get("decision") in
          ("confirm_pipeline", "choose_other", "undetermined")
          and (decision.get("decision") == "undetermined" or decision.get("account_resource_id") in allowed))
    return (decision if ok else None), text, calls


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "samples"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    flagged = [c for c in tools.list_cases(flagged_only=True)["case_ids"]
               if (int(c.rsplit("_", 1)[1]) <= 32) == (which == "samples")][:limit]
    gold = {}
    if which == "samples":
        import csv
        with open(Path(__file__).resolve().parents[1] / "dataset" / "sample_output.csv", encoding="utf-8") as f:
            gold = {r["case_id"]: {s["resource_id"] for s in json.loads(r["steps_json"])
                                   if s["resource_id"].startswith("account_")} for r in csv.DictReader(f)}
    results = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else {}
    score = {"reviewed": 0, "accepted": 0, "pipeline_right": 0, "reviewer_right": 0}
    for cid in flagged:
        pick = tools.list_account_candidates(cid)["pipeline_pick"]
        decision, raw, calls = review(cid)
        score["reviewed"] += 1
        chosen = pick if decision and decision["decision"] == "confirm_pipeline" else (
            decision or {}).get("account_resource_id")
        line = f"{cid}: pipeline={pick} reviewer={decision['decision'] + ':' + str(chosen) if decision else 'REJECTED'}"
        if decision:
            score["accepted"] += 1
            results[cid] = decision
        if cid in gold:
            score["pipeline_right"] += pick in gold[cid]
            score["reviewer_right"] += bool(chosen) and chosen in gold[cid]
            line += f" gold={sorted(gold[cid])}"
        print(line, "| tools:", len(calls))
        if not decision:
            print("   raw answer:", raw[:300])
    REVIEWS.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(score)


if __name__ == "__main__":
    main()
