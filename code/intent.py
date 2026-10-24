"""Does a request authorize deletion?  Rule pass; 'uncertain' results go to the model.

Order matters: a request that asks for something else, or says to keep the account,
is not a deletion even when it uses words like "remove" ("remove me from marketing
lists only") - so non-deletion intents are checked before deletion verbs.
"""
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
