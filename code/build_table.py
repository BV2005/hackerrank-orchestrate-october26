"""Build the case x resource audit table.

request email -> user identity set -> evidence records naming that user -> resource_ids
-> system_activity claims for those resources -> latest observed state at audit_at.

Run: python code/build_table.py [requests_csv]
     (default deletion_requests.csv; writes artifacts/<requests stem>_table.csv)
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from evidence import RES_RE, load_evidence, system_of
from identity import EMAIL_RE, USER_RE, Identities

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"
OUT = ROOT / "artifacts"

# fields that hold the lookup key rather than stored data: they link a record to a
# user but do not mean the record itself still exposes that identifier
QUERY_KEYS = {"lookup_key", "match_key", "correlation_key", "subject_key", "scan_key",
              "query", "lookup_value", "subject_reference", "subject_selector",
              "subject_ref", "customer_reference", "user_id"}
TEXT_KEYS = {"_text", "message_text", "message_excerpt", "details"}
CATEGORY_BY_PREFIX = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "analytics": "ANALYTICS_PROFILE",
    "export": "USER_EXPORT", "marketing": "MARKETING_PROFILE", "cache": "CACHE_ENTRY",
    "email_delivery": "EMAIL_DELIVERY_LOG", "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD", "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE", "data_flow": "DATA_FLOW", "object": "SUPPORT_ATTACHMENT",
}
TRUE = {True, 1, "1", "true", "True"}
RUN_WINDOW_MIN = 80  # account delete lands 40-75 min before reported_at in the samples


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_csv(name):
    with open(DATASET / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record_users(rec, ids):
    """(user_id, how) pairs a record links to, from any field or free text."""
    hits = set()
    for k, v in rec["fields"].items():
        if not isinstance(v, str):
            continue
        hits |= ids.match_text(v) if k.split(".")[-1] in TEXT_KEYS else ids.match_value(v)
    return hits


def exposes_pii(rec, uid, ids):
    """Does a stored (non-lookup) field still carry one of the user's direct identifiers?"""
    name = ids.users[uid]["name"].lower()
    for k, v in rec["fields"].items():
        if not isinstance(v, str) or k.split(".")[-1] in QUERY_KEYS or k.startswith("_ts"):
            continue
        if any(u == uid for u, _ in (ids.match_text(v) if k.split(".")[-1] in TEXT_KEYS
                                     else ids.match_value(v))):
            return True
        if v.strip().lower() == name:
            return True
    return False


def observed_state(rec, uid, ids):
    f = {k.split(".")[-1]: v for k, v in rec["fields"].items()}
    result = str(f.get("result") or "").upper()
    if result in {"NOT_FOUND", "MISS"}:
        return "ABSENT"
    if result in {"ACTIVE", "BLOCKED", "DISABLED"}:
        return result
    if result in {"ANONYMIZED", "REDACTED"}:
        return "PRESENT_NO_PII"
    if result == "IDENTIFIABLE":
        return "PRESENT_WITH_PII"
    if str(f.get("http_status") or f.get("status_code") or "") == "404" or str(f.get("rows")) == "0":
        return "ABSENT"
    if "profile" in rec["fields"] and rec["fields"]["profile"] is None:
        return "ABSENT"
    if "enabled" in f:
        return "ACTIVE" if f["enabled"] in TRUE else "DISABLED"
    if "restore_allowed" in f:
        return "ACTIVE" if f["restore_allowed"] in TRUE else "BLOCKED"
    if "eligible" in f:  # backup restore evaluation in service logs
        return "ACTIVE" if f["eligible"] in TRUE and f.get("deletion_block") not in TRUE else "BLOCKED"
    return "PRESENT_WITH_PII" if exposes_pii(rec, uid, ids) else "PRESENT_NO_PII"


def category_of(resource_id, recs):
    for rec in recs:
        rt = rec["fields"].get("record_type")
        if rt and rt != "DATA_FLOW" and rt in RULES:
            return rt
    prefix = re.sub(r"_\d+$", "", resource_id)
    if prefix == "support":
        attachment = any("attachment" in rec["container"] or "file_name" in rec["fields"]
                         or rec["fields"].get("record_type") == "SUPPORT_ATTACHMENT"
                         for rec in recs)
        return "SUPPORT_ATTACHMENT" if attachment else "SUPPORT_MESSAGE"
    return CATEGORY_BY_PREFIX.get(prefix, "UNKNOWN")


RULES = {r["data_category"]: r for r in read_csv("deletion_rules.csv")}


def main(requests_file="deletion_requests.csv"):
    ids = Identities(DATASET)
    records, coverage, skipped = load_evidence(DATASET)
    activity = read_csv("system_activity.csv")

    by_resource = defaultdict(list)
    for rec in records:
        by_resource[rec["resource_id"]].append(rec)
    in_container = defaultdict(set)
    for rec in records:
        in_container[(rec["file_id"], rec["container"])].add(rec["resource_id"])

    # ownership: resource -> [(user, captured_at, via)]
    owners = defaultdict(list)
    for rec in records:
        for uid, how in record_users(rec, ids):
            owners[rec["resource_id"]].append((uid, rec["captured_at"], f"{how}@{rec['file_id']}"))
    claims = defaultdict(list)
    for a in activity:
        claims[a["resource_id"]].append(a)
        if a["event_type"] == "LINK_RESOURCE_IDENTITY":
            linked = {u for e in EMAIL_RE.findall(a["details"]) for u, _ in ids.match_value(e)}
            linked |= {u for m in USER_RE.findall(a["details"]) for u, _ in ids.match_value(m)}
            for rid in set(RES_RE.findall(a["details"])):
                for uid in linked:
                    owners[rid].append((uid, a["event_time"], f"catalog@{a['activity_id']}"))
    # one hop: a record that references a resource the user owns (e.g. search doc -> account)
    for rec in records:
        for v in rec["fields"].values():
            if isinstance(v, str) and RES_RE.fullmatch(v) and v != rec["resource_id"]:
                for uid, when, _ in owners.get(v, []):
                    owners[rec["resource_id"]].append((uid, rec["captured_at"], f"ref:{v}@{rec['file_id']}"))

    account_deletes = [a for a in activity if a["event_type"] == "DELETE_RECORD"
                       and a["target_system"] == "account_service"]
    rows = []
    for req in read_csv(requests_file):
        uid, kind = ids.resolve_email(req["email"])
        user = ids.users.get(uid, {})
        cutoff = req["audit_at"]
        mine = defaultdict(set)
        for rid, links in owners.items():
            for owner, when, via in links:
                if owner == uid and when <= cutoff:
                    mine[rid].add(via)
        account_candidates = []
        if uid and not any(r.startswith("account_") for r in mine):
            # no evidence names the account: the deletion run submits it shortly before
            # reported_at; accept only an unambiguous candidate no other user owns
            rep_at = ts(req["reported_at"])
            account_candidates = sorted({
                a["resource_id"] for a in account_deletes
                if -RUN_WINDOW_MIN * 60 <= (ts(a["event_time"]) - rep_at).total_seconds() <= 10 * 60
                and not any(o != uid for o, _, _ in owners.get(a["resource_id"], []))})
            if len(account_candidates) == 1:
                rid = account_candidates[0]
                act_id = next(a["activity_id"] for a in account_deletes if a["resource_id"] == rid)
                mine[rid].add(f"deletion_run@{act_id}")

        for rid in sorted(mine):
            system = system_of(rid)
            recs = [r for r in by_resource.get(rid, []) if r["captured_at"] <= cutoff]
            obs = [(r["captured_at"], r["file_id"], r["container"], observed_state(r, uid, ids)) for r in recs]
            for (fid, container), (captured, systems) in coverage.items():
                if captured <= cutoff and system in systems and rid not in in_container[(fid, container)]:
                    obs.append((captured, fid, "omitted", f"ABSENT:{container}"))
            explicit = [o for o in obs if o[2] != "omitted"] or obs
            explicit.sort()
            # explicit observations beat absence inferred from a dump that omits the record
            latest = [o for o in explicit if o[0] == explicit[-1][0]]
            # within the latest capture any surviving copy (e.g. a replica) wins over absence
            pick = next((o for o in latest if not o[3].startswith("ABSENT")), latest[0]) if latest else None
            # catalog link rows are ownership facts, not claims about the resource's state
            cl = sorted((a for a in claims.get(rid, []) if a["event_time"] <= cutoff
                         and a["event_type"] != "LINK_RESOURCE_IDENTITY"),
                        key=lambda a: a["event_time"])
            last = cl[-1] if cl else {}
            category = category_of(rid, recs)
            rule = RULES.get(category, {})
            rows.append({
                "case_id": req["case_id"], "request_email": req["email"], "email_kind": kind,
                "user_id": uid, "user_name": user.get("name"), "current_email": user.get("email"),
                "requested_at": req["requested_at"], "reported_status": req["reported_status"],
                "reported_at": req["reported_at"], "audit_at": cutoff,
                "resource_id": rid, "system_id": system, "category": category,
                "required_action": rule.get("required_action", "DISABLE" if category == "DATA_FLOW" else ""),
                "link_via": ";".join(sorted(mine[rid])),
                "claim_count": len(cl), "claim_activity_id": last.get("activity_id"),
                "claim_event_time": last.get("event_time"), "claim_event_type": last.get("event_type"),
                "claim_source_system": last.get("source_system"),
                "claim_target_system": last.get("target_system"), "claim_result": last.get("reported_result"),
                "observed_state": pick[3].split(":")[0] if pick else "NO_EVIDENCE",
                "observed_evidence_id": pick[1] if pick else "", "observed_captured_at": pick[0] if pick else "",
                "observed_container": (pick[3].split(":", 1)[1] if pick[2] == "omitted" else pick[2]) if pick else "",
                "unresolved_account_candidates": ";".join(account_candidates) if len(account_candidates) > 1 else "",
                "observation_history": " | ".join(f"{o[1]}:{o[2]}:{o[3]}" for o in sorted(obs)[-6:]),
            })

    OUT.mkdir(exist_ok=True)
    with open(OUT / f"{Path(requests_file).stem}_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cases = {r["case_id"] for r in rows}
    print(f"{len(rows)} rows for {len(cases)} cases; images skipped (no OCR yet): {len(skipped)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
