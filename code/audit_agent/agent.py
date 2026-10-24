"""Google ADK agents for the deletion audit.

  audit_coordinator (root_agent)   answers questions about any case, explains statuses and
                                   steps, and can ask the reviewer to investigate a case
  case_reviewer                    investigates one flagged case with read-only tools and
                                   returns a ReviewDecision (schema-enforced)

Models are reached through LiteLLM (google.adk.models.lite_llm.LiteLlm) with the same
fallback chain as the batch pipeline (LLM_MODELS in .env). Run interactively with
`adk web code` or `adk run code/audit_agent`; batch review with code/audit_agent/review.py.
"""
import os
import sys
from pathlib import Path
from typing import Literal, Optional

# LiteLLM is the chosen model layer (one fallback chain for pipeline and agents), so
# ADK's suggestion to use its native Gemini client instead is noise here
os.environ.setdefault("ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS", "true")

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm  # noqa: E402

from . import callbacks, tools  # noqa: E402

llm._export_keys()  # keys from .env / the Windows user environment, for LiteLLM
_chain = llm.models() or ["gemini/gemini-3.6-flash"]
MODEL = LiteLlm(model=_chain[0], fallbacks=_chain[1:], temperature=0)


class ReviewDecision(BaseModel):
    """The reviewer's verdict on a case's deleted-account assignment."""
    case_id: str
    decision: Literal["confirm_pipeline", "choose_other", "undetermined"] = Field(
        description="confirm the pipeline's pick, choose another listed candidate, or say the evidence cannot decide")
    account_resource_id: Optional[str] = Field(
        description="the account you judge belongs to this case; must be one of the listed candidates")
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(description="file_id / activity_id values your reasoning rests on")
    reason: str = Field(description="two or three sentences")


case_reviewer = LlmAgent(
    name="case_reviewer",
    model=MODEL,
    description="Investigates one flagged deletion case and decides which deleted account belongs to it.",
    instruction=(
        "You review one case of a privacy deletion audit. The deterministic pipeline could not tell from "
        "evidence which deleted account belongs to this person: deleted accounts keep no email or user id, "
        "so several account deletions in the same deletion-run window are candidates, and the pipeline "
        "picked one by timing.\n"
        "Investigate before deciding: call get_case, then list_account_candidates, then get_activity / "
        "get_evidence for the candidates and for the person's other resources. Useful signals: a deletion "
        "run deletes the account first and then works through the same person's other resources; "
        "activity rows are claims, not proof; evidence after audit_at is withheld from you on purpose.\n"
        "Only choose among the listed candidates. If the evidence does not single one out, answer "
        "'undetermined' rather than guessing. Cite the evidence ids you relied on."),
    tools=[tools.get_case, tools.list_account_candidates, tools.get_activity, tools.get_evidence],
    output_schema=ReviewDecision,
    output_key="review",
    before_model_callback=callbacks.before_model,
    after_model_callback=callbacks.after_model,
)

audit_coordinator = LlmAgent(
    name="audit_coordinator",
    model=MODEL,
    description="Answers questions about the Ghost Data deletion audit.",
    instruction=(
        "You are the front desk of a privacy deletion audit over 550 requests (case_01 to case_550). "
        "The answers were produced by a deterministic pipeline: identity resolution over current and "
        "historical identifiers, evidence parsed from databases, logs, exports, PDFs and screenshots, the "
        "state of every linked record at the case's audit_at, the policy in deletion_rules.csv, and "
        "ordered findings / remediation / verification steps.\n"
        "Use the tools to answer: get_case for what was concluded and why, get_evidence and get_activity "
        "for what the records show (both already limited to evidence up to the case's audit_at), "
        "explain_rules for the policy, list_cases to find cases. For a case whose deleted account was "
        "placed by timing, you may ask case_reviewer to investigate.\n"
        "Ground every statement in tool results and cite file_id / activity_id values. Workflow statuses "
        "and activity results are claims, not proof. Never change or invent an answer: you explain the "
        "audit, the pipeline writes it."),
    tools=[tools.list_cases, tools.get_case, tools.get_evidence, tools.get_activity,
           tools.list_account_candidates, tools.explain_rules, AgentTool(agent=case_reviewer)],
    before_model_callback=callbacks.before_model,
    after_model_callback=callbacks.after_model,
)

root_agent = audit_coordinator
