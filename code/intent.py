"""Does a request authorize deletion?  Rule pass; 'uncertain' results go to the model.

Order matters: a request that asks for something else, or says to keep the account,
is not a deletion even when it uses words like "remove" ("remove me from marketing
lists only") - so non-deletion intents are checked before deletion verbs.
"""
import json
import re

# requests for another privacy right, or explicit instructions to keep the account
NOT_DELETION = {
    "marketing_opt_out": r"marketing lists? only|stop (the )?(promotional|marketing) (e-?mails|messages)|unsubscribe",
    "export_or_access": r"download my (account )?(information|data)|portable format|(send me|need|want) a copy of"
                        r"|what (personal )?information you hold|information request",
    "correction": r"misspell|out of date|correct (it|my)|change the (e-?mail|name|phone)|update my",
    "account_support": r"reset(ting)? (the |my )?(login|password)|can.t access my",
    "keep_account": r"do not (close|delete|remove)|don.t (close|delete)|keep (the|my) (account|profile)"
                    r"|without removing|still (need|use) (the|it|my)",
}
DELETION = (
    r"\b(delet\w*|eras\w*|remov\w*|close[ds]?|closing)\b|account-deletion"
    r"|\b(is|are|was) gone\b"             # asking to confirm a deletion took effect
    r"|suppression|supprim\w+"            # fr
    r"|eliminaci[oó]n|eliminar"           # es
    r"|exclus[aã]o|excluir"               # pt
    r"|l[öo]sch\w*"                       # de
    r"|cancellazione|cancellare"          # it
    r"|削除|삭제"                          # ja, ko
)


MODEL_PROMPT = """You review privacy requests sent to an online shop. For each request decide whether
the person is asking for their account / personal data to be deleted (including asking to
confirm or finish a deletion, or to remove remaining copies), or for something else
(correction, access or export, a copy of data or invoices, marketing opt-out, login help,
changing an email). A request that says to keep the account is not a deletion.
Requests may be in any language. Answer for every case_id, with a one-line English reason.

Requests (JSON):
"""
MODEL_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {"case_id": {"type": "string"},
                       "decision": {"type": "string", "enum": ["deletion", "not_deletion"]},
                       "reason": {"type": "string"}},
        "required": ["case_id", "decision", "reason"]}}},
    "required": ["decisions"],
}


def model_opinions(requests, run_id=None, batch=50):
    """case_id -> (decision, reason) from Gemini, in batches. Empty if not configured."""
    from llm import ask_json, configured
    if not configured():
        return {}
    out = {}
    for i in range(0, len(requests), batch):
        chunk = [{"case_id": r["case_id"], "request_text": r["request_text"]} for r in requests[i:i + batch]]
        answer = ask_json("intent", MODEL_PROMPT + json.dumps(chunk, ensure_ascii=False), MODEL_SCHEMA, run_id=run_id)
        out.update({d["case_id"]: (d["decision"], d["reason"]) for d in answer["decisions"]})
    return out


def decide(requests, run_id=None):
    """case_id -> (decision, reason, source). Rules decide when confident; the model
    decides 'uncertain' ones. Disagreements stay with the rules and are reported."""
    opinions = model_opinions(requests, run_id)
    decisions = {}
    for r in requests:
        label, why = classify(r["request_text"])
        model = opinions.get(r["case_id"])
        if label == "uncertain" and model:
            decisions[r["case_id"]] = (model[0], f"model: {model[1]}", "model")
        elif label == "uncertain":
            decisions[r["case_id"]] = ("deletion", "no rule matched; audited as deletion", "default")
        else:
            agrees = "" if model is None else (" (model agrees)" if model[0] == label else
                                               f" (MODEL DISAGREES: {model[1]})")
            decisions[r["case_id"]] = (label, why + agrees, "rules")
    return decisions


def classify(text):
    """('deletion' | 'not_deletion' | 'uncertain', reason)."""
    lowered = text.lower()
    for intent, pattern in NOT_DELETION.items():
        if re.search(pattern, lowered):
            return "not_deletion", intent
    m = re.search(DELETION, lowered)
    if m:
        return "deletion", f"deletion term '{m.group(0)}'"
    return "uncertain", "no rule matched"
