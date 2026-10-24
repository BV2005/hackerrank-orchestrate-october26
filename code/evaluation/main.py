"""Score the pipeline on the public samples and on custom cases; write evaluation_report.md.

Run from the repository root:  python code/evaluation/main.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from build_table import Identities, DATASET, read_csv  # noqa: E402
from main import audit, case_number  # noqa: E402

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def step_key(s):
    return tuple(s[f] for f in FIELDS)


def score(pred, gold):
    """pred/gold: case_id -> (status, steps). Returns a dict of metrics and per-case rows."""
    m = Counter()
    per_case = []
    for cid, (gstatus, gsteps) in gold.items():
        pstatus, psteps = pred[cid][:2]
        m["cases"] += 1
        m["status_ok"] += pstatus == gstatus
        # step level: multiset match on every field except step_no
        g, p = Counter(map(step_key, gsteps)), Counter(map(step_key, psteps))
        hit = sum((g & p).values())
        m["gold_steps"] += sum(g.values())
        m["pred_steps"] += sum(p.values())
        m["step_hits"] += hit
        m["exact_case"] += [step_key(s) for s in psteps] == [step_key(s) for s in gsteps]
        # field level: align steps on (phase, system_id, resource_id), compare the rest
        galign = {(s["phase"], s["system_id"], s["resource_id"]): s for s in gsteps}
        palign = {(s["phase"], s["system_id"], s["resource_id"]): s for s in psteps}
        for k in galign.keys() & palign.keys():
            m["aligned"] += 1
            for f in ("operation", "state", "evidence_id"):
                m[f"field_{f}"] += galign[k][f] == palign[k][f]
        m["gold_resources"] += len({s["resource_id"] for s in gsteps})
        m["resource_hits"] += len({s["resource_id"] for s in gsteps} & {s["resource_id"] for s in psteps})
        m["pred_resources"] += len({s["resource_id"] for s in psteps})
        # sequence: same ordered (phase, operation, system, resource) skeleton
        skel = lambda steps: [(s["phase"], s["operation"], s["system_id"], s["resource_id"]) for s in steps]
        m["sequence_ok"] += skel(psteps) == skel(gsteps)
        shared = [x for x in skel(psteps) if x in set(skel(gsteps))]
        m["order_ok"] += shared == [x for x in skel(gsteps) if x in set(shared)]
        # strict field level: step i against step i, every field including step_no
        for i in range(max(len(gsteps), len(psteps))):
            m["positions"] += 1
            for f in ("step_no",) + FIELDS:
                gv = gsteps[i][f] if i < len(gsteps) else None
                pv = psteps[i][f] if i < len(psteps) else None
                m[f"pos_{f}"] += gv is not None and gv == pv
        m[f"confusion:{gstatus}->{pstatus}"] += 1
        gres = {(s["system_id"], s["resource_id"]) for s in gsteps}
        pres = {(s["system_id"], s["resource_id"]) for s in psteps}
        per_case.append((cid, gstatus, pstatus, len(gsteps), len(psteps), hit,
                         sorted(r for _, r in gres - pres), sorted(r for _, r in pres - gres)))
    return m, per_case


def identity_resolution(requests):
    """How request emails resolve: current vs historical identifier, unresolved."""
    ids = Identities(DATASET)
    kinds = Counter(ids.resolve_email(r["email"])[1] or "unresolved" for r in requests)
    return kinds


def pct(a, b):
    return f"{a}/{b} = {a / b:.1%}" if b else "n/a"


def custom_cases(samples, gold):
    """(name, request, expected (status, steps), what it tests). Built from sample cases."""
    ids = Identities(DATASET)
    by_id = {r["case_id"]: r for r in samples}
    cur10 = ids.users[ids.resolve_email(by_id["case_10"]["email"])[0]]["email"]
    cases = [
        ("marketing opt-out from a known user", dict(by_id["case_11"], request_text=(
            "Please remove me from marketing lists only. I still want receipts for this account.")),
         ("NOT_APPLICABLE", []), "'remove' used for a non-deletion right"),
        ("keep-the-account instruction", dict(by_id["case_24"], request_text=(
            "I only need a copy of my invoices. Do not close the account.")),
         ("NOT_APPLICABLE", []), "negated deletion wording"),
        ("same subject, current instead of historical email", dict(by_id["case_10"], email=cur10),
         gold["case_10"], "identity resolution across email changes"),
        ("Korean deletion request", dict(by_id["case_01"], request_text=(
            "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.")),
         gold["case_01"], "multilingual intent"),
        ("Spanish keep-my-account request", dict(by_id["case_02"], request_text=(
            "Por favor, no eliminen mi cuenta. Solo quiero actualizar mi número de teléfono.")),
         ("NOT_APPLICABLE", []), "keep-the-account wording in another language"),
        ("'close' about a ticket, account must stay open", dict(by_id["case_05"], request_text=(
            "Please close the ticket about my login problem; my account must stay open.")),
         ("NOT_APPLICABLE", []), "a deletion word used for something else"),
        ("German deletion request", dict(by_id["case_01"], request_text=(
            "Bitte löschen Sie mein Konto und alle zugehörigen personenbezogenen Daten.")),
         gold["case_01"], "multilingual intent (language not in the dataset)"),
    ]
    results = []
    for name, req, expected, tests in cases:
        status, steps, intent = audit([req])[req["case_id"]]
        ok = status == expected[0] and [step_key(s) for s in steps] == [step_key(s) for s in expected[1]]
        results.append((name, req["case_id"], tests, expected[0], status, ok, intent[1]))
    return results


def vision_section(test, test_answers):
    """Gemini screenshot extraction scored against the reference transcription, and the
    test set re-audited with the reference instead, to see whether any answer changes."""
    import evidence
    import vision
    if not vision.OUT.exists():
        return ["No model extraction yet (`code/image_extractions/gemini.json` missing): screenshots "
                "come from the reference transcription."]
    extracted = json.loads(vision.OUT.read_text(encoding="utf-8"))
    s = vision.agreement(extracted)
    # same value recorded, possibly under a neighbouring field name (email vs subject_selector)
    ref = json.loads(vision.REFERENCE.read_text(encoding="utf-8"))
    vals = lambda r: {str(r.get(f)).strip().lower() for f in vision.COMPARED if r.get(f)}
    same = total = 0
    for fid, entry in ref.items():
        if fid.startswith("_"):
            continue
        got = {r["resource_id"]: r for r in extracted.get(fid, {}).get("records", []) if r.get("resource_id")}
        for r in entry["records"]:
            if r.get("resource_id") in got:
                total += len(vals(r))
                same += len(vals(r) & vals(got[r["resource_id"]]))
    original = evidence.load_image_extractions
    evidence.load_image_extractions = lambda: {k: v for k, v in original().items()
                                               if v["producer"] != "gemini-vision"} | {
        k: v for k, v in json.loads(vision.REFERENCE.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    try:
        with_reference = audit(test)
    finally:
        evidence.load_image_extractions = original
    changed_ids = [c for c in test_answers if test_answers[c][:2] != with_reference[c][:2]]
    changed = len(changed_ids)
    return ["Gemini (`code/vision.py`) read each screenshot; the reference is the transcription made while "
            "building the system (`code/image_extractions/reference_transcription.json`).", "",
            "| Measure | Result |", "|---|---|",
            f"| Resource ids found | {pct(s['found_ids'], s['ref_ids'])} (extra: {s['extra_ids']}) |",
            f"| Field agreement, same field name | {pct(s['fields_ok'], s['fields'])} |",
            f"| Value agreement, any identifier field | {pct(same, total)} |", "",
            "Differences: the model repeated correct values in a second field (`email` beside "
            "`subject_selector`, `customer_reference` beside `subject_reference`); it listed `support_9538`, "
            "whose id the screen prints without the ticket's contents; it gave no id for the user_17 "
            "reference screen, which prints none. The pipeline handles both: a record listed without "
            "contents proves existence but not redaction, and an id-less reference resolves through the "
            "catalog alias (`tombstone_17 (user_17)`).", "",
            f"End to end, re-auditing the {len(test)} test requests with the reference transcription in "
            f"place of the model's extraction changes {changed} answer(s)" +
            (f": {', '.join(changed_ids[:10])}." if changed_ids else ".")]


def configuration(answers):
    """What the evaluated run used: screenshot source, intent source and model coverage."""
    from evidence import load_image_extractions
    from llm import configured, models
    producers = Counter(e["producer"] for e in load_image_extractions().values())
    reasons = [a[2][1] for a in answers.values()]
    agree = sum("(model agrees)" in r for r in reasons)
    disagree = sum("MODEL DISAGREES" in r for r in reasons)
    intent = "rule classifier only (no model configured)"
    if configured():
        intent = (f"rule classifier + model second opinion via LiteLLM (chain: {', '.join(models())}): model opinion "
                  f"obtained for {agree + disagree}/{len(reasons)} test requests, agreeing on {agree}, "
                  f"disagreeing on {disagree}" +
                  (f"; the other {len(reasons) - agree - disagree} were decided by the rules alone (model "
                   "unavailable)" if agree + disagree < len(reasons) else ""))
    return [f"- Screenshot evidence: {', '.join(f'{k} ({v} files)' for k, v in sorted(producers.items()))}",
            f"- Intent: {intent}",
            "- Everything else (identity, evidence parsing, resource linking, policy, steps, validation): "
            "deterministic Python"]


def main():
    samples = read_csv("sample_deletion_requests.csv")
    gold = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
            for r in read_csv("sample_output.csv")}
    pred = audit(samples)
    m, per_case = score(pred, gold)
    custom = custom_cases(samples, gold)
    test = read_csv("deletion_requests.csv")
    test_answers = audit(test)
    test_status = Counter(s for s, _, _ in test_answers.values())
    idres = identity_resolution(samples), identity_resolution(test)
    statuses = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")

    lines = ["# Evaluation report", "",
             "Generated by `python code/evaluation/main.py` against `dataset/sample_output.csv` "
             "(32 public cases). The test set has no public answers; its status mix is shown for reference.", "",
             "## Configuration evaluated", ""] + configuration(test_answers) + ["",
             "## Metrics on the 32 sample cases", "",
             "| Metric | Result |", "|---|---|",
             f"| Case-level deletion status accuracy | {pct(m['status_ok'], m['cases'])} |",
             f"| Step-level precision (all fields but step_no match) | {pct(m['step_hits'], m['pred_steps'])} |",
             f"| Step-level recall | {pct(m['step_hits'], m['gold_steps'])} |",
             f"| Cases whose whole steps_json is exactly right | {pct(m['exact_case'], m['cases'])} |",
             f"| Resource discovery recall | {pct(m['resource_hits'], m['gold_resources'])} |",
             f"| Resource discovery precision | {pct(m['resource_hits'], m['pred_resources'])} |",
             f"| Field accuracy: operation (steps aligned on phase+system+resource) | {pct(m['field_operation'], m['aligned'])} |",
             f"| Field accuracy: state | {pct(m['field_state'], m['aligned'])} |",
             f"| Field accuracy: evidence_id | {pct(m['field_evidence_id'], m['aligned'])} |",
             f"| Sequence accuracy (identical ordered step skeleton) | {pct(m['sequence_ok'], m['cases'])} |",
             f"| Order consistency (shared steps in the same relative order) | {pct(m['order_ok'], m['cases'])} |",
             "", "### Deletion status confusion matrix (rows: gold, columns: predicted)", "",
             "| Gold \\ Predicted | " + " | ".join(statuses) + " |", "|---|---|---|---|"]
    lines += [f"| {g} | " + " | ".join(str(m[f"confusion:{g}->{p}"]) for p in statuses) + " |"
              for g in statuses]
    lines += ["", "### Strict field-level accuracy (step i compared with gold step i)", "",
              "A missing or extra step counts as wrong for every field, so one missing step shifts every "
              "later position; this is the harshest view of the same answers.", "",
              "| Field | Correct positions |", "|---|---|"]
    lines += [f"| {f} | {pct(m[f'pos_{f}'], m['positions'])} |" for f in ("step_no",) + FIELDS]
    lines += ["", "### Identity resolution", "",
              "Request emails resolved against `user_identities.csv` plus every historical value in "
              "`user_audit_log.csv`.", "",
              "| Set | Current email | Historical email | Unresolved |", "|---|---|---|---|"]
    lines += [f"| {name} | {k['current']} | {k['historical']} | {k['unresolved']} |"
              for name, k in (("samples (32)", idres[0]), ("test (518)", idres[1]))]
    lines += ["", "## Per-case results", "",
              "| Case | Gold status | Predicted | Gold steps | Predicted steps | Exact step matches | "
              "Missed resources | Extra resources |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {c} | {g} | {p}{'' if g == p else ' ✗'} | {gs} | {ps} | {h} | {', '.join(mi) or '-'} | "
              f"{', '.join(ex) or '-'} |"
              for c, g, p, gs, ps, h, mi, ex in sorted(per_case, key=lambda x: case_number(x[0]))]
    lines += ["", "## Screenshot extraction (model vs reference)", ""] + vision_section(test, test_answers)
    lines += ["", "## Additional evaluation cases (created for this project)", "",
              "Each reuses a sample case's subject and audit time, changing only what the case tests. "
              "Expected outputs are the sample answer (same subject, same evidence) or `NOT_APPLICABLE`.", "",
              "| Case | Based on | Tests | Expected | Got | Pass | Intent reason |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {n} | {c} | {t} | {e} | {g} | {'yes' if ok else 'no'} | {why} |"
              for n, c, t, e, g, ok, why in custom]
    lines += ["", "## Test-set status mix (518 cases, no public answers)", "",
              ", ".join(f"{k}: {v}" for k, v in sorted(test_status.items())), ""]
    for part in ("manual_spot_check.md", "findings.md"):  # hand-written sections
        lines += [line.rstrip() for line in (HERE / part).read_text(encoding="utf-8").splitlines()] + [""]
    (HERE / "evaluation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"custom cases passed: {sum(r[5] for r in custom)}/{len(custom)}")


if __name__ == "__main__":
    main()
