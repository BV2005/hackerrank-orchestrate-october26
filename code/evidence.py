"""Turn every evidence file into flat records: one dict per observed resource.

Each record: file_id, captured_at, container, resource_id, system, fields.
Full-dump containers (SQL tables, snapshot-bundle collections) also report the
systems they cover, so a missing resource there counts as observed ABSENT.
"""
import csv
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

RES_RE = re.compile(
    r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|"
    r"tombstone|legal_hold|marketing|email_delivery|cache|object)_\d+\b")
SYSTEM_BY_PREFIX = {
    "account": "account_service", "search": "search_service",
    "analytics": "analytics_platform", "support": "support_platform",
    "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service",
    "tombstone": "identity_service", "legal_hold": "legal_service",
    "marketing": "marketing_platform", "email_delivery": "email_delivery_service",
    "cache": "cache_service", "object": "object_storage",
}
KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
TRACE_RE = re.compile(r"\b(?:tr-[\w-]+|rb-\d+-\d+)\b")
HANDOVER_RE = re.compile(r"^- (\S+) \| (\w+) \| (\w+) \| (\w+) \| (\w+) \| (.+)$")


def system_of(resource_id):
    return SYSTEM_BY_PREFIX.get(re.sub(r"_\d+$", "", resource_id))


def flatten(obj, prefix=""):
    """Flatten nested dicts/lists, skipping nested objects that are records themselves."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and "resource_id" in v:
                continue
            if isinstance(v, (dict, list)):
                out.update(flatten(v, f"{prefix}{k}."))
            else:
                out[f"{prefix}{k}"] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if not (isinstance(v, dict) and "resource_id" in v):
                out.update(flatten(v, f"{prefix}{i}."))
    return out


def _rec(fid, captured, container, fields):
    rid = fields.get("resource_id")
    return {"file_id": fid, "captured_at": captured, "container": container,
            "resource_id": rid, "system": system_of(rid), "fields": fields}


# ---------------------------------------------------------------- SQL
def parse_sql(path, fid, captured):
    db = sqlite3.connect(":memory:")
    db.executescript(path.read_text(encoding="utf-8"))
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    scope = set()
    if "snapshot_scope" in tables:
        scope = {r[0] for r in db.execute("SELECT origin_system FROM snapshot_scope")}
    records, coverage = [], defaultdict(set)
    for table in tables:
        if table == "snapshot_scope":
            continue
        cur = db.execute(f'SELECT * FROM "{table}"')
        cols = [c[0] for c in cur.description]
        for row in cur:
            fields = dict(zip(cols, row))
            if "record_json" in fields:
                fields.update(flatten(json.loads(fields.pop("record_json"))))
            rec = _rec(fid, captured, table, fields)
            records.append(rec)
            coverage[table].add(rec["system"])
    # inventory snapshots: one table per system; snapshot_scope says which were captured,
    # so an empty table still proves absence for its system
    for table, system in INVENTORY_TABLE_SYSTEM.items():
        if table in tables:
            coverage[table] = {system} if system in scope else set()
    return records, coverage


INVENTORY_TABLE_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
}


# ---------------------------------------------------------------- JSON
def parse_json(path, fid, captured):
    data = json.loads(path.read_text(encoding="utf-8"))
    records, coverage = [], defaultdict(set)

    def walk(obj, container):
        if isinstance(obj, dict):
            if "data_store" in obj and "collection" in obj:
                container = f'{obj["data_store"]}/{obj["collection"]}'
                if "operational_record_history" not in container:
                    coverage[container].add(obj["data_store"])
            if "resource_id" in obj:
                records.append(_rec(fid, captured, container, flatten(obj)))
            for k, v in obj.items():
                walk(v, f"{container}/{k}" if k in SYSTEM_BY_PREFIX.values() else container)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, container)

    walk(data, path.stem)
    return records, coverage


# ---------------------------------------------------------------- logs
def _log_fields(line):
    ts, _, rest = line.partition(" ")
    fields = {}
    if rest.startswith("{"):
        fields = json.loads(rest)
    else:
        for k, v in KV_RE.findall(rest):
            fields[k] = v[1:-1].replace('\\"', '"') if v.startswith('"') else v
        if "message" in fields:  # cloud runtime: kv pairs nested in message="..."
            fields.update((k, v.strip('"')) for k, v in KV_RE.findall(fields.pop("message")))
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) >= 5:  # "LEVEL | service | trace | event | kv..."
            fields.setdefault("service", parts[1])
            fields.setdefault("event", parts[3])
        m = re.match(r"\[\w+\] \[(\w+)\] \[[\w-]+\] (\w+):", rest)
        if m:
            fields.setdefault("service", m.group(1))
            fields.setdefault("event", m.group(2))
        m = re.match(r"<\d+>\d+ \S+ (\w+) ", rest)
        if m:
            fields.setdefault("service", m.group(1))
    for k, v in list(fields.items()):
        if v == "null":
            fields[k] = None
    fields["_ts"] = ts
    return fields


def parse_log(path, fid, captured):
    traces, singles = defaultdict(list), []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = _log_fields(line)
        m = TRACE_RE.search(line)
        (traces[m.group(0)] if m else singles).append(fields)
    records = []
    for group in list(traces.values()) + [[s] for s in singles]:
        merged = {}
        # lines carrying the resource_id (the "completed" result) win on conflicts
        for fields in sorted(group, key=lambda f: "resource_id" in f):
            merged.update({k: v for k, v in fields.items() if v is not None or k not in merged})
        if merged.get("resource_id") and RES_RE.fullmatch(str(merged["resource_id"])):
            records.append(_rec(fid, captured, f"log:{merged.get('service', '')}", merged))
    return records, {}


# ---------------------------------------------------------------- text / pdf
def parse_handover(path, fid, captured):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = HANDOVER_RE.match(line.strip())
        if m:
            ts, system, rid, category, state, ident = m.groups()
            records.append(_rec(fid, captured, "handover", {
                "resource_id": rid, "observed_at": ts, "record_type": category,
                "result": state, "subject_reference": ident}))
    return records, {}


def parse_pdf(path, fid, captured):
    import pypdf
    text = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(path).pages)
    matches = list(RES_RE.finditer(text))
    records = []
    for i, m in enumerate(matches):  # segment from one resource mention to the next
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        records.append(_rec(fid, captured, "pdf", {"resource_id": m.group(0),
                                                    "_text": text[m.start():end]}))
    return records, {}


PARSERS = {".sql": parse_sql, ".json": parse_json, ".log": parse_log,
           ".txt": parse_handover, ".pdf": parse_pdf}
IMAGE_EXTRACTIONS = Path(__file__).resolve().parent / "image_extractions"


def load_image_extractions():
    """file_id -> extracted screenshot records; a model extractor's output wins over
    the reference transcription when both describe the same file."""
    found = {}
    for path in sorted(IMAGE_EXTRACTIONS.glob("*.json"), key=lambda p: p.name != "reference_transcription.json"):
        for fid, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if not fid.startswith("_"):
                found[fid] = entry
    return found


CATALOG_ALIAS_RE = re.compile(r"linked (\w+?_\d+) \(([^)]+)\)")
DIRECT_IDENTIFIERS = ("email", "phone", "full_name")


def catalog_aliases(dataset):
    """Display reference -> resource id, from catalog rows like
    'Data catalog linked tombstone_17 (user_17) to ...'; only unambiguous aliases."""
    seen = defaultdict(set)
    with open(dataset / "system_activity.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["event_type"] == "LINK_RESOURCE_IDENTITY":
                for rid, alias in CATALOG_ALIAS_RE.findall(row["details"]):
                    if alias != rid:
                        seen[alias].add(rid)
    return {alias: rids.pop() for alias, rids in seen.items() if len(rids) == 1}


def parse_image(entry, fid, captured, aliases):
    records = []
    for r in entry["records"]:
        fields = dict(r)
        if not fields.get("resource_id"):
            # the screen prints only a reference (e.g. user_17): use the catalog's alias for it
            ref = fields.get("subject_reference") or fields.get("customer_reference")
            if ref not in aliases:
                continue
            fields["resource_id"], fields["resource_id_source"] = aliases[ref], f"catalog alias of {ref}"
        if fields.get("result") == "FOUND" and not any(fields.get(k) for k in DIRECT_IDENTIFIERS):
            # listed without its contents: proves the record exists and who it belongs to,
            # but PII that the screen does not show is not evidence the PII was removed
            fields["_contents_not_shown"] = True
        records.append(_rec(fid, captured, f"image:{entry['producer']}", fields))
    return records, {}


def load_evidence(dataset):
    """Return (records, coverage, skipped) for every manifest file."""
    records, coverage, skipped = [], {}, []
    images, aliases = load_image_extractions(), catalog_aliases(dataset)
    with open(dataset / "evidence_files.csv", encoding="utf-8") as f:
        manifest = list(csv.DictReader(f))
    for row in manifest:
        path = dataset / row["file_path"]
        if row["file_id"] in images:
            recs, cov = parse_image(images[row["file_id"]], row["file_id"], row["captured_at"], aliases)
            records += recs
            continue
        parser = PARSERS.get(path.suffix)
        if parser is None:
            skipped.append(row["file_id"])  # images with no extraction yet
            continue
        recs, cov = parser(path, row["file_id"], row["captured_at"])
        records += recs
        for container, systems in cov.items():
            coverage[(row["file_id"], container)] = (row["captured_at"], systems)
    return records, coverage, skipped
