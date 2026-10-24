"""Ask the audit coordinator agent a question.

Run from the repository root:  python code/ask_agent.py "Why is case_34 INCOMPLETE?"
Interactive UI instead:          adk web code   (then pick audit_agent)
"""
import sys

from audit_agent.agent import root_agent
from audit_agent.runner import run_agent


def main():
    question = " ".join(sys.argv[1:]) or "Summarise the audit of case_34 and cite the evidence."
    answer, calls = run_agent(root_agent, question)
    for c in calls:
        print("tool:", c)
    print(answer)


if __name__ == "__main__":
    main()
