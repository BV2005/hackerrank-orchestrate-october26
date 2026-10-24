"""Extract evidence records from the PNG screenshots with Gemini (milestone M5).

Run from the repository root:  python code/vision.py
Writes code/image_extractions/gemini.json (loaded by evidence.py in preference to the
reference transcription) and prints field agreement against that transcription.
"""
import csv
import json
import sys
from pathlib import Path

from llm import NotConfigured, ask_json_many

HERE = Path(__file__).resolve().parent
DATASET = HERE.parent / "dataset"
OUT = HERE / "image_extractions" / "gemini.json"
REFERENCE = HERE / "image_extractions" / "reference_transcription.json"

PROMPT = """This is a screenshot from an internal admin tool of an online shop. It is evidence in a
privacy audit of whether customer data was deleted.

List every data record the screen shows (customer profile, search document, analytics profile,
export job, support ticket, backup archive, audit event, connector/data flow, marketing profile,
customer reference, stored file). For each record give:
- resource_id: the internal id exactly as printed (forms like account_5762, search_428,
  analytics_7438, export_1260, support_1034, backup_9287, audit_record_003, data_flow_1121,
  marketing_001), or null when the screen prints none. Never invent or derive an id.
- result: FOUND if the record exists; IDENTIFIABLE for an analytics profile that still names the
  person; ACTIVE for a data flow or connector that is enabled/syncing; DISABLED or BLOCKED when the
  screen says so; REDACTED when the screen shows the record kept without the person's details.
- email, phone, full_name, customer_reference (ids like user_11), subject_reference, and
  subject_selector (the address a connector selects): copy exactly as shown, else null.
- note: one short line of other facts on the screen about this record (status, dates, sizes).
Report only what is visible."""

SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string", "nullable": True},
            "result": {"type": "string", "enum": ["FOUND", "IDENTIFIABLE", "ACTIVE", "DISABLED", "BLOCKED", "REDACTED"]},
            "email": {"type": "string", "nullable": True},
            "phone": {"type": "string", "nullable": True},
            "full_name": {"type": "string", "nullable": True},
            "customer_reference": {"type": "string", "nullable": True},
            "subject_reference": {"type": "string", "nullable": True},
            "subject_selector": {"type": "string", "nullable": True},
            "note": {"type": "string"},
        },
        "required": ["resource_id", "result", "note"],
    }}},
    "required": ["records"],
}
COMPARED = ("result", "email", "phone", "full_name", "customer_reference", "subject_reference", "subject_selector")


def extract(run_id=None):
    with open(DATASET / "evidence_files.csv", encoding="utf-8") as f:
        images = [r for r in csv.DictReader(f) if r["file_path"].endswith(".png")]
    out = {"_about": "Records extracted from the PNG screenshots by Gemini via code/vision.py."}
    # screenshots are independent: send them concurrently (llm.WORKERS at a time), each
    # with its own retry/backoff, so one overloaded request does not hold up the rest
    jobs = [{"task": "vision", "prompt": PROMPT, "schema": SCHEMA, "run_id": run_id,
             "images": [(row["file_path"], (DATASET / row["file_path"]).read_bytes())]} for row in images]
    failed = []
    for row, answer in zip(images, ask_json_many(jobs)):
        if isinstance(answer, Exception):
            failed.append(row["file_id"])
            continue
        out[row["file_id"]] = {"producer": "gemini-vision", "screen": row["file_path"],
                               "records": answer["records"]}
        print(f"{row['file_id']}: {len(answer['records'])} records")
    if failed:
        # keep the last complete extraction (or the reference transcription) rather than
        # replacing it with a partial one
        print(f"vision: model unavailable for {failed}; existing extraction kept")
        return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def agreement(extracted):
    """Field agreement with the reference transcription, on records matched by resource_id."""
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    stats = {"ref_ids": 0, "found_ids": 0, "extra_ids": 0, "fields": 0, "fields_ok": 0, "diffs": []}
    norm = lambda v: (v or "").strip().lower() or None
    for fid, entry in ref.items():
        if fid.startswith("_"):
            continue
        want = {r["resource_id"]: r for r in entry["records"] if r.get("resource_id")}
        got = {r["resource_id"]: r for r in extracted.get(fid, {}).get("records", []) if r.get("resource_id")}
        stats["ref_ids"] += len(want)
        stats["found_ids"] += len(want.keys() & got.keys())
        stats["extra_ids"] += len(got.keys() - want.keys())
        for rid in want.keys() & got.keys():
            for f in COMPARED:
                stats["fields"] += 1
                if norm(want[rid].get(f)) == norm(got[rid].get(f)):
                    stats["fields_ok"] += 1
                else:
                    stats["diffs"].append((fid, rid, f, want[rid].get(f), got[rid].get(f)))
    return stats


def main():
    try:
        extracted = extract(run_id=sys.argv[1] if len(sys.argv) > 1 else None)
    except NotConfigured as e:
        sys.exit(f"vision extraction skipped: {e}")
    s = agreement(extracted)
    print(f"resource ids found {s['found_ids']}/{s['ref_ids']}, extra {s['extra_ids']}; "
          f"field agreement {s['fields_ok']}/{s['fields']}")
    for d in s["diffs"]:
        print("  diff", d)


if __name__ == "__main__":
    main()
